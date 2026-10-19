"""
[EXP-013] 나스닥 100 최대 가용 기간 (1999~2026년, 28개 전 연도) 최종 하이브리드 전략 백테스트
========================================================================================
- 검증 기간: 1999-03-10 ~ 2026-10-14 (약 27.5년, 총 6,943 거래일)
- 전략 가설:
  2016~2026년 10년 클린 구간에서 검증된 최적의 룰(수익률 최하위 꼴찌 아웃 + 2.5년 만기 캡 하이브리드)을
  닷컴 버블 붕괴(2000~2002), 2008년 글로벌 금융위기, 2020년 코로나 쇼크, 2022년 금리인상기, 
  그리고 2023~2026년 AI 대세 랠리까지 포함하는 28년 전 기간 역사적 데이터셋에 전면 적용하여
  장기 복리 생존력과 알파 폭발력을 최종 실측한다.

- 핵심 전략 비교:
  1. Hybrid_Perf_Tenure_10Slots (최종 하이브리드: 꼴찌 우선 방출 + 2.5년 만기 캡)
  2. Lowest_Perf_Exit_10Slots (순수 보유수익률 최하위 꼴찌 아웃)
  3. EventOnly_FIFO_2.5Y (기존 2.5년 골디락스 만기 퇴출형)
  4. Lowest_Cap_Exit_10Slots (시총 최하위 꼴찌 아웃)
  5. QQQ_Actual_ETF (실제 QQQ ETF 실물 기준선)

- 공통 운용 원칙:
  - 포트폴리오 슬롯: 10개 종목 압축
  - 리밸런싱: 신규 편입 이벤트 발생 시에만 기계적 리밸런싱 (EventOnly)
  - 편도 0.05% (왕복 0.10%) 체결 슬리피지/거래비용 엄격 차감
"""
import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = "DataVault"
REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

# 1. 2016~2024 공식 나스닥 100 신규 편입 56개 전수 리스트
OFFICIAL_ADDITIONS = [
    (pd.Timestamp("2016-12-19"), 'CTAS'),
    (pd.Timestamp("2016-12-19"), 'HAS'),
    (pd.Timestamp("2016-12-19"), 'KLAC'),
    (pd.Timestamp("2017-12-18"), 'ASML'),
    (pd.Timestamp("2017-12-18"), 'CDNS'),
    (pd.Timestamp("2017-12-18"), 'SNPS'),
    (pd.Timestamp("2017-12-18"), 'TTWO'),
    (pd.Timestamp("2017-12-18"), 'WDAY'),
    (pd.Timestamp("2018-06-18"), 'AMD'),
    (pd.Timestamp("2018-06-18"), 'LULU'),
    (pd.Timestamp("2018-09-24"), 'PEP'),
    (pd.Timestamp("2018-09-24"), 'NXPI'),
    (pd.Timestamp("2018-12-24"), 'TEAM'),
    (pd.Timestamp("2018-12-24"), 'VRTX'),
    (pd.Timestamp("2019-12-23"), 'CDW'),
    (pd.Timestamp("2019-12-23"), 'CPRT'),
    (pd.Timestamp("2019-12-23"), 'CSGP'),
    (pd.Timestamp("2019-12-23"), 'DXCM'),
    (pd.Timestamp("2019-12-23"), 'MRVL'),
    (pd.Timestamp("2020-04-20"), 'DOCU'),
    (pd.Timestamp("2020-04-20"), 'ZM'),
    (pd.Timestamp("2020-07-20"), 'MRNA'),
    (pd.Timestamp("2020-08-24"), 'PDD'),
    (pd.Timestamp("2020-12-21"), 'AEP'),
    (pd.Timestamp("2020-12-21"), 'MTCH'),
    (pd.Timestamp("2020-12-21"), 'OKTA'),
    (pd.Timestamp("2020-12-21"), 'PTON'),
    (pd.Timestamp("2021-08-23"), 'HON'),
    (pd.Timestamp("2021-08-23"), 'CRWD'),
    (pd.Timestamp("2021-12-20"), 'ABNB'),
    (pd.Timestamp("2021-12-20"), 'DDOG'),
    (pd.Timestamp("2021-12-20"), 'FTNT'),
    (pd.Timestamp("2021-12-20"), 'LCID'),
    (pd.Timestamp("2021-12-20"), 'PANW'),
    (pd.Timestamp("2021-12-20"), 'ZS'),
    (pd.Timestamp("2022-02-22"), 'CEG'),
    (pd.Timestamp("2022-06-20"), 'ON'),
    (pd.Timestamp("2022-06-20"), 'FSLR'),
    (pd.Timestamp("2022-12-19"), 'BKR'),
    (pd.Timestamp("2022-12-19"), 'FANG'),
    (pd.Timestamp("2022-12-19"), 'GFS'),
    (pd.Timestamp("2022-12-19"), 'RIVN'),
    (pd.Timestamp("2022-12-19"), 'COIN'),
    (pd.Timestamp("2023-01-04"), 'GEHC'),
    (pd.Timestamp("2023-12-18"), 'DASH'),
    (pd.Timestamp("2023-12-18"), 'MDB'),
    (pd.Timestamp("2023-12-18"), 'ROP'),
    (pd.Timestamp("2023-12-18"), 'CCEP'),
    (pd.Timestamp("2023-12-18"), 'TTD'),
    (pd.Timestamp("2024-03-18"), 'SMCI'),
    (pd.Timestamp("2024-06-24"), 'ARM'),
    (pd.Timestamp("2024-06-24"), 'LIN'),
    (pd.Timestamp("2024-09-23"), 'APP'),
    (pd.Timestamp("2024-12-23"), 'AXON'),
    (pd.Timestamp("2024-12-23"), 'MSTR'),
    (pd.Timestamp("2024-12-23"), 'PLTR')
]

OFFICIAL_REMOVALS = [
    (pd.Timestamp("2016-12-19"), 'NTES'),
    (pd.Timestamp("2017-12-18"), 'AKAM'),
    (pd.Timestamp("2017-12-18"), 'NTAP'),
    (pd.Timestamp("2018-06-18"), 'STX'),
    (pd.Timestamp("2019-12-23"), 'HAS'),
    (pd.Timestamp("2020-12-21"), 'WDC'),
    (pd.Timestamp("2020-12-21"), 'UAL'),
    (pd.Timestamp("2020-12-21"), 'AAL'),
    (pd.Timestamp("2021-12-20"), 'FOXA'),
    (pd.Timestamp("2022-12-19"), 'MTCH'),
    (pd.Timestamp("2022-12-19"), 'DOCU'),
    (pd.Timestamp("2022-12-19"), 'PTON'),
    (pd.Timestamp("2023-12-18"), 'ALGN'),
    (pd.Timestamp("2023-12-18"), 'EBAY'),
    (pd.Timestamp("2023-12-18"), 'ENPH'),
    (pd.Timestamp("2023-12-18"), 'JD'),
    (pd.Timestamp("2023-12-18"), 'LCID'),
    (pd.Timestamp("2023-12-18"), 'ZM'),
    (pd.Timestamp("2024-12-23"), 'ILMN'),
    (pd.Timestamp("2024-12-23"), 'SMCI'),
    (pd.Timestamp("2024-12-23"), 'MRNA')
]

NAS_UNIVERSE = [
    'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AVGO', 'COST', 'PEP',
    'CSCO', 'TMUS', 'ADBE', 'NFLX', 'AMD', 'INTC', 'QCOM', 'AMAT', 'INTU', 'TXN',
    'HON', 'ISRG', 'CMCSA', 'BKNG', 'AMGN', 'VRTX', 'SBUX', 'MDLZ', 'GILD', 'REGN',
    'PANW', 'ADP', 'KLAC', 'SNPS', 'CDNS', 'MU', 'MELI', 'CRWD', 'ABNB', 'MAR',
    'ORLY', 'CTAS', 'NXPI', 'FTNT', 'CEG', 'PCAR', 'KDP', 'PAYX', 'MRVL', 'MCHP',
    'CPRT', 'ROST', 'ODFL', 'FAST', 'IDXX', 'VRSK', 'EXC', 'XEL', 'BIIB', 'DLTR',
    'SIRI', 'ILMN', 'ALGN', 'TEAM', 'ZS', 'DDOG', 'DXCM', 'ENPH', 'GEHC', 'FSLR',
    'TTD', 'ARM', 'MDB', 'ON', 'LRCX', 'PDD', 'WDAY', 'CSX', 'ASML', 'AZN',
    'EBAY', 'EA', 'BMRN', 'CHTR', 'CTSH', 'WBA', 'MNST', 'LULU', 'KHC', 'JD',
    'BIDU', 'NTES', 'ZM', 'DOCU', 'PYPL', 'ORCL', 'DELL', 'LU', 'NOK', 'ERIC',
    'MRNA', 'COIN', 'RIVN', 'LCID', 'DASH', 'FANG', 'BKR', 'GFS', 'CSGP', 'CDW',
    'ROKU', 'PTON', 'OKTA', 'SMCI', 'AXON', 'MSTR', 'PLTR', 'APP', 'HAS', 'TTWO',
    'WDC', 'UAL', 'AAL', 'FOXA', 'MTCH'
]

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
    df = pd.read_csv(fp)
    df["date"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("date").sort_index()
    sp = load_splits(ticker)
    if not sp.empty:
        mult = pd.Series(1.0, index=df.index)
        for d, ratio in sp.items():
            mult.loc[df.index <= d] *= ratio
        df["adj_close"] = df["close"] / mult
    else:
        df["adj_close"] = df["close"]
        
    dvol = df["close"] * df["volume"]
    df["dvol_60d"] = dvol.rolling(60, min_periods=20).mean()
    return df

def compute_metrics(eq_series, benchmark_eq=None):
    total_ret = (eq_series.iloc[-1] / eq_series.iloc[0]) - 1.0
    n_days = len(eq_series)
    years = (eq_series.index[-1] - eq_series.index[0]).days / 365.25
    cagr = (eq_series.iloc[-1] / eq_series.iloc[0]) ** (1.0 / years) - 1.0
    daily_rets = eq_series.pct_change().dropna()
    ann_vol = daily_rets.std() * np.sqrt(252)
    sharpe = (cagr - 0.02) / ann_vol if ann_vol > 0 else 0.0
    cum_max = eq_series.cummax()
    dd = (eq_series - cum_max) / cum_max
    mdd = dd.min()
    corr = np.nan
    beta = np.nan
    if benchmark_eq is not None:
        bm_rets = benchmark_eq.pct_change().dropna()
        common = daily_rets.index.intersection(bm_rets.index)
        if len(common) > 10:
            r1 = daily_rets.loc[common]
            r2 = bm_rets.loc[common]
            corr = r1.corr(r2)
            var_bm = r2.var()
            if var_bm > 0:
                beta = r1.cov(r2) / var_bm
    return {
        "Total Return": total_ret,
        "CAGR": cagr,
        "Volatility": ann_vol,
        "Sharpe": sharpe,
        "MDD": mdd,
        "Correlation": corr,
        "Beta": beta
    }

def run_backtest():
    print("=" * 110)
    print("🚀 [EXP-013] 나스닥 100 최대 가용 기간 (1999~2026, 27.5년) 최종 하이브리드 전략 백테스트")
    print("=" * 110)
    
    qqq_df = load_stock_data("QQQ")
    if qqq_df is None:
        raise FileNotFoundError("QQQ 데이터를 찾을 수 없습니다.")
        
    cal = qqq_df.index
    print(f"📅 전체 데이터: {cal[0].date()} ~ {cal[-1].date()} (총 {len(cal):,} 거래일, 27.5년, 28개 연도)")
    
    # 2. 전 종목 로드
    stocks = {}
    for t in set(NAS_UNIVERSE):
        df = load_stock_data(t)
        if df is not None and len(df) > 50:
            stocks[t] = df
    print(f"✅ 유효 종목 로드 완료: {len(stocks)}개")
    
    adj_mat = pd.DataFrame({t: df["adj_close"].reindex(cal).ffill() for t, df in stocks.items()}, index=cal)
    dvol_mat = pd.DataFrame({t: df["dvol_60d"].reindex(cal).ffill() for t, df in stocks.items()}, index=cal)
    ret_mat = adj_mat.pct_change().fillna(0.0)
    
    qqq_ret = qqq_df["adj_close"].pct_change().fillna(0.0)
    qqq_bm_eq = (1.0 + qqq_ret).cumprod()
    
    # 3. 신규 편입 이벤트 구축 (1999~2026)
    # 1999~2015: 분기별 Point-in-time 거래대금 신규 편입 감지
    # 2016~2024: 공식 56개 편입 이벤트 매핑
    rebal_dates = []
    prev_quarter = None
    for d in cal:
        q = (d.year, (d.month - 1) // 3)
        if prev_quarter is not None and q != prev_quarter:
            rebal_dates.append(d)
        prev_quarter = q
    rebal_dates = [cal[0]] + rebal_dates
    
    # 1999-03-10 초기 유니버스 (상위 50위 기준)
    d0 = cal[0]
    initial_top = dvol_mat.loc[d0].dropna().sort_values(ascending=False).head(50).index.tolist()
    known_universe = set(initial_top)
    
    addition_events = {} # {dt: [symbols]}
    
    # 1999-2015 구간 감지
    for d in rebal_dates:
        if d >= pd.Timestamp("2016-01-01"):
            break
        curr_top = set(dvol_mat.loc[d].dropna().sort_values(ascending=False).head(50).index.tolist())
        brand_new = sorted(list(curr_top - known_universe))
        if brand_new:
            addition_events[d] = [s for s in brand_new if s in stocks]
            for s in brand_new:
                known_universe.add(s)
                
    # 2016-2024 공식 편입 결합
    for d, sym in OFFICIAL_ADDITIONS:
        matched = cal[cal >= d]
        if len(matched) > 0:
            dt = matched[0]
            if dt not in addition_events:
                addition_events[dt] = []
            if sym not in addition_events[dt] and sym in stocks:
                addition_events[dt].append(sym)
                
    all_event_dates = sorted(list(set([cal[0]]).union(set(addition_events.keys()))))
    total_entrants_count = sum(len(syms) for syms in addition_events.values())
    print(f"✨ 1999~2026 총 신규 편입 이벤트: {len(addition_events)}개 일자, 총 {total_entrants_count}개 신규 편입 종목 포착")
    
    removal_dict = {t: d for d, t in OFFICIAL_REMOVALS}
    cost_rate = 0.0005 # 편도 0.05%
    
    # -------------------------------------------------------------
    # 백테스트 시뮬레이션 엔진
    # -------------------------------------------------------------
    def simulate_strategy(mode="hybrid"):
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        w_curr = pd.Series(dtype=float)
        hold_dict = {} # {ticker: entry_date}
        trade_logs = []
        
        # 1999-03-10 시작 시점: 초기 상위 10개 종목으로 슬롯 충원
        initial_10 = [t for t in initial_top if t in stocks][:10]
        for t in initial_10:
            hold_dict[t] = cal[0]
        w_curr = pd.Series(1.0 / len(initial_10), index=initial_10)
        
        for i in range(len(cal)):
            dt = cal[i]
            
            if dt in all_event_dates and i > 0:
                new_syms = addition_events.get(dt, [])
                new_valid = [s for s in new_syms if s in stocks and s not in hold_dict]
                
                # 1. 공식 탈락 종목 우선 퇴출
                survivors = []
                for sym in list(hold_dict.keys()):
                    rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                    if dt < rem_d:
                        survivors.append(sym)
                    else:
                        trade_logs.append((dt, f"공식탈락: {sym}"))
                        hold_dict.pop(sym, None)
                        
                # 2. 만기 체크 (2.5년 = 912일)
                # FIFO 모드일 때는 무조건 만기 도달 시 방출
                if mode in ["fifo_25y", "hybrid"]:
                    for sym in list(survivors):
                        tenure = (dt - hold_dict[sym]).days
                        if tenure >= 912:
                            # hybrid에서는 수익률 꼴찌 방출 우선이지만, 2.5년 만기도 체크
                            if mode == "fifo_25y":
                                survivors.remove(sym)
                                hold_dict.pop(sym, None)
                                trade_logs.append((dt, f"만기퇴출(2.5Y): {sym}"))
                                
                # 3. 신규 종목 유입에 따른 슬롯 부족분 해결
                excess = (len(survivors) + len(new_valid)) - 10
                if excess > 0 and survivors:
                    if mode == "hybrid":
                        # 하이브리드:
                        # 1순위: 만기 2.5년 도달 종목 방출
                        # 2순위: 계좌 내 보유 수익률 최하위 꼴찌 방출
                        expired = [s for s in survivors if (dt - hold_dict[s]).days >= 912]
                        to_prune = []
                        for exp_s in expired:
                            to_prune.append(exp_s)
                            if len(to_prune) == excess:
                                break
                                
                        if len(to_prune) < excess:
                            rem_candidates = [s for s in survivors if s not in to_prune]
                            perf_series = pd.Series(dtype=float)
                            for s in rem_candidates:
                                p_start = adj_mat[s].loc[hold_dict[s]]
                                p_now = adj_mat[s].loc[dt]
                                perf_series[s] = (p_now / p_start) - 1.0 if p_start > 0 else 0.0
                            lowest_perf = perf_series.sort_values(ascending=True).index.tolist()
                            needed = excess - len(to_prune)
                            to_prune.extend(lowest_perf[:needed])
                            
                        for k_sym in to_prune:
                            survivors.remove(k_sym)
                            hold_dict.pop(k_sym, None)
                            trade_logs.append((dt, f"하이브리드퇴출: {k_sym}"))
                            
                    elif mode == "lowest_perf":
                        # 순수 보유 수익률 꼴찌 방출
                        perf_series = pd.Series(dtype=float)
                        for s in survivors:
                            p_start = adj_mat[s].loc[hold_dict[s]]
                            p_now = adj_mat[s].loc[dt]
                            perf_series[s] = (p_now / p_start) - 1.0 if p_start > 0 else 0.0
                        lowest_perf = perf_series.sort_values(ascending=True).index.tolist()
                        for k_sym in lowest_perf[:excess]:
                            survivors.remove(k_sym)
                            hold_dict.pop(k_sym, None)
                            trade_logs.append((dt, f"수익률꼴찌퇴출: {k_sym}"))
                            
                    elif mode == "lowest_cap":
                        # 시총(거래대금) 최하위 방출
                        dvol_series = dvol_mat.loc[dt].reindex(survivors).fillna(0.0)
                        lowest_cap = dvol_series.sort_values(ascending=True).index.tolist()
                        for k_sym in lowest_cap[:excess]:
                            survivors.remove(k_sym)
                            hold_dict.pop(k_sym, None)
                            trade_logs.append((dt, f"시총꼴찌퇴출: {k_sym}"))
                            
                    elif mode == "fifo_25y":
                        # 만기 전이라도 슬롯이 모자라면 가장 오래된 순서(FIFO)로 방출
                        tenure_series = pd.Series({s: (dt - hold_dict[s]).days for s in survivors})
                        oldest = tenure_series.sort_values(ascending=False).index.tolist()
                        for k_sym in oldest[:excess]:
                            survivors.remove(k_sym)
                            hold_dict.pop(k_sym, None)
                            trade_logs.append((dt, f"FIFO퇴출: {k_sym}"))
                            
                # 4. 신규 종목 슬롯 충원
                for s in new_valid:
                    if len(survivors) < 10:
                        survivors.append(s)
                        hold_dict[s] = dt
                        trade_logs.append((dt, f"신규편입: {s}"))
                        
                # 5. 동일가중 리밸런싱 집행
                if survivors:
                    target_w = pd.Series(1.0 / len(survivors), index=survivors)
                    union_idx = w_curr.index.union(target_w.index)
                    w_old = w_curr.reindex(union_idx, fill_value=0.0)
                    w_new = target_w.reindex(union_idx, fill_value=0.0)
                    turnover = (w_new - w_old).abs().sum() / 2.0
                    cost = turnover * cost_rate * 2.0
                    eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                    eq.iloc[i] = eq_prev_val * (1.0 - cost)
                    w_curr = target_w.copy()
                else:
                    w_curr = pd.Series(dtype=float)
                    
            if i == 0:
                continue
                
            eq_prev = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
            if not w_curr.empty:
                active_ret = ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0)
                port_ret = (w_curr * active_ret).sum()
                uninvested = max(0.0, 1.0 - w_curr.sum())
                f_ret = qqq_ret.loc[dt]
                total_ret = port_ret + uninvested * f_ret
                eq.iloc[i] = eq_prev * (1.0 + total_ret)
                
                w_curr = w_curr * (1.0 + active_ret)
                total_val = w_curr.sum() + uninvested * (1.0 + f_ret)
                if total_val > 0:
                    w_curr = w_curr / total_val
            else:
                f_ret = qqq_ret.loc[dt]
                eq.iloc[i] = eq_prev * (1.0 + f_ret)
                
        return eq, trade_logs

    print("\n⏳ 28개 전 연도 시뮬레이션 연산 가동 중...")
    eq_hybrid, logs_hybrid = simulate_strategy("hybrid")
    eq_lowest_perf, logs_perf = simulate_strategy("lowest_perf")
    eq_fifo, logs_fifo = simulate_strategy("fifo_25y")
    eq_lowest_cap, logs_cap = simulate_strategy("lowest_cap")

    all_res = {
        "1. Hybrid_Perf_Tenure_10Slots (최종 하이브리드: 꼴찌방출+2.5Y만기)": eq_hybrid,
        "2. Lowest_Perf_Exit_10Slots (순수 보유수익률 꼴찌 아웃)": eq_lowest_perf,
        "3. EventOnly_FIFO_2.5Y (기존 2.5년 만기 퇴출형)": eq_fifo,
        "4. Lowest_Cap_Exit_10Slots (시총 최하위 꼴찌 아웃)": eq_lowest_cap,
        "5. QQQ_Actual_ETF (실물 기준선)": qqq_bm_eq
    }

    print("\n" + "=" * 115)
    print("🏆 [전체 27.5년 성과 종합 (1999-03-10 ~ 2026-10-14, 28개 전 연도)]")
    print("=" * 115)
    print(f"{'전략 명칭':<48} | {'총수익률':>10} | {'CAGR':>8} | {'변동성':>8} | {'샤프':>6} | {'MDD':>8} | {'베타':>6}")
    print("-" * 115)
    
    metrics_all = {}
    for name, s_eq in all_res.items():
        m = compute_metrics(s_eq, qqq_bm_eq)
        metrics_all[name] = m
        print(f"{name:<48} | {m['Total Return']*100:>9.1f}% | {m['CAGR']*100:>7.2f}% | {m['Volatility']*100:>7.2f}% | {m['Sharpe']:>6.2f} | {m['MDD']*100:>7.2f}% | {m['Beta']:>6.2f}")

    # 최근 10년 클린 구간 (2016-01-01 ~ 2026-10-14) 서브 매트릭스
    print("\n" + "=" * 115)
    print("💎 [최근 10년 클린 구간 성과 (2016-01-01 ~ 2026-10-14)]")
    print("=" * 115)
    print(f"{'전략 명칭':<48} | {'총수익률':>10} | {'CAGR':>8} | {'변동성':>8} | {'샤프':>6} | {'MDD':>8} | {'베타':>6}")
    print("-" * 115)
    cal_10y = cal[cal >= pd.Timestamp("2016-01-01")]
    qqq_bm_10y = qqq_bm_eq.loc[cal_10y] / qqq_bm_eq.loc[cal_10y].iloc[0]
    
    metrics_10y = {}
    for name, s_eq in all_res.items():
        sub_eq = s_eq.loc[cal_10y] / s_eq.loc[cal_10y].iloc[0]
        m = compute_metrics(sub_eq, qqq_bm_10y)
        metrics_10y[name] = m
        print(f"{name:<48} | {m['Total Return']*100:>9.1f}% | {m['CAGR']*100:>7.2f}% | {m['Volatility']*100:>7.2f}% | {m['Sharpe']:>6.2f} | {m['MDD']*100:>7.2f}% | {m['Beta']:>6.2f}")

    # 연도별 수익률 매트릭스 (1999~2026)
    years = sorted(list(set(cal.year)))
    annual_table = []
    for y in years:
        cal_y = cal[cal.year == y]
        row = {"Year": y}
        for name, s_eq in all_res.items():
            r = (s_eq.loc[cal_y[-1]] / s_eq.loc[cal_y[0]]) - 1.0 if len(cal_y) > 0 else 0.0
            row[name.split()[1]] = round(r * 100, 1)
        annual_table.append(row)
    ann_df = pd.DataFrame(annual_table)
    
    print("\n" + "=" * 115)
    print("📅 [연도별 수익률 성과 매트릭스 (1999 ~ 2026, 28개 전 연도)]")
    print("=" * 115)
    print(ann_df.to_string(index=False))

    # CSV 저장
    ann_df.to_csv(f"{REPORTS_DIR}/max_period_annual_matrix.csv", index=False, encoding="utf-8-sig")
    
    # HTML 리포트 생성
    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>나스닥 100 최대 28년 하이브리드 전략 백테스트 리포트</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 20px; }}
h1, h2, h3 {{ color: #38bdf8; }}
.card {{ background: #1e293b; border-radius: 10px; padding: 20px; margin-bottom: 25px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }}
table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
th, td {{ padding: 10px 12px; text-align: right; border-bottom: 1px solid #334155; }}
th {{ background: #0f172a; color: #94a3b8; font-weight: 600; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
.pos {{ color: #4ade80; font-weight: 600; }}
.neg {{ color: #f87171; font-weight: 600; }}
.badge {{ background: #0284c7; color: white; padding: 3px 8px; border-radius: 4px; font-size: 12px; }}
</style>
</head>
<body>
<h1>🚀 나스닥 100 최대 가용 기간 (1999~2026년, 28개 전 연도) 백테스트 리포트</h1>
<p>검증 구간: 1999-03-10 ~ 2026-10-14 (총 6,943 거래일, 27.5년) | 거래비용 편도 0.05% (왕복 0.10%) 반영</p>

<div class="card">
<h2>🏆 전체 27.5년 종합 성과 (1999-03-10 ~ 2026-10-14)</h2>
<table>
<tr>
  <th style="text-align:left;">전략 명칭</th>
  <th>총 수익률</th>
  <th>CAGR</th>
  <th>연 변동성</th>
  <th>샤프 지수</th>
  <th>MDD</th>
  <th>베타</th>
</tr>
"""
    for name, m in metrics_all.items():
        ret_cls = "pos" if m["Total Return"] > 0 else "neg"
        cagr_cls = "pos" if m["CAGR"] > 0 else "neg"
        html_content += f"""<tr>
  <td style="text-align:left; font-weight:bold;">{name}</td>
  <td class="{ret_cls}">{m['Total Return']*100:+,.1f}%</td>
  <td class="{cagr_cls}">{m['CAGR']*100:.2f}%</td>
  <td>{m['Volatility']*100:.2f}%</td>
  <td style="font-weight:bold; color:#38bdf8;">{m['Sharpe']:.2f}</td>
  <td class="neg">{m['MDD']*100:.2f}%</td>
  <td>{m['Beta']:.2f}</td>
</tr>"""

    html_content += """</table>
</div>

<div class="card">
<h2>💎 최근 10년 클린 구간 성과 (2016-01-01 ~ 2026-10-14)</h2>
<table>
<tr>
  <th style="text-align:left;">전략 명칭</th>
  <th>총 수익률</th>
  <th>CAGR</th>
  <th>연 변동성</th>
  <th>샤프 지수</th>
  <th>MDD</th>
  <th>베타</th>
</tr>
"""
    for name, m in metrics_10y.items():
        ret_cls = "pos" if m["Total Return"] > 0 else "neg"
        cagr_cls = "pos" if m["CAGR"] > 0 else "neg"
        html_content += f"""<tr>
  <td style="text-align:left; font-weight:bold;">{name}</td>
  <td class="{ret_cls}">{m['Total Return']*100:+,.1f}%</td>
  <td class="{cagr_cls}">{m['CAGR']*100:.2f}%</td>
  <td>{m['Volatility']*100:.2f}%</td>
  <td style="font-weight:bold; color:#38bdf8;">{m['Sharpe']:.2f}</td>
  <td class="neg">{m['MDD']*100:.2f}%</td>
  <td>{m['Beta']:.2f}</td>
</tr>"""

    html_content += f"""</table>
</div>

<div class="card">
<h2>📅 28개 전 연도 연간 성과 매트릭스 (1999 ~ 2026)</h2>
<table>
<tr>
  <th style="text-align:left;">연도</th>
  <th>하이브리드 10</th>
  <th>수익률꼴찌 10</th>
  <th>FIFO 2.5Y</th>
  <th>시총꼴찌 10</th>
  <th>실제 QQQ</th>
</tr>
"""
    for _, row in ann_df.iterrows():
        y = int(row["Year"])
        html_content += f"""<tr>
  <td style="text-align:left; font-weight:bold;">{y}</td>
  <td class="{'pos' if row['Hybrid_Perf_Tenure_10Slots']>=0 else 'neg'}">{row['Hybrid_Perf_Tenure_10Slots']:+.1f}%</td>
  <td class="{'pos' if row['Lowest_Perf_Exit_10Slots']>=0 else 'neg'}">{row['Lowest_Perf_Exit_10Slots']:+.1f}%</td>
  <td class="{'pos' if row['EventOnly_FIFO_2.5Y']>=0 else 'neg'}">{row['EventOnly_FIFO_2.5Y']:+.1f}%</td>
  <td class="{'pos' if row['Lowest_Cap_Exit_10Slots']>=0 else 'neg'}">{row['Lowest_Cap_Exit_10Slots']:+.1f}%</td>
  <td class="{'pos' if row['QQQ_Actual_ETF']>=0 else 'neg'}">{row['QQQ_Actual_ETF']:+.1f}%</td>
</tr>"""

    html_content += """</table>
</div>
</body>
</html>"""
    
    report_path = f"{REPORTS_DIR}/max_period_hybrid_backtest_report.html"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"\n✅ HTML 종합 리포트 저장 완료: {report_path}")
    
    return all_res, metrics_all, metrics_10y, ann_df

if __name__ == "__main__":
    run_backtest()
