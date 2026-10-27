"""월 수출(HS4) 전년비·가속 ↔ 대표 종목 초과수익(대 코스피) 선후. 탐색·기술 통계.
신호는 월 t 확정 수출(t 말까지 20일 잠정치로 거의 아는 값). 수익은 t+k월(k=-6..+6) 월 초과수익. 상관(피어슨)과 비중첩 표본이 아니므로 t는 참고만.
"""
import numpy as np, pandas as pd
R="/home/kuzimo/projects/CTradeSystem"; S=f"{R}/DataVault/staging"
d=pd.read_csv("exports_monthly_hs4.csv",parse_dates=["date"],dtype={"hs4":str}); X=d.pivot(index="date",columns="hs4",values="exp"); X.index=X.index.to_period("M")
pr=pd.read_parquet(f"{S}/kr_equity/derived/prices_1d.parquet",columns=["code","date","close"])
kos=pd.read_csv(f"{S}/global_macro/series/kr_kospi.csv",parse_dates=["date"]).set_index("date").value
def mret(s): s=s.dropna(); m=s.groupby(s.index.to_period("M")).last(); return np.log(m).diff()
km=mret(kos)
PAIRS=[("8542","A000660","SK하이닉스"),("8542","A005930","삼성전자"),("8703","A005380","현대차"),("8703","A000270","기아"),("8708","A012330","현대모비스"),("8901","A009540","HD한국조선해양"),("2710","A096770","SK이노베이션"),("8507","A373220","LG에너지솔루션")]
print("HS4=품목 | 종목 | 동시(k=0) | 신호가 앞섬: k=+1,+2,+3 | 주가가 앞섬: k=-1,-2,-3,-6 | 가속(3개월 YoY 변화) k=+1,+2 | n")
for hs,code,nm in PAIRS:
    s=pr[pr.code==code].set_index("date").close
    if s.dropna().empty: print(hs,nm,"가격 없음"); continue
    r=mret(s)-km
    yoy=np.log(X[hs]).diff(12); acc=yoy.rolling(3).mean().diff(3)
    df=pd.DataFrame({"yoy":yoy,"acc":acc,"r":r}).dropna(subset=["yoy"]); df=df[df.index>=pd.Period("2013-01","M")]
    c=lambda sig,k:df[sig].corr(df["r"].shift(-k))
    print(f"{hs} {nm:10s} 동시 {c('yoy',0):+.2f} | +1 {c('yoy',1):+.2f} +2 {c('yoy',2):+.2f} +3 {c('yoy',3):+.2f} | -1 {c('yoy',-1):+.2f} -2 {c('yoy',-2):+.2f} -3 {c('yoy',-3):+.2f} -6 {c('yoy',-6):+.2f} | 가속 +1 {c('acc',1):+.2f} +2 {c('acc',2):+.2f} | n={df.r.notna().sum()}")
# 12개월 롤링 누적수익 vs yoy 수준 (추세 동조)
print("\n=== 12개월 누적 초과수익 ↔ 같은 달 수출 전년비(수준), 수출 YoY가 6개월 뒤 12개월 수익에 선행? ===")
for hs,code,nm in PAIRS[:4]:
    s=pr[pr.code==code].set_index("date").close; r=(mret(s)-km).rolling(12).sum(); yoy=np.log(X[hs]).diff(12)
    df=pd.DataFrame({"yoy":yoy,"r12":r}).dropna(); df=df[df.index>=pd.Period("2013-01","M")]
    print(f"{nm:8s} 동시 {df.yoy.corr(df.r12):+.2f} | 수출→6개월 뒤 12개월수익 {df.yoy.corr(df.r12.shift(-6)):+.2f} | 12개월수익→6개월 뒤 수출 {df.r12.corr(df.yoy.shift(-6)):+.2f}")
print("\n=== 최근 수출 전년비 (%) ===")
y=(X/X.shift(12)-1)*100; print(y.tail(9).round(0).to_string())
