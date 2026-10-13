"""'당일청산 4대 필승 조건' 시뮬레이션 (PREREGISTRATION.md).

  .venv/bin/python docs/research/20261011_intraday_rules/run_rules.py
"""
import os

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
N = os.path.join(REPO, "DataVault", "normalized")
GRID = [int(f"{t.components.hours:02d}{t.components.minutes:02d}") for t in pd.timedelta_range("09:01:00", "15:20:00", freq="1min")]
POS = {t: i for i, t in enumerate(GRID)}
RECENT = pd.Timestamp("2025-10-09")


def piv(path, col):
    d = pd.read_parquet(path)
    ts = pd.to_datetime(d["timestamp"])
    d["day"], d["tm"] = ts.dt.normalize(), ts.dt.hour * 100 + ts.dt.minute
    p = d.pivot_table(index="day", columns="tm", values=col, aggfunc="last")
    return p.reindex(columns=GRID)


def load_etf(sym):
    path = os.path.join(N, "candles_1m", f"{sym}_1m.parquet")
    o, h, l, c, v = (piv(path, k) for k in ("open", "high", "low", "close", "volume"))
    first = pd.read_parquet(path, columns=["timestamp"])
    t = pd.to_datetime(first["timestamp"])
    regular = (t.groupby(t.dt.normalize()).min().dt.strftime("%H:%M") == "09:01")
    traded = c.notna()
    c = c.ffill(axis=1)
    o, h, l = (x.where(traded, c) for x in (o, h, l))
    v = v.fillna(0)
    return {"o": o, "h": h, "l": l, "c": c, "v": v, "traded": traded}, regular.reindex(c.index).fillna(False)


def cost(px):
    return 0.0003 + 5.0 / px


def simulate(m, days, signal_fn, exit_rule, start, end, max_trades=2, extra=None):
    """signal_fn(day, t_idx) -> (stop, target) 또는 None. 반환: 거래 목록(day, ret_net)."""
    out = []
    c, o, h, l, tr = m["c"], m["o"], m["h"], m["l"], m["traded"]
    i1515 = POS[1515]
    for day in days:
        n, t = 0, POS[start]
        while t <= POS[end] and n < max_trades:
            sig = signal_fn(day, t)
            if sig is None:
                t += 1
                continue
            j = next((k for k in range(t + 1, min(t + 4, i1515)) if tr.at[day, GRID[k]]), None)
            if j is None:
                t += 1
                continue
            px = o.at[day, GRID[j]]
            stop, target, hold = sig
            x_px, x = None, i1515
            last = i1515 if hold is None else min(j + hold - 1, i1515)
            for k in range(j, last + 1):
                lo_, hi_ = l.at[day, GRID[k]], h.at[day, GRID[k]]
                if exit_rule >= 2 and stop is not None and lo_ <= stop:
                    x_px, x = min(stop, o.at[day, GRID[k]]) if k > j else stop, k
                    break
                if exit_rule >= 3 and target is not None and hi_ >= target:
                    x_px, x = max(target, o.at[day, GRID[k]]) if k > j else target, k
                    break
            if x_px is None:
                x, x_px = last, c.at[day, GRID[last]]
            out.append((day, x_px / px - 1 - cost(px)))
            n += 1
            t = x + 1
    return out


def judge(trades, baseline=None):
    if not trades:
        return {"n": 0, "PASS": False}
    s = pd.Series([r for _, r in trades], index=pd.DatetimeIndex([d for d, _ in trades]))
    dly = s.groupby(level=0).sum()
    t = dly.mean() / dly.std(ddof=1) * np.sqrt(len(dly)) if len(dly) > 2 else np.nan
    q = dly.groupby(dly.index.to_period("Q")).sum()
    rec = s[s.index >= RECENT].sum()
    ok = s.mean() > 0 and t > 3 and (q > 0).mean() >= 2 / 3 and rec > 0 and (baseline is None or s.mean() > baseline)
    eq = (1 + dly).cumprod()
    return {"n": len(s), "mean%": s.mean() * 100, "t": t, "win": (s > 0).mean(), "q+": f"{int((q > 0).sum())}/{len(q)}",
            "recent%": rec * 100, "total%": (eq.iloc[-1] - 1) * 100, "mdd%": (eq / eq.cummax() - 1).min() * 100, "PASS": bool(ok)}


def fmt(name, s):
    if not s["n"]:
        return f"{name:44s} 거래 0"
    return (f"{name:44s} n={s['n']:4d} 거래당 {s['mean%']:+.3f}% t{s['t']:+.2f} 승률 {s['win']:.0%} 분기+ {s['q+']} "
            f"최근12M {s['recent%']:+.1f}% 누적 {s['total%']:+.1f}% 낙폭 {s['mdd%']:.1f}% " + ("✅" if s["PASS"] else "❌"))


def main():
    fut = piv(os.path.join(N, "macro", "10100_1m.parquet"), "close").ffill(axis=1, limit=5)
    idx = piv(os.path.join(N, "macro", "U180_1m.parquet"), "close").ffill(axis=1, limit=5)
    basis = fut - idx.reindex(fut.index)
    z = basis.T.rolling(60, min_periods=15).apply(lambda x: (x[-1] - x.mean()) / (x.std() + 1e-8), raw=True).T
    f5 = fut / fut.shift(5, axis=1) - 1
    for sym in ("069500", "122630"):
        m, regular = load_etf(sym)
        days = [d for d in m["c"].index if regular.get(d, False) and d in fut.index and m["traded"].loc[d].sum() > 300]
        vol_share = (m["v"].loc[days, [t for t in GRID if t <= 1000]].sum(axis=1) / m["v"].loc[days].sum(axis=1)).median()
        print(f"\n######## {sym}: {len(days)}일 {days[0]:%Y-%m-%d}~{days[-1]:%Y-%m-%d} | 09:00~10:00 거래량 비중(중앙) {vol_share:.0%}")
        base = []
        for d in days:
            px = m["o"].at[d, 916]
            base.append((d, m["c"].at[d, 1515] / px - 1 - cost(px)))
        bj = judge(base)
        print(fmt("[기준선] 09:16 매수 → 15:15 (조건 없음)", bj))
        vavg = m["v"].rolling(20, min_periods=15).mean().shift(1)
        orh = m["h"][[t for t in GRID if t <= 915]].max(axis=1)
        orl = m["l"][[t for t in GRID if t <= 915]].min(axis=1)

        def sig_a(day, t):
            tm = GRID[t]
            if m["c"].at[day, tm] > orh[day] and m["v"].at[day, tm] >= 2 * (vavg.at[day, tm] if np.isfinite(vavg.at[day, tm]) else np.inf):
                return orl[day], orh[day] + 2 * (orh[day] - orl[day]), None
            return None

        for e, lbl in ((1, "15:15 보유"), (2, "손절"), (3, "손절+익절")):
            print(fmt(f"[A ORB] {lbl}", judge(simulate(m, days, sig_a, e, 916, 1015), bj["mean%"] / 100)))

        def make_b(hold):
            def sig_b(day, t):
                tm = GRID[t]
                if day in z.index and z.at[day, tm] > 1.5 and f5.at[day, tm] > 0:
                    px = m["c"].at[day, tm]
                    return px * 0.995, px * 1.01, hold
                return None
            return sig_b
        for e, lbl, hold in ((1, "15:15 보유", None), (2, "손절 −0.5%", None), (3, "손절+익절 +1%", None), (1, "+15분 청산", 15), (1, "+30분 청산", 30)):
            print(fmt(f"[B 선물 선행] {lbl}", judge(simulate(m, days, make_b(hold), e, 910, 1015), bj["mean%"] / 100)))


if __name__ == "__main__":
    main()
