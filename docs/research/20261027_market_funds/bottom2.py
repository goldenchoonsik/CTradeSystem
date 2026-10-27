import pandas as pd,numpy as np,warnings; warnings.filterwarnings("ignore")
F=pd.read_csv("monthly_funds_kospi.csv",index_col=0,parse_dates=True)
F["r"]=F.신용융자/F.예탁금; F["dd"]=(F.KOSPI/F.KOSPI.cummax()-1)*100
# 에피소드: 새 고점 후 dd<=-10% 가 되는 국면, 새 고점 회복까지
ep=[]; pk=F.index[0]; inn=False
for t in F.index:
    if F.KOSPI[t]>=F.KOSPI[:t].max(): 
        if inn: ep.append((pk,tr,t)); inn=False
        pk=t
    if F.dd[t]<=-10 and not inn: inn=True; tr=t
    if inn and F.KOSPI[t]<F.KOSPI[tr]: tr=t
if inn: ep.append((pk,tr,None))
rows=[]
for k,(p,tr,rc) in enumerate(ep):
    mx=F.loc[p:tr].신용융자.idxmax()
    rows.append(dict(id=k,고점=p.date(),바닥=tr.date(),낙폭=round(F.dd[tr]),하락월=(tr.year-p.year)*12+tr.month-p.month,
      회복월=None if rc is None else (rc.year-tr.year)*12+rc.month-tr.month,융자바닥_고점대비=round((F.신용융자[tr]/F.신용융자[mx]-1)*100),비율고점=round(F.r[p],2),비율바닥=round(F.r[tr],2)))
E=pd.DataFrame(rows); print(E.to_string(index=False)); E.to_csv("episodes.csv",index=False,encoding="utf-8-sig")
print("\n하락월 중앙",E.하락월.median(),"낙폭 중앙",E.낙폭.median())
# 월 관측: 에피소드 내 dd<=-10% 인 달. 라벨: 바닥까지 남은 개월 <=3 (바닥 구간)
obs=[]
for k,(p,tr,rc) in enumerate(ep):
    for t in F.loc[p:(rc or F.index[-1])].index:
        if t>tr: break
        if F.dd[t]>-10: continue
        m_to=(tr.year-t.year)*12+tr.month-t.month
        mx=F.loc[p:t].신용융자.idxmax()
        f=dict(ep=k,t=t,m_to=m_to,label=int(m_to<=3),
          dd=F.dd[t],dur=(t.year-p.year)*12+t.month-p.month,
          speed=F.dd[t]/max((t.year-p.year)*12+t.month-p.month,1),
          crd_from_peak=(F.신용융자[t]/F.신용융자[mx]-1)*100,
          crd3=np.log(F.신용융자).diff(3)[t]*100,
          r=F.r[t], r_chg=F.r[t]-F.r[p],
          dep3=np.log(F.예탁금).diff(3)[t]*100,
          off_low=(F.KOSPI[t]/F.loc[p:t].KOSPI.min()-1)*100,   # 지금까지 최저 대비 반등
          mon_since_low=(t.year-F.loc[p:t].KOSPI.idxmin().year)*12+t.month-F.loc[p:t].KOSPI.idxmin().month)
        obs.append(f)
O=pd.DataFrame(obs).dropna(); print("\n관측",len(O),"월, 에피소드",O.ep.nunique(),", 바닥구간(≤3개월 전) 비율",round(O.label.mean(),2))
def auc(x,y):
    r=pd.Series(x).rank().values; n1=y.sum(); n0=len(y)-n1
    return (r[y==1].sum()-n1*(n1+1)/2)/(n1*n0)
print("\n단일 지표 AUC (0.5=무의미, 에피소드별 평균 AUC도 표기)")
feats=["dd","dur","speed","crd_from_peak","crd3","r","r_chg","dep3","off_low","mon_since_low"]
for f in feats:
    a=auc(O[f].values,O.label.values)
    per=[auc(g[f].values,g.label.values) for _,g in O.groupby("ep") if 0<g.label.sum()<len(g)]
    print(f"{f:14s} AUC {a:.2f}  에피소드내 평균 {np.mean(per):.2f} (n={len(per)})")
O.to_csv("obs.csv",index=False,encoding="utf-8-sig")
