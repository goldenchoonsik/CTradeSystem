"""A(동행 분해)·B(전환점 표)·D(사건 반응) — 판정 없는 기술 분석 (PREREGISTRATION.md 3절).

출력: results_A.md, results_B.md, results_D.md (이 폴더)
  .venv/bin/python -I docs/research/20261022_samsung_single/analysis_ABD.py
"""
import glob, json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from shared.data_library.paths import data_vault_path  # noqa: E402

SC = data_vault_path("staging", "samsung_cycle")
D = pd.read_parquet(os.path.join(SC, "derived", "daily.parquet"))
M = pd.read_parquet(os.path.join(SC, "derived", "monthly.parquet"))
W = pd.read_parquet(os.path.join(SC, "derived", "weekly.parquet"))


def ols(y, X):
    X = np.column_stack([np.ones(len(X)), X])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    e = y - X @ b
    r2 = 1 - e.var() / y.var()
    return b, r2


# ---------------------------------------------------------------- A
def part_A():
    L = ["# A — 삼성 수익률은 무엇과 같이 움직이나 (동행 분해, 예측 아님)", "",
         "로그수익률. SOX·마이크론은 달러 지수/주가, 원/달러는 별도 요인(원화 환산 효과 포함). 미 10년물은 변화(%p). "
         "한국 월말 종가 vs 직전 미국 거래일 종가(하루 어긋남 포함). 코스피는 삼성을 포함하므로 '시장 몫' 참고용.", ""]
    for name, df, per_year in [("월", M, 12), ("주", W, 52)]:
        f = pd.DataFrame({
            "sam": np.log(df.sam).diff(), "sox": np.log(df.sox).diff(), "mu": np.log(df.mu).diff(),
            "fx": np.log(df.usdkrw).diff(), "y10": df.us10y.diff(), "kospi": np.log(df.kospi).diff()})
        if name == "월":
            f.index = df["date"]
        f = f.dropna()
        f = f[f.index <= "2026-09-11"]
        L += [f"## {name}봉", "", "| 시기 | n | SOX β | 원/달러 β | 미10년 β(%p당) | R² (SOX+환율+금리) | R² SOX만 | R² 마이크론만 | R² 환율만 | R² 코스피만 | 연변동성 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for a, b in [("1995", "2004"), ("2005", "2014"), ("2015", "2026"), ("1995", "2026")]:
            g = f.loc[a:b]
            bb, r2 = ols(g.sam.values, g[["sox", "fx", "y10"]].values)
            r2s = {c: ols(g.sam.values, g[[c]].values)[1] for c in ["sox", "mu", "fx", "kospi"]}
            L.append(f"| {a}~{b} | {len(g)} | {bb[1]:.2f} | {bb[2]:.2f} | {bb[3] * 100:+.1f}% | {r2:.2f} | {r2s['sox']:.2f} | {r2s['mu']:.2f} | {r2s['fx']:.2f} | {r2s['kospi']:.2f} | {g.sam.std() * np.sqrt(per_year) * 100:.0f}% |")
        L.append("")
        if name == "월":
            # 5년 이동창 SOX R² — 연말 표본
            roll = []
            for end in pd.date_range("2000-12-31", "2026-08-31", freq="YE"):
                g = f.loc[end - pd.DateOffset(years=5):end]
                if len(g) >= 50:
                    roll.append((end.year, ols(g.sam.values, g[["sox"]].values)[1], ols(g.sam.values, g[["sox", "fx", "y10"]].values)[1],
                                 ols(g.sam.values, g[["sox"]].values)[0][1]))
            L += ["5년 이동창(연말 기준): 연도 SOX R² / 3요인 R² / SOX β", "", " · ".join(f"{y} {a:.2f}/{b:.2f}/{c:.2f}" for y, a, b, c in roll), ""]
    open(os.path.join(HERE, "results_A.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


# ---------------------------------------------------------------- B
def turning_points(s: pd.Series, win=12, min_move=None):
    """월 시계열의 고점·저점: 앞뒤 win 개월 안 최고/최저. 고점·저점 교대, min_move 면 고점→저점 하락률 조건."""
    s = s.dropna()
    v = s.values
    pts = []
    for i in range(len(v)):
        lo, hi = max(0, i - win), min(len(v), i + win + 1)
        if v[i] == v[lo:hi].max():
            pts.append((s.index[i], "P", v[i]))
        elif v[i] == v[lo:hi].min():
            pts.append((s.index[i], "T", v[i]))
    out = []
    for p in pts:   # 교대 강제: 같은 종류 연속이면 더 극단적인 것
        if out and out[-1][1] == p[1]:
            if (p[1] == "P" and p[2] > out[-1][2]) or (p[1] == "T" and p[2] < out[-1][2]):
                out[-1] = p
            continue
        out.append(p)
    if min_move:
        keep = []
        for i, p in enumerate(out):
            if p[1] == "P":
                nxt = next((q for q in out[i + 1:] if q[1] == "T"), None)
                if nxt and nxt[2] / p[2] - 1 <= -min_move:
                    keep += [p, nxt]
        out = sorted(set(keep), key=lambda x: x[0])
    return out


def part_B():
    sam = M.sam.loc["1994-01":]
    tp = turning_points(sam, 12, 0.25)
    ec = pd.read_csv(os.path.join(SC, "ecos_monthly.csv"), dtype={"ym": str})
    ec.index = pd.PeriodIndex(pd.to_datetime(ec.pop("ym"), format="%Y%m"), freq="M")
    ks = pd.read_csv(os.path.join(SC, "kosis_semis_monthly.csv"), dtype={"ym": str})
    ks.index = pd.PeriodIndex(pd.to_datetime(ks.pop("ym"), format="%Y%m"), freq="M")
    cu = pd.read_csv(os.path.join(SC, "customs_854232_monthly.csv"), dtype={"ym": str})
    cu.index = pd.PeriodIndex(pd.to_datetime(cu.pop("ym"), format="%Y%m"), freq="M")
    mu = pd.read_csv(os.path.join(SC, "derived", "micron_quarterly.csv"), parse_dates=["period_end"])
    mu_gm = pd.Series(mu.gm.values, index=pd.PeriodIndex(mu.period_end, freq="M"))   # 분기 → 월 선형 보간
    mu_gm = mu_gm[~mu_gm.index.duplicated()].reindex(pd.period_range(mu_gm.index.min(), mu_gm.index.max(), freq="M")).interpolate()
    yoy = lambda x: np.log(x / x.shift(12))
    s3 = cu.exp_usd_k.astype(float).rolling(3).sum()
    ind = {
        "D램 수출물가 전년비": (yoy(ec.xpi_dram_usd).rolling(3).mean(), 1),
        "플래시 수출물가 전년비": (yoy(ec.xpi_flash_usd).rolling(3).mean(), 1),
        "반도체 출하−재고 전년비": ((yoy(ks.ship_raw) - yoy(ks.inv_raw)).rolling(3).mean(), 2),
        "반도체 재고 전년비(역)": (-yoy(ks.inv_raw).rolling(3).mean(), 2),
        "메모리 수출 전년비": (np.log(s3 / s3.shift(12)), 1),
        "마이크론 매출총이익률": (mu_gm, 1),
        "SOX(달러)": (M.sox.loc["1994-06":], 0),
        "미 10년물(역)": (-M.us10y, 0),
        "원/달러(역)": (-M.usdkrw, 0),
    }
    L = ["# B — 삼성 고점·저점 vs 지표 전환점 (선후 개월)", "",
         "삼성 월말 종가(수정, 1994~2026-09-11) 앞뒤 12개월 극값, 고점→저점 −25% 이상 국면만. 지표 전환점은 같은 12개월 극값 규칙(3개월 평균, 전년비). "
         "값 = 지표 전환월 − 삼성 전환월(개월). **음수 = 지표가 먼저**, 양수 = 삼성이 먼저. ±18개월 안 가장 가까운 같은 종류 전환점. "
         "괄호 = 발표 시차(공표까지 걸리는 달 수)를 더한 '알 수 있게 된 시점' 기준.", ""]
    hdr = "| 삼성 | 날짜 | 종가 | " + " | ".join(ind) + " |"
    L += [hdr, "|" + "---|" * (3 + len(ind))]
    stats = {k: [] for k in ind}
    tps = {k: turning_points(v.dropna(), 12) for k, (v, _) in ind.items()}
    for date, kind, val in tp:
        cells = []
        for k, (v, lag) in ind.items():
            cand = [(d, t) for d, t, _ in tps[k] if t == kind and abs((d - date).n) <= 18]
            if not cand or v.dropna().index.min() > date - 18:
                cells.append("–")
                continue
            d = min(cand, key=lambda x: abs((x[0] - date).n))[0]
            lead = (d - date).n
            stats[k].append((kind, lead, lead + lag))
            cells.append(f"{lead:+d} ({lead + lag:+d})" if lag else f"{lead:+d}")
        L.append(f"| {'고점' if kind == 'P' else '저점'} | {date} | {val:,.0f} | " + " | ".join(cells) + " |")
    L += ["", "## 요약 — 지표가 삼성보다 먼저 꺾인 비율(발표 시차 포함 기준), 선후 중앙값", "",
          "| 지표 | 대응 전환점 수 | 지표가 먼저(알게 된 시점 기준) | 중앙값(데이터월) | 중앙값(알게 된 시점) |", "|---|---|---|---|---|"]
    for k, xs in stats.items():
        if not xs:
            continue
        a = np.array([x[1] for x in xs])
        b = np.array([x[2] for x in xs])
        L.append(f"| {k} | {len(xs)} | {(b < 0).sum()}/{len(xs)} | {np.median(a):+.0f} | {np.median(b):+.0f} |")
    nP = sum(1 for x in tp if x[1] == "P")
    L += ["", f"삼성 국면 수: 고점 {nP}, 저점 {len(tp) - nP}."]
    open(os.path.join(HERE, "results_B.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


# ---------------------------------------------------------------- D
def event_returns(dates, label):
    d = D[D.index <= "2026-09-11"]
    idx = d.index
    r = np.log(d.sam).diff()
    rk = np.log(d.kospi).diff()
    out = []
    for e in dates:
        i = idx.searchsorted(e)
        if i <= 0 or i + 20 >= len(idx):
            continue
        r0 = r.iloc[i]
        x0 = r0 - rk.iloc[i]
        r20 = np.log(d.sam.iloc[i + 20] / d.sam.iloc[i])
        x20 = r20 - np.log(d.kospi.iloc[i + 20] / d.kospi.iloc[i])
        out.append(dict(event=label, day=idx[i], r0=r0, x0=x0, r20=r20, x20=x20))
    return pd.DataFrame(out)


def part_D():
    L = ["# D — 사건 당일 반응 vs 이후 20거래일", ""]
    d = D[(D.index >= "2004-01-01") & (D.index <= "2026-09-11")]
    absr = np.log(d.sam).diff().abs()
    # 1) 잠정실적: 분기 첫 공시(정정 제외, 1·4·7·10월 1~15일)
    rows = []
    for f in glob.glob(data_vault_path("staging", "kr_equity", "raw", "dart", "list", "*_Y_*.json")):
        for r in json.load(open(f))["response"].get("list", []):
            if r["stock_code"] == "005930" and "잠정" in r["report_nm"] and "정정" not in r["report_nm"]:
                rows.append(pd.Timestamp(r["rcept_dt"]))
    pre = sorted({x for x in rows if x.month in (1, 4, 7, 10) and x.day <= 15})
    ev1 = event_returns(pre, "삼성 잠정실적")
    # 영업이익 전년비(같은 분기, DART 확정치로 근사) → 방향
    fq = pd.read_parquet(data_vault_path("staging", "kr_equity", "derived", "fundamentals_q.parquet"))
    fq = fq[fq.stock_code == "005930"].sort_values("period_end")
    fq["op_yoy"] = fq.op_income / fq.op_income.shift(4) - 1
    fq["ann"] = pd.to_datetime(fq.period_end) + pd.offsets.MonthBegin(1)
    ev1["op_yoy"] = [fq.loc[(fq.ann.dt.to_period("M") == pd.Timestamp(x).to_period("M")), "op_yoy"].mean() for x in ev1.day]
    # 2) 마이크론 실적: 8-K 2.02 접수(UTC) → 한국 종가(06:30 UTC) 가 그 뒤인 첫 거래일
    mu = pd.read_csv(os.path.join(SC, "mu_earnings_8k.csv"), parse_dates=["accepted"])
    kdays = d.index
    mud = []
    mur = []
    yah = pd.read_csv(os.path.join(SC, "yahoo_MU.csv"), parse_dates=["date"]).set_index("date").adjclose
    for a in mu.accepted:
        a = a.tz_localize(None) if a.tzinfo else a
        k = kdays[kdays + pd.Timedelta(hours=6, minutes=30) > a]
        if len(k):
            mud.append(k[0])
            # 마이크론 자체 반응: 접수 뒤 첫 미국 종가(21:00 UTC 근사) / 직전 종가
            ui = yah.index.searchsorted(a - pd.Timedelta(hours=21), side="right")
            mur.append(np.log(yah.iloc[ui] / yah.iloc[ui - 1]) if 0 < ui < len(yah) else np.nan)
    ev2 = event_returns(mud, "마이크론 실적")
    ev2["mu_r0"] = pd.Series(mur[:len(ev2)])
    # 3) 자사주 취득결정
    bb = json.load(open(data_vault_path("staging", "kr_equity", "raw", "dart", "events", "tsstkAqDecsn", "20261013", "00126380.json")))
    bb = bb.get("response", bb)["list"]
    bdates = sorted(pd.Timestamp(x["rcept_no"][:8]) for x in bb)
    ev3 = event_returns(bdates, "자사주 취득결정")

    L += [f"평상시 일간 |수익| 평균(2004~): {absr.mean() * 100:.2f}%", "",
          "| 사건 | n | 당일 |수익| 평균 | 당일 평균 | 당일 초과(코스피) | 이후 20일 평균 | 이후 20일 초과 | 당일↔이후20일 상관 | 당일 부호 = 이후 부호 비율 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for ev in (ev1, ev2, ev3):
        if ev.empty:
            continue
        L.append(f"| {ev.event.iloc[0]} | {len(ev)} | {ev.r0.abs().mean() * 100:.2f}% | {ev.r0.mean() * 100:+.2f}% | {ev.x0.mean() * 100:+.2f}% | "
                 f"{ev.r20.mean() * 100:+.2f}% | {ev.x20.mean() * 100:+.2f}% | {ev.r0.corr(ev.r20):+.2f} | {(np.sign(ev.r0) == np.sign(ev.r20)).mean() * 100:.0f}% |")
    e = ev1.dropna(subset=["op_yoy"])
    if len(e):
        up, dn = e[e.op_yoy > 0], e[e.op_yoy <= 0]
        L += ["", f"잠정실적 — 영업이익 전년비 증가 분기 {len(up)}개: 당일 {up.r0.mean() * 100:+.2f}%, 이후 20일 {up.r20.mean() * 100:+.2f}% / "
              f"감소 분기 {len(dn)}개: 당일 {dn.r0.mean() * 100:+.2f}%, 이후 20일 {dn.r20.mean() * 100:+.2f}% (2016~, DART 확정치 기준 방향)"]
    e2 = ev2.dropna(subset=["mu_r0"])
    if len(e2):
        L += [f"마이크론 실적 — 마이크론 자체 반응 ↔ 삼성 다음 거래일 반응 상관 {e2.mu_r0.corr(e2.r0):+.2f} (n={len(e2)}), "
              f"↔ 삼성 이후 20일 {e2.mu_r0.corr(e2.r20):+.2f}; 마이크론 +5% 이상 {int((e2.mu_r0 > 0.05).sum())}건: 삼성 당일 {e2[e2.mu_r0 > 0.05].r0.mean() * 100:+.2f}%·이후 20일 {e2[e2.mu_r0 > 0.05].r20.mean() * 100:+.2f}% / "
              f"−5% 이하 {int((e2.mu_r0 < -0.05).sum())}건: 당일 {e2[e2.mu_r0 < -0.05].r0.mean() * 100:+.2f}%·이후 20일 {e2[e2.mu_r0 < -0.05].r20.mean() * 100:+.2f}%"]
    L += ["", "## 자사주 취득결정 사례 (이후 20/60/120거래일)", "", "| 공시일 | 목적 | 금액(조) | 20일 | 60일 | 120일 |", "|---|---|---|---|---|---|"]
    dd = D[D.index <= "2026-09-11"]
    for x in sorted(bb, key=lambda x: x["rcept_no"]):
        t = pd.Timestamp(x["rcept_no"][:8])
        i = dd.index.searchsorted(t)
        cells = []
        for h in (20, 60, 120):
            cells.append(f"{(dd.sam.iloc[i + h] / dd.sam.iloc[i - 1] - 1) * 100:+.1f}%" if i + h < len(dd) else "–")
        amt = float(x["aqpln_prc_ostk"].replace(",", "")) / 1e12
        L.append(f"| {t.date()} | {x['aq_pp']} | {amt:.1f} | " + " | ".join(cells) + " |")
    open(os.path.join(HERE, "results_D.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))
    pd.concat([ev1, ev2, ev3]).to_csv(os.path.join(HERE, "results_D_events.csv"), index=False)


if __name__ == "__main__":
    for p in sys.argv[1:] or ["A", "B", "D"]:
        globals()[f"part_{p}"]()
