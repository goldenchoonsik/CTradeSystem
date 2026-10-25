"""외국인 현물 순매도·큰 매도일의 프로그램매매(차익=선물 연계, 비차익=바스켓) — KIS 프로그램매매 종합현황(일별, 코스피) + KRX 코스피 투자자별.
차익 순매수 = 차익 매수 − 차익 매도 (음수 = 현물 매도·선물 매수 우세 = 역차익). 단위 백만원 → 전체 거래대금 대비 %.
  .venv/bin/python docs/research/20261024_foreign_accum/program_vs_foreign.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
pg = pd.read_csv(f"{ROOT}/DataVault/staging/kr_investor_daily/KSP_program_daily.csv", dtype=str); pg["date"] = pd.to_datetime(pg.stck_bsop_date); pg = pg.set_index("date").sort_index()
num = lambda c: pg[c].astype(float) * 1e6
P = pd.DataFrame({"arb_b": num("arbt_smtn_shnu_tr_pbmn"), "arb_s": num("arbt_smtn_seln_tr_pbmn"), "nab_b": num("nabt_smtn_shnu_tr_pbmn"), "nab_s": num("nabt_smtn_seln_tr_pbmn"), "who_b": num("whol_smtn_shnu_tr_pbmn"), "who_s": num("whol_smtn_seln_tr_pbmn")})
def ld(n):
    x = pd.read_csv(f"{ROOT}/KRX/{n}.csv", encoding="cp949"); x["일자"] = pd.to_datetime(x["일자"], format="%Y/%m/%d"); return x.set_index("일자").sort_index()
sb, ss = ld("거래대금_매수"), ld("거래대금_매도"); tot = sb["전체"]
ix = pd.read_parquet(f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"); k = ix[ix.code == "U001"].set_index("date").sort_index().close
d = pd.DataFrame({"tot": tot, "f": (sb["외국인"] + sb["기타외국인"] - ss["외국인"] - ss["기타외국인"]), "ret": k.pct_change() * 100}).join(P).dropna(subset=["arb_b", "f", "ret"])
d["arb"] = (d.arb_b - d.arb_s) / d.tot * 100; d["nab"] = (d.nab_b - d.nab_s) / d.tot * 100; d["who"] = (d.who_b - d.who_s) / d.tot * 100; d["fr"] = d.f / d.tot * 100
d["prog_share"] = (d.who_b + d.who_s) / (2 * d.tot) * 100; d["arb_share"] = (d.arb_b + d.arb_s) / (2 * d.tot) * 100
d = d[d.index >= "2013-01-01"]; x = d[d.index >= "2016-01-01"]
print("일수", len(d), d.index.min().date(), d.index.max().date(), "| 프로그램 거래 비중(2016~) 평균 %.1f%% (차익 %.1f%%)" % (x.prog_share.mean(), x.arb_share.mean()))
print("연도별 프로그램 비중/차익 비중(%):", {y: (round(g.prog_share.mean(), 1), round(g.arb_share.mean(), 1)) for y, g in d.groupby(d.index.year) if y >= 2013})
print("\n== 상관 (2016~) ==")
for c, n in (("arb", "차익 순매수"), ("nab", "비차익 순매수"), ("who", "프로그램 전체 순매수"), ("fr", "외국인 현물 순매수")): print(f"{n}~같은날 KOSPI 수익 {x[c].corr(x.ret):.3f}")
print("외국인 현물 순매수 ~ 차익 %.3f / 비차익 %.3f / 전체 프로그램 %.3f" % (x.fr.corr(x.arb), x.fr.corr(x.nab), x.fr.corr(x.who)))
lo = x[x.fr <= x.fr.quantile(.05)]; hi = x[x.fr >= x.fr.quantile(.95)]; mid = x.drop(lo.index).drop(hi.index)
t = pd.DataFrame({"전체평균": x[["fr", "arb", "nab", "who", "ret"]].mean(), "외국인 큰 매도일(하위5%)": lo[["fr", "arb", "nab", "who", "ret"]].mean(), "외국인 큰 매수일(상위5%)": hi[["fr", "arb", "nab", "who", "ret"]].mean()})
t.index = ["외국인 현물 순매수", "차익 순매수", "비차익 순매수", "프로그램 전체", "KOSPI 수익%"]; print("\n== 외국인 큰 매도·매수일 (전체 거래대금 대비 %) =="); print(t.round(2).to_string())
print("큰 매도일 중 차익 순매수 음수(역차익 우세) 비율 %.0f%% (전체 %.0f%%), 비차익 음수 %.0f%% (전체 %.0f%%)" % ((lo.arb < 0).mean() * 100, (x.arb < 0).mean() * 100, (lo.nab < 0).mean() * 100, (x.nab < 0).mean() * 100))
print("큰 매도일 차익 매도 비중: 차익 매도/전체 거래대금 %.2f%% (전체 평균 %.2f%%), 외국인 순매도의 몇 %%가 차익 순매도인가 %.1f%%" % ((lo.arb_s / lo.tot * 100).mean(), (x.arb_s / x.tot * 100).mean(), (-lo.arb.mean() / -lo.fr.mean() * 100)))
big = x[x.arb <= x.arb.quantile(.05)]; print("\n역차익 상위 5%% 일(n=%d): 차익 순매수 %.2f%%, 외국인 현물 %.2f%%, KOSPI %.2f%% (하락 %.0f%%)" % (len(big), big.arb.mean(), big.fr.mean(), big.ret.mean(), (big.ret < 0).mean() * 100))
for lab, m in (("2016~19", (x.index < "2020-01-01")), ("2020~22", (x.index >= "2020-01-01") & (x.index < "2023-01-01")), ("2023~", x.index >= "2023-01-01")):
    z = x[m]; q = z[z.fr <= z.fr.quantile(.05)]; print(lab, "큰 매도일 차익 %.2f%% 비차익 %.2f%% (전체 평균 차익 %.2f 비차익 %.2f)" % (q.arb.mean(), q.nab.mean(), z.arb.mean(), z.nab.mean()))
d.to_csv(os.path.join(os.path.dirname(__file__), "program_vs_foreign_daily.csv"))
