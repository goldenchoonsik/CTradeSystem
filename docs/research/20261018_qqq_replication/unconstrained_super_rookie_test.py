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

def run_unconstrained_super_rookie_test():
    print("=" * 115)
    print("🚀 [실측 백테스트] 슈퍼루키 전략 종목수 제한 없는(Unconstrained) 전수 매매 검증 (1999~2024, 26년)")
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
    
    # 1. 신규 편입 이벤트 매핑 (1999~2015 거래대금 50위 밖->진입 + 2016~2024 공식 편입 56개 전수)
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
    cost_rate = 0.0005 # 편도 0.05% (왕복 0.10%)
    daily_sgov_ret = 0.025 / 252.0 # 단기 국채 현금 이자 (연 2.5%)
    
    # =========================================================================
    # 시뮬레이터 함수: 종목수 무제한 vs 특정 슬롯 N
    # =========================================================================
    def simulate_super_rookie(target_n=None, stop_loss_pct=0.15):
        """
        target_n: int or None. None이면 '종목수 무제한(Unconstrained)'
        stop_loss_pct: float or None. 0.15이면 -15% 손절 후 현금 공백 유지
        """
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        w_curr = pd.Series(dtype=float)
        hold_entry_px = {}
        hold_entry_dt = {}
        
        # 시작 시점 포트폴리오
        if target_n is not None:
            init_syms = [t for t in initial_top if t in stocks][:target_n]
            slot_w = 1.0 / target_n
        else:
            # 무제한의 경우: 초기 거래대금 상위 10종목으로 시작 (비교 공정성)
            init_syms = [t for t in initial_top if t in stocks][:10]
            slot_w = 1.0 / len(init_syms) if init_syms else 0.0
            
        for t in init_syms:
            hold_entry_dt[t] = cal[0]
            hold_entry_px[t] = adj_mat[t].iloc[0]
        w_curr = pd.Series(slot_w, index=init_syms)
        
        stop_count = 0
        port_sizes = []
        cash_history = []
        
        for i in range(len(cal)):
            dt = cal[i]
            
            # 1. 일별 손절 검사 (진입가 대비 -15%)
            if stop_loss_pct is not None and not w_curr.empty:
                for sym in list(w_curr.index):
                    px_now = adj_mat[sym].iloc[i]
                    p_entry = hold_entry_px[sym]
                    stock_ret = (px_now / p_entry) - 1.0 if p_entry > 0 else 0.0
                    
                    if stock_ret <= -stop_loss_pct:
                        # 손절 집행: 슬롯 공백 유지 (해당 비중만큼 현금으로 전환)
                        w_loss = w_curr[sym]
                        eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                        eq.iloc[i] = eq_prev_val * (1.0 - w_loss * 0.0007) # 슬리피지/수수료 0.07%
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
                        
                # B. 2.5년 만기(912일) 방출
                expired = [s for s in survivors if (dt - hold_entry_dt[s]).days >= 912]
                for exp_s in expired:
                    survivors.remove(exp_s)
                    hold_entry_px.pop(exp_s, None)
                    hold_entry_dt.pop(exp_s, None)
                    
                # C. 신규 종목 충원
                if target_n is not None:
                    # 슬롯 수 제한이 있는 경우: 부족한 자리만큼 꼴찌 방출 후 충원
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
                    
                    open_slots = target_n - len(survivors)
                    if open_slots > 0 and new_valid:
                        for s in new_valid[:open_slots]:
                            survivors.append(s)
                            hold_entry_dt[s] = dt
                            hold_entry_px[s] = adj_mat[s].iloc[i]
                    target_slot_w = 1.0 / target_n
                else:
                    # ⭐ [종목수 제한 없음 (Unconstrained)] ⭐
                    # 새로 들어오는 모든 종목을 슬롯 한계 없이 100% 전부 편입!
                    if new_valid:
                        for s in new_valid:
                            survivors.append(s)
                            hold_entry_dt[s] = dt
                            hold_entry_px[s] = adj_mat[s].iloc[i]
                    # 살아남은 모든 종목에 1/M 균등 배분
                    target_slot_w = 1.0 / len(survivors) if survivors else 0.0
                    
                # D. 리밸런싱 가중치 갱신
                if survivors:
                    target_w = pd.Series(target_slot_w, index=survivors)
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
                    
            port_sizes.append(len(w_curr))
            
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
            cash_history.append(cash_w)
            cash_ret = cash_w * daily_sgov_ret
            total_ret = port_ret + cash_ret
            eq.iloc[i] = eq_prev * (1.0 + total_ret)
            
            tot_w = w_curr.sum() + cash_w
            if tot_w > 0:
                w_curr = w_curr / tot_w
                
        stats = {
            "stop_count": stop_count,
            "avg_size": np.mean(port_sizes),
            "max_size": max(port_sizes) if port_sizes else 0,
            "min_size": min(port_sizes) if port_sizes else 0,
            "avg_cash": np.mean(cash_history) if cash_history else 0.0
        }
        return eq, stats

    # =========================================================================
    # 전략 대조군 구성 및 시뮬레이션 실행
    # =========================================================================
    strategies = {
        "1. 슈퍼루키_Unconstrained (종목수 무제한 + -15% 손절)": (None, 0.15),
        "2. 슈퍼루키_Unconstrained_NoStop (종목수 무제한, 손절 없음)": (None, None),
        "3. 슈퍼루키-3 (3종목 집중 챔피언)": (3, 0.15),
        "4. 슈퍼루키-5 (5종목 균형)": (5, 0.15),
        "5. 슈퍼루키-10 (10종목 분산 기준)": (10, 0.15),
        "6. 슈퍼루키-20 (20종목 다변화)": (20, 0.15),
    }
    
    results = {}
    stats_map = {}
    
    print("\n⏳ 각 전략별 시뮬레이션 연산 실행 중...")
    for name, (n_val, sl_val) in strategies.items():
        eq_res, st = simulate_super_rookie(target_n=n_val, stop_loss_pct=sl_val)
        results[name] = eq_res
        stats_map[name] = st
        
    results["7. QQQ_Actual_ETF (기준선)"] = qqq_bm_eq
    stats_map["7. QQQ_Actual_ETF (기준선)"] = {
        "stop_count": 0, "avg_size": 100.0, "max_size": 100, "min_size": 100, "avg_cash": 0.0
    }
    
    # =========================================================================
    # 결과 집계 및 성과 분석
    # =========================================================================
    summary_rows = []
    for name, s_eq in results.items():
        m = compute_metrics(s_eq, qqq_bm_eq)
        st = stats_map[name]
        
        # 닷컴 및 금융위기 MDD
        sub_dotcom = s_eq.loc["2000-01-01":"2002-12-31"]
        mdd_dotcom = ((sub_dotcom - sub_dotcom.cummax()) / sub_dotcom.cummax()).min()
        
        sub_gfc = s_eq.loc["2007-10-01":"2009-03-31"]
        mdd_gfc = ((sub_gfc - sub_gfc.cummax()) / sub_gfc.cummax()).min()
        
        # 2022년 금리인상기 MDD
        sub_2022 = s_eq.loc["2022-01-01":"2022-12-31"]
        mdd_2022 = ((sub_2022 - sub_2022.cummax()) / sub_2022.cummax()).min()
        
        summary_rows.append({
            "전략 명칭": name,
            "평균보유종목": round(st["avg_size"], 1),
            "최대보유": st["max_size"],
            "총수익률(%)": round(m["Total Return"] * 100, 1),
            "CAGR(%)": round(m["CAGR"] * 100, 2),
            "연변동성(%)": round(m["Volatility"] * 100, 2),
            "샤프지수": round(m["Sharpe"], 2),
            "전체MDD(%)": round(m["MDD"] * 100, 2),
            "닷컴MDD(%)": round(mdd_dotcom * 100, 2),
            "금융위기MDD(%)": round(mdd_gfc * 100, 2),
            "2022MDD(%)": round(mdd_2022 * 100, 2),
            "베타": round(m["Beta"], 2) if not np.isnan(m["Beta"]) else 1.0,
            "손절횟수": st["stop_count"]
        })
        
    df_summary = pd.DataFrame(summary_rows)
    
    print("\n" + "=" * 135)
    print("🏆 [종목수 제한 없는(Unconstrained) 슈퍼루키 vs 슬롯 제한형 성과 비교표 (1999~2024년 26년)]")
    print("=" * 135)
    print(df_summary.to_string(index=False))
    
    csv_path = f"{REPORTS_DIR}/super_rookie_unconstrained_comparison.csv"
    try:
        df_summary.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ 비교 분석 결과 CSV 저장 완료: {csv_path}")
    except PermissionError:
        csv_path_fixed = f"{REPORTS_DIR}/super_rookie_unconstrained_comparison_fixed.csv"
        df_summary.to_csv(csv_path_fixed, index=False, encoding="utf-8-sig")
        print(f"\n⚠️ 기존 파일이 열려 있어 새 파일로 저장 완료: {csv_path_fixed}")

    # 최근 10년 클린 구간 (2016~2024) 분석
    print("\n" + "=" * 135)
    print("💎 [최근 10년 클린 구간 성과 (2016-01-01 ~ 2024-12-31)]")
    print("=" * 135)
    rows_10y = []
    for name, s_eq in results.items():
        s_10y = s_eq.loc["2016-01-01":"2024-12-31"]
        q_10y = qqq_bm_eq.loc["2016-01-01":"2024-12-31"]
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
    print(df_10y.to_string(index=False))

if __name__ == "__main__":
    run_unconstrained_super_rookie_test()
