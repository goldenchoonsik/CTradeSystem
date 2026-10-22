"""H7(미 10년물 6개월 변화) 강건성 점검 — 사전등록 밖 '참고' 분석(판정에 쓰지 않음).

1) 연도 빼기(leave-one-year-out) 기울기 범위  2) 겹치지 않는 6개월 표본(시작월 6가지)
3) 같은 신호로 코스피·SOX(달러)·마이크론 예측 → 삼성만의 현상인가  4) H7 단독 비중 규칙(참고)
5) 현재 신호값
  .venv/bin/python -I docs/research/20261022_samsung_single/robustness_H7.py
"""
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from test_hypotheses import M, nw_ols, perf, DEV, CONF  # noqa: E402

R = pd.read_csv(os.path.join(HERE, "results_C.csv")).set_index("h")
mu, sd = R.loc["H7", "mu"], R.loc["H7", "sd"]
M = M.copy()
M["z7"] = (M.H7 - mu) / sd
for h in (6,):
    M["sox6"] = np.log(M.sox.shift(-h) / M.sox)
    M["mu6"] = np.log(M.mu.shift(-h) / M.mu)
    M["spx6"] = np.log(M.spx.shift(-h) / M.spx)


def slope(df, y="r6", x="z7"):
    d = df[[x, y]].dropna()
    X = np.column_stack([np.ones(len(d)), d[x].values])
    b, se, n = nw_ols(d[y].values, X, 6)
    return b[1], b[1] / se[1], n


L = ["# H7 강건성 (사전등록 밖 참고 — 판정 불변)", ""]
for tag, (a, b) in [("개발", DEV), ("확인", CONF)]:
    w = M.loc[a:b]
    base = slope(w)
    yrs = sorted(set(w.index.year))
    loo = [(y, slope(w[w.index.year != y])[0]) for y in yrs]
    lo, hi = min(loo, key=lambda x: x[1]), max(loo, key=lambda x: x[1])
    # 겹치지 않는 표본: 시작월 6가지
    nov = []
    for off in range(6):
        ww = w.iloc[off::6]
        d = ww[["z7", "r6"]].dropna()
        bb = np.polyfit(d.z7, d.r6, 1)[0]
        e = d.r6 - np.polyval(np.polyfit(d.z7, d.r6, 1), d.z7)
        se = np.sqrt(e.var(ddof=2) / ((d.z7 - d.z7.mean()) ** 2).sum())
        nov.append((bb, bb / se, len(d)))
    L += [f"## {tag} {a}~{b}", "",
          f"- 기본: 기울기 {base[0] * 100:+.1f}%p / t {base[1]:+.1f} (n={base[2]})",
          f"- 한 해씩 빼기: {lo[1] * 100:+.1f}%p({lo[0]} 제외) ~ {hi[1] * 100:+.1f}%p({hi[0]} 제외) — 부호 유지 {sum(1 for _, v in loo if v < 0)}/{len(loo)}",
          f"- 겹치지 않는 6개월 표본(시작월 6가지): 기울기 {min(x[0] for x in nov) * 100:+.1f}~{max(x[0] for x in nov) * 100:+.1f}%p, t {min(x[1] for x in nov):+.1f}~{max(x[1] for x in nov):+.1f}, n≈{nov[0][2]}"]
    for nm, y in [("코스피", "rk6"), ("코스피 대비 삼성 초과", "x6"), ("SOX(달러)", "sox6"), ("마이크론(달러)", "mu6"), ("S&P500", "spx6")]:
        s = slope(w, y)
        L.append(f"- 같은 신호 → {nm} 6개월: {s[0] * 100:+.1f}%p / t {s[1]:+.1f}")
    # 신호 3분위별 평균 6개월 수익
    q = M.loc[DEV[0]:DEV[1], "z7"].quantile([1 / 3, 2 / 3]).values
    g = pd.cut(w.z7, [-np.inf, q[0], q[1], np.inf], labels=["금리 하락(하위1/3)", "중간", "금리 상승(상위1/3)"])
    t = w.groupby(g, observed=True).r6.agg(["mean", "count"])
    L.append("- 3분위 평균 6개월 수익: " + " · ".join(f"{i} {r['mean'] * 100:+.1f}% (n={int(r['count'])})" for i, r in t.iterrows()))
    L.append("")

# 참고: H7 단독 비중 규칙 (사전등록 규칙과 같은 형태)
r1 = np.exp(M.r1) - 1
w7 = (1 + 0.5 * (-M.z7)).clip(0.25, 1.75).fillna(1.0)
L += ["## 참고 — H7 단독 비중 규칙 w = clip(1 − 0.5·z, 0.25, 1.75), 비용 0.25%/비중변화", "", "| 구간 | 규칙 연수익 | 규칙 MDD | 노출맞춤 보유 연수익 | 노출맞춤 MDD | 연 초과 | 평균 w |", "|---|---|---|---|---|---|---|"]
for tag, (a, b) in [("개발", ("2000-01", "2016-11")), ("확인", ("2017-01", "2026-07"))]:
    ww, rr = w7.loc[a:b], r1.loc[a:b]
    rule = ww * rr - 0.0025 * ww.diff().abs().fillna(0)
    match = ww.mean() * rr
    p, m = perf(rule), perf(match)
    L.append(f"| {tag} | {p['cagr'] * 100:+.1f}% | {p['mdd'] * 100:.0f}% | {m['cagr'] * 100:+.1f}% | {m['mdd'] * 100:.0f}% | {(p['cagr'] - m['cagr']) * 100:+.1f}%p | {ww.mean():.2f} |")

last = M.dropna(subset=["H7"]).iloc[-1]
L += ["", f"## 현재 ({last.name}, 직전 미국 거래일 기준)", "",
      f"미 10년물 {last.us10y:.2f}%, 6개월 변화 {last.H7:+.2f}%p → z {last.z7:+.2f} (개발 평균 {mu:+.2f}, 표준편차 {sd:.2f})"]
open(os.path.join(HERE, "results_H7_robustness.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))
