"""
[EXP-012] 최하위 시총(Weakest-Link) 퇴출형 10종목 압축 백테스트
================================================================
- 전략 룰:
  1. 포트폴리오 10개 슬롯 고정 압축.
  2. 신규 편입 이벤트 발생 시에만 리밸런싱.
  3. 퇴출 규칙 (새로운 신규 편입 종목 K개 유입 시):
     - 공식 탈락 종목 우선 퇴출.
     - 슬롯이 부족하면, 현재 보유 종목 중(방금 들어온 신규 종목은 제외!) 
       해당 시점 '시가총액(60일 거래대금 프록시)'이 가장 낮은 꼴찌 K종목을 아웃(Out)!
  4. 비중: 10개 종목 1/N 동일가중.
  5. 거래비용: 편도 0.05% (왕복 0.10%) 차감.
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
REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

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

def run_lowest_cap_experiment():
    print("=" * 95)
    print("🏆 [EXP-012] 최하위 시총(Weakest-Link) 종목 퇴출형 10종목 백테스트")
    print("=" * 95)
    
    qqq_df = load_stock_data("QQQ")
    cal = qqq_df.index
    cal_10y = cal[cal >= pd.Timestamp("2016-01-01")]
    
    all_tickers = sorted(list(set([t for _, t in OFFICIAL_ADDITIONS])))
    stocks = {}
    for t in all_tickers:
        df = load_stock_data(t)
        if df is not None and len(df) > 100:
            stocks[t] = df
            
    adj_dict = {t: df["adj_close"].reindex(cal).ffill() for t, df in stocks.items()}
    dvol_dict = {t: df["dvol_60d"].reindex(cal).ffill() for t, df in stocks.items()}
    adj_mat = pd.DataFrame(adj_dict, index=cal)
    dvol_mat = pd.DataFrame(dvol_dict, index=cal)
    ret_mat = adj_mat.pct_change().fillna(0.0)
    
    qqq_ret_10y = qqq_df["adj_close"].pct_change().fillna(0.0).loc[cal_10y]
    qqq_bm_eq = (1.0 + qqq_ret_10y).cumprod()
    
    removal_dict = {t: d for d, t in OFFICIAL_REMOVALS}
    cost_rate = 0.0005
    
    # 신규 편입 이벤트 매핑
    addition_events = {}
    for d, sym in OFFICIAL_ADDITIONS:
        matched = cal_10y[cal_10y >= d]
        if len(matched) > 0:
            dt = matched[0]
            if dt not in addition_events:
                addition_events[dt] = []
            addition_events[dt].append(sym)
            
    event_dates = sorted(list(set([cal_10y[0]]).union(set(addition_events.keys()))))
    
    # -------------------------------------------------------------
    # 전략 1: Lowest_Cap_Exit_10Slots (최하위 시총/거래대금 아웃)
    # -------------------------------------------------------------
    def simulate_lowest_pruning(metric_mode="cap_dvol"):
        eq = pd.Series(index=cal_10y, dtype=float)
        eq.iloc[0] = 1.0
        w_curr = pd.Series(dtype=float)
        hold_dict = {} # {ticker: entry_date}
        trade_logs = []
        
        for i in range(len(cal_10y)):
            dt = cal_10y[i]
            
            if dt in event_dates:
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
                        
                # 2. 신규 편입 종목 수 K개만큼 슬롯 확보 필요
                # 만약 현재 생존 종목 + 신규 편입 > 10개라면,
                # 초과분 = len(survivors) + len(new_valid) - 10
                excess = (len(survivors) + len(new_valid)) - 10
                if excess > 0 and survivors:
                    # 기존 보유 종목(방금 들어온 new_valid 제외!) 중에서 지표가 가장 낮은 종목 excess개 아웃
                    if metric_mode == "cap_dvol":
                        # 해당 dt 시점의 60일 거래대금(시총 프록시) 기준 오름차순 정렬
                        dvol_series = dvol_mat.loc[dt].reindex(survivors).fillna(0.0)
                        sorted_candidates = dvol_series.sort_values(ascending=True) # 최하위 꼴찌부터
                    elif metric_mode == "perf":
                        # 보유 기간 누적 수익률 기준 꼴찌
                        perf_series = pd.Series(dtype=float)
                        for s in survivors:
                            p_start = adj_mat[s].loc[hold_dict[s]]
                            p_now = adj_mat[s].loc[dt]
                            perf_series[s] = (p_now / p_start) - 1.0 if p_start > 0 else 0.0
                        sorted_candidates = perf_series.sort_values(ascending=True)
                        
                    kick_out = list(sorted_candidates.index[:excess])
                    for k_sym in kick_out:
                        survivors.remove(k_sym)
                        trade_logs.append((dt, f"최하위아웃({metric_mode}): {k_sym}"))
                        hold_dict.pop(k_sym, None)
                        
                # 3. 신규 종목 슬롯 충원
                for s in new_valid:
                    if len(survivors) < 10:
                        survivors.append(s)
                        hold_dict[s] = dt
                        
                # 4. 리밸런싱 집행 (1/N 동일가중)
                if survivors:
                    target_w = pd.Series(1.0 / len(survivors), index=survivors)
                    union_idx = w_curr.index.union(target_w.index)
                    w_old = w_curr.reindex(union_idx, fill_value=0.0)
                    w_new = target_w.reindex(union_idx, fill_value=0.0)
                    turnover = (w_new - w_old).abs().sum() / 2.0
                    cost = turnover * cost_rate * 2.0
                    if i > 0:
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
                f_ret = qqq_ret_10y.loc[dt]
                total_ret = port_ret + uninvested * f_ret
                eq.iloc[i] = eq_prev * (1.0 + total_ret)
                
                w_curr = w_curr * (1.0 + active_ret)
                total_val = w_curr.sum() + uninvested * (1.0 + f_ret)
                if total_val > 0:
                    w_curr = w_curr / total_val
            else:
                f_ret = qqq_ret_10y.loc[dt]
                eq.iloc[i] = eq_prev * (1.0 + f_ret)
                
        return eq, trade_logs

    # 시뮬레이션 집행
    eq_lowest_cap, logs_cap = simulate_lowest_pruning("cap_dvol")
    eq_lowest_perf, logs_perf = simulate_lowest_pruning("perf")

    # 비교군: 기존 EventOnly_2.5Y (FIFO 만기형)
    # 표준 EventOnly 2.5Y 계산
    hold_start_f = {}
    eq_fifo_25y = pd.Series(index=cal_10y, dtype=float)
    eq_fifo_25y.iloc[0] = 1.0
    w_curr_f = pd.Series(dtype=float)
    
    for i in range(len(cal_10y)):
        dt = cal_10y[i]
        if dt in event_dates:
            eligible = [t for d, t in OFFICIAL_ADDITIONS if d <= dt]
            survivors_f = []
            for sym in list(w_curr_f.index):
                rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                tenure_days = (dt - hold_start_f.get(sym, dt)).days
                if dt < rem_d and tenure_days < 912:
                    survivors_f.append(sym)
                else:
                    hold_start_f.pop(sym, None)
            needed_f = 10 - len(survivors_f)
            if needed_f > 0:
                for sym in eligible:
                    rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                    if sym not in survivors_f and dt < rem_d and sym in stocks:
                        if sym not in hold_start_f:
                            survivors_f.append(sym)
                            hold_start_f[sym] = dt
                            if len(survivors_f) >= 10:
                                break
            if survivors_f:
                target_w = pd.Series(1.0 / len(survivors_f), index=survivors_f)
                union_idx = w_curr_f.index.union(target_w.index)
                w_old = w_curr_f.reindex(union_idx, fill_value=0.0)
                w_new = target_w.reindex(union_idx, fill_value=0.0)
                turnover = (w_new - w_old).abs().sum() / 2.0
                cost = turnover * cost_rate * 2.0
                if i > 0:
                    eq_prev_val = eq_fifo_25y.iloc[i] if not np.isnan(eq_fifo_25y.iloc[i]) else eq_fifo_25y.iloc[i-1]
                    eq_fifo_25y.iloc[i] = eq_prev_val * (1.0 - cost)
                w_curr_f = target_w.copy()
            else:
                w_curr_f = pd.Series(dtype=float)
        if i == 0:
            continue
        eq_prev_f = eq_fifo_25y.iloc[i] if not np.isnan(eq_fifo_25y.iloc[i]) else eq_fifo_25y.iloc[i-1]
        if not w_curr_f.empty:
            active_ret = ret_mat.loc[dt].reindex(w_curr_f.index, fill_value=0.0)
            port_ret = (w_curr_f * active_ret).sum()
            uninvested = max(0.0, 1.0 - w_curr_f.sum())
            f_ret = qqq_ret_10y.loc[dt]
            total_ret = port_ret + uninvested * f_ret
            eq_fifo_25y.iloc[i] = eq_prev_f * (1.0 + total_ret)
            w_curr_f = w_curr_f * (1.0 + active_ret)
            total_val = w_curr_f.sum() + uninvested * (1.0 + f_ret)
            if total_val > 0:
                w_curr_f = w_curr_f / total_val
        else:
            f_ret = qqq_ret_10y.loc[dt]
            eq_fifo_25y.iloc[i] = eq_prev_f * (1.0 + f_ret)

    all_res = {
        "1. Lowest_Cap_Exit_10Slots (최하위 시총/거래대금 아웃)": eq_lowest_cap,
        "2. Lowest_Perf_Exit_10Slots (보유수익률 최하위 아웃)": eq_lowest_perf,
        "3. EventOnly_FIFO_2.5Y (기존 2.5년 만기 퇴출형)": eq_fifo_25y,
        "4. QQQ_Actual_ETF (실물 기준선)": qqq_bm_eq
    }
    
    print("\n" + "=" * 110)
    print("📊 [최하위 시총 꼴찌 아웃 vs 기존 만기형 성과 비교표]")
    print("=" * 110)
    print(f"{'전략 명칭':<48} | {'총 수익률':>10} | {'CAGR':>8} | {'변동성':>7} | {'샤프':>6} | {'MDD':>8} | {'베타':>5}")
    print("-" * 110)
    for name, s in all_res.items():
        m = compute_metrics(s, qqq_bm_eq)
        tot_str = f"{m['Total Return']*100:+.1f}%"
        cagr_str = f"{m['CAGR']*100:.2f}%"
        vol_str = f"{m['Volatility']*100:.2f}%"
        sh_str = f"{m['Sharpe']:.2f}"
        mdd_str = f"{m['MDD']*100:.2f}%"
        beta_str = f"{m['Beta']:.2f}" if not np.isnan(m['Beta']) else "1.00"
        print(f"{name:<48} | {tot_str:>10} | {cagr_str:>8} | {vol_str:>7} | {sh_str:>6} | {mdd_str:>8} | {beta_str:>5}")
    print("=" * 110)
    
    print("\n[퇴출 로그 샘플 (최하위 시총 기준 Top 10건)]")
    for d, log_msg in logs_cap[:10]:
        print(f"[{d.strftime('%Y-%m-%d')}] {log_msg}")

if __name__ == "__main__":
    run_lowest_cap_experiment()
