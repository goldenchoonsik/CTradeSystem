"""iShares 4종(EWY·EEM·IEMG·ACWI) 한국 귀속 순설정 합산 vs KRX 외국인 순매수. 탐색·기술 통계.
한국 귀속 = Δ발행주식수×NAV × 한국 비중(2026-10-22 보유 파일 기준 상수: EWY 1.0, EEM 0.187, IEMG 0.182, ACWI 0.021).
과거 한국 비중은 현재(2026 급등 후)보다 낮았으므로 EEM·IEMG·ACWI 과거 한국 귀속은 과대 — 비중 0.6배 민감도 병행.
"""
import numpy as np, pandas as pd
R="/home/kuzimo/projects/CTradeSystem"; S=f"{R}/DataVault/staging"; H=f"{R}/docs/research/20261025_foreign_channels/etf/hist"
W={"EWY":1.0,"EEM":0.187,"IEMG":0.182,"ACWI":0.021}
fl={}
for n in W:
    e=pd.read_csv(f"{H}/{n}_hist_shares.csv"); e["date"]=pd.to_datetime(e.date.astype(str)); e=e.set_index("date")
    fl[n]=(e.shares.diff()*e.nav/1e9).dropna()
F=pd.DataFrame(fl).fillna(0)
k=pd.read_csv(f"{S}/kr_investor_daily/KSP_0001_daily.csv",dtype={"stck_bsop_date":str}); k["date"]=pd.to_datetime(k.stck_bsop_date); k=k.set_index("date").sort_index()
fx=pd.read_csv(f"{S}/fx_usdkrw/usdkrw_daily.csv",parse_dates=["date"]).set_index("date").usdkrw; fx=fx.reindex(fx.index.union(k.index)).ffill().reindex(k.index)
kr=(k.frgn_ntby_tr_pbmn/1e6*1e12/fx/1e9)
cal=pd.date_range("2012-01-01","2026-10-23")
F=F.reindex(cal).fillna(0); krx=kr.reindex(cal).fillna(0)
tot={}
for lab,s in [("w=현재",1.0),("w=0.6배(과거 보수)",0.6)]:
    tot[lab]=sum(F[n]*(W[n]*(1 if n=="EWY" else s)) for n in W)
print("=== 펀드 전체 누적 순설정 (십억달러, 2011-12~2026-10) ===")
print({n:round(F[n].sum(),1) for n in W})
print("한국 귀속 누적(현재 비중):",{n:round((F[n]*W[n]).sum(),1) for n in W}, "합 %.1f"%tot["w=현재"].sum())
yr=pd.DataFrame({**{n:(F[n]*W[n]).groupby(F.index.year).sum() for n in W},"패시브합":tot["w=현재"].groupby(F.index.year).sum(),"KRX외국인":krx.groupby(krx.index.year).sum()})
print("\n=== 연도별 한국 귀속 순설정 vs KRX 외국인 (십억달러) ===");print(yr.loc[2012:].round(2).to_string())
for lab,t in tot.items():
    print(f"\n[{lab}]")
    for fr,nm in [("D","일"),("W","주"),("ME","월")]:
        a=t.resample(fr).sum(); b=krx.resample(fr).sum()
        print(f"  {nm}: 동기 {a.corr(b):+.3f} | 패시브→다음 {nm} KRX {a.corr(b.shift(-1)):+.3f} | KRX→다음 {nm} 패시브 {b.corr(a.shift(-1)):+.3f}")
    m=pd.DataFrame({"p":t.resample("ME").sum(),"k":krx.resample("ME").sum()})
    print(f"  월 |패시브| 평균 {m.p.abs().mean():.2f} vs |KRX| {m.k.abs().mean():.2f} = {m.p.abs().mean()/m.k.abs().mean():.1%}")
    print("  2020~ 월 상관 %+.3f, 2013~19 %+.3f"%(m.loc['2020':].p.corr(m.loc['2020':].k), m.loc[:'2019'].p.corr(m.loc[:'2019'].k)))
m=pd.DataFrame({"패시브(현재비중)":tot["w=현재"].resample("ME").sum(),"KRX":krx.resample("ME").sum()})
print("\n=== 2026 월별 ===");print(m.loc["2026-01":].round(2).to_string())
print("\n=== 2026 합계: 패시브 %.1f vs KRX %.1f 십억달러 ==="%(m.loc["2026","패시브(현재비중)"].sum(),m.loc["2026","KRX"].sum()))
