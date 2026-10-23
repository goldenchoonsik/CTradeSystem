"""
[EXP-KRX-017] MSCI 한국 지수 신규 편입 이후 개월수별 주가 수익률 및 궤적 실측 분석
========================================================================================
- 목적: 2014~2024년 MSCI Korea 지수에 신규 편입된 전 종목을 대상으로
        편입일(Rebalancing Date, T_0) 기준 +1M, +2M, +3M, +6M, +9M, +12M 주가 궤적 실측
        'MSCI 편입 효과'의 피크(상투) 시점 및 편입 이후 감쇠(Post-Inclusion Hangover) 정량화
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

# 2014~2024년 MSCI Korea 신규 편입 전수 리스트 (편입일 = 리밸런싱 당일 종가 기준)
MSCI_INCLUSIONS = [
    # 2014
    {"code": "A000120", "name": "CJ대한통운", "date": "2014-11-25"},
    {"code": "A035720", "name": "카카오", "date": "2014-11-25"},
    {"code": "A051600", "name": "한전KPS", "date": "2014-11-25"},
    # 2015
    {"code": "A009240", "name": "한샘", "date": "2015-02-27"},
    {"code": "A128940", "name": "한미약품", "date": "2015-05-29"},
    {"code": "A008930", "name": "한미사이언스", "date": "2015-08-31"},
    {"code": "A026960", "name": "동서", "date": "2015-08-31"},
    {"code": "A007070", "name": "GS리테일", "date": "2015-11-30"},
    {"code": "A007310", "name": "오뚜기", "date": "2015-11-30"},
    {"code": "A035760", "name": "CJ ENM", "date": "2015-11-30"},
    {"code": "A005300", "name": "롯데칠성", "date": "2015-11-30"},
    # 2017
    {"code": "A251270", "name": "넷마블", "date": "2017-05-31"},
    {"code": "A091990", "name": "셀트리온헬스케어", "date": "2017-08-31"},
    {"code": "A263750", "name": "펄어비스", "date": "2017-11-30"},
    {"code": "A215600", "name": "신라젠", "date": "2017-11-30"},
    # 2018
    {"code": "A068760", "name": "셀트리온제약", "date": "2018-05-31"},
    {"code": "A084990", "name": "헬릭스미스", "date": "2018-05-31"},
    {"code": "A028050", "name": "삼성E&A", "date": "2018-05-31"},
    {"code": "A081660", "name": "휠라홀딩스", "date": "2018-08-31"},
    {"code": "A003670", "name": "포스코퓨처엠", "date": "2018-08-31"},
    {"code": "A042660", "name": "한화오션", "date": "2018-11-30"},
    {"code": "A010620", "name": "HD현대미포", "date": "2018-11-30"},
    # 2020
    {"code": "A012510", "name": "더존비즈온", "date": "2020-05-29"},
    {"code": "A196170", "name": "알테오젠", "date": "2020-05-29"},
    {"code": "A019170", "name": "신풍제약", "date": "2020-08-31"},
    {"code": "A096530", "name": "씨젠", "date": "2020-08-31"},
    {"code": "A326030", "name": "SK바이오팜", "date": "2020-11-30"},
    {"code": "A285130", "name": "SK케미칼", "date": "2020-11-30"},
    {"code": "A034020", "name": "두산에너빌리티", "date": "2020-11-30"},
    # 2021
    {"code": "A352820", "name": "하이브", "date": "2021-02-26"},
    {"code": "A006280", "name": "녹십자", "date": "2021-02-26"},
    {"code": "A011200", "name": "HMM", "date": "2021-05-27"},
    {"code": "A011790", "name": "SKC", "date": "2021-05-27"},
    {"code": "A302440", "name": "SK바이오사이언스", "date": "2021-08-31"},
    {"code": "A361610", "name": "SK아이이테크놀로지", "date": "2021-08-31"},
    {"code": "A247540", "name": "에코프로비엠", "date": "2021-08-31"},
    {"code": "A293490", "name": "카카오게임즈", "date": "2021-08-31"},
    {"code": "A259960", "name": "크래프톤", "date": "2021-11-30"},
    {"code": "A066970", "name": "엘앤에프", "date": "2021-11-30"},
    {"code": "A383220", "name": "F&F", "date": "2021-11-30"},
    {"code": "A020150", "name": "롯데에너지머티리얼즈", "date": "2021-11-30"},
    {"code": "A137310", "name": "에스디바이오센서", "date": "2021-11-30"},
    # 2022
    {"code": "A373220", "name": "LG에너지솔루션", "date": "2022-02-14"},
    {"code": "A329180", "name": "HD현대중공업", "date": "2022-05-31"},
    # 2023
    {"code": "A377300", "name": "카카오페이", "date": "2023-02-28"},
    {"code": "A012450", "name": "한화에어로스페이스", "date": "2023-05-31"},
    {"code": "A005070", "name": "코스모신소재", "date": "2023-05-31"},
    {"code": "A047050", "name": "포스코인터내셔널", "date": "2023-05-31"},
    {"code": "A086520", "name": "에코프로", "date": "2023-08-31"},
    {"code": "A035900", "name": "JYP Ent.", "date": "2023-08-31"},
    {"code": "A042700", "name": "한미반도체", "date": "2023-08-31"},
    {"code": "A022100", "name": "포스코DX", "date": "2023-11-30"},
    {"code": "A001570", "name": "금양", "date": "2023-11-30"},
    # 2024
    {"code": "A450080", "name": "에코프로머티", "date": "2024-02-29"},
    {"code": "A180640", "name": "한진칼", "date": "2024-02-29"},
    {"code": "A348370", "name": "엔켐", "date": "2024-05-31"},
    {"code": "A267260", "name": "HD현대일렉트릭", "date": "2024-05-31"},
    {"code": "A010120", "name": "LS ELECTRIC", "date": "2024-08-30"},
]

def main():
    print("=" * 125)
    print("🔬 [실측 연구] MSCI 한국 지수 신규 편입 이후 개월수별 주가 수익률 및 감쇠 궤적 분석")
    print("=" * 125)

    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"

    df_idx = pd.read_parquet(idx_path)
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u180.index

    codes = list(set([x["code"] for x in MSCI_INCLUSIONS]))
    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close'],
        filters=[('code', 'in', codes)]
    )
    adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()

    records = []

    # 분석 대상 지평 (거래일 기준)
    # 편입 전: -60일(-3M), -40일(-2M), -20일(-1M)
    # 편입 후: +20일(+1M), +40일(+2M), +60일(+3M), +120일(+6M), +180일(+9M), +250일(+12M)
    horizons = {
        "pre_3m": -60,
        "pre_2m": -40,
        "pre_1m": -20,
        "post_1m": 20,
        "post_2m": 40,
        "post_3m": 60,
        "post_6m": 120,
        "post_9m": 180,
        "post_12m": 250,
    }

    for item in MSCI_INCLUSIONS:
        c = item["code"]
        name = item["name"]
        inc_date_str = item["date"]

        # 편입일과 가장 가까운 영업일 찾기
        target_dates = cal[cal >= pd.Timestamp(inc_date_str)]
        if len(target_dates) == 0:
            continue
        inc_d = target_dates[0]
        if inc_d not in adj_mat.index or c not in adj_mat.columns:
            continue

        p_0 = adj_mat.loc[inc_d, c]
        if np.isnan(p_0) or p_0 <= 0:
            continue

        inc_idx = cal.get_loc(inc_d)

        row = {
            "code": c,
            "name": name,
            "편입일": inc_d.strftime("%Y-%m-%d"),
            "편입가": p_0
        }

        # 편입 전 수익률 (편입일까지의 사전 상승률)
        for h_key in ["pre_3m", "pre_2m", "pre_1m"]:
            offset = horizons[h_key]
            p_idx = inc_idx + offset
            if p_idx >= 0 and p_idx < len(cal):
                d_past = cal[p_idx]
                p_past = adj_mat.loc[d_past, c] if d_past in adj_mat.index else np.nan
                if not np.isnan(p_past) and p_past > 0:
                    row[h_key] = (p_0 / p_past - 1.0) * 100.0
                else:
                    row[h_key] = np.nan
            else:
                row[h_key] = np.nan

        # 편입 후 개월수별 수익률
        for h_key in ["post_1m", "post_2m", "post_3m", "post_6m", "post_9m", "post_12m"]:
            offset = horizons[h_key]
            f_idx = inc_idx + offset
            if f_idx < len(cal):
                d_future = cal[f_idx]
                p_future = adj_mat.loc[d_future, c] if d_future in adj_mat.index else np.nan
                if not np.isnan(p_future) and p_future > 0:
                    row[h_key] = (p_future / p_0 - 1.0) * 100.0
                else:
                    row[h_key] = np.nan
            else:
                row[h_key] = np.nan

        # 편입 이후 1년 내 최고점 및 최저점 추적
        end_idx = min(len(cal) - 1, inc_idx + 250)
        future_cal = cal[inc_idx : end_idx + 1]
        future_px = adj_mat.loc[future_cal, c].dropna()

        if len(future_px) > 1:
            max_p = future_px.max()
            min_p = future_px.min()
            row["max_gain"] = (max_p / p_0 - 1.0) * 100.0
            row["max_drop"] = (min_p / p_0 - 1.0) * 100.0
            row["days_to_peak"] = (future_px.idxmax() - inc_d).days
        else:
            row["max_gain"] = np.nan
            row["max_drop"] = np.nan
            row["days_to_peak"] = np.nan

        records.append(row)

    df_res = pd.DataFrame(records)
    out_csv = f"{RESEARCH_DIR}/msci_post_inclusion_stats.csv"
    df_res.to_csv(out_csv, index=False)
    print(f"✅ 분석 완료: 총 {len(df_res)}개 MSCI 신규 편입 케이스 (저장: {out_csv})")

    # 1. 시점별 평균 / 중앙값 / 승률(플러스 비율) 통계 테이블
    stat_keys = [
        ("pre_3m", "편입 전 -3개월"),
        ("pre_2m", "편입 전 -2개월"),
        ("pre_1m", "편입 전 -1개월"),
        ("post_1m", "편입 후 +1개월"),
        ("post_2m", "편입 후 +2개월"),
        ("post_3m", "편입 후 +3개월"),
        ("post_6m", "편입 후 +6개월"),
        ("post_9m", "편입 후 +9개월"),
        ("post_12m", "편입 후 +12개월"),
    ]

    summary_rows = []
    for k, label in stat_keys:
        s = df_res[k].dropna()
        n = len(s)
        mean_v = s.mean()
        med_v = s.median()
        win_rate = (s > 0).sum() / n * 100.0 if n > 0 else 0.0
        q25 = s.quantile(0.25)
        q75 = s.quantile(0.75)
        summary_rows.append({
            "시점 구분": label,
            "표본수": f"{n}개",
            "평균 수익률": f"{mean_v:+.1f}%",
            "중앙값": f"{med_v:+.1f}%",
            "상승 확률(승률)": f"{win_rate:.1f}%",
            "하위 25%": f"{q25:+.1f}%",
            "상위 25%": f"{q75:+.1f}%"
        })

    df_sum = pd.DataFrame(summary_rows)

    print("\n" + "=" * 115)
    print("📊 [MSCI 편입 전후 시점별 주가 수익률 및 상승 확률 통계]")
    print("=" * 115)
    print(df_sum.to_string(index=False))
    print("=" * 115)

    # 2. 편입 이후 최고점 및 최대 낙폭
    m_gain = df_res["max_gain"].dropna()
    m_drop = df_res["max_drop"].dropna()
    d_peak = df_res["days_to_peak"].dropna()

    print("\n" + "=" * 115)
    print("🎯 [편입 이후 1년 내 주가 극값 통계]")
    print("=" * 115)
    print(f"• 편입 이후 추가 최고점 상승률 평균: {m_gain.mean():+.1f}% (중앙값: {m_gain.median():+.1f}%)")
    print(f"• 편입 이후 최대 낙폭(MDD) 평균: {m_drop.mean():.1f}% (중앙값: {m_drop.median():.1f}%)")
    print(f"• 편입일로부터 최고점 도달까지 소요 일수 중앙값: {d_peak.median():.0f}일 (약 {d_peak.median()/30:.1f}개월)")
    print("=" * 115)

    # 3. 대표 종목별 상세 궤적 (에코프로, 신풍제약, 포스코DX, 알테오젠, 한미반도체 등)
    picks = ["에코프로", "포스코DX", "신풍제약", "알테오젠", "한미반도체", "HD현대일렉트릭", "HMM", "하이브", "한미약품", "카카오"]
    sub_picks = df_res[df_res['name'].isin(picks)][['name', '편입일', 'pre_3m', 'post_1m', 'post_3m', 'post_6m', 'post_12m', 'max_drop']]
    print("\n" + "=" * 115)
    print("🌟 [주요 슈퍼스타 종목들의 MSCI 편입 전후 궤적]")
    print("=" * 115)
    print(sub_picks.to_string(index=False, float_format=lambda x: f"{x:+.1f}%" if isinstance(x, float) else str(x)))
    print("=" * 115)

if __name__ == "__main__":
    main()
