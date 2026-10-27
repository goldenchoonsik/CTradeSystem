"""꾸준 종목 13개 — 외국인 지분율(2013-01·2024-12·현재), MSCI Korea 포함 이력(현재=iShares EWY 2026-10-22, 이력=msci_korea_changes.csv 역산), 수익률.
  .venv/bin/python docs/research/20261025_kr_steady_long_hold/steady_info.py"""
import os, io, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..")); D = f"{ROOT}/DataVault/staging/kr_equity"
sel = pd.read_csv(f"{HERE}/steady_selected.csv", index_col="code"); codes = list(sel.index)
raw = open(f"{ROOT}/DataVault/staging/kr_investor_daily/ref/ewy_holdings_20261022.csv", encoding="utf-8").read().splitlines()
H = pd.read_csv(io.StringIO("\n".join(raw[next(i for i, l in enumerate(raw) if l.startswith("Ticker,")):])), dtype=str); H = H[H["Asset Class"] == "Equity"]
H["code"] = "A" + H.Ticker.str.zfill(6); W = H.set_index("code")["Weight (%)"].astype(float)
ch = pd.read_csv(f"{ROOT}/docs/research/20261021_index_rebalance_event/msci_korea_changes.csv", dtype=str); ch = ch[ch.action.isin(["add", "delete"])]; ch["eff"] = pd.to_datetime(ch.effective_close_date)
def member_at(c, T):
    m = c in W.index
    for _, r in ch[(ch.code == c) & (ch.eff > T)].sort_values("eff", ascending=False).iterrows(): m = (r.action == "delete")   # 역산: T 이후 add 되돌리면 비회원, delete 되돌리면 회원
    return m
g = pd.read_parquet(f"{D}/derived/gold_1d.parquet", columns=["code", "date", "market_cap", "foreign_hold_pct"]).set_index(["code", "date"])
px = pd.read_parquet(f"{D}/derived/prices_1d.parquet", columns=["code", "date", "close"]).pivot(index="date", columns="code", values="close").sort_index()
t0, t1, t2 = pd.Timestamp("2013-01-02"), pd.Timestamp("2024-12-30"), None
at = lambda f, T: f.loc[:T].iloc[-1] if len(f.loc[:T]) else np.nan
rows = []
for c in codes:
    f = g.foreign_hold_pct.xs(c, level="code").dropna(); mc = g.market_cap.xs(c, level="code").dropna(); s = px[c].dropna()
    ev = ch[ch.code == c].sort_values("eff"); evs = " ".join(f"{r.eff:%Y-%m}{'편입' if r.action == 'add' else '편출'}" for _, r in ev.iterrows())
    alive = mc.index[-1] >= pd.Timestamp("2026-10-01")
    cagr_now = ((s.iloc[-1] / s.loc[:t0].iloc[-1]) ** (365.25 / (s.index[-1] - t0).days) - 1) * 100
    rows.append(dict(종목=sel.loc[c, "종목"], 코드=c, 상장유지=alive,
        외국인_2013초=at(f, t0), 외국인_2024말=at(f, t1), 외국인_현재=f.iloc[-1] if (alive and len(f)) else np.nan, 외국인_변화_13_24=at(f, t1) - at(f, t0),
        MSCI_현재=("O" if c in W.index else "X") if alive else "-", EWY비중=W.get(c, np.nan), MSCI_2013초=("O" if member_at(c, t0) else "X"), MSCI_2024말=("O" if member_at(c, t1) else "X"), MSCI_변경이력=evs or "-",
        시총_현재_조=mc.iloc[-1] / 1e12 if alive else np.nan, CAGR_13_24=sel.loc[c, "CAGR"], CAGR_13_현재=cagr_now, 수익_2025이후=sel.loc[c, "이후_2025이후수익"], MDD_13_24=sel.loc[c, "MDD"]))
T = pd.DataFrame(rows).sort_values("CAGR_13_24", ascending=False); T.round(2).to_csv(f"{HERE}/steady_info.csv", index=False, encoding="utf-8-sig")
pd.set_option("display.width", 300); pd.set_option("display.max_columns", 30)
print(T[["종목", "상장유지", "외국인_2013초", "외국인_2024말", "외국인_현재", "외국인_변화_13_24", "MSCI_2013초", "MSCI_2024말", "MSCI_현재", "EWY비중", "MSCI_변경이력"]].round(1).to_string(index=False))
print(); print(T[["종목", "CAGR_13_24", "CAGR_13_현재", "수익_2025이후", "MDD_13_24", "시총_현재_조"]].round(1).to_string(index=False))
