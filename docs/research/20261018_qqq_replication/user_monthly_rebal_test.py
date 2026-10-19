"""
[EXP-008] 사용자 지정 룰 백테스트:
1. 신규 편입 이벤트 발생 시에만 종목 교체 (탈락 종목의 비중/매도대금을 신규 종목이 1:1 승계)
2. 포트폴리오 비중 리밸런싱은 매월(Monthly) 정기 집행
========================================================================================
- 검증 기간: 2016-01-01 ~ 2026-10-14 (최근 10년 클린 구간)
- 대상: 나스닥 공식 56개 신규 편입 전수 종목
- 10종목 포트폴리오, 2.5년 만기 퇴출, 편도 0.05% (왕복 0.10%) 거래비용 차감
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

# 공식 신규 편입 56개
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

def run_simulation():
    print("=" * 95)
    print("🚀 [EXP-008] 사용자 지정 하이브리드 전략 백테스트")
    print("   1. 종목 변경: 신규 편입 이벤트 발생 시 탈락 종목의 비중을 1:1 승계")
    print("   2. 비중 리밸런싱: 매월(Monthly) 1회 정기 집행 (10종목 1/N 동일가중 복원)")
    print("=" * 95)
    
    qqq_df = load_stock_data("QQQ")
    if qqq_df is None:
        raise FileNotFoundError("QQQ 데이터 부재")
        
    cal = qqq_df.index
    cal_10y = cal[cal >= "2016-01-01"]
    
    # 56개 주식 로드
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
    
    # 매월 첫 거래일 (Monthly Rebalancing Dates)
    monthly_rebal_dates = []
    prev_month = None
    for d in cal_10y:
        m = (d.year, d.month)
        if prev_month is not None and m != prev_month:
            monthly_rebal_dates.append(d)
        prev_month = m
    monthly_rebal_dates = [cal_10y[0]] + monthly_rebal_dates
    monthly_set = set(monthly_rebal_dates)
    
    # 신규 편입 이벤트 거래일 맵핑
    addition_events = {}
    for d, sym in OFFICIAL_ADDITIONS:
        matched = cal_10y[cal_10y >= d]
        if len(matched) > 0:
            dt = matched[0]
            if dt not in addition_events:
                addition_events[dt] = []
            addition_events[dt].append(sym)
            
    event_set = set(addition_events.keys())
    removal_dict = {t: d for d, t in OFFICIAL_REMOVALS}
    
    print(f"📅 검증 기간: {cal_10y[0].date()} ~ {cal_10y[-1].date()} (총 {len(cal_10y):,} 거래일)")
    print(f"🔄 월별 리밸런싱 횟수: {len(monthly_rebal_dates)}회")
    print(f"🔄 신규 편입 이벤트 발생 횟수: {len(addition_events)}회")
    
    cost_rate = 0.0005 # 편도 0.05%
    max_tenure_days = 912 # 2.5년 = 912일
    
    # -------------------------------------------------------------
    # 시뮬레이션 엔진: 일별 자산 추적 및 조건부 이벤트 처리
    # -------------------------------------------------------------
    eq_user = pd.Series(index=cal_10y, dtype=float)
    eq_user.iloc[0] = 1.0
    w_curr = pd.Series(dtype=float) # 종목별 가중치
    hold_start = {} # {ticker: entry_date}
    
    # 시작 시점 초기화 (2016-01-04)
    # 시작 시점에는 아직 신규 편입 종목이 없으므로 QQQ 폴백으로 시작
    
    trade_logs = []
    
    for i in range(len(cal_10y)):
        d = cal_10y[i]
        
        # 1. 신규 편입 이벤트 발생일인 경우 (종목 변경 집행)
        if d in event_set:
            new_syms = addition_events[d]
            
            # (1) 퇴출 대상 종목 탐색: 2.5년 만기 도달 종목 또는 공식 탈락 종목
            exit_candidates = []
            for sym in list(w_curr.index):
                rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                start_d = hold_start.get(sym, d)
                tenure_days = (d - start_d).days
                if d >= rem_d or tenure_days >= max_tenure_days:
                    exit_candidates.append(sym)
                    
            # 만약 만기 종목이 없는데 포트폴리오가 10개 꽉 차 있고 새 종목이 들어온다면?
            # -> FIFO 기준 가장 오래된 종목을 퇴출 후보로 선정
            if len(w_curr) >= 10 and not exit_candidates:
                # 가장 오래 보유한 종목 순 정렬
                sorted_by_age = sorted(list(w_curr.index), key=lambda s: hold_start.get(s, d))
                exit_candidates = sorted_by_age[:len(new_syms)]
                
            # (2) 종목 1:1 승계 교체 (탈락 종목 비중 -> 신규 종목 비중)
            w_new = w_curr.copy()
            for new_s in new_syms:
                if new_s not in stocks:
                    continue
                if new_s in w_new.index:
                    continue # 이미 보유 중
                    
                if exit_candidates:
                    # 탈락 종목 비중을 그대로 승계
                    exit_s = exit_candidates.pop(0)
                    transferred_w = w_new.get(exit_s, 0.0)
                    w_new.drop(exit_s, inplace=True, errors="ignore")
                    hold_start.pop(exit_s, None)
                    
                    # 신규 종목에 승계
                    w_new[new_s] = transferred_w
                    hold_start[new_s] = d
                    
                    # 교체 턴오버 및 수수료 (탈락 매도 + 신규 매수)
                    turnover = transferred_w
                    cost = turnover * cost_rate * 2.0
                    if i > 0:
                        eq_user.iloc[i] = eq_user.iloc[i-1] * (1.0 - cost)
                    trade_logs.append((d, f"SWAP: {exit_s}({transferred_w*100:.1f}%) -> {new_s}"))
                else:
                    # 빈 슬롯이 있는 경우 (포트폴리오 < 10개)
                    # 목표 비중 10.0%로 신규 매수
                    target_slot_w = 0.10
                    w_new[new_s] = target_slot_w
                    hold_start[new_s] = d
                    cost = target_slot_w * cost_rate
                    if i > 0:
                        eq_user.iloc[i] = eq_user.iloc[i-1] * (1.0 - cost)
                    trade_logs.append((d, f"ADD: {new_s} (비중 {target_slot_w*100:.1f}%)"))
                    
            w_curr = w_new.copy()

        # 2. 매월 첫 거래일인 경우 (월별 정기 리밸런싱 집행: 1/N 동일가중 복원)
        if d in monthly_set and not w_curr.empty:
            # 퇴출 대상 재확인 (월말에 만기 지난 종목 정리)
            survivors = []
            for sym in list(w_curr.index):
                rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                start_d = hold_start.get(sym, d)
                tenure_days = (d - start_d).days
                if d < rem_d and tenure_days < max_tenure_days:
                    survivors.append(sym)
                else:
                    hold_start.pop(sym, None)
                    
            if survivors:
                target_w_val = 1.0 / len(survivors)
                target_w = pd.Series(target_w_val, index=survivors)
                
                # 턴오버 계산
                union_idx = w_curr.index.union(target_w.index)
                w_old = w_curr.reindex(union_idx, fill_value=0.0)
                w_new_rebal = target_w.reindex(union_idx, fill_value=0.0)
                turnover = (w_new_rebal - w_old).abs().sum() / 2.0
                cost = turnover * cost_rate * 2.0
                if i > 0:
                    eq_prev_val = eq_user.iloc[i] if not np.isnan(eq_user.iloc[i]) else eq_user.iloc[i-1]
                    eq_user.iloc[i] = eq_prev_val * (1.0 - cost)
                w_curr = target_w.copy()

        if i == 0:
            continue
            
        # 3. 일별 자산 가치 평가 (PnL 집행 및 주가 표류)
        eq_prev = eq_user.iloc[i] if not np.isnan(eq_user.iloc[i]) else eq_user.iloc[i-1]
        
        if not w_curr.empty:
            active_ret = ret_mat.loc[d].reindex(w_curr.index, fill_value=0.0)
            port_ret = (w_curr * active_ret).sum()
            uninvested = max(0.0, 1.0 - w_curr.sum())
            f_ret = qqq_ret_10y.loc[d]
            total_ret = port_ret + uninvested * f_ret
            eq_user.iloc[i] = eq_prev * (1.0 + total_ret)
            
            # 주가 등락에 따른 비중 자연 표류 (Drift)
            w_curr = w_curr * (1.0 + active_ret)
            total_val = w_curr.sum() + uninvested * (1.0 + f_ret)
            if total_val > 0:
                w_curr = w_curr / total_val
        else:
            f_ret = qqq_ret_10y.loc[d]
            eq_user.iloc[i] = eq_prev * (1.0 + f_ret)

    # -------------------------------------------------------------
    # 비교군 1: 기존 분기 고정 리밸런싱 (Quarterly 2.5Y)
    # -------------------------------------------------------------
    def local_tenure_logic(max_tenure_days=912):
        hold_start_q = {}
        def get_w(date, w_curr):
            eligible = [t for d, t in OFFICIAL_ADDITIONS if d <= date]
            survivors = []
            for sym in w_curr.index:
                rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                start_d = hold_start_q.get(sym, date)
                tenure_days = (date - start_d).days
                if date < rem_d and tenure_days < max_tenure_days:
                    survivors.append(sym)
                else:
                    hold_start_q.pop(sym, None)
            needed = 10 - len(survivors)
            if needed > 0:
                for sym in eligible:
                    rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                    if sym not in survivors and date < rem_d and sym in stocks:
                        if sym not in hold_start_q:
                            survivors.append(sym)
                            hold_start_q[sym] = date
                            if len(survivors) >= 10:
                                break
            if not survivors:
                return pd.Series(dtype=float)
            return pd.Series(1.0 / len(survivors), index=survivors)
        return get_w

    def local_simulate(cal_dates, rebal_dates, get_weights_fn):
        eq = pd.Series(index=cal_dates, dtype=float)
        eq.iloc[0] = 1.0
        w_curr_sim = pd.Series(dtype=float)
        rebal_set = set(rebal_dates)
        for idx in range(len(cal_dates)):
            dt = cal_dates[idx]
            if dt in rebal_set:
                target_w = get_weights_fn(dt, w_curr_sim)
                if not target_w.empty:
                    union_idx = w_curr_sim.index.union(target_w.index)
                    w_old = w_curr_sim.reindex(union_idx, fill_value=0.0)
                    w_new = target_w.reindex(union_idx, fill_value=0.0)
                    turnover = (w_new - w_old).abs().sum() / 2.0
                    cost = turnover * cost_rate * 2.0
                    if idx > 0:
                        eq.iloc[idx] = eq.iloc[idx-1] * (1.0 - cost)
                    w_curr_sim = target_w.copy()
            if idx == 0:
                continue
            eq_prev_sim = eq.iloc[idx] if not np.isnan(eq.iloc[idx]) else eq.iloc[idx-1]
            if not w_curr_sim.empty:
                active_ret = ret_mat.loc[dt].reindex(w_curr_sim.index, fill_value=0.0)
                port_ret = (w_curr_sim * active_ret).sum()
                uninvested = max(0.0, 1.0 - w_curr_sim.sum())
                f_ret = qqq_ret_10y.loc[dt]
                total_ret = port_ret + uninvested * f_ret
                eq.iloc[idx] = eq_prev_sim * (1.0 + total_ret)
                w_curr_sim = w_curr_sim * (1.0 + active_ret)
                total_val = w_curr_sim.sum() + uninvested * (1.0 + f_ret)
                if total_val > 0:
                    w_curr_sim = w_curr_sim / total_val
            else:
                f_ret = qqq_ret_10y.loc[dt]
                eq.iloc[idx] = eq_prev_sim * (1.0 + f_ret)
        return eq
    
    quarterly_rebal_dates = []
    prev_quarter = None
    for d in cal_10y:
        q = (d.year, (d.month - 1) // 3)
        if prev_quarter is not None and q != prev_quarter:
            quarterly_rebal_dates.append(d)
        prev_quarter = q
    quarterly_rebal_dates = [cal_10y[0]] + quarterly_rebal_dates
    
    eq_q_25y = local_simulate(cal_10y, quarterly_rebal_dates, local_tenure_logic(912))
    
    # -------------------------------------------------------------
    # 비교군 2: EventOnly (이벤트 날에만 전체 비중 재균등화)
    # -------------------------------------------------------------
    addition_event_dates = sorted(list(addition_events.keys()))
    event_only_rebal_dates = sorted(list(set([cal_10y[0]]).union(set(addition_event_dates))))
    eq_eo_25y = local_simulate(cal_10y, event_only_rebal_dates, local_tenure_logic(912))

    # -------------------------------------------------------------
    # 성과 비교표 출력
    # -------------------------------------------------------------
    all_results = {
        "1. User_Monthly_Strategy (신규시 승계교체 + 매월 1/N 리밸런싱)": eq_user,
        "2. EventOnly_2.5Y (신규 발생일에만 전체 1/N 리밸런싱)": eq_eo_25y,
        "3. Quarterly_2.5Y (기존 분기 고정 리밸런싱)": eq_q_25y,
        "4. QQQ_Actual_ETF (실물 기준선)": qqq_bm_eq
    }

    print("\n" + "=" * 110)
    print("📊 [사용자 하이브리드 전략 vs 기존 전략 최종 성과 비교표]")
    print("=" * 110)
    print(f"{'전략 명칭':<48} | {'총 수익률':>10} | {'CAGR':>8} | {'변동성':>7} | {'샤프':>6} | {'MDD':>8} | {'베타':>5}")
    print("-" * 110)
    
    for name, s in all_results.items():
        m = compute_metrics(s, qqq_bm_eq)
        tot_str = f"{m['Total Return']*100:+.1f}%"
        cagr_str = f"{m['CAGR']*100:.2f}%"
        vol_str = f"{m['Volatility']*100:.2f}%"
        sh_str = f"{m['Sharpe']:.2f}"
        mdd_str = f"{m['MDD']*100:.2f}%"
        beta_str = f"{m['Beta']:.2f}" if not np.isnan(m['Beta']) else "1.00"
        print(f"{name:<48} | {tot_str:>10} | {cagr_str:>8} | {vol_str:>7} | {sh_str:>6} | {mdd_str:>8} | {beta_str:>5}")
    print("=" * 110)

if __name__ == "__main__":
    run_simulation()
