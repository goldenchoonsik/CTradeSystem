import pandas as pd,numpy as np
F=pd.read_csv("monthly_funds_kospi.csv",index_col=0,parse_dates=True)
F["r"]=F.신용융자/F.예탁금
lk=np.log(F.KOSPI); F["dd"]=(F.KOSPI/F.KOSPI.cummax()-1)*100
F["crd3"]=(np.log(F.신용융자).diff(3))*100; F["crd1"]=np.log(F.신용융자).diff()*100
F["dep3"]=np.log(F.예탁금).diff(3)*100
fw=lambda n:(lk.shift(-n)-lk)*100
# 에피소드: dd<=-20% 로 들어간 뒤 새 고점 회복 전까지
ep=[]; inn=False
for t,row in F.iterrows():
    if row.dd<=-20 and not inn: inn=True; s=t
    if inn and row.dd==0:
        ep.append((s,t)); inn=False
if inn: ep.append((s,F.index[-1]))
print("에피소드(고점대비 -20% 진입 ~ 고점 회복)")
rows=[]
for s,e in ep:
    seg=F.loc[s:e]; tr=seg.KOSPI.idxmin(); pk=F.loc[:tr].KOSPI.idxmax(); pre=F.loc[pk:tr]
    mx=pre.신용융자.idxmax()
    rows.append(dict(고점=pk.date(),바닥=tr.date(),낙폭=round(F.dd[tr],0),융자최고월=mx.date(),
      바닥월_융자_고점대비=round((F.신용융자[tr]/F.신용융자[mx]-1)*100,0),바닥월_3개월융자=round(F.crd3[tr],0),
      바닥월_예탁금3개월=round(F.dep3[tr],0),비율_고점=round(F.r[pk],3),비율_바닥=round(F.r[tr],3),바닥후12m=round(fw(12)[tr],0)))
print(pd.DataFrame(rows).to_string(index=False))
# 신호: dd<=-20 & 신용융자 3개월 감소 <= X
print("\n확인 신호 시험: 낙폭 -20% 이하인 달 중 조건 충족 시 이후 수익(%)  (전체 낙폭달 기준선 포함)")
base=F[(F.dd<=-20)]
print(f"기준선: 낙폭≤-20% {len(base)}개월  이후 6m {fw(6)[base.index].mean():.1f}  12m {fw(12)[base.index].mean():.1f}  (바닥 이후 승률 12m {(fw(12)[base.index]>0).mean()*100:.0f}%)")
for X in (-5,-10,-15):
    s=base[base.crd3<=X]; print(f"  + 융자 3개월 {X}% 이하 감소: {len(s)}개월, 이후 6m {fw(6)[s.index].mean():.1f}, 12m {fw(12)[s.index].mean():.1f}, 승률12m {(fw(12)[s.index]>0).mean()*100:.0f}%  첫 발생월 {[str(i.date()) for i in s.index[:0]]}")
    ev=[str(t.date()) for t in s.index]; print("     해당월:",ev[:14])
for lab,c in (("예탁금 3개월 증가(저가 유입) ≥+5%",base.dep3>=5),("비율 0.30 미만(신용 청산)",base.r<0.30)):
    s=base[c]; print(f"  + {lab}: {len(s)}개월, 이후 6m {fw(6)[s.index].mean():.1f}, 12m {fw(12)[s.index].mean():.1f}, 승률12m {(fw(12)[s.index]>0).mean()*100:.0f}%")
# 신용 청산 완료 후 '재증가 전환' 신호: 융자 감소 중 처음 +로 전환
