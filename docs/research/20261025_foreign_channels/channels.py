"""외국인 자금 유입 경로 탐색: 거시(환율·미국·달러·VIX)·달력(MSCI 리밸런싱·만기)·지속성.
탐색·기술 통계(사전등록 아님). 코스피 주체별 일별 순매수(KIS) 2012-02~2026-10-23.
"""
import numpy as np
import pandas as pd

S = "/home/kuzimo/projects/CTradeSystem/DataVault/staging"
ksp = pd.read_csv(f"{S}/kr_investor_daily/KSP_0001_daily.csv", dtype={"stck_bsop_date": str})
ksp["date"] = pd.to_datetime(ksp["stck_bsop_date"])
ksp = ksp.set_index("date").sort_index()
flow = ksp["frgn_ntby_tr_pbmn"] / 1e6  # 조원
reg = ksp["frgn_reg_ntby_pbmn"] / 1e6
nreg = ksp["frgn_nreg_ntby_pbmn"] / 1e6
kospi_r = ksp["bstp_nmix_prdy_ctrt"] / 100


def ser(name):
    d = pd.read_csv(f"{S}/global_macro/series/{name}.csv", parse_dates=["date"]).set_index("date")["value"]
    return d[~d.index.duplicated()].sort_index()


spx, dxy, vix, fx = ser("us_spx"), ser("w_dxy"), ser("w_vix"), ser("kr_usdkrw")
ust = ser("us_10y")

idx = flow.index
def on_kr_days(s):  # 한국 거래일 기준으로 정렬(미국은 직전 영업일 종가 = 한국 개장 전)
    return s.reindex(s.index.union(idx)).ffill().reindex(idx)

# 미국/달러/VIX: 한국 t일 개장 전에 확정된 직전 값의 변화
def prior_change(s, log=True):
    a = on_kr_days(s)
    return (np.log(a).diff() if log else a.diff())

df = pd.DataFrame({
    "flow": flow, "kospi": kospi_r,
    "spx_prev": prior_change(spx),            # 한국 t일 이전에 마감한 미국 수익(전일 미국장)
    "dxy_prev": prior_change(dxy),
    "dvix_prev": prior_change(vix, log=False),
    "fx_t": prior_change(fx),                 # 원/달러 당일 변화(+ = 원화 약세)
    "y10_prev": prior_change(ust, log=False),
}).dropna()
# 표준화(연도별 변동성 변화 영향 줄이기 위해 252일 롤링 표준편차로 나눔)
z = df.copy()
for c in z.columns:
    z[c] = df[c] / df[c].rolling(250, min_periods=60).std()
z = z.dropna()

def ols(y, X):
    X1 = np.column_stack([np.ones(len(X)), X.values])
    b, *_ = np.linalg.lstsq(X1, y.values, rcond=None)
    e = y.values - X1 @ b
    r2 = 1 - e.var() / y.values.var()
    return b[1:], r2

print("=== 1. 일별 외국인 순매수(표준화) 설명 — 변수별 상관 및 다중회귀 R² ===")
for lab, sub in [("전체 2013~", z), ("2013~19", z[:"2019"]), ("2020~23", z["2020":"2023"]), ("2024~", z["2024":])]:
    cors = {c: sub["flow"].corr(sub[c]) for c in ["kospi", "spx_prev", "dxy_prev", "dvix_prev", "fx_t", "y10_prev"]}
    _, r_k = ols(sub["flow"], sub[["kospi"]])
    _, r_g = ols(sub["flow"], sub[["spx_prev", "dxy_prev", "dvix_prev", "y10_prev"]])
    _, r_f = ols(sub["flow"], sub[["fx_t"]])
    _, r_all = ols(sub["flow"], sub[["kospi", "spx_prev", "dxy_prev", "dvix_prev", "y10_prev", "fx_t"]])
    print(f"{lab:9s} n={len(sub)} " + " ".join(f"{k}={v:+.2f}" for k, v in cors.items()))
    print(f"   R²: 코스피 당일 {r_k:.3f} | 미국·달러·VIX·금리(전일) {r_g:.3f} | 원화 당일 {r_f:.3f} | 전부 {r_all:.3f}")

# 코스피 당일 수익을 통제한 뒤 글로벌 요인의 순수 기여
print("\n=== 2. 코스피 당일 수익 통제 후 글로벌 요인 편계수(표준화, 전체) ===")
b, r2 = ols(z["flow"], z[["kospi", "spx_prev", "dxy_prev", "dvix_prev", "y10_prev", "fx_t"]])
for n, v in zip(["kospi", "spx_prev", "dxy_prev", "dvix_prev", "y10_prev", "fx_t"], b):
    print(f"  {n:10s} {v:+.3f}")
print(f"  R² {r2:.3f}")

print("\n=== 3. 월 단위 (월합 외국인 순매수 조원 vs 월 변화) ===")
m = pd.DataFrame({"flow": flow.resample("ME").sum()})
for n, s, lg in [("fx", fx, True), ("dxy", dxy, True), ("spx", spx, True), ("vix", vix, False), ("y10", ust, False)]:
    mm = s.resample("ME").last()
    m[n] = np.log(mm).diff() if lg else mm.diff()
m["kospi"] = np.log(ser("kr_kospi").resample("ME").last()).diff()
m = m.dropna()
for lab, sub in [("전체", m), ("2013~19", m[:"2019"]), ("2020~", m["2020":])]:
    c0 = {k: sub["flow"].corr(sub[k]) for k in ["kospi", "fx", "dxy", "spx", "vix", "y10"]}
    c1 = {k: sub["flow"].corr(sub[k].shift(-1)) for k in ["kospi", "fx", "dxy", "spx", "vix"]}
    c_1 = {k: sub["flow"].corr(sub[k].shift(1)) for k in ["kospi", "fx", "dxy", "spx", "vix"]}
    print(f"{lab} n={len(sub)} 동월 " + " ".join(f"{k}={v:+.2f}" for k, v in c0.items()))
    print(f"      다음달 변화 " + " ".join(f"{k}={v:+.2f}" for k, v in c1.items()) +
          "  | 전월 변화→이번달 흐름 " + " ".join(f"{k}={v:+.2f}" for k, v in c_1.items()))

print("\n=== 4. 달력: 규모(|순매수| / 250일 롤링 중앙값) ===")
absz = flow.abs() / flow.abs().rolling(250, min_periods=60).median()
d = pd.DataFrame({"flow": flow, "absz": absz}).dropna()
d["ym"] = d.index.to_period("M")
last_bd = d.groupby("ym").apply(lambda g: g.index.max())
d["lastbd"] = d.index.isin(last_bd.values)
d["month"] = d.index.month
# MSCI SAIR(5·11월 말), 분기 리뷰(2·8월 말): 월 마지막 거래일
for lab, mask in [("5·11월 말일(MSCI 반기)", d.lastbd & d.month.isin([5, 11])),
                  ("2·8월 말일(MSCI 분기)", d.lastbd & d.month.isin([2, 8])),
                  ("그 외 월말", d.lastbd & ~d.month.isin([2, 5, 8, 11])),
                  ("월말 아닌 날", ~d.lastbd)]:
    s = d[mask]
    print(f"  {lab:24s} n={len(s):4d} 평균 순매수 {s.flow.mean():+.3f}조 | 규모 {s.absz.mean():.2f}배 | 순매수>0 비율 {(s.flow>0).mean():.0%}")
# 옵션/선물 동시만기: 3·6·9·12월 둘째 목요일
def second_thu(ix):
    out = []
    for (y, mth), g in pd.Series(ix, index=ix).groupby([ix.year, ix.month]):
        thu = g[g.index.dayofweek == 3]
        if len(thu) >= 2: out.append(thu.index[1])
    return pd.DatetimeIndex(out)
st = second_thu(d.index)
quad = st[st.month.isin([3, 6, 9, 12])]
mask = d.index.isin(quad)
print(f"  {'분기 동시만기(3·6·9·12 둘째 목)':24s} n={mask.sum():4d} 평균 순매수 {d[mask].flow.mean():+.3f}조 | 규모 {d[mask].absz.mean():.2f}배")
mask2 = d.index.isin(st[~st.month.isin([3, 6, 9, 12])])
print(f"  {'월간 옵션 만기(그 외 둘째 목)':24s} n={mask2.sum():4d} 평균 순매수 {d[mask2].flow.mean():+.3f}조 | 규모 {d[mask2].absz.mean():.2f}배")

print("\n=== 5. 월별 평균 순매수(조원, 연도별 합계 평균) — 계절성 ===")
mo = flow.resample("ME").sum()
mo = mo[:"2025"]  # 완전한 연도만
print(mo.groupby(mo.index.month).agg(["mean", "median", lambda x: (x > 0).mean()]).round(2).rename(columns={"<lambda_0>": "양수비율"}).T.to_string())

print("\n=== 6. 지속성: 일별 외국인 순매수 자기상관 / 5일·20일 합의 다음 구간 상관 ===")
for lag in [1, 2, 3, 5, 10, 20]:
    print(f"  lag{lag}: {flow.autocorr(lag):+.3f}", end="")
print()
f5 = flow.rolling(5).sum(); f20 = flow.rolling(20).sum()
print(f"  직전 5일합 ↔ 다음 5일합 {f5.corr(f5.shift(-5)):+.3f} | 직전 20일합 ↔ 다음 20일합 {f20.corr(f20.shift(-20)):+.3f} | 60일 {flow.rolling(60).sum().corr(flow.rolling(60).sum().shift(-60)):+.3f}")

print("\n=== 7. 등록외국인 vs 기타(비등록) 외국인 ===")
print(f"  누적 합(조원) 등록 {reg.sum():+.1f} / 기타 {nreg.sum():+.1f} | 일별 상관 {reg.corr(nreg):+.2f} | 기타 |순매수| 중앙 {nreg.abs().median():.3f} vs 등록 {reg.abs().median():.3f}")

print("\n=== 8. 장기: 연도별 외국인 순매수(조원)와 연간 원/달러·달러지수 변화 ===")
y = pd.DataFrame({"flow": flow.resample("YE").sum()})
y["fx%"] = (fx.resample("YE").last().pct_change() * 100)
y["dxy%"] = (dxy.resample("YE").last().pct_change() * 100)
y["spx%"] = (spx.resample("YE").last().pct_change() * 100)
y["kospi%"] = (ser("kr_kospi").resample("YE").last().pct_change() * 100)
y.index = y.index.year
y = y.loc[2013:]
print(y.round(1).to_string())
print(f"  상관(연, n={len(y)}): fx {y.flow.corr(y['fx%']):+.2f} dxy {y.flow.corr(y['dxy%']):+.2f} spx {y.flow.corr(y['spx%']):+.2f} kospi {y.flow.corr(y['kospi%']):+.2f}")
