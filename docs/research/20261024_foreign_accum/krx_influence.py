"""KRX 일별 총 매수·매도(전체 시장) → 주체별 영향력: 같은날 지수 수익 설명력, 큰 매도일, 선후, 외국인 비중과 변동성. 지수=KOSPI(U001, index_1d).
  .venv/bin/python docs/research/20261024_foreign_accum/krx_influence.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
def ld(n):
    x = pd.read_csv(f"{ROOT}/KRX/{n}.csv", encoding="cp949"); x["일자"] = pd.to_datetime(x["일자"], format="%Y/%m/%d"); x = x.set_index("일자").sort_index()
    if "기관 합계" not in x:   # 상세보기 파일(코스피): 기관 = 7개 세부 합, 외국인 합계 = 외국인 + 기타외국인
        x["기관 합계"] = x[["금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금 등"]].sum(axis=1, min_count=1)
        x["외국인 합계"] = x[["외국인", "기타외국인"]].sum(axis=1, min_count=1)
    return x
b, s = ld("거래대금_매수"), ld("거래대금_매도"); tot = b["전체"]
ix = pd.read_parquet(f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"); k = ix[ix.code == "U001"].set_index("date").sort_index().close
d = pd.DataFrame({"ret": k.pct_change() * 100})
for c, n in (("외국인 합계", "f"), ("기관 합계", "i"), ("개인", "p"), ("기타법인", "e")):
    d[n] = (b[c] - s[c]) / tot * 100; d[n + "_gross"] = (b[c] + s[c]) / (2 * tot) * 100
d["tot"] = tot / 1e12; d = d.dropna(subset=["ret", "f", "i", "p"]); print("지수 일수", len(d), d.index.min().date(), d.index.max().date())
def r2(df, xs):
    X = np.column_stack([np.ones(len(df))] + [df[c] for c in xs]); bt = np.linalg.lstsq(X, df.ret.values, rcond=None)[0]; e = df.ret.values - X @ bt
    return 1 - e.var() / df.ret.var(), bt[1:]
print("\n== 같은날 KOSPI 수익 설명력 R² (단독 / 외국인+기관 동시) ==")
rows = []
for lab, m in (("1999~2026", d.index >= "1999-01-01"), ("2010~", d.index >= "2010-01-01"), ("2016~", d.index >= "2016-01-01"), ("2024~", d.index >= "2024-01-01"), ("2026", d.index >= "2026-01-01")):
    x = d[m]; ra = {c: r2(x, [c])[0] for c in ("f", "i", "p")}; rb, bt = r2(x, ["f", "i"])
    rows.append(dict(기간=lab, 일수=len(x), 외국인=ra["f"], 기관=ra["i"], 개인=ra["p"], 외인기관동시=rb, 외국인계수=bt[0], 기관계수=bt[1]))
print(pd.DataFrame(rows).round(3).to_string(index=False))
print("\n== 연도별 R²: 외국인 / 기관 / 개인 ==")
print(pd.DataFrame({y: {c: r2(x, [c])[0] for c in ("f", "i", "p")} for y, x in d.groupby(d.index.year) if len(x) > 100}).T.round(2).loc[2005:].to_string())
d["dec"] = pd.qcut(d.f[d.index >= "2016-01-01"], 10, labels=False) if False else np.nan
x = d[d.index >= "2016-01-01"].copy(); x["dec"] = pd.qcut(x.f, 10, labels=False)
for h in (1, 5, 20): x[f"n{h}"] = (k.shift(-h) / k - 1).reindex(x.index) * 100
t = x.groupby("dec").agg(일수=("ret", "size"), 외국인순매수=("f", "mean"), 기관=("i", "mean"), 개인=("p", "mean"), 외국인비중=("f_gross", "mean"), 같은날=("ret", "mean"), 하락일=("ret", lambda z: (z < 0).mean() * 100), 다음날=("n1", "mean"), 다음20일=("n20", "mean"))
print("\n== 외국인 순매수 10분위 (2016~, 전체시장 거래대금 대비 %) =="); print(t.round(2).to_string())
lo = x[x.f <= x.f.quantile(.05)]; print(f"매도 상위 5% n={len(lo)}: 외국인 순매수 {lo.f.mean():.2f}% 기관 {lo.i.mean():.2f}% 개인 {lo.p.mean():.2f}% | 같은날 {lo.ret.mean():.2f}% 하락 {(lo.ret<0).mean()*100:.0f}% | 외국인 거래비중 {lo.f_gross.mean():.1f}% (전체 평균 {x.f_gross.mean():.1f}%)")
print("\n== 선후 (1999~): 흐름(t)~수익(t+1) / 수익(t)~흐름(t+1) ==")
for c in ("f", "i", "p"): print(c, "%.3f / %.3f" % (d[c].corr(d.ret.shift(-1)), d.ret.corr(d[c].shift(-1))), "| 같은날 %.3f" % d[c].corr(d.ret))
print("\n== 외국인 거래 비중과 변동성 (2016~) ==")
x["q"] = pd.qcut(x.f_gross, 5, labels=False); print(x.groupby("q").agg(외국인비중=("f_gross", "mean"), 절대수익=("ret", lambda z: z.abs().mean()), 거래대금조=("tot", "mean")).round(2).to_string())
print("상관: 외국인 비중~|수익| %.3f, 거래대금~|수익| %.3f" % (x.f_gross.corr(x.ret.abs()), x.tot.corr(x.ret.abs())))
x["abs_f"] = x.f.abs(); print("|외국인 순매수|~|수익| %.3f, |기관| %.3f, |개인| %.3f" % (x.abs_f.corr(x.ret.abs()), x.i.abs().corr(x.ret.abs()), x.p.abs().corr(x.ret.abs())))
d.to_csv(os.path.join(os.path.dirname(__file__), "krx_influence_daily.csv"))
