"""보충(사전등록 밖, 판정에 안 씀): 수요 판독(R2)이 '5년 평균으로 되돌아감' 위에 경기선행지수 정보를 얼마나 더하나.
실제 변화 = a·(평균 회귀 항) + b·(엔진 예측) 회귀 — 엔진 예측의 추가 설명력과 적중률 차이.
  .venv/bin/python -I docs/research/20261022_global_topdown/reading_increment.py
"""
import os, sys
import numpy as np
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from shared.data_library.global_macro import reading as R  # noqa: E402
from reading_walkforward import run_engine, me, SPLIT  # noqa: E402

D = R.Data()
hist = run_engine(D)
rows = []
for r in hist:
    t = pd.Timestamp(r["t"]); L, T = me(t, -1), me(t, R.H)
    for g, d in r["demand"].items():
        yT, yL = D.y[g].get(T), D.y[g].get(L)
        if yT is None or pd.isna(yT):
            continue
        win = D.y[g].loc[:L].dropna().iloc[-R.WINDOW:]
        rows.append({"t": t, "g": g, "act": yT - yL, "pred": d["delta"], "mr": win.mean() - yL})
df = pd.DataFrame(rows)
def r2(y, X):
    X = np.column_stack([np.ones(len(X))] + [X[:, i] for i in range(X.shape[1])])
    b = np.linalg.lstsq(X, y, rcond=None)[0]; e = y - X @ b
    return 1 - e.var() / y.var(), b
L = ["# 보충: 수요 판독의 정보가 평균 회귀를 넘는가 (사전등록 밖)", "", "| 구간 | n | R² 평균 회귀만 | R² 엔진만 | R² 둘 다 | 엔진 추가 R² | 엔진 계수(둘 다) | 엔진 적중 | 평균 회귀 적중 | 둘이 다를 때 엔진 적중 |", "|---|---|---|---|---|---|---|---|---|---|"]
for per, d in (("전체", df), ("~2016", df[df.t <= SPLIT]), ("2017~", df[df.t > SPLIT])):
    y = d.act.values
    a, _ = r2(y, d[["mr"]].values); b, _ = r2(y, d[["pred"]].values); c, coef = r2(y, d[["mr", "pred"]].values)
    hit_e = (np.sign(d.pred) == np.sign(d.act)).mean(); hit_m = (np.sign(d.mr) == np.sign(d.act)).mean()
    dis = d[np.sign(d.pred) != np.sign(d.mr)]
    L.append(f"| {per} | {len(d)} | {a:.3f} | {b:.3f} | {c:.3f} | {c - a:+.3f} | {coef[2]:+.2f} | {hit_e*100:.0f}% | {hit_m*100:.0f}% | {(np.sign(dis.pred) == np.sign(dis.act)).mean()*100:.0f}% (n={len(dis)}) |")
L += ["", "상대국별 엔진 추가 R²(전체): " + " · ".join(f"{g} {r2(d.act.values, d[['mr','pred']].values)[0] - r2(d.act.values, d[['mr']].values)[0]:+.3f}" for g, d in df.groupby("g"))]
open(os.path.join(HERE, "results_reading_increment.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))
