"""미국 고배당 ETF·리츠 ETF·대표 리츠를 SPY 와 캘린더 연도로 비교, 연초 10년물 금리 국면별 (사전등록 4번, 리츠 별도).
총수익 = 가격 + 배당(15% 원천징수 후, 재투자). 실행: .venv/bin/python -I docs/research/20261026_high_dividend/us_etf.py"""
import sys, numpy as np, pandas as pd
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261021_slot_swing_recompute")
from us_data import load, ROOT

def tr(t, wh=0.85):
    df = load(t)
    c = df["close"]
    ca = pd.read_csv(f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv", parse_dates=["date"])
    raw = load(t); mult = pd.Series(1.0, index=raw.index)
    for d, n, m in zip(ca[ca.type == "split"].date, ca[ca.type == "split"].numerator, ca[ca.type == "split"].denominator):
        mult[raw.index < d] *= n / m
    dv = ca[ca.type == "dividend"].groupby("date").amount.sum()
    dv = (dv / mult.reindex(dv.index).fillna(1.0)).reindex(c.index).fillna(0.0)
    r = ((c + wh * dv) / c.shift(1)).fillna(1.0)
    return r.cumprod()

rate = pd.read_csv(f"{ROOT}/staging/global_macro/series/us_10y.csv", parse_dates=["date"]).set_index("date")["value"]
GROUPS = {"고배당 ETF": ["DVY", "VYM", "HDV", "SCHD", "SPYD", "SDY"],
          "배당성장 ETF": ["NOBL", "VIG"],
          "리츠 ETF": ["VNQ", "IYR", "SCHH", "USRT", "XLRE"],
          "모기지 리츠 ETF": ["REM", "MORT"],
          "개별 리츠": ["O", "PLD", "AMT", "NLY"]}
names = ["SPY"] + [t for g in GROUPS.values() for t in g]
idx = {t: tr(t) for t in names}
years = range(2001, 2026)   # 2026 은 미완

def yret(t, y):
    s = idx[t]
    a = s[s.index < f"{y}-01-01"]; b = s[s.index < f"{y+1}-01-01"]
    if len(a) == 0 or len(b) == 0 or s.index[0] > pd.Timestamp(f"{y}-01-10"): return np.nan
    return b.iloc[-1] / a.iloc[-1] - 1

tab = pd.DataFrame({t: {y: yret(t, y) for y in years} for t in names})
r0 = pd.Series({y: rate[rate.index < f"{y}-01-01"].iloc[-1] for y in years})
chg = pd.Series({y: r0[y] - (rate[rate.index < f"{y-1}-01-01"].iloc[-1] if y > 2001 else np.nan) for y in years})
q = r0.quantile([1/3, 2/3]).values
reg = r0.apply(lambda v: "고금리(상위1/3)" if v >= q[1] else ("저금리(하위1/3)" if v <= q[0] else "중간"))
print("연초 10년물 3분위 경계:", np.round(q, 2))
print(pd.concat([r0.rename("10Y연초"), chg.rename("전년변화%p"), reg.rename("국면")], axis=1).round(2).to_string())
ex = tab.sub(tab["SPY"], axis=0).drop(columns="SPY")
print("\n=== 연도별 총수익(세후배당) % ===\n", (tab * 100).round(1).to_string())
def summ(cols, mask, label):
    out = []
    for t in cols:
        e = ex.loc[mask, t].dropna(); r = tab.loc[mask, t].dropna()
        if len(e) == 0: continue
        out.append([t, len(e), r.mean() * 100, tab.loc[e.index, "SPY"].mean() * 100, e.mean() * 100, (e > 0).mean() * 100, e.min() * 100])
    print(f"\n--- {label} ---")
    print(pd.DataFrame(out, columns=["티커", "연수", "평균수익%", "SPY평균%", "초과%p", "이긴해%", "최악초과%p"]).round(1).to_string(index=False))
for g, cols in GROUPS.items():
    for rg in ["고금리(상위1/3)", "중간", "저금리(하위1/3)"]:
        summ(cols, reg == rg, f"{g} | {rg} (연초 10Y {'≥%.1f' % q[1] if rg[0]=='고' else ('≤%.1f' % q[0] if rg[0]=='저' else '사이')})")
up = chg > 0.5; dn = chg < -0.5
for g, cols in GROUPS.items():
    summ(cols, up.fillna(False), f"{g} | 직전 1년 금리 +0.5%p 이상 상승한 해"); summ(cols, dn.fillna(False), f"{g} | 금리 −0.5%p 이상 하락한 해")
# 전 기간 연환산(공통 시작: 각 ETF 상장 이후) 대 SPY
print("\n=== 상장 이후 전기간 연환산(세후배당) ===")
for t in names:
    s = idx[t]; yrs = (s.index[-1] - s.index[0]).days / 365.25
    sp = idx["SPY"].reindex(s.index).ffill(); sp = sp / sp.iloc[0]
    print(f"{t:5s} {s.index[0].date()}~ {yrs:4.1f}년  연{(s.iloc[-1]**(1/yrs)-1)*100:5.1f}%  SPY연{(sp.iloc[-1]**(1/yrs)-1)*100:5.1f}%  최대낙폭{((s/s.cummax()-1).min())*100:6.1f}%  SPY낙폭{((sp/sp.cummax()-1).min())*100:6.1f}%")
tab.to_csv(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "us_etf_yearly.csv"))
