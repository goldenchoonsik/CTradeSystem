import pandas as pd,numpy as np
F=pd.read_csv("monthly_funds_kospi.csv",index_col=0,parse_dates=True)
F["r"]=F.신용융자/F.예탁금; F=F.dropna(subset=["r"])
lk=np.log(F.KOSPI)
fw=lambda n:(lk.shift(-n)-lk)*100
thr=F.r.quantile(.6); hi=F.r>=thr
print(f"[A] 전체 기준 상위40% 컷 {thr:.3f} — 기간별 이후 12개월")
for lab,a,b in (("1998-2007","1998","2007"),("2008-2014","2008","2014"),("2015-2020","2015","2020"),("2021-2025","2021","2025")):
    m=slice(a,b); x=fw(12)[m]; h=hi[m]
    print(f"{lab} 상위40% {int(h.sum())}/{len(h)}개월: 평균 {x[h].mean():.1f}, 승률 {(x[h]>0).mean()*100:.0f} | 나머지 평균 {x[~h].mean():.1f}, 승률 {(x[~h]>0).mean()*100:.0f}")
pr=F.r.expanding(60).apply(lambda s:(s<=s.iloc[-1]).mean(),raw=False)
print("\n[B] 확장창 백분위(과거만) 5분위 → 이후 N개월")
q=pd.cut(pr,[0,.2,.4,.6,.8,1.0],labels=[1,2,3,4,5],include_lowest=True)
for n in (3,6,12):
    g=pd.DataFrame({"x":fw(n),"q":q}).dropna()
    print(f"{n}개월 평균",g.groupby("q",observed=True).x.mean().round(1).tolist(),"승률",(g.x>0).groupby(g.q,observed=True).mean().mul(100).round(0).tolist(),"개수",g.groupby("q",observed=True).size().tolist())
y=F[F.index.month==12]; yy=pd.DataFrame({"p":pr[y.index],"fw":fw(12)[y.index]}).dropna()
h=yy.p>=.6
print(f"\n[C] 연말 판정(비겹침 {len(yy)}년): 백분위≥0.6 평균 {yy[h].fw.mean():.1f} (n={h.sum()}, 승률 {(yy[h].fw>0).mean()*100:.0f}) | <0.6 평균 {yy[~h].fw.mean():.1f} (n={(~h).sum()}, 승률 {(yy[~h].fw>0).mean()*100:.0f})")
yoy=np.log(F.신용융자).diff(12)*100; ex=yoy-np.log(F.KOSPI).diff(12)*100
for lab,s in (("신용융자 YoY",yoy),("신용융자YoY−지수YoY",ex)):
    hh=s>=s.quantile(.6); print(f"[D] {lab}: 이후12m 상관 {s.corr(fw(12)):.2f}; 상위40% 평균 {fw(12)[hh].mean():.1f} vs 나머지 {fw(12)[~hh].mean():.1f}")
print(f"\n현재 비율 {F.r.iloc[-1]:.3f}, 확장창 백분위 {pr.iloc[-1]:.2f}")
