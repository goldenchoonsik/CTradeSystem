"""종목별·규모별 재검증 — 외국인 누적 순매수(시총 대비%, 120일) vs 같은 창 수익(동행)·이후 120일(선행). 거래량 초과·기업행위일은 흐름 0 처리.
  .venv/bin/python docs/research/20261024_foreign_accum/by_stock.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived")
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code","date","close_unadj","adv_20","market_cap"])
e = pd.read_parquet(f"{D}/daily_ext.parquet", columns=["code","date","shares_listed","foreign_hold_qty"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close","volume","factor"])
df = g.merge(e, on=["code","date"]).merge(p, on=["code","date"]).sort_values(["code","date"])
df = df[df.date >= "2014-06-01"]; gb = df.groupby("code")
dq = gb.foreign_hold_qty.diff()
bad = (gb.shares_listed.diff().fillna(0) != 0) | (gb.factor.diff().fillna(0) != 0) | dq.isna() | (dq.abs() > df.volume + 1)
df["flow"] = np.where(bad, 0.0, dq * df.close); df["ok"] = (~bad).astype(float); df["lc"] = np.log(df.close)
W, H = 120, 120
df["f"] = np.where(gb.ok.transform(lambda s: s.rolling(W, min_periods=108).mean()) > .9,
                   gb.flow.transform(lambda s: s.rolling(W, min_periods=108).sum()) / gb.market_cap.shift(W) * 100, np.nan)
df["r"] = gb.lc.diff(W); df["x"] = gb.lc.shift(-H) - df.lc
me = df.groupby(df.date.dt.to_period("M")).date.max()
m = df[df.date.isin(me.values) & (df.close_unadj >= 1000) & (df.adv_20 >= 5e8) & (df.date >= "2016-01-01")].dropna(subset=["f","r"]).copy()
# 규모 3분위(월별) IC
m["sz"] = m.groupby("date").market_cap.transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=["소형","중형","대형"]))
rows = []
for sz, s in m.groupby("sz", observed=True):
    for lab, b in [("동행", "r"), ("선행120일", "x")]:
        for per, mk in [("2016~23", s.date < "2024-01-01"), ("2024~", s.date >= "2024-01-01")]:
            t = s[mk].dropna(subset=[b]).groupby("date").apply(lambda x: x.f.rank().corr(x[b].rank()) if len(x) > 40 else np.nan).dropna()
            if lab.startswith("선행"): t = t.iloc[::6]                       # 비중첩
            rows.append(dict(규모=sz, 종류=lab, 기간=per, 표본월=len(t), IC=t.mean(), t=t.mean()/t.std(ddof=1)*np.sqrt(len(t))))
print(pd.DataFrame(rows).round(3).to_string())
# 대형 30종목 개별 시계열 (월 샘플)
top = df[df.date == df.date.max()].nlargest(30, "market_cap").code
rr = []
for c in top:
    s = m[m.code == c]
    if len(s) < 60: continue
    s2 = s.dropna(subset=["x"])
    rr.append(dict(code=c, 월수=len(s), 동행상관=s.f.corr(s.r), 선행상관_120=s2.f.corr(s2.x)))
rr = pd.DataFrame(rr); print(rr.round(2).to_string(index=False))
print("대형30 동행상관 중앙값", rr.동행상관.median().round(2), "양(+) 종목", (rr.동행상관 > 0).sum(), "/", len(rr),
      "| 선행 중앙값", rr.선행상관_120.median().round(2), "양(+)", (rr.선행상관_120 > 0).sum())
