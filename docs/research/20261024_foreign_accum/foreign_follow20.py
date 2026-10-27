"""PREREGISTRATION_v4.md 구현 — 시총 상위 20, 외국인 매수 때 같이 사고 매도 때 같이 파는 추종 (사후 기술 통계).
  .venv/bin/python docs/research/20261024_foreign_accum/foreign_follow20.py"""
import os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived"); TC = 0.00185; N = 20
e = pd.read_parquet(f"{D}/daily_ext.parquet", columns=["code", "date", "foreign_hold_qty", "market_cap"])
Q = e.pivot(index="date", columns="code", values="foreign_hold_qty").sort_index()
C = e.pivot(index="date", columns="code", values="market_cap").sort_index()
px = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"]).pivot(index="date", columns="code", values="close").sort_index()
idx = pd.read_parquet(f"{D}/index_1d.parquet"); kospi = idx[idx.code == "U001"].set_index("date").close.sort_index()
days = px.index[px.index >= "2013-10-01"]; Q, C, px = Q.reindex(days), C.reindex(days).ffill(limit=5), px.reindex(days)
ret = px.pct_change(fill_method=None)
# 월말 상위 20 → 다음 달 적용
me = pd.Series(days, index=days).groupby(days.to_period("M")).max()
member = pd.DataFrame(False, index=days, columns=px.columns)
for i in range(len(me) - 1):
    top = C.loc[me.iloc[i]].dropna().nlargest(N).index; member.loc[me.iloc[i + 1] if False else slice(me.iloc[i], me.iloc[i + 1]), top] = True
member = member.shift(1).fillna(False).astype(bool)      # 월말 다음 거래일부터
def run(pos):
    pos = pos.shift(2).fillna(0.0) / N                    # 체결 지연: 포지션(d)=신호(d−2)
    r = (pos * ret.fillna(0.0)).sum(axis=1)
    cost = pos.diff().abs().sum(axis=1) * TC
    return (r - cost).loc["2014-01-02":], pos.sum(axis=1).loc["2014-01-02":]
A, expA = run(member.astype(float))
w = C.where(member).shift(2); w = w.div(w.sum(axis=1), axis=0)
B = (w * ret.fillna(0.0)).sum(axis=1).loc["2014-01-02":]
res = {"A": A, "B": B, "코스피": kospi.pct_change().reindex(A.index)}; expo = {}
for W in (5, 20, 60):
    sig = (Q.diff(W) > 0) & member.shift(0)
    res[f"W{W}"], expo[W] = run(sig.astype(float))
    res[f"A_blend{W}"] = A * expo[W].mean()
R = pd.DataFrame(res).dropna(how="all").fillna(0.0)
def stats(r):
    s = (1 + r).cumprod(); y = (r.index[-1] - r.index[0]).days / 365.25
    return dict(CAGR=(s.iloc[-1] ** (1 / y) - 1) * 100, 변동성=r.std() * 252 ** .5 * 100, 샤프=r.mean() * 252 / (r.std() * 252 ** .5), MDD=((s / s.cummax()) - 1).min() * 100)
print("기간", R.index[0].date(), "~", R.index[-1].date(), "| 평균 투자 비중", {W: f"{expo[W].mean()*100:.0f}%" for W in expo})
S = pd.DataFrame({k: stats(R[k]) for k in R}).T; print(S.round(2).to_string())
x25 = R[R.index.year != 2025]; S25 = pd.DataFrame({k: stats(x25[k]) for k in R}).T
print("\n2025 제외"); print(S25[["CAGR", "샤프", "MDD"]].round(2).to_string())
turn = {W: (run(((Q.diff(W) > 0) & member).astype(float))[0] * 0).index.size for W in ()}
for W in (5, 20, 60):
    k = f"W{W}"; bl = f"A_blend{W}"
    c1 = S.loc[k, "샤프"] > S.loc["A", "샤프"]; c2 = S.loc[k, "CAGR"] > S.loc[bl, "CAGR"]
    c3 = S25.loc[k, "샤프"] > S25.loc["A", "샤프"] and S25.loc[k, "CAGR"] > S25.loc[bl, "CAGR"]; c4 = S.loc[k, "MDD"] >= S.loc["A", "MDD"]
    print(f"W={W}: 기준 1)샤프>A {c1}  2)CAGR>덜투자 {c2}  3)2025제외 {c3}  4)MDD {c4} → {'전진 기록 후보' if all([c1,c2,c3,c4]) else '기준 미충족'}")
Y = ((1 + R[["A", "B", "코스피", "W5", "W20", "W60"]]).groupby(R.index.year).prod() - 1) * 100; print("\n연도별(%)"); print(Y.round(1).to_string())
R.to_csv(os.path.join(HERE, "foreign_follow20_returns.csv"))
