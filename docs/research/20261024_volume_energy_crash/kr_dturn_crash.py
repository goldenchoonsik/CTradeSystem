"""거래량 추세 이탈(DTURN) → 이후 6개월 종목 낙폭. 사전등록: PREREGISTRATION.md
  .venv/bin/python docs/research/20261024_volume_energy_crash/kr_dturn_crash.py
"""
import os,numpy as np,pandas as pd
REPO=os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..",".."))
KR=os.path.join(REPO,"DataVault","staging","kr_equity","derived")
p=pd.read_parquet(f"{KR}/prices_1d.parquet",columns=["code","date","close","close_unadj","value"])
e=pd.read_parquet(f"{KR}/daily_ext.parquet",columns=["code","date","market_cap"])
p["date"]=pd.to_datetime(p.date); e["date"]=pd.to_datetime(e.date)
ordinary=[c for c in p.code.unique() if c.endswith("0")]
p=p[p.code.isin(ordinary)]; e=e[e.code.isin(ordinary)]
pv=lambda df,c: df.pivot(index="date",columns="code",values=c).sort_index()
C=pv(p,"close"); U=pv(p,"close_unadj"); V=pv(p,"value").fillna(0).where(C.notna(),np.nan); M=pv(e,"market_cap").reindex(C.index).ffill(limit=5)
print("종목",C.shape[1],"거래일",C.shape[0])
H=126
recent=V.rolling(H,min_periods=100).mean(); prior=V.rolling(378,min_periods=300).mean().shift(H)
dturn=np.log(recent.where(recent>0)/prior.where(prior>0))
r=C.pct_change(fill_method=None); vol=r.rolling(H,min_periods=100).std(); pr=C/C.shift(H)-1
med20=V.rolling(20,min_periods=15).median()
fmin=C.iloc[::-1].rolling(H,min_periods=20).min().iloc[::-1]
mdd=fmin/C-1
idx=C.index; last_ok=len(idx)-H-1
me=pd.Series(idx,index=idx).groupby([idx.year,idx.month]).last().values
forms=[d for d in me if d>=pd.Timestamp("2015-01-01") and idx.get_loc(d)<=last_ok]
rows=[]
for d in forms:
    ok=(C.loc[d].notna())&(U.loc[d]>=1000)&(M.loc[d]>=1e11)&(med20.loc[d]>=3e8)&dturn.loc[d].notna()&vol.loc[d].notna()&pr.loc[d].notna()&mdd.loc[d].notna()
    x=pd.DataFrame({"dt":dturn.loc[d][ok],"vol":vol.loc[d][ok],"pr":pr.loc[d][ok],"m":mdd.loc[d][ok]})
    if len(x)<300: continue
    x["dtp"]=x.dt.rank(pct=True); x["crash"]=(x.m<=-.30).astype(float)
    x["vb"]=pd.qcut(x.vol,3,labels=False); x["pb"]=pd.qcut(x.pr,3,labels=False)
    top=x[x.dtp>=.8]; bot=x[x.dtp<=.2]
    plain=(top.m.mean()-bot.m.mean(),top.crash.mean()-bot.crash.mean())
    d1=[];d2=[]
    for v in range(3):
        for q in range(3):
            c=x[(x.vb==v)&(x.pb==q)]; t=c[c.dtp>=.8]; b=c[c.dtp<=.2]   # 칸 안 상/하위 20% (칸 내 순위 재계산)
            c=c.assign(rk=c.dt.rank(pct=True)); t=c[c.rk>=.8]; b=c[c.rk<=.2]
            if len(t)<10 or len(b)<10: continue
            d1.append(t.m.mean()-b.m.mean()); d2.append(t.crash.mean()-b.crash.mean())
    if not d1: continue
    rows.append(dict(date=d,n=len(x),plain_m=plain[0],plain_c=plain[1],D_m=np.mean(d1),D_c=np.mean(d2),base_c=x.crash.mean(),base_m=x.m.mean()))
R=pd.DataFrame(rows).set_index("date"); R.to_csv(os.path.join(os.path.dirname(__file__),"monthly_diff.csv"))
def nw(s,L=5):
    s=s.dropna(); n=len(s); m=s.mean(); u=s-m; g=[(u*u).sum()/n]+[(u[k:].values*u[:-k].values).sum()/n for k in range(1,L+1)]
    var=g[0]+2*sum((1-k/(L+1))*g[k] for k in range(1,L+1)); return m,m/np.sqrt(var/n)
out=[f"형성 시점 {len(R)}개월 ({R.index[0].date()}~{R.index[-1].date()}) · 평균 표본 {R.n.mean():.0f}종목 · 기본 급락(-30%) 확률 {100*R.base_c.mean():.1f}% · 평균 최대낙폭 {100*R.base_m.mean():.1f}%"]
def rep(lab,sub):
    a=nw(sub.D_m); b=nw(sub.D_c); pa=nw(sub.plain_m); pb=nw(sub.plain_c)
    ns=sub.iloc[::6]; ns1=nw(ns.D_m,0); ns2=nw(ns.D_c,0)
    out.append(f"{lab} (n={len(sub)}): 통제 후  M1(최대낙폭) {100*a[0]:+.2f}%p (t={a[1]:+.1f}) · M2(급락확률) {100*b[0]:+.2f}%p (t={b[1]:+.1f}) | 통제 전 M1 {100*pa[0]:+.2f}%p (t={pa[1]:+.1f}) M2 {100*pb[0]:+.2f}%p (t={pb[1]:+.1f}) | 비겹침 M1 {100*ns1[0]:+.2f}%p(t={ns1[1]:+.1f}) M2 {100*ns2[0]:+.2f}%p(t={ns2[1]:+.1f}, n={len(ns)})")
rep("전체",R); rep("전반 2015~2020",R[R.index<"2021-01-01"]); rep("후반 2021~",R[R.index>="2021-01-01"])
open(os.path.join(os.path.dirname(__file__),"results.md"),"w").write("# 결과 (자동 생성)\n\n"+"\n\n".join(out)+"\n"); print("\n".join(out))
