"""
[EXP-011] 슬롯 제한 없는 56개 전수 2.5년 만기 보유 (All-Entrants 2.5Y Unconstrained) 백테스트
==============================================================================================
- 전략 가설:
  "10종목이라는 슬롯 한계를 없애고, 나스닥 100에 신규 편입되는 모든 종목(56개 전수)을 
   입성 즉시 예외 없이 100% 매수하되, 각 종목별로 정확히 2.5년(912일) 동안만 보유하고 만기 퇴출하면
   (공식 탈락 시 조기 퇴출), 슬롯 병목으로 인한 대기 지연 없이 모든 슈퍼 루키의 2.5년 복리를 전수 수확할 수 있을 것이다."
- 검증 기간: 2016-01-01 ~ 2026-10-14 (최근 10년 클린 구간)
- 대상: 공식 56개 신규 편입 전 종목 100% 전수
- 리밸런싱: 신규 편입 및 만기/탈락 퇴출 이벤트 발생 시 현재 살아있는 모든 종목에 1/N 균등 배분
- 거래비용: 편도 0.05% (왕복 0.10%) 거래비용 엄격 차감
"""
import os
import sys
import numpy as np
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

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

def run_unconstrained_experiment():
    print("=" * 95)
    print("🚀 [EXP-011] 슬롯 제한 없는 56개 전수 즉시 편입 × 2.5년 만기 보유 백테스트")
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
    adj_mat = pd.DataFrame(adj_dict, index=cal)
    ret_mat = adj_mat.pct_change().fillna(0.0)
    
    qqq_ret_10y = qqq_df["adj_close"].pct_change().fillna(0.0).loc[cal_10y]
    qqq_bm_eq = (1.0 + qqq_ret_10y).cumprod()
    
    removal_dict = {t: d for d, t in OFFICIAL_REMOVALS}
    cost_rate = 0.0005 # 편도 0.05%
    max_days = 912 # 2.5년 = 912일
    
    # 신규 편입 이벤트 발생일
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
    # 시뮬레이션 1: All_Entrants_2.5Y (슬롯 무제한, 56개 전수 편입)
    # -------------------------------------------------------------
    eq_all = pd.Series(index=cal_10y, dtype=float)
    eq_all.iloc[0] = 1.0
    w_curr = pd.Series(dtype=float)
    hold_start = {} # {ticker: entry_date}
    all_trade_records = []
    
    rebal_counts = 0
    port_size_history = []
    
    for i in range(len(cal_10y)):
        dt = cal_10y[i]
        
        # 이벤트 날짜 또는 매일 퇴출 검사 (만기 2.5년 도달 또는 공식 탈락)
        # 신규 편입일이거나 만기 종목이 발생한 날 리밸런싱 집행
        exited = []
        for sym in list(hold_start.keys()):
            rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
            tenure_days = (dt - hold_start[sym]).days
            if dt >= rem_d:
                exited.append(sym)
                all_trade_records.append({
                    "ticker": sym, "entry_date": hold_start[sym], "exit_date": dt, "exit_reason": f"공식탈락({rem_d.date()})"
                })
                hold_start.pop(sym)
            elif tenure_days >= max_days:
                exited.append(sym)
                all_trade_records.append({
                    "ticker": sym, "entry_date": hold_start[sym], "exit_date": dt, "exit_reason": f"2.5년만기({tenure_days}일)"
                })
                hold_start.pop(sym)
                
        # 신규 편입 종목이 있는 날
        new_adds = []
        if dt in addition_events:
            for s in addition_events[dt]:
                if s in stocks and s not in hold_start:
                    hold_start[s] = dt
                    new_adds.append(s)
                    
        # 리밸런싱 트리거 조건: 신규 편입이 있거나 퇴출이 발생한 경우
        if new_adds or exited or (i == 0 and hold_start):
            rebal_counts += 1
            if hold_start:
                n_hold = len(hold_start)
                target_w = pd.Series(1.0 / n_hold, index=list(hold_start.keys()))
                
                # 턴오버 및 비용 차감
                union_idx = w_curr.index.union(target_w.index)
                w_old = w_curr.reindex(union_idx, fill_value=0.0)
                w_new = target_w.reindex(union_idx, fill_value=0.0)
                turnover = (w_new - w_old).abs().sum() / 2.0
                cost = turnover * cost_rate * 2.0
                if i > 0:
                    eq_prev_val = eq_all.iloc[i] if not np.isnan(eq_all.iloc[i]) else eq_all.iloc[i-1]
                    eq_all.iloc[i] = eq_prev_val * (1.0 - cost)
                w_curr = target_w.copy()
            else:
                w_curr = pd.Series(dtype=float)
                
        port_size_history.append((dt, len(hold_start)))
        
        if i == 0:
            continue
            
        eq_prev = eq_all.iloc[i] if not np.isnan(eq_all.iloc[i]) else eq_all.iloc[i-1]
        if not w_curr.empty:
            active_ret = ret_mat.loc[dt].reindex(w_curr.index, fill_value=0.0)
            port_ret = (w_curr * active_ret).sum()
            uninvested = max(0.0, 1.0 - w_curr.sum())
            f_ret = qqq_ret_10y.loc[dt]
            total_ret = port_ret + uninvested * f_ret
            eq_all.iloc[i] = eq_prev * (1.0 + total_ret)
            
            # 주가 등락에 따른 표류 (Drift)
            w_curr = w_curr * (1.0 + active_ret)
            total_val = w_curr.sum() + uninvested * (1.0 + f_ret)
            if total_val > 0:
                w_curr = w_curr / total_val
        else:
            f_ret = qqq_ret_10y.loc[dt]
            eq_all.iloc[i] = eq_prev * (1.0 + f_ret)
            
    # 시뮬레이션 종료 시점 보유 종목 기록
    end_dt = cal_10y[-1]
    for sym, start_d in hold_start.items():
        all_trade_records.append({
            "ticker": sym, "entry_date": start_d, "exit_date": end_dt, "exit_reason": "현재보유중(진행형)"
        })
        
    print(f"📊 총 편입된 종목 수: {len(all_trade_records)}개 (56개 공식 편입 전수 100% 반영!)")
    print(f"🔄 총 리밸런싱 횟수: {rebal_counts}회")
    df_sizes = pd.DataFrame(port_size_history, columns=["date", "size"]).set_index("date")
    print(f"📈 평균 보유 종목 수: {df_sizes['size'].mean():.1f}개 (최소 {df_sizes['size'].min()}개 ~ 최대 {df_sizes['size'].max()}개)")
    
    # -------------------------------------------------------------
    # 56개 전수 월별 수익률 테이블 데이터 구축
    # -------------------------------------------------------------
    full_56_rows = []
    for tr in all_trade_records:
        sym = tr["ticker"]
        en_d = tr["entry_date"]
        ex_d = tr["exit_date"]
        series = adj_mat[sym].loc[en_d:ex_d]
        if len(series) < 2:
            continue
        p_start = series.iloc[0]
        p_end = series.iloc[-1]
        p_max = series.max()
        p_min = series.min()
        tot_ret = (p_end / p_start) - 1.0
        max_gain = (p_max / p_start) - 1.0
        max_draw = (p_min / p_start) - 1.0
        holding_days = (ex_d - en_d).days
        
        n_months = int(np.ceil(holding_days / 30.4))
        m_rets = []
        for m in range(n_months):
            m_s_idx = int(m * 21)
            m_e_idx = min(len(series) - 1, int((m + 1) * 21))
            if m_s_idx >= len(series) - 1:
                break
            ps = series.iloc[m_s_idx]
            pe = series.iloc[m_e_idx]
            m_ret = (pe / ps) - 1.0 if ps > 0 else 0.0
            m_rets.append(m_ret)
            
        row = {
            "종목코드": sym,
            "진입일": en_d.strftime("%Y-%m-%d"),
            "퇴출일": ex_d.strftime("%Y-%m-%d"),
            "체류일수": holding_days,
            "퇴출사유": tr["exit_reason"],
            "누적수익률(%)": round(tot_ret * 100, 2),
            "최고수익률(%)": round(max_gain * 100, 2),
            "최대낙폭(%)": round(max_draw * 100, 2)
        }
        for i in range(30):
            row[f"M{i+1}"] = round(m_rets[i] * 100, 2) if i < len(m_rets) else np.nan
        full_56_rows.append(row)
        
    df_all_56 = pd.DataFrame(full_56_rows).sort_values(["진입일", "종목코드"]).reset_index(drop=True)
    
    # 엑셀/CSV/HTML 저장
    excel_all_path = f"{REPORTS_DIR}/nasdaq100_all56_unconstrained_monthly.xlsx"
    csv_all_path = f"{REPORTS_DIR}/nasdaq100_all56_unconstrained_monthly.csv"
    html_all_path = f"{REPORTS_DIR}/nasdaq100_all56_unconstrained_monthly.html"
    
    df_all_56.to_csv(csv_all_path, index=False, encoding="utf-8-sig")
    
    with pd.ExcelWriter(excel_all_path, engine="openpyxl") as writer:
        df_all_56.to_excel(writer, sheet_name="All56_Monthly_Returns", index=False)
        ws = writer.sheets["All56_Monthly_Returns"]
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        header_font = Font(name="맑은 고딕", size=10, bold=True, color="FFFFFF")
        cell_font = Font(name="맑은 고딕", size=9)
        bold_font = Font(name="맑은 고딕", size=9, bold=True)
        thin_border = Border(left=Side(style='thin', color='D9D9D9'), right=Side(style='thin', color='D9D9D9'), top=Side(style='thin', color='D9D9D9'), bottom=Side(style='thin', color='D9D9D9'))
        pos_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
        neg_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
        
        ws.row_dimensions[1].height = 28
        for col_idx in range(1, len(df_all_56.columns) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
            
        for row_idx in range(2, len(df_all_56) + 2):
            ws.row_dimensions[row_idx].height = 20
            for col_idx in range(1, len(df_all_56.columns) + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.font = cell_font
                cell.border = thin_border
                col_name = df_all_56.columns[col_idx - 1]
                if col_name in ["종목코드", "진입일", "퇴출일"]:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    if col_name == "종목코드":
                        cell.font = bold_font
                elif col_name in ["체류일수"]:
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.number_format = "#,##0"
                elif col_name in ["누적수익률(%)", "최고수익률(%)", "최대낙폭(%)"]:
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.number_format = "+#,##0.0;-#,##0.0;0.0"
                    cell.font = bold_font
                    val = cell.value
                    if isinstance(val, (int, float)):
                        if val > 50:
                            cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                        elif val < -20:
                            cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                elif col_name.startswith("M"):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.number_format = "+#,##0.0;-#,##0.0;0.0"
                    val = cell.value
                    if isinstance(val, (int, float)):
                        if val > 0:
                            cell.fill = pos_fill
                        elif val < 0:
                            cell.fill = neg_fill
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                    
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 8)
        ws.freeze_panes = "D2"
        
    print(f"✅ Excel (.xlsx) 저장: {excel_all_path}")
    print(f"✅ CSV 저장: {csv_all_path}")
    
    all_res = {
        "1. All_Entrants_2.5Y (56개 전수 무제한 편입 + 2.5년 만기)": eq_all,
        "2. QQQ_Actual_ETF (실물 기준선)": qqq_bm_eq
    }
    
    print("\n" + "=" * 105)
    print("📊 [56개 전수 무제한 편입 vs 10개 슬롯 제한형 성과 비교표]")
    print("=" * 105)
    print(f"{'전략 명칭':<48} | {'총 수익률':>10} | {'CAGR':>8} | {'변동성':>7} | {'샤프':>6} | {'MDD':>8} | {'베타':>5}")
    print("-" * 105)
    for name, s in all_res.items():
        m = compute_metrics(s, qqq_bm_eq)
        tot_str = f"{m['Total Return']*100:+.1f}%"
        cagr_str = f"{m['CAGR']*100:.2f}%"
        vol_str = f"{m['Volatility']*100:.2f}%"
        sh_str = f"{m['Sharpe']:.2f}"
        mdd_str = f"{m['MDD']*100:.2f}%"
        beta_str = f"{m['Beta']:.2f}" if not np.isnan(m['Beta']) else "1.00"
        print(f"{name:<48} | {tot_str:>10} | {cagr_str:>8} | {vol_str:>7} | {sh_str:>6} | {mdd_str:>8} | {beta_str:>5}")
    print("=" * 105)

if __name__ == "__main__":
    run_unconstrained_experiment()
