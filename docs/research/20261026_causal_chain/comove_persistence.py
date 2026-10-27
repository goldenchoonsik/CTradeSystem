"""동행성이 강한 상승 구간에서 상승이 이어지는가. 탐색·기술 통계.
(1) 폭: 사슬 5단계(NVDA·MU·미국장비·한국메모리·한국소부장) 중 50일선 위 개수 → 한국 메모리·소부장·코스피의 이후 20·60거래일 수익(로그, %). 
(2) 동행도: 최근 60일 일수익 상관(미국 사슬 평균 vs 한국 사슬 평균, 미국 d → 한국 다음 세션 정렬) 상위 1/3 vs 하위 1/3, 그리고 상승 폭 5/5 조건과 교차.
비중첩 근사: 20거래일 간격 표본(시작일 3가지 오프셋 평균), 단순 t. 한국 신호 시점 = 한국 종가 기준 같은 날 확정된 정보.
"""
import numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
V="/home/kuzimo/projects/CTradeSystem/DataVault"
def us(t): d=pd.read_csv(f"{V}/raw/yahoo/candle_1d/{t}_1d.csv",parse_dates=["timestamp"]).set_index("timestamp").close; d.index=d.index.normalize(); return d
pr=pd.read_parquet(f"{V}/staging/kr_equity/derived/prices_1d.parquet",columns=["code","date","close"]); pr["date"]=pd.to_datetime(pr.date)
def kr(c): return pr[pr.code==c].set_index("date").close
def idx(cs): x=pd.concat([np.log(kr(c)).diff() for c in cs],axis=1,sort=True).mean(axis=1).fillna(0).cumsum(); return np.exp(x)
i=pd.read_parquet(f"{V}/staging/kr_equity/derived/index_1d.parquet",columns=["code","date","close"]); i["date"]=pd.to_datetime(i.date); kospi=i[i.code=="U001"].set_index("date").close
mem=idx(["A000660","A005930"]); sobu=idx(["A042700","A240810","A036930","A039030","A319660","A095610","A084370","A058470","A007660"])
usb=np.exp(pd.concat([np.log(us(t)).diff() for t in ["AMAT","LRCX","KLAC","ASML"]],axis=1,sort=True).mean(axis=1).fillna(0).cumsum())
kd=kospi.index
def onkr(s): return s.reindex(s.index.union(kd)).ffill().reindex(kd)   # 미국 d 종가는 한국 d+1 세션 전 확정 → 한국 d+1 날짜에 d 값(직전 미국 종가)
S={"NVDA":onkr(us("NVDA").shift(1).reindex(us("NVDA").index)) if False else None}
def us_prior(s): # 한국 날짜 t 에서 확정된 최신 미국 종가(t 이전 미국 거래일)
    s2=s.copy(); s2.index=s2.index+pd.Timedelta(hours=24)  # 미국 d 종가가 한국 d+1 에 반영
    return s2.reindex(s2.index.union(kd)).ffill().reindex(kd)
C={"NVDA":us_prior(us("NVDA")),"MU":us_prior(us("MU")),"미국장비":us_prior(usb),"한국메모리":mem.reindex(kd).ffill(),"한국소부장":sobu.reindex(kd).ffill()}
up=pd.DataFrame({k:(v>v.rolling(50).mean()).astype(float) for k,v in C.items()}); breadth=up.sum(axis=1)
usr=np.log(pd.concat([C["NVDA"],C["MU"],C["미국장비"]],axis=1)).diff().mean(axis=1); krr=np.log(pd.concat([C["한국메모리"],C["한국소부장"]],axis=1)).diff().mean(axis=1)
corr=usr.rolling(60).corr(krr)
def fwd(s,h): s=s.reindex(kd); return np.log(s.shift(-h)/s)*100
T={"한국 메모리":mem.reindex(kd),"한국 소부장":sobu.reindex(kd),"코스피":kospi.reindex(kd)}
df=pd.DataFrame({"breadth":breadth,"corr":corr}); 
for n,s in T.items():
    for h in (20,60): df[f"{n}{h}"]=fwd(s,h)
df=df[df.index>="2016-01-01"].dropna()
def sample(d,step=20):
    out=[]
    for o in (0,7,14): out.append(d.iloc[o::step])
    return out
def stat(d,col):
    ms=[x[col].mean() for x in sample(d)]; n=min(len(x) for x in sample(d)); sd=np.mean([x[col].std() for x in sample(d)])
    return np.mean(ms), np.mean(ms)/(sd/np.sqrt(n)) if sd>0 else np.nan, n
print("=== (1) 폭: 사슬 5단계 중 50일선 위 개수 → 이후 수익(%, 20거래일 간격 비중첩 근사) ===")
cols=[f"{n}{h}" for n in T for h in (20,60)]
print("폭     표본일수 | "+" | ".join(cols))
for b in [0,1,2,3,4,5]:
    d=df[df.breadth==b]
    if len(d)<30: print(f" {b}개  {len(d):4d}일 (표본 적음)"); continue
    print(f" {b}개  {len(d):4d}일 | "+" | ".join(f"{stat(d,c)[0]:+5.1f}" for c in cols))
print(" 전체  %4d일 | "%len(df)+" | ".join(f"{stat(df,c)[0]:+5.1f}" for c in cols))
print("\n폭 5개(전부 상승) vs 나머지 — 차이(t):")
for c in cols:
    a=df[df.breadth==5]; r=df[df.breadth<5]; da=stat(a,c); dr=stat(r,c)
    print(f"  {c}: 5/5 {da[0]:+.1f}% (n≈{da[2]}) vs 나머지 {dr[0]:+.1f}% → 차 {da[0]-dr[0]:+.1f}%p")
print("\n=== (2) 동행도: 최근 60일 상관(미국 사슬↔한국 사슬) 3분위 × 상승 폭 ===")
q1,q2=df["corr"].quantile([1/3,2/3])
df["cq"]=np.where(df["corr"]>=q2,"동행↑",np.where(df["corr"]<=q1,"동행↓","중간"))
print(f"상관 3분위 경계 {q1:.2f}/{q2:.2f}; 현재 상관 {corr.iloc[-1]:.2f}, 현재 폭 {int(breadth.iloc[-1])}/5")
for cq in ["동행↑","중간","동행↓"]:
    for lab,msk in [("전체",df.breadth>=0),("폭≥4",df.breadth>=4),("폭≤2",df.breadth<=2)]:
        d=df[(df.cq==cq)&msk]
        if len(d)<30: continue
        print(f"  {cq} {lab:5s} {len(d):4d}일 | "+" | ".join(f"{stat(d,c)[0]:+5.1f}" for c in cols))
print("\n열 순서: "+" | ".join(cols))

print("\n=== 검증: '동행↑ & 폭≥4' 상태의 연도별 분포·최악 ===")
S_=df[(df.cq=="동행↑")&(df.breadth>=4)]
rows=[]
for y,g in S_.groupby(S_.index.year):
    a=df[df.index.year==y]
    rows.append((y,len(g),len(a),g["한국 메모리60"].mean(),a["한국 메모리60"].mean(),g["한국 소부장60"].mean(),g["코스피60"].mean(),g["한국 메모리60"].min()))
print(pd.DataFrame(rows,columns=["연도","상태일수","그해일수","메모리60(상태)","메모리60(그해 전체)","소부장60(상태)","코스피60(상태)","메모리60 최악"]).round(1).to_string(index=False))
# 연도 블록 t: 연도별 (상태 평균 − 그해 전체 평균)
dd=[r[3]-r[4] for r in rows if r[1]>=10]; print(f"\n연도 블록 차이(상태−그해 전체, 메모리60) 평균 {np.mean(dd):+.1f}%p, 양수 {sum(x>0 for x in dd)}/{len(dd)}년, t {np.mean(dd)/(np.std(dd,ddof=1)/np.sqrt(len(dd))):+.1f}")
print(f"상태 구간 메모리60 분포: 하위10% {S_['한국 메모리60'].quantile(.1):+.1f}% / 중앙 {S_['한국 메모리60'].median():+.1f}% / 상위10% {S_['한국 메모리60'].quantile(.9):+.1f}%; 60일 수익<0 비율 {(S_['한국 메모리60']<0).mean():.0%} (전체 {(df['한국 메모리60']<0).mean():.0%})")
print("현재(최신 일자) 상태:", df.index[-1].date() if len(df) else "", "— 데이터상 최신 신호일", corr.dropna().index[-1].date(), f"폭 {int(breadth.iloc[-1])}/5, 상관 {corr.iloc[-1]:.2f}")
