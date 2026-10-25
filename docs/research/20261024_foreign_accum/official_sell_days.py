"""공식(KIS) 코스피 주체별 순매수로 재분석 — 외국인 큰 매도일 vs KOSPI, 개인·기관 대응. 거래대금=index_1d U001 value.
  .venv/bin/python docs/research/20261024_foreign_accum/official_sell_days.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
V = os.path.join(ROOT, "DataVault", "staging")
x = pd.read_csv(f"{V}/kr_investor_daily/KSP_0001_daily.csv", dtype=str); x["date"] = pd.to_datetime(x.stck_bsop_date); x = x.set_index("date")
a = x[["frgn_ntby_tr_pbmn", "prsn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn", "etc_corp_ntby_tr_pbmn"]].astype(float) * 1e6
a["close"] = x.bstp_nmix_prpr.astype(float); a["ret"] = a.close.pct_change() * 100
ix = pd.read_parquet(f"{V}/kr_equity/derived/index_1d.parquet"); k = ix[ix.code == "U001"].set_index("date")
a["v"] = k.value.reindex(a.index)
print("거래대금 중앙값(조):", (a.v.median()/1e12).round(1), " 결측:", a.v.isna().sum())
a = a[(a.index >= "2016-01-01")].dropna(subset=["v", "ret"])
for c, n in (("frgn_ntby_tr_pbmn", "fr"), ("prsn_ntby_tr_pbmn", "pr"), ("orgn_ntby_tr_pbmn", "ir"), ("etc_corp_ntby_tr_pbmn", "er")): a[n] = a[c] / a.v * 100
for h in (1, 5, 20): a[f"n{h}"] = (a.close.shift(-h) / a.close - 1) * 100
print("일수", len(a), a.index.min().date(), a.index.max().date())
print("상관 흐름(t)~수익(t) %.2f  흐름(t)~수익(t+1) %.2f  수익(t)~흐름(t+1) %.2f" % (a.fr.corr(a.ret), a.fr.corr(a.ret.shift(-1)), a.ret.corr(a.fr.shift(-1))))
a["dec"] = pd.qcut(a.fr, 10, labels=False)
t = a.groupby("dec").agg(일수=("ret", "size"), 외국인=("fr", "mean"), 개인=("pr", "mean"), 기관=("ir", "mean"), 기타법인=("er", "mean"), 같은날수익=("ret", "mean"), 하락일비율=("ret", lambda s: (s<0).mean()*100), 다음날=("n1", "mean"), 다음5일=("n5", "mean"), 다음20일=("n20", "mean"))
print(t.round(2).to_string())
for lab, s in (("매도 상위 5%", a[a.fr <= a.fr.quantile(.05)]), ("매수 상위 5%", a[a.fr >= a.fr.quantile(.95)])):
    print(f"{lab} n={len(s)}: 같은날 {s.ret.mean():.2f}% (하락 {(s.ret<0).mean()*100:.0f}%), 다음날 {s.n1.mean():.2f}%, 5일 {s.n5.mean():.2f}%, 20일 {s.n20.mean():.2f}%  외국인 {s.fr.mean():.1f}% 개인 {s.pr.mean():.1f}% 기관 {s.ir.mean():.1f}%")
print("연도별 상관:", a.groupby(a.index.year).apply(lambda z: z.fr.corr(z.ret)).round(2).to_dict())
# 연속 매도(5일 합) → 이후 20일
a["f5"] = a.fr.rolling(5).sum(); s = a[a.f5 <= a.f5.quantile(.05)]
print("5일 누적 순매도 하위 5%% (n=%d): 이 5일 지수수익 %.2f%%, 이후 20일 %.2f%% (전체 %.2f%%)" % (len(s), (a.close.pct_change(5)*100).reindex(s.index).mean(), s.n20.mean(), a.n20.mean()))
