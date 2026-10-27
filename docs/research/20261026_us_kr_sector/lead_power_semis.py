"""전력·반도체: 미국 vs 한국 선후(시차별 상관), 2024년 이후 미국 선행 가설 점검. 탐색·기술 통계.
정렬 오프셋 j: 미국 거래일 d 의 수익을 한국 'd 다음 첫 거래일(p)'에서 j 세션 옮긴 한국 수익과 상관. j=0 미국→한국 즉시, j=+1,+2 한국이 이틀·사흘째 반응(미국 선행 지속),
j=-1 한국 d 당일 세션(한국 d 종가 뒤 미국 개장 → 한국 선행), j=-2 한국이 하루 더 앞섬. 시장 제거 = 미국은 SPY, 한국은 코스피 회귀 잔차.
"""
import numpy as np, pandas as pd
R="/home/kuzimo/projects/CTradeSystem/DataVault"
def us(t):
    d=pd.read_csv(f"{R}/raw/yahoo/candle_1d/{t}_1d.csv",parse_dates=["timestamp"]).set_index("timestamp").close; d.index=d.index.normalize(); return np.log(d).diff().dropna()
pr=pd.read_parquet(f"{R}/staging/kr_equity/derived/prices_1d.parquet",columns=["code","date","close"]); pr["date"]=pd.to_datetime(pr.date)
def kr(c): s=pr[pr.code==c].set_index("date").close; return np.log(s).diff().dropna()
idx=pd.read_parquet(f"{R}/staging/kr_equity/derived/index_1d.parquet",columns=["code","date","close"]); idx["date"]=pd.to_datetime(idx.date)
kospi=np.log(idx[idx.code=="U001"].set_index("date").close).diff().dropna()
spy=us("SPY")
def basket(names,f): return pd.concat([f(n) for n in names],axis=1).mean(axis=1,skipna=True)
US_POWER=["GEV","ETN","VRT","PWR","HUBB","POWL"]; KR_POWER=["A267260","A010120","A298040","A103590","A001440"]
KR_SEMI=["A005930","A000660","A042700","A058470","A007660"]
G={"전력":(basket(US_POWER,us),basket(KR_POWER,kr)),
   "전력(GRID ETF)":(us("GRID"),basket(KR_POWER,kr)),
   "반도체(SOXX)→삼성·하이닉스":(us("SOXX"),basket(["A005930","A000660"],kr)),
   "반도체(SOXX)→반도체 5종":(us("SOXX"),basket(KR_SEMI,kr)),
   "메모리(MU)→하이닉스·삼성":(us("MU"),basket(["A000660","A005930"],kr)),
   "AI(NVDA)→반도체 5종":(us("NVDA"),basket(KR_SEMI,kr))}
OFFS=[-2,-1,0,1,2,3]
def table(u,k,start,end,neutral):
    kd=k.index; pos=np.searchsorted(kd.values,u.index.values,side="right")
    ud=u.copy(); sp=spy.reindex(u.index); kp=kospi.reindex(kd)
    if neutral:
        j=pd.concat([u,sp],axis=1).dropna(); b=np.polyfit(j.iloc[:,1],j.iloc[:,0],1); ud=u-np.polyval(b,sp.fillna(0))
        j2=pd.concat([k,kp],axis=1).dropna(); b2=np.polyfit(j2.iloc[:,1],j2.iloc[:,0],1); k=k-np.polyval(b2,kp.reindex(k.index).fillna(0))
    out=[]
    for o in OFFS:
        p=pos+o; ok=(p>=0)&(p<len(kd)); 
        a=ud.values[ok]; kk=k.reindex(kd).values[p[ok]]; dt=kd[p[ok]]
        sel=(dt>=pd.Timestamp(start))&(dt<pd.Timestamp(end))&~np.isnan(a)&~np.isnan(kk)
        out.append(np.corrcoef(a[sel],kk[sel])[0,1] if sel.sum()>50 else np.nan)
    return out
print("열: 오프셋 "+" ".join(f"{o:+d}" for o in OFFS)+"   (j=0 미국→한국 다음 세션 / +1·+2 한국이 늦게 반응 / −1·−2 한국 선행)")
for name,(u,k) in G.items():
    print(f"\n[{name}]")
    for lab,(a,b) in {"2013~19":("2013-01-01","2020-01-01"),"2020~23":("2020-01-01","2024-01-01"),"2024~":("2024-01-01","2027-01-01")}.items():
        if name.startswith("전력") and lab=="2013~19": continue
        r=table(u,k,a,b,False); n=table(u,k,a,b,True)
        print(f"  {lab:8s} 원 "+" ".join(f"{x:+.2f}" for x in r)+" | 시장제거 "+" ".join(f"{x:+.2f}" for x in n))
# 주 단위: 미국 주 t 수익 vs 한국 주 t+1 (선행), 한국 주 t vs 미국 주 t+1
print("\n=== 주 단위(월~일 합): 미국 t→한국 t+1, 한국 t→미국 t+1, 동기 — 2024~ ===")
for name,(u,k) in G.items():
    w=pd.concat([u.resample("W").sum(),k.resample("W").sum()],axis=1).dropna(); w.columns=["u","k"]; w=w[w.index>="2024-01-01"]
    print(f"  {name:22s} 동기 {w.u.corr(w.k):+.2f} | 미국→한국 다음 주 {w.u.corr(w.k.shift(-1)):+.2f} | 한국→미국 다음 주 {w.k.corr(w.u.shift(-1)):+.2f} (n={len(w)})")
