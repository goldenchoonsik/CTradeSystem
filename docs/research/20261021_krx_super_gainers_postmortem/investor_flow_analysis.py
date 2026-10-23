"""
[EXP-KRX-005-B] 슈퍼 급등주 409선의 외국인 및 기관 순매수/지분율 특별 흔적 정밀 분석
========================================================================================
- 분석 대상: 2013~2024년 추출된 슈퍼 급등주 409선
- 추적 지표:
  1. 출발 직전 외국인 지분율 (Foreign Hold Pct) 수준: 외인이 이미 쥐고 있었는가?
  2. 급등 초기(1~3개월) 외국인 지분율 변동 (%p): 외인이 쓸어 담았는가?
  3. 급등 초기 기관 순매수 누적 강도: 기관이 매집했는가?
  4. '외인/기관 쌍끌이형' vs '개인 수급/세력 주도형' 비중 분석
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

def analyze_investor_flow():
    t_start = time.time()
    print("=" * 115)
    print("🔍 [수급 정밀 분석] 2013~2024 슈퍼 급등주 409선의 외국인·기관 매수 흔적 전수 추적")
    print("=" * 115)

    list_path = f"{RESEARCH_DIR}/super_gainers_list_2013_2024.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    df_gainers = pd.read_csv(list_path)
    codes = df_gainers['Code'].unique()

    print(f"📊 분석 대상 급등주: {len(df_gainers)}개 케이스 (고유 종목 {len(codes)}개)")

    # 1. 외인 및 기관 수급 데이터 로드
    print("⏳ 외국인 지분율 및 기관 순매수 시계열 로딩 중...")
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'foreign_hold_pct', 'inst_net_qty', 'shares_listed'],
        filters=[('code', 'in', list(codes))]
    )
    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'volume'],
        filters=[('code', 'in', list(codes))]
    )

    piv_fgn = df_ext.pivot(index='date', columns='code', values='foreign_hold_pct').sort_index()
    piv_inst = df_ext.pivot(index='date', columns='code', values='inst_net_qty').sort_index()
    piv_shares = df_ext.pivot(index='date', columns='code', values='shares_listed').sort_index()
    piv_vol = df_p.pivot(index='date', columns='code', values='volume').sort_index()
    piv_px = df_p.pivot(index='date', columns='code', values='close').sort_index()

    flow_records = []

    for idx, row in df_gainers.iterrows():
        c = row['Code']
        y = int(row['Year'])
        name = row['Name']
        mkt = row['Market']
        ann_ret = row['Annual_Return']
        start_cap = row['Start_Market_Cap_억']

        if c not in piv_fgn.columns:
            continue

        dates_y = [d for d in piv_fgn.index if d.year == y]
        if len(dates_y) < 100:
            continue

        d_start = dates_y[0]
        # 급등 초기 60거래일(약 3개월)
        d_60 = dates_y[min(60, len(dates_y)-1)]
        d_end = dates_y[-1]

        # 1) 출발 시점 외인 지분율
        fgn_0 = piv_fgn.loc[d_start, c] if d_start in piv_fgn.index else np.nan
        # 2) 3개월 후 외인 지분율 변화 (%p)
        fgn_60 = piv_fgn.loc[d_60, c] if d_60 in piv_fgn.index else np.nan
        fgn_delta_60 = fgn_60 - fgn_0 if (not np.isnan(fgn_0) and not np.isnan(fgn_60)) else np.nan
        # 3) 연간 외인 지분율 변화 (%p)
        fgn_end = piv_fgn.loc[d_end, c] if d_end in piv_fgn.index else np.nan
        fgn_delta_year = fgn_end - fgn_0 if (not np.isnan(fgn_0) and not np.isnan(fgn_end)) else np.nan

        # 4) 초기 60일 기관 순매수 누적 수량 / 상장주식수 (%)
        shs = piv_shares.loc[d_start, c] if (c in piv_shares.columns and d_start in piv_shares.index) else np.nan
        if c in piv_inst.columns and not np.isnan(shs) and shs > 0:
            inst_sub = piv_inst.loc[dates_y[:60], c].fillna(0.0)
            inst_net_pct_60 = (inst_sub.sum() / shs) * 100.0  # 지분율 기준 기관 순매수 비중
            
            inst_year = piv_inst.loc[dates_y, c].fillna(0.0)
            inst_net_pct_year = (inst_year.sum() / shs) * 100.0
        else:
            inst_net_pct_60 = np.nan
            inst_net_pct_year = np.nan

        # 5) 수급 주체 분류
        # 외인이나 기관이 지분을 2%p 이상 순매수하면 "스마트머니(외인/기관) 주도"
        smart_money = False
        driver = "개인/세력(테마) 주도"
        
        f_buy = fgn_delta_year >= 2.0 or fgn_delta_60 >= 1.5
        i_buy = inst_net_pct_year >= 2.0 or inst_net_pct_60 >= 1.5

        if f_buy and i_buy:
            driver = "외인+기관 쌍끌이"
            smart_money = True
        elif f_buy:
            driver = "외국인 주도 매집"
            smart_money = True
        elif i_buy:
            driver = "기관 주도 매집"
            smart_money = True

        flow_records.append({
            "Year": y,
            "Code": c,
            "Name": name,
            "Market": mkt,
            "Annual_Return": ann_ret,
            "Start_Market_Cap_억": start_cap,
            "Start_Foreign_Hold_Pct": fgn_0,
            "Foreign_Delta_60d_p": fgn_delta_60,
            "Foreign_Delta_Year_p": fgn_delta_year,
            "Inst_Net_Pct_60d": inst_net_pct_60,
            "Inst_Net_Pct_Year": inst_net_pct_year,
            "Driver_Type": driver
        })

    df_flow = pd.DataFrame(flow_records)
    out_csv = f"{RESEARCH_DIR}/super_gainers_investor_flow.csv"
    df_flow.to_csv(out_csv, index=False)
    print(f"💾 수급 분석 데이터 저장 완료: {out_csv}")

    # 통계 출력
    print("\n" + "=" * 115)
    print("📊 [슈퍼 급등주 409선의 외국인·기관 수급 정밀 프로필]")
    print("=" * 115)

    # 1. 출발 시점 외국인 지분율 분포
    f_start = df_flow['Start_Foreign_Hold_Pct'].dropna()
    print("1️⃣ [출발 시점 외국인 지분율 수준]")
    print(f"   - 외인 지분율 중앙값: {f_start.median():.2f}% (평균: {f_start.mean():.2f}%)")
    print(f"   - 외인 지분율 5% 미만인 종목 비중: {(f_start < 5.0).mean()*100:.1f}%")
    print(f"   - 외인 지분율 2% 미만인 극소수 종목 비중: {(f_start < 2.0).mean()*100:.1f}%")
    print(f"   👉 대부분 외인의 손을 전혀 타지 않은 '무주공산' 스몰캡 상태에서 출발!")

    # 2. 수급 주체 유형별 분포
    print("\n2️⃣ [수급 주체 유형 분류 (Driver Type)]")
    driver_dist = df_flow['Driver_Type'].value_counts()
    for drv, cnt in driver_dist.items():
        print(f"   - {drv:<18}: {cnt:>3}개 ({cnt / len(df_flow) * 100:>5.1f}%)")

    # 3. 시총 규모별 수급 주체 차이 (대형주 vs 스몰캡)
    print("\n3️⃣ [시총 규모에 따른 수급 주체 극적 차이]")
    small = df_flow[df_flow['Start_Market_Cap_억'] < 3000]
    large = df_flow[df_flow['Start_Market_Cap_억'] >= 3000]
    
    print(f"   [시총 3,000억 미만 스몰캡 (총 {len(small)}개)]")
    for drv, cnt in small['Driver_Type'].value_counts().items():
        print(f"      - {drv:<18}: {cnt:>3}개 ({cnt / len(small) * 100:>5.1f}%)")
    
    print(f"   [시총 3,000억 이상 중대형주 (총 {len(large)}개)]")
    for drv, cnt in large['Driver_Type'].value_counts().items():
        print(f"      - {drv:<18}: {cnt:>3}개 ({cnt / len(large) * 100:>5.1f}%)")

    # 4. 외인/기관이 대량 매집한 슈퍼 급등주 TOP 10 쇼케이스
    print("\n" + "=" * 115)
    print("🏆 [외국인·기관 대량 매집 주도형 대표 급등주 쇼케이스]")
    print("=" * 115)
    print(f"{'연도':<6} | {'종목명 (코드)':<18} | {'수익률':>9} | {'출발시총':>8} | {'외인초기지분':>10} | {'외인지분변동':>11} | {'기관순매수비중':>12} | {'주도유형':<14}")
    print("-" * 115)
    smart_top = df_flow[df_flow['Driver_Type'] != '개인/세력(테마) 주도'].sort_values('Annual_Return', ascending=False).head(12)
    for _, r in smart_top.iterrows():
        f_delta = f"{r['Foreign_Delta_Year_p']:>+5.1f}%p" if not np.isnan(r['Foreign_Delta_Year_p']) else "-"
        i_delta = f"{r['Inst_Net_Pct_Year']:>+5.1f}%" if not np.isnan(r['Inst_Net_Pct_Year']) else "-"
        print(f"{int(r['Year']):<6} | {r['Name']+'('+r['Code']+')':<18} | {r['Annual_Return']*100:>8.1f}% | {r['Start_Market_Cap_억']:>7.0f}억 | {r['Start_Foreign_Hold_Pct']:>9.1f}% | {f_delta:>11} | {i_delta:>12} | {r['Driver_Type']:<14}")
    print("=" * 115)
    print(f"⏱️ 수급 분석 소요 시간: {time.time() - t_start:.2f}초")

if __name__ == "__main__":
    analyze_investor_flow()
