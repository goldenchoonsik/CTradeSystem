"""현재 위치표 — 각 신호의 최신값, 2000~ 역사 속 백분위, 개발 구간 3분위 위치.

  .venv/bin/python -I docs/research/20261022_samsung_single/current_state.py
"""
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from test_hypotheses import M, HYP, DEV  # noqa: E402
from shared.data_library.paths import data_vault_path  # noqa: E402

R = pd.read_csv(os.path.join(HERE, "results_C.csv")).set_index("h")
D = pd.read_parquet(data_vault_path("staging", "samsung_cycle", "derived", "daily.parquet"))
L = ["# 현재 위치 (2026-10-22 작성, 월말 신호는 2026-09 기준)", "",
     "| # | 신호 | 최신값 | 2000~ 백분위 | 개발 3분위 위치 | 판정 |", "|---|---|---|---|---|---|"]
for h, col, sign, pre, desc in HYP:
    s = M[col].dropna()
    v = s.iloc[-1]
    hist = s.loc["2000-01":]
    pct = (hist <= v).mean() * 100
    ref = M.loc[DEV[0]:DEV[1], col].dropna()
    if len(ref) < 24:
        ref = s
    lo, hi = ref.quantile(1 / 3), ref.quantile(2 / 3)
    pos = "상위 1/3" if v > hi else ("하위 1/3" if v <= lo else "중간")
    L.append(f"| {h} | {desc} | {v:+.3f} ({s.index[-1]}) | {pct:.0f} | {pos} | {R.loc[h, 'verdict']} |")
# 수준 지표
lv = [("D램 수출물가 전년비", "lvl_xpi_dram_usd_yoy"), ("플래시 수출물가 전년비", "lvl_xpi_flash_usd_yoy"),
      ("반도체 출하 전년비", "lvl_ship_yoy"), ("반도체 재고 전년비", "lvl_inv_yoy"), ("메모리 수출 전년비", "lvl_mem_export_yoy"),
      ("마이크론 매출총이익률", "lvl_mu_gm"), ("외국인 보유율(%)", "foreign_pct_prev"), ("우선주 할인율", "pref_disc"), ("PBR", "H8"), ("TTM PER", "H8b")]
L += ["", "| 수준 지표 | 최신 | 2000~ 백분위 | 최근 3개월 변화 |", "|---|---|---|---|"]
for nm, c in lv:
    s = M[c].dropna()
    v = s.iloc[-1]
    L.append(f"| {nm} | {v:.3f} | {(s.loc['2000-01':] <= v).mean() * 100:.0f} | {v - s.iloc[-4]:+.3f} |")
d = D.dropna(subset=["sam"])
pk = d.sam.loc["2025-01-01":].max()
L += ["", f"삼성 종가(Creon, 2026-10-19 기준 {d.sam.iloc[-1]:,.0f}원 — 2026-09-14~ NXT 20:00 종가) / 2026 고점 {pk:,.0f}원 대비 {d.sam.iloc[-1] / pk - 1:+.0%}"]
open(os.path.join(HERE, "results_current.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))
