"""
한국 개별종목에서 실제로 비용(0.25~0.28%) 차감 후 양(+)의 엣지가 발생하는 조건 전수 해부
실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/analyze_stock_edge_conditions.py
"""
import os
import sys
import json
import numpy as np
import pandas as pd

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
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

    g = df.groupby("code", sort=False)
    df["ret_1d"] = (df["close"] / g["close"].shift(1) - 1.0) * 100.0
    df["ret_5d_past"] = (df["close"] / g["close"].shift(5) - 1.0) * 100.0
    df["ret_20d_past"] = (df["close"] / g["close"].shift(20) - 1.0) * 100.0
    df["vol_ma20"] = g["volume"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["val_ma20"] = g["value"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["ma60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).mean())

    # Forward returns (Close[t] -> Close[t+h]) without SL-first distortion
    for h in [1, 3, 5, 10, 20]:
        df[f"fwd_{h}d"] = (g["close"].shift(-h) / df["close"] - 1.0) * 100.0

    # Flow ratios relative to daily trading value (note: flows_panel is net buying quantity or KRW? Let's check!)
    # Wait: let's inspect what unit frgn/orgn/prsn/prog are in flows_panel!
    sample_valid = df[df["frgn"] != 0].head(5)
    print("Sample flows vs volume/value:")
    print(sample_valid[["code", "date", "close_unadj", "volume", "value", "frgn", "orgn", "prsn", "prog"]])

    # In flows_panel, frgn, orgn, prsn, prog are in KRW (same unit as value)!
    df["smart_krw"] = df["frgn"] + df["orgn"]
    df["smart_ratio_val"] = np.where(df["value"] > 0, df["smart_krw"] / df["value"] * 100.0, 0.0)
    df["prog_ratio_val"] = np.where(df["value"] > 0, df["prog"] / df["value"] * 100.0, 0.0)
    df["prsn_ratio_val"] = np.where(df["value"] > 0, df["prsn"] / df["value"] * 100.0, 0.0)
    df["smart_5d_ratio"] = g["smart_ratio_val"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    df["prog_5d_ratio"] = g["prog_ratio_val"].transform(lambda s: s.rolling(5, min_periods=3).mean())

    df["g3_anchor"] = (
        (df["value"] >= 2.0e10)
        & (df["volume"] >= df["vol_ma20"] * 2.0)
        & (df["ret_1d"].between(3.0, 15.0))
        & (df["smart_ratio_val"] >= 10.0)
        & (df["prog_ratio_val"] >= 5.0)
        & (df["close"] > df["ma20"])
    ).astype(int)
    df["recent_g3_5d"] = g["g3_anchor"].transform(lambda s: s.shift(1).rolling(5, min_periods=1).max())

    # Filter analysis window: 2024-09-15 ~ 2026-09-15, liquid stocks (close_unadj >= 3000, val_ma20 >= 30억)
    sub = df[
        (df["date"] >= pd.Timestamp("2024-09-15"))
        & (df["date"] <= pd.Timestamp("2026-09-15"))
        & (df["close_unadj"] >= 3000)
        & (df["val_ma20"] >= 3.0e9)
    ].copy()

    cost_pct = 0.25  # 왕복 마찰비용 0.25% (거래세 0.18% + 수수료 0.03% + 슬리피지 0.04%)

    conditions = [
        (
            "G0. 전체 유동성 유니버스 평균 (Baseline)",
            sub["close"] > 0,
        ),
        (
            "G1. 거래량 3배+ & 당일 +5%~+20% 급등 추격 (수급 무관)",
            (sub["volume"] >= sub["vol_ma20"] * 3.0) & (sub["ret_1d"].between(5.0, 20.0)),
        ),
        (
            "G2. 거래량 3배+ 급등이나 '개인 순매수 독식' (개인 +5%↑, 외인+기관 매도)",
            (sub["volume"] >= sub["vol_ma20"] * 3.0) & (sub["ret_1d"].between(5.0, 20.0)) & (sub["prsn_ratio_val"] >= 5.0),
        ),
        (
            "G3. 거래대금 200억+ 돌파 & '외인+기관(+10%↑) & 프로그램(+5%↑) 쌍끌이'",
            (sub["value"] >= 2.0e10)
            & (sub["volume"] >= sub["vol_ma20"] * 2.0)
            & (sub["ret_1d"].between(3.0, 15.0))
            & (sub["smart_ratio_val"] >= 10.0)
            & (sub["prog_ratio_val"] >= 5.0)
            & (sub["close"] > sub["ma20"]),
        ),
        (
            "G4. 중대형주(시총 5천억+) 5일간 외인+기관 강한 매집(+5%↑) 후 음봉 눌림목",
            (sub["market_cap"] >= 5.0e11)
            & (sub["smart_5d_ratio"] >= 5.0)
            & (sub["close"] > sub["ma60"])
            & (sub["ret_1d"].between(-4.0, -0.5)),
        ),
        (
            "G7. [G3+G4 결합] 쌍끌이 돌파 기준봉 발생 후 5일 내 첫 음봉 눌림목 진입",
            (sub["recent_g3_5d"] == 1)
            & (sub["close"] > sub["ma20"])
            & (sub["ret_1d"].between(-5.0, -0.3))
            & (sub["smart_5d_ratio"] >= 3.0),
        ),
        (
            "G8. [G7 + 중대형 시총 3천억+] 우량 주도주 쌍끌이 기준봉 후 첫 음봉 눌림목",
            (sub["market_cap"] >= 3.0e11)
            & (sub["recent_g3_5d"] == 1)
            & (sub["close"] > sub["ma20"])
            & (sub["ret_1d"].between(-5.0, -0.3))
            & (sub["smart_5d_ratio"] >= 3.0),
        ),
    ]

    print("\n" + "=" * 115)
    print("[분석] 조건별 향후 보유일수(1일, 3일, 5일, 10일, 20일) 평균 순수익률 (왕복 세금·비용 0.25% 차감 후)")
    print("=" * 115)
    summary_rows = []
    for title, mask in conditions:
        cdf = sub[mask]
        row = {"condition": title, "count": int(len(cdf))}
        line = f"• {title:<65} (n={len(cdf):6d}) | "
        for h in [1, 3, 5, 10, 20]:
            valid_r = cdf[f"fwd_{h}d"].dropna().values - cost_pct
            if len(valid_r) > 0:
                mean_r = float(np.mean(valid_r))
                wr = float(np.mean(valid_r > 0) * 100.0)
                # Signal-date equal-weighted t-stat (to remove cross-sectional correlation on same day)
                daily_means = cdf.dropna(subset=[f"fwd_{h}d"]).groupby("date")[f"fwd_{h}d"].mean().values - cost_pct
                t_val = float(np.mean(daily_means) / (np.std(daily_means, ddof=1) / np.sqrt(len(daily_means)))) if len(daily_means) > 2 else 0.0
            else:
                mean_r, wr, t_val = 0.0, 0.0, 0.0
            row[f"net_{h}d_pct"] = round(mean_r, 3)
            row[f"wr_{h}d_pct"] = round(wr, 1)
            row[f"t_{h}d"] = round(t_val, 2)
            line += f"{h:2d}일: {mean_r:+6.2f}%(t={t_val:+5.2f}) | "
        # Also record Dev (<=2025) vs OOS (2026) for 10d and 20d
        for h in [10, 20]:
            dev_c = cdf[cdf["date"] < pd.Timestamp("2026-01-01")][f"fwd_{h}d"].dropna().values - cost_pct
            oos_c = cdf[cdf["date"] >= pd.Timestamp("2026-01-01")][f"fwd_{h}d"].dropna().values - cost_pct
            row[f"dev_{h}d_net"] = round(float(np.mean(dev_c)), 2) if len(dev_c) > 0 else 0.0
            row[f"oos_{h}d_net"] = round(float(np.mean(oos_c)), 2) if len(oos_c) > 0 else 0.0
            row[f"oos_{h}d_wr"] = round(float(np.mean(oos_c > 0) * 100.0), 1) if len(oos_c) > 0 else 0.0
        line += f"[20일 Dev:{row['dev_20d_net']:+5.2f}% / 2026 OOS:{row['oos_20d_net']:+5.2f}%(승률 {row['oos_20d_wr']:.1f}%)]"
        print(line)
        summary_rows.append(row)

    out_path = os.path.join(OUT_DIR, "stock_edge_conditions_summary.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary_rows, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
