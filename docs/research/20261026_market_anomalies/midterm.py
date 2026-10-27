"""중간선거(미국, 1994·98·2002·06·10·14·18·22) 전후 수익. 선거일 = 11월 첫 월요일 다음 화요일. SPY 1993~, QQQ 1999~ (배당 포함).
  .venv/bin/python docs/research/20261026_market_anomalies/midterm.py"""
import os, numpy as np, pandas as pd
from math import sqrt
f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calendar_fx.py"); g = {"__file__": f}; exec(compile(open(f).read().split("spy, iwm, qqq =")[0], f, "exec"), g)
def elec(y):
    d = pd.Timestamp(f"{y}-11-02")
    while d.weekday() != 1: d += pd.Timedelta(days=1)
    return d
def study(sym, years, label):
    r = g["etf"](sym); eq = (1 + r).cumprod(); idx = eq.index; rows = []
    for y in years:
        e = elec(y); i = idx.searchsorted(e)
        if i - 126 < 0 or i + 252 >= len(idx): continue
        rows.append(dict(y=y, pre6=eq.iloc[i] / eq.iloc[i-126] - 1, post3=eq.iloc[i+63] / eq.iloc[i] - 1, post6=eq.iloc[i+126] / eq.iloc[i] - 1, post12=eq.iloc[i+252] / eq.iloc[i] - 1,
                         ytd=eq.iloc[i] / eq[eq.index.year == y].iloc[0] - 1, low2elec=eq.iloc[i] / eq.loc[f"{y}-01-01":e].min() - 1, drawdown=(eq.loc[f"{y}-01-01":e] / eq.loc[f"{y}-01-01":e].cummax() - 1).min()))
    d = pd.DataFrame(rows).set_index("y"); print(f"\n== {label}: 중간선거 {len(d)}회 ({list(d.index)}) ==")
    for c, h in (("pre6", 126), ("post3", 63), ("post6", 126), ("post12", 252)):
        base = (eq.shift(-h) / eq - 1).dropna(); x = d[c]
        print(f"  {c:6s} 평균 {x.mean()*100:5.1f}% 중앙 {x.median()*100:5.1f}% 양수 {(x>0).sum()}/{len(x)} 최소 {x.min()*100:5.1f}% | 평소 같은 길이 평균 {base.mean()*100:5.1f}% 중앙 {base.median()*100:5.1f}% | 초과 t={(x.mean()-base.mean())/(x.std(ddof=1)/sqrt(len(x))):5.2f}")
    print("  선거일 직전 낙폭(그해 초~선거일 최대) 평균 %.1f%%, 선거일 가격이 그해 최저점 대비 +%.1f%%" % (d.drawdown.mean()*100, d.low2elec.mean()*100))
    print("  연도별 post6%:", " ".join(f"{y}:{v*100:.0f}" for y, v in d.post6.items()), "| post12%:", " ".join(f"{y}:{v*100:.0f}" for y, v in d.post12.items()))
    return eq
eq = study("SPY", [1994,1998,2002,2006,2010,2014,2018,2022], "SPY"); study("QQQ", [2002,2006,2010,2014,2018,2022], "QQQ (1998 제외)")
study("SPY", [1996,2000,2004,2008,2012,2016,2020], "대조: SPY 대선 해(선거일 기준)")
e = elec(2026); i = eq.index.searchsorted(e); print("\n2026 중간선거일", e.date(), "| 현재 데이터 마지막", eq.index[-1].date(), "| SPY 올해 수익 %.1f%%, 올해 최대낙폭 %.1f%%, 최근 126일 %.1f%%" % ((eq.iloc[-1] / eq[eq.index.year == 2026].iloc[0] - 1) * 100, ((eq.loc["2026-01-01":] / eq.loc["2026-01-01":].cummax() - 1).min()) * 100, (eq.iloc[-1] / eq.iloc[-127] - 1) * 100))
