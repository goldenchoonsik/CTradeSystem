import json,subprocess,urllib.request,numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
sh=lambda v:subprocess.run(["bash","-ic",f"echo -n ${v}"],capture_output=True,text=True).stdout.strip()
OK,EK=sh("OPEN_DATA_KEY"),sh("ECOS_KEY")
B="https://apis.data.go.kr/1160100/service/GetKofiaStatisticsInfoService/getGrantingOfCreditBalanceInfo"
it=[]
for p in range(1,4):
    d=json.load(urllib.request.urlopen(f"{B}?serviceKey={OK}&numOfRows=500&pageNo={p}&resultType=json"))
    it+=d["response"]["body"]["items"]["item"]
C=pd.DataFrame(it); C["date"]=pd.to_datetime(C.basDt); C=C.set_index("date").sort_index().drop(columns="basDt").astype(float)/1e8  # 억원
C=C.rename(columns={"crdTrFingWhl":"융자_전체","crdTrFingScrs":"융자_코스피","crdTrFingKosdaq":"융자_코스닥","crdTrLndrScrs":"대주_코스피","dpsgScrtMogFing":"예탁증권담보융자"})
C=C[~C.index.duplicated()]
d=json.load(urllib.request.urlopen(f"https://ecos.bok.or.kr/api/StatisticSearch/{EK}/json/kr/1/10000/802Y001/D/20211001/20261026/0001000"))
ko=pd.Series({pd.Timestamp(x["TIME"]):float(x["DATA_VALUE"]) for x in d["StatisticSearch"]["row"]}).sort_index()
D=C.join(ko.rename("KOSPI"),how="inner"); D.to_csv("daily_credit_kospi.csv",encoding="utf-8-sig")
print(C.index.min().date(),C.index.max().date(),len(C),len(D))
r=np.log(D.KOSPI).diff()*100
chg=lambda c:np.log(D[c]).diff()*100
cols=["융자_코스피","융자_코스닥","융자_전체","대주_코스피","예탁증권담보융자"]
print("\n## 일 변화율 vs KOSPI 일수익률, 시차 k (k>0 자금이 선행: 자금 t ↔ 수익 t+k)")
print("k:",list(range(-5,6)))
for c in cols: print(f"{c:10s}",[round(float(chg(c).corr(r.shift(-k))),2) for k in range(-5,6)])
# 수준 비율: 융자/KOSPI 지수 시총 대용 불가 → 융자 수준 자체의 20일 변화 → 이후 20일 수익
print("\n## 20일 융자 증가율 vs 이후 5/20/60일 KOSPI 수익 (겹침, 참고)")
for c in ["융자_코스피","융자_전체"]:
    ch=np.log(D[c]).diff(20); print(c,[round(float(ch.corr(np.log(D.KOSPI).shift(-n)-np.log(D.KOSPI))),2) for n in (5,20,60)])
# 융자 증가가 지수 증가 이상인가: 지수 대비 초과 증가(잔차) → 이후
ex=np.log(D.융자_코스피).diff(20)-np.log(D.KOSPI).diff(20)*np.polyfit(np.log(D.KOSPI).diff(20).dropna(),np.log(D.융자_코스피).diff(20).dropna(),1)[0]
print("지수 설명분 제거한 융자 20일 증가 vs 이후 5/20/60일",[round(float(ex.corr(np.log(D.KOSPI).shift(-n)-np.log(D.KOSPI))),2) for n in (5,20,60)])
# 하루 급감(반대매매 추정): 융자 일 변화 하위 5% 다음 날/5일 수익
dc=chg("융자_코스피"); q=dc<=dc.quantile(.05)
print("\n융자 하루 급감(하위5%) 평균 변화 %.2f%% · 당일 지수 %.2f%% · 다음날 %.2f%% · 5일 %.2f%% (무조건 다음날 %.2f%%, 5일 %.2f%%)"%(dc[q].mean(),r[q].mean(),r.shift(-1)[q].mean(),(np.log(D.KOSPI).shift(-5)-np.log(D.KOSPI))[q].mean()*100,r.mean(),(np.log(D.KOSPI).shift(-5)-np.log(D.KOSPI)).mean()*100))
print("급감일 수",int(q.sum()))
