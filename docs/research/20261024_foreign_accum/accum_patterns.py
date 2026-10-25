"""외국인 지분율 패턴 유형(꾸준한 증가/감소/급변/횡보) — 지속성, 추종·역추세, 유형별 이후 성과·하방위험, 전환점, 현재 목록. 상위 200(시총), 12월 형성.
  .venv/bin/python docs/research/20261024_foreign_accum/accum_patterns.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
V = f"{ROOT}/DataVault/staging/kr_equity"; D = f"{V}/derived"
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"]).dropna(subset=["close"])
d = g.merge(p, on=["code", "date"]); d["ym"] = d.date.dt.to_period("M"); m = d.sort_values("date").groupby(["code", "ym"]).tail(1)
P = m.pivot(index="ym", columns="code", values="close"); F = m.pivot(index="ym", columns="code", values="foreign_hold_pct"); C = m.pivot(index="ym", columns="code", values="market_cap")
nm = pd.read_csv(f"{V}/reference/listed_snapshot.csv").set_index("code"); last = P.index.max()
def fwd_ret(cols, t, H):
    return pd.Series({c: (P[c].loc[t:t + H].dropna().iloc[-1] if P[c].loc[t:t + H].notna().any() else np.nan) for c in cols}) / P.loc[t, cols] - 1
def cls(dp, cons):
    return np.where((dp >= 5) & (cons >= .6), "꾸준한 증가", np.where((dp <= -5) & (cons <= .4), "꾸준한 감소", np.where(dp.abs() >= 5, "급변", "횡보")))
rows = []
for y in range(2016, 2026):
    t = pd.Period(f"{y}-12", "M")
    top = C.loc[t].dropna().nlargest(200).index; w = F.loc[t - 36:t, top]; top = top[(w.notna().sum() >= 34).values]; w = F.loc[t - 36:t, top]
    df = pd.DataFrame({"dp": w.iloc[-1] - w.iloc[0], "cons": (w.diff().iloc[1:] > 0).mean(), "dp12": F.loc[t, top] - F.loc[t - 12, top], "past36": P.loc[t, top] / P.loc[t - 36, top] - 1})
    df["cls"] = cls(df.dp, df.cons); df["y"] = y
    if (t + 12) <= last:
        df["dpn"] = F.loc[t + 12, top] - F.loc[t, top]; f = fwd_ret(top, t, 12); df["fwd"] = f; df["ex"] = f - f.median()
    rows.append(df.reset_index().rename(columns={"index": "code"}))
A = pd.concat(rows)
print("== 유형 분포(상위 200, 형성연도 2016~2025 합) =="); print(A.cls.value_counts().to_dict())
B = A.dropna(subset=["fwd"])
print("\n== 1) 지분율 변화의 지속성: 과거 36개월 변화 → 이후 12개월 변화 ==")
r = B.dropna(subset=["dpn"]).groupby("y").apply(lambda z: z.dp.rank().corr(z.dpn.rank())); print("연도별 순위상관 평균 %.2f (양수 %d/%d)" % (r.mean(), (r > 0).sum(), len(r)))
tm = pd.crosstab(B.cls, np.where(B.dpn >= 1, "이후 +1%p↑", np.where(B.dpn <= -1, "이후 −1%p↓", "이후 횡보")), normalize="index").mul(100).round(0); print(tm.to_string())
print("\n== 2) 유형별 이후 12개월 성과(상위 200 중앙값 대비 초과, %) ==")
o = B.groupby("cls").agg(건수=("fwd", "size"), 직전36m수익중앙=("past36", lambda s: s.median() * 100), 이후12m수익중앙=("fwd", lambda s: s.median() * 100), 초과중앙=("ex", lambda s: s.median() * 100), 초과평균=("ex", lambda s: s.mean() * 100),
                          승률=("ex", lambda s: (s > 0).mean() * 100), 하위10퍼센타일수익=("fwd", lambda s: s.quantile(.1) * 100), 마이너스30이하비율=("fwd", lambda s: (s <= -.3).mean() * 100))
print(o.round(1).to_string())
yy = B.pivot_table(index="y", columns="cls", values="ex", aggfunc="median") * 100; print("\n연도별 초과 중앙값(%):"); print(yy.round(1).to_string())
print("\n== 3) 전환점: 꾸준한 증가 종목 중 직전 12개월에도 계속 늘었나 / 멈췄나 ==")
acc = B[B.cls == "꾸준한 증가"]; cont, stop = acc[acc.dp12 > 0.5], acc[acc.dp12 <= 0.5]
for lab, z in (("계속 증가(12m +0.5%p 초과)", cont), ("멈춤·감소(12m +0.5%p 이하)", stop)):
    print(f"{lab}: n={len(z)}, 이후 12m 초과 중앙 {z.ex.median()*100:.1f}%, 평균 {z.ex.mean()*100:.1f}%, 승률 {(z.ex>0).mean()*100:.0f}%, 하위10% 수익 {z.fwd.quantile(.1)*100:.0f}%")
dec = B[B.cls == "꾸준한 감소"]; dc, ds = dec[dec.dp12 < -0.5], dec[dec.dp12 >= -0.5]
for lab, z in (("계속 감소", dc), ("멈춤·반등", ds)):
    print(f"꾸준한 감소 중 {lab}: n={len(z)}, 이후 12m 초과 중앙 {z.ex.median()*100:.1f}%, 평균 {z.ex.mean()*100:.1f}%, 승률 {(z.ex>0).mean()*100:.0f}%")
print("\n== 4) 외국인의 행동: 월별 지분율 변화 vs 직전 수익 (상위 200, 2016~) ==")
res = []
for t in pd.period_range("2016-01", last - 1, freq="M"):
    top = C.loc[t].dropna().nlargest(200).index
    z = pd.DataFrame({"dF": F.loc[t + 1, top] - F.loc[t, top], "r1": P.loc[t, top] / P.loc[t - 1, top] - 1, "r3": P.loc[t, top] / P.loc[t - 3, top] - 1, "r12": P.loc[t, top] / P.loc[t - 12, top] - 1}).dropna()
    z = z[z.dF.abs() < 3]
    if len(z) > 100: res.append({k: z.dF.rank().corr(z[k].rank()) for k in ("r1", "r3", "r12")})
res = pd.DataFrame(res); print("순위상관 평균(양=오른 종목 추가 매수/추종, 음=역추세) r1 %.3f r3 %.3f r12 %.3f (월 %d개)" % (res.r1.mean(), res.r3.mean(), res.r12.mean(), len(res)))
print("\n== 5) 현재(2026-10) 상위 200 중 꾸준한 증가 / 꾸준한 감소 (36개월) ==")
t = last; top = C.loc[t].dropna().nlargest(200).index; w = F.loc[t - 36:t, top]; top = top[(w.notna().sum() >= 34).values]; w = F.loc[t - 36:t, top]
cur = pd.DataFrame({"dp": w.iloc[-1] - w.iloc[0], "cons": (w.diff().iloc[1:] > 0).mean(), "지분율": w.iloc[-1], "36m수익%": (P.loc[t, top] / P.loc[t - 36, top] - 1) * 100, "시총순위": C.loc[t, top].rank(ascending=False)})
cur["유형"] = cls(cur.dp, cur.cons); cur["이름"] = [nm.name.get(c, c) for c in cur.index]
for lab in ("꾸준한 증가", "꾸준한 감소"):
    z = cur[cur.유형 == lab].sort_values("dp", ascending=(lab == "꾸준한 감소")); print(f"\n[{lab}] {len(z)}종목"); print(z[["이름", "시총순위", "지분율", "dp", "cons", "36m수익%"]].head(15).round(1).to_string())
cur.to_csv(os.path.join(os.path.dirname(__file__), "accum_patterns_current.csv"), encoding="utf-8-sig")
A.to_csv(os.path.join(os.path.dirname(__file__), "accum_patterns_panel.csv"), index=False)
