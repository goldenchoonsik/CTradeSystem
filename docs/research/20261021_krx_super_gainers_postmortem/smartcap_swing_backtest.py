"""
[EXP-KRX-007] 스마트머니 스몰캡 턴어라운드 "2~4개월 스윙 익절" 실측 백테스트
=============================================================================
- 검증 기간: 2013-01-02 ~ 2024-12-31 (12개년 전수 조사)
- 유니버스: KOSPI & KOSDAQ 보통주 전체 (우선주, 스팩 제외)
- 4대 진입 조건 (매월 초 스캔):
  1. 시총: 500억 ~ 2,500억 원 스몰캡
  2. 위치: 200일선 근처(0.90 ~ 1.15) AND 52주 최고가 대비 85% 이하
  3. 거래대금: 최근 20일 거래대금 / 직전 60일 거래대금 >= 1.5배 (유연화)
  4. 수급 지속성: 최근 20일 외인 지분율 증가 >= +0.5%p OR 기관 순매수 >= 상장주식수의 0.8%
- 자금 운용:
  - 최대 5개 슬롯 (종목당 최대 20%), 포착 종목 수 K개에 대해 1/min(K, 5) 동적 배분 (현금 놀림 최소화)
- 실전 스윙 청산 룰:
  1. 1차 익절: +30% 도달 시 50% 분할 익절
  2. 2차 익절: +50% 도달 시 전량 청산
  3. 트레일링 스탑: 진입 후 최고점 대비 -15% 반락 시 전량 청산 (이익 보존)
  4. 시간 청산(Time Cut): 최대 4개월(84거래일) 만기 도달 시 전량 청산 (장투 원천 차단)
  5. 손절선: 진입가 대비 -12% 도달 시 전량 손절
- 거래비용: 왕복 0.27% 엄격 차감
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

def run_swing_backtest():
    t_start = time.time()
    print("=" * 115)
    print("🚀 [실측 백테스트] 스마트머니 스몰캡 턴어라운드 '2~4개월 스윙 익절' 전략 (2013~2024)")
    print("=" * 115)

    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    name_map = dict(zip(uni['code'], uni['name']))
    mkt_map = dict(zip(uni['code'], uni['market']))
    valid_codes = set(uni[(uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])

    df_idx = pd.read_parquet(idx_path)
    u201 = df_idx[df_idx['code'] == 'U201'].sort_values('date').set_index('date')['close']
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u201.loc[(u201.index >= '2013-01-01') & (u201.index <= '2024-12-31')].index
    print(f"📅 검증 기간: {cal[0].strftime('%Y-%m-%d')} ~ {cal[-1].strftime('%Y-%m-%d')} (총 {len(cal):,} 거래일, 12개년)")

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

    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()
    h52_mat = adj_mat.rolling(250, min_periods=100).max()
    val20_mat = val_mat.rolling(20, min_periods=10).mean()
    val60_mat = val_mat.rolling(60, min_periods=30).mean()
    fgn_delta20_mat = fgn_mat - fgn_mat.shift(20)
    inst_pct20_mat = (inst_mat.rolling(20, min_periods=10).sum() / shares_mat) * 100.0

    bm_kq = u201.loc[cal] / u201.loc[cal].iloc[0]
    bm_k200 = u180.loc[cal] / u180.loc[cal].iloc[0]

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

    max_slots = 5  # 최대 5개 슬롯 (각 20%)
    cost_rate = 0.0027  # 왕복 0.27%
    daily_cash_ret = 0.025 / 252.0

    # 스크리너 (1.5배 폭증 완화)
    def screen_candidates(d_prev):
        mcap_row = mcap_mat.loc[d_prev].dropna() / 10000_0000
        cond_cap = (mcap_row >= 500) & (mcap_row <= 2500)
        cands = mcap_row[cond_cap].index

        p_row = adj_mat.loc[d_prev, cands]
        ma_row = ma200_mat.loc[d_prev, cands]
        h52_row = h52_mat.loc[d_prev, cands]
        cond_loc = (p_row / ma_row >= 0.90) & (p_row / ma_row <= 1.15) & (p_row / h52_row <= 0.85)
        cands = cands[cond_loc.fillna(False)]

        v20_row = val20_mat.loc[d_prev, cands]
        v60_row = val60_mat.loc[d_prev, cands]
        vol_surge = v20_row / v60_row
        cond_vol = (vol_surge >= 1.5)  # 1.5배로 유연화
        cands = cands[cond_vol.fillna(False)]

        f_delta = fgn_delta20_mat.loc[d_prev, cands]
        i_pct = inst_pct20_mat.loc[d_prev, cands]
        cond_flow = (f_delta >= 0.5) | (i_pct >= 0.8)
        cands = cands[cond_flow.fillna(False)]

        if len(cands) == 0:
            return []

        score = (vol_surge.loc[cands].fillna(1.5)) + (f_delta.loc[cands].fillna(0.0) * 2.0) + (i_pct.loc[cands].fillna(0.0) * 2.0)
        return score.sort_values(ascending=False).head(max_slots).index.tolist()

    # 일별 시뮬레이션
    def simulate_swing():
        eq = pd.Series(index=cal, dtype=float)
        start_date = monthly_dates[0]
        start_pos = cal.get_loc(start_date)
        eq.iloc[:start_pos] = 1.0

        # 슬롯 관리: list of dict
        # {ticker, entry_price, peak_price, holding_days, weight, remaining_qty (1.0 or 0.5), is_cash}
        slots = []
        for _ in range(max_slots):
            slots.append({"ticker": None, "entry_price": 0.0, "peak_price": 0.0, "holding_days": 0, "remaining_ratio": 1.0, "is_cash": True})

        trade_logs = []
        monthly_cash_history = []

        for i in range(start_pos, len(cal)):
            d = cal[i]
            prev_d = cal[i-1]
            prev_eq = eq.iloc[i-1]

            # 1. 월초 신규 진입 (비어있는 슬롯 채우기)
            if d in monthly_set:
                # 빈 슬롯 확인
                empty_slots = [idx for idx, s in enumerate(slots) if s["is_cash"] or s["ticker"] is None]
                if empty_slots:
                    cands = screen_candidates(prev_d)
                    curr_holding = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]
                    new_picks = [c for c in cands if c not in curr_holding]
                    
                    for slot_idx, ticker in zip(empty_slots, new_picks):
                        p_entry = adj_mat.loc[d, ticker] if ticker in adj_mat.columns else np.nan
                        if not np.isnan(p_entry) and p_entry > 0:
                            slots[slot_idx] = {
                                "ticker": ticker,
                                "entry_price": p_entry,
                                "peak_price": p_entry,
                                "holding_days": 0,
                                "remaining_ratio": 1.0,
                                "is_cash": False
                            }
                            prev_eq *= (1.0 - (1.0 / max_slots) * 0.00035)  # 매수 수수료

            # 2. 일별 보유 종목 스윙 룰 검사 및 수익률 집계
            slot_returns = []
            cash_slots = 0

            for s in slots:
                if s["is_cash"] or s["ticker"] is None:
                    slot_returns.append(daily_cash_ret)
                    cash_slots += 1
                    continue

                t = s["ticker"]
                r_day = ret_mat.loc[d, t] if t in ret_mat.columns else 0.0
                curr_px = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan

                if np.isnan(curr_px) or curr_px <= 0:
                    slot_returns.append(0.0)
                    continue

                s["holding_days"] += 1
                if curr_px > s["peak_price"]:
                    s["peak_price"] = curr_px

                gain_from_entry = (curr_px / s["entry_price"]) - 1.0
                drop_from_peak = (curr_px / s["peak_price"]) - 1.0

                action = None
                exit_return = 0.0

                # 룰 1. +50% 전량 청산
                if gain_from_entry >= 0.50:
                    action = "🎯 +50% 목표 익절"
                    exit_return = 0.50 - cost_rate
                # 룰 2. 최고점 대비 -15% 트레일링 스탑 (최소 +10% 이상 이익 보존 시)
                elif gain_from_entry >= 0.15 and drop_from_peak <= -0.15:
                    action = "🛡️ 트레일링 스탑 (이익 보존)"
                    exit_return = gain_from_entry - cost_rate
                # 룰 3. 손절선 -12%
                elif gain_from_entry <= -0.12:
                    action = "🛑 -12% 손절"
                    exit_return = -0.12 - cost_rate
                # 룰 4. 시간 청산 (84거래일, 4개월 만기)
                elif s["holding_days"] >= 84:
                    action = "⏱️ 4개월 만기 시간 청산"
                    exit_return = gain_from_entry - cost_rate

                if action is not None:
                    # 청산 발생!
                    slot_returns.append(exit_return * s["remaining_ratio"])
                    trade_logs.append({
                        "date": d.strftime("%Y-%m-%d"),
                        "code": t,
                        "name": name_map.get(t, t),
                        "gain_pct": gain_from_entry * 100.0,
                        "holding_days": s["holding_days"],
                        "reason": action
                    })
                    s["is_cash"] = True
                    s["ticker"] = None
                    cash_slots += 1
                else:
                    # 룰 5. +30% 도달 시 50% 분할 익절
                    if s["remaining_ratio"] == 1.0 and gain_from_entry >= 0.30:
                        s["remaining_ratio"] = 0.5
                        # 절반에 대해 익절 수익 반영
                        slot_returns.append(r_day * 0.5 + 0.30 * 0.5)
                    else:
                        slot_returns.append(r_day * s["remaining_ratio"])

            port_ret = sum(slot_returns) / max_slots
            eq.iloc[i] = prev_eq * (1.0 + port_ret)
            monthly_cash_history.append(cash_slots / max_slots)

        sub_eq = eq.iloc[start_pos:]
        sub_bm = bm_kq.iloc[start_pos:] / bm_kq.iloc[start_pos]
        m = compute_metrics(sub_eq, sub_bm)
        m["avg_cash_pct"] = np.mean(monthly_cash_history) * 100.0 if monthly_cash_history else 0.0
        m["trade_count"] = len(trade_logs)
        m["win_rate"] = (pd.DataFrame(trade_logs)['gain_pct'] > 0).mean() * 100.0 if trade_logs else 0.0
        return sub_eq, m, pd.DataFrame(trade_logs)

    eq_swing, m_swing, df_trades = simulate_swing()

    start_pos = cal.get_loc(monthly_dates[0])
    sub_bm_kq = bm_kq.iloc[start_pos:] / bm_kq.iloc[start_pos]
    sub_bm_k200 = bm_k200.iloc[start_pos:] / bm_k200.iloc[start_pos]
    m_kq = compute_metrics(sub_bm_kq, sub_bm_kq)
    m_k200 = compute_metrics(sub_bm_k200, sub_bm_kq)

    print("\n" + "=" * 125)
    print("📊 [스마트머니 스몰캡 '2~4개월 스윙 익절' 실측 성과 (2013~2024, 12개년)]")
    print("=" * 125)
    header = f"{'전략명':<34} | {'누적수익률':>12} | {'CAGR':>8} | {'MDD':>9} | {'변동성':>8} | {'샤프':>6} | {'승률':>6} | {'총매매수':>6}"
    print(header)
    print("-" * 125)

    all_res = [
        ("1. 스마트캡 2~4M 스윙 (목표익절/트레일링)", m_swing),
        ("2. 스마트캡 12M 장투 (기존 풀투자)", {"Total Return": 0.743, "CAGR": 0.0481, "MDD": -0.2334, "Volatility": 0.0946, "Sharpe": 0.30, "Beta": 0.23}),
        ("3. 벤치마크 (코스닥 종합 지수 U201)", m_kq),
        ("4. 벤치마크 (코스피 200 지수 U180)", m_k200),
    ]

    for name, m in all_res:
        win_str = f"{m.get('win_rate', 0.0):.1f}%" if 'win_rate' in m else "-"
        cnt_str = f"{m.get('trade_count', 0)}회" if 'trade_count' in m else "-"
        print(f"{name:<34} | {m['Total Return']*100:>11.1f}% | {m['CAGR']*100:>7.2f}% | {m['MDD']*100:>8.2f}% | {m['Volatility']*100:>7.2f}% | {m['Sharpe']:>6.2f} | {win_str:>6} | {cnt_str:>6}")
    print("=" * 125)

    # 연도별 성과 매트릭스
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

        r_sw, mdd_sw = calc_yr(eq_swing)
        r_kq, mdd_kq = calc_yr(sub_bm_kq)

        annual_rows.append({
            "Year": y,
            "SmartCap_Swing_Ret": r_sw, "SmartCap_Swing_MDD": mdd_sw,
            "KOSDAQ_Ret": r_kq, "KOSDAQ_MDD": mdd_kq,
        })

    annual_df = pd.DataFrame(annual_rows)
    annual_csv = f"{RESEARCH_DIR}/smartcap_swing_annual.csv"
    annual_df.to_csv(annual_csv, index=False)

    print("\n📅 [연도별 수익률 및 MDD: 스윙 전략 vs 코스닥 지수]")
    print("-" * 115)
    print(f"{'연도':<6} | {'스마트캡 스윙 전략':>18} (MDD) | {'코스닥 종합 지수':>18} (MDD)")
    print("-" * 115)
    for _, row in annual_df.iterrows():
        y = int(row["Year"])
        s_sw = f"{row['SmartCap_Swing_Ret']*100:>+6.1f}% ({row['SmartCap_Swing_MDD']*100:>5.1f}%)"
        s_kq = f"{row['KOSDAQ_Ret']*100:>+6.1f}% ({row['KOSDAQ_MDD']*100:>5.1f}%)"
        print(f"{y:<6} | {s_sw:>24} | {s_kq:>24}")
    print("=" * 115)

    # 청산 사유별 통계
    print("\n🔍 [스윙 청산 사유별 분포]")
    if not df_trades.empty:
        for rsn, cnt in df_trades['reason'].value_counts().items():
            sub_t = df_trades[df_trades['reason'] == rsn]
            print(f"   - {rsn:<26}: {cnt:>3}회 (평균수익: {sub_t['gain_pct'].mean():>+6.1f}%, 평균보유: {sub_t['holding_days'].mean():.0f}일)")

    # 대표 성공 매매 쇼케이스
    print("\n" + "=" * 115)
    print("🏆 [스윙 익절 대표 성공 매매 TOP 8]")
    print("=" * 115)
    print(f"{'청산일자':<12} | {'종목명 (코드)':<18} | {'실현수익률':>12} | {'보유일수':>8} | {'청산사유':<24}")
    print("-" * 115)
    top_trades = df_trades.sort_values('gain_pct', ascending=False).head(8)
    for _, r in top_trades.iterrows():
        print(f"{r['date']:<12} | {r['name']+'('+r['code']+')':<18} | {r['gain_pct']:>+11.1f}% | {r['holding_days']:>7}일 | {r['reason']:<24}")
    print("=" * 115)

    # 저장
    results_df = pd.DataFrame({
        "Date": sub_bm_kq.index,
        "SmartCap_Swing": eq_swing.values,
        "KOSDAQ": sub_bm_kq.values,
        "KOSPI200": sub_bm_k200.values,
    }).set_index("Date")
    results_csv = f"{RESEARCH_DIR}/smartcap_swing_results.csv"
    results_df.to_csv(results_csv)
    trades_csv = f"{RESEARCH_DIR}/smartcap_swing_trade_log.csv"
    df_trades.to_csv(trades_csv, index=False)

    print(f"\n💾 백테스트 시계열 저장 완료: {results_csv}")
    print(f"💾 매매일지 저장 완료: {trades_csv}")
    print(f"⏱️ 소요 시간: {time.time() - t_start:.2f}초")

if __name__ == "__main__":
    run_swing_backtest()
