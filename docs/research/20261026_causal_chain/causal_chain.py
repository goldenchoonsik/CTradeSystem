"""인과 고리별 단서 점검: (A) 엔비디아→메모리(실적 이벤트·주·월), (B) 메모리→소부장(시차·주·월)·미국 장비주→소부장, (C) 미국 발전 건설지출→한국 전력기기(수출·주가).
탐색·기술 통계(사전등록 아님). 시장 제거 = 미국 SPY·한국 코스피 회귀 잔차.
"""
import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
R="/home/kuzimo/projects/CTradeSystem"; V=f"{R}/DataVault"; D=f"{R}/docs/research"
def us_ohlc(t): d=pd.read_csv(f"{V}/raw/yahoo/candle_1d/{t}_1d.csv",parse_dates=["timestamp"]).set_index("timestamp"); d.index=d.index.normalize(); return d
def us(t): return np.log(us_ohlc(t).close).diff().dropna()
pr=pd.read_parquet(f"{V}/staging/kr_equity/derived/prices_1d.parquet",columns=["code","date","close"]); pr["date"]=pd.to_datetime(pr.date)
def kr(c): s=pr[pr.code==c].set_index("date").close; return np.log(s).diff().dropna()
def kb(cs): return pd.concat([kr(c) for c in cs],axis=1,sort=True).mean(axis=1)
idx=pd.read_parquet(f"{V}/staging/kr_equity/derived/index_1d.parquet",columns=["code","date","close"]); idx["date"]=pd.to_datetime(idx.date)
kospi=np.log(idx[idx.code=="U001"].set_index("date").close).diff().dropna()
MEM=["A000660","A005930"]; SOBU=["A042700","A240810","A036930","A039030","A319660","A095610","A084370","A403870","A058470","A007660"]
mem, sobu = kb(MEM), kb(SOBU)
def neut(x,m): j=pd.concat([x,m],axis=1,sort=True).dropna(); b=np.polyfit(j.iloc[:,1],j.iloc[:,0],1); return x-np.polyval(b,m.reindex(x.index).fillna(0))
def per(s,a,b=None): return s[(s.index>=pd.Timestamp(a))&((s.index<pd.Timestamp(b)) if b else True)]
print("=== A. 엔비디아 → 메모리 ===")
nv=us_ohlc("NVDA"); gap=(nv.open/nv.close.shift(1)-1)
ev=[]
for (y,q),g in gap[gap.index.month.isin([2,5,8,11])].groupby([gap[gap.index.month.isin([2,5,8,11])].index.year,gap[gap.index.month.isin([2,5,8,11])].index.quarter]):
    ev.append(g.abs().idxmax())
ev=pd.DatetimeIndex(ev); ev=ev[ev>=pd.Timestamp("2016-01-01")]  # 실적 다음 US 거래일(갭 최대일) 추정
rows=[]
for e in ev:
    prev=nv.index[nv.index.get_loc(e)-1]  # 발표일(장 마감 후) d
    kd=mem.index[mem.index>prev]; 
    if len(kd)<6: continue
    k0=kd[0]; r0=mem[k0]; r1=mem[kd[1]:kd[4]].sum()
    rows.append((prev.date(),gap[e],r0,r1,sobu.get(k0,np.nan)))
E=pd.DataFrame(rows,columns=["발표일","NVDA갭","메모리 한국 다음세션","메모리 그후4일합","소부장 다음세션"])
print(f"이벤트 {len(E)}개(분기별 갭 최대일 추정, 2016~) — NVDA 갭과 한국 메모리 다음 세션 상관 {E['NVDA갭'].corr(E['메모리 한국 다음세션']):+.2f}, 그후 4일합 {E['NVDA갭'].corr(E['메모리 그후4일합']):+.2f}, 소부장 {E['NVDA갭'].corr(E['소부장 다음세션']):+.2f}")
print(f"  NVDA 갭>0 {int((E.NVDA갭>0).sum())}건: 한국 메모리 다음 세션 평균 {E[E.NVDA갭>0]['메모리 한국 다음세션'].mean()*100:+.2f}% / 갭<0 {int((E.NVDA갭<0).sum())}건 {E[E.NVDA갭<0]['메모리 한국 다음세션'].mean()*100:+.2f}%; 부호 일치율 {(np.sign(E.NVDA갭)==np.sign(E['메모리 한국 다음세션'])).mean():.0%}")
print(E.tail(8).round(3).to_string(index=False))
sp=us("SPY")
def lagtable(u,k,a,b,neutral,offs=(-1,0,1,2)):
    kd=k.index; pos=np.searchsorted(kd.values,u.index.values,side="right"); 
    if neutral: u=neut(u,sp); k=neut(k,kospi)
    r=[]
    for o in offs:
        p=pos+o; ok=(p>=0)&(p<len(kd)); a_=u.values[ok]; kk=k.reindex(kd).values[p[ok]]; dt=kd[p[ok]]
        sel=(dt>=pd.Timestamp(a))&(dt<pd.Timestamp(b))&~np.isnan(a_)&~np.isnan(kk); r.append(np.corrcoef(a_[sel],kk[sel])[0,1])
    return r
print("\n일 단위 NVDA→한국 (j=−1 한국선행, 0 즉시, +1·+2 지연): 원 / 시장제거")
for lab,(a,b) in {"2020~23":("2020-01-01","2024-01-01"),"2024~":("2024-01-01","2027-01-01")}.items():
    for nm,k in [("메모리",mem),("소부장",sobu)]:
        print(f"  {lab} NVDA→{nm}: "+" ".join(f"{x:+.2f}" for x in lagtable(us('NVDA'),k,a,b,False))+" | "+" ".join(f"{x:+.2f}" for x in lagtable(us('NVDA'),k,a,b,True)))
def mon(s): return s.groupby(s.index.to_period("M")).sum()
nvm,spm,kom=mon(us("NVDA")),mon(sp),mon(kospi)
print("\n월 단위(초과수익=대 시장): NVDA(t) → 메모리(t,t+1,t+2) 상관, 2020~ / 2024~")
for lab,a in [("2020~","2020-01"),("2024~","2024-01")]:
    x=(nvm-spm)[a:]; 
    print(f"  {lab} "+" ".join(f"{k}:{x.corr((mon(mem)-kom).shift(-k)[a:]):+.2f}" for k in (0,1,2))+f" (n={len(x)})")
print("\n=== B. 메모리 → 소부장 ===")
for lab,(a,b) in {"2013~19":("2013-01-01","2020-01-01"),"2020~23":("2020-01-01","2024-01-01"),"2024~":("2024-01-01","2027-01-01")}.items():
    m_,s_=neut(per(mem,a,b),kospi),neut(per(sobu,a,b),kospi)
    w=lambda x:x.resample("W").sum()
    print(f"  {lab} 일 동시 {per(mem,a,b).corr(per(sobu,a,b)):+.2f}(제거 {m_.corr(s_):+.2f}) | 메모리(t)→소부장(t+1일) {m_.corr(s_.shift(-1)):+.2f} | 주 동시 {w(m_).corr(w(s_)):+.2f}, 메모리→다음 주 소부장 {w(m_).corr(w(s_).shift(-1)):+.2f}, 소부장→다음 주 메모리 {w(s_).corr(w(m_).shift(-1)):+.2f}")
mm=mon(neut(mem,kospi)); ss=mon(neut(sobu,kospi))
print("  월(시장제거): 동시 %+.2f | 메모리→다음달 소부장 %+.2f | 소부장→다음달 메모리 %+.2f | 2024~ 메모리→다음달 소부장 %+.2f"%(mm.corr(ss),mm.corr(ss.shift(-1)),ss.corr(mm.shift(-1)),mm["2024":].corr(ss.shift(-1)["2024":])))
print("  미국 장비주 평균(AMAT·LRCX·KLAC·ASML)→한국 소부장:")
usb=pd.concat([us(t) for t in ["AMAT","LRCX","KLAC","ASML"]],axis=1,sort=True).mean(axis=1)
for lab,(a,b) in {"2020~23":("2020-01-01","2024-01-01"),"2024~":("2024-01-01","2027-01-01")}.items():
    print(f"   {lab} j=−1,0,+1,+2: "+" ".join(f"{x:+.2f}" for x in lagtable(usb,sobu,a,b,False))+" | 제거 "+" ".join(f"{x:+.2f}" for x in lagtable(usb,sobu,a,b,True)))
print("\n=== C. 전력: 미국 발전 건설지출 → 한국 전력기기 ===")
def fred(f): d=pd.read_csv(f"{D}/20261026_causal_chain/{f}.csv",parse_dates=["observation_date"]).set_index("observation_date").iloc[:,0]; d.index=d.index.to_period("M"); return d
pw=fred("PRPWRCONS"); pwy=np.log(pw).diff(12)
X=pd.read_csv(f"{D}/20261025_export_leading/exports_monthly_hs4.csv",parse_dates=["date"],dtype={"hs4":str}); X=X.pivot(index="date",columns="hs4",values="exp"); X.index=X.index.to_period("M")
KRP=["A267260","A010120","A298040","A103590","A001440"]; krp=kb(KRP); krpm=mon(neut(krp,kospi)); r12=krpm.rolling(12).sum()
print(f"  미국 민간 발전 건설지출 최신 {pw.index[-1]} ${pw.iloc[-1]/1000:.0f}bn 연율, 전년비 {pwy.iloc[-1]*100:+.0f}% (2023-12 {pwy.get(pd.Period('2023-12','M'),np.nan)*100:+.0f}%)")
for hs,nm in [("8504","변압기 등(8504)"),("8544","전선(8544)"),("8537","배전반(8537)")]:
    ey=np.log(X[hs]).diff(12); j=pd.concat([pwy,ey],axis=1).dropna(); j.columns=["p","e"]; j=j[j.index>=pd.Period("2016-01","M")]
    print(f"  건설지출 YoY → 한국 {nm} 수출 YoY: 동시 {j.p.corr(j.e):+.2f}, 건설이 k개월 앞섬 "+" ".join(f"k={k}:{j.p.corr(j.e.shift(-k)):+.2f}" for k in (3,6,9,12))+f" | 수출→건설 k=6 {j.e.corr(j.p.shift(-6)):+.2f} | 최근 수출 YoY {ey.iloc[-1]*100:+.0f}%")
j=pd.concat([pwy,r12,mon(us('GEV'))*0],axis=1).iloc[:,:2].dropna(); j.columns=["p","r12"]; j=j[j.index>=pd.Period("2016-01","M")]
print(f"  건설지출 YoY → 한국 전력주(5종, 시장제거) 12개월 누적 수익: 동시 {j.p.corr(j.r12):+.2f}, 건설이 k개월 앞섬 "+" ".join(f"k={k}:{j.p.corr(j.r12.shift(-k)):+.2f}" for k in (3,6,9,12))+f" | 주가→건설 k=6 {j.r12.corr(j.p.shift(-6)):+.2f}")
for lab,a in [("2016~","2016-01"),("2022~","2022-01")]:
    jj=j[a:]; print(f"    {lab}: 동시 {jj.p.corr(jj.r12):+.2f}, k=6 {jj.p.corr(jj.r12.shift(-6)):+.2f}, n={len(jj)}")
