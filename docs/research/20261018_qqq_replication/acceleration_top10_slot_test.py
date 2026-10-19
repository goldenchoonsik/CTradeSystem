"""
[EXP-017] 나스닥 100 시총 증가 가속도(Acceleration) & 모멘텀 TOP 10 × 슬롯 비우기 백테스트
========================================================================================
- 검증 기간: 1999-03-10 ~ 2024-12-31 (26년) 및 최근 10년 (2015-2024)
- 유니버스: 나스닥 100 구성종목
- 전략 로직:
  1. 매월 첫 거래일에 유니버스 내 '시총 증가 가속도(Acceleration)' 또는 '시총 증가율(Velocity)' 상위 10종목 선정
  2. 10개 슬롯에 각 10%씩 균등 배분 (1/10)
  3. 월중 -15% 손절 발생 시 즉시 매도 후, 해당 슬롯은 다음 월초 리밸런싱까지 '현금(SGOV 이자)'으로 비워둠 (슬롯 비우기)
  4. 슬롯 비우기 적용 vs 미적용(풀투자) vs QQQ 비교
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.append(r"docs/research/20261018_qqq_replication")
from max_tenure_hybrid_backtest import (
    load_stock_data, compute_metrics, NAS_UNIVERSE
)

ROOT = "DataVault"
REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

def run_acceleration_slot_test():
    print("=" * 115)
    print("🚀 [실측 백테스트] 나스닥 100 시총 증가 가속도(Acceleration) TOP 10 × 슬롯 비우기 전략")
    print("기간: 1999-03-10 ~ 2024-12-31 (26개년)")
    print("슬롯: 10개 (각 10%), 월간 리밸런싱, -15% 도달 시 해당 슬롯 월말까지 현금화(슬롯 비우기)")
    print("=" * 115)
    
    # 1. 데이터 로드
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
    
    # 월초 리밸런싱 날짜
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
    daily_sgov_ret = 0.025 / 252.0 # 연 2.5% 무위험 현금 이자
    stop_loss_pct = 0.15 # -15% 손절
    target_n = 10
    slot_w = 1.0 / target_n
    
    # 지표 계산:
    # 1) 시총(거래대금 프록시 dvol)의 1개월 증가율 (Velocity_1M)
    #    V_t = dvol_t / dvol_{t-21} - 1.0
    # 2) 이전 1개월 증가율 (Velocity_prev1M)
    #    V_prev = dvol_{t-21} / dvol_{t-42} - 1.0
    # 3) 가속도 (Acceleration) = V_t - V_prev
    # 4) 주가 1개월 수익률 모멘텀 (Price_Mom_1M)
    # 5) 주가 3개월 수익률 모멘텀 (Price_Mom_3M)
    
    # 21일 및 42일 시프트
    dvol_shift21 = dvol_mat.shift(21)
    dvol_shift42 = dvol_mat.shift(42)
    
    v1m = (dvol_mat / dvol_shift21) - 1.0
    v_prev = (dvol_shift21 / dvol_shift42) - 1.0
    accel_mat = v1m - v_prev
    
    # 주가 모멘텀
    px_shift21 = adj_mat.shift(21)
    px_shift63 = adj_mat.shift(63)
    px_mom1m = (adj_mat / px_shift21) - 1.0
    px_mom3m = (adj_mat / px_shift63) - 1.0
    
    # 전략 시뮬레이터 함수
    def simulate_strategy(ranking_metric_mat, enable_stop_loss=True, name="Strategy"):
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        
        # 슬롯 상태: slot_id (0~9) -> dict(ticker, entry_price, is_cash, peak_price)
        slots = [None] * target_n
        
        # 통계 추적
        stop_loss_count = 0
        rebalance_count = 0
        monthly_cash_history = []
        
        for i in range(1, len(cal)):
            d = cal[i]
            prev_d = cal[i-1]
            prev_eq = eq.iloc[i-1]
            
            # 1. 월초 리밸런싱
            if d in monthly_set:
                rebalance_count += 1
                # 직전일 기준 상위 10개 종목 산출
                scores = ranking_metric_mat.loc[prev_d].dropna()
                # 충분한 유동성이 있는 종목 필터링 (최소 dvol 상위 80% 내)
                dvol_row = dvol_mat.loc[prev_d].dropna()
                if len(dvol_row) > 10:
                    med_dvol = dvol_row.median()
                    scores = scores[scores.index.isin(dvol_row[dvol_row >= med_dvol].index)]
                    
                top_tickers = scores.sort_values(ascending=False).head(target_n).index.tolist()
                
                # 기존 보유 중인 티커와 비교하여 변경 비용 계산
                old_tickers = [s["ticker"] for s in slots if s is not None and not s["is_cash"]]
                new_slots = []
                for idx, t in enumerate(top_tickers):
                    p_entry = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan
                    new_slots.append({
                        "ticker": t,
                        "entry_price": p_entry,
                        "peak_price": p_entry,
                        "is_cash": False
                    })
                # 교체되는 슬롯에 비용 차감
                turnover = 0
                for idx in range(target_n):
                    old_t = old_tickers[idx] if idx < len(old_tickers) else None
                    new_t = new_slots[idx]["ticker"] if idx < len(new_slots) else None
                    if old_t != new_t:
                        turnover += 1
                turnover_cost = (turnover / target_n) * cost_rate * 2
                prev_eq *= (1.0 - turnover_cost)
                slots = new_slots
            
            # 2. 일별 수익률 계산 및 슬롯별 손절 검사
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
                
                # 피크 가격 업데이트
                if not np.isnan(curr_px):
                    if curr_px > s["peak_price"]:
                        s["peak_price"] = curr_px
                
                # 손절 검사: 진입가 대비 -15%
                loss_from_entry = (curr_px / s["entry_price"]) - 1.0 if (s["entry_price"] > 0 and not np.isnan(curr_px)) else 0.0
                
                if enable_stop_loss and loss_from_entry <= -stop_loss_pct:
                    # 손절 발생! 당일 손절 수익률 적용 후 현금 슬롯으로 전환
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

    # 4가지 모델 테스트:
    # 1. 시총 증가 가속도(Acceleration) + 슬롯 비우기
    # 2. 시총 증가 가속도(Acceleration) - 슬롯 비우기 없음 (풀투자)
    # 3. 시총 1M 증가 속도(Velocity/1M) + 슬롯 비우기
    # 4. 주가 3M 모멘텀(Price Mom 3M) + 슬롯 비우기
    # 5. QQQ ETF
    
    print("\n⏳ 5가지 전략 시뮬레이션 계산 중...")
    eq_accel_slot, m_accel_slot = simulate_strategy(accel_mat, enable_stop_loss=True, name="Cap_Accel_SlotEmpty")
    eq_accel_full, m_accel_full = simulate_strategy(accel_mat, enable_stop_loss=False, name="Cap_Accel_FullInvest")
    eq_vel_slot, m_vel_slot = simulate_strategy(v1m, enable_stop_loss=True, name="Cap_Vel1M_SlotEmpty")
    eq_mom3m_slot, m_mom3m_slot = simulate_strategy(px_mom3m, enable_stop_loss=True, name="Price_Mom3M_SlotEmpty")
    
    m_qqq = compute_metrics(qqq_eq, qqq_eq)
    
    # 10년 클린 구간 (2015-01-01 ~ 2024-12-31)
    cal_10y = cal[cal >= "2015-01-01"]
    def get_10y_metrics(eq_series):
        sub_eq = eq_series.loc[cal_10y]
        sub_eq = sub_eq / sub_eq.iloc[0]
        sub_qqq = qqq_eq.loc[cal_10y]
        sub_qqq = sub_qqq / sub_qqq.iloc[0]
        return compute_metrics(sub_eq, sub_qqq)
    
    m10_accel_slot = get_10y_metrics(eq_accel_slot)
    m10_accel_full = get_10y_metrics(eq_accel_full)
    m10_vel_slot = get_10y_metrics(eq_vel_slot)
    m10_mom3m_slot = get_10y_metrics(eq_mom3m_slot)
    m10_qqq = get_10y_metrics(qqq_eq)
    
    # 결과 출력
    print("\n" + "=" * 115)
    print("📊 [26년 전 기간 성과 비교: 1999-03-10 ~ 2024-12-31 (26개년)]")
    print("=" * 115)
    header = f"{'전략명':<32} | {'누적수익률':>12} | {'CAGR':>8} | {'MDD':>9} | {'변동성':>8} | {'샤프':>6} | {'손절횟수':>8} | {'평균현금%':>8}"
    print(header)
    print("-" * 115)
    
    results = [
        ("1. 시총 가속도 TOP10 + 슬롯비우기", m_accel_slot),
        ("2. 시총 가속도 TOP10 (풀투자, 비우기X)", m_accel_full),
        ("3. 시총 1M속도 TOP10 + 슬롯비우기", m_vel_slot),
        ("4. 주가 3M모멘텀 TOP10 + 슬롯비우기", m_mom3m_slot),
        ("5. 벤치마크 (QQQ ETF)", m_qqq),
    ]
    
    for name, m in results:
        sl_str = f"{m.get('stop_loss_count', 0):,}회" if 'stop_loss_count' in m else "-"
        cash_str = f"{m.get('avg_cash_pct', 0.0):.1f}%" if 'avg_cash_pct' in m else "0.0%"
        print(f"{name:<32} | {m['Total Return']*100:>11.1f}% | {m['CAGR']*100:>7.2f}% | {m['MDD']*100:>8.2f}% | {m['Volatility']*100:>7.2f}% | {m['Sharpe']:>6.2f} | {sl_str:>8} | {cash_str:>8}")
    print("=" * 115)
    
    print("\n" + "=" * 115)
    print("🌟 [최근 10년 클린 구간 성과 비교: 2015-01-01 ~ 2024-12-31 (10개년)]")
    print("=" * 115)
    header_10y = f"{'전략명':<32} | {'누적수익률':>12} | {'CAGR':>8} | {'MDD':>9} | {'변동성':>8} | {'샤프':>6}"
    print(header_10y)
    print("-" * 115)
    
    results_10y = [
        ("1. 시총 가속도 TOP10 + 슬롯비우기", m10_accel_slot),
        ("2. 시총 가속도 TOP10 (풀투자, 비우기X)", m10_accel_full),
        ("3. 시총 1M속도 TOP10 + 슬롯비우기", m10_vel_slot),
        ("4. 주가 3M모멘텀 TOP10 + 슬롯비우기", m10_mom3m_slot),
        ("5. 벤치마크 (QQQ ETF)", m10_qqq),
    ]
    for name, m in results_10y:
        print(f"{name:<32} | {m['Total Return']*100:>11.1f}% | {m['CAGR']*100:>7.2f}% | {m['MDD']*100:>8.2f}% | {m['Volatility']*100:>7.2f}% | {m['Sharpe']:>6.2f}")
    print("=" * 115)
    
    # 주요 위기 구간(MDD) 방어력 분석
    crises = [
        ("닷컴 버블 붕괴", "2000-03-24", "2002-10-09"),
        ("2008 글로벌 금융위기", "2007-10-31", "2008-11-20"),
        ("2020 코로나 쇼크", "2020-02-19", "2020-03-23"),
        ("2022 인플레 금리인상", "2021-11-19", "2022-12-28")
    ]
    print("\n🛡️ [역사적 4대 폭락장 방어력 실측 (최대 낙폭 MDD)]")
    print("-" * 105)
    print(f"{'위기 국면':<22} | {'QQQ ETF':>12} | {'가속도+슬롯비우기':>18} | {'가속도 풀투자':>15} | {'방어 효과':>16}")
    print("-" * 105)
    
    for c_name, st, ed in crises:
        def get_sub_mdd(eq_series):
            sub = eq_series.loc[(eq_series.index >= st) & (eq_series.index <= ed)]
            if len(sub) == 0:
                return 0.0
            cum_m = sub.cummax()
            return ((sub - cum_m) / cum_m).min()
            
        q_mdd = get_sub_mdd(qqq_eq)
        sl_mdd = get_sub_mdd(eq_accel_slot)
        full_mdd = get_sub_mdd(eq_accel_full)
        diff = sl_mdd - q_mdd
        print(f"{c_name:<22} | {q_mdd*100:>11.2f}% | {sl_mdd*100:>17.2f}% | {full_mdd*100:>14.2f}% | {diff*100:>+15.2f}%p")
    print("=" * 105)
    
    # CSV 저장
    df_out = pd.DataFrame({
        "Date": cal,
        "QQQ": qqq_eq,
        "Cap_Accel_SlotEmpty": eq_accel_slot,
        "Cap_Accel_FullInvest": eq_accel_full,
        "Cap_Vel1M_SlotEmpty": eq_vel_slot,
        "Price_Mom3M_SlotEmpty": eq_mom3m_slot
    })
    df_out.to_csv(f"{REPORTS_DIR}/acceleration_top10_slot_test_results.csv", index=False)
    print(f"\n💾 일별 성과 곡선 저장 완료: {REPORTS_DIR}/acceleration_top10_slot_test_results.csv")

if __name__ == "__main__":
    run_acceleration_slot_test()
