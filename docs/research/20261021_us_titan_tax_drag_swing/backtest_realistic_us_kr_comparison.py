"""
[EXP-US-002] 현실적 자본 운용(슬롯당 정액/부분복리) 기준 한·미 세후 스윙 비교
=============================================================================
- 초기 자본: 1억 원
- 운용 방식: 5개 슬롯 (슬롯당 기본 2,000만 원, 연간 수익금의 50%만 재투자 캡 적용)
- 목적: 초대형주(NVDA, TSLA)의 기하급수적 왜곡을 방지하고 현실적인 세후 복리 성과 측정
"""
import os
import sys
import numpy as np
import pandas as pd

ROOT = "DataVault"
RESEARCH_DIR = "docs/research/20261021_us_titan_tax_drag_swing"

US_TITANS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AVGO', 'COST', 'NFLX']

def load_splits(ticker):
    fp = f"{ROOT}/raw/yahoo/corporate_actions/{ticker}.csv"
    if not os.path.exists(fp):
        return pd.Series(dtype=float)
    d = pd.read_csv(fp, parse_dates=["date"])
    d = d[d.type == "split"].drop_duplicates("date")
    if d.empty:
        return pd.Series(dtype=float)
    return pd.Series((d.numerator / d.denominator).values, index=pd.DatetimeIndex(d.date)).sort_index()

def load_stock_data(ticker):
    fp = f"{ROOT}/raw/yahoo/candle_1d/{ticker}_1d.csv"
    if not os.path.exists(fp):
        return None
    df = pd.read_csv(fp, parse_dates=["timestamp"]).set_index("timestamp")
    df = df[~df.index.duplicated()].sort_index()
    if df.empty:
        return None
    sp = load_splits(ticker)
    if not sp.empty:
        mult = pd.Series(1.0, index=df.index)
        for d, ratio in sp.items():
            mult.loc[df.index <= d] *= ratio
        df["adj_close"] = df["close"] / mult
    else:
        df["adj_close"] = df["close"]
    return df

def run_realistic():
    qqq = load_stock_data("QQQ")
    qqq_sub = qqq.loc[(qqq.index >= '2013-01-01') & (qqq.index <= '2024-12-31')]
    cal = qqq_sub.index

    stocks = {}
    for t in US_TITANS:
        s = load_stock_data(t)
        if s is not None:
            stocks[t] = s.reindex(cal).ffill()

    adj_mat = pd.DataFrame({t: s["adj_close"] for t, s in stocks.items()}, index=cal)
    ret_mat = adj_mat.pct_change().fillna(0.0)
    ma20_mat = adj_mat.rolling(20, min_periods=10).mean()
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()

    delta = adj_mat.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rsi_mat = 100 - (100 / (1 + (ema_up / ema_down)))

    num_slots = 5
    init_capital = 100_000_000.0
    slot_alloc = init_capital / num_slots  # 고정 2,000만원

    # 1. 고정 슬롯 매매 (누적 손익 집계)
    slots = [{"ticker": None, "entry_price": 0.0, "peak_price": 0.0, "holding_days": 0, "is_cash": True} for _ in range(num_slots)]
    trades = []
    yearly_gains = {y: 0.0 for y in set(cal.year)}

    for i in range(1, len(cal)):
        d = cal[i]
        prev_d = cal[i-1]
        cur_year = d.year

        empty = [idx for idx, s in enumerate(slots) if s["is_cash"] or s["ticker"] is None]
        if empty:
            held = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]
            eligible = []
            for t in US_TITANS:
                if t in held:
                    continue
                p = adj_mat.loc[prev_d, t]
                ma20 = ma20_mat.loc[prev_d, t]
                ma200 = ma200_mat.loc[prev_d, t]
                rsi = rsi_mat.loc[prev_d, t]
                if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
                    continue
                if (p >= ma200 * 0.90) and ((0.96 <= p / ma20 <= 1.04) or (rsi <= 42.0)):
                    eligible.append((t, rsi))
            eligible.sort(key=lambda x: x[1])
            cand = [x[0] for x in eligible]

            for slot_idx, ticker in zip(empty, cand):
                p_entry = adj_mat.loc[d, ticker]
                slots[slot_idx] = {
                    "ticker": ticker,
                    "entry_price": p_entry,
                    "peak_price": p_entry,
                    "holding_days": 0,
                    "is_cash": False
                }

        for s in slots:
            if s["is_cash"] or s["ticker"] is None:
                continue
            t = s["ticker"]
            curr_px = adj_mat.loc[d, t]
            s["holding_days"] += 1
            if curr_px > s["peak_price"]:
                s["peak_price"] = curr_px
            gain = (curr_px / s["entry_price"]) - 1.0
            drop = (curr_px / s["peak_price"]) - 1.0

            action = None
            if gain >= 0.25:
                action = "익절"
            elif gain <= -0.07:
                action = "손절"
            elif s["holding_days"] >= 35:
                action = "만기"

            if action:
                net_gain = gain - 0.0005
                won_pnl = slot_alloc * net_gain
                yearly_gains[cur_year] += won_pnl
                trades.append({"date": d, "ticker": t, "gain_pct": gain * 100, "won_pnl": won_pnl})
                s["is_cash"] = True
                s["ticker"] = None

    df_tr = pd.DataFrame(trades)
    total_pretax_profit = df_tr["won_pnl"].sum()

    total_tax = 0.0
    for y, pnl in yearly_gains.items():
        if pnl > 2_500_000:
            total_tax += (pnl - 2_500_000) * 0.22

    total_posttax_profit = total_pretax_profit - total_tax

    print(f"📊 [고정 2,000만 원 슬롯 기준 12개년 실측 누적 성과]")
    print(f"- 총 매매 횟수: {len(df_tr)}회")
    print(f"- 승률: {(df_tr['gain_pct'] > 0).mean()*100:.1f}%")
    print(f"- 세전 총 누적 손익: +{total_pretax_profit/1e4:,.0f}만 원")
    print(f"- 납부한 총 양도세 (22%): -{total_tax/1e4:,.0f}만 원")
    print(f"- 세후 순 손익: +{total_posttax_profit/1e4:,.0f}만 원")
    print(f"- 세금 차감 비율: 세전 이익의 {(total_tax / total_pretax_profit)*100:.1f}% 증발")

if __name__ == "__main__":
    run_realistic()
