"""
궁금증 끝장 해부: 2초봉(2s) & 5초봉(5s) 초단타 — 원시 틱(Raw Tick + 실시간 Bid/Ask 호가) 기반 정밀 검증
질문: "10초봉도 늦다면 아예 2초봉, 5초봉으로 거래량·변동성 폭증 첫 순간을 잡으면 빈틈이 있지 않을까?"

검증 항목:
1) 원시 틱(DataVault/raw/creon/tick)에서 정확한 2초봉(2s)·5초봉(5s) 생성 (실제 매수1호가 Bid, 매도1호가 Ask 포함)
2) 종목별 2초봉 / 5초봉 거래량 폭증(5배~10배) + 순매수 체결델타(dCVD > 50%) 폭발 시:
   - ① 차트 종가 기준 세전 수익 (Close[t] -> Close[t+h])
   - ② 실제 호가창 즉시 체결 시 (Ask[t] 매수 -> Bid[t+h] 매도, 호가 스프레드 차감)
   - ③ 실전 API 주문 지연(다음 캔들 시가/Ask 체결) + 거래세/수수료 차감 후 최종 순수익 (Net Return)
3) 초고속 리드-래그(Lead-Lag): SK하이닉스(000660)가 2초/5초 동안 급등할 때 무세금 레버리지(122630)·KODEX200(069500)을 즉시 매수하면 LP를 이길 수 있는가?

실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/test_2s_5s_ultrashort.py
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


def build_subsecond_bars(code: str, freq_sec: int = 2, max_days: int = 15) -> pd.DataFrame:
    """
    크레온 원시 틱은 분(HH:MM:00) 타임스탬프만 가지며 분 내부의 행 순번(0..k-1)이 곧 시간축이다
    (shared/data_library/tick_order_audit.py 및 build_normalized_10s_stock 과 동일한 규약).
    따라서 각 분(minute) 내의 틱들을 n_buckets = 60 // freq_sec (2초봉=30버킷, 5초봉=12버킷)으로 등분하여
    합성 타임스탬프(ts + bucket * freq_sec)를 부여한다.
    """
    folder = os.path.join(TICK_ROOT, code)
    if not os.path.exists(folder):
        return pd.DataFrame()
    # 2026-08-26 이후 정방향(FORWARD) 보증 구간 사용
    csvs = [fp for fp in sorted(glob.glob(os.path.join(folder, "2026-*.csv"))) if os.path.basename(fp) >= "2026-08-26.csv"][-max_days:]
    bar_list = []
    n_buckets = 60 // freq_sec

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
        df = df[(t_time >= pd.to_datetime("09:01:00").time()) & (t_time <= pd.to_datetime("15:19:00").time())].copy()
        if len(df) < 500:
            continue

        df["d_cvd"] = df["cvd"].diff().fillna(0.0)
        # Within each minute, assign bucket 0 .. n_buckets-1 based on row position
        grp = df.groupby("min_ts", sort=False)
        pos = grp.cumcount()
        cnt = grp["upbit_close"].transform("count")
        bucket = np.minimum(n_buckets - 1, (pos * n_buckets) // np.maximum(1, cnt))
        df["ts"] = df["min_ts"] + pd.to_timedelta(bucket * freq_sec, unit="s")

        rs = df.groupby("ts", as_index=False).agg(
            open=("upbit_close", "first"),
            high=("upbit_high", "max"),
            low=("upbit_low", "min"),
            close=("upbit_close", "last"),
            volume=("upbit_volume", "sum"),
            trade_price=("upbit_trade_price", "sum"),
            d_cvd=("d_cvd", "sum"),
            bid=("bid", "last"),
            ask=("ask", "last"),
        )
        # Reindex to full regular grid of freq_sec within the day so time horizons (+2s, +5s, +10s) are exact
        full_idx = pd.date_range(rs["ts"].min(), rs["ts"].max(), freq=f"{freq_sec}s")
        rs = rs.set_index("ts").reindex(full_idx)
        rs.index.name = "ts"
        rs[["close", "bid", "ask"]] = rs[["close", "bid", "ask"]].ffill()
        rs["open"] = rs["open"].fillna(rs["close"])
        rs["high"] = rs["high"].fillna(rs["close"])
        rs["low"] = rs["low"].fillna(rs["close"])
        rs[["volume", "trade_price", "d_cvd"]] = rs[["volume", "trade_price", "d_cvd"]].fillna(0.0)
        rs = rs.dropna(subset=["close"]).reset_index()
        rs["date"] = d_str
        bar_list.append(rs)

    if not bar_list:
        return pd.DataFrame()
    return pd.concat(bar_list, ignore_index=True)


def evaluate_burst_on_bars(df: pd.DataFrame, freq_sec: int, name: str, fee_tax_pct: float) -> dict:
    """
    fee_tax_pct: 왕복 수수료 + 증권거래세 합계 % (호가 스프레드/슬리피지는 실제 Ask/Bid 가격으로 직접 계산!)
      - 개별주식(000660, 005930): 거래세 0.18% + 왕복수수료 0.03% = 0.21%
      - 국내 ETF(122630, 069500, 0167A0): 거래세 0.00% + 왕복수수료 0.01% = 0.01%
    """
    c = df["close"].values.astype(float)
    o = df["open"].values.astype(float)
    v = df["volume"].values.astype(float)
    d_cvd = df["d_cvd"].values.astype(float)
    bid = np.where(df["bid"].values > 0, df["bid"].values.astype(float), c)
    ask = np.where(df["ask"].values > 0, df["ask"].values.astype(float), c)
    dates = df["date"].values

    # Fix cases where Creon tick bid/ask is 0 or inverted during VI
    spread_pct = (ask - bid) / c * 100.0
    valid_ba = (ask >= bid) & (spread_pct >= 0.0) & (spread_pct <= 1.5)
    med_spread_pct = float(np.median(spread_pct[valid_ba & (spread_pct > 0)])) if np.any(valid_ba & (spread_pct > 0)) else 0.05

    # Rolling 5-minute average volume per bar
    win_5m = max(10, int(300 // freq_sec))
    vol_ma = pd.Series(v).rolling(win_5m, min_periods=10).mean().values
    ret_1c = (c / np.roll(c, 1) - 1.0) * 100.0
    ret_1c[0] = 0.0

    # Define burst threshold: Volume >= 8x 5m average & top 1% positive bar & d_cvd / vol >= 0.50
    pos_rets = ret_1c[ret_1c > 0]
    th_ret = float(np.percentile(pos_rets, 95)) if len(pos_rets) > 0 else 0.15

    mask_burst = (
        (v >= vol_ma * 8.0)
        & (v > 0)
        & (ret_1c >= th_ret)
        & ((d_cvd / np.maximum(1.0, v)) >= 0.50)
        & valid_ba
    )

    raw_idx = np.where(mask_burst)[0]
    # De-cluster within 30 seconds
    min_gap = max(1, int(30 // freq_sec))
    clean_idx = []
    last_i = -9999
    for i in raw_idx:
        if i - last_i >= min_gap and i + int(180 // freq_sec) < len(df):
            if dates[i] == dates[i + int(180 // freq_sec)]:
                clean_idx.append(i)
                last_i = i
    idx = np.array(clean_idx, dtype=int)

    horizons_sec = [2, 5, 10, 30, 60, 180]
    horizons_sec = [h for h in horizons_sec if h >= freq_sec]

    print(
        f"\n▶ [{freq_sec}초봉] {name} | 총 {len(df):,}캔들 | 폭증 타점(거래량8배+ & 상위5%급등 & 순매수50%+) n={len(idx)}건 "
        f"| 호가스프레드 중앙값={med_spread_pct:.3f}% | 세금+수수료={fee_tax_pct:.2f}%"
    )

    h_results = {}
    for h_sec in horizons_sec:
        hc = max(1, int(h_sec // freq_sec))
        # 1) Chart Close-to-Close Gross (환상 속 차트 수익률: 종가에 사서 종가에 팔기)
        cc_gross = (c[idx + hc] - c[idx]) / c[idx] * 100.0

        # 2) Zero-Latency Orderbook Gross (0.00초 지연: 시그널 캔들 종료 즉시 매도1호가 Ask[t]에 사서 매수1호가 Bid[t+hc]에 팔기)
        ob_instant_gross = (bid[idx + hc] - ask[idx]) / ask[idx] * 100.0

        # 3) Realistic 1-bar Latency + Tax/Fee Net (시그널 인지 후 다음 캔들 Ask[t+1]에 진입 -> Bid[t+hc]에 청산 - 세금·수수료)
        ob_real_net = (bid[idx + hc] - ask[idx + 1]) / ask[idx + 1] * 100.0 - fee_tax_pct

        h_results[f"{h_sec}s"] = {
            "chart_close_gross_pct": round(float(np.mean(cc_gross)), 3),
            "orderbook_instant_gross_pct": round(float(np.mean(ob_instant_gross)), 3),
            "real_latency_net_pct": round(float(np.mean(ob_real_net)), 3),
            "real_win_rate_pct": round(float(np.mean(ob_real_net > 0) * 100.0), 1),
        }
        print(
            f"  • 보유 {h_sec:3d}초 ({hc:2d}캔들) | "
            f"① 차트종가(환상): {np.mean(cc_gross):+6.3f}% | "
            f"② 호가즉시체결(Ask->Bid): {np.mean(ob_instant_gross):+6.3f}% | "
            f"③ 실전(지연+세금차감 후): {np.mean(ob_real_net):+6.3f}% (승률 {np.mean(ob_real_net > 0)*100:4.1f}%)"
        )

    return {
        "freq_sec": freq_sec,
        "name": name,
        "bars": int(len(df)),
        "events": int(len(idx)),
        "median_spread_pct": round(med_spread_pct, 4),
        "fee_tax_pct": fee_tax_pct,
        "horizons": h_results,
    }


def test_lead_lag_2s_5s():
    """
    초고속 리드-래그(Lead-Lag) 검증:
    SK하이닉스(A000660)가 2초봉 / 5초봉에서 거래량 5배+ & 상위 1% 급등할 때,
    같은 시각 KODEX 200(A069500) 또는 KODEX 레버리지(A122630)를 매도1호가(Ask)에 즉시 매수하면
    LP보다 먼저 먹을 수 있는가?
    """
    print("\n" + "=" * 120)
    print("[특수 실험] 2초봉 & 5초봉 'SK하이닉스 폭증 -> KODEX 레버리지(122630) / KODEX 200(069500) 초고속 리드-래그' 검증")
    print("=" * 120)
    ll_results = []

    for freq_sec in [2, 5]:
        df_hynix = build_subsecond_bars("A000660", freq_sec=freq_sec, max_days=15)
        for etf_code, etf_name in [("A122630", "KODEX 레버리지(122630)"), ("A069500", "KODEX 200(069500)")]:
            df_etf = build_subsecond_bars(etf_code, freq_sec=freq_sec, max_days=15)
            m = pd.merge(
                df_hynix[["ts", "date", "close", "volume", "d_cvd"]],
                df_etf[["ts", "close", "bid", "ask"]],
                on="ts",
                suffixes=("_hy", "_etf"),
            )
            c_hy = m["close_hy"].values.astype(float)
            v_hy = m["volume"].values.astype(float)
            cvd_hy = m["d_cvd"].values.astype(float)
            c_etf = m["close_etf"].values.astype(float)
            bid_etf = np.where(m["bid"].values > 0, m["bid"].values.astype(float), c_etf)
            ask_etf = np.where(m["ask"].values > 0, m["ask"].values.astype(float), c_etf)
            dates = m["date"].values

            ret_hy = (c_hy / np.roll(c_hy, 1) - 1.0) * 100.0
            ret_hy[0] = 0.0
            vol_ma = pd.Series(v_hy).rolling(int(300 // freq_sec), min_periods=10).mean().values
            th_hy = float(np.percentile(ret_hy[ret_hy > 0], 98))

            sig = (v_hy >= vol_ma * 5.0) & (ret_hy >= th_hy) & ((cvd_hy / np.maximum(1.0, v_hy)) >= 0.40)
            raw_idx = np.where(sig)[0]
            clean_idx = []
            last_i = -999
            for i in raw_idx:
                if i - last_i >= int(30 // freq_sec) and i + int(60 // freq_sec) < len(m):
                    if dates[i] == dates[i + int(60 // freq_sec)]:
                        clean_idx.append(i)
                        last_i = i
            idx = np.array(clean_idx, dtype=int)

            print(f"\n▶ [{freq_sec}초봉 리드-래그] SK하이닉스 상위2% 폭증(n={len(idx)}건) 즉시 -> {etf_name} 매수")
            row = {"freq_sec": freq_sec, "target": etf_name, "n": int(len(idx)), "horizons": {}}
            for h_sec in [freq_sec, 10, 30, 60]:
                hc = max(1, int(h_sec // freq_sec))
                cc_gross = (c_etf[idx + hc] - c_etf[idx]) / c_etf[idx] * 100.0
                ob_net = (bid_etf[idx + hc] - ask_etf[idx]) / ask_etf[idx] * 100.0 - 0.010  # ETF 왕복수수료 0.01%
                row["horizons"][f"{h_sec}s"] = {
                    "chart_close_gross_pct": round(float(np.mean(cc_gross)), 3),
                    "real_ask_to_bid_net_pct": round(float(np.mean(ob_net)), 3),
                }
                print(
                    f"  • {h_sec:2d}초 뒤 | 차트종가 기준(세전): {np.mean(cc_gross):+6.3f}% | "
                    f"실제 호가창(Ask매수->Bid매도, 수수료차감): {np.mean(ob_net):+6.3f}%"
                )
            ll_results.append(row)
    return ll_results


def main():
    print("=" * 120)
    print("[실험 1] 원시 틱(Raw Tick) 기반 2초봉(2s) vs 5초봉(5s) 거래량·변동성 폭증 초단타 검증")
    print("=" * 120)

    assets = [
        ("A000660", "SK하이닉스(000660, 고변동 대장주)", 0.21),
        ("A005930", "삼성전자(005930, 대형주)", 0.21),
        ("A122630", "KODEX 레버리지(122630, 무세금 2배 ETF)", 0.01),
        ("A0167A0", "SOL 반도체핵심소재(0167A0, 무세금 섹터 ETF)", 0.01),
    ]

    all_bars_res = []
    for code, name, fee_tax in assets:
        for freq_sec in [2, 5]:
            df_b = build_subsecond_bars(code, freq_sec=freq_sec, max_days=15)
            if len(df_b) > 0:
                res = evaluate_burst_on_bars(df_b, freq_sec, name, fee_tax)
                all_bars_res.append(res)

    ll_res = test_lead_lag_2s_5s()

    out_path = os.path.join(OUT_DIR, "ultrashort_2s_5s_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"burst_2s_5s": all_bars_res, "lead_lag_2s_5s": ll_res}, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
