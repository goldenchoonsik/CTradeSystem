"""R2(두 종목 듀얼 모멘텀) 합격 후 점검 — 이웃 파라미터 고원·판단일 이동·연도별·보유 구성 (판정 아님, 사전등록 5절 보고용).

실행: .venv/bin/python -I docs/research/20261021_samsung_hynix_swing/r2_plateau.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swing_test as st  # noqa: E402

S, H, idx = st.S, st.H, st.idx


def r2_weights(lb, points, absolute=True):
    cs, ch = S["close"], H["close"]
    rs, rh = cs / cs.shift(lb) - 1, ch / ch.shift(lb) - 1
    win_s = rs >= rh
    ws = win_s & ((rs > 0) if absolute else True)
    wh = ~win_s & rh.notna() & ((rh > 0) if absolute else True)
    return st.at_points(ws, points), st.at_points(wh, points)


def summary(ws, wh):
    r, a = st.build("ROT", (ws, wh))
    b = st.BENCH["ROT"]
    out = {}
    for lab, per in (("dev", st.DEV), ("hold", st.HOLD), ("full", st.FULL)):
        rr = st.sl(r, per)
        bb = st.sl(b, per).reindex(rr.index)
        aa = st.sl(a, per).reindex(rr.index)
        f = aa.mean()
        out[lab] = (st.metrics(rr), st.metrics(bb), st.tstat(st.monthly(rr) - st.monthly(f * bb)), f)
    return out, r, a


def fmt(o):
    (c, m, s), (bc, bm, bs), tb, f = o
    return f"{c:5.1%} MDD{m:4.0%} 샤프{s:4.2f} (보유 {bc:5.1%}) tB{tb:5.2f} 노출{f:4.0%}"


lines = ["# R2 듀얼 모멘텀 점검 (판정 아님)", ""]
day_in_month = pd.Series(idx.to_period("M"), index=idx)
pos_in_month = day_in_month.groupby(day_in_month).cumcount()
lines.append("## 1. 비교 기간 이웃 (월말 판단)")
for lb in (126, 189, 252, 315, 378):
    o, _, _ = summary(*r2_weights(lb, st.is_me))
    lines.append(f"- {lb}일: 개발 {fmt(o['dev'])} | 검증 {fmt(o['hold'])} | 전체 {fmt(o['full'])}")
lines.append("")
lines.append("## 2. 판단일 이동 (252일)")
for k in (0, 5, 10, 15):
    pts = st.is_me if k == 0 else (pos_in_month == k - 1)
    o, _, _ = summary(*r2_weights(252, pts))
    lines.append(f"- {'월말' if k == 0 else f'매월 {k}번째 거래일'}: 개발 {fmt(o['dev'])} | 검증 {fmt(o['hold'])} | 전체 {fmt(o['full'])}")
lines.append("")
lines.append("## 3. 구성요소 분해 (252일, 월말)")
o, _, _ = summary(*r2_weights(252, st.is_me, absolute=False))
lines.append(f"- 상대 모멘텀만(항상 100% 투자): 개발 {fmt(o['dev'])} | 검증 {fmt(o['hold'])} | 전체 {fmt(o['full'])}")
row = pd.read_csv(os.path.join(st.HERE, "results.csv")).set_index(["target", "rule"])
t7 = row.loc[("PAIR", "T7 12개월 모멘텀")]
lines.append(f"- 절대 모멘텀만(각 다리 6:4 유지, T7 PAIR): 개발 {t7.dev_cagr:.1%} 검증 {t7.hold_cagr:.1%} 전체 {t7.full_cagr:.1%} MDD {t7.full_mdd:.0%}")
lines.append("")

o, r, a = summary(*r2_weights(252, st.is_me))
ws, wh = r2_weights(252, st.is_me)
ws, wh = ws.shift(1).loc[st.FULL[0]:st.FULL[1]], wh.shift(1).loc[st.FULL[0]:st.FULL[1]]
lines.append(f"## 4. 보유 구성 (2000~): 삼성 {ws.mean():.0%} · 하이닉스 {wh.mean():.0%} · 현금 {1 - ws.mean() - wh.mean():.0%} 의 날")
lines.append("")
lines.append("## 5. 연도별 (R2 vs 6:4 보유, 비용 0.45% 반영)")
lines.append("| 연도 | R2 | 보유 | 차 | 주 보유 |")
lines.append("|---|---|---|---|---|")
rr = st.sl(r, st.FULL)
bb = st.sl(st.BENCH["ROT"], st.FULL).reindex(rr.index)
yr = (1 + rr).groupby(rr.index.year).prod() - 1
yb = (1 + bb).groupby(bb.index.year).prod() - 1
for y in yr.index:
    s_, h_ = ws[str(y)].mean(), wh[str(y)].mean()
    lines.append(f"| {y} | {yr[y]:.0%} | {yb[y]:.0%} | {yr[y] - yb[y]:+.0%} | 삼성 {s_:.0%}·하닉 {h_:.0%}·현금 {1 - s_ - h_:.0%} |")
d = yr - yb
lines.append("")
lines.append(f"R2 우위 해 {(d > 0).sum()}/{len(d)}, 연도별 차 평균 {d.mean():+.1%} t={d.mean() / d.std(ddof=1) * np.sqrt(len(d)):.2f}; 2000~03 제외 평균 {d.loc[2004:].mean():+.1%} t={d.loc[2004:].mean() / d.loc[2004:].std(ddof=1) * np.sqrt(len(d.loc[2004:])):.2f}")
lines.append("")
lines.append("## 6. 현재 신호 (2026-09-11 종가 기준 마지막 월말 판단)")
cs, ch = S["close"], H["close"]
me_last = st.is_me[st.is_me].index[-1]
lines.append(f"- 마지막 월말 {me_last.date()}: 삼성 252일 {cs[me_last] / cs.shift(252)[me_last] - 1:+.0%}, 하이닉스 {ch[me_last] / ch.shift(252)[me_last] - 1:+.0%} → "
             f"{'삼성' if ws.iloc[-1] > 0 else ('하이닉스' if wh.iloc[-1] > 0 else '현금')} 보유 중")

with open(os.path.join(st.HERE, "results_r2_check.md"), "w") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
