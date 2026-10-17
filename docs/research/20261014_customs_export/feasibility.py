"""1단계 실현 가능성 — 관세청 (시군구×HS6) 수출액이 회사 매출(DART 별도 분기)을 따라가는가.

  .venv/bin/python docs/research/20261014_customs_export/feasibility.py   → results_feasibility.md

판정 지표(회사별):
  share   : 대상 시군구 합 / 전국 같은 HS6 수출 (2016·2024) — 높을수록 '그 칸 = 그 회사' 해석 가능
  ratio   : 분기 수출(원화 환산) / 별도 매출 — 1 을 넘으면 다른 회사가 섞인 것
  corr_yoy: 분기 전년비 성장률 상관(수출 원화 vs 매출) — 핵심
  corr_lvl: 분기 수준 상관
"""
import os, sys, io, urllib.request
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import customs_api as c

SIDOS = ["11", "26", "27", "28", "29", "30", "31", "36", "41", "43", "44", "46", "47", "48", "50", "51", "52"]
YEARS = range(2015, 2027)

# 회사 → (HS6 목록, [(시도코드, 시군구명 부분일치)]) — 공장 소재지는 공개 정보(사업보고서) 기준, 이 단계에선 후보
CANDS = {
    "003230 삼양식품":        (["190230"], [("48", "밀양시"), ("51", "원주시"), ("52", "익산시")]),
    "000660 SK하이닉스":      (["854232"], [("41", "이천시"), ("43", "청주시")]),
    "042700 한미반도체":      (["848620"], [("28", "서구")]),
    "267260 HD현대일렉트릭":  (["850422", "850423"], [("31", "동구")]),
    "298040 효성중공업":      (["850422", "850423"], [("48", "창원시")]),
    "010130 고려아연":        (["790111"], [("31", "울주군")]),
    "002350 넥센타이어":      (["401110"], [("48", "양산시"), ("48", "창녕군")]),
    "161390 한국타이어":      (["401110"], [("30", "대덕구"), ("44", "금산군")]),
    "073240 금호타이어":      (["401110"], [("29", "광산구"), ("46", "곡성군"), ("41", "평택시")]),
    "009150 삼성전기":        (["853224"], [("26", "강서구"), ("41", "수원시")]),
}


def fx() -> pd.Series:
    fp = os.path.join(c.ROOT, "raw", "fred_EXKOUS.csv")
    if not os.path.exists(fp) or (pd.Timestamp.now() - pd.Timestamp(os.path.getmtime(fp), unit="s")).days > 7:
        t = urllib.request.urlopen("https://fred.stlouisfed.org/graph/fredgraph.csv?id=EXKOUS", timeout=60).read()
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, "wb").write(t)
    d = pd.read_csv(fp, parse_dates=["observation_date"])
    return d.set_index(d.observation_date.dt.to_period("M"))["EXKOUS"]


def frame(hs: str, sido: str, year: int) -> pd.DataFrame:
    r = pd.DataFrame(c.rows(hs, sido, year))
    if r.empty:
        return r
    r = r[r.priodTitle.str.match(r"^\d{4}\.\d{2}$")]          # '총계' 행 제외
    r["month"] = pd.PeriodIndex(r.priodTitle.str.replace(".", "-"), freq="M")
    r["sido"] = sido
    return r[["month", "sido", "sggNm", "hsSgn", "expUsdAmt", "expCnt"]]


def concentration(hs: str, year: int, targets) -> tuple[float, str]:
    d = pd.concat([frame(hs, s, year) for s in SIDOS])
    by = d.groupby("sggNm").expUsdAmt.sum().sort_values(ascending=False)
    mask = [any(ts == sd and tn in g for ts, tn in targets) for g, sd in
            d.groupby("sggNm").sido.first().reindex(by.index).items()]
    share = by[mask].sum() / by.sum() if by.sum() else np.nan
    top = ", ".join(f"{g.split()[-1]} {v / by.sum():.0%}" for g, v in by.head(4).items())
    return share, top


def series(hs_list, targets) -> pd.Series:
    parts = []
    for hs in hs_list:
        for sido in sorted({s for s, _ in targets}):
            names = [n for s, n in targets if s == sido]
            for y in YEARS:
                d = frame(hs, sido, y)
                if not d.empty:
                    parts.append(d[d.sggNm.apply(lambda g: any(n in g for n in names))])
    d = pd.concat(parts)
    return d.groupby("month").expUsdAmt.sum()          # 천 달러


def sales_q(code: str) -> pd.Series:
    """분기 매출 — 패널은 회사당 재무제표 1종(연결 우선, 없으면 별도), sales 는 이미 분기값(Q4=연간−Q3누적)."""
    f = pd.read_parquet(c.data_vault_path("staging", "kr_equity", "derived", "fundamentals_q.parquet"),
                        columns=["stock_code", "fs_div", "period_end", "sales", "rcept_dt"])
    f = f[f.stock_code == code].sort_values(["period_end", "rcept_dt"]).drop_duplicates("period_end", keep="last")
    s = f.set_index("period_end").sales
    s.index = s.index.to_period("Q")
    return s


def main():
    fxm = fx()
    lines = ["# 1단계 실현 가능성 — 관세청 시군구×HS6 수출 vs DART 별도 분기 매출\n",
             "금액 단위 가정: 천 달러 → 월평균 원달러(FRED EXKOUS)로 원화 환산. 매출 = 패널 분기 매출(연결 우선·없으면 별도) — 연결은 해외법인 매출이 섞임.\n",
             "| 회사 | HS | 지역 점유 2016 | 2024 | 2024 상위 시군구 | 수출/매출(중앙) | 전년비 상관 | 수준 상관 | 분기수 |",
             "|---|---|---|---|---|---|---|---|---|"]
    detail = []
    for name, (hs_list, targets) in CANDS.items():
        code = name.split()[0]
        sh = {y: concentration(hs_list[-1], y, targets) for y in (2016, 2024)}
        m = series(hs_list, targets)
        krw = (m * 1000 * fxm.reindex(m.index)).dropna()
        q = krw.groupby(krw.index.asfreq("Q")).agg(["sum", "count"])
        q = q[q["count"] == 3]["sum"]
        s = sales_q(code)
        j = pd.concat({"exp": q, "sales": s}, axis=1).dropna()
        j = j[j.sales > 0]
        ratio = (j.exp / j.sales).median() if len(j) else np.nan
        yoy = np.log(j.where(j > 0)).diff(4).replace([np.inf, -np.inf], np.nan).dropna()
        cy = yoy.exp.corr(yoy.sales) if len(yoy) > 6 else np.nan
        cl = j.exp.corr(j.sales) if len(j) > 6 else np.nan
        lines.append(f"| {name} | {','.join(hs_list)} | {sh[2016][0]:.0%} | {sh[2024][0]:.0%} | {sh[2024][1]} | "
                     f"{ratio:.2f} | {cy:+.2f} | {cl:+.2f} | {len(j)} |")
        j["exp/sales"] = j.exp / j.sales
        detail.append(f"\n## {name}\n\n```\n" + (j / [1e8, 1e8, 1]).round(2).rename(
            columns={"exp": "수출(억원)", "sales": "매출(억원)"}).to_string() + "\n```")
        print(lines[-1], flush=True)
    out = "\n".join(lines) + "\n" + "\n".join(detail) + "\n"
    open(os.path.join(HERE, "results_feasibility.md"), "w", encoding="utf-8").write(out)


if __name__ == "__main__":
    main()
