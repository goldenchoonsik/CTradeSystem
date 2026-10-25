"""MSCI 한국 현재 구성종목(iShares EWY 보유종목 2026-10-22, MSCI Korea 추종) vs 비구성종목 — 외국인 지분율·지분 변화·규모·수익. 기준일=gold 마지막 날(2026-10-19).
  .venv/bin/python docs/research/20261024_foreign_accum/msci_members.py"""
import os, io, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
raw = open(f"{ROOT}/DataVault/staging/kr_investor_daily/ref/ewy_holdings_20261022.csv", encoding="utf-8").read().splitlines()
h = next(i for i, l in enumerate(raw) if l.startswith("Ticker,"))
H = pd.read_csv(io.StringIO("\n".join(raw[h:])), dtype=str); H = H[H["Asset Class"] == "Equity"]; H["code"] = "A" + H.Ticker.str.zfill(6); H["w"] = H["Weight (%)"].astype(float)
print("EWY 주식 보유 %d종목, 비중 합 %.1f%%, 최소 비중 %.3f%%" % (len(H), H.w.sum(), H.w.min()))
D = f"{ROOT}/DataVault/staging/kr_equity/derived"
g = pd.read_parquet(f"{D}/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"]); p = pd.read_parquet(f"{D}/prices_1d.parquet", columns=["code", "date", "close"]).dropna(subset=["close"])
d = g.merge(p, on=["code", "date"]); d["ym"] = d.date.dt.to_period("M"); m = d.sort_values("date").groupby(["code", "ym"]).tail(1)
P = m.pivot(index="ym", columns="code", values="close"); F = m.pivot(index="ym", columns="code", values="foreign_hold_pct"); C = m.pivot(index="ym", columns="code", values="market_cap"); t = P.index.max()
nm = pd.read_csv(f"{ROOT}/DataVault/staging/kr_equity/reference/listed_snapshot.csv").set_index("code")
X = pd.DataFrame({"mc": C.loc[t], "f": F.loc[t], "d12": F.loc[t] - F.loc[t - 12], "d36": F.loc[t] - F.loc[t - 36], "r12": P.loc[t] / P.loc[t - 12] - 1, "r36": P.loc[t] / P.loc[t - 36] - 1}).dropna(subset=["mc", "f"])
X["rank"] = X.mc.rank(ascending=False); X["mem"] = X.index.isin(H.code); X["mkt"] = nm.market.reindex(X.index); X["pref"] = nm.is_preferred.reindex(X.index).astype(str)
miss = sorted(set(H.code) - set(X.index)); print("명단 중 우리 패널에 없는 코드:", miss, [H[H.code == c].Name.iloc[0] for c in miss])
M = X[X.mem]; print("우리 데이터에 매칭 %d종목 | 시총 순위 중앙 %.0f, 최대 %.0f" % (len(M), M["rank"].median(), M["rank"].max()))
print("구성종목 시장: ", M.mkt.value_counts().to_dict())
print("\n== 시총 구간별 구성종목 비율 / 외국인 지분율(구성 vs 비구성) ==")
rows = []
for lo, hi in ((0, 10), (10, 30), (30, 60), (60, 100), (100, 150), (150, 250), (250, 400)):
    z = X[(X["rank"] > lo) & (X["rank"] <= hi)]; a, b = z[z.mem], z[~z.mem]
    rows.append(dict(시총순위=f"{lo+1}~{hi}", 종목수=len(z), 구성비율=len(a) / len(z) * 100, 구성_지분율=a.f.mean(), 비구성_지분율=b.f.mean(), 차이=a.f.mean() - b.f.mean(), 구성_12m지분변화=a.d12.mean(), 비구성_12m지분변화=b.d12.mean(), 구성_36m수익중앙=a.r36.median() * 100, 비구성_36m수익중앙=b.r36.median() * 100))
print(pd.DataFrame(rows).round(1).to_string(index=False))
# 규모 맞춤 쌍(구성종목 각각에 ±15위 비구성종목 평균)
res = []
for c, r in M.iterrows():
    near = X[(abs(X["rank"] - r["rank"]) <= 15) & (~X.mem) & (X.pref == "0")]
    if len(near) >= 3: res.append(dict(code=c, rank=r["rank"], f=r.f, cf=near.f.mean(), d12=r.d12, cd12=near.d12.mean(), r12=r.r12, cr12=near.r12.mean(), r36=r.r36, cr36=near.r36.mean(), n=len(near)))
R = pd.DataFrame(res); print("\n== 규모(±15위) 맞춤 비구성(우선주 제외) 대비, 구성종목 %d개 ==" % len(R))
print("외국인 지분율 %.1f%% vs %.1f%% (차이 %+.1f%%p, 구성이 더 높은 비율 %.0f%%) | 12m 지분 변화 %+.1f vs %+.1f%%p | 12m 수익 중앙 %.0f%% vs %.0f%% | 36m 수익 중앙 %.0f%% vs %.0f%%" % (R.f.mean(), R.cf.mean(), (R.f - R.cf).mean(), (R.f > R.cf).mean() * 100, R.d12.mean(), R.cd12.mean(), R.r12.median() * 100, R.cr12.median() * 100, R.r36.median() * 100, R.cr36.median() * 100))
top = X[X["rank"] <= 100]; print("\n시총 상위 100 중 구성 %d·비구성 %d. 비구성 상위100 종목:" % (top.mem.sum(), (~top.mem).sum()))
nb = top[~top.mem].sort_values("rank"); nb["이름"] = nm.name.reindex(nb.index); nb["우선주"] = nb.pref; print(nb[["이름", "rank", "f", "d12", "mkt", "우선주"]].round(1).head(30).to_string())
X.assign(이름=nm.name.reindex(X.index)).to_csv(os.path.join(os.path.dirname(__file__), "msci_members_snapshot.csv"), encoding="utf-8-sig")
