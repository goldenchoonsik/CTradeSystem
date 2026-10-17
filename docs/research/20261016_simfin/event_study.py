"""실적 발표(8-K 2.02) 전·후 초과수익률 이벤트 스터디 — PREREG_events.md 규칙 그대로."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, pandas as pd
import factor_test as ft

R = ft.RAW; D = R.parent / "derived"
WIN = {"pre[-20,-6]": (-20, -6), "pre[-5,-1]": (-5, -1), "d0[0]": (0, 0), "post[1,5]": (1, 5), "post[6,20]": (6, 20), "post[21,60]": (21, 60)}
SIGS = ["rev_yoy", "sue", "gm_chg"]


def quarters(cid):
    d = json.loads((R / "statements" / f"{cid}.json").read_text())
    st = {s["statement"]: ft.table(s) for s in d[0]["statements"]}
    if not {"PL", "BS"} <= set(st): return None
    key = ["Fiscal Year", "Fiscal Period"]
    pl = st["PL"]; pl = pl[pl["Fiscal Period"].isin(["Q1", "Q2", "Q3", "Q4"]) & (pl["TTM"] == 0)]
    bs = st["BS"]; bs = bs[bs["Fiscal Period"].isin(["Q1", "Q2", "Q3", "Q4"]) & (bs["TTM"] == 0)]
    q = pl.reindex(columns=key + ["Report Date", "Publish Date", "Revenue", "Gross Profit", "Net Income"]).drop_duplicates(key, keep="last").merge(
        bs.reindex(columns=key + ["Total Assets"]).drop_duplicates(key, keep="last"), on=key, how="left")
    q["rd"] = pd.to_datetime(q["Report Date"]); q["pdt"] = pd.to_datetime(q["Publish Date"], errors="coerce")
    q = q.sort_values("rd").reset_index(drop=True)
    prev = []
    for r in q["rd"]:
        m = q[(q["rd"] >= r - pd.Timedelta(days=380)) & (q["rd"] <= r - pd.Timedelta(days=350))]
        prev.append(m.index[0] if len(m) else -1)
    q["pi"] = prev
    g = lambda col: np.where(q["pi"] >= 0, q[col].reindex(q["pi"].clip(lower=0)).values, np.nan)
    q["rev_py"], q["ni_py"], q["gp_py"], q["as_py"] = g("Revenue"), g("Net Income"), g("Gross Profit"), g("Total Assets")
    q["rev_yoy"] = q["Revenue"] / q["rev_py"] - 1
    q["sue"] = (q["Net Income"] - q["ni_py"]) / q["as_py"]
    q["gm_chg"] = q["Gross Profit"] / q["Revenue"] - q["gp_py"] / q["rev_py"]
    return q


def main():
    ev8 = pd.read_csv(D / "earnings_8k.csv", parse_dates=["accepted_et"])
    ev8["accepted_et"] = pd.to_datetime(ev8["accepted_et"], utc=True).dt.tz_convert("America/New_York")
    ids = sorted(ev8["id"].unique())
    adj, close, rows = {}, {}, []
    qs = {}
    for cid in ids:
        px = ft.load_prices(cid)
        q = quarters(cid)
        if px is None or q is None: continue
        adj[cid] = px["Adjusted Closing Price"]; close[cid] = px["Last Closing Price"]; qs[cid] = q
    A = pd.DataFrame(adj).sort_index(); A = A.where(A > 0); C = pd.DataFrame(close).reindex(A.index)
    ret = A.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    mkt = ret.mean(axis=1).where(ret.notna().sum(axis=1) >= 30)
    ab = ret.sub(mkt, axis=0)
    dates = A.index
    shares = {cid: ft.load_prices(cid)["Common Shares Outstanding"] for cid in qs}
    from collections import Counter
    fn = Counter()
    for _, e in ev8.iterrows():
        cid = e["id"]; fn["8-K"] += 1
        if cid not in qs: fn["가격/재무 없음"] += 1; continue
        acc = e["accepted_et"]; day = pd.Timestamp(acc.date())
        pos = dates.searchsorted(day)
        if pos >= len(dates): fn["날짜초과"] += 1; continue
        same_day_open = dates[pos] == day and acc.hour < 16
        d0 = pos if same_day_open else (pos + 1 if dates[pos] == day else pos)
        if d0 >= len(dates) or d0 < 61: fn["D0 범위밖"] += 1; continue
        q = qs[cid]
        cand = q[(q["pdt"] >= day - pd.Timedelta(days=2)) & (q["pdt"] <= day + pd.Timedelta(days=90)) & (q["rd"] < day)]
        if cand.empty: fn["재무행 매칭 실패"] += 1; continue
        r = cand.sort_values("pdt").iloc[0]
        px0 = C[cid].iloc[d0 - 1]; sh = shares[cid].reindex(dates).ffill().iloc[d0 - 1]
        if sh is None or pd.isna(sh) or pd.isna(px0): fn["가격/주식수 결측"] += 1; continue
        if not (px0 >= ft.MIN_PX and px0 * sh >= ft.MIN_MCAP): fn["시총/가격 필터"] += 1; continue
        rec = dict(id=cid, d0=dates[d0], period=r["rd"], **{s: r[s] for s in SIGS})
        for name, (a, b) in WIN.items():
            seg = ab[cid].iloc[d0 + a: d0 + b + 1]
            rec[name] = seg.sum() if len(seg) == b - a + 1 and seg.notna().all() else np.nan
        rows.append(rec)
    print("이벤트 탈락 추적:", dict(fn))
    X = pd.DataFrame(rows).drop_duplicates(["id", "period"])
    X["cq"] = X["d0"].dt.to_period("Q"); X["m"] = X["d0"].dt.to_period("M")
    print(f"이벤트 {len(X)}건, 회사 {X['id'].nunique()}개, D0 {X['d0'].min().date()}~{X['d0'].max().date()}")
    print("전체 평균 CAR:", {k: f"{X[k].mean():.2%}" for k in WIN}, "\n")
    out = []
    for s in SIGS:
        Y = X.dropna(subset=[s]).copy()
        Y = Y[Y.groupby("cq")[s].transform("size") >= 15]
        Y["g"] = Y.groupby("cq")[s].transform(lambda v: pd.qcut(v.rank(method="first"), 3, labels=False))
        print(f"[{s}] 이벤트 {len(Y)}건")
        for w in WIN:
            z = Y.dropna(subset=[w])
            gm = z.groupby(["m", "g"])[w].mean().unstack().reindex(columns=[0, 1, 2])
            sp = (gm[2] - gm[0]).dropna()
            if len(sp) < 12: print(f"  {w}: 월 부족"); continue
            t = sp.mean() / (sp.std(ddof=1) / np.sqrt(len(sp)))
            h = len(sp) // 2
            same = np.sign(sp.iloc[:h].mean()) == np.sign(sp.iloc[h:].mean())
            ok = abs(t) >= 3.0 and same
            lo, hi = z.loc[z.g == 0, w].mean(), z.loc[z.g == 2, w].mean()
            print(f"  {w:12s} 하위 {lo:+.2%} 상위 {hi:+.2%} 스프레드 {sp.mean():+.2%} t={t:+.2f} 전/후반 부호 {'같음' if same else '다름'} → {'합격' if ok else '불합격'}")
    X.to_csv(D / "event_study_panel.csv", index=False)


if __name__ == "__main__":
    main()
