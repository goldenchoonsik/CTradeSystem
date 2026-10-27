"""앞서 정리한 대선 주기 공통 특징 각각이 6구간(4주기씩)에서 유지되는지. S&P500 월말 종가 가격수익.
  .venv/bin/python docs/research/20261026_market_anomalies/cycle_claims_by_period.py"""
import os, json, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
j = json.load(open(f"{ROOT}/DataVault/staging/us_valuation/raw/yahoo/GSPC_20261023.json"))["chart"]["result"][0]
P = pd.Series(j["indicators"]["quote"][0]["close"], index=pd.to_datetime(j["timestamp"], unit="s").normalize()).dropna(); P = P[~P.index.duplicated()]
M = P.resample("ME").last(); M = M[M.index <= "2026-09-30"]; r = M.pct_change().dropna(); r.index = r.index.to_period("M")
S = list(range(1929, 2025, 4)); party = dict(zip(S, "RDDDDDRRDDRRDRRRDDRRDDRD"))
C = pd.DataFrame({y: r[f"{y}-01":f"{y+3}-12"].values for y in S}, index=range(1, 49)).T
PER = [(1929, 1944), (1945, 1960), (1961, 1976), (1977, 1992), (1993, 2008), (2009, 2024)]; names = [f"{a}~{b}" for a, b in PER]
comp = lambda X, a, b: (1 + X.iloc[:, a:b]).prod(axis=1) - 1
def per(f):
    out = []
    for a, b in PER: X = C.loc[[y for y in S if a <= y <= b - 3]]; out.append(f(X))
    return out
def show(title, f, fmt):
    print(f"\n[{title}]"); print("  " + " | ".join(f"{n}: {fmt(v)}" for n, v in zip(names, per(f))))
allm = C.values.mean(); base3 = (1 + allm) ** 3 - 1; print(f"전체 평소 분기 {base3*100:.1f}%, 평소 월 {allm*100:.2f}%")
q = lambda X, k: comp(X, (k-1)*3, k*3)
for k, nm in ((8, "2년차 4분기(강세)"), (9, "3년차 1분기(강세)"), (10, "3년차 2분기(강세)"), (6, "2년차 2분기(약세)"), (7, "2년차 3분기(약세)"), (1, "1년차 1분기(약)")):
    show(nm + " 평균% (양수/주기)", lambda X, k=k: (q(X, k).mean() * 100, (q(X, k) > 0).sum(), q(X, k).median() * 100), lambda v: f"{v[0]:5.1f}% (중앙 {v[2]:5.1f}, {v[1]}/4)")
def trough(X):
    cum = (1 + X).cumprod(axis=1); cum.columns = range(1, 49); lo = (cum.idxmin(axis=1) - 1) // 12 + 1; hi = (cum.idxmax(axis=1) - 1) // 12 + 1
    return ((lo <= 2).sum(), (hi >= 3).sum(), (hi == 4).sum(), ((cum.idxmin(axis=1)[lo == 2] - 1) % 12 + 1).tolist())
show("저점이 1·2년차 / 고점이 3·4년차 / 고점이 4년차 / 2년차 저점 달", trough, lambda v: f"{v[0]}/4, {v[1]}/4, {v[2]}/4, {v[3]}")
def halves(X):
    a, b = comp(X, 0, 24), comp(X, 24, 48); return (a.mean() * 100, b.mean() * 100, (b > a).sum())
show("전반 2년 vs 후반 2년 평균% (후반이 큰 주기)", halves, lambda v: f"{v[0]:5.1f} vs {v[1]:5.1f} ({v[2]}/4)")
show("연차별 평균% 1/2/3/4", lambda X: [comp(X, i*12, i*12+12).mean() * 100 for i in range(4)], lambda v: "/".join(f"{x:5.1f}" for x in v))
show("3년차가 1·2·4년차 평균보다 큰 주기", lambda X: (comp(X, 24, 36) > (comp(X, 0, 12) + comp(X, 12, 24) + comp(X, 36, 48)) / 3).sum(), lambda v: f"{v}/4")
print("\n[정당별(3개 시대로 합침: 8주기씩)] 1년차/2년차/3년차/4년차 평균%, 4년 누적 평균%")
for a, b in ((1929, 1960), (1961, 1992), (1993, 2024)):
    for pc, pn in (("D", "민주"), ("R", "공화")):
        ys = [y for y in S if a <= y <= b - 3 and party[y] == pc]; X = C.loc[ys]
        print(f"  {a}~{b} {pn} {len(ys)}주기: " + "/".join(f"{comp(X, i*12, i*12+12).mean()*100:5.1f}" for i in range(4)) + f" | 4년 {comp(X, 0, 48).mean()*100:.0f}%")
