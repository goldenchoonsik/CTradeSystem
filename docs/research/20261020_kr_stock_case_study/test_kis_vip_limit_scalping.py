"""
한투 우대수수료(왕복 0.0073%, 세금 0%) 기준 '과적합 없는' 가혹한 실전 호가/틱 체결 시뮬레이션:
- 진입: 상위 1분봉 유효 구간(장초반 ORB 돌파 / VWAP 눌림목 반등 / 단독 10초봉 폭증)에서
        실제 주문 지연(다음 틱 체결) 반영 후 매수 진입(px0).
- 청산: 매수 직후 +2틱/+3틱/+5틱(+10원/+15원/+25원) 위에 지정가 매도(Limit Sell) 대기.
  * [엄격한 체결 조건]: 지정가(target_px)에서 매수 주도 체결(d_cvd > 0)이 발생하거나 가격이 target_px 위로 뚫려야만 지정가 익절 체결 인정!
  * [역선택(Adverse Selection) 반영]: 제한시간(10초 / 30초 / 60초 / 5분) 내에 지정가가 체결되지 않거나 손절선(-3틱/-5틱)을 건드리면,
    가차 없이 현재가에서 -1틱(5원, 매수1호가 투매 슬리피지) 불리한 가격으로 시장가 타임컷/손절!

실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/test_kis_vip_limit_scalping.py
"""
import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_mtf_1m_to_subsec import load_all_valid_tick_days, OUT_DIR

KIS_VIP_ROUNDTRIP_FEE_PCT = 0.0073  # 한국투자증권 ETF 우대수수료 왕복 0.00728% (세금 0%)
TICK_SIZE = 5.0  # KODEX 200 / KODEX 레버리지 호가 단위 = 5원


def simulate_limit_exit_with_adverse_selection(
    sub_ticks: pd.DataFrame,
    entry_idx: int,
    tp_ticks: int,
    sl_ticks: int,
    timeout_sec: int,
) -> dict:
    """
    entry_idx 시점 신호 발생 -> 주문 지연 반영하여 entry_idx + 1 (다음 틱)에 매수 진입.
    이후 지정가 익절(+tp_ticks) vs 손절(-sl_ticks) vs 타임컷(timeout_sec) 시뮬레이션.
    타임컷/손절은 시장가 투매이므로 현재 체결가에서 -1틱(5원) 슬리피지 추가 부과!
    """
    if entry_idx + 2 >= len(sub_ticks):
        return {}
    entry_row = sub_ticks.iloc[entry_idx + 1]  # 1틱 뒤 체결 (Latency 반영)
    px0 = float(entry_row["upbit_close"])
    t0 = entry_row["ts_exact"]

    target_px = px0 + tp_ticks * TICK_SIZE
    stop_px = px0 - sl_ticks * TICK_SIZE
    end_time = t0 + pd.Timedelta(seconds=timeout_sec)

    future = sub_ticks.iloc[entry_idx + 2 :]
    exit_px = None
    exit_type = "TIMEOUT"

    for _, row in future.iterrows():
        px = float(row["upbit_close"])
        ts = row["ts_exact"]
        d_cvd = float(row["d_cvd"])

        # 1) 손절 먼저 체크 (보수적 원칙: 같은 틱이면 손절 우선)
        if px <= stop_px:
            exit_px = px - TICK_SIZE  # 시장가 손절 투매 시 -1틱 슬리피지
            exit_type = "SL"
            break

        # 2) 지정가 익절 체결 조건: 가격이 target_px 돌파, 또는 target_px에서 매수체결(d_cvd > 0) 발생 시
        if px > target_px or (px >= target_px and d_cvd > 0):
            exit_px = target_px  # 지정가이므로 정확히 target_px에 체결 (슬리피지 0)
            exit_type = "TP_LIMIT"
            break

        # 3) 타임아웃 도달 시 시장가 청산 (-1틱 매수호가 투매)
        if ts >= end_time:
            exit_px = px - TICK_SIZE
            exit_type = "TIMEOUT"
            break

    if exit_px is None:
        last_px = float(future["upbit_close"].iloc[-1])
        exit_px = last_px - TICK_SIZE
        exit_type = "EOD_TIMEOUT"

    gross_pct = (exit_px / px0 - 1.0) * 100.0
    net_pct = gross_pct - KIS_VIP_ROUNDTRIP_FEE_PCT
    return {
        "px0": px0,
        "exit_px": exit_px,
        "exit_type": exit_type,
        "gross_pct": gross_pct,
        "net_pct": net_pct,
    }


def run_symbol_audit(code: str, name: str):
    days = load_all_valid_tick_days(code)
    print("\n" + "=" * 125)
    print(f"▶ {name} ({code}) | 한투 우대수수료 왕복 {KIS_VIP_ROUNDTRIP_FEE_PCT:.4f}% + 미체결/손절 시 시장가 -1틱(5원) 페널티 강제 적용")
    print("=" * 125)

    # Collect entry triggers across 3 setups:
    # 1) ORB_2S: 장초반 20분 대량거래+CVD>0 후 전고점 돌파 첫 틱
    # 2) VWAP_5S: 장중 상승추세 중 1분봉 VWAP 눌림목 구간 내 5초봉 양봉+CVD>0 반등 틱
    # 3) BURST_10S: 10초봉 거래량 5배+ 폭증 & +0.04% 양봉 틱
    configs = [
        (2, 4, 10, "+2틱(+10원) 지정가익절 / -4틱 손절 / 10초 타임컷"),
        (3, 5, 10, "+3틱(+15원) 지정가익절 / -5틱 손절 / 10초 타임컷"),
        (3, 5, 30, "+3틱(+15원) 지정가익절 / -5틱 손절 / 30초 타임컷"),
        (4, 6, 60, "+4틱(+20원) 지정가익절 / -6틱 손절 / 60초 타임컷"),
        (8, 8, 300, "+8틱(+40원) 지정가익절 / -8틱 손절 / 5분(300초) 타임컷"),
        (15, 10, 900, "+15틱(+75원) 지정가익절 / -10틱 손절 / 15분(900초) 타임컷"),
    ]

    summary_out = {"code": code, "name": name, "setups": {}}

    for setup_key, setup_title in [
        ("ORB", "[타점 1] 장초반 20분 수급폭발 후 전고점(ORB) 돌파 순간 진입"),
        ("VWAP", "[타점 2] 1분봉 상승추세 + VWAP 눌림목 구간 내 5초봉 반등 진입"),
    ]:
        print(f"\n  {setup_title}")
        summary_out["setups"][setup_key] = {}
        for tp_t, sl_t, to_s, cfg_lbl in configs:
            trades = []
            for d in days:
                m1 = d["m1"].copy()
                ticks = d["ticks"].reset_index(drop=True)
                m1["cum_vol"] = m1["volume"].cumsum()
                m1["vwap"] = (m1["close"] * m1["volume"]).cumsum() / np.maximum(1, m1["cum_vol"])
                m1["vol_ma15"] = m1["volume"].rolling(15, min_periods=5).mean()

                if setup_key == "ORB":
                    or_high = float(m1.iloc[:20]["high"].max())
                    if float(m1.iloc[:20]["d_cvd"].sum()) <= 0:
                        continue
                    for i in range(20, min(90, len(m1) - 20)):
                        if m1.iloc[i]["high"] > or_high and m1.iloc[i]["d_cvd"] > 0:
                            min_ts = m1.iloc[i]["min_ts"]
                            cand_idx = ticks.index[(ticks["min_ts"] == min_ts) & (ticks["upbit_close"] > or_high)]
                            if len(cand_idx) > 0:
                                tr = simulate_limit_exit_with_adverse_selection(ticks, int(cand_idx[0]), tp_t, sl_t, to_s)
                                if tr:
                                    trades.append(tr)
                                break
                elif setup_key == "VWAP":
                    day_open = float(m1["open"].iloc[0])
                    last_i = -99
                    for i in range(25, len(m1) - 35):
                        if i - last_i < 15:
                            continue
                        row_prev = m1.iloc[i - 1]
                        if (
                            row_prev["close"] >= day_open * 1.004
                            and row_prev["cvd_last"] > 0
                            and abs(row_prev["low"] / row_prev["vwap"] - 1.0) <= 0.0025
                        ):
                            min_ts = m1.iloc[i]["min_ts"]
                            sub_m = ticks[ticks["min_ts"] == min_ts]
                            for b_end in range(5, 61, 5):
                                w = sub_m[(sub_m["sec_offset"] >= b_end - 5) & (sub_m["sec_offset"] < b_end)]
                                if len(w) > 0 and w["d_cvd"].sum() > 0 and w["upbit_close"].iloc[-1] > w["upbit_close"].iloc[0]:
                                    trig_idx = int(w.index[-1])
                                    tr = simulate_limit_exit_with_adverse_selection(ticks, trig_idx, tp_t, sl_t, to_s)
                                    if tr:
                                        trades.append(tr)
                                    last_i = i
                                    break

            if not trades:
                continue
            df_t = pd.DataFrame(trades)
            n = len(df_t)
            tp_rate = float((df_t["exit_type"] == "TP_LIMIT").mean() * 100.0)
            sl_rate = float((df_t["exit_type"] == "SL").mean() * 100.0)
            to_rate = float((df_t["exit_type"].str.contains("TIMEOUT")).mean() * 100.0)
            gross_m = float(df_t["gross_pct"].mean())
            net_m = float(df_t["net_pct"].mean())
            wr_net = float((df_t["net_pct"] > 0).mean() * 100.0)
            cum_net = float(df_t["net_pct"].sum())
            print(
                f"    • {cfg_lbl:<46} (n={n:3d}) | 지정가체결 {tp_rate:5.1f}% / 손절 {sl_rate:4.1f}% / 타임컷 {to_rate:4.1f}% "
                f"| 평균 세전 {gross_m:+6.3f}% -> 세후 {net_m:+6.3f}% (승률 {wr_net:5.1f}%, 누적 {cum_net:+6.2f}%)"
            )
            summary_out["setups"][setup_key][cfg_lbl] = {
                "n": n,
                "tp_fill_rate_pct": round(tp_rate, 1),
                "sl_rate_pct": round(sl_rate, 1),
                "timeout_rate_pct": round(to_rate, 1),
                "avg_gross_pct": round(gross_m, 4),
                "avg_net_pct": round(net_m, 4),
                "win_rate_net_pct": round(wr_net, 1),
                "cum_net_pct": round(cum_net, 3),
            }
    return summary_out


def main():
    out = []
    for code, name in [
        ("A069500", "KODEX 200 (무세금 1배 대표 지수 ETF)"),
        ("A122630", "KODEX 레버리지 (무세금 2배 ETF)"),
    ]:
        out.append(run_symbol_audit(code, name))
    out_path = os.path.join(OUT_DIR, "kis_vip_limit_scalping_audit.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
