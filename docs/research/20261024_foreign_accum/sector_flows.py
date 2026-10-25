"""외국인 업종별 순매수 이동 — 종목별 Δ외국인 보유수량×수정종가(기업행위·거래량초과 행 제외, 우선주는 보통주 업종으로) 합. 업종 = DART KSIC(induty_code) 표준 분류. 2026-01-01~10-19 vs 2025.
  .venv/bin/python docs/research/20261024_foreign_accum/sector_flows.py"""
import os, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.font_manager as fm
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); OUT = os.path.dirname(os.path.abspath(__file__)); V = f"{ROOT}/DataVault/staging"
[fm.fontManager.addfont(p) for p in ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")]; plt.rcParams["font.family"] = ["NanumGothic", "DejaVu Sans"]; plt.rcParams["axes.unicode_minus"] = False
INK, INK2, MUTED, GRID, SURF, UP, DOWN = "#0b0b0b", "#52514e", "#8a8985", "#e4e3df", "#fcfcfb", "#e34948", "#2a78d6"
K = pd.read_parquet(f"{V}/global_macro/derived/kr_industry/company_ksic.parquet").set_index("stock_code")
GROUP = {**{k: "식음료" for k in ("10", "11")}, **{k: "섬유·의복·가죽" for k in ("13", "14", "15")}, **{k: "목재·종이·인쇄" for k in ("16", "17", "18")}, "19": "석유정제", "20": "화학", "21": "의약품", "22": "고무·플라스틱", "23": "비금속광물",
         "24": "철강·금속", "25": "철강·금속", "26": "반도체·전자부품·IT기기", "27": "의료·정밀기기", "28": "전기장비(배터리 등)", "29": "기계·장비", "30": "자동차·부품", "31": "기타 운송장비(조선·방산·항공)", "32": "기타 제조", "33": "기타 제조",
         "35": "전기·가스", "41": "건설", "42": "건설", "46": "도소매", "47": "도소매", "49": "운수·물류", "50": "운수·물류", "51": "운수·물류", "52": "운수·물류", "55": "숙박·외식", "56": "숙박·외식",
         **{k: "소프트웨어·정보서비스·통신" for k in ("58", "61", "62", "63")}, **{k: "방송·영상·콘텐츠" for k in ("59", "60")}, "64": "은행·금융지주·증권", "66": "은행·금융지주·증권", "65": "보험", "68": "부동산", "70": "연구개발·전문서비스", "71": "연구개발·전문서비스", "72": "연구개발·전문서비스", "75": "연구개발·전문서비스", "73": "연구개발·전문서비스"}
def sec(code):
    base = "A" + code[1:-1] + "0" if code[-1] != "0" else code
    k = K.induty_code.get(base) if base in K.index else None
    return GROUP.get(str(k)[:2], "기타") if k is not None and str(k)[:2].isdigit() else "분류없음"
ext = pd.read_parquet(f"{V}/kr_equity/derived/daily_ext.parquet", columns=["code", "date", "shares_listed", "foreign_hold_qty"]); px = pd.read_parquet(f"{V}/kr_equity/derived/prices_1d.parquet", columns=["code", "date", "close", "volume", "factor"]).dropna(subset=["close"])
d = ext.merge(px, on=["code", "date"]).sort_values(["code", "date"]); g = d.groupby("code"); dq = g.foreign_hold_qty.diff()
bad = (g.shares_listed.diff().fillna(0) != 0) | (g.factor.diff().fillna(0) != 0) | dq.isna() | (dq.abs() > d.volume + 1); d["v"] = np.where(bad, 0.0, dq * d.close) / 1e12
mc = pd.read_parquet(f"{V}/kr_equity/derived/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"]); last = mc[mc.date == mc.date.max()].set_index("code"); mc3 = mc[mc.date == mc.date.max() - pd.DateOffset(years=3)]
nm = pd.read_csv(f"{V}/kr_equity/reference/listed_snapshot.csv").set_index("code").name
res = {}
for lab, a, b in (("y2026", "2026-01-01", "2026-10-19"), ("y2025", "2025-01-01", "2025-12-31")): res[lab] = d[(d.date >= a) & (d.date <= b)].groupby("code").v.sum()
S = pd.DataFrame(res).fillna(0); S["업종"] = [sec(c) for c in S.index]; S["이름"] = [nm.get(c, c) for c in S.index]
S["그룹"] = np.where(S.index.isin(["A005930", "A005935", "A000660"]), "삼성전자·SK하이닉스", S.업종)
tab = S.groupby("업종")[["y2025", "y2026"]].sum(); tab["종목수"] = S.groupby("업종").size()
lm = last.market_cap.groupby(lambda c: sec(c)).sum() / 1e12; tab["시총(조)"] = lm.reindex(tab.index)
fp = last.assign(f=last.foreign_hold_pct * last.market_cap / 100).groupby(lambda c: sec(c)).f.sum() / 1e12; tab["외국인보유(조)"] = fp.reindex(tab.index); tab["외국인지분율(%)"] = tab["외국인보유(조)"] / tab["시총(조)"] * 100
p3 = mc3.assign(f=mc3.foreign_hold_pct * mc3.market_cap / 100).groupby(mc3.code.map(sec)); tab["3년전 지분율(%)"] = (p3.f.sum() / p3.market_cap.sum() * 100).reindex(tab.index)
tab["지분율 3년변화(%p)"] = tab["외국인지분율(%)"] - tab["3년전 지분율(%)"]
tab["2026 상위 순매도"] = S.groupby("업종").apply(lambda z: ", ".join(f"{r.이름} {r.y2026:.1f}" for r in z.nsmallest(3, "y2026").itertuples() if r.y2026 < -0.3)); tab["2026 상위 순매수"] = S.groupby("업종").apply(lambda z: ", ".join(f"{r.이름} +{r.y2026:.1f}" for r in z.nlargest(3, "y2026").itertuples() if r.y2026 > 0.3))
tab = tab.sort_values("y2026"); pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 80)
print(f"합계 2025 {tab.y2025.sum():+.0f}조 / 2026 {tab.y2026.sum():+.0f}조 (분류없음 포함)\n"); print(tab[["y2025", "y2026", "시총(조)", "외국인지분율(%)", "지분율 3년변화(%p)", "종목수"]].round(1).to_string())
print("\n== 삼성전자·SK하이닉스(+삼성전자우) 합 ==", S[S.그룹 == "삼성전자·SK하이닉스"][["y2025", "y2026"]].sum().round(1).to_dict())
rest = S[S.그룹 != "삼성전자·SK하이닉스"].groupby("업종")[["y2025", "y2026"]].sum().sort_values("y2026"); print("\n== 삼성전자·SK하이닉스 제외 업종별 (조원) =="); print(rest.round(1).to_string()); print("제외 후 합계", rest.sum().round(1).to_dict())
print("\n== 업종별 상위 매도·매수 종목 =="); print(tab[["2026 상위 순매도", "2026 상위 순매수"]].to_string())
tab.to_csv(f"{OUT}/sector_flows.csv", encoding="utf-8-sig")
# 그림: 삼성전자·SK하이닉스 제외 업종 + 두 종목 막대
bars = rest.y2026.copy(); bars.loc["삼성전자·SK하이닉스(우선주 포함)"] = S[S.그룹 == "삼성전자·SK하이닉스"].y2026.sum(); bars = bars.drop("분류없음", errors="ignore").sort_values()
fig, axs = plt.subplots(1, 2, figsize=(13, 7.4), dpi=150, facecolor=SURF, gridspec_kw=dict(width_ratios=[0.9, 1.5]))
for ax in axs: ax.set_facecolor(SURF)
a0 = axs[0]; v = S[S.그룹 == "삼성전자·SK하이닉스"].y2026.sum(); a0.barh([""], [v], color=DOWN, height=0.55); a0.text(v - 4, 0, f"{v:.0f}조", ha="right", va="center", fontsize=12, color=INK, fontweight="bold"); a0.set_xlim(v * 1.3, 5); a0.axvline(0, color=INK2, linewidth=1)
a0.set_title("삼성전자·SK하이닉스 (우선주 포함, 조원)", loc="left", fontsize=11.5, color=INK, fontweight="bold"); a0.set_yticks([]); a0.tick_params(colors=INK2, labelsize=10); [a0.spines[s].set_visible(False) for s in ("top", "right", "left")]; a0.spines["bottom"].set_color(GRID); a0.grid(axis="x", color=GRID, linewidth=0.8); a0.set_axisbelow(True)
a1 = axs[1]; b = bars.drop("삼성전자·SK하이닉스(우선주 포함)"); a1.barh(b.index, b.values, color=[UP if x > 0 else DOWN for x in b.values], height=0.62, edgecolor=SURF)
for yv, x in zip(b.index, b.values): a1.text(x + (0.25 if x >= 0 else -0.25), yv, f"{x:+.1f}", va="center", ha="left" if x >= 0 else "right", fontsize=9, color=INK, fontweight="bold")
a1.axvline(0, color=INK2, linewidth=1); a1.grid(axis="x", color=GRID, linewidth=0.8); a1.set_axisbelow(True); [a1.spines[s].set_visible(False) for s in ("top", "right", "left")]; a1.spines["bottom"].set_color(GRID); a1.tick_params(colors=INK2, labelsize=9, axis="x"); a1.tick_params(axis="y", length=0, labelsize=9.5, labelcolor=INK)
a1.set_xlim(b.min() * 1.25 - 1, b.max() * 1.25 + 1); a1.set_title("나머지 업종 (삼성전자·SK하이닉스 제외, 조원)", loc="left", fontsize=11.5, color=INK, fontweight="bold")
fig.suptitle("2026년 외국인 업종별 순매수 (1/1~10/19, 조원)", x=0.012, ha="left", fontsize=14, color=INK, fontweight="bold"); fig.text(0.012, 0.925, "보유 수량 변화 × 수정종가 근사 · 빨강=순매수, 파랑=순매도 · 업종=DART 표준산업분류 · 현재 상장 종목 기준(분류 없음 제외)", fontsize=9.5, color=INK2)
fig.subplots_adjust(left=0.03, right=0.97, top=0.86, bottom=0.07, wspace=0.62); fig.savefig(f"{OUT}/sector_flows_2026.png", facecolor=SURF)
