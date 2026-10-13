"""새벽 나스닥 → K200 장중 판정 (PREREGISTRATION.md).

  .venv/bin/python docs/research/20261011_nasdaq_filter/run_nq.py
"""
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "docs", "research", "20261011_structural_c"))
from run_c import load_futures  # noqa: E402  (같은 제외일 규칙)

NQ = os.path.join(REPO, "DataVault", "normalized", "candles_1m", "US_TECH_100_1m.parquet")
COST = 0.0005
RECENT_FROM, VAL_FROM = pd.Timestamp("2025-10-09"), pd.Timestamp("2024-01-01")


def nq_overnight(days, prev_days):
    d = pd.read_parquet(NQ, columns=["timestamp", "close"])
    s = d.set_index(pd.to_datetime(d["timestamp"]))["close"].sort_index()
    def at(ts):
        return s.reindex(pd.DatetimeIndex(ts), method="ffill", tolerance=pd.Timedelta(hours=5)).to_numpy()
    now = at(days + pd.Timedelta(hours=8, minutes=55))
    then = at(prev_days + pd.Timedelta(hours=15, minutes=29))
    return pd.Series(now / then - 1, index=days)


def judge(r: pd.Series):
    r = r.dropna()
    if len(r) < 10:
        return {"n": len(r), "PASS": False}
    t = r.mean() / r.std(ddof=1) * np.sqrt(len(r))
    yr = r.groupby(r.index.year).agg(["sum", "size"])
    yr = yr[yr["size"] >= 10]
    v = r[r.index >= VAL_FROM]
    vt = v.mean() / v.std(ddof=1) * np.sqrt(len(v)) if len(v) > 2 else np.nan
    rec = r[r.index >= RECENT_FROM].sum()
    ok = r.mean() > 0 and t > 3 and len(yr) and (yr["sum"] > 0).mean() >= 2 / 3 and rec > 0 and v.mean() > 0 and vt > 2
    return {"n": len(r), "mean%": r.mean() * 100, "t": t, "win": (r > 0).mean(), "years": f"{int((yr['sum'] > 0).sum())}/{len(yr)}",
            "recent%": rec * 100, "val_n": len(v), "val_mean%": v.mean() * 100, "val_t": vt, "PASS": bool(ok)}


def main():
    piv, prev_last, ok = load_futures()
    days = piv.index
    prev_close1530 = piv[1530].shift(1)
    prev_day = pd.Series(days, index=days).shift(1)
    use = ok & ok.shift(1, fill_value=False) & prev_day.notna() & (days >= "2020-01-02")
    days_u = days[use.to_numpy()]
    q = nq_overnight(days_u, pd.DatetimeIndex(prev_day[use]))
    gap = (piv[901] / prev_close1530 - 1)[use]
    fwd = {"10:00": (piv[1000] / piv[901] - 1)[use], "15:30": (piv[1530] / piv[901] - 1)[use]}
    df = pd.DataFrame({"q": q, "gap": gap, **{f"f{k}": v for k, v in fwd.items()}}).dropna()
    print(f"표본 {df.index.min():%Y-%m-%d}~{df.index.max():%Y-%m-%d} {len(df)}일")
    print(f"N1 corr(q, gap) = {df.q.corr(df.gap):+.3f}  회귀 기울기 {np.polyfit(df.q, df.gap, 1)[0]:.3f}  "
          f"corr(q, 09:01→10:00) = {df.q.corr(df['f10:00']):+.3f}  corr(q, 09:01→15:30) = {df.q.corr(df['f15:30']):+.3f}")
    print(f"   참고: 조건 없는 롱 09:01→15:30 거래당 {df['f15:30'].mean()*100:+.3f}% (비용 전)")
    # N3 잔차: 과거 250일만
    beta, sig = [], []
    for i in range(len(df)):
        h = df.iloc[max(0, i - 250):i]
        if len(h) < 120:
            beta.append(np.nan); sig.append(np.nan); continue
        b = np.polyfit(h.q, h.gap, 1)
        beta.append(b[0]); sig.append((h.gap - np.polyval(b, h.q)).std())
        df.loc[df.index[i], "a"] = b[1]
    df["beta"], df["sig"] = beta, sig
    df["e"] = df.gap - (df["a"] + df.beta * df.q)

    rows = []
    for X in (0.005, 0.010):
        for ex in ("10:00", "15:30"):
            f = df[f"f{ex}"]
            rows.append((f"N2 q>+{X:.1%} 롱 →{ex}", judge((f - COST)[df.q > X])))
            rows.append((f"N2 q<-{X:.1%} 숏 →{ex}", judge((-f - COST)[df.q < -X])))
    for k in (1.0, 1.5):
        for ex in ("10:00", "15:30"):
            f = df[f"f{ex}"]
            rows.append((f"N3 e<-{k}σ 롱 →{ex}", judge((f - COST)[df.e < -k * df.sig])))
            rows.append((f"N3 e>+{k}σ 숏 →{ex}", judge((-f - COST)[df.e > k * df.sig])))
    for name, s in rows:
        if "t" not in s:
            print(f"{name:24s} n={s['n']} 표본 부족 ❌"); continue
        print(f"{name:24s} n={s['n']:4d} 순 {s['mean%']:+.3f}% t{s['t']:+.2f} 승률 {s['win']:.0%} 연도+ {s['years']} "
              f"최근12M {s['recent%']:+.2f}% | 검증(2024~) n={s['val_n']} {s['val_mean%']:+.3f}% t{s['val_t']:+.2f} "
              + ("✅" if s["PASS"] else "❌"))
    print(f"\n합격 {sum(s['PASS'] for _, s in rows)} / {len(rows)}")


if __name__ == "__main__":
    main()
