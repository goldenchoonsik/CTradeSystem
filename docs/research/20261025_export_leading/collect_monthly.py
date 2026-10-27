"""관세청 품목별 국가별 수출입실적(GW) Itemtrade — 전국 월별 수출(expDlr, 달러)을 HS 묶음별로 수집.
HS4: 8542(집적회로=반도체 주력)·8541(반도체소자)·8703(승용차)·8708(자동차부품)·8901(선박)·2710(석유제품)·8507(축전지). 원문 XML은 DataVault/staging/customs_export_monthly/raw 에 보존.
"""
import os, re, subprocess, time, urllib.parse, pandas as pd
R="/home/kuzimo/projects/CTradeSystem"; RAW=f"{R}/DataVault/staging/customs_export_monthly/raw"
key=subprocess.run(["bash","-ic","echo -n $OPEN_DATA_KEY"],capture_output=True,text=True).stdout.strip()
HS=["8542","8541","8703","8708","8901","2710","8507","8504","8544","8537"]
rows=[]
for hs in HS:
    for y in range(2012,2027):
        f=f"{RAW}/{hs}_{y}.xml"
        if not os.path.exists(f):
            e=f"{y}09" if y==2026 else f"{y}12"
            for pg in range(1,4):
                u=f"https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList?serviceKey={key}&strtYymm={y}01&endYymm={e}&hsSgn={hs}&numOfRows=5000&pageNo={pg}"
                x=subprocess.run(["curl","-s","-m","90",u],capture_output=True,text=True).stdout
                open(f if pg==1 else f+f".p{pg}","w").write(x)
                if int(re.search(r"<totalCount>(\d+)",x).group(1) if "<totalCount>" in x else 0)<=pg*5000: break
            time.sleep(0.3)
        for fp in [f,f+".p2",f+".p3"]:
            if not os.path.exists(fp): continue
            t=open(fp).read()
            for it in re.findall(r"<item>(.*?)</item>",t,re.S):
                g=lambda k:(re.search(rf"<{k}>(.*?)</{k}>",it) or [0,""])[1]
                if re.fullmatch(r"\d{4}\.\d\d",g("year")): rows.append((hs,g("year"),float(g("expDlr") or 0),float(g("impDlr") or 0)))
        print(hs,y,flush=True) if y in (2012,2026) else None
d=pd.DataFrame(rows,columns=["hs4","ym","exp","imp"]); d=d.groupby(["hs4","ym"],as_index=False).sum()
d["date"]=pd.to_datetime(d.ym.str.replace(".","-")+"-01"); d.to_csv("exports_monthly_hs4.csv",index=False)
p=d.pivot(index="date",columns="hs4",values="exp")/1e9; print(p.tail(8).round(2).to_string()); print(len(d),"행")
