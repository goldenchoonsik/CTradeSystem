"""외국인 지분 변화와 순이익 변화의 선후(Forward PER 효과) — 지분 4분기 변화(q−4→q) vs 순이익 LTM YoY 대칭변화를 k분기 옮겨 비교. 상위 200(시총, 분기말), 분기별 순위상관 평균.
k>0: 이익 구간이 지분 변화 구간보다 k분기 뒤(지분이 선행), k<0: 이익이 앞(지분이 후행). 주가 4분기 수익 통제 편상관 병행. 연결(CFS) 연간 누계 기반 LTM.
  .venv/bin/python docs/research/20261024_foreign_accum/leadlag_earnings.py"""
import os, numpy as np, pandas as pd
from scipy import stats
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); V = f"{ROOT}/DataVault/staging/kr_equity"; D = f"{V}/derived"
fq = pd.read_parquet(f"{D}/fundamentals_q.parquet", columns=["stock_code", "fs_div", "period_end", "net_income_cum", "rcept_dt"])
fq = fq[fq.fs_div == "CFS"].sort_values(["stock_code", "period_end", "rcept_dt"]).drop_duplicates(["stock_code", "period_end"], keep="last")
fq["q"] = fq.period_end.dt.to_period("Q"); CUM = fq.pivot(index="q", columns="stock_code", values="net_income_cum")
qs = pd.period_range("2015Q1", "2026Q2", freq="Q")
CUM = CUM.reindex(qs); LTM = pd.DataFrame(index=qs, columns=CUM.columns, dtype=float)
for q in qs:
    if q.quarter == 4: LTM.loc[q] = CUM.loc[q]
    elif q - q.quarter >= qs[0]: LTM.loc[q] = CUM.loc[q] + CUM.loc[q - q.quarter] - CUM.loc[q - 4]
G = (LTM - LTM.shift(4)) / (LTM.abs() + LTM.shift(4).abs())          # YoY 대칭변화 (−1~+1)
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"]).dropna(subset=["market_cap", "foreign_hold_pct"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"]).dropna(subset=["close"]); d = g.merge(p, on=["code", "date"]); d["q"] = d.date.dt.to_period("Q")
nm = pd.read_csv(f"{V}/reference/listed_snapshot.csv").set_index("code"); d = d[~d.code.isin(set(nm.index[nm.is_preferred.astype(str) != "0"]))]
m = d.sort_values("date").groupby(["code", "q"]).tail(1); d = None
F = m.pivot(index="q", columns="code", values="foreign_hold_pct").reindex(qs); C = m.pivot(index="q", columns="code", values="market_cap").reindex(qs); P = m.pivot(index="q", columns="code", values="close").reindex(qs)
G.columns = ["A" + c for c in G.columns]; dF = F - F.shift(4); pr = P / P.shift(4) - 1
def spearman(a, b): return a.rank().corr(b.rank())
def partial(a, b, c):
    r = pd.concat([a.rank(), b.rank(), c.rank()], axis=1).dropna()
    if len(r) < 60: return np.nan
    X = np.column_stack([np.ones(len(r)), r.iloc[:, 2]]); ra = r.iloc[:, 0] - X @ np.linalg.lstsq(X, r.iloc[:, 0], rcond=None)[0]; rb = r.iloc[:, 1] - X @ np.linalg.lstsq(X, r.iloc[:, 1], rcond=None)[0]; return np.corrcoef(ra, rb)[0, 1]
rows = []
valid_q = [q for q in qs if q >= pd.Period("2016Q1") and q <= pd.Period("2025Q4")]
for k in range(-8, 9):
    raw, par, prc, n = [], [], [], []
    for q in valid_q:
        qk = q + k
        if qk not in G.index or qk > pd.Period("2026Q2"): continue
        top = C.loc[q].dropna().nlargest(200).index.intersection(G.columns); x = dF.loc[q, top]; y = G.loc[qk, top].astype(float); z = pr.loc[q, top]
        mk = x.notna() & y.notna()
        if mk.sum() < 80: continue
        raw.append(spearman(x[mk], y[mk])); par.append(partial(x[mk], y[mk], z[mk])); prc.append(spearman(z[mk], y[mk])); n.append(mk.sum())
    if len(raw) >= 6:
        t = lambda v: np.nanmean(v) / (np.nanstd(v, ddof=1) / np.sqrt(len(v)))
        rows.append(dict(k=k, 분기수=len(raw), 종목수=np.mean(n), 지분_원시=np.nanmean(raw), 지분_주가통제=np.nanmean(par), 주가=np.nanmean(prc), 양수비율=np.mean(np.array(raw) > 0) * 100, t_겹침무시=t(raw)))
R = pd.DataFrame(rows); pd.set_option("display.width", 200); print(R.round(3).to_string(index=False))
# 비중첩(연말 Q4만)
ann = []
for k in (-4, 0, 4):
    v = []
    for q in [q for q in valid_q if q.quarter == 4]:
        qk = q + k
        if qk not in G.index or qk > pd.Period("2026Q2"): continue
        top = C.loc[q].dropna().nlargest(200).index.intersection(G.columns); x = dF.loc[q, top]; y = G.loc[qk, top].astype(float); mk = x.notna() & y.notna()
        if mk.sum() >= 80: v.append(spearman(x[mk], y[mk]))
    ann.append((k, len(v), np.mean(v), np.mean(np.array(v) > 0) * 100, np.mean(v) / (np.std(v, ddof=1) / np.sqrt(len(v)))))
print("\n== 연말(Q4) 비중첩: k, 연수, 평균 순위상관, 양수 비율, t =="); [print(f"k={a:+d} n={b} 상관 {c:+.3f} 양수 {dd:.0f}% t {e:.2f}") for a, b, c, dd, e in ann]
R.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "leadlag_earnings.csv"), index=False)
