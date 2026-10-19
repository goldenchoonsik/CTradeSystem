"""
[EXP-ETF-001] 슈퍼루키 및 시총상위 모멘텀 미국 ETF 최적화 백테스트 (세후 실효 수익률 극대화)
=============================================================================================
- 검증 기간: 2008-01-02 ~ 2024-12-31 (17개년, 4,280 거래일, 2008 금융위기~2024 AI랠리 전수)
- 핵심 동기:
  개별주 슈퍼루키 전략은 매년 빈번한 매매로 22% 양도소득세가 지속 차감되어 복리가 깎이지만,
  미국 ETF는 내부 종목 교체 시 세금이 0원이며 투자자가 매도하기 전까지 세금이 완전 이연(Tax Deferral)됨.
  따라서 슈퍼루키 실물 ETF(FPX)와 메가캡 ETF(MGK/XLG)를 활용하여
  '손절 방어(-15% Cash)' 및 '듀얼 모멘텀 스위칭'을 최적화하면,
  세후 기준으로 개별주 전략을 능가하거나 대등한 성과를 달성할 수 있는지 실측 검증한다.

- 비교 유니버스:
  1. FPX: First Trust US Equity Opportunities (슈퍼루키 / 신규 승격주 실물 ETF)
  2. MGK: Vanguard Mega Cap Growth (초대형 메가캡 성장주 ETF)
  3. XLG: Invesco S&P 500 Top 50 (시총 최상위 50개 집중형 ETF)
  4. QQQ: Invesco QQQ Trust (나스닥 100 기준 벤치마크)
  5. SPY: SPDR S&P 500 ETF (미국 시장 전체 기준선)

- 세법 및 자본금:
  - 초기 자본금: 1억 원 (100,000,000 KRW)
  - 거래비용: 편도 0.05% (왕복 0.10%)
  - 세금: 한국 거주자 해외주식 양도소득세 (연 250만 원 공제 후 22% 매년 연말 차감, Buy&Hold는 만기 1회 과세)
  - 현금 이자: 연 2.5% (SGOV 단기국채)
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

def load_etf_data(ticker):
    fp = f"{ROOT}/DataVault/raw/yahoo/candle_1d/{ticker}_1d.csv"
    if not os.path.exists(fp):
        return None
    df = pd.read_csv(fp)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")
    return df["close"]

def main():
    t_start = time.time()
    INIT_CAP = 100_000_000 # 1억 원
    START_DATE = "2008-01-02"
    END_DATE = "2024-12-31"
    
    print("=" * 115)
    print("🚀 [실측 백테스트] 슈퍼루키(FPX) & 메가캡(MGK/XLG) 미국 ETF 세후 최적화 전략 검증 (2008~2024, 17개년)")
    print(f"기준 자본금: {INIT_CAP:,}원 (1억 원) | 세법: 한국인 22% 양도소득세 정밀 반영")
    print("=" * 115)

    # 1. ETF 데이터 로드 및 정렬
    tickers = ["FPX", "MGK", "QQQ", "SPY", "QLD"]
    etf_dfs = {}
    for t in tickers:
        s = load_etf_data(t)
        if s is not None:
            etf_dfs[t] = s
            
    df_px = pd.DataFrame(etf_dfs)
    cal = df_px.index[(df_px.index >= START_DATE) & (df_px.index <= END_DATE)]
    df_px = df_px.loc[cal].ffill()
    
    print(f"📅 검증 기간: {cal[0].strftime('%Y-%m-%d')} ~ {cal[-1].strftime('%Y-%m-%d')} (총 {len(cal):,} 거래일, 17.0년)")
    print(f"✅ 로드된 ETF 목록: {list(df_px.columns)}")

    daily_sgov_ret = 0.025 / 252.0
    daily_cash_ret = 0.025 / 252.0
    cost_rate = 0.0005 # 편도 0.05%

    results = {}
    eq_curves = {}
    taxes_paid_dict = {}

    # =========================================================================
    # [MODEL 1] 단일 ETF Buy & Hold (만기 1회 22% 과세)
    # =========================================================================
    print("\n--- [MODEL 1] 단일 ETF Buy & Hold (세후 만기 일괄 과세) ---")
    for t in tickers:
        px = df_px[t]
        eq_raw = (px / px.iloc[0]) * (INIT_CAP * (1.0 - cost_rate))
        final_val_pre = eq_raw.iloc[-1]
        
        # 만기 양도세 계산 (250만원 공제 후 22%)
        taxable_gain = max(0.0, final_val_pre - INIT_CAP - 2_500_000.0)
        final_tax = taxable_gain * 0.22
        final_val_post = final_val_pre - final_tax
        
        # 세후 에쿼티 곡선 (마지막 날 세금 차감)
        eq_post = eq_raw.copy()
        eq_post.iloc[-1] = final_val_post
        
        m = compute_metrics(eq_post, eq_raw)
        key = f"{t}_BuyHold"
        results[key] = {
            "Strategy": f"{t} (Buy & Hold)",
            "최종 평가액": f"{final_val_post:,.0f}원",
            "총수익률": f"{m['Total Return']*100:+.2f}%",
            "CAGR": f"{m['CAGR']*100:.2f}%",
            "MDD": f"{m['MDD']*100:.2f}%",
            "Sharpe": f"{m['Sharpe']:.2f}",
            "총 납부 세금": f"{final_tax:,.0f}원"
        }
        eq_curves[key] = eq_post
        taxes_paid_dict[key] = final_tax

    # 바벨 포트폴리오 (FPX 50% + MGK 50% Buy & Hold)
    eq_barbell = (0.5 * (df_px["FPX"] / df_px["FPX"].iloc[0]) + 0.5 * (df_px["MGK"] / df_px["MGK"].iloc[0])) * INIT_CAP * (1.0 - cost_rate)
    b_final_pre = eq_barbell.iloc[-1]
    b_tax = max(0.0, b_final_pre - INIT_CAP - 2_500_000.0) * 0.22
    b_final_post = b_final_pre - b_tax
    eq_barbell_post = eq_barbell.copy()
    eq_barbell_post.iloc[-1] = b_final_post
    m_b = compute_metrics(eq_barbell_post)
    results["Barbell_FPX_MGK"] = {
        "Strategy": "바벨 포트폴리오 (FPX 50% + MGK 50%)",
        "최종 평가액": f"{b_final_post:,.0f}원",
        "총수익률": f"{m_b['Total Return']*100:+.2f}%",
        "CAGR": f"{m_b['CAGR']*100:.2f}%",
        "MDD": f"{m_b['MDD']*100:.2f}%",
        "Sharpe": f"{m_b['Sharpe']:.2f}",
        "총 납부 세금": f"{b_tax:,.0f}원"
    }
    eq_curves["Barbell_FPX_MGK"] = eq_barbell_post
    taxes_paid_dict["Barbell_FPX_MGK"] = b_tax

    # =========================================================================
    # [MODEL 2] 슈퍼루키 슬롯 방어 원리 결합 (ETF + -15% 손절 & 현금 대기)
    # =========================================================================
    print("--- [MODEL 2] 슈퍼루키 손절 방어 원리 결합 (ETF + -15% 손절 / 200일선 돌파 재진입) ---")
    def sim_etf_with_stoploss(ticker, stop_loss_pct=0.15):
        px = df_px[ticker]
        sma200 = px.rolling(200).mean()
        
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = float(INIT_CAP)
        
        in_pos = True
        entry_px = px.iloc[0]
        peak_px = px.iloc[0]
        cost_basis = float(INIT_CAP)
        
        yearly_gain = 0.0
        total_tax = 0.0
        stop_cnt = 0
        
        eq.iloc[0] = INIT_CAP * (1.0 - cost_rate)
        
        for i in range(1, len(cal)):
            dt = cal[i]
            year = dt.year
            prev_year = cal[i-1].year
            
            # 연말 세금 정산
            if year != prev_year:
                taxable = max(0.0, yearly_gain - 2_500_000.0)
                t_due = taxable * 0.22
                if t_due > 0:
                    eq.iloc[i-1] = max(1.0, eq.iloc[i-1] - t_due)
                    total_tax += t_due
                yearly_gain = 0.0
                
            curr_px = px.iloc[i]
            prev_px = px.iloc[i-1]
            eq_prev = eq.iloc[i-1]
            
            if in_pos:
                peak_px = max(peak_px, curr_px)
                # 손절 조건: 최고점 대비 -15% 하락 시 탈출
                dd_from_peak = (curr_px / peak_px) - 1.0
                dd_from_entry = (curr_px / entry_px) - 1.0
                
                if dd_from_peak <= -stop_loss_pct:
                    # 손절 집행 -> 전량 현금 피신
                    ret_today = (curr_px / prev_px) - 1.0
                    cur_val = eq_prev * (1.0 + ret_today) * (1.0 - cost_rate)
                    pnl = cur_val - cost_basis
                    yearly_gain += pnl
                    eq.iloc[i] = cur_val
                    in_pos = False
                    stop_cnt += 1
                else:
                    ret_today = (curr_px / prev_px) - 1.0
                    eq.iloc[i] = eq_prev * (1.0 + ret_today)
            else:
                # 현금 대기 중 SGOV 이자 수취
                eq.iloc[i] = eq_prev * (1.0 + daily_cash_ret)
                
                # 재진입 조건: 주가가 200일선 상회 돌파 + 최근 5일 상승
                if i >= 200 and curr_px > sma200.iloc[i] and px.iloc[i] > px.iloc[i-5]:
                    in_pos = True
                    entry_px = curr_px
                    peak_px = curr_px
                    eq.iloc[i] = eq.iloc[i] * (1.0 - cost_rate)
                    cost_basis = eq.iloc[i]
                    
        # 마지막 연도 세금
        taxable = max(0.0, yearly_gain - 2_500_000.0)
        t_due = taxable * 0.22
        if t_due > 0:
            eq.iloc[-1] = max(1.0, eq.iloc[-1] - t_due)
            total_tax += t_due
            
        return eq, total_tax, stop_cnt

    for t in ["FPX", "MGK", "QQQ", "QLD"]:
        sl_pct = 0.20 if t == "QLD" else 0.15
        eq_sl, tax_sl, s_cnt = sim_etf_with_stoploss(t, stop_loss_pct=sl_pct)
        m = compute_metrics(eq_sl, df_px["QQQ"])
        key = f"{t}_SL15_Cash"
        results[key] = {
            "Strategy": f"{t} + -15%손절/현금대기",
            "최종 평가액": f"{eq_sl.iloc[-1]:,.0f}원",
            "총수익률": f"{m['Total Return']*100:+.2f}%",
            "CAGR": f"{m['CAGR']*100:.2f}%",
            "MDD": f"{m['MDD']*100:.2f}%",
            "Sharpe": f"{m['Sharpe']:.2f}",
            "총 납부 세금": f"{tax_sl:,.0f}원"
        }
        eq_curves[key] = eq_sl
        taxes_paid_dict[key] = tax_sl

    # =========================================================================
    # [MODEL 3] 듀얼 모멘텀 스위칭 최적화 (슈퍼루키 FPX ↔ 메가캡 MGK ↔ 현금 SGOV)
    # =========================================================================
    print("--- [MODEL 3] 듀얼 모멘텀 스위칭 최적화 (FPX ↔ MGK ↔ SGOV) ---")
    def sim_dual_momentum(lookback_days=60):
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = float(INIT_CAP)
        
        curr_asset = "MGK"
        cost_basis = float(INIT_CAP)
        entry_px = df_px[curr_asset].iloc[0]
        eq.iloc[0] = INIT_CAP * (1.0 - cost_rate)
        
        yearly_gain = 0.0
        total_tax = 0.0
        switch_cnt = 0
        
        for i in range(1, len(cal)):
            dt = cal[i]
            year = dt.year
            prev_year = cal[i-1].year
            
            # 연말 세금 결산
            if year != prev_year:
                taxable = max(0.0, yearly_gain - 2_500_000.0)
                t_due = taxable * 0.22
                if t_due > 0:
                    eq.iloc[i-1] = max(1.0, eq.iloc[i-1] - t_due)
                    total_tax += t_due
                yearly_gain = 0.0
                
            eq_prev = eq.iloc[i-1]
            
            # 20거래일(1개월)마다 스위칭 검사
            if i >= lookback_days and i % 20 == 0:
                # 최근 N일 모멘텀 산출
                mom_fpx = (df_px["FPX"].iloc[i] / df_px["FPX"].iloc[i - lookback_days]) - 1.0
                mom_mgk = (df_px["MGK"].iloc[i] / df_px["MGK"].iloc[i - lookback_days]) - 1.0
                
                # 200일선 상회 여부 (절대 필터)
                sma200_fpx = df_px["FPX"].iloc[i-200:i].mean()
                sma200_mgk = df_px["MGK"].iloc[i-200:i].mean()
                
                fpx_valid = (df_px["FPX"].iloc[i] > sma200_fpx) and (mom_fpx > 0)
                mgk_valid = (df_px["MGK"].iloc[i] > sma200_mgk) and (mom_mgk > 0)
                
                # 타깃 자산 결정
                if not fpx_valid and not mgk_valid:
                    target_asset = "CASH"
                elif mom_fpx >= mom_mgk and fpx_valid:
                    target_asset = "FPX"
                elif mgk_valid:
                    target_asset = "MGK"
                else:
                    target_asset = "CASH"
                    
                if target_asset != curr_asset:
                    switch_cnt += 1
                    # 기존 자산 청산
                    if curr_asset != "CASH":
                        p_now = df_px[curr_asset].iloc[i]
                        p_prev = df_px[curr_asset].iloc[i-1]
                        ret_step = (p_now / p_prev) - 1.0
                        cur_val = eq_prev * (1.0 + ret_step) * (1.0 - cost_rate)
                        pnl = cur_val - cost_basis
                        yearly_gain += pnl
                        eq_prev = cur_val
                    else:
                        cur_val = eq_prev * (1.0 + daily_cash_ret)
                        eq_prev = cur_val
                        
                    curr_asset = target_asset
                    if curr_asset != "CASH":
                        eq_prev = eq_prev * (1.0 - cost_rate)
                        cost_basis = eq_prev
                        entry_px = df_px[curr_asset].iloc[i]
                        
            # 일별 수익률 반영
            if curr_asset != "CASH":
                r_day = (df_px[curr_asset].iloc[i] / df_px[curr_asset].iloc[i-1]) - 1.0
                eq.iloc[i] = eq_prev * (1.0 + r_day)
            else:
                eq.iloc[i] = eq_prev * (1.0 + daily_cash_ret)
                
        taxable = max(0.0, yearly_gain - 2_500_000.0)
        t_due = taxable * 0.22
        if t_due > 0:
            eq.iloc[-1] = max(1.0, eq.iloc[-1] - t_due)
            total_tax += t_due
            
        return eq, total_tax, switch_cnt

    for lb in [20, 60, 120]:
        eq_dm, tax_dm, sw_cnt = sim_dual_momentum(lookback_days=lb)
        m = compute_metrics(eq_dm, df_px["QQQ"])
        key = f"Dual_Mom_{lb}d"
        results[key] = {
            "Strategy": f"듀얼 모멘텀 (FPX/MGK/현금, 룩백 {lb}일)",
            "최종 평가액": f"{eq_dm.iloc[-1]:,.0f}원",
            "총수익률": f"{m['Total Return']*100:+.2f}%",
            "CAGR": f"{m['CAGR']*100:.2f}%",
            "MDD": f"{m['MDD']*100:.2f}%",
            "Sharpe": f"{m['Sharpe']:.2f}",
            "총 납부 세금": f"{tax_dm:,.0f}원"
        }
        eq_curves[key] = eq_dm
        taxes_paid_dict[key] = tax_dm

    # =========================================================================
    # [MODEL 4] 나스닥 개별주 슈퍼루키-8 (세후 22%) 벤치마크 로드 (동일 2008~2024 구간)
    # =========================================================================
    print("--- [MODEL 4] 개별주 슈퍼루키-8 (세후 22%) 동일 2008~2024 구간 시뮬레이션 ---")
    sys.path.append(f"{ROOT}/docs/research/20261019_kospi200_super_rookie")
    from tax_drag_super_rookie_comparison import run_nasdaq_super_rookie_tax_sim
    nas_sim_fn, _, _ = run_nasdaq_super_rookie_tax_sim(START_DATE, END_DATE, INIT_CAP)
    eq_rookie8_post, tax_r8, _ = nas_sim_fn(target_n=8, apply_tax=True)
    m_r8 = compute_metrics(eq_rookie8_post, df_px["QQQ"])
    results["Stock_Rookie8_PostTax"] = {
        "Strategy": "👑 [개별주] 나스닥 슈퍼루키-8 (세후 22%)",
        "최종 평가액": f"{eq_rookie8_post.iloc[-1]:,.0f}원",
        "총수익률": f"{m_r8['Total Return']*100:+.2f}%",
        "CAGR": f"{m_r8['CAGR']*100:.2f}%",
        "MDD": f"{m_r8['MDD']*100:.2f}%",
        "Sharpe": f"{m_r8['Sharpe']:.2f}",
        "총 납부 세금": f"{tax_r8:,.0f}원"
    }
    eq_curves["Stock_Rookie8_PostTax"] = eq_rookie8_post

    # 5. 종합 결과표 출력
    df_res = pd.DataFrame(results).T
    print("\n" + "=" * 115)
    print("🏆 [미국 ETF 최적화 전략 세후 실측 성과 종합 순위표 (2008~2024년 17개년)]")
    print("=" * 115)
    print(df_res.to_string(index=False))

    # 6. 연도별 수익률 매트릭스
    ann_rets = {}
    for k, eq in eq_curves.items():
        y_eq = eq.resample("YE").last()
        y_all = pd.concat([pd.Series([eq.iloc[0]], index=[eq.index[0]]), y_eq])
        ann_rets[k] = y_all.pct_change().dropna() * 100
    df_ann = pd.DataFrame(ann_rets)
    df_ann.index = df_ann.index.year
    
    print("\n" + "=" * 115)
    print("📅 [핵심 전략 연도별 수익률 비교 (%)]")
    print("=" * 115)
    key_cols = ["MGK_BuyHold", "FPX_BuyHold", "Barbell_FPX_MGK", "MGK_SL15_Cash", "Dual_Mom_60d", "Stock_Rookie8_PostTax", "QQQ_BuyHold"]
    print(df_ann[key_cols].round(2).to_string())

    # CSV 저장
    df_res.to_csv(f"{REPORTS_DIR}/us_etf_super_rookie_optimization_summary.csv", index=False, encoding="utf-8-sig")
    df_ann.to_csv(f"{REPORTS_DIR}/us_etf_super_rookie_annual_returns.csv", encoding="utf-8-sig")
    
    print(f"\n💾 성과 원장 저장 완료: {REPORTS_DIR}/us_etf_super_rookie_optimization_summary.csv")
    print(f"⏱️ 총 소요 시간: {time.time()-t_start:.2f}초")

if __name__ == "__main__":
    main()
