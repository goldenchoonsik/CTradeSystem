import numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
P=pd.read_csv("strategy_B_path.csv",index_col=0,parse_dates=True)
M=pd.read_csv("../20261027_market_funds/monthly_funds_kospi.csv",index_col=0,parse_dates=True); M["r"]=M.신용융자/M.예탁금
SIDE=0.00185
def weights(idx,c,w,lag):
    r=M.r.copy(); r.index=r.index.to_period("M")
    pm=idx.to_period("M"); src=pm-lag
    val=pd.Series([r.get(p,np.nan) for p in src],index=idx).ffill()
    return pd.Series(np.where(val<c,1.0,w),index=idx)
def apply(s,c,w,lag):
    ret=s.pct_change().fillna(0); wt=weights(s.index,c,w,lag)
    cost=wt.diff().abs().fillna(0)*SIDE
    return (1+(wt*ret-cost)).cumprod()
def st(s,a,b):
    s=s[(s.index>=a)&(s.index<=b)]; r=np.log(s).diff().dropna(); yrs=len(r)/252
    return dict(CAGR=(np.exp(r.sum())**(1/yrs)-1)*100,샤프=r.mean()/r.std()*np.sqrt(252),MDD=((s/s.cummax())-1).min()*100,변동성=r.std()*np.sqrt(252)*100)
per={"개발":("2015-01-01","2020-12-31"),"검증":("2021-01-01","2025-12-31"),"사후":("2026-01-01","2026-10-23")}
base=P["hi"]; print("### 선택(개발, w=0.5, 2개월 지연)")
dv={c:st(apply(base,c,0.5,2),*per["개발"]) for c in (0.30,0.33,0.36)}
for c,v in dv.items(): print(c,{k:round(x,2) for k,x in v.items()})
cs=max(dv,key=lambda c:dv[c]["샤프"]); print("선택 c =",cs)
def show(label,s):
    print(f"{label:34s}"+" | ".join(f"{p}: CAGR {st(s,*ab)['CAGR']:5.1f} 샤프 {st(s,*ab)['샤프']:4.2f} MDD {st(s,*ab)['MDD']:6.1f}" for p,ab in per.items()))
print("\n### 고동행 B (선택 c)")
show("기초 단순 보유",base)
for w in (0.5,0.0):
    for lag in (2,1): show(f"c={cs} w={w} 지연{lag}개월",apply(base,cs,w,lag))
print("\n### 컷 민감도(w=0.5, 지연 2): 검증 구간")
for c in (0.28,0.30,0.33,0.36,0.40): show(f"c={c}",apply(base,c,0.5,2))
print("\n### 일반성: 같은 규칙(c=%s, w=0.5, 지연2)"%cs)
for k,n in (("all","상위300 EW"),("코스피","코스피")):
    show(n+" 단순",P[k]); show(n+" +규칙",apply(P[k],cs,0.5,2))
# 현재 상태
cur=M.r.iloc[-1]; print(f"\n최근 월말 비율 {cur:.3f} ({M.index[-1].date()}) → 규칙 c={cs} 기준 {'경고(주식 50%)' if cur>=cs else '정상(100%)'}")
w=weights(base.index,cs,0.5,2); print("2015~2026-10 경고월 비중: %.0f%%"%((w<1).mean()*100))
apply(base,cs,0.5,2).to_csv("overlay_path.csv")

print("\n### 대조군: 신호 없이 고정 비중(평균 비중 동일) vs 규칙, 그리고 월 무작위 비중 5000회")
ret=base.pct_change().fillna(0); wt=weights(base.index,cs,0.5,2)
for lab,(a,b) in per.items():
    m=wt[a:b].mean(); const=(1+ret*m).cumprod()
    print(f"{lab} 평균 비중 {m:.2f}: 고정비중 샤프 {st(const,a,b)['샤프']:.2f} MDD {st(const,a,b)['MDD']:.1f} CAGR {st(const,a,b)['CAGR']:.1f} | 규칙 샤프 {st(apply(base,cs,0.5,2),a,b)['샤프']:.2f} MDD {st(apply(base,cs,0.5,2),a,b)['MDD']:.1f}")
rng=np.random.default_rng(1); months=base.index.to_period("M"); um=months.unique(); a,b=per["검증"]
k=int((wt.groupby(months).first()[um]<1).sum()); sh=[]
mask=pd.Series(months,index=base.index)
wm=wt.groupby(months).first(); sel=[p for p in um if str(p)>="2021-01" and str(p)<="2025-12"]; nwarn=int((wm[sel]<1).sum())
actual=st(apply(base,cs,0.5,2),a,b)["샤프"]
for _ in range(3000):
    pick=set(rng.choice(sel,nwarn,replace=False)); ww=pd.Series([0.5 if p in pick else 1.0 for p in months],index=base.index)
    # 개발 포함 전 기간 대신 검증 기간만 적용
    rr=(1+(ww*ret-ww.diff().abs().fillna(0)*SIDE))[a:b]
    s=(1+(ww*ret-ww.diff().abs().fillna(0)*SIDE)).cumprod(); sh.append(st(s,a,b)["샤프"])
print(f"검증 구간 경고월 {nwarn}/{len(sel)}개월을 무작위로 뽑은 3000회 샤프: 중앙 {np.median(sh):.2f}, 실제 {actual:.2f}보다 큰 비율 {np.mean(np.array(sh)>=actual)*100:.1f}%")
