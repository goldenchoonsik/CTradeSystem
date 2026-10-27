"""PREREGISTRATION_v2.md 구현 — 코어-위성 오버레이 + 분기 4코호트 (사후 기술 통계).
  .venv/bin/python docs/research/20261024_foreign_accum/foreign_overlay.py"""
import os, sys, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived"); COST = 0.0037
sys.argv = ["x", "track"]
src = open(os.path.join(HERE, "foreign_pick.py"), encoding="utf-8").read().rsplit('\n{"pick"', 1)[0]
exec(compile(src, "foreign_pick", "exec"))                      # P,F,C,pick(t)
px = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"]).pivot(index="date", columns="code", values="close").sort_index()
idx = pd.read_parquet(f"{D}/index_1d.parquet"); kospi = idx[idx.code == "U001"].set_index("date").close.sort_index()
forms = [pd.Period(f"{y}-{mo:02d}", "M") for y in range(2016, 2027) for mo in (3, 6, 9, 12)]
forms = [t for t in forms if t <= pd.Period("2026-09", "M")]

def cohort(t, mode):
    t0 = px.index[px.index.to_period("M") == t].max(); end = min(px.index[px.index.to_period("M") == t + 12].max() if (t + 12) <= px.index.max().to_period("M") else px.index.max(), px.index.max())
    sel, uni = pick(t); uni = list(uni)
    cap = C.loc[t, uni].fillna(0)
    if mode == "V1": w = cap * (1 + pd.Series(uni, index=uni).isin(sel.index).astype(float))
    elif mode == "BM": w = cap
    else: w = pd.Series(0.0, index=uni); w[sel.index] = 1.0                                    # R1
    w = w / w.sum(); seg = px.loc[t0:end, uni].ffill(); v = (seg / seg.iloc[0]).fillna(1.0).mul(w, axis=1).sum(axis=1)
    r = v.pct_change().dropna()
    if mode != "BM": r.iloc[0] = (1 + r.iloc[0]) * (1 - COST) - 1
    return r

def portfolio(mode):
    rs = [cohort(t, mode) for t in forms]
    R = pd.concat(rs, axis=1); return R.mean(axis=1, skipna=True).loc["2016-12-30":], R.loc["2016-12-30":].count(axis=1)

res = {}
for m in ("V1", "R1", "BM"): res[m], n = portfolio(m)
res["코스피"] = kospi.pct_change().reindex(res["BM"].index)
R = pd.DataFrame(res).dropna(); E = (1 + R).cumprod()
print("기간", R.index[0].date(), "~", R.index[-1].date(), "| 활성 코호트 수(최소/최대):", int(n.min()), int(n.max()))
def stats(r):
    s = (1 + r).cumprod(); y = (r.index[-1] - r.index[0]).days / 365.25
    return dict(최종=s.iloc[-1], CAGR=(s.iloc[-1] ** (1 / y) - 1) * 100, 변동성=r.std() * 252 ** .5 * 100, 샤프=r.mean() * 252 / (r.std() * 252 ** .5), MDD=((s / s.cummax()) - 1).min() * 100)
S = pd.DataFrame({k: stats(R[k]) for k in R}).T; print(S.round(2).to_string())
for k in ("V1", "R1"):
    a = R[k] - R["BM"]; rel = (E[k] / E["BM"]); print(f"{k} 대 BM: 추적오차 {a.std()*252**.5*100:.1f}%, 정보비율 {a.mean()*252/(a.std()*252**.5):.2f}, 상대곡선 최대하락 {((rel/rel.cummax())-1).min()*100:.1f}%")
Y = (1 + R).groupby(R.index.year).prod() - 1; Y = Y * 100; Y["V1초과"] = Y.V1 - Y.BM; Y["R1초과"] = Y.R1 - Y.BM; print("\n연도별(%)"); print(Y.round(1).to_string())
x25 = R[R.index.year != 2025]; print("\n2025 제외 CAGR:", {k: round(stats(x25[k])["CAGR"], 1) for k in ("V1", "R1", "BM", "코스피")})
c1 = S.loc["V1", "CAGR"] > S.loc["코스피", "CAGR"]; c2 = S.loc["V1", "MDD"] >= S.loc["코스피", "MDD"] - 2
yrs = Y[(Y.index >= 2017) & (Y.index <= 2025)]; c3 = (yrs.V1초과 > 0).sum() > len(yrs) / 2; c4 = stats(x25["V1"])["CAGR"] > stats(x25["BM"])["CAGR"]
print(f"\n사전 기준: 1)CAGR>코스피 {c1}  2)MDD 2%p 이내 {c2}  3)연도 과반 {c3}({(yrs.V1초과>0).sum()}/{len(yrs)})  4)2025제외>BM {c4}")
print("판정:", "위성 후보로 전진 기록 계속" if all([c1, c2, c3, c4]) else "코어-위성 형태가 EW20 보다 나은 점 없음/기준 미충족")
E.to_csv(os.path.join(HERE, "foreign_overlay_equity.csv"))
