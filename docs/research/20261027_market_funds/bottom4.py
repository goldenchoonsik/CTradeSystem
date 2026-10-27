import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
ko=pd.read_csv("kospi_daily_ecos.csv",index_col=0,parse_dates=True).iloc[:,0]
D=pd.read_csv("daily_credit_kospi.csv",index_col=0,parse_dates=True)
M=pd.read_csv("monthly_funds_kospi.csv",index_col=0,parse_dates=True)
dd=(ko/ko.cummax()-1)*100
ep=[];inn=False;pk=ko.index[0]
for t in ko.index:
    if ko[t]>=ko[:t].max():
        if inn: ep.append((pk,tr,t)); inn=False
        pk=t
    if dd[t]<=-10 and not inn: inn=True;tr=t
    if inn and ko[t]<ko[tr]: tr=t
if inn: ep.append((pk,tr,None))
rows=[]
for p,tr,rc in ep:
    for th in (-20,-30,-40):
        seg=dd[p:tr]; hit=seg[seg<=th]
        if hit.empty: continue
        t=hit.index[0]; low_after=ko[t:(rc or ko.index[-1])].min()
        # 도달 시점부터 최종 바닥까지 추가 하락·소요
        fin=ko[p:(rc or ko.index[-1])].min() if rc else ko[t:].min()
        fin_date=ko[t:(rc or ko.index[-1])].idxmin()
        rows.append(dict(고점=p.date(),깊이=th,도달일=t.date(),고점후일수=(t-p).days,속도_pp일=round(dd[t]/max((t-p).days,1)*30,1),
          추가하락=round((fin/ko[t]-1)*100,1),바닥까지일=(fin_date-t).days,진행중=rc is None))
R=pd.DataFrame(rows); pd.set_option("display.width",200); print(R.to_string(index=False)); R.to_csv("depth_reach.csv",index=False,encoding="utf-8-sig")
for th in (-20,-30,-40):
    s=R[(R.깊이==th)&(~R.진행중)]; print(f"\n-{abs(th)}% 도달 후 (완료 국면 {len(s)}개): 추가하락 중앙 {s.추가하락.median():.1f}% [최소 {s.추가하락.min()}, 최대 {s.추가하락.max()}] / 바닥까지 중앙 {s.바닥까지일.median():.0f}일 [{s.바닥까지일.min()}~{s.바닥까지일.max()}]")
# 현재
p=ko.idxmax(); print("\n현재:",ko.index[-1].date(),round(ko.iloc[-1]),f"고점 {p.date()} {ko.max():.0f}, 낙폭 {dd.iloc[-1]:.1f}%, 경과 {(ko.index[-1]-p).days}일; 최저 {ko[p:].min():.0f} ({ko[p:].idxmin().date()})")
t40=dd[dd<=-40].index[0]; print("-40% 최초 도달:",t40.date(),"고점후",(t40-p).days,"일")
# 신용 상황 (일별 융자 vs 고점)
x=D.loc[p-pd.Timedelta(days=10):,["융자_전체","KOSPI"]]; mxd=x.융자_전체.idxmax()
print(f"신용융자 최고 {x.융자_전체.max():.0f}억 ({mxd.date()}), 최근 {x.융자_전체.iloc[-1]:.0f}억, 고점대비 {(x.융자_전체.iloc[-1]/x.융자_전체.max()-1)*100:.1f}%")
print(x.loc["2026-09-25":].round(0).T.to_string())
