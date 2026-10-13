"""H5: 급등·거래량 폭증일 종가 매수 → 익일 시가(보조: 익일 종가) 매도. 개발 구간만 (PREREGISTRATION.md).

  .venv/bin/python docs/research/20261011_vol_spike/h5_overnight.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from candidate_pool import STAGE, build_pool, load_panel  # noqa: E402

DEV_END = pd.Timestamp("2026-05-29")
RT_COST = 0.0027
SLIPS = [0.0, 0.001, 0.003]
JUDGE_SLIP = 0.001


def daily_stats(ev, col, slip):
    net = ev[col] - RT_COST - slip
    by_day = net.groupby(ev["date"]).mean()
    t = by_day.mean() / by_day.std(ddof=1) * np.sqrt(len(by_day)) if len(by_day) > 2 else np.nan
    return net.mean(), by_day, t


def report(ev, label, col="ret_on"):
    if len(ev) == 0:
        print(f"  {label}: 이벤트 0")
        return
    gross = ev[col].mean()
    parts = []
    for s in SLIPS:
        m, by_day, t = daily_stats(ev, col, s)
        parts.append(f"slip{s*100:.1f}%: {m*100:+.3f}% t{t:+.2f}")
    _, by_day, t = daily_stats(ev, col, JUDGE_SLIP)
    q = by_day.groupby(by_day.index.to_period("Q")).mean()
    last3 = by_day[by_day.index >= DEV_END - pd.DateOffset(months=3)].mean()
    print(f"  {label}: n={len(ev)} 일수={ev['date'].nunique()} 총 {gross*100:+.3f}% 승률 {(ev[col] > 0).mean():.0%} | "
          + " | ".join(parts)
          + f" | 분기+ {int((q > 0).sum())}/{len(q)} | 최근3M {last3*100:+.3f}%")


def main():
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv")).set_index("code")
    uni = uni[(uni["is_preferred"] == 0) & (uni["is_spac"] == 0)]
    p = load_panel(uni)
    g = p.groupby("code", sort=False)
    p["ret_d"] = p["close"] / p["prev_close"] - 1
    p["vol20"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    p["next_open"] = g["open"].shift(-1)
    p["next_close"] = g["close"].shift(-1)
    p["next_date"] = g["date"].shift(-1)
    p["ret_on"] = p["next_open"] / p["close"] - 1          # 익일 시가 청산
    p["ret_1d"] = p["next_close"] / p["close"] - 1         # 보조: 익일 종가 청산
    p = p[(p["date"] <= DEV_END) & p["next_open"].notna() & (p["next_date"] <= DEV_END + pd.Timedelta(days=5))]
    p = p[(p["prev_close"] >= 1000) & (p["volume"] > 0) & p["vol20"].notna()]
    pool = build_pool(load_panel(uni), 100)[["date", "code"]]
    pool["in_pool"] = True
    p = p.merge(pool, on=["date", "code"], how="left")
    p["in_pool"] = p["in_pool"].fillna(False).astype(bool)
    p["vmult"] = p["volume"] / p["vol20"]
    print(f"개발 구간 {p['date'].min():%Y-%m-%d}~{p['date'].max():%Y-%m-%d}, 종목-일 {len(p):,}")

    print("\n[대조] 조건 없는 오버나잇 (종가→익일 시가)")
    report(p[p["in_pool"]], "후보 풀 전체")
    report(p, "전 유니버스")

    for scope, base in [("후보 풀", p[p["in_pool"]]), ("전 유니버스", p)]:
        print(f"\n[H5] {scope} — 익일 시가 청산 (판정 슬리피지 {JUDGE_SLIP*100:.1f}%)")
        for x in (0.05, 0.10, 0.15):
            for k in (3, 5):
                ev = base[(base["ret_d"] >= x) & (base["ret_d"] < 0.295) & (base["vmult"] >= k)]
                report(ev, f"X{x:.0%} k{k}")
    print("\n[H5 보조] 후보 풀 — 익일 종가 청산")
    base = p[p["in_pool"]]
    for x in (0.05, 0.10, 0.15):
        for k in (3, 5):
            ev = base[(base["ret_d"] >= x) & (base["ret_d"] < 0.295) & (base["vmult"] >= k)]
            report(ev, f"X{x:.0%} k{k}", col="ret_1d")
    print("\n[H5 시장별] 후보 풀 X10% k3 — 익일 시가")
    ev = base[(base["ret_d"] >= 0.10) & (base["ret_d"] < 0.295) & (base["vmult"] >= 3)]
    for m in ("KOSPI", "KOSDAQ"):
        report(ev[ev["market"] == m], m)
    print("\n[참고] 상한가 마감(≥29.5%, 매수 체결 어려움) 후보 풀 — 익일 시가")
    report(base[base["ret_d"] >= 0.295], "상한가")


if __name__ == "__main__":
    main()
