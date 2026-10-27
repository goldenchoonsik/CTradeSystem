import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
Y="/home/kuzimo/projects/CTradeSystem/DataVault/raw/yahoo/candle_1d"
T="SPY QQQ IWM DIA EFA EEM EWY EWJ EWT FXI EWG EWU EWZ INDA EWH EWA EWC EWW EWS EWP EWI EWQ ILF VGK".split()
S={t:pd.read_csv(f"{Y}/{t}_1d.csv",usecols=["timestamp","close"],parse_dates=["timestamp"]).set_index("timestamp").close.dropna() for t in T}
k=pd.read_csv("kospi_daily_ecos.csv",index_col=0,parse_dates=True).iloc[:,0]; S["KOSPI"]=k
for s in S.values(): s.index=s.index.normalize()
def episodes(c,th=-20):
    c=c[~c.index.duplicated()]; dd=(c/c.cummax()-1)*100; out=[]; pk=c.index[0]; inn=False
    for t in c.index:
        if c[t]>=c[:t].max():
            if inn: out.append((pk,t0,tr,True)); inn=False
            pk=t
        if dd[t]<=th and not inn: inn=True; t0=t; tr=t
        if inn and c[t]<c[tr]: tr=t
    if inn: out.append((pk,t0,tr,False))
    rows=[]
    for pk,t0,tr,done in out:
        rows.append(dict(고점=pk,도달=t0,바닥=tr,완료=done,일수=(t0-pk).days,속도=dd[t0]/max((t0-pk).days,1)*30,
           추가하락=(c[tr]/c[t0]-1)*100,바닥까지=(tr-t0).days,총낙폭=dd[tr]))
    return rows
R=[]
for n,c in S.items():
    for r in episodes(c,-20): r["자산"]=n; R.append(r)
R=pd.DataFrame(R); R["군집"]=R.고점.dt.year//2  # 겹치는 글로벌 사건 군집
A=R[R.완료].copy(); print("완료 에피소드",len(A),"자산",A.자산.nunique())
print(A.groupby(A.고점.dt.year).size().to_string())
print(f"\n-20% 도달 시점 기준 추가 하락 분포: 중앙 {A.추가하락.median():.1f}, 25% {A.추가하락.quantile(.25):.1f}, 75% {A.추가하락.quantile(.75):.1f}")
A["빠름"]=A.일수<=A.일수.median()
print(f"\n[도달 소요일 중앙 {A.일수.median():.0f}일 기준] 빠른 하락 vs 느린 하락")
print(A.groupby("빠름")[["추가하락","바닥까지"]].agg(["median","mean"]).round(1).to_string())
print("\n일수 분위(5구간)별 추가 하락·바닥까지 중앙:")
A["q"]=pd.qcut(A.일수,5,labels=False)+1; print(A.groupby("q").agg(일수=("일수","median"),추가하락=("추가하락","median"),바닥까지=("바닥까지","median"),n=("q","size")).round(1).to_string())
from scipy.stats import spearmanr
for c in ("일수","속도"): 
    r,p=spearmanr(A[c],A.추가하락); r2,p2=spearmanr(A[c],A.바닥까지); print(f"{c}: 추가하락과 ρ={r:.2f}(p={p:.3f}) / 바닥까지 ρ={r2:.2f}(p={p2:.3f})")
# 글로벌 사건 중복 제거: 연도 군집 평균 후 상관
g=A.groupby(A.고점.dt.year//2).agg(일수=("일수","median"),추가하락=("추가하락","median"),n=("일수","size")); print("\n군집(2년 단위) 수",len(g)); print(f"군집 단위 ρ(일수,추가하락)={spearmanr(g.일수,g.추가하락)[0]:.2f}")
# 속도 급락(≤30일에 -20%) 후: 바닥 확률
f=A[A.일수<=30]; print(f"\n30일 이내 -20% 급락 {len(f)}건 ({f.고점.dt.year.nunique()}개 연도 군집): 추가 하락 중앙 {f.추가하락.median():.1f}%, 추가하락>-10% 비율 {(f.추가하락>-10).mean()*100:.0f}%, 바닥까지 중앙 {f.바닥까지.median():.0f}일")
s=A[A.일수>300]; print(f"300일 초과 완만 하락 {len(s)}건: 추가 하락 중앙 {s.추가하락.median():.1f}%, 추가하락>-10% 비율 {(s.추가하락>-10).mean()*100:.0f}%, 바닥까지 중앙 {s.바닥까지.median():.0f}일")
R.to_csv("episodes_pooled.csv",index=False,encoding="utf-8-sig")

print("\n===== 깊이별 재진입: 해당 깊이 도달 시점부터 추가 하락·바닥까지 (완료 에피소드) =====")
Rr=[]
for n,c in S.items():
    for r in episodes(c,-40): r["자산"]=n; Rr.append(r)
R40=pd.DataFrame(Rr); B=R40[R40.완료]
print(f"-40% 도달 {len(B)}건 ({B.고점.dt.year.nunique()}개 고점연도): 추가하락 중앙 {B.추가하락.median():.1f}% [25% {B.추가하락.quantile(.25):.1f} / 75% {B.추가하락.quantile(.75):.1f}], 바닥까지 중앙 {B.바닥까지.median():.0f}일 (25%~75% {B.바닥까지.quantile(.25):.0f}~{B.바닥까지.quantile(.75):.0f}), 추가하락<5% 비율 {(B.추가하락>-5).mean()*100:.0f}%")
print(B.assign(고점=B.고점.dt.date)[["자산","고점","일수","추가하락","바닥까지","총낙폭"]].round(0).sort_values("고점").to_string(index=False))
print("-40%를 150일 이내(빠른) 도달:",B[B.일수<=150][["자산","일수","추가하락","바닥까지"]].round(0).to_string(index=False) if (B.일수<=150).any() else "없음")
