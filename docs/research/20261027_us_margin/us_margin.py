import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
V="/home/kuzimo/projects/CTradeSystem/DataVault"; SIDE=0.0005
m=pd.read_csv(f"{V}/staging/us_margin_finra/margin_monthly.csv"); m["d"]=pd.to_datetime(m.month+"-01")+pd.offsets.MonthEnd(0); m=m.set_index("d")
m["R"]=m.debit/m.free_cash
def px(t): 
    s=pd.read_csv(f"{V}/raw/yahoo/candle_1d/{t}_1d.csv",usecols=["timestamp","close"],parse_dates=["timestamp"]).set_index("timestamp").close; s.index=s.index.normalize(); return s[~s.index.duplicated()]
SPY,QQQ=px("SPY"),px("QQQ"); print("SPY",SPY.index.min().date(),SPY.index.max().date(),"R 범위",m.R.min().round(2),m.R.max().round(2),"현재",m.R.iloc[-1].round(2))
sm=SPY.resample("ME").last(); f12=(np.log(sm).shift(-12)-np.log(sm))*100
D=m[["R"]].join(sm.rename("P"),how="inner"); D["f12"]=f12.reindex(D.index)
dev=D[:"2009-12"]; cut=dev.R.quantile(.6); print(f"개발 60% 분위 컷 c_dev = {cut:.2f}")
print("\n### R 5분위 → 이후 12개월 SPY (%): 전체 / 기간별")
for lab,x in (("1997~2026",D),("1997~2009",D[:"2009"]),("2010~2026",D["2010":])):
    x=x.dropna(subset=["f12"]); q=pd.qcut(x.R,5,labels=False)+1
    print(f"{lab:10s} 평균",x.f12.groupby(q).mean().round(1).tolist(),"승률",(x.f12>0).groupby(q).mean().mul(100).round(0).tolist(),"R범위",x.R.min().round(2),"~",x.R.max().round(2))
hi=D.R>=cut; x=D.dropna(subset=["f12"])
for lab,a,b in (("1997~2009","1997","2009"),("2010~2018","2010","2018"),("2019~2026","2019","2026")):
    y=x[a:b]; h=y.R>=cut; print(f"{lab}: R≥{cut:.2f} {int(h.sum())}/{len(h)}개월 평균 {y.f12[h].mean():.1f}(승률 {(y.f12[h]>0).mean()*100:.0f}) | 미만 평균 {y.f12[~h].mean():.1f}(승률 {(y.f12[~h]>0).mean()*100:.0f})")
Rl=m.R.copy(); Rl.index=Rl.index.to_period("M")
pct=m.R.expanding(60).apply(lambda s:(s<=s.iloc[-1]).mean(),raw=False); pct.index=pct.index.to_period("M")
def wser(idx,mode,lag=2):
    src=idx.to_period("M")-lag
    if mode=="fix": v=pd.Series([Rl.get(p,np.nan) for p in src],index=idx).ffill(); return pd.Series(np.where(v>=cut,0.5,1.0),index=idx)
    v=pd.Series([pct.get(p,np.nan) for p in src],index=idx).ffill(); return pd.Series(np.where(v>=.6,0.5,1.0),index=idx)
def run(s,w): r=s.pct_change().fillna(0); w=w.shift(1).fillna(1.0); return (1+(w*r-w.diff().abs().fillna(0)*SIDE)).cumprod()
def st(x,a,b):
    x=x[(x.index>=a)&(x.index<=b)]; l=np.log(x).diff().dropna(); y=len(l)/252
    return dict(C=(np.exp(l.sum())**(1/y)-1)*100,S=l.mean()/l.std()*np.sqrt(252),M=((x/x.cummax())-1).min()*100)
per={"개발 ~2009":("1997-01-01","2009-12-31"),"검증 2010~":("2010-01-01","2026-10-26"),"2022~":("2022-01-01","2026-10-26"),"전체":("1997-01-01","2026-10-26")}
for name,s in (("SPY",SPY),("QQQ",QQQ)):
    s=s[s.index>="1997-01-01"] if name=="SPY" else s
    V_={"보유":s/s.iloc[0],"고정컷":run(s,wser(s.index,"fix")),"확장창":run(s,wser(s.index,"exp"))}
    print(f"\n### {name} (평균비중 고정컷 {wser(s.index,'fix').mean():.2f} / 확장창 {wser(s.index,'exp').mean():.2f})")
    for k,(a,b) in per.items():
        try: print(f"{k:11s}"+" | ".join(f"{n}: {st(v,a,b)['C']:5.1f}% 샤{st(v,a,b)['S']:4.2f} MDD{st(v,a,b)['M']:6.1f}" for n,v in V_.items()))
        except Exception as e: pass
print("\n현재 R",m.R.iloc[-1].round(2),m.index[-1].date(),"/ 컷",round(cut,2),"→",'경고' if m.R.iloc[-1]>=cut else '정상',"; 2개월 전(적용 기준)",m.R.iloc[-3].round(2) if len(m)>3 else '')
