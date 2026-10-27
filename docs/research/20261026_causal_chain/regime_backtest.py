import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
exec(open("comove_persistence.py").read().split("def fwd")[0])   # C, breadth, corr, mem, sobu, kospi, kd 재사용
COST=0.00185
dev=("2016-01-01","2023-01-01"); hold=("2023-01-01","2027-01-01")
q67=corr[(corr.index>=dev[0])&(corr.index<dev[1])].quantile(2/3)
def monthly_signal(sig):
    d=sig.reindex(kd).fillna(False); me=d.groupby([d.index.year,d.index.month]).tail(1).index
    pos=pd.Series(np.nan,index=kd); pos.loc[me]=d.loc[me].astype(float); pos=pos.shift(1).ffill()   # 월말 신호 → 다음 거래일부터
    return pos.fillna(0.0)
def run(asset,pos):
    r=np.log(asset.reindex(kd)).diff().fillna(0); chg=pos.diff().abs().fillna(0); net=pos*r-chg*COST
    return net
def metrics(net,a,b):
    x=net[(net.index>=a)&(net.index<b)]; yrs=len(x)/252; cum=np.exp(x.cumsum()); dd=(cum/cum.cummax()-1).min()
    return dict(CAGR=(np.exp(x.sum())**(1/yrs)-1)*100,샤프=x.mean()/x.std()*np.sqrt(252) if x.std()>0 else np.nan,MDD=dd*100,투자=0)
res=[]
for aname,asset in [("한국 메모리",mem),("한국 소부장",sobu)]:
    own50=(asset.reindex(kd)>asset.reindex(kd).rolling(50).mean()); own200=(asset.reindex(kd)>asset.reindex(kd).rolling(200).mean())
    sigs={"BH":pd.Series(True,index=kd),"S1 폭≥4":breadth>=4,"S2 폭≥4&동행↑":(breadth>=4)&(corr>=q67),"S3 자기50일선":own50,"S4 자기200일선":own200}
    for sn,sg in sigs.items():
        pos=monthly_signal(sg) if sn!="BH" else pd.Series(1.0,index=kd)
        net=run(asset,pos)
        for per,(a,b) in {"개발":dev,"검증":hold}.items():
            m=metrics(net,a,b); x=pos[(pos.index>=a)&(pos.index<b)]; m["투자"]=x.mean()*100; m["연 거래수"]=(x.diff().abs().sum()/2)/(len(x)/252)
            res.append((aname,sn,per,m["CAGR"],m["샤프"],m["MDD"],m["투자"],m["연 거래수"]))
T=pd.DataFrame(res,columns=["자산","규칙","구간","CAGR%","샤프","MDD%","투자비중%","연 거래수"]); pd.set_option("display.width",200); print(T.round(2).to_string(index=False)); T.to_csv("regime_backtest.csv",index=False)
print(f"\n동행 경계(개발 상위 1/3) {q67:.2f}; 현재 상관 {corr.iloc[-1]:.2f}, 폭 {int(breadth.iloc[-1])}/5")
print("\n=== 핵심 판정: S2 vs S3 (샤프 높고 MDD 얕은가) ===")
for aname in ["한국 메모리","한국 소부장"]:
    out=[]
    for per in ["개발","검증"]:
        a=T[(T.자산==aname)&(T.구간==per)&(T.규칙=="S2 폭≥4&동행↑")].iloc[0]; b=T[(T.자산==aname)&(T.구간==per)&(T.규칙=="S3 자기50일선")].iloc[0]
        out.append(f"{per}: S2 샤프 {a.샤프:.2f}/MDD {a['MDD%']:.0f}% vs S3 {b.샤프:.2f}/{b['MDD%']:.0f}% → "+("S2 우위" if (a.샤프>b.샤프 and a['MDD%']>=b['MDD%']) else "S2 비우위"))
    print(f"  {aname}: "+" | ".join(out))
