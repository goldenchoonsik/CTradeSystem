"""대형주(매일 직전 60일 거래대금 상위 30) 대표 알고리즘 비교 — 개발 구간만, 같은 비용·같은 포트폴리오 규칙.

탐색용(사전등록 판정 아님). 파라미터는 교과서 기본값 그대로, 튜닝 없음.
- 일봉: 신호는 D 종가, 체결은 D+1 시가 (수정주가 일봉)
- 분봉: 신호 다음 분 시가 진입, 하루 안 청산 (event_engine.load_day — 원장 수정 계수)
- 포트폴리오: 자본을 30칸으로 나눠 한 종목 = 1칸(1/30), 일별 평가
- 비용: 왕복 0.27% + 1틱(실제 가격 기준)

  .venv/bin/python docs/research/20261011_vol_spike/compare_algos.py
"""
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import event_engine as ee  # noqa: E402
from evaluate_events import tick_size  # noqa: E402

STAGE = ee.STAGE
START, END = pd.Timestamp("2024-11-15"), pd.Timestamp("2026-05-29")
TOPN, SLOTS, RT = 30, 30, 0.0027


def load_daily():
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv"))
    uni = uni[(uni.is_preferred == 0) & (uni.is_spac == 0)]
    frames = []
    for c in uni.code:
        a = pd.read_csv(os.path.join(STAGE, "raw", "creon", "candle_1d", f"{c}_1d.csv"), parse_dates=["timestamp"]).drop_duplicates("timestamp")
        r = pd.read_csv(os.path.join(STAGE, "raw", "creon", "candle_1d_unadj", f"{c}_1d.csv"), parse_dates=["timestamp"]).drop_duplicates("timestamp")
        a = a.merge(r[["timestamp", "upbit_close"]].rename(columns={"upbit_close": "raw_close"}), on="timestamp", how="left")
        a["code"] = c
        frames.append(a)
    d = pd.concat(frames).rename(columns={"timestamp": "date", "upbit_open": "o", "upbit_high": "h", "upbit_low": "l",
                                          "upbit_close": "c", "upbit_volume": "v", "upbit_trade_price": "val"})
    return d[d.date <= END + pd.Timedelta(days=10)]


def top_membership(d, mode="value_top30"):
    if mode == "value_top30":
        val = d.pivot(index="date", columns="code", values="val")
        med = val.rolling(60, min_periods=40).median().shift(1)          # 직전 60일(당일 제외)
        rank = med.rank(axis=1, ascending=False)
        return rank <= TOPN
    # kosdaq_cap_50_100: 코스닥 시가총액 50~100위 — 시총 ≈ 전일 실제 종가 × 현재 상장주식수(과거 주식수 없음 → 근사)
    sh = pd.read_csv(os.path.join(STAGE, "shares_now.csv")).set_index("code")["shares"]
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv")).set_index("code")
    raw = d.pivot(index="date", columns="code", values="raw_close")
    kq = [c for c in raw.columns if uni.market.get(c) == "KOSDAQ"]
    cap = (raw[kq] * sh.reindex(kq)).shift(1)
    rank = cap.rank(axis=1, ascending=False)
    return ((rank >= 50) & (rank <= 100)).reindex(columns=raw.columns, fill_value=False)


# ---------------------------------------------------------------- 일봉 알고리즘 (신호 D 종가 → D+1 시가)
def daily_signals(g):
    c = g["c"]
    ma5, ma20 = c.rolling(5).mean(), c.rolling(20).mean()
    delta = c.diff()
    up, dn = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn)
    sd = c.rolling(20).std()
    hi20, lo10 = g["h"].rolling(20).max().shift(1), g["l"].rolling(10).min().shift(1)
    return {
        "MA 5/20 교차": (ma5 > ma20) & (ma5.shift(1) <= ma20.shift(1)), "MA 5/20 교차_x": (ma5 < ma20),
        "RSI(14) 30 매수": rsi < 30, "RSI(14) 30 매수_x": rsi > 50,
        "볼린저 하단 반등": c < ma20 - 2 * sd, "볼린저 하단 반등_x": c >= ma20,
        "돈치안 20일 돌파": c > hi20, "돈치안 20일 돌파_x": c < lo10,
    }


def run_daily(d, member):
    out = {}
    for code, g in d.groupby("code"):
        g = g.sort_values("date").set_index("date")
        sig = daily_signals(g)
        mem = member[code].reindex(g.index).fillna(False) if code in member else pd.Series(False, index=g.index)
        dates = g.index
        for name in [k for k in sig if not k.endswith("_x")]:
            ent, ex = sig[name].to_numpy(), sig[name + "_x"].to_numpy()
            i, n = 0, len(dates)
            while i < n - 1:
                if ent[i] and mem.iloc[i] and START <= dates[i] <= END:
                    j = i + 1
                    k = j
                    while k < n - 1 and not ex[k] and dates[k] < END:
                        k += 1
                    k_exit = min(k + 1, n - 1)                      # 청산 신호 다음날 시가
                    out.setdefault(name, []).append((code, dates[j], dates[k_exit], g["o"].iloc[j], g["o"].iloc[k_exit],
                                                     g["raw_close"].iloc[i]))
                    i = k_exit
                else:
                    i += 1
    return out


# ---------------------------------------------------------------- 분봉 알고리즘 (하루 안)
def intraday_one(args):
    code, days_in = args
    day = ee.load_day(code)
    if day is None:
        return []
    mats, tr = day.mats, day.mats["traded"]
    fac = day.factor
    rows = []
    ev = ee.compute_events(code, day)
    ev = ev[(ev.hyp == "H6a") & (ev.cfg == "k=2,x=0.05")]
    h6 = {r.date: r for r in ev.itertuples()}
    o30 = ee.m("09:30")
    for di, dt in enumerate(day.dates):
        if dt not in days_in or not day.usable[di]:
            continue
        c, o, h, lo, val = mats["c"][di], mats["o"][di], mats["h"][di], mats["l"][di], mats["val"][di]
        raw = lambda px: px / fac[di]
        # ORB 30분: 09:01~09:30 고가 돌파 → 다음 분 시가, 저가 이탈 또는 15:15 청산
        if tr[di, : o30 + 1].sum() >= 10:
            rh, rl = np.nanmax(np.where(tr[di, : o30 + 1], h[: o30 + 1], -np.inf)), np.nanmin(np.where(tr[di, : o30 + 1], lo[: o30 + 1], np.inf))
            for t in range(o30 + 1, ee.m("14:30")):
                if c[t] > rh:
                    j, px = ee.entry_price(mats, di, t)
                    if j is not None:
                        x = next((k for k in range(j, ee.EXIT_1515 + 1) if c[k] < rl), ee.EXIT_1515)
                        rows.append(("ORB 30분 돌파", code, dt, px, c[x], raw(px)))
                    break
        # VWAP 1% 이탈 회귀: 10:00~14:30 종가 < VWAP×0.99 → VWAP 복귀 또는 15:15
        vol = np.where(tr[di], val / np.where(c > 0, c, np.nan), 0.0)
        vwap = np.cumsum(val) / np.where(np.cumsum(vol) > 0, np.cumsum(vol), np.nan)
        for t in range(ee.m("10:00"), ee.m("14:30")):
            if tr[di, t] and c[t] < vwap[t] * 0.99:
                j, px = ee.entry_price(mats, di, t)
                if j is not None:
                    x = next((k for k in range(j, ee.EXIT_1515 + 1) if c[k] >= vwap[k]), ee.EXIT_1515)
                    rows.append(("VWAP 1% 이탈 회귀", code, dt, px, c[x], raw(px)))
                break
        if dt in h6:
            r = h6[dt]
            rows.append(("거래대금 폭증 모멘텀(H6a)", code, dt, r.entry_px, r.entry_px * (1 + r.r1515), r.entry_px_raw))
    return rows


def portfolio(trades, d_close, daily_index, intraday):
    """trades: (code, entry_date, exit_date, entry_px, exit_px, raw_px) — 1칸 = 1/SLOTS, 일별 평가 + 비용."""
    pnl = pd.Series(0.0, index=daily_index)
    gross, net = [], []
    for code, ed, xd, ep, xp, rawp in trades:
        cost = RT + tick_size([rawp])[0] / rawp
        g = xp / ep - 1
        gross.append(g)
        net.append(g - cost)
        if intraday or ed == xd:
            if ed in pnl.index:
                pnl[ed] += (g - cost) / SLOTS
            continue
        cl = d_close[code].loc[ed:xd]
        path = np.r_[ep, cl.iloc[:-1].to_numpy(), xp]          # 진입 시가 → 매일 종가 → 청산 시가
        dr = path[1:] / path[:-1] - 1
        idx = cl.index
        for k, dt in enumerate(idx):
            if dt in pnl.index:
                pnl[dt] += dr[k] / SLOTS
        if idx[-1] in pnl.index:
            pnl[idx[-1]] -= cost / SLOTS
    eq = (1 + pnl).cumprod()
    yrs = len(pnl) / 250
    return {"거래 수": len(gross), "거래당 비용 전": np.mean(gross) * 100 if gross else np.nan,
            "거래당 비용 후": np.mean(net) * 100 if net else np.nan, "승률(비용 후)": np.mean(np.array(net) > 0) if net else np.nan,
            "연수익": (eq.iloc[-1] ** (1 / yrs) - 1) * 100, "샤프": pnl.mean() / pnl.std() * np.sqrt(250) if pnl.std() > 0 else np.nan,
            "최대낙폭": (eq / eq.cummax() - 1).min() * 100}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "value_top30"
    d = load_daily()
    member = top_membership(d, mode)
    d_close = d.pivot(index="date", columns="code", values="c")
    idx = d_close.index[(d_close.index >= START) & (d_close.index <= END)]
    ever = sorted(member.columns[member.loc[START:END].any()])
    print(f"[{mode}] 구간 {START:%Y-%m-%d}~{END:%Y-%m-%d} ({len(idx)}일), 대상에 한 번이라도 든 종목 {len(ever)}")

    rows = []
    # 보유 기준선: 그날 상위 30 동일가중(종가→종가, 비용 없음)
    ret = d_close.pct_change()
    m = member.reindex(ret.index).fillna(False).astype(bool)
    bh = ret.where(m).mean(axis=1).reindex(idx).fillna(0)
    eq = (1 + bh).cumprod()
    rows.append(("[기준] 대상 종목 동일가중 보유", {"거래 수": 0, "연수익": (eq.iloc[-1] ** (250 / len(idx)) - 1) * 100,
                                         "샤프": bh.mean() / bh.std() * np.sqrt(250), "최대낙폭": (eq / eq.cummax() - 1).min() * 100}))
    for name, tr in run_daily(d, member).items():
        rows.append((f"[일봉] {name}", portfolio([t for t in tr if t[0] in ever], d_close, idx, False)))

    m1 = set(os.listdir(ee.M1_DIR))
    have = [c for c in ever if c in m1]
    days_in = {c: set(member.index[member[c] & (member.index >= START) & (member.index <= END)]) for c in have}
    with Pool(4) as p:
        res = p.map(intraday_one, [(c, days_in[c]) for c in have])
    by = {}
    for r in res:
        for name, code, dt, ep, xp, rawp in r:
            by.setdefault(name, []).append((code, dt, dt, ep, xp, rawp))
    print(f"분봉 알고리즘: 1분봉 받은 종목 {len(have)}/{len(ever)} (수집 진행 중)")
    for name in ("ORB 30분 돌파", "VWAP 1% 이탈 회귀", "거래대금 폭증 모멘텀(H6a)"):
        rows.append((f"[분봉] {name}", portfolio(by.get(name, []), d_close, idx, True)))

    cols = ["거래 수", "거래당 비용 전", "거래당 비용 후", "승률(비용 후)", "연수익", "샤프", "최대낙폭"]
    t = pd.DataFrame({k: v for k, v in rows}).T.reindex(columns=cols)
    pd.set_option("display.width", 200)
    print(t.to_string(float_format=lambda x: f"{x:,.2f}"))


if __name__ == "__main__":
    main()
