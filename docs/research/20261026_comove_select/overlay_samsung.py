import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
V="/home/kuzimo/projects/CTradeSystem/DataVault"
c=pd.read_csv(f"{V}/raw/creon/candle_1d/A005930_1d.csv",usecols=["timestamp","upbit_close"],parse_dates=["timestamp"]).set_index("timestamp").upbit_close
s=c[c>0].sort_index(); s.index=s.index.normalize(); s=s[~s.index.duplicated()]
print("삼성전자(Creon 일봉, 분할 반영)",s.index.min().date(),s.index.max().date(),len(s),"최대 일간 변동 %.1f%% / %.1f%%"%(s.pct_change().min()*100,s.pct_change().max()*100))
M=pd.read_csv("../20261027_market_funds/monthly_funds_kospi.csv",index_col=0,parse_dates=True); M["r"]=M.신용융자/M.예탁금
exec(open("overlay.py").read().split("per={")[0].split("P=pd.read_csv")[0])
r=M.r.copy(); r.index=r.index.to_period("M")
def weights(idx,c,w,lag):
    src=idx.to_period("M")-lag; val=pd.Series([r.get(p,np.nan) for p in src],index=idx).ffill()
    return pd.Series(np.where(val<c,1.0,w),index=idx)
SIDE=0.00185
def apply(x,c,w,lag):
    ret=x.pct_change().fillna(0); wt=weights(x.index,c,w,lag); return (1+(wt*ret-wt.diff().abs().fillna(0)*SIDE)).cumprod(),wt
def st(x,a,b):
    x=x[(x.index>=a)&(x.index<=b)]
    if len(x)<60: return None
    l=np.log(x).diff().dropna(); y=len(l)/252
    return dict(CAGR=(np.exp(l.sum())**(1/y)-1)*100,샤프=l.mean()/l.std()*np.sqrt(252),MDD=((x/x.cummax())-1).min()*100)
per={"2000~2009":("2000-01-01","2009-12-31"),"2010~2014":("2010-01-01","2014-12-31"),"2015~2020 개발":("2015-01-01","2020-12-31"),"2021~2025 검증":("2021-01-01","2025-12-31"),"2026-01~10-26":("2026-01-01","2026-10-26"),"전체 2000~2026-10":("2000-01-01","2026-10-26")}
s=s[s.index>="1999-06-01"]
print(s.loc["2018-04-26":"2018-05-08"].to_dict())
for w in (0.5,0.0):
    ov,wt=apply(s,0.33,w,2)
    print(f"\n### 경고월 주식 비중 {w}")
    print(f"{'구간':16s}{'보유 CAGR':>10s}{'샤프':>6s}{'MDD':>8s} | {'규칙 CAGR':>10s}{'샤프':>6s}{'MDD':>8s} | 평균비중")
    for k,(a,b) in per.items():
        h=st(s,a,b); o=st(ov,a,b)
        if h and o: print(f"{k:16s}{h['CAGR']:10.1f}{h['샤프']:6.2f}{h['MDD']:8.1f} | {o['CAGR']:10.1f}{o['샤프']:6.2f}{o['MDD']:8.1f} | {wt[a:b].mean():.2f}")
# 낙폭 에피소드별: 삼성 대형 하락기 (고점→저점) 에서 규칙 손익
ov,wt=apply(s,0.33,0.5,2)
print("\n### 삼성 큰 하락기에 규칙이 줄인 낙폭 (고점~저점 구간 수익)")
dd=(s/s.cummax()-1); 
for a,b in (("2000-01-01","2001-09-30"),("2007-10-31","2008-11-30"),("2018-01-01","2018-12-31"),("2021-01-01","2022-12-31"),("2026-06-22","2026-10-26")):
    x=s[a:b]; y=ov[a:b]
    if len(x)<2: continue
    print(f"{a}~{b}: 보유 {(x.iloc[-1]/x.iloc[0]-1)*100:6.1f}%  규칙 {(y.iloc[-1]/y.iloc[0]-1)*100:6.1f}%  평균비중 {wt[a:b].mean():.2f}")
cur=M.r.iloc[-1]; print(f"\n최근 월말 비율 {cur:.3f} → {'경고' if cur>=0.33 else '정상'}; 오늘 적용 비중(2개월 전 {M.index[-2].date()} 값 {M.r.iloc[-2]:.3f}): {1.0 if M.r.iloc[-2]<0.33 else 0.5}")
