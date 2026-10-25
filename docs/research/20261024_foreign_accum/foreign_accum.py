"""외국인 누적 순매수 vs 주가 — 같은 기간 수익(동행)·이후 수익(선행). 탐색(기술) 분석, 사전등록 아님.
  .venv/bin/python docs/research/20261024_foreign_accum/foreign_accum.py
흐름 = Σ Δ외국인보유수량(분할 소급수량) × 수정종가 / 기간초 시총. 상장주식수·수정계수 바뀐 날(기업행위 의심)은 제외. 월말 형성, 창 W 거래일."""
import os, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
D = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived")
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code","date","close_unadj","adv_20","market_cap","ret_1"])
e = pd.read_parquet(f"{D}/daily_ext.parquet", columns=["code","date","shares_listed","foreign_hold_qty","mcap_approx"])
p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code","date","close","factor"])
df = g.merge(e, on=["code","date"]).merge(p, on=["code","date"]).sort_values(["code","date"])
df = df[df.date >= "2014-06-01"]
gb = df.groupby("code")
dq = gb.foreign_hold_qty.diff()
bad = (gb.shares_listed.diff().fillna(0) != 0) | (gb.factor.diff().fillna(0) != 0) | dq.isna() | (df.mcap_approx == True)
df["flow"] = np.where(bad, 0.0, dq * df.close)                    # 원
df["flow_ok"] = (~bad).astype(float)
df["lc"] = np.log(df.close)
res = {}
month_end = df.groupby(df.date.dt.to_period("M")).date.max()
rows = []
for W in (60, 120, 250):
    cf = gb.flow.transform(lambda s: s.rolling(W, min_periods=int(W*.9)).sum())
    ok = gb.flow_ok.transform(lambda s: s.rolling(W, min_periods=int(W*.9)).mean())
    cap0 = gb.market_cap.shift(W)
    df[f"f{W}"] = np.where(ok > .95, cf / cap0 * 100, np.nan)           # 시총 대비 %
    df[f"r{W}"] = gb.lc.diff(W)
    for H in (60, 120):
        df[f"x{W}_{H}"] = gb.lc.shift(-H) - df.lc
df["ok"] = (df.close_unadj >= 1000) & (df.adv_20 >= 5e8)
m = df[df.date.isin(month_end.values) & df.ok]
out = []
for W in (60, 120, 250):
    s = m.dropna(subset=[f"f{W}", f"r{W}"])
    s = s[(s.date >= "2016-01-01")]
    def ic(a, b, grp):
        return grp.apply(lambda x: x[a].rank().corr(x[b].rank()) if len(x) > 50 else np.nan)
    for lab, a, b in [("동행(같은 창 수익)", f"f{W}", f"r{W}")] + [(f"선행(이후 {H}일)", f"f{W}", f"x{W}_{H}") for H in (60, 120)]:
        ss = s.dropna(subset=[b]); gp = ss.groupby("date")
        icv = ic(a, b, gp).dropna()
        # 5분위 스프레드
        ss = ss.assign(q=gp[a].transform(lambda x: pd.qcut(x.rank(method="first"), 5, labels=False)))
        qm = ss.groupby(["date", "q"])[b].mean().unstack()
        spr = (qm[4] - qm[0])
        for per, mask in [("2016~2023", icv.index < "2024-01-01"), ("2024~", icv.index >= "2024-01-01")]:
            i = icv[mask]; sp = spr.reindex(icv.index)[mask]
            out.append(dict(창=W, 종류=lab, 기간=per, 월수=len(i), IC평균=i.mean(), IC_t=i.mean()/i.std()*np.sqrt(len(i)) if len(i)>2 else np.nan,
                            상위5분위_수익=qm[4].reindex(icv.index)[mask].mean()*100, 하위5분위_수익=qm[0].reindex(icv.index)[mask].mean()*100,
                            스프레드=sp.mean()*100))
o = pd.DataFrame(out)
# 시장 전체: 월별 외국인 합계 순매수(시총 대비) vs 시장 수익
mk = df[df.ok].groupby("date").agg(flow=("flow","sum"), cap=("market_cap","sum"))
idx = pd.read_parquet(f"{D}/index_1d.parquet"); print(idx.code.unique()[:10])
o.to_csv(os.path.join(os.path.dirname(__file__), "stock_level.csv"), index=False)
pd.set_option("display.width", 250); print(o.round(3).to_string())
mk.to_pickle("/tmp/claude-1000/-home-kuzimo-projects-CTradeSystem/dbb98a3f-37e1-43cd-a6a6-c22f23e37b4b/scratchpad/mk.pkl")

# ---- 2) 통제 후·비중첩 ----
print("\n== 통제(같은 창 과거수익·시총 순위) 후 Fama-MacBeth, 비중첩(H개월마다) ==")
m = m.assign(lm=np.log(m.market_cap))
rows = []
for W in (60, 120, 250):
    for H in (60, 120):
        b = f"x{W}_{H}"
        s = m.dropna(subset=[f"f{W}", f"r{W}", b, "lm"]); s = s[s.date >= "2016-01-01"]
        dates = sorted(s.date.unique()); step = 3 if H == 60 else 6
        use = dates[::step]
        for per, sel in [("2016~2023", [d for d in use if d < pd.Timestamp("2024-01-01")]), ("2024~", [d for d in use if d >= pd.Timestamp("2024-01-01")])]:
            cs = []
            for d in sel:
                x = s[s.date == d]
                if len(x) < 200: continue
                R = lambda c: (x[c].rank(pct=True) - .5).values
                X = np.column_stack([np.ones(len(x)), R(f"f{W}"), R(f"r{W}"), R("lm")])
                cs.append(np.linalg.lstsq(X, x[b].values, rcond=None)[0][1:])
            cs = np.array(cs)
            if len(cs) > 2:
                rows.append(dict(창=W, 이후=H, 기간=per, 표본월=len(cs), 흐름계수_pct=cs[:,0].mean()*100, t=cs[:,0].mean()/cs[:,0].std(ddof=1)*np.sqrt(len(cs)),
                                 과거수익계수_pct=cs[:,1].mean()*100, t_ret=cs[:,1].mean()/cs[:,1].std(ddof=1)*np.sqrt(len(cs))))
print(pd.DataFrame(rows).round(2).to_string())

# ---- 3) 시장 전체: 코스피 지수 vs 외국인 누적 순매수 ----
mk["cum"] = mk.flow.cumsum() / 1e12
ix = idx[idx.code == "U001"].set_index("date").close if (idx.code == "U001").any() else None
print("\n지수 코드별 최종 종가:", idx.groupby("code").close.last().head(6).to_dict())
mk = mk[mk.index >= "2016-01-01"]
mkm = mk.resample("ME").agg({"flow": "sum", "cap": "last"})
mkm["f_pct"] = mkm.flow / mkm.cap.shift(1) * 100
import sys
ret = idx[idx.code == "U001"].set_index("date").close.resample("ME").last().pipe(np.log).diff()
j = pd.concat([mkm.f_pct, ret.rename("ret")], axis=1).dropna()
print("월별 합계 흐름(시총 대비%) ~ 같은달 지수수익 상관:", round(j.f_pct.corr(j.ret), 3), " 다음달:", round(j.f_pct.corr(j.ret.shift(-1)), 3))
for W in (6, 12, 24):
    a = j.f_pct.rolling(W).sum(); r = j.ret.rolling(W).sum()
    fw = j.ret.rolling(W).sum().shift(-W)
    print(f"{W}개월 누적흐름 vs 같은 창 지수수익 상관 {a.corr(r):.3f} | vs 이후 {W}개월 수익 {a.corr(fw):.3f} (비중첩 n≈{len(j)//W})")
yr = pd.concat([j.f_pct.groupby(j.index.year).sum().rename("연_흐름%시총"), j.ret.groupby(j.index.year).sum().mul(100).rename("연_지수수익%")], axis=1)
print(yr.round(1).T.to_string())
