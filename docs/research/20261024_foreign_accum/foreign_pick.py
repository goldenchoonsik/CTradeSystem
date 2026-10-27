"""외국인 지분 36개월 증가 신호 명단 산출·전진 기록 (PREREGISTRATION.md 규칙 그대로, 실매수 없음).
  .venv/bin/python docs/research/20261024_foreign_accum/foreign_pick.py pick [YYYY-MM]   # 명단 저장 picks/picks_YYYYMM.csv (정식 형성은 12월)
  .venv/bin/python docs/research/20261024_foreign_accum/foreign_pick.py track            # picks/ 의 모든 명단 형성일 이후 수익 vs 상위200 동일가중"""
import os, sys, math, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived"); OUT = os.path.join(HERE, "picks"); os.makedirs(OUT, exist_ok=True)
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"])
d = g.merge(p, on=["code", "date"]).dropna(subset=["close"]); d["ym"] = d.date.dt.to_period("M")
m = d.sort_values("date").groupby(["code", "ym"]).tail(1)
P = m.pivot(index="ym", columns="code", values="close"); F = m.pivot(index="ym", columns="code", values="foreign_hold_pct"); C = m.pivot(index="ym", columns="code", values="market_cap")
NAME = pd.read_csv(os.path.join(ROOT, "DataVault", "staging", "kr_equity", "universe.csv")).set_index("code").name

def pick(t):
    uni = C.loc[t].dropna().nlargest(200).index
    df = pd.DataFrame({"dp": F.loc[t, uni] - F.loc[t - 36, uni], "past": P.loc[t, uni] / P.loc[t - 36, uni] - 1,
                       "peak": F.loc[t - 36:t, uni].max() - F.loc[t, uni]})
    c = df.dropna(); c = c[c.peak < 3].copy()
    x, y = c.past.rank(pct=True).values, c.dp.rank(pct=True).values
    c["score"] = y - np.polyval(np.polyfit(x, y, 1), x)
    sel = c.nlargest(math.ceil(0.2 * len(c)), "score").copy()
    sel["name"] = [NAME.get(i) for i in sel.index]; sel["base_date"] = d.date[d.ym == t].max().date()
    sel["base_close"] = P.loc[t, sel.index]
    return sel, uni

def cmd_pick():
    t = pd.Period(sys.argv[2] if len(sys.argv) > 2 else d.ym.max(), "M")
    sel, uni = pick(t); f = os.path.join(OUT, f"picks_{t.strftime('%Y%m')}.csv")
    sel.round(4).to_csv(f, index_label="code", encoding="utf-8-sig")
    pd.Series(uni).to_csv(os.path.join(OUT, f"universe_{t.strftime('%Y%m')}.csv"), index=False, header=["code"])
    print(f"{t} {len(sel)}종목 → {f}"); print(sel[["name", "dp", "past", "score"]].round(2).to_string())

def cmd_track():
    now = P.index.max(); print("현재", now)
    for f in sorted(x for x in os.listdir(OUT) if x.startswith("picks_")):
        ym = f[6:12]; t = pd.Period(f"{ym[:4]}-{ym[4:]}", "M")
        if t >= now: print(ym, "형성월 이후 데이터 없음"); continue
        sel = pd.read_csv(os.path.join(OUT, f), index_col="code"); uni = pd.read_csv(os.path.join(OUT, f"universe_{ym}.csv")).code
        r = lambda cs: (P.loc[now, cs].combine_first(P.loc[:now, cs].ffill().iloc[-1]) / P.loc[t, cs] - 1)
        rs, ru = r(sel.index), r(uni)
        print(f"{ym} → {now}: 선택 평균 {rs.mean()*100:.1f}% (중앙 {rs.median()*100:.1f}%) | 상위200 {ru.mean()*100:.1f}% | 초과 {(rs.mean()-ru.mean())*100:+.2f}%p (비용 전·미확정 형성월은 비공식)")

{"pick": cmd_pick, "track": cmd_track}[sys.argv[1]]()
