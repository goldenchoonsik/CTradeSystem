"""상한가 당일 누가 샀나 (개인·외국인·기관·프로그램 순매수) + 주체별 이후 5거래일 — 서술 분석.

이후 경로는 after5 와 같은 정의: 상한가 다음 날 시가 진입 → 그날부터 5거래일 종가.

  .venv/bin/python docs/research/20261012_limit_up/lu_flows.py
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "20261012_endgame_e"))
import after5  # noqa: E402
import analyze_limit_up as alu  # noqa: E402
import run_e  # noqa: E402


def main():
    d, uni = alu.features()
    fl = run_e.load_flows()
    d = d.merge(fl, on=["date", "code"], how="left")
    for c in ("frgn", "orgn", "prsn", "prog"):
        d[f"s_{c}"] = d[c] / d.val                          # 그날 거래대금 대비 순매수
    p = after5.paths(d)
    g = p.sort_values(["code", "date"]).groupby("code", sort=False)
    for c in ("s_frgn", "s_orgn", "s_prsn", "s_prog"):
        p[f"prev_{c}"] = g[c].shift(1)                      # 전날(=상한가 날) 수급
    lu_day = d[d.lu == 1]
    fail = d[(d.touch == 1) & (d.lu == 0)]
    L = ["# 상한가 당일 수급 (KIS 종목별 투자자·프로그램 순매수, 그날 거래대금 대비)", "",
         "| 구분 | 건 | 개인 순매수 | 외국인 | 기관 | 프로그램 | 개인 순매수>0 비율 | 외국인>0 | 기관>0 |", "|---|---|---|---|---|---|---|---|---|"]
    for lab, x in (("상한가 마감", lu_day), ("상한가 터치 후 풀림", fail), ("전체 종목-일", d)):
        x = x.dropna(subset=["s_prsn"])
        L.append(f"| {lab} | {len(x):,} | {x.s_prsn.median()*100:+.1f}% | {x.s_frgn.median()*100:+.1f}% | {x.s_orgn.median()*100:+.1f}% | "
                 f"{x.s_prog.median()*100:+.1f}% | {(x.s_prsn > 0).mean():.0%} | {(x.s_frgn > 0).mean():.0%} | {(x.s_orgn > 0).mean():.0%} |")
    nxt = p[p.r1 >= alu.LU].dropna(subset=["prev_s_prsn"])
    lead = np.select([(nxt.prev_s_frgn + nxt.prev_s_orgn > 0.02) & (nxt.prev_s_prsn < 0),
                      nxt.prev_s_prsn > 0.02], ["외국인+기관 순매수, 개인 순매도", "개인 순매수 2%+"], default="그 외")
    L += ["", "## 상한가 다음 날 시가 진입 → 5거래일 (상한가 날 주체별)", "",
          "| 상한가 날 주체 | 건 | 갭 | 1일차 | 5일차 | 5일 −10%↓ | 5일 안 상한가 | 비용 후 5일 평균(신호일 가중) |", "|---|---|---|---|---|---|---|---|"]
    for k in ("외국인+기관 순매수, 개인 순매도", "개인 순매수 2%+", "그 외"):
        x = nxt[lead == k]
        if len(x) < 30:
            continue
        L.append(f"| {k} | {len(x):,} | {x.gap.median()*100:+.1f}% | {x.d1.median()*100:+.1f}% | {x.d5.median()*100:+.1f}% | {(x.d5 <= -0.10).mean():.0%} | "
                 f"{x.any_lu.mean():.0%} | {x.groupby('date').net5.mean().mean()*100:+.2f}% |")
    out = "\n".join(L)
    print(out)
    with open(os.path.join(HERE, "results_lu_flows.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    main()
