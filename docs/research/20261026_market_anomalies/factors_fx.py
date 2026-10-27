"""팩터 검증: 한국 전 종목(2013~, 상폐 포함) 월말 형성·다음 달 수익, 5분위 롱숏 + 미국 ETF 대용(IWM−SPY 소형, IWD−IWF·VTV−VUG 가치, MTUM−SPY 모멘텀).
소형=낮은 시총, 가치=높은 B/M·E/P, 모멘텀=12−1개월 수익(mom_250_20). 동일가중, 비용 미반영(총 스프레드). 유니버스 A=거래대금 필터 후 전체, B=시총 상위 200.
  .venv/bin/python docs/research/20261026_market_anomalies/factors_fx.py"""
import os, numpy as np, pandas as pd
from math import sqrt
exec(open(os.path.join(os.path.dirname(__file__), "calendar_fx.py")).read().split("def t1")[0])    # etf(), ROOT
D = f"{ROOT}/DataVault/staging/kr_equity/derived"
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code","date","market_cap","bm","ep","mom_250_20","ret_20","adv_20","traded"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close"]).dropna(subset=["close"])
g["ym"] = g.date.dt.to_period("M"); p["ym"] = p.date.dt.to_period("M")
me = g.sort_values("date").groupby(["code","ym"]).tail(1)
P = p.sort_values("date").groupby(["code","ym"]).close.last().unstack("code"); R = P.pct_change(fill_method=None).shift(-1)      # 월 t 말 형성 → t+1 수익
R = R.stack().rename("nxt").reset_index(); me = me.merge(R, on=["code","ym"]).dropna(subset=["nxt"])
me = me[(me.nxt > -0.95) & (me.nxt < 3)]; me = me[me.ym >= "2013-06"]
print("관측", len(me), "월", me.ym.nunique(), "종목", me.code.nunique(), "| adv_20 중앙", me.adv_20.median())
def t1(x): x = pd.Series(x).dropna(); return x.mean() / (x.std(ddof=1) / sqrt(len(x)))
def ls(df, col, hi_good=True, q=5, tag=""):
    out = []
    for ym, x in df.dropna(subset=[col]).groupby("ym"):
        if len(x) < q * 20: continue
        b = pd.qcut(x[col].rank(method="first"), q, labels=False)
        top = x[b == (q - 1 if hi_good else 0)].nxt.mean(); bot = x[b == (0 if hi_good else q - 1)].nxt.mean(); out.append((ym, top, bot, x.nxt.mean()))
    o = pd.DataFrame(out, columns=["ym","top","bot","all"]).set_index("ym"); o["ls"] = o.top - o.bot; o["ex"] = o.top - o["all"]
    h = len(o) // 2
    print(f"  {tag}{col}: 롱숏 {o.ls.mean()*100:5.2f}%/월 (t={t1(o.ls):5.2f}, 양수 {(o.ls>0).mean()*100:.0f}%) | 롱단독 초과 {o.ex.mean()*100:5.2f}%/월 (t={t1(o.ex):4.2f}) | 전반 {o.ls.iloc[:h].mean()*100:5.2f} 후반 {o.ls.iloc[h:].mean()*100:5.2f} | 상위 {o.top.mean()*100:.2f} 하위 {o.bot.mean()*100:.2f} 전체 {o['all'].mean()*100:.2f}")
    return o
A = me[(me.adv_20 >= me.adv_20.quantile(0.2)) & (me.traded.fillna(True))]; me["rk"] = me.groupby("ym").market_cap.rank(ascending=False); B = me[me.rk <= 200]
for nm, df in (("A 전체(거래대금 하위20% 제외)", A), ("B 시총 상위200", B)):
    print(f"\n== 한국 {nm} ==")
    df = df.assign(small=-np.log(df.market_cap), bmp=df.bm.where(df.bm > 0), epp=df.ep)
    ls(df, "small", True, tag="소형주(작을수록) "); ls(df, "bmp", True, tag="가치 B/M "); ls(df, "epp", True, tag="가치 E/P "); ls(df, "mom_250_20", True, tag="모멘텀 12−1 "); ls(df, "ret_20", False, tag="단기반전(1개월 낮을수록) ")
print("\n== 미국 ETF 대용 (월 총수익 차) ==")
def mret(s): return (1 + s).groupby(s.index.to_period("M")).prod() - 1
E = {k: mret(etf(k)) for k in ("SPY","IWM","IWD","IWF","VTV","VUG","MTUM","IJR")}
def pair(a, b, nm):
    d = (E[a] - E[b]).dropna(); h = len(d) // 2
    print(f"  {nm} ({d.index[0]}~{d.index[-1]}, {len(d)}월): {d.mean()*100:5.2f}%/월 (연 {d.mean()*1200:.1f}%p, t={t1(d):5.2f}, 양수 {(d>0).mean()*100:.0f}%) | 전반 {d.iloc[:h].mean()*100:5.2f} 후반 {d.iloc[h:].mean()*100:5.2f} | 최근5년 {d.iloc[-60:].mean()*100:5.2f}")
pair("IWM","SPY","소형주 IWM−SPY"); pair("IJR","SPY","소형주 IJR−SPY"); pair("IWD","IWF","가치 IWD−성장 IWF"); pair("VTV","VUG","가치 VTV−성장 VUG"); pair("MTUM","SPY","모멘텀 MTUM−SPY")
