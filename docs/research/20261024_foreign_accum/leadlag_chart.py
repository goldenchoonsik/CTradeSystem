"""leadlag_earnings.csv 그래프. .venv/bin/python docs/research/20261024_foreign_accum/leadlag_chart.py"""
import os, pandas as pd, numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.font_manager as fm
OUT = os.path.dirname(os.path.abspath(__file__))
[fm.fontManager.addfont(p) for p in ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")]; plt.rcParams["font.family"] = ["NanumGothic", "DejaVu Sans"]; plt.rcParams["axes.unicode_minus"] = False
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#8a8985", "#e4e3df", "#fcfcfb"; C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
R = pd.read_csv(f"{OUT}/leadlag_earnings.csv")
fig, ax = plt.subplots(figsize=(10.5, 6.4), dpi=160, facecolor=SURF); ax.set_facecolor(SURF)
ax.axhline(0, color=INK2, linewidth=1); ax.axvline(0, color=MUTED, linewidth=0.8, linestyle=(0, (3, 3))); ax.grid(axis="y", color=GRID, linewidth=0.8); ax.set_axisbelow(True)
for col, c, lab in (("주가", C3, "주가 수익(4분기)"), ("지분_원시", C1, "외국인 지분 변화(4분기)"), ("지분_주가통제", C2, "외국인 지분 변화, 주가 통제 후")):
    i = R[col].idxmax(); ax.plot(R.k, R[col], color=c, linewidth=2.2, marker="o", markersize=5, markeredgecolor=SURF, markeredgewidth=1.2, solid_capstyle="round", label=f"{lab} — 최대 {R.loc[i, col]:+.2f} (k={int(R.loc[i, 'k'])})")
ax.legend(loc="upper left", frameon=False, fontsize=9.5, labelcolor=INK)
for sp in ("top", "right"): ax.spines[sp].set_visible(False)
for sp in ("left", "bottom"): ax.spines[sp].set_color(GRID)
ax.set_xticks(range(-8, 9)); ax.tick_params(colors=INK2, labelsize=9); ax.set_xlim(-8.5, 8.5)
ax.set_xlabel("이익 변화 구간의 위치 (분기)   ◀ 이익이 앞(지분이 후행)   0 = 같은 구간   이익이 뒤(지분이 선행) ▶", color=INK2, fontsize=9.5)
ax.set_ylabel("이익 변화와의 순위상관(분기별 평균)", color=INK2, fontsize=10)
ax.set_title("외국인 지분 변화는 이익보다 앞서는가 — 시총 상위 200, 2016~2025", loc="left", fontsize=13.5, color=INK, fontweight="bold", pad=26)
ax.text(0, 1.025, "분기마다 상위 200개 종목의 순위상관을 구해 평균 · 이익 = 순이익 LTM YoY 대칭변화 · 양수 k = 이익 구간이 지분 변화 구간보다 k분기 뒤", transform=ax.transAxes, fontsize=9, color=INK2)
fig.subplots_adjust(left=0.09, right=0.97, top=0.88, bottom=0.12); fig.savefig(f"{OUT}/leadlag_earnings.png", facecolor=SURF)
