"""한국 대형주 모멘텀 매매 시험: 월말 시총 상위 N 중 12−1 모멘텀 상위 K 동일가중 보유, 다음 달 말 리밸런싱. 비용 = 교체 비중 × 왕복 0.27%+슬리피지 0.1%. 비교 = 상위 N 동일가중·코스피.
  .venv/bin/python docs/research/20261026_market_anomalies/mom_strategy.py"""
import os, numpy as np, pandas as pd
D = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "DataVault", "staging", "kr_equity", "derived"))
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code","date","market_cap","mom_250_20","traded"]); p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close"]).dropna(subset=["close"])
g["ym"] = g.date.dt.to_period("M"); p["ym"] = p.date.dt.to_period("M")
me = g.sort_values("date").groupby(["code","ym"]).tail(1)
P = p.sort_values("date").groupby(["code","ym"]).close.last().unstack("code"); R = P.pct_change(fill_method=None).shift(-1).stack().rename("nxt").reset_index()
me = me.merge(R, on=["code","ym"]).dropna(subset=["nxt","mom_250_20"]); me = me[(me.ym >= "2013-06") & me.nxt.between(-0.95, 3)]
ix = pd.read_parquet(f"{D}/index_1d.parquet", columns=["code","date","close"]); ix = ix[ix.code == "U001"].set_index("date").close; kospi = ix.groupby(ix.index.to_period("M")).last().pct_change()
COST = 0.0037
def run(N, K, cost=COST):
    prev, rows = set(), []
    for ym, x in me.groupby("ym"):
        x = x.assign(rk=x.market_cap.rank(ascending=False)); x = x[x.rk <= N]
        if len(x) < N * .8: continue
        pick = x.nlargest(K, "mom_250_20"); s = set(pick.code); turn = 1 - len(s & prev) / K if prev else 1.0
        rows.append((ym, pick.nxt.mean() - turn * cost, pick.nxt.mean(), x.nxt.mean(), turn)); prev = s
    o = pd.DataFrame(rows, columns=["ym","net","gross","uni","turn"]).set_index("ym"); o["kospi"] = kospi.shift(-1).reindex(o.index); return o
def summ(o, c, nm):
    r = o[c].dropna(); eq = (1 + r).cumprod(); yrs = len(r) / 12
    return f"{nm}: 연 {((eq.iloc[-1])**(1/yrs)-1)*100:5.1f}% 변동 {r.std()*sqrt12:4.1f}% 샤프 {r.mean()/r.std()*sqrt12:4.2f} MDD {((eq/eq.cummax()-1).min())*100:5.1f}%"
sqrt12 = 12 ** .5
for N, K in ((200, 40), (200, 20), (100, 20), (300, 60)):
    o = run(N, K); print(f"\n== 상위{N} 중 모멘텀 상위 {K} | 월 교체율 {o.turn.mean()*100:.0f}% | {o.index[0]}~{o.index[-1]} {len(o)}월 ==")
    for c, nm in (("net","전략(비용후)"),("gross","전략(비용전)"),("uni","상위N 동일가중"),("kospi","코스피(가격)")): print(" ", summ(o, c, nm))
    d = (o.net - o.uni); h = len(d)//2; print(f"  초과(비용후−유니버스): {d.mean()*1200:.1f}%p/년 t={d.mean()/d.std()*len(d)**.5:.2f} 양수 {(d>0).mean()*100:.0f}% | 전반 {d.iloc[:h].mean()*1200:.1f} 후반 {d.iloc[h:].mean()*1200:.1f}%p")
    y = o.groupby(o.index.year).apply(lambda z: pd.Series({"전략": (1+z.net).prod()-1, "유니": (1+z.uni).prod()-1})); print("  연도별 전략−유니(%p):", " ".join(f"{k}:{(v.전략-v.유니)*100:+.0f}" for k, v in y.iterrows()))
