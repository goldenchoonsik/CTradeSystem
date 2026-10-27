"""정체 매집 조건 최적화: 개발 2014~2021 에서 격자 탐색, 검증 2022~ 에서 확인. 비교 기준 = 같은 날 '정체만(매집 조건 없음)' 종목.
선정 = 상위500 중 창 W 의 외국인 누적 순매수 상위 q% & 순매수일 비율 ≥ u & 창 수익 ≤ r. 보유 H일. 지표 = 날짜별 (선정 평균 − 같은날 상위500 중앙) 초과수익, 날짜 평균·t. 왕복비용 0.27% 차감.
  .venv/bin/python docs/research/20261026_foreign_stall_breakout/optimize.py"""
import os, itertools, numpy as np, pandas as pd
exec(open(os.path.join(os.path.dirname(__file__), "stall.py")).read().split("c120 = flow")[0])   # 데이터 적재·flow
cap = sh * cu; rank = cap.rank(axis=1, ascending=False).values; C = cl.values; dates = cl.index; last = len(dates) - 1
Fv = flow.fillna(0).values; Pos = (flow > 0).values.astype(float); Nz = ((flow != 0) & flow.notna()).values.astype(float); Vd = flow.notna().values.astype(float)
cs = lambda a: np.vstack([np.zeros((1, a.shape[1])), np.cumsum(a, 0)])
cF, cP, cN, cV = cs(Fv), cs(Pos), cs(Nz), cs(Vd)
Ws, Hs, COST = (20, 40, 60, 90, 120, 180, 250), (20, 60, 120), 0.0027
STEP = 10; idx = [i for i in range(260, last - 120, STEP)]
rec = {}   # (W) -> list per date of dict
for W in Ws:
    L = []
    for i in idx:
        top = np.where(rank[i] <= 500)[0]
        c = cF[i + 1, top] - cF[i + 1 - W, top]; nz = cN[i + 1, top] - cN[i + 1 - W, top]; up = (cP[i + 1, top] - cP[i + 1 - W, top]) / np.maximum(nz, 1)
        ok = (cV[i + 1, top] - cV[i + 1 - W, top]) >= 0.85 * W
        r = C[i, top] / C[i - W, top] - 1
        f = {H: C[i + H, top] / C[i, top] - 1 for H in Hs if i + H <= last}
        L.append((i, c, up, r, ok, f))
    rec[W] = L
def run(W, q, u, rmax, H, lo, hi, ctl=False):
    ex, n = [], 0
    for i, c, up, r, ok, f in rec[W]:
        if not (lo <= dates[i] < hi) or H not in f: continue
        fw = f[H]; med = np.nanmedian(fw)
        stall = ok & (r <= rmax) & np.isfinite(fw)
        if ctl: sel = stall
        else:
            thr = np.nanpercentile(c[ok], 100 - q) if ok.sum() > 20 else np.inf
            sel = stall & (c >= thr) & (c > 0) & (up >= u)
        if sel.sum() < 4: continue
        ex.append(fw[sel].mean() - COST - med); n += sel.sum()
    ex = np.array(ex)
    return (ex.mean(), ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex))) if len(ex) > 2 else np.nan, len(ex), n) if len(ex) else (np.nan, np.nan, 0, 0)
DEV = (pd.Timestamp("2014-01-01"), pd.Timestamp("2022-01-01")); TEST = (pd.Timestamp("2022-01-01"), pd.Timestamp("2027-01-01"))
rows = []
for W, q, u, rmax, H in itertools.product(Ws, (10, 20, 30), (0.5, 0.55, 0.6), (-0.10, 0.0, 0.05, 0.10), Hs):
    d = run(W, q, u, rmax, H, *DEV); t = run(W, q, u, rmax, H, *TEST)
    dc = run(W, q, u, rmax, H, *DEV, ctl=True); tc = run(W, q, u, rmax, H, *TEST, ctl=True)
    rows.append(dict(W=W, q=q, u=u, rmax=rmax, H=H, dev=d[0]*100, dev_t=d[1], dev_n=d[3], dev_ctl=dc[0]*100, test=t[0]*100, test_t=t[1], test_n=t[3], test_ctl=tc[0]*100))
R = pd.DataFrame(rows); R["dev_gain"] = R.dev - R.dev_ctl; R["test_gain"] = R.test - R.test_ctl
R.to_csv(os.path.join(OUT, "optimize_grid.csv"), index=False)
print("조합", len(R), "| 개발 초과>0 비율 %.0f%%, 검증 초과>0 비율 %.0f%%" % ((R.dev > 0).mean()*100, (R.test > 0).mean()*100))
print("개발·검증 순위상관(초과수익): %.2f" % R.dev.corr(R.test, method="spearman"))
top = R[R.dev_n >= 150].sort_values("dev_t", ascending=False).head(12)
print("\n개발구간 상위 12 (t 기준) → 검증구간 성적 (% = 비용 후 시장중앙 대비 초과)"); print(top[["W","q","u","rmax","H","dev","dev_t","dev_gain","test","test_t","test_gain","test_n"]].round(2).to_string(index=False))
G = R[R.dev_n >= 150]; k = max(1, len(G)//10)
print("\n[매집 효과만 = 정체만 대조군 대비 초과] 개발·검증 순위상관: %.2f" % G.dev_gain.corr(G.test_gain, method="spearman"))
print("개발 초과(대조군 대비) 상위 10%% 평균 → 검증: %.2f%%p → %.2f%%p ; 전체 평균 검증 %.2f%%p" % (G.nlargest(k, "dev_gain").dev_gain.mean(), G.nlargest(k, "dev_gain").test_gain.mean(), G.test_gain.mean()))
print(G.nlargest(8, "dev_gain")[["W","q","u","rmax","H","dev_gain","test_gain","test_n"]].round(2).to_string(index=False))
print("대조군(정체만) 평균초과: 개발 %.2f%% 검증 %.2f%% / 선정: 개발 %.2f%% 검증 %.2f%%" % (G.dev_ctl.mean(), G.test_ctl.mean(), G.dev.mean(), G.test.mean()))
print("\n파라미터별 평균 (개발/검증 초과 %):")
for k in ("W","q","u","rmax","H"): print(k, R.groupby(k)[["dev","test"]].mean().round(2).T.to_string(), "\n")
