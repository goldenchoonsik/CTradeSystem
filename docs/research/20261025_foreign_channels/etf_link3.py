"""패시브·ETF 25개(iShares 21 + SPDW; DRAM은 별도 추정) 한국 귀속 순설정 합산 vs KRX 외국인 순매수(코스피+코스닥).
한국 귀속 = Δ발행주식수×NAV × 한국 비중(2026-10-22 보유 파일, 상수). 과거 비중 과대 가능 → 0.6배 민감도.
"""
import numpy as np, pandas as pd, os
R="/home/kuzimo/projects/CTradeSystem"; S=f"{R}/DataVault/staging"; E=f"{R}/docs/research/20261025_foreign_channels/etf"
sc=pd.read_csv(f"{E}/scan_korea_funds.csv").dropna(subset=["korea_mv"]); sc=sc[sc.korea_mv>5e7]
W=dict(zip(sc.ticker,sc.korea_pct)); W["SPDW"]=0.0759; W["EWY"]=1.0
fl={}
for n in W:
    e=pd.read_csv(f"{E}/hist/{n}_hist_shares.csv"); e=e[(e.date>=20111201)&(e.shares>0)]; e["date"]=pd.to_datetime(e.date.astype(str)); e=e.set_index("date")
    fl[n]=(e.shares.diff()*e.nav/1e9).dropna()
F=pd.DataFrame(fl).fillna(0); cal=pd.date_range("2012-01-01","2026-10-23"); F=F.reindex(cal).fillna(0)
def krx(f):
    k=pd.read_csv(f"{S}/kr_investor_daily/{f}",dtype={"stck_bsop_date":str}); k["date"]=pd.to_datetime(k.stck_bsop_date); k=k.set_index("date").sort_index()
    fx=pd.read_csv(f"{S}/fx_usdkrw/usdkrw_daily.csv",parse_dates=["date"]).set_index("date").usdkrw; fx=fx.reindex(fx.index.union(k.index)).ffill().reindex(k.index)
    return (k.frgn_ntby_tr_pbmn/1e6*1e12/fx/1e9)
krx_=(krx("KSP_0001_daily.csv")+krx("KSQ_1001_daily.csv").reindex(krx("KSP_0001_daily.csv").index).fillna(0)).reindex(cal).fillna(0)
KR={n:F[n]*W[n] for n in W}; tot=sum(KR.values()); tot6=sum(KR[n]*(1 if n=="EWY" else 0.6) for n in W)
print("펀드 %d개 한국 비중 합 노출(현재) $%.1fbn"%(len(W),0))
print("\n=== 한국 귀속 누적 순설정(십억달러, 2011-12~2026-10) 상위 ===")
cum=pd.Series({n:KR[n].sum() for n in W}).sort_values(ascending=False); print(cum.round(2).head(10).to_dict(),"... 합 %.1f"%cum.sum())
yr=pd.DataFrame({"패시브25":tot.groupby(tot.index.year).sum(),"KRX외국인(KSP+KSQ)":krx_.groupby(krx_.index.year).sum()}); print("\n=== 연도별 ===");print(yr.round(2).to_string())
for lab,t in [("w=현재",tot),("w=0.6배",tot6)]:
    print(f"\n[{lab}]")
    for fr,nm in [("D","일"),("W","주"),("ME","월")]:
        a=t.resample(fr).sum(); b=krx_.resample(fr).sum(); print(f"  {nm}: 동기 {a.corr(b):+.3f} | 패시브→다음 {a.corr(b.shift(-1)):+.3f} | KRX→다음 패시브 {b.corr(a.shift(-1)):+.3f}")
    m=pd.DataFrame({"p":t.resample("ME").sum(),"k":krx_.resample("ME").sum()}); print("  월 상관 2013~19 %+.3f | 2020~ %+.3f | |패시브|/|KRX| %.1f%%"%(m.loc[:'2019'].p.corr(m.loc[:'2019'].k),m.loc['2020':].p.corr(m.loc['2020':].k),100*m.p.abs().mean()/m.k.abs().mean()))
m=pd.DataFrame({"패시브25":tot.resample("ME").sum(),"KRX":krx_.resample("ME").sum()}); print("\n=== 2026 월별 ===");print(m.loc["2026-01":].round(2).to_string())
print("\n2026 합: 패시브25 %.1f vs KRX %.1f ; 4~10월 합: %.1f vs %.1f"%(m.loc["2026","패시브25"].sum(),m.loc["2026","KRX"].sum(),m.loc["2026-04":,"패시브25"].sum(),m.loc["2026-04":,"KRX"].sum()))
