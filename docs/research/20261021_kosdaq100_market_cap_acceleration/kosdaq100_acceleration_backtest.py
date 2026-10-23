"""
[EXP-KRX-004] 코스닥 100 (KOSDAQ 100) 시가총액 가속도(Market Cap Acceleration) TOP 10 실측 백테스트
===================================================================================================
- 검증 기간: 2013-01-02 ~ 2026-10-12 (약 13.8년, 총 3,380여 거래일)
- 벤치마크: 코스닥 종합 지수 (KRX U201) & 코스닥 150 지수 (KRX U390)
- 유니버스: 매월 초 60일 평균 시가총액 기준 KOSDAQ 보통주 상위 100개 종목 (동적 서바이버 편향 방지)
- 팩터 정의:
  1. 시총 1개월 증가속도: V_t = Cap_t / Cap_{t-21} - 1.0
  2. 직전 1개월 증가속도: V_prev = Cap_{t-21} / Cap_{t-42} - 1.0
  3. 시총 증가 가속도(Acceleration): A_t = V_t - V_prev
- 운용 메커니즘:
  1. 슬롯 구조: 10개 슬롯 (각 10% 균등 배분)
  2. 매월 첫 거래일 상위 10종목 교체 리밸런싱 (기존 보유 종목 유지 시 거래비용 절감)
  3. 실전 거래비용: 매수 0.035%, 매도 0.235% (증권거래세 0.20% 포함) = 왕복 0.27% 차감
  4. 현금 대기 이자: 연 2.5% 무위험 이자 일할 지급 (0.025 / 252)
  5. 슬롯 비우기(Slot Emptying): 진입가 대비 특정 손절선(-15%, -25%) 도달 시 즉시 전량 매도 후 월말까지 현금 대기
"""
import os
import sys
import time
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = "/home/kuzimo/projects/CTradeSystem"
RESEARCH_DIR = f"{ROOT}/docs/research/20261021_kosdaq100_market_cap_acceleration"
os.makedirs(RESEARCH_DIR, exist_ok=True)

def compute_metrics(eq_series, benchmark_eq=None):
    """표준 퀀트 성과 지표 산출 함수"""
    total_ret = (eq_series.iloc[-1] / eq_series.iloc[0]) - 1.0
    years = (eq_series.index[-1] - eq_series.index[0]).days / 365.25
    cagr = (eq_series.iloc[-1] / eq_series.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    
    daily_rets = eq_series.pct_change().dropna()
    ann_vol = daily_rets.std() * np.sqrt(252) if len(daily_rets) > 0 else 0.0
    sharpe = (cagr - 0.02) / ann_vol if ann_vol > 0 else 0.0
    
    cum_max = eq_series.cummax()
    dd = (eq_series - cum_max) / cum_max
    mdd = dd.min()
    
    corr = np.nan
    beta = np.nan
    if benchmark_eq is not None:
        bm_rets = benchmark_eq.pct_change().dropna()
        common = daily_rets.index.intersection(bm_rets.index)
        if len(common) > 10:
            r1 = daily_rets.loc[common]
            r2 = bm_rets.loc[common]
            corr = r1.corr(r2)
            var_bm = r2.var()
            if var_bm > 0:
                beta = r1.cov(r2) / var_bm
                
    return {
        "Total Return": total_ret,
        "CAGR": cagr,
        "Volatility": ann_vol,
        "Sharpe": sharpe,
        "MDD": mdd,
        "Correlation": corr,
        "Beta": beta
    }

def run_backtest():
    t_start = time.time()
    print("=" * 115)
    print("🚀 [실측 백테스트] 코스닥 100 시가총액 가속도(Market Cap Acceleration) TOP 10 전략 검증 (2013~2026)")
    print("=" * 115)
    
    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    # 1. 유니버스 로드
    uni = pd.read_csv(uni_path)
    name_map = dict(zip(uni['code'], uni['name']))
    kosdaq_codes = set(uni[(uni['market'] == 'KOSDAQ') & (uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])
    print(f"📊 KOSDAQ 보통주 전체 유니버스: {len(kosdaq_codes)}개 종목 로드 완료")

    # 2. 벤치마크 (U201 = 코스닥 종합, U390 = 코스닥 150) 로드
    df_idx = pd.read_parquet(idx_path)
    u201 = df_idx[df_idx['code'] == 'U201'].sort_values('date').set_index('date')['close']
    u390 = df_idx[df_idx['code'] == 'U390'].sort_values('date').set_index('date')['close']
    cal = u201.index
    print(f"📅 검증 기간: {cal[0].strftime('%Y-%m-%d')} ~ {cal[-1].strftime('%Y-%m-%d')} (총 {len(cal):,} 거래일, 13.8개년)")

    # 3. 가격 및 시가총액 데이터 로드
    print("⏳ 가격 및 시가총액 시계열 로딩 중...")
    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'value'],
        filters=[('code', 'in', list(kosdaq_codes))]
    )
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap'],
        filters=[('code', 'in', list(kosdaq_codes))]
    )

    adj_mat = df_p.pivot(index='date', columns='code', values='close').reindex(cal).ffill()
    mcap_mat = df_ext.pivot(index='date', columns='code', values='market_cap').reindex(cal).ffill()
    ret_mat = adj_mat.pct_change().fillna(0.0)

    # 벤치마크 자산곡선
    bm_kq_ret = u201.pct_change().fillna(0.0)
    bm_kq_eq = (1.0 + bm_kq_ret).cumprod()

    bm_kq150_ret = u390.pct_change().fillna(0.0)
    bm_kq150_eq = (1.0 + bm_kq150_ret).cumprod()

    # 4. 월초 리밸런싱 날짜 도출
    monthly_dates = []
    prev_m = None
    for d in cal:
        m = (d.year, d.month)
        if prev_m is not None and m != prev_m:
            monthly_dates.append(d)
        prev_m = m
    warmup_idx = 42
    monthly_dates = [d for d in monthly_dates if d >= cal[warmup_idx]]
    monthly_set = set(monthly_dates)
    print(f"🔄 월간 리밸런싱 횟수: {len(monthly_dates)}회 (첫 리밸런싱: {monthly_dates[0].strftime('%Y-%m-%d')})")

    # 5. 팩터 산출
    # 60일 평균 시가총액으로 코스닥 100 동적 유니버스 정의
    mcap_roll = mcap_mat.rolling(60, min_periods=20).mean()

    # 시총 속도 및 가속도
    mcap_shift21 = mcap_mat.shift(21)
    mcap_shift42 = mcap_mat.shift(42)
    v1m_mat = (mcap_mat / mcap_shift21) - 1.0
    v_prev_mat = (mcap_shift21 / mcap_shift42) - 1.0
    accel_mat = v1m_mat - v_prev_mat

    # 주가 속도 및 가속도
    adj_shift21 = adj_mat.shift(21)
    adj_shift42 = adj_mat.shift(42)
    p_v1m_mat = (adj_mat / adj_shift21) - 1.0
    p_v_prev_mat = (adj_shift21 / adj_shift42) - 1.0
    p_accel_mat = p_v1m_mat - p_v_prev_mat

    # 파라미터
    target_n = 10
    buy_fee = 0.00035    # 매수 수수료 0.035%
    sell_fee = 0.00235   # 매도 수수료+세금 0.235%
    daily_cash_ret = 0.025 / 252.0  # 연 2.5% 단기이자

    def simulate(metric_mat, stop_loss_pct=None, name="Strategy"):
        """범용 포트폴리오 백테스터"""
        eq = pd.Series(index=cal, dtype=float)
        start_date = monthly_dates[0]
        start_pos = cal.get_loc(start_date)
        eq.iloc[:start_pos] = 1.0
        
        slots = [None] * target_n
        stop_loss_count = 0
        rebal_count = 0
        cash_history = []
        turnover_count = 0
        rebalance_log = []

        for i in range(start_pos, len(cal)):
            d = cal[i]
            prev_d = cal[i-1]
            prev_eq = eq.iloc[i-1]

            # 1. 월초 리밸런싱
            if d in monthly_set:
                rebal_count += 1
                # 당월 코스닥 100 유니버스 도출 (직전일 기준 60일 평균 시총 상위 100개)
                top100_universe = mcap_roll.loc[prev_d].dropna().sort_values(ascending=False).head(100).index
                
                # 유니버스 내에서 팩터 스코어 상위 10종목 선정
                scores = metric_mat.loc[prev_d, top100_universe].dropna()
                top_tickers = scores.sort_values(ascending=False).head(target_n).index.tolist()
                
                old_tickers = [s["ticker"] for s in slots if s is not None and not s["is_cash"]]
                new_slots = []
                turnover = 0
                
                for idx, t in enumerate(top_tickers):
                    p_entry = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan
                    new_slots.append({
                        "ticker": t,
                        "entry_price": p_entry,
                        "peak_price": p_entry,
                        "is_cash": False
                    })
                    if t not in old_tickers:
                        turnover += 1
                        
                cost = (turnover / target_n) * (buy_fee + sell_fee)
                turnover_count += turnover
                prev_eq *= (1.0 - cost)
                slots = new_slots
                
                if len(rebalance_log) < 5 or (d.year in [2015, 2020, 2024] and d.month == 1):
                    rebalance_log.append({
                        "date": d.strftime("%Y-%m-%d"),
                        "tickers": [f"{name_map.get(t, t)}({t})" for t in top_tickers[:5]]
                    })

            # 2. 일별 수익률 계산 및 손절 검사
            slot_returns = []
            cash_slots = 0
            
            for s in slots:
                if s is None or s["is_cash"]:
                    slot_returns.append(daily_cash_ret)
                    cash_slots += 1
                    continue
                
                t = s["ticker"]
                r_day = ret_mat.loc[d, t] if t in ret_mat.columns else 0.0
                curr_px = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan
                
                if not np.isnan(curr_px):
                    if curr_px > s["peak_price"]:
                        s["peak_price"] = curr_px
                
                loss_from_entry = (curr_px / s["entry_price"]) - 1.0 if (s["entry_price"] > 0 and not np.isnan(curr_px)) else 0.0
                
                if stop_loss_pct is not None and loss_from_entry <= -stop_loss_pct:
                    slot_returns.append(-stop_loss_pct - sell_fee)
                    s["is_cash"] = True
                    stop_loss_count += 1
                    cash_slots += 1
                else:
                    slot_returns.append(r_day)
                    
            port_ret = sum(slot_returns) / target_n
            eq.iloc[i] = prev_eq * (1.0 + port_ret)
            cash_history.append(cash_slots / target_n)

        sub_eq = eq.iloc[start_pos:]
        sub_bm_kq = bm_kq_eq.iloc[start_pos:]
        sub_bm_kq = sub_bm_kq / sub_bm_kq.iloc[0]
        
        m = compute_metrics(sub_eq, sub_bm_kq)
        m["stop_loss_count"] = stop_loss_count
        m["avg_cash_pct"] = np.mean(cash_history) * 100.0 if cash_history else 0.0
        m["turnover_count"] = turnover_count
        m["rebalance_log"] = rebalance_log
        return sub_eq, m

    print("\n⏳ 5대 핵심 전략 시뮬레이션 일괄 수행 중...")
    # 1. 시총 가속도 TOP 10 (풀투자)
    eq_accel_full, m_accel_full = simulate(accel_mat, stop_loss_pct=None, name="Cap_Accel_Full")
    # 2. 시총 가속도 TOP 10 + 슬롯 비우기 (-15% 손절)
    eq_accel_sl15, m_accel_sl15 = simulate(accel_mat, stop_loss_pct=0.15, name="Cap_Accel_SlotEmpty_15")
    # 3. 시총 가속도 TOP 10 + 슬롯 비우기 (-25% 완화 손절)
    eq_accel_sl25, m_accel_sl25 = simulate(accel_mat, stop_loss_pct=0.25, name="Cap_Accel_SlotEmpty_25")
    # 4. 시총 1M 속도 TOP 10 (풀투자)
    eq_vel_full, m_vel_full = simulate(v1m_mat, stop_loss_pct=None, name="Cap_Vel1M_Full")
    # 5. 주가 가속도 TOP 10 (풀투자)
    eq_px_accel_full, m_px_accel_full = simulate(p_accel_mat, stop_loss_pct=None, name="Price_Accel_Full")

    # 벤치마크 정규화
    start_pos = cal.get_loc(monthly_dates[0])
    sub_bm_kq = bm_kq_eq.iloc[start_pos:] / bm_kq_eq.iloc[start_pos]
    sub_bm_kq150 = bm_kq150_eq.iloc[start_pos:] / bm_kq150_eq.iloc[start_pos]
    m_bm_kq = compute_metrics(sub_bm_kq, sub_bm_kq)
    m_bm_kq150 = compute_metrics(sub_bm_kq150, sub_bm_kq)

    print("\n" + "=" * 125)
    print("📊 [코스닥 100 시총 가속도 vs 주요 전략 전 기간 실측 성과 (2013-03-04 ~ 2026-10-12, 13.6개년)]")
    print("=" * 125)
    header = f"{'전략명':<34} | {'누적수익률':>12} | {'CAGR':>8} | {'MDD':>9} | {'변동성':>8} | {'샤프':>6} | {'베타':>6} | {'손절회수':>7} | {'평균현금%':>7}"
    print(header)
    print("-" * 125)

    all_strategies = [
        ("1. 시총 가속도 TOP10 (풀투자)", m_accel_full),
        ("2. 시총 가속도 TOP10 (슬롯비우기 -15%)", m_accel_sl15),
        ("3. 시총 가속도 TOP10 (슬롯비우기 -25%)", m_accel_sl25),
        ("4. 시총 1M속도 TOP10 (풀투자)", m_vel_full),
        ("5. 주가 가속도 TOP10 (풀투자)", m_px_accel_full),
        ("6. 벤치마크 (코스닥 종합 지수 U201)", m_bm_kq),
        ("7. 벤치마크 (코스닥 150 지수 U390)", m_bm_kq150),
    ]

    for name, m in all_strategies:
        sl_str = f"{m.get('stop_loss_count', 0):,}회" if 'stop_loss_count' in m else "-"
        cash_str = f"{m.get('avg_cash_pct', 0.0):.1f}%" if 'avg_cash_pct' in m else "0.0%"
        beta_str = f"{m['Beta']:.2f}" if not np.isnan(m['Beta']) else "-"
        print(f"{name:<34} | {m['Total Return']*100:>11.1f}% | {m['CAGR']*100:>7.2f}% | {m['MDD']*100:>8.2f}% | {m['Volatility']*100:>7.2f}% | {m['Sharpe']:>6.2f} | {beta_str:>6} | {sl_str:>7} | {cash_str:>7}")
    print("=" * 125)

    # 최근 코로나 이후 (2020-01-02 ~ 2026-10-12) 성과 비교
    print("\n" + "=" * 125)
    print("🌟 [최근 코로나 이후 6.8년 구간 성과 비교 (2020-01-02 ~ 2026-10-12)]")
    print("=" * 125)
    header_recent = f"{'전략명':<34} | {'구간 누적수익률':>14} | {'구간 CAGR':>10} | {'구간 MDD':>10} | {'구간 변동성':>9} | {'구간 샤프':>7}"
    print(header_recent)
    print("-" * 125)

    sub_cal = sub_bm_kq.index[sub_bm_kq.index >= "2020-01-02"]
    def get_recent_metrics(s_series):
        sub = s_series.loc[sub_cal]
        sub = sub / sub.iloc[0]
        bm_sub = sub_bm_kq.loc[sub_cal]
        bm_sub = bm_sub / bm_sub.iloc[0]
        return compute_metrics(sub, bm_sub)

    for name, eq_s in [
        ("1. 시총 가속도 TOP10 (풀투자)", eq_accel_full),
        ("2. 시총 가속도 TOP10 (슬롯비우기 -15%)", eq_accel_sl15),
        ("3. 시총 가속도 TOP10 (슬롯비우기 -25%)", eq_accel_sl25),
        ("4. 시총 1M속도 TOP10 (풀투자)", eq_vel_full),
        ("5. 주가 가속도 TOP10 (풀투자)", eq_px_accel_full),
        ("6. 벤치마크 (코스닥 종합 지수 U201)", sub_bm_kq),
        ("7. 벤치마크 (코스닥 150 지수 U390)", sub_bm_kq150),
    ]:
        rm = get_recent_metrics(eq_s)
        print(f"{name:<34} | {rm['Total Return']*100:>13.1f}% | {rm['CAGR']*100:>9.2f}% | {rm['MDD']*100:>9.2f}% | {rm['Volatility']*100:>8.2f}% | {rm['Sharpe']:>7.2f}")
    print("=" * 125)

    # 연도별 성과 매트릭스 계산
    years = sorted(list(set(sub_bm_kq.index.year)))
    annual_rows = []
    for y in years:
        idx_y = sub_bm_kq.index[sub_bm_kq.index.year == y]
        if len(idx_y) < 5:
            continue
        def calc_yr(s):
            sub = s.loc[idx_y]
            r = (sub.iloc[-1] / sub.iloc[0]) - 1.0
            cum = sub.cummax()
            mdd = ((sub - cum) / cum).min()
            return r, mdd

        r_af, mdd_af = calc_yr(eq_accel_full)
        r_as15, mdd_as15 = calc_yr(eq_accel_sl15)
        r_as25, mdd_as25 = calc_yr(eq_accel_sl25)
        r_vf, mdd_vf = calc_yr(eq_vel_full)
        r_pf, mdd_pf = calc_yr(eq_px_accel_full)
        r_bm, mdd_bm = calc_yr(sub_bm_kq)

        annual_rows.append({
            "Year": y,
            "Cap_Accel_Full_Ret": r_af, "Cap_Accel_Full_MDD": mdd_af,
            "Cap_Accel_SL15_Ret": r_as15, "Cap_Accel_SL15_MDD": mdd_as15,
            "Cap_Accel_SL25_Ret": r_as25, "Cap_Accel_SL25_MDD": mdd_as25,
            "Cap_Vel1M_Full_Ret": r_vf, "Cap_Vel1M_Full_MDD": mdd_vf,
            "Price_Accel_Full_Ret": r_pf, "Price_Accel_Full_MDD": mdd_pf,
            "KOSDAQ_Ret": r_bm, "KOSDAQ_MDD": mdd_bm,
        })

    annual_df = pd.DataFrame(annual_rows)
    annual_csv = f"{RESEARCH_DIR}/kosdaq100_acceleration_annual.csv"
    annual_df.to_csv(annual_csv, index=False)

    print("\n📅 [연도별 수익률 및 MDD 매트릭스: 코스닥 100 가속도(풀투자) vs 슬롯비우기(-15%) vs 코스닥 종합 지수]")
    print("-" * 115)
    print(f"{'연도':<6} | {'가속도 풀투자':>15} (MDD) | {'가속도 슬롯비우기':>15} (MDD) | {'코스닥 종합 지수':>15} (MDD)")
    print("-" * 115)
    for _, row in annual_df.iterrows():
        y = int(row["Year"])
        s_af = f"{row['Cap_Accel_Full_Ret']*100:>+6.1f}% ({row['Cap_Accel_Full_MDD']*100:>5.1f}%)"
        s_as = f"{row['Cap_Accel_SL15_Ret']*100:>+6.1f}% ({row['Cap_Accel_SL15_MDD']*100:>5.1f}%)"
        s_bm = f"{row['KOSDAQ_Ret']*100:>+6.1f}% ({row['KOSDAQ_MDD']*100:>5.1f}%)"
        print(f"{y:<6} | {s_af:>21} | {s_as:>21} | {s_bm:>21}")
    print("=" * 115)

    # 일별 자산곡선 CSV 저장
    results_df = pd.DataFrame({
        "Date": sub_bm_kq.index,
        "Cap_Accel_Full": eq_accel_full.values,
        "Cap_Accel_SL15": eq_accel_sl15.values,
        "Cap_Accel_SL25": eq_accel_sl25.values,
        "Cap_Vel1M_Full": eq_vel_full.values,
        "Price_Accel_Full": eq_px_accel_full.values,
        "KOSDAQ": sub_bm_kq.values,
        "KOSDAQ150": sub_bm_kq150.values,
    }).set_index("Date")
    results_csv = f"{RESEARCH_DIR}/kosdaq100_acceleration_results.csv"
    results_df.to_csv(results_csv)
    print(f"\n💾 백테스트 상세 시계열 저장 완료: {results_csv}")
    print(f"💾 연도별 성과 매트릭스 저장 완료: {annual_csv}")
    print(f"⏱️ 소요 시간: {time.time() - t_start:.2f}초")

if __name__ == "__main__":
    run_backtest()
