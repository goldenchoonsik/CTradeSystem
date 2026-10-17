"""AAPL: 주가 vs 순이익(EPS) 관계 3가지 — 수준(장기), 변화율(1년), 실적 공시 반응. 재무=SEC companyfacts 최초 공시본, 가격=Yahoo 무수정(분할 환산)."""
import sys, json, numpy as np, pandas as pd
from scipy.stats import spearmanr, pearsonr
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale")
import us_panel as U
cal = pd.bdate_range("2016-01-01", "2026-10-14"); T = "AAPL"
d = U.build(T, cal).dropna(subset=["price", "ttm_eps"])
j = json.load(open(f"{U.SEC}/{T}.json")); q = U.quarters(U.facts(j, U.EPS_TAGS, "USD/shares")); sp = U.splits(T)
q["eps"] = [v / U.S(sp, f, pd.Timestamp("2100-01-01")) for v, f in zip(q.v, q.filed)]   # 현재 주식 기준 분기 EPS
out = {}
# 1) 수준: 로그 주가 vs 로그 TTM EPS
L = np.log(d.price), np.log(d.ttm_eps)
out["level"] = dict(n=len(d), corr_log=float(np.corrcoef(*L)[0, 1]), per_min=float((d.price / d.ttm_eps).min()), per_med=float((d.price / d.ttm_eps).median()), per_max=float((d.price / d.ttm_eps).max()),
    first=str(d.index[0].date()), eps0=float(d.ttm_eps.iloc[0]), eps1=float(d.ttm_eps.iloc[-1]), p0=float(d.price.iloc[0]), p1=float(d.price.iloc[-1]))
# 2) 변화율: 월말마다 TTM EPS 1년 변화 vs (같은 1년 수익률, 이후 12개월 수익률)
m = d.resample("ME").last().dropna(); g = m.ttm_eps / m.ttm_eps.shift(12) - 1; r = m.price / m.price.shift(12) - 1; fr = m.price.shift(-12) / m.price - 1
x = pd.DataFrame({"g": g, "r": r, "fr": fr})
def sp_(a, b):
    z = x[[a, b]].dropna(); rho, p = spearmanr(z[a], z[b]); return dict(n=len(z), rho=float(rho), p=float(p))
out["growth_vs_ret_same"] = sp_("g", "r"); out["growth_vs_ret_next12"] = sp_("g", "fr")
out["growth_vs_ret_same_nonoverlap"] = dict(zip(["n", "rho", "p"], [*(lambda z: (len(z), *map(float, spearmanr(z.g, z.r))))(x.iloc[11::12][["g", "r"]].dropna())]))
# 3) 실적 공시 반응: 분기 EPS 전년비 성장 vs 접수일 전날 종가→다음날 종가 초과수익(QQQ)
qq = pd.read_csv(f"{U.ROOT}/raw/yahoo/candle_1d/QQQ_1d.csv", parse_dates=["timestamp"]).set_index("timestamp").close
px = pd.read_csv(f"{U.ROOT}/raw/yahoo/candle_1d/{T}_1d.csv", parse_dates=["timestamp"]).set_index("timestamp").close
rows = []
for i in range(4, len(q)):
    f = q.filed.iloc[i]; k = px.index.searchsorted(f)   # 접수일 위치
    if k < 1 or k + 1 >= len(px) or q.eps.iloc[i - 4] <= 0: continue
    a, b = px.index[k - 1], px.index[min(k + 1, len(px) - 1)]
    ex = (px[b] / px[a] - 1) - (qq[b] / qq[a] - 1)   # 단순 비조정 가격이나 2일 창이라 분할 영향 없음(분할일 제외)
    rows.append((q.end.iloc[i].date().isoformat(), f.date().isoformat(), q.eps.iloc[i] / q.eps.iloc[i - 4] - 1, ex))
Z = pd.DataFrame(rows, columns=["end", "filed", "eps_yoy", "reaction"]); Z = Z[Z.reaction.abs() < 0.3]   # 분할 오염 제거
rho, p = spearmanr(Z.eps_yoy, Z.reaction); out["earnings_reaction"] = dict(n=len(Z), rho=float(rho), p=float(p), up_mean=float(Z[Z.eps_yoy > Z.eps_yoy.median()].reaction.mean()), dn_mean=float(Z[Z.eps_yoy <= Z.eps_yoy.median()].reaction.mean()))
# 차트용 월별 시계열
ch = m[["price", "ttm_eps"]].copy(); ch["per"] = ch.price / ch.ttm_eps
json.dump(dict(stats=out, series=[[i.strftime("%Y-%m"), round(r.price, 2), round(r.ttm_eps, 3), round(r.per, 1)] for i, r in ch.iterrows()], reactions=Z.round(4).values.tolist()), open("aapl_price_earnings.json", "w"), ensure_ascii=False)
print(json.dumps(out, indent=1, ensure_ascii=False))
