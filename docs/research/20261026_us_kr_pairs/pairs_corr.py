import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
R="/home/kuzimo/projects/CTradeSystem"; V=f"{R}/DataVault"; SC="/tmp/claude-1000/-home-kuzimo-projects-CTradeSystem/d094c582-b7b0-44c9-8c91-b513d85505c6/scratchpad"
usc=pd.read_parquet(f"{SC}/us_close.parquet"); usr=np.log(usc).diff().clip(-0.4,0.4)
spy=pd.read_csv(f"{V}/raw/yahoo/candle_1d/SPY_1d.csv",usecols=["timestamp","close"],parse_dates=["timestamp"]).set_index("timestamp").close; spy.index=spy.index.normalize(); spyr=np.log(spy).diff()
uni=pd.read_csv(f"{V}/staging/us_equity/reference/universe.csv",dtype=str).set_index("yahoo_symbol").name
kuni=pd.read_csv(f"{V}/staging/kr_equity/universe.csv",dtype=str).set_index("code")
de=pd.read_parquet(f"{V}/staging/kr_equity/derived/daily_ext.parquet",columns=["code","date","market_cap"])
last=de[de.date==de.date.max()].set_index("code").market_cap
ok=[c for c in last.index if c in kuni.index and kuni.loc[c,"is_preferred"]=="0" and kuni.loc[c,"is_spac"]=="0"]
top=last.loc[ok].nlargest(400).index
pr=pd.read_parquet(f"{V}/staging/kr_equity/derived/prices_1d.parquet",columns=["code","date","close"]); pr["date"]=pd.to_datetime(pr.date)
kc=pr[pr.code.isin(top)].pivot(index="date",columns="code",values="close"); kr=np.log(kc).diff().clip(-0.3,0.3)
i=pd.read_parquet(f"{V}/staging/kr_equity/derived/index_1d.parquet",columns=["code","date","close"]); i["date"]=pd.to_datetime(i.date); ko=np.log(i[i.code=="U001"].set_index("date").close).diff()
kd=kr.index[kr.index>="2015-01-01"]; kr=kr.loc[kd]; ko=ko.reindex(kd)
# 미국 d → 한국 d 다음 세션으로 정렬(공휴일로 여러 미국일이 한 한국일에 겹치면 합산)
pos=np.searchsorted(kd.values,usr.index.values,side="right"); okp=pos<len(kd)
def align(df):
    out=np.zeros((len(kd),df.shape[1])); cnt=np.zeros((len(kd),df.shape[1])); v=df.values[okp]; m=~np.isnan(v)
    np.add.at(out,pos[okp],np.where(m,v,0)); np.add.at(cnt,pos[okp],m); out[cnt==0]=np.nan; return pd.DataFrame(out,index=kd,columns=df.columns)
U=align(usr); S=align(spyr.reindex(usr.index).to_frame("spy"))["spy"]
def resid(M,m,mask):
    X=M[mask].copy(); mm=m[mask]; res=pd.DataFrame(np.nan,index=X.index,columns=X.columns)
    for c in X.columns:
        j=pd.concat([X[c],mm],axis=1).dropna()
        if len(j)<300: continue
        b=np.polyfit(j.iloc[:,1],j.iloc[:,0],1); res.loc[j.index,c]=j.iloc[:,0]-np.polyval(b,j.iloc[:,1])
    return res
def corrmat(A,B):
    A=A.where(A.notna(),np.nan); B=B.where(B.notna(),np.nan)
    ma=A.notna().astype(float).values; mb=B.notna().astype(float).values
    a=A.fillna(0).values; b=B.fillna(0).values
    n=ma.T@mb; sa=a.T@mb; sb=ma.T@b; sab=a.T@b; saa=(a**2).T@mb; sbb=ma.T@(b**2)
    cov=sab/n-(sa/n)*(sb/n); va=saa/n-(sa/n)**2; vb=sbb/n-(sb/n)**2
    return pd.DataFrame(cov/np.sqrt(va*vb),index=A.columns,columns=B.columns), pd.DataFrame(n,index=A.columns,columns=B.columns)
periods={"dev":("2016-01-01","2023-01-01"),"hold":("2023-01-01","2027-01-01")}
res={}
for k,(a,b) in periods.items():
    mask=(kd>=pd.Timestamp(a))&(kd<pd.Timestamp(b))
    Ur=resid(U,S,mask); Kr=resid(kr,ko,mask); C,N=corrmat(Ur,Kr); C[N<(0.7*mask.sum())]=np.nan; res[k]=C; print(k,mask.sum(),"일",C.shape)
dev,hold=res["dev"],res["hold"]
pairs=pd.DataFrame({"dev":dev.stack(),"hold":hold.stack()}).dropna(); pairs.index.names=["us","kr"]
pairs=pairs.reset_index(); pairs["us_name"]=pairs.us.map(uni); pairs["kr_name"]=pairs.kr.map(kuni.name)
pairs.to_parquet(f"{SC}/pairs.parquet"); print(len(pairs),"쌍; 상관 분포 dev",pairs.dev.describe()[["mean","std","min","max"]].round(3).to_dict())
print("dev–hold 전체 쌍 상관(구간 간 재현성):",round(pairs.dev.corr(pairs.hold),3))
sel=pairs[(pairs.dev>=0.10)]
print(f"dev ≥0.10 쌍 {len(sel)}개 → 검증 평균 {sel.hold.mean():.3f}, 검증 ≥0.05 비율 {(sel.hold>=0.05).mean():.0%}, 검증 ≥0.10 비율 {(sel.hold>=0.10).mean():.0%}")
best=pairs[(pairs.dev>=0.08)&(pairs.hold>=0.08)].assign(m=lambda d:d[["dev","hold"]].min(axis=1)).sort_values("m",ascending=False)
print(f"\n개발·검증 모두 ≥0.08: {len(best)}쌍 (무작위 기대 {len(pairs)*0.0:.0f}에 가까움 — 양쪽 SE≈0.025/0.034)")
pd.set_option("display.width",220); print(best.head(60)[["us","us_name","kr","kr_name","dev","hold"]].round(3).to_string(index=False,max_colwidth=34))
best.to_csv("pairs_stable.csv",index=False)
