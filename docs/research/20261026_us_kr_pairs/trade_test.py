import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
R="/home/kuzimo/projects/CTradeSystem"; V=f"{R}/DataVault"; COST=0.0037
def us(t): d=pd.read_csv(f"{V}/raw/yahoo/candle_1d/{t}_1d.csv",usecols=["timestamp","close"],parse_dates=["timestamp"]).set_index("timestamp").close; d.index=d.index.normalize(); return np.log(d[~d.index.duplicated()]).diff().dropna()
spy=us("SPY"); sig_src={"MU":us("MU"),"장비3":pd.concat([us(t) for t in ["AMAT","LRCX","KLAC"]],axis=1,sort=True).mean(axis=1)}
pr=pd.read_parquet(f"{V}/staging/kr_equity/derived/prices_1d.parquet",columns=["code","date","open","close"]); pr["date"]=pd.to_datetime(pr.date)
PAIRS=[("MU","A000660","SK하이닉스"),("장비3","A240810","원익IPS"),("장비3","A084370","유진테크"),("장비3","A095610","테스")]
dev=("2016-01-01","2023-01-01"); hold=("2023-01-01","2027-01-01")
def resid_us(s):
    j=pd.concat([s,spy],axis=1,sort=True).dropna(); j.columns=["a","m"]; jd=j[(j.index>=dev[0])&(j.index<dev[1])]; b=np.polyfit(jd.m,jd.a,1); return (j.a-np.polyval(b,j.m))
rows=[]
for sname,code,nm in PAIRS:
    r=resid_us(sig_src[sname]); k=pr[pr.code==code].set_index("date").sort_index(); k=k[k.open>0]; kd=k.index
    # 신호: 미국 d → 한국 d 다음 세션(여러 미국일이 한 한국일에 겹치면 합산)
    pos=np.searchsorted(kd.values,r.index.values,side="right"); ok=pos<len(kd)
    sg=pd.Series(0.0,index=kd); np.add.at(sg.values,pos[ok],r.values[ok]); sg=sg.where(sg!=0)
    o=k.open; c=k.close; prev=c.shift(1)
    gap=np.log(o/prev); intra=np.log(c/o); two=np.log(c.shift(-1)/o)
    d=pd.DataFrame({"sig":sg,"gap":gap,"intra":intra,"two":two}).dropna(subset=["sig","gap","intra"])
    q=d[(d.index>=dev[0])&(d.index<dev[1])].sig.quantile([.33,.67,.80])
    for qn,th in [("q67",q[.67]),("q80",q[.80])]:
        for per,(a,b) in {"개발":dev,"검증":hold}.items():
            x=d[(d.index>=a)&(d.index<b)]; sel=x[x.sig>=th]; al=x
            for hz,col in [("open→close","intra"),("open→다음 종가","two")]:
                v=sel[col].dropna(); base=al[col].dropna()
                rows.append((nm,qn,per,hz,len(v),v.mean()*100,(v.mean()-COST)*100,(v.mean()-COST)/(v.std()/np.sqrt(len(v)))*np.sqrt(1) if len(v)>2 else np.nan,(v-COST>0).mean(),base.mean()*100,sel.gap.mean()*100,al.gap.mean()*100))
T=pd.DataFrame(rows,columns=["종목","임계","구간","보유","n","평균%(비용전)","평균%(비용후)","t(비용후)","승률(비용후)","무조건 평균%","신호일 갭%","무조건 갭%"])
pd.set_option("display.width",250); print(T.round(2).to_string(index=False))
T.to_csv("trade_test.csv",index=False)
print("\n=== 합격 판정(검증 비용 후 평균>0 & t≥1.5 & 개발 부호 일치) ===")
for (nm,qn,hz),g in T.groupby(["종목","임계","보유"]):
    d_=g[g.구간=="개발"].iloc[0]; h_=g[g.구간=="검증"].iloc[0]
    ok=(h_["평균%(비용후)"]>0)&(h_["t(비용후)"]>=1.5)&(d_["평균%(비용후)"]>0)
    print(f"  {nm} {qn} {hz}: 개발 {d_['평균%(비용후)']:+.2f}%(n{int(d_.n)}) 검증 {h_['평균%(비용후)']:+.2f}%(t{h_['t(비용후)']:+.1f}, n{int(h_.n)}) → {'합격' if ok else '불합격'}")
