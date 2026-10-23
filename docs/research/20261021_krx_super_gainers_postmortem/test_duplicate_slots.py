"""
[EXP-KRX-014] K-Titan Top 5 스윙: 종목 중복 허용(피라미딩) vs 중복 불허(1종목1슬롯) 실측 비교
===================================================================================================
- 분석 기간: 2013-01-02 ~ 2024-12-31 (12개년)
- 파라미터: TP1=+10%, TP2=+20%, SL=-6%, Max_Days=40일, 5-슬롯 (20%씩)
- 비교:
  1. [기존] 중복 불허 (No Duplicates): 이미 보유 중인 종목은 추가 신호 무시 (1종목 최대 1슬롯, 비중 20%)
  2. [신규] 중복 허용 (Allow Duplicates): 빈 슬롯이 있고 신호가 뜨면 동일 종목 추가 진입 허용 (1종목 최대 5슬롯, 비중 최대 100%)
"""
import os
import sys
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = "/home/kuzimo/projects/CTradeSystem"
RESEARCH_DIR = f"{ROOT}/docs/research/20261021_krx_super_gainers_postmortem"

def run_simulation(max_slots_per_ticker=1):
    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    kospi_codes = set(uni[(uni['market'] == 'KOSPI') & (uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])

    df_idx = pd.read_parquet(idx_path)
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u180.loc[(u180.index >= '2013-01-01') & (u180.index <= '2024-12-31')].index

    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close'],
        filters=[('code', 'in', list(kospi_codes)), ('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap', 'foreign_hold_pct', 'inst_net_qty'],
        filters=[('code', 'in', list(kospi_codes)), ('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )

    adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()
    mcap_mat = df_ext.pivot(index='date', columns='code', values='market_cap').sort_index()
    fgn_mat = df_ext.pivot(index='date', columns='code', values='foreign_hold_pct').sort_index()
    inst_mat = df_ext.pivot(index='date', columns='code', values='inst_net_qty').sort_index()

    ret_mat = adj_mat.pct_change().fillna(0.0)

    ma20_mat = adj_mat.rolling(20, min_periods=10).mean()
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()

    delta = adj_mat.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rs = ema_up / ema_down
    rsi_mat = 100 - (100 / (1 + rs))

    fgn_diff5 = fgn_mat.diff(5)
    inst_sum5 = inst_mat.rolling(5, min_periods=3).sum()

    monthly_dates = []
    prev_m = None
    for d in cal:
        m = (d.year, d.month)
        if prev_m is not None and m != prev_m:
            monthly_dates.append(d)
        prev_m = m
    warmup_d = cal[cal >= '2013-03-01'][0]
    monthly_dates = [d for d in monthly_dates if d >= warmup_d]
    monthly_set = set(monthly_dates)

    num_slots = 5
    slot_weight = 1.0 / num_slots
    cost_rate = 0.0027
    daily_cash_ret = 0.025 / 252.0
    start_pos = cal.get_loc(monthly_dates[0])

    top5_by_month = {}
    for d in cal:
        prev_idx = cal.get_loc(d) - 1
        if prev_idx < 0:
            continue
        prev_d = cal[prev_idx]
        if d in monthly_set:
            mcap_row = mcap_mat.loc[prev_d].dropna()
            top5_by_month[d] = mcap_row.sort_values(ascending=False).head(num_slots).index.tolist()

    curr_top5 = top5_by_month.get(monthly_dates[0], [])
    signal_cache = {}
    for i in range(start_pos, len(cal)):
        d = cal[i]
        prev_d = cal[i-1]
        if d in monthly_set:
            curr_top5 = top5_by_month.get(d, curr_top5)

        sig_list = []
        for code in curr_top5:
            p = adj_mat.loc[prev_d, code] if code in adj_mat.columns else np.nan
            ma20 = ma20_mat.loc[prev_d, code] if code in ma20_mat.columns else np.nan
            ma200 = ma200_mat.loc[prev_d, code] if code in ma200_mat.columns else np.nan
            rsi = rsi_mat.loc[prev_d, code] if code in rsi_mat.columns else np.nan

            if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
                continue

            cond_loc = (p / ma200 >= 0.85) and ((0.95 <= p / ma20 <= 1.03) or (rsi <= 42.0))
            f_flow = fgn_diff5.loc[prev_d, code] if code in fgn_diff5.columns else 0.0
            i_flow = inst_sum5.loc[prev_d, code] if code in inst_sum5.columns else 0.0
            cond_flow = (f_flow > 0.0) or (i_flow > 0.0)

            if cond_loc and cond_flow:
                sig_list.append(code)
        signal_cache[d] = (curr_top5, sig_list)

    tp1, tp2, sl, max_days = 0.10, 0.20, -0.06, 40

    eq = pd.Series(index=cal, dtype=float)
    eq.iloc[:start_pos] = 1.0

    slots = [{
        "ticker": None,
        "entry_price": 0.0,
        "peak_price": 0.0,
        "holding_days": 0,
        "remaining_ratio": 1.0,
        "is_cash": True
    } for _ in range(num_slots)]

    trade_gains = []
    active_slot_days = 0
    max_single_stock_slots = 0

    for i in range(start_pos, len(cal)):
        d = cal[i]
        prev_eq = eq.iloc[i-1]

        curr_top5, sig_list = signal_cache.get(d, ([], []))

        # 신규 진입 로직: 종목별 최대 보유 슬롯 수 제어
        empty_slots = [idx for idx, s in enumerate(slots) if s["is_cash"] or s["ticker"] is None]
        if empty_slots and sig_list:
            held_tickers = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]
            held_counts = pd.Series(held_tickers).value_counts().to_dict()

            new_picks = []
            for c in sig_list:
                current_cnt = held_counts.get(c, 0)
                if current_cnt < max_slots_per_ticker:
                    new_picks.append(c)

            for slot_idx, ticker in zip(empty_slots, new_picks):
                p_entry = adj_mat.loc[d, ticker] if ticker in adj_mat.columns else np.nan
                if not np.isnan(p_entry) and p_entry > 0:
                    slots[slot_idx] = {
                        "ticker": ticker,
                        "entry_price": p_entry,
                        "peak_price": p_entry,
                        "holding_days": 0,
                        "remaining_ratio": 1.0,
                        "is_cash": False
                    }
                    prev_eq *= (1.0 - slot_weight * 0.00035)

        held_tickers = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]
        if held_tickers:
            counts = pd.Series(held_tickers).value_counts()
            if not counts.empty and counts.max() > max_single_stock_slots:
                max_single_stock_slots = counts.max()

        for s in slots:
            if not s["is_cash"] and s["ticker"] is not None:
                active_slot_days += 1

        slot_returns = []
        for s in slots:
            if s["is_cash"] or s["ticker"] is None:
                slot_returns.append(daily_cash_ret)
                continue

            t = s["ticker"]
            r_day = ret_mat.loc[d, t] if t in ret_mat.columns else 0.0
            curr_px = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan

            if np.isnan(curr_px) or curr_px <= 0:
                slot_returns.append(0.0)
                continue

            s["holding_days"] += 1
            if curr_px > s["peak_price"]:
                s["peak_price"] = curr_px

            gain_from_entry = (curr_px / s["entry_price"]) - 1.0
            drop_from_peak = (curr_px / s["peak_price"]) - 1.0

            action = None
            exit_ret = 0.0

            if gain_from_entry >= tp2:
                action = "TP2"
                exit_ret = tp2 - cost_rate
            elif gain_from_entry >= tp1 and drop_from_peak <= -0.05:
                action = "TRAIL"
                exit_ret = gain_from_entry - cost_rate
            elif gain_from_entry <= sl:
                action = "SL"
                exit_ret = sl - cost_rate
            elif s["holding_days"] >= max_days:
                action = "TIME"
                exit_ret = gain_from_entry - cost_rate

            if action is not None:
                slot_returns.append(exit_ret * s["remaining_ratio"])
                trade_gains.append(gain_from_entry * 100.0)
                s["is_cash"] = True
                s["ticker"] = None
            else:
                if s["remaining_ratio"] == 1.0 and gain_from_entry >= tp1:
                    s["remaining_ratio"] = 0.5
                    slot_returns.append(r_day * 0.5 + tp1 * 0.5)
                else:
                    slot_returns.append(r_day * s["remaining_ratio"])

        port_ret = sum(slot_returns) / num_slots
        eq.iloc[i] = prev_eq * (1.0 + port_ret)

    sub_eq = eq.iloc[start_pos:]
    tot_ret = (sub_eq.iloc[-1] / sub_eq.iloc[0]) - 1.0
    years = (sub_eq.index[-1] - sub_eq.index[0]).days / 365.25
    cagr = (sub_eq.iloc[-1] / sub_eq.iloc[0]) ** (1.0 / years) - 1.0
    daily_r = sub_eq.pct_change().dropna()
    vol = daily_r.std() * np.sqrt(252)
    sharpe = (cagr - 0.02) / vol if vol > 0 else 0.0
    dd = (sub_eq - sub_eq.cummax()) / sub_eq.cummax()
    mdd = dd.min()

    n_tr = len(trade_gains)
    tg = np.array(trade_gains) if n_tr > 0 else np.array([])
    wins = tg[tg > 0]
    losses = tg[tg <= 0]
    win_rate = (len(wins) / n_tr * 100.0) if n_tr > 0 else 0.0
    sum_w = np.sum(wins) if len(wins) > 0 else 0.0
    sum_l = abs(np.sum(losses)) if len(losses) > 0 else 0.0
    net_sum = sum_w - sum_l
    pf = (sum_w / sum_l) if sum_l > 0 else 0.0
    avg_util = (active_slot_days / (len(sub_eq) * num_slots)) * 100.0

    return {
        "eq": sub_eq,
        "tot_ret": tot_ret * 100.0,
        "cagr": cagr * 100.0,
        "mdd": mdd * 100.0,
        "sharpe": sharpe,
        "n_tr": n_tr,
        "win_rate": win_rate,
        "pf": pf,
        "net_sum": net_sum,
        "avg_util": avg_util,
        "max_single_stock_slots": max_single_stock_slots
    }

def main():
    print("=" * 115)
    print("🔬 [실측 검증] K-Titan Top 5: 종목 중복 제한(1개 vs 2개 vs 5개) 실측 비교")
    print("=" * 115)

    res_1 = run_simulation(max_slots_per_ticker=1)
    res_2 = run_simulation(max_slots_per_ticker=2)
    res_5 = run_simulation(max_slots_per_ticker=5)

    df_res = pd.DataFrame([
        {
            "운용 모델": "1. 중복 불허 (현행)",
            "종목당 캡": "최대 1슬롯 (20%)",
            "누적수익률": f"{res_1['tot_ret']:+.1f}%",
            "CAGR": f"{res_1['cagr']:.1f}%",
            "MDD": f"{res_1['mdd']:.1f}%",
            "샤프지수": f"{res_1['sharpe']:.2f}",
            "승률": f"{res_1['win_rate']:.1f}%",
            "손익비": f"{res_1['pf']:.2f}",
            "순합산손익": f"{res_1['net_sum']:+.1f}%",
            "총 매매수": f"{res_1['n_tr']}회",
            "평균 가동률": f"{res_1['avg_util']:.1f}%"
        },
        {
            "운용 모델": "2. 부분 중복 허용 (권장형)",
            "종목당 캡": "최대 2슬롯 (40%)",
            "누적수익률": f"{res_2['tot_ret']:+.1f}%",
            "CAGR": f"{res_2['cagr']:.1f}%",
            "MDD": f"{res_2['mdd']:.1f}%",
            "샤프지수": f"{res_2['sharpe']:.2f}",
            "승률": f"{res_2['win_rate']:.1f}%",
            "손익비": f"{res_2['pf']:.2f}",
            "순합산손익": f"{res_2['net_sum']:+.1f}%",
            "총 매매수": f"{res_2['n_tr']}회",
            "평균 가동률": f"{res_2['avg_util']:.1f}%"
        },
        {
            "운용 모델": "3. 무제한 중복 허용 (몰빵형)",
            "종목당 캡": "최대 5슬롯 (100%)",
            "누적수익률": f"{res_5['tot_ret']:+.1f}%",
            "CAGR": f"{res_5['cagr']:.1f}%",
            "MDD": f"{res_5['mdd']:.1f}%",
            "샤프지수": f"{res_5['sharpe']:.2f}",
            "승률": f"{res_5['win_rate']:.1f}%",
            "손익비": f"{res_5['pf']:.2f}",
            "순합산손익": f"{res_5['net_sum']:+.1f}%",
            "총 매매수": f"{res_5['n_tr']}회",
            "평균 가동률": f"{res_5['avg_util']:.1f}%"
        }
    ])

    print("\n" + "=" * 115)
    print("📊 [비교 결과 테이블]")
    print("=" * 115)
    print(df_res.to_string(index=False))
    print("=" * 115)

    # 연도별 수익률 비교
    years = list(range(2013, 2025))
    yearly_rows = []
    eq1 = res_1["eq"]
    eq2 = res_2["eq"]
    eq5 = res_5["eq"]

    for yr in years:
        d_yr = eq1.index[eq1.index.year == yr]
        if len(d_yr) == 0:
            continue
        d_start = d_yr[0]
        d_end = d_yr[-1]
        idx_s = eq1.index.get_loc(d_start)
        base_d = eq1.index[max(0, idx_s - 1)]

        r1 = (eq1.loc[d_end] / eq1.loc[base_d] - 1.0) * 100.0
        r2 = (eq2.loc[d_end] / eq2.loc[base_d] - 1.0) * 100.0
        r5 = (eq5.loc[d_end] / eq5.loc[base_d] - 1.0) * 100.0

        yearly_rows.append({
            "연도": yr,
            "1. 중복불허(20%)": f"{r1:+.1f}%",
            "2. 2슬롯제한(40%)": f"{r2:+.1f}%",
            "3. 무제한(100%)": f"{r5:+.1f}%"
        })

    print("\n📅 [연도별 수익률 비교]")
    print(df_yr.to_string(index=False))
    print("=" * 115)

if __name__ == "__main__":
    main()

    # 연도별 수익률 비교
    years = list(range(2013, 2025))
    yearly_rows = []
    eq1 = res_no_dup["eq"]
    eq2 = res_dup["eq"]

    for yr in years:
        d_yr = eq1.index[eq1.index.year == yr]
        if len(d_yr) == 0:
            continue
        d_start = d_yr[0]
        d_end = d_yr[-1]
        idx_s = eq1.index.get_loc(d_start)
        base_d = eq1.index[max(0, idx_s - 1)]

        r1 = (eq1.loc[d_end] / eq1.loc[base_d] - 1.0) * 100.0
        r2 = (eq2.loc[d_end] / eq2.loc[base_d] - 1.0) * 100.0

        yearly_rows.append({
            "연도": yr,
            "1. 중복 불허(%)": f"{r1:+.1f}%",
            "2. 중복 허용(%)": f"{r2:+.1f}%",
            "수익률 차이": f"{r2 - r1:+.1f}%p"
        })

    print("\n📅 [연도별 수익률 비교]")
    print(df_yr.to_string(index=False))
    print("=" * 115)

if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
