"""KRX 코스피200 선물(6일)·옵션(콜/풋, 1년) 외국인 — 현물과 대조, 지수 관계. 외국인 열과 기타외국인 열은 동일 값(외국인 합계) → 한 번만 계산.
  .venv/bin/python docs/research/20261024_foreign_accum/krx_deriv.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
def ld(n):
    x = pd.read_csv(f"{ROOT}/KRX/{n}.csv", encoding="cp949"); x["일자"] = pd.to_datetime(x["일자"], format="%Y/%m/%d"); return x.set_index("일자").sort_index()
sb, ss = ld("거래대금_매수"), ld("거래대금_매도")
spot = (sb["외국인"] + sb["기타외국인"] - ss["외국인"] - ss["기타외국인"]) / 1e12
fn = ld("코스피200선물_정규_거래대금_순매수"); fb, fs = ld("코스피200선물_정규_거래대금_매수"), ld("코스피200선물_정규_거래대금_매도")
print("외국인 열 = 기타외국인 열 동일:", np.allclose(fb["외국인"], fb["기타외국인"]), "| 선물 주체 합 vs 전체 오차:", ((fb.drop(columns=["전체", "기타외국인"]).sum(axis=1) - fb["전체"]) / fb["전체"]).abs().max().round(4))
ix = pd.read_parquet(f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"); k = ix[ix.code == "U001"].set_index("date").sort_index().close
r = (k.pct_change() * 100)
t = pd.DataFrame({"현물_외국인순매수_조": spot, "선물_외국인순매수_조": fn["외국인"] / 1e12, "선물_개인_조": fn["개인"] / 1e12, "선물_금융투자_조": fn["금융투자"] / 1e12, "선물_투신_조": fn["투신"] / 1e12,
                  "외국인_선물거래_비중%": (fb["외국인"] + fs["외국인"]) / (2 * fb["전체"]) * 100, "KOSPI수익%": r}).reindex(fn.index)
print("\n== 선물 6거래일: 외국인 현물 vs 선물 =="); print(t.round(2).to_string())
cb, cs, pb, ps = ld("코스피200옵션_정규_CALL_거래대금_매수"), ld("코스피200옵션_정규_CALL_거래대금_매도"), ld("코스피200옵션_정규_PUT_거래대금_매수"), ld("코스피200옵션_정규_PUT_거래대금_매도")
o = pd.DataFrame({"call_net": (cb["외국인"] - cs["외국인"]) / 1e9, "put_net": (pb["외국인"] - ps["외국인"]) / 1e9, "call_tot": cb["전체"] / 1e9, "put_tot": pb["전체"] / 1e9,
                  "call_share": (cb["외국인"] + cs["외국인"]) / (2 * cb["전체"]) * 100, "put_share": (pb["외국인"] + ps["외국인"]) / (2 * pb["전체"]) * 100})
o["cp"] = o.call_net - o.put_net; o["spot"] = spot; o["ret"] = r; o = o.dropna(subset=["ret"])
print("\n== 옵션(콜·풋 거래대금=프리미엄, 억원 단위 아님: 십억원) 외국인 %d일 %s~%s ==" % (len(o), o.index.min().date(), o.index.max().date()))
print("외국인 비중(%%) 콜 %.1f 풋 %.1f | 일평균 거래대금(십억) 콜 %.0f 풋 %.0f" % (o.call_share.mean(), o.put_share.mean(), o.call_tot.mean(), o.put_tot.mean()))
print("상관(같은날 KOSPI 수익): 콜순매수 %.3f 풋순매수 %.3f (콜−풋) %.3f | 현물 외국인 순매수 %.3f" % (o.call_net.corr(o.ret), o.put_net.corr(o.ret), o.cp.corr(o.ret), o.spot.corr(o.ret)))
print("상관(다음날 수익): 콜−풋 %.3f 현물 %.3f | 수익→다음날 콜−풋 %.3f" % (o.cp.corr(o.ret.shift(-1)), o.spot.corr(o.ret.shift(-1)), o.ret.corr(o.cp.shift(-1))))
print("외국인 현물 순매수 vs 옵션 콜−풋 상관 %.3f" % o.spot.corr(o.cp))
big = o[o.spot <= o.spot.quantile(.1)]; print("현물 순매도 하위 10%% (%d일): 콜−풋 평균 %.1f십억, 지수 %.2f%%; 나머지 %.1f십억" % (len(big), big.cp.mean(), big.ret.mean(), o.drop(big.index).cp.mean()))
