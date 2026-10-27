"""외국인 수급 상태 한 장 — 예측용이 아니라 '지금 어떤 상태인가' 읽기용(외국인 순매수의 미래수익 예측력은 0으로 확인됨).
항목: (1) 외국인 5·20·60일 순매수(조원)와 2016~ 분포 내 백분위 (2) 같은 기간 상대방(개인·기관·금융투자·기타법인) (3) 패시브·ETF 한국 귀속 순설정 20·60일
(4) 비거래성 점검: 국제수지 잔차 최근값 (5) 원/달러 20일 변화.   사용: .venv/bin/python foreign_state.py [--refresh]  (--refresh = iShares 주요 5개 이력 재수신, 공개 GET)
"""
import re, subprocess, sys, pandas as pd, numpy as np
R="/home/kuzimo/projects/CTradeSystem"; S=f"{R}/DataVault/staging"; D=f"{R}/docs/research/20261025_foreign_channels"; E=f"{D}/etf"
FUNDS={"IEMG":("244050",0.182),"EWY":("239681",1.0),"EMXC":("288504",0.235),"EEM":("239637",0.187),"IXUS":("244048",0.057)}
if "--refresh" in sys.argv:
    for t,(p,_) in FUNDS.items():
        subprocess.run(["curl","-s","-m","120","-A","Mozilla/5.0","-o",f"{E}/raw_xls/{t}.xls",f"https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares&locale=en_US&portfolioId={p}&component=fundDownload&userType=individual"])
    subprocess.run([f"{R}/.venv/bin/python",f"{D}/etf_xls_to_hist.py",*FUNDS])
k=pd.read_csv(f"{S}/kr_investor_daily/KSP_0001_daily.csv",dtype={"stck_bsop_date":str}); k["date"]=pd.to_datetime(k.stck_bsop_date); k=k.set_index("date").sort_index()
cols={"외국인":"frgn_ntby_tr_pbmn","개인":"prsn_ntby_tr_pbmn","기관":"orgn_ntby_tr_pbmn","금융투자":"scrt_ntby_tr_pbmn","기타법인":"etc_corp_ntby_tr_pbmn"}
f={n:k[c]/1e6 for n,c in cols.items()}   # 조원
last=k.index[-1]; print(f"기준일 {last.date()} (코스피, 조원)")
print("\n[1·2] 기간별 순매수 합 / 외국인 백분위(2016~ 같은 길이 합 분포)")
rows=[]
for w in [5,20,60]:
    fs=f["외국인"].rolling(w).sum(); hist=fs["2016":].dropna(); pct=(hist<fs.iloc[-1]).mean()*100
    rows.append([f"{w}일",*[round(f[n].tail(w).sum(),1) for n in cols],f"{pct:.0f}%"])
print(pd.DataFrame(rows,columns=["기간",*cols,"외국인 백분위"]).to_string(index=False))
print("\n[3] 패시브·ETF 한국 귀속 순설정(십억달러, 현재 비중 상수)")
fl={}
for t,(_,w) in FUNDS.items():
    e=pd.read_csv(f"{E}/hist/{t}_hist_shares.csv"); e=e[e.shares>0]; e["date"]=pd.to_datetime(e.date.astype(str)); e=e.set_index("date")
    fl[t]=(e.shares.diff()*e.nav/1e9*w).dropna()
F=pd.DataFrame(fl).fillna(0); end=F.index[-1]; print(f"  ETF 이력 끝 {end.date()}")
for w in [20,60]: print(f"  최근 {w}일: 합 {F.tail(w).sum().sum():+.2f}  ("+", ".join(f"{t} {F[t].tail(w).sum():+.2f}" for t in F)+")")
m=F.sum(axis=1).resample("ME").sum(); print("  최근 6개월(월):", ", ".join(f"{d:%y-%m} {v:+.1f}" for d,v in m.tail(6).items()))
print("\n[4] 비거래성(신주·IPO·DR) 점검: 국제수지(ECOS 월) − (코스피+코스닥 외국인) 최근 잔차 — recon_out.txt 참조 (BoP 2026-08까지)")
txt=open(f"{D}/recon_out.txt",encoding="utf-8").read().split("=== 잔차 양")[0].splitlines(); print("  월      BoP   KRX합  잔차(십억달러)"); print("\n".join("  "+l for l in [x for x in txt if x.startswith("2026-0")][-4:]))
fx=pd.read_csv(f"{S}/global_macro/series/kr_usdkrw.csv",parse_dates=["date"]).set_index("date").value
print(f"\n[5] 원/달러 {fx.iloc[-1]:.0f} ({fx.index[-1].date()}), 20일 변화 {100*(fx.iloc[-1]/fx.iloc[-21]-1):+.1f}%")
print("\n[읽는 법] 외국인 백분위는 '평소 대비 얼마나 극단인가'만 말한다. 상대방 열에서 기타법인이 크면 자사주 취득, 개인·금융투자가 크면 매도 흡수. 패시브 순설정은 외국인 매도와 같은 방향이 아니라면 매도 주체는 직접 운용 자금.")
