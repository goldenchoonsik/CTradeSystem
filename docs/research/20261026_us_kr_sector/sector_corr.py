"""미국 업종 ETF ↔ 한국 업종(KRX 지수) 상관·선후. 탐색·기술 통계.
시차 정렬: 미국 거래일 d 종가(한국 d+1 새벽) → 한국 d 다음 거래일 수익에 반영(미국→한국, US_d ~ KR_next). 한국 d 종가(15:30) 뒤 미국 d 장 → (한국→미국, KR_d ~ US_d).
KRX 업종 코드 이름은 표준 배열 가정, 삼성전자·하이닉스와의 상관으로 U013(전기전자) 확인.
"""
import numpy as np, pandas as pd
R="/home/kuzimo/projects/CTradeSystem/DataVault"
def us(t):
    d=pd.read_csv(f"{R}/raw/yahoo/candle_1d/{t}_1d.csv",parse_dates=["timestamp"]).set_index("timestamp").close; d.index=d.index.normalize(); return np.log(d).diff().dropna()
idx=pd.read_parquet(f"{R}/staging/kr_equity/derived/index_1d.parquet",columns=["code","date","close"])
idx["date"]=pd.to_datetime(idx.date); kr=idx.pivot(index="date",columns="code",values="close")
KRN={"U001":"코스피","U005":"음식료","U008":"화학","U009":"의약품","U011":"철강금속","U012":"기계","U013":"전기전자","U015":"운수장비","U016":"유통","U017":"전기가스","U018":"건설","U019":"운수창고","U020":"통신","U021":"금융","U024":"증권","U025":"보험"}
krr=np.log(kr[list(KRN)]).diff().dropna(how="all"); krr.columns=[KRN[c] for c in krr.columns]
pr=pd.read_parquet(f"{R}/staging/kr_equity/derived/prices_1d.parquet",columns=["code","date","close"]); pr["date"]=pd.to_datetime(pr.date)
for c,n in [("A005930","삼성전자"),("A000660","SK하이닉스")]:
    s=pr[pr.code==c].set_index("date").close; krr[n]=np.log(s).diff()
print("지수 매핑 점검: 전기전자↔삼성전자 일상관 %.2f, ↔하이닉스 %.2f"%(krr["전기전자"].corr(krr["삼성전자"]),krr["전기전자"].corr(krr["SK하이닉스"])))
US={"SOXX":"반도체","SMH":"반도체2","XLK":"기술","XLE":"에너지","XLF":"금융","XLV":"헬스","XLI":"산업재","XLB":"소재","XLY":"경기소비","XLP":"필수소비","XLU":"유틸리티","XLC":"통신서비스","ITA":"방산","CARZ":"자동차","LIT":"배터리","XBI":"바이오","KBE":"은행","XME":"금속광산","IYT":"운송","SPY":"S&P500"}
ur=pd.DataFrame({t:us(t) for t in US}); ur=ur[ur.index>="2013-01-01"]
kd=krr.index
# 미국 d → 한국 d 다음 거래일
pos=np.searchsorted(kd.values,ur.index.values,side="right"); ok=pos<len(kd)
m=ur[ok].copy(); m["krdate"]=kd[pos[ok]]
def pairs(sub,label):
    out={}
    A=m.set_index("krdate")  # 미국→한국
    for u in US:
        for k in krr.columns:
            a=A[u].groupby(level=0).sum().reindex(krr.index); b=krr[k]; sel=sub(a.index)
            out[(u,k)]=(a[sel].corr(b[sel]))
    return pd.Series(out)
full=pairs(lambda i:i>=pd.Timestamp("2013-01-01"),"전체"); p1=pairs(lambda i:(i>=pd.Timestamp("2013-01-01"))&(i<pd.Timestamp("2020-01-01")),"~19"); p2=pairs(lambda i:(i>=pd.Timestamp("2020-01-01"))&(i<pd.Timestamp("2024-01-01")),"20~23"); p3=pairs(lambda i:i>=pd.Timestamp("2024-01-01"),"24~")
M=pd.DataFrame({"전체":full,"~2019":p1,"2020~23":p2,"2024~":p3})
print("\n=== 미국 업종(전일 종가) → 한국 업종(다음 거래일) 일 수익 상관: 대응쌍 ===")
PAIR=[("SOXX","전기전자"),("SOXX","SK하이닉스"),("SOXX","삼성전자"),("SMH","전기전자"),("XLK","전기전자"),("XLE","화학"),("XLF","금융"),("KBE","금융"),("XLV","의약품"),("XBI","의약품"),("XLI","기계"),("XLB","철강금속"),("XME","철강금속"),("CARZ","운수장비"),("ITA","운수장비"),("LIT","화학"),("XLU","전기가스"),("XLC","통신"),("IYT","운수창고"),("XLY","유통"),("XLP","음식료"),("SPY","코스피")]
print(M.loc[PAIR].round(2).to_string())
print("\n=== 같은 대응쌍의 한국→미국(한국 d ~ 미국 d 같은 날짜) ===")
def kr2us(sub):
    r={}
    for u,k in PAIR:
        a=ur[u]; b=krr[k]; j=pd.concat([a,b],axis=1,join="inner").dropna(); j=j[sub(j.index)]; r[(u,k)]=j.iloc[:,0].corr(j.iloc[:,1])
    return pd.Series(r)
K=pd.DataFrame({"전체":kr2us(lambda i:i>=pd.Timestamp("2013-01-01")),"2024~":kr2us(lambda i:i>=pd.Timestamp("2024-01-01"))}); print(K.round(2).to_string())
print("\n=== 같은 주(월~금) 수익 상관 (동기 근사), 전체 / 2024~ ===")
wu=ur.resample("W").sum(); wk=krr.resample("W").sum()
rows={}
for u,k in PAIR: 
    j=pd.concat([wu[u],wk[k]],axis=1).dropna(); rows[(u,k)]=(j.iloc[:,0].corr(j.iloc[:,1]), j[j.index>="2024-01-01"].iloc[:,0].corr(j[j.index>="2024-01-01"].iloc[:,1]))
print(pd.DataFrame(rows,index=["전체","2024~"]).T.round(2).to_string())
print("\n=== 각 한국 업종별 가장 상관 높은 미국 업종(전일→다음날, 전체) ===")
best=M["전체"].unstack(0).idxmax(axis=1); bv=M["전체"].unstack(0).max(axis=1); print(pd.DataFrame({"최고 미국업종":best,"상관":bv.round(2)}).to_string())
M.to_csv("us_kr_sector_corr.csv")

print("\n=== 시장 효과 제거 후(미국 업종은 SPY, 한국 업종은 코스피 회귀 잔차) 전일→다음날 상관 ===")
A=m.set_index("krdate")
def resid(y,x):
    j=pd.concat([y,x],axis=1).dropna(); b=np.polyfit(j.iloc[:,1],j.iloc[:,0],1); return j.iloc[:,0]-np.polyval(b,j.iloc[:,1])
rr={}
for u,k in PAIR:
    if u=="SPY": continue
    a=A[u].groupby(level=0).sum().reindex(krr.index); s=A["SPY"].groupby(level=0).sum().reindex(krr.index)
    ra=resid(a,s); rk=resid(krr[k],krr["코스피"]) if k!="코스피" else krr[k]
    j=pd.concat([ra,rk],axis=1).dropna(); j2=j[j.index>="2024-01-01"]
    rr[(u,k)]=(j.iloc[:,0].corr(j.iloc[:,1]),j2.iloc[:,0].corr(j2.iloc[:,1]),M.loc[(u,k),"전체"])
print(pd.DataFrame(rr,index=["잔차 전체","잔차 2024~","원래 전체"]).T.round(2).to_string())
