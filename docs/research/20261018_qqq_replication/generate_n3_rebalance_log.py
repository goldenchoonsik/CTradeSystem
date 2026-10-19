import sys
import os
import pandas as pd
import numpy as np

sys.path.append('docs/research/20261018_qqq_replication')
from max_tenure_hybrid_backtest import load_stock_data, compute_metrics, NAS_UNIVERSE, OFFICIAL_ADDITIONS, OFFICIAL_REMOVALS

ROOT = "DataVault"
REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

def generate_n3_rebalance_log():
    print("=" * 115)
    print("🔍 [N=3 상세 추적 (버그 수정판)] 리밸런싱 이벤트 기준 성과/편입/운용/교체 내역 시뮬레이션 (1999-03-10 ~ 2024-12-31)")
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
    stop_loss_pct = 0.15
    target_n = 3
    slot_w = 1.0 / target_n
    
    eq = pd.Series(index=cal, dtype=float)
    eq.iloc[0] = 1.0
    hold_entry_px = {}
    hold_entry_dt = {}
    
    initial_n = [t for t in initial_top if t in stocks][:target_n]
    for t in initial_n:
        hold_entry_dt[t] = cal[0]
        hold_entry_px[t] = adj_mat[t].iloc[0]
    w_curr = pd.Series(slot_w, index=initial_n)
    
    rebal_records = []
    stopped_between = [] # 최근 리밸런싱 이후 손절된 내역 추적
    prev_event_eq = 1.0
    
    # 0번째 이벤트 기록 (최초 포트 구성)
    rebal_records.append({
        "리밸런싱일자": cal[0].strftime("%Y-%m-%d"),
        "이벤트구분": "최초 포트폴리오 개설",
        "신규 진입 후보": ", ".join(initial_top[:5]),
        "교체/탈락 종목": "-",
        "신규 편입 종목": ", ".join(initial_n),
        "최종 운용 종목(3개 슬롯)": ", ".join(initial_n),
        "현금 비중(%)": f"{(1.0 - w_curr.sum())*100:.1f}%",
        "구간 수익률(%)": "0.0%",
        "누적 수익률(%)": "0.0%",
        "누적 자산(기준=1.0)": 1.0,
        "손절 발생 내역": "-"
    })
    
    total_stops = 0
    total_prunes = 0
    
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
                    stopped_between.append(f"{sym}({dt.strftime('%m/%d')} -15%손절)")
                    total_stops += 1
                    
        # 2. 리밸런싱 이벤트 도래 시
        if dt in all_event_dates and i > 0:
            new_syms = addition_events.get(dt, [])
            new_valid = [s for s in new_syms if s in stocks and s not in w_curr.index]
            
            # 구간 성과 측정 (직전 리밸런싱일 ~ 현재 리밸런싱일 직전)
            curr_raw_eq = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
            period_ret = (curr_raw_eq / prev_event_eq - 1.0) * 100.0
            cum_ret = (curr_raw_eq - 1.0) * 100.0
            
            removed_list = []
            added_list = []
            
            # A. 1순위: 공식 탈락 방출
            survivors = []
            for sym in list(w_curr.index):
                rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                if dt < rem_d:
                    survivors.append(sym)
                else:
                    removed_list.append(f"{sym}(나스닥100공식탈락)")
                    hold_entry_px.pop(sym, None)
                    hold_entry_dt.pop(sym, None)
                    
            # B. 2순위: 2.5년 만기 방출
            expired = [s for s in survivors if (dt - hold_entry_dt[s]).days >= 912]
            for exp_s in expired:
                survivors.remove(exp_s)
                removed_list.append(f"{exp_s}(2.5년만기)")
                hold_entry_px.pop(exp_s, None)
                hold_entry_dt.pop(exp_s, None)
                
            # C. 3순위: 신규 종목 충원을 위한 부족 슬롯 계산 및 최하위 꼴찌 방출 (수정된 정상 로직!)
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
                    removed_list.append(f"{k_sym}(성과최하위교체 {perf_series[k_sym]*100:+.1f}%)")
                    hold_entry_px.pop(k_sym, None)
                    hold_entry_dt.pop(k_sym, None)
                    total_prunes += 1
                    
            # D. 신규 종목 충원
            open_slots = target_n - len(survivors)
            if open_slots > 0 and new_valid:
                to_add = new_valid[:open_slots]
                for s in to_add:
                    survivors.append(s)
                    added_list.append(s)
                    hold_entry_dt[s] = dt
                    hold_entry_px[s] = adj_mat[s].iloc[i]
                    
            # E. 리밸런싱 집행 (종목당 1/3 균등 배분)
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
                
            # 기록 저장
            rebal_records.append({
                "리밸런싱일자": dt.strftime("%Y-%m-%d"),
                "이벤트구분": "정기/수시 리밸런싱",
                "신규 진입 후보": ", ".join(new_syms) if new_syms else "(신규없음)",
                "교체/탈락 종목": ", ".join(removed_list) if removed_list else "(없음)",
                "신규 편입 종목": ", ".join(added_list) if added_list else "(없음)",
                "최종 운용 종목(3개 슬롯)": ", ".join(survivors) if survivors else "(전액현금 100%)",
                "현금 비중(%)": f"{(1.0 - w_curr.sum())*100:.1f}%",
                "구간 수익률(%)": f"{period_ret:+.2f}%",
                "누적 수익률(%)": f"{cum_ret:+.1f}%",
                "누적 자산(기준=1.0)": round(curr_raw_eq, 2),
                "손절 발생 내역": ", ".join(stopped_between) if stopped_between else "(없음)"
            })
            
            prev_event_eq = curr_raw_eq
            stopped_between = []
            
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
            
    df_rebal = pd.DataFrame(rebal_records)
    csv_path = f"{REPORTS_DIR}/n3_rebalance_event_log_1999_2024.csv"
    try:
        df_rebal.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ 수정 완료된 N=3 리밸런싱 이벤트 상세 로그 저장 완료: {csv_path}")
    except PermissionError:
        csv_path_fixed = f"{REPORTS_DIR}/n3_rebalance_event_log_1999_2024_fixed.csv"
        df_rebal.to_csv(csv_path_fixed, index=False, encoding="utf-8-sig")
        print(f"\n⚠️ 기존 파일이 열려 있어 새 파일로 저장 완료: {csv_path_fixed}")
    print(f"총 리밸런싱 이벤트 수: {len(df_rebal)}회 | 총 손절 횟수: {total_stops}회 | 총 꼴찌교체 횟수: {total_prunes}회")
    
    m = compute_metrics(eq, qqq_bm_eq)
    print("\n" + "=" * 115)
    print(f"🏆 [수정된 슈퍼루키-3 최종 26년 성과]")
    print(f"총수익률: {m['Total Return']*100:+.1f}% | CAGR: {m['CAGR']*100:.2f}% | 변동성: {m['Volatility']*100:.2f}% | 샤프: {m['Sharpe']:.2f} | MDD: {m['MDD']*100:.2f}%")
    print("=" * 115)

if __name__ == '__main__':
    generate_n3_rebalance_log()
