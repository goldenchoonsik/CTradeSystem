"""MSCI 한국 편입·편출 종목의 외국인 지분율 — 편입 시점 수준, 전후 변화, 규모 맞춤 대조군 비교. 명단=docs/research/20261021_index_rebalance_event/msci_korea_changes.csv(사건만, 구성종목 전체 아님).
  .venv/bin/python docs/research/20261024_foreign_accum/msci_foreign.py"""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ev = pd.read_csv(f"{ROOT}/docs/research/20261021_index_rebalance_event/msci_korea_changes.csv", dtype=str)
ev = ev[ev.action.isin(["add", "delete"]) & ev.code.notna()]; ev["eff"] = pd.to_datetime(ev.effective_close_date); ev["t"] = ev.eff.dt.to_period("M")
D = f"{ROOT}/DataVault/staging/kr_equity/derived"
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"]).dropna(subset=["close"])
d = g.merge(p, on=["code", "date"]); d["ym"] = d.date.dt.to_period("M"); m = d.sort_values("date").groupby(["code", "ym"]).tail(1)
P = m.pivot(index="ym", columns="code", values="close"); F = m.pivot(index="ym", columns="code", values="foreign_hold_pct"); C = m.pivot(index="ym", columns="code", values="market_cap")
print("사건: 편입 %d·편출 %d (코드 가격 존재: 편입 %d·편출 %d)" % ((ev.action == "add").sum(), (ev.action == "delete").sum(), ev[ev.action == "add"].code.isin(P.columns).sum(), ev[ev.action == "delete"].code.isin(P.columns).sum()))
adds_all = set(zip(ev.t, ev.code)); rng = np.random.default_rng(1)
rows = []
for _, e in ev.iterrows():
    c, t = e.code, e.t; pre = t - 2
    if c not in F.columns or pre not in F.index or t + 12 > F.index.max() or pd.isna(F.loc[pre, c]) or pd.isna(C.loc[pre, c]): continue
    rank = C.loc[pre].rank(ascending=False); r0 = rank[c]
    near = rank[(rank - r0).abs() <= 20].index.difference([c])
    excl = {x for (tt, x) in adds_all if abs((tt - t).n) <= 3}; near = [x for x in near if x not in excl and pd.notna(F.loc[pre, x])]
    if len(near) < 10: continue
    ctl = rng.choice(near, size=min(30, len(near)), replace=False)
    def dF(cols, a, b): return (F.loc[t + b, cols] - F.loc[t + a, cols])
    row = dict(action=e.action, t=str(t), code=c, name=e["name"], rank=r0, lvl=F.loc[pre, c], ctl_lvl=F.loc[pre, ctl].mean(), past12=P.loc[pre, c] / P.loc[pre - 12, c] - 1 if (pre - 12) in P.index and pd.notna(P.loc[pre - 12, c]) else np.nan,
               ctl_past12=(P.loc[pre, ctl] / P.loc[pre - 12, ctl] - 1).mean() if (pre - 12) in P.index else np.nan, dpre12=F.loc[pre, c] - F.loc[pre - 12, c] if (pre - 12) in F.index else np.nan, ctl_dpre12=(F.loc[pre, ctl] - F.loc[pre - 12, ctl]).mean() if (pre - 12) in F.index else np.nan)
    for lab, (a, b) in {"d_pre_p1": (-2, 1), "d_pre_p3": (-2, 3), "d_pre_p12": (-2, 12)}.items():
        v = F.loc[t + b, c] - F.loc[t + a, c]; row[lab] = v; row["ctl_" + lab] = dF(ctl, a, b).mean()
    rows.append(row)
R = pd.DataFrame(rows)
for act in ("add", "delete"):
    z = R[R.action == act]; print(f"\n===== {'편입' if act=='add' else '편출'} {len(z)}건 (규모 순위 ±20 대조군 최대 30종목, 편입 전 2개월 말 기준) =====")
    print("편입 직전 시총 순위 중앙 %.0f | 외국인 지분율: 사건 %.1f%% vs 대조군 %.1f%% (차이 %.1f%%p, 사건이 더 높은 비율 %.0f%%)" % (z["rank"].median(), z.lvl.mean(), z.ctl_lvl.mean(), (z.lvl - z.ctl_lvl).mean(), (z.lvl > z.ctl_lvl).mean() * 100))
    print("직전 12개월 주가 수익: 사건 %.0f%% vs 대조군 %.0f%% | 직전 12개월 지분 변화: 사건 %+.1f%%p vs 대조군 %+.1f%%p" % (z.past12.median() * 100, z.ctl_past12.median() * 100, z.dpre12.mean(), z.ctl_dpre12.mean()))
    for k, lab in (("d_pre_p1", "편입월 직후(+1개월)"), ("d_pre_p3", "+3개월"), ("d_pre_p12", "+12개월")):
        dd = z[k] - z["ctl_" + k]; print(f"지분 변화 {lab}: 사건 {z[k].mean():+.2f}%p vs 대조군 {z['ctl_'+k].mean():+.2f}%p → 초과 {dd.mean():+.2f}%p (t {dd.mean()/dd.std()*np.sqrt(len(dd)):.2f}, 사건이 더 큰 비율 {(dd>0).mean()*100:.0f}%)")
    z2 = z.assign(period=np.where(z.t < "2018", "2013~17", np.where(z.t < "2022", "2018~21", "2022~26")))
    print((z2.groupby("period").apply(lambda q: pd.Series({"건수": len(q), "지분율 차(사건−대조, %p)": (q.lvl - q.ctl_lvl).mean(), "직후 초과변화(%p)": (q.d_pre_p1 - q.ctl_d_pre_p1).mean(), "+3개월 초과(%p)": (q.d_pre_p3 - q.ctl_d_pre_p3).mean(), "+12개월 초과(%p)": (q.d_pre_p12 - q.ctl_d_pre_p12).mean()}))).round(2).to_string())
R.to_csv(os.path.join(os.path.dirname(__file__), "msci_foreign_events.csv"), index=False, encoding="utf-8-sig")
