"""[탐색·의도적 과적합] 2020~2024 에서 외국인 보유수량 증감 규칙의 최대 수익 조건 탐색 → 2014~19·2025~26 에서 붕괴 정도 측정.
  규칙: 시총 상위 N(월 갱신) 종목마다 x = W거래일 외국인 보유수량 변화(상장주식수 대비 %p)·방향 dir(+1 추종/−1 역추세).
        z=dir*x > hi 이면 보유 진입, z <= lo 이면 현금(그 사이는 직전 상태 유지). 체결 지연 2일, 편도 0.185%.
  .venv/bin/python docs/research/20261024_foreign_accum/foreign_overfit.py"""
import os, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived"); TC = 0.00185
e = pd.read_parquet(f"{D}/daily_ext.parquet", columns=["code", "date", "foreign_hold_qty", "market_cap", "shares_listed"])
Q = e.pivot(index="date", columns="code", values="foreign_hold_qty").sort_index()
SH = e.pivot(index="date", columns="code", values="shares_listed").sort_index()
C = e.pivot(index="date", columns="code", values="market_cap").sort_index()
px = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"]).pivot(index="date", columns="code", values="close").sort_index()
days = px.index[px.index >= "2013-10-01"]
Q, SH, C, px = [f.reindex(days) for f in (Q, SH, C, px)]; C = C.ffill(limit=5); SH = SH.ffill(limit=5)
ret = px.pct_change(fill_method=None).fillna(0.0).values
me = pd.Series(days, index=days).groupby(days.to_period("M")).max()
def members(N):
    m = pd.DataFrame(False, index=days, columns=px.columns)
    for i in range(len(me) - 1):
        m.loc[me.iloc[i]:me.iloc[i + 1], C.loc[me.iloc[i]].dropna().nlargest(N).index] = True
    return m.shift(1).fillna(False).values
NS, WS = (20, 50, 100), (3, 5, 10, 20, 40, 60)
HI, LO = (0.0, 0.05, 0.1, 0.2, 0.4), (-0.4, -0.2, -0.1, -0.05, 0.0)
M = {N: members(N) for N in NS}
X = {W: (Q.diff(W) / SH * 100).values for W in WS}
cols = np.where(M[100].any(axis=0))[0]                     # 어느 시점이든 상위 100에 든 적 있는 종목만(상위 20·50은 부분집합)
M = {N: m[:, cols] for N, m in M.items()}; X = {W: x[:, cols] for W, x in X.items()}; ret = ret[:, cols]; print("대상 종목", len(cols), flush=True)
dates = days; IS = (dates >= "2020-01-01") & (dates <= "2024-12-31")
PER = {"2014-2019": (dates >= "2014-01-02") & (dates <= "2019-12-31"), "2020-2024(학습)": IS, "2025-2026": dates >= "2025-01-01"}

def daily(N, W, hi, lo, d, Xw):
    z = d * Xw; s = np.full(z.shape, np.nan); s[z > hi] = 1.0; s[z <= lo] = 0.0
    s = pd.DataFrame(s).ffill().fillna(0.0).values * M[N]
    pos = np.vstack([np.zeros((2, s.shape[1])), s[:-2]]) / N
    cost = np.abs(np.diff(pos, axis=0, prepend=0)).sum(axis=1) * TC
    return (pos * ret).sum(axis=1) - cost, pos.sum(axis=1)

def cagr(r, mask):
    r = r[mask]; s = np.prod(1 + r); y = mask.sum() / 252; return (s ** (1 / y) - 1) * 100
def sharpe(r, mask): r = r[mask]; return r.mean() * 252 / (r.std() * 252 ** .5) if r.std() > 0 else 0

def search(Xd):
    rows = []
    for N, W, (hi, lo), d in itertools.product(NS, WS, [(h, l) for h in HI for l in LO if l <= h], (1, -1)):
        r, ex = daily(N, W, hi, lo, d, Xd[W]); rows.append(dict(N=N, W=W, hi=hi, lo=lo, dir=d, IS_CAGR=cagr(r, IS), IS_샤프=sharpe(r, IS), 투자비중=ex[IS].mean() * 100, r=r))
    return pd.DataFrame(rows)

T = search(X); print("조합 수", len(T), flush=True)
buy = {N: ((M[N] / N) * ret).sum(axis=1) for N in NS}
print("\n[참고] 상시 보유(동일가중) CAGR:", {f"상위{N}": {k: round(cagr(buy[N], v), 1) for k, v in PER.items()} for N in NS})
top = T.nlargest(10, "IS_CAGR")
print("\n[학습 구간 2020-2024 CAGR 상위 10 → 구간별 CAGR(%)]")
for _, t in top.iterrows():
    print(f"N{t.N:>3} W{t.W:>2} 진입>{t.hi:+.2f} 이탈<={t.lo:+.2f} {'추종' if t.dir == 1 else '역추세'} | 투자비중 {t.투자비중:.0f}% | " + "  ".join(f"{k} {cagr(t.r, v):6.1f}" for k, v in PER.items()) + f" | IS 샤프 {t.IS_샤프:.2f}")
T["OOS1"] = [cagr(r, PER["2014-2019"]) for r in T.r]; T["OOS2"] = [cagr(r, PER["2025-2026"]) for r in T.r]
t50 = T.nlargest(50, "IS_CAGR")
print(f"\n학습 상위 50 평균: 학습 {t50.IS_CAGR.mean():.1f}% → 2014-19 {t50.OOS1.mean():.1f}% · 2025-26 {t50.OOS2.mean():.1f}% | 2014-19 에서 음수 {int((t50.OOS1<0).sum())}/50, 2025-26 음수 {int((t50.OOS2<0).sum())}/50")
print(f"전체 {len(T)}개 평균: 학습 {T.IS_CAGR.mean():.1f}% · 2014-19 {T.OOS1.mean():.1f}% · 2025-26 {T.OOS2.mean():.1f}%; 학습-검증 순위상관 {T.IS_CAGR.corr(T.OOS1, method='spearman'):.2f} / {T.IS_CAGR.corr(T.OOS2, method='spearman'):.2f}")
print("\n[대조] 신호를 시간상으로 밀어 무의미하게 만든 뒤 같은 탐색의 학습구간 최대 CAGR (우연의 상한)")
nulls = []
for sh in (300, 700, 1100):
    Xs = {W: np.roll(X[W], sh, axis=0) for W in WS}; Tn = search(Xs); nulls.append(Tn.IS_CAGR.max()); print(f"  shift {sh}일: 최대 {Tn.IS_CAGR.max():.1f}%  (상위 10 평균 {Tn.nlargest(10,'IS_CAGR').IS_CAGR.mean():.1f}%)")
print(f"실제 신호 최대 {T.IS_CAGR.max():.1f}% vs 우연 최대 평균 {np.mean(nulls):.1f}%")
T.drop(columns="r").to_csv(os.path.join(HERE, "foreign_overfit_grid.csv"), index=False)
