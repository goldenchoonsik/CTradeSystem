"""
[EXP-KRX-006-B] 스마트머니 스몰캡 턴어라운드 선정 종목별 개별 수익률 전수 분해
=================================================================================
- 목적: 스크리너가 선정한 개별 종목들이 실제로 1년간 얼마나 올랐고,
        최고점(Peak)은 언제, 몇 %까지 찍었으며, 최저점은 어디였는지 전수 추적
"""
import os
import sys
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = "/home/kuzimo/projects/CTradeSystem"
RESEARCH_DIR = f"{ROOT}/docs/research/20261021_krx_super_gainers_postmortem"

def inspect_individual_returns():
    results_csv = f"{RESEARCH_DIR}/smartcap_turnaround_results.csv"
    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    name_map = dict(zip(uni['code'], uni['name']))
    mkt_map = dict(zip(uni['code'], uni['market']))

    # 4대 조건 스크리너 실행하여 선정된 종목 리스트 재추출
    df_p = pd.read_parquet(px_path, columns=['code', 'date', 'close', 'value'])
    df_ext = pd.read_parquet(ext_path, columns=['code', 'date', 'market_cap', 'foreign_hold_pct', 'inst_net_qty', 'shares_listed'])

    adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()
    val_mat = df_p.pivot(index='date', columns='code', values='value').sort_index()
    mcap_mat = df_ext.pivot(index='date', columns='code', values='market_cap').sort_index()
    fgn_mat = df_ext.pivot(index='date', columns='code', values='foreign_hold_pct').sort_index()
    inst_mat = df_ext.pivot(index='date', columns='code', values='inst_net_qty').sort_index()
    shares_mat = df_ext.pivot(index='date', columns='code', values='shares_listed').sort_index()

    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()
    h52_mat = adj_mat.rolling(250, min_periods=100).max()
    val20_mat = val_mat.rolling(20, min_periods=10).mean()
    val60_mat = val_mat.rolling(60, min_periods=30).mean()
    fgn_delta20_mat = fgn_mat - fgn_mat.shift(20)
    inst_pct20_mat = (inst_mat.rolling(20, min_periods=10).sum() / shares_mat) * 100.0

    # 1년 주기 리밸런싱 날짜 (매년 1월 첫 거래일)
    cal = adj_mat.loc[(adj_mat.index >= '2013-01-01') & (adj_mat.index <= '2024-12-31')].index
    years = range(2013, 2025)

    item_rows = []

    for y in years:
        dates_y = [d for d in cal if d.year == y]
        if len(dates_y) < 100:
            continue
        d_entry = dates_y[0]
        # 직전일 기준
        idx_entry = adj_mat.index.get_loc(d_entry)
        if idx_entry < 60:
            continue
        d_prev = adj_mat.index[idx_entry - 1]

        # 4대 조건 검사
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
        cond_vol = (v20_row / v60_row >= 2.0)
        cands = cands[cond_vol.fillna(False)]

        f_delta = fgn_delta20_mat.loc[d_prev, cands]
        i_pct = inst_pct20_mat.loc[d_prev, cands]
        cond_flow = (f_delta >= 0.5) | (i_pct >= 0.8)
        cands = cands[cond_flow.fillna(False)]

        if len(cands) == 0:
            continue

        vol_surge = v20_row / v60_row
        score = (vol_surge.loc[cands].fillna(2.0)) + (f_delta.loc[cands].fillna(0.0) * 2.0) + (i_pct.loc[cands].fillna(0.0) * 2.0)
        picked = score.sort_values(ascending=False).head(15).index.tolist()

        # 각 종목의 1년(252영업일) 궤적 추적
        end_idx = min(idx_entry + 252, len(adj_mat) - 1)
        sub_cal = adj_mat.index[idx_entry : end_idx + 1]

        for code in picked:
            px_series = adj_mat.loc[sub_cal, code].dropna()
            if len(px_series) < 20:
                continue
            p0 = px_series.iloc[0]
            p_end = px_series.iloc[-1]
            p_max = px_series.max()
            p_min = px_series.min()

            ret_final = (p_end / p0) - 1.0
            ret_peak = (p_max / p0) - 1.0
            ret_worst = (p_min / p0) - 1.0

            # 최고점 도달 소요 거래일수
            peak_date = px_series.idxmax()
            days_to_peak = sub_cal.get_loc(peak_date)

            item_rows.append({
                "Year": y,
                "Entry_Date": d_entry.strftime("%Y-%m-%d"),
                "Code": code,
                "Name": name_map.get(code, code),
                "Market": mkt_map.get(code, "UNKNOWN"),
                "Start_Mcap_억": mcap_row.loc[code] if code in mcap_row else np.nan,
                "Ret_1Y_Final": ret_final,
                "Ret_1Y_Peak": ret_peak,
                "Ret_1Y_Worst": ret_worst,
                "Trade_Days_To_Peak": days_to_peak,
                "Peak_Month_Approx": days_to_peak // 21 + 1
            })

    df_items = pd.DataFrame(item_rows)
    out_csv = f"{RESEARCH_DIR}/smartcap_picked_items_detail.csv"
    df_items.to_csv(out_csv, index=False)

    print("=" * 125)
    print("📋 [스마트머니 스몰캡 턴어라운드 선정 종목별 1년 수익률 & 최고점 전수 분석표]")
    print("=" * 125)
    header = f"{'연도':<6} | {'선정 종목명 (코드)':<18} | {'시장':<6} | {'진입시총':>8} | {'1년최종수익':>11} | {'🔥연중최고수익':>13} | {'최대낙폭':>9} | {'최고점소요':>9}"
    print(header)
    print("-" * 125)

    for y in years:
        sub = df_items[df_items['Year'] == y]
        if sub.empty:
            print(f"{y:<6} | {'(조건 만족 종목 없음 - 100% 현금 방어)':<40} | {'-':>11} | {'-':>13} | {'-':>9} | {'-':>9}")
            continue
        for _, r in sub.iterrows():
            name_str = f"{r['Name']}({r['Code']})"
            mcap_str = f"{r['Start_Mcap_억']:.0f}억" if not np.isnan(r['Start_Mcap_억']) else "-"
            peak_str = f"+{r['Ret_1Y_Peak']*100:.1f}%"
            final_str = f"{r['Ret_1Y_Final']*100:>+6.1f}%"
            worst_str = f"{r['Ret_1Y_Worst']*100:>+5.1f}%"
            days_str = f"{r['Trade_Days_To_Peak']}일 ({r['Peak_Month_Approx']}개월차)"
            print(f"{int(r['Year']):<6} | {name_str:<18} | {r['Market']:<6} | {mcap_str:>8} | {final_str:>11} | {peak_str:>13} | {worst_str:>9} | {days_str:>9}")

    print("=" * 125)
    print("\n📊 [선정 종목들의 수익률 통계 요약]")
    print(f"   - 총 선정 종목 수: {len(df_items)}개")
    print(f"   - 🔥 연중 최고점 수익률(Peak Return) 평균: +{df_items['Ret_1Y_Peak'].mean()*100:.1f}% (중앙값: +{df_items['Ret_1Y_Peak'].median()*100:.1f}%)")
    print(f"   - 1년 후 최종 종가 수익률 평균: {df_items['Ret_1Y_Final'].mean()*100:+.1f}% (중앙값: {df_items['Ret_1Y_Final'].median()*100:+.1f}%)")
    print(f"   - 연중 +30% 이상 급등 경험 종목 비중: {(df_items['Ret_1Y_Peak'] >= 0.30).mean()*100:.1f}%")
    print(f"   - 연중 +50% 이상 급등 경험 종목 비중: {(df_items['Ret_1Y_Peak'] >= 0.50).mean()*100:.1f}%")
    print(f"   - 연중 +100% 이상 2배주 달성 종목 비중: {(df_items['Ret_1Y_Peak'] >= 1.00).mean()*100:.1f}%")
    print(f"   - ⏱️ 최고점 도달 평균 소요 기간: {df_items['Trade_Days_To_Peak'].mean():.0f} 거래일 (약 {df_items['Trade_Days_To_Peak'].mean()/21:.1f}개월 차)")

if __name__ == "__main__":
    inspect_individual_returns()
