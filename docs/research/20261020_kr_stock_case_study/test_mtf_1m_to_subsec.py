"""
다중 타임프레임(MTF) 결합 검증:
[상위 1분봉 / 장초반 30분 구간]에서 '진짜 거래량 폭증 + 유의미한 맥점 구간(전고점 돌파 임박 / VWAP 눌림목 지지)'을 먼저 확정하고,
그 구간 안에서만 [하위 2초봉 / 5초봉 / 10초봉]으로 정밀 진입 타점을 잡았을 때 vs [1분봉 완성 후 진입] 비교!

실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/test_mtf_1m_to_subsec.py
"""
import os
import sys
import glob
import json
import numpy as np
import pandas as pd

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
TICK_ROOT = os.path.join(ROOT_DIR, "DataVault/raw/creon/tick")


def load_all_valid_tick_days(code: str):
    """
    2026-08-26 이후 정방향(FORWARD) 보증된 모든 거래일의 원시 틱을 읽어
    1분봉(1m)과 2초봉(2s), 5초봉(5s), 10초봉(10s)을 동시 구축한다.
    """
    folder = os.path.join(TICK_ROOT, code)
    if not os.path.exists(folder):
        return []
    csvs = [fp for fp in sorted(glob.glob(os.path.join(folder, "2026-*.csv"))) if os.path.basename(fp) >= "2026-08-26.csv"]
    days_data = []

    for fp in csvs:
        d_str = os.path.basename(fp).replace(".csv", "")
        try:
            df = pd.read_csv(fp)
        except Exception:
            continue
        if len(df) < 500:
            continue
        df["min_ts"] = pd.to_datetime(df["timestamp"])
        t_time = df["min_ts"].dt.time
        df = df[(t_time >= pd.to_datetime("09:00:00").time()) & (t_time <= pd.to_datetime("15:19:00").time())].copy()
        if len(df) < 500:
            continue
        df["d_cvd"] = df["cvd"].diff().fillna(0.0)

        # 1) Build 1-minute bars for macro/intraday context
        m1 = df.groupby("min_ts", as_index=False).agg(
            open=("upbit_close", "first"),
            high=("upbit_high", "max"),
            low=("upbit_low", "min"),
            close=("upbit_close", "last"),
            volume=("upbit_volume", "sum"),
            trade_price=("upbit_trade_price", "sum"),
            d_cvd=("d_cvd", "sum"),
            cvd_last=("cvd", "last"),
        ).sort_values("min_ts").reset_index(drop=True)

        # Assign synthetic sub-minute bucket inside each minute (30 buckets = 2s)
        grp = df.groupby("min_ts", sort=False)
        pos = grp.cumcount()
        cnt = grp["upbit_close"].transform("count")
        df["sec_offset"] = ((pos * 60) // np.maximum(1, cnt)).astype(int)
        df["ts_exact"] = df["min_ts"] + pd.to_timedelta(df["sec_offset"], unit="s")

        days_data.append({"date": d_str, "m1": m1, "ticks": df})
    return days_data


def run_mtf_comparison_for_symbol(code: str, name: str, cost_pct: float) -> dict:
    days_data = load_all_valid_tick_days(code)
    if not days_data:
        return {}

    # Compute rolling daily morning (first 30m) baseline volume across days
    morning_30m_vols = []
    for d in days_data:
        m1 = d["m1"]
        m_30 = m1[m1["min_ts"].dt.time <= pd.to_datetime("09:30:00").time()]
        morning_30m_vols.append(float(m_30["volume"].sum()))
    median_morning_vol = float(np.median(morning_30m_vols)) if morning_30m_vols else 1.0

    # We compare two classic MTF setups:
    # Setup A [장개시 후 15~30분 대량거래 추세 확정 -> 오전장(09:15~10:30) 전고점 돌파 구간(ORB) 진입]:
    #   - 1분봉이 장초반 고점(Opening Range High)을 돌파할 때:
    #     (1) 1m_close: 그 1분봉이 끝날 때(60초 뒤) 종가에 진입
    #     (2) 10s_entry: 돌파 순간 첫 10초봉(0~10초) 종가에 즉시 진입
    #     (3) 5s_entry: 돌파 순간 첫 5초봉(0~5초) 종가에 즉시 진입
    #     (4) 2s_entry: 돌파 순간 첫 2초봉(0~2초) 종가에 즉시 진입
    # Setup B [장개시 후 거래량 폭발 주도 흐름 -> 1분봉 VWAP 눌림목 지지 구간에서 초단위 반등 타점 진입]:
    #   - 장중 누적 거래량이 평소보다 터지고 당일 상승 추세(시가 대비 +0.5% 이상 & 누적 CVD > 0)인 상태에서,
    #   - 1분봉이 당일 VWAP 부근(±0.25%)까지 눌린 유의미한 지지 구간에 들어왔을 때:
    #     (1) 1m_close: 1분봉 양봉 반등이 완전히 끝난 종가에 진입
    #     (2) 10s_entry: VWAP 지지 구간에서 첫 10초봉 순매수(dCVD>0) 양봉 전환 시 진입
    #     (3) 5s_entry: VWAP 지지 구간에서 첫 5초봉 순매수(dCVD>0) 양봉 전환 시 진입
    #     (4) 2s_entry: VWAP 지지 구간에서 첫 2초봉 순매수(dCVD>0) 양봉 전환 시 진입

    records_orb = {k: [] for k in ["1m_close", "10s_entry", "5s_entry", "2s_entry"]}
    records_vwap = {k: [] for k in ["1m_close", "10s_entry", "5s_entry", "2s_entry"]}

    for d in days_data:
        d_str = d["date"]
        m1 = d["m1"].copy()
        ticks = d["ticks"]
        if len(m1) < 120:
            continue

        m1["cum_vol"] = m1["volume"].cumsum()
        m1["cum_pv"] = (m1["close"] * m1["volume"]).cumsum()
        m1["vwap"] = np.where(m1["cum_vol"] > 0, m1["cum_pv"] / m1["cum_vol"], m1["close"])
        m1["vol_ma15"] = m1["volume"].rolling(15, min_periods=5).mean()

        # Helper to get forward prices at +5m, +15m, +30m, +60m, and EOD from minute index i
        def eval_from_entry(entry_px: float, m_idx: int):
            c_arr = m1["close"].values.astype(float)
            n = len(c_arr)
            return {
                "date": d_str,
                "entry_px": entry_px,
                "r5m": (c_arr[min(n - 1, m_idx + 5)] / entry_px - 1.0) * 100.0,
                "r15m": (c_arr[min(n - 1, m_idx + 15)] / entry_px - 1.0) * 100.0,
                "r30m": (c_arr[min(n - 1, m_idx + 30)] / entry_px - 1.0) * 100.0,
                "r60m": (c_arr[min(n - 1, m_idx + 60)] / entry_px - 1.0) * 100.0,
                "reod": (c_arr[-1] / entry_px - 1.0) * 100.0,
            }

        # --- SETUP A: 장초반 15~30분 유효 거래량 터진 날 ORB 돌파 (100% 실시간 무-미래참조) ---
        # 09:00~09:20 (첫 20분간) 대량 거래량 + 양(+)의 수급(CVD>0)이 확인되면 당일 오전 고점(or_high)을 '유효 돌파 타점 구간'으로 설정
        m_first20 = m1.iloc[:20]
        or_high = float(m_first20["high"].max())
        or_vol = float(m_first20["volume"].sum())
        or_cvd = float(m_first20["d_cvd"].sum())

        if or_vol >= median_morning_vol * 0.50 and or_cvd > 0:
            # 각 진입 타이밍별로 당일 첫 돌파 진입 시점을 독립적으로 포착 (미래 1분봉 종가를 미리 훔쳐보지 않음!)
            done_modes = set()
            for i in range(20, min(90, len(m1) - 35)):
                if len(done_modes) == 4:
                    break
                row_m = m1.iloc[i]
                if row_m["high"] <= or_high:
                    continue
                min_ts = row_m["min_ts"]
                sub_t = ticks[ticks["min_ts"] == min_ts]
                if len(sub_t) < 10:
                    continue

                # 하위 초봉(2s, 5s, 10s) 돌파 확인: 해당 초봉 종가가 or_high를 돌파하고 그 초봉의 d_cvd > 0 일 때 즉시 진입
                for w_sec, m_key in [(2, "2s_entry"), (5, "5s_entry"), (10, "10s_entry")]:
                    if m_key in done_modes:
                        continue
                    for b_end in range(w_sec, 61, w_sec):
                        w = sub_t[(sub_t["sec_offset"] >= b_end - w_sec) & (sub_t["sec_offset"] < b_end)]
                        if len(w) > 0 and float(w["upbit_close"].iloc[-1]) > or_high and float(w["d_cvd"].sum()) > 0:
                            px = float(w["upbit_close"].iloc[-1])
                            records_orb[m_key].append(eval_from_entry(px, i))
                            done_modes.add(m_key)
                            break

                # 1분봉 종가 확인 진입(1m_close): 60초가 끝난 시점에 1분봉 종가가 or_high 위에서 안착(윗꼬리 페이크 필터링)했을 때 진입
                if "1m_close" not in done_modes:
                    if float(row_m["close"]) > or_high and float(row_m["d_cvd"]) > 0:
                        records_orb["1m_close"].append(eval_from_entry(float(row_m["close"]), i))
                        done_modes.add("1m_close")

        # --- SETUP B: 장중 1분봉 대량거래 상승추세 확정 + 직전 1분봉(i-1)이 VWAP 눌림목 타점 구간에 진입했을 때 ---
        # i-1분봉까지 보고 '유효한 1분봉 눌림목 타점 구간'이 확정된 상태에서, i분봉 안에서 2s/5s/10s 초단위로 잡기 vs i분봉 종가 확인 후 잡기
        last_vwap_i = -99
        for i in range(25, len(m1) - 35):
            if i - last_vwap_i < 15:
                continue
            row_prev = m1.iloc[i - 1]
            row_m = m1.iloc[i]
            day_open = float(m1["open"].iloc[0])
            # [상위 1분봉 타점 구간 조건 (i-1분봉 시점에 100% 확정된 정보만 사용)]:
            # 1) 장중 누적 거래대금/거래량 활발 & 시가 대비 +0.4% 이상 상승 추세 & 누적 CVD > 0
            # 2) 직전 1분봉(i-1)의 저가 또는 종가가 당일 VWAP 지지선 부근(±0.25%)까지 눌림 발생
            if (
                row_prev["close"] >= day_open * 1.004
                and row_prev["cvd_last"] > 0
                and abs(row_prev["low"] / row_prev["vwap"] - 1.0) <= 0.0025
                and row_prev["volume"] >= row_prev["vol_ma15"] * 0.8
            ):
                min_ts = row_m["min_ts"]
                sub_t = ticks[ticks["min_ts"] == min_ts]
                if len(sub_t) < 10:
                    continue

                # 이 유효 타점 구간(i분봉) 안에서 첫 2s / 5s / 10s 수급 반등(d_cvd > 0 & 양봉) 포착
                def get_rebound_subbar_px(window_sec):
                    for b_end in range(window_sec, 61, window_sec):
                        w = sub_t[(sub_t["sec_offset"] >= b_end - window_sec) & (sub_t["sec_offset"] < b_end)]
                        if len(w) > 0 and w["d_cvd"].sum() > 0 and w["upbit_close"].iloc[-1] > w["upbit_close"].iloc[0]:
                            return float(w["upbit_close"].iloc[-1])
                    return None

                px_2s = get_rebound_subbar_px(2)
                px_5s = get_rebound_subbar_px(5)
                px_10s = get_rebound_subbar_px(10)

                if px_2s is not None:
                    records_vwap["2s_entry"].append(eval_from_entry(px_2s, i))
                if px_5s is not None:
                    records_vwap["5s_entry"].append(eval_from_entry(px_5s, i))
                if px_10s is not None:
                    records_vwap["10s_entry"].append(eval_from_entry(px_10s, i))
                if float(row_m["close"]) > float(row_m["open"]) and float(row_m["d_cvd"]) > 0:
                    records_vwap["1m_close"].append(eval_from_entry(float(row_m["close"]), i))
                last_vwap_i = i

    print("\n" + "=" * 125)
    print(f"▶ {name} ({code}) | 유효거래일 {len(days_data)}일 | 왕복 마찰비용(세금+수수료+슬리피지) = {cost_pct:.2f}%")
    print("=" * 125)

    out_sym = {"code": code, "name": name, "cost_pct": cost_pct, "orb_setup": {}, "vwap_setup": {}}

    for setup_name, rec_dict, key_out in [
        ("[전략 A: 장개시 15~30분 거래량 폭발 후 전고점 돌파(ORB) 구간]", records_orb, "orb_setup"),
        ("[전략 B: 장중 거래량·수급 상승추세 중 1분봉 VWAP 눌림목 반등 구간]", records_vwap, "vwap_setup"),
    ]:
        print(f"  {setup_name}")
        base_df = pd.DataFrame(rec_dict["1m_close"])
        if len(base_df) == 0 and len(rec_dict["2s_entry"]) == 0:
            print("    (이벤트 없음)")
            continue
        for k_mode, mode_label in [
            ("1m_close", "① 1분봉 종가 확인 후 진입 (60초 대기)"),
            ("10s_entry", "② 1분봉 타점구간 + 10초봉(10s) 선제 진입"),
            ("5s_entry", "③ 1분봉 타점구간 +  5초봉( 5s) 선제 진입"),
            ("2s_entry", "④ 1분봉 타점구간 +  2초봉( 2s) 선제 진입"),
        ]:
            df_m = pd.DataFrame(rec_dict[k_mode])
            if len(df_m) == 0:
                continue
            if len(base_df) > 0:
                merged = pd.merge(
                    df_m[["date", "entry_px"]],
                    base_df[["date", "entry_px"]],
                    on="date",
                    suffixes=("_sub", "_1m"),
                )
                if len(merged) > 0:
                    price_improve_pct = float(((merged["entry_px_1m"] - merged["entry_px_sub"]) / merged["entry_px_1m"] * 100.0).mean())
                else:
                    price_improve_pct = 0.0
            else:
                price_improve_pct = 0.0
            row_stat = {"n": int(len(df_m)), "entry_price_improvement_vs_1m_pct": round(price_improve_pct, 3)}
            line = f"    • {mode_label:<40} (n={len(df_m):3d}, 1분봉대비 진입가 절감 {price_improve_pct:+6.3f}%) | "
            for h_col, h_lbl in [("r5m", "5분뒤"), ("r15m", "15분뒤"), ("r30m", "30분뒤"), ("r60m", "60분뒤"), ("reod", "장마감")]:
                gross = float(df_m[h_col].mean())
                net = gross - cost_pct
                wr_net = float((df_m[h_col] > cost_pct).mean() * 100.0)
                row_stat[f"{h_col}_gross"] = round(gross, 3)
                row_stat[f"{h_col}_net"] = round(net, 3)
                row_stat[f"{h_col}_wr"] = round(wr_net, 1)
                line += f"{h_lbl}: 세전{gross:+6.2f}%/세후{net:+6.2f}% | "
            print(line)
            out_sym[key_out][k_mode] = row_stat

    return out_sym


def run_kodex200_ultrashort_wave():
    code, name, cost = "A069500", "KODEX 200 (무세금 대표 지수 ETF)", 0.025
    run_mtf_comparison_for_symbol(code, name, cost)
    days = load_all_valid_tick_days(code)
    d_closes = [float(d["m1"]["close"].iloc[-1]) for d in days]

    for mode_name, use_daily_filter in [
        ("전체 장중 1분봉 돌파 (ORB)", False),
        ("일봉 정배열 상승추세 + 장중 1분봉 돌파 (ORB)", True),
    ]:
        rets = {h: [] for h in ["2s", "5s", "10s", "30s", "60s", "5m", "15m", "60m", "eod"]}
        for idx, d in enumerate(days):
            if use_daily_filter and idx < 5:
                continue
            if use_daily_filter and d_closes[idx - 1] < np.mean(d_closes[idx - 5 : idx]):
                continue
            m1, ticks = d["m1"], d["ticks"].reset_index(drop=True)
            or_high = m1.iloc[:20]["high"].max()
            if m1.iloc[:20]["d_cvd"].sum() <= 0:
                continue
            for i in range(20, min(90, len(m1) - 61)):
                if m1.iloc[i]["high"] > or_high and m1.iloc[i]["d_cvd"] > 0:
                    sub = ticks[ticks["min_ts"] >= m1.iloc[i]["min_ts"]].reset_index(drop=True)
                    cross = sub[sub["upbit_close"] > or_high]
                    if len(cross) == 0:
                        continue
                    t0 = cross["ts_exact"].iloc[0]
                    px0 = float(cross["upbit_close"].iloc[0])
                    for s_h, k in [(2, "2s"), (5, "5s"), (10, "10s"), (30, "30s"), (60, "60s")]:
                        after = sub[sub["ts_exact"] >= t0 + pd.Timedelta(seconds=s_h)]
                        if len(after) > 0:
                            rets[k].append((float(after["upbit_close"].iloc[0]) / px0 - 1.0) * 100.0)
                    rets["5m"].append((float(m1.iloc[i + 5]["close"]) / px0 - 1.0) * 100.0)
                    rets["15m"].append((float(m1.iloc[i + 15]["close"]) / px0 - 1.0) * 100.0)
                    rets["60m"].append((float(m1.iloc[i + 60]["close"]) / px0 - 1.0) * 100.0)
                    rets["eod"].append((float(m1.iloc[-1]["close"]) / px0 - 1.0) * 100.0)
                    break
        cnt = len(rets["10s"])
        print(f"\n[{mode_name}] (n={cnt}, 왕복비용 {cost:.3f}%):")
        for k in ["2s", "5s", "10s", "30s", "60s", "5m", "15m", "60m", "eod"]:
            if rets[k]:
                arr = np.array(rets[k])
                print(
                    f"  {k:>4}: 세전 {np.mean(arr):+6.3f}% | 세후 {np.mean(arr)-cost:+6.3f}% | 세후승률 {np.mean(arr>cost)*100:5.1f}%"
                )

    # Also test 2s/5s/10s standalone burst on KODEX 200
    for w_sec in [2, 5, 10]:
        b_rets = {h: [] for h in ["2s", "5s", "10s", "30s", "60s"]}
        for d in days:
            ticks = d["ticks"].copy()
            ticks["b_ts"] = ticks["min_ts"] + pd.to_timedelta((ticks["sec_offset"] // w_sec) * w_sec, unit="s")
            bars = ticks.groupby("b_ts", as_index=False).agg(
                open=("upbit_close", "first"),
                close=("upbit_close", "last"),
                vol=("upbit_volume", "sum"),
                d_cvd=("d_cvd", "sum"),
            ).sort_values("b_ts").reset_index(drop=True)
            bars["vol_ma"] = bars["vol"].rolling(30, min_periods=10).mean()
            bars["ret"] = (bars["close"] / bars["open"] - 1.0) * 100.0
            c_arr = bars["close"].values.astype(float)
            n = len(c_arr)
            last_i = -99
            for i in range(30, n - 35):
                if i - last_i < 15:
                    continue
                if bars["vol"].iloc[i] >= bars["vol_ma"].iloc[i] * 5.0 and bars["ret"].iloc[i] >= 0.04 and bars["d_cvd"].iloc[i] > 0:
                    px0 = c_arr[i]
                    for h_s, h_k in [(2, "2s"), (5, "5s"), (10, "10s"), (30, "30s"), (60, "60s")]:
                        step = max(1, h_s // w_sec)
                        if i + step < n:
                            b_rets[h_k].append((c_arr[i + step] / px0 - 1.0) * 100.0)
                    last_i = i
        print(f"\n[KODEX 200 단독 {w_sec}초봉 거래량 폭증(5배+) & 양봉(+0.04%+)] (n={len(b_rets['10s'])}, 왕복비용 {cost:.3f}%):")
        for k in ["2s", "5s", "10s", "30s", "60s"]:
            if b_rets[k]:
                arr = np.array(b_rets[k])
                print(
                    f"  {k:>4}: 세전 {np.mean(arr):+6.3f}% | 세후 {np.mean(arr)-cost:+6.3f}% | 세후승률 {np.mean(arr>cost)*100:5.1f}%"
                )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "A069500":
        run_kodex200_ultrashort_wave()
        return
    targets = [
        ("A069500", "KODEX 200 (무세금 대표 지수 ETF)", 0.025),
        ("A000660", "SK하이닉스 (고변동 대형 주도주)", 0.23),
        ("A005930", "삼성전자 (대형주)", 0.23),
        ("A122630", "KODEX 레버리지 (무세금 2배 ETF)", 0.03),
        ("A0167A0", "SOL 반도체핵심소재 (무세금 고변동 섹터 ETF)", 0.04),
        ("A229200", "KODEX 코스닥150 (무세금 코스닥 ETF)", 0.03),
    ]
    results = []
    for code, name, cost in targets:
        r = run_mtf_comparison_for_symbol(code, name, cost)
        if r:
            results.append(r)

    out_path = os.path.join(OUT_DIR, "mtf_1m_to_subsec_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
