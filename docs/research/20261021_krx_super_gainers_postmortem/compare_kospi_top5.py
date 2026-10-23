"""
[EXP-KRX-010] 코스피 시총 상위 5대 종목 vs 스마트머니 스몰캡 5-슬롯 스윙 시스템 비교
======================================================================================
- 분석 기간: 2013-01-02 ~ 2024-12-30 (12개년)
- 비교 대상:
  1. 스마트머니 스몰캡 5-슬롯 스윙 시스템 (EXP-009)
  2. 코스피 시가총액 상위 5개 종목 동일가중 포트폴리오 (KOSPI Top 5 Equal Weight)
  3. 코스피 시총 상위 개별 대형주들: 삼성전자, SK하이닉스, 현대차, 네이버 등
  4. 코스피 200 지수 (KRX U180) & 코스닥 종합 지수 (KRX U201)
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

def compare_with_kospi_top5():
    # 1. 스몰캡 5-슬롯 시스템 자산곡선 로드
    sys_res = pd.read_csv(f"{RESEARCH_DIR}/selective_5slots_results.csv", parse_dates=["Date"]).set_index("Date")
    cal = sys_res.index

    # 2. 데이터 로드
    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    name_map = dict(zip(uni['code'], uni['name']))
    kospi_codes = set(uni[(uni['market'] == 'KOSPI') & (uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])

    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close'],
        filters=[('code', 'in', list(kospi_codes)), ('date', '>=', cal[0]), ('date', '<=', cal[-1])]
    )
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap'],
        filters=[('code', 'in', list(kospi_codes)), ('date', '>=', cal[0]), ('date', '<=', cal[-1])]
    )

    adj_mat = df_p.pivot(index='date', columns='code', values='close').reindex(cal).ffill()
    mcap_mat = df_ext.pivot(index='date', columns='code', values='market_cap').reindex(cal).ffill()
    ret_mat = adj_mat.pct_change().fillna(0.0)

    # 3. 코스피 동적 시총 상위 5개 종목 동일가중 포트폴리오 (매월 초 리밸런싱)
    monthly_dates = []
    prev_m = None
    for d in cal:
        m = (d.year, d.month)
        if prev_m is not None and m != prev_m:
            monthly_dates.append(d)
        prev_m = m
    monthly_dates = [cal[0]] + monthly_dates
    monthly_set = set(monthly_dates)

    eq_top5 = pd.Series(index=cal, dtype=float)
    eq_top5.iloc[0] = 1.0

    curr_top5 = []
    for i in range(1, len(cal)):
        d = cal[i]
        prev_d = cal[i-1]
        prev_eq = eq_top5.iloc[i-1]

        if d in monthly_set:
            # 직전일 시총 상위 5개 종목
            top5 = mcap_mat.loc[prev_d].dropna().sort_values(ascending=False).head(5).index.tolist()
            curr_top5 = top5

        day_rets = [ret_mat.loc[d, c] if c in ret_mat.columns else 0.0 for c in curr_top5]
        port_ret = np.mean(day_rets) if day_rets else 0.0
        eq_top5.iloc[i] = prev_eq * (1.0 + port_ret)

    # 4. 코스피 상위 대표 5대 종목 개별 자산곡선
    # 삼성전자(A005930), SK하이닉스(A000660), 현대차(A005380), NAVER(A035420), 기아(A000270)
    rep_stocks = {
        "삼성전자": "A005930",
        "SK하이닉스": "A000660",
        "현대차": "A005380",
        "NAVER": "A035420",
        "기아": "A000270"
    }
    rep_eqs = {}
    for s_name, code in rep_stocks.items():
        if code in adj_mat.columns:
            s_px = adj_mat[code].dropna()
            common_idx = cal.intersection(s_px.index)
            rep_eqs[s_name] = s_px.loc[common_idx] / s_px.loc[common_idx].iloc[0]

    # 성과 지표 산출 함수
    def calc_metrics(eq_s):
        tot_ret = (eq_s.iloc[-1] / eq_s.iloc[0]) - 1.0
        years = (eq_s.index[-1] - eq_s.index[0]).days / 365.25
        cagr = (eq_s.iloc[-1] / eq_s.iloc[0]) ** (1.0 / years) - 1.0
        daily_r = eq_s.pct_change().dropna()
        vol = daily_r.std() * np.sqrt(252)
        sharpe = (cagr - 0.02) / vol if vol > 0 else 0.0
        dd = (eq_s - eq_s.cummax()) / eq_s.cummax()
        mdd = dd.min()
        return tot_ret, cagr, mdd, vol, sharpe

    # 출력
    print("=" * 125)
    print("📊 [코스피 시총 상위 5대 종목 vs 스마트머니 스몰캡 5-슬롯 시스템 실측 비교 (2013~2024, 12개년)]")
    print("=" * 125)
    header = f"{'자산 / 전략 명칭':<32} | {'12년 누적수익':>12} | {'CAGR':>8} | {'MDD':>9} | {'연변동성':>8} | {'샤프 지수':>7}"
    print(header)
    print("-" * 125)

    all_series = [
        ("SK하이닉스 (A000660)", rep_eqs.get("SK하이닉스")),
        ("코스피 시총 상위 5개사 동일가중 (Top 5)", eq_top5),
        ("삼성전자 (A005930)", rep_eqs.get("삼성전자")),
        ("기아 (A000270)", rep_eqs.get("기아")),
        ("스마트캡 초엄격 5-슬롯 시스템", sys_res["Selective_5Slots_System"]),
        ("현대차 (A005380)", rep_eqs.get("현대차")),
        ("NAVER (A035420)", rep_eqs.get("NAVER")),
        ("벤치마크 (코스피 200 지수 U180)", sys_res["KOSPI200"]),
        ("벤치마크 (코스닥 종합 지수 U201)", sys_res["KOSDAQ"]),
    ]

    for name, s in all_series:
        if s is not None and len(s) > 10:
            tot, cagr, mdd, vol, shp = calc_metrics(s)
            print(f"{name:<32} | {tot*100:>11.1f}% | {cagr*100:>7.2f}% | {mdd*100:>8.2f}% | {vol*100:>7.2f}% | {shp:>7.2f}")
    print("=" * 125)

    # 연도별 수익률 비교 매트릭스
    years = sorted(list(set(cal.year)))
    rows = []
    for y in years:
        idx_y = cal[cal.year == y]
        if len(idx_y) < 5:
            continue
        def get_yr_ret(s):
            sub = s.loc[idx_y]
            return (sub.iloc[-1] / sub.iloc[0]) - 1.0

        r_sys = get_yr_ret(sys_res["Selective_5Slots_System"])
        r_top5 = get_yr_ret(eq_top5)
        r_sec = get_yr_ret(rep_eqs["삼성전자"])
        r_hynix = get_yr_ret(rep_eqs["SK하이닉스"])
        r_k200 = get_yr_ret(sys_res["KOSPI200"])

        rows.append({
            "Year": y,
            "SmartCap_System": r_sys,
            "KOSPI_Top5_EW": r_top5,
            "Samsung_Elec": r_sec,
            "SK_Hynix": r_hynix,
            "KOSPI200": r_k200
        })

    df_annual = pd.DataFrame(rows)
    print("\n📅 [연도별 수익률 비교 매트릭스 (2013~2024)]")
    print("-" * 115)
    print(f"{'연도':<6} | {'스마트캡 5슬롯':>14} | {'코스피 Top5 동일가중':>18} | {'삼성전자':>12} | {'SK하이닉스':>12} | {'코스피 200':>12}")
    print("-" * 115)
    for _, r in df_annual.iterrows():
        y = int(r["Year"])
        s_sys = f"{r['SmartCap_System']*100:>+6.1f}%"
        s_top5 = f"{r['KOSPI_Top5_EW']*100:>+6.1f}%"
        s_sec = f"{r['Samsung_Elec']*100:>+6.1f}%"
        s_hyn = f"{r['SK_Hynix']*100:>+6.1f}%"
        s_k2 = f"{r['KOSPI200']*100:>+6.1f}%"
        print(f"{y:<6} | {s_sys:>14} | {s_top5:>18} | {s_sec:>12} | {s_hyn:>12} | {s_k2:>12}")
    print("=" * 115)

if __name__ == "__main__":
    compare_with_kospi_top5()
