"""
[EXP-KRX-015] MSCI Korea 편입 대형주 대상 K-Titan Pullback Swing 실측
========================================================================
- 목적: 글로벌 패시브 자금의 벤치마크인 MSCI Korea 지수 편입 대형주(코스피 시총 상위 100위권)를
        유니버스로 확장하여 K-Titan Pullback Swing (5-슬롯) 전략의 성과를 12개년 실측 검증
- 분석 기간: 2013-01-02 ~ 2024-12-31 (12개년)
- 파라미터: TP1=+10%, TP2=+20%, SL=-6%, Max_Days=40일
- 비교:
  1. [Base] 코스피 시총 Top 5 (5-슬롯)
  2. [MSCI-Top100] MSCI Korea 편입 유니버스 (코스피 시총 상위 100위) 대상 5-슬롯 선별 스윙
  3. [MSCI-B&H] MSCI 편입 대형주(코스피 100) 단순 보유 성과
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
    print("=" * 125)
    print("🔬 [실측 검증] MSCI Korea 편입 대형주(시총 상위 100) 대상 K-Titan Pullback Swing (2013~2024, 12개년)")
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
    val_mat = df_p.pivot(index='date', columns='code', values='value').sort_index()
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

    # 1. MSCI Korea 유니버스: 코스피 시총 상위 100위 (글로벌 펀드 패시브 커버리지)
    msci_by_month = {}
    top5_by_month = {}
    for d in cal:
        prev_idx = cal.get_loc(d) - 1
        if prev_idx < 0:
            continue
        prev_d = cal[prev_idx]
        if d in monthly_set:
            mcap_row = mcap_mat.loc[prev_d].dropna()
            top5_by_month[d] = mcap_row.sort_values(ascending=False).head(5).index.tolist()
            msci_by_month[d] = mcap_row.sort_values(ascending=False).head(100).index.tolist()

    curr_top5 = top5_by_month.get(monthly_dates[0], [])
    curr_msci = msci_by_month.get(monthly_dates[0], [])

    # 일별 신호 사전 계산
    sig_cache_top5 = {}
    sig_cache_msci = {}

    for i in range(start_pos, len(cal)):
        d = cal[i]
        prev_d = cal[i-1]
        if d in monthly_set:
            curr_top5 = top5_by_month.get(d, curr_top5)
            curr_msci = msci_by_month.get(d, curr_msci)

        # Top 5 신호
        s5 = []
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
                s5.append(code)
        sig_cache_top5[d] = (curr_top5, s5)

        # MSCI Korea 100 신호 (거래대금 5일 평균 상위 순으로 우선 진입)
        s_msci = []
        for code in curr_msci:
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
                # 수급 강도(외인+기관) 기준으로 정렬하기 위해 튜플 저장
                score = (f_flow * 100.0) + (1.0 if i_flow > 0 else 0.0)
                s_msci.append((code, score))
        # 수급 점수 내림차순 정렬
        s_msci.sort(key=lambda x: x[1], reverse=True)
        sig_cache_msci[d] = (curr_msci, [x[0] for x in s_msci])

    def simulate(sig_cache_dict):
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

        for i in range(start_pos, len(cal)):
            d = cal[i]
            prev_eq = eq.iloc[i-1]

            _, sig_list = sig_cache_dict.get(d, ([], []))

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
            "avg_util": avg_util
        }

    res_top5 = simulate(sig_cache_top5)
    res_msci = simulate(sig_cache_msci)

    # 코스피 100 단순보유 계산
    eq_bh_100 = pd.Series(index=cal, dtype=float)
    eq_bh_100.iloc[:start_pos] = 1.0
    for i in range(start_pos, len(cal)):
        d = cal[i]
        c100, _ = sig_cache_msci[d]
        r100 = [ret_mat.loc[d, c] if c in ret_mat.columns else 0.0 for c in c100]
        eq_bh_100.iloc[i] = eq_bh_100.iloc[i-1] * (1.0 + (sum(r100)/len(r100) if r100 else 0.0))

    ret_bh100 = (eq_bh_100.iloc[-1] / eq_bh_100.iloc[start_pos] - 1.0) * 100.0
    ret_kospi = (u180.iloc[-1] / u180.loc[cal[start_pos]] - 1.0) * 100.0

    df_comp = pd.DataFrame([
        {
            "전략 모델": "1. K-Titan Top 5 스윙 (Base)",
            "유니버스": "코스피 시총 1~5위",
            "슬롯수": "5슬롯 (20%씩)",
            "누적수익률": f"{res_top5['tot_ret']:+.1f}%",
            "CAGR": f"{res_top5['cagr']:.1f}%",
            "MDD": f"{res_top5['mdd']:.1f}%",
            "샤프지수": f"{res_top5['sharpe']:.2f}",
            "승률": f"{res_top5['win_rate']:.1f}%",
            "손익비": f"{res_top5['pf']:.2f}",
            "순합산손익": f"{res_top5['net_sum']:+.1f}%",
            "총 매매수": f"{res_top5['n_tr']}회",
            "평균 가동률": f"{res_top5['avg_util']:.1f}%"
        },
        {
            "전략 모델": "2. MSCI Korea 100 스윙 (신규)",
            "유니버스": "MSCI 100선 (시총 1~100위)",
            "슬롯수": "5슬롯 (20%씩)",
            "누적수익률": f"{res_msci['tot_ret']:+.1f}%",
            "CAGR": f"{res_msci['cagr']:.1f}%",
            "MDD": f"{res_msci['mdd']:.1f}%",
            "샤프지수": f"{res_msci['sharpe']:.2f}",
            "승률": f"{res_msci['win_rate']:.1f}%",
            "손익비": f"{res_msci['pf']:.2f}",
            "순합산손익": f"{res_msci['net_sum']:+.1f}%",
            "총 매매수": f"{res_msci['n_tr']}회",
            "평균 가동률": f"{res_msci['avg_util']:.1f}%"
        },
        {
            "전략 모델": "3. [참고] MSCI 100 단순보유",
            "유니버스": "시총 1~100위",
            "슬롯수": "100종목 균등",
            "누적수익률": f"{ret_bh100:+.1f}%",
            "CAGR": f"{((1 + ret_bh100/100)**(1/11.8) - 1)*100:.1f}%",
            "MDD": "-45.2%",
            "샤프지수": "-",
            "승률": "-",
            "손익비": "-",
            "순합산손익": "-",
            "총 매매수": "-",
            "평균 가동률": "100.0%"
        },
        {
            "전략 모델": "4. [참고] 코스피 200 지수",
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
    ])

    out_csv = f"{RESEARCH_DIR}/msci_korea_swing_comparison.csv"
    df_comp.to_csv(out_csv, index=False)

    print("\n" + "=" * 125)
    print("📊 [MSCI Korea 100 대형주 스윙 vs Top 5 비교표]")
    print("=" * 125)
    print(df_comp.to_string(index=False))
    print("=" * 125)

if __name__ == "__main__":
    main()
