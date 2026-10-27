"""미국 증시 연대기 정적 차트 (1928~2026): 대통령·정당 띠 / S&P500(로그)+침체·약세장·전쟁·폭락일 / 금리 / 인플레 / 신용스프레드 / CAPE.
  .venv/bin/python docs/research/20261026_market_anomalies/chronicle_png.py"""
import os, json, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt, matplotlib.dates as md
from matplotlib import font_manager as fm
OUT = os.path.dirname(os.path.abspath(__file__)); J = json.load(open(f"{OUT}/chronicle_data.json")); m = pd.DataFrame(J["months"]); m["d"] = pd.PeriodIndex(m.ym, freq="M").to_timestamp(how="end")
plt.rcParams["font.family"] = "NanumGothic" if any("NanumGothic" in f.name for f in fm.fontManager.ttflist) else "sans-serif"; plt.rcParams["axes.unicode_minus"] = False
INK, MUTED, GRID = "#1f2937", "#6b7280", "#e5e7eb"; C_SP, C_10, C_3, C_INF, C_CR, C_CAPE = "#1f2937", "#0f766e", "#d97706", "#7c3aed", "#b45309", "#0369a1"; DEM, REP = "#93c5fd", "#fca5a5"
fig, axs = plt.subplots(6, 1, figsize=(16, 14), sharex=True, gridspec_kw={"height_ratios": [0.55, 4, 1.7, 1.3, 1.1, 1.3], "hspace": 0.12}); a0, a1, a2, a3, a4, a5 = axs
t0, t1 = pd.Timestamp("1928-01-01"), pd.Timestamp("2026-12-31")
# 대통령 띠
terms = J["terms"]; ends = [t["start"] for t in terms[1:]] + ["2026-10"]
for t, e in zip(terms, ends):
    s, e = pd.Timestamp(t["start"] + "-01"), pd.Timestamp(e + "-01"); s = max(s, t0)
    a0.axvspan(s, e, color=DEM if t["party"] == "D" else REP, lw=0); mid = s + (e - s) / 2
    if (e - s).days > 1100: a0.text(mid, 0.5, t["name"].replace("F.Roosevelt", "FDR").replace("G.H.W.Bush", "Bush").replace("G.W.Bush", "Bush").replace("L.Johnson", "L.Johnson"), ha="center", va="center", fontsize=7.5, color=INK, rotation=0)
a0.set_ylim(0, 1); a0.set_yticks([]); a0.set_ylabel("집권", rotation=0, ha="right", va="center", color=INK, fontsize=9); a0.text(1.001, 0.75, "민주", transform=a0.transAxes, color="#3b82f6", fontsize=8, va="center"); a0.text(1.001, 0.25, "공화", transform=a0.transAxes, color="#ef4444", fontsize=8, va="center")
for e in J["elections"]:
    d = pd.Timestamp(e["date"]); 
    if d < t0 or d > t1: continue
    if e["kind"] == "중간선거": a0.plot([d], [0.06], marker="D", ms=3.2, color=INK, zorder=5)
a0.text(0.0, 1.12, "◆ = 중간선거일(11월 첫 월요일 다음 화요일)", transform=a0.transAxes, fontsize=8, color=MUTED)
# S&P
rec = m[m.rec == 1]; 
for ax in axs[1:]:
    for _, g in rec.groupby((rec.index.to_series().diff() != 1).cumsum()): ax.axvspan(g.d.iloc[0] - pd.Timedelta(days=28), g.d.iloc[-1], color="#e5e7eb", alpha=0.75, lw=0, zorder=0)
a1.set_yscale("log"); a1.plot(m.d, m.sp, color=C_SP, lw=1.4, zorder=3)
for b in J["bears"]:
    s, e = pd.Timestamp(b["start"]), pd.Timestamp(b["end"]); a1.axvspan(s, e, color="#fecaca", alpha=0.42, lw=0, zorder=1)
    if b["drop"] <= -0.30 and not (pd.Timestamp("1929-01-01") <= s <= pd.Timestamp("1942-12-31") and b["drop"] > -0.5): a1.text(e, 2.55, f"{b['drop']*100:.0f}%".replace("-", "-"), fontsize=7, color="#b91c1c", ha="center")
for c in J["crashes"]: a1.plot([pd.Timestamp(c["date"])], [m.sp[m.d <= pd.Timestamp(c["date"]) + pd.Timedelta(days=31)].iloc[-1] * 1.0], marker="o", ms=3.5, color="#dc2626", zorder=4)
lab0 = {"독일 폴란드 침공": "WWII 발발", "진주만": "진주만", "한국전쟁": "한국전", "통킹만": "통킹만", "욤키푸르": "욤키푸르", "포클랜드": "포클랜드", "쿠웨이트 침공": "걸프", "걸프전 공습": "걸프공습", "코소보": "코소보", "9/11": "9/11", "아프간": "아프간", "이라크전": "이라크", "크림반도": "크림", "우크라이나": "우크라이나", "하마스 공격": "하마스", "이스라엘-이란": "이-이란"}
lab = lab0
for i, w in enumerate(J["wars"]):
    d = pd.Timestamp(w["date"]); a1.plot([d, d], [2.9, 3.6], color="#b45309", lw=1.1, zorder=2); a1.text(d, 3.9 if i % 2 == 0 else 5.6, lab.get(w["name"], ""), fontsize=7, color="#92400e", ha="center", rotation=90, va="bottom")
a1.set_ylim(2.4, 14000); a1.set_yticks([3, 10, 30, 100, 300, 1000, 3000, 10000]); a1.set_yticklabels(["3", "10", "30", "100", "300", "1,000", "3,000", "10,000"]); a1.set_ylabel("S&P500 (로그, 월말)", color=INK)
a1.text(0.005, 0.965, "회색 음영=NBER 경기침체 · 분홍 음영=약세장(고점 대비 -20% 이상) · 빨간 점=하루 -7% 이하 · 갈색 막대=전쟁·위기 발발(사전 지정 16건)", transform=a1.transAxes, fontsize=8.5, color=MUTED, va="top")
# 금리
a2.axhline(0, color=MUTED, lw=0.6); a2.plot(m.d, m.gs10, color=C_10, lw=1.3, label="10년 국채"); a2.plot(m.d, m.tbl, color=C_3, lw=1.1, label="3개월 단기"); a2.set_ylabel("금리 (%)", color=INK); a2.legend(frameon=False, loc="upper left", fontsize=8, ncol=2)
a3.axhline(0, color=MUTED, lw=0.6); a3.plot(m.d, m.infl.clip(-12, 20), color=C_INF, lw=1.2); a3.set_ylabel("소비자물가\n전년비 (%)", color=INK, fontsize=9)
a4.plot(m.d, m.credit, color=C_CR, lw=1.2); a4.set_ylabel("신용 스프레드\n(Baa-국채10년, %p)", color=INK, fontsize=8.5)
a5.plot(m.d, m.cape, color=C_CAPE, lw=1.3); a5.axhline(m.cape.median(), color=MUTED, lw=0.7, ls="--"); a5.text(pd.Timestamp("1992-06-01"), m.cape.median() - 4, f"역대 중앙 {m.cape.median():.1f}", fontsize=7.5, color=MUTED); a5.set_ylabel("CAPE", color=INK)
a5.annotate(f"지금 {m.cape.iloc[-1]:.1f}", (m.d.iloc[-1], m.cape.iloc[-1]), xytext=(-60, -4), textcoords="offset points", fontsize=8, color=C_CAPE)
for ax in axs[1:]:
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax.grid(axis="y", color=GRID, lw=0.7); ax.tick_params(colors=MUTED)
for s in ("top", "right", "left", "bottom"): a0.spines[s].set_visible(False)
a5.xaxis.set_major_locator(md.YearLocator(10)); a5.xaxis.set_major_formatter(md.DateFormatter("%Y")); a5.set_xlim(t0, t1)
fig.suptitle("미국 증시 연대기 1928~2026 — 집권·금리·인플레·신용·밸류에이션과 주가", x=0.06, ha="left", fontsize=15, color=INK, y=0.925)
fig.savefig(f"{OUT}/chronicle.png", dpi=130, bbox_inches="tight"); print("saved")
