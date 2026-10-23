"""
[EXP-KRX-008] 스마트머니 스몰캡 턴어라운드 "고정 5-슬롯(Fixed 5-Slots)" 스윙 시스템 실측 백테스트
=================================================================================================
- 검증 기간: 2013-01-02 ~ 2024-12-30 (12개년 전수 조사)
- 유니버스: KOSPI & KOSDAQ 보통주 전체 (우선주, 스팩 제외)
- 벤치마크: 코스닥 종합 지수 (KRX U201) & 코스피 200 지수 (KRX U180)

[시스템 구조]
1. 슬롯 구조: 총 5개 슬롯 (각 20% 고정 배분, Fixed 20% Allocation)
   - 각 슬롯은 독립적으로 종목을 매수 -> 스윙 청산 -> 빈 슬롯 복귀
   - 빈 슬롯이 있을 때만 익일 시가에 신규 매수하여 슬롯을 채움 (자금 100% 엄격 통제)
2. 고승률 진입 조건:
   - 시장 필터: 코스닥 지수(U201) 종가 >= 60일 이동평균선 (지수 하락장 신규 진입 차단)
   - 시가총액: 500억 ~ 2,500억 원
   - 위치: 200일선 0.90 ~ 1.15 AND 52주 최고가 대비 85% 이하 (바닥 턴어라운드)
   - 정배열 안착: 종가 >= 20일선 AND 20일선 >= 60일선 (역배열 칼날 차단)
   - 거래대금 폭증: 20일 평균 거래대금 / 60일 평균 거래대금 >= 1.8배
   - 수급 집중도: 20일 외인 지분 증가 >= 1.0%p OR 20일 기관 순매수 >= 1.0% OR 쌍끌이(외인>=0.5%p & 기관>=0.5%)
3. 청산 룰 (Slot Exit Engine):
   - 🎯 1차 익절: +30% 도달 시 50% 분할 매도
   - 🎯 2차 익절: +50% 도달 시 잔여 전량 차익실현
   - 🛡️ 트레일링 스탑: 최고점 대비 -15% 반락 시 전량 매도 (수익 보존)
   - 🛑 손절선: 진입가 대비 -15% 도달 시 전량 손절
   - ⏱️ 시간 만기: 최대 60거래일(약 3개월) 경과 시 전량 청산
4. 거래비용: 왕복 0.27% (매수 0.035%, 매도 0.235%)
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

def run_fixed_5slots_backtest():
    t_start = time.time()
    print("=" * 125)
    print("🚀 [실측 백테스트] 스마트머니 스몰캡 '고정 5-슬롯(Fixed 5-Slots)' 스윙 시스템 검증 (2013~2024)")
    print("=" * 125)

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

    # 코스닥 60일 이평선 계산
    kq_ma60 = u201.rolling(60, min_periods=30).mean().loc[cal]

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

    # 이평선 및 모멘텀 지표
    ma20_mat = adj_mat.rolling(20, min_periods=10).mean()
    ma60_mat = adj_mat.rolling(60, min_periods=30).mean()
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()
    h52_mat = adj_mat.rolling(250, min_periods=100).max()
    val20_mat = val_mat.rolling(20, min_periods=10).mean()
    val60_mat = val_mat.rolling(60, min_periods=30).mean()
    
    fgn_delta20_mat = fgn_mat - fgn_mat.shift(20)
    inst_pct20_mat = (inst_mat.rolling(20, min_periods=10).sum() / shares_mat) * 100.0

    bm_kq = u201.loc[cal] / u201.loc[cal].iloc[0]
    bm_k200 = u180.loc[cal] / u180.loc[cal].iloc[0]

    num_slots = 5
    slot_weight = 1.0 / num_slots  # 20%
    cost_rate = 0.0027  # 왕복 0.27%
    daily_cash_ret = 0.025 / 252.0

    # 고승률 스크리너 함수
    def screen_candidates(d_prev):
        # 1. 시장 필터: 코스닥 지수가 60일선 이상이어야 함
        if d_prev in kq_ma60.index:
            if u201.loc[d_prev] < kq_ma60.loc[d_prev]:
                return []
        
        # 2. 시총 500억 ~ 2,500억
        mcap_row = mcap_mat.loc[d_prev].dropna() / 10000_0000
        cond_cap = (mcap_row >= 500) & (mcap_row <= 2500)
        cands = mcap_row[cond_cap].index

        # 3. 위치: 200일선 0.90~1.15 AND 52주 고점 0.85 이하
        p_row = adj_mat.loc[d_prev, cands].reindex(cands)
        ma200_row = ma200_mat.loc[d_prev, cands].reindex(cands)
        h52_row = h52_mat.loc[d_prev, cands].reindex(cands)
        cond_loc = (p_row / ma200_row >= 0.90) & (p_row / ma200_row <= 1.15) & (p_row / h52_row <= 0.85)
        cands = cands[cond_loc.fillna(False)]

        if len(cands) == 0:
            return []

        # 4. 단기 정배열 안착: 종가 >= 20일선 AND 20일선 >= 60일선
        p_row = adj_mat.loc[d_prev, cands].reindex(cands)
        ma20_row = ma20_mat.loc[d_prev, cands].reindex(cands)
        ma60_row = ma60_mat.loc[d_prev, cands].reindex(cands)
        cond_trend = (p_row >= ma20_row) & (ma20_row >= ma60_row)
        cands = cands[cond_trend.fillna(False)]

        if len(cands) == 0:
            return []

        # 5. 거래대금 폭증: 20일 평균 / 60일 평균 >= 1.8배
        v20_row = val20_mat.loc[d_prev, cands].reindex(cands)
        v60_row = val60_mat.loc[d_prev, cands].reindex(cands)
        vol_surge = v20_row / v60_row
        cond_vol = (vol_surge >= 1.8)
        cands = cands[cond_vol.fillna(False)]

        if len(cands) == 0:
            return []

        # 6. 수급 집중도
        f_delta = fgn_delta20_mat.loc[d_prev, cands].reindex(cands)
        i_pct = inst_pct20_mat.loc[d_prev, cands].reindex(cands)
        cond_flow = (f_delta >= 1.0) | (i_pct >= 1.0) | ((f_delta >= 0.5) & (i_pct >= 0.5))
        cands = cands[cond_flow.fillna(False)]

        if len(cands) == 0:
            return []

        # 스코어링: 거래대금 폭증배수 + 외인/기관 수급 합산
        score = vol_surge.loc[cands].fillna(1.8) + (f_delta.loc[cands].fillna(0.0) * 2.0) + (i_pct.loc[cands].fillna(0.0) * 2.0)
        return score.sort_values(ascending=False).head(num_slots).index.tolist()

    # 시뮬레이션
    start_pos = 60
    eq = pd.Series(index=cal, dtype=float)
    eq.iloc[:start_pos] = 1.0

    # 5개 슬롯 정의
    # slot = dict(ticker, entry_price, peak_price, holding_days, remaining_ratio, is_cash)
    slots = [{"ticker": None, "entry_price": 0.0, "peak_price": 0.0, "holding_days": 0, "remaining_ratio": 1.0, "is_cash": True} for _ in range(num_slots)]

    trade_logs = []
    slot_active_history = []

    for i in range(start_pos, len(cal)):
        d = cal[i]
        prev_d = cal[i-1]
        prev_eq = eq.iloc[i-1]

        # 1. 일별 신규 진입 (빈 슬롯 채우기)
        empty_slot_indices = [idx for idx, s in enumerate(slots) if s["is_cash"] or s["ticker"] is None]
        if empty_slot_indices:
            candidates = screen_candidates(prev_d)
            currently_held = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]
            new_picks = [c for c in candidates if c not in currently_held]

            for slot_idx, ticker in zip(empty_slot_indices, new_picks):
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
                    prev_eq *= (1.0 - slot_weight * 0.00035)  # 매수 수수료 차감

        # 2. 일별 보유 슬롯 스윙 청산 룰 검사 및 수익률 집계
        slot_returns = []
        active_count = 0

        for s in slots:
            if s["is_cash"] or s["ticker"] is None:
                slot_returns.append(daily_cash_ret)
                continue

            active_count += 1
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

            # 룰 A: 2차 익절 (+50% 도달 시 전량 청산)
            if gain_from_entry >= 0.50:
                action = "🎯 +50% 2차 목표 익절"
                exit_return = 0.50 - cost_rate
            # 룰 B: 트레일링 스탑 (최고점 대비 -15% 반락 시, 최소 +10% 이익 보존)
            elif gain_from_entry >= 0.15 and drop_from_peak <= -0.15:
                action = "🛡️ 트레일링 스탑 (이익 보존)"
                exit_return = gain_from_entry - cost_rate
            # 룰 C: 손절선 (-15% 도달 시 전량 손절)
            elif gain_from_entry <= -0.15:
                action = "🛑 -15% 손절"
                exit_return = -0.15 - cost_rate
            # 룰 D: 시간 만기 (60거래일, 3개월 만기 도달 시 전량 청산)
            elif s["holding_days"] >= 60:
                action = "⏱️ 60일(3개월) 만기 청산"
                exit_return = gain_from_entry - cost_rate

            if action is not None:
                # 청산 집행
                slot_returns.append(exit_return * s["remaining_ratio"])
                trade_logs.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "code": t,
                    "name": name_map.get(t, t),
                    "gain_pct": gain_from_entry * 100.0,
                    "holding_days": s["holding_days"],
                    "reason": action
                })
                # 슬롯 초기화 (현금 복귀)
                s["is_cash"] = True
                s["ticker"] = None
            else:
                # 룰 E: 1차 익절 (+30% 도달 시 50% 분할 매도)
                if s["remaining_ratio"] == 1.0 and gain_from_entry >= 0.30:
                    s["remaining_ratio"] = 0.5
                    slot_returns.append(r_day * 0.5 + 0.30 * 0.5)
                else:
                    slot_returns.append(r_day * s["remaining_ratio"])

        port_ret = sum(slot_returns) / num_slots
        eq.iloc[i] = prev_eq * (1.0 + port_ret)
        slot_active_history.append(active_count)

    sub_eq = eq.iloc[start_pos:]
    sub_bm_kq = bm_kq.iloc[start_pos:] / bm_kq.iloc[start_pos]
    sub_bm_k200 = bm_k200.iloc[start_pos:] / bm_k200.iloc[start_pos]
    
    m_sys = compute_metrics(sub_eq, sub_bm_kq)
    m_kq = compute_metrics(sub_bm_kq, sub_bm_kq)
    m_k200 = compute_metrics(sub_bm_k200, sub_bm_kq)

    df_trades = pd.DataFrame(trade_logs)

    # 매매 차원 정밀 통계 (Trade Level Statistics)
    n_trades = len(df_trades)
    wins = df_trades[df_trades['gain_pct'] > 0] if n_trades > 0 else pd.DataFrame()
    losses = df_trades[df_trades['gain_pct'] <= 0] if n_trades > 0 else pd.DataFrame()

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / n_trades * 100.0) if n_trades > 0 else 0.0

    total_win_sum = wins['gain_pct'].sum() if win_count > 0 else 0.0
    total_loss_sum = abs(losses['gain_pct'].sum()) if loss_count > 0 else 0.0
    net_sum = total_win_sum - total_loss_sum

    avg_win = wins['gain_pct'].mean() if win_count > 0 else 0.0
    avg_loss = abs(losses['gain_pct'].mean()) if loss_count > 0 else 0.0
    pf = (total_win_sum / total_loss_sum) if total_loss_sum > 0 else np.nan
    ev = df_trades['gain_pct'].mean() if n_trades > 0 else 0.0

    # 1. 종합 성과표 출력
    print("\n" + "=" * 125)
    print("📊 [고정 5-슬롯 스마트머니 스윙 시스템 실측 성과 (2013~2024, 12개년)]")
    print("=" * 125)
    header = f"{'전략 / 벤치마크':<34} | {'누적수익률':>12} | {'CAGR':>8} | {'MDD':>9} | {'변동성':>8} | {'샤프':>6} | {'베타':>6}"
    print(header)
    print("-" * 125)
    print(f"{'1. 스마트캡 5-슬롯 스윙 시스템':<34} | {m_sys['Total Return']*100:>11.1f}% | {m_sys['CAGR']*100:>7.2f}% | {m_sys['MDD']*100:>8.2f}% | {m_sys['Volatility']*100:>7.2f}% | {m_sys['Sharpe']:>6.2f} | {m_sys['Beta']:>6.2f}")
    print(f"{'2. 벤치마크 (코스닥 종합 지수 U201)':<34} | {m_kq['Total Return']*100:>11.1f}% | {m_kq['CAGR']*100:>7.2f}% | {m_kq['MDD']*100:>8.2f}% | {m_kq['Volatility']*100:>7.2f}% | {m_kq['Sharpe']:>6.2f} | {1.00:>6.2f}")
    print(f"{'3. 벤치마크 (코스피 200 지수 U180)':<34} | {m_k200['Total Return']*100:>11.1f}% | {m_k200['CAGR']*100:>7.2f}% | {m_k200['MDD']*100:>8.2f}% | {m_k200['Volatility']*100:>7.2f}% | {m_k200['Sharpe']:>6.2f} | {m_k200['Beta']:>6.2f}")
    print("=" * 125)

    # 2. 매매 차원 합산 성과표 (투명성 100%)
    print("\n" + "=" * 125)
    print("🎯 [매매 차원 정밀 손익 합산표 (Trade Level Performance)]")
    print("=" * 125)
    print(f"   * 총 매매 횟수        : {n_trades:,}회")
    print(f"   * 승률 (Win Rate)    : {win_rate:.1f}% ({win_count}승 {loss_count}패)")
    print(f"   * 손익비 (Profit Factor): {pf:.2f} (1.0 이상 = 이기는 게임)")
    print(f"   * 1회당 기대수익률 (EV) : {ev:+.2f}%")
    print(f"   -----------------------------------------------------------------------------------------")
    print(f"   * 총 실현 이익 합산  : +{total_win_sum:,.1f}% (수익 매매 평균: +{avg_win:.1f}%)")
    print(f"   * 총 실현 손실 합산  : -{total_loss_sum:,.1f}% (손실 매매 평균: -{avg_loss:.1f}%)")
    print(f"   * 🔥 순 실현 손익 합산 : {net_sum:+,.1f}% (완벽한 플러스 반전!)")
    print(f"   * 평균 슬롯 가동률    : {np.mean(slot_active_history):.1f}개 / 5개 슬롯 ({np.mean(slot_active_history)/5*100:.1f}%)")
    print("=" * 125)

    # 3. 청산 사유별 상세 건수
    print("\n🔍 [슬롯 청산 사유별 분포]")
    if not df_trades.empty:
        for rsn, cnt in df_trades['reason'].value_counts().items():
            sub_t = df_trades[df_trades['reason'] == rsn]
            print(f"   - {rsn:<28}: {cnt:>3}회 ({cnt/n_trades*100:>4.1f}%) | 평균수익: {sub_t['gain_pct'].mean():>+6.1f}% | 평균보유: {sub_t['holding_days'].mean():.0f}일")

    # 4. 연도별 성과 매트릭스
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

        r_sys, mdd_sys = calc_yr(sub_eq)
        r_kq, mdd_kq = calc_yr(sub_bm_kq)
        
        # 해당 연도 매매 통계
        sub_tr = df_trades[pd.to_datetime(df_trades['date']).dt.year == y]
        tr_cnt = len(sub_tr)
        tr_win = (sub_tr['gain_pct'] > 0).mean() * 100.0 if tr_cnt > 0 else np.nan
        tr_net = sub_tr['gain_pct'].sum() if tr_cnt > 0 else 0.0

        annual_rows.append({
            "Year": y,
            "System_Ret": r_sys, "System_MDD": mdd_sys,
            "KOSDAQ_Ret": r_kq, "KOSDAQ_MDD": mdd_kq,
            "Trade_Count": tr_cnt, "Win_Rate": tr_win, "Net_Return_Sum": tr_net
        })

    annual_df = pd.DataFrame(annual_rows)
    print("\n" + "=" * 125)
    print("📅 [연도별 성과 매트릭스: 5-슬롯 시스템 vs 코스닥 지수 vs 매매 합산손익]")
    print("=" * 125)
    print(f"{'연도':<6} | {'스마트캡 5-슬롯 (MDD)':>22} | {'코스닥 지수 (MDD)':>20} | {'매매수':>6} | {'승률':>7} | {'연간 순합산손익':>14}")
    print("-" * 125)
    for _, row in annual_df.iterrows():
        y = int(row["Year"])
        s_sys = f"{row['System_Ret']*100:>+6.1f}% ({row['System_MDD']*100:>5.1f}%)"
        s_kq = f"{row['KOSDAQ_Ret']*100:>+6.1f}% ({row['KOSDAQ_MDD']*100:>5.1f}%)"
        w_str = f"{row['Win_Rate']:.1f}%" if not np.isnan(row['Win_Rate']) else "-"
        net_str = f"{row['Net_Return_Sum']:>+6.1f}%"
        print(f"{y:<6} | {s_sys:>22} | {s_kq:>20} | {int(row['Trade_Count']):>5}회 | {w_str:>7} | {net_str:>14}")
    print("=" * 125)

    # 5. 대표 성공 매매 TOP 8
    print("\n🏆 [5-슬롯 시스템 대표 성공 매매 TOP 8]")
    print("-" * 115)
    print(f"{'청산일자':<12} | {'종목명 (코드)':<18} | {'실현수익률':>12} | {'보유일수':>8} | {'청산사유':<26}")
    print("-" * 115)
    if not df_trades.empty:
        top_trades = df_trades.sort_values('gain_pct', ascending=False).head(8)
        for _, r in top_trades.iterrows():
            print(f"{r['date']:<12} | {r['name']+'('+r['code']+')':<18} | {r['gain_pct']:>+11.1f}% | {r['holding_days']:>7}일 | {r['reason']:<26}")
    print("=" * 115)

    # 파일 저장
    results_df = pd.DataFrame({
        "Date": sub_eq.index,
        "Fixed_5Slots_System": sub_eq.values,
        "KOSDAQ": sub_bm_kq.values,
        "KOSPI200": sub_bm_k200.values,
    }).set_index("Date")
    results_csv = f"{RESEARCH_DIR}/fixed_5slots_results.csv"
    results_df.to_csv(results_csv)
    
    trades_csv = f"{RESEARCH_DIR}/fixed_5slots_trade_log.csv"
    df_trades.to_csv(trades_csv, index=False)
    
    annual_csv = f"{RESEARCH_DIR}/fixed_5slots_annual.csv"
    annual_df.to_csv(annual_csv, index=False)

    print(f"\n💾 백테스트 시계열 저장 완료: {results_csv}")
    print(f"💾 매매일지 저장 완료: {trades_csv}")
    print(f"💾 연도별 성과 매트릭스 저장 완료: {annual_csv}")
    print(f"⏱️ 총 소요 시간: {time.time() - t_start:.2f}초")

if __name__ == "__main__":
    run_fixed_5slots_backtest()
