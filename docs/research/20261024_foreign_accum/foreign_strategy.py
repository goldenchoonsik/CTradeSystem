"""PREREGISTRATION.md 구현 — 외국인 지분 36개월 증가 신호 매수 전략.
  .venv/bin/python docs/research/20261024_foreign_accum/foreign_strategy.py            # 개발
  .venv/bin/python docs/research/20261024_foreign_accum/foreign_strategy.py --sealed   # 봉인(합격 시 1회)"""
import os, sys, math, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived")
COST = 0.0037
SEALED = "--sealed" in sys.argv
YEARS = (2023, 2024) if SEALED else tuple(range(2016, 2023))

g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"])
d = g.merge(p, on=["code", "date"]).dropna(subset=["close"])
d["ym"] = d.date.dt.to_period("M")
m = d.sort_values("date").groupby(["code", "ym"]).tail(1)
P = m.pivot(index="ym", columns="code", values="close")
F = m.pivot(index="ym", columns="code", values="foreign_hold_pct")
C = m.pivot(index="ym", columns="code", values="market_cap")
LAST = d.groupby("code").ym.max()          # 종목 마지막 월
ENDM = P.index.max()

def term_price(t, H, codes):
    """t+H 월말 종가, 이미 폐지됐으면 마지막 종가."""
    tgt = t + H
    out = {}
    for c in codes:
        col = P[c].loc[:min(tgt, ENDM)].dropna()
        out[c] = col.iloc[-1] if len(col) else np.nan
    return pd.Series(out)

def run(t, W=36, topfrac=0.2, N=200, H=12, neutral=True, drop_big=0):
    if (t - W) not in P.index or (t + H) > ENDM: return None
    uni = C.loc[t].dropna().nlargest(N).index
    p0, p1 = P.loc[t, uni], term_price(t, H, uni)
    ret = (p1 / p0 - 1); ok = ret.notna(); uni, ret = uni[ok.values], ret[ok]
    bench = ret.mean()
    f_t, f_0 = F.loc[t, uni], F.loc[t - W, uni]
    win = F.loc[t - W:t, uni]
    df = pd.DataFrame({"dp": f_t - f_0, "past": P.loc[t, uni] / P.loc[t - W, uni] - 1,
                       "peak": win.max() - f_t, "ret": ret})
    miss = df[df.dp.isna() | df.past.isna()]
    c = df.dropna(subset=["dp", "past", "peak"]); c = c[c.peak < 3]
    pool = len(c) + len(miss)
    if len(c) < 10: return None
    if neutral:
        x, y = c.past.rank(pct=True).values, c.dp.rank(pct=True).values
        b = np.polyfit(x, y, 1); score = pd.Series(y - np.polyval(b, x), index=c.index)
    else:
        score = c.dp
    k = max(1, math.ceil(topfrac * len(c)))
    sel = c.loc[score.nlargest(k).index]
    if drop_big: sel = sel.drop(sel.ret.nlargest(drop_big).index)
    s0 = sel.ret.mean()
    mm = miss.ret.mean() if len(miss) else 0.0
    s1 = (len(c) / pool) * s0 + (len(miss) / pool) * mm if len(miss) else s0
    dying = miss[[LAST[x] < t + H for x in miss.index]]
    s2 = pd.concat([sel.ret, dying.ret]).mean() if len(dying) else s0
    return dict(t=str(t), n_sel=len(sel), n_miss=len(miss), bench=bench, s0=s0, s1=s1, s2=s2,
                ex0=s0 - COST - bench, ex1=s1 - COST - bench, ex2=s2 - COST - bench,
                stock_ex=(sel.ret - bench).tolist())

def annual(**kw):
    rows = [run(pd.Period(f"{y}-12", "M"), **kw) for y in YEARS]
    return [r for r in rows if r]

def monthly(**kw):
    out = []
    for y in YEARS:
        for mo in range(1, 13):
            r = run(pd.Period(f"{y}-{mo:02d}", "M"), **kw)
            if r: out.append(r)
    return out

def summ(rows, label):
    if not rows: print(label, "없음"); return
    ex0 = np.mean([r["ex0"] for r in rows]); ex1 = np.mean([r["ex1"] for r in rows]); ex2 = np.mean([r["ex2"] for r in rows])
    print(f"{label:<28} n={len(rows):>3} 선택 {np.mean([r['s0'] for r in rows])*100:6.1f}% 벤치 {np.mean([r['bench'] for r in rows])*100:6.1f}% | 초과(비용후) S0 {ex0*100:6.2f}%p S1 {ex1*100:6.2f}%p S2 {ex2*100:6.2f}%p | 양수 {sum(r['ex0']>0 for r in rows)}/{len(rows)}")

rows = annual()
print(("봉인" if SEALED else "개발"), "연 단위 상세")
for r in rows:
    print(f"  t={r['t']} 선택{r['n_sel']}종목 신호없음{r['n_miss']} | 선택 {r['s0']*100:6.1f}% 벤치 {r['bench']*100:6.1f}% | 초과 S0 {r['ex0']*100:6.2f} S1 {r['ex1']*100:6.2f} S2 {r['ex2']*100:6.2f}")
summ(rows, "기준(연 단위)")
allst = np.concatenate([r["stock_ex"] for r in rows]); print(f"종목 단위 초과 중앙값 {np.median(allst)*100:.2f}%p (n={len(allst)}, 비용 전)")
if not SEALED:
    ex = [r for r in rows if r["t"] != "2022-12"]; summ(ex, "2022-12 제외")
    ok1 = np.mean([r["ex0"] for r in rows]) > 0; ok2 = np.median(allst) > 0
    ok3 = sum(r["ex0"] > 0 for r in rows) >= 4; ok4 = np.mean([r["ex0"] for r in ex]) > 0
    ok5 = np.mean([r["ex1"] for r in rows]) > 0 and np.mean([r["ex1"] for r in ex]) > 0
    print("\n합격선: 1)평균초과>0", ok1, " 2)종목중앙>0", ok2, " 3)양수연도>=4", ok3, " 4)2022제외>0", ok4, " 5)S1", ok5)
    print("개발 판정:", "합격 → 봉인 실행 가능" if all([ok1, ok2, ok3, ok4, ok5]) else "불합격")
    print("\n[민감도, 보고만]")
    summ(monthly(), "월별 형성 코호트")
    for fr in (0.1, 0.3): summ(annual(topfrac=fr), f"상위 {int(fr*100)}%")
    for W in (24, 48): summ(annual(W=W), f"창 {W}개월")
    for N in (100, 300): summ(annual(N=N), f"유니버스 {N}")
    summ(annual(neutral=False), "중립화 없음")
    summ(annual(drop_big=5), "수익 상위 5종목 제거(연별)")
