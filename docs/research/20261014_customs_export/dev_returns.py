"""3단계(개발 구간만) — 관세청 칸 수출 증가율이 다음 한 달 수익을 예측하는가. 2026-10-14 수익률 보기 전 고정.

  .venv/bin/python docs/research/20261014_customs_export/dev_returns.py   → results_dev_returns.md

규칙:
  대상     : mapping_dev.json 통과 · 다른 회사와 칸 공유 없음 · 개발 상관 ≥0.67 (results_mapping.md 판정 메모의 23개)
  신호     : 고른 칸 합(USD) 최근 3개월 / 1년 전 같은 3개월 − 1 (log)
  형성일   : 데이터 월 m → m+1 월 16일 이후 첫 거래일 종가(공표 15일경을 보수적으로), 다음 형성일 종가까지 보유
  포트폴리오: 신호 상위 1/3 동일가중(L) vs 대상 전체 동일가중(B), 형성일 대상 ≥6 종목일 때만
  비용     : factor_test 와 같은 편도(매수 0.185%, 매도 0.385%) × L 의 교체분, B 는 무비용
  기간     : 형성 2017-04 ~ 2023-11 (보유 종료 2023-12, 봉인 2024-01~ 미사용)
  통과(봉인 시험 진행 조건): 비용 후 L−B 평균 t ≥ 1.5 · 순위 IC 평균 > 0 · 연도 7개 중 ≥4 양수
"""
import os, sys, json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import customs_api as c
from feasibility import SIDOS, frame
from step2_map import YEARS

BUY_C, SELL_C = 0.00035 + 0.0015, 0.00035 + 0.0020 + 0.0015
FORM_FIRST, FORM_LAST = pd.Period("2017-04", "M"), pd.Period("2023-11", "M")
MIN_N = 6


# results_mapping.md 판정 메모(10-14, 수익률 보기 전)의 23개 — 칸 공유는 전체 105개 후보 기준(시군구 끝 이름 일치 + HS 겹침)
UNIVERSE_23 = ["씨젠", "해성디에스", "SK하이닉스", "금호석유화학", "서울반도체", "삼성전자", "고영", "동진쎄미켐", "휴스틸",
               "이수페타시스", "바텍", "풍산", "인터로조", "코리아써키트", "클래시스", "ISC", "레이언스", "인바디",
               "금호타이어", "롯데에너지머티리얼즈", "LX세미콘", "휴젤", "리노공업"]


def universe(names=None) -> dict:
    m = json.load(open(os.path.join(HERE, "mapping_dev.json"), encoding="utf-8"))
    return {n: m[n] for n in (names or UNIVERSE_23)}


def monthly_exports(v) -> pd.Series:
    parts = []
    for h in v["hs"]:
        for s in SIDOS:
            for y in YEARS:
                if os.path.exists(os.path.join(c.ROOT, "raw", h, f"{s}_{y}.xml")):     # 수집된 시도만(새 호출 없음)
                    d = frame(h, s, y)
                    if not d.empty:
                        parts.append(d[d.sggNm.isin(v["cells"])])
    d = pd.concat(parts)
    return d.groupby("month").expUsdAmt.sum()


def main():
    U = universe(sys.argv[1].split(",") if len(sys.argv) > 1 else None)
    sig = {}
    for n, v in U.items():
        m = monthly_exports(v)
        m = m.reindex(pd.period_range(m.index.min(), m.index.max(), freq="M"), fill_value=0)
        s3 = m.rolling(3).sum()
        sig[v["code"]] = np.log(s3.where(s3 > 0)).diff(12).replace([np.inf, -np.inf], np.nan)
    S = pd.DataFrame(sig)                                   # 행 = 데이터 월

    p = pd.read_parquet(c.data_vault_path("staging", "kr_equity", "derived", "prices_1d.parquet"),
                        columns=["code", "date", "close", "value"],
                        filters=[("code", "in", ["A" + k for k in S.columns])])
    p["code"] = p.code.astype(str).str[1:]
    px = p.pivot(index="date", columns="code", values="close").sort_index()
    val = p.pivot(index="date", columns="code", values="value").reindex_like(px)
    px = px[px.index <= "2024-01-31"]
    cal = px.index

    def form_day(month: pd.Period):
        d = pd.Timestamp(month.year, month.month, 16)
        return cal[cal.searchsorted(d)]

    rows, prev_L = [], set()
    data_months = [m for m in S.index if FORM_FIRST <= m + 1 <= FORM_LAST]
    for dm in data_months:
        t0, t1 = form_day(dm + 1), form_day(dm + 2)
        s = S.loc[dm].dropna()
        live = [k for k in s.index if np.isfinite(px.at[t0, k]) and val.at[t0, k] > 0 and np.isfinite(px.at[t1, k])]
        s = s[live]
        if len(s) < MIN_N:
            continue
        r = px.loc[t1, s.index] / px.loc[t0, s.index] - 1
        L = set(s.sort_values(ascending=False).index[: max(len(s) // 3, 2)])
        Q1 = set(s.sort_values().index[: max(len(s) // 3, 2)])
        nl = len(L)
        buys = len(L - prev_L) / nl if prev_L else 1.0
        sells = len(prev_L - L) / max(len(prev_L), 1) if prev_L else 0.0
        cost = buys * BUY_C + sells * SELL_C
        rl, rb = r[list(L)].mean(), r.mean()
        rows.append(dict(form=t0.date(), year=t0.year, n=len(s), L=rl, B=rb, Q1=r[list(Q1)].mean(),
                         cost=cost, net=rl - cost - rb, gross=rl - rb,
                         ic=s.rank().corr(r.rank())))
        prev_L = L
    R = pd.DataFrame(rows)

    def t(x):
        x = x.dropna()
        return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 else np.nan

    yr = R.groupby("year").agg(n_months=("net", "size"), names=("n", "mean"), gross=("gross", "mean"),
                              net=("net", "mean"), spread=("L", "mean"), ic=("ic", "mean"))
    yr["spread"] = R.groupby("year").apply(lambda g: (g.L - g.Q1).mean())
    pos = int((yr.net > 0).sum())
    ok = t(R.net) >= 1.5 and R.ic.mean() > 0 and pos >= 4
    L = ["# 3단계 개발 구간 수익률 점검 — 관세청 칸 수출 증가율 → 다음 한 달\n",
         f"대상 {len(U)}종목: {', '.join(U)}\n",
         f"형성 {len(R)}개월 ({R.form.min()} ~ {R.form.max()}), 형성일 평균 종목 {R.n.mean():.1f}, 상위 1/3 평균 {R.n.mean() / 3:.1f}종목.\n",
         "| 지표 | 월평균 | t |", "|---|---|---|",
         f"| L−B 비용 전 | {R.gross.mean():+.2%} | {t(R.gross):+.2f} |",
         f"| L−B 비용 후 | {R.net.mean():+.2%} | {t(R.net):+.2f} |",
         f"| 상위−하위 1/3 (비용 전) | {(R.L - R.Q1).mean():+.2%} | {t(R.L - R.Q1):+.2f} |",
         f"| 순위 IC | {R.ic.mean():+.3f} | {t(R.ic):+.2f} |",
         f"| 비용 | {R.cost.mean():.2%} | |\n",
         "연도별:\n", "| 연도 | 개월 | 종목 | L−B 비용 전 | 비용 후 | 상위−하위 | IC |", "|---|---|---|---|---|---|---|"]
    for y, x in yr.iterrows():
        L.append(f"| {y} | {x.n_months} | {x.names:.1f} | {x.gross:+.2%} | {x.net:+.2%} | {x.spread:+.2%} | {x.ic:+.3f} |")
    L.append(f"\n**판정: {'통과 → 봉인 시험 사전등록 가능' if ok else '불합격 → 봉인 구간 미사용, 종결'}** "
             f"(기준: 비용 후 t≥1.5 [{t(R.net):+.2f}] · IC>0 [{R.ic.mean():+.3f}] · 양수 연도≥4/7 [{pos}/{len(yr)}])")
    out = "\n".join(L) + "\n"
    if len(sys.argv) == 1:
        open(os.path.join(HERE, "results_dev_returns.md"), "w", encoding="utf-8").write(out)
    print(out)


if __name__ == "__main__":
    main()
