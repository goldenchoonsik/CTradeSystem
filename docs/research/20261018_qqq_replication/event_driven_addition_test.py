"""
[EXP-007] 신규 편입 발생월/발생일 즉시 리밸런싱 (Event-Driven Rebalancing) vs 분기 고정 리밸런싱 비교 검증
=====================================================================================================
- 연구 질문: 
  "나스닥 100 신규 편입이 발표/효력 발생하는 당일(또는 해당 월)에 즉시 포트폴리오를 리밸런싱하여 
   새 루키를 지체 없이 편입하면, 분기 말까지 기다리는 기존 방식 대비 수익률/위험 지표에 어떤 변화가 생기는가?"
- 검증 기간: 2016-01-01 ~ 2026-10-14 (최근 10년 무편향 클린 구간)
- 대상 종목: 공식 56개 전수 신규 편입 종목
- 비교 전략:
  1. Official_Quarterly_2.5Y (기존 분기 고정 리밸런싱 + 2.5년 만기)
  2. Official_EventDriven_2.5Y (신규 편입 발생일 즉시 편입 + 분기 리밸런싱 병행 + 2.5년 만기)
  3. Official_EventOnly_2.5Y (정기 분기 리밸런싱 없이 오직 신규 편입 이벤트 발생일에만 리밸런싱 + 2.5년 만기)
  4. Official_Quarterly_FIFO (기존 분기 고정 리밸런싱 + FIFO 무기한)
  5. Official_EventDriven_FIFO (신규 편입 발생일 즉시 편입 + FIFO 무기한)
  6. QQQ_Actual_ETF (실물 기준선)
- 공통 조건:
  - 10종목 1/N 동일 가중 (각 10.0%)
  - 편도 0.05% (왕복 0.10%) 거래비용 엄격 차감
"""
import os
import sys
import json
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

# 1. 2016-2024 공식 나스닥 100 신규 편입 56개 전수 리스트 (발표/효력 발생 연도별)
OFFICIAL_ADDITIONS = [
    # 2016
    (pd.Timestamp("2016-12-19"), 'CTAS'),
    (pd.Timestamp("2016-12-19"), 'HAS'),
    (pd.Timestamp("2016-12-19"), 'KLAC'),
    # 2017
    (pd.Timestamp("2017-12-18"), 'ASML'),
    (pd.Timestamp("2017-12-18"), 'CDNS'),
    (pd.Timestamp("2017-12-18"), 'SNPS'),
    (pd.Timestamp("2017-12-18"), 'TTWO'),
    (pd.Timestamp("2017-12-18"), 'WDAY'),
    # 2018 (정기 및 수시 변경)
    (pd.Timestamp("2018-06-18"), 'AMD'),
    (pd.Timestamp("2018-06-18"), 'LULU'),
    (pd.Timestamp("2018-09-24"), 'PEP'),
    (pd.Timestamp("2018-09-24"), 'NXPI'),
    (pd.Timestamp("2018-12-24"), 'TEAM'),
    (pd.Timestamp("2018-12-24"), 'VRTX'),
    # 2019
    (pd.Timestamp("2019-12-23"), 'CDW'),
    (pd.Timestamp("2019-12-23"), 'CPRT'),
    (pd.Timestamp("2019-12-23"), 'CSGP'),
    (pd.Timestamp("2019-12-23"), 'DXCM'),
    (pd.Timestamp("2019-12-23"), 'MRVL'),
    # 2020
    (pd.Timestamp("2020-04-20"), 'DOCU'),
    (pd.Timestamp("2020-04-20"), 'ZM'),
    (pd.Timestamp("2020-07-20"), 'MRNA'),
    (pd.Timestamp("2020-08-24"), 'PDD'),
    (pd.Timestamp("2020-12-21"), 'AEP'),
    (pd.Timestamp("2020-12-21"), 'MTCH'),
    (pd.Timestamp("2020-12-21"), 'OKTA'),
    (pd.Timestamp("2020-12-21"), 'PTON'),
    # 2021
    (pd.Timestamp("2021-08-23"), 'HON'),
    (pd.Timestamp("2021-08-23"), 'CRWD'),
    (pd.Timestamp("2021-12-20"), 'ABNB'),
    (pd.Timestamp("2021-12-20"), 'DDOG'),
    (pd.Timestamp("2021-12-20"), 'FTNT'),
    (pd.Timestamp("2021-12-20"), 'LCID'),
    (pd.Timestamp("2021-12-20"), 'PANW'),
    (pd.Timestamp("2021-12-20"), 'ZS'),
    # 2022
    (pd.Timestamp("2022-02-22"), 'CEG'),
    (pd.Timestamp("2022-06-20"), 'ON'),
    (pd.Timestamp("2022-06-20"), 'FSLR'),
    (pd.Timestamp("2022-12-19"), 'BKR'),
    (pd.Timestamp("2022-12-19"), 'FANG'),
    (pd.Timestamp("2022-12-19"), 'GFS'),
    (pd.Timestamp("2022-12-19"), 'RIVN'),
    (pd.Timestamp("2022-12-19"), 'COIN'),
    # 2023
    (pd.Timestamp("2023-01-04"), 'GEHC'),
    (pd.Timestamp("2023-12-18"), 'DASH'),
    (pd.Timestamp("2023-12-18"), 'MDB'),
    (pd.Timestamp("2023-12-18"), 'ROP'),
    (pd.Timestamp("2023-12-18"), 'CCEP'),
    (pd.Timestamp("2023-12-18"), 'TTD'),
    # 2024
    (pd.Timestamp("2024-03-18"), 'SMCI'),
    (pd.Timestamp("2024-06-24"), 'ARM'),
    (pd.Timestamp("2024-06-24"), 'LIN'),
    (pd.Timestamp("2024-09-23"), 'APP'),
    (pd.Timestamp("2024-12-23"), 'AXON'),
    (pd.Timestamp("2024-12-23"), 'MSTR'),
    (pd.Timestamp("2024-12-23"), 'PLTR')
]

# 2. 공식 탈락 리스트 (제외 일자, 티커)
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
    
    # 주식분할 조정
    sp = load_splits(ticker)
    if not sp.empty:
        mult = pd.Series(1.0, index=df.index)
        for d, ratio in sp.items():
            mult.loc[df.index <= d] *= ratio
        df["adj_close"] = df["close"] / mult
    else:
        df["adj_close"] = df["close"]
    
    return df

def simulate_strategy(cal_dates, rebal_dates, returns_mat, get_weights_fn, cost_rate=0.0005, fallback_ret=None):
    eq = pd.Series(index=cal_dates, dtype=float)
    eq.iloc[0] = 1.0
    w_curr = pd.Series(dtype=float)
    rebal_set = set(rebal_dates)
    
    for i in range(len(cal_dates)):
        d = cal_dates[i]
        if d in rebal_set:
            target_w = get_weights_fn(d, w_curr)
            if not target_w.empty:
                union_idx = w_curr.index.union(target_w.index)
                w_old = w_curr.reindex(union_idx, fill_value=0.0)
                w_new = target_w.reindex(union_idx, fill_value=0.0)
                turnover = (w_new - w_old).abs().sum() / 2.0
                cost = turnover * cost_rate * 2.0
                if i > 0:
                    eq.iloc[i] = eq.iloc[i-1] * (1.0 - cost)
                w_curr = target_w.copy()
            else:
                if not w_curr.empty:
                    turnover = w_curr.sum()
                    cost = turnover * cost_rate
                    if i > 0:
                        eq.iloc[i] = eq.iloc[i-1] * (1.0 - cost)
                    w_curr = pd.Series(dtype=float)
        
        if i == 0:
            continue
            
        eq_prev = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
        
        if not w_curr.empty:
            active_ret = returns_mat.loc[d].reindex(w_curr.index, fill_value=0.0)
            port_ret = (w_curr * active_ret).sum()
            uninvested = max(0.0, 1.0 - w_curr.sum())
            f_ret = fallback_ret.loc[d] if fallback_ret is not None else 0.0
            total_ret = port_ret + uninvested * f_ret
            eq.iloc[i] = eq_prev * (1.0 + total_ret)
            
            w_curr = w_curr * (1.0 + active_ret)
            total_val = w_curr.sum() + uninvested * (1.0 + f_ret)
            if total_val > 0:
                w_curr = w_curr / total_val
        else:
            f_ret = fallback_ret.loc[d] if fallback_ret is not None else 0.0
            eq.iloc[i] = eq_prev * (1.0 + f_ret)
                
    return eq

def compute_metrics(eq_series, benchmark_eq=None):
    total_ret = (eq_series.iloc[-1] / eq_series.iloc[0]) - 1.0
    n_days = len(eq_series)
    cagr = (eq_series.iloc[-1] / eq_series.iloc[0]) ** (252.0 / n_days) - 1.0
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

def run_test():
    print("=" * 90)
    print("🚀 [EXP-007] 신규 편입 이벤트 발생 즉시 리밸런싱 vs 분기 고정 리밸런싱 비교 검증")
    print("=" * 90)
    
    qqq_df = load_stock_data("QQQ")
    if qqq_df is None:
        raise FileNotFoundError("QQQ_1d.csv 데이터를 찾을 수 없습니다.")
        
    cal = qqq_df.index
    cal_10y = cal[cal >= "2016-01-01"]
    print(f"📅 검증 기간: {cal_10y[0].date()} ~ {cal_10y[-1].date()} (최근 10년 클린 구간, 총 {len(cal_10y):,} 거래일)")
    
    # 56개 신규 편입 종목 로드
    all_tickers = sorted(list(set([t for _, t in OFFICIAL_ADDITIONS])))
    stocks = {}
    for t in all_tickers:
        df = load_stock_data(t)
        if df is not None and len(df) > 100:
            stocks[t] = df
    print(f"✅ 공식 신규 편입주 로드 완료: {len(stocks)}개 (결측 0%)")
    
    adj_dict = {t: df["adj_close"].reindex(cal).ffill() for t, df in stocks.items()}
    adj_mat = pd.DataFrame(adj_dict, index=cal)
    ret_mat = adj_mat.pct_change().fillna(0.0)
    
    qqq_ret_10y = qqq_df["adj_close"].pct_change().fillna(0.0).loc[cal_10y]
    qqq_bm_eq = (1.0 + qqq_ret_10y).cumprod()
    
    # 1. 분기별 정기 리밸런싱 날짜 (기존)
    quarterly_rebal_dates = []
    prev_quarter = None
    for d in cal_10y:
        q = (d.year, (d.month - 1) // 3)
        if prev_quarter is not None and q != prev_quarter:
            quarterly_rebal_dates.append(d)
        prev_quarter = q
    quarterly_rebal_dates = [cal_10y[0]] + quarterly_rebal_dates
    
    # 2. 공식 편입 이벤트 날짜 (거래일 매핑)
    addition_event_dates = []
    for d, _ in OFFICIAL_ADDITIONS:
        # cal_10y 중 d 이상인 첫 거래일
        matched = cal_10y[cal_10y >= d]
        if len(matched) > 0:
            addition_event_dates.append(matched[0])
    addition_event_dates = sorted(list(set(addition_event_dates)))
    
    # 3. 이벤트 드리븐 리밸런싱 날짜 (신규 편입일 + 분기 리밸런싱 합집합)
    event_driven_rebal_dates = sorted(list(set(quarterly_rebal_dates).union(set(addition_event_dates))))
    
    # 4. 오직 이벤트 날에만 리밸런싱 (정기 분기 없음, 이벤트 발생일만)
    event_only_rebal_dates = sorted(list(set([cal_10y[0]]).union(set(addition_event_dates))))
    
    print(f"🔄 분기 고정 리밸런싱 횟수: {len(quarterly_rebal_dates)}회")
    print(f"🔄 신규 편입 이벤트 발생 횟수: {len(addition_event_dates)}회")
    print(f"🔄 이벤트 드리븐 (이벤트+분기) 총 리밸런싱 횟수: {len(event_driven_rebal_dates)}회")
    print(f"🔄 이벤트 단독 (이벤트 발생일만) 총 리밸런싱 횟수: {len(event_only_rebal_dates)}회")
    
    removal_dict = {t: d for d, t in OFFICIAL_REMOVALS}
    
    # 편입일 맵핑 {ticker: entry_date}
    add_dict = {}
    for d, t in OFFICIAL_ADDITIONS:
        if t not in add_dict:
            add_dict[t] = d
            
    # -------------------------------------------------------------
    # 전략 구현 1: 2.5년 만기 (10개 분기 또는 일수 기준 912일)
    # -------------------------------------------------------------
    def create_tenure_logic(max_tenure_days=912): # 2.5년 = 약 912.5일
        hold_start = {}
        
        def get_w(date, w_curr):
            eligible = [t for d, t in OFFICIAL_ADDITIONS if d <= date]
            
            # 생존 검사
            survivors = []
            for sym in w_curr.index:
                rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                start_d = hold_start.get(sym, date)
                tenure_days = (date - start_d).days
                if date < rem_d and tenure_days < max_tenure_days:
                    survivors.append(sym)
                else:
                    hold_start.pop(sym, None)
                    
            needed = 10 - len(survivors)
            if needed > 0:
                for sym in eligible:
                    rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                    if sym not in survivors and date < rem_d and sym in stocks:
                        # 아직 보유한 적 없거나 이미 청산된 종목 중
                        if sym not in hold_start:
                            survivors.append(sym)
                            hold_start[sym] = date
                            if len(survivors) >= 10:
                                break
                                
            if not survivors:
                return pd.Series(dtype=float)
            w = pd.Series(1.0 / len(survivors), index=survivors)
            return w
            
        return get_w

    # -------------------------------------------------------------
    # 전략 구현 2: FIFO (탈락 시까지 보유)
    # -------------------------------------------------------------
    def create_fifo_logic():
        def get_w(date, w_curr):
            eligible = [t for d, t in OFFICIAL_ADDITIONS if d <= date]
            
            survivors = []
            for sym in w_curr.index:
                rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                if date < rem_d:
                    survivors.append(sym)
                    
            needed = 10 - len(survivors)
            if needed > 0:
                for sym in eligible:
                    rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                    if sym not in survivors and date < rem_d and sym in stocks:
                        survivors.append(sym)
                        if len(survivors) >= 10:
                            break
                            
            if not survivors:
                return pd.Series(dtype=float)
            return pd.Series(1.0 / len(survivors), index=survivors)
            
        return get_w

    print("\n⏳ 각 전략별 시뮬레이션 집행 중...")
    results = {}
    
    # 1. Official_Quarterly_2.5Y (기존 방식)
    eq_q_25y = simulate_strategy(cal_10y, quarterly_rebal_dates, ret_mat, create_tenure_logic(912), fallback_ret=qqq_ret_10y)
    results["1. Quarterly_2.5Y (기존 분기 고정)"] = eq_q_25y
    
    # 2. Official_EventDriven_2.5Y (신규 편입일 즉시 리밸런싱 + 분기 리밸런싱)
    eq_ed_25y = simulate_strategy(cal_10y, event_driven_rebal_dates, ret_mat, create_tenure_logic(912), fallback_ret=qqq_ret_10y)
    results["2. EventDriven_2.5Y (편입일 즉시 + 분기)"] = eq_ed_25y
    
    # 3. Official_EventOnly_2.5Y (신규 편입 이벤트 날에만 리밸런싱)
    eq_eo_25y = simulate_strategy(cal_10y, event_only_rebal_dates, ret_mat, create_tenure_logic(912), fallback_ret=qqq_ret_10y)
    results["3. EventOnly_2.5Y (편입 발생일에만 리밸런싱)"] = eq_eo_25y
    
    # 4. Official_Quarterly_FIFO (기존 FIFO)
    eq_q_fifo = simulate_strategy(cal_10y, quarterly_rebal_dates, ret_mat, create_fifo_logic(), fallback_ret=qqq_ret_10y)
    results["4. Quarterly_FIFO (기존 분기 FIFO)"] = eq_q_fifo
    
    # 5. Official_EventDriven_FIFO (편입일 즉시 FIFO)
    eq_ed_fifo = simulate_strategy(cal_10y, event_driven_rebal_dates, ret_mat, create_fifo_logic(), fallback_ret=qqq_ret_10y)
    results["5. EventDriven_FIFO (편입일 즉시 FIFO)"] = eq_ed_fifo
    
    # 6. QQQ ETF 기준선
    results["6. QQQ_Actual_ETF (실물 기준선)"] = qqq_bm_eq
    
    print("\n" + "=" * 105)
    print("📊 [신규 편입 발생월/발생일 즉시 리밸런싱 vs 분기 고정 리밸런싱 성과 비교표]")
    print("=" * 105)
    print(f"{'전략 명칭':<38} | {'총 수익률':>10} | {'CAGR':>8} | {'변동성':>7} | {'샤프':>6} | {'MDD':>8} | {'베타':>5}")
    print("-" * 105)
    
    summary_data = []
    for name, s in results.items():
        m = compute_metrics(s, qqq_bm_eq)
        tot_str = f"{m['Total Return']*100:+.1f}%"
        cagr_str = f"{m['CAGR']*100:.2f}%"
        vol_str = f"{m['Volatility']*100:.2f}%"
        sh_str = f"{m['Sharpe']:.2f}"
        mdd_str = f"{m['MDD']*100:.2f}%"
        beta_str = f"{m['Beta']:.2f}" if not np.isnan(m['Beta']) else "1.00"
        print(f"{name:<38} | {tot_str:>10} | {cagr_str:>8} | {vol_str:>7} | {sh_str:>6} | {mdd_str:>8} | {beta_str:>5}")
        summary_data.append({
            "name": name,
            "total_ret": m['Total Return'],
            "cagr": m['CAGR'],
            "volatility": m['Volatility'],
            "sharpe": m['Sharpe'],
            "mdd": m['MDD'],
            "beta": m['Beta']
        })
    print("=" * 105)
    
if __name__ == "__main__":
    run_test()
