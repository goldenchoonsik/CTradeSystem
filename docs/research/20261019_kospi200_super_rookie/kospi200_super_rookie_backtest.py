"""
[EXP-KRX-001] 코스피 200 (KOSPI 200) 대상 슈퍼루키 (Super Rookie) 포트폴리오 전략 실측 백테스트
=============================================================================================
- 검증 기간: 2013-01-02 ~ 2026-10-12 (약 13.8년, 총 3,380여 거래일)
- 전략 가설:
  코스피 200 지수에 신규 진입/승격되는 '가장 강력한 모멘텀의 새내기 승격주'를 포착하여
  승격 초기 복리 랠리를 향유하고, 국내 약세장(2018년 미중무역분쟁, 2022년 금리인상 등)에서는
  '개별 종목 -15% 손절 후 슬롯 공백(현금 대기)' 메커니즘으로 하락 충격을 원천 방어하여
  코스피 200 벤치마크 대비 압도적인 샤프 지수와 절대 초과 수익(알파)을 실현한다.

- 핵심 운용 규칙:
  1. 슬롯 구조: 3개(N=3), 5개(N=5), 8개(N=8), 10개(N=10), 무제한(Unconstrained)
  2. 슬롯 공백 현금 대기: 진입가 대비 -15% 도달 시 즉시 전량 손절 후 슬롯 공백(현금 연 2.5% 이자) 유지
  3. 만기 방출: -15% 손절선을 건드리지 않는 한 최대 2.5년(912일) 보유 후 차익실현
  4. 꼴찌 우선 방출: 신규 편입주 대비 슬롯이 부족한 경우 기존 보유 종목 중 수익률 최하위부터 방출
  5. 거래비용: 매수 편도 0.035%, 매도 편도 0.235% (증권거래세 0.20% 포함) = 왕복 0.27% 엄격 차감
"""
import os
import sys
import time
import numpy as np
import pandas as pd

ROOT = "/home/kuzimo/projects/CTradeSystem"
REPORTS_DIR = f"{ROOT}/reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

def compute_metrics(eq_series, benchmark_eq=None):
    """표준 퀀트 성과 지표 산출 함수"""
    total_ret = (eq_series.iloc[-1] / eq_series.iloc[0]) - 1.0
    years = (eq_series.index[-1] - eq_series.index[0]).days / 365.25
    cagr = (eq_series.iloc[-1] / eq_series.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    
    daily_rets = eq_series.pct_change().dropna()
    ann_vol = daily_rets.std() * np.sqrt(252) if len(daily_rets) > 0 else 0.0
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

def run_kospi200_super_rookie_backtest():
    t_start = time.time()
    print("=" * 115)
    print("🚀 [실측 백테스트] 코스피 200 (KOSPI 200) 대상 슈퍼루키 (Super Rookie) 포트폴리오 전략 검증 (2013~2026)")
    print("=" * 115)
    
    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    # 1. 유니버스 로드
    uni = pd.read_csv(uni_path)
    name_map = dict(zip(uni['code'], uni['name']))
    kospi_codes = set(uni[(uni['market'] == 'KOSPI') & (uni['is_preferred'] == 0) & (uni['is_spac'] == 0)]['code'])
    print(f"📊 KOSPI 보통주 유니버스: {len(kospi_codes)}개 종목")

    # 2. 벤치마크 (U180 = 코스피 200 지수) 로드
    df_idx = pd.read_parquet(idx_path)
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u180.index
    print(f"📅 검증 기간: {cal[0].strftime('%Y-%m-%d')} ~ {cal[-1].strftime('%Y-%m-%d')} (총 {len(cal):,} 거래일, 13.8년)")

    # 3. 가격 및 시가총액 데이터 로드
    print("⏳ 가격 및 시가총액 시계열 로딩 중...")
    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'value'],
        filters=[('code', 'in', list(kospi_codes))]
    )
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap'],
        filters=[('code', 'in', list(kospi_codes))]
    )

    # 피벗 행렬 구축
    adj_mat = df_p.pivot(index='date', columns='code', values='close').reindex(cal).ffill()
    mcap_mat = df_ext.pivot(index='date', columns='code', values='market_cap').reindex(cal).ffill()
    ret_mat = adj_mat.pct_change().fillna(0.0)

    # 벤치마크 자산곡선
    bm_ret = u180.pct_change().fillna(0.0)
    bm_eq = (1.0 + bm_ret).cumprod()

    # 4. 정기변경 리밸런싱 날짜 및 신규 편입주(슈퍼루키) 도출
    # 60일 이동평균 시가총액 계산
    mcap_roll = mcap_mat.rolling(60, min_periods=20).mean()

    # 정기변경 일정: 6월 둘째주 / 12월 둘째주 (반기별 정기변경)
    rebal_dates = []
    prev_period = None
    for d in cal:
        period = (d.year, 0 if d.month <= 6 else 1)
        if prev_period is not None and period != prev_period:
            rebal_dates.append(d)
        prev_period = period
    rebal_dates = [cal[40]] + rebal_dates

    d0 = rebal_dates[0]
    top200_0 = mcap_roll.loc[d0].dropna().sort_values(ascending=False).head(200).index.tolist()
    known_universe = set(top200_0)

    addition_events = {}
    for d in rebal_dates[1:]:
        top200_curr = set(mcap_roll.loc[d].dropna().sort_values(ascending=False).head(200).index.tolist())
        brand_new = sorted(list(top200_curr - known_universe))
        if brand_new:
            # 가격 데이터가 정상 존재하는 종목만 채택
            valid_new = [s for s in brand_new if s in adj_mat.columns and not np.isnan(adj_mat[s].loc[d])]
            if valid_new:
                addition_events[d] = valid_new
                for s in valid_new:
                    known_universe.add(s)

    all_event_dates = sorted(list(set([cal[0]]).union(set(addition_events.keys()))))
    total_entrants_count = sum(len(v) for v in addition_events.values())
    print(f"🎯 총 신규 진입 이벤트: {len(addition_events)}회, 총 포착된 슈퍼루키: {total_entrants_count}개 종목")

    # 거래비용 모델
    cost_buy = 0.00035    # 매수 편도 0.035% (수수료+슬리피지)
    cost_sell = 0.00235   # 매도 편도 0.235% (수수료+슬리피지+거래세 0.20%)
    daily_cash_ret = 0.025 / 252.0 # 단기 금리/CD 연 2.5%

    # =========================================================================
    # 슈퍼루키 시뮬레이션 코어 함수
    # =========================================================================
    def simulate_super_rookie(target_n=None, stop_loss_pct=0.15, max_tenure_days=912):
        """
        target_n: 슬롯 개수 (3, 5, 8, 10, None=무제한)
        stop_loss_pct: 손절 임계값 (0.15 = -15% 손절 후 슬롯 공백 유지)
        max_tenure_days: 최대 보유 일수 (912일 = 2.5년)
        """
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = 1.0
        w_curr = pd.Series(dtype=float)
        hold_entry_px = {}
        hold_entry_dt = {}
        
        # 시작 시점 포트폴리오 (초기 Top 200 중 상위 N개로 시작)
        if target_n is not None:
            init_syms = [t for t in top200_0 if t in adj_mat.columns][:target_n]
            slot_w = 1.0 / target_n
        else:
            init_syms = [t for t in top200_0 if t in adj_mat.columns][:10]
            slot_w = 1.0 / len(init_syms) if init_syms else 0.0
            
        for t in init_syms:
            hold_entry_dt[t] = cal[0]
            hold_entry_px[t] = adj_mat[t].iloc[0]
        w_curr = pd.Series(slot_w, index=init_syms)
        
        # 초기 매수 비용 차감
        init_cost = w_curr.sum() * cost_buy
        eq.iloc[0] = 1.0 - init_cost
        
        stop_count = 0
        tenure_exit_count = 0
        trade_log = []
        cash_history = []
        
        for i in range(len(cal)):
            dt = cal[i]
            
            # 1. 일별 손절 검사 (진입가 대비 -15%)
            if stop_loss_pct is not None and not w_curr.empty:
                for sym in list(w_curr.index):
                    px_now = adj_mat[sym].iloc[i]
                    p_entry = hold_entry_px.get(sym, 0.0)
                    if p_entry > 0 and px_now > 0:
                        stock_ret = (px_now / p_entry) - 1.0
                        if stock_ret <= -stop_loss_pct:
                            # 손절 집행: 슬롯 공백(현금)으로 전환
                            w_loss = w_curr[sym]
                            eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                            eq.iloc[i] = eq_prev_val * (1.0 - w_loss * cost_sell)
                            w_curr.drop(sym, inplace=True)
                            hold_days = (dt - hold_entry_dt[sym]).days
                            trade_log.append({
                                "code": sym, "name": name_map.get(sym, sym),
                                "entry_dt": hold_entry_dt[sym], "exit_dt": dt,
                                "entry_px": p_entry, "exit_px": px_now,
                                "return": stock_ret, "hold_days": hold_days,
                                "exit_reason": "STOP_LOSS"
                            })
                            hold_entry_px.pop(sym, None)
                            hold_entry_dt.pop(sym, None)
                            stop_count += 1
                            
            # 2. 리밸런싱 이벤트 도래 시
            if dt in all_event_dates and i > 0:
                new_syms = addition_events.get(dt, [])
                new_valid = [s for s in new_syms if s in adj_mat.columns and s not in w_curr.index]
                
                survivors = list(w_curr.index)
                
                # A. 2.5년(912일) 만기 차익실현 방출
                expired = []
                for s in survivors:
                    if (dt - hold_entry_dt[s]).days >= max_tenure_days:
                        expired.append(s)
                for exp_s in expired:
                    survivors.remove(exp_s)
                    p_entry = hold_entry_px[exp_s]
                    p_now = adj_mat[exp_s].iloc[i]
                    s_ret = (p_now / p_entry) - 1.0 if p_entry > 0 else 0.0
                    hold_days = (dt - hold_entry_dt[exp_s]).days
                    trade_log.append({
                        "code": exp_s, "name": name_map.get(exp_s, exp_s),
                        "entry_dt": hold_entry_dt[exp_s], "exit_dt": dt,
                        "entry_px": p_entry, "exit_px": p_now,
                        "return": s_ret, "hold_days": hold_days,
                        "exit_reason": "TENURE_EXPIRED"
                    })
                    hold_entry_px.pop(exp_s, None)
                    hold_entry_dt.pop(exp_s, None)
                    tenure_exit_count += 1
                    
                # B. 신규 종목 충원
                if target_n is not None:
                    needed_slots = len(new_valid)
                    free_slots = target_n - len(survivors)
                    # 만약 빈 슬롯보다 신규 진입주가 많다면, 기존 생존 종목 중 '수익률 최하위'부터 방출
                    if needed_slots > free_slots and survivors:
                        excess = min(len(survivors), needed_slots - free_slots)
                        perf_s = pd.Series(dtype=float)
                        for s in survivors:
                            p_start = hold_entry_px[s]
                            p_curr_val = adj_mat[s].iloc[i]
                            perf_s[s] = (p_curr_val / p_start) - 1.0 if p_start > 0 else 0.0
                        lowest_perf = perf_s.sort_values(ascending=True).index.tolist()
                        for k_sym in lowest_perf[:excess]:
                            survivors.remove(k_sym)
                            p_entry = hold_entry_px[k_sym]
                            p_now = adj_mat[k_sym].iloc[i]
                            s_ret = (p_now / p_entry) - 1.0 if p_entry > 0 else 0.0
                            trade_log.append({
                                "code": k_sym, "name": name_map.get(k_sym, k_sym),
                                "entry_dt": hold_entry_dt[k_sym], "exit_dt": dt,
                                "entry_px": p_entry, "exit_px": p_now,
                                "return": s_ret, "hold_days": (dt - hold_entry_dt[k_sym]).days,
                                "exit_reason": "PRUNED_FOR_NEW_ROOKIE"
                            })
                            hold_entry_px.pop(k_sym, None)
                            hold_entry_dt.pop(k_sym, None)
                            
                    open_slots = target_n - len(survivors)
                    if open_slots > 0 and new_valid:
                        for s in new_valid[:open_slots]:
                            survivors.append(s)
                            hold_entry_dt[s] = dt
                            hold_entry_px[s] = adj_mat[s].iloc[i]
                    target_slot_w = 1.0 / target_n
                else:
                    # 무제한(Unconstrained): 들어오는 모든 신규주 편입
                    if new_valid:
                        for s in new_valid:
                            survivors.append(s)
                            hold_entry_dt[s] = dt
                            hold_entry_px[s] = adj_mat[s].iloc[i]
                    target_slot_w = 1.0 / len(survivors) if survivors else 0.0
                    
                # C. 리밸런싱 가중치 갱신 및 거래비용 차감
                if survivors:
                    target_w = pd.Series(target_slot_w, index=survivors)
                    union_idx = w_curr.index.union(target_w.index)
                    w_old = w_curr.reindex(union_idx, fill_value=0.0)
                    w_new = target_w.reindex(union_idx, fill_value=0.0)
                    
                    # 매수 증가분과 매도 감소분 분리 비용 계산
                    buy_turnover = np.maximum(0.0, w_new - w_old).sum()
                    sell_turnover = np.maximum(0.0, w_old - w_new).sum()
                    cost = (buy_turnover * cost_buy) + (sell_turnover * cost_sell)
                    
                    eq_prev_val = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
                    eq.iloc[i] = eq_prev_val * (1.0 - cost)
                    w_curr = target_w.copy()
                else:
                    w_curr = pd.Series(dtype=float)
                    
            if i == 0:
                continue
                
            # 3. 자산 가격 표류 및 현금 이자 반영
            eq_prev = eq.iloc[i] if not np.isnan(eq.iloc[i]) else eq.iloc[i-1]
            if not w_curr.empty:
                r_stocks = ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0)
                port_ret = (w_curr * r_stocks).sum()
                w_curr = w_curr * (1.0 + r_stocks)
            else:
                port_ret = 0.0
                
            cash_w = max(0.0, 1.0 - w_curr.sum())
            cash_history.append(cash_w)
            cash_ret = cash_w * daily_cash_ret
            total_ret = port_ret + cash_ret
            eq.iloc[i] = eq_prev * (1.0 + total_ret)
            
        return eq, trade_log, stop_count, tenure_exit_count, cash_history

    # =========================================================================
    # 전체 전략 라인업 실행 및 평가
    # =========================================================================
    strategies = {
        "KOSPI_ROOKIE_N3": {"target_n": 3, "stop_loss_pct": 0.15, "max_tenure_days": 912, "desc": "슈퍼루키-3 (3슬롯, -15% 손절)"},
        "KOSPI_ROOKIE_N5": {"target_n": 5, "stop_loss_pct": 0.15, "max_tenure_days": 912, "desc": "슈퍼루키-5 (5슬롯, -15% 손절)"},
        "KOSPI_ROOKIE_N8": {"target_n": 8, "stop_loss_pct": 0.15, "max_tenure_days": 912, "desc": "슈퍼루키-8 (8슬롯, -15% 손절)"},
        "KOSPI_ROOKIE_N10": {"target_n": 10, "stop_loss_pct": 0.15, "max_tenure_days": 912, "desc": "슈퍼루키-10 (10슬롯, -15% 손절)"},
        "ROOKIE_N3_NO_SL": {"target_n": 3, "stop_loss_pct": None, "max_tenure_days": 912, "desc": "슈퍼루키-3 무손절 (손절효과 대조군)"},
        "ROOKIE_N10_NO_SL": {"target_n": 10, "stop_loss_pct": None, "max_tenure_days": 912, "desc": "슈퍼루키-10 무손절 (손절효과 대조군)"},
        "ROOKIE_UNCONSTRAINED": {"target_n": None, "stop_loss_pct": 0.15, "max_tenure_days": 912, "desc": "슈퍼루키 무제한 (전체 편입)"},
        "KOSPI_200_INDEX": {"target_n": "BM", "desc": "KOSPI 200 지수 (U180 실물)"}
    }

    results = {}
    eq_curves = {}
    all_trades = {}

    print("\n" + "=" * 115)
    print("📈 백테스트 시뮬레이션 집행 중...")
    print("=" * 115)

    for name, cfg in strategies.items():
        if name == "KOSPI_200_INDEX":
            m = compute_metrics(bm_eq, bm_eq)
            results[name] = m
            eq_curves[name] = bm_eq
            print(f"  ✅ [완료] {cfg['desc']:<35} | 총수익률: {m['Total Return']*100:>+7.2f}% | CAGR: {m['CAGR']*100:>5.2f}% | MDD: {m['MDD']*100:>6.2f}% | Sharpe: {m['Sharpe']:>5.2f}")
        else:
            eq, t_log, s_cnt, t_cnt, cash_h = simulate_super_rookie(
                target_n=cfg["target_n"],
                stop_loss_pct=cfg["stop_loss_pct"],
                max_tenure_days=cfg["max_tenure_days"]
            )
            m = compute_metrics(eq, bm_eq)
            m["Stop Count"] = s_cnt
            m["Tenure Exits"] = t_cnt
            m["Avg Cash Ratio"] = np.mean(cash_h) if cash_h else 0.0
            results[name] = m
            eq_curves[name] = eq
            all_trades[name] = t_log
            print(f"  ✅ [완료] {cfg['desc']:<35} | 총수익률: {m['Total Return']*100:>+7.2f}% | CAGR: {m['CAGR']*100:>5.2f}% | MDD: {m['MDD']*100:>6.2f}% | Sharpe: {m['Sharpe']:>5.2f} (손절: {s_cnt}회)")

    # 5. 성과 비교표 요약
    res_df = pd.DataFrame(results).T
    res_df["Total Return (%)"] = res_df["Total Return"] * 100
    res_df["CAGR (%)"] = res_df["CAGR"] * 100
    res_df["Volatility (%)"] = res_df["Volatility"] * 100
    res_df["MDD (%)"] = res_df["MDD"] * 100

    display_cols = ["Total Return (%)", "CAGR (%)", "Volatility (%)", "Sharpe", "MDD (%)", "Beta", "Stop Count"]
    print("\n" + "=" * 115)
    print("🏆 [2013~2026년 13.8년 실측 성과 비교표]")
    print("=" * 115)
    print(res_df[display_cols].to_string())

    # 6. 연도별 수익률 매트릭스 계산
    annual_rets = {}
    for name, eq in eq_curves.items():
        # 연말 종가 추출
        y_eq = eq.resample("YE").last()
        # 시작점 추가
        y_eq_all = pd.concat([pd.Series([eq.iloc[0]], index=[eq.index[0]]), y_eq])
        y_ret = y_eq_all.pct_change().dropna()
        annual_rets[name] = y_ret
        
    ann_df = pd.DataFrame(annual_rets)
    ann_df.index = ann_df.index.year
    ann_df_pct = ann_df * 100

    print("\n" + "=" * 115)
    print("📅 [연도별 수익률 매트릭스 (%)]")
    print("=" * 115)
    print(ann_df_pct.round(2).to_string())

    # 7. 대박 종목 Top 15 (슈퍼루키-3 & 슈퍼루키-10)
    for target_key in ["KOSPI_ROOKIE_N3", "KOSPI_ROOKIE_N10"]:
        t_list = all_trades.get(target_key, [])
        if t_list:
            df_t = pd.DataFrame(t_list)
            top15 = df_t.sort_values("return", ascending=False).head(15)
            print("\n" + "=" * 115)
            print(f"🌟 [{strategies[target_key]['desc']}] 역대 최고 수익 슈퍼루키 TOP 15")
            print("=" * 115)
            for idx, r in top15.iterrows():
                print(f"  {r['code']} | {r['name']:<12} | 진입: {r['entry_dt'].strftime('%Y-%m-%d')} | 청산: {r['exit_dt'].strftime('%Y-%m-%d')} | 보유일: {r['hold_days']:>4}일 | 수익률: {r['return']*100:>+7.2f}% | 사유: {r['exit_reason']}")

    # 8. CSV 보고서 저장
    summary_csv = f"{REPORTS_DIR}/kospi200_super_rookie_summary.csv"
    res_df.to_csv(summary_csv, encoding="utf-8-sig")
    ann_csv = f"{REPORTS_DIR}/kospi200_super_rookie_annual_returns.csv"
    ann_df_pct.to_csv(ann_csv, encoding="utf-8-sig")
    
    # 슈퍼루키-3 거래 내역 저장
    n3_trades_df = pd.DataFrame(all_trades.get("KOSPI_ROOKIE_N3", []))
    n3_trades_df.to_csv(f"{REPORTS_DIR}/kospi200_rookie3_trade_log.csv", index=False, encoding="utf-8-sig")

    print(f"\n💾 성과 보고서 저장 완료: {summary_csv}, {ann_csv}")
    print(f"⏱️ 총 소요 시간: {time.time()-t_start:.2f}초")

if __name__ == "__main__":
    run_kospi200_super_rookie_backtest()
