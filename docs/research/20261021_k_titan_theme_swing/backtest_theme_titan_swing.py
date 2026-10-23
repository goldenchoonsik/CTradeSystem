"""
[EXP-KRX-012] K-Titan 테마 대장주 스윙 시스템 실측 백테스트
=============================================================
- 분석 기간: 2013-01-02 ~ 2024-12-30 (12개년 전수 조사)
- 슬롯 구조: 5개 슬롯 (각 20% 고정 배분, Fixed 20% Allocation, 인내형 현금 대기)
- 비교 대상:
  1. [Model 0: KOSPI Top 5 Baseline (기존 K-Titan)]: 매월 초 코스피 시총 1~5위 고정
  2. [Model 1: K-Titan Theme Leaders (테마/섹터 대표 대장주 풀)]: 테마/업종별 거래대금+시총 1위 대장주 풀 (15개 종목 풀)
  3. [Model 2: Dynamic Market Titans (동적 주도 테마 대장주)]: 매월 20일 거래대금 상위 + 60일 RS 상위 테마 주도주 풀 (Top 10 풀)

- 거래비용: 왕복 0.27% 엄격 차감
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
RESEARCH_DIR = f"{ROOT}/docs/research/20261021_k_titan_theme_swing"

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
    
    beta = np.nan
    if benchmark_eq is not None:
        bm_rets = benchmark_eq.pct_change().dropna()
        common = daily_rets.index.intersection(bm_rets.index)
        if len(common) > 10:
            r1 = daily_rets.loc[common]
            r2 = bm_rets.loc[common]
            var_bm = r2.var()
            if var_bm > 0:
                beta = r1.cov(r2) / var_bm
                
    return {
        "Total Return": total_ret,
        "CAGR": cagr,
        "Volatility": ann_vol,
        "Sharpe": sharpe,
        "MDD": mdd,
        "Beta": beta
    }

def run_simulation(model_name, universe_fn, cal, adj_mat, val_mat, mcap_mat, fgn_diff5, inst_sum5, ma20_mat, ma200_mat, rsi_mat, ret_mat, monthly_dates, name_map, tp1=0.10, tp2=0.20, sl=-0.06, trail_drop=-0.05, max_hold=40):
    num_slots = 5
    slot_weight = 1.0 / num_slots
    cost_rate = 0.0027  # 왕복 0.27%
    daily_cash_ret = 0.025 / 252.0

    start_pos = cal.get_loc(monthly_dates[0])
    eq_series = pd.Series(index=cal, dtype=float)
    eq_series.iloc[:start_pos] = 1.0

    slots = [{"ticker": None, "entry_price": 0.0, "peak_price": 0.0, "holding_days": 0, "remaining_ratio": 1.0, "is_cash": True} for _ in range(num_slots)]
    trade_logs = []
    current_universe = []

    monthly_set = set(monthly_dates)

    for i in range(start_pos, len(cal)):
        d = cal[i]
        prev_d = cal[i-1]
        prev_eq = eq_series.iloc[i-1]

        # 1. 월초 사전 대장주 유니버스 리밸런싱
        if d in monthly_set:
            current_universe = universe_fn(prev_d)

        # 2. 신규 진입 검사
        empty_slot_indices = [idx for idx, s in enumerate(slots) if s["is_cash"] or s["ticker"] is None]
        if empty_slot_indices and current_universe:
            currently_held = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]

            eligible = []
            for code in current_universe:
                if code in currently_held:
                    continue
                p = adj_mat.loc[prev_d, code] if code in adj_mat.columns else np.nan
                ma20 = ma20_mat.loc[prev_d, code] if code in ma20_mat.columns else np.nan
                ma200 = ma200_mat.loc[prev_d, code] if code in ma200_mat.columns else np.nan
                rsi = rsi_mat.loc[prev_d, code] if code in rsi_mat.columns else np.nan

                if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
                    continue

                # 진입 조건: 200일선 85% 이상 AND (20일선 눌림목 OR RSI<=42)
                cond_loc = (p / ma200 >= 0.85) and ((0.95 <= p / ma20 <= 1.03) or (rsi <= 42.0))
                
                # 수급 조건: 외인 지분 증가 OR 기관 순매수
                f_flow = fgn_diff5.loc[prev_d, code] if code in fgn_diff5.columns else 0.0
                i_flow = inst_sum5.loc[prev_d, code] if code in inst_sum5.columns else 0.0
                cond_flow = (f_flow > 0.0) or (i_flow > 0.0)

                if cond_loc and cond_flow:
                    eligible.append(code)

            for slot_idx, ticker in zip(empty_slot_indices, eligible):
                p_entry = adj_mat.loc[d, ticker] if ticker in adj_mat.columns else np.nan
                if not np.isnan(p_entry) and p_entry > 0:
                    slots[slot_idx] = {
                        "ticker": ticker,
                        "entry_price": p_entry,
                        "peak_price": p_entry,
                        "holding_days": 0,
                        "remaining_ratio": 1.0,
                        "is_cash": False
                    }
                    prev_eq *= (1.0 - slot_weight * 0.00035)

        # 3. 보유 슬롯 일별 청산 검사
        slot_returns = []
        for s in slots:
            if s["is_cash"] or s["ticker"] is None:
                slot_returns.append(daily_cash_ret)
                continue

            t = s["ticker"]
            r_day = ret_mat.loc[d, t] if t in ret_mat.columns else 0.0
            curr_px = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan

            if np.isnan(curr_px) or curr_px <= 0:
                slot_returns.append(0.0)
                continue

            s["holding_days"] += 1
            if curr_px > s["peak_price"]:
                s["peak_price"] = curr_px

            gain_from_entry = (curr_px / s["entry_price"]) - 1.0
            drop_from_peak = (curr_px / s["peak_price"]) - 1.0

            action = None
            exit_return = 0.0

            # 룰 A: 2차 전량 익절
            if gain_from_entry >= tp2:
                action = f"🎯 +{int(tp2*100)}% 2차익절"
                exit_return = tp2 - cost_rate
            # 룰 B: 1차 분할 익절 (+10% 도달 시 절반 매도)
            elif gain_from_entry >= tp1 and s["remaining_ratio"] == 1.0:
                s["remaining_ratio"] = 0.5
                trade_logs.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "code": t,
                    "name": name_map.get(t, t),
                    "gain_pct": gain_from_entry * 100.0,
                    "holding_days": s["holding_days"],
                    "reason": f"🎯 +{int(tp1*100)}% 1차 분할익절"
                })
                # 이번 봉에는 잔여 50%만 유지
                slot_returns.append((tp1 - cost_rate) * 0.5 + r_day * 0.5)
                continue
            # 룰 C: 트레일링 스탑 (최고점 대비 -5% 밀림, 최소 1차 익절 이상 수익 보호)
            elif gain_from_entry >= tp1 and drop_from_peak <= trail_drop:
                action = f"🛡️ 트레일링 스탑 ({drop_from_peak*100:.1f}%)"
                exit_return = gain_from_entry - cost_rate
            # 룰 D: 손절선 (칼손절)
            elif gain_from_entry <= sl:
                action = f"🛑 {int(sl*100)}% 손절"
                exit_return = sl - cost_rate
            # 룰 E: 시간 만기
            elif s["holding_days"] >= max_hold:
                action = f"⏱️ {max_hold}일 만기"
                exit_return = gain_from_entry - cost_rate

            if action is not None:
                slot_returns.append(exit_return * s["remaining_ratio"])
                trade_logs.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "code": t,
                    "name": name_map.get(t, t),
                    "gain_pct": gain_from_entry * 100.0,
                    "holding_days": s["holding_days"],
                    "reason": action
                })
                s["is_cash"] = True
                s["ticker"] = None
            else:
                slot_returns.append(r_day * s["remaining_ratio"])

        eq_series.iloc[i] = prev_eq * (1.0 + sum(slot_returns) * slot_weight)

    df_trades = pd.DataFrame(trade_logs)
    win_rate = (df_trades["gain_pct"] > 0).mean() if len(df_trades) > 0 else 0.0
    wins = df_trades[df_trades["gain_pct"] > 0]["gain_pct"]
    losses = df_trades[df_trades["gain_pct"] <= 0]["gain_pct"]
    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 1.0
    profit_factor = (wins.sum() / abs(losses.sum())) if len(losses) > 0 and abs(losses.sum()) > 0 else 0.0

    return {
        "name": model_name,
        "equity": eq_series,
        "trades": df_trades,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "total_trades": len(df_trades)
    }

def main():
    print("=" * 110)
    print("🚀 [K-Titan 테마 대장주 스윙] 3대 모델 12개년 비교 백테스트 (2013~2024)")
    print("=" * 110)

    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    name_map = dict(zip(uni['code'], uni['name']))
    kospi_codes = set(uni[(uni['market'] == 'KOSPI') & (uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])
    all_codes = set(uni[(uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])

    df_idx = pd.read_parquet(idx_path)
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u180.loc[(u180.index >= '2013-01-01') & (u180.index <= '2024-12-31')].index
    print(f"📅 검증 기간: {cal[0].strftime('%Y-%m-%d')} ~ {cal[-1].strftime('%Y-%m-%d')} ({len(cal):,} 거래일)")

    print("⏳ 가격 및 수급 데이터 로딩 중...")
    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'value'],
        filters=[('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )
    df_p = df_p[df_p['code'].isin(all_codes)]

    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap', 'foreign_hold_pct', 'inst_net_qty'],
        filters=[('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )
    df_ext = df_ext[df_ext['code'].isin(all_codes)]

    print("⏳ 피벗 행렬 구축 중...")
    adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()
    val_mat = df_p.pivot(index='date', columns='code', values='value').sort_index()
    mcap_mat = df_ext.pivot(index='date', columns='code', values='market_cap').sort_index()
    fgn_mat = df_ext.pivot(index='date', columns='code', values='foreign_hold_pct').sort_index()
    inst_mat = df_ext.pivot(index='date', columns='code', values='inst_net_qty').sort_index()

    ret_mat = adj_mat.pct_change().fillna(0.0)

    # 기술적 지표 사전 계산
    ma20_mat = adj_mat.rolling(20, min_periods=10).mean()
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()

    # RSI(14)
    delta = adj_mat.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rs = ema_up / ema_down
    rsi_mat = 100 - (100 / (1 + rs))

    # 수급 지표 (최근 5일 외인 지분율 변동, 기관 순매수 합)
    fgn_diff5 = fgn_mat.diff(5)
    inst_sum5 = inst_mat.rolling(5, min_periods=3).sum()

    # 20일 평균 거래대금 & 60일 수익률
    val_ma20 = val_mat.rolling(20, min_periods=10).mean()
    ret60_mat = adj_mat.pct_change(60).fillna(0.0)

    # 벤치마크 (KOSPI 200)
    bm_k200 = u180.loc[cal] / u180.loc[cal].iloc[0]

    # 매월 첫 거래일 추출
    monthly_dates = []
    prev_m = None
    for d in cal:
        m = (d.year, d.month)
        if prev_m is not None and m != prev_m:
            monthly_dates.append(d)
        prev_m = m
    warmup_d = cal[cal >= '2013-03-01'][0]
    monthly_dates = [d for d in monthly_dates if d >= warmup_d]

    # -------------------------------------------------------------
    # 유니버스 생성 함수 정의
    # -------------------------------------------------------------
    # Model 0: 코스피 시총 상위 5대 종목 (K-Titan 원본)
    def universe_model0(as_of_date):
        mcap_row = mcap_mat.loc[as_of_date, list(kospi_codes.intersection(mcap_mat.columns))].dropna()
        return mcap_row.sort_values(ascending=False).head(5).index.tolist()

    # Model 1: 고정 테마/섹터별 1등 대장주 풀 (Representative Theme Titans)
    # 대표 8대 테마 대장주 풀 (반도체, 2차전지, 제약바이오, 자동차, 방산/기계, IT플랫폼, 금융/지주, 원전/전력)
    # 각 테마별 후보군 중 해당 시점 시가총액 & 거래대금 1위 종목을 선정하여 총 8~10개 풀 구성
    theme_candidates = {
        "반도체": ["A005930", "A000660", "A042700"],               # 삼전, 하이닉스, 한미반도체
        "2차전지/배터리": ["A373220", "A006400", "A051910", "A086520"], # LG엔솔, 삼성SDI, LG화학, 에코프로
        "제약/바이오": ["A207940", "A068270", "A196170"],           # 삼바, 셀트리온, 알테오젠
        "자동차/모빌리티": ["A005380", "A000270", "A012330"],         # 현대차, 기아, 현대모비스
        "방산/기계/조선": ["A012450", "A042660", "A010140", "A267260"], # 한화에어로, 대우조선/한화오션, 삼성중공업, HD현대일렉
        "IT/인터넷/플랫폼": ["A035420", "A035720"],                 # NAVER, 카카오
        "전력기기/원전": ["A267260", "A034020", "A052690"],         # HD현대일렉트릭, 두산에너빌리티, 한전기술
        "금융/지주": ["A105560", "A055550", "A086790"]             # KB금융, 신한지주, 하나금융
    }
    
    def universe_model1(as_of_date):
        pool = []
        for theme, codes in theme_candidates.items():
            valid_codes = [c for c in codes if c in mcap_mat.columns and as_of_date in mcap_mat.index and not np.isnan(mcap_mat.loc[as_of_date, c])]
            if not valid_codes:
                continue
            # 해당 테마 내 시총 1위 대장주 선별
            best = max(valid_codes, key=lambda c: mcap_mat.loc[as_of_date, c])
            pool.append(best)
        return pool

    # Model 2: 동적 시장 주도 테마 대장주 (Dynamic Market Leader Titans)
    # 사전 룰: 최근 20일 일평균 거래대금 상위 30개 중, 60일 상대강도(수익률) 상위 10개 종목을 테마 대장주 풀로 선정
    def universe_model2(as_of_date):
        if as_of_date not in val_ma20.index or as_of_date not in ret60_mat.index:
            return []
        v_row = val_ma20.loc[as_of_date].dropna()
        # 거래대금 상위 30개
        top_val_codes = v_row.sort_values(ascending=False).head(30).index
        # 그 중 60일 수익률이 양수이고 가장 높은 상위 10개 종목 선정
        r_sub = ret60_mat.loc[as_of_date, top_val_codes].dropna()
        r_positive = r_sub[r_sub > 0.0]
        leaders = r_positive.sort_values(ascending=False).head(10).index.tolist()
        return leaders

    print("\n⚡ 백테스트 시뮬레이션 가동 중...")
    
    print("1. [Model 0] KOSPI Top 5 Baseline (기존 K-Titan) 실행...")
    res0 = run_simulation("K-Titan (KOSPI Top 5 Baseline)", universe_model0, cal, adj_mat, val_mat, mcap_mat, fgn_diff5, inst_sum5, ma20_mat, ma200_mat, rsi_mat, ret_mat, monthly_dates, name_map)
    
    print("2. [Model 1] K-Titan Theme Leaders (8대 섹터/테마 대표 대장주 풀) 실행...")
    res1 = run_simulation("K-Titan Theme Leaders (8대 섹터 대장주)", universe_model1, cal, adj_mat, val_mat, mcap_mat, fgn_diff5, inst_sum5, ma20_mat, ma200_mat, rsi_mat, ret_mat, monthly_dates, name_map)
    
    print("3. [Model 2] Dynamic Market Titans (동적 주도 테마 대장주 풀) 실행...")
    res2 = run_simulation("Dynamic Market Titans (동적 거래대금/모멘텀 대장주)", universe_model2, cal, adj_mat, val_mat, mcap_mat, fgn_diff5, inst_sum5, ma20_mat, ma200_mat, rsi_mat, ret_mat, monthly_dates, name_map, tp1=0.12, tp2=0.25, sl=-0.07, max_hold=35)

    print("\n" + "=" * 110)
    print("🏆 [최종 성과 비교 결과] 12개년 (2013~2024) 실측 통계")
    print("=" * 110)

    rows = []
    models = [res0, res1, res2]
    for m in models:
        metrics = compute_metrics(m["equity"], bm_k200)
        rows.append({
            "전략 모델": m["name"],
            "누적 수익률": f"{metrics['Total Return']*100:+.1f}%",
            "CAGR (연복리)": f"{metrics['CAGR']*100:.2f}%",
            "MDD (최대낙폭)": f"{metrics['MDD']*100:.1f}%",
            "샤프 지수": f"{metrics['Sharpe']:.2f}",
            "승률 (Win Rate)": f"{m['win_rate']*100:.1f}%",
            "손익비 (P/F)": f"{m['profit_factor']:.2f}",
            "총 매매횟수": f"{m['total_trades']}회",
            "평균수익/평균손실": f"+{m['avg_win']:.1f}% / -{m['avg_loss']:.1f}%"
        })

    bm_metrics = compute_metrics(bm_k200)
    rows.append({
        "전략 모델": "KOSPI 200 지수 (Buy & Hold)",
        "누적 수익률": f"{bm_metrics['Total Return']*100:+.1f}%",
        "CAGR (연복리)": f"{bm_metrics['CAGR']*100:.2f}%",
        "MDD (최대낙폭)": f"{bm_metrics['MDD']*100:.1f}%",
        "샤프 지수": f"{bm_metrics['Sharpe']:.2f}",
        "승률 (Win Rate)": "-",
        "손익비 (P/F)": "-",
        "총 매매횟수": "-",
        "평균수익/평균손실": "-"
    })

    df_summary = pd.DataFrame(rows)
    print(df_summary.to_string(index=False))

    # 연도별 성과 비교
    print("\n📅 [연도별 연간 수익률 (Annual Returns)]")
    annual_dict = {}
    years = sorted(list(set(cal.year)))
    for y in years:
        sub_cal = cal[cal.year == y]
        if len(sub_cal) < 10:
            continue
        row = {}
        for m in models:
            sub_eq = m["equity"].loc[sub_cal]
            row[m["name"]] = (sub_eq.iloc[-1] / sub_eq.iloc[0] - 1.0) * 100.0
        sub_bm = bm_k200.loc[sub_cal]
        row["KOSPI 200"] = (sub_bm.iloc[-1] / sub_bm.iloc[0] - 1.0) * 100.0
        annual_dict[y] = row
    df_annual = pd.DataFrame(annual_dict).T
    print(df_annual.round(1).to_string())

    # 결과 CSV 저장
    df_summary.to_csv(f"{RESEARCH_DIR}/comparison_summary.csv", index=False, encoding="utf-8-sig")
    df_annual.to_csv(f"{RESEARCH_DIR}/annual_returns.csv", encoding="utf-8-sig")
    res0["trades"].to_csv(f"{RESEARCH_DIR}/trades_model0.csv", index=False, encoding="utf-8-sig")
    res1["trades"].to_csv(f"{RESEARCH_DIR}/trades_model1.csv", index=False, encoding="utf-8-sig")
    res2["trades"].to_csv(f"{RESEARCH_DIR}/trades_model2.csv", index=False, encoding="utf-8-sig")
    print(f"\n💾 결과 파일이 {RESEARCH_DIR} 에 성공적으로 저장되었습니다.")

if __name__ == "__main__":
    main()
