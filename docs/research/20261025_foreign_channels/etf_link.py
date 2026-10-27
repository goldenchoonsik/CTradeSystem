"""EWY 일별 순설정(Δ발행주식수×NAV) vs KRX 외국인 순매수 연결. 탐색·기술 통계.
EWY 이력: iShares 'Data Download' 엑셀 Historical 시트(일별 NAV·발행주식수). 미국 일자 t의 설정은 한국 t+1일 장에서 기초자산이 거래되므로 시차 -2..+3 시험.
"""
import numpy as np, pandas as pd
R="/home/kuzimo/projects/CTradeSystem"; S=f"{R}/DataVault/staging"
e=pd.read_csv(f"{R}/docs/research/20261025_foreign_channels/etf/hist/EWY_hist_shares.csv")
e["date"]=pd.to_datetime(e.date.astype(str)); e=e.set_index("date")
e["dsh"]=e.shares.diff(); e["flow_usd"]=e.dsh*e.nav/1e9   # 십억달러
e=e.dropna()
k=pd.read_csv(f"{S}/kr_investor_daily/KSP_0001_daily.csv",dtype={"stck_bsop_date":str}); k["date"]=pd.to_datetime(k.stck_bsop_date); k=k.set_index("date").sort_index()
fx=pd.read_csv(f"{S}/fx_usdkrw/usdkrw_daily.csv",parse_dates=["date"]).set_index("date").usdkrw
fx=fx.reindex(fx.index.union(k.index)).ffill().reindex(k.index)
kr=(k.frgn_ntby_tr_pbmn/1e6*1e12/fx/1e9).rename("krx")   # 십억달러
print("EWY 일별 이력",e.index[0].date(),"~",e.index[-1].date(),len(e),"일; 변화일",int((e.dsh!=0).sum()))
print("누적 순설정 $%.1fbn (발행주식 %.0f→%.0f백만)"%(e.flow_usd.sum(),e.shares.iloc[0]/1e6,e.shares.iloc[-1]/1e6))
yy=pd.DataFrame({"EWY":e.flow_usd.groupby(e.index.year).sum(),"KRX외국인":kr.groupby(kr.index.year).sum()}).dropna()
yy["EWY/KRX 매수규모"]=np.nan
print("\n=== 연도별 (십억달러) ===");print(yy.round(2).to_string())
print("\n=== 일별: EWY 순설정(미국 t) vs KRX 외국인 순매수(한국 t+lag) ===")
d=pd.DataFrame({"ewy":e.flow_usd}).join(kr,how="outer")
# 한국 거래일 축으로: 미국 설정을 직전 한국일이 아니라 일자 그대로 달력 매핑 후 lag
cal=pd.date_range(min(d.index),max(d.index)); 
ewy=e.flow_usd.reindex(cal).fillna(0); krx=kr.reindex(cal).fillna(0)
for lag in [-2,-1,0,1,2,3]:
    print(f"  lag {lag:+d}(KRX가 {lag}일 뒤): 상관 {ewy.corr(krx.shift(-lag)):+.3f}",end="")
    for lab,sl in [("~2019",slice(None,'2019')),("2020~",slice('2020',None))]:
        a=ewy[sl]; b=krx.shift(-lag)[sl]; print(f" | {lab} {a.corr(b):+.3f}",end="")
    print()
print("\n=== 주간·월간 합 상관 ===")
for lab,f in [("주","W"),("월","ME")]:
    a=ewy.resample(f).sum(); b=krx.resample(f).sum()
    print(f"  {lab}: 동기 {a.corr(b):+.3f} | EWY→다음 {lab} KRX {a.corr(b.shift(-1)):+.3f} | KRX→다음 {lab} EWY {b.corr(a.shift(-1)):+.3f} (n={len(a)})")
m=pd.DataFrame({"ewy":ewy.resample("ME").sum(),"krx":krx.resample("ME").sum()})
b=np.polyfit(m.ewy,m.krx,1); print(f"  월 회귀 KRX = {b[0]:.1f}×EWY {b[1]:+.2f}")
print("\n=== EWY 설정 규모 / KRX 외국인 총 매매 방향 ===")
print(f"  |EWY 월 순설정| 평균 ${m.ewy.abs().mean():.2f}bn vs |KRX 외국인 월 순매수| ${m.krx.abs().mean():.2f}bn → {m.ewy.abs().mean()/m.krx.abs().mean():.1%}")
print("\n=== 2026 월별 (십억달러) ===");print(m.loc["2025-10":].round(2).to_string())
big=m.nsmallest(8,"krx"); print("\nKRX 외국인 대량 순매도 월 vs EWY");print(big.round(2).to_string())
