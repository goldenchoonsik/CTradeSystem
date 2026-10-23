"""탐색(판정 아님): 경기민감 업종은 PER 이 거꾸로인가 — 조선 2016~ 이력 + 상태별(적자/고PER/저PER) 이후 12개월 초과수익.

실행: .venv/bin/python -I docs/research/20261021_kospi50_fundamental/cyclical_per.py
경기민감 목록은 표준 업종(조선·해운·화학·철강·정유·반도체·디스플레이)으로 손으로 지정 — 사후 선정 위험 있음.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kospi50_test as kt  # noqa: E402

CYC = {
    "조선": ["A009540", "A010140", "A042660", "A329180", "A010620"],
    "해운": ["A011200", "A028670"],
    "화학": ["A011170", "A051910", "A011780", "A009830"],
    "철강·비철": ["A005490", "A004020", "A010130"],
    "정유": ["A010950", "A096770"],
    "반도체": ["A005930", "A000660"],
    "디스플레이": ["A034220"],
}
SECTOR = {c: s for s, cs in CYC.items() for c in cs}
cf, tdays = kt.cf, kt.tdays


def fwd12(d, codes):
    i = tdays.searchsorted(d, side="right")
    j = i + 252
    if j >= len(tdays) or tdays[j] > pd.Timestamp("2026-09-11"):
        return None
    return cf.iloc[j][codes] / cf.iloc[i][codes] - 1


# 1) 조선 연말 이력
L = ["# 경기민감 업종 PER 탐색 (판정 아님)", "", "## 1. 조선 연말 이력 (TTM 순이익·PER·다음 12개월 주가)", "",
     "| 연말 | 종목 | TTM 순이익(억) | PER | 상위50 안 | 다음 12개월 |", "|---|---|---|---|---|---|"]
mdates = pd.Series(sorted(kt.mc.Date.unique()))
ye = mdates.groupby(mdates.dt.year).max()
for y, d in ye.items():
    if y < 2016 or y > 2025:
        continue
    day = kt.mc[kt.mc.Date == d].set_index("code")
    top50 = set(day.sort_values("Marcap", ascending=False).head(50).index)
    fu = kt.fund_at(d, CYC["조선"])
    f12 = fwd12(d, [c for c in CYC["조선"] if c in cf.columns])
    for c in CYC["조선"]:
        if c not in day.index or c not in fu.index:
            continue
        ni, mcap = fu.at[c, "ttm_ni"], day.at[c, "Marcap"]
        per = mcap / ni if ni and ni > 0 else np.nan
        fr = f12.get(c, np.nan) if f12 is not None else np.nan
        L.append(f"| {y} | {kt.names.get(c, c)} | {ni / 1e8:,.0f} | {'적자' if ni <= 0 else f'{per:.1f}'} | {'O' if c in top50 else ''} | "
                 f"{'' if fr != fr else f'{fr:+.0%}'} |")

# 2) 상태별 이후 12개월 (상위50 안 종목-월, 겹치는 창이라 t 없음)
rows = []
for d in kt.sig_dates:
    if d > pd.Timestamp("2025-08-31"):
        break
    s = kt.signals_at(d)
    f = fwd12(d, list(s.index))
    if f is None:
        continue
    bench = f.mean()
    ep = s["S2 E/P"]
    pos = ep[ep > 0]
    lo, hi = pos.quantile(1 / 3), pos.quantile(2 / 3)
    for c in s.index:
        e = ep.get(c, np.nan)
        if e != e:
            continue
        st = "적자" if e <= 0 else ("고PER(하위1/3 E/P)" if e < lo else ("저PER(상위1/3 E/P)" if e >= hi else "중간"))
        rows.append(dict(d=d, code=c, cyc=c in SECTOR, sector=SECTOR.get(c, "비경기"), state=st, x=f[c] - bench))
X = pd.DataFrame(rows)
X.to_csv(os.path.join(kt.HERE, "cyclical_states.csv"), index=False)
L += ["", "## 2. 상태별 이후 12개월 초과수익 (상위50 동일가중 대비, 2017-03~2025-08 월말, 겹치는 창)", "",
      "| 집단 | 상태 | 종목-월 | 종목 수 | 평균 | 중앙 |", "|---|---|---|---|---|---|"]
for cyc, lab in ((True, "경기민감"), (False, "그 밖")):
    for st in ["적자", "고PER(하위1/3 E/P)", "중간", "저PER(상위1/3 E/P)"]:
        x = X[(X.cyc == cyc) & (X.state == st)]
        if len(x):
            L.append(f"| {lab} | {st} | {len(x)} | {x.code.nunique()} | {x.x.mean():+.1%} | {x.x.median():+.1%} |")
L += ["", "## 3. 경기민감 업종별 (적자·고PER vs 저PER)", "", "| 업종 | 적자+고PER 평균 (n) | 저PER 평균 (n) |", "|---|---|---|"]
for sec in CYC:
    x = X[X.sector == sec]
    a = x[x.state.isin(["적자", "고PER(하위1/3 E/P)"])]
    b = x[x.state == "저PER(상위1/3 E/P)"]
    L.append(f"| {sec} | {a.x.mean():+.1%} ({len(a)}) | {b.x.mean():+.1%} ({len(b)}) |" if len(a) or len(b) else f"| {sec} | - | - |")
with open(os.path.join(kt.HERE, "cyclical_per.md"), "w") as fh:
    fh.write("\n".join(L) + "\n")
print("\n".join(L))
