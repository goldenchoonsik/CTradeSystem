"""동행성 선택 효과의 견고성: 한 번에 한 가지만 변경(사전 고정 목록). 형성 2014~2024(연 12월 말), 12개월 보유, 5분위 고−저 차, FM 계수.
"""
import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
R="/home/kuzimo/projects/CTradeSystem"; V=f"{R}/DataVault"
kuni=pd.read_csv(f"{V}/staging/kr_equity/universe.csv",dtype=str).set_index("code")
de=pd.read_parquet(f"{V}/staging/kr_equity/derived/daily_ext.parquet",columns=["code","date","market_cap"]); de["date"]=pd.to_datetime(de.date)
pr=pd.read_parquet(f"{V}/staging/kr_equity/derived/prices_1d.parquet",columns=["code","date","close"]); pr["date"]=pd.to_datetime(pr.date)
px=pr.pivot(index="date",columns="code",values="close").sort_index()
def us(t): d=pd.read_csv(f"{V}/raw/yahoo/candle_1d/{t}_1d.csv",usecols=["timestamp","close"],parse_dates=["timestamp"]).set_index("timestamp").close; d.index=d.index.normalize(); return d
BM={"SPY":us("SPY"),"QQQ":us("QQQ")}; bw={k:np.log(v.resample("W-FRI").last()).diff() for k,v in BM.items()}
pw=np.log(px.resample("W-FRI").last()).diff()
i=pd.read_parquet(f"{V}/staging/kr_equity/derived/index_1d.parquet",columns=["code","date","close"]); i["date"]=pd.to_datetime(i.date); ko=i[i.code=="U001"].set_index("date").close
CFG=[("기준(상위300·SPY·104주)",dict()),("나스닥(QQQ) 대비",dict(bm="QQQ")),("창 52주",dict(win=52,minw=40)),("창 156주",dict(win=156,minw=120)),
     ("시총 301~600위",dict(lo=300,hi=600)),("코스피만 상위200",dict(mkt="KOSPI",hi=200)),("코스닥만 상위200",dict(mkt="KOSDAQ",hi=200)),("코스닥 상위200·QQQ",dict(mkt="KOSDAQ",hi=200,bm="QQQ"))]
def run(bm="SPY",win=104,minw=80,lo=0,hi=300,mkt=None):
    out=[]; ALL=[]
    for y in range(2014,2025):
        d0=px.index[px.index<=f"{y}-12-31"][-1]; d1=px.index[px.index<=f"{y+1}-12-31"][-1]
        cap=de[de.date==de.date[de.date<=d0].max()].set_index("code").market_cap.dropna()
        ok=[c for c in cap.index if c in kuni.index and kuni.loc[c,"is_preferred"]=="0" and kuni.loc[c,"is_spac"]=="0" and c in px.columns and (mkt is None or kuni.loc[c,"market"]==mkt)]
        top=cap.loc[ok].sort_values(ascending=False).index[lo:hi]
        w=pw.loc[(pw.index<=d0)&(pw.index>d0-pd.Timedelta(days=7*win+14)),top]; s=bw[bm].reindex(w.index)
        cor=w.apply(lambda c:c.corr(s) if c.notna().sum()>=minw else np.nan)
        p0=px.loc[d0,top]; p1=px.loc[:d1,top].ffill().iloc[-1]; ret=(p1/p0-1)*100
        df=pd.DataFrame({"cor":cor,"ret":ret}).dropna(); df=df[np.isfinite(df.ret)]
        if len(df)<60: continue
        df["q"]=pd.qcut(df.cor.rank(method="first"),5,labels=False)+1; g=df.groupby("q").ret.mean(); out.append((y,g[5],g[1],g[5]-g[1],df.ret.mean())); df["y"]=y; ALL.append(df)
    T=pd.DataFrame(out,columns=["y","hi","lo","diff","all"]).set_index("y"); D=pd.concat(ALL); b=[]
    for y,g in D.groupby("y"):
        x=(g.cor-g.cor.mean())/g.cor.std(); co=np.polyfit(x,g.ret,1)[0]; b.append(co)
    b=np.array(b)
    return T,b
rows=[]; 
for nm,kw in CFG:
    T,b=run(**kw); d=T["diff"]; dv=d.loc[:2020]; va=d.loc[2021:]
    rows.append((nm,len(T),T.hi.mean(),T.lo.mean(),T["all"].mean(),d.mean(),d.median(),int((d>0).sum()),d.mean()/(d.std(ddof=1)/np.sqrt(len(d))),dv.mean(),va.mean(),b.mean(),b.mean()/(b.std(ddof=1)/np.sqrt(len(b))),int((b>0).sum())))
R_=pd.DataFrame(rows,columns=["설정","연수","고동행%","저동행%","전체평균%","차평균%p","차중앙","양수연","t","개발차","검증차","FM계수","FM t","FM양수"]); pd.set_option("display.width",250); print(R_.round(1).to_string(index=False)); R_.to_csv("robustness.csv",index=False)
