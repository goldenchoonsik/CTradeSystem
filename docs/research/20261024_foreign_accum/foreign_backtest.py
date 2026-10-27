"""사전등록 규칙 그대로의 일별 평가 매매 백테스트 (신규 파라미터 없음, 기술 통계). 2016-12 ~ 현재.
  .venv/bin/python docs/research/20261024_foreign_accum/foreign_backtest.py"""
import os, sys, math, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived"); COST = 0.0037
sys.argv = ["x", "track"]
src = open(os.path.join(HERE, "foreign_pick.py"), encoding="utf-8").read().rsplit('\n{"pick"', 1)[0]
exec(compile(src, "foreign_pick", "exec"))                      # P,F,C,pick(t), d
px = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"]).pivot(index="date", columns="code", values="close").sort_index()
idx = pd.read_parquet(f"{D}/index_1d.parquet"); kospi = idx[idx.code == "U001"].set_index("date").close.sort_index()
days = px.index[px.index >= "2016-12-01"]

def segment(t, end, codes):
    """t월 마지막 거래일 종가 매수 → end(다음 형성일 또는 마지막 날)까지 동일가중 매수후보유. 폐지는 마지막 종가 유지."""
    t0 = px.index[px.index.to_period("M") == t].max(); seg = px.loc[t0:end, codes].ffill()
    return (seg / seg.iloc[0]).mean(axis=1)

curves = {"전략": [], "상위200": []}; rebs = [pd.Period(f"{y}-12", "M") for y in range(2016, 2026)]
for i, t in enumerate(rebs):
    nxt = rebs[i + 1] if i + 1 < len(rebs) else None
    end = px.index[px.index.to_period("M") == nxt].max() if nxt is not None else px.index.max()
    sel, uni = pick(t)
    for k, codes in (("전략", sel.index), ("상위200", uni)):
        s = segment(t, end, list(codes))
        if k == "전략": s = s * (1 - COST)                       # 매 형성일 전량 회전 가정(보수적)
        curves[k].append(s.iloc[1:] if curves[k] else s)
eq = {}
for k, segs in curves.items():
    lvl, out = 1.0, []
    for s in segs:
        r = s / s.iloc[0] if False else s
        out.append(r * lvl); lvl = out[-1].iloc[-1]
    eq[k] = pd.concat(out)
E=pd.DataFrame(eq); E["코스피"] = kospi.reindex(E.index).ffill(); E["코스피"] /= E["코스피"].iloc[0]
def stats(s):
    r = s.pct_change().dropna(); y = (s.index[-1] - s.index[0]).days / 365.25
    return dict(최종=s.iloc[-1], CAGR=(s.iloc[-1] ** (1 / y) - 1) * 100, 변동성=r.std() * 252 ** .5 * 100, 샤프=(r.mean() * 252) / (r.std() * 252 ** .5), MDD=((s / s.cummax()) - 1).min() * 100)
print("기간", E.index[0].date(), "~", E.index[-1].date()); print(pd.DataFrame({k: stats(E[k]) for k in E}).round(2).T.to_string())
print("\n연도별 수익(%)"); Y = E.resample("YE").last(); Y.loc[E.index[0] - pd.Timedelta(days=1)] = 1; Y = Y.sort_index().pct_change().dropna() * 100
Y.index = Y.index.year; Y["초과"] = Y["전략"] - Y["상위200"]; print(Y.round(1).to_string())
for lo, hi, nm in (("2016-12", "2022-12", "개발(~2023-01)"), ("2023-01", "2026-12", "개발 이후(봉인 포함)")):
    s = E.loc[lo:hi]; print(nm, {k: round(stats(s[k] / s[k].iloc[0])["CAGR"], 1) for k in ("전략", "상위200", "코스피")})
print("\n전략/상위200 상대곡선 최대 하락(%):", round(((E["전략"] / E["상위200"]) / (E["전략"] / E["상위200"]).cummax() - 1).min() * 100, 1))
E.to_csv(os.path.join(HERE, "foreign_backtest_equity.csv"))
