"""
[EXP-018] 나스닥 100 주가 가속도(Price Acceleration) TOP 10 전략 vs 시총 가속도 실측 백테스트
=============================================================================================
- 검증 기간: 1999-03-10 ~ 2024-12-31 (26년 전 기간) 및 최근 10년 (2015~2024)
- 비교군:
  1. 주가 가속도 TOP 10 (풀투자)
  2. 주가 가속도 TOP 10 (슬롯 비우기, -15% 손절 시 현금대기)
  3. 시총 가속도 TOP 10 (풀투자) - [EXP-017] 챔피언
  4. QQQ ETF (벤치마크)
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

sys.path.append(r"docs/research/20261018_qqq_replication")
from max_tenure_hybrid_backtest import (
    load_stock_data, compute_metrics, NAS_UNIVERSE
)

ROOT = "DataVault"
REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

def run_price_vs_cap_acceleration_test():
    print("=" * 115)
    print("🚀 [실측 백테스트] 주가 가속도(Price Acceleration) TOP 10 vs 시총 가속도 비교 분석")
    print("기간: 1999-03-10 ~ 2024-12-31 (26개년, 6,497 거래일)")
    print("정의: 가속도(a) = 최근 1개월 수익률(v_t) - 직전 1개월 수익률(v_{t-1})")
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
    qqq_eq = (1.0 + qqq_ret).cumprod()
    
    # 1. 월초 리밸런싱 날짜 도출
    monthly_dates = []
    prev_m = None
    for d in cal:
        m = (d.year, d.month)
        if prev_m is not None and m != prev_m:
            monthly_dates.append(d)
        prev_m = m
    monthly_dates = [cal[0]] + monthly_dates
    monthly_set = set(monthly_dates)
    
    cost_rate = 0.0005 # 편도 0.05%
    daily_sgov_ret = 0.025 / 252.0 # 연 2.5% 무위험 이자
    stop_loss_pct = 0.15
    target_n = 10
    
    # 2. 지표 행렬 계산
    # A) 주가 가속도: (P_t / P_{t-21} - 1) - (P_{t-21} / P_{t-42} - 1)
    px_shift21 = adj_mat.shift(21)
    px_shift42 = adj_mat.shift(42)
    px_v1 = (adj_mat / px_shift21) - 1.0
    px_v0 = (px_shift21 / px_shift42) - 1.0
    px_accel = px_v1 - px_v0
    
    # B) 시총(거래대금 프록시) 가속도
    dvol_shift21 = dvol_mat.shift(21)
    dvol_shift42 = dvol_mat.shift(42)
    dvol_v1 = (dvol_mat / dvol_shift21) - 1.0
    dvol_v0 = (dvol_shift21 / dvol_shift42) - 1.0
    cap_accel = dvol_v1 - dvol_v0
    
    # 시뮬레이션 엔진
    def simulate(metric_mat, enable_stop_loss=False, require_liquidity=True):
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        slots = [None] * target_n
        stop_loss_count = 0
        monthly_cash_history = []
        
        for i in range(1, len(cal)):
            d = cal[i]
            prev_d = cal[i-1]
            prev_eq = eq.iloc[i-1]
            
            # 월초 리밸런싱
            if d in monthly_set:
                scores = metric_mat.loc[prev_d].dropna()
                if require_liquidity:
                    dvol_row = dvol_mat.loc[prev_d].dropna()
                    if len(dvol_row) > 10:
                        med_dvol = dvol_row.median()
                        scores = scores[scores.index.isin(dvol_row[dvol_row >= med_dvol].index)]
                        
                top_tickers = scores.sort_values(ascending=False).head(target_n).index.tolist()
                old_tickers = [s["ticker"] for s in slots if s is not None and not s["is_cash"]]
                new_slots = []
                for t in top_tickers:
                    p_entry = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan
                    new_slots.append({
                        "ticker": t,
                        "entry_price": p_entry,
                        "is_cash": False
                    })
                # 교체 턴오버 비용
                turnover = 0
                for idx in range(target_n):
                    old_t = old_tickers[idx] if idx < len(old_tickers) else None
                    new_t = new_slots[idx]["ticker"] if idx < len(new_slots) else None
                    if old_t != new_t:
                        turnover += 1
                turnover_cost = (turnover / target_n) * cost_rate * 2
                prev_eq *= (1.0 - turnover_cost)
                slots = new_slots
                
            # 일별 수익률 계산
            slot_returns = []
            cash_slots = 0
            for s in slots:
                if s is None or s["is_cash"]:
                    slot_returns.append(daily_sgov_ret)
                    cash_slots += 1
                    continue
                t = s["ticker"]
                r_day = ret_mat.loc[d, t] if t in ret_mat.columns else 0.0
                curr_px = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan
                
                loss_from_entry = (curr_px / s["entry_price"]) - 1.0 if (s["entry_price"] > 0 and not np.isnan(curr_px)) else 0.0
                if enable_stop_loss and loss_from_entry <= -stop_loss_pct:
                    slot_returns.append(-stop_loss_pct - cost_rate)
                    s["is_cash"] = True
                    stop_loss_count += 1
                    cash_slots += 1
                else:
                    slot_returns.append(r_day)
                    
            port_ret = sum(slot_returns) / target_n
            eq.iloc[i] = prev_eq * (1.0 + port_ret)
            monthly_cash_history.append(cash_slots / target_n)
            
        m = compute_metrics(eq, qqq_eq)
        m["stop_loss_count"] = stop_loss_count
        m["avg_cash_pct"] = np.mean(monthly_cash_history) * 100.0
        return eq, m
        
    print("⏳ 시뮬레이션 수행 중...")
    eq_px_accel_full, m_px_full = simulate(px_accel, enable_stop_loss=False)
    eq_px_accel_slot, m_px_slot = simulate(px_accel, enable_stop_loss=True)
    eq_cap_accel_full, m_cap_full = simulate(cap_accel, enable_stop_loss=False)
    eq_cap_accel_slot, m_cap_slot = simulate(cap_accel, enable_stop_loss=True)
    m_qqq = compute_metrics(qqq_eq, qqq_eq)
    
    # 10년 클린 구간 (2015-01-01 ~ 2024-12-31)
    cal_10y = cal[cal >= "2015-01-01"]
    def get_10y(eq_series):
        sub = eq_series.loc[cal_10y]
        sub = sub / sub.iloc[0]
        sub_q = qqq_eq.loc[cal_10y]
        sub_q = sub_q / sub_q.iloc[0]
        return compute_metrics(sub, sub_q)
        
    m10_px_full = get_10y(eq_px_accel_full)
    m10_px_slot = get_10y(eq_px_accel_slot)
    m10_cap_full = get_10y(eq_cap_accel_full)
    m10_cap_slot = get_10y(eq_cap_accel_slot)
    m10_qqq = get_10y(qqq_eq)
    
    # 결과 비교 출력
    print("\n" + "=" * 115)
    print("📊 [26년 전 기간 성과 비교: 1999-03-10 ~ 2024-12-31 (26개년)]")
    print("=" * 115)
    header = f"{'전략명':<32} | {'누적수익률':>12} | {'CAGR':>8} | {'MDD':>9} | {'변동성':>8} | {'샤프':>6} | {'손절횟수':>8}"
    print(header)
    print("-" * 115)
    
    all_res = [
        ("🥇 시총 가속도 TOP10 (풀투자)", m_cap_full),
        ("🥈 주가 가속도 TOP10 (풀투자)", m_px_full),
        ("🥉 QQQ ETF (벤치마크)", m_qqq),
        ("4. 주가 가속도 TOP10 (슬롯비우기)", m_px_slot),
        ("5. 시총 가속도 TOP10 (슬롯비우기)", m_cap_slot),
    ]
    for name, m in all_res:
        sl_str = f"{m.get('stop_loss_count', 0):,}회" if 'stop_loss_count' in m else "-"
        print(f"{name:<32} | {m['Total Return']*100:>11.1f}% | {m['CAGR']*100:>7.2f}% | {m['MDD']*100:>8.2f}% | {m['Volatility']*100:>7.2f}% | {m['Sharpe']:>6.2f} | {sl_str:>8}")
    print("=" * 115)
    
    print("\n" + "=" * 115)
    print("🌟 [최근 10년 클린 구간 성과: 2015-01-01 ~ 2024-12-31 (10개년)]")
    print("=" * 115)
    header_10y = f"{'전략명':<32} | {'누적수익률':>12} | {'CAGR':>8} | {'MDD':>9} | {'변동성':>8} | {'샤프':>6}"
    print(header_10y)
    print("-" * 115)
    all_10y = [
        ("🥇 시총 가속도 TOP10 (풀투자)", m10_cap_full),
        ("🥈 주가 가속도 TOP10 (풀투자)", m10_px_full),
        ("🥉 QQQ ETF (벤치마크)", m10_qqq),
        ("4. 주가 가속도 TOP10 (슬롯비우기)", m10_px_slot),
        ("5. 시총 가속도 TOP10 (슬롯비우기)", m10_cap_slot),
    ]
    for name, m in all_10y:
        print(f"{name:<32} | {m['Total Return']*100:>11.1f}% | {m['CAGR']*100:>7.2f}% | {m['MDD']*100:>8.2f}% | {m['Volatility']*100:>7.2f}% | {m['Sharpe']:>6.2f}")
    print("=" * 115)
    
    # 연도별 비교 데이터 생성
    years = sorted(list(set(cal.year)))
    annual_rows = []
    for y in years:
        sub_px = eq_px_accel_full[eq_px_accel_full.index.year == y]
        sub_cap = eq_cap_accel_full[eq_cap_accel_full.index.year == y]
        sub_q = qqq_eq[qqq_eq.index.year == y]
        if len(sub_px) < 5:
            continue
        r_px = (sub_px.iloc[-1] / sub_px.iloc[0]) - 1.0
        r_cap = (sub_cap.iloc[-1] / sub_cap.iloc[0]) - 1.0
        r_q = (sub_q.iloc[-1] / sub_q.iloc[0]) - 1.0
        
        def calc_mdd(s):
            c = s.cummax()
            return ((s - c) / c).min()
            
        annual_rows.append({
            "Year": y,
            "Price_Accel_Ret": r_px,
            "Price_Accel_MDD": calc_mdd(sub_px),
            "Cap_Accel_Ret": r_cap,
            "Cap_Accel_MDD": calc_mdd(sub_cap),
            "QQQ_Ret": r_q,
            "QQQ_MDD": calc_mdd(sub_q)
        })
    df_ann = pd.DataFrame(annual_rows)
    df_ann.to_csv(f"{REPORTS_DIR}/price_vs_cap_acceleration_annual.csv", index=False)
    
    # 4대 폭락장 비교
    crises = [
        ("닷컴 버블 붕괴", "2000-03-24", "2002-10-09"),
        ("2008 글로벌 금융위기", "2007-10-31", "2008-11-20"),
        ("2020 코로나 쇼크", "2020-02-19", "2020-03-23"),
        ("2022 인플레 금리인상", "2021-11-19", "2022-12-28")
    ]
    print("\n🛡️ [4대 대폭락장 방어력 비교: 주가 가속도 vs 시총 가속도 vs QQQ]")
    print("-" * 105)
    print(f"{'위기 국면':<22} | {'QQQ ETF':>12} | {'주가 가속도':>15} | {'시총 가속도':>15} | {'주가 vs 시총 우열':<18}")
    print("-" * 105)
    for c_name, st, ed in crises:
        def get_sub_mdd(eq_series):
            sub = eq_series.loc[(eq_series.index >= st) & (eq_series.index <= ed)]
            if len(sub) == 0: return 0.0
            cum_m = sub.cummax()
            return ((sub - cum_m) / cum_m).min()
            
        q_m = get_sub_mdd(qqq_eq)
        px_m = get_sub_mdd(eq_px_accel_full)
        cap_m = get_sub_mdd(eq_cap_accel_full)
        diff = px_m - cap_m
        better = "주가 가속도 우위" if diff > 0 else "시총 가속도 우위"
        print(f"{c_name:<22} | {q_m*100:>11.2f}% | {px_m*100:>14.2f}% | {cap_m*100:>14.2f}% | {better} ({diff*100:>+5.1f}%p)")
    print("=" * 105)

if __name__ == "__main__":
    run_price_vs_cap_acceleration_test()
