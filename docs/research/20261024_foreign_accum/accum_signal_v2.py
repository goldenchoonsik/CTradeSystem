"""장기 외국인 지분 증가 신호 고도화 — 연속 점수·통제·연 1회 포트폴리오. 탐색(사전등록 아님). 12월 말 형성, 이후 12개월.
점수: dp=36개월 지분율 변화(%p) / cons=월별 상승 비율 / trend=월별 지분율 선형추세 t값(꾸준함+크기) / comb=dp·cons 순위평균.
  .venv/bin/python docs/research/20261024_foreign_accum/accum_signal_v2.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
D = f"{ROOT}/DataVault/staging/kr_equity/derived"
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"]).dropna(subset=["close"])
d = g.merge(p, on=["code", "date"]); d["ym"] = d.date.dt.to_period("M"); m = d.sort_values("date").groupby(["code", "ym"]).tail(1)
P = m.pivot(index="ym", columns="code", values="close"); F = m.pivot(index="ym", columns="code", values="foreign_hold_pct"); C = m.pivot(index="ym", columns="code", values="market_cap")
last = P.index.max()
def slope_t(y):
    n = len(y); x = np.arange(n) - (n - 1) / 2; b = (x * (y - y.mean())).sum() / (x ** 2).sum(); e = y - y.mean() - b * x
    s = np.sqrt((e ** 2).sum() / (n - 2) / (x ** 2).sum()); return b / s if s > 0 else np.nan
def last_valid(col, t, H):
    s = P[col].loc[t:t + H].dropna(); return s.iloc[-1] if len(s) else np.nan
rows = []
for y in range(2016, 2026):
    t = pd.Period(f"{y}-12", "M"); 
    if (t + 12) > last: continue
    top = C.loc[t].dropna().nlargest(200).index
    win = F.loc[t - 36:t, top]
    ok = win.notna().sum() >= 34; top = top[ok.values]
    win = F.loc[t - 36:t, top]
    df = pd.DataFrame({"dp": win.iloc[-1] - win.iloc[0], "cons": (win.diff().iloc[1:] > 0).mean(), "trend": win.apply(lambda c: slope_t(c.dropna().values) if c.notna().sum() > 30 else np.nan),
                       "lvl": win.iloc[-1], "past36": P.loc[t, top] / P.loc[t - 36, top] - 1, "mom12": P.loc[t, top] / P.loc[t - 12, top] - 1, "lm": np.log(C.loc[t, top]), "rank_mc": C.loc[t, top].rank(ascending=False)})
    fwd = pd.Series({c: last_valid(c, t, 12) for c in top}) / P.loc[t, top] - 1
    df["ex"] = fwd - fwd.median(); df["fwd"] = fwd; df["y"] = y; df["code"] = df.index; rows.append(df.dropna(subset=["dp", "cons", "trend", "ex"]))
A = pd.concat(rows); A["comb"] = A.groupby("y").dp.rank(pct=True) + A.groupby("y").cons.rank(pct=True)
print("형성 연도", sorted(A.y.unique()), "| 연도당 종목", A.groupby("y").size().min(), "~", A.groupby("y").size().max())
def ic(sig, df=A):
    r = df.groupby("y").apply(lambda z: z[sig].rank().corr(z.ex.rank())); return r
out = []
for s in ("dp", "cons", "trend", "comb"):
    r = ic(s); q = A.groupby("y").apply(lambda z: z[z[s] >= z[s].quantile(.8)].ex.mean() - z[z[s] <= z[s].quantile(.2)].ex.mean())
    top20 = A.groupby("y").apply(lambda z: z[z[s] >= z[s].quantile(.8)].ex.mean())
    out.append(dict(점수=s, IC평균=r.mean(), IC양수연도=f"{(r > 0).sum()}/{len(r)}", 상위20_초과평균=top20.mean() * 100, 상위20_양수연도=f"{(top20 > 0).sum()}/{len(top20)}", 스프레드_평균=q.mean() * 100, 스프레드_양수연도=f"{(q > 0).sum()}/{len(q)}"))
print("\n== 점수별 (상위 200 종목, 12월 형성→12개월 초과수익; 단위 %) =="); print(pd.DataFrame(out).round(3).to_string(index=False))
print("\n연도별 IC(trend):", ic("trend").round(2).to_dict())
# 통제: 연도별 횡단 회귀(순위화) — 신호 + 직전 36개월 수익 + 12개월 모멘텀 + 시총 + 지분율 수준
print("\n== 통제 회귀(연도별 횡단, 순위 변수, 계수 평균·t(연도 수=%d)) ==" % A.y.nunique())
for sig in ("trend", "dp", "cons"):
    cs = []
    for y, z in A.groupby("y"):
        z = z.dropna(subset=[sig, "past36", "mom12", "lm", "lvl"]); R = lambda c: z[c].rank(pct=True).values - .5
        X = np.column_stack([np.ones(len(z)), R(sig), R("past36"), R("mom12"), R("lm"), R("lvl")]); cs.append(np.linalg.lstsq(X, z.ex.values, rcond=None)[0][1:])
    cs = np.array(cs); t = cs.mean(0) / cs.std(0, ddof=1) * np.sqrt(len(cs))
    print(f"{sig:6s} 신호 {cs[:,0].mean()*100:6.2f} (t {t[0]:.2f}) | 과거36m {cs[:,1].mean()*100:6.2f} (t {t[1]:.2f}) | 모멘텀12m {cs[:,2].mean()*100:6.2f} (t {t[2]:.2f}) | 시총 {cs[:,3].mean()*100:6.2f} (t {t[3]:.2f}) | 지분율수준 {cs[:,4].mean()*100:6.2f} (t {t[4]:.2f})  양수연도 {(cs[:,0]>0).sum()}/{len(cs)}")
# 구간 분할
for lab, ys in (("2016~2020 형성", range(2016, 2021)), ("2021~2024 형성", range(2021, 2025))):
    z = A[A.y.isin(ys)]; r = ic("trend", z); q = z.groupby("y").apply(lambda w: w[w.trend >= w.trend.quantile(.8)].ex.mean() - w[w.trend <= w.trend.quantile(.2)].ex.mean())
    print(lab, "trend IC %.3f (양수 %d/%d), 스프레드 %.1f%%p" % (r.mean(), (r > 0).sum(), len(r), q.mean() * 100))
# 포트폴리오: trend 상위 20% 동일가중 매년 보유 vs 상위200 동일가중
pt = A.groupby("y").apply(lambda z: pd.Series({"신호포트": z[z.trend >= z.trend.quantile(.8)].fwd.mean(), "벤치(상위200 동일가중)": z.fwd.mean(), "하위20": z[z.trend <= z.trend.quantile(.2)].fwd.mean(), "종목수": (z.trend >= z.trend.quantile(.8)).sum()}))
pt["초과"] = pt.신호포트 - pt["벤치(상위200 동일가중)"]; print("\n== 매년 12월 trend 상위 20% 동일가중 12개월 보유 =="); print((pt[["신호포트", "벤치(상위200 동일가중)", "하위20", "초과"]] * 100).round(1).assign(종목수=pt.종목수).to_string())
cg = lambda s: (np.prod(1 + s) ** (1 / len(s)) - 1) * 100
print("연복리: 신호 %.1f%% / 벤치 %.1f%% / 하위20 %.1f%% | 초과 양수연도 %d/%d, 평균 초과 %.1f%%p, 최악 %.1f%%p" % (cg(pt.신호포트), cg(pt["벤치(상위200 동일가중)"]), cg(pt.하위20), (pt.초과 > 0).sum(), len(pt), pt.초과.mean() * 100, pt.초과.min() * 100))
A.to_csv(os.path.join(os.path.dirname(__file__), "accum_signal_v2_panel.csv"), index=False)
