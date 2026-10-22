"""나스닥100(QQQ 기초지수) vs 미국 금리 — 동행·예측·금리 급등 국면 (2026-10-22 탐색, 사전등록 아님).

예측 신호는 삼성 연구 사전등록 H7 정의를 그대로 가져옴(새로 고르지 않음):
  x = 미 10년물 6개월 변화(%p), 대상 = 이후 6개월 로그수익, 개발 ~2016-06 / 확인 2017-01~2026-03.
원천: DataVault/staging/samsung_cycle/yahoo_{IDX_NDX,IDX_TNX,IDX_IRX}.csv (가격지수, 배당 제외)
  .venv/bin/python -I docs/research/20261022_qqq_rates/qqq_rates.py
"""
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
sys.path.insert(0, os.path.join(HERE, "../20261022_samsung_single"))
from shared.data_library.paths import data_vault_path  # noqa: E402

SC = data_vault_path("staging", "samsung_cycle")


def y(name, col):
    return pd.read_csv(os.path.join(SC, f"yahoo_{name}.csv"), parse_dates=["date"]).set_index("date")[col]


def nw(yv, x, lag):
    X = np.column_stack([np.ones(len(x)), x])
    b = np.linalg.lstsq(X, yv, rcond=None)[0]
    e = yv - X @ b
    XtXi = np.linalg.inv(X.T @ X)
    S = (X * e[:, None]).T @ (X * e[:, None])
    for L in range(1, lag + 1):
        G = (X[L:] * e[L:, None]).T @ (X[:-L] * e[:-L, None])
        S += (1 - L / (lag + 1)) * (G + G.T)
    se = np.sqrt(np.diag(XtXi @ S @ XtXi))
    return b[1], b[1] / se[1]


d = pd.DataFrame({"ndx": y("IDX_NDX", "adjclose"), "y10": y("IDX_TNX", "close"), "y3m": y("IDX_IRX", "close")}).dropna()
d = d[d.index >= "1985-10-01"]
m = d.groupby(d.index.to_period("M")).tail(1)
m.index = m.index.to_period("M")
m["r1"] = np.log(m.ndx).diff()
m["dy"] = m.y10.diff()
m["d3"] = m.y3m.diff()
m["r6f"] = np.log(m.ndx.shift(-6) / m.ndx)
m["r12f"] = np.log(m.ndx.shift(-12) / m.ndx)
m["x"] = m.y10 - m.y10.shift(6)          # H7 정의
m["x3"] = m.y3m - m.y3m.shift(6)          # 단기금리(정책) 6개월 변화 — 참고
m["r6p"] = np.log(m.ndx / m.ndx.shift(6))  # 같은 6개월 동행

L = ["# 나스닥100 vs 미국 금리 (2026-10-22 탐색)", "", "가격지수(배당 제외), 월말. 미 10년물 = ^TNX, 3개월 = ^IRX(Yahoo). FRED 점검 중이라 실질금리 없음.", ""]

# 1) 동행: 같은 달 금리 변화 vs 수익 — 시기별
L += ["## 1. 같은 달 (동행)", "", "| 시기 | 10년물 1%p 상승당 같은 달 수익 | 상관 | 일간 상관 |", "|---|---|---|---|"]
dd = d.copy()
dd["r"] = np.log(dd.ndx).diff()
dd["dy"] = dd.y10.diff()
for a, b in [("1986", "1997"), ("1998", "2007"), ("2008", "2020"), ("2021", "2026"), ("2022", "2022"), ("2023", "2026")]:
    g = m.loc[a:b].dropna(subset=["r1", "dy"])
    beta = np.polyfit(g.dy, g.r1, 1)[0]
    gd = dd.loc[a:b].dropna(subset=["r", "dy"])
    L.append(f"| {a}~{b} | {beta * 100:+.1f}% | {g.r1.corr(g.dy):+.2f} | {gd.r.corr(gd.dy):+.2f} |")
# 연도별 일간 상관
yr = dd.dropna(subset=["r", "dy"]).groupby(dd.dropna(subset=["r", "dy"]).index.year).apply(lambda g: g.r.corr(g.dy))
L += ["", "연도별 일간 상관(나스닥100 수익 vs 10년물 변화; 음수 = 금리 오르면 주가 하락): " +
      " · ".join(f"{k} {v:+.2f}" for k, v in yr.items()), ""]

# 2) 예측: H7 정의
L += ["## 2. 이후 6개월 (예측) — 삼성 H7 정의 그대로", "",
      "| 구간 | 신호 | n | 1표준편차당 6개월 수익 | t(NW6) | 금리 하락 1/3 | 중간 | 금리 상승 1/3 |", "|---|---|---|---|---|---|---|---|"]
dev = m.loc[:"2016-06"]
for sig, nm in [("x", "10년물 6개월 변화"), ("x3", "3개월물 6개월 변화(참고)")]:
    mu, sd = dev[sig].mean(), dev[sig].std()
    q = dev[sig].quantile([1 / 3, 2 / 3]).values
    for tag, (a, b) in [("개발 1986~2016", ("1986-04", "2016-06")), ("확인 2017~2026", ("2017-01", "2026-03")), ("전체", ("1986-04", "2026-03"))]:
        g = m.loc[a:b].dropna(subset=[sig, "r6f"])
        s, t = nw(g.r6f.values, ((g[sig] - mu) / sd).values, 6)
        lo, mid, hi = g.r6f[g[sig] <= q[0]], g.r6f[(g[sig] > q[0]) & (g[sig] <= q[1])], g.r6f[g[sig] > q[1]]
        L.append(f"| {tag} | {nm} | {len(g)} | {s * 100:+.1f}%p | {t:+.1f} | {lo.mean() * 100:+.1f}% | {mid.mean() * 100:+.1f}% | {hi.mean() * 100:+.1f}% |")
# 연도 빼기(전체, x)
g = m.loc["1986-04":"2026-03"].dropna(subset=["x", "r6f"])
mu, sd = dev.x.mean(), dev.x.std()
loo = [(yy, nw(g[g.index.year != yy].r6f.values, ((g[g.index.year != yy].x - mu) / sd).values, 6)[0]) for yy in sorted(set(g.index.year))]
L += ["", f"전체 구간 한 해씩 빼기(10년물): {min(v for _, v in loo) * 100:+.1f}%p({min(loo, key=lambda z: z[1])[0]} 제외) ~ "
      f"{max(v for _, v in loo) * 100:+.1f}%p({max(loo, key=lambda z: z[1])[0]} 제외), 음수 {sum(v < 0 for _, v in loo)}/{len(loo)}"]
# 10년대별
L += ["", "10년 단위 기울기(10년물, %p/표준편차, t): " + " · ".join(
    f"{a[:4]}s {nw(m.loc[a:b].dropna(subset=['x','r6f']).r6f.values, ((m.loc[a:b].dropna(subset=['x','r6f']).x - mu) / sd).values, 6)[0] * 100:+.1f}"
    f"({nw(m.loc[a:b].dropna(subset=['x','r6f']).r6f.values, ((m.loc[a:b].dropna(subset=['x','r6f']).x - mu) / sd).values, 6)[1]:+.1f})"
    for a, b in [("1986-04", "1995-12"), ("1996-01", "2005-12"), ("2006-01", "2015-12"), ("2016-01", "2026-03")]), ""]

# 3) 금리 급등 국면: 6개월 +1%p 이상 — 처음 넘은 달 기준
L += ["## 3. 10년물 6개월 +1%p 이상 급등 국면 (처음 넘은 달)", "",
      "| 달 | 10년물 | 6개월 변화 | 그 6개월 나스닥 | 이후 6개월 | 이후 12개월 |", "|---|---|---|---|---|---|"]
hit = m[(m.x >= 1.0)]
prev = None
for p, r in hit.iterrows():
    if prev is not None and (p - prev).n <= 12:
        prev = p
        continue
    prev = p
    f6 = "–" if np.isnan(r.r6f) else f"{np.expm1(r.r6f) * 100:+.0f}%"
    f12 = "–" if np.isnan(r.r12f) else f"{np.expm1(r.r12f) * 100:+.0f}%"
    L.append(f"| {p} | {r.y10:.2f}% | {r.x:+.2f}%p | {np.expm1(r.r6p) * 100:+.0f}% | {f6} | {f12} |")
L += ["", f"무조건 평균(1986-04~2026-03): 이후 6개월 {np.expm1(m.loc['1986-04':'2026-03'].r6f).mean() * 100:+.1f}%, 12개월 {np.expm1(m.loc['1986-04':'2025-09'].r12f).mean() * 100:+.1f}%"]

last = m.dropna(subset=["x"]).iloc[-1]
L += ["", f"현재({last.name}): 10년물 {last.y10:.2f}%, 6개월 변화 {last.x:+.2f}%p, 3개월물 {last.y3m:.2f}%, 6개월 변화 {last.x3:+.2f}%p"]
open(os.path.join(HERE, "results.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))
