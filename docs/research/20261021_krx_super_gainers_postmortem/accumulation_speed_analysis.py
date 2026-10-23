"""
[EXP-KRX-005-C] 슈퍼 급등주의 외인·기관 지분 매집 속도 및 궤적(급격 vs 완만) 정밀 분석
========================================================================================
- 핵심 질문: 지분 변동이 급격(단기 1~2주 폭증)한가, 아니면 천천히(수개월간 지속 누적) 늘어나는가?
- 분석 항목:
  1. 전체 매집량 중 첫 10일(2주), 20일(1개월), 60일(3개월) 누적 비중
  2. 일평균 지분율 증가 속도 (%p / day)
  3. 지분 증가 곡선의 유형:
     - 유형 A (벼락치기 급격형): 20일 이내에 전체 매집의 70% 이상 완료
     - 유형 B (점진적 계단형): 2~4개월에 걸쳐 꾸준히 지속 매집
     - 유형 C (장기 마라톤형): 6개월 이상 지속 우상향 누적
  4. 주가 상승과의 선행/동행 관계
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

def analyze_accumulation_speed():
    t_start = time.time()
    print("=" * 115)
    print("⏱️ [매집 속도 정밀 분석] 외인·기관 스마트머니 지분 매집: 급격한 폭증인가 vs 완만한 누적 매집인가?")
    print("=" * 115)

    flow_path = f"{RESEARCH_DIR}/super_gainers_investor_flow.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    df_flow = pd.read_csv(flow_path)
    # 외인 또는 기관 매집형 종목만 필터링 (196개 종목)
    smart_df = df_flow[df_flow['Driver_Type'] != '개인/세력(테마) 주도'].copy()
    codes = smart_df['Code'].unique()

    print(f"📊 스마트머니(외인/기관) 매집형 급등주: {len(smart_df)}개 케이스 분석 대상")

    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'foreign_hold_pct', 'inst_net_qty', 'shares_listed'],
        filters=[('code', 'in', list(codes))]
    )
    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close'],
        filters=[('code', 'in', list(codes))]
    )

    piv_fgn = df_ext.pivot(index='date', columns='code', values='foreign_hold_pct').sort_index()
    piv_inst = df_ext.pivot(index='date', columns='code', values='inst_net_qty').sort_index()
    piv_shares = df_ext.pivot(index='date', columns='code', values='shares_listed').sort_index()
    piv_px = df_p.pivot(index='date', columns='code', values='close').sort_index()

    speed_records = []

    for idx, row in smart_df.iterrows():
        c = row['Code']
        y = int(row['Year'])
        name = row['Name']
        drv = row['Driver_Type']
        ann_ret = row['Annual_Return']

        dates_y = [d for d in piv_fgn.index if d.year == y]
        if len(dates_y) < 100 or c not in piv_fgn.columns:
            continue

        shs = piv_shares.loc[dates_y[0], c] if c in piv_shares.columns else np.nan

        # 시계열 추출 (1년)
        sub_dates = dates_y
        px_s = piv_px.loc[sub_dates, c]
        
        # 지분율 시계열 구성:
        # 외국인 주도면 foreign_hold_pct, 기관 주도면 누적 inst_net_qty / shares * 100
        if "외국인" in drv:
            hold_series = piv_fgn.loc[sub_dates, c].ffill()
            cum_series = hold_series - hold_series.iloc[0]  # 순증가분
        elif "기관" in drv:
            if c not in piv_inst.columns or np.isnan(shs) or shs <= 0:
                continue
            inst_qty = piv_inst.loc[sub_dates, c].fillna(0.0)
            cum_series = (inst_qty.cumsum() / shs) * 100.0  # 지분율 환산
        else: # 쌍끌이
            f_s = piv_fgn.loc[sub_dates, c].ffill()
            f_cum = f_s - f_s.iloc[0]
            inst_qty = piv_inst.loc[sub_dates, c].fillna(0.0)
            i_cum = (inst_qty.cumsum() / shs) * 100.0 if (not np.isnan(shs) and shs > 0) else 0.0
            cum_series = f_cum + i_cum

        total_gain = cum_series.max()  # 연중 최대 지분 순증치
        if total_gain <= 0.5:  # 지분 증가가 미미한 경우 제외
            continue

        # 최대 매집 도달 소요 거래일 수
        peak_idx = cum_series.idxmax()
        days_to_peak = (peak_idx - sub_dates[0]).days
        trade_days_to_peak = sub_dates.index(peak_idx)

        # 구간별 매집 비중:
        # 10영업일(2주), 20영업일(1개월), 60영업일(3개월), 120영업일(6개월)
        cum_10d = cum_series.iloc[min(10, len(cum_series)-1)]
        cum_20d = cum_series.iloc[min(20, len(cum_series)-1)]
        cum_60d = cum_series.iloc[min(60, len(cum_series)-1)]
        cum_120d = cum_series.iloc[min(120, len(cum_series)-1)]

        pct_in_20d = (cum_20d / total_gain) * 100.0 if total_gain > 0 else 0.0
        pct_in_60d = (cum_60d / total_gain) * 100.0 if total_gain > 0 else 0.0

        # 매집 패턴 분류
        if trade_days_to_peak <= 30 or pct_in_20d >= 70.0:
            speed_type = "⚡ 급격형 (1개월 내 폭풍 매집)"
        elif trade_days_to_peak <= 90 or pct_in_60d >= 70.0:
            speed_type = "📈 점진형 (2~3개월 지속 매집)"
        else:
            speed_type = "🐢 마라톤형 (4개월 이상 장기 누적)"

        speed_records.append({
            "Year": y,
            "Code": c,
            "Name": name,
            "Driver_Type": drv,
            "Annual_Return": ann_ret,
            "Total_Stake_Gain_p": total_gain,
            "Trade_Days_To_Peak": trade_days_to_peak,
            "Stake_20d_Pct": pct_in_20d,
            "Stake_60d_Pct": pct_in_60d,
            "Speed_Type": speed_type
        })

    df_speed = pd.DataFrame(speed_records)
    out_csv = f"{RESEARCH_DIR}/accumulation_speed_analysis.csv"
    df_speed.to_csv(out_csv, index=False)

    print("\n" + "=" * 115)
    print("📊 [외인·기관 지분 매집 속도 및 패턴 분포 (196개 스마트머니 급등주)]")
    print("=" * 115)

    speed_dist = df_speed['Speed_Type'].value_counts()
    for st, cnt in speed_dist.items():
        print(f"   - {st:<28}: {cnt:>3}개 ({cnt / len(df_speed) * 100:>5.1f}%)")

    print("\n⏱️ [매집 기간 정밀 수치]")
    print(f"   - 최대 지분 도달 소요 거래일수 중앙값: {df_speed['Trade_Days_To_Peak'].median():.0f} 거래일 (약 {df_speed['Trade_Days_To_Peak'].median()/20:.1f}개월)")
    print(f"   - 첫 1개월(20일) 내 매집 완료율 중앙값: {df_speed['Stake_20d_Pct'].median():.1f}%")
    print(f"   - 첫 3개월(60일) 내 매집 완료율 중앙값: {df_speed['Stake_60d_Pct'].median():.1f}%")

    print("\n🏢 [외국인 vs 기관 매집 속도 차이]")
    for d_name in ["외국인 주도 매집", "기관 주도 매집", "외인+기관 쌍끌이"]:
        sub_d = df_speed[df_speed['Driver_Type'] == d_name]
        if len(sub_d) > 0:
            print(f"   * [{d_name}] ({len(sub_d)}개):")
            print(f"     - 매집 소요 거래일 중앙값: {sub_d['Trade_Days_To_Peak'].median():.0f}일 (약 {sub_d['Trade_Days_To_Peak'].median()/20:.1f}개월)")
            print(f"     - 20일 완료율: {sub_d['Stake_20d_Pct'].median():.1f}% | 60일 완료율: {sub_d['Stake_60d_Pct'].median():.1f}%")
            top_pattern = sub_d['Speed_Type'].value_counts().index[0]
            print(f"     - 대표 매집 패턴: {top_pattern}")

    # 대표 케이스 쇼케이스
    print("\n" + "=" * 115)
    print("🏆 [유형별 대표 종목의 매집 속도 및 주가 상승 쇼케이스]")
    print("=" * 115)
    print(f"{'유형':<16} | {'종목명 (코드)':<18} | {'수익률':>9} | {'총지분증가':>10} | {'매집소요일':>10} | {'1개월완료율':>10} | {'3개월완료율':>10}")
    print("-" * 115)
    for st in ["⚡ 급격형 (1개월 내 폭풍 매집)", "📈 점진형 (2~3개월 지속 매집)", "🐢 마라톤형 (4개월 이상 장기 누적)"]:
        sample = df_speed[df_speed['Speed_Type'] == st].sort_values('Annual_Return', ascending=False).head(2)
        for _, r in sample.iterrows():
            print(f"{st[:10]:<16} | {r['Name']+'('+r['Code']+')':<18} | {r['Annual_Return']*100:>8.1f}% | {r['Total_Stake_Gain_p']:>9.1f}%p | {r['Trade_Days_To_Peak']:>8}거래일 | {r['Stake_20d_Pct']:>9.1f}% | {r['Stake_60d_Pct']:>9.1f}%")
    print("=" * 115)
    print(f"⏱️ 소요 시간: {time.time() - t_start:.2f}초")

if __name__ == "__main__":
    analyze_accumulation_speed()
