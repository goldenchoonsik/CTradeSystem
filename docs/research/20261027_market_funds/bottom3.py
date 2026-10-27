import json,subprocess,urllib.request,numpy as np,pandas as pd,warnings; warnings.filterwarnings("ignore")
EK=subprocess.run(["bash","-ic","echo -n $ECOS_KEY"],capture_output=True,text=True).stdout.strip()
d=json.load(urllib.request.urlopen(f"https://ecos.bok.or.kr/api/StatisticSearch/{EK}/json/kr/1/10000/802Y001/D/19950103/20261026/0001000"))
ko=pd.Series({pd.Timestamp(x["TIME"]):float(x["DATA_VALUE"]) for x in d["StatisticSearch"]["row"]}).sort_index()
ko.to_csv("kospi_daily_ecos.csv"); print(ko.index.min().date(),len(ko))
dd=(ko/ko.cummax()-1)*100
# 에피소드: -10% 이하 진입
ep=[];inn=False;pk=ko.index[0]
for t in ko.index:
    if ko[t]>=ko[:t].max():
        if inn: ep.append((pk,tr,t)); inn=False
        pk=t
    if dd[t]<=-10 and not inn: inn=True;tr=t
    if inn and ko[t]<ko[tr]: tr=t
if inn: ep.append((pk,tr,None))
rows=[(p.date(),tr.date(),round(dd[tr]),(tr-p).days,None if rc is None else (rc-tr).days) for p,tr,rc in ep]
E=pd.DataFrame(rows,columns=["고점","바닥","낙폭","하락일(달력)","회복일"]); print(E.to_string()); 
print("\n하락일 분위(10/25/50/75/90):",E["하락일(달력)"].quantile([.1,.25,.5,.75,.9]).round(0).tolist())
for th in (-10,-15,-20,-30):
    s=E[E.낙폭<=th]; print(f"낙폭 {th}% 이하 {len(s)}회: 하락일 중앙 {s['하락일(달력)'].median():.0f} (범위 {s['하락일(달력)'].min()}~{s['하락일(달력)'].max()})")
E.to_csv("episodes_daily.csv",index=False,encoding="utf-8-sig")
