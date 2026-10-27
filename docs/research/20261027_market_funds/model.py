import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
from scipy.optimize import minimize
Y="/home/kuzimo/projects/CTradeSystem/DataVault/raw/yahoo/candle_1d"
T="SPY QQQ IWM DIA EFA EEM EWY EWJ EWT FXI EWG EWU EWZ INDA EWH EWA EWC EWW EWS EWP EWI EWQ ILF VGK".split()
S={t:pd.read_csv(f"{Y}/{t}_1d.csv",usecols=["timestamp","close"],parse_dates=["timestamp"]).set_index("timestamp").close.dropna() for t in T}
S["KOSPI"]=pd.read_csv("kospi_daily_ecos.csv",index_col=0,parse_dates=True).iloc[:,0]
for k,s in S.items(): s.index=s.index.normalize(); S[k]=s[~s.index.duplicated()]
rows=[]
def build(name,c):
    dd=(c/c.cummax()-1)*100; idx=c.index; pk=idx[0]; start=0; inn=False; out=[]
    i=0; n=len(c); peak_i=0
    cm=c.cummax()
    # 에피소드 구간 분할: 신고가 갱신일 기준
    newhigh=(c>=cm).values
    seg_start=0
    for i in range(n):
        if newhigh[i]:
            if i-seg_start>1: out.append((seg_start,i)); 
            seg_start=i
    out.append((seg_start,n-1,True))   # 마지막(미완료)
    res=[]
    for seg in out:
        a,b=seg[0],seg[1]; done=len(seg)==2
        v=c.values[a:b+1]; 
        if (v.min()/v[0]-1)*100>-20: continue
        low_run=np.minimum.accumulate(v); fin=v.min()
        for j in range(1,len(v),5):
            d=(v[j]/v[0]-1)*100
            if d>-20: continue
            lo_i=int(np.argmin(v[:j+1])); 
            res.append(dict(자산=name,t=idx[a+j],고점=idx[a],완료=done,낙폭=d,일수=(idx[a+j]-idx[a]).days,
              속도=d/max((idx[a+j]-idx[a]).days,1)*30,저점후일=(idx[a+j]-idx[a+lo_i]).days,
              반등=(v[j]/v[lo_i]-1)*100,r20=(v[j]/v[max(j-20,0)]-1)*100,
              추가하락=(v[j:].min()/v[j]-1)*100))
    return res
for n,c in S.items(): rows+=build(n,c)
A=pd.DataFrame(rows); A["바닥나옴"]=(A.추가하락>-5).astype(int)
tr=A[A.완료].copy(); tr["군집"]=tr.고점.dt.year//3
print(f"학습 관측 {len(tr)}, 에피소드 {tr.groupby(['자산','고점']).ngroups}, 군집 {tr.군집.nunique()}, 바닥나옴 비율 {tr.바닥나옴.mean():.2f}")
F0=["낙폭","일수","속도","저점후일","반등","r20"]
def tf(df,cols,mu=None,sd=None):
    X=df[cols].copy(); X["일수"]=np.log1p(X["일수"]) if "일수" in cols else 0; 
    if "저점후일" in cols: X["저점후일"]=np.log1p(X["저점후일"])
    if "속도" in cols: X["속도"]=-np.log1p(-X["속도"])*-1 if False else X["속도"].clip(-30,0)
    return X
def fit(X,y,l2=1.0):
    mu,sd=X.mean(),X.std()+1e-9; Z=((X-mu)/sd).values; Z=np.c_[np.ones(len(Z)),Z]
    def f(w): z=Z@w; return np.sum(np.logaddexp(0,z)-y*z)+l2*np.sum(w[1:]**2)
    w=minimize(f,np.zeros(Z.shape[1]),method="BFGS").x; return w,mu,sd
def pred(m,X): w,mu,sd=m; Z=np.c_[np.ones(len(X)),((X-mu)/sd).values]; return 1/(1+np.exp(-(Z@w)))
def auc(p,y):
    r=pd.Series(p).rank().values; n1=y.sum(); n0=len(y)-n1; return (r[y==1].sum()-n1*(n1+1)/2)/(n1*n0)
def loco(cols,df=tr):
    X=tf(df,cols); y=df.바닥나옴.values; p=np.zeros(len(df))
    for g in df.군집.unique():
        te=(df.군집==g).values; m=fit(X[~te],y[~te]); p[te]=pred(m,X[te])
    return p
sets={"낙폭만":["낙폭"],"기간만(일수)":["일수"],"속도만":["속도"],"낙폭+일수+속도":["낙폭","일수","속도"],
      "반등·저점후일":["반등","저점후일"],"전체 6개":F0}
print("\n군집 제외 검증 AUC (0.5=무의미)")
res={}
for k,c in sets.items():
    p=loco(c); res[k]=p; ae=[auc(p[(tr.군집==g).values],tr.바닥나옴.values[(tr.군집==g).values]) for g in tr.군집.unique() if 0<tr.바닥나옴[(tr.군집==g)].sum()<(tr.군집==g).sum()]
    print(f"{k:16s} 전체 AUC {auc(p,tr.바닥나옴.values):.2f}  군집별 평균 {np.mean(ae):.2f} (최소 {np.min(ae):.2f}, n={len(ae)})")
# 확률 구간별 실제 바닥나옴 비율 (전체 6개)
p=res["전체 6개"]; q=pd.qcut(p,5,labels=False)+1
print("\n예측확률 5분위별 실제 '바닥 나옴' 비율:",tr.groupby(q).바닥나옴.mean().round(2).tolist(),"예측 평균",pd.Series(p).groupby(q).mean().round(2).tolist())
# 군집별 성과 (과적합 점검)
print("군집별 AUC(전체 6개):",{int(g)*3:round(auc(p[(tr.군집==g).values],tr.바닥나옴.values[(tr.군집==g).values]),2) for g in tr.군집.unique() if 0<tr.바닥나옴[(tr.군집==g)].sum()<(tr.군집==g).sum()})
# 계수
m=fit(tf(tr,F0),tr.바닥나옴.values); print("\n계수(표준화, +면 바닥 가능성↑):",dict(zip(["절편"]+F0,np.round(m[0],2))))
# 현재 KOSPI
cur=A[(A.자산=="KOSPI")&(~A.완료)].tail(1); 
c=S["KOSPI"]; pkd=c.idxmax(); v=c[pkd:]; j=len(v)-1; lo=v.idxmin()
now=pd.DataFrame([dict(낙폭=(v.iloc[-1]/v.iloc[0]-1)*100,일수=(v.index[-1]-pkd).days,속도=((v.iloc[-1]/v.iloc[0]-1)*100)/max((v.index[-1]-pkd).days,1)*30,저점후일=(v.index[-1]-lo).days,반등=(v.iloc[-1]/v.min()-1)*100,r20=(v.iloc[-1]/v.iloc[-21]-1)*100)])
print("\n현재 KOSPI 상태:",now.round(1).to_dict("records")[0])
pn=pred(m,tf(now,F0))[0]; print(f"모델 '바닥 이미 나옴' 확률 {pn:.2f} (학습 기준 비율 {tr.바닥나옴.mean():.2f})")
for nm,cols in (("낙폭만",["낙폭"]),("낙폭+일수+속도",["낙폭","일수","속도"])):
    mm=fit(tf(tr,cols),tr.바닥나옴.values); print(f"  {nm}: {pred(mm,tf(now,cols))[0]:.2f}")
# 추가하락 규모 회귀(중앙값 분위): 전체 6개에서 비슷한 상태(최근접 이웃 50) 의 추가하락 분포
Z=tf(tr,F0); mu,sd=Z.mean(),Z.std(); d=(((Z-mu)/sd).values-((tf(now,F0)-mu)/sd).values)**2; nn=np.argsort(d.sum(1))[:80]
sub=tr.iloc[nn]; print(f"\n현재와 가장 비슷한 80개 상태(에피소드 {sub.groupby(['자산','고점']).ngroups}개, 군집 {sub.군집.nunique()}개)의 추가 하락: 중앙 {sub.추가하락.median():.1f}%, 25% {sub.추가하락.quantile(.25):.1f}, 75% {sub.추가하락.quantile(.75):.1f}, 5% 미만 비율 {sub.바닥나옴.mean():.2f}")
tr.to_csv("model_states.csv",index=False,encoding="utf-8-sig")
