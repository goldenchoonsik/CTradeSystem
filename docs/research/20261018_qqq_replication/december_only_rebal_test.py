"""
[EXP-009] 매년 12월 연 1회 단독 종목 변경 및 리밸런싱 (Annual December-Only Reconstitution) 백테스트
====================================================================================================
- 연구 질문:
  "연중 수시 변경도 배제하고, 오직 매년 12월 나스닥 100 정기 변경일(셋째 주 월요일)에만 
   딱 1년에 1번 종목 교체 및 1/N 리밸런싱을 수행하면(총 10회 거래),
   이벤트 드리븐(+946%)이나 분기 고정(+846%) 대비 어떤 성과가 나오는가?"
- 검증 기간: 2016-01-01 ~ 2026-10-14 (최근 10년 클린 구간)
- 대상: 공식 56개 신규 편입 전수 종목
- 비교 전략:
  1. Annual_Dec_Only_2.5Y (12월 정기 변경 시점에만 연 1회 리밸런싱/종목교체)
  2. Annual_Dec_Only_FIFO (12월 연 1회 리밸런싱 + FIFO 무기한 보유)
  3. EventOnly_2.5Y (신규 편입 발생일에만 리밸런싱, 연 2.2회)
  4. Quarterly_2.5Y (기존 분기 고정 리밸런싱, 연 4회)
  5. QQQ_Actual_ETF (실물 기준선)
- 공통 조건: 10종목 1/N 동일가중, 편도 0.05% (왕복 0.10%) 수수료 차감
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

def run_december_test():
    print("=" * 95)
    print("🎄 [EXP-009] 매년 12월 정기 변경일 연 1회 단독 리밸런싱/종목변경 백테스트")
    print("=" * 95)
    
    qqq_df = load_stock_data("QQQ")
    cal = qqq_df.index
    cal_10y = cal[cal >= "2016-01-01"]
    
    # 56개 종목 로드
    all_tickers = sorted(list(set([t for _, t in OFFICIAL_ADDITIONS])))
    stocks = {}
    for t in all_tickers:
        df = load_stock_data(t)
        if df is not None and len(df) > 100:
            stocks[t] = df
            
    adj_dict = {t: df["adj_close"].reindex(cal).ffill() for t, df in stocks.items()}
    adj_mat = pd.DataFrame(adj_dict, index=cal)
    ret_mat = adj_mat.pct_change().fillna(0.0)
    
    qqq_ret_10y = qqq_df["adj_close"].pct_change().fillna(0.0).loc[cal_10y]
    qqq_bm_eq = (1.0 + qqq_ret_10y).cumprod()
    
    removal_dict = {t: d for d, t in OFFICIAL_REMOVALS}
    cost_rate = 0.0005
    
    def simulate(cal_dates, rebal_dates, get_weights_fn):
        eq = pd.Series(index=cal_dates, dtype=float)
        eq.iloc[0] = 1.0
        w_curr = pd.Series(dtype=float)
        rebal_set = set(rebal_dates)
        
        for idx in range(len(cal_dates)):
            dt = cal_dates[idx]
            if dt in rebal_set:
                target_w = get_weights_fn(dt, w_curr)
                if not target_w.empty:
                    union_idx = w_curr.index.union(target_w.index)
                    w_old = w_curr.reindex(union_idx, fill_value=0.0)
                    w_new = target_w.reindex(union_idx, fill_value=0.0)
                    turnover = (w_new - w_old).abs().sum() / 2.0
                    cost = turnover * cost_rate * 2.0
                    if idx > 0:
                        eq.iloc[idx] = eq.iloc[idx-1] * (1.0 - cost)
                    w_curr = target_w.copy()
            if idx == 0:
                continue
            eq_prev = eq.iloc[idx] if not np.isnan(eq.iloc[idx]) else eq.iloc[idx-1]
            if not w_curr.empty:
                active_ret = ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0)
                port_ret = (w_curr * active_ret).sum()
                uninvested = max(0.0, 1.0 - w_curr.sum())
                f_ret = qqq_ret_10y.loc[dt]
                total_ret = port_ret + uninvested * f_ret
                eq.iloc[idx] = eq_prev * (1.0 + total_ret)
                w_curr = w_curr * (1.0 + active_ret)
                total_val = w_curr.sum() + uninvested * (1.0 + f_ret)
                if total_val > 0:
                    w_curr = w_curr / total_val
            else:
                f_ret = qqq_ret_10y.loc[dt]
                eq.iloc[idx] = eq_prev * (1.0 + f_ret)
        return eq

    # 1. 12월 정기 변경일 날짜 목록 도출 (매년 12월 셋째 주 월요일 공식 효력 발생일)
    december_dates = []
    # 2016-01-04 (시작일) + 매년 12월 셋째 주 월요일
    december_dates.append(cal_10y[0])
    for y in range(2016, 2026):
        # 12월 날짜들 중 15일~24일 사이의 월요일
        dec_days = cal_10y[(cal_10y.year == y) & (cal_10y.month == 12) & (cal_10y.day >= 15) & (cal_10y.day <= 25)]
        if len(dec_days) > 0:
            # 12월 셋째 주 월요일 근처 첫 날
            december_dates.append(dec_days[0])
            
    december_dates = sorted(list(set(december_dates)))
    print(f"📅 12월 연 1회 리밸런싱 날짜 ({len(december_dates)}회): {[str(d.date()) for d in december_dates]}")

    # -------------------------------------------------------------
    # 12월 연 1회 전략 로직: 2.5년 만기 (12월 기준 2년 또는 3년 보유)
    # 연 1회 리밸런싱이므로 max_tenure는 2.5년(912일)으로 두면 2번의 12월을 지나고 3번째 12월(약 730~1095일 사이)에 교체됨
    # -------------------------------------------------------------
    def create_dec_tenure_logic(max_days=912):
        hold_start = {}
        def get_w(date, w_curr):
            eligible = [t for d, t in OFFICIAL_ADDITIONS if d <= date]
            survivors = []
            for sym in w_curr.index:
                rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                start_d = hold_start.get(sym, date)
                tenure_days = (date - start_d).days
                if date < rem_d and tenure_days < max_days:
                    survivors.append(sym)
                else:
                    hold_start.pop(sym, None)
            needed = 10 - len(survivors)
            if needed > 0:
                for sym in eligible:
                    rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                    if sym not in survivors and date < rem_d and sym in stocks:
                        if sym not in hold_start:
                            survivors.append(sym)
                            hold_start[sym] = date
                            if len(survivors) >= 10:
                                break
            if not survivors:
                return pd.Series(dtype=float)
            return pd.Series(1.0 / len(survivors), index=survivors)
        return get_w

    def create_dec_fifo_logic():
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

    # 1. Annual_Dec_2.5Y (12월 연 1회 + 2.5년 만기)
    eq_dec_25y = simulate(cal_10y, december_dates, create_dec_tenure_logic(912))
    
    # 2. Annual_Dec_3.0Y (12월 연 1회 + 3.0년 만기)
    eq_dec_3y = simulate(cal_10y, december_dates, create_dec_tenure_logic(1100))

    # 3. Annual_Dec_FIFO (12월 연 1회 + FIFO)
    eq_dec_fifo = simulate(cal_10y, december_dates, create_dec_fifo_logic())

    # 4. 비교군: EventOnly_2.5Y (이벤트 발생 시 리밸런싱, 22회)
    addition_events = sorted(list(set([d for d, _ in OFFICIAL_ADDITIONS if d >= pd.Timestamp("2016-01-01")])))
    event_only_dates = sorted(list(set([cal_10y[0]]).union(set(addition_events))))
    eq_event_only = simulate(cal_10y, event_only_dates, create_dec_tenure_logic(912))

    # 5. 비교군: Quarterly_2.5Y (분기 리밸런싱, 44회)
    quarterly_rebal_dates = []
    prev_quarter = None
    for d in cal_10y:
        q = (d.year, (d.month - 1) // 3)
        if prev_quarter is not None and q != prev_quarter:
            quarterly_rebal_dates.append(d)
        prev_quarter = q
    quarterly_rebal_dates = [cal_10y[0]] + quarterly_rebal_dates
    eq_quarterly = simulate(cal_10y, quarterly_rebal_dates, create_dec_tenure_logic(912))

    all_results = {
        "1. Annual_Dec_2.5Y (12월 연 1회 리밸런싱 + 2.5년 만기)": (eq_dec_25y, len(december_dates)),
        "2. Annual_Dec_3.0Y (12월 연 1회 리밸런싱 + 3.0년 만기)": (eq_dec_3y, len(december_dates)),
        "3. Annual_Dec_FIFO (12월 연 1회 리밸런싱 + FIFO 무기한)": (eq_dec_fifo, len(december_dates)),
        "4. EventOnly_2.5Y (신규 발생일에만 리밸런싱)": (eq_event_only, len(event_only_dates)),
        "5. Quarterly_2.5Y (기존 분기 고정 리밸런싱)": (eq_quarterly, len(quarterly_rebal_dates)),
        "6. QQQ_Actual_ETF (실물 기준선)": (qqq_bm_eq, 0)
    }

    print("\n" + "=" * 110)
    print("📊 [매년 12월 연 1회 단독 리밸런싱 vs 이벤트 전용 vs 분기 고정 성과 비교표]")
    print("=" * 110)
    print(f"{'전략 명칭':<48} | {'거래횟수':>5} | {'총 수익률':>10} | {'CAGR':>8} | {'샤프':>6} | {'MDD':>8} | {'베타':>5}")
    print("-" * 110)
    
    for name, (s, cnt) in all_results.items():
        m = compute_metrics(s, qqq_bm_eq)
        tot_str = f"{m['Total Return']*100:+.1f}%"
        cagr_str = f"{m['CAGR']*100:.2f}%"
        sh_str = f"{m['Sharpe']:.2f}"
        mdd_str = f"{m['MDD']*100:.2f}%"
        beta_str = f"{m['Beta']:.2f}" if not np.isnan(m['Beta']) else "1.00"
        cnt_str = f"{cnt}회" if cnt > 0 else "-"
        print(f"{name:<48} | {cnt_str:>5} | {tot_str:>10} | {cagr_str:>8} | {sh_str:>6} | {mdd_str:>8} | {beta_str:>5}")
    print("=" * 110)

if __name__ == "__main__":
    run_december_test()
