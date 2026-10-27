import json,subprocess,urllib.request,numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
K=subprocess.run(["bash","-ic","echo -n $ECOS_KEY"],capture_output=True,text=True).stdout.strip()
nm={"S23A":"예탁금","S23B":"파생예수금","S23C":"RP","S23D":"미수금","S23E":"신용융자","S23F":"신용대주"}
f={}
for c,n in nm.items():
    d=json.load(urllib.request.urlopen(f"https://ecos.bok.or.kr/api/StatisticSearch/{K}/json/kr/1/1000/901Y056/M/199806/202609/{c}"))
    r=d["StatisticSearch"]["row"]; f[n]=pd.Series({pd.Timestamp(x["TIME"]+"01")+pd.offsets.MonthEnd(0):float(x["DATA_VALUE"]) for x in r})
F=pd.DataFrame(f)/1e6   # 백만원
F=F.where(F>0)
print(F.notna().idxmax().to_string()," ← 항목별 첫 유효월")
d=json.load(urllib.request.urlopen(f"https://ecos.bok.or.kr/api/StatisticSearch/{K}/json/kr/1/10000/802Y001/D/19980601/20261026/0001000"))
ko=pd.Series({pd.Timestamp(x["TIME"]):float(x["DATA_VALUE"]) for x in d["StatisticSearch"]["row"]}).sort_index()
km=ko.resample("ME").last()
print("KOSPI",ko.index.min().date(),ko.index.max().date())
F=F.join(km.rename("KOSPI"),how="inner"); F.to_csv("monthly_funds_kospi.csv",encoding="utf-8-sig")
F["신용/예탁금"]=F["신용융자"]/F["예탁금"]; F["미수/예탁금"]=F["미수금"]/F["예탁금"]
r=np.log(F.KOSPI).diff()
cols=["예탁금","미수금","신용융자","신용대주","RP","파생예수금","신용/예탁금","미수/예탁금"]
print("기간",F.index.min().date(),F.index.max().date(),len(F))
def block(title,fn):
    print("\n##",title); return fn
# 1 수준 상관(참고)
print("\n## 수준 로그 상관(참고, 추세 때문에 과대)")
for c in cols[:6]: print(c, round(np.corrcoef(np.log(F[c]),np.log(F.KOSPI))[0,1],2))
# 2 변화율 동행
d={c:np.log(F[c]).diff() for c in cols[:6]}; d["신용/예탁금"]=F["신용/예탁금"].diff(); d["미수/예탁금"]=F["미수/예탁금"].diff()
print("\n## 월 변화율 동행 상관(KOSPI 월수익률) 전체 / 2010~")
for c in cols: print(c, round(d[c].corr(r),2), round(d[c]["2010":].corr(r["2010":]),2))
for lab,sl in (("전체",slice(None)),("2010~",slice("2010",None))):
    print(f"\n## [{lab}] 시차: corr(자금변화 t, KOSPI수익 t+k) k=-2..+6 (k>0 자금이 선행)")
    for c in cols: print(f"{c:8s}",[round(float(d[c][sl].corr(r.shift(-k)[sl])),2) for k in range(-2,7)])
    print(f"## [{lab}] 12개월 자금변화 vs 이후 3/6/12개월 KOSPI 수익 상관(겹침)")
    for c in cols:
        ch=(np.log(F[c]).diff(12) if c in cols[:6] else F[c].diff(12))
        print(f"{c:8s}",[round(float(ch[sl].corr(((np.log(F.KOSPI).shift(-n)-np.log(F.KOSPI)))[sl])),2) for n in (3,6,12)])
# 5 비율 수준 → 이후 12개월
print("\n## 신용/예탁금·미수/예탁금 수준(5분위) → 이후 12개월 KOSPI 수익 평균(%)")
fw=(np.log(F.KOSPI).shift(-12)-np.log(F.KOSPI))*100
for c in ["신용/예탁금","미수/예탁금"]:
    q=pd.qcut(F[c],5,labels=False); print(c, fw.groupby(q).mean().round(1).tolist(), "현재",round(F[c].iloc[-1],3), "현재 분위", int(q.iloc[-1])+1)
# 6 주식 비중: 예탁금/KOSPI 수준 정규화
print("\n## 최근 12개월"); print(F[["예탁금","미수금","신용융자","KOSPI"]].tail(12).round(1))
