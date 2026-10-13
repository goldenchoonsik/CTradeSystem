"""D4 — 연말 대주주 양도세 회피 매도·1월 반등: 서술 통계만(표본 12월 2회, 판정 안 함). PREREGISTRATION.md 참고.

  .venv/bin/python docs/research/20261012_endgame_dr/run_d4_descriptive.py
"""
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "docs", "research", "20261011_vol_spike"))
import compare_algos  # noqa: E402


def main():
    compare_algos.END = pd.Timestamp("2026-10-08")
    d = compare_algos.load_daily()
    uni = pd.read_csv(os.path.join(compare_algos.STAGE, "universe.csv")).set_index("code")
    c = d.pivot(index="date", columns="code", values="c")
    val = d.pivot(index="date", columns="code", values="val").rolling(60, min_periods=40).median()
    lines = ["# D4 연말 매도·1월 반등 — 서술 통계 (판정 아님, 12월 2회)", "",
             "구간 수익 = 동일가중(실제가 ≥ 1,000원). 소형 = 11월 말 기준 60일 거래대금 하위 50%, 대형 = 상위 10%.", "",
             "| 연말 | 구간 | 소형 KOSDAQ | 소형 KOSPI | 대형 | 소형−대형 |", "|---|---|---|---|---|---|"]
    raw = d.pivot(index="date", columns="code", values="raw_close")
    for y in (2024, 2025):
        nov = c.index[c.index <= pd.Timestamp(y, 11, 30)][-1]
        ok = raw.loc[nov] >= 1000
        rk = val.loc[nov][ok].rank(pct=True)
        small, large = rk[rk <= 0.5].index, rk[rk >= 0.9].index
        dec_days = c.index[(c.index.year == y) & (c.index.month == 12)]
        jan_days = c.index[(c.index.year == y + 1) & (c.index.month == 1)]
        windows = {"12월 둘째 주→12월 말": (dec_days[dec_days >= pd.Timestamp(y, 12, 10)][0], dec_days[-1]),
                   "12월 말→1월 15일": (dec_days[-1], jan_days[jan_days <= pd.Timestamp(y + 1, 1, 15)][-1])}
        for lab, (a, b) in windows.items():
            r = c.loc[b] / c.loc[a] - 1
            sk = r[[x for x in small if uni.market.get(x) == "KOSDAQ"]].mean()
            sp = r[[x for x in small if uni.market.get(x) == "KOSPI"]].mean()
            lg = r[large].mean()
            lines.append(f"| {y} | {lab} ({a:%m-%d}→{b:%m-%d}) | {sk*100:+.2f}% | {sp*100:+.2f}% | {lg*100:+.2f}% | {(sk+sp)/2*100 - lg*100:+.2f}%p |")
    out = "\n".join(lines)
    print(out)
    with open(os.path.join(os.path.dirname(__file__), "results_d4.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    main()
