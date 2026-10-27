"""PREREGISTRATION_v3.md 구현 — 외국인 지분 증가 상위 5종목 분기 매수 (사후 기술 통계).
  .venv/bin/python docs/research/20261024_foreign_accum/foreign_top5.py"""
import os, sys, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived"); COST = 0.0037; K = 5
sys.argv = ["x", "track"]
src = open(os.path.join(HERE, "foreign_pick.py"), encoding="utf-8").read().rsplit('\n{"pick"', 1)[0]
exec(compile(src, "foreign_pick", "exec"))                      # P,F,C,NAME
px = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"]).pivot(index="date", columns="code", values="close").sort_index()
idx = pd.read_parquet(f"{D}/index_1d.parquet"); kospi = idx[idx.code == "U001"].set_index("date").close.sort_index()
LASTD = px.index.max(); forms = [t for t in (pd.Period(f"{y}-{mo:02d}", "M") for y in range(2016, 2027) for mo in (3, 6, 9, 12)) if t <= pd.Period("2026-09", "M")]
eom = lambda t: px.index[px.index.to_period("M") == t].max()

def select(t, neutral):
    uni = C.loc[t].dropna().nlargest(200).index
    df = pd.DataFrame({"dp": F.loc[t, uni] - F.loc[t - 36, uni], "past": P.loc[t, uni] / P.loc[t - 36, uni] - 1}).dropna()
    if neutral:
        x, y = df.past.rank(pct=True).values, df.dp.rank(pct=True).values
        df["s"] = y - np.polyval(np.polyfit(x, y, 1), x)
    else: df["s"] = df.dp
    return df.nlargest(K, "s").index, uni

def seg_ret(codes, w, t0, end, cost):
    seg = px.loc[t0:end, list(codes)].ffill(); v = (seg / seg.iloc[0]).fillna(1.0).mul(w, axis=1).sum(axis=1)
    r = v.pct_change().dropna()
    if cost: r.iloc[0] = (1 + r.iloc[0]) * (1 - COST) - 1
    return r

def series(neutral, hold):
    segs = []
    for t in forms:
        t0 = eom(t); end = min(eom(t + hold) if (t + hold) <= LASTD.to_period("M") else LASTD, LASTD)
        if hold == 12 and t + hold > LASTD.to_period("M"): end = LASTD
        sel, _ = select(t, neutral); segs.append(seg_ret(sel, pd.Series(1 / K, index=sel), t0, end, True))
    R = pd.concat(segs, axis=1) if hold == 12 else None
    if hold == 12: return R.mean(axis=1, skipna=True)
    return pd.concat(segs).sort_index().groupby(level=0).first()

def bm():
    segs = []
    for i, t in enumerate(forms):
        t0 = eom(t); end = eom(t + 3) if (t + 3) <= LASTD.to_period("M") else LASTD
        _, uni = select(t, False); uni = list(uni); w = C.loc[t, uni].fillna(0); segs.append(seg_ret(uni, w / w.sum(), t0, end, False))
    return pd.concat(segs).sort_index().groupby(level=0).first()

res = {"U3": series(False, 3), "U12": series(False, 12), "N3": series(True, 3), "N12": series(True, 12), "BM": bm()}
res["코스피"] = kospi.pct_change()
R = pd.DataFrame(res).loc["2016-12-30":].dropna(); E = (1 + R).cumprod()
print("기간", R.index[0].date(), "~", R.index[-1].date())
def stats(r):
    s = (1 + r).cumprod(); y = (r.index[-1] - r.index[0]).days / 365.25
    return dict(최종=s.iloc[-1], CAGR=(s.iloc[-1] ** (1 / y) - 1) * 100, 변동성=r.std() * 252 ** .5 * 100, 샤프=r.mean() * 252 / (r.std() * 252 ** .5), MDD=((s / s.cummax()) - 1).min() * 100)
S = pd.DataFrame({k: stats(R[k]) for k in R}).T; print(S.round(2).to_string())
Y = ((1 + R).groupby(R.index.year).prod() - 1) * 100
x25 = R[R.index.year != 2025]; s25 = {k: stats(x25[k])["CAGR"] for k in R}
print("\n2025 제외 CAGR:", {k: round(v, 1) for k, v in s25.items()})
Q = (1 + R).groupby(R.index.to_period("Q")).prod() - 1
for k in ("U3", "U12", "N3", "N12"):
    a = R[k] - R["BM"]; rel = E[k] / E["BM"]; yrs = Y[(Y.index >= 2017) & (Y.index <= 2025)]
    c1 = S.loc[k, "CAGR"] > S.loc["BM", "CAGR"] and S.loc[k, "CAGR"] > S.loc["코스피", "CAGR"]; c2 = s25[k] > s25["BM"]
    n = ((yrs[k] - yrs["BM"]) > 0).sum(); c3 = n >= 5; c4 = S.loc[k, "MDD"] >= S.loc["BM", "MDD"] - 10
    print(f"{k}: 추적오차 {a.std()*252**.5*100:.1f}% IR {a.mean()*252/(a.std()*252**.5):.2f} 상대최대하락 {((rel/rel.cummax())-1).min()*100:.1f}% 분기승률 {(Q[k]>Q['BM']).mean()*100:.0f}% | 기준 1){c1} 2){c2} 3){c3}({n}/9) 4){c4} → {'전진 기록 계속' if all([c1,c2,c3,c4]) else '기준 미충족'}")
print("\n연도별(%)"); print(Y.round(1).to_string())
t = forms[-1]
for neutral, nm in ((False, "U"), (True, "N")):
    sel, _ = select(t, neutral); print(f"\n{nm} 최근 형성({t}) 상위 5:", [NAME.get(c) for c in sel])
E.to_csv(os.path.join(HERE, "foreign_top5_equity.csv"))
