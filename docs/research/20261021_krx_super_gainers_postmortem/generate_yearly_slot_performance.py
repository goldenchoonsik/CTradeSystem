"""
[EXP-KRX-012] K-Titan Pullback Swing 연도별 및 슬롯별 성과 매트릭스 산출
========================================================================
- 최적 파라미터: TP1=+10%, TP2=+20%, SL=-6%, Max_Days=40일
- 분석 대상: 2013~2024년 (12개년)
- 산출물:
  1. 연도별 계좌 수익률 vs 지수 vs 단순보유 비교표
  2. 연도별 × 슬롯별(Slot 1~5) 수익률 매트릭스 표
  3. 슬롯별 통산 성과 (매매수, 승률, 손익비, 순합산손익)
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

def main():
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

    # 최적 파라미터
    tp1 = 0.10
    tp2 = 0.20
    sl = -0.06
    max_days = 40

    # 시뮬레이션: 전체 계좌 및 각 슬롯별 일별 에쿼티 추적
    eq_total = pd.Series(index=cal, dtype=float)
    eq_total.iloc[:start_pos] = 1.0

    # 각 슬롯별 개별 에쿼티 (초기값 1.0)
    slot_eq = {s_idx: pd.Series(index=cal, dtype=float) for s_idx in range(num_slots)}
    for s_idx in range(num_slots):
        slot_eq[s_idx].iloc[:start_pos] = 1.0

    slots = [{
        "ticker": None,
        "entry_price": 0.0,
        "peak_price": 0.0,
        "holding_days": 0,
        "remaining_ratio": 1.0,
        "is_cash": True
    } for _ in range(num_slots)]

    # 슬롯별 매매 기록 저장
    slot_trades = {s_idx: [] for s_idx in range(num_slots)}
    yearly_trades = {yr: [] for yr in range(2013, 2025)}

    for i in range(start_pos, len(cal)):
        d = cal[i]
        yr = d.year
        prev_eq = eq_total.iloc[i-1]

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

        # 일별 수익률 계산
        slot_daily_rets = []
        for s_idx, s in enumerate(slots):
            prev_s_eq = slot_eq[s_idx].iloc[i-1]
            if s["is_cash"] or s["ticker"] is None:
                r_slot = daily_cash_ret
                slot_eq[s_idx].iloc[i] = prev_s_eq * (1.0 + r_slot)
                slot_daily_rets.append(r_slot)
                continue

            t = s["ticker"]
            r_day = ret_mat.loc[d, t] if t in ret_mat.columns else 0.0
            curr_px = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan

            if np.isnan(curr_px) or curr_px <= 0:
                slot_eq[s_idx].iloc[i] = prev_s_eq
                slot_daily_rets.append(0.0)
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
                r_slot = exit_ret * s["remaining_ratio"]
                trade_record = {
                    "year": yr,
                    "date": d,
                    "slot": s_idx + 1,
                    "ticker": t,
                    "gain_pct": gain_from_entry * 100.0,
                    "action": action,
                    "days": s["holding_days"]
                }
                slot_trades[s_idx].append(trade_record)
                yearly_trades[yr].append(trade_record)

                s["is_cash"] = True
                s["ticker"] = None
            else:
                if s["remaining_ratio"] == 1.0 and gain_from_entry >= tp1:
                    s["remaining_ratio"] = 0.5
                    r_slot = r_day * 0.5 + tp1 * 0.5
                else:
                    r_slot = r_day * s["remaining_ratio"]

            slot_eq[s_idx].iloc[i] = prev_s_eq * (1.0 + r_slot)
            slot_daily_rets.append(r_slot)

        port_ret = sum(slot_daily_rets) / num_slots
        eq_total.iloc[i] = prev_eq * (1.0 + port_ret)

    # 벤치마크: Top 5 단순보유(B&H) 일별 에쿼티
    eq_bh = pd.Series(index=cal, dtype=float)
    eq_bh.iloc[:start_pos] = 1.0
    for i in range(start_pos, len(cal)):
        d = cal[i]
        curr_top5, _ = signal_cache.get(d, ([], []))
        rets = [ret_mat.loc[d, c] if c in ret_mat.columns else 0.0 for c in curr_top5]
        day_ret = (sum(rets) / len(rets)) if rets else 0.0
        eq_bh.iloc[i] = eq_bh.iloc[i-1] * (1.0 + day_ret)

    # 1. 연도별 전체 성과 테이블
    years = list(range(2013, 2025))
    yearly_summary = []

    for yr in years:
        d_yr = cal[cal.year == yr]
        if len(d_yr) == 0:
            continue
        d_start = d_yr[0]
        d_end = d_yr[-1]

        # 연간 수익률: 해당 연도 말 / 해당 연도 초 (또는 전년도 말)
        idx_start = cal.get_loc(d_start)
        p_idx = max(0, idx_start - 1)
        base_d = cal[p_idx]

        r_strat = (eq_total.loc[d_end] / eq_total.loc[base_d] - 1.0) * 100.0
        r_bh = (eq_bh.loc[d_end] / eq_bh.loc[base_d] - 1.0) * 100.0
        r_kospi = (u180.loc[d_end] / u180.loc[base_d] - 1.0) * 100.0

        tr_yr = yearly_trades[yr]
        n_tr = len(tr_yr)
        if n_tr > 0:
            gains = [t["gain_pct"] for t in tr_yr]
            wins = [g for g in gains if g > 0]
            losses = [g for g in gains if g <= 0]
            win_rate = (len(wins) / n_tr) * 100.0
            net_sum = sum(wins) - abs(sum(losses))
        else:
            win_rate = 0.0
            net_sum = 0.0

        yearly_summary.append({
            "연도": yr,
            "전략 수익률(%)": r_strat,
            "Top5 단순보유(%)": r_bh,
            "코스피 지수(%)": r_kospi,
            "초과수익(vs지수)": r_strat - r_kospi,
            "매매횟수": n_tr,
            "승률(%)": win_rate,
            "순합산손익(%)": net_sum
        })

    df_yearly = pd.DataFrame(yearly_summary)

    # 2. 연도별 × 슬롯별 수익률 매트릭스
    slot_yearly_matrix = []
    for yr in years:
        d_yr = cal[cal.year == yr]
        if len(d_yr) == 0:
            continue
        d_start = d_yr[0]
        d_end = d_yr[-1]
        idx_start = cal.get_loc(d_start)
        p_idx = max(0, idx_start - 1)
        base_d = cal[p_idx]

        row = {"연도": yr}
        for s_idx in range(num_slots):
            r_s = (slot_eq[s_idx].loc[d_end] / slot_eq[s_idx].loc[base_d] - 1.0) * 100.0
            row[f"슬롯 {s_idx+1}"] = r_s
        slot_yearly_matrix.append(row)

    df_slot_yearly = pd.DataFrame(slot_yearly_matrix)

    # 3. 슬롯별 12개년 통산 성과
    slot_lifetime = []
    for s_idx in range(num_slots):
        sub_s_eq = slot_eq[s_idx].iloc[start_pos:]
        tot_r = (sub_s_eq.iloc[-1] / sub_s_eq.iloc[0] - 1.0) * 100.0
        cagr = ((sub_s_eq.iloc[-1] / sub_s_eq.iloc[0]) ** (1.0 / 11.8) - 1.0) * 100.0
        dd = (sub_s_eq - sub_s_eq.cummax()) / sub_s_eq.cummax()
        mdd = dd.min() * 100.0

        tr_s = slot_trades[s_idx]
        n_tr = len(tr_s)
        gains = [t["gain_pct"] for t in tr_s]
        wins = [g for g in gains if g > 0]
        losses = [g for g in gains if g <= 0]
        win_rate = (len(wins) / n_tr * 100.0) if n_tr > 0 else 0.0
        sum_w = sum(wins) if wins else 0.0
        sum_l = abs(sum(losses)) if losses else 0.0
        net_s = sum_w - sum_l
        pf = (sum_w / sum_l) if sum_l > 0 else 0.0

        slot_lifetime.append({
            "슬롯": f"슬롯 {s_idx+1}",
            "누적수익률(%)": tot_r,
            "CAGR(%)": cagr,
            "MDD(%)": mdd,
            "총 매매수": n_tr,
            "승률(%)": win_rate,
            "손익비(PF)": pf,
            "순합산손익(%)": net_s
        })
    df_slot_life = pd.DataFrame(slot_lifetime)

    # CSV 저장
    df_yearly.to_csv(f"{RESEARCH_DIR}/yearly_performance.csv", index=False)
    df_slot_yearly.to_csv(f"{RESEARCH_DIR}/slot_yearly_matrix.csv", index=False)
    df_slot_life.to_csv(f"{RESEARCH_DIR}/slot_lifetime_stats.csv", index=False)

    print("=" * 110)
    print("📅 [1] 연도별 전체 성과 비교 테이블 (2013~2024)")
    print("=" * 110)
    print(df_yearly.to_string(index=False, float_format=lambda x: f"{x:+.1f}" if isinstance(x, float) else f"{x}"))

    print("\n" + "=" * 110)
    print("🎰 [2] 연도별 × 슬롯별(Slot 1~5) 연간 수익률 매트릭스 (%)")
    print("=" * 110)
    print(df_slot_yearly.to_string(index=False, float_format=lambda x: f"{x:+.1f}%" if isinstance(x, float) else f"{x}"))

    print("\n" + "=" * 110)
    print("💎 [3] 슬롯별 12개년 통산 누적 성과 지표")
    print("=" * 110)
    print(df_slot_life.to_string(index=False, float_format=lambda x: f"{x:+.1f}" if isinstance(x, float) else f"{x}"))
    print("=" * 110)

if __name__ == "__main__":
    main()
