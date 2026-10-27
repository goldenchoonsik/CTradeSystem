"""대선 주기 패턴(중간선거 해 10월 말 → 3년차 6월 말 9개월 강세, 3년차 연수익)이 매크로 레짐에 따라 달라지는가. 24주기(1929~2024), 중간선거 해 10월 말 기준 레짐.
  .venv/bin/python docs/research/20261026_market_anomalies/chronicle_regime.py"""
import os, numpy as np, pandas as pd
from math import sqrt
OUT = os.path.dirname(os.path.abspath(__file__)); D = pd.read_csv(f"{OUT}/regime_monthly.csv", index_col="ym"); D.index = pd.PeriodIndex(D.index, freq="M")
rows = []
for y in range(1929, 2025, 4):
    o = pd.Period(f"{y+1}-10", "M"); b = pd.Period(f"{y+2}-06", "M"); p = pd.Period(f"{y}-10", "M")
    strong = D.sp[b] / D.sp[o] - 1; y3 = D.sp[pd.Period(f"{y+2}-12", "M")] / D.sp[pd.Period(f"{y+1}-12", "M")] - 1
    wk = D.sp[pd.Period(f"{y+1}-09", "M")] / D.sp[pd.Period(f"{y+1}-01", "M")] - 1; pre = D.sp[o] / D.sp[pd.Period(f"{y+1}-02", "M")] - 1
    rows.append(dict(y=y, strong=strong, y3=y3, weak=wk, d10=D.gs10[o] - D.gs10[p], infl=D.infl[o], cape=D.cape[o], credit=D.credit[o], rec=int(D.rec[pd.Period(f"{y+1}-04", "M"):o].max() == 1), party=D.party[o], dd=D.dd[o], low_before=pre))
R = pd.DataFrame(rows).set_index("y"); base = (1 + D.ret.mean()) ** 9 - 1 if "ret" in D else np.nan
print(f"전체 24주기: 강세구간 평균 {R.strong.mean()*100:.1f}% 중앙 {R.strong.median()*100:.1f}% 양수 {(R.strong>0).sum()}/24 | 평소 9개월 {base*100:.1f}%")
def grp(nm, mask):
    a, b = R[mask], R[~mask]; ta = (a.strong.mean() - b.strong.mean()) / sqrt(a.strong.var()/len(a) + b.strong.var()/len(b))
    print(f"  {nm:34s} 해당 {len(a):2d}: 평균 {a.strong.mean()*100:5.1f}% 중앙 {a.strong.median()*100:5.1f}% 양수 {(a.strong>0).sum():2d}/{len(a):2d} | 나머지 {len(b):2d}: 평균 {b.strong.mean()*100:5.1f}% 중앙 {b.strong.median()*100:5.1f}% 양수 {(b.strong>0).sum():2d}/{len(b):2d} | t(차) {ta:5.2f} | 3년차 연수익 {a.y3.mean()*100:5.1f}% vs {b.y3.mean()*100:5.1f}%")
print("\n== 레짐별 (중간선거 해 10월 말 기준) =="); 
grp("금리 상승(직전 12개월 10년물 ↑)", R.d10 > 0); grp("인플레 높음(중앙값 이상)", R.infl >= R.infl.median()); grp("CAPE 높음(중앙값 이상)", R.cape >= R.cape.median()); grp("신용스프레드 높음(중앙값 이상)", R.credit >= R.credit.median())
grp("직전 침체 경험(그해 4월~10월)", R.rec == 1); grp("공화당 집권", R.party == "R"); grp("그해 이미 큰 낙폭(고점 대비 −10% 이하)", R.dd <= -0.10); grp("그해 2~10월 하락(10월 말 기준)", R.low_before < 0)
print("\n중앙값:", {k: round(float(R[k].median()), 2) for k in ("d10", "infl", "cape", "credit")})
print("\n== 주기별 레짐·결과 =="); T = R.copy(); T[["strong", "y3", "weak", "low_before", "dd"]] *= 100; print(T.round(1).to_string())
print("\n상관(24주기): 강세구간 수익과 — 금리변화 %.2f, 인플레 %.2f, CAPE %.2f, 신용스프레드 %.2f, 선거 전 2~10월 수익 %.2f, 10월 낙폭 %.2f" % tuple(R.strong.corr(R[c], method="spearman") for c in ("d10", "infl", "cape", "credit", "low_before", "dd")))
R.to_csv(f"{OUT}/chronicle_regime_table.csv")
cur = D.loc["2026-09"].iloc[0] if False else D.iloc[-1]; print(f"\n현재(2026-09): 금리 10년물 {cur.gs10:.2f}% (직전 12개월 변화 {cur.gs10 - D.gs10['2025-09']:+.2f}%p), 인플레 {cur.infl:.1f}%, CAPE {cur.cape:.1f}, 신용스프레드 {cur.credit:.2f}, 고점 대비 {cur.dd*100:.1f}%, 그해 1월→9월 {(D.sp['2026-09']/D.sp['2026-01']-1)*100:.1f}%, 집권 {cur.pres}")
