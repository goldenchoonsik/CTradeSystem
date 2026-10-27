import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
R="/home/kuzimo/projects/CTradeSystem"; V=f"{R}/DataVault"
kuni=pd.read_csv(f"{V}/staging/kr_equity/universe.csv",dtype=str).set_index("code")
de=pd.read_parquet(f"{V}/staging/kr_equity/derived/daily_ext.parquet",columns=["code","date","market_cap"]); de["date"]=pd.to_datetime(de.date)
pr=pd.read_parquet(f"{V}/staging/kr_equity/derived/prices_1d.parquet",columns=["code","date","close"]); pr["date"]=pd.to_datetime(pr.date)
px=pr.pivot(index="date",columns="code",values="close").sort_index()
spy=pd.read_csv(f"{V}/raw/yahoo/candle_1d/SPY_1d.csv",usecols=["timestamp","close"],parse_dates=["timestamp"]).set_index("timestamp").close; spy.index=spy.index.normalize()
i=pd.read_parquet(f"{V}/staging/kr_equity/derived/index_1d.parquet",columns=["code","date","close"]); i["date"]=pd.to_datetime(i.date); ko=i[i.code=="U001"].set_index("date").close
wk=lambda s:np.log(s.resample("W-FRI").last()).diff()
pw=px.resample("W-FRI").last(); pw=np.log(pw).diff(); sw=wk(spy)
rows=[]; ALL=[]
for y in range(2014,2025):
    d0=px.index[px.index<=f"{y}-12-31"][-1]; d1=px.index[px.index<=f"{y+1}-12-31"][-1]
    cap=de[de.date==de.date[de.date<=d0].max()].set_index("code").market_cap.dropna()
    ok=[c for c in cap.index if c in kuni.index and kuni.loc[c,"is_preferred"]=="0" and kuni.loc[c,"is_spac"]=="0" and c in px.columns]
    top=cap.loc[ok].nlargest(300).index
    w=pw.loc[(pw.index<=d0)&(pw.index>d0-pd.Timedelta(days=735)),top]; s=sw.reindex(w.index)
    cor=w.apply(lambda c:c.corr(s) if c.notna().sum()>=80 else np.nan)
    p0=px.loc[d0,top]; p1=px.loc[:d1,top].ffill().iloc[-1]; ret=(p1/p0-1)*100
    past=(p0/px.loc[:d0-pd.Timedelta(days=365),top].ffill().iloc[-1]-1)*100
    df=pd.DataFrame({"cor":cor,"ret":ret,"cap":np.log(cap.loc[top]),"past":past}).dropna(subset=["cor","ret"])
    df["q"]=pd.qcut(df.cor,5,labels=False)+1; kr_=(ko.loc[:d1].iloc[-1]/ko.loc[:d0].iloc[-1]-1)*100
    g=df.groupby("q").ret.agg(["mean","median"]); rows.append(dict(년도=y,n=len(df),고동행평균=g["mean"][5],저동행평균=g["mean"][1],차평균=g["mean"][5]-g["mean"][1],고동행중앙=g["median"][5],저동행중앙=g["median"][1],상위300=df.ret.mean(),코스피=kr_,고동행_평균상관=df[df.q==5].cor.mean(),저동행_평균상관=df[df.q==1].cor.mean(),고동행_시총로그=df[df.q==5].cap.mean(),저동행_시총로그=df[df.q==1].cap.mean()))
    df['y']=y; ALL.append(df)
T=pd.DataFrame(rows).set_index("년도"); pd.set_option("display.width",230); print(T.round(1).to_string())
def summ(a,b,lab):
    x=T.loc[a:b]; d=x.차평균; m=(x.고동행중앙-x.저동행중앙)
    print(f"{lab}: 고동행 평균 {x.고동행평균.mean():.1f}% / 저동행 {x.저동행평균.mean():.1f}% / 상위300 {x.상위300.mean():.1f}% / 코스피 {x.코스피.mean():.1f}% | 차 평균 {d.mean():+.1f}%p·중앙 {d.median():+.1f}·양수 {int((d>0).sum())}/{len(d)}년·t {d.mean()/(d.std(ddof=1)/np.sqrt(len(d))):+.1f} | 중앙값 기준 차 평균 {m.mean():+.1f}%p")
print(); summ(2014,2020,"개발(형성 2014~2020)"); summ(2021,2024,"검증(형성 2021~2024)"); summ(2014,2024,"전체")
T.to_csv("comove_select.csv")

D=pd.concat(ALL); print("\n=== 통제(Fama-MacBeth: 연도별 횡단면 회귀 계수 평균, 12개월 수익(%) ~ 표준화 동행성 + 시총로그 + 직전12개월(%)) ===")
def fm(cols):
    b=[]
    for y,g in D.dropna(subset=cols+["ret"]).groupby("y"):
        X=(g[cols]-g[cols].mean())/g[cols].std(); X=np.column_stack([np.ones(len(X)),X.values]); co,*_=np.linalg.lstsq(X,g.ret.values,rcond=None); b.append(co[1:])
    b=np.array(b); return b.mean(axis=0), b.mean(axis=0)/(b.std(axis=0,ddof=1)/np.sqrt(len(b))), (b>0).sum(axis=0), len(b)
for cols in [["cor"],["cor","cap"],["cor","cap","past"]]:
    m,t,pos,n=fm(cols); print("  "+" + ".join(cols)+": "+", ".join(f"{c} {mm:+.1f}%p(t{tt:+.1f}, 양수 {p}/{n})" for c,mm,tt,p in zip(cols,m,t,pos)))
print("\n고동행(상위 20%) 구성 특성: 평균 동행 상관 %.2f, 시총 로그 평균 %.1f vs 저동행 %.1f"%(D[D.q==5].cor.mean(),D[D.q==5].cap.mean(),D[D.q==1].cap.mean()))
