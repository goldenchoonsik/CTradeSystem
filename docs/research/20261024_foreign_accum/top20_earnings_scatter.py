"""상위 20 종목: 순이익 변화(대칭) vs 외국인 지분율 3년 변화 산점도. 입력=top20_earnings_corr.csv.
  .venv/bin/python docs/research/20261024_foreign_accum/top20_earnings_scatter.py"""
import os, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.font_manager as fm
from scipy import stats
OUT = os.path.dirname(os.path.abspath(__file__))
[fm.fontManager.addfont(p) for p in ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")]; plt.rcParams["font.family"] = ["NanumGothic", "DejaVu Sans"]; plt.rcParams["axes.unicode_minus"] = False
INK, INK2, MUTED, GRID, SURF, UP, DOWN = "#0b0b0b", "#52514e", "#8a8985", "#e4e3df", "#fcfcfb", "#e34948", "#2a78d6"
R = pd.read_csv(f"{OUT}/top20_earnings_corr.csv"); sp = stats.spearmanr(R.순이익_대칭변화, R.지분변화)
fig, ax = plt.subplots(figsize=(10.5, 7.2), dpi=160, facecolor=SURF); ax.set_facecolor(SURF)
ax.axhline(0, color=INK2, linewidth=1); ax.axvline(0, color=INK2, linewidth=1); ax.grid(color=GRID, linewidth=0.8); ax.set_axisbelow(True)
cols = [UP if v > 0 else DOWN for v in R.지분변화]; ax.scatter(R.순이익_대칭변화, R.지분변화, s=70, c=cols, edgecolor=SURF, linewidth=1.5, zorder=3)
off = {"삼성전자": (8, 6), "SK하이닉스": (-8, -14), "SK스퀘어": (8, 7), "SK": (8, 5), "HD현대중공업": (-8, -15), "삼성SDI": (8, -3), "현대차": (8, -3), "한화에어로스페이스": (8, 5), "두산에너빌리티": (8, 4), "삼성물산": (-8, 6), "삼성생명": (-8, 8), "셀트리온": (8, 4), "삼성전기": (6, -15), "KB금융": (-8, -4), "신한지주": (8, 3), "삼성바이오로직스": (8, -4), "LG에너지솔루션": (8, -4), "기아": (-8, -14), "하나금융지주": (8, -13), "현대모비스": (-8, -15)}
for _, r in R.iterrows():
    dx, dy = off.get(r.종목, (6, 4)); ax.annotate(r.종목, (r.순이익_대칭변화, r.지분변화), xytext=(dx, dy), textcoords="offset points", fontsize=9, color=INK, ha="left" if dx > 0 else "right")
for sp_ in ("top", "right"): ax.spines[sp_].set_visible(False)
for sp_ in ("left", "bottom"): ax.spines[sp_].set_color(GRID)
ax.set_xlim(-1.18, 1.25); ax.set_ylim(-26, 15); ax.tick_params(colors=INK2, labelsize=9)
ax.set_xlabel("순이익 변화 (2023 회계연도 ~ 최근 12개월)   −1 = 큰 폭 감소·적자 전환   0 = 변화 없음   +1 = 큰 폭 증가·흑자 전환", color=INK2, fontsize=9.5)
ax.set_ylabel("외국인 지분율 3년 변화 (%p)", color=INK2, fontsize=10)
ax.set_title("시총 상위 20: 순이익 변화와 외국인 지분율 변화", loc="left", fontsize=13.5, color=INK, fontweight="bold", pad=26)
ax.text(0, 1.025, f"순위상관 {sp.statistic:+.2f} (p {sp.pvalue:.2f}, n=20) · 삼성전자·SK하이닉스 제외 +0.56 · 삼성SDI·현대차 제외 +0.10", transform=ax.transAxes, fontsize=9.5, color=INK2)
ax.text(0.99, 0.02, "대칭변화 = (최근12개월 − 2023) / (|2023| + |최근12개월|)  ·  빨강=지분 증가, 파랑=감소", transform=ax.transAxes, ha="right", fontsize=8.5, color=MUTED)
fig.subplots_adjust(left=0.08, right=0.97, top=0.89, bottom=0.10); fig.savefig(f"{OUT}/top20_earnings_scatter.png", facecolor=SURF)
