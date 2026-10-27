import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
V="/home/kuzimo/projects/CTradeSystem/DataVault"; SIDE=0.00185
def ladder_w(s):
    pk=s.iloc[0]; lo=s.iloc[0]; floor=1.0; w=[]
    for x in s.values:
        if x>=pk: pk=x; lo=x; floor=1.0; w.append(1.0); continue
        dd=x/pk-1
        if x<lo: lo=x
        depth=lo/pk-1
        floor=1.0 if depth>-0.10 else 0.75 if depth>-0.20 else 0.5 if depth>-0.30 else 0.25
        reb=x/lo-1
        w.append(min(1.0,floor+0.25*np.floor(reb/0.10)) if depth<=-0.10 else 1.0)
    return pd.Series(w,index=s.index)
M=pd.read_csv("monthly_funds_kospi.csv",index_col=0,parse_dates=True); M["r"]=M.신용융자/M.예탁금; rm=M.r.copy(); rm.index=rm.index.to_period("M")
def credit_w(idx,c=0.33,w=0.5,lag=2):
    src=idx.to_period("M")-lag; v=pd.Series([rm.get(p,np.nan) for p in src],index=idx).ffill(); return pd.Series(np.where(v<c,1.0,w),index=idx)
def run(s,w):
    ret=s.pct_change().fillna(0); w=w.shift(1).fillna(1.0)  # 다음날 체결
    return (1+(w*ret-w.diff().abs().fillna(0)*SIDE)).cumprod()
def st(x,a,b):
    x=x[(x.index>=a)&(x.index<=b)]
    if len(x)<120: return None
    l=np.log(x).diff().dropna(); y=len(l)/252
    return dict(C=(np.exp(l.sum())**(1/y)-1)*100,S=l.mean()/l.std()*np.sqrt(252),M=((x/x.cummax())-1).min()*100)
ko=pd.read_csv("kospi_daily_ecos.csv",index_col=0,parse_dates=True).iloc[:,0]
sam=pd.read_csv(f"{V}/raw/creon/candle_1d/A005930_1d.csv",usecols=["timestamp","upbit_close"],parse_dates=["timestamp"]).set_index("timestamp").upbit_close; sam=sam[sam>0]; sam.index=sam.index.normalize(); sam=sam[~sam.index.duplicated()]; sam=sam[sam.index>="1999-06-01"]
pf=pd.read_csv("../20261026_comove_select/strategy_B_path.csv",index_col=0,parse_dates=True)["hi"]
T=[("KOSPI",ko,{"개발 ~2009":("1996-01-01","2009-12-31"),"검증 2010~":("2010-01-01","2026-10-26"),"전체":("1996-01-01","2026-10-26")}),
   ("삼성전자",sam,{"개발 ~2009":("2000-01-01","2009-12-31"),"검증 2010~":("2010-01-01","2026-10-26"),"전체":("2000-01-01","2026-10-26")}),
   ("동행성 포트B",pf,{"개발 2015~20":("2015-01-01","2020-12-31"),"검증 2021~25":("2021-01-01","2025-12-31"),"2026":("2026-01-01","2026-10-23")})]
for name,s,per in T:
    wl=ladder_w(s); wc=credit_w(s.index)
    V_={"보유":s/s.iloc[0],"L 사다리":run(s,wl),"신용만":run(s,wc),"L×C":run(s,wl*wc)}
    print(f"\n### {name}  (평균비중 L {wl.mean():.2f} · L×C {(wl*wc).mean():.2f})")
    for k,(a,b) in per.items():
        print(f"{k:14s}"+" | ".join(f"{n}: {st(v,a,b)['C']:5.1f}% 샤{st(v,a,b)['S']:4.2f} MDD{st(v,a,b)['M']:6.1f}" for n,v in V_.items() if st(v,a,b)))
# 이번 하락 현재 위치
wl=ladder_w(ko); print("\nKOSPI 현재 사다리 목표 비중:",round(wl.iloc[-1],2),"(저점 대비 반등",round((ko.iloc[-1]/ko[ko.idxmax():].min()-1)*100,1),"%, 낙폭",round((ko.iloc[-1]/ko.max()-1)*100,1),"%)")
