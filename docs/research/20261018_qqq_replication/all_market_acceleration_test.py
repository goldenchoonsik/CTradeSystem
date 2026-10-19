"""
[EXP-019] 전 종목(All-Market, 12,000+ 종목) 대상 시총 가속도 TOP 10 발굴 및 백테스트
========================================================================================
- 대상 유니버스: DataVault 내 12,896개 미국 상장 전 종목
- 최소 체결 조건 (현실적 필터):
  - 주가 >= $3.0
  - 20일 평균 일 거래대금 >= $3,000,000 ($3M, 약 40억 원)
- 시총 가속도:
  - Dollar Volume D_t (20일 평균 거래대금)
  - v1 = D_t / D_{t-21} - 1.0 (최근 1개월 증가율)
  - v0 = D_{t-21} / D_{t-42} - 1.0 (직전 1개월 증가율)
  - a = v1 - v0 (가속도)
- 비교군:
  1. 전 종목 시총 가속도 TOP 10 (풀투자)
  2. 나스닥 100 시총 가속도 TOP 10 (풀투자)
  3. QQQ ETF
  4. SPY ETF
"""
import os
import sys
import glob
import time
import numpy as np
import pandas as pd
from multiprocessing import Pool, cpu_count

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)
CACHE_FILE = f"{REPORTS_DIR}/all_stocks_monthly_summary.pkl"

def process_single_stock(fp):
    ticker = os.path.basename(fp).replace("_1d.csv", "")
    try:
        df = pd.read_csv(fp, usecols=["timestamp", "close", "volume"])
        df["date"] = pd.to_datetime(df["timestamp"])
        df = df.dropna().sort_values("date")
        if len(df) < 60:
            return None
        
        df["dvol"] = df["close"] * df["volume"]
        df["dvol_20d"] = df["dvol"].rolling(20, min_periods=10).mean()
        
        # 월별 마지막 거래일 추출
        df["year_month"] = df["date"].dt.to_period("M")
        monthly = df.groupby("year_month").last().reset_index()
        
        # 가속도 계산을 위해 시프트
        monthly["prev_dvol"] = monthly["dvol_20d"].shift(1)
        monthly["prev2_dvol"] = monthly["dvol_20d"].shift(2)
        monthly["next_month_ret"] = monthly["close"].shift(-1) / monthly["close"] - 1.0
        
        # 1개월 속도 및 가속도
        monthly["v1"] = (monthly["dvol_20d"] / monthly["prev_dvol"]) - 1.0
        monthly["v0"] = (monthly["prev_dvol"] / monthly["prev2_dvol"]) - 1.0
        monthly["accel"] = monthly["v1"] - monthly["v0"]
        monthly["ticker"] = ticker
        
        # 유효 행만 반환
        valid = monthly.dropna(subset=["accel", "next_month_ret"])
        if len(valid) == 0:
            return None
            
        return valid[["year_month", "ticker", "date", "close", "dvol_20d", "accel", "next_month_ret"]]
    except Exception:
        return None

def build_or_load_all_stocks_summary():
    if os.path.exists(CACHE_FILE):
        print(f"📦 기존 캐시 파일 로드: {CACHE_FILE}")
        return pd.read_pickle(CACHE_FILE)
        
    print("⏳ 미국 상장 전 종목(12,000+개) 월별 지표 데이터 추출 중 (멀티프로세싱)...")
    files = glob.glob("DataVault/raw/yahoo/candle_1d/*_1d.csv")
    print(f"총 분석 대상 파일: {len(files):,}개")
    
    t0 = time.time()
    n_workers = max(1, cpu_count() - 1)
    with Pool(n_workers) as pool:
        results = pool.map(process_single_stock, files, chunksize=100)
        
    valid_results = [r for r in results if r is not None]
    print(f"✅ 유효 종목 처리 완료: {len(valid_results):,}개 (소요시간: {time.time()-t0:.1f}초)")
    
    all_df = pd.concat(valid_results, ignore_index=True)
    all_df.to_pickle(CACHE_FILE)
    print(f"💾 캐시 저장 완료: {CACHE_FILE} (총 {len(all_df):,} 레코드)")
    return all_df

def run_all_market_acceleration_backtest():
    all_df = build_or_load_all_stocks_summary()
    
    # 2. 벤치마크 (QQQ, SPY) 로드
    qqq_df = pd.read_csv("DataVault/raw/yahoo/candle_1d/QQQ_1d.csv")
    qqq_df["date"] = pd.to_datetime(qqq_df["timestamp"])
    qqq_df["year_month"] = qqq_df["date"].dt.to_period("M")
    qqq_monthly = qqq_df.groupby("year_month").last().reset_index()
    qqq_monthly["qqq_ret"] = qqq_monthly["close"].shift(-1) / qqq_monthly["close"] - 1.0
    qqq_monthly = qqq_monthly.dropna(subset=["qqq_ret"]).set_index("year_month")
    
    spy_df = pd.read_csv("DataVault/raw/yahoo/candle_1d/SPY_1d.csv")
    spy_df["date"] = pd.to_datetime(spy_df["timestamp"])
    spy_df["year_month"] = spy_df["date"].dt.to_period("M")
    spy_monthly = spy_df.groupby("year_month").last().reset_index()
    spy_monthly["spy_ret"] = spy_monthly["close"].shift(-1) / spy_monthly["close"] - 1.0
    spy_monthly = spy_monthly.dropna(subset=["spy_ret"]).set_index("year_month")
    
    # 공통 월간 범위: 2000-01 ~ 2024-11 (25년)
    months = sorted(list(set(all_df["year_month"]).intersection(set(qqq_monthly.index))))
    months = [m for m in months if m >= pd.Period("2000-01", "M") and m <= pd.Period("2024-11", "M")]
    print(f"📅 백테스트 기간: {months[0]} ~ {months[-1]} (총 {len(months)}개월, 25년)")
    
    # 3. 월별 포트폴리오 시뮬레이션
    target_n = 10
    cost_rate = 0.0010 # 편도 0.05% (왕복 0.10%)
    
    # 나스닥 100 리스트 로드
    sys.path.append(r"docs/research/20261018_qqq_replication")
    from max_tenure_hybrid_backtest import NAS_UNIVERSE
    nas_set = set(NAS_UNIVERSE)
    
    port_all_rets = []
    port_nas_rets = []
    bench_qqq_rets = []
    bench_spy_rets = []
    
    for m in months:
        sub = all_df[all_df["year_month"] == m]
        
        # A) 전 종목 유니버스 필터: 주가 >= $3, 거래대금 >= $3M (약 40억 원)
        valid_all = sub[(sub["close"] >= 3.0) & (sub["dvol_20d"] >= 3_000_000)]
        # B) 나스닥 100 유니버스 필터
        valid_nas = sub[sub["ticker"].isin(nas_set)]
        
        # 전 종목 가속도 TOP 10 선정
        top_all = valid_all.sort_values("accel", ascending=False).head(target_n)
        r_all = top_all["next_month_ret"].mean() - cost_rate if len(top_all) > 0 else 0.0
        
        # 나스닥 100 가속도 TOP 10 선정
        top_nas = valid_nas.sort_values("accel", ascending=False).head(target_n)
        r_nas = top_nas["next_month_ret"].mean() - cost_rate if len(top_nas) > 0 else 0.0
        
        r_q = qqq_monthly.loc[m, "qqq_ret"] if m in qqq_monthly.index else 0.0
        r_s = spy_monthly.loc[m, "spy_ret"] if m in spy_monthly.index else 0.0
        
        port_all_rets.append(r_all)
        port_nas_rets.append(r_nas)
        bench_qqq_rets.append(r_q)
        bench_spy_rets.append(r_s)
        
    df_res = pd.DataFrame({
        "Month": months,
        "All_Stocks_Accel_10": port_all_rets,
        "Nas100_Accel_10": port_nas_rets,
        "QQQ_ETF": bench_qqq_rets,
        "SPY_ETF": bench_spy_rets
    }).set_index("Month")
    
    # 누적 자산 곡선 계산
    df_res["Eq_All"] = (1.0 + df_res["All_Stocks_Accel_10"]).cumprod()
    df_res["Eq_Nas"] = (1.0 + df_res["Nas100_Accel_10"]).cumprod()
    df_res["Eq_QQQ"] = (1.0 + df_res["QQQ_ETF"]).cumprod()
    df_res["Eq_SPY"] = (1.0 + df_res["SPY_ETF"]).cumprod()
    
    # 성과 지표 계산 함수
    def calc_stats(ret_series, eq_series):
        tot = eq_series.iloc[-1] - 1.0
        years = len(ret_series) / 12.0
        cagr = (eq_series.iloc[-1]) ** (1.0 / years) - 1.0
        vol = ret_series.std() * np.sqrt(12)
        sharpe = (cagr - 0.02) / vol if vol > 0 else 0.0
        cum = eq_series.cummax()
        dd = (eq_series - cum) / cum
        mdd = dd.min()
        return tot, cagr, vol, sharpe, mdd
        
    tot_all, cagr_all, vol_all, sh_all, mdd_all = calc_stats(df_res["All_Stocks_Accel_10"], df_res["Eq_All"])
    tot_nas, cagr_nas, vol_nas, sh_nas, mdd_nas = calc_stats(df_res["Nas100_Accel_10"], df_res["Eq_Nas"])
    tot_qqq, cagr_qqq, vol_qqq, sh_qqq, mdd_qqq = calc_stats(df_res["QQQ_ETF"], df_res["Eq_QQQ"])
    tot_spy, cagr_spy, vol_spy, sh_spy, mdd_spy = calc_stats(df_res["SPY_ETF"], df_res["Eq_SPY"])
    
    print("\n" + "=" * 115)
    print("📊 [25년 전 기간 성과 비교 (2000-01 ~ 2024-11, 300개월)]")
    print("=" * 115)
    header = f"{'전략 구분':<32} | {'누적수익률':>12} | {'CAGR':>8} | {'MDD':>9} | {'변동성':>8} | {'샤프':>6}"
    print(header)
    print("-" * 115)
    print(f"{'1. 전 종목(12,000+) 시총 가속도 TOP10':<32} | {tot_all*100:>11.1f}% | {cagr_all*100:>7.2f}% | {mdd_all*100:>8.2f}% | {vol_all*100:>7.2f}% | {sh_all:>6.2f}")
    print(f"{'2. 나스닥 100 시총 가속도 TOP10':<32} | {tot_nas*100:>11.1f}% | {cagr_nas*100:>7.2f}% | {mdd_nas*100:>8.2f}% | {vol_nas*100:>7.2f}% | {sh_nas:>6.2f}")
    print(f"{'3. QQQ ETF (나스닥 100)':<32} | {tot_qqq*100:>11.1f}% | {cagr_qqq*100:>7.2f}% | {mdd_qqq*100:>8.2f}% | {vol_qqq*100:>7.2f}% | {sh_qqq:>6.2f}")
    print(f"{'4. SPY ETF (S&P 500)':<32} | {tot_spy*100:>11.1f}% | {cagr_spy*100:>7.2f}% | {mdd_spy*100:>8.2f}% | {vol_spy*100:>7.2f}% | {sh_spy:>6.2f}")
    print("=" * 115)
    
    # 최근 10년 (2015-01 ~ 2024-11)
    df_10y = df_res[df_res.index >= pd.Period("2015-01", "M")].copy()
    df_10y["Eq_All"] = (1.0 + df_10y["All_Stocks_Accel_10"]).cumprod()
    df_10y["Eq_Nas"] = (1.0 + df_10y["Nas100_Accel_10"]).cumprod()
    df_10y["Eq_QQQ"] = (1.0 + df_10y["QQQ_ETF"]).cumprod()
    df_10y["Eq_SPY"] = (1.0 + df_10y["SPY_ETF"]).cumprod()
    
    tot_all_10, cagr_all_10, vol_all_10, sh_all_10, mdd_all_10 = calc_stats(df_10y["All_Stocks_Accel_10"], df_10y["Eq_All"])
    tot_nas_10, cagr_nas_10, vol_nas_10, sh_nas_10, mdd_nas_10 = calc_stats(df_10y["Nas100_Accel_10"], df_10y["Eq_Nas"])
    tot_qqq_10, cagr_qqq_10, vol_qqq_10, sh_qqq_10, mdd_qqq_10 = calc_stats(df_10y["QQQ_ETF"], df_10y["Eq_QQQ"])
    tot_spy_10, cagr_spy_10, vol_spy_10, sh_spy_10, mdd_spy_10 = calc_stats(df_10y["SPY_ETF"], df_10y["Eq_SPY"])
    
    print("\n" + "=" * 115)
    print("🌟 [최근 10년 성과 비교 (2015-01 ~ 2024-11, 10개년)]")
    print("=" * 115)
    print(header)
    print("-" * 115)
    print(f"{'1. 전 종목(12,000+) 시총 가속도 TOP10':<32} | {tot_all_10*100:>11.1f}% | {cagr_all_10*100:>7.2f}% | {mdd_all_10*100:>8.2f}% | {vol_all_10*100:>7.2f}% | {sh_all_10:>6.2f}")
    print(f"{'2. 나스닥 100 시총 가속도 TOP10':<32} | {tot_nas_10*100:>11.1f}% | {cagr_nas_10*100:>7.2f}% | {mdd_nas_10*100:>8.2f}% | {vol_nas_10*100:>7.2f}% | {sh_nas_10:>6.2f}")
    print(f"{'3. QQQ ETF (나스닥 100)':<32} | {tot_qqq_10*100:>11.1f}% | {cagr_qqq_10*100:>7.2f}% | {mdd_qqq_10*100:>8.2f}% | {vol_qqq_10*100:>7.2f}% | {sh_qqq_10:>6.2f}")
    print(f"{'4. SPY ETF (S&P 500)':<32} | {tot_spy_10*100:>11.1f}% | {cagr_spy_10*100:>7.2f}% | {mdd_spy_10*100:>8.2f}% | {vol_spy_10*100:>7.2f}% | {sh_spy_10:>6.2f}")
    print("=" * 115)
    
    # 연도별 수익률 비교
    df_res["Year"] = [m.year for m in df_res.index]
    ann_groups = df_res.groupby("Year")
    ann_data = []
    for y, g in ann_groups:
        r_all_y = (1.0 + g["All_Stocks_Accel_10"]).prod() - 1.0
        r_nas_y = (1.0 + g["Nas100_Accel_10"]).prod() - 1.0
        r_qqq_y = (1.0 + g["QQQ_ETF"]).prod() - 1.0
        r_spy_y = (1.0 + g["SPY_ETF"]).prod() - 1.0
        ann_data.append({
            "Year": y,
            "All_Stocks_Ret": r_all_y,
            "Nas100_Ret": r_nas_y,
            "QQQ_Ret": r_qqq_y,
            "SPY_Ret": r_spy_y
        })
    df_ann = pd.DataFrame(ann_data)
    df_ann.to_csv(f"{REPORTS_DIR}/all_market_vs_nas100_acceleration_annual.csv", index=False)
    print(f"\n💾 연도별 성과 리포트 저장 완료: {REPORTS_DIR}/all_market_vs_nas100_acceleration_annual.csv")

if __name__ == "__main__":
    run_all_market_acceleration_backtest()
