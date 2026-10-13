"""H1~H4 판정 (PREREGISTRATION.md) — 개발 구간만, 날짜별 동일가중 포트폴리오 t.

  .venv/bin/python docs/research/20261011_vol_spike/evaluate_events.py [--events path] [--holdout]
--holdout 은 개발 구간 합격 구성만 봉인 구간에서 1회 시험할 때만 쓴다.
"""
import argparse
import os

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
DEV_END = pd.Timestamp("2026-05-29")
HOLD_START = pd.Timestamp("2026-06-01")
RT_COST = 0.0027
SLIPS = [0.0, 0.001, 0.003, 0.005]
JUDGE = 0.003
EXITS = ["r5", "r15", "r30", "r60", "r1515"]
# KRX 호가 단위 (2023-01-25 부터 KOSPI·KOSDAQ 공통): (가격 하한, 단위)
TICK_TABLE = [(500_000, 1000), (200_000, 500), (50_000, 100), (20_000, 50), (5_000, 10), (2_000, 5), (0, 1)]


def tick_size(px):
    px = np.asarray(px, dtype=float)
    out = np.ones_like(px)
    for floor, unit in reversed(TICK_TABLE):
        out = np.where(px >= floor, unit, out)
    return out


def judge_slip(ev):
    """판정 슬리피지 = max(0.3%, 왕복 2틱/진입가) — 저가주의 호가 단위 비용을 과소평가하지 않게."""
    px = ev["entry_px_raw"] if "entry_px_raw" in ev else ev["entry_px"]      # 호가 단위는 그날 실제 가격 기준
    return np.maximum(JUDGE, 2 * tick_size(px) / px)


def stats(ev, col, slip):
    x = ev.dropna(subset=[col])
    if isinstance(slip, pd.Series):
        slip = slip.loc[x.index]
    net = x[col] - RT_COST - slip
    by_day = net.groupby(x["date"]).mean()
    t = by_day.mean() / by_day.std(ddof=1) * np.sqrt(len(by_day)) if len(by_day) > 2 else np.nan
    return net.mean(), by_day, t, len(x)


def judge(ev, col, end):
    m, by_day, t, n = stats(ev, col, pd.Series(judge_slip(ev), index=ev.index))
    if n == 0:
        return None
    q = by_day.groupby(by_day.index.to_period("Q")).mean()
    last3 = by_day[by_day.index > end - pd.DateOffset(months=3)].mean()
    gross = ev[col].mean()
    ok = (m > 0) and (t > 3) and ((q > 0).mean() >= 2 / 3) and (last3 > 0)
    return {"n": n, "days": by_day.size, "gross%": gross * 100, "slip%": judge_slip(ev).mean() * 100,
            "net%@judge": m * 100, "t@judge": t,
            "t@0": stats(ev, col, 0.0)[2], "net%@0": (gross - RT_COST) * 100,
            "q+": f"{int((q > 0).sum())}/{len(q)}", "last3M%": last3 * 100, "win": (ev[col] > 0).mean(), "PASS": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default=os.path.join(STAGE, "events", "events.parquet"))
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()
    ev = pd.read_parquet(a.events)
    pool = pd.read_csv(os.path.join(STAGE, "pool_daily_top100.csv"), parse_dates=["date"])[["date", "code"]]
    pool["in_pool"] = True
    ev = ev.merge(pool, on=["date", "code"], how="left")
    ev["in_pool"] = ev["in_pool"].fillna(False).astype(bool)
    if a.holdout:
        ev, end = ev[ev["date"] >= HOLD_START], ev["date"].max()
    else:
        ev, end = ev[ev["date"] <= DEV_END], DEV_END
    rows = []
    for scope, sub in [("pool", ev[ev["in_pool"]]), ("all", ev)]:
        for mkt in ("ALL", "KOSPI", "KOSDAQ"):
            s2 = sub if mkt == "ALL" else sub[sub["market"] == mkt]
            for (hyp, cfg), g in s2.groupby(["hyp", "cfg"]):
                for col in EXITS:
                    r = judge(g, col, end)
                    if r:
                        rows.append({"scope": scope, "market": mkt, "hyp": hyp, "cfg": cfg, "exit": col, **r})
    res = pd.DataFrame(rows)
    out = os.path.join(STAGE, "events", "judge_holdout.csv" if a.holdout else "judge_dev.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    res.to_csv(out, index=False)
    pd.set_option("display.width", 220)
    main_tbl = res[(res["scope"] == "pool") & (res["market"] == "ALL")]
    print(main_tbl.drop(columns=["scope", "market"]).round(3).to_string(index=False))
    print(f"\n합격(전 범위): {int(res['PASS'].sum())} / {len(res)} → {out}")
    if res["PASS"].any():
        print(res[res["PASS"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
