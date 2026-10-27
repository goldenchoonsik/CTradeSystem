"""상위 계열의 추세 전환 신호일 → 하위 계열 이후 초과수익(대 코스피) 이벤트 스터디. 탐색·기술 통계.
전환 정의 3종: (D) 60일 고점 대비 −10% 이탈(하락 전환), (U) 60일 저점 대비 +10% 반등(상승 전환), (M) 종가가 50일선을 하향/상향 돌파. 같은 방향 이벤트는 20거래일 쿨다운.
상위: NVDA·MU·미국 장비주 바구니·한국 메모리. 하위: 한국 메모리·소부장. 수익은 신호 미국 d 종가 이후 한국 다음 세션부터 h거래일 초과수익(코스피 차감), 한국 계열이 상위면 d 다음 세션부터.
무조건 평균(전체 날 h일 초과수익 평균) 대비 차이와 이벤트 수, 단순 t(이벤트 간 20일 쿨다운으로 대체로 비중첩).
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
UP={"NVDA":us("NVDA"),"MU":us("MU"),"미국 장비주":usb,"한국 메모리":mem}
DN={"한국 메모리":mem,"한국 소부장":sobu}
def events(s,kind,start="2016-01-01"):
    s=s[s.index>="2013-01-01"]; hi=s.rolling(60).max(); lo=s.rolling(60).min(); ma=s.rolling(50).mean()
    if kind=="D": sig=(s<hi*0.90)&(s.shift(1)>=hi.shift(1)*0.90)
    elif kind=="U": sig=(s>lo*1.10)&(s.shift(1)<=lo.shift(1)*1.10)
    elif kind=="Md": sig=(s<ma)&(s.shift(1)>=ma.shift(1))
    else: sig=(s>ma)&(s.shift(1)<=ma.shift(1))
    d=list(s.index[sig&(s.index>=start)]); out=[]
    for x in d:
        if not out or (s.index.get_loc(x)-s.index.get_loc(out[-1]))>=20: out.append(x)
    return pd.DatetimeIndex(out)
kd=kospi.index
def fwd(dn,start_after,h):
    # 한국 다음 세션 종가부터가 아니라 '다음 세션 시가 직전 종가' 기준: d 다음 한국 세션의 전일 종가 → 그 세션+h−1 종가 (미국 신호가 이미 한국 첫 세션에 반영 가능)
    p=np.searchsorted(kd.values,start_after.values,side="right"); ok=p+h<len(kd); p=p[ok]; sa=start_after[ok]
    dnr=dn.reindex(kd).ffill(); kr_=kospi.reindex(kd)
    a=dnr.values[p-1]; b=dnr.values[p+h-1]; ka=kr_.values[p-1]; kb_=kr_.values[p+h-1]
    return pd.Series(np.log(b/a)-np.log(kb_/ka),index=sa)
def base(dn,h,start="2016-01-01"):
    kk=kd[kd>=pd.Timestamp(start)]; return fwd(dn,pd.DatetimeIndex(kk[:-h-1])-pd.Timedelta(days=1),h)
H=[1,5,20,60]
LAB={"D":"하락전환(−10% 이탈)","U":"상승전환(+10% 반등)","Md":"50일선 하향","Mu":"50일선 상향"}
for un,u in UP.items():
    for dnn,dn in DN.items():
        if un==dnn: continue
        print(f"\n### {un} → {dnn}   (초과수익 %, 이벤트 평균 − 무조건 평균, [이벤트수])")
        for k in ["D","U","Md","Mu"]:
            ev=events(u,k); row=[]
            for h in H:
                f=fwd(dn,ev,h); b=base(dn,h)
                if len(f)<8: row.append("—"); continue
                diff=f.mean()-b.mean(); t=diff/(f.std()/np.sqrt(len(f)))
                row.append(f"{diff*100:+.1f}(t{t:+.1f})")
            print(f"  {LAB[k]:14s} n={len(ev):3d} h=1/5/20/60일: "+"  ".join(row))
