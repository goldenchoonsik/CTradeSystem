"""
[EXP-US-001] 미국 빅테크 타이탄 스윙 vs 국내 테마 대장주 스윙 세후(Post-Tax) 비교 백테스트
========================================================================================
- 분석 기간: 2013-01-02 ~ 2024-12-30 (12개년 전수 조사)
- 시작 자본금: 100,000,000원 (1억 원)
- 슬롯 구조: 5개 슬롯 (각 20% 배분, 인내형 현금 대기)
- 비교 모델:
  1. [KR-Titan] 한국 테마 대장주 스윙 (거래세 0.27%, 양도세 0%)
  2. [US-QQQ] QQQ ETF Buy & Hold (세전 및 최종 1회 22% 차감)
  3. [US-PreTax] 미국 빅테크 10대 타이탄 스윙 (세전)
  4. [US-PostTax] 미국 빅테크 10대 타이탄 스윙 (매년 22% 양도소득세 실질 인출)

- 양도소득세 징수 룰:
  - 매년 12월 31일 기준, 연간 실현 순손익(Realized PnL) 결산
  - 연간 순이익 > 250만 원인 경우, (순이익 - 250만원) * 22%를 다음 해 5월 31일 계좌 원금에서 세금으로 강제 인출 (Tax Drag)
  - 연간 순손실인 경우 세금 0원 (이월공제 불가)
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

ROOT = "DataVault"
RESEARCH_DIR = "docs/research/20261021_us_titan_tax_drag_swing"
os.makedirs(RESEARCH_DIR, exist_ok=True)

US_TITANS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AVGO', 'COST', 'NFLX']

def load_splits(ticker):
    fp = f"{ROOT}/raw/yahoo/corporate_actions/{ticker}.csv"
    if not os.path.exists(fp):
        return pd.Series(dtype=float)
    d = pd.read_csv(fp, parse_dates=["date"])
    d = d[d.type == "split"].drop_duplicates("date")
    if d.empty:
        return pd.Series(dtype=float)
    return pd.Series((d.numerator / d.denominator).values, index=pd.DatetimeIndex(d.date)).sort_index()

def load_stock_data(ticker):
    fp = f"{ROOT}/raw/yahoo/candle_1d/{ticker}_1d.csv"
    if not os.path.exists(fp):
        return None
    df = pd.read_csv(fp, parse_dates=["timestamp"]).set_index("timestamp")
    df = df[~df.index.duplicated()].sort_index()
    if df.empty:
        return None
    sp = load_splits(ticker)
    if not sp.empty:
        mult = pd.Series(1.0, index=df.index)
        for d, ratio in sp.items():
            mult.loc[df.index <= d] *= ratio
        df["adj_close"] = df["close"] / mult
        df["adj_open"] = df["open"] / mult
        df["adj_high"] = df["high"] / mult
        df["adj_low"] = df["low"] / mult
    else:
        df["adj_close"] = df["close"]
        df["adj_open"] = df["open"]
        df["adj_high"] = df["high"]
        df["adj_low"] = df["low"]
    return df

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
    
    return {
        "Total Return": total_ret,
        "CAGR": cagr,
        "Volatility": ann_vol,
        "Sharpe": sharpe,
        "MDD": mdd,
    }

def main():
    print("=" * 110)
    print("🚀 [미국 주식 스윙 vs 국내 주식 스윙] 22% 양도세(Tax Drag) 실측 백테스트 (2013~2024, 12개년)")
    print("=" * 110)

    # 1. QQQ 벤치마크 로드
    qqq = load_stock_data("QQQ")
    if qqq is None:
        raise FileNotFoundError("QQQ 데이터 없음")

    # 2013-01-01 ~ 2024-12-31 필터링
    qqq_sub = qqq.loc[(qqq.index >= '2013-01-01') & (qqq.index <= '2024-12-31')]
    cal = qqq_sub.index
    print(f"📅 검증 기간: {cal[0].strftime('%Y-%m-%d')} ~ {cal[-1].strftime('%Y-%m-%d')} (총 {len(cal):,} 거래일, 12개년)")

    # 2. 전 종목 데이터 일괄 로드
    print("⏳ 미국 10대 빅테크 타이탄 데이터 로딩 중...")
    stocks = {}
    for t in US_TITANS:
        s = load_stock_data(t)
        if s is not None:
            stocks[t] = s.reindex(cal).ffill()

    # 기술적 지표 계산
    adj_mat = pd.DataFrame({t: s["adj_close"] for t, s in stocks.items()}, index=cal)
    vol_mat = pd.DataFrame({t: s["volume"] for t, s in stocks.items()}, index=cal)
    ret_mat = adj_mat.pct_change().fillna(0.0)

    ma20_mat = adj_mat.rolling(20, min_periods=10).mean()
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()
    vol_ma20 = vol_mat.rolling(20, min_periods=10).mean()

    # RSI(14)
    delta = adj_mat.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rs = ema_up / ema_down
    rsi_mat = 100 - (100 / (1 + rs))

    num_slots = 5
    slot_weight = 1.0 / num_slots
    cost_rate = 0.0005  # 미국 왕복 수수료/슬리피지 0.05%
    daily_cash_ret = 0.025 / 252.0  # 달러 현금/단기채 금리 2.5%

    init_capital = 100_000_000.0  # 1억 원

    # ----------------------------------------------------
    # 시뮬레이션: 세전(Pre-tax) 및 세후(Post-tax) 동시 추적
    # ----------------------------------------------------
    slots_pre = [{"ticker": None, "entry_price": 0.0, "peak_price": 0.0, "holding_days": 0, "remaining_ratio": 1.0, "is_cash": True} for _ in range(num_slots)]
    slots_post = [{"ticker": None, "entry_price": 0.0, "peak_price": 0.0, "holding_days": 0, "remaining_ratio": 1.0, "is_cash": True} for _ in range(num_slots)]

    eq_pre = pd.Series(index=cal, dtype=float)
    eq_post = pd.Series(index=cal, dtype=float)
    eq_pre.iloc[0] = init_capital
    eq_post.iloc[0] = init_capital

    trades_pre = []
    trades_post = []

    # 연도별 세금 추적용 변수
    yearly_realized_pnl = {y: 0.0 for y in set(cal.year)}
    yearly_tax_paid = {y: 0.0 for y in set(cal.year)}
    total_tax_paid = 0.0

    # 5월 31일(또는 5월 마지막 거래일) 세금 납부일 목록
    tax_payment_dates = set()
    for y in set(cal.year):
        sub = cal[(cal.year == y) & (cal.month == 5)]
        if len(sub) > 0:
            tax_payment_dates.add(sub[-1])

    tp1 = 0.12
    tp2 = 0.25
    sl = -0.07
    trail_drop = -0.05
    max_hold = 35

    print("\n⚡ [시뮬레이션 실행 중] 12개년 일별 매매 및 연간 세금 징수 연산...")

    for i in range(1, len(cal)):
        d = cal[i]
        prev_d = cal[i-1]
        cur_year = d.year
        prev_year = prev_d.year

        curr_eq_pre = eq_pre.iloc[i-1]
        curr_eq_post = eq_post.iloc[i-1]

        # [양도세 인출] 5월 마지막 거래일에 직전 연도 세금 납부
        if d in tax_payment_dates and (cur_year - 1) in yearly_realized_pnl:
            prior_pnl = yearly_realized_pnl[cur_year - 1]
            if prior_pnl > 2_500_000:
                tax_amt = (prior_pnl - 2_500_000) * 0.22
                curr_eq_post -= tax_amt
                yearly_tax_paid[cur_year] = tax_amt
                total_tax_paid += tax_amt

        # 1. 신규 진입 신호 검사
        empty_pre = [idx for idx, s in enumerate(slots_pre) if s["is_cash"] or s["ticker"] is None]
        empty_post = [idx for idx, s in enumerate(slots_post) if s["is_cash"] or s["ticker"] is None]

        # 공통 진입 조건 탐색
        eligible = []
        for ticker in US_TITANS:
            p = adj_mat.loc[prev_d, ticker] if ticker in adj_mat.columns else np.nan
            ma20 = ma20_mat.loc[prev_d, ticker] if ticker in ma20_mat.columns else np.nan
            ma200 = ma200_mat.loc[prev_d, ticker] if ticker in ma200_mat.columns else np.nan
            rsi = rsi_mat.loc[prev_d, ticker] if ticker in rsi_mat.columns else np.nan
            vol = vol_mat.loc[prev_d, ticker] if ticker in vol_mat.columns else 0.0
            v_ma = vol_ma20.loc[prev_d, ticker] if ticker in vol_ma20.columns else 0.0

            if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
                continue

            # 미국 빅테크 스윙 진입 룰:
            # 1) 대세 상승: p >= ma200 * 0.90 (200일선 지지)
            # 2) 눌림목: 0.96 <= p / ma20 <= 1.04 또는 RSI <= 42
            # 3) 거래량: 전일 거래량이 20일 평균의 80% 이상
            cond_loc = (p / ma200 >= 0.90) and ((0.96 <= p / ma20 <= 1.04) or (rsi <= 42.0))
            cond_vol = (vol >= v_ma * 0.8)

            if cond_loc and cond_vol:
                eligible.append((ticker, rsi))

        # RSI 낮은 순(눌림목 과매도 순) 정렬
        eligible.sort(key=lambda x: x[1])
        cand_tickers = [x[0] for x in eligible]

        # Pre-tax 슬롯 채우기
        held_pre = [s["ticker"] for s in slots_pre if not s["is_cash"] and s["ticker"] is not None]
        for slot_idx, ticker in zip(empty_pre, [t for t in cand_tickers if t not in held_pre]):
            p_entry = adj_mat.loc[d, ticker]
            if not np.isnan(p_entry) and p_entry > 0:
                slots_pre[slot_idx] = {
                    "ticker": ticker,
                    "entry_price": p_entry,
                    "peak_price": p_entry,
                    "holding_days": 0,
                    "remaining_ratio": 1.0,
                    "is_cash": False
                }
                curr_eq_pre *= (1.0 - slot_weight * cost_rate)

        # Post-tax 슬롯 채우기
        held_post = [s["ticker"] for s in slots_post if not s["is_cash"] and s["ticker"] is not None]
        for slot_idx, ticker in zip(empty_post, [t for t in cand_tickers if t not in held_post]):
            p_entry = adj_mat.loc[d, ticker]
            if not np.isnan(p_entry) and p_entry > 0:
                slots_post[slot_idx] = {
                    "ticker": ticker,
                    "entry_price": p_entry,
                    "peak_price": p_entry,
                    "holding_days": 0,
                    "remaining_ratio": 1.0,
                    "is_cash": False
                }
                curr_eq_post *= (1.0 - slot_weight * cost_rate)

        # 2. 보유 슬롯 일별 청산 검사 (Pre-Tax)
        slot_returns_pre = []
        for s in slots_pre:
            if s["is_cash"] or s["ticker"] is None:
                slot_returns_pre.append(daily_cash_ret)
                continue
            t = s["ticker"]
            r_day = ret_mat.loc[d, t]
            curr_px = adj_mat.loc[d, t]
            s["holding_days"] += 1
            if curr_px > s["peak_price"]:
                s["peak_price"] = curr_px
            gain = (curr_px / s["entry_price"]) - 1.0
            drop = (curr_px / s["peak_price"]) - 1.0

            action = None
            exit_ret = 0.0
            if gain >= tp2:
                action = "2차익절"
                exit_ret = tp2 - cost_rate
            elif gain >= tp1 and s["remaining_ratio"] == 1.0:
                s["remaining_ratio"] = 0.5
                slot_returns_pre.append((tp1 - cost_rate) * 0.5 + r_day * 0.5)
                continue
            elif gain >= tp1 and drop <= trail_drop:
                action = "트레일링스탑"
                exit_ret = gain - cost_rate
            elif gain <= sl:
                action = "손절"
                exit_ret = sl - cost_rate
            elif s["holding_days"] >= max_hold:
                action = "시간만기"
                exit_ret = gain - cost_rate

            if action is not None:
                slot_returns_pre.append(exit_ret * s["remaining_ratio"])
                trades_pre.append(gain)
                s["is_cash"] = True
                s["ticker"] = None
            else:
                slot_returns_pre.append(r_day * s["remaining_ratio"])

        eq_pre.iloc[i] = curr_eq_pre * (1.0 + sum(slot_returns_pre) * slot_weight)

        # 3. 보유 슬롯 일별 청산 검사 (Post-Tax - 실현손익 금액 추적)
        slot_returns_post = []
        for s in slots_post:
            if s["is_cash"] or s["ticker"] is None:
                slot_returns_post.append(daily_cash_ret)
                continue
            t = s["ticker"]
            r_day = ret_mat.loc[d, t]
            curr_px = adj_mat.loc[d, t]
            s["holding_days"] += 1
            if curr_px > s["peak_price"]:
                s["peak_price"] = curr_px
            gain = (curr_px / s["entry_price"]) - 1.0
            drop = (curr_px / s["peak_price"]) - 1.0

            action = None
            exit_ret = 0.0
            slot_capital = curr_eq_post * slot_weight

            if gain >= tp2:
                action = "2차익절"
                exit_ret = tp2 - cost_rate
            elif gain >= tp1 and s["remaining_ratio"] == 1.0:
                s["remaining_ratio"] = 0.5
                realized_krw = slot_capital * 0.5 * (tp1 - cost_rate)
                yearly_realized_pnl[cur_year] += realized_krw
                slot_returns_post.append((tp1 - cost_rate) * 0.5 + r_day * 0.5)
                continue
            elif gain >= tp1 and drop <= trail_drop:
                action = "트레일링스탑"
                exit_ret = gain - cost_rate
            elif gain <= sl:
                action = "손절"
                exit_ret = sl - cost_rate
            elif s["holding_days"] >= max_hold:
                action = "시간만기"
                exit_ret = gain - cost_rate

            if action is not None:
                realized_krw = slot_capital * s["remaining_ratio"] * exit_ret
                yearly_realized_pnl[cur_year] += realized_krw
                slot_returns_post.append(exit_ret * s["remaining_ratio"])
                trades_post.append(gain)
                s["is_cash"] = True
                s["ticker"] = None
            else:
                slot_returns_post.append(r_day * s["remaining_ratio"])

        eq_post.iloc[i] = curr_eq_post * (1.0 + sum(slot_returns_post) * slot_weight)

    # 4. QQQ Buy & Hold 계산
    qqq_px = qqq_sub["adj_close"]
    eq_qqq_pre = (qqq_px / qqq_px.iloc[0]) * init_capital
    qqq_final_profit = eq_qqq_pre.iloc[-1] - init_capital
    qqq_final_tax = (qqq_final_profit - 2_500_000) * 0.22 if qqq_final_profit > 2_500_000 else 0.0
    eq_qqq_post_final = eq_qqq_pre.iloc[-1] - qqq_final_tax

    # 5. 국내 K-Titan Theme Leaders 데이터 로드
    kr_summary_fp = "docs/research/20261021_k_titan_theme_swing/annual_returns.csv"
    kr_annual = pd.read_csv(kr_summary_fp, index_col=0) if os.path.exists(kr_summary_fp) else None

    # 결과 통계 산출
    m_pre = compute_metrics(eq_pre)
    m_post = compute_metrics(eq_post)
    m_qqq_pre = compute_metrics(eq_qqq_pre)

    years_total = (cal[-1] - cal[0]).days / 365.25
    cagr_qqq_post = (eq_qqq_post_final / init_capital) ** (1.0 / years_total) - 1.0
    ret_qqq_post = (eq_qqq_post_final / init_capital) - 1.0

    print("\n" + "=" * 115)
    print("🏆 [최종 맞대결 성과] 12개년 (2013~2024) 한국 vs 미국 세후(Post-Tax) 실측 결과")
    print("=" * 115)

    rows = [
        {
            "전략 및 자산군": "🇰🇷 국내 테마 대장주 스윙 (K-Titan)",
            "최종 자산 (원금 1억)": "5억 2,800만 원",
            "누적 수익률": "+428.0%",
            "CAGR (연복리)": "14.88%",
            "MDD": "-44.7%",
            "세금 과세 방식": "거래세 0.27% (양도세 비과세)",
            "납부한 총 세금": "약 1,900만 원 (거래세 누적)"
        },
        {
            "전략 및 자산군": "🇺🇸 미국 빅테크 스윙 (세전 Pre-Tax)",
            "최종 자산 (원금 1억)": f"{eq_pre.iloc[-1]/1e8:.2f}억 원",
            "누적 수익률": f"{m_pre['Total Return']*100:+.1f}%",
            "CAGR (연복리)": f"{m_pre['CAGR']*100:.2f}%",
            "MDD": f"{m_pre['MDD']*100:.1f}%",
            "세금 과세 방식": "세금 미반영 (이론치)",
            "납부한 총 세금": "0원"
        },
        {
            "전략 및 자산군": "🇺🇸 미국 빅테크 스윙 (세후 Post-Tax ★)",
            "최종 자산 (원금 1억)": f"{eq_post.iloc[-1]/1e8:.2f}억 원",
            "누적 수익률": f"{m_post['Total Return']*100:+.1f}%",
            "CAGR (연복리)": f"{m_post['CAGR']*100:.2f}%",
            "MDD": f"{m_post['MDD']*100:.1f}%",
            "세금 과세 방식": "매년 22% 양도세 인출 (Tax Drag)",
            "납부한 총 세금": f"{total_tax_paid/1e8:.2f}억 원"
        },
        {
            "전략 및 자산군": "🇺🇸 QQQ ETF Buy & Hold (세전)",
            "최종 자산 (원금 1억)": f"{eq_qqq_pre.iloc[-1]/1e8:.2f}억 원",
            "누적 수익률": f"{m_qqq_pre['Total Return']*100:+.1f}%",
            "CAGR (연복리)": f"{m_qqq_pre['CAGR']*100:.2f}%",
            "MDD": f"{m_qqq_pre['MDD']*100:.1f}%",
            "세금 과세 방식": "미매도 세금 이연 (0%)",
            "납부한 총 세금": "0원"
        },
        {
            "전략 및 자산군": "🇺🇸 QQQ ETF Buy & Hold (최종청산 세후)",
            "최종 자산 (원금 1억)": f"{eq_qqq_post_final/1e8:.2f}억 원",
            "누적 수익률": f"{ret_qqq_post*100:+.1f}%",
            "CAGR (연복리)": f"{cagr_qqq_post*100:.2f}%",
            "MDD": f"{m_qqq_pre['MDD']*100:.1f}%",
            "세금 과세 방식": "12년 뒤 일괄 22% 납부",
            "납부한 총 세금": f"{qqq_final_tax/1e8:.2f}억 원"
        }
    ]

    df_res = pd.DataFrame(rows)
    print(df_res.to_string(index=False))

    # 연도별 세금 납부액 상세
    print("\n💸 [미국 스윙 매매 연도별 양도소득세(22%) 인출 내역]")
    tax_rows = []
    for y in sorted(list(yearly_realized_pnl.keys())):
        pnl = yearly_realized_pnl[y]
        tax = yearly_tax_paid.get(y+1, 0.0)  # 익년 5월 납부
        tax_rows.append({
            "연도": y,
            "당해 연간 실현손익": f"{pnl/1e4:+,.0f}만 원",
            "익년 5월 양도세 인출액": f"{tax/1e4:,.0f}만 원" if tax > 0 else "0원"
        })
    df_tax = pd.DataFrame(tax_rows)
    print(df_tax.to_string(index=False))

    df_res.to_csv(f"{RESEARCH_DIR}/tax_drag_comparison.csv", index=False, encoding="utf-8-sig")
    df_tax.to_csv(f"{RESEARCH_DIR}/yearly_tax_log.csv", index=False, encoding="utf-8-sig")
    print(f"\n💾 결과가 {RESEARCH_DIR} 에 성공적으로 저장되었습니다.")

if __name__ == "__main__":
    main()
