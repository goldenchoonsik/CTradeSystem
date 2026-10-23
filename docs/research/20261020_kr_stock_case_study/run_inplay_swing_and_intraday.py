"""
국내 개별종목 조건부 In-Play (거래대금 폭발 + 스마트머니 수급 + 눌림목 반격 + 1분봉 재점화 타점) 실증 백테스트
실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/run_inplay_swing_and_intraday.py
"""
import os
import sys
import json
import time
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def summarize_trades(trades_df: pd.DataFrame, label: str) -> dict:
    if trades_df is None or len(trades_df) == 0:
        return {
            "label": label,
            "trades": 0,
            "win_rate_pct": 0.0,
            "avg_net_ret_pct": 0.0,
            "median_net_ret_pct": 0.0,
            "profit_factor": 0.0,
            "t_stat": 0.0,
        }
    rets = trades_df["net_ret"].values.astype(float)
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    win_rate = float(len(wins) / len(rets) * 100.0)
    avg_ret = float(np.mean(rets))
    med_ret = float(np.median(rets))
    pf = float(np.sum(wins) / max(1e-9, abs(np.sum(losses))))
    std = float(np.std(rets, ddof=1)) if len(rets) > 1 else 1.0
    t_stat = float(avg_ret / (std / np.sqrt(len(rets)))) if std > 1e-9 else 0.0

    # In-sample (~2025) vs Out-of-sample (2026~)
    dates = pd.to_datetime(trades_df["entry_date"])
    dev_rets = rets[dates < pd.Timestamp("2026-01-01")]
    oos_rets = rets[dates >= pd.Timestamp("2026-01-01")]

    dev_avg = float(np.mean(dev_rets)) if len(dev_rets) > 0 else 0.0
    oos_avg = float(np.mean(oos_rets)) if len(oos_rets) > 0 else 0.0
    oos_wr = float(np.mean(oos_rets > 0) * 100.0) if len(oos_rets) > 0 else 0.0
    oos_wins = oos_rets[oos_rets > 0]
    oos_losses = oos_rets[oos_rets <= 0]
    oos_pf = float(np.sum(oos_wins) / max(1e-9, abs(np.sum(oos_losses)))) if len(oos_rets) > 0 else 0.0

    res = {
        "label": label,
        "trades": int(len(rets)),
        "win_rate_pct": round(win_rate, 2),
        "avg_net_ret_pct": round(avg_ret, 3),
        "median_net_ret_pct": round(med_ret, 3),
        "profit_factor": round(pf, 2),
        "t_stat": round(t_stat, 2),
        "dev_trades": int(len(dev_rets)),
        "dev_avg_net_pct": round(dev_avg, 3),
        "oos_2026_trades": int(len(oos_rets)),
        "oos_2026_win_rate_pct": round(oos_wr, 2),
        "oos_2026_avg_net_pct": round(oos_avg, 3),
        "oos_2026_pf": round(oos_pf, 2),
    }
    print(
        f"  • {label:<46} | 총 {res['trades']:4d}회 | 승률 {res['win_rate_pct']:5.1f}% | "
        f"평균 {res['avg_net_ret_pct']:+6.2f}% | PF {res['profit_factor']:4.2f} (t={res['t_stat']:+5.2f}) | "
        f"2026 OOS: {res['oos_2026_trades']:3d}회, 승률 {res['oos_2026_win_rate_pct']:5.1f}%, 평균 {res['oos_2026_avg_net_pct']:+6.2f}%, PF {res['oos_2026_pf']:4.2f}"
    )
    return res


def test_part_a_leader_stocks():
    """
    Part A: 대표 대형 주도주 (000660 SK하이닉스, 005930 삼성전자) 일봉 스윙 맥점 반격 vs 과매도 역발상
    왕복 마찰비용 0.25% (거래세 0.18% + 수수료 0.03% + 슬리피지 0.04%) 차감 후 성과
    """
    print("=" * 100)
    print("[PART A] 대표 대형 주도주(000660 SK하이닉스, 005930 삼성전자) 일봉 맥점 스윙 검증 (왕복비용 0.25% 차감)")
    print("=" * 100)

    sys.path.insert(0, os.path.join(ROOT_DIR, "docs/research/20261019_smart_money_counter_pivot"))
    from backtest_smart_money_counter_pivot import run_enhanced_pullback, run_oversold_reversal

    part_a_results = []
    for sym, name in [("000660", "SK하이닉스"), ("005930", "삼성전자")]:
        p = os.path.join(ROOT_DIR, "DataVault/normalized/candles_1d", f"{sym}_1d.parquet")
        df = pd.read_parquet(p)
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        t_pull = run_enhanced_pullback(df, name, fee_pct=0.25)
        part_a_results.append(summarize_trades(t_pull, f"{name}({sym}) - 맥점 반격 v2 (ATR 눌림목)"))

        t_os = run_oversold_reversal(df, name, fee_pct=0.25)
        part_a_results.append(summarize_trades(t_os, f"{name}({sym}) - 과매도 양봉 반격 (RSI<=32)"))

    return part_a_results


def check_1m_intraday_trigger(code: str, date_str: str) -> tuple:
    """
    1분봉 데이터(DataVault/staging/vol_spike_universe/raw/creon/candle_1m/<code>/<date>.csv)를 열어
    오전장(09:10 ~ 11:00) 동안:
    1) 당일 VWAP 위에서 양봉 거래대금이 직전 10분 평균의 2배 이상 재점화되고
    2) 누적 체결델타(cvd)가 0보다 크며 상승 중일 때
    실제 진입가(그 분봉 종가)를 반환. 조건 미충족 시 (False, None) 반환.
    """
    csv_path = os.path.join(
        ROOT_DIR,
        "DataVault/staging/vol_spike_universe/raw/creon/candle_1m",
        code,
        f"{date_str}.csv",
    )
    if not os.path.exists(csv_path):
        return False, None
    try:
        df1m = pd.read_csv(csv_path)
        if len(df1m) < 25:
            return False, None
        # Columns: timestamp, upbit_open, upbit_high, upbit_low, upbit_close, upbit_volume, upbit_trade_price, cvd
        c = df1m["upbit_close"].values.astype(float)
        o = df1m["upbit_open"].values.astype(float)
        v = df1m["upbit_volume"].values.astype(float)
        amt = df1m["upbit_trade_price"].values.astype(float)
        cvd = df1m["cvd"].values.astype(float) if "cvd" in df1m.columns else np.ones(len(df1m))

        cum_vol = np.cumsum(v)
        cum_amt = np.cumsum(c * v)
        vwap = np.where(cum_vol > 0, cum_amt / cum_vol, c)

        # Check between bar index 10 (09:11) and min(120, len-1) (11:00)
        end_idx = min(120, len(df1m) - 1)
        for idx in range(10, end_idx):
            avg_amt_10 = np.mean(amt[idx - 10 : idx])
            if (
                c[idx] > vwap[idx]
                and c[idx] > o[idx]
                and c[idx] >= c[0] * 0.99  # 시가 대비 무너지지 않음
                and c[idx] <= c[0] * 1.05  # 이미 과도하게 날아간 추격 제외
                and avg_amt_10 > 0
                and amt[idx] >= avg_amt_10 * 2.0
                and cvd[idx] > 0
                and cvd[idx] > cvd[idx - 3]
            ):
                return True, float(c[idx])
        return False, None
    except Exception:
        return False, None


def test_part_b_universe_inplay():
    """
    Part B: 한국 전 종목(2024-09 ~ 2026-10) 일봉 + 외국인/기관/프로그램 수급 + 1분봉 결합 검증
    왕복 마찰비용 0.28% (거래세 0.18% + 수수료 0.03% + 슬리피지 0.07%) 차감
    """
    print("\n" + "=" * 100)
    print("[PART B] 한국 전 종목(KOSPI/KOSDAQ) 조건부 In-Play & 스마트머니 맥점 케이스 스터디 (왕복비용 0.28% 차감)")
    print("=" * 100)

    p_prices = os.path.join(ROOT_DIR, "DataVault/staging/kr_equity/derived/prices_1d.parquet")
    p_flows = os.path.join(ROOT_DIR, "DataVault/staging/vol_spike_universe/derived/flows_panel.parquet")

    df_p = pd.read_parquet(p_prices)
    df_p["date"] = pd.to_datetime(df_p["date"])
    df_p = df_p[(df_p["date"] >= pd.Timestamp("2024-08-01")) & (df_p["date"] <= pd.Timestamp("2026-10-08"))].copy()
    df_p = df_p.dropna(subset=["open", "high", "low", "close", "volume", "value"])

    df_f = pd.read_parquet(p_flows)
    df_f["date"] = pd.to_datetime(df_f["date"])
    df_f = df_f[(df_f["date"] >= pd.Timestamp("2024-08-01")) & (df_f["date"] <= pd.Timestamp("2026-10-08"))].copy()

    df = pd.merge(df_p, df_f, on=["code", "date"], how="left")
    df[["frgn", "orgn", "prsn", "prog"]] = df[["frgn", "orgn", "prsn", "prog"]].fillna(0.0)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    # Filter ordinary liquid stocks (exclude SPAC/preferred/penny stocks)
    # In KRX, ordinary stocks end with '0'
    df = df[df["code"].str.endswith("0")].copy()

    # Compute rolling indicators per stock
    g = df.groupby("code", sort=False)
    df["prev_close"] = g["close"].shift(1)
    df["ret_1d"] = (df["close"] / df["prev_close"] - 1.0) * 100.0
    df["vol_ma20"] = g["volume"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["val_ma20"] = g["value"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["ma60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).mean())

    # Smart money net buying (frgn + orgn) in KRW -> note: in flows_panel, let's check unit of frgn/orgn/value
    # Wait: let's normalize smart money flow by stock's own 20d rolling std or positive indicator
    df["smart_net"] = df["frgn"] + df["orgn"]
    df["smart_3d"] = g["smart_net"].transform(lambda s: s.rolling(3, min_periods=2).sum())

    # Filter to valid dates >= 2024-09-15
    df = df[df["date"] >= pd.Timestamp("2024-09-15")].reset_index(drop=True)

    fee_pct = 0.28  # 왕복 0.28% (거래세 0.18% + 수수료 0.03% + 슬리피지 0.07%)

    trades_u1_blind = []
    trades_u2_chart_pb = []
    trades_u3_smart_pb = []
    trades_u4_1m_sniper = []

    # Process per stock for clean event tracking
    for code, sdf in df.groupby("code", sort=False):
        if len(sdf) < 40:
            continue
        # Only consider stocks that have reached at least 150억 daily trading value at least once
        if sdf["value"].max() < 1.5e10:
            continue

        sdf = sdf.reset_index(drop=True)
        n = len(sdf)
        dates = sdf["date"].values
        opens = sdf["open"].values.astype(float)
        highs = sdf["high"].values.astype(float)
        lows = sdf["low"].values.astype(float)
        closes = sdf["close"].values.astype(float)
        cu_unadj = sdf["close_unadj"].values.astype(float)
        vols = sdf["volume"].values.astype(float)
        vals = sdf["value"].values.astype(float)
        vol_ma20 = sdf["vol_ma20"].values.astype(float)
        ma20 = sdf["ma20"].values.astype(float)
        ma60 = sdf["ma60"].values.astype(float)
        ret_1d = sdf["ret_1d"].values.astype(float)
        smart_net = sdf["smart_net"].values.astype(float)
        prog_net = sdf["prog"].values.astype(float)
        prsn_net = sdf["prsn"].values.astype(float)

        # Helper to simulate trade from entry_i at entry_px with TP/SL/max_hold
        def sim_exit(entry_i, entry_px, tp_pct=7.0, sl_pct=3.5, max_hold=5, same_day_check=False):
            tp_px = entry_px * (1.0 + tp_pct / 100.0)
            sl_px = entry_px * (1.0 - sl_pct / 100.0)
            start_j = entry_i if same_day_check else entry_i + 1
            for j in range(start_j, min(n, entry_i + max_hold + 1)):
                if lows[j] <= sl_px:
                    return (sl_px / entry_px - 1.0) * 100.0 - fee_pct, str(dates[j])[:10], "SL"
                if highs[j] >= tp_px:
                    return (tp_px / entry_px - 1.0) * 100.0 - fee_pct, str(dates[j])[:10], "TP"
            last_j = min(n - 1, entry_i + max_hold)
            return (closes[last_j] / entry_px - 1.0) * 100.0 - fee_pct, str(dates[last_j])[:10], "TIME"

        # 1) U1: 단순 거래량 폭발 추격 (거래대금 200억+ & 거래량 3배+ & 당일 +6%~+20% 급등 종가 매수)
        next_free_u1 = 0
        for i in range(20, n - 5):
            if i < next_free_u1:
                continue
            if cu_unadj[i] < 2000 or np.isnan(vol_ma20[i]) or vol_ma20[i] <= 0:
                continue
            if vals[i] >= 2.0e10 and vols[i] >= vol_ma20[i] * 3.0 and 6.0 <= ret_1d[i] <= 20.0:
                net_r, ex_d, rsn = sim_exit(i, closes[i], tp_pct=7.0, sl_pct=3.5, max_hold=5)
                trades_u1_blind.append({"code": code, "entry_date": str(dates[i])[:10], "net_ret": net_r, "reason": rsn})
                next_free_u1 = i + 5

        # 2) U2: 수급 구분 없는 단순 기술적 눌림목 (거래대금 200억+ & 거래량 2.5배+ & +4% 기준봉 후 2~6일 내 거래량 45% 이하 감소 눌림목)
        ref_u2 = None
        next_free_u2 = 0
        for i in range(20, n - 6):
            if i < next_free_u2:
                continue
            if cu_unadj[i] < 2000 or np.isnan(vol_ma20[i]) or vol_ma20[i] <= 0 or np.isnan(ma20[i]):
                continue
            if closes[i] > ma20[i] and vals[i] >= 2.0e10 and vols[i] >= vol_ma20[i] * 2.5 and 4.0 <= ret_1d[i] <= 22.0 and closes[i] > opens[i]:
                ref_u2 = i
                continue
            if ref_u2 is not None:
                ds = i - ref_u2
                if ds > 6 or lows[i] < opens[ref_u2] * 0.98:
                    ref_u2 = None
                elif ds >= 2 and vols[i] <= vols[ref_u2] * 0.45 and closes[i] >= ma20[i] * 0.98:
                    net_r, ex_d, rsn = sim_exit(i, closes[i], tp_pct=7.0, sl_pct=3.5, max_hold=6)
                    trades_u2_chart_pb.append({"code": code, "entry_date": str(dates[i])[:10], "net_ret": net_r, "reason": rsn})
                    ref_u2 = None
                    next_free_u2 = i + 6

        # 3) U3 & U4: 스마트머니(외국인+기관 순매수 & 프로그램 유입) 정배열 기준봉 + 거래량 급감 눌림목 후 수급 재점화 반격
        ref_u3 = None
        next_free_u3 = 0
        for i in range(20, n - 7):
            if i < next_free_u3:
                continue
            if cu_unadj[i] < 3000 or np.isnan(vol_ma20[i]) or vol_ma20[i] <= 0 or np.isnan(ma20[i]) or np.isnan(ma60[i]):
                continue
            # Anchor bar:
            # - 정배열 (종가 > 20일선 > 60일선)
            # - 거래대금 300억 이상 & 거래량 2.5배 이상 & +4%~+18% 양봉 (과열 상한가 제외)
            # - 당일 외국인+기관 동반 순매수 (smart_net > 0 & prsn_net < 0) 및 프로그램 순매수(prog_net > 0)
            if (
                closes[i] > ma20[i] > ma60[i]
                and vals[i] >= 3.0e10
                and vols[i] >= vol_ma20[i] * 2.5
                and 4.0 <= ret_1d[i] <= 18.0
                and closes[i] > opens[i]
                and smart_net[i] > 0
                and prsn_net[i] < 0
                and prog_net[i] > 0
            ):
                ref_u3 = i
                continue

            if ref_u3 is not None:
                ds = i - ref_u3
                if ds > 7 or lows[i] < opens[ref_u3] * 0.985:
                    ref_u3 = None
                elif (
                    ds >= 2
                    and vols[i] <= vols[ref_u3] * 0.50  # 거래량 절반 이하로 급감 (매물 소화)
                    and closes[i] >= ma20[i] * 0.985     # 20일선 및 기준봉 시가 지지
                    and closes[i] > opens[i]             # 눌림목 양봉 반격 확인
                    and smart_net[i] >= 0                # 반격일에도 스마트머니 이탈 없음
                ):
                    # U3: 일봉 종가 진입
                    net_r, ex_d, rsn = sim_exit(i, closes[i], tp_pct=7.0, sl_pct=3.5, max_hold=7)
                    trades_u3_smart_pb.append({"code": code, "entry_date": str(dates[i])[:10], "net_ret": net_r, "reason": rsn})

                    # U4: 동일 눌림목 후보에서 익일(또는 당일) 1분봉 장중 오전 VWAP+CVD 재점화 확인 진입
                    d_str = str(dates[i])[:10]
                    ok_1m, px_1m = check_1m_intraday_trigger(code, d_str)
                    if ok_1m and px_1m is not None and opens[i] > 0:
                        # Adjust 1m unadjusted price to adjusted scale if needed
                        adj_ratio = closes[i] / cu_unadj[i] if cu_unadj[i] > 0 else 1.0
                        entry_px_adj = px_1m * adj_ratio
                        net_r_1m, ex_d_1m, rsn_1m = sim_exit(i, entry_px_adj, tp_pct=7.0, sl_pct=3.5, max_hold=7, same_day_check=True)
                        trades_u4_1m_sniper.append({"code": code, "entry_date": d_str, "net_ret": net_r_1m, "reason": rsn_1m})

                    ref_u3 = None
                    next_free_u3 = i + 6

    part_b_results = [
        summarize_trades(pd.DataFrame(trades_u1_blind), "U1. 단순 거래량폭발 급등 추격 (수급무관)"),
        summarize_trades(pd.DataFrame(trades_u2_chart_pb), "U2. 단순 차트 기준봉 눌림목 (수급무관)"),
        summarize_trades(pd.DataFrame(trades_u3_smart_pb), "U3. 스마트머니(외인+기관+프로그램) 기준봉 눌림목 반격"),
        summarize_trades(pd.DataFrame(trades_u4_1m_sniper), "U4. 스마트머니 눌림목 + 1분봉 VWAP·CVD 재점화 타격"),
    ]
    return part_b_results


def main():
    r_a = test_part_a_leader_stocks()
    r_b = test_part_b_universe_inplay()

    out_path = os.path.join(OUT_DIR, "inplay_swing_and_intraday_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"part_a_leaders": r_a, "part_b_universe": r_b}, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
