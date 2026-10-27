import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
exec(open("comove_persistence.py").read().split("def fwd")[0])
COST=0.00185; dev=("2016-01-01","2023-01-01"); hold=("2023-01-01","2027-01-01")
def monthly(sig):
    d=sig.reindex(kd).fillna(False); me=d.groupby([d.index.year,d.index.month]).tail(1).index
    pos=pd.Series(np.nan,index=kd); pos.loc[me]=d.loc[me].astype(float); return pos.shift(1).ffill().fillna(0.0)
def daily(sig): return sig.reindex(kd).fillna(False).astype(float).shift(1).fillna(0.0)
def d2(asset):
    a=asset.reindex(kd); a50=a>a.rolling(50).mean(); hi60=a.rolling(60).max(); ent=(breadth>=4)&a50
    mu=C["MU"]; eq=C["미국장비"]; mu_dn=(mu<mu.rolling(50).mean())&(mu.shift(1)>=mu.rolling(50).mean().shift(1)); eq_dn=(eq<eq.rolling(50).mean())&(eq.shift(1)>=eq.rolling(50).mean().shift(1))
    ex=((a<hi60*0.90)|mu_dn|eq_dn); pos=np.zeros(len(kd)); inpos=False; last_exit=-99
    for i in range(len(kd)):
        if inpos and ex.iloc[i]: inpos=False; last_exit=i
        elif (not inpos) and ent.iloc[i] and i-last_exit>=5: inpos=True
        pos[i]=1.0 if inpos else 0.0
    return pd.Series(pos,index=kd).shift(1).fillna(0.0)
def run(asset,pos): r=np.log(asset.reindex(kd)).diff().fillna(0); return pos*r-pos.diff().abs().fillna(0)*COST
def met(net,pos,a,b):
    x=net[(net.index>=a)&(net.index<b)]; p=pos[(pos.index>=a)&(pos.index<b)]; yrs=len(x)/252; cum=np.exp(x.cumsum())
    return (np.exp(x.sum())**(1/yrs)-1)*100, x.mean()/x.std()*np.sqrt(252), (cum/cum.cummax()-1).min()*100, p.mean()*100, (p.diff().abs().sum()/2)/yrs
rows=[]
for an,asset in [("한국 메모리",mem),("한국 소부장",sobu)]:
    P={"BH":pd.Series(1.0,index=kd),"M1 월말 폭":monthly(breadth>=4),"D1 일 단위 폭":daily(breadth>=4),"D2 전환 즉시 매도":d2(asset)}
    for rn,pos in P.items():
        net=run(asset,pos)
        for per,(a,b) in {"개발":dev,"검증":hold}.items(): rows.append((an,rn,per,*met(net,pos,a,b)))
T=pd.DataFrame(rows,columns=["자산","규칙","구간","CAGR%","샤프","MDD%","투자%","연거래수"]); pd.set_option("display.width",200); print(T.round(2).to_string(index=False)); T.to_csv("exit_backtest.csv",index=False)
print("\n=== 판정 ===")
for an in ["한국 메모리","한국 소부장"]:
    for rn in ["D1 일 단위 폭","D2 전환 즉시 매도"]:
        ok=[]
        for per in ["개발","검증"]:
            g=lambda r:T[(T.자산==an)&(T.규칙==r)&(T.구간==per)].iloc[0]; x,b,m=g(rn),g("BH"),g("M1 월말 폭")
            ok.append((per,x.샤프>b.샤프 and x["MDD%"]>b["MDD%"],x.샤프>=m.샤프 and x["MDD%"]>=m["MDD%"]))
        print(f"  {an} {rn}: "+" | ".join(f"{p}: BH 대비 {'○' if a else '×'}, 월말 대비 {'○' if c else '×'}" for p,a,c in ok))
