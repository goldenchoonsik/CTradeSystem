"""외국인 순매도 큰 날 vs 지수 — 일 단위. 흐름 = Σ Δ외국인보유수량×수정종가(기업행위·거래량초과 행 제외) / 그날 전종목 거래대금. 지수=KOSPI(U001).
  .venv/bin/python docs/research/20261024_foreign_accum/daily_sell_days.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived")
e = pd.read_parquet(f"{D}/daily_ext.parquet", columns=["code","date","shares_listed","foreign_hold_qty","inst_net_qty"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close","volume","value","factor"]).dropna(subset=["close"])
d = e.merge(p, on=["code","date"]).sort_values(["code","date"]); g = d.groupby("code")
dq = g.foreign_hold_qty.diff()
bad = (g.shares_listed.diff().fillna(0) != 0) | (g.factor.diff().fillna(0) != 0) | dq.isna() | (dq.abs() > d.volume + 1)
d["f"] = np.where(bad, 0.0, dq * d.close); d["i"] = np.where(bad, 0.0, d.inst_net_qty * d.close)
a = d[d.date >= "2016-01-01"].groupby("date").agg(f=("f","sum"), i=("i","sum"), v=("value","sum"))
a["fr"] = a.f / a.v * 100; a["ir"] = a.i / a.v * 100; a["pr"] = -(a.f + a.i) / a.v * 100
ix = pd.read_parquet(f"{D}/index_1d.parquet"); k = ix[ix.code == "U001"].set_index("date").close
a["ret"] = k.pct_change().reindex(a.index) * 100
a = a.dropna(subset=["ret"]); a = a[a.v > 0]
for h in (1, 5, 20): a[f"n{h}"] = (k.shift(-h) / k - 1).reindex(a.index) * 100
a["prev"] = a.ret.shift(1)
print("일수", len(a), a.index.min().date(), a.index.max().date())
print("상관: 흐름(t)~수익(t) %.2f | 흐름(t)~수익(t-1) %.2f | 흐름(t)~수익(t+1) %.2f | 수익(t)~흐름(t+1) %.2f" % (
    a.fr.corr(a.ret), a.fr.corr(a.prev), a.fr.corr(a.ret.shift(-1)), a.ret.corr(a.fr.shift(-1))))
a["dec"] = pd.qcut(a.fr, 10, labels=False)
t = a.groupby("dec").agg(일수=("ret","size"), 외인순매수_거래대금비=("fr","mean"), 같은날수익=("ret","mean"), 하락일비율=("ret", lambda s: (s<0).mean()*100),
                         전일수익=("prev","mean"), 다음날=("n1","mean"), 다음5일=("n5","mean"), 다음20일=("n20","mean"), 기관=("ir","mean"), 개인역산=("pr","mean"))
print(t.round(2).to_string())
print("\n전체 평균: 같은날 %.2f, 하락일 %.0f%%" % (a.ret.mean(), (a.ret < 0).mean()*100))
for lab, s in [("매도 상위 5% 일", a[a.fr <= a.fr.quantile(.05)]), ("매수 상위 5% 일", a[a.fr >= a.fr.quantile(.95)])]:
    print(f"{lab} n={len(s)}: 같은날 {s.ret.mean():.2f}% (하락 {(s.ret<0).mean()*100:.0f}%), 전일 {s.prev.mean():.2f}%, 다음날 {s.n1.mean():.2f}%, 5일 {s.n5.mean():.2f}%, 20일 {s.n20.mean():.2f}%")
# 매도 큰 날의 전일: 이미 하락 중이었나 — 전일 수익 부호
s = a[a.fr <= a.fr.quantile(.05)]; print("매도 상위 5% 일 중 전일도 하락:", round((s.prev < 0).mean()*100), "%  | 전체:", round((a.prev < 0).mean()*100), "%")
# 연도별 같은날 상관
print((a.groupby(a.index.year).apply(lambda x: x.fr.corr(x.ret))).round(2).to_dict())
a.to_csv(os.path.join(os.path.dirname(__file__), "daily_sell_days.csv"))
