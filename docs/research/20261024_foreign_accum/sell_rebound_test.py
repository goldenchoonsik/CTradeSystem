"""외국인 5일 누적 순매도 후 코스피 20일 수익 — 같은 하락폭의 대조군과 비교, 겹치지 않는 사건(20일 간격). 탐색, 사전등록 아님.
  .venv/bin/python docs/research/20261024_foreign_accum/sell_rebound_test.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
d = pd.read_csv(f"{ROOT}/docs/research/20261024_foreign_accum/krx_influence_daily.csv", index_col=0, parse_dates=True)
ix = pd.read_parquet(f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"); k = ix[ix.code == "U001"].set_index("date").sort_index().close
x = pd.DataFrame({"f5": d.f.rolling(5).sum()}).join(d.tot); x["r5"] = (k.pct_change(5) * 100).reindex(x.index)
for h in (5, 20, 60): x[f"n{h}"] = ((k.shift(-h) / k - 1) * 100).reindex(x.index)
x = x[x.index >= "2016-01-01"].dropna(subset=["f5", "r5", "n20"])
ev = x[x.f5 <= x.f5.quantile(.05)]
def nonoverlap(df, gap=20):
    out, last = [], None
    for t in df.index:
        if last is None or (t - last).days >= gap * 1.45: out.append(t); last = t
    return df.loc[out]
e1 = nonoverlap(ev)
# 대조군: 5일 지수수익이 이벤트와 같은 구간(이벤트 r5 5~95% 범위)이면서 외국인 5일 누적이 하위 5%가 아닌 날
lo, hi = ev.r5.quantile(.2), ev.r5.quantile(.8)
ctl = x[(x.r5 >= lo) & (x.r5 <= hi) & (x.f5 > x.f5.quantile(.20))]; c1 = nonoverlap(ctl)
al = nonoverlap(x)
print(f"이벤트: 외국인 5일 누적 순매도 하위 5%  전체 {len(ev)}일 → 겹침 제거 {len(e1)}건")
print(f"대조군: 같은 5일 지수 하락폭 구간·외국인 순매도 약함  전체 {len(ctl)}일 → 겹침 제거 {len(c1)}건")
t = pd.DataFrame({"사건수": [len(e1), len(c1), len(al)], "직전5일 지수%": [e1.r5.mean(), c1.r5.mean(), al.r5.mean()], "다음5일%": [e1.n5.mean(), c1.n5.mean(), al.n5.mean()], "다음20일%": [e1.n20.mean(), c1.n20.mean(), al.n20.mean()], "20일 상승비율%": [(e1.n20 > 0).mean() * 100, (c1.n20 > 0).mean() * 100, (al.n20 > 0).mean() * 100], "다음60일%": [e1.n60.mean(), c1.n60.mean(), al.n60.mean()]}, index=["외국인 큰 순매도 후", "같은 하락 대조군", "아무 날(겹침 제거)"])
print(t.round(2).to_string())
dif = e1.n20.mean() - c1.n20.mean(); se = np.sqrt(e1.n20.var() / len(e1) + c1.n20.var() / len(c1)); print("\n이벤트−대조군 20일 수익 차이 %.2f%%p, t=%.2f" % (dif, dif / se))
ye = e1.groupby(e1.index.year).n20.mean().round(1).to_dict(); print("연도별 이벤트 20일 수익:", ye)
print("이벤트 중 20일 수익 > 0 인 연도 수:", sum(v > 0 for v in ye.values()), "/", len(ye))
