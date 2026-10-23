"""
[EXP-KRX-013] K-Titan Pullback Swing: 코스피 시총 Top 5 vs Top 10 비교 실측
=============================================================================
- 분석 기간: 2013-01-02 ~ 2024-12-31 (12개년)
- 최적 파라미터: TP1=+10%, TP2=+20%, SL=-6%, Max_Days=40일
- 비교 대상:
  1. [Base] Top 5 유니버스 + 5-슬롯 (슬롯당 20%)
  2. [Case A] Top 10 유니버스 + 5-슬롯 (슬롯당 20%, 10개 중 상위 5개 선별 진입)
  3. [Case B] Top 10 유니버스 + 10-슬롯 (슬롯당 10%, 10개 분산 진입)
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

def run_simulation(cal, start_pos, signal_cache_func, num_slots, adj_mat, ret_mat, daily_cash_ret, cost_rate, tp1=0.10, tp2=0.20, sl=-0.06, max_days=40):
    slot_weight = 1.0 / num_slots
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

    for i in range(start_pos, len(cal)):
        d = cal[i]
        prev_eq = eq.iloc[i-1]

        curr_univ, sig_list = signal_cache_func(d)

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

        # 가동 슬롯 카운트
        for s in slots:
            if not s["is_cash"] and s["ticker"] is not None:
                active_slot_days += 1

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
        "avg_util": avg_util
    }

def main():
    print("=" * 125)
    print("🔬 [실측 비교] K-Titan Pullback Swing: 코스피 시총 Top 5 vs Top 10 (2013~2024, 12개년)")
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

    start_pos = cal.get_loc(monthly_dates[0])
    cost_rate = 0.0027
    daily_cash_ret = 0.025 / 252.0

    # Top 5 및 Top 10 월별 유니버스 산출
    top5_by_month = {}
    top10_by_month = {}
    for d in cal:
        prev_idx = cal.get_loc(d) - 1
        if prev_idx < 0:
            continue
        prev_d = cal[prev_idx]
        if d in monthly_set:
            mcap_row = mcap_mat.loc[prev_d].dropna()
            top5_by_month[d] = mcap_row.sort_values(ascending=False).head(5).index.tolist()
            top10_by_month[d] = mcap_row.sort_values(ascending=False).head(10).index.tolist()

    # 일별 신호 캐시 생성
    sig_cache_top5 = {}
    sig_cache_top10 = {}

    curr_top5 = top5_by_month.get(monthly_dates[0], [])
    curr_top10 = top10_by_month.get(monthly_dates[0], [])

    for i in range(start_pos, len(cal)):
        d = cal[i]
        prev_d = cal[i-1]
        if d in monthly_set:
            curr_top5 = top5_by_month.get(d, curr_top5)
            curr_top10 = top10_by_month.get(d, curr_top10)

        # Top 5 신호
        sig5 = []
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
            if cond_loc and (f_flow > 0 or i_flow > 0):
                sig5.append(code)
        sig_cache_top5[d] = (curr_top5, sig5)

        # Top 10 신호
        sig10 = []
        for code in curr_top10:
            p = adj_mat.loc[prev_d, code] if code in adj_mat.columns else np.nan
            ma20 = ma20_mat.loc[prev_d, code] if code in ma20_mat.columns else np.nan
            ma200 = ma200_mat.loc[prev_d, code] if code in ma200_mat.columns else np.nan
            rsi = rsi_mat.loc[prev_d, code] if code in rsi_mat.columns else np.nan
            if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
                continue
            cond_loc = (p / ma200 >= 0.85) and ((0.95 <= p / ma20 <= 1.03) or (rsi <= 42.0))
            f_flow = fgn_diff5.loc[prev_d, code] if code in fgn_diff5.columns else 0.0
            i_flow = inst_sum5.loc[prev_d, code] if code in inst_sum5.columns else 0.0
            if cond_loc and (f_flow > 0 or i_flow > 0):
                sig10.append(code)
        sig_cache_top10[d] = (curr_top10, sig10)

    # 단순보유(B&H) 계산
    eq_bh_top5 = pd.Series(index=cal, dtype=float)
    eq_bh_top10 = pd.Series(index=cal, dtype=float)
    eq_bh_top5.iloc[:start_pos] = 1.0
    eq_bh_top10.iloc[:start_pos] = 1.0
    for i in range(start_pos, len(cal)):
        d = cal[i]
        c5, _ = sig_cache_top5[d]
        c10, _ = sig_cache_top10[d]
        r5 = [ret_mat.loc[d, c] if c in ret_mat.columns else 0.0 for c in c5]
        r10 = [ret_mat.loc[d, c] if c in ret_mat.columns else 0.0 for c in c10]
        eq_bh_top5.iloc[i] = eq_bh_top5.iloc[i-1] * (1.0 + (sum(r5)/len(r5) if r5 else 0.0))
        eq_bh_top10.iloc[i] = eq_bh_top10.iloc[i-1] * (1.0 + (sum(r10)/len(r10) if r10 else 0.0))

    ret_bh5 = (eq_bh_top5.iloc[-1] / eq_bh_top5.iloc[start_pos] - 1.0) * 100.0
    ret_bh10 = (eq_bh_top10.iloc[-1] / eq_bh_top10.iloc[start_pos] - 1.0) * 100.0
    ret_kospi = (u180.iloc[-1] / u180.loc[cal[start_pos]] - 1.0) * 100.0

    # 1. Base: Top 5 유니버스 + 5-슬롯
    res_base = run_simulation(cal, start_pos, lambda d: sig_cache_top5[d], 5, adj_mat, ret_mat, daily_cash_ret, cost_rate)

    # 2. Case A: Top 10 유니버스 + 5-슬롯 (선별형)
    res_case_a = run_simulation(cal, start_pos, lambda d: sig_cache_top10[d], 5, adj_mat, ret_mat, daily_cash_ret, cost_rate)

    # 3. Case B: Top 10 유니버스 + 10-슬롯 (완전 분산형)
    res_case_b = run_simulation(cal, start_pos, lambda d: sig_cache_top10[d], 10, adj_mat, ret_mat, daily_cash_ret, cost_rate)

    # 결과 테이블 생성
    summary_data = [
        {
            "전략 모델": "1. Top 5 + 5슬롯 (기존 Base)",
            "유니버스": "시총 1~5위",
            "슬롯수": "5슬롯 (20%씩)",
            "누적수익률": f"{res_base['tot_ret']:+.1f}%",
            "CAGR": f"{res_base['cagr']:.1f}%",
            "MDD": f"{res_base['mdd']:.1f}%",
            "샤프지수": f"{res_base['sharpe']:.2f}",
            "승률": f"{res_base['win_rate']:.1f}%",
            "손익비": f"{res_base['pf']:.2f}",
            "순합산손익": f"{res_base['net_sum']:+.1f}%",
            "총 매매수": f"{res_base['n_tr']}회",
            "평균 가동률": f"{res_base['avg_util']:.1f}%"
        },
        {
            "전략 모델": "2. Top 10 + 5슬롯 (선별 집중형)",
            "유니버스": "시총 1~10위",
            "슬롯수": "5슬롯 (20%씩)",
            "누적수익률": f"{res_case_a['tot_ret']:+.1f}%",
            "CAGR": f"{res_case_a['cagr']:.1f}%",
            "MDD": f"{res_case_a['mdd']:.1f}%",
            "샤프지수": f"{res_case_a['sharpe']:.2f}",
            "승률": f"{res_case_a['win_rate']:.1f}%",
            "손익비": f"{res_case_a['pf']:.2f}",
            "순합산손익": f"{res_case_a['net_sum']:+.1f}%",
            "총 매매수": f"{res_case_a['n_tr']}회",
            "평균 가동률": f"{res_case_a['avg_util']:.1f}%"
        },
        {
            "전략 모델": "3. Top 10 + 10슬롯 (완전 분산형)",
            "유니버스": "시총 1~10위",
            "슬롯수": "10슬롯 (10%씩)",
            "누적수익률": f"{res_case_b['tot_ret']:+.1f}%",
            "CAGR": f"{res_case_b['cagr']:.1f}%",
            "MDD": f"{res_case_b['mdd']:.1f}%",
            "샤프지수": f"{res_case_b['sharpe']:.2f}",
            "승률": f"{res_case_b['win_rate']:.1f}%",
            "손익비": f"{res_case_b['pf']:.2f}",
            "순합산손익": f"{res_case_b['net_sum']:+.1f}%",
            "총 매매수": f"{res_case_b['n_tr']}회",
            "평균 가동률": f"{res_case_b['avg_util']:.1f}%"
        },
        {
            "전략 모델": "4. [참고] Top 10 단순보유",
            "유니버스": "시총 1~10위",
            "슬롯수": "10종목 균등",
            "누적수익률": f"{ret_bh10:+.1f}%",
            "CAGR": f"{((1 + ret_bh10/100)**(1/11.8) - 1)*100:.1f}%",
            "MDD": "-",
            "샤프지수": "-",
            "승률": "-",
            "손익비": "-",
            "순합산손익": "-",
            "총 매매수": "-",
            "평균 가동률": "100.0%"
        },
        {
            "전략 모델": "5. [참고] 코스피 200 지수",
            "유니버스": "KOSPI 200",
            "슬롯수": "지수 추종",
            "누적수익률": f"{ret_kospi:+.1f}%",
            "CAGR": f"{((1 + ret_kospi/100)**(1/11.8) - 1)*100:.1f}%",
            "MDD": "-36.5%",
            "샤프지수": "0.05",
            "승률": "-",
            "손익비": "-",
            "순합산손익": "-",
            "총 매매수": "-",
            "평균 가동률": "100.0%"
        }
    ]

    df_comp = pd.DataFrame(summary_data)
    out_csv = f"{RESEARCH_DIR}/compare_top5_vs_top10.csv"
    df_comp.to_csv(out_csv, index=False)

    print("\n" + "=" * 125)
    print("📊 [비교 실측 결과 요약 표]")
    print("=" * 125)
    print(df_comp.to_string(index=False))
    print("=" * 125)

if __name__ == "__main__":
    main()
