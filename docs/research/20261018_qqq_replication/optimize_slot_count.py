import os
import sys
import pandas as pd
import numpy as np

sys.path.append(r"docs/research/20261018_qqq_replication")
from max_tenure_hybrid_backtest import (
    load_stock_data, compute_metrics, NAS_UNIVERSE, OFFICIAL_ADDITIONS, OFFICIAL_REMOVALS
)

ROOT = "DataVault"
REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

def run_slot_optimization():
    print("=" * 115)
    print("🔍 [실측] 최적의 슬롯(종목수 N) 탐색 백테스트 - 꼴찌교체 버그수정판 (1999-03-10 ~ 2024-12-31, 26년)")
    print("=" * 115)
    
    qqq_df = load_stock_data("QQQ")
    cal = qqq_df.index[(qqq_df.index >= "1999-03-10") & (qqq_df.index <= "2024-12-31")]
    
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
    qqq_bm_eq = (1.0 + qqq_ret).cumprod()
    
    # 신규 편입 이벤트 매핑
    rebal_dates = []
    prev_quarter = None
    for d in cal:
        q = (d.year, (d.month - 1) // 3)
        if prev_quarter is not None and q != prev_quarter:
            rebal_dates.append(d)
        prev_quarter = q
    rebal_dates = [cal[0]] + rebal_dates
    
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
    stop_loss_pct = 0.15 # -15% 손절

    # Simulator function for slot count N (버그 수정판)
    def simulate_n_slots(target_n):
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        w_curr = pd.Series(dtype=float)
        hold_entry_px = {}
        hold_entry_dt = {}
        
        slot_w = 1.0 / target_n
        initial_n = [t for t in initial_top if t in stocks][:target_n]
        for t in initial_n:
            hold_entry_dt[t] = cal[0]
            hold_entry_px[t] = adj_mat[t].iloc[0]
        w_curr = pd.Series(slot_w, index=initial_n)
        
        stop_count = 0
        prune_count = 0
        
        for i in range(len(cal)):
            dt = cal[i]
            
            # 1. 일별 손절 검사 (-15%)
            if not w_curr.empty:
                for sym in list(w_curr.index):
                    px_now = adj_mat[sym].iloc[i]
                    p_entry = hold_entry_px[sym]
                    stock_ret = (px_now / p_entry) - 1.0 if p_entry > 0 else 0.0
                    
                    if stock_ret <= -stop_loss_pct:
                        w_loss = w_curr[sym]
                        eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                        eq.iloc[i] = eq_prev_val * (1.0 - w_loss * 0.0007)
                        w_curr.drop(sym, inplace=True)
                        hold_entry_px.pop(sym, None)
                        hold_entry_dt.pop(sym, None)
                        stop_count += 1
                        
            # 2. 리밸런싱 이벤트 도래 시
            if dt in all_event_dates and i > 0:
                new_syms = addition_events.get(dt, [])
                new_valid = [s for s in new_syms if s in stocks and s not in w_curr.index]
                
                # A. 공식 탈락 방출
                survivors = []
                for sym in list(w_curr.index):
                    rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                    if dt < rem_d:
                        survivors.append(sym)
                    else:
                        hold_entry_px.pop(sym, None)
                        hold_entry_dt.pop(sym, None)
                        
                # B. 2.5년 만기 방출
                expired = [s for s in survivors if (dt - hold_entry_dt[s]).days >= 912]
                for exp_s in expired:
                    survivors.remove(exp_s)
                    hold_entry_px.pop(exp_s, None)
                    hold_entry_dt.pop(exp_s, None)
                    
                # C. [수정됨] 신규 종목 충원을 위한 부족 슬롯 계산 및 최하위 꼴찌 방출
                needed_slots = len(new_valid)
                free_slots = target_n - len(survivors)
                if needed_slots > free_slots and survivors:
                    excess_to_remove = min(len(survivors), needed_slots - free_slots)
                    perf_series = pd.Series(dtype=float)
                    for s in survivors:
                        p_start = hold_entry_px[s]
                        p_now = adj_mat[s].iloc[i]
                        perf_series[s] = (p_now / p_start) - 1.0 if p_start > 0 else 0.0
                    lowest_perf = perf_series.sort_values(ascending=True).index.tolist()
                    for k_sym in lowest_perf[:excess_to_remove]:
                        survivors.remove(k_sym)
                        hold_entry_px.pop(k_sym, None)
                        hold_entry_dt.pop(k_sym, None)
                        prune_count += 1
                        
                # D. 신규 종목 충원
                open_slots = target_n - len(survivors)
                if open_slots > 0 and new_valid:
                    to_add = new_valid[:open_slots]
                    for s in to_add:
                        survivors.append(s)
                        hold_entry_dt[s] = dt
                        hold_entry_px[s] = adj_mat[s].iloc[i]
                        
                # E. 리밸런싱 집행 (종목당 1/N 배분)
                if survivors:
                    target_w = pd.Series(slot_w, index=survivors)
                    union_idx = w_curr.index.union(target_w.index)
                    w_old = w_curr.reindex(union_idx, fill_value=0.0)
                    w_new = target_w.reindex(union_idx, fill_value=0.0)
                    turnover = (w_new - w_old).abs().sum() / 2.0
                    cost = turnover * cost_rate * 2.0
                    eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                    eq.iloc[i] = eq_prev_val * (1.0 - cost)
                    w_curr = target_w.copy()
                else:
                    w_curr = pd.Series(dtype=float)
                    
            if i == 0:
                continue
                
            eq_prev = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
            if not w_curr.empty:
                r_stocks = ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0)
                port_ret = (w_curr * r_stocks).sum()
                w_curr = w_curr * (1.0 + r_stocks)
            else:
                port_ret = 0.0
                
            cash_w = max(0.0, 1.0 - w_curr.sum())
            cash_ret = cash_w * daily_sgov_ret
            total_ret = port_ret + cash_ret
            eq.iloc[i] = eq_prev * (1.0 + total_ret)
            
            tot_w = w_curr.sum() + cash_w
            if tot_w > 0:
                w_curr = w_curr / tot_w
                
        return eq, stop_count, prune_count

    # Test candidate slot counts
    candidate_slots = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20]
    sweep_results = []
    
    print("⏳ 후보 슬롯수별 시뮬레이션 연산 중 (N = 1 ~ 20)...")
    for n in candidate_slots:
        eq_n, stop_cnt, prune_cnt = simulate_n_slots(n)
        m = compute_metrics(eq_n, qqq_bm_eq)
        
        # Check 2000 Dotcom MDD and 2008 GFC MDD
        sub_dotcom = eq_n.loc["2000-01-01":"2002-12-31"]
        mdd_dotcom = ((sub_dotcom - sub_dotcom.cummax()) / sub_dotcom.cummax()).min()
        
        sub_gfc = eq_n.loc["2007-10-01":"2009-03-31"]
        mdd_gfc = ((sub_gfc - sub_gfc.cummax()) / sub_gfc.cummax()).min()
        
        sweep_results.append({
            "슬롯수(N)": n,
            "종목당 비중": f"{100/n:.1f}%",
            "총수익률(%)": round(m["Total Return"] * 100, 1),
            "CAGR(%)": round(m["CAGR"] * 100, 2),
            "연 변동성(%)": round(m["Volatility"] * 100, 2),
            "샤프 지수": round(m["Sharpe"], 2),
            "전체 MDD(%)": round(m["MDD"] * 100, 2),
            "닷컴 MDD(%)": round(mdd_dotcom * 100, 2),
            "금융위기 MDD(%)": round(mdd_gfc * 100, 2),
            "손절횟수": stop_cnt,
            "꼴찌교체횟수": prune_cnt
        })
        
    df_res = pd.DataFrame(sweep_results)
    
    print("\n" + "=" * 130)
    print("🏆 [슬롯수(종목수 N) 최적화 스윕 결과 - 꼴찌교체 정상 반영판 (1999~2024년 26년 전수 실측)]")
    print("=" * 130)
    print(df_res.to_string(index=False))
    
    csv_path = f"{REPORTS_DIR}/slot_optimization_results.csv"
    try:
        df_res.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ 스윕 결과 CSV 저장 완료: {csv_path}")
    except PermissionError:
        csv_path_fixed = f"{REPORTS_DIR}/slot_optimization_results_fixed.csv"
        df_res.to_csv(csv_path_fixed, index=False, encoding="utf-8-sig")
        print(f"\n⚠️ 기존 파일이 열려 있어 새 파일로 저장 완료: {csv_path_fixed}")

if __name__ == "__main__":
    run_slot_optimization()
