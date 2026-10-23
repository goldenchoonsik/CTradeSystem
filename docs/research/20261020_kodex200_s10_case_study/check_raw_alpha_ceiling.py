"""
069500 10초봉의 '이론적 알파 상한선(Raw Signal Gross vs Net Return Ceiling)' 검정
질문: LightGBM이나 Optuna가 못 찾는 것인가, 아니면 가장 강력한 선행 지표(SK하이닉스 리드랙 + 선물 베이시스)의
최상위 0.5% ~ 5% 타점조차 평균 총수익(Gross Return)이 왕복 마찰비용(0.030% = 3.0bp)을 넘지 못하는가?
"""
import os
import sys
import json
import numpy as np
import pandas as pd

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BAKER_DIR = os.path.join(ROOT_DIR, "CAiModelBaker")
for p in (ROOT_DIR, BAKER_DIR, os.path.join(ROOT_DIR, "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.core.market_utils import session_forward_return, decision_point_mask
from shared.features.feature_registry import (
    DEFAULT_FEATURE_REGISTRY,
    register_configured_reference_features,
    get_incompatible_features,
)
from CAiModelBaker.core.model_engine import run_feature_engineering

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    cfg_path = os.path.join(
        BAKER_DIR, "deploy", "069500_10s", "KODEX200_S10_NEW2", "config_snapshot.json"
    )
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    snap_10s = os.path.join(ROOT_DIR, "DataVault", "features", "069500", "data_snapshot_10s.csv")
    df = pd.read_csv(snap_10s)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = df[df["timestamp"] >= pd.to_datetime("2026-08-26")].copy().reset_index(drop=True)

    register_configured_reference_features(cfg)
    all_pool = [
        fid for fid, defn in DEFAULT_FEATURE_REGISTRY.items() if not defn.get("is_alias", False)
    ]
    bad = {x["feature_id"] for x in get_incompatible_features("069500", all_pool, session_id="KODEX200_S10_NEW2")}
    kept_pool = [f for f in all_pool if f not in bad]
    fc = dict(cfg.get("FEATURE_CONFIG", {}))
    fc["enabled_features"] = kept_pool
    fc["active_features"] = kept_pool
    df = run_feature_engineering(df, "10s", feature_config=fc, config=cfg)

    close = df["target_close"].to_numpy(dtype=float)
    ts = df["timestamp"]
    dmask = decision_point_mask(ts, True)

    # Composite Z-score of the 5 best consistent features (with their true IC signs!)
    # Positive IC: ref_000660_ret_1c (+0.044), ref_000660_spread_vs_target (+0.036), top2_lead_momentum (+0.028)
    # Negative IC: kospi_basis_z (-0.030), target_trade_frequency (-0.030)
    def zscore(s):
        v = pd.to_numeric(s, errors="coerce")
        return (v - v.mean()) / (v.std() + 1e-9)

    df["composite_alpha"] = (
        +1.0 * zscore(df["ref_000660_ret_1c"])
        + 1.0 * zscore(df["ref_000660_spread_vs_target"])
        + 1.0 * zscore(df["top2_lead_momentum"])
        - 1.0 * zscore(df["kospi_basis_z"])
        - 1.0 * zscore(df["target_trade_frequency"])
    )

    is_train = (df["timestamp"] < pd.to_datetime("2026-10-07")).to_numpy()
    is_hold = (df["timestamp"] >= pd.to_datetime("2026-10-07")).to_numpy()

    print("=" * 95)
    print("[핵심 검정] 069500 10초봉 최강 선행 피처(Composite & 단일)의 상위 분위수별 Gross 수익률 vs 마찰비용(0.030%)")
    print("=" * 95)
    friction = 0.030  # 3.0 bps round-trip (fee 0.01% + slippage 0.02%)

    results = []
    for sig_col in ["ref_000660_ret_1c", "ref_000660_spread_vs_target", "composite_alpha"]:
        print(f"\n▶ 신호: {sig_col}")
        print(f"  {'지평':<8} | {'상위분위':<8} | {'구간':<7} | {'표본수':>6} | {'Gross평균(%)':>12} | {'Net평균(-0.03%)':>15} | {'Net승률(%)':>10}")
        print("  " + "-" * 80)
        s_val = pd.to_numeric(df[sig_col], errors="coerce").to_numpy()
        for h in [6, 12, 18, 36, 60]:
            fwd = session_forward_return(close, ts, h, True) * 100.0
            for q in [0.90, 0.95, 0.98, 0.99, 0.995]:
                # Threshold fitted on Train only!
                tr_valid = dmask & is_train & ~np.isnan(s_val) & ~np.isnan(fwd)
                thresh = float(np.quantile(s_val[tr_valid], q))
                for period_name, pmask in [("Train", is_train), ("Holdout", is_hold)]:
                    m = dmask & pmask & ~np.isnan(s_val) & ~np.isnan(fwd) & (s_val >= thresh)
                    n_ev = int(m.sum())
                    if n_ev == 0:
                        continue
                    gross_mean = float(np.mean(fwd[m]))
                    net_mean = gross_mean - friction
                    net_win = float(np.mean((fwd[m] - friction) > 0) * 100.0)
                    row = {
                        "signal": sig_col,
                        "horizon_candles": h,
                        "quantile": q,
                        "period": period_name,
                        "n_events": n_ev,
                        "gross_mean_pct": round(gross_mean, 4),
                        "net_mean_pct": round(net_mean, 4),
                        "net_win_pct": round(net_win, 1),
                    }
                    results.append(row)
                    if q in (0.95, 0.99, 0.995) and h in (6, 18, 36, 60):
                        print(
                            f"  {f'{h}c({h//6}m)':<8} | {f'Top {(1-q)*100:.1f}%':<8} | {period_name:<7} | {n_ev:6d} | "
                            f"{gross_mean:+11.4f}% | {net_mean:+14.4f}% | {net_win:9.1f}%"
                        )

    out_file = os.path.join(OUT_DIR, "raw_alpha_ceiling.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
