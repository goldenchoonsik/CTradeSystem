"""대선 4년 주기 구간별 비교. 1929~2024 = S&P500 일봉 월말 종가(6구간×4주기), 1873~1925 = Shiller 월평균 가격(참고, 평활됨).
  .venv/bin/python docs/research/20261026_market_anomalies/cycle_periods.py"""
import os, json, numpy as np, pandas as pd
from math import sqrt
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
j = json.load(open(f"{ROOT}/DataVault/staging/us_valuation/raw/yahoo/GSPC_20261023.json"))["chart"]["result"][0]
P = pd.Series(j["indicators"]["quote"][0]["close"], index=pd.to_datetime(j["timestamp"], unit="s").normalize()).dropna(); P = P[~P.index.duplicated()]
M = P.resample("ME").last(); M = M[M.index <= "2026-09-30"]; r = M.pct_change().dropna(); r.index = r.index.to_period("M")
sh = pd.read_csv(f"{ROOT}/DataVault/staging/us_valuation/derived/panel_monthly.csv", usecols=["ym", "P"]).dropna(); sh.index = pd.PeriodIndex(sh.ym, freq="M"); rs = sh.P.pct_change().dropna()
ov = pd.concat([r, rs], axis=1, join="inner").dropna(); print("Shiller vs GSPC 월수익 상관(1928~):", round(ov.corr().iloc[0, 1], 2))
def cycles(rr, starts):
    out = {}
    for y in starts:
        x = rr[f"{y}-01":f"{y+3}-12"]
        if len(x) == 48: out[y] = x.values
    return pd.DataFrame(out, index=range(1, 49)).T
def comp(C, sl): return (1 + C.iloc[:, sl]).prod(axis=1) - 1
def report(C, nm):
    base9 = (1 + C.values.mean()) ** 9 - 1; n = len(C)
    yy = {k: comp(C, slice((k-1)*12, k*12)) for k in range(1, 5)}
    strong = comp(C, slice(21, 30))          # 2년차 10월 ~ 3년차 6월 (9개월)
    weak = comp(C, slice(13, 21))            # 2년차 2~9월 (8개월)
    q = lambda k: comp(C, slice((k-1)*3, k*3))
    ex = strong - base9
    print(f"{nm:18s} 주기 {n:2d} | 연차별 " + " ".join(f"{k}:{yy[k].mean()*100:5.1f}" for k in range(1, 5)) + f" | 강세구간(2년차10월~3년차6월) 평균 {strong.mean()*100:5.1f}% 양수 {(strong>0).sum()}/{n} 평소 {base9*100:.1f}% | 약세구간(2년차2~9월) 평균 {weak.mean()*100:5.1f}% 양수 {(weak>0).sum()}/{n} | 3년차>나머지 {(yy[3] > (yy[1]+yy[2]+yy[4])/3).sum()}/{n}")
    return strong, weak, yy
S = list(range(1929, 2025, 4)); print("\n== 구간별 (S&P500 월말 종가, 가격) ==")
rows = {}
for a, b in ((1929, 1944), (1945, 1960), (1961, 1976), (1977, 1992), (1993, 2008), (2009, 2024)):
    C = cycles(r, [y for y in S if a <= y <= b - 3]); rows[f"{a}~{b}"] = report(C, f"{a}~{b}")
print("\n== 참고: 1925 이전 (Shiller 월평균 가격, 평활되어 일부 과소·평탄) ==")
Sp = list(range(1873, 1926, 4)); 
for a, b in ((1873, 1900), (1901, 1924), (1925, 1928)):
    C = cycles(rs, [y for y in Sp if a <= y <= b]); 
    if len(C): report(C, f"{a}~{b+3} (n 시작년)")
print("\n== 1925 주기부터(Shiller 1925-28 + S&P 1929~) 총 25주기 = 5구간 5주기 씩 ==")
allr = pd.concat([rs[:"1928-12"], r["1929-01":]]); S25 = list(range(1925, 2025, 4)); Call = cycles(allr, S25)
for i in range(0, 25, 5): report(Call.iloc[i:i+5], f"{Call.index[i]}~{Call.index[min(i+4,24)]+3}")
print("\n== 구간 내 분기별 (2-4, 3-1, 3-2 평균% / 양수 개수), 6구간 ==")
for a, b in ((1929, 1944), (1945, 1960), (1961, 1976), (1977, 1992), (1993, 2008), (2009, 2024)):
    C = cycles(r, [y for y in S if a <= y <= b - 3]); qq = lambda k: ((1 + C.iloc[:, (k-1)*3:k*3]).prod(axis=1) - 1)
    print(f"  {a}~{b}: " + " | ".join(f"{(k-1)//4+1}-{(k-1)%4+1}Q {qq(k).mean()*100:5.1f}%({(qq(k)>0).sum()}/{len(C)})" for k in (6, 7, 8, 9, 10, 11, 12)))
print("\n== 주기별 최저·최고 시점(연차), 구간별 ==")
for a, b in ((1929, 1944), (1945, 1960), (1961, 1976), (1977, 1992), (1993, 2008), (2009, 2024)):
    C = cycles(r, [y for y in S if a <= y <= b - 3]); cum = (1 + C).cumprod(axis=1); cum.columns = range(1, 49)
    print(f"  {a}~{b}: 최저 연차 {((cum.idxmin(axis=1)-1)//12+1).tolist()} 최고 연차 {((cum.idxmax(axis=1)-1)//12+1).tolist()}")
print("\n주기 시작년: ", S)
