"""국제수지 비거주자 국내 주식 순투자(월) = KRX 코스피+코스닥 외국인 순매수 + 잔차(비거래성: 신주·DR 예탁·IPO·장외·ETF 등). 잔차 월별·연도별.
비거래성 보유 변동(daily_ext 보유수량 변화 중 거래량 초과분 × 수정종가 아님→원 종가) 별도 집계로 7월 하이닉스 ADR 신주 검증.
"""
import json, numpy as np, pandas as pd
R="/home/kuzimo/projects/CTradeSystem"; S=f"{R}/DataVault/staging"; D=f"{R}/docs/research/20261025_foreign_channels"
def bop(c):
    r=json.load(open(f"{D}/bop_{c}.json"))["StatisticSearch"]["row"]; return pd.Series({pd.Period(x["TIME"],"M"):float(x["DATA_VALUE"]) for x in r}).sort_index()/1000
fx=pd.read_csv(f"{S}/fx_usdkrw/usdkrw_daily.csv",parse_dates=["date"]).set_index("date").usdkrw
def krx(f):
    k=pd.read_csv(f"{S}/kr_investor_daily/{f}",dtype={"stck_bsop_date":str}); k["date"]=pd.to_datetime(k.stck_bsop_date); k=k.set_index("date").sort_index()
    f2=fx.reindex(fx.index.union(k.index)).ffill().reindex(k.index); u=(k.frgn_ntby_tr_pbmn/1e6*1e12/f2/1e9); return u.groupby(u.index.to_period("M")).sum()
df=pd.DataFrame({"BoP":bop("BOPF22100000"),"KSP":krx("KSP_0001_daily.csv"),"KSQ":krx("KSQ_1001_daily.csv")}).dropna()
df["KRX합"]=df.KSP+df.KSQ; df["잔차"]=df.BoP-df.KRX합
print("=== 연도별 (십억달러): BoP vs 코스피+코스닥 외국인 순매수, 잔차=비거래성 등 ===")
y=df.groupby(df.index.year).sum(); print(y.round(1).to_string())
print(f"\n전체 합 BoP {df.BoP.sum():.1f} / KRX합 {df.KRX합.sum():.1f} / 잔차 {df.잔차.sum():.1f}; 월 상관 BoP~KRX합 {df.BoP.corr(df.KRX합):.3f}, 잔차 월 표준편차 {df.잔차.std():.2f}")
print("\n=== 2025-10~2026-08 월별 ===");print(df.loc["2025-10":].round(1).to_string())
print("\n=== 잔차 양(+)이 큰 달 상위 8 / 음(−) 하위 5 ===");print(df.nlargest(8,"잔차")[["BoP","KRX합","잔차"]].round(1).to_string());print(df.nsmallest(5,"잔차")[["BoP","KRX합","잔차"]].round(1).to_string())
# 비거래성 보유 변동: 보유수량 일변화 - 외국인 순매수 추정? daily_ext 만으로는 순매수 없음 → 거래량 초과 일변화 (종목별 |Δ|>거래량)
de=pd.read_parquet(f"{R}/DataVault/staging/kr_equity/derived/daily_ext.parquet",columns=["code","date","foreign_hold_qty","shares_listed"])
pr=pd.read_parquet(f"{R}/DataVault/staging/kr_equity/derived/prices_1d.parquet")
print("\nprices_1d cols:",pr.columns.tolist()[:12])
