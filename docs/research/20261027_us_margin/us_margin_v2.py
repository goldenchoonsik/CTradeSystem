import numpy as np,pandas as pd,urllib.request,io,warnings; warnings.filterwarnings("ignore")
V="/home/kuzimo/projects/CTradeSystem/DataVault"; SIDE=0.0005
m=pd.read_csv(f"{V}/staging/us_margin_finra/margin_monthly.csv"); m["d"]=pd.to_datetime(m.month+"-01")+pd.offsets.MonthEnd(0); m=m.set_index("d")
def px(t):
    s=pd.read_csv(f"{V}/raw/yahoo/candle_1d/{t}_1d.csv",usecols=["timestamp","close"],parse_dates=["timestamp"]).set_index("timestamp").close; s.index=s.index.normalize(); return s[~s.index.duplicated()]
SPY,QQQ=px("SPY"),px("QQQ")
sm=SPY.resample("ME").last()
S=pd.DataFrame({"debit":m.debit}).join(sm.rename("P"),how="left"); S["W"]=S.P
S["a"]=np.log(S.debit).diff(12)*100; S["b"]=S.a-np.log(S.P).diff(12)*100; S["c"]=S.debit/S.W
S["f12"]=((np.log(S.P).shift(-12)-np.log(S.P))*100)
cands={"a 부채 YoY":"a","b 부채YoY−SPY YoY":"b","c 부채/SPY가격":"c"}
def pct(x): return x.expanding(60).apply(lambda s:(s<=s.iloc[-1]).mean(),raw=False)
for k,c in cands.items(): S["p_"+c]=pct(S[c].dropna()).reindex(S.index)
print("\n### 지표별: 확장창 70%분위↑ 경고월의 이후 12개월 SPY (기간별)")
for k,c in cands.items():
    x=S.dropna(subset=["p_"+c,"f12"]); h=x["p_"+c]>=.7
    out=[]
    for lab,a,b in (("~2009","1997","2009"),("2010~2018","2010","2018"),("2019~","2019","2026")):
        y=x[a:b]; hh=h[y.index]
        out.append(f"{lab}: 경고 {int(hh.sum())}/{len(hh)} 평균 {y.f12[hh].mean():5.1f}(승 {(y.f12[hh]>0).mean()*100:3.0f}) vs 나머지 {y.f12[~hh].mean():5.1f}(승 {(y.f12[~hh]>0).mean()*100:3.0f})")
    print(k,"|"," | ".join(out))
def wser(idx,col,lag=2):
    p=S["p_"+col].copy(); p.index=p.index.to_period("M"); src=idx.to_period("M")-lag
    v=pd.Series([p.get(q,np.nan) for q in src],index=idx).ffill(); return pd.Series(np.where(v>=.7,0.5,1.0),index=idx)
def run(s,w): r=s.pct_change().fillna(0); w=w.shift(1).fillna(1.0); return (1+(w*r-w.diff().abs().fillna(0)*SIDE)).cumprod()
def st(x,a,b):
    x=x[(x.index>=a)&(x.index<=b)]; l=np.log(x).diff().dropna(); y=len(l)/252
    return dict(C=(np.exp(l.sum())**(1/y)-1)*100,S=l.mean()/l.std()*np.sqrt(252),M=((x/x.cummax())-1).min()*100)
per={"개발 ~2009":("1997-01-01","2009-12-31"),"검증 2010~":("2010-01-01","2026-10-26"),"2022~":("2022-01-01","2026-10-26")}
for name,s in (("SPY",SPY[SPY.index>="1997-01-01"]),("QQQ",QQQ)):
    print(f"\n### {name}")
    base=s/s.iloc[0]
    for k,(a,b) in per.items():
        h=st(base,a,b); line=f"{k:11s}보유 {h['C']:5.1f}% 샤{h['S']:4.2f} MDD{h['M']:6.1f}"
        for kk,c in cands.items():
            w=wser(s.index,c); o=run(s,w); r_=st(o,a,b); mw=w[a:b].mean()
            const=run(s,pd.Series(mw,index=s.index)); cc=st(const,a,b)
            line+=f"\n   {kk}: 규칙 {r_['C']:5.1f}% 샤{r_['S']:4.2f} MDD{r_['M']:6.1f} (평균비중 {mw:.2f}) | 같은 비중 고정 샤{cc['S']:4.2f} MDD{cc['M']:6.1f}"
        print(line)
for k,c in cands.items():
    p=S["p_"+c].dropna(); print(f"현재 {k}: 값 {S[c].dropna().iloc[-1]:.2f}, 확장창 백분위 {p.iloc[-1]:.2f}")
