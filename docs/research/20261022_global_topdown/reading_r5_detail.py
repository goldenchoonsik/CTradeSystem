"""R5 업종 판독 상세 — 사전등록 ③ 상위 2 − 하위 2 업종 6개월 차(코스피 대비) + 성분별 IC(보충: 수요 충격만·미국 점유만).
  .venv/bin/python -I docs/research/20261022_global_topdown/reading_r5_detail.py
"""
import os, sys
import numpy as np
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from shared.data_library.global_macro import reading as R  # noqa: E402
from shared.data_library.global_macro.reading_eval import run_engine, me, SPLIT, mean_nw  # noqa: E402

D = R.Data(); hist = run_engine(D); k = D.close["U001"]
rows = []
for r in hist:
    t = pd.Timestamp(r["t"]); T = me(t, R.H)
    if not r["sectors"] or T not in D.close.index:
        continue
    rk = np.log(k[T] / k[t])
    for s, v in r["sectors"].items():
        rows.append({"t": t, "s": s, "score": v["score"], "D": v["D"], "C": v["C"], "out": np.log(D.close.loc[T, s] / D.close.loc[t, s]) - rk})
df = pd.DataFrame(rows)
def ic_of(col):
    return df.dropna(subset=[col]).groupby("t").apply(lambda g: g[col].rank().corr(g.out.rank()) if len(g) >= 5 else np.nan).dropna()
spr = df.groupby("t").apply(lambda g: g.nlargest(2, "score").out.mean() - g.nsmallest(2, "score").out.mean() if len(g) >= 5 else np.nan).dropna()
L = ["# R5 업종 판독 상세", "", "| 구간 | 상위2−하위2 6개월(%p, 평균) | 상위2>하위2 비율 | 점수 IC | 수요 충격만 IC(보충) | 미국 점유만 IC(보충, 2012~) |", "|---|---|---|---|---|---|"]
icS, icD, icC = ic_of("score"), ic_of("D"), ic_of("C")
for per, m in (("전체", lambda i: i == i), ("~2016", lambda i: i <= SPLIT), ("2017~", lambda i: i > SPLIT)):
    sp = spr[m(spr.index)]
    f = lambda s: "{:+.3f} (t {:+.1f})".format(*mean_nw(s[m(s.index)], R.H)[:2]) if m(s.index).sum() >= 24 else "–"
    L.append(f"| {per} | {np.expm1(sp).mean() * 100 if False else sp.mean() * 100:+.1f} | {(sp > 0).mean() * 100:.0f}% | {f(icS)} | {f(icD)} | {f(icC)} |")
L += ["", "업종별 평균 점수 순위와 실현 초과수익(전체): " + " · ".join(f"{R.SECTOR_HS[s]['name']} 점수>0 일 때 {g[g.score > 0].out.mean() * 100:+.1f}% / <0 일 때 {g[g.score < 0].out.mean() * 100:+.1f}%" for s, g in df.groupby("s"))]
open(os.path.join(HERE, "results_reading_r5.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))
