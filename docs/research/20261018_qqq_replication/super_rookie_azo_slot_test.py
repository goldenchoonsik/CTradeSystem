import os
import sys
import pandas as pd
import numpy as np

sys.path.append(r"docs/research/20261018_qqq_replication")
from max_tenure_hybrid_backtest import (
    load_stock_data, compute_metrics, NAS_UNIVERSE, OFFICIAL_ADDITIONS, OFFICIAL_REMOVALS
)

REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

def run_super_rookie_azo_test():
    print("=" * 115)
    print("🔬 [EXP-020] 슈퍼루키-3 전략 빈 슬롯(Empty Slot) '오토존(AZO)' 대체 백테스트 (1999~2024, 26개년)")
    print("가설: 나스닥 100 신규 승격주 3슬롯 중 손절/미편입으로 인한 공백을 현금 대신 오토존(AZO)으로 채운다.")
    print("=" * 115)
    
    qqq_df = load_stock_data("QQQ")
    cal = qqq_df.index[(qqq_df.index >= "1999-03-10") & (qqq_df.index <= "2024-12-31")]
    
    # AZO 데이터 로드
    azo_raw = pd.read_csv("DataVault/raw/yahoo/candle_1d/AZO_1d.csv")
    azo_raw["date"] = pd.to_datetime(azo_raw["timestamp"])
    azo_raw = azo_raw.drop_duplicates(subset=["date"]).set_index("date").sort_index()
    azo_px = azo_raw["close"].reindex(cal).ffill().bfill()
    azo_ret = azo_px.pct_change().fillna(0.0)
    
    stocks = {}
    for t in set(NAS_UNIVERSE):
        df = load_stock_data(t)
        if df is not None and len(df) > 50:
            stocks[t] = df
            
    adj_mat = pd.DataFrame({t: df["adj_close"].reindex(cal).ffill() for t, df in stocks.items()}, index=cal)
    dvol_mat = pd.DataFrame({t: df["dvol_60d"].reindex(cal).ffill() for t, df in stocks.items()}, index=cal)
    ret_mat = adj_mat.pct_change().fillna(0.0)
    
    qqq_px = qqq_df["adj_close"].loc[cal]
    qqq_ret = qqq_px.pct_change().fillna(0.0)
    
    # 분기 리밸런싱 날짜
    rebal_dates = []
    prev_quarter = None
    for d in cal:
        q = (d.year, (d.month - 1) // 3)
        if prev_quarter is not None and q != prev_quarter:
            rebal_dates.append(d)
        prev_quarter = q
    rebal_dates = [cal[0]] + rebal_dates
    
    # 신규 편입 이벤트 구축
    d0 = cal[0]
    initial_top = dvol_mat.loc[d0].dropna().sort_values(ascending=False).head(50).index.tolist()
    known_universe = set(initial_top)
    
    addition_events = {}
    for d in rebal_dates:
        if d >= pd.Timestamp("2016-01-01"):
            break
        curr_top = set(dvol_mat.loc[d].dropna().sort_values(ascending=False).head(50).index.tolist())
        brand_new = sorted(list(curr_top - known_universe))
        if brand_new:
            addition_events[d] = [s for s in brand_new if s in stocks]
            for s in brand_new:
                known_universe.add(s)
                
    for d, sym in OFFICIAL_ADDITIONS:
        if d > pd.Timestamp("2024-12-31"):
            continue
        matched = cal[cal >= d]
        if len(matched) > 0:
            dt = matched[0]
            if dt not in addition_events:
                addition_events[dt] = []
            if sym not in addition_events[dt] and sym in stocks:
                addition_events[dt].append(sym)
                
    all_event_dates = sorted(list(set([cal[0]]).union(set(addition_events.keys()))))
    removal_dict = {t: d for d, t in OFFICIAL_REMOVALS}
    cost_rate = 0.0005
    daily_sgov_ret = 0.025 / 252.0
    
    # -------------------------------------------------------------
    # 백테스트 엔진: fill_mode = "cash" vs "azo"
    # -------------------------------------------------------------
    def sim_super_rookie_3(fill_mode="cash"):
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        target_n = 3
        slot_w = 1.0 / target_n
        init_syms = [t for t in initial_top if t in stocks][:target_n]
        hold_entry_px = {t: adj_mat[t].iloc[0] for t in init_syms}
        hold_entry_dt = {t: cal[0] for t in init_syms}
        w_curr = pd.Series(slot_w, index=init_syms)
        
        azo_w = 0.0 # AZO 비중
        stop_cnt = 0
        prune_cnt = 0
        
        for i in range(len(cal)):
            dt = cal[i]
            
            # 1. 일중 -15% 손절 심사 (루키 종목 대상)
            if not w_curr.empty:
                for sym in list(w_curr.index):
                    p_entry = hold_entry_px[sym]
                    if (adj_mat[sym].iloc[i] / p_entry - 1.0) <= -0.15:
                        w_loss = w_curr[sym]
                        eq.iloc[i] = (eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]) * (1.0 - w_loss * 0.0007)
                        w_curr.drop(sym, inplace=True)
                        hold_entry_px.pop(sym, None)
                        hold_entry_dt.pop(sym, None)
                        stop_cnt += 1
                        
            # 2. 신규 편입 및 만기(2.5년=912일) / 탈락 심사
            if dt in all_event_dates and i > 0:
                new_syms = addition_events.get(dt, [])
                new_valid = [s for s in new_syms if s in stocks and s not in w_curr.index]
                survivors = []
                for sym in list(w_curr.index):
                    if dt < removal_dict.get(sym, pd.Timestamp("2099-12-31")):
                        survivors.append(sym)
                    else:
                        hold_entry_px.pop(sym, None)
                        hold_entry_dt.pop(sym, None)
                expired = [s for s in survivors if (dt - hold_entry_dt[s]).days >= 912]
                for exp_s in expired:
                    survivors.remove(exp_s)
                    hold_entry_px.pop(exp_s, None)
                    hold_entry_dt.pop(exp_s, None)
                    
                needed_slots = len(new_valid)
                free_slots = target_n - len(survivors)
                if needed_slots > free_slots and survivors:
                    excess_to_remove = min(len(survivors), needed_slots - free_slots)
                    perf_series = pd.Series({s: adj_mat[s].iloc[i] / hold_entry_px[s] - 1.0 for s in survivors})
                    lowest_perf = perf_series.sort_values(ascending=True).index.tolist()
                    for k_sym in lowest_perf[:excess_to_remove]:
                        survivors.remove(k_sym)
                        hold_entry_px.pop(k_sym, None)
                        hold_entry_dt.pop(k_sym, None)
                        prune_cnt += 1
                        
                open_slots = target_n - len(survivors)
                for s in new_valid[:open_slots]:
                    survivors.append(s)
                    hold_entry_dt[s] = dt
                    hold_entry_px[s] = adj_mat[s].iloc[i]
                    
                if survivors:
                    target_w = pd.Series(slot_w, index=survivors)
                    turnover = (target_w.reindex(w_curr.index.union(target_w.index), fill_value=0.0) - w_curr.reindex(w_curr.index.union(target_w.index), fill_value=0.0)).abs().sum() / 2.0
                    eq.iloc[i] = (eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]) * (1.0 - turnover * cost_rate * 2.0)
                    w_curr = target_w.copy()
                else:
                    w_curr = pd.Series(dtype=float)
                    
            if i == 0:
                continue
                
            eq_prev = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
            
            # 빈 슬롯 비중 계산
            empty_slots_w = max(0.0, 1.0 - w_curr.sum())
            
            if fill_mode == "cash":
                port_ret = (w_curr * ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0)).sum() if not w_curr.empty else 0.0
                fill_ret = empty_slots_w * daily_sgov_ret
            elif fill_mode == "azo":
                port_ret = (w_curr * ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0)).sum() if not w_curr.empty else 0.0
                fill_ret = empty_slots_w * azo_ret.iloc[i]
                
            eq.iloc[i] = eq_prev * (1.0 + port_ret + fill_ret)
            
            # 드리프트 및 정규화
            if not w_curr.empty:
                w_curr = w_curr * (1.0 + ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0))
            tot_w = w_curr.sum() + empty_slots_w * (1.0 + (azo_ret.iloc[i] if fill_mode == "azo" else daily_sgov_ret))
            if tot_w > 0:
                w_curr = w_curr / tot_w
                
        return eq, stop_cnt, prune_cnt

    print("⏳ 백테스트 시뮬레이션 계산 중...")
    eq_cash, stop_cash, prune_cash = sim_super_rookie_3(fill_mode="cash")
    eq_azo, stop_azo, prune_azo = sim_super_rookie_3(fill_mode="azo")
    eq_azo_only = (1.0 + azo_ret).cumprod()
    eq_qqq = (1.0 + qqq_ret).cumprod()
    
    # 성과 분석
    results = {}
    strategies = {
        "🥇 슈퍼루키-3 + AZO 빈슬롯": eq_azo,
        "🥈 슈퍼루키-3 (현금 빈슬롯, 기존 원본)": eq_cash,
        "🥉 오토존(AZO) 100% 바이앤홀드": eq_azo_only,
        "4위 QQQ ETF (기준선)": eq_qqq,
    }
    
    # 위기 구간
    crashes = {
        "닷컴버블": ("2000-03-24", "2002-10-09"),
        "금융위기": ("2007-10-09", "2009-03-09"),
        "코로나19": ("2020-02-19", "2020-03-23"),
        "2022금리인상": ("2022-01-03", "2022-10-13"),
    }
    
    print("\n" + "=" * 115)
    print("🏆 26년 전체 (1999~2024년) 전수 성과 비교표")
    print("=" * 115)
    print(f"{'전략명':<35} | {'총수익률':>12} | {'CAGR':>8} | {'연변동성':>8} | {'샤프지수':>8} | {'전체MDD':>9} | {'닷컴MDD':>9} | {'2008MDD':>9}")
    print("-" * 115)
    
    for name, s_eq in strategies.items():
        m = compute_metrics(s_eq, qqq_ret)
        
        # 각 위기별 MDD
        def get_sub_mdd(sub_eq):
            return ((sub_eq / sub_eq.cummax() - 1.0).min()) * 100
            
        mdd_dot = get_sub_mdd(s_eq.loc["2000-03-24":"2002-10-09"])
        mdd_gfc = get_sub_mdd(s_eq.loc["2007-10-09":"2009-03-09"])
        
        print(f"{name:<35} | {m['Total Return']:+11.1f}% | {m['CAGR']:7.2f}% | {m['Volatility']:7.2f}% | {m['Sharpe']:8.2f} | {m['MDD']:8.2f}% | {mdd_dot:8.2f}% | {mdd_gfc:8.2f}%")

    print("\n" + "=" * 115)
    print("💎 최근 10년 클린 구간 (2015-01-01 ~ 2024-12-31) 성과")
    print("=" * 115)
    print(f"{'전략명':<35} | {'10년 총수익률':>12} | {'10년 CAGR':>9} | {'10년 변동성':>9} | {'10년 샤프':>8} | {'10년 MDD':>9}")
    print("-" * 115)
    for name, s_eq in strategies.items():
        sub_eq = s_eq.loc["2015-01-01":"2024-12-31"]
        sub_eq = sub_eq / sub_eq.iloc[0]
        m = compute_metrics(sub_eq, qqq_ret.loc["2015-01-01":"2024-12-31"])
        print(f"{name:<35} | {m['Total Return']:+11.1f}% | {m['CAGR']:8.2f}% | {m['Volatility']:8.2f}% | {m['Sharpe']:8.2f} | {m['MDD']:8.2f}%")

    print("\n" + "=" * 115)
    print("🛡️ 역대 4대 대폭락 기간별 실제 수익률 비교 (%)")
    print("=" * 115)
    print(f"{'전략명':<35} | {'1.닷컴버블':>10} | {'2.금융위기':>10} | {'3.코로나19':>10} | {'4.2022금리':>10}")
    print("-" * 115)
    for name, s_eq in strategies.items():
        row_str = f"{name:<35}"
        for c_name, (s_dt, e_dt) in crashes.items():
            sub = s_eq.loc[s_dt:e_dt]
            r = (sub.iloc[-1] / sub.iloc[0] - 1.0) * 100
            row_str += f" | {r:+9.2f}%"
        print(row_str)

if __name__ == "__main__":
    run_super_rookie_azo_test()
