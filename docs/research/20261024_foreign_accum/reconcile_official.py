"""내 일집계(보유수량 변화×수정종가) vs KIS 공식 일별 외국인 순매수(KSP+KSQ, 백만원). 
  .venv/bin/python docs/research/20261024_foreign_accum/reconcile_official.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
V = os.path.join(ROOT, "DataVault", "staging"); D = f"{V}/kr_equity/derived"
off = None
for m, c in (("KSP", "0001"), ("KSQ", "1001")):
    x = pd.read_csv(f"{V}/kr_investor_daily/{m}_{c}_daily.csv", dtype=str)
    x["date"] = pd.to_datetime(x.stck_bsop_date)
    x = x.set_index("date")[["frgn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn", "prsn_ntby_tr_pbmn"]].astype(float) * 1e6   # 원
    x.columns = [f"{m}_{k[:4]}" for k in x.columns]; off = x if off is None else off.join(x, how="outer")
off["off_f"] = off.KSP_frgn + off.KSQ_frgn; off["off_i"] = off.KSP_orgn + off.KSQ_orgn
e = pd.read_parquet(f"{D}/daily_ext.parquet", columns=["code","date","shares_listed","foreign_hold_qty","inst_net_qty"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close","volume","factor"]).dropna(subset=["close"])
d = e.merge(p, on=["code","date"]).sort_values(["code","date"]); g = d.groupby("code")
dq = g.foreign_hold_qty.diff()
bad = (g.shares_listed.diff().fillna(0) != 0) | (g.factor.diff().fillna(0) != 0) | dq.isna()
bad2 = bad | (dq.abs() > d.volume + 1)
d["f_all"] = np.where(dq.isna(), 0.0, dq * d.close); d["f_ok"] = np.where(bad2, 0.0, dq * d.close)
d["i"] = np.where(d.inst_net_qty.isna(), 0.0, d.inst_net_qty * d.close)
mine = d.groupby("date").agg(m_all=("f_all", "sum"), m_ok=("f_ok", "sum"), m_i=("i", "sum"), n=("code", "size"))
j = off.join(mine, how="inner"); j = j[(j.index >= "2016-01-01") & (j.index <= "2026-10-08")].dropna()
print("대조 일수", len(j), j.index.min().date(), j.index.max().date())
for c in ("m_all", "m_ok"):
    print(f"{c}: 상관 {j.off_f.corr(j[c]):.3f}  회귀기울기 {np.polyfit(j[c], j.off_f, 1)[0]:.3f}  부호일치 {((j.off_f > 0) == (j[c] > 0)).mean()*100:.1f}%  연합 공식 {j.off_f.sum()/1e12:.0f}조 vs 내 {j[c].sum()/1e12:.0f}조")
print("기관: 상관 %.3f 기울기 %.3f" % (j.off_i.corr(j.m_i), np.polyfit(j.m_i, j.off_i, 1)[0]))
y = pd.DataFrame({"공식_조": j.off_f.groupby(j.index.year).sum() / 1e12, "내집계_조": j.m_ok.groupby(j.index.year).sum() / 1e12})
y["차이_조"] = y.내집계_조 - y.공식_조; y["일상관"] = j.groupby(j.index.year).apply(lambda x: x.off_f.corr(x.m_ok)); print(y.round(2).to_string())
j["gap"] = j.m_ok - j.off_f; j["relgap"] = j.gap.abs() / j.off_f.abs().clip(lower=2e11)
big = j.reindex(j.gap.abs().sort_values(ascending=False).index).head(8)[["off_f", "m_ok", "gap"]] / 1e12
print("\n차이 큰 날 상위 8 (조원):"); print(big.round(2).to_string())
print("절대차 중앙값 %.2f조, 공식 절대값 중앙값 %.2f조; 일 오차 비율(|차|/|공식|) 중앙 %.0f%%" % (j.gap.abs().median()/1e12, j.off_f.abs().median()/1e12, (j.gap.abs()/j.off_f.abs().clip(lower=5e10)).median()*100))
# 최근 날짜 확인
print("\n공식 마지막 5일 외국인(조):", (off.off_f.tail(5)/1e12).round(2).to_dict()); print("내 집계 마지막 5일(조):", (mine.m_ok.tail(5)/1e12).round(2).to_dict())
j.to_csv(os.path.join(os.path.dirname(__file__), "reconcile_official.csv"))
