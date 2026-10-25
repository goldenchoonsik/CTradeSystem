"""KRX 정보데이터시스템 투자자별 거래실적(일별, 원) — 총 매수·매도 거래대금으로 주체별 거래 비중과 지수 영향. 파일: KRX/거래대금_매수.csv·거래대금_매도.csv (·거래량_*).
  .venv/bin/python docs/research/20261024_foreign_accum/krx_gross_share.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
K = os.path.join(ROOT, "KRX"); V = os.path.join(ROOT, "DataVault", "staging")
def load(n):
    x = pd.read_csv(f"{K}/{n}.csv", encoding="cp949", thousands=","); x["일자"] = pd.to_datetime(x["일자"], format="%Y/%m/%d")
    x = x.set_index("일자").sort_index().astype(float)
    if "기관 합계" not in x:   # 상세보기 파일(코스피): 기관 = 7개 세부 합, 외국인 합계 = 외국인 + 기타외국인
        x["기관 합계"] = x[["금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금 등"]].sum(axis=1, min_count=1)
        x["외국인 합계"] = x[["외국인", "기타외국인"]].sum(axis=1, min_count=1)
    return x
b, s = load("거래대금_매수"), load("거래대금_매도")
print("범위", b.index.min().date(), b.index.max().date(), len(b), "일 | 매수·매도 전체 일치:", np.allclose(b["전체"], s["전체"]))
cols = ["기관 합계", "기타법인", "개인", "외국인 합계"]
print("주체 합 = 전체 (매수) 최대 오차 비율: %.4f" % ((b[cols].sum(axis=1) - b["전체"]).abs() / b["전체"]).max())
tot = b["전체"]; net = (b[cols] - s[cols])
# 검증: 순매수 vs KIS 공식(KSP), 거래대금 vs U001
x = pd.read_csv(f"{V}/kr_investor_daily/KSP_0001_daily.csv", dtype=str); x["date"] = pd.to_datetime(x.stck_bsop_date); x = x.set_index("date").sort_index()
kf = x.frgn_ntby_tr_pbmn.astype(float) * 1e6; kp = x.prsn_ntby_tr_pbmn.astype(float) * 1e6
j = pd.concat([net["외국인 합계"].rename("krx"), kf.rename("kis")], axis=1).dropna()
print("외국인 순매수 KRX vs KIS(KSP) %d일: 상관 %.4f, 평균 절대차 %.3f조, 최대 절대차 %.2f조" % (len(j), j.krx.corr(j.kis), (j.krx - j.kis).abs().mean() / 1e12, (j.krx - j.kis).abs().max() / 1e12))
ix = pd.read_parquet(f"{V}/kr_equity/derived/index_1d.parquet"); k = ix[ix.code == "U001"].set_index("date").sort_index()
jj = pd.concat([tot.rename("krx"), k.value.rename("u001")], axis=1).dropna(); r = jj.krx / jj.u001
print("전체 거래대금 KRX/U001 비율 중앙 %.2f (10~90%%: %.2f~%.2f), 최근 %s" % (r.median(), r.quantile(.1), r.quantile(.9), r.tail(3).round(2).tolist()))
# 연도별 비중
sh = pd.DataFrame({c: (b[c] + s[c]) / (2 * tot) * 100 for c in cols}); sh["외국인_매수"] = b["외국인 합계"] / tot * 100; sh["외국인_매도"] = s["외국인 합계"] / tot * 100
y = sh.groupby(sh.index.year).mean(); y["거래대금_일평균_조"] = tot.groupby(tot.index.year).mean() / 1e12
print("\n연도별 거래대금 비중(%, 매수·매도 평균) — 일평균 비중"); print(y.round(1).loc[2005:].to_string())
yv = pd.DataFrame({c: (b[c] + s[c]).groupby(b.index.year).sum() / (2 * tot.groupby(tot.index.year).sum()) * 100 for c in cols})
print("\n연도별 거래대금 가중 비중(%)"); print(yv.round(1).loc[2015:].to_string())
sh.to_csv(os.path.join(os.path.dirname(__file__), "krx_share_daily.csv"))
net.div(tot, axis=0).mul(100).to_csv(os.path.join(os.path.dirname(__file__), "krx_net_ratio_daily.csv"))
