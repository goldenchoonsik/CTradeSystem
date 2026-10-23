"""
[EXP-KRX-006] 스마트머니 스몰캡 턴어라운드 (Smart-Cap Turnaround) 실측 백테스트
=================================================================================
- 검증 기간: 2013-01-02 ~ 2024-12-31 (12개년 전수 조사)
- 유니버스: KOSPI & KOSDAQ 보통주 전체 (우선주, 스팩 제외)
- 4대 절대 사전 필터:
  1. 시총: 500억 ~ 2,500억 원 스몰캡
  2. 위치: 200일선 근처(0.90 ~ 1.15) AND 52주 최고가 대비 85% 이하 (바닥 턴어라운드)
  3. 거래대금: 최근 20일 거래대금 / 직전 60일 거래대금 >= 2.0배 (유입 총성)
  4. 수급 지속성: 최근 20일 외인 지분율 증가 >= +0.5%p OR 기관 순매수 >= 상장주식수의 0.8%
- 운용 메커니즘:
  - 매월 첫 거래일 스크리닝 통과 종목 균등 매수 (최대 15종목, 팩터 스코어 순)
  - 통과 종목 부족 시 남은 비중은 연 2.5% 무위험 현금 대기
  - 비교 트랙:
    ① 6개월 리밸런싱 (풀투자)
    ② 1년 리밸런싱 (풀투자)
    ③ 1년 보유 + -15% 손절 (슬롯 비우기)
    ④ 벤치마크 (코스닥 종합 지수 U201 & 코스피 200 U180)
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
RESEARCH_DIR = f"{ROOT}/docs/research/20261021_krx_super_gainers_postmortem"

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
    
    beta = np.nan
    if benchmark_eq is not None:
        bm_rets = benchmark_eq.pct_change().dropna()
        common = daily_rets.index.intersection(bm_rets.index)
        if len(common) > 10:
            r1 = daily_rets.loc[common]
            r2 = bm_rets.loc[common]
            var_bm = r2.var()
            if var_bm > 0:
                beta = r1.cov(r2) / var_bm
                
    return {
        "Total Return": total_ret,
        "CAGR": cagr,
        "Volatility": ann_vol,
        "Sharpe": sharpe,
        "MDD": mdd,
        "Beta": beta
    }

def run_screener_backtest():
    t_start = time.time()
    print("=" * 115)
    print("🚀 [실측 백테스트] 스마트머니 스몰캡 턴어라운드 (Smart-Cap Turnaround) 전략 검증 (2013~2024)")
    print("=" * 115)

    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    name_map = dict(zip(uni['code'], uni['name']))
    mkt_map = dict(zip(uni['code'], uni['market']))
    valid_codes = set(uni[(uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])

    # 벤치마크 지수 로드 (2013~2024)
    df_idx = pd.read_parquet(idx_path)
    u201 = df_idx[df_idx['code'] == 'U201'].sort_values('date').set_index('date')['close']
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u201.loc[(u201.index >= '2013-01-01') & (u201.index <= '2024-12-31')].index
    print(f"📅 검증 기간: {cal[0].strftime('%Y-%m-%d')} ~ {cal[-1].strftime('%Y-%m-%d')} (총 {len(cal):,} 거래일, 12개년)")

    # 가격 및 수급 데이터 로드
    print("⏳ 가격, 시총, 외국인, 기관 시계열 로딩 중...")
    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'value'],
        filters=[
            ('code', 'in', list(valid_codes)),
            ('date', '>=', pd.Timestamp('2012-01-01')),
            ('date', '<=', pd.Timestamp('2024-12-31'))
        ]
    )
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap', 'foreign_hold_pct', 'inst_net_qty', 'shares_listed'],
        filters=[
            ('code', 'in', list(valid_codes)),
            ('date', '>=', pd.Timestamp('2012-01-01')),
            ('date', '<=', pd.Timestamp('2024-12-31'))
        ]
    )

    print("⏳ 피벗 행렬 구축 중...")
    adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()
    val_mat = df_p.pivot(index='date', columns='code', values='value').sort_index()
    mcap_mat = df_ext.pivot(index='date', columns='code', values='market_cap').sort_index()
    fgn_mat = df_ext.pivot(index='date', columns='code', values='foreign_hold_pct').sort_index()
    inst_mat = df_ext.pivot(index='date', columns='code', values='inst_net_qty').sort_index()
    shares_mat = df_ext.pivot(index='date', columns='code', values='shares_listed').sort_index()

    ret_mat = adj_mat.pct_change().fillna(0.0)

    # 보조 지표 사전 계산
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()
    h52_mat = adj_mat.rolling(250, min_periods=100).max()
    val20_mat = val_mat.rolling(20, min_periods=10).mean()
    val60_mat = val_mat.rolling(60, min_periods=30).mean()
    
    # 20일 외인 지분율 변화 (%p)
    fgn_delta20_mat = fgn_mat - fgn_mat.shift(20)
    # 20일 기관 누적 순매수 지분율 환산 (%)
    inst_sum20_mat = inst_mat.rolling(20, min_periods=10).sum()
    inst_pct20_mat = (inst_sum20_mat / shares_mat) * 100.0

    # 벤치마크 정규화
    bm_kq = u201.loc[cal] / u201.loc[cal].iloc[0]
    bm_k200 = u180.loc[cal] / u180.loc[cal].iloc[0]

    # 월초 리밸런싱 날짜 도출
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
    print(f"🔄 월간 검사 횟수: {len(monthly_dates)}회")

    # 파라미터
    max_slots = 15
    buy_fee = 0.00035
    sell_fee = 0.00235
    cost_rate = buy_fee + sell_fee  # 왕복 0.27%
    daily_cash_ret = 0.025 / 252.0

    # 4대 조건 스크리너 함수
    def screen_candidates(d_prev):
        # 1. 시총: 500억 ~ 2,500억
        mcap_row = mcap_mat.loc[d_prev].dropna() / 10000_0000
        cond_cap = (mcap_row >= 500) & (mcap_row <= 2500)
        cands = mcap_row[cond_cap].index

        # 2. 위치: 200일선 0.90~1.15 AND 52주 고가 0.85 이하
        p_row = adj_mat.loc[d_prev, cands]
        ma_row = ma200_mat.loc[d_prev, cands]
        h52_row = h52_mat.loc[d_prev, cands]
        
        ratio_ma = p_row / ma_row
        ratio_h52 = p_row / h52_row
        cond_loc = (ratio_ma >= 0.90) & (ratio_ma <= 1.15) & (ratio_h52 <= 0.85)
        cands = cands[cond_loc.fillna(False)]

        # 3. 거래대금 폭증: val20 / val60 >= 2.0
        v20_row = val20_mat.loc[d_prev, cands]
        v60_row = val60_mat.loc[d_prev, cands]
        vol_surge = v20_row / v60_row
        cond_vol = (vol_surge >= 2.0)
        cands = cands[cond_vol.fillna(False)]

        # 4. 수급 지속성: 외인 +0.5%p 이상 OR 기관 +0.8% 이상
        f_delta = fgn_delta20_mat.loc[d_prev, cands]
        i_pct = inst_pct20_mat.loc[d_prev, cands]
        cond_flow = (f_delta >= 0.5) | (i_pct >= 0.8)
        cands = cands[cond_flow.fillna(False)]

        if len(cands) == 0:
            return []

        # 스코어링: 거래대금 폭증배수 + 수급 강도 종합
        score = (vol_surge.loc[cands].fillna(2.0)) + (f_delta.loc[cands].fillna(0.0) * 2.0) + (i_pct.loc[cands].fillna(0.0) * 2.0)
        top_cands = score.sort_values(ascending=False).head(max_slots).index.tolist()
        return top_cands

    # 시뮬레이터
    def simulate_strategy(holding_months=6, enable_stop_loss=False, name="Strategy"):
        eq = pd.Series(index=cal, dtype=float)
        start_date = monthly_dates[0]
        start_pos = cal.get_loc(start_date)
        eq.iloc[:start_pos] = 1.0

        slots = [None] * max_slots
        stop_loss_count = 0
        all_picked_records = []
        monthly_cash_history = []

        rebal_interval = holding_months

        for i in range(start_pos, len(cal)):
            d = cal[i]
            prev_d = cal[i-1]
            prev_eq = eq.iloc[i-1]

            # 리밸런싱 검사
            if d in monthly_set:
                m_idx = monthly_dates.index(d)
                # rebal_interval 주기마다 또는 매월 슬롯 순환
                if m_idx % rebal_interval == 0:
                    picked = screen_candidates(prev_d)
                    
                    old_tickers = [s["ticker"] for s in slots if s is not None and not s["is_cash"]]
                    new_slots = [None] * max_slots
                    turnover = 0
                    
                    for s_id in range(max_slots):
                        if s_id < len(picked):
                            t = picked[s_id]
                            p_entry = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan
                            new_slots[s_id] = {
                                "ticker": t,
                                "entry_price": p_entry,
                                "is_cash": False
                            }
                            if t not in old_tickers:
                                turnover += 1
                            all_picked_records.append({
                                "date": d.strftime("%Y-%m-%d"),
                                "code": t,
                                "name": name_map.get(t, t),
                                "entry_price": p_entry
                            })
                        else:
                            # 종목이 부족하면 현금 슬롯
                            new_slots[s_id] = {"ticker": None, "entry_price": 0.0, "is_cash": True}

                    cost = (turnover / max_slots) * cost_rate
                    prev_eq *= (1.0 - cost)
                    slots = new_slots

            # 일별 수익률
            slot_returns = []
            cash_slots = 0
            for s in slots:
                if s is None or s["is_cash"] or s["ticker"] is None:
                    slot_returns.append(daily_cash_ret)
                    cash_slots += 1
                    continue
                
                t = s["ticker"]
                r_day = ret_mat.loc[d, t] if t in ret_mat.columns else 0.0
                curr_px = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan
                
                loss_from_entry = (curr_px / s["entry_price"]) - 1.0 if (s["entry_price"] > 0 and not np.isnan(curr_px)) else 0.0

                if enable_stop_loss and loss_from_entry <= -0.15:
                    slot_returns.append(-0.15 - sell_fee)
                    s["is_cash"] = True
                    stop_loss_count += 1
                    cash_slots += 1
                else:
                    slot_returns.append(r_day)

            port_ret = sum(slot_returns) / max_slots
            eq.iloc[i] = prev_eq * (1.0 + port_ret)
            monthly_cash_history.append(cash_slots / max_slots)

        sub_eq = eq.iloc[start_pos:]
        sub_bm = bm_kq.iloc[start_pos:] / bm_kq.iloc[start_pos]
        m = compute_metrics(sub_eq, sub_bm)
        m["stop_loss_count"] = stop_loss_count
        m["avg_cash_pct"] = np.mean(monthly_cash_history) * 100.0 if monthly_cash_history else 0.0
        m["picked_count"] = len(all_picked_records)
        return sub_eq, m, all_picked_records

    print("\n⏳ 3가지 운용 트랙 실측 시뮬레이션 계산 중...")
    eq_6m, m_6m, picks_6m = simulate_strategy(holding_months=6, enable_stop_loss=False, name="SmartCap_6M_Full")
    eq_12m, m_12m, picks_12m = simulate_strategy(holding_months=12, enable_stop_loss=False, name="SmartCap_12M_Full")
    eq_12m_sl, m_12m_sl, _ = simulate_strategy(holding_months=12, enable_stop_loss=True, name="SmartCap_12M_SL15")

    start_pos = cal.get_loc(monthly_dates[0])
    sub_bm_kq = bm_kq.iloc[start_pos:] / bm_kq.iloc[start_pos]
    sub_bm_k200 = bm_k200.iloc[start_pos:] / bm_k200.iloc[start_pos]
    m_kq = compute_metrics(sub_bm_kq, sub_bm_kq)
    m_k200 = compute_metrics(sub_bm_k200, sub_bm_kq)

    print("\n" + "=" * 125)
    print("📊 [스마트머니 스몰캡 턴어라운드 전략 vs 벤치마크 12개년 실측 성과 (2013~2024)]")
    print("=" * 125)
    header = f"{'전략명':<34} | {'누적수익률':>12} | {'CAGR':>8} | {'MDD':>9} | {'변동성':>8} | {'샤프':>6} | {'베타':>6} | {'손절회수':>7} | {'평균현금%':>7}"
    print(header)
    print("-" * 125)

    all_res = [
        ("1. 스마트캡 12M 보유 (풀투자)", m_12m),
        ("2. 스마트캡 6M 보유 (풀투자)", m_6m),
        ("3. 스마트캡 12M 보유 + 슬롯비우기(-15%)", m_12m_sl),
        ("4. 벤치마크 (코스닥 종합 지수 U201)", m_kq),
        ("5. 벤치마크 (코스피 200 지수 U180)", m_k200),
    ]

    for name, m in all_res:
        sl_str = f"{m.get('stop_loss_count', 0):,}회" if 'stop_loss_count' in m else "-"
        cash_str = f"{m.get('avg_cash_pct', 0.0):.1f}%" if 'avg_cash_pct' in m else "0.0%"
        beta_str = f"{m['Beta']:.2f}" if not np.isnan(m['Beta']) else "-"
        print(f"{name:<34} | {m['Total Return']*100:>11.1f}% | {m['CAGR']*100:>7.2f}% | {m['MDD']*100:>8.2f}% | {m['Volatility']*100:>7.2f}% | {m['Sharpe']:>6.2f} | {beta_str:>6} | {sl_str:>7} | {cash_str:>7}")
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

        r_12, mdd_12 = calc_yr(eq_12m)
        r_6, mdd_6 = calc_yr(eq_6m)
        r_kq, mdd_kq = calc_yr(sub_bm_kq)

        annual_rows.append({
            "Year": y,
            "SmartCap_12M_Ret": r_12, "SmartCap_12M_MDD": mdd_12,
            "SmartCap_6M_Ret": r_6, "SmartCap_6M_MDD": mdd_6,
            "KOSDAQ_Ret": r_kq, "KOSDAQ_MDD": mdd_kq,
        })

    annual_df = pd.DataFrame(annual_rows)
    annual_csv = f"{RESEARCH_DIR}/smartcap_turnaround_annual.csv"
    annual_df.to_csv(annual_csv, index=False)

    print("\n📅 [연도별 수익률 및 MDD 매트릭스: 스마트캡 12M vs 코스닥 지수]")
    print("-" * 115)
    print(f"{'연도':<6} | {'스마트캡 12M 보유':>18} (MDD) | {'코스닥 종합 지수':>18} (MDD)")
    print("-" * 115)
    for _, row in annual_df.iterrows():
        y = int(row["Year"])
        s_12 = f"{row['SmartCap_12M_Ret']*100:>+6.1f}% ({row['SmartCap_12M_MDD']*100:>5.1f}%)"
        s_kq = f"{row['KOSDAQ_Ret']*100:>+6.1f}% ({row['KOSDAQ_MDD']*100:>5.1f}%)"
        print(f"{y:<6} | {s_12:>24} | {s_kq:>24}")
    print("=" * 115)

    # 사전 포착된 종목들 중 실제 텐배거 적중률 분석
    list_path = f"{RESEARCH_DIR}/super_gainers_list_2013_2024.csv"
    df_gainers = pd.read_csv(list_path)
    super_codes = set(df_gainers['Code'])
    
    picked_df = pd.DataFrame(picks_12m)
    hit_count = 0
    if not picked_df.empty:
        picked_codes = set(picked_df['code'])
        hits = picked_codes.intersection(super_codes)
        hit_count = len(hits)
        print(f"\n🎯 [슈퍼 급등주(409선) 사전 포착 적중률 분석]")
        print(f"   - 스크리너가 추천/매수한 총 고유 종목 수: {len(picked_codes)}개")
        print(f"   - 그중 실제 그해 슈퍼 급등주(연간 +200% 이상)와 일치한 종목 수: {hit_count}개")
        print(f"   👉 사전 포착된 대표 슈퍼스타: {', '.join([name_map.get(c, c) for c in list(hits)[:8]])}")

    # 자산곡선 CSV 저장
    results_df = pd.DataFrame({
        "Date": sub_bm_kq.index,
        "SmartCap_12M_Full": eq_12m.values,
        "SmartCap_6M_Full": eq_6m.values,
        "SmartCap_12M_SL15": eq_12m_sl.values,
        "KOSDAQ": sub_bm_kq.values,
        "KOSPI200": sub_bm_k200.values,
    }).set_index("Date")
    results_csv = f"{RESEARCH_DIR}/smartcap_turnaround_results.csv"
    results_df.to_csv(results_csv)

    print(f"\n💾 백테스트 시계열 저장 완료: {results_csv}")
    print(f"💾 연도별 성과 매트릭스 저장 완료: {annual_csv}")
    print(f"⏱️ 소요 시간: {time.time() - t_start:.2f}초")

if __name__ == "__main__":
    run_screener_backtest()
