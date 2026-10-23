"""
'거래량 폭증 + 대변동성 폭발' 상황에서 실제로 존재하는 빈틈(Edge) 전수 탐색
1) 상방 폭발 추격 (Upward Climax): 왜 실패하는지, 예외 조건(수급/시총/신고가 돌파)이 있는지?
2) 하방 투매 폭발 (Selling Climax / Panic Capitulation): 거래량 폭증 + 급락 시 누가 받아먹을 때 반등하는지?
3) 폭발 후 수축 (Expansion -> Contraction): 거래량·변동성 대폭발 이후 며칠 뒤 어떤 조건에서 가장 큰 알파가 나는지?
4) 1분봉 장중 극단적 투매(Flash Crash + Volume Spike) 직후 단기 반등(Dead-Cat Bounce)이 비용(0.28%)을 이기는지?

실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/test_extreme_vol_loopholes.py
"""
import os
import sys
import glob
import json
import numpy as np
import pandas as pd

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
CANDLE_1M_ROOT = os.path.join(ROOT_DIR, "DataVault/staging/vol_spike_universe/raw/creon/candle_1m")


def eval_condition(cdf: pd.DataFrame, label: str, cost_pct: float = 0.25) -> dict:
    row = {"label": label, "n": int(len(cdf))}
    line = f"  • {label:<66} (n={len(cdf):5d}) | "
    for h in [1, 3, 5, 10, 20]:
        col = f"fwd_{h}d"
        sub_h = cdf.dropna(subset=[col])
        if len(sub_h) == 0:
            continue
        vals = sub_h[col].values - cost_pct
        mean_r = float(np.mean(vals))
        wr = float(np.mean(vals > 0) * 100.0)
        # Date-grouped t-stat (prevent same-day market crash clustering illusion)
        daily_m = sub_h.groupby("date")[col].mean().values - cost_pct
        t_val = float(np.mean(daily_m) / (np.std(daily_m, ddof=1) / np.sqrt(len(daily_m)))) if len(daily_m) > 3 else 0.0
        row[f"net_{h}d"] = round(mean_r, 2)
        row[f"day_eq_net_{h}d"] = round(float(np.mean(daily_m)), 2) if len(daily_m) > 0 else 0.0
        row[f"t_{h}d"] = round(t_val, 2)
        row[f"wr_{h}d"] = round(wr, 1)
        line += f"{h:2d}d:{mean_r:+5.2f}%(날짜가중{row[f'day_eq_net_{h}d']:+5.2f}%,t={t_val:+5.2f}) | "

    # Check Dev vs 2026 OOS for best horizon (5d and 20d)
    for h in [5, 20]:
        col = f"fwd_{h}d"
        dev_s = cdf[cdf["date"] < pd.Timestamp("2026-01-01")].dropna(subset=[col])
        oos_s = cdf[cdf["date"] >= pd.Timestamp("2026-01-01")].dropna(subset=[col])
        row[f"dev_{h}d"] = round(float(dev_s[col].mean() - cost_pct), 2) if len(dev_s) > 0 else 0.0
        row[f"oos_{h}d"] = round(float(oos_s[col].mean() - cost_pct), 2) if len(oos_s) > 0 else 0.0
    line += f"[20d Dev:{row['dev_20d']:+5.2f}% / OOS:{row['oos_20d']:+5.2f}%]"
    print(line)
    return row


def test_daily_extreme_vol():
    print("=" * 130)
    print("[PART 1] 일봉 기준 '거래량 폭증 + 대변동성' 10가지 세부 시나리오 전수 검증 (왕복 세금·비용 0.25% 차감)")
    print("=" * 130)

    p_prices = os.path.join(ROOT_DIR, "DataVault/staging/kr_equity/derived/prices_1d.parquet")
    p_flows = os.path.join(ROOT_DIR, "DataVault/staging/vol_spike_universe/derived/flows_panel.parquet")
    p_ext = os.path.join(ROOT_DIR, "DataVault/staging/kr_equity/derived/daily_ext.parquet")

    df_p = pd.read_parquet(p_prices)
    df_p["date"] = pd.to_datetime(df_p["date"])
    df_p = df_p[(df_p["date"] >= pd.Timestamp("2024-06-01")) & (df_p["date"] <= pd.Timestamp("2026-10-08"))].copy()
    df_p = df_p.dropna(subset=["open", "high", "low", "close", "volume", "value"])
    df_p = df_p[df_p["code"].str.endswith("0")].copy()

    df_f = pd.read_parquet(p_flows)
    df_f["date"] = pd.to_datetime(df_f["date"])
    df_f = df_f[(df_f["date"] >= pd.Timestamp("2024-06-01")) & (df_f["date"] <= pd.Timestamp("2026-10-08"))].copy()

    df_e = pd.read_parquet(p_ext, columns=["code", "date", "market_cap"])
    df_e["date"] = pd.to_datetime(df_e["date"])
    df_e = df_e[(df_e["date"] >= pd.Timestamp("2024-06-01")) & (df_e["date"] <= pd.Timestamp("2026-10-08"))].copy()

    df = df_p.merge(df_f, on=["code", "date"], how="left").merge(df_e, on=["code", "date"], how="left")
    df[["frgn", "orgn", "prsn", "prog"]] = df[["frgn", "orgn", "prsn", "prog"]].fillna(0.0)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    # Market daily median return to distinguish market-wide crash from stock-specific event
    mkt_daily = df.groupby("date")
    g = df.groupby("code", sort=False)

    df["ret_1d"] = (df["close"] / g["close"].shift(1) - 1.0) * 100.0
    df["mkt_ret_1d"] = df["date"].map(df.groupby("date")["ret_1d"].median())
    df["idiosyncratic_ret_1d"] = df["ret_1d"] - df["mkt_ret_1d"]

    df["intraday_range_pct"] = (df["high"] - df["low"]) / df["low"] * 100.0
    df["upper_wick_ratio"] = np.where(df["high"] > df["low"], (df["high"] - df["close"]) / (df["high"] - df["low"]), 0.0)
    df["lower_wick_ratio"] = np.where(df["high"] > df["low"], (df["close"] - df["low"]) / (df["high"] - df["low"]), 0.0)

    df["vol_ma20"] = g["volume"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["val_ma20"] = g["value"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["vol_mult"] = np.where(df["vol_ma20"] > 0, df["volume"] / df["vol_ma20"], 0.0)
    df["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["ma60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).mean())
    df["hi_120"] = g["high"].transform(lambda s: s.shift(1).rolling(120, min_periods=60).max())

    df["smart_ratio"] = np.where(df["value"] > 0, (df["frgn"] + df["orgn"]) / df["value"] * 100.0, 0.0)
    df["prog_ratio"] = np.where(df["value"] > 0, df["prog"] / df["value"] * 100.0, 0.0)

    for h in [1, 3, 5, 10, 20]:
        df[f"fwd_{h}d"] = (g["close"].shift(-h) / df["close"] - 1.0) * 100.0

    # Define Extreme Expansion Anchor (거래량 4배+ & 당일 +7%~+25% 장대양봉 & 스마트머니 순매수)
    df["smart_climax_up"] = (
        (df["vol_mult"] >= 4.0)
        & (df["value"] >= 2.0e10)
        & (df["ret_1d"].between(7.0, 25.0))
        & (df["smart_ratio"] >= 8.0)
        & (df["upper_wick_ratio"] <= 0.30)  # 윗꼬리 짧은 꽉 찬 장대양봉
    ).astype(int)
    df["had_smart_climax_5d"] = g["smart_climax_up"].transform(lambda s: s.shift(1).rolling(5, min_periods=1).max())

    sub = df[
        (df["date"] >= pd.Timestamp("2024-09-15"))
        & (df["date"] <= pd.Timestamp("2026-09-15"))
        & (df["close_unadj"] >= 3000)
        & (df["val_ma20"] >= 3.0e9)
    ].copy()

    scenarios = [
        # --- A. 상방 거래량 폭증 + 대변동성 폭발 당일 진입 ---
        (
            "V1. [상방폭발-일반] 거래량 5배+ & 당일 +10%~+25% 급등 (조건 없음)",
            (sub["vol_mult"] >= 5.0) & (sub["ret_1d"].between(10.0, 25.0)),
        ),
        (
            "V2. [상방폭발-윗꼬리] 거래량 5배+ & 장중 +10%↑ 후 윗꼬리 50%+ (설거지 의심)",
            (sub["vol_mult"] >= 5.0) & (sub["intraday_range_pct"] >= 10.0) & (sub["upper_wick_ratio"] >= 0.50),
        ),
        (
            "V3. [상방폭발-꽉찬양봉+쌍끌이] 거래량 4배+ & +8%~+25% & 윗꼬리<20% & 외인·기관+10%↑",
            (sub["vol_mult"] >= 4.0)
            & (sub["value"] >= 2.0e10)
            & (sub["ret_1d"].between(8.0, 25.0))
            & (sub["upper_wick_ratio"] <= 0.20)
            & (sub["smart_ratio"] >= 10.0),
        ),
        (
            "V4. [상방폭발-120일 신고가 돌파+쌍끌이] 거래량 3배+ & 120일 고점 돌파 & 외인·기관+10%↑ & 프로그램+5%↑",
            (sub["vol_mult"] >= 3.0)
            & (sub["value"] >= 2.0e10)
            & (sub["close"] > sub["hi_120"])
            & (sub["ret_1d"].between(5.0, 22.0))
            & (sub["smart_ratio"] >= 10.0)
            & (sub["prog_ratio"] >= 5.0),
        ),
        # --- B. 하방 거래량 폭증 + 패닉 투매 (Selling Climax) ---
        (
            "V5. [하방폭발-개별악재 투매] 시장 평온일(지수>-1%)에 개별주만 거래량 4배+ & -8%↓ 폭락",
            (sub["mkt_ret_1d"] > -1.0) & (sub["vol_mult"] >= 4.0) & (sub["ret_1d"] <= -8.0),
        ),
        (
            "V6. [하방폭발-스마트머니 투매흡수] 거래량 3배+ & -7%↓ 급락 중 외인+기관(+10%↑) 대량 받아먹기",
            (sub["vol_mult"] >= 3.0) & (sub["ret_1d"] <= -7.0) & (sub["smart_ratio"] >= 10.0) & (sub["market_cap"] >= 3.0e11),
        ),
        # --- C. 폭발 직후 거래량 급감 눌림목 (Expansion -> Contraction) ---
        (
            "V7. [폭발 후 수축] V3(거래량 4배+ & 스마트머니 장대양봉) 발생 후 5일 내 첫 음봉 조정일 진입",
            (sub["had_smart_climax_5d"] == 1) & (sub["ret_1d"].between(-6.0, -0.5)) & (sub["close"] > sub["ma20"]),
        ),
    ]

    out = []
    for label, mask in scenarios:
        out.append(eval_condition(sub[mask], label, cost_pct=0.25))
    return out


def test_intraday_1m_extreme_volatility():
    """
    1분봉 기준 '장중 거래량 대폭발 + 급등/급락 변동성 폭발' 4가지 빈틈 테스트 (유동성 상위 100종목, 2025~2026)
    - I1: 5분간 거래대금 10억+ & +4%~+8% 급등 + CVD +50% 이상 (초강력 VI 직전 모멘텀 돌파)
    - I2: 5분간 거래대금 10억+ & +5% 급등 직후 첫 3분 거래량 급감 눌림목 (장중 첫 조정 매수)
    - I3: 10분간 거래대금 10억+ & -4% 이하 급락 투매 + 아래꼬리 & CVD 양전환 (장중 패닉셀 역추세 스캘핑)
    """
    print("\n" + "=" * 130)
    print("[PART 2] 1분봉 장중 '거래량 폭증 + 대변동성 폭발' 3대 단타 시나리오 검증 (왕복 세금·비용 0.28% 차감)")
    print("=" * 130)

    # Pick 100 active stocks from candle_1m
    codes = sorted(os.listdir(CANDLE_1M_ROOT))[:120]
    events = {"I1_MOMENTUM_BURST": [], "I2_BURST_FIRST_PULLBACK": [], "I3_PANIC_FLUSH_BOUNCE": []}

    for code in codes:
        folder = os.path.join(CANDLE_1M_ROOT, code)
        if not os.path.isdir(folder):
            continue
        files = sorted(glob.glob(os.path.join(folder, "2026-*.csv")))
        for fp in files:
            d_str = os.path.basename(fp).replace(".csv", "")
            try:
                df = pd.read_csv(fp)
            except Exception:
                continue
            n = len(df)
            if n < 60:
                continue
            o = df["upbit_open"].values.astype(float)
            h = df["upbit_high"].values.astype(float)
            l = df["upbit_low"].values.astype(float)
            c = df["upbit_close"].values.astype(float)
            v = df["upbit_volume"].values.astype(float)
            amt = df["upbit_trade_price"].values.astype(float)
            cvd = df["cvd"].values.astype(float) if "cvd" in df.columns else np.zeros(n)

            if c[0] < 3000 or np.sum(amt) < 5.0e9:
                continue

            avg_5m_amt = max(1.0, np.sum(amt) / (n / 5.0))

            def rec(key, idx):
                px = c[idx]
                events[key].append(
                    {
                        "date": d_str,
                        "r5": (c[min(n - 1, idx + 5)] / px - 1.0) * 100.0,
                        "r15": (c[min(n - 1, idx + 15)] / px - 1.0) * 100.0,
                        "r30": (c[min(n - 1, idx + 30)] / px - 1.0) * 100.0,
                        "r60": (c[min(n - 1, idx + 60)] / px - 1.0) * 100.0,
                        "reod": (c[-1] / px - 1.0) * 100.0,
                    }
                )

            used = set()
            burst_bar = None
            for i in range(10, n - 30):
                ret_5m = (c[i] / c[i - 5] - 1.0) * 100.0
                amt_5m = np.sum(amt[i - 4 : i + 1])
                vol_5m = np.sum(v[i - 4 : i + 1])
                cvd_5m = cvd[i] - cvd[i - 5]

                # I1: 5분간 거래대금 10억+ (평소 8배+) & +4%~+9% 폭발 & CVD 순매수 50%+
                if (
                    "I1" not in used
                    and 4.0 <= ret_5m <= 9.0
                    and amt_5m >= 1.0e9
                    and vol_5m > 0
                    and (cvd_5m / vol_5m) >= 0.50
                    and (h[i] - c[i]) <= (h[i] - l[i]) * 0.25
                ):
                    rec("I1_MOMENTUM_BURST", i)
                    used.add("I1")
                    burst_bar = i

                # I2: I1 폭발 발생 후 3~10분 내 거래량이 폭발 시점의 30% 이하로 줄어든 첫 음봉 눌림목
                if (
                    "I2" not in used
                    and burst_bar is not None
                    and 3 <= (i - burst_bar) <= 10
                    and c[i] < o[i]
                    and c[i] >= c[burst_bar - 5] * 1.02
                    and amt[i] <= (amt_5m / 5.0) * 0.35
                ):
                    rec("I2_BURST_FIRST_PULLBACK", i)
                    used.add("I2")
                    burst_bar = None

                # I3: 10분간 -4% 이하 패닉 투매 + 거래대금 10억+ 후 첫 강한 CVD 양봉 반등 (투매 데드캣 바운스)
                ret_10m = (c[i - 1] / c[i - 10] - 1.0) * 100.0
                amt_10m = np.sum(amt[i - 10 : i])
                if (
                    "I3" not in used
                    and ret_10m <= -4.0
                    and amt_10m >= 1.0e9
                    and c[i] > o[i]
                    and v[i] > 0
                    and ((cvd[i] - cvd[i - 1]) / v[i]) >= 0.40
                ):
                    rec("I3_PANIC_FLUSH_BOUNCE", i)
                    used.add("I3")

    cost_pct = 0.28
    out_1m = []
    titles = {
        "I1_MOMENTUM_BURST": "I1. [1분봉 상방폭발] 5분 거래대금 10억+ & +4~9% 급등 & CVD +50%↑ 추격",
        "I2_BURST_FIRST_PULLBACK": "I2. [1분봉 폭발후 눌림] I1 폭발 직후 3~10분 내 거래량 급감 첫 음봉 매수",
        "I3_PANIC_FLUSH_BOUNCE": "I3. [1분봉 투매반등] 10분 -4%↓ 패닉 투매(10억+) 직후 첫 CVD 양봉 반등",
    }
    for k, title in titles.items():
        edf = pd.DataFrame(events[k])
        if len(edf) == 0:
            continue
        row = {"id": k, "title": title, "n": int(len(edf))}
        line = f"  • {title:<66} (n={len(edf):4d}) | "
        for col, h_name in [("r5", "5분"), ("r15", "15분"), ("r30", "30분"), ("r60", "60분"), ("reod", "장마감")]:
            gross = float(edf[col].mean())
            net = gross - cost_pct
            daily_net = edf.groupby("date")[col].mean().values - cost_pct
            t_val = float(np.mean(daily_net) / (np.std(daily_net, ddof=1) / np.sqrt(len(daily_net)))) if len(daily_net) > 3 else 0.0
            row[f"{col}_gross"] = round(gross, 2)
            row[f"{col}_net"] = round(net, 2)
            row[f"{col}_t"] = round(t_val, 2)
            line += f"{h_name}: 세전{gross:+5.2f}%/세후{net:+5.2f}%(t={t_val:+4.1f}) | "
        print(line)
        out_1m.append(row)
    return out_1m


def main():
    r1 = test_daily_extreme_vol()
    r2 = test_intraday_1m_extreme_volatility()
    out_path = os.path.join(OUT_DIR, "extreme_vol_loopholes_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"daily_extreme": r1, "intraday_1m_extreme": r2}, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
