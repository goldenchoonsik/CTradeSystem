"""
[EXP-010] EventOnly_2.5Y 포트폴리오 편입 종목별 진입~퇴출 전 기간 월별 수익률 전수 분석
======================================================================================
- 목표:
  1. EventOnly_2.5Y 전략에서 실제로 매수되어 2.5년 만기(또는 공식 탈락)까지 보유된 모든 종목의
     진입일(Entry Date), 퇴출일(Exit Date), 보유 일수, 누적 수익률(Total Return), 최대 상승률(Max High) 전수 추출.
  2. 각 종목별 보유 기간 동안의 월별 수익률(Monthly Return) 매트릭스 도출.
  3. 경과 개월차(Month 1 ~ Month 30)별 평균 수익률 및 승률 패턴 심층 분석:
     "입성 후 몇 개월 차에 가장 주가가 폭발하고, 언제부터 힘이 빠지는가?"
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

def run_tenure_analysis():
    print("=" * 95)
    print("🔍 [EXP-010] EventOnly_2.5Y 편입 종목별 진입~퇴출 월별 수익률 전수 분석")
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
    
    removal_dict = {t: d for d, t in OFFICIAL_REMOVALS}
    
    # 신규 편입 이벤트 발생일 목록
    addition_events = sorted(list(set([d for d, _ in OFFICIAL_ADDITIONS if d >= pd.Timestamp("2016-01-01")])))
    event_only_dates = sorted(list(set([cal_10y[0]]).union(set(addition_events))))
    
    # EventOnly_2.5Y 시뮬레이션 추적
    hold_start = {}
    trade_history = [] # list of dict: ticker, entry_date, exit_date, exit_reason
    
    w_curr = pd.Series(dtype=float)
    max_days = 912 # 2.5년
    
    for idx in range(len(cal_10y)):
        dt = cal_10y[idx]
        if dt in event_only_dates:
            eligible = [t for d, t in OFFICIAL_ADDITIONS if d <= dt]
            
            # 생존 검사
            survivors = []
            for sym in list(w_curr.index):
                rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                start_d = hold_start.get(sym, dt)
                tenure_days = (dt - start_d).days
                
                if dt >= rem_d:
                    # 공식 탈락 퇴출
                    trade_history.append({
                        "ticker": sym,
                        "entry_date": start_d,
                        "exit_date": dt,
                        "exit_reason": f"공식탈락({rem_d.date()})"
                    })
                    hold_start.pop(sym, None)
                elif tenure_days >= max_days:
                    # 2.5년 만기 퇴출
                    trade_history.append({
                        "ticker": sym,
                        "entry_date": start_d,
                        "exit_date": dt,
                        "exit_reason": f"2.5년만기({tenure_days}일)"
                    })
                    hold_start.pop(sym, None)
                else:
                    survivors.append(sym)
                    
            needed = 10 - len(survivors)
            if needed > 0:
                for sym in eligible:
                    rem_d = removal_dict.get(sym, pd.Timestamp("2099-12-31"))
                    if sym not in survivors and dt < rem_d and sym in stocks:
                        if sym not in hold_start:
                            survivors.append(sym)
                            hold_start[sym] = dt
                            if len(survivors) >= 10:
                                break
                                
            w_curr = pd.Series(1.0 / len(survivors), index=survivors) if survivors else pd.Series(dtype=float)
            
    # 시뮬레이션 종료 시점(2026-10-14)까지 여전히 보유 중인 종목들 기록
    end_dt = cal_10y[-1]
    for sym, start_d in hold_start.items():
        trade_history.append({
            "ticker": sym,
            "entry_date": start_d,
            "exit_date": end_dt,
            "exit_reason": "현재보유중(진행형)"
        })
        
    print(f"📊 총 편입 및 운용된 포지션 수: {len(trade_history)}건")
    
    # -------------------------------------------------------------
    # 각 종목별 보유 기간 동안의 월별 수익률 및 누적 성과 계산
    # -------------------------------------------------------------
    records = []
    monthly_matrix_data = [] # {ticker, entry_date, exit_date, m1, m2, ..., m30}
    
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
        
        # 월별 수익률 계산 (진입일로부터 30일 간격 또는 월력 기준)
        # 1~30개월 차 월별 수익률 (Tenure Month 1 ~ Month 30)
        # 각 월(30.4일 기준) 슬라이스
        n_months = int(np.ceil(holding_days / 30.4))
        m_rets = []
        for m in range(n_months):
            m_start_idx = int(m * 21) # 약 21 영업일
            m_end_idx = min(len(series) - 1, int((m + 1) * 21))
            if m_start_idx >= len(series) - 1:
                break
            p_s = series.iloc[m_start_idx]
            p_e = series.iloc[m_end_idx]
            m_ret = (p_e / p_s) - 1.0 if p_s > 0 else 0.0
            m_rets.append(m_ret)
            
        rec = {
            "Ticker": sym,
            "진입일": en_d.strftime("%Y-%m-%d"),
            "퇴출일": ex_d.strftime("%Y-%m-%d"),
            "보유일수": holding_days,
            "퇴출사유": tr["exit_reason"],
            "총수익률(%)": round(tot_ret * 100, 1),
            "최고상승률(%)": round(max_gain * 100, 1),
            "최대하락률(%)": round(max_draw * 100, 1),
            "월수": len(m_rets)
        }
        records.append(rec)
        
        # 월별 매트릭스 행
        m_row = {
            "Ticker": sym,
            "Entry": en_d.strftime("%Y-%m-%d"),
            "Exit": ex_d.strftime("%Y-%m-%d"),
            "TotalRet": round(tot_ret * 100, 1),
            "Reason": tr["exit_reason"]
        }
        for i, r in enumerate(m_rets[:30]):
            m_row[f"M{i+1}"] = round(r * 100, 1)
        monthly_matrix_data.append(m_row)

    df_summary = pd.DataFrame(records).sort_values("진입일")
    df_matrix = pd.DataFrame(monthly_matrix_data)
    
    # -------------------------------------------------------------
    # 개월차별(Month 1 ~ Month 30) 평균 수익률 및 승률 통계 도출
    # -------------------------------------------------------------
    m_cols = [f"M{i+1}" for i in range(30) if f"M{i+1}" in df_matrix.columns]
    m_stats = []
    for col in m_cols:
        vals = df_matrix[col].dropna()
        if len(vals) > 0:
            avg_ret = vals.mean()
            med_ret = vals.median()
            win_rate = (vals > 0).mean() * 100
            m_stats.append({
                "Month": col,
                "표본수": len(vals),
                "평균수익률(%)": round(avg_ret, 2),
                "중앙값(%)": round(med_ret, 2),
                "월승률(%)": round(win_rate, 1)
            })
    df_m_stats = pd.DataFrame(m_stats)

    def df_to_md(df):
        headers = [str(c) for c in df.columns]
        lines = []
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---:"] * len(headers)) + " |")
        for _, row in df.iterrows():
            r_str = [str(val) if val is not None and not pd.isna(val) else "" for val in row]
            lines.append("| " + " | ".join(r_str) + " |")
        return "\n".join(lines)

    # 마크다운 리포트 파일로 출력 저장
    md_out_path = "docs/research/20261018_qqq_replication/tenure_monthly_report.md"
    with open(md_out_path, "w", encoding="utf-8") as f:
        f.write("# EventOnly_2.5Y 편입 종목별 진입~퇴출 월별 수익률 전수 분석 리포트\n\n")
        f.write("## 1. 전체 편입 종목 성과 일람표\n\n")
        f.write(df_to_md(df_summary))
        f.write("\n\n---\n\n")
        f.write("## 2. 입성 후 개월차별(Month 1 ~ 30) 평균 수익률 및 승률 패턴\n\n")
        f.write(df_to_md(df_m_stats))
        f.write("\n\n---\n\n")
        f.write("## 3. 종목별 월별 수익률 매트릭스 (상세)\n\n")
        f.write(df_to_md(df_matrix))
        f.write("\n")
        
    print(f"\n✅ 리포트 생성 완료: {md_out_path}")
    
    # 콘솔 요약 출력
    print("\n" + "=" * 95)
    print("🏆 [EventOnly_2.5Y 전수 편입 종목 성과 랭킹 Top 10]")
    print("=" * 95)
    top10 = df_summary.sort_values("총수익률(%)", ascending=False).head(10)
    print(top10[["Ticker", "진입일", "퇴출일", "보유일수", "총수익률(%)", "최고상승률(%)", "퇴출사유"]].to_string(index=False))
    
    print("\n" + "=" * 95)
    print("📉 [EventOnly_2.5Y 부진 종목 Bottom 5]")
    print("=" * 95)
    bottom5 = df_summary.sort_values("총수익률(%)", ascending=True).head(5)
    print(bottom5[["Ticker", "진입일", "퇴출일", "보유일수", "총수익률(%)", "최대하락률(%)", "퇴출사유"]].to_string(index=False))

    print("\n" + "=" * 95)
    print("📈 [입성 후 구간별 월평균 수익률 패턴]")
    print("=" * 95)
    m1_6 = df_m_stats.iloc[0:6]["평균수익률(%)"].mean()
    m7_12 = df_m_stats.iloc[6:12]["평균수익률(%)"].mean()
    m13_18 = df_m_stats.iloc[12:18]["평균수익률(%)"].mean()
    m19_24 = df_m_stats.iloc[18:24]["평균수익률(%)"].mean()
    m25_30 = df_m_stats.iloc[24:30]["평균수익률(%)"].mean()
    print(f"- 1~6개월 차 (초기 패시브 유입기): 월평균 {m1_6:+.2f}%")
    print(f"- 7~12개월 차 (본격 복리 성장기) : 월평균 {m7_12:+.2f}%")
    print(f"- 13~18개월 차 (주도주 가속기)   : 월평균 {m13_18:+.2f}%")
    print(f"- 19~24개월 차 (성숙 정점기)     : 월평균 {m19_24:+.2f}%")
    print(f"- 25~30개월 차 (탄력 둔화/퇴출기): 월평균 {m25_30:+.2f}%")
    print("=" * 95)

if __name__ == "__main__":
    run_tenure_analysis()
