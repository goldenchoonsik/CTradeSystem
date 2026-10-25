"""꾸준한 외국인 매집(60거래일) — 이후 수익과 가격 영구성. 상위 300(시총), 20거래일마다 윈도우(2016~2025), 기업행위 제외, 일별 순매수=Δ외국인 보유수량/상장주식수(%).
C60=누적 순매수, U=순매수 양(+)인 날 비율(변화 있는 날 기준), R60=윈도우 수익. 사후 수익은 같은 날 횡단면 중앙값 대비 초과. 창 날짜별 평균→t.
  .venv/bin/python docs/research/20261024_foreign_accum/steady_buy_episodes.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); V = f"{ROOT}/DataVault/staging/kr_equity/derived"
ext = pd.read_parquet(f"{V}/daily_ext.parquet", columns=["code", "date", "shares_listed", "foreign_hold_qty"]); px = pd.read_parquet(f"{V}/prices_1d.parquet", columns=["code", "date", "close", "factor"]).dropna(subset=["close"])
d = ext.merge(px, on=["code", "date"]); d = d[d.date >= "2015-09-01"]
fz = d.pivot(index="date", columns="code", values="foreign_hold_qty").astype("float64"); sh = d.pivot(index="date", columns="code", values="shares_listed").astype("float64"); cl = d.pivot(index="date", columns="code", values="close").astype("float64"); fc = d.pivot(index="date", columns="code", values="factor").astype("float64")
bad = (sh.diff().fillna(0) != 0) | (fc.diff().fillna(0) != 0); flow = (fz.diff() / sh.shift(1) * 100).where(~bad)      # %포인트(상장주식수 대비), 기업행위일 NaN
cap = (sh * cl).values; F = flow.values; C = cl.values; dates = fz.index
rows = []
for i in range(80, len(dates) - 125, 20):
    if dates[i] < pd.Timestamp("2016-06-01"): continue
    top = np.argsort(-np.nan_to_num(cap[i]))[:300]; w = F[i - 59:i + 1][:, top]
    ok = np.isfinite(w).sum(0) >= 50
    c60 = np.nansum(w, 0); nz = np.where(np.isfinite(w) & (w != 0), 1, 0).sum(0); up = np.where(np.isfinite(w) & (w > 0), 1, 0).sum(0) / np.maximum(nz, 1)
    r60 = C[i, top] / C[i - 60, top] - 1; f20 = C[i + 20, top] / C[i, top] - 1; f60 = C[i + 60, top] / C[i, top] - 1; f120 = C[i + 120, top] / C[i, top] - 1; r10 = C[i, top] / C[i - 10, top] - 1
    df = pd.DataFrame(dict(date=dates[i], c60=c60, up=up, r60=r60, f20=f20, f60=f60, f120=f120, r10=r10))[ok]
    for k in ("f20", "f60", "f120"): df[k] = df[k] - df[k].median()
    rows.append(df.dropna())
X = pd.concat(rows); print("윈도우 날짜 %d개, 관측 %d" % (X.date.nunique(), len(X)))
X["q"] = X.groupby("date").c60.transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False)); X["steady"] = (X.q == 4) & (X.up >= 0.6)
def tt(s): s = s.dropna(); return s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
print("\n== 60일 누적 순매수 5분위(날짜 내): 윈도우 수익(R60)과 사후 초과수익(%) ==")
g = X.groupby(["date", "q"]).agg(r60=("r60", "mean"), f20=("f20", "mean"), f60=("f60", "mean"), f120=("f120", "mean")).reset_index()
t = g.groupby("q").agg(순매수평균=("r60", "size"), R60=("r60", "mean"), f20=("f20", "mean"), f60=("f60", "mean"), f120=("f120", "mean")); t["순매수평균"] = X.groupby("q").c60.mean(); print((t * [1, 100, 100, 100, 100]).round(2).to_string())
sp = g.pivot(index="date", columns="q", values=["f20", "f60", "f120"]); print("상위−하위 5분위: ", {k: (round((sp[k][4] - sp[k][0]).mean() * 100, 2), round(tt(sp[k][4] - sp[k][0]), 1), f"{((sp[k][4] - sp[k][0]) > 0).mean() * 100:.0f}%") for k in ("f20", "f60", "f120")}, "(평균 %p, t, 양수 날짜 비율)")
print("\n== 꾸준한 매집(상위 20%% & 순매수 날 60%%↑): 건수 %d(%.1f%%) ==" % (X.steady.sum(), X.steady.mean() * 100))
# 대조: 같은 R60 3분위 안에서 steady vs 같은 5분위 상위인데 steady 아닌 것 vs 나머지
X["rt"] = X.groupby("date").r60.transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False))
for lab, m in (("꾸준한 매집", X.steady), ("상위 20%지만 간헐적(순매수 날 <60%)", (X.q == 4) & ~X.steady), ("하위 20%(순매도)", X.q == 0)):
    z = X[m]; gg = z.groupby("date")[["f20", "f60", "f120", "r60"]].mean(); print(f"{lab:34s} n={len(z):5d} R60 {gg.r60.mean()*100:+.1f}% | 사후 초과 20일 {gg.f20.mean()*100:+.2f}% (t {tt(gg.f20):.1f}) 60일 {gg.f60.mean()*100:+.2f}% (t {tt(gg.f60):.1f}) 120일 {gg.f120.mean()*100:+.2f}% (t {tt(gg.f120):.1f})")
print("\n== 같은 R60 구간 통제: R60 3분위별 꾸준한 매집 vs 나머지의 사후 60일 초과(%p) ==")
for rt in (0, 1, 2):
    a = X[(X.rt == rt) & X.steady].groupby("date").f60.mean(); b = X[(X.rt == rt) & ~X.steady].groupby("date").f60.mean(); dd = (a - b).dropna()
    print(f"R60 {['하','중','상'][rt]}분위: 꾸준한 매집 − 나머지 {dd.mean()*100:+.2f}%p (t {tt(dd):.1f}, n일 {len(dd)}, 양수 {((dd>0).mean()*100):.0f}%) | 꾸준한 매집 건수 {int(((X.rt==rt)&X.steady).sum())}")
print("\n== 가격 영구성: 꾸준한 매집 종목의 윈도우 수익(R60) 대비 사후 반응 — 되돌림? ==")
z = X[X.steady]; rr = z.groupby("date").apply(lambda q: pd.Series({"R60": q.r60.mean(), "f60": q.f60.mean()})); print("꾸준한 매집 윈도우 수익 평균 %+.1f%%, 사후 60일 초과 %+.2f%%, 상관(R60 크기↔사후 60일, 시즌 평균) %+.3f" % (rr.R60.mean() * 100, rr.f60.mean() * 100, z.groupby("date").apply(lambda q: q.r60.rank().corr(q.f60.rank())).mean()))
X.to_csv(f"{ROOT}/docs/research/20261024_foreign_accum/steady_buy_episodes.csv", index=False)
