"""iShares 미국 상장 주식형 펀드(순자산>$0.5bn) 전수에서 한국 노출 펀드 선별 + 일별 NAV·발행주식수 이력 저장.
선별 = Holdings 시트 Location에 Korea 포함 시장가치 합 > $50M.  결과 etf/scan_korea_funds.csv, 이력 etf/hist/{ticker}_hist_shares.csv
"""
import json, re, time, subprocess, os, pandas as pd
R="/home/kuzimo/projects/CTradeSystem/docs/research/20261025_foreign_channels/etf"
scr=json.load(open("/tmp/claude-1000/-home-kuzimo-projects-CTradeSystem/d094c582-b7b0-44c9-8c91-b513d85505c6/scratchpad/scr.json"))
M={m:i+1 for i,m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
def sheet(x,name):
    s=x.find(f'Name="{name}"')
    if s<0: return None
    e=min(i for i in (x.find("</Worksheet>",s),x.find("</ss:Worksheet>",s)) if i>0); return x[s:e]
def rows(h):
    for r in re.finditer(r"<(?:ss:)?Row[^>]*>(.*?)</(?:ss:)?Row>",h,re.S):
        yield [re.sub(r"<[^>]+>","",m.group(1)) for m in re.finditer(r"<(?:ss:)?Data[^>]*>(.*?)</(?:ss:)?Data>",r.group(1),re.S)]
def na_(r):
    v=r.get("totalNetAssets"); v=v.get("r") if isinstance(v,dict) else v
    return v or 0
cand=[(p,r["localExchangeTicker"],r["fundName"],na_(r))
      for p,r in scr.items() if r.get("aladdinAssetClass")=="Equity" and r.get("localExchangeTicker") and na_(r)>5e8 and not re.fullmatch(r"[A-Z]{5}",r["localExchangeTicker"] or "")]
print(len(cand),"후보")
out=[]
for p,t,nm,na in sorted(cand,key=lambda x:-x[3]):
    f=f"{R}/raw_xls/{t}.xls"
    if not os.path.exists(f):
        subprocess.run(["curl","-s","-m","120","-A","Mozilla/5.0","-o",f,f"https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares&locale=en_US&portfolioId={p}&component=fundDownload&userType=individual"]); time.sleep(0.5)
    try: x=open(f,encoding="utf-8",errors="ignore").read()
    except Exception: continue
    h=sheet(x,"Holdings")
    if not h: out.append((t,nm,na,None,None)); os.remove(f); continue
    hdr=None; kr=0.0; tot=0.0
    for r in rows(h):
        if hdr is None:
            if "Location" in r and "Market Value" in r: hdr=r
            continue
        if len(r)<len(hdr): continue
        d=dict(zip(hdr,r))
        try: mv=float(d["Market Value"].replace(",",""))
        except: continue
        if d.get("Asset Class")=="Equity":
            tot+=mv
            if "Korea" in d.get("Location",""): kr+=mv
    out.append((t,nm,na,kr,tot))
    if kr>5e7:
        hs=sheet(x,"Historical"); recs=[]
        for r in rows(hs):
            m=re.fullmatch(r"([A-Z][a-z]{2}) (\d\d), (\d{4})",r[0]) if r else None
            if m and len(r)==5 and r[3] not in ("--","0"):
                recs.append((int(m[3])*10000+M[m[1]]*100+int(m[2]),float(r[1]),float(r[3])))
        pd.DataFrame(recs,columns=["date","nav","shares"]).query("date>=20111201").sort_values("date").to_csv(f"{R}/hist/{t}_hist_shares.csv",index=False)
    else: os.remove(f)
    print(t,nm[:48],"net $%.1fbn"%(na/1e9),"Korea $%.2fbn (%.1f%%)"%(kr/1e9,100*kr/tot if tot else 0),flush=True)
df=pd.DataFrame(out,columns=["ticker","name","net_assets","korea_mv","equity_mv"]); df["korea_pct"]=df.korea_mv/df.equity_mv
df.sort_values("korea_mv",ascending=False).to_csv(f"{R}/scan_korea_funds.csv",index=False)
