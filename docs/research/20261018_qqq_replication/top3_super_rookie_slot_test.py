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

def run_top3_slot_strategy_test():
    print("=" * 115)
    print("🔬 [실측 백테스트] 시총 최상위 3종목 × 슈퍼루키 슬롯 방식 결합 전략 (1999~2024년 26년)")
    print("규칙: 3개 슬롯(종목당 33.3%), -15% 손절 시 슬롯 공백(현금대기), 월단위 상위 3종목 변경 시에만 리밸런싱")
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

    # 1. 월말 리밸런싱 검사 일자 (Monthly check dates)
    monthly_dates = []
    prev_month = None
    for d in cal:
        m = (d.year, d.month)
        if prev_month is not None and m != prev_month:
            monthly_dates.append(d)
        prev_month = m
    monthly_dates = [cal[0]] + monthly_dates
    monthly_set = set(monthly_dates)
    
    cost_rate = 0.0005 # 편도 0.05% (왕복 0.10%)
    daily_sgov_ret = 0.025 / 252.0 # 현금 슬롯 SGOV 이자
    stop_loss_pct = 0.15 # -15% 손절
    target_n = 3
    slot_w = 1.0 / target_n
    
    # =========================================================================
    # 전략: 시총 최상위 3종목 × 슈퍼루키 슬롯형 (Monthly Change-Triggered + StopLoss)
    # =========================================================================
    def sim_top3_slot_strategy():
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        w_curr = pd.Series(dtype=float)
        hold_entry_px = {}
        hold_entry_dt = {}
        
        # 시작 시점: 상위 3종목 편입
        d0 = cal[0]
        initial_top3 = dvol_mat.loc[d0].dropna().sort_values(ascending=False).head(3).index.tolist()
        for t in initial_top3:
            hold_entry_dt[t] = d0
            hold_entry_px[t] = adj_mat[t].iloc[0]
        w_curr = pd.Series(slot_w, index=initial_top3)
        
        prev_top3_set = set(initial_top3)
        
        stop_count = 0
        rebal_count = 0
        rebal_logs = []
        
        for i in range(len(cal)):
            dt = cal[i]
            
            # 1. 일별 손절 검사 (-15% 도달 시 즉시 매도 & 슬롯 비워두기)
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
                        
            # 2. 월 단위 리밸런싱 검사 시점
            if dt in monthly_set and i > 0:
                # 현재 시점 시총(거래대금) 상위 3종목 산출
                curr_top3 = dvol_mat.loc[dt].dropna().sort_values(ascending=False).head(3).index.tolist()
                curr_top3_set = set(curr_top3)
                
                # ⭐ 트리거 조건: 최고 시총 종목 3개 중에 하나라도 변경되었을 경우!
                has_changed = (curr_top3_set != prev_top3_set)
                
                if has_changed:
                    rebal_count += 1
                    
                    # A. 현재 보유 종목 중 새로운 Top 3에 들지 못하는 종목 방출
                    survivors = []
                    for sym in list(w_curr.index):
                        if sym in curr_top3_set:
                            survivors.append(sym)
                        else:
                            hold_entry_px.pop(sym, None)
                            hold_entry_dt.pop(sym, None)
                            
                    # B. 새로운 Top 3 중 아직 포트폴리오에 없는 종목 충원
                    new_adds = [s for s in curr_top3 if s not in survivors]
                    open_slots = target_n - len(survivors)
                    for s in new_adds[:open_slots]:
                        survivors.append(s)
                        hold_entry_dt[s] = dt
                        hold_entry_px[s] = adj_mat[s].iloc[i]
                        
                    # C. 리밸런싱 가중치 재조정 (살아남은 종목당 33.3% 고정, 손절 슬롯은 현금)
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
                        
                    rebal_logs.append({
                        "date": dt.strftime("%Y-%m-%d"),
                        "old_top3": ", ".join(sorted(list(prev_top3_set))),
                        "new_top3": ", ".join(curr_top3),
                        "held": ", ".join(survivors) if survivors else "(현금100%)",
                        "cash_pct": f"{(1.0 - w_curr.sum())*100:.1f}%"
                    })
                    
                    prev_top3_set = curr_top3_set
                    
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
                
        return eq, stop_count, rebal_count, rebal_logs

    # -------------------------------------------------------------
    # 2. 슈퍼루키-3
    # -------------------------------------------------------------
    def sim_super_rookie_3():
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        target_n = 3
        slot_w = 1.0 / target_n
        init_syms = [t for t in initial_top if t in stocks][:target_n]
        hold_entry_px = {t: adj_mat[t].iloc[0] for t in init_syms}
        hold_entry_dt = {t: cal[0] for t in init_syms}
        w_curr = pd.Series(slot_w, index=init_syms)
        stop_cnt = 0
        prune_cnt = 0
        
        for i in range(len(cal)):
            dt = cal[i]
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
            port_ret = (w_curr * ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0)).sum() if not w_curr.empty else 0.0
            cash_ret = max(0.0, 1.0 - w_curr.sum()) * daily_sgov_ret
            eq.iloc[i] = eq_prev * (1.0 + port_ret + cash_ret)
            if not w_curr.empty:
                w_curr = w_curr * (1.0 + ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0))
            tot_w = w_curr.sum() + max(0.0, 1.0 - w_curr.sum())
            if tot_w > 0:
                w_curr = w_curr / tot_w
        return eq, stop_cnt, prune_cnt

    # -------------------------------------------------------------
    # 3. 기존 시총 Top 3 (분기 고정, 손절 없음)
    # -------------------------------------------------------------
    def sim_top3_mega(rebalance_mode="quarterly"):
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        d0_top3 = dvol_mat.loc[cal[0]].dropna().sort_values(ascending=False).head(3).index.tolist()
        w_curr = pd.Series(1.0 / 3.0, index=d0_top3)
        rebal_check_dates = set(rebal_dates) if rebalance_mode == "quarterly" else set(all_event_dates)
        
        for i in range(len(cal)):
            dt = cal[i]
            if dt in rebal_check_dates and i > 0:
                curr_top3 = dvol_mat.loc[dt].dropna().sort_values(ascending=False).head(3).index.tolist()
                target_w = pd.Series(1.0 / 3.0, index=curr_top3)
                turnover = (target_w.reindex(w_curr.index.union(target_w.index), fill_value=0.0) - w_curr.reindex(w_curr.index.union(target_w.index), fill_value=0.0)).abs().sum() / 2.0
                eq.iloc[i] = (eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]) * (1.0 - turnover * cost_rate * 2.0)
                w_curr = target_w.copy()
                
            if i == 0:
                continue
            eq_prev = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
            r_stocks = ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0)
            port_ret = (w_curr * r_stocks).sum()
            eq.iloc[i] = eq_prev * (1.0 + port_ret)
            w_curr = w_curr * (1.0 + r_stocks)
            if w_curr.sum() > 0:
                w_curr = w_curr / w_curr.sum()
        return eq

    eq_rookie3, r_sc, r_pc = sim_super_rookie_3()
    eq_top3_orig = sim_top3_mega("quarterly")
    eq_top3_slot, t_sc, t_rc, t_logs = sim_top3_slot_strategy()
    
    # 4개 전략 비교
    comparison = {
        "1. 시총Top3_슬롯형 (월단위 변경시 리밸런싱 + -15% 손절공백)": eq_top3_slot,
        "2. 슈퍼루키-3 (신규 승격주 3종목 집중 챔피언)": eq_rookie3,
        "3. 기존 시총Top3 (분기 고정 리밸런싱, 손절 없음)": eq_top3_orig,
        "4. QQQ_Actual_ETF (기준선)": qqq_bm_eq
    }
    
    # 26년 전체 성과 (1999~2024)
    rows_26y = []
    for name, s_eq in comparison.items():
        m = compute_metrics(s_eq, qqq_bm_eq)
        
        sub_dotcom = s_eq.loc["2000-01-01":"2002-12-31"]
        mdd_dotcom = ((sub_dotcom - sub_dotcom.cummax()) / sub_dotcom.cummax()).min()
        
        sub_gfc = s_eq.loc["2007-10-01":"2009-03-31"]
        mdd_gfc = ((sub_gfc - sub_gfc.cummax()) / sub_gfc.cummax()).min()
        
        sub_2022 = s_eq.loc["2022-01-01":"2022-12-31"]
        mdd_2022 = ((sub_2022 - sub_2022.cummax()) / sub_2022.cummax()).min()
        
        stops_str = str(t_sc) if "슬롯형" in name else (str(r_sc) if "슈퍼루키" in name else "-")
        rebal_str = str(t_rc) if "슬롯형" in name else "-"
        
        rows_26y.append({
            "전략 명칭": name,
            "총수익률(%)": round(m["Total Return"] * 100, 1),
            "CAGR(%)": round(m["CAGR"] * 100, 2),
            "연변동성(%)": round(m["Volatility"] * 100, 2),
            "샤프지수": round(m["Sharpe"], 2),
            "전체MDD(%)": round(m["MDD"] * 100, 2),
            "닷컴MDD(%)": round(mdd_dotcom * 100, 2),
            "금융위기MDD(%)": round(mdd_gfc * 100, 2),
            "2022MDD(%)": round(mdd_2022 * 100, 2),
            "베타": round(m["Beta"], 2) if not np.isnan(m["Beta"]) else 1.0,
            "손절횟수": stops_str,
            "리밸런싱횟수": rebal_str
        })
        
    df_26y = pd.DataFrame(rows_26y)
    
    print("\n" + "=" * 140)
    print("🏆 [전체 26개년 (1999~2024) 시총 Top 3 슬롯형 vs 슈퍼루키-3 vs 기존 시총Top3 성과 비교표]")
    print("=" * 140)
    print(df_26y.to_string(index=False))
    
    # 최근 10년 클린 구간 (2016~2024)
    rows_10y = []
    q_10y = qqq_bm_eq.loc["2016-01-01":"2024-12-31"]
    for name, s_eq in comparison.items():
        s_10y = s_eq.loc["2016-01-01":"2024-12-31"]
        m10 = compute_metrics(s_10y, q_10y)
        rows_10y.append({
            "전략 명칭": name,
            "10년 총수익률(%)": round(m10["Total Return"] * 100, 1),
            "10년 CAGR(%)": round(m10["CAGR"] * 100, 2),
            "10년 변동성(%)": round(m10["Volatility"] * 100, 2),
            "10년 샤프": round(m10["Sharpe"], 2),
            "10년 MDD(%)": round(m10["MDD"] * 100, 2)
        })
    df_10y = pd.DataFrame(rows_10y)
    
    print("\n" + "=" * 140)
    print("💎 [최근 10년 클린 구간 (2016-01-01 ~ 2024-12-31) 성과 비교표]")
    print("=" * 140)
    print(df_10y.to_string(index=False))
    
    # 리밸런싱 이벤트 로그 출력
    df_logs = pd.DataFrame(t_logs)
    print(f"\n📋 [시총 Top 3 종목 변경에 따른 리밸런싱 집행 총 횟수]: 26년간 총 {len(df_logs)}회 (연평균 약 {len(df_logs)/26:.1f}회)")
    print("\n[주요 리밸런싱 발생 시점 샘플 (최근 10회)]:")
    print(df_logs.tail(10).to_string(index=False))
    
    # CSV 저장
    csv_path = f"{REPORTS_DIR}/top3_slot_strategy_results.csv"
    try:
        df_26y.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ 비교 분석 결과 CSV 저장 완료: {csv_path}")
    except PermissionError:
        csv_path_f = f"{REPORTS_DIR}/top3_slot_strategy_results_fixed.csv"
        df_26y.to_csv(csv_path_f, index=False, encoding="utf-8-sig")
        print(f"\n✅ 비교 분석 결과 CSV 저장 완료: {csv_path_f}")
        
    log_csv = f"{REPORTS_DIR}/top3_slot_rebalance_events.csv"
    try:
        df_logs.to_csv(log_csv, index=False, encoding="utf-8-sig")
        print(f"✅ 리밸런싱 이벤트 원장 CSV 저장 완료: {log_csv}")
    except PermissionError:
        log_csv_f = f"{REPORTS_DIR}/top3_slot_rebalance_events_fixed.csv"
        df_logs.to_csv(log_csv_f, index=False, encoding="utf-8-sig")
        print(f"✅ 리밸런싱 이벤트 원장 CSV 저장 완료: {log_csv_f}")

if __name__ == "__main__":
    run_top3_slot_strategy_test()
