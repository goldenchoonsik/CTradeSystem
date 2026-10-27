"""미국 미드스트림: ETF(AMLP·MLPX·ENFR) vs SPY·XLE 연도별 총수익, 개별 생존 종목의 배당 경로·금리 대비 수익률.
Yahoo 에 상폐 종목(ETP·EEP·SXL·MMP·BPL·EQM 등) 없음 → 개별종목은 생존편향, ETF 가 보정. 실행: .venv/bin/python -I docs/research/20261026_high_dividend/us_midstream.py"""
import sys, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261021_slot_swing_recompute")
from us_data import load, ROOT
def parts(t):
    df = load(t); ca = pd.read_csv(f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv", parse_dates=["date"])
    mult = pd.Series(1.0, index=df.index)
    for d, n, m in zip(ca[ca.type == "split"].date, ca[ca.type == "split"].numerator, ca[ca.type == "split"].denominator): mult[df.index < d] *= n / m
    g = ca[ca.type == "dividend"].groupby("date").amount.sum(); dv = (g / mult.reindex(g.index, method="ffill").fillna(1.0))
    return df["close"], dv
def tr(t, wh=0.85):
    c, dv = parts(t); d = dv.reindex(c.index).fillna(0.0)
    return (((c + wh * d) / c.shift(1)).fillna(1.0)).cumprod()
rate = pd.read_csv(f"{ROOT}/staging/global_macro/series/us_10y.csv", parse_dates=["date"]).set_index("date")["value"]
def yr(s, y):
    a = s[s.index < f"{y}-01-01"]; b = s[s.index < f"{y+1}-01-01"]
    return np.nan if len(a) == 0 or s.index[0] > pd.Timestamp(f"{y}-01-10") else b.iloc[-1] / a.iloc[-1] - 1
etfs = ["AMLP", "MLPX", "ENFR", "XLE", "SPY"]; ix = {t: tr(t) for t in etfs}
years = range(2011, 2026)
T = pd.DataFrame({t: {y: yr(ix[t], y) * 100 for y in years} for t in etfs})
T["10Y연초"] = [rate[rate.index < f"{y}-01-01"].iloc[-1] for y in years]
print("=== 연도별 총수익 % (배당 15% 원천 후) ===\n", T.round(1).to_string())
ex = (T.AMLP - T.SPY)
print(f"\nAMLP − SPY: 평균 {ex.mean():+.1f}%p, 이긴 해 {(ex>0).sum()}/{ex.notna().sum()}")
hi = T["10Y연초"] >= 3.0
print("연초 10Y ≥3%(2011,14,23~25) 평균 초과:", round(ex[hi].mean(), 1), "| 그 외:", round(ex[~hi].mean(), 1))
print("\n=== 상장 이후 전기간 연환산 / 최대낙폭 ===")
for t in ["AMLP", "MLPX", "ENFR", "XLE", "SPY"]:
    s = ix[t]; n = (s.index[-1] - s.index[0]).days / 365.25; sp = ix["SPY"].reindex(s.index).ffill(); sp /= sp.iloc[0]
    print(f"{t:5s} {s.index[0].date()}~ 연 {(s.iloc[-1]**(1/n)-1)*100:5.1f}% | 같은 기간 SPY 연 {(sp.iloc[-1]**(1/n)-1)*100:5.1f}% | 최대낙폭 {((s/s.cummax()-1).min())*100:6.1f}%")
for lab, a, b in (("2014-06~2016-02 유가 폭락", "2014-06-30", "2016-02-11"), ("2020 코로나", "2020-02-19", "2020-03-23"), ("2022", "2021-12-31", "2022-12-30")):
    print(f"{lab}: " + ", ".join(f"{t} {(ix[t][ix[t].index<=b].iloc[-1]/ix[t][ix[t].index<=a].iloc[-1]-1)*100:+.0f}%" for t in ["AMLP", "XLE", "SPY"]))
# 개별 생존 종목: 연간 배당(분할 보정) 경로
names = ["EPD", "KMI", "WMB", "OKE", "TRGP", "MPLX", "WES", "PAA", "CQP", "GEL", "DKL", "HESM", "CAPL"]
print("\n=== 개별(생존) 종목 연간 주당 배당, 2014 = 100 (2014 이후 상장은 첫 완전연도 기준) ===")
rows = {}
for t in names:
    c, dv = parts(t); a = dv.groupby(dv.index.year).sum()
    first = c.index[0].year + 1; base_y = 2014 if first <= 2014 else first
    rows[t] = (a / a.get(base_y, np.nan) * 100).reindex(range(2012, 2026))
D = pd.DataFrame(rows).T; D.insert(0, "기준연", [2014 if parts(t)[0].index[0].year + 1 <= 2014 else parts(t)[0].index[0].year + 1 for t in names])
print(D.round(0).to_string())
# 연초 배당률 vs 10년물, 고금리 해 이후 배당
print("\n=== 연초 배당률(직전 12개월 배당/가격) − 10년물, 개별 종목 평균·최소 ===")
sp = {}
for y in (2014, 2015, 2019, 2022, 2023, 2024, 2025):
    ys = []
    for t in names:
        c, dv = parts(t); d0 = c.index[c.index >= f"{y}-01-01"]
        if len(d0) == 0 or c.index[0] > pd.Timestamp(f"{y-1}-01-01"): continue
        d0 = d0[0]; tr12 = dv[(dv.index > d0 - pd.Timedelta(days=365)) & (dv.index < d0)].sum(); ys.append(100 * tr12 / c.loc[d0])
    r = rate[rate.index < f"{y}-01-01"].iloc[-1]
    print(f"{y}: n={len(ys)} 평균 배당률 {np.mean(ys):.1f}% (10Y {r:.1f}%) 평균 스프레드 {np.mean(ys)-r:+.1f}%p, 금리 초과 종목 {sum(v>r for v in ys)}/{len(ys)}")
