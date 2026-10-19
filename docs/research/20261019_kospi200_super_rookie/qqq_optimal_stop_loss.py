import os
import sys
import pandas as pd
import numpy as np

def main():
    root = "/home/kuzimo/projects/CTradeSystem"
    fp = f"{root}/DataVault/raw/yahoo/candle_1d/QQQ_1d.csv"
    df = pd.read_csv(fp)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")

    # 1999-03-10 ~ 2024-12-31 (26년 전수)
    cal = df.index[(df.index >= "1999-03-10") & (df.index <= "2024-12-31")]
    px = df["close"].loc[cal].ffill()
    sma200 = px.rolling(200).mean()

    INIT_CAP = 100_000_000
    daily_cash = 0.025 / 252.0
    cost_rate = 0.0005

    # Benchmark QQQ Buy & Hold
    bh_final = (px.iloc[-1] / px.iloc[0]) * INIT_CAP * (1.0 - cost_rate)
    tax_bh = max(0.0, bh_final - INIT_CAP - 2_500_000.0) * 0.22
    bh_post = bh_final - tax_bh
    years = (cal[-1] - cal[0]).days / 365.25
    cagr_bh = (bh_post / INIT_CAP) ** (1.0 / years) - 1.0
    daily_rets_bh = px.pct_change().dropna()
    vol_bh = daily_rets_bh.std() * np.sqrt(252)
    sharpe_bh = (cagr_bh - 0.02) / vol_bh
    mdd_bh = ((px / px.cummax()) - 1.0).min()

    print("=" * 110)
    print(f"📊 [기준선] QQQ Buy & Hold (26개년): 세후 최종 {bh_post:,.0f}원 | CAGR {cagr_bh*100:.2f}% | MDD {mdd_bh*100:.2f}% | Sharpe {sharpe_bh:.2f} | 세금 {tax_bh:,.0f}원")
    print("=" * 110)
    print(f"{'손절폭(%)':<10} | {'세후최종자산(원)':<18} | {'CAGR(%)':<10} | {'MDD(%)':<10} | {'Sharpe':<8} | {'손절횟수':<8} | {'총납부세금(원)':<15}")
    print("-" * 110)

    grid = [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.14, 0.15, 0.16, 0.18, 0.20, 0.22, 0.25, 0.30]
    records = []

    for sl_pct in grid:
        eq = pd.Series(index=cal, dtype=float)
        eq.iloc[0] = float(INIT_CAP) * (1.0 - cost_rate)
        
        in_pos = True
        peak_px = px.iloc[0]
        entry_px = px.iloc[0]
        cost_basis = eq.iloc[0]
        
        yearly_gain = 0.0
        total_tax = 0.0
        stop_cnt = 0
        
        for i in range(1, len(cal)):
            dt = cal[i]
            prev_dt = cal[i-1]
            
            # 연말 세금 정산
            if dt.year != prev_dt.year:
                taxable = max(0.0, yearly_gain - 2_500_000.0)
                t_due = taxable * 0.22
                if t_due > 0:
                    eq.iloc[i-1] = max(1.0, eq.iloc[i-1] - t_due)
                    total_tax += t_due
                yearly_gain = 0.0
                
            curr_px = px.iloc[i]
            prev_px = px.iloc[i-1]
            eq_prev = eq.iloc[i-1]
            
            if in_pos:
                peak_px = max(peak_px, curr_px)
                dd_peak = (curr_px / peak_px) - 1.0
                
                if dd_peak <= -sl_pct:
                    ret_step = (curr_px / prev_px) - 1.0
                    cur_val = eq_prev * (1.0 + ret_step) * (1.0 - cost_rate)
                    pnl = cur_val - cost_basis
                    yearly_gain += pnl
                    eq.iloc[i] = cur_val
                    in_pos = False
                    stop_cnt += 1
                else:
                    ret_step = (curr_px / prev_px) - 1.0
                    eq.iloc[i] = eq_prev * (1.0 + ret_step)
            else:
                eq.iloc[i] = eq_prev * (1.0 + daily_cash)
                # 재진입: 주가가 200일선 상회 + 단기 5일 상승
                if i >= 200 and curr_px > sma200.iloc[i] and px.iloc[i] > px.iloc[i-5]:
                    in_pos = True
                    entry_px = curr_px
                    peak_px = curr_px
                    eq.iloc[i] = eq.iloc[i] * (1.0 - cost_rate)
                    cost_basis = eq.iloc[i]
                    
        taxable = max(0.0, yearly_gain - 2_500_000.0)
        t_due = taxable * 0.22
        if t_due > 0:
            eq.iloc[-1] = max(1.0, eq.iloc[-1] - t_due)
            total_tax += t_due
            
        final_post = eq.iloc[-1]
        cagr = (final_post / INIT_CAP) ** (1.0 / years) - 1.0
        daily_rets = eq.pct_change().dropna()
        ann_vol = daily_rets.std() * np.sqrt(252)
        sharpe = (cagr - 0.02) / ann_vol if ann_vol > 0 else 0.0
        cum_max = eq.cummax()
        mdd = ((eq - cum_max) / cum_max).min()
        
        records.append({
            "sl_pct": sl_pct,
            "final_post": final_post,
            "cagr": cagr,
            "mdd": mdd,
            "sharpe": sharpe,
            "stop_cnt": stop_cnt,
            "tax": total_tax
        })
        print(f"-{sl_pct*100:>4.1f}%     | {final_post:>15,.0f}원 | {cagr*100:>8.2f}% | {mdd*100:>8.2f}% | {sharpe:>6.2f} | {stop_cnt:>6}회 | {total_tax:>13,.0f}원")

    # 최근 15년(2010~2024년 현대 강세장)만 따로 테스트
    print("\n" + "=" * 110)
    print("📈 [최근 15년 현대 강세장 (2010~2024년)] 손절폭별 성과")
    print("=" * 110)
    cal_15 = df.index[(df.index >= "2010-01-01") & (df.index <= "2024-12-31")]
    px_15 = df["close"].loc[cal_15].ffill()
    sma200_15 = px_15.rolling(200).mean()
    years_15 = (cal_15[-1] - cal_15[0]).days / 365.25

    bh_final_15 = (px_15.iloc[-1] / px_15.iloc[0]) * INIT_CAP * (1.0 - cost_rate)
    tax_15 = max(0.0, bh_final_15 - INIT_CAP - 2_500_000.0) * 0.22
    bh_post_15 = bh_final_15 - tax_15
    cagr_15 = (bh_post_15 / INIT_CAP) ** (1.0 / years_15) - 1.0
    mdd_15 = ((px_15 / px_15.cummax()) - 1.0).min()
    print(f"QQQ Buy & Hold (2010~2024): 세후 최종 {bh_post_15:,.0f}원 | CAGR {cagr_15*100:.2f}% | MDD {mdd_15*100:.2f}%")
    print("-" * 110)

    for sl_pct in [0.08, 0.10, 0.12, 0.14, 0.15, 0.16, 0.18, 0.20]:
        eq = pd.Series(index=cal_15, dtype=float)
        eq.iloc[0] = float(INIT_CAP) * (1.0 - cost_rate)
        in_pos = True
        peak_px = px_15.iloc[0]
        cost_basis = eq.iloc[0]
        yearly_gain = 0.0
        total_tax = 0.0
        stop_cnt = 0
        
        for i in range(1, len(cal_15)):
            dt = cal_15[i]
            prev_dt = cal_15[i-1]
            if dt.year != prev_dt.year:
                taxable = max(0.0, yearly_gain - 2_500_000.0)
                t_due = taxable * 0.22
                if t_due > 0:
                    eq.iloc[i-1] = max(1.0, eq.iloc[i-1] - t_due)
                    total_tax += t_due
                yearly_gain = 0.0
                
            curr_px = px_15.iloc[i]
            prev_px = px_15.iloc[i-1]
            eq_prev = eq.iloc[i-1]
            
            if in_pos:
                peak_px = max(peak_px, curr_px)
                if (curr_px / peak_px - 1.0) <= -sl_pct:
                    cur_val = eq_prev * (curr_px / prev_px) * (1.0 - cost_rate)
                    yearly_gain += (cur_val - cost_basis)
                    eq.iloc[i] = cur_val
                    in_pos = False
                    stop_cnt += 1
                else:
                    eq.iloc[i] = eq_prev * (curr_px / prev_px)
            else:
                eq.iloc[i] = eq_prev * (1.0 + daily_cash)
                if i >= 200 and curr_px > sma200_15.iloc[i] and px_15.iloc[i] > px_15.iloc[i-5]:
                    in_pos = True
                    peak_px = curr_px
                    eq.iloc[i] = eq.iloc[i] * (1.0 - cost_rate)
                    cost_basis = eq.iloc[i]
                    
        taxable = max(0.0, yearly_gain - 2_500_000.0)
        t_due = taxable * 0.22
        if t_due > 0:
            eq.iloc[-1] = max(1.0, eq.iloc[-1] - t_due)
            total_tax += t_due
            
        final_post = eq.iloc[-1]
        cagr = (final_post / INIT_CAP) ** (1.0 / years_15) - 1.0
        mdd = ((eq - eq.cummax()) / eq.cummax()).min()
        daily_rets = eq.pct_change().dropna()
        ann_vol = daily_rets.std() * np.sqrt(252)
        sharpe = (cagr - 0.02) / ann_vol if ann_vol > 0 else 0.0
        print(f"-{sl_pct*100:>4.1f}%     | {final_post:>15,.0f}원 | {cagr*100:>8.2f}% | {mdd*100:>8.2f}% | {sharpe:>6.2f} | {stop_cnt:>6}회 | {total_tax:>13,.0f}원")

if __name__ == "__main__":
    main()
