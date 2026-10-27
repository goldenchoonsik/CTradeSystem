"""국제수지(ECOS 301Y013)로 본 외국인 자금: 비거주자 국내 주식·채권 순투자 vs KRX 외국인 순매수.
BoP 부호: 부채(비거주자의 국내 투자) 양수 = 순유입, 자산(거주자의 해외 투자) 양수 = 순유출(자산 증가).
"""
import json
import numpy as np
import pandas as pd

D = "/home/kuzimo/projects/CTradeSystem/docs/research/20261025_foreign_channels"
S = "/home/kuzimo/projects/CTradeSystem/DataVault/staging"


def bop(code):
    rows = json.load(open(f"{D}/bop_{code}.json"))["StatisticSearch"]["row"]
    s = pd.Series({pd.Period(r["TIME"], "M"): float(r["DATA_VALUE"]) for r in rows})
    return s.sort_index()


df = pd.DataFrame({
    "비거주자_국내주식": bop("BOPF22100000"),
    "비거주자_국내채권": bop("BOPF22200000"),
    "거주자_해외주식": bop("BOPF21100000"),
    "거주자_해외채권": bop("BOPF21200000"),
    "외국인직접투자": bop("BOPF12100000"),
}) / 1000  # 십억달러

ksp = pd.read_csv(f"{S}/kr_investor_daily/KSP_0001_daily.csv", dtype={"stck_bsop_date": str})
ksp["date"] = pd.to_datetime(ksp["stck_bsop_date"])
ksp = ksp.set_index("date").sort_index()
krw = ksp["frgn_ntby_tr_pbmn"] / 1e6  # 조원
fx = pd.read_csv(f"{S}/fx_usdkrw/usdkrw_daily.csv", parse_dates=["date"]).set_index("date")["usdkrw"]
fx = fx.reindex(fx.index.union(krw.index)).ffill().reindex(krw.index)
usd = (krw * 1e12 / fx) / 1e9  # 십억달러, 일별 환율로 환산
df["KRX_외국인순매수_코스피"] = usd.groupby(usd.index.to_period("M")).sum()
df = df.dropna()
df["주식_KRX차이"] = df["비거주자_국내주식"] - df["KRX_외국인순매수_코스피"]

print(f"기간 {df.index[0]}~{df.index[-1]} n={len(df)}  단위 십억달러")
print("\n=== 1. 월 상관 ===")
print(df.corr().round(2).to_string())
print("\n=== 2. 연도별 합계 ===")
y = df.groupby(df.index.year).sum()
print(y.round(1).to_string())
print("\n=== 3. 비거주자 주식 vs KRX 외국인(코스피) — 회귀 ===")
x = df["KRX_외국인순매수_코스피"]; yy = df["비거주자_국내주식"]
b = np.polyfit(x, yy, 1)
print(f"  기울기 {b[0]:.2f}, 절편 {b[1]:.2f}, R² {np.corrcoef(x,yy)[0,1]**2:.2f}; 합계 BoP {yy.sum():.1f} vs KRX {x.sum():.1f}")
print("\n=== 4. 외국인 주식 대량 순유출 월(BoP 하위 10) ===")
w = df.nsmallest(10, "비거주자_국내주식")[["비거주자_국내주식", "KRX_외국인순매수_코스피", "비거주자_국내채권", "거주자_해외주식"]]
print(w.round(1).to_string())
print("\n=== 5. 주식 순매도 월(BoP<0) 에 채권은? / 거주자 해외주식은? ===")
neg = df[df["비거주자_국내주식"] < 0]; pos = df[df["비거주자_국내주식"] >= 0]
for lab, g in [("주식 순유출월", neg), ("주식 순유입월", pos)]:
    print(f"  {lab} n={len(g)} 평균: 국내채권 {g['비거주자_국내채권'].mean():+.2f}, 해외주식(거주자) {g['거주자_해외주식'].mean():+.2f}, 직접투자 {g['외국인직접투자'].mean():+.2f}")
print(f"  월 상관 주식↔채권 {df['비거주자_국내주식'].corr(df['비거주자_국내채권']):+.2f} / 주식↔거주자 해외주식 {df['비거주자_국내주식'].corr(df['거주자_해외주식']):+.2f}")
print("\n=== 6. 2026 월별 ===")
print(df.loc["2026-01":].round(1).to_string())
