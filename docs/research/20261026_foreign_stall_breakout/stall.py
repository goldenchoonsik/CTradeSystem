"""외국인 꾸준한 매수에도 주가 정체 → 이후 상승 전환 종목 탐지.
일별 순매수 = Δ외국인 보유수량/상장주식수(%p, 기업행위일 NaN). 120거래일 창: 누적 C120, 순매수일 비율 U, 창 수익 R120.
정체 매집 = C120 ≥ CMIN & U ≥ 0.55 & R120 ≤ +5% & 시총 상위 500. 연속 충족은 한 에피소드(공백 20일 초과 시 분리), 신호일 = 첫 충족일.
  .venv/bin/python docs/research/20261026_foreign_stall_breakout/stall.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
V = f"{ROOT}/DataVault/staging/kr_equity"; D = f"{V}/derived"; OUT = os.path.dirname(__file__)
W, CMIN, UMIN, RMAX, TOPN = 120, 1.0, 0.55, 0.05, 500
ext = pd.read_parquet(f"{D}/daily_ext.parquet", columns=["code","date","shares_listed","foreign_hold_qty"])
px = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close","close_unadj","factor"]).dropna(subset=["close"])
d = ext.merge(px, on=["code","date"]); d = d[d.date >= "2013-06-01"]
pv = lambda c: d.pivot(index="date", columns="code", values=c).astype("float64")
fz, sh, cl, cu, fc = pv("foreign_hold_qty"), pv("shares_listed"), pv("close"), pv("close_unadj"), pv("factor")
bad = (sh.diff().fillna(0) != 0) | (fc.diff().fillna(0) != 0)
flow = (fz.diff() / sh.shift(1) * 100).where(~bad)
valid = flow.notna().astype(float)
c120 = flow.fillna(0).rolling(W).sum(); nv = valid.rolling(W).sum()
nz = ((flow != 0) & flow.notna()).astype(float).rolling(W).sum(); upn = (flow > 0).astype(float).rolling(W).sum()
U = upn / nz.clip(lower=1); r120 = cl / cl.shift(W) - 1
cap = sh * cu; rank = cap.rank(axis=1, ascending=False)
mkt = r120.median(axis=1)
sig = (c120 >= CMIN) & (U >= UMIN) & (r120 <= RMAX) & (nv >= 100) & (rank <= TOPN)
# 대조군: 같은 매집인데 주가는 오른 경우
sigUp = (c120 >= CMIN) & (U >= UMIN) & (r120 > 0.15) & (nv >= 100) & (rank <= TOPN)
dates = cl.index; ix = {t: i for i, t in enumerate(dates)}
def episodes(S):
    out = []
    A = S.values
    for j, code in enumerate(S.columns):
        idx = np.where(A[:, j])[0]
        if len(idx) == 0: continue
        start = prev = idx[0]
        for k in idx[1:]:
            if k - prev > 20: out.append((code, start, prev)); start = k
            prev = k
        out.append((code, start, prev))
    return out
nm = pd.read_csv(f"{V}/reference/listed_snapshot.csv").set_index("code")
last = len(dates) - 1; C = cl.values; cols = {c: j for j, c in enumerate(cl.columns)}
def build(S, tag):
    rows = []
    for code, a, b in episodes(S):
        j = cols[code]; p0 = C[a, j]
        r = dict(tag=tag, code=code, name=nm["name"].get(code, ""), t0=dates[a], t1=dates[b], days=b - a + 1, c120=c120.values[a, j], U=U.values[a, j], r120=r120.values[a, j], mkt120=mkt.values[a])
        for H in (60, 120, 250):
            r[f"f{H}"] = C[a + H, j] / p0 - 1 if a + H <= last else np.nan
            r[f"x{H}"] = (r[f"f{H}"] - np.nanmedian(C[a + H] / C[a] - 1)) if a + H <= last else np.nan   # 같은 날 전 종목 중앙값 대비 초과
        # 전환일: 신호일 이후 종가가 신호일 대비 +20% 처음 넘는 날 (최대 500일 내), 그 전 최대 낙폭
        seg = C[a:min(a + 500, last) + 1, j] / p0 - 1; hit = np.where(seg >= 0.20)[0]
        r["turn_days"] = hit[0] if len(hit) else np.nan
        r["mdd_before"] = np.nanmin(seg[:hit[0] + 1] if len(hit) else seg)
        r["censored"] = a + 500 > last and len(hit) == 0
        rows.append(r)
    return pd.DataFrame(rows)
E = build(sig, "정체매집"); G = build(sigUp, "상승매집")
E.to_csv(f"{OUT}/stall_episodes.csv", index=False); G.to_csv(f"{OUT}/rise_episodes.csv", index=False)
# 기저율: 상위 500 전 종목-날짜(20일 간격)에서 f120 ≥ +20% 비율
base = []
for i in range(W, last - 250, 20):
    top = np.where(rank.values[i] <= TOPN)[0]; f = C[i + 120, top] / C[i, top] - 1; f = f[np.isfinite(f)]
    base.append(((f >= .2).mean(), np.median(f), (f - np.median(f) >= .2).mean()))
base = np.array(base).mean(0)
def rep(X, nmn):
    x = X[X.t0 <= dates[last - 250]]
    print(f"\n== {nmn}: 에피소드 {len(X)}개 (종목 {X.code.nunique()}), 250일 관측 가능 {len(x)}개 ==")
    for H in (60, 120, 250):
        y = x.dropna(subset=[f"f{H}"]); print(f"  +{H}일: 평균 {y[f'f{H}'].mean()*100:5.1f}% 중앙 {y[f'f{H}'].median()*100:5.1f}% | 시장대비 초과 중앙 {y[f'x{H}'].median()*100:5.1f}%·평균 {y[f'x{H}'].mean()*100:5.1f}% | +20%↑ {(y[f'f{H}']>=.2).mean()*100:4.1f}%  손실 {(y[f'f{H}']<0).mean()*100:4.1f}%")
    y = x.dropna(subset=["f250"]); t = x.turn_days
    print(f"  500일 내 +20% 도달 {t.notna().mean()*100:.1f}%, 도달까지 중앙 {t.median():.0f}일, 도달 전 최대낙폭 중앙 {x.mdd_before.median()*100:.1f}%")
print(f"기저율(상위{TOPN} 전체, 20일 간격 평균): 120일 +20%↑ {base[0]*100:.1f}%, 중앙수익 {base[1]*100:.1f}%, 중앙값 대비 +20%p↑ {base[2]*100:.1f}%")
rep(E, "정체 매집(외국인 매수·주가 R120≤5%)"); rep(G, "대조: 상승 매집(R120>15%)")
E["year"] = E.t0.dt.year; x = E[E.t0 <= dates[last - 120]].dropna(subset=["f120"])
print("\n연도별 정체매집 에피소드 수 / 120일 초과 중앙% / +20%↑ 비율%"); print(x.groupby("year").agg(n=("f120","size"), x120=("x120", lambda s: s.median()*100), hit=("f120", lambda s: (s>=.2).mean()*100)).round(1).to_string())
# 현재 진행 중(마지막 충족일이 최근 5거래일 내)
cur = E[E.t1 >= dates[last - 5]].sort_values("c120", ascending=False)
cur.to_csv(f"{OUT}/stall_current.csv", index=False)
print(f"\n== 현재({dates[last].date()}) 정체 매집 진행 중 {len(cur)}개 =="); print(cur[["code","name","t0","c120","U","r120","days"]].round(2).to_string(index=False))
