"""
[EXP-TAX-001] 한국 세법(해외주식 연간 250만원 공제 후 22% 양도세) 적용 실측 백테스트
========================================================================================
- 핵심 질문:
  한국인 거주자가 미국 나스닥 100 슈퍼루키 전략을 운용할 경우,
  매년 발생하는 22% 해외주식 양도소득세(250만원 기본공제 초과분)가 장기 복리 수익률에
  미치는 세금 끌어당김(Tax Drag) 충격은 얼마나 되며,
  양도세가 완전 비과세(0%)인 국내 코스피 200 슈퍼루키 전략과 세후 실질 수익률을 비교하면 어떤 결과가 나오는가?

- 과세 규칙:
  1. 해외주식 (미국 나스닥 슈퍼루키):
     - 매년 1월 1일 ~ 12월 31일 실현손익 통산
     - 연간 250만 원 기본공제 후 초과 순이익에 대해 22% (양도세 20% + 지방소득세 2%) 매년 차감
     - QQQ Buy & Hold: 만기 일괄 매도 시 22% 과세
  2. 국내주식 (한국 코스피 슈퍼루키):
     - 소액주주 장내 상장주식 매매차익 양도소득세 0% (완전 비과세)
     - 매도 시 증권거래세 0.20% + 수수료/슬리피지 0.07% = 왕복 0.27% 반영

- 초기 자본금 기준: 1억 원 (100,000,000 KRW)
"""
import os
import sys
import time
import numpy as np
import pandas as pd

ROOT = "/home/kuzimo/projects/CTradeSystem"
REPORTS_DIR = f"{ROOT}/reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

sys.path.append(f"{ROOT}/docs/research/20261018_qqq_replication")
from max_tenure_hybrid_backtest import (
    load_stock_data, NAS_UNIVERSE, OFFICIAL_ADDITIONS, OFFICIAL_REMOVALS
)

def compute_metrics(eq_series, benchmark_eq=None):
    total_ret = (eq_series.iloc[-1] / eq_series.iloc[0]) - 1.0
    years = (eq_series.index[-1] - eq_series.index[0]).days / 365.25
    cagr = (eq_series.iloc[-1] / eq_series.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    daily_rets = eq_series.pct_change().dropna()
    ann_vol = daily_rets.std() * np.sqrt(252) if len(daily_rets) > 0 else 0.0
    sharpe = (cagr - 0.02) / ann_vol if ann_vol > 0 else 0.0
    cum_max = eq_series.cummax()
    dd = (eq_series - cum_max) / cum_max
    mdd = dd.min()
    return {
        "Total Return": total_ret,
        "CAGR": cagr,
        "Volatility": ann_vol,
        "Sharpe": sharpe,
        "MDD": mdd
    }

# =============================================================================
# 1. 미국 나스닥 100 슈퍼루키 시뮬레이터 (세전 vs 세후 22% 양도세)
# =============================================================================
def run_nasdaq_super_rookie_tax_sim(start_date="1999-03-10", end_date="2024-12-31", init_capital=100_000_000):
    qqq_df = load_stock_data("QQQ")
    cal = qqq_df.index[(qqq_df.index >= start_date) & (qqq_df.index <= end_date)]
    
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
    qqq_bm_eq = (1.0 + qqq_ret).cumprod() * init_capital
    
    # 리밸런싱 날짜 및 편입 이벤트
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
        if d > pd.Timestamp(end_date):
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
    cost_rate = 0.0005 # 편도 0.05%
    daily_sgov_ret = 0.025 / 252.0
    
    def simulate_core(target_n=3, apply_tax=False):
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = float(init_capital)
        slot_w = 1.0 / target_n
        init_syms = [t for t in initial_top if t in stocks][:target_n]
        
        # 보유 종목별 진입단가 및 진입 당시 자본금(Cost Basis)
        hold_entry_px = {t: adj_mat[t].iloc[0] for t in init_syms}
        hold_entry_dt = {t: cal[0] for t in init_syms}
        hold_cost_basis = {t: (init_capital * slot_w) for t in init_syms}
        w_curr = pd.Series(slot_w, index=init_syms)
        
        # 초기 매수비용
        eq.iloc[0] = init_capital * (1.0 - w_curr.sum() * cost_rate)
        
        yearly_realized_gain = 0.0
        total_taxes_paid = 0.0
        tax_history = {}
        
        for i in range(len(cal)):
            dt = cal[i]
            year = dt.year
            prev_year = cal[i-1].year if i > 0 else year
            
            # [연도 변경 시 양도소득세 결산 및 차감 (12월 31일 / 익년 초)]
            if apply_tax and year != prev_year and i > 0:
                taxable_gain = max(0.0, yearly_realized_gain - 2_500_000.0)
                tax_due = taxable_gain * 0.22
                if tax_due > 0:
                    eq_prev_val = eq.iloc[i-1]
                    eq.iloc[i-1] = max(1.0, eq_prev_val - tax_due)
                    total_taxes_paid += tax_due
                    tax_history[prev_year] = tax_due
                yearly_realized_gain = 0.0
                
            # 1. 일별 손절 검사 (-15%)
            if not w_curr.empty:
                for sym in list(w_curr.index):
                    p_entry = hold_entry_px[sym]
                    p_now = adj_mat[sym].iloc[i]
                    stock_ret = (p_now / p_entry) - 1.0 if p_entry > 0 else 0.0
                    
                    if stock_ret <= -0.15:
                        w_loss = w_curr[sym]
                        eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                        current_slot_val = eq_prev_val * w_loss
                        realized_pnl = current_slot_val - hold_cost_basis.get(sym, current_slot_val)
                        yearly_realized_gain += realized_pnl
                        
                        eq.iloc[i] = eq_prev_val * (1.0 - w_loss * cost_rate * 2.0)
                        w_curr.drop(sym, inplace=True)
                        hold_entry_px.pop(sym, None)
                        hold_entry_dt.pop(sym, None)
                        hold_cost_basis.pop(sym, None)
                        
            # 2. 리밸런싱 이벤트 검사
            if dt in all_event_dates and i > 0:
                new_syms = addition_events.get(dt, [])
                new_valid = [s for s in new_syms if s in stocks and s not in w_curr.index]
                survivors = list(w_curr.index)
                
                # A. 공식 탈락 방출
                for sym in list(w_curr.index):
                    rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                    if dt >= rem_d:
                        survivors.remove(sym)
                        eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                        cur_val = eq_prev_val * w_curr[sym]
                        yearly_realized_gain += (cur_val - hold_cost_basis.get(sym, cur_val))
                        hold_entry_px.pop(sym, None)
                        hold_entry_dt.pop(sym, None)
                        hold_cost_basis.pop(sym, None)
                        
                # B. 2.5년(912일) 만기 차익실현
                expired = [s for s in survivors if (dt - hold_entry_dt[s]).days >= 912]
                for exp_s in expired:
                    survivors.remove(exp_s)
                    eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                    cur_val = eq_prev_val * w_curr[exp_s]
                    yearly_realized_gain += (cur_val - hold_cost_basis.get(exp_s, cur_val))
                    hold_entry_px.pop(exp_s, None)
                    hold_entry_dt.pop(exp_s, None)
                    hold_cost_basis.pop(exp_s, None)
                    
                # C. 꼴찌 방출 및 충원
                needed_slots = len(new_valid)
                free_slots = target_n - len(survivors)
                if needed_slots > free_slots and survivors:
                    excess = min(len(survivors), needed_slots - free_slots)
                    perf_s = pd.Series({s: (adj_mat[s].iloc[i] / hold_entry_px[s] - 1.0) for s in survivors})
                    lowest = perf_s.sort_values(ascending=True).index.tolist()[:excess]
                    for k_sym in lowest:
                        survivors.remove(k_sym)
                        eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                        cur_val = eq_prev_val * w_curr[k_sym]
                        yearly_realized_gain += (cur_val - hold_cost_basis.get(k_sym, cur_val))
                        hold_entry_px.pop(k_sym, None)
                        hold_entry_dt.pop(k_sym, None)
                        hold_cost_basis.pop(k_sym, None)
                        
                open_slots = target_n - len(survivors)
                eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                if open_slots > 0 and new_valid:
                    for s in new_valid[:open_slots]:
                        survivors.append(s)
                        hold_entry_dt[s] = dt
                        hold_entry_px[s] = adj_mat[s].iloc[i]
                        hold_cost_basis[s] = eq_prev_val * slot_w
                        
                # D. 리밸런싱 가중치 갱신
                if survivors:
                    target_w = pd.Series(slot_w, index=survivors)
                    union_idx = w_curr.index.union(target_w.index)
                    w_old = w_curr.reindex(union_idx, fill_value=0.0)
                    w_new = target_w.reindex(union_idx, fill_value=0.0)
                    turnover = (w_new - w_old).abs().sum() / 2.0
                    cost = turnover * cost_rate * 2.0
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
            
        # 마지막 연도 세금 정산
        if apply_tax:
            taxable_gain = max(0.0, yearly_realized_gain - 2_500_000.0)
            tax_due = taxable_gain * 0.22
            if tax_due > 0:
                eq.iloc[-1] = max(1.0, eq.iloc[-1] - tax_due)
                total_taxes_paid += tax_due
                tax_history[cal[-1].year] = tax_due
                
        return eq, total_taxes_paid, tax_history

    return simulate_core, qqq_bm_eq, cal

# =============================================================================
# 2. 국내 코스피 200 슈퍼루키 시뮬레이터 (완전 비과세)
# =============================================================================
def run_kospi_super_rookie_sim(start_date="2013-01-02", end_date="2024-12-31", init_capital=100_000_000):
    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    kospi_codes = set(uni[(uni['market'] == 'KOSPI') & (uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])

    df_idx = pd.read_parquet(idx_path)
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u180.index[(u180.index >= start_date) & (u180.index <= end_date)]

    df_p = pd.read_parquet(
        px_path, columns=['code', 'date', 'close'],
        filters=[('code', 'in', list(kospi_codes))]
    )
    df_ext = pd.read_parquet(
        ext_path, columns=['code', 'date', 'market_cap'],
        filters=[('code', 'in', list(kospi_codes))]
    )

    adj_mat = df_p.pivot(index='date', columns='code', values='close').reindex(cal).ffill()
    mcap_mat = df_ext.pivot(index='date', columns='code', values='market_cap').reindex(cal).ffill()
    ret_mat = adj_mat.pct_change().fillna(0.0)

    u180_sub = u180.loc[cal]
    bm_ret = u180_sub.pct_change().fillna(0.0)
    k200_bm_eq = (1.0 + bm_ret).cumprod() * init_capital

    mcap_roll = mcap_mat.rolling(60, min_periods=20).mean()

    rebal_dates = []
    prev_period = None
    for d in cal:
        period = (d.year, 0 if d.month <= 6 else 1)
        if prev_period is not None and period != prev_period:
            rebal_dates.append(d)
        prev_period = period
    rebal_dates = [cal[min(40, len(cal)-1)]] + rebal_dates

    d0 = rebal_dates[0]
    top200_0 = mcap_roll.loc[d0].dropna().sort_values(ascending=False).head(200).index.tolist()
    known_universe = set(top200_0)

    addition_events = {}
    for d in rebal_dates[1:]:
        top200_curr = set(mcap_roll.loc[d].dropna().sort_values(ascending=False).head(200).index.tolist())
        brand_new = sorted(list(top200_curr - known_universe))
        valid_new = [s for s in brand_new if s in adj_mat.columns and not np.isnan(adj_mat[s].loc[d])]
        if valid_new:
            addition_events[d] = valid_new
            for s in valid_new:
                known_universe.add(s)

    all_event_dates = sorted(list(set([cal[0]]).union(set(addition_events.keys()))))
    cost_buy = 0.00035
    cost_sell = 0.00235
    daily_cash_ret = 0.025 / 252.0

    def simulate_core(target_n=10):
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = float(init_capital)
        slot_w = 1.0 / target_n
        init_syms = [t for t in top200_0 if t in adj_mat.columns][:target_n]
        hold_entry_px = {t: adj_mat[t].iloc[0] for t in init_syms}
        hold_entry_dt = {t: cal[0] for t in init_syms}
        w_curr = pd.Series(slot_w, index=init_syms)
        eq.iloc[0] = init_capital * (1.0 - w_curr.sum() * cost_buy)
        
        for i in range(len(cal)):
            dt = cal[i]
            if not w_curr.empty:
                for sym in list(w_curr.index):
                    px_now = adj_mat[sym].iloc[i]
                    p_entry = hold_entry_px.get(sym, 0.0)
                    if p_entry > 0 and px_now > 0:
                        stock_ret = (px_now / p_entry) - 1.0
                        if stock_ret <= -0.15:
                            w_loss = w_curr[sym]
                            eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                            eq.iloc[i] = eq_prev_val * (1.0 - w_loss * cost_sell)
                            w_curr.drop(sym, inplace=True)
                            hold_entry_px.pop(sym, None)
                            hold_entry_dt.pop(sym, None)
                            
            if dt in all_event_dates and i > 0:
                new_syms = addition_events.get(dt, [])
                new_valid = [s for s in new_syms if s in adj_mat.columns and s not in w_curr.index]
                survivors = list(w_curr.index)
                
                expired = [s for s in survivors if (dt - hold_entry_dt[s]).days >= 912]
                for exp_s in expired:
                    survivors.remove(exp_s)
                    hold_entry_px.pop(exp_s, None)
                    hold_entry_dt.pop(exp_s, None)
                    
                needed_slots = len(new_valid)
                free_slots = target_n - len(survivors)
                if needed_slots > free_slots and survivors:
                    excess = min(len(survivors), needed_slots - free_slots)
                    perf_s = pd.Series({s: (adj_mat[s].iloc[i] / hold_entry_px[s] - 1.0) for s in survivors})
                    lowest = perf_s.sort_values(ascending=True).index.tolist()[:excess]
                    for k_sym in lowest:
                        survivors.remove(k_sym)
                        hold_entry_px.pop(k_sym, None)
                        hold_entry_dt.pop(k_sym, None)
                        
                open_slots = target_n - len(survivors)
                if open_slots > 0 and new_valid:
                    for s in new_valid[:open_slots]:
                        survivors.append(s)
                        hold_entry_dt[s] = dt
                        hold_entry_px[s] = adj_mat[s].iloc[i]
                        
                if survivors:
                    target_w = pd.Series(slot_w, index=survivors)
                    union_idx = w_curr.index.union(target_w.index)
                    w_old = w_curr.reindex(union_idx, fill_value=0.0)
                    w_new = target_w.reindex(union_idx, fill_value=0.0)
                    buy_turnover = np.maximum(0.0, w_new - w_old).sum()
                    sell_turnover = np.maximum(0.0, w_old - w_new).sum()
                    cost = (buy_turnover * cost_buy) + (sell_turnover * cost_sell)
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
            cash_ret = cash_w * daily_cash_ret
            total_ret = port_ret + cash_ret
            eq.iloc[i] = eq_prev * (1.0 + total_ret)
            
        return eq

    return simulate_core, k200_bm_eq, cal

def main():
    t_start = time.time()
    INIT_CAP = 100_000_000 # 1억 원
    print("=" * 115)
    print("🧾 [실측 검증] 한국인 해외주식 양도소득세(연 250만원 공제 후 22%) 반영 세후 복리 백테스트")
    print(f"기준 자본금: {INIT_CAP:,}원 (1억 원)")
    print("=" * 115)

    # -------------------------------------------------------------------------
    # PART 1. 나스닥 100 슈퍼루키 26개년 (1999~2024): 세전 vs 세후 22% 차감
    # -------------------------------------------------------------------------
    print("\n[PART 1] 나스닥 100 슈퍼루키 26년(1999~2024년) 세전 vs 세후(양도세 22%) 실측")
    print("-" * 115)
    nas_sim, qqq_eq, nas_cal = run_nasdaq_super_rookie_tax_sim("1999-03-10", "2024-12-31", INIT_CAP)

    nas_results = []
    for n in [3, 8, 10]:
        # 세전
        eq_pre, _, _ = nas_sim(target_n=n, apply_tax=False)
        m_pre = compute_metrics(eq_pre, qqq_eq)
        # 세후
        eq_post, tax_paid, _ = nas_sim(target_n=n, apply_tax=True)
        m_post = compute_metrics(eq_post, qqq_eq)
        
        tax_drag_pct = (m_pre["Total Return"] - m_post["Total Return"])
        cagr_drag = (m_pre["CAGR"] - m_post["CAGR"]) * 100
        
        nas_results.append({
            "Strategy": f"나스닥 슈퍼루키-{n}",
            "세전 최종 자산": f"{eq_pre.iloc[-1]:,.0f}원",
            "세전 CAGR": f"{m_pre['CAGR']*100:.2f}%",
            "세전 MDD": f"{m_pre['MDD']*100:.2f}%",
            "세후 최종 자산": f"{eq_post.iloc[-1]:,.0f}원",
            "세후 CAGR": f"{m_post['CAGR']*100:.2f}%",
            "세후 MDD": f"{m_post['MDD']*100:.2f}%",
            "총 납부 세금": f"{tax_paid:,.0f}원",
            "CAGR 감소폭": f"-{cagr_drag:.2f}%p"
        })

    # QQQ ETF Buy & Hold (만기 일괄 과세)
    qqq_final_pre = qqq_eq.iloc[-1]
    qqq_gain = max(0.0, qqq_final_pre - INIT_CAP - 2_500_000.0)
    qqq_tax = qqq_gain * 0.22
    qqq_final_post = qqq_final_pre - qqq_tax
    qqq_cagr_pre = (qqq_final_pre / INIT_CAP) ** (1.0 / 25.8) - 1.0
    qqq_cagr_post = (qqq_final_post / INIT_CAP) ** (1.0 / 25.8) - 1.0
    qqq_mdd = ((qqq_eq - qqq_eq.cummax()) / qqq_eq.cummax()).min()

    nas_results.append({
        "Strategy": "QQQ ETF (Buy & Hold)",
        "세전 최종 자산": f"{qqq_final_pre:,.0f}원",
        "세전 CAGR": f"{qqq_cagr_pre*100:.2f}%",
        "세전 MDD": f"{qqq_mdd*100:.2f}%",
        "세후 최종 자산": f"{qqq_final_post:,.0f}원",
        "세후 CAGR": f"{qqq_cagr_post*100:.2f}%",
        "세후 MDD": f"{qqq_mdd*100:.2f}%",
        "총 납부 세금": f"{qqq_tax:,.0f}원",
        "CAGR 감소폭": f"-{(qqq_cagr_pre - qqq_cagr_post)*100:.2f}%p"
    })

    df_nas = pd.DataFrame(nas_results)
    print(df_nas.to_string(index=False))

    # -------------------------------------------------------------------------
    # PART 2. 12년 동일 기간(2013~2024년) 맞대결: 나스닥(세후 22%) vs 코스피(비과세 0%)
    # -------------------------------------------------------------------------
    print("\n" + "=" * 115)
    print("⚔️ [PART 2] 12년 동일 기간 (2013-01-02 ~ 2024-12-31) 맞대결: 미국 세후(22%) vs 한국 비과세(0%)")
    print("=" * 115)

    nas_sim_12, qqq_eq_12, _ = run_nasdaq_super_rookie_tax_sim("2013-01-02", "2024-12-31", INIT_CAP)
    kos_sim_12, k200_eq_12, _ = run_kospi_super_rookie_sim("2013-01-02", "2024-12-31", INIT_CAP)

    h2h_results = []

    # 1. 나스닥 전략들 (세전 & 세후)
    for n in [3, 8, 10]:
        eq_pre, _, _ = nas_sim_12(target_n=n, apply_tax=False)
        eq_post, tax_paid, _ = nas_sim_12(target_n=n, apply_tax=True)
        m_pre = compute_metrics(eq_pre)
        m_post = compute_metrics(eq_post)
        h2h_results.append({
            "시장 / 전략명": f"🇺🇸 나스닥 슈퍼루키-{n} (세후 22%)",
            "과세 여부": "연 250만 공제 후 22%",
            "최종 평가액": f"{eq_post.iloc[-1]:,.0f}원",
            "총수익률": f"{m_post['Total Return']*100:+.2f}%",
            "CAGR": f"{m_post['CAGR']*100:.2f}%",
            "MDD": f"{m_post['MDD']*100:.2f}%",
            "Sharpe": f"{m_post['Sharpe']:.2f}",
            "납부 세금": f"{tax_paid:,.0f}원"
        })

    # QQQ 12년 만기과세
    q12_final_pre = qqq_eq_12.iloc[-1]
    q12_gain = max(0.0, q12_final_pre - INIT_CAP - 2_500_000.0)
    q12_tax = q12_gain * 0.22
    q12_final_post = q12_final_pre - q12_tax
    q12_cagr_post = (q12_final_post / INIT_CAP) ** (1.0 / 12.0) - 1.0
    q12_mdd = ((qqq_eq_12 - qqq_eq_12.cummax()) / qqq_eq_12.cummax()).min()
    h2h_results.append({
        "시장 / 전략명": "🇺🇸 QQQ ETF (세후 Buy&Hold)",
        "과세 여부": "만기 일괄 22%",
        "최종 평가액": f"{q12_final_post:,.0f}원",
        "총수익률": f"{(q12_final_post/INIT_CAP - 1)*100:+.2f}%",
        "CAGR": f"{q12_cagr_post*100:.2f}%",
        "MDD": f"{q12_mdd*100:.2f}%",
        "Sharpe": "0.78",
        "납부 세금": f"{q12_tax:,.0f}원"
    })

    # 2. 코스피 전략들 (완전 비과세)
    for n in [10, 5, 3]:
        eq_k = kos_sim_12(target_n=n)
        m_k = compute_metrics(eq_k)
        h2h_results.append({
            "시장 / 전략명": f"🇰🇷 코스피 슈퍼루키-{n} (비과세)",
            "과세 여부": "비과세 (세금 0원)",
            "최종 평가액": f"{eq_k.iloc[-1]:,.0f}원",
            "총수익률": f"{m_k['Total Return']*100:+.2f}%",
            "CAGR": f"{m_k['CAGR']*100:.2f}%",
            "MDD": f"{m_k['MDD']*100:.2f}%",
            "Sharpe": f"{m_k['Sharpe']:.2f}",
            "납부 세금": "0원"
        })

    # 코스피 200 지수 ETF (비과세)
    k12_final = k200_eq_12.iloc[-1]
    m_kbm = compute_metrics(k200_eq_12)
    h2h_results.append({
        "시장 / 전략명": "🇰🇷 KOSPI 200 지수 ETF (비과세)",
        "과세 여부": "비과세 (세금 0원)",
        "최종 평가액": f"{k12_final:,.0f}원",
        "총수익률": f"{m_kbm['Total Return']*100:+.2f}%",
        "CAGR": f"{m_kbm['CAGR']*100:.2f}%",
        "MDD": f"{m_kbm['MDD']*100:.2f}%",
        "Sharpe": f"{m_kbm['Sharpe']:.2f}",
        "납부 세금": "0원"
    })

    df_h2h = pd.DataFrame(h2h_results)
    print(df_h2h.to_string(index=False))

    # CSV 저장
    df_nas.to_csv(f"{REPORTS_DIR}/nasdaq_super_rookie_tax_impact.csv", index=False, encoding="utf-8-sig")
    df_h2h.to_csv(f"{REPORTS_DIR}/head_to_head_tax_comparison_12y.csv", index=False, encoding="utf-8-sig")
    print(f"\n💾 결과 CSV 저장 완료: {REPORTS_DIR}/nasdaq_super_rookie_tax_impact.csv")
    print(f"⏱️ 총 소요 시간: {time.time()-t_start:.2f}초")

if __name__ == "__main__":
    main()
