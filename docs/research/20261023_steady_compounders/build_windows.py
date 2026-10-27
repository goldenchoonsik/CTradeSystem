"""가격 기반 창 패널 — (시작일, 종목) 마다 앞 3년 결과(SC/RW 분류)와 시작 시점 가격 특징.

출력 DataVault/staging/steady_compounders/derived/windows.parquet, starts.csv(시작일별 명단·빠진 수)
실행: .venv/bin/python -I build_windows.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sc_common import STAGE, members_at, membership, price_tr  # noqa: E402

OUT = os.path.join(STAGE, "derived")
END_DATA = pd.Timestamp("2026-08-31")


def r2_line(y):
    """log 총수익 y(1차원) 의 시간 직선 적합 R²."""
    n = len(y)
    x = np.arange(n, dtype=float)
    x -= x.mean()
    yc = y - y.mean()
    sxx = (x * x).sum()
    syy = (yc * yc).sum()
    if syy <= 0:
        return np.nan
    return float((x * yc).sum() ** 2 / (sxx * syy))


def win_stats(tr):
    """tr: 창 안 총수익 지수(양 끝 포함). → (연복리, 최대낙폭, R²)."""
    yrs = len(tr) / 252.0
    cagr = (tr[-1] / tr[0]) ** (1 / yrs) - 1
    mdd = float((tr / np.maximum.accumulate(tr) - 1).min())
    return cagr, mdd, r2_line(np.log(tr))


def main():
    memb = membership()
    from sc_common import load_raw
    cal = load_raw("SPY").loc["1996-01-01":END_DATA].index
    qends = pd.date_range("2000-03-31", "2026-06-30", freq="QE")   # 2023-07~ 는 시작 특징만(창 미완 → 결과 없음, 바구니 선정용)
    starts = [cal[cal <= q][-1] for q in qends]
    mem = {s: members_at(memb, s) for s in starts}
    need = sorted({y for s in starts for _, y in mem[s] if y} | {"SPY"})
    P = {}
    for t in need:
        d = price_tr(t, cal)
        if d is not None:
            P[t] = d
    print("필요", len(need), "가격 있음", len(P), flush=True)
    spy_r = np.log(P["SPY"]["tr"]).diff()
    pos = {d: i for i, d in enumerate(cal)}
    rows, srows = [], []
    for s in starts:
        i0 = pos[s]
        incomplete = s + pd.DateOffset(years=3) > END_DATA
        e = cal[cal <= min(s + pd.DateOffset(years=3), END_DATA)][-1]
        i1 = pos[e]
        ip = pos[cal[cal >= s - pd.DateOffset(years=3)][0]]
        cand = [(t, y) for t, y in mem[s] if y in P and pd.notna(P[y]["tr"].iloc[i0]) and P[y]["obs"].iloc[max(i0 - 5, 0):i0 + 1].any()]
        dv = {y: P[y]["dvol"].iloc[i0] for _, y in cand}
        floor = 0.10 * np.nanmedian(list(dv.values()))
        n_memb = len(mem[s])
        ok = [(t, y) for t, y in cand if dv[y] >= floor]
        srows.append({"start": s.date(), "명단": n_memb, "가격있음": len(ok), "빠짐": n_memb - len(ok),
                      "빠진비율": round(1 - len(ok) / n_memb, 3), "재사용제외": len(cand) - len(ok)})
        spy_c, spy_m, _ = (np.nan, np.nan, np.nan) if incomplete else win_stats(P["SPY"]["tr"].values[i0:i1 + 1])
        sr = spy_r.values[i0 - 503:i0 + 1]
        for t, y in ok:
            d = P[y]
            tr = d["tr"].values
            obs = d["obs"].values
            last_obs = np.where(obs[:i1 + 1])[0]
            ended = (len(last_obs) == 0) or (last_obs[-1] < i1 - 5)
            r = {"start": s, "ticker": y, "memb_ticker": t, "ended": ended and not incomplete, "incomplete": incomplete,
                 "spy_cagr": spy_c, "spy_mdd": spy_m}
            if not ended and not incomplete:
                r["cagr"], r["mdd"], r["r2"] = win_stats(tr[i0:i1 + 1])
            # 시작 시점 특징
            if np.isfinite(tr[ip]) and obs[ip:ip + 10].any():
                r["p_cagr"], r["p_mdd"], r["p_r2"] = win_stats(tr[ip:i0 + 1])
            lr = np.diff(np.log(tr[i0 - 252:i0 + 1]))
            if np.isfinite(lr).sum() > 200:
                r["vol1y"] = float(np.nanstd(lr) * np.sqrt(252))
            lr2 = np.diff(np.log(tr[i0 - 504:i0 + 1]))
            m = np.isfinite(lr2) & np.isfinite(sr)
            if m.sum() > 400:
                r["beta"] = float(np.cov(lr2[m], sr[m])[0, 1] / np.var(sr[m], ddof=1))
            if np.isfinite(tr[i0 - 252]):
                r["mom12_1"] = tr[i0 - 21] / tr[i0 - 252] - 1
            c = d["close"].values
            hi = np.nanmax(c[i0 - 251:i0 + 1])
            r["hi52"] = c[i0] / hi - 1
            r["divy"] = d["div12"].values[i0] / c[i0]
            r["dvol"] = dv[y]
            r["raw_close"] = d["raw_close"].values[i0]
            r["close"] = c[i0]
            r["end"] = e
            r["raw_close_end"] = d["raw_close"].values[i1]
            r["close_end"] = c[i1]
            rows.append(r)
    W = pd.DataFrame(rows)
    W["SC"] = ((W.cagr >= 0.15) & (W.mdd >= -0.20) & (W.r2 >= 0.80)).astype(float)
    W["RW"] = ((W.cagr >= 0.15) & (W.mdd < -0.30)).astype(float)
    W["p_SC"] = np.where(W.p_cagr.notna(), ((W.p_cagr >= 0.15) & (W.p_mdd >= -0.20) & (W.p_r2 >= 0.80)).astype(float), np.nan)
    W.loc[W.ended | W.incomplete, ["SC", "RW"]] = np.nan
    os.makedirs(OUT, exist_ok=True)
    W.to_parquet(os.path.join(OUT, "windows.parquet"))
    pd.DataFrame(srows).to_csv(os.path.join(OUT, "starts.csv"), index=False)
    print("창", len(W), "끊김", int(W.ended.sum()), "미완", int(W.incomplete.sum()), "| SC", int(W.SC.sum()), "RW", int(W.RW.sum()), flush=True)


if __name__ == "__main__":
    main()
