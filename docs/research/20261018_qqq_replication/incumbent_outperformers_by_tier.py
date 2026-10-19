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

def analyze_incumbent_outperformers():
    print("=" * 115)
    print("🔬 [퀀트 실측] 신규 편입주 제외 나스닥 100 기존 종목 중 초과수익 종목의 순위 구간(Tier) 분포 분석")
    print("=" * 115)
    
    qqq_df = load_stock_data("QQQ")
    cal = qqq_df.index[(qqq_df.index >= "2016-01-01") & (qqq_df.index <= "2024-12-31")]
    
    # 1. 2016~2024년 공식 편입 종목 집합 (Rookies)
    new_entrant_set = set([sym for _, sym in OFFICIAL_ADDITIONS])
    
    stocks = {}
    for t in set(NAS_UNIVERSE):
        df = load_stock_data(t)
        if df is not None and len(df) > 50:
            stocks[t] = df
            
    adj_mat = pd.DataFrame({t: df["adj_close"].reindex(cal).ffill() for t, df in stocks.items()}, index=cal)
    dvol_mat = pd.DataFrame({t: df["dvol_60d"].reindex(cal).ffill() for t, df in stocks.items()}, index=cal)
    
    # 2. 2016-01-04 기준 기존 나스닥 100 베이스라인 유니버스 (Incumbents)
    d0 = cal[0]
    rank_d0 = dvol_mat.loc[d0].dropna().sort_values(ascending=False)
    incumbents_all = [s for s in rank_d0.index if s in stocks and s not in new_entrant_set][:100]
    
    # 각 종목의 2016-01-01 시작 순위 (Rank 1 ~ 100)
    incumbent_ranks = {s: rank_d0.index.get_loc(s) + 1 for s in incumbents_all}
    
    # 3. 신규 편입주(Rookies) 성과 기준선 계산 (2016~2024)
    # 편입주들의 1년 평균 수익률, 3년 평균 수익률, 10년 누적 수익률
    rookie_10y_rets = []
    for s in new_entrant_set:
        if s in adj_mat.columns:
            series = adj_mat[s].dropna()
            if len(series) > 100:
                tot_r = (series.iloc[-1] / series.iloc[0] - 1.0) * 100.0
                rookie_10y_rets.append(tot_r)
                
    rookie_median_ret = np.median(rookie_10y_rets)
    rookie_mean_ret = np.mean(rookie_10y_rets)
    
    print(f"📌 신규 편입주(56개 전수) 누적 수익률 기준선: 평균 +{rookie_mean_ret:.1f}% | 중앙값 +{rookie_median_ret:.1f}%")
    
    # 4. 기존 종목(Incumbents) 10년 성과 측정 및 순위 매핑
    incumbent_records = []
    for s in incumbents_all:
        series = adj_mat[s].loc[cal].dropna()
        if len(series) < 200:
            continue
        p_start = series.iloc[0]
        p_end = series.iloc[-1]
        tot_ret = (p_end / p_start - 1.0) * 100.0
        n_days = len(series)
        cagr = ((p_end / p_start) ** (252.0 / n_days) - 1.0) * 100.0
        
        # 10년 평균 시총/거래대금 순위
        avg_dvol = dvol_mat[s].loc[cal].mean()
        
        incumbent_records.append({
            "Ticker": s,
            "InitRank": incumbent_ranks[s],
            "AvgDvol": avg_dvol,
            "TotalRet(%)": round(tot_ret, 1),
            "CAGR(%)": round(cagr, 2),
            "BeatsRookieMedian": tot_ret > rookie_median_ret,
            "BeatsRookieMean": tot_ret > rookie_mean_ret
        })
        
    df_inc = pd.DataFrame(incumbent_records).sort_values("InitRank").reset_index(drop=True)
    
    # 5. 순위 구간(Tier) 정의
    # Tier 1: 1 ~ 10위 (Mega-Cap)
    # Tier 2: 11 ~ 30위 (Large-Cap)
    # Tier 3: 31 ~ 50위 (Mid-Large)
    # Tier 4: 51 ~ 70위 (Mid-Cap)
    # Tier 5: 71 ~ 100위 (Lower-Tail)
    def assign_tier(r):
        if r <= 10:
            return "1. 메가캡 (1~10위)"
        elif r <= 30:
            return "2. 대형주 (11~30위)"
        elif r <= 50:
            return "3. 중대형 (31~50위)"
        elif r <= 70:
            return "4. 중형주 (51~70위)"
        else:
            return "5. 하위권 (71~100위)"
            
    df_inc["Tier"] = df_inc["InitRank"].apply(assign_tier)
    
    # 6. 순위 구간별 초과수익 종목 분포 분석
    tier_summary = []
    tier_order = [
        "1. 메가캡 (1~10위)",
        "2. 대형주 (11~30위)",
        "3. 중대형 (31~50위)",
        "4. 중형주 (51~70위)",
        "5. 하위권 (71~100위)"
    ]
    
    for t_name in tier_order:
        sub = df_inc[df_inc["Tier"] == t_name]
        n_total = len(sub)
        n_beats_median = sub["BeatsRookieMedian"].sum()
        pct_beats_median = (n_beats_median / n_total * 100.0) if n_total > 0 else 0.0
        n_beats_mean = sub["BeatsRookieMean"].sum()
        pct_beats_mean = (n_beats_mean / n_total * 100.0) if n_total > 0 else 0.0
        
        avg_ret = sub["TotalRet(%)"].mean()
        med_ret = sub["TotalRet(%)"].median()
        avg_cagr = sub["CAGR(%)"].mean()
        
        # 대표 초과달성 종목들
        outperformers = sub[sub["BeatsRookieMedian"]].sort_values("TotalRet(%)", ascending=False)["Ticker"].tolist()
        
        tier_summary.append({
            "순위 구간 (Tier)": t_name,
            "구간 종목수": n_total,
            "평균 수익률(%)": round(avg_ret, 1),
            "중앙값(%)": round(med_ret, 1),
            "평균 CAGR(%)": round(avg_cagr, 1),
            "루키 능가 종목수": f"{n_beats_median}개 ({pct_beats_median:.1f}%)",
            "초과수익 핵심 대표 종목": ", ".join(outperformers[:6])
        })
        
    df_tier = pd.DataFrame(tier_summary)
    
    print("\n" + "=" * 125)
    print("🏆 [순위 구간(Tier)별 신규 편입주 능가 종목 분포 통계]")
    print("=" * 125)
    print(df_tier.to_string(index=False))
    
    # 7. 신규 편입주를 압도적으로 능가한 기존 나스닥 100 주도주 Top 15
    print("\n" + "=" * 125)
    print("🌟 [신규 편입주 평균을 아득히 뛰어넘은 기존 나스닥 100 슈퍼스타 Top 15]")
    print("=" * 125)
    top15 = df_inc.sort_values("TotalRet(%)", ascending=False).head(15)
    print(top15[["Ticker", "InitRank", "Tier", "TotalRet(%)", "CAGR(%)"]].to_string(index=False))
    
    # CSV 저장
    csv_out = f"{REPORTS_DIR}/incumbent_outperformers_by_tier.csv"
    df_tier.to_csv(csv_out, index=False, encoding="utf-8-sig")
    print(f"\n✅ 분석 결과 CSV 저장: {csv_out}")

if __name__ == "__main__":
    analyze_incumbent_outperformers()
