"""코스피 투자자 세부(금융투자·보험·투신·사모·은행·기타금융·연기금·기타법인·개인·외국인) — 외국인 큰 매도일 상대방, 주체별 선후.
  .venv/bin/python docs/research/20261024_foreign_accum/krx_inst_detail.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
def ld(n):
    x = pd.read_csv(f"{ROOT}/KRX/{n}.csv", encoding="cp949"); x["일자"] = pd.to_datetime(x["일자"], format="%Y/%m/%d"); return x.set_index("일자").sort_index()
b, s = ld("거래대금_매수"), ld("거래대금_매도"); tot = b["전체"]
cols = ["금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금 등", "기타법인", "개인", "외국인", "기타외국인"]
net = (b[cols] - s[cols]).div(tot, axis=0) * 100; net["외국인계"] = net["외국인"] + net["기타외국인"]
gr = ((b[cols] + s[cols]).div(2 * tot, axis=0) * 100)
ix = pd.read_parquet(f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"); k = ix[ix.code == "U001"].set_index("date").sort_index().close
d = net.join(((k.pct_change()) * 100).rename("ret")).dropna(subset=["ret"]); d = d[d.index >= "2013-01-01"]
g = gr.reindex(d.index)
print("2016~ 거래대금 가중 비중(%):", (g[g.index >= "2016-01-01"].mean()).round(1).to_dict())
print("2026 비중(%):", (g[g.index >= "2026-01-01"].mean()).round(1).to_dict())
x = d[d.index >= "2016-01-01"]
lo = x[x.외국인계 <= x.외국인계.quantile(.05)]; hi = x[x.외국인계 >= x.외국인계.quantile(.95)]
t = pd.DataFrame({"평균(전체)": x[cols[:-1] + ["외국인계"]].mean(), "외국인 큰 매도일": lo[cols[:-1] + ["외국인계"]].mean(), "외국인 큰 매수일": hi[cols[:-1] + ["외국인계"]].mean()})
print("\n== 외국인 큰 매도/매수일 주체별 순매수(전체 거래대금 대비 %p) =="); print(t.round(2).to_string())
print("\n== 주체별 같은날 상관(지수수익)·선후: 수익(t)→흐름(t+1), 흐름(t)→수익(t+1) ==")
r = []
for c in cols[:-1] + ["외국인계"]:
    r.append(dict(주체=c, 같은날=x[c].corr(x.ret), **{"수익(t)→흐름(t+1)": x.ret.corr(x[c].shift(-1)), "흐름(t)→수익(t+1)": x[c].corr(x.ret.shift(-1)), "R2": x[c].corr(x.ret) ** 2}))
print(pd.DataFrame(r).round(3).to_string(index=False))
# 기간별 외국인계 R2 vs 연기금
for lab, m in (("2016~19", (x.index >= "2016-01-01") & (x.index < "2020-01-01")), ("2020~22", (x.index >= "2020-01-01") & (x.index < "2023-01-01")), ("2023~", x.index >= "2023-01-01")):
    z = x[m]; print(lab, "R2 외국인계 %.2f 연기금 %.2f 금융투자 %.2f 투신 %.2f 사모 %.2f 개인 %.2f" % tuple(z[c].corr(z.ret) ** 2 for c in ["외국인계", "연기금 등", "금융투자", "투신", "사모", "개인"]))
