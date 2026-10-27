"""미국 4년 대선 주기 포개기: S&P500(^GSPC 가격, 1927-12~2026-10) 24개 완결 주기(1929~2024)를 주기 시작(1년차 1월)부터 48개월로 정렬해 평균 경로·월/분기별 평균·저점/고점 위치·시대/정당 분할 확인.
  .venv/bin/python docs/research/20261026_market_anomalies/cycle_overlay.py"""
import os, json, numpy as np, pandas as pd
from math import sqrt
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); OUT = os.path.dirname(__file__)
j = json.load(open(f"{ROOT}/DataVault/staging/us_valuation/raw/yahoo/GSPC_20261023.json"))["chart"]["result"][0]
P = pd.Series(j["indicators"]["quote"][0]["close"], index=pd.to_datetime(j["timestamp"], unit="s").normalize()).dropna(); P = P[~P.index.duplicated()]
M = P.resample("ME").last(); M = M[M.index <= "2026-09-30"]; r = M.pct_change().dropna()          # 월수익 (2026-10 미완료 제외)
starts = list(range(1929, 2025, 4)); party = dict(zip(starts, "RDDDDDRRDDRRDRRRDDRRDDRD"))
cyc = {}
for y in starts:
    x = r[f"{y}-01-01":f"{y+3}-12-31"]
    if len(x) == 48: cyc[y] = x.values
C = pd.DataFrame(cyc, index=range(1, 49)).T; print("완결 주기", len(C), C.index[0], "~", C.index[-1])
def t1(x): x = np.asarray(x, float); return x.mean() / (x.std(ddof=1) / sqrt(len(x)))
allm = C.values.mean()
print("전체 월평균 %.2f%%" % (allm * 100))
# 1) 분기(주기 내 3개월 단위 16개)별
q = pd.DataFrame({k: (1 + C.iloc[:, (k-1)*3:k*3]).prod(axis=1) - 1 for k in range(1, 17)}); base = (1 + allm) ** 3 - 1
print("\n== 주기 내 분기별 수익 (24주기; 평소 분기 %.1f%%) ==" % (base * 100))
for k in range(1, 17):
    s = q[k]; print(f"  {(k-1)//4+1}년차 {(k-1)%4+1}분기: 평균 {s.mean()*100:5.1f}% 중앙 {s.median()*100:5.1f}% 양수 {(s>0).sum():2d}/24 t(평균−평소)={(s.mean()-base)/(s.std(ddof=1)/sqrt(24)):5.2f}")
# 2) 월별(1~48) 중 상·하위
mm = pd.DataFrame({"mean": C.mean() * 100, "pos": (C > 0).mean() * 100, "t": [(t1(C[i] - allm)) for i in C.columns]}); mm["연차"] = (mm.index - 1) // 12 + 1; mm["달"] = (mm.index - 1) % 12 + 1
print("\n== 주기 내 월별 상위 5·하위 5 (평균 월수익%) =="); print(mm.sort_values("mean", ascending=False).head(5).round(2).to_string()); print(mm.sort_values("mean").head(5).round(2).to_string())
# 3) 누적 경로의 저점·고점 월
cum = (1 + C).cumprod(axis=1); cum.columns = range(1, 49); lo = cum.idxmin(axis=1); hi = cum.idxmax(axis=1)
print("\n== 주기별 48개월 누적 경로의 최저·최고 시점 (월 번호 1~48) ==")
print("  최저 분포(연차별): ", ((lo - 1) // 12 + 1).value_counts().sort_index().to_dict(), "| 최고 분포: ", ((hi - 1) // 12 + 1).value_counts().sort_index().to_dict())
print("  최저가 2년차에: %d/24, 최고가 4년차에: %d/24, 최저가 1·2년차에: %d/24, 최고가 3·4년차에: %d/24" % (((lo-1)//12+1 == 2).sum(), ((hi-1)//12+1 == 4).sum(), ((lo-1)//12+1 <= 2).sum(), ((hi-1)//12+1 >= 3).sum()))
print("  중간선거 해(25~36개월 중) 최저점 월 분포:", ((lo[((lo-1)//12+1) == 2] - 1) % 12 + 1).value_counts().sort_index().to_dict())
# 4) 연차별 + 반 주기
yy = pd.DataFrame({k: (1 + C.iloc[:, (k-1)*12:k*12]).prod(axis=1) - 1 for k in range(1, 5)})
print("\n== 연차별 (평균/중앙/양수) =="); [print(f"  {k}년차: {yy[k].mean()*100:5.1f}% / {yy[k].median()*100:5.1f}% / {(yy[k]>0).sum()}/24") for k in range(1, 5)]
h1, h2 = (1 + C.iloc[:, :24]).prod(axis=1) - 1, (1 + C.iloc[:, 24:]).prod(axis=1) - 1
print(f"  전반 2년 평균 {h1.mean()*100:.1f}% (양수 {(h1>0).sum()}/24), 후반 2년 평균 {h2.mean()*100:.1f}% (양수 {(h2>0).sum()}/24), 후반>전반 {(h2>h1).sum()}/24 주기")
# 5) 시대·정당 분할
for nm, idx in (("1929~1973 (12주기)", C.index[:12]), ("1977~2021 (12주기)", C.index[12:])):
    print(f"  {nm}: " + " ".join(f"{k}년차 {yy.loc[idx,k].mean()*100:.1f}%" for k in range(1, 5)))
for pnm, pc in (("공화당 집권", "R"), ("민주당 집권", "D")):
    idx = [y for y in C.index if party[y] == pc]; print(f"  {pnm} {len(idx)}주기: " + " ".join(f"{k}년차 {yy.loc[idx,k].mean()*100:.1f}%" for k in range(1, 5)) + f" | 전체 4년 평균 {((1+C.loc[idx]).prod(axis=1)-1).mean()*100:.0f}%")
# 6) 현재 주기 (2025~)
cur = r["2025-01-01":]; cc = (1 + cur).cumprod(); print(f"\n현재 주기: 2025-01 이후 {len(cur)}개월 경과(월 번호 1~{len(cur)}), 누적 {((cc.iloc[-1]-1)*100):.1f}%; 과거 평균 같은 시점 누적 {(cum[len(cur)].mean()-1)*100:.1f}% 중앙 {(cum[len(cur)].median()-1)*100:.1f}%")
# 그림
plt.rcParams["font.family"] = "NanumGothic" if any("NanumGothic" in f.name for f in fm.fontManager.ttflist) else "sans-serif"; plt.rcParams["axes.unicode_minus"] = False
INK, MUTED, GRID, ACC = "#1f2937", "#6b7280", "#e5e7eb", "#2563eb"
fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [1.5, 1]})
x = np.arange(0, 49)
for y in C.index: a1.plot(x, np.r_[1, cum.loc[y].values] * 100 - 100, color="#cbd5e1", lw=0.8)
mean = np.r_[1, cum.mean().values] * 100 - 100; med = np.r_[1, cum.median().values] * 100 - 100
a1.plot(x, med, color=ACC, lw=2.4, label="중앙값 경로"); a1.plot(x, mean, color=INK, lw=1.6, ls="--", label="평균 경로")
for v in (12, 24, 36): a1.axvline(v, color=GRID, lw=1, zorder=0)
a1.axvline(22.2, color=MUTED, lw=1, ls=":"); a1.text(22.4, a1.get_ylim()[1] * 0.9 if False else 85, "중간선거\n(2년차 11월)", fontsize=9, color=MUTED, va="top")
a1.set_xlim(0, 48); a1.set_ylim(-60, 130); a1.set_xticks([0, 12, 24, 36, 48]); a1.set_xticklabels(["1년차 시작", "2년차 시작\n(중간선거 해)", "3년차 시작", "4년차 시작\n(대선 해)", "주기 끝"], fontsize=9); a1.set_ylabel("주기 시작 대비 누적 수익률(%)", color=INK)
a1.set_title("24개 대선 주기를 포갠 S&P500 경로 (1929~2024, 가격)", loc="left", fontsize=12, color=INK); a1.legend(frameon=False, loc="upper left", fontsize=9)
a2.bar(range(1, 17), q.mean().values * 100, color=[ACC if v > base else "#94a3b8" for v in q.mean().values], width=0.72); a2.axhline(base * 100, color=MUTED, lw=1, ls="--"); a2.text(16.6, base * 100 + 0.25, "평소 분기", fontsize=8, color=MUTED, ha="right")
a2.set_xticks(range(1, 17)); a2.set_xticklabels([f"{(k-1)//4+1}-{(k-1)%4+1}" for k in range(1, 17)], fontsize=8); a2.set_xlabel("임기 연차-분기", color=MUTED); a2.set_ylabel("분기 평균 수익률(%)", color=INK)
a2.set_title("주기 내 분기별 평균 (파랑=평소보다 높음)", loc="left", fontsize=12, color=INK)
for a in (a1, a2):
    for s in ("top", "right"): a.spines[s].set_visible(False)
    a.tick_params(colors=MUTED); a.grid(axis="y", color=GRID, lw=0.8)
plt.tight_layout(); fig.savefig(f"{OUT}/cycle_overlay.png", dpi=150); print("saved")
