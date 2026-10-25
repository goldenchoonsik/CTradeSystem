"""현재 시총 상위 20(보통주) 외국인 지분율 3년 변화 — 표(CSV)·그래프(PNG 2장). 지분율 기준일=gold 마지막 날, 3년 전=그 날짜의 3년 전 이전 마지막 거래일.
  .venv/bin/python docs/research/20261024_foreign_accum/top20_3y.py"""
import os, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.font_manager as fm
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); OUT = os.path.dirname(os.path.abspath(__file__))
V = f"{ROOT}/DataVault/staging/kr_equity"; N = 20
[fm.fontManager.addfont(p) for p in ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")]; plt.rcParams["font.family"] = ["NanumGothic", "DejaVu Sans"]; plt.rcParams["axes.unicode_minus"] = False
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#8a8985", "#e4e3df", "#fcfcfb"; UP, DOWN, NEUT = "#e34948", "#2a78d6", "#52514e"   # 한국 관례: 증가=빨강, 감소=파랑
g = pd.read_parquet(f"{V}/derived/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"]).dropna(subset=["market_cap", "foreign_hold_pct"])
nm = pd.read_csv(f"{V}/reference/listed_snapshot.csv").set_index("code"); pref = set(nm.index[nm.is_preferred.astype(str) != "0"]); g = g[~g.code.isin(pref)]
end = g.date.max(); start = end - pd.DateOffset(years=3)
top = g[g.date == end].nlargest(N, "market_cap"); codes = list(top.code)
s = g[g.code.isin(codes) & (g.date >= start - pd.Timedelta(days=7))].copy(); s["ym"] = s.date.dt.to_period("M")
mon = s.sort_values("date").groupby(["code", "ym"]).foreign_hold_pct.last().unstack(0)[codes]
at = lambda c, d: g[(g.code == c) & (g.date <= d)].sort_values("date").foreign_hold_pct.iloc[-1]
rows = []
for r, c in enumerate(codes, 1):
    f0, f1, f3 = at(c, end), at(c, end - pd.DateOffset(years=1)), at(c, start); ser = mon[c].dropna(); dm = ser.diff().dropna()
    rows.append({"순위": r, "종목": nm.name.get(c, c), "코드": c, "시총(조)": top.set_index("code").market_cap[c] / 1e12, "3년전(%)": f3, "1년전(%)": f1, "현재(%)": f0, "3년변화(%p)": f0 - f3, "최근1년(%p)": f0 - f1,
                 "3년 최고(%)": ser.max(), "3년 최저(%)": ser.min(), "오른달비율(%)": (dm > 0).mean() * 100})
T = pd.DataFrame(rows); T.to_csv(f"{OUT}/top20_foreign_3y.csv", index=False, encoding="utf-8-sig")
print(f"기준일 {end.date()} (3년 전 {start.date()}), 상위 {N}개 시총 합 {T['시총(조)'].sum():.0f}조"); print(T.round(1).to_string(index=False))
# ---- 그림 1: 3년 변화폭(막대), 변화 큰 순 ----
D = T.sort_values("3년변화(%p)")
fig, ax = plt.subplots(figsize=(10, 7.6), dpi=160, facecolor=SURF); ax.set_facecolor(SURF)
y = np.arange(len(D)); cols = [UP if v > 0 else DOWN for v in D["3년변화(%p)"]]
ax.barh(y, D["3년변화(%p)"], color=cols, height=0.62, edgecolor=SURF, linewidth=1.2)
for yi, (_, r) in zip(y, D.iterrows()):
    v = r["3년변화(%p)"]; ax.text(v + (0.5 if v >= 0 else -0.5), yi, f"{v:+.1f}%p", va="center", ha="left" if v >= 0 else "right", fontsize=9.5, color=INK, fontweight="bold")
    ax.text(1.0, yi, f"{r['3년전(%)']:.1f}% → {r['현재(%)']:.1f}%", transform=ax.get_yaxis_transform(), va="center", ha="left", fontsize=9, color=INK2, family="DejaVu Sans")
ax.set_yticks(y); ax.set_yticklabels([f"{r['종목']} ({int(r['순위'])})" for _, r in D.iterrows()], fontsize=10, color=INK)
ax.axvline(0, color=INK2, linewidth=1); ax.grid(axis="x", color=GRID, linewidth=0.8); ax.set_axisbelow(True)
for sp in ("top", "right", "left"): ax.spines[sp].set_visible(False)
ax.spines["bottom"].set_color(GRID); ax.tick_params(axis="x", colors=INK2, labelsize=9); ax.tick_params(axis="y", length=0)
ax.set_xlim(D["3년변화(%p)"].min() - 8, D["3년변화(%p)"].max() + 8); ax.set_xlabel("외국인 지분율 변화 (%p)", color=INK2, fontsize=10)
ax.set_title(f"시총 상위 20 종목 외국인 지분율 3년 변화  (괄호=현재 시총 순위)", loc="left", fontsize=13.5, color=INK, fontweight="bold", pad=26)
ax.text(0, 1.02, f"{start.date()} ~ {end.date()}  ·  빨강=증가, 파랑=감소  ·  우측 숫자=3년 전 / 현재 지분율", transform=ax.transAxes, fontsize=9.5, color=INK2)
ax.text(1.0, -0.115, "우선주 제외 · 보유 수량 기준(비거래성 변동 포함)", transform=ax.transAxes, ha="right", fontsize=8.5, color=MUTED)
fig.subplots_adjust(left=0.22, right=0.82, top=0.89, bottom=0.10); fig.savefig(f"{OUT}/top20_foreign_3y_change.png", facecolor=SURF); plt.close(fig)
# ---- 그림 2: 종목별 월별 추이(작은 배수), 현재 시총 순 ----
fig, axs = plt.subplots(4, 5, figsize=(14, 8.6), dpi=140, facecolor=SURF)
for ax, (_, r) in zip(axs.ravel(), T.iterrows()):
    ser = mon[r["코드"]].dropna(); x = np.arange(len(ser)); col = UP if r["3년변화(%p)"] > 0 else DOWN; ax.set_facecolor(SURF)
    ax.plot(x, ser.values, color=NEUT, linewidth=1.8, solid_capstyle="round"); ax.scatter([x[0]], [ser.iloc[0]], s=22, color=SURF, edgecolor=NEUT, zorder=3, linewidth=1.4); ax.scatter([x[-1]], [ser.iloc[-1]], s=34, color=col, edgecolor=SURF, linewidth=1.4, zorder=4)
    pad = max((ser.max() - ser.min()) * 0.35, 1.2); ax.set_ylim(ser.min() - pad, ser.max() + pad)
    ax.set_title(f"{int(r['순위'])}. {r['종목']}", loc="left", fontsize=10.5, color=INK, fontweight="bold", pad=19)
    ax.text(0.0, 1.03, f"{ser.iloc[0]:.1f} → {ser.iloc[-1]:.1f}  ({r['3년변화(%p)']:+.1f}%p)", transform=ax.transAxes, va="bottom", fontsize=9, color=col, fontweight="bold", family="DejaVu Sans")
    ax.grid(axis="y", color=GRID, linewidth=0.7); ax.set_axisbelow(True)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"): ax.spines[sp].set_color(GRID)
    ax.set_xticks([0, 12, 24, len(ser) - 1]); ax.set_xticklabels([str(ser.index[i])[2:] for i in (0, 12, 24, len(ser) - 1)], fontsize=8, color=INK2); ax.tick_params(axis="y", labelsize=8, colors=INK2, length=0); ax.tick_params(axis="x", length=2, color=GRID)
fig.suptitle("시총 상위 20 종목 외국인 지분율 월별 추이 (최근 3년, %)", x=0.012, ha="left", fontsize=14, color=INK, fontweight="bold")
fig.text(0.012, 0.935, "종목마다 세로 축 범위가 다름 · 속 빈 점=3년 전, 색 점=현재(빨강=증가, 파랑=감소) · 번호=현재 시총 순위", fontsize=9.5, color=INK2)
fig.subplots_adjust(left=0.04, right=0.99, top=0.87, bottom=0.05, hspace=0.78, wspace=0.22); fig.savefig(f"{OUT}/top20_foreign_3y_trend.png", facecolor=SURF); plt.close(fig)
print("저장:", [f for f in os.listdir(OUT) if f.startswith("top20_")])
