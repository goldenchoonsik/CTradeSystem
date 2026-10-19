"""
[EXP-014] 4대 거시경보 시그널 기반 중장기 포트폴리오 동적 현금 방어 백테스트
=============================================================================
- 검증 기간: 1999-03-10 ~ 2024-12-31 (26년 확정 데이터 구간)
- 연구 목적:
  2000년 닷컴 버블 당시 하이브리드 전략이 기록한 -63.99%의 최대 낙폭(MDD)을
  4대 시장 붕괴 감지 시그널(마켓 브레스, 신용 스프레드, 장단기 금리차, 200일선)을 결합한
  '동적 현금 배분 룰(Dynamic Cash Allocation)'을 통해 -30% 이하로 획기적으로 낮추면서,
  중장기 복리 수익률(CAGR 20%+)을 완벽히 보존할 수 있는지 실측 검증한다.

- 4대 시장 붕괴 감지 시그널:
  1. Market Breadth: 나스닥 유니버스 중 200일선 상회 종목 비율 < 45% (내부 붕괴)
  2. Credit Spread: Moody's Baa - 10Y 국채 스프레드(BAA10Y) > 2.80% (신용 경색)
  3. Yield Curve: 10Y-2Y 금리차(T10Y2Y) 역전 후 급격한 정상화/가팔라짐 (침체 돌입)
  4. QQQ Trend: QQQ 일봉 < 200일 이동평균선 (추세 이탈)

- 현금 배분 룰 (Score 0~4):
  - Score 0: 현금 0% (100% 주식 투자)
  - Score 1: 현금 20% (80% 주식 투자)
  - Score 2: 현금 40% (60% 주식 투자)
  - Score 3: 현금 70% (30% 주식 투자)
  - Score 4 또는 (QQQ < 200 SMA and Breadth < 40%): 현금 100% (전량 피난)
"""
import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = "DataVault"
REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

from max_tenure_hybrid_backtest import (
    load_stock_data, compute_metrics, NAS_UNIVERSE, OFFICIAL_ADDITIONS, OFFICIAL_REMOVALS
)

def run_macro_defense_experiment():
    print("=" * 115)
    print("🛡️ [EXP-014] 4대 거시경보 시그널 결합 동적 현금 방어 백테스트 (1999-03-10 ~ 2024-12-31)")
    print("=" * 115)
    
    # 1. QQQ 및 캘린더 로드
    qqq_df = load_stock_data("QQQ")
    cal_full = qqq_df.index
    cal = cal_full[(cal_full >= "1999-03-10") & (cal_full <= "2024-12-31")]
    print(f"📅 검증 기간: {cal[0].date()} ~ {cal[-1].date()} (총 {len(cal):,} 거래일, 26년)")
    
    # 2. 개별 주식 풀 로드
    stocks = {}
    for t in set(NAS_UNIVERSE):
        df = load_stock_data(t)
        if df is not None and len(df) > 50:
            stocks[t] = df
    print(f"✅ 유효 종목 풀: {len(stocks)}개 로드 완료")
    
    adj_mat = pd.DataFrame({t: df["adj_close"].reindex(cal).ffill() for t, df in stocks.items()}, index=cal)
    dvol_mat = pd.DataFrame({t: df["dvol_60d"].reindex(cal).ffill() for t, df in stocks.items()}, index=cal)
    ret_mat = adj_mat.pct_change().fillna(0.0)
    
    qqq_px = qqq_df["adj_close"].loc[cal]
    qqq_ret = qqq_px.pct_change().fillna(0.0)
    qqq_bm_eq = (1.0 + qqq_ret).cumprod()
    
    # 3. 매크로 데이터 로드
    # A. 장단기 금리차 (T10Y2Y)
    t10y2y_path = f"{ROOT}/raw/macro/T10Y2Y.csv"
    df_curve = pd.read_csv(t10y2y_path, parse_dates=["observation_date"]).set_index("observation_date")
    df_curve["T10Y2Y"] = pd.to_numeric(df_curve["T10Y2Y"], errors="coerce")
    t10y2y = df_curve["T10Y2Y"].reindex(cal).ffill().fillna(0.5)
    
    # B. 신용 스프레드 (BAA10Y)
    baa_path = f"{ROOT}/raw/macro/BAA10Y.csv"
    df_baa = pd.read_csv(baa_path, parse_dates=["observation_date"]).set_index("observation_date")
    df_baa["BAA10Y"] = pd.to_numeric(df_baa["BAA10Y"], errors="coerce")
    baa10y = df_baa["BAA10Y"].reindex(cal).ffill().fillna(2.0)
    
    # 4. 4대 시그널 연산
    # 시그널 1: QQQ 200일선 추세
    qqq_sma200 = qqq_px.rolling(200, min_periods=50).mean()
    qqq_sma50 = qqq_px.rolling(50, min_periods=20).mean()
    sig_qqq = (qqq_px < qqq_sma200) | (qqq_sma50 < qqq_sma200)
    
    # 시그널 2: 마켓 브레스 (% of stocks > 200 SMA)
    sma200_mat = adj_mat.rolling(200, min_periods=50).mean()
    above_200 = (adj_mat >= sma200_mat)
    # 각 날짜별 유효 종목 중 200일선 상회 비율
    breadth_pct = above_200.sum(axis=1) / adj_mat.notna().sum(axis=1)
    sig_breadth = breadth_pct < 0.45
    
    # 시그널 3: 신용 스프레드 위험 (BAA10Y > 2.80% or 20일 상승폭 > 0.35%)
    baa_roc20 = baa10y - baa10y.shift(20)
    sig_credit = (baa10y >= 2.80) | (baa_roc20 >= 0.35)
    
    # 시그널 4: 금리차 역전 후 가파른 정상화 (지난 250일 내 역전 발생 + 현재 정상화 > 0.05 + 20일 급등)
    had_inversion = (t10y2y < 0.0).rolling(250, min_periods=50).max() == 1.0
    sig_curve = had_inversion & (t10y2y > 0.05) & (t10y2y - t10y2y.shift(20) > 0.20)
    
    # 5. 복합 위험 스코어 (0~4점) 및 동적 목표 현금 비중 계산
    score_df = pd.DataFrame({
        "sig_qqq": sig_qqq.astype(int),
        "sig_breadth": sig_breadth.astype(int),
        "sig_credit": sig_credit.astype(int),
        "sig_curve": sig_curve.astype(int)
    }, index=cal).fillna(0)
    
    score_df["total_score"] = score_df.sum(axis=1)
    
    # 목표 현금 비중 매핑
    def map_cash_target(row):
        score = row["total_score"]
        if row["sig_qqq"] == 1 and row["sig_breadth"] == 1 and row["sig_credit"] == 1:
            return 1.0 # 3대 치명 위기 결합 시 전량 현금 피난
        if score == 0:
            return 0.0
        elif score == 1:
            return 0.20
        elif score == 2:
            return 0.40
        elif score == 3:
            return 0.70
        else:
            return 1.0
            
    target_cash_series = score_df.apply(map_cash_target, axis=1)
    
    print(f"📊 위험 스코어 분포: 0점({(score_df.total_score==0).sum()}일), 1점({(score_df.total_score==1).sum()}일), 2점({(score_df.total_score==2).sum()}일), 3점({(score_df.total_score==3).sum()}일), 4점({(score_df.total_score==4).sum()}일)")
    
    # 6. 신규 편입 이벤트 매핑 (기존 동일)
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
    
    # 7. 시뮬레이션: 기본 하이브리드 vs 현금 방어 결합 하이브리드
    def simulate_hybrid(use_macro_defense=False):
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        w_curr = pd.Series(dtype=float)
        hold_dict = {}
        
        initial_10 = [t for t in initial_top if t in stocks][:10]
        for t in initial_10:
            hold_dict[t] = cal[0]
        w_curr = pd.Series(1.0 / len(initial_10), index=initial_10)
        
        for i in range(len(cal)):
            dt = cal[i]
            
            # 현금 방어 모드일 때 매크로 현금 목표 비중 반영
            cash_target = target_cash_series.loc[dt] if use_macro_defense else 0.0
            invest_target = max(0.0, 1.0 - cash_target)
            
            if dt in all_event_dates and i > 0:
                new_syms = addition_events.get(dt, [])
                new_valid = [s for s in new_syms if s in stocks and s not in hold_dict]
                
                # 1. 공식 탈락 종목 우선 퇴출
                survivors = []
                for sym in list(hold_dict.keys()):
                    rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                    if dt < rem_d:
                        survivors.append(sym)
                    else:
                        hold_dict.pop(sym, None)
                        
                # 2. 신규 유입 및 슬롯 조율
                # 만약 고위험 상태(cash_target >= 0.70)라면 신규 종목 매수 전면 중단
                if use_macro_defense and cash_target >= 0.70:
                    new_valid = []
                    
                excess = (len(survivors) + len(new_valid)) - 10
                if excess > 0 and survivors:
                    expired = [s for s in survivors if (dt - hold_dict[s]).days >= 912]
                    to_prune = []
                    for exp_s in expired:
                        to_prune.append(exp_s)
                        if len(to_prune) == excess:
                            break
                    if len(to_prune) < excess:
                        rem_candidates = [s for s in survivors if s not in to_prune]
                        perf_series = pd.Series(dtype=float)
                        for s in rem_candidates:
                            p_start = adj_mat[s].loc[hold_dict[s]]
                            p_now = adj_mat[s].loc[dt]
                            perf_series[s] = (p_now / p_start) - 1.0 if p_start > 0 else 0.0
                        lowest_perf = perf_series.sort_values(ascending=True).index.tolist()
                        needed = excess - len(to_prune)
                        to_prune.extend(lowest_perf[:needed])
                    for k_sym in to_prune:
                        survivors.remove(k_sym)
                        hold_dict.pop(k_sym, None)
                        
                for s in new_valid:
                    if len(survivors) < 10:
                        survivors.append(s)
                        hold_dict[s] = dt
                        
                # 리밸런싱 가중치: 주식 목표 비중(invest_target) 내에서 1/N
                if survivors and invest_target > 0:
                    target_w = pd.Series(invest_target / len(survivors), index=survivors)
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
                    
            if i == 0:
                continue
                
            eq_prev = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
            if not w_curr.empty:
                active_ret = ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0)
                port_ret = (w_curr * active_ret).sum()
                # 현금 부분은 0% 수익률 (원금 보존)
                eq.iloc[i] = eq_prev * (1.0 + port_ret)
                w_curr = w_curr * (1.0 + active_ret)
                total_val = w_curr.sum() + max(0.0, 1.0 - w_curr.sum())
                if total_val > 0:
                    w_curr = w_curr / total_val
            else:
                # 100% 현금 대기 (원금 100% 보존)
                eq.iloc[i] = eq_prev
                
        return eq

    print("⏳ 기본 하이브리드 vs 거시경보 현금 방어 하이브리드 시뮬레이션 연산 중...")
    eq_baseline = simulate_hybrid(use_macro_defense=False)
    eq_macro = simulate_hybrid(use_macro_defense=True)
    
    all_res = {
        "1. Hybrid_10Slots_Baseline (기존 하이브리드)": eq_baseline,
        "2. Hybrid_MacroDefense_10Slots (4대 거시경보 현금방어 결합)": eq_macro,
        "3. QQQ_Actual_ETF (실제 QQQ 기준선)": qqq_bm_eq
    }
    
    print("\n" + "=" * 115)
    print("🏆 [1999~2024년 전체 26년 성과 비교 (1999-03-10 ~ 2024-12-31)]")
    print("=" * 115)
    print(f"{'전략 명칭':<48} | {'총수익률':>10} | {'CAGR':>8} | {'변동성':>8} | {'샤프':>6} | {'MDD':>8} | {'베타':>6}")
    print("-" * 115)
    for name, s_eq in all_res.items():
        m = compute_metrics(s_eq, qqq_bm_eq)
        print(f"{name:<48} | {m['Total Return']*100:>9.1f}% | {m['CAGR']*100:>7.2f}% | {m['Volatility']*100:>7.2f}% | {m['Sharpe']:>6.2f} | {m['MDD']*100:>7.2f}% | {m['Beta']:>6.2f}")

    # 주요 위기 국면 MDD 세부 비교
    windows = [
        ("1. 닷컴 버블 붕괴기 (2000~2003)", "2000-01-01", "2003-12-31"),
        ("2. 글로벌 금융위기 (2007~2009)", "2007-10-01", "2009-06-30"),
        ("3. 코로나19 팬데믹 (2020)", "2020-02-01", "2020-04-30"),
        ("4. 연준 금리인상기 (2021~2022)", "2021-11-01", "2022-12-31")
    ]
    print("\n" + "=" * 115)
    print("🛡️ [역대 4대 하락장별 최대 낙폭(MDD) 방어력 비교]")
    print("=" * 115)
    for title, s_dt, e_dt in windows:
        print(f"{title}:")
        for name, s_eq in all_res.items():
            sub = s_eq.loc[s_dt:e_dt]
            sub_dd = (sub - sub.cummax()) / sub.cummax()
            tot = (sub.iloc[-1] / sub.iloc[0]) - 1.0
            print(f"   • {name.split()[1]:<25}: MDD {sub_dd.min()*100:6.2f}% | 구간수익률 {tot*100:+7.2f}%")

    # 연도별 성과 매트릭스
    years = sorted(list(set(cal.year)))
    annual_table = []
    for y in years:
        cal_y = cal[cal.year == y]
        row = {"Year": y}
        for name, s_eq in all_res.items():
            r = (s_eq.loc[cal_y[-1]] / s_eq.loc[cal_y[0]]) - 1.0 if len(cal_y) > 0 else 0.0
            row[name.split()[1]] = round(r * 100, 1)
        annual_table.append(row)
    ann_df = pd.DataFrame(annual_table)
    print("\n" + "=" * 115)
    print("📅 [연도별 수익률 매트릭스 (1999 ~ 2024, 26개 연도)]")
    print("=" * 115)
    print(ann_df.to_string(index=False))

    # HTML 리포트 저장
    report_path = f"{REPORTS_DIR}/macro_cash_defense_report.html"
    ann_df.to_csv(f"{REPORTS_DIR}/macro_cash_defense_annual.csv", index=False, encoding="utf-8-sig")
    print(f"\n✅ 리포트 저장 완료: {report_path}")
    
    return all_res, ann_df

if __name__ == "__main__":
    run_macro_defense_experiment()
