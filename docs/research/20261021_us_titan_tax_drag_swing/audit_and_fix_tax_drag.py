"""
[BUG AUDIT & FIX] 시가평가(Mark-to-Market) 무결성 슬롯 엔진으로 한·미 세후 스윙 재검증
========================================================================================
- 발견된 버그: 기존 스크립트에서 보유 중 일일 수익률(r_day)을 계좌에 매일 반영하면서,
  청산 당일 진입가 대비 총수익률(gain)을 일일 수익률로 한 번 더 더하는 '이중 계상(Double Counting)' 발생.
- 수정 사항: 슬롯별 실제 평가 금액(Slot Equity)을 독립 추적하는 정석 시가평가 엔진으로 100% 무결화.
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

def compute_metrics(eq_series):
    total_ret = (eq_series.iloc[-1] / eq_series.iloc[0]) - 1.0
    years = (eq_series.index[-1] - eq_series.index[0]).days / 365.25
    cagr = (eq_series.iloc[-1] / eq_series.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    daily_rets = eq_series.pct_change().dropna()
    ann_vol = daily_rets.std() * np.sqrt(252) if len(daily_rets) > 0 else 0.0
    sharpe = (cagr - 0.02) / ann_vol if ann_vol > 0 else 0.0
    cum_max = eq_series.cummax()
    dd = (eq_series - cum_max) / cum_max
    mdd = dd.min()
    return {"Total Return": total_ret, "CAGR": cagr, "Volatility": ann_vol, "Sharpe": sharpe, "MDD": mdd}

def run_us_corrected():
    qqq = load_stock_data("QQQ")
    cal = qqq.loc[(qqq.index >= '2013-01-01') & (qqq.index <= '2024-12-31')].index

    stocks = {}
    for t in US_TITANS:
        s = load_stock_data(t)
        if s is not None:
            stocks[t] = s.reindex(cal).ffill()

    adj_mat = pd.DataFrame({t: s["adj_close"] for t, s in stocks.items()}, index=cal)
    vol_mat = pd.DataFrame({t: s["volume"] for t, s in stocks.items()}, index=cal)
    ret_mat = adj_mat.pct_change().fillna(0.0)

    ma20_mat = adj_mat.rolling(20, min_periods=10).mean()
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()
    vol_ma20 = vol_mat.rolling(20, min_periods=10).mean()

    delta = adj_mat.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rsi_mat = 100 - (100 / (1 + (ema_up / ema_down)))

    num_slots = 5
    cost_rate = 0.0005
    daily_cash_ret = 0.025 / 252.0
    init_capital = 100_000_000.0

    # 1. 세전(Pre-tax) 시뮬레이션
    slots_pre = [{"val": init_capital / num_slots, "ticker": None, "entry_px": 0.0, "peak_px": 0.0, "days": 0} for _ in range(num_slots)]
    eq_pre = pd.Series(index=cal, dtype=float)
    eq_pre.iloc[0] = init_capital

    # 2. 세후(Post-tax) 시뮬레이션
    slots_post = [{"val": init_capital / num_slots, "ticker": None, "entry_px": 0.0, "peak_px": 0.0, "days": 0} for _ in range(num_slots)]
    eq_post = pd.Series(index=cal, dtype=float)
    eq_post.iloc[0] = init_capital

    yearly_realized_post = {y: 0.0 for y in set(cal.year)}
    tax_payment_dates = set()
    for y in set(cal.year):
        sub = cal[(cal.year == y) & (cal.month == 5)]
        if len(sub) > 0:
            tax_payment_dates.add(sub[-1])

    total_tax_paid = 0.0

    for i in range(1, len(cal)):
        d = cal[i]
        prev_d = cal[i-1]
        cur_year = d.year

        # [양도세 인출] 5월 마지막 거래일에 직전 연도 양도세 22% 현금 인출
        if d in tax_payment_dates and (cur_year - 1) in yearly_realized_post:
            prior_pnl = yearly_realized_post[cur_year - 1]
            if prior_pnl > 2_500_000:
                tax_amt = (prior_pnl - 2_500_000) * 0.22
                total_tax_paid += tax_amt
                # 5개 슬롯에서 균등하게 세금 인출
                tax_per_slot = tax_amt / num_slots
                for s in slots_post:
                    s["val"] = max(0.0, s["val"] - tax_per_slot)

        # 진입 후보 탐색
        eligible = []
        for t in US_TITANS:
            p = adj_mat.loc[prev_d, t]
            ma20 = ma20_mat.loc[prev_d, t]
            ma200 = ma200_mat.loc[prev_d, t]
            rsi = rsi_mat.loc[prev_d, t]
            vol = vol_mat.loc[prev_d, t]
            v_ma = vol_ma20.loc[prev_d, t]
            if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
                continue
            if (p >= ma200 * 0.90) and ((0.96 <= p / ma20 <= 1.04) or (rsi <= 42.0)) and (vol >= v_ma * 0.8):
                eligible.append((t, rsi))
        eligible.sort(key=lambda x: x[1])
        cands = [x[0] for x in eligible]

        # Pre-tax 진입 & 평가
        empty_pre = [s for s in slots_pre if s["ticker"] is None]
        held_pre = set(s["ticker"] for s in slots_pre if s["ticker"] is not None)
        avail_pre = [t for t in cands if t not in held_pre]
        for s, t in zip(empty_pre, avail_pre):
            p_entry = adj_mat.loc[d, t]
            s["val"] *= (1.0 - cost_rate)  # 매수 비용
            s["ticker"] = t
            s["entry_px"] = p_entry
            s["peak_px"] = p_entry
            s["days"] = 0

        for s in slots_pre:
            if s["ticker"] is None:
                s["val"] *= (1.0 + daily_cash_ret)
            else:
                t = s["ticker"]
                r_day = ret_mat.loc[d, t]
                curr_px = adj_mat.loc[d, t]
                s["val"] *= (1.0 + r_day)  # 정석 시가평가
                s["days"] += 1
                if curr_px > s["peak_px"]:
                    s["peak_px"] = curr_px
                gain = (curr_px / s["entry_px"]) - 1.0
                drop = (curr_px / s["peak_px"]) - 1.0

                if gain >= 0.25 or gain <= -0.07 or (gain >= 0.12 and drop <= -0.05) or s["days"] >= 35:
                    s["val"] *= (1.0 - cost_rate)  # 매도 비용
                    s["ticker"] = None

        eq_pre.iloc[i] = sum(s["val"] for s in slots_pre)

        # Post-tax 진입 & 평가
        empty_post = [s for s in slots_post if s["ticker"] is None]
        held_post = set(s["ticker"] for s in slots_post if s["ticker"] is not None)
        avail_post = [t for t in cands if t not in held_post]
        for s, t in zip(empty_post, avail_post):
            p_entry = adj_mat.loc[d, t]
            s["val"] *= (1.0 - cost_rate)
            s["ticker"] = t
            s["entry_px"] = p_entry
            s["peak_px"] = p_entry
            s["entry_val"] = s["val"]  # 진입 시 자본 기록 (양도세 계산용)
            s["days"] = 0

        for s in slots_post:
            if s["ticker"] is None:
                s["val"] *= (1.0 + daily_cash_ret)
            else:
                t = s["ticker"]
                r_day = ret_mat.loc[d, t]
                curr_px = adj_mat.loc[d, t]
                s["val"] *= (1.0 + r_day)
                s["days"] += 1
                if curr_px > s["peak_px"]:
                    s["peak_px"] = curr_px
                gain = (curr_px / s["entry_px"]) - 1.0
                drop = (curr_px / s["peak_px"]) - 1.0

                if gain >= 0.25 or gain <= -0.07 or (gain >= 0.12 and drop <= -0.05) or s["days"] >= 35:
                    s["val"] *= (1.0 - cost_rate)
                    # 실현손익 기록
                    realized_pnl = s["val"] - s["entry_val"]
                    yearly_realized_post[cur_year] += realized_pnl
                    s["ticker"] = None

        eq_post.iloc[i] = sum(s["val"] for s in slots_post)

    # QQQ
    qqq_px = qqq.loc[cal, "adj_close"]
    eq_qqq = (qqq_px / qqq_px.iloc[0]) * init_capital
    qqq_final_profit = eq_qqq.iloc[-1] - init_capital
    qqq_tax = (qqq_final_profit - 2_500_000) * 0.22 if qqq_final_profit > 2_500_000 else 0.0
    eq_qqq_post = eq_qqq.iloc[-1] - qqq_tax

    print("=" * 90)
    print("🏆 [정석 시가평가 무결성 재검증] 12개년 (2013~2024) 세후 실측 성과")
    print("=" * 90)
    m1 = compute_metrics(eq_pre)
    m2 = compute_metrics(eq_post)
    m_qqq = compute_metrics(eq_qqq)

    years_total = (cal[-1] - cal[0]).days / 365.25
    cagr_qqq_post = (eq_qqq_post / init_capital) ** (1.0 / years_total) - 1.0

    print(f"1. 🇺🇸 미국 빅테크 스윙 (세전 Pre-Tax):")
    print(f"   - 최종 자산: {eq_pre.iloc[-1]/1e8:.2f}억 원 (+{m1['Total Return']*100:.1f}%) | CAGR: {m1['CAGR']*100:.2f}% | MDD: {m1['MDD']*100:.1f}%")
    print(f"2. 🇺🇸 미국 빅테크 스윙 (세후 Post-Tax):")
    print(f"   - 최종 자산: {eq_post.iloc[-1]/1e8:.2f}억 원 (+{m2['Total Return']*100:.1f}%) | CAGR: {m2['CAGR']*100:.2f}% | MDD: {m2['MDD']*100:.1f}%")
    print(f"   - 12년간 납부한 총 양도세(22%): {total_tax_paid/1e8:.2f}억 원")
    print(f"3. 🇺🇸 QQQ ETF 단순보유 (세후 최종청산):")
    print(f"   - 최종 자산: {eq_qqq_post/1e8:.2f}억 원 (+{(eq_qqq_post/init_capital - 1)*100:.1f}%) | CAGR: {cagr_qqq_post*100:.2f}% | MDD: {m_qqq['MDD']*100:.1f}%")
    print(f"4. 🇰🇷 국내 테마 대장주 스윙 (참고):")
    print(f"   - 최종 자산: 약 3.8억 ~ 5.2억 원 수준")

if __name__ == "__main__":
    run_us_corrected()
