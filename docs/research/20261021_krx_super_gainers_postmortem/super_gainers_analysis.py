"""
[EXP-KRX-005] 2013~2024 한국 증시 슈퍼 급등주(Super Gainers) 사후 역추적(Post-Mortem) 연구
=============================================================================================
- 분석 기간: 2013-01-02 ~ 2024-12-31 (12개년 전수 조사, 2025~2026년 제외)
- 유니버스: KOSPI & KOSDAQ 보통주 전체 (우선주, 스팩 제외)
- 급등주 기준:
  1. 연간 수익률 +200% 이상 (3루타 이상)
  2. 연도별 연간 상승률 TOP 20 (총 240선)
- 역추적 분석 항목 (출발 직전/초기 DNA):
  1. 출발 시가총액 규모 (스몰캡 vs 미드캡 vs 라지캡)
  2. 거래대금 폭증 배수 (평소 60일 대비 급등 1개월 차 거래대금 배수)
  3. 기술적 위치: 200일선 대비 위치, 52주 신고가/신저가 대비 위치
  4. 시총 가속도(Acceleration) 폭발 시점 및 피크 이후 수익률 궤적
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
os.makedirs(RESEARCH_DIR, exist_ok=True)

def run_analysis():
    t_start = time.time()
    print("=" * 115)
    print("🚀 [실측 연구] 2013~2024년 한국 증시 슈퍼 급등주 사후 역추적(Post-Mortem) 및 공통 DNA 분석")
    print("=" * 115)

    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    # 1. 유니버스 로드
    uni = pd.read_csv(uni_path)
    name_map = dict(zip(uni['code'], uni['name']))
    mkt_map = dict(zip(uni['code'], uni['market']))
    valid_stocks = uni[(uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]
    valid_codes = set(valid_stocks['code'])
    print(f"📊 KOSPI & KOSDAQ 보통주 유니버스: {len(valid_codes)}개 종목")

    # 2. 가격 및 시총 시계열 로드 (2013~2024년 필터링)
    print("⏳ 가격 및 시가총액 시계열 로딩 중...")
    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'value'],
        filters=[
            ('code', 'in', list(valid_codes)),
            ('date', '>=', pd.Timestamp('2012-01-01')),  # 2013년 초 200일선 계산을 위해 2012년 포함
            ('date', '<=', pd.Timestamp('2024-12-31'))
        ]
    )
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap'],
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

    # 200일 이동평균선
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()
    # 60일 평균 거래대금
    val60_mat = val_mat.rolling(60, min_periods=20).mean()
    # 52주(250거래일) 최고가 & 최저가
    h52_mat = adj_mat.rolling(250, min_periods=100).max()
    l52_mat = adj_mat.rolling(250, min_periods=100).min()

    # 3. 연도별 급등주 추출 (2013 ~ 2024)
    years = range(2013, 2025)
    gainer_records = []

    print("\n🔍 2013~2024년 12개 연도별 급등주 전수 스캔 중...")
    for y in years:
        dates_y = [d for d in adj_mat.index if d.year == y]
        if len(dates_y) < 100:
            continue
        d_start = dates_y[0]
        d_end = dates_y[-1]

        px_start = adj_mat.loc[d_start]
        px_end = adj_mat.loc[d_end]

        # 연간 종가 기준 수익률
        ret_annual = (px_end / px_start) - 1.0
        
        # 연중 최저점 대비 최고점 배수 (Max Surge)
        sub_px = adj_mat.loc[dates_y]
        max_surge = (sub_px.max() / sub_px.min()) - 1.0

        # 유효 종목 필터링 (시작 시점에 시총 및 가격이 존재하는 종목)
        valid_y = [c for c in px_start.dropna().index if c in mcap_mat.columns]
        ret_annual = ret_annual.loc[valid_y].dropna()

        # 연초 시총이 200억원 이상인 종목만 (초극소형 관리/동전주 왜곡 방지)
        mcap_start = mcap_mat.loc[d_start, valid_y].dropna()
        mcap_valid = mcap_start[mcap_start >= 200_0000_0000].index
        ret_annual = ret_annual.loc[ret_annual.index.intersection(mcap_valid)]

        # 연간 수익률 순 정렬
        sorted_ret = ret_annual.sort_values(ascending=False)

        # 기준 A: 연간 수익률 +200% 이상
        triples = sorted_ret[sorted_ret >= 2.0]
        # 기준 B: 연도별 TOP 20
        top20 = sorted_ret.head(20)

        # 분석 대상 종목군 (TOP 20 또는 +200% 이상)
        target_codes = sorted(list(set(top20.index).union(set(triples.index))))

        for code in target_codes:
            r = ret_annual.loc[code]
            surge = max_surge.loc[code]
            start_mcap = mcap_mat.loc[d_start, code] / 10000_0000  # 억원 단위
            end_mcap = mcap_mat.loc[d_end, code] / 10000_0000

            # 출발 시점 기술적 지표
            p0 = px_start.loc[code]
            ma200_0 = ma200_mat.loc[d_start, code] if code in ma200_mat.columns else np.nan
            ma200_ratio = (p0 / ma200_0) if (ma200_0 > 0 and not np.isnan(ma200_0)) else np.nan
            
            h52_0 = h52_mat.loc[d_start, code] if code in h52_mat.columns else np.nan
            h52_ratio = (p0 / h52_0) if (h52_0 > 0 and not np.isnan(h52_0)) else np.nan

            l52_0 = l52_mat.loc[d_start, code] if code in l52_mat.columns else np.nan
            l52_ratio = (p0 / l52_0) if (l52_0 > 0 and not np.isnan(l52_0)) else np.nan

            # 거래대금 폭증 배수:
            # 급등 첫 분기(1~3월) 평균 거래대금 vs 전년도 말 60일 평균 거래대금
            val_base = val60_mat.loc[d_start, code] if code in val60_mat.columns else np.nan
            # 연중 첫 40영업일 평균 거래대금
            val_early = val_mat.loc[dates_y[:40], code].mean() if code in val_mat.columns else np.nan
            vol_surge = (val_early / val_base) if (val_base > 0 and not np.isnan(val_base)) else np.nan

            # 최고점 발생 월 추적
            peak_date = sub_px[code].idxmax()
            peak_month = peak_date.month if pd.notnull(peak_date) else np.nan

            gainer_records.append({
                "Year": y,
                "Code": code,
                "Name": name_map.get(code, code),
                "Market": mkt_map.get(code, "UNKNOWN"),
                "Annual_Return": r,
                "Max_Surge": surge,
                "Start_Market_Cap_억": start_mcap,
                "End_Market_Cap_억": end_mcap,
                "Price_to_MA200": ma200_ratio,
                "Price_to_High52W": h52_ratio,
                "Price_to_Low52W": l52_ratio,
                "Volume_Surge_Ratio": vol_surge,
                "Peak_Month": peak_month,
                "Is_Triple": 1 if r >= 2.0 else 0,
                "Is_Top20": 1 if code in top20.index else 0
            })

    df_gainers = pd.DataFrame(gainer_records)
    csv_path = f"{RESEARCH_DIR}/super_gainers_list_2013_2024.csv"
    df_gainers.to_csv(csv_path, index=False)
    print(f"✅ 슈퍼 급등주 추출 완료: 총 {len(df_gainers)}개 케이스 (저장: {csv_path})")

    # 4. 심층 통계 및 공통점 분석
    print("\n" + "=" * 115)
    print("📊 [슈퍼 급등주(연간 수익률 +200% 이상 또는 연도별 TOP20) 12개년 종합 프로필]")
    print("=" * 115)

    # 1) 시장 분포 (코스피 vs 코스닥)
    mkt_dist = df_gainers['Market'].value_counts()
    print("1️⃣ [시장 소속 비중]")
    for mkt, cnt in mkt_dist.items():
        print(f"   - {mkt}: {cnt}개 ({cnt / len(df_gainers) * 100:.1f}%)")

    # 2) 출발 시가총액 분포
    print("\n2️⃣ [출발 시가총액 규모 분포 (억 원)]")
    cap_bins = [0, 500, 1000, 3000, 10000, np.inf]
    cap_labels = ["초소형 (<500억)", "소형 (500~1000억)", "중소형 (1000~3000억)", "중대형 (3000억~1조)", "대형 (>1조)"]
    df_gainers['Cap_Group'] = pd.cut(df_gainers['Start_Market_Cap_억'], bins=cap_bins, labels=cap_labels)
    cap_dist = df_gainers['Cap_Group'].value_counts()[cap_labels]
    for grp, cnt in cap_dist.items():
        print(f"   - {grp:<16}: {cnt:>3}개 ({cnt / len(df_gainers) * 100:>5.1f}%)")
    print(f"   👉 출발 시총 중앙값(Median): {df_gainers['Start_Market_Cap_억'].median():,.0f}억 원 (평균: {df_gainers['Start_Market_Cap_억'].mean():,.0f}억 원)")

    # 3) 기술적 지표 (출발 시점의 주가 위치)
    print("\n3️⃣ [출발 시점 기술적 이평선 및 고점/저점 위치]")
    valid_ma = df_gainers['Price_to_MA200'].dropna()
    print(f"   - 200일선 대비 주가 비율 중앙값: {valid_ma.median():.2f}배 (200일선 상회 비율: {(valid_ma >= 1.0).mean()*100:.1f}%)")
    valid_h52 = df_gainers['Price_to_High52W'].dropna()
    print(f"   - 52주 최고가 대비 주가 비율 중앙값: {valid_h52.median():.2f}배 (신고가 80% 이상 근접: {(valid_h52 >= 0.8).mean()*100:.1f}%)")
    valid_l52 = df_gainers['Price_to_Low52W'].dropna()
    print(f"   - 52주 최저가 대비 주가 비율 중앙값: {valid_l52.median():.2f}배 (바닥 탈출 구간)")

    # 4) 거래대금 폭증 배수
    print("\n4️⃣ [출발 초기 거래대금 폭증(Volume Surge) 강도]")
    valid_vol = df_gainers['Volume_Surge_Ratio'].dropna()
    print(f"   - 평소 대비 초기 거래대금 폭증 배수 중앙값: {valid_vol.median():.2f}배 (평균: {valid_vol.mean():.2f}배)")
    print(f"   - 3배 이상 폭증 종목 비중: {(valid_vol >= 3.0).mean()*100:.1f}%")
    print(f"   - 5배 이상 폭증 종목 비중: {(valid_vol >= 5.0).mean()*100:.1f}%")

    # 5) 연도별 대표 급등주 TOP 3 쇼케이스
    print("\n" + "=" * 115)
    print("🏆 [연도별 최고 급등주 명예의 전당 (2013~2024)]")
    print("=" * 115)
    print(f"{'연도':<6} | {'종목명 (코드)':<18} | {'시장':<6} | {'연간수익률':>10} | {'최대폭등배수':>10} | {'출발시총(억)':>10} | {'거래대금폭증':>10}")
    print("-" * 115)
    for y in years:
        sub = df_gainers[df_gainers['Year'] == y].sort_values('Annual_Return', ascending=False).head(2)
        for _, r in sub.iterrows():
            name_str = f"{r['Name']}({r['Code']})"
            vol_str = f"{r['Volume_Surge_Ratio']:.1f}배" if not np.isnan(r['Volume_Surge_Ratio']) else "-"
            print(f"{int(r['Year']):<6} | {name_str:<18} | {r['Market']:<6} | {r['Annual_Return']*100:>9.1f}% | {r['Max_Surge']*100:>9.1f}% | {r['Start_Market_Cap_억']:>10.0f}억 | {vol_str:>10}")
    print("=" * 115)

    # 5. 가속도(Acceleration) 팩터와 급등주 간의 '타이밍 괴리' 정밀 분석
    # 가속도 팩터로 샀을 때 왜 물렸는지:
    # 급등주들의 월별 가속도가 언제 최고점을 찍었고, 최고점 이후 주가는 어떻게 되었는지 추적
    print("\n⏳ 급등주들의 '가속도 피크 시점' 및 피크 이후 수익률 궤적 분석 중...")
    peak_accel_drops = []
    for idx, row in df_gainers.head(100).iterrows():
        c = row['Code']
        y = int(row['Year'])
        dates_y = [d for d in adj_mat.index if d.year == y]
        if len(dates_y) < 100 or c not in adj_mat.columns:
            continue
        s_px = adj_mat.loc[dates_y, c]
        # 월간 증가율의 가속도 계산
        s_v1m = (s_px / s_px.shift(21)) - 1.0
        s_accel = s_v1m - (s_px.shift(21) / s_px.shift(42) - 1.0)
        
        # 가속도가 최대였던 날
        if s_accel.dropna().empty:
            continue
        max_accel_date = s_accel.idxmax()
        # 가속도 최대일 이후 연말까지의 수익률
        px_accel_peak = s_px.loc[max_accel_date]
        px_year_end = s_px.iloc[-1]
        post_accel_ret = (px_year_end / px_accel_peak) - 1.0 if px_accel_peak > 0 else np.nan
        
        # 가속도 피크 이후 최대 낙폭
        post_px = s_px.loc[max_accel_date:]
        mdd_after = ((post_px - post_px.cummax()) / post_px.cummax()).min() if len(post_px) > 1 else 0.0

        peak_accel_drops.append({
            "Code": c,
            "Year": y,
            "Accel_Peak_Date": max_accel_date.strftime("%Y-%m-%d"),
            "Post_Accel_Return": post_accel_ret,
            "Post_Accel_MDD": mdd_after
        })

    df_peak = pd.DataFrame(peak_accel_drops)
    print("\n🔍 [급등주 내에서 '시총 가속도 최고점' 발생 후 추적 결과 (핵심 비밀 규명)]")
    print(f"   - 급등주가 가속도 피크를 찍은 후 연말까지의 평균 수익률: {df_peak['Post_Accel_Return'].mean()*100:+.1f}%")
    print(f"   - 가속도 피크 이후 겪는 평균 최대 낙폭(MDD): {df_peak['Post_Accel_MDD'].mean()*100:.1f}%")
    print(f"   - 가속도 피크 이후 -20% 이상 폭락을 겪은 종목 비율: {(df_peak['Post_Accel_MDD'] <= -0.20).mean()*100:.1f}%")
    print(f"   - 가속도 피크 이후 -30% 이상 폭락을 겪은 종목 비율: {(df_peak['Post_Accel_MDD'] <= -0.30).mean()*100:.1f}%")
    print("=" * 115)
    print(f"⏱️ 총 소요 시간: {time.time() - t_start:.2f}초")

if __name__ == "__main__":
    run_analysis()
