import os
import sys
import pandas as pd
import numpy as np

sys.path.append(r"docs/research/20261018_qqq_replication")
from max_tenure_hybrid_backtest import (
    load_stock_data, NAS_UNIVERSE, OFFICIAL_ADDITIONS, OFFICIAL_REMOVALS
)

ROOT = "DataVault"
REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

def analyze_entrant_golden_window():
    print("=" * 115)
    print("🔬 [퀀트 실측] 나스닥 100 신규 편입 종목의 보유 기간별 수익률 궤적 및 최고점(Golden Window) 전수 분석")
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
    
    # 1. 1999~2024 전체 신규 편입 이벤트 구축
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
    
    all_entrants = [] # [(date, ticker, source)]
    
    # 1999~2015 거래대금 신규 승격주
    for d in rebal_dates:
        if d >= pd.Timestamp("2016-01-01"):
            break
        curr_top = set(dvol_mat.loc[d].dropna().sort_values(ascending=False).head(50).index.tolist())
        brand_new = sorted(list(curr_top - known_universe))
        for s in brand_new:
            if s in stocks:
                all_entrants.append((d, s, "1999~2015 거래대금신규"))
            known_universe.add(s)
            
    # 2016~2024 공식 56개 신규 편입주
    for d, sym in OFFICIAL_ADDITIONS:
        if d > pd.Timestamp("2024-12-31"):
            continue
        matched = cal[cal >= d]
        if len(matched) > 0:
            dt = matched[0]
            if sym in stocks:
                all_entrants.append((dt, sym, "2016~2024 공식편입"))
                
    print(f"✅ 분석 대상 전체 신규 편입 표본 수: 총 {len(all_entrants)}개 종목 (1999~2024년 26개년)")
    
    # 2. 개별 종목별 편입일 이후 일별/월별 궤적 추적
    # 구간 정의 (영업일 기준):
    # 1M(21일), 3M(63일), 6M(126일), 9M(189일), 1Y(252일), 1.5Y(378일), 2Y(504일), 2.5Y(630일), 3Y(756일), 4Y(1008일), 5Y(1260일)
    horizons = {
        "1개월 (21일)": 21,
        "3개월 (63일)": 63,
        "6개월 (126일)": 126,
        "9개월 (189일)": 189,
        "1년 (252일)": 252,
        "1.5년 (378일)": 378,
        "2년 (504일)": 504,
        "2.5년 (630일)": 630,
        "3년 (756일)": 756,
        "4년 (1008일)": 1008,
        "5년 (1260일)": 1260
    }
    
    records = []
    monthly_trajectories = [] # 개월차별(M1 ~ M36) 수익률
    
    for entry_dt, sym, src in all_entrants:
        if sym not in adj_mat.columns:
            continue
        series = adj_mat[sym].loc[entry_dt:].dropna()
        if len(series) < 20: # 최소 20일 이상 데이터
            continue
            
        p0 = series.iloc[0]
        rec = {
            "Ticker": sym,
            "EntryDate": entry_dt.strftime("%Y-%m-%d"),
            "Source": src,
            "AvailableDays": len(series)
        }
        
        # 각 구간별 누적 수익률
        for h_name, h_days in horizons.items():
            if len(series) > h_days:
                p_h = series.iloc[h_days]
                ret_h = (p_h / p0 - 1.0) * 100.0
                rec[h_name] = ret_h
            else:
                rec[h_name] = np.nan
                
        # 최고점(Peak) 분석: 최초 3년(756영업일) 내 최고점 도달 일수 및 최고 수익률
        window_3y = series.iloc[:min(len(series), 756)]
        peak_px = window_3y.max()
        peak_idx = window_3y.values.argmax()
        peak_days = peak_idx
        peak_months = round(peak_days / 21.0, 1)
        max_ret = (peak_px / p0 - 1.0) * 100.0
        
        rec["PeakGain(%)"] = round(max_ret, 1)
        rec["PeakDays"] = peak_days
        rec["PeakMonths"] = peak_months
        
        records.append(rec)
        
        # M1 ~ M36 월별 누적 및 월별 개별 수익률
        m_cum = []
        for m in range(1, 37):
            idx = int(m * 21)
            if idx < len(series):
                m_cum.append((series.iloc[idx] / p0 - 1.0) * 100.0)
            else:
                m_cum.append(np.nan)
        monthly_trajectories.append(m_cum)
        
    df_res = pd.DataFrame(records)
    
    # -------------------------------------------------------------
    # 1. 구간별 평균/중앙값/승률 및 연환산(CAGR) 요약
    # -------------------------------------------------------------
    summary_rows = []
    prev_h_days = 0
    prev_avg_ret = 0.0
    
    for h_name, h_days in horizons.items():
        vals = df_res[h_name].dropna()
        if len(vals) == 0:
            continue
        avg_ret = vals.mean()
        med_ret = vals.median()
        win_rate = (vals > 0).mean() * 100.0
        
        # 연환산 수익률 (CAGR = (1 + ret)^(252 / days) - 1)
        years = h_days / 252.0
        avg_cagr = ((1.0 + avg_ret / 100.0) ** (1.0 / years) - 1.0) * 100.0 if avg_ret > -90 else -99.9
        
        # 해당 구간의 월평균 상승 속도 (Marginal Monthly Velocity)
        delta_days = h_days - prev_h_days
        delta_months = delta_days / 21.0
        marginal_monthly = (avg_ret - prev_avg_ret) / delta_months if delta_months > 0 else 0.0
        
        summary_rows.append({
            "보유 기간": h_name,
            "표본 종목수": len(vals),
            "평균 누적수익률(%)": round(avg_ret, 1),
            "중앙값(%)": round(med_ret, 1),
            "승률(상승비율%)": round(win_rate, 1),
            "연환산 CAGR(%)": round(avg_cagr, 1),
            "구간 월평균 상승폭(%p/월)": round(marginal_monthly, 2)
        })
        
        prev_h_days = h_days
        prev_avg_ret = avg_ret
        
    df_summary = pd.DataFrame(summary_rows)
    
    print("\n" + "=" * 115)
    print("📈 [1. 나스닥 100 신규 편입 종목의 보유 기간별 수익률 라이프사이클]")
    print("=" * 115)
    print(df_summary.to_string(index=False))
    
    # -------------------------------------------------------------
    # 2. 최고점(Peak) 도달 시점 분포 분석
    # -------------------------------------------------------------
    print("\n" + "=" * 115)
    print("🎯 [2. 신규 편입 종목이 최고가(Peak)에 도달하는 시점 분포]")
    print("=" * 115)
    
    peak_months = df_res["PeakMonths"].dropna()
    avg_peak_m = peak_months.mean()
    med_peak_m = peak_months.median()
    
    print(f"- 편입 후 최고가 도달 평균 시점: 약 {avg_peak_m:.1f}개월 차 ({avg_peak_m*21:.0f} 영업일)")
    print(f"- 편입 후 최고가 도달 중앙값    : 약 {med_peak_m:.1f}개월 차 ({med_peak_m*21:.0f} 영업일)")
    print(f"- 평균 최고점 도달 수익률      : +{df_res['PeakGain(%)'].mean():.1f}% (중앙값 +{df_res['PeakGain(%)'].median():.1f}%)")
    
    # 최고점 도달 구간별 비중
    bins = [0, 3, 6, 12, 18, 24, 30, 36, 999]
    labels = ["0~3개월", "3~6개월", "6~12개월", "1~1.5년", "1.5~2년", "2~2.5년", "2.5~3년", "3년 초과"]
    df_res["PeakWindow"] = pd.cut(df_res["PeakMonths"], bins=bins, labels=labels, right=True)
    dist = df_res["PeakWindow"].value_counts(normalize=True).loc[labels] * 100.0
    
    print("\n📊 [최고점(Peak) 도달 구간별 발생 확률 분포]")
    for w_name, pct in dist.items():
        bar = "█" * int(pct // 2)
        print(f"  • {w_name:<10}: {pct:5.1f}% {bar}")
        
    # -------------------------------------------------------------
    # 3. M1 ~ M24 개월차별 월간 순수 상승률 통계 (Marginal)
    # -------------------------------------------------------------
    df_m = pd.DataFrame(monthly_trajectories, columns=[f"M{i}" for i in range(1, 37)])
    m_means = df_m.mean()
    m_vel = m_means.diff().fillna(m_means.iloc[0]) # 월별 증분
    
    print("\n" + "=" * 115)
    print("⚡ [3. 편입 후 개월차별(Month 1 ~ Month 24) 한계 상승 탄력 (월별 추가 상승폭)]")
    print("=" * 115)
    for m in range(1, 25):
        cum_val = m_means.iloc[m-1]
        inc_val = m_vel.iloc[m-1]
        print(f"  • Month {m:2d}: 누적 +{cum_val:5.1f}% | 당월 증분 {inc_val:+5.2f}%p")
        
    # -------------------------------------------------------------
    # 4. CSV 저장
    # -------------------------------------------------------------
    out_csv = f"{REPORTS_DIR}/entrant_golden_window_analysis.csv"
    try:
        df_summary.to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"\n✅ 분석 결과 요약 CSV 저장 완료: {out_csv}")
    except PermissionError:
        out_csv_f = f"{REPORTS_DIR}/entrant_golden_window_analysis_fixed.csv"
        df_summary.to_csv(out_csv_f, index=False, encoding="utf-8-sig")
        print(f"\n✅ 분석 결과 요약 CSV 저장 완료: {out_csv_f}")
        
    out_all_csv = f"{REPORTS_DIR}/entrant_all_trajectories.csv"
    try:
        df_res.to_csv(out_all_csv, index=False, encoding="utf-8-sig")
        print(f"✅ 전 종목별 궤적 원장 CSV 저장 완료: {out_all_csv}")
    except PermissionError:
        out_all_csv_f = f"{REPORTS_DIR}/entrant_all_trajectories_fixed.csv"
        df_res.to_csv(out_all_csv_f, index=False, encoding="utf-8-sig")
        print(f"✅ 전 종목별 궤적 원장 CSV 저장 완료: {out_all_csv_f}")

if __name__ == "__main__":
    analyze_entrant_golden_window()
