# 사전 고정: 격자 N∈{10,20,40}거래일 × R∈{5,10,15}% (저점 대비 반등). 에피소드(낙폭≤-20%)당 첫 충족일 1회 진입.
# 선택 규칙(개발: 고점 2010년 이전): 120일 후 평균 수익이 큰 규칙 중 '새 저점 재하락 비율' 최저. 검증: 고점 2010년 이후 1회 평가.
import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
Y="/home/kuzimo/projects/CTradeSystem/DataVault/raw/yahoo/candle_1d"
T="SPY QQQ IWM DIA EFA EEM EWY EWJ EWT FXI EWG EWU EWZ INDA EWH EWA EWC EWW EWS EWP EWI EWQ ILF VGK".split()
S={t:pd.read_csv(f"{Y}/{t}_1d.csv",usecols=["timestamp","close"],parse_dates=["timestamp"]).set_index("timestamp").close.dropna() for t in T}
S["KOSPI"]=pd.read_csv("kospi_daily_ecos.csv",index_col=0,parse_dates=True).iloc[:,0]
for k,s in S.items(): s.index=s.index.normalize(); S[k]=s[~s.index.duplicated()]
GR=[(N,R) for N in (10,20,40) for R in (5,10,15)]
rows=[]
for name,c in S.items():
    v=c.values; idx=c.index; n=len(v); cm=np.maximum.accumulate(v); newh=v>=cm
    starts=[i for i in range(n) if newh[i]]+[n]
    for a,b in zip(starts[:-1],starts[1:]):
        seg=v[a:b]
        if len(seg)<30 or seg.min()/seg[0]-1>-0.20: continue
        done=b<n
        cross=int(np.argmax(seg/seg[0]-1<=-0.20))  # -20% 도달
        def outcome(j,tag,rule):
            i=a+j; e=v[i]
            f=lambda h: (v[i+h]/e-1)*100 if i+h<n else np.nan
            fut=v[i+1:min(i+121,n)]
            mae=(fut.min()/e-1)*100 if len(fut) else np.nan
            low=seg[:j+1].min(); fail=float(len(fut)>0 and fut.min()<low*1.0)  # 진입 전 저점 이탈
            rows.append(dict(자산=name,고점=idx[a],규칙=rule,진입=idx[i],r60=f(60),r120=f(120),r250=f(250),mae120=mae,재하락=fail,완료=done))
        outcome(cross,"x","기준: -20% 도달 진입")
        # 확인 규칙
        low=np.minimum.accumulate(seg); lowi=np.zeros(len(seg),int)
        cur=0
        for j in range(len(seg)):
            if seg[j]<=seg[cur]: cur=j
            lowi[j]=cur
        for N,R in GR:
            for j in range(cross,len(seg)):
                if j-lowi[j]>=N and seg[j]/seg[lowi[j]]-1>=R/100 and seg[lowi[j]]/seg[0]-1<=-0.20:
                    outcome(j,"c",f"확인 N{N}·R{R}"); break
D=pd.DataFrame(rows); D["기간"]=np.where(D.고점.dt.year<2010,"개발(<2010)","검증(≥2010)")
D.to_csv("confirm_entries.csv",index=False,encoding="utf-8-sig")
def summ(g): return pd.Series(dict(n=len(g),에피소드=g.groupby(["자산","고점"]).ngroups,r60=g.r60.mean(),r120=g.r120.mean(),중앙120=g.r120.median(),승률120=(g.r120>0).mean()*100,r250=g.r250.mean(),mae120=g.mae120.mean(),재하락=g.재하락.mean()*100))
pd.set_option("display.width",220)
for per in ("개발(<2010)","검증(≥2010)"):
    print(f"\n=== {per} (이후 수익 %, mae120=진입 후 120일 중 최대 추가 하락, 재하락=진입 직후 120일 내 진입 전 저점 이탈 %)")
    print(D[D.기간==per].groupby("규칙").apply(summ).round(1).to_string())
dev=D[D.기간=="개발(<2010)"].groupby("규칙").apply(summ); pick=dev[dev.index.str.startswith("확인")].sort_values("r120",ascending=False).head(3)
print("\n개발 구간 상위 3(120일 평균):",list(pick.index))
