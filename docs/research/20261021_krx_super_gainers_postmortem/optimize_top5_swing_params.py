"""
[EXP-KRX-011-OPT] 코스피 시총 Top 5 대형주 스윙 시스템 익절률/손절률 그리드 최적화
======================================================================================
- 목적: 손익비(Profit Factor), 승률(Win Rate), 순합산손익(Net Sum), CAGR을 극대화하는
        '대형주 스윙 최적의 골디락스 손절률 & 익절률 조합'을 12개년 실측 데이터로 전수 탐색
- 탐색 파라미터 그리드:
  1. 1차 익절률 (TP1): +5%, +7%, +10%, +12%
  2. 2차 익절률 (TP2): +10%, +15%, +20%, +25%
  3. 손절률 (SL): -4%, -5%, -6%, -7%, -8%, -10%
  4. 시간 만기: 30일, 40일, 60일
"""
import os
import sys
import time
import itertools
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = "/home/kuzimo/projects/CTradeSystem"
RESEARCH_DIR = f"{ROOT}/docs/research/20261021_krx_super_gainers_postmortem"

def run_grid_search():
    t_start = time.time()
    print("=" * 125)
    print("🔍 [그리드 최적화] 코스피 시총 Top 5 대형주 스윙 손절률 & 익절률 전수 탐색 (2013~2024, 12개년)")
    print("=" * 125)

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
        columns=['code', 'date', 'close', 'value'],
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

    # 사전 신호 계산 (속도 최적화: 날짜별 Top5 및 진입 신호 캐싱)
    top5_by_month = {}
    signal_cache = {}

    for d in cal:
        prev_idx = cal.get_loc(d) - 1
        if prev_idx < 0:
            continue
        prev_d = cal[prev_idx]

        if d in monthly_set:
            mcap_row = mcap_mat.loc[prev_d].dropna()
            top5_by_month[d] = mcap_row.sort_values(ascending=False).head(num_slots).index.tolist()

    curr_top5 = top5_by_month.get(monthly_dates[0], [])
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

    # 파라미터 그리드 정의
    tp1_list = [0.05, 0.07, 0.10]
    tp2_list = [0.10, 0.15, 0.20]
    sl_list = [-0.04, -0.05, -0.06, -0.07, -0.08, -0.10]
    max_days_list = [30, 40, 60]

    param_combinations = []
    for tp1, tp2, sl, m_days in itertools.product(tp1_list, tp2_list, sl_list, max_days_list):
        if tp1 < tp2:  # 1차 익절 < 2차 익절인 유효 조합만
            param_combinations.append((tp1, tp2, sl, m_days))

    print(f"⚙️ 총 {len(param_combinations)}개 파라미터 조합 시뮬레이션 시작...")

    results = []

    for tp1, tp2, sl, max_days in param_combinations:
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[:start_pos] = 1.0

        slots = [{"ticker": None, "entry_price": 0.0, "peak_price": 0.0, "holding_days": 0, "remaining_ratio": 1.0, "is_cash": True} for _ in range(num_slots)]
        trade_gains = []

        for i in range(start_pos, len(cal)):
            d = cal[i]
            prev_eq = eq.iloc[i-1]

            curr_top5, sig_list = signal_cache.get(d, ([], []))

            # 신규 진입
            empty_slots = [idx for idx, s in enumerate(slots) if s["is_cash"] or s["ticker"] is None]
            if empty_slots and sig_list:
                currently_held = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]
                new_picks = [c for c in sig_list if c not in currently_held]

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

            # 일별 청산 검사
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

        # 매매 지표
        n_tr = len(trade_gains)
        if n_tr > 0:
            tg = np.array(trade_gains)
            wins = tg[tg > 0]
            losses = tg[tg <= 0]
            win_rate = len(wins) / n_tr * 100.0
            sum_win = np.sum(wins) if len(wins) > 0 else 0.0
            sum_loss = abs(np.sum(losses)) if len(losses) > 0 else 0.0
            net_sum = sum_win - sum_loss
            pf = (sum_win / sum_loss) if sum_loss > 0 else np.nan
            ev = np.mean(tg)
        else:
            win_rate, pf, net_sum, ev = 0, 0, 0, 0

        results.append({
            "TP1": tp1 * 100,
            "TP2": tp2 * 100,
            "SL": sl * 100,
            "Max_Days": max_days,
            "Total_Ret": tot_ret * 100,
            "CAGR": cagr * 100,
            "MDD": mdd * 100,
            "Sharpe": sharpe,
            "Trades": n_tr,
            "Win_Rate": win_rate,
            "Profit_Factor": pf,
            "Net_Sum": net_sum,
            "EV": ev
        })

    df_res = pd.DataFrame(results)
    out_csv = f"{RESEARCH_DIR}/top5_swing_param_grid.csv"
    df_res.to_csv(out_csv, index=False)
    print(f"✅ 그리드 최적화 완료: {len(df_res)}개 조합 (저장: {out_csv})")

    # 1. 손익비(Profit Factor) 및 순합산손익 기준 TOP 5 조합
    print("\n" + "=" * 125)
    print("🏆 [순합산손익(Net Sum) & 손익비(PF) 기준 최적의 파라미터 TOP 5]")
    print("=" * 125)
    header = f"{'순위':<4} | {'1차익절':>6} | {'2차익절':>6} | {'손절선':>6} | {'만기':>5} | {'순합산손익':>11} | {'손익비':>6} | {'승률':>6} | {'누적수익':>9} | {'MDD':>8} | {'매매수':>6}"
    print(header)
    print("-" * 125)

    top_by_net = df_res.sort_values('Net_Sum', ascending=False).head(5)
    rank = 1
    for _, r in top_by_net.iterrows():
        print(f"{rank:<4} | {r['TP1']:>5.0f}% | {r['TP2']:>5.0f}% | {r['SL']:>5.0f}% | {r['Max_Days']:>4.0f}일 | {r['Net_Sum']:>+10.1f}% | {r['Profit_Factor']:>6.2f} | {r['Win_Rate']:>5.1f}% | {r['Total_Ret']:>+8.1f}% | {r['MDD']:>7.1f}% | {int(r['Trades']):>5}회")
        rank += 1
    print("=" * 125)

    # 2. 승률(Win Rate) 기준 TOP 5 조합
    print("\n" + "=" * 125)
    print("🎯 [승률(Win Rate) 기준 최적의 파라미터 TOP 5]")
    print("=" * 125)
    print(header)
    print("-" * 125)
    top_by_win = df_res.sort_values('Win_Rate', ascending=False).head(5)
    rank = 1
    for _, r in top_by_win.iterrows():
        print(f"{rank:<4} | {r['TP1']:>5.0f}% | {r['TP2']:>5.0f}% | {r['SL']:>5.0f}% | {r['Max_Days']:>4.0f}일 | {r['Net_Sum']:>+10.1f}% | {r['Profit_Factor']:>6.2f} | {r['Win_Rate']:>5.1f}% | {r['Total_Ret']:>+8.1f}% | {r['MDD']:>7.1f}% | {int(r['Trades']):>5}회")
        rank += 1
    print("=" * 125)

    # 3. 누적 수익률(CAGR) 기준 TOP 5 조합
    print("\n" + "=" * 125)
    print("📈 [계좌 누적수익률 기준 최적의 파라미터 TOP 5]")
    print("=" * 125)
    print(header)
    print("-" * 125)
    top_by_cagr = df_res.sort_values('Total_Ret', ascending=False).head(5)
    rank = 1
    for _, r in top_by_cagr.iterrows():
        print(f"{rank:<4} | {r['TP1']:>5.0f}% | {r['TP2']:>5.0f}% | {r['SL']:>5.0f}% | {r['Max_Days']:>4.0f}일 | {r['Net_Sum']:>+10.1f}% | {r['Profit_Factor']:>6.2f} | {r['Win_Rate']:>5.1f}% | {r['Total_Ret']:>+8.1f}% | {r['MDD']:>7.1f}% | {int(r['Trades']):>5}회")
        rank += 1
    print("=" * 125)
    print(f"⏱️ 총 소요 시간: {time.time() - t_start:.2f}초")

if __name__ == "__main__":
    run_grid_search()
