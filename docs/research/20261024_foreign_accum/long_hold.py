"""장기(5·10년) 외국인 지분율 변화 vs 주가 — 현재 시총 상위(사후 선택)와 시작 시점 시총 상위(사전 선택)를 구분.
  .venv/bin/python docs/research/20261024_foreign_accum/long_hold.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived")
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code","date","market_cap","foreign_hold_pct","adv_20"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close"])
d = g.merge(p, on=["code","date"]).dropna(subset=["close"])
end = d.date.max(); print("끝", end.date())
def at(dt):
    x = d[d.date <= dt].sort_values("date").groupby("code").tail(1); return x[x.date >= dt - pd.Timedelta(days=7)].set_index("code")
E_ = at(end)
rows = []
for yrs in (5, 10):
    S = at(end - pd.DateOffset(years=yrs))
    j = S.join(E_, lsuffix="0", rsuffix="1", how="inner").dropna(subset=["foreign_hold_pct0","foreign_hold_pct1","close0","close1","market_cap0"])
    j = j[j.adv_200 >= 5e8] if "adv_200" in j else j
    j["ret"] = j.close1 / j.close0 - 1; j["dp"] = j.foreign_hold_pct1 - j.foreign_hold_pct0
    for lab, sel in [("시작시점 시총 상위50(사전)", j.nlargest(50, "market_cap0")), ("현재 시총 상위50(사후)", j.nlargest(50, "market_cap1")), ("전체 생존(시총 상위300 시작시점)", j.nlargest(300, "market_cap0"))]:
        up = sel[sel.dp > 0]; dn = sel[sel.dp <= 0]
        rows.append(dict(기간=f"{yrs}년", 집단=lab, n=len(sel), 지분율증가_비율=(sel.dp > 0).mean(), 지분율증가_수익중앙=up.ret.median(), 지분율감소_수익중앙=dn.ret.median(),
                         상관_dp_ret=sel.dp.corr(sel.ret), 순위상관=sel.dp.rank().corr(sel.ret.rank()), 상승종목비율=(sel.ret > 0).mean()))
r = pd.DataFrame(rows); pd.set_option("display.width", 250); print(r.round(2).to_string(index=False))
S = at(end - pd.DateOffset(years=5)); j = S.join(E_, lsuffix="0", rsuffix="1", how="inner")
j["ret"] = j.close1 / j.close0 - 1; j["dp"] = j.foreign_hold_pct1 - j.foreign_hold_pct0
t = j.nlargest(30, "market_cap1")[["foreign_hold_pct0","foreign_hold_pct1","dp","ret"]]
print("\n현재 시총 상위 30 (5년): 지분율 시작→현재, 변화%p, 수익")
print(t.round(2).to_string())
