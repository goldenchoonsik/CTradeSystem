"""캘린더 이상현상 검증: 산타랠리·1월 효과(소형주)·5월에 팔아라·월말월초·대통령 임기 사이클. 미국=SPY(배당 포함 총수익, 1993~)·IWM·QQQ, 한국=코스피 지수 U001(가격, 2013~)·U002 대형/U004 소형.
  .venv/bin/python docs/research/20261026_market_anomalies/calendar_fx.py"""
import os, numpy as np, pandas as pd
from math import sqrt
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
Y = f"{ROOT}/DataVault/raw/yahoo"
def etf(sym):
    p = pd.read_csv(f"{Y}/candle_1d/{sym}_1d.csv", parse_dates=["timestamp"]).set_index("timestamp").close
    ca = pd.read_csv(f"{Y}/corporate_actions/{sym}.csv", parse_dates=["date"]) if os.path.exists(f"{Y}/corporate_actions/{sym}.csv") else pd.DataFrame(columns=["date","type","numerator","denominator","amount"])
    div = ca[ca.type == "dividend"].groupby("date").amount.sum().reindex(p.index).fillna(0)
    sp = ca[ca.type == "split"].assign(f=lambda x: x.numerator / x.denominator).groupby("date").f.prod().reindex(p.index).fillna(1)
    return ((p * sp + div) / p.shift(1) - 1).dropna()
kr = pd.read_parquet(f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet", columns=["code","date","close"])
krr = lambda c: kr[kr.code == c].set_index("date").close.pct_change().dropna()
def t1(x):
    x = pd.Series(x).dropna(); return x.mean() / (x.std(ddof=1) / sqrt(len(x))) if len(x) > 2 else np.nan
def monthly(r): return (1 + r).groupby(r.index.to_period("M")).prod() - 1
def pr(*a): print(*a)
def santa(r, nm):
    out = []
    for y in sorted(set(r.index.year)):
        dec = r[(r.index.year == y) & (r.index.month == 12)]; jan = r[(r.index.year == y + 1) & (r.index.month == 1)]
        if len(dec) < 15 or len(jan) < 2: continue
        w = pd.concat([dec.iloc[-5:], jan.iloc[:2]]); out.append((y, (1 + w).prod() - 1))
    s = pd.Series(dict(out)); base = (1 + r).prod() ** (1 / len(r)) - 1; b7 = (1 + base) ** 7 - 1
    pr(f"[산타랠리 {nm}] {len(s)}년: 7일 평균 {s.mean()*100:.2f}% 중앙 {s.median()*100:.2f}% 양수 {(s>0).mean()*100:.0f}% | 평소 7일 기대 {b7*100:.2f}% | 초과 t={t1(s-b7):.2f} | 최근10년 평균 {s.iloc[-10:].mean()*100:.2f}%")
def jan(r, nm, small=None, large=None):
    m = monthly(r); mm = m.groupby(m.index.month).agg(["mean", "median", lambda s: (s > 0).mean()]); mm.columns = ["평균", "중앙", "양수비"]
    j = m[m.index.month == 1]; o = m[m.index.month != 1]
    pr(f"[1월 효과 {nm}] 1월 평균 {j.mean()*100:.2f}% (n={len(j)}, 양수 {(j>0).mean()*100:.0f}%) vs 타월 {o.mean()*100:.2f}% | 차 t={(j.mean()-o.mean())/sqrt(j.var()/len(j)+o.var()/len(o)):.2f} | 월 순위 {int((mm['평균'].rank(ascending=False)).loc[1])}위/12")
    pr("   월별 평균%:", " ".join(f"{k}:{v*100:.1f}" for k, v in mm["평균"].items()))
    if small is not None:
        d = (monthly(small) - monthly(large)).dropna(); dj = d[d.index.month == 1]; do = d[d.index.month != 1]
        pr(f"   소형−대형 1월 {dj.mean()*100:.2f}%p (양수 {(dj>0).mean()*100:.0f}%, n={len(dj)}) vs 타월 {do.mean()*100:.2f}%p | t(차)={(dj.mean()-do.mean())/sqrt(dj.var()/len(dj)+do.var()/len(do)):.2f}")
def may(r, nm):
    m = monthly(r); rows = []
    for y in sorted(set(m.index.year)):
        w = m[(m.index >= pd.Period(f"{y}-05", "M")) & (m.index <= pd.Period(f"{y}-10", "M"))]; s = m[(m.index >= pd.Period(f"{y-1}-11", "M")) & (m.index <= pd.Period(f"{y}-04", "M"))]
        if len(w) == 6 and len(s) == 6: rows.append((y, (1 + s).prod() - 1, (1 + w).prod() - 1))
    d = pd.DataFrame(rows, columns=["y", "winter", "summer"]).set_index("y"); df = d.winter - d.summer
    pr(f"[5월에 팔아라 {nm}] {len(d)}년: 11~4월 평균 {d.winter.mean()*100:.1f}% (중앙 {d.winter.median()*100:.1f}) vs 5~10월 {d.summer.mean()*100:.1f}% (중앙 {d.summer.median()*100:.1f}) | 차 {df.mean()*100:.1f}%p, 겨울이 큰 해 {(df>0).sum()}/{len(df)}, t={t1(df):.2f} | 5~10월 음수 {(d.summer<0).sum()}년")
    h = len(d) // 2; pr(f"   전반 {d.index[0]}~{d.index[h-1]} 차 {df.iloc[:h].mean()*100:.1f}%p / 후반 {d.index[h]}~{d.index[-1]} 차 {df.iloc[h:].mean()*100:.1f}%p")
def tom(r, nm):
    ym = r.index.to_period("M"); pos = r.groupby(ym).cumcount(); n = r.groupby(ym).transform("size"); fromend = n - pos - 1      # 0=월 마지막 거래일
    inw = (fromend <= 0) | (pos <= 2)      # 마지막 1일 + 다음 달 첫 3일 = 4일
    a, b = r[inw], r[~inw]; yr = r.index.year
    pr(f"[월말·월초 {nm}] 창 4일/월: 일평균 {a.mean()*1e4:.1f}bp vs 그 외 {b.mean()*1e4:.1f}bp (t={(a.mean()-b.mean())/sqrt(a.var()/len(a)+b.var()/len(b)):.2f}) | 창 수익 비중 {a.sum()/r.sum()*100:.0f}% (거래일 비중 {inw.mean()*100:.0f}%)")
    h = r.index[len(r) // 2]; a1, b1, a2, b2 = r[inw & (r.index < h)], r[~inw & (r.index < h)], r[inw & (r.index >= h)], r[~inw & (r.index >= h)]
    pr(f"   전반 차 {(a1.mean()-b1.mean())*1e4:.1f}bp / 후반 차 {(a2.mean()-b2.mean())*1e4:.1f}bp")
def pres(r, nm):
    y = (1 + r).groupby(r.index.year).prod() - 1; y = y[y.index <= 2025]; t = pd.Series(((y.index - 1993) % 4) + 1, index=y.index)
    g = y.groupby(t).agg(["mean", "median", "count"]); pr(f"[대통령 사이클 {nm}] 임기연차별 연수익% (평균/중앙/n): " + " | ".join(f"{k}년차 {v['mean']*100:.1f}/{v['median']*100:.1f}/{int(v['count'])}" for k, v in g.iterrows()))
    a, b = y[t == 3], y[t != 3]; pr(f"   3년차 vs 나머지: {a.mean()*100:.1f}% vs {b.mean()*100:.1f}%, t(차)={(a.mean()-b.mean())/sqrt(a.var()/len(a)+b.var()/len(b)):.2f}, 3년차 양수 {(a>0).sum()}/{len(a)}, 순위 {int(g['mean'].rank(ascending=False)[3])}/4")
spy, iwm, qqq = etf("SPY"), etf("IWM"), etf("QQQ")
print("SPY", spy.index[0].date(), spy.index[-1].date(), len(spy), "| 코스피", krr("U001").index[0].date(), krr("U001").index[-1].date())
for nm, r in (("SPY 1993~", spy), ("코스피 2013~", krr("U001"))): santa(r, nm)
print(); jan(spy, "SPY", iwm.reindex(spy.index).dropna(), spy.reindex(iwm.index).dropna()); jan(krr("U001"), "코스피", krr("U004"), krr("U002"))
print(); may(spy, "SPY"); may(krr("U001"), "코스피")
print(); tom(spy, "SPY"); tom(krr("U001"), "코스피")
print(); pres(spy, "SPY (1993~2025, 8.25사이클)")
# 최근 10년만 따로
print("\n== 최근 10년(2016~) 재확인 =="); s10 = spy[spy.index >= "2016-01-01"]; may(s10, "SPY 2016~"); tom(s10, "SPY 2016~"); santa(s10, "SPY 2016~")
