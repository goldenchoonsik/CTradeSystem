"""
[EXP-017-B] 시총 가속도 TOP 10 전략: 1999~2024년 26개 연도별 수익률 & MDD 심층 분석
====================================================================================
"""
import os
import sys
import pandas as pd
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

REPORTS_DIR = "reports"
fp = f"{REPORTS_DIR}/acceleration_top10_slot_test_results.csv"

def analyze_annual_performance():
    df = pd.read_csv(fp, parse_dates=["Date"]).set_index("Date")
    
    # 연도 목록
    years = sorted(list(set(df.index.year)))
    
    rows = []
    for y in years:
        sub = df[df.index.year == y]
        if len(sub) < 5:
            continue
            
        r_accel_full = (sub["Cap_Accel_FullInvest"].iloc[-1] / sub["Cap_Accel_FullInvest"].iloc[0]) - 1.0
        r_accel_slot = (sub["Cap_Accel_SlotEmpty"].iloc[-1] / sub["Cap_Accel_SlotEmpty"].iloc[0]) - 1.0
        r_qqq = (sub["QQQ"].iloc[-1] / sub["QQQ"].iloc[0]) - 1.0
        
        # 연간 MDD 계산
        def calc_mdd(series):
            cum = series.cummax()
            dd = (series - cum) / cum
            return dd.min()
            
        mdd_accel_full = calc_mdd(sub["Cap_Accel_FullInvest"])
        mdd_accel_slot = calc_mdd(sub["Cap_Accel_SlotEmpty"])
        mdd_qqq = calc_mdd(sub["QQQ"])
        
        rows.append({
            "Year": y,
            "Accel_Full_Ret": r_accel_full,
            "Accel_Full_MDD": mdd_accel_full,
            "Accel_Slot_Ret": r_accel_slot,
            "Accel_Slot_MDD": mdd_accel_slot,
            "QQQ_Ret": r_qqq,
            "QQQ_MDD": mdd_qqq
        })
        
    res_df = pd.DataFrame(rows)
    out_csv = f"{REPORTS_DIR}/acceleration_annual_breakdown.csv"
    res_df.to_csv(out_csv, index=False)
    
    print("=" * 115)
    print("📅 [시총 가속도 TOP 10 vs QQQ: 1999~2024년 26개 전 연도 성과 매트릭스]")
    print("=" * 115)
    header = f"{'연도':<6} | {'가속도 풀투자':>13} (MDD) | {'가속도 슬롯비우기':>15} (MDD) | {'QQQ ETF':>11} (MDD) | {'특이사항/국면':<18}"
    print(header)
    print("-" * 115)
    
    crisis_years = {
        2000: "닷컴버블 붕괴 초입",
        2001: "닷컴버블 대폭락",
        2002: "닷컴버블 바닥",
        2008: "글로벌 금융위기",
        2020: "코로나19 팬데믹",
        2022: "인플레·금리인상 쇼크"
    }
    
    for _, r in res_df.iterrows():
        y = int(r["Year"])
        note = crisis_years.get(y, "")
        if r["Accel_Full_Ret"] > 1.0:
            note = f"🔥 폭등 (+{r['Accel_Full_Ret']*100:.0f}%)" if not note else f"{note} / 폭등"
            
        full_str = f"{r['Accel_Full_Ret']*100:>+6.1f}% ({r['Accel_Full_MDD']*100:>5.1f}%)"
        slot_str = f"{r['Accel_Slot_Ret']*100:>+6.1f}% ({r['Accel_Slot_MDD']*100:>5.1f}%)"
        qqq_str = f"{r['QQQ_Ret']*100:>+6.1f}% ({r['QQQ_MDD']*100:>5.1f}%)"
        
        print(f"{y:<6} | {full_str:>20} | {slot_str:>23} | {qqq_str:>18} | {note:<18}")
        
    print("=" * 115)

if __name__ == "__main__":
    analyze_annual_performance()
