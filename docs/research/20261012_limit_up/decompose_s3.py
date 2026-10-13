"""S3(상한가 확률 모델 상위 10) 당일 매매 손익 분해 — 상한가 간 종목 vs 나머지 (개발 구간, 비용 전·후).

  .venv/bin/python -W ignore docs/research/20261012_limit_up/decompose_s3.py
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
import trade_pre_lu as tp  # noqa: E402


def main():
    d = tp.prepare()
    ex = tp.exits(d)
    d["g1"], d["g3"] = ex["X1"], ex["X3"]
    idx = tp.model_top(d)
    s = d.loc[idx]
    s = s[(s.date <= tp.DEV_END) & s.g1.notna()].copy()
    L = [f"# S3 당일 매매 분해 (개발 2026-01~05, {len(s)}건)", "",
         "| 구분 | 건 | 비중 | 시가→종가(비용 전) 평균 | 중앙값 | 비용 | 비용 후 평균 | 전체 손익 기여(비용 전) |", "|---|---|---|---|---|---|---|---|"]
    tot = s.g1.sum()
    for lab, m in (("그날 상한가 마감", s.lu == 1), ("상한가 못 감", s.lu == 0), ("전체", s.lu >= 0)):
        x = s[m]
        L.append(f"| {lab} | {len(x)} | {len(x)/len(s):.0%} | {x.g1.mean()*100:+.2f}% | {x.g1.median()*100:+.2f}% | {x.cost.mean()*100:.2f}% | "
                 f"{(x.g1 - x.cost).mean()*100:+.2f}% | {x.g1.sum()/len(s)*100:+.2f}%p |")
    nl = s[s.lu == 0]
    L += ["", "상한가 못 간 종목의 당일(시가→종가) 분포:",
          f"- +10% 이상 {(nl.g1 >= 0.10).mean():.0%} · 0~+10% {nl.g1.between(0, 0.10, inclusive='left').mean():.0%} · 0~−10% {nl.g1.between(-0.10, 0, inclusive='left').mean():.0%} · −10% 이하 {(nl.g1 < -0.10).mean():.0%}",
          f"- 상한가 간 종목의 시가 = 상한가(전일 +30%)보다 평균 {(s[s.lu == 1].g1 / (1 + s[s.lu == 1].g1)).mean()*100:.1f}% 아래 "
          f"→ 전일 종가 대비 시가 갭 약 {(1.3 * (1 - (s[s.lu == 1].g1 / (1 + s[s.lu == 1].g1)).mean()) - 1)*100:+.0f}% (이미 오른 채 시작)"]
    out = "\n".join(L)
    print(out)
    with open(os.path.join(HERE, "results_decompose_s3.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    main()
