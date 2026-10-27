"""문헌 미검증 팩터 한국 시험: 52주 고가 근접(hi_250_ratio), 복권형(max5_20), 저변동성(vol_60), 비유동성(adv_20), 회전율, 수익성(opa), 매출/시총. 월말 형성→다음 달 수익, 5분위 롱숏. 유니버스 A(거래대금 하위20% 제외), B(시총 상위200). 비용 전.
  .venv/bin/python docs/research/20261026_market_anomalies/factors2_fx.py"""
import os, numpy as np, pandas as pd
from math import sqrt
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); D = f"{ROOT}/DataVault/staging/kr_equity/derived"
cols = ["code","date","market_cap","hi_250_ratio","max5_20","vol_60","adv_20","turnover_20","opa","sales_to_mcap","traded"]
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=cols); p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close"]).dropna(subset=["close"])
g["ym"] = g.date.dt.to_period("M"); p["ym"] = p.date.dt.to_period("M"); me = g.sort_values("date").groupby(["code","ym"]).tail(1)
P = p.sort_values("date").groupby(["code","ym"]).close.last().unstack("code"); R = P.pct_change(fill_method=None).shift(-1).stack().rename("nxt").reset_index()
me = me.merge(R, on=["code","ym"]).dropna(subset=["nxt"]); me = me[(me.ym >= "2013-06") & me.nxt.between(-0.95, 3)]
def t1(x): x = pd.Series(x).dropna(); return x.mean() / (x.std(ddof=1) / sqrt(len(x)))
def ls(df, col, hi_good, nm):
    out = []
    for ym, x in df.dropna(subset=[col]).groupby("ym"):
        if len(x) < 100: continue
        b = pd.qcut(x[col].rank(method="first"), 5, labels=False); top = x[b == (4 if hi_good else 0)].nxt.mean(); bot = x[b == (0 if hi_good else 4)].nxt.mean(); out.append((top - bot, top - x.nxt.mean()))
    o = pd.DataFrame(out, columns=["ls","ex"]); h = len(o)//2
    print(f"  {nm:22s} 롱숏 {o.ls.mean()*100:5.2f}%/월 (t={t1(o.ls):5.2f}, 양수 {(o.ls>0).mean()*100:.0f}%) | 롱단독 초과 {o.ex.mean()*100:5.2f} (t={t1(o.ex):4.2f}) | 전반 {o.ls.iloc[:h].mean()*100:5.2f} 후반 {o.ls.iloc[h:].mean()*100:5.2f}")
A = me[(me.adv_20 >= me.adv_20.quantile(0.2)) & me.traded.fillna(True)]; me["rk"] = me.groupby("ym").market_cap.rank(ascending=False); B = me[me.rk <= 200]
for nm, df in (("A 전체(거래대금 하위20% 제외)", A), ("B 시총 상위200", B)):
    print(f"\n== {nm} ==")
    for c, h, n in (("hi_250_ratio", True, "52주 고가 근접(높을수록)"), ("max5_20", False, "복권형 MAX(낮을수록)"), ("vol_60", False, "저변동성(낮을수록)"), ("adv_20", False, "거래 적음(낮을수록)"), ("turnover_20", False, "회전율(낮을수록)"), ("opa", True, "수익성 opa(높을수록)"), ("sales_to_mcap", True, "매출/시총(높을수록)")): ls(df, c, h, n)
