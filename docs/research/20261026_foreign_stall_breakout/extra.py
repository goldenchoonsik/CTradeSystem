"""정체 매집이 '정체 + 매집 없음' 대조보다 나은가, 그리고 전환 종목을 사전에 가르는 특징이 있나. stall.py 와 같은 정의 재사용."""
import os, sys, numpy as np, pandas as pd
exec(open(os.path.join(os.path.dirname(__file__), "stall.py")).read().split("nm = pd.read_csv")[0])
last = len(dates) - 1; C = cl.values
ctl = (c120 <= 0) & (U < 0.45) & (r120 <= RMAX) & (nv >= 100) & (rank <= TOPN)       # 정체 + 외국인 매도 우위
neu = (r120 <= RMAX) & (nv >= 100) & (rank <= TOPN)                                  # 정체 전체
def stat(S, tag):
    A = S.values; res = []
    for i in range(W, last - 120, 20):
        js = np.where(A[i])[0]
        if len(js) < 5: continue
        f = C[i + 120, js] / C[i, js] - 1; m = np.nanmedian(C[i + 120] / C[i] - 1); f = f[np.isfinite(f)]
        res.append((np.median(f) - m, (f >= .2).mean(), len(f)))
    r = np.array(res); print(f"{tag}: 날짜 {len(r)}개, 120일 초과수익 중앙(날짜평균) {r[:,0].mean()*100:.1f}%p, +20%↑ {r[:,1].mean()*100:.1f}%, 날짜당 평균 {r[:,2].mean():.0f}종목")
stat(sig, "정체+외국인매집"); stat(ctl, "정체+외국인매도"); stat(neu, "정체 전체")
E = pd.read_csv(os.path.join(OUT, "stall_episodes.csv"), parse_dates=["t0"]); E = E[E.t0 <= dates[last - 120]].dropna(subset=["f120"]); E["win"] = E.f120 >= .2
E["deep"] = E.r120 < -0.1
for c in ("c120", "U", "r120", "days"):
    q = pd.qcut(E[c].rank(method="first"), 4, labels=False); print(c, "4분위별 +20%↑ 비율:", (E.groupby(q).win.mean()*100).round(1).tolist())
print("에피소드 수", len(E), " +20%↑ 전환", E.win.sum())
