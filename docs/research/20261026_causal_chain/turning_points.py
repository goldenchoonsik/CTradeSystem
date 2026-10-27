"""동반 성장 구간에서는 시차 상관이 묻히므로 '전환점(고점·저점)' 시각을 비교: 지그재그(되돌림 ≥20%) 극값 날짜를 NVDA·메모리·소부장·미국 장비주에서 뽑아 가장 가까운 극값 쌍의 시차(일)."""
import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
V="/home/kuzimo/projects/CTradeSystem/DataVault"
def us(t): d=pd.read_csv(f"{V}/raw/yahoo/candle_1d/{t}_1d.csv",parse_dates=["timestamp"]).set_index("timestamp").close; d.index=d.index.normalize(); return d
pr=pd.read_parquet(f"{V}/staging/kr_equity/derived/prices_1d.parquet",columns=["code","date","close"]); pr["date"]=pd.to_datetime(pr.date)
def kr(c): return pr[pr.code==c].set_index("date").close
def idx(cs): x=pd.concat([np.log(kr(c)).diff() for c in cs],axis=1,sort=True).mean(axis=1).fillna(0).cumsum(); return np.exp(x)
mem=idx(["A000660","A005930"]); sobu=idx(["A042700","A240810","A036930","A039030","A319660","A095610","A084370","A058470","A007660"])
usb=np.exp(pd.concat([np.log(us(t)).diff() for t in ["AMAT","LRCX","KLAC","ASML"]],axis=1,sort=True).mean(axis=1).fillna(0).cumsum())
S={"NVDA":us("NVDA"),"MU":us("MU"),"미국장비":usb,"한국 메모리":mem,"한국 소부장":sobu}
def zigzag(s,th=0.20):
    s=s[s.index>="2016-01-01"].dropna(); out=[]; hi=lo=s.index[0]; mode=0
    for d,v in s.items():
        if v>s[hi]: hi=d
        if v<s[lo]: lo=d
        if mode>=0 and v<s[hi]*(1-th): out.append((hi,"고점")); mode=-1; lo=d
        elif mode<=0 and v>s[lo]*(1+th): out.append((lo,"저점")); mode=1; hi=d
    return out
Z={k:zigzag(v) for k,v in S.items()}
for k,v in Z.items(): print(f"{k}: "+", ".join(f"{d:%y-%m}{t[0]}" for d,t in v))
def lag(a,b,win=150):
    r=[]
    for d,t in Z[a]:
        c=[(abs((d2-d).days),(d2-d).days) for d2,t2 in Z[b] if t2==t and abs((d2-d).days)<=win]
        if c: r.append((d,t,min(c)[1]))
    return r
print("\n전환점 시차(일): 양수 = 뒤의 계열이 늦게 전환(앞의 계열이 선행). 150일 내 같은 종류 극값 쌍")
for a,b in [("NVDA","한국 메모리"),("MU","한국 메모리"),("미국장비","한국 소부장"),("한국 메모리","한국 소부장"),("NVDA","한국 소부장")]:
    r=lag(a,b); l=[x[2] for x in r]
    print(f"  {a}→{b}: n={len(r)}, 중앙 {np.median(l) if l else float('nan'):+.0f}일, 평균 {np.mean(l) if l else float('nan'):+.0f}일, 양수(뒤가 늦음) {sum(x>0 for x in l)}/{len(l)} | "+", ".join(f"{d:%y-%m}{t[0]}{x:+d}" for d,t,x in r))
