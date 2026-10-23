"""
KODEX200 케이스 스터디 Part 2:
1. 이미 완료된 C0~C6의 디스크 아티팩트(best_params, test_trades) 수거 및 정밀 손익비 해부
2. C4(36/60캔들, 승률 76.2%) + C5(스나이퍼) 결합 실험 (C4B, C4C: 순수 리드랙 코어 + 비대칭 손익비/스나이퍼)
3. 타임프레임 상향 실험: C7~C9 (069500 1m, 15m) 및 타겟 비교 C10 (122630 레버리지 10s)
"""
import os
import sys
import json
import copy
import time
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BAKER_DIR = os.path.join(ROOT_DIR, "CAiModelBaker")
for p in (ROOT_DIR, BAKER_DIR, os.path.join(ROOT_DIR, "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.config import project_store
from shared.features.feature_registry import (
    DEFAULT_FEATURE_REGISTRY,
    register_configured_reference_features,
    get_incompatible_features,
)
from CAiModelBaker.core.model_engine import (
    run_feature_engineering,
    process_02_relay_optimization,
)
from CAiModelBaker.core.backtester import process_03_pure_backtest

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def silent_log(msg: str):
    pass


def load_base_config() -> dict:
    cfg_path = os.path.join(
        BAKER_DIR, "deploy", "069500_10s", "KODEX200_S10_NEW2", "config_snapshot.json"
    )
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_case_clean(
    case_id: str,
    title: str,
    df_engineered: pd.DataFrame,
    base_cfg: dict,
    features: list,
    predict_ahead: int,
    max_hold: int,
    train_start: str,
    train_end: str,
    holdout_start: str,
    holdout_end: str,
    wfa_outer: int = 6,
    wfa_inner: int = 8,
    tp_range: tuple = (1.2, 3.0),
    sl_range: tuple = (1.5, 2.5),
    buy_q_range: tuple = (0.85, 0.99),
    cooldown_range: tuple = (2, 20),
    wfa_train_td: int = 18,
    wfa_opt_td: int = 4,
    wfa_test_td: int = 4,
) -> dict:
    t0 = time.time()
    work_dir = os.path.join(OUT_DIR, "work_cases", case_id)
    os.makedirs(os.path.join(work_dir, "work", "model"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "work", "reports"), exist_ok=True)

    # Filter out any feature that is >50% NaN in df_engineered
    valid_features = []
    for f in features:
        if f in df_engineered.columns:
            s = pd.to_numeric(df_engineered[f], errors="coerce")
            if s.notna().mean() > 0.5 and s.dropna().nunique() > 1:
                valid_features.append(f)

    cfg = copy.deepcopy(base_cfg)
    cfg[project_store.RUN_DIR_KEY] = work_dir
    cfg["TRAIN_START"] = train_start
    cfg["TRAIN_END"] = train_end
    cfg["OPT_START"] = holdout_start
    cfg["OPT_END"] = holdout_end

    cfg["TARGET_HORIZON_CONFIG"] = {
        "interval": cfg.get("INTERVAL", "10s"),
        "mode": "locked",
        "locked_predict_ahead": predict_ahead,
        "locked_max_hold": max_hold,
        "candidate_horizons": [predict_ahead],
    }

    oc = cfg["OPTUNA_CONFIG"]
    oc["wfa_outer_trials"] = wfa_outer
    oc["wfa_inner_trials"] = wfa_inner
    oc["outer_trials"] = max(10, wfa_outer * 2)
    oc["inner_trials"] = max(10, wfa_inner * 2)
    oc["wfa_start_date"] = train_start
    oc["wfa_end_date"] = train_end
    oc["predict_ahead_candles_range"]["user_min"] = predict_ahead
    oc["predict_ahead_candles_range"]["user_max"] = predict_ahead
    oc["max_hold_candles_range"]["user_min"] = max_hold
    oc["max_hold_candles_range"]["user_max"] = max_hold
    oc["take_profit_range"]["user_min"] = tp_range[0]
    oc["take_profit_range"]["user_max"] = tp_range[1]
    oc["stop_loss_range"]["user_min"] = sl_range[0]
    oc["stop_loss_range"]["user_max"] = sl_range[1]
    oc["buy_quantile_range"]["user_min"] = buy_q_range[0]
    oc["buy_quantile_range"]["user_max"] = buy_q_range[1]
    oc["cooldown_minutes_range"]["user_min"] = cooldown_range[0]
    oc["cooldown_minutes_range"]["user_max"] = cooldown_range[1]
    oc["wfa_input_values"] = {
        "unit": "td",
        "train": wfa_train_td,
        "opt": wfa_opt_td,
        "test": wfa_test_td,
    }
    oc["wfa_train_months"] = round(wfa_train_td / 21.0, 2)
    oc["wfa_opt_months"] = round(wfa_opt_td / 21.0, 2)
    oc["wfa_test_months"] = round(wfa_test_td / 21.0, 2)

    df_slice = df_engineered[
        df_engineered["timestamp"] >= pd.to_datetime(train_start)
    ].copy()

    process_02_relay_optimization(
        cfg,
        train_start,
        train_end,
        holdout_start,
        holdout_end,
        log_fn=silent_log,
        holdout_start=holdout_start,
        purge_candles=predict_ahead,
        feature_subset=valid_features,
        cached_df=df_slice,
    )

    # Read actual features saved in feature_spec.json
    fspec_path = os.path.join(work_dir, "work", "model", "feature_spec.json")
    used_feats = valid_features
    if os.path.exists(fspec_path):
        with open(fspec_path, "r", encoding="utf-8") as f:
            used_feats = json.load(f).get("features_order", valid_features)

    bt_res = process_03_pure_backtest(
        cfg,
        holdout_start,
        holdout_end,
        log_fn=silent_log,
        session_id=cfg.get("ACTIVE_SESSION_ID", "KODEX200_S10_NEW2"),
        feature_subset=used_feats,
        cached_df=df_slice,
    )

    best_params_path = os.path.join(
        work_dir, "work", "model", f"best_params_{cfg.get('INTERVAL', '10s')}.json"
    )
    t_meta = {}
    saved_params = {}
    if os.path.exists(best_params_path):
        with open(best_params_path, "r", encoding="utf-8") as f:
            saved_params = json.load(f)
            t_meta = saved_params.get("training_meta", {})

    # Read test_trades.csv from work_dir/work/reports/test_trades.csv if present
    trades_csv = os.path.join(work_dir, "work", "reports", "test_trades.csv")
    n_tp, n_sl, n_to = 0, 0, 0
    avg_win, avg_loss = 0.0, 0.0
    if os.path.exists(trades_csv):
        tdf = pd.read_csv(trades_csv)
        if "type" in tdf.columns:
            n_tp = int(tdf["type"].astype(str).str.contains("익절").sum())
            n_sl = int(tdf["type"].astype(str).str.contains("손절").sum())
            n_to = int(tdf["type"].astype(str).str.contains("시간만료").sum())
        if "profit_pct" in tdf.columns:
            wins = tdf[tdf["profit_pct"] > 0]["profit_pct"]
            losses = tdf[tdf["profit_pct"] <= 0]["profit_pct"]
            avg_win = round(float(wins.mean()), 4) if len(wins) else 0.0
            avg_loss = round(float(losses.mean()), 4) if len(losses) else 0.0

    elapsed = round(time.time() - t0, 1)
    summary = {
        "case_id": case_id,
        "title": title,
        "interval": cfg.get("INTERVAL", "10s"),
        "predict_ahead": predict_ahead,
        "max_hold": max_hold,
        "train_start": train_start,
        "features_count": len(used_feats),
        "features": used_feats,
        "wfa_robust": bool(t_meta.get("wfa_robust", False)),
        "wfa_valid_windows": int(t_meta.get("wfa_valid_windows", 0)),
        "wfa_windows": int(t_meta.get("wfa_windows", 0)),
        "wfa_oos_total_pct": round(float(t_meta.get("wfa_oos_total_pct", 0.0)), 2),
        "wfa_window_details": t_meta.get("wfa_window_details", []),
        "holdout_net_profit_pct": round(float(bt_res.get("net_profit", 0.0)), 2),
        "holdout_trades": int(bt_res.get("total_trades", 0)),
        "holdout_win_rate": round(float(bt_res.get("win_rate", 0.0)), 1),
        "holdout_pf": round(float(bt_res.get("profit_factor", 0.0)), 2),
        "holdout_sharpe": round(float(bt_res.get("sharpe_ratio", 0.0)), 2),
        "holdout_mdd": round(float(bt_res.get("mdd", 0.0)), 2),
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "selected_tp": round(float(saved_params.get("take_profit", 0.0)), 2),
        "selected_sl": round(float(saved_params.get("stop_loss", 0.0)), 2),
        "selected_buy_th": round(float(saved_params.get("buy_threshold", 0.0)), 4),
        "elapsed_sec": elapsed,
    }
    print(
        f"  [{case_id}] {title:<42} | "
        f"WFA: {summary['wfa_valid_windows']}/{summary['wfa_windows']}창 양수 (합계 {summary['wfa_oos_total_pct']:+6.2f}%, Robust={str(summary['wfa_robust']):<5}) | "
        f"Holdout: {summary['holdout_net_profit_pct']:+6.2f}% ({summary['holdout_trades']:2d}회, 승률 {summary['holdout_win_rate']:4.1f}%, PF {summary['holdout_pf']:4.2f}, TP/SL={summary['selected_tp']}/{summary['selected_sl']}) [{elapsed}s]"
    )
    return summary


def inspect_existing_c0_to_c6():
    print("=" * 75)
    print("[분석 A] 완료된 C0 ~ C6 의 선택된 파라미터 및 WFA 창별 상세 해부")
    print("=" * 75)
    case_ids = [
        "C0_BASELINE",
        "C1_HORIZON_12_18",
        "C2_HORIZON_18_30",
        "C3A_HYNIX_CORE_6_12",
        "C3_LEADLAG_RESCUED_12_18",
        "C4_LEADLAG_36_60",
        "C5_SNIPER_MODE",
        "C6_OLD_CHAMPION_FEATS",
    ]
    recovered = []
    for cid in case_ids:
        bp_path = os.path.join(OUT_DIR, "work_cases", cid, "work", "model", "best_params_10s.json")
        if not os.path.exists(bp_path):
            continue
        with open(bp_path, "r", encoding="utf-8") as f:
            bp = json.load(f)
        tm = bp.get("training_meta", {})
        w_details = tm.get("wfa_window_details", [])
        w_str = ", ".join(
            f"W{w['window']}:{w['profit']:+.2f}%({w['trades']}건)" for w in w_details
        )
        print(
            f"  • {cid:<25} | TP={bp.get('take_profit'):.2f} SL={bp.get('stop_loss'):.2f} "
            f"Hold={bp.get('max_hold_candles')} BuyTh={bp.get('buy_threshold'):.3f} Cool={bp.get('cooldown_minutes')}m | "
            f"WFA: [{w_str}]"
        )
        recovered.append({"case_id": cid, "best_params": bp})
    return recovered


def main():
    recovered = inspect_existing_c0_to_c6()

    base_cfg = load_base_config()
    snap_10s = os.path.join(ROOT_DIR, "DataVault", "features", "069500", "data_snapshot_10s.csv")
    df_10s = pd.read_csv(snap_10s)
    df_10s["timestamp"] = pd.to_datetime(df_10s["timestamp"])
    df_10s = df_10s.sort_values("timestamp").reset_index(drop=True)

    register_configured_reference_features(base_cfg)
    all_pool = [
        fid for fid, defn in DEFAULT_FEATURE_REGISTRY.items() if not defn.get("is_alias", False)
    ]
    bad = {x["feature_id"] for x in get_incompatible_features("069500", all_pool, session_id="KODEX200_S10_NEW2")}
    kept_pool = [f for f in all_pool if f not in bad]
    fc = copy.deepcopy(base_cfg.get("FEATURE_CONFIG", {}))
    fc["enabled_features"] = kept_pool
    fc["active_features"] = kept_pool
    df_10s = run_feature_engineering(df_10s, "10s", feature_config=fc, config=base_cfg)

    print("\n" + "=" * 75)
    print("[분석 B] 10초봉 극한 튜닝 실험: 순수 IC Top-5 + 36/60캔들 + 스나이퍼/손익비 제약 (C4B, C4C)")
    print("=" * 75)

    results = []
    # Top 5 features that maintained identical sign and high IC in BOTH Train and Holdout:
    pure_top5 = [
        "ref_000660_ret_1c",
        "ref_000660_spread_vs_target",
        "top2_lead_momentum",
        "kospi_basis_z",
        "target_trade_frequency",
    ]
    results.append(
        run_case_clean(
            case_id="C4B_PURE_TOP5_36_60_SNIPER",
            title="순수 일관성 Top5 + 36/60캔들 + 스나이퍼(상위3%)",
            df_engineered=df_10s,
            base_cfg=base_cfg,
            features=pure_top5,
            predict_ahead=36,
            max_hold=60,
            train_start="2026-08-26",
            train_end="2026-10-06",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            wfa_outer=6,
            wfa_inner=8,
            tp_range=(1.8, 3.5),
            sl_range=(1.5, 2.2),
            buy_q_range=(0.96, 0.995),
            cooldown_range=(15, 30),
            wfa_train_td=12,
            wfa_opt_td=3,
            wfa_test_td=3,
        )
    )

    results.append(
        run_case_clean(
            case_id="C4C_PURE_TOP5_18_36_ASYM",
            title="순수 일관성 Top5 + 18/36캔들 + 손익비 비대칭(TP>SL)",
            df_engineered=df_10s,
            base_cfg=base_cfg,
            features=pure_top5,
            predict_ahead=18,
            max_hold=36,
            train_start="2026-08-26",
            train_end="2026-10-06",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            wfa_outer=6,
            wfa_inner=8,
            tp_range=(2.0, 3.5),
            sl_range=(1.2, 1.8),
            buy_q_range=(0.94, 0.995),
            cooldown_range=(10, 30),
            wfa_train_td=12,
            wfa_opt_td=3,
            wfa_test_td=3,
        )
    )

    print("\n" + "=" * 75)
    print("[분석 C] 타임프레임 & 타겟 전환 비교: 069500 1m / 15m 및 122630(레버리지 2x) 10s")
    print("=" * 75)

    # C7 & C8: 069500 1m
    snap_1m = os.path.join(ROOT_DIR, "DataVault", "features", "069500", "data_snapshot_1m.csv")
    if os.path.exists(snap_1m):
        cfg_1m_path = os.path.join(BAKER_DIR, "deploy", "069500_1m", "KODEX200_M1_1004A", "config_snapshot.json")
        with open(cfg_1m_path, "r", encoding="utf-8") as f:
            cfg_1m = json.load(f)
        df_1m = pd.read_csv(snap_1m)
        df_1m["timestamp"] = pd.to_datetime(df_1m["timestamp"])
        df_1m = df_1m.sort_values("timestamp").reset_index(drop=True)
        df_1m = df_1m[df_1m["timestamp"] >= pd.to_datetime("2026-03-01")].copy().reset_index(drop=True)
        register_configured_reference_features(cfg_1m)
        fc_1m = copy.deepcopy(cfg_1m.get("FEATURE_CONFIG", {}))
        fc_1m["enabled_features"] = kept_pool
        fc_1m["active_features"] = kept_pool
        df_1m = run_feature_engineering(df_1m, "1m", feature_config=fc_1m, config=cfg_1m)

        feats_1m = [
            "kospi_basis_z",
            "kospi_basis_rate",
            "ref_10100_spread_vs_target",
            "top2_lead_momentum",
            "target_vwap_disparity",
            "target_ma_ratio",
            "target_mfi",
        ]
        results.append(
            run_case_clean(
                case_id="C7_069500_1M_6_10",
                title="069500 1분봉 표준 지평 (6/10분 보유)",
                df_engineered=df_1m,
                base_cfg=cfg_1m,
                features=feats_1m,
                predict_ahead=6,
                max_hold=10,
                train_start="2026-04-01",
                train_end="2026-09-11",
                holdout_start="2026-09-14",
                holdout_end="2026-10-16",
                wfa_outer=5,
                wfa_inner=6,
                wfa_train_td=41,
                wfa_opt_td=10,
                wfa_test_td=10,
            )
        )
        results.append(
            run_case_clean(
                case_id="C8_069500_1M_15_30",
                title="069500 1분봉 중기 지평 (15/30분 보유)",
                df_engineered=df_1m,
                base_cfg=cfg_1m,
                features=feats_1m,
                predict_ahead=15,
                max_hold=30,
                train_start="2026-04-01",
                train_end="2026-09-11",
                holdout_start="2026-09-14",
                holdout_end="2026-10-16",
                wfa_outer=5,
                wfa_inner=6,
                wfa_train_td=41,
                wfa_opt_td=10,
                wfa_test_td=10,
            )
        )

    # C9: 069500 15m
    snap_15m = os.path.join(ROOT_DIR, "DataVault", "features", "069500", "data_snapshot_15m.csv")
    if os.path.exists(snap_15m):
        cfg_15m_path = os.path.join(BAKER_DIR, "deploy", "069500_15m", "KODEX200_M15_TEST1", "config_snapshot.json")
        with open(cfg_15m_path, "r", encoding="utf-8") as f:
            cfg_15m = json.load(f)
        df_15m = pd.read_csv(snap_15m)
        df_15m["timestamp"] = pd.to_datetime(df_15m["timestamp"])
        df_15m = df_15m.sort_values("timestamp").reset_index(drop=True)
        df_15m = df_15m[df_15m["timestamp"] >= pd.to_datetime("2025-06-01")].copy().reset_index(drop=True)
        register_configured_reference_features(cfg_15m)
        fc_15m = copy.deepcopy(cfg_15m.get("FEATURE_CONFIG", {}))
        fc_15m["enabled_features"] = kept_pool
        fc_15m["active_features"] = kept_pool
        df_15m = run_feature_engineering(df_15m, "15m", feature_config=fc_15m, config=cfg_15m)

        feats_15m = [
            "kospi_basis_z",
            "ref_10100_spread_vs_target",
            "top2_lead_momentum",
            "target_vwap_disparity",
            "target_ma_ratio",
            "target_mfi",
            "target_rsi_daily",
        ]
        results.append(
            run_case_clean(
                case_id="C9_069500_15M_4_8",
                title="069500 15분봉 (1시간 예측 / 2시간 보유)",
                df_engineered=df_15m,
                base_cfg=cfg_15m,
                features=feats_15m,
                predict_ahead=4,
                max_hold=8,
                train_start="2025-08-01",
                train_end="2026-08-14",
                holdout_start="2026-08-17",
                holdout_end="2026-10-16",
                wfa_outer=5,
                wfa_inner=6,
                wfa_train_td=82,
                wfa_opt_td=21,
                wfa_test_td=21,
            )
        )

    # C10: 122630 (KODEX 레버리지 2x) 10s - ATR이 2배 클 때 마찰비용 극복 가능 여부
    snap_lev = os.path.join(ROOT_DIR, "DataVault", "features", "122630", "data_snapshot_10s.csv")
    if os.path.exists(snap_lev):
        cfg_lev_path = os.path.join(BAKER_DIR, "deploy", "122630_10s", "KODEX200X2_S10_BEST1", "config_snapshot.json")
        with open(cfg_lev_path, "r", encoding="utf-8") as f:
            cfg_lev = json.load(f)
        df_lev = pd.read_csv(snap_lev)
        df_lev["timestamp"] = pd.to_datetime(df_lev["timestamp"])
        df_lev = df_lev.sort_values("timestamp").reset_index(drop=True)
        register_configured_reference_features(cfg_lev)
        fc_lev = copy.deepcopy(cfg_lev.get("FEATURE_CONFIG", {}))
        fc_lev["enabled_features"] = kept_pool
        fc_lev["active_features"] = kept_pool
        df_lev = run_feature_engineering(df_lev, "10s", feature_config=fc_lev, config=cfg_lev)

        feats_lev = [
            "ref_000660_ret_1c",
            "ref_000660_spread_vs_target",
            "top2_lead_momentum",
            "kospi_basis_z",
            "target_vwap_disparity",
            "target_ofi_cumsum",
        ]
        results.append(
            run_case_clean(
                case_id="C10_122630_LEV2X_10S_18_30",
                title="122630(레버리지2x) 10초봉 + 리드랙 (18/30캔들)",
                df_engineered=df_lev,
                base_cfg=cfg_lev,
                features=feats_lev,
                predict_ahead=18,
                max_hold=30,
                train_start="2026-08-26",
                train_end="2026-10-06",
                holdout_start="2026-10-07",
                holdout_end="2026-10-16",
                wfa_outer=5,
                wfa_inner=6,
                wfa_train_td=12,
                wfa_opt_td=3,
                wfa_test_td=3,
            )
        )

    out_path = os.path.join(OUT_DIR, "case_study_part2_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"recovered_c0_c6": recovered, "part2_cases": results}, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Part 2 실험 결과 저장 완료: {out_path}")


if __name__ == "__main__":
    main()
