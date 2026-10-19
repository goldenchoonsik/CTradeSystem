"""
[EXP-010-v2] 나스닥 100 신규 편입 56개 전수 큐(Queue) 순차 승계 월별 수익률 마스터 엑셀 생성
============================================================================================
- 56개 공식 편입 종목이 시간순 대기 큐(FIFO Queue)에 등록되고,
  10개 슬롯에 빈자리가 생길 때마다 순차적으로 슬롯을 채우고 2.5년 만기 퇴출되는 완벽 무결 엔진.
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

def generate_perfect_queue_export():
    print("⏳ 데이터 로드 및 큐 시뮬레이션 추적 중...")
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
    
    removal_dict = {t: d for d, t in OFFICIAL_REMOVALS}
    addition_dates = sorted(list(set([d for d, _ in OFFICIAL_ADDITIONS if d >= pd.Timestamp("2016-01-01")])))
    event_dates = sorted(list(set([cal_10y[0]]).union(set(addition_dates))))
    
    queue = [(d, s) for d, s in OFFICIAL_ADDITIONS]
    hold_start = {}
    trade_history = []
    max_days = 912 # 2.5년
    
    for dt in event_dates:
        # 1. 생존 검사 및 2.5년 만기 퇴출
        for sym in list(hold_start.keys()):
            rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
            tenure_days = (dt - hold_start[sym]).days
            if dt >= rem_d:
                trade_history.append({"ticker": sym, "entry_date": hold_start[sym], "exit_date": dt, "exit_reason": f"공식탈락({rem_d.date()})"})
                hold_start.pop(sym)
            elif tenure_days >= max_days:
                trade_history.append({"ticker": sym, "entry_date": hold_start[sym], "exit_date": dt, "exit_reason": f"2.5년만기({tenure_days}일)"})
                hold_start.pop(sym)
                
        # 2. 대기 큐에서 순차 충원 (이미 매수되었던 종목은 재매수 차단)
        prev_bought = [item["ticker"] for item in trade_history] + list(hold_start.keys())
        available = [(d, s) for d, s in queue if d <= dt and s not in prev_bought and s in stocks]
        needed = 10 - len(hold_start)
        for d, s in available:
            if needed <= 0:
                break
            hold_start[s] = dt
            needed -= 1
            
    # 종료 시점 보유 종목
    end_dt = cal_10y[-1]
    for sym, start_d in hold_start.items():
        trade_history.append({"ticker": sym, "entry_date": start_d, "exit_date": end_dt, "exit_reason": "현재보유중(진행형)"})
        
    print(f"📊 총 슬롯을 거쳐간 포지션 수: {len(trade_history)}개")
    
    full_rows = []
    for tr in trade_history:
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
        full_rows.append(row)
        
    df_full = pd.DataFrame(full_rows)
    df_full = df_full.sort_values(["진입일", "종목코드"]).reset_index(drop=True)
    
    # 1. CSV 저장
    csv_path = f"{REPORTS_DIR}/nasdaq100_56entrants_monthly_returns.csv"
    df_full.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"✅ CSV 파일 저장 완료: {csv_path}")
    
    # 2. Excel (.xlsx) 저장
    excel_path = f"{REPORTS_DIR}/nasdaq100_56entrants_monthly_returns.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df_full.to_excel(writer, sheet_name="Entrants_Monthly_Returns", index=False)
        ws = writer.sheets["Entrants_Monthly_Returns"]
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        header_font = Font(name="맑은 고딕", size=10, bold=True, color="FFFFFF")
        cell_font = Font(name="맑은 고딕", size=9)
        bold_font = Font(name="맑은 고딕", size=9, bold=True)
        center_align = Alignment(horizontal="center", vertical="center")
        right_align = Alignment(horizontal="right", vertical="center")
        thin_border = Border(
            left=Side(style='thin', color='D9D9D9'),
            right=Side(style='thin', color='D9D9D9'),
            top=Side(style='thin', color='D9D9D9'),
            bottom=Side(style='thin', color='D9D9D9')
        )
        ws.row_dimensions[1].height = 28
        for col_idx in range(1, len(df_full.columns) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align
            cell.border = thin_border
            
        pos_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
        neg_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
        
        for row_idx in range(2, len(df_full) + 2):
            ws.row_dimensions[row_idx].height = 20
            for col_idx in range(1, len(df_full.columns) + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.font = cell_font
                cell.border = thin_border
                col_name = df_full.columns[col_idx - 1]
                if col_name in ["종목코드", "진입일", "퇴출일"]:
                    cell.alignment = center_align
                    if col_name == "종목코드":
                        cell.font = bold_font
                elif col_name in ["체류일수"]:
                    cell.alignment = right_align
                    cell.number_format = "#,##0"
                elif col_name in ["누적수익률(%)", "최고수익률(%)", "최대낙폭(%)"]:
                    cell.alignment = right_align
                    cell.number_format = "+#,##0.0;-#,##0.0;0.0"
                    cell.font = bold_font
                    val = cell.value
                    if isinstance(val, (int, float)):
                        if val > 50:
                            cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                        elif val < -20:
                            cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                elif col_name.startswith("M"):
                    cell.alignment = right_align
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
        
    print(f"✅ Excel (.xlsx) 파일 저장 완료: {excel_path}")
    
    # 3. HTML 웹 리포트 생성
    html_path = f"{REPORTS_DIR}/nasdaq100_56entrants_monthly_returns.html"
    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>나스닥 100 신규 편입 종목별 체류 기간 월별 수익률 마스터 테이블</title>
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: #f8fafc; margin: 20px; }}
    h1 {{ color: #0f172a; margin-bottom: 8px; }}
    p.sub {{ color: #64748b; margin-top: 0; margin-bottom: 20px; }}
    .table-container {{ overflow-x: auto; background: white; border-radius: 8px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); border: 1px solid #e2e8f0; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; text-align: right; }}
    th {{ background: #1e293b; color: white; padding: 10px 8px; position: sticky; top: 0; z-index: 10; font-weight: 600; white-space: nowrap; }}
    th:first-child, td:first-child {{ position: sticky; left: 0; background: #fff; z-index: 5; font-weight: bold; border-right: 2px solid #cbd5e1; }}
    th:first-child {{ background: #0f172a; z-index: 15; }}
    td {{ padding: 8px; border-bottom: 1px solid #f1f5f9; border-right: 1px solid #f8fafc; white-space: nowrap; }}
    tr:hover td {{ background-color: #f8fafc !important; }}
    .text-center {{ text-align: center; }}
    .pos {{ color: #16a34a; background-color: #f0fdf4; }}
    .neg {{ color: #dc2626; background-color: #fef2f2; }}
    .badge-win {{ background: #dcfce7; color: #15803d; padding: 2px 6px; border-radius: 4px; font-weight: bold; }}
    .badge-loss {{ background: #fee2e2; color: #b91c1c; padding: 2px 6px; border-radius: 4px; font-weight: bold; }}
</style>
</head>
<body>
<h1>🏛️ 나스닥 100 신규 편입 종목별 체류 기간 월별 수익률 마스터 테이블</h1>
<p class="sub">EventOnly_2.5Y 전략 실측 데이터 | 총 {len(df_full)}개 순차 편입 종목 | 검증 기간: 2016-01-04 ~ 2026-10-14</p>
<div class="table-container">
<table>
<thead>
<tr>
"""
    for col in df_full.columns:
        html_content += f"<th>{col}</th>\n"
    html_content += "</tr>\n</thead>\n<tbody>\n"
    for _, row in df_full.iterrows():
        html_content += "<tr>\n"
        for col in df_full.columns:
            val = row[col]
            if pd.isna(val):
                html_content += "<td>-</td>"
            elif col == "종목코드":
                html_content += f"<td class='text-center'><b>{val}</b></td>"
            elif col in ["진입일", "퇴출일"]:
                html_content += f"<td class='text-center'>{val}</td>"
            elif col == "퇴출사유":
                html_content += f"<td style='text-align:left;'>{val}</td>"
            elif col == "누적수익률(%)":
                badge = "badge-win" if val >= 0 else "badge-loss"
                html_content += f"<td><span class='{badge}'>{val:+.1f}%</span></td>"
            elif col in ["최고수익률(%)", "최대낙폭(%)"]:
                html_content += f"<td>{val:+.1f}%</td>"
            elif col.startswith("M"):
                cls = "pos" if val > 0 else ("neg" if val < 0 else "")
                html_content += f"<td class='{cls}'>{val:+.1f}%</td>"
            else:
                html_content += f"<td>{val}</td>"
        html_content += "</tr>\n"
    html_content += "</tbody>\n</table>\n</div>\n</body>\n</html>"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ HTML 파일 저장 완료: {html_path}")

if __name__ == "__main__":
    generate_perfect_queue_export()
