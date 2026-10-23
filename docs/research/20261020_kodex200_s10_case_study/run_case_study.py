"""
KODEX200_S10_NEW2 (069500 10s) 및 대안 케이스별 정밀 실험 스크립트
실행 위치: 저장소 루트에서 `.venv/bin/python docs/research/20261020_kodex200_s10_case_study/run_case_study.py`
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
from shared.core.market_utils import get_target_symbol, detect_market_type, session_forward_return, decision_point_mask
from shared.features.feature_registry import (
    DEFAULT_FEATURE_REGISTRY,
    register_configured_reference_features,
    get_incompatible_features,
)
from shared.features.feature_health import find_dead_features, find_unstable_availability, find_insufficient_coverage
from CAiModelBaker.core.model_engine import (
    run_feature_engineering,
    process_02_relay_optimization,
)
from CAiModelBaker.core.backtester import process_03_pure_backtest
from CAiModelBaker.core.feature_tuner import calculate_ic
from CAiModelBaker.core.physics_constraint import calc_mean_atr_pct, apply_physics_constraints_to_config, slice_pre_holdout

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def silent_log(msg: str):
    pass


def load_base_config() -> dict:
    cfg_path = os.path.join(
        BAKER_DIR, "deploy", "069500_10s", "KODEX200_S10_NEW2", "config_snapshot.json"
    )
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)


def analyze_fundamental_signal_to_noise(df_full: pd.DataFrame, cfg: dict) -> dict:
    """
    파트 1: 069500 10초봉의 물리적 마찰비용 대비 변동성(SNR) 및 지평별(6, 12, 18, 36, 60캔들) 진짜 IC 전수 측정
    """
    print("\n" + "=" * 75)
    print("[분석 1] 069500 10초봉 지평별(Horizon) 물리적 마찰 한계 및 전 지표 IC 구조 분석")
    print("=" * 75)

    holdout_start = "2026-10-07"
    df_pre = df_full[df_full["timestamp"] < pd.to_datetime(holdout_start)].copy()
    df_Aug26 = df_pre[df_pre["timestamp"] >= pd.to_datetime("2026-08-26")].copy()
    df_ho = df_full[df_full["timestamp"] >= pd.to_datetime(holdout_start)].copy()

    close = df_Aug26["target_close"].to_numpy(dtype=float)
    high = df_Aug26["target_high"].to_numpy(dtype=float)
    low = df_Aug26["target_low"].to_numpy(dtype=float)
    ts = df_Aug26["timestamp"]
    dmask = decision_point_mask(ts, True)

    horizons = [6, 12, 18, 36, 60]  # 1m, 2m, 3m, 6m, 10m
    horizon_stats = []
    friction_pct = (float(cfg.get("FEE_UPBIT", 5e-5)) * 2 + float(cfg.get("SLIPPAGE", 0.0001)) * 2) * 100.0
    # Note: KRX roundtrip friction in CTradeSystem is ~0.030%

    for h in horizons:
        fwd_ret = session_forward_return(close, ts, h, True) * 100.0
        valid = dmask & ~np.isnan(fwd_ret)
        r = fwd_ret[valid]

        # MFE / MAE over h candles
        mfe_list = np.zeros(len(close))
        mae_list = np.zeros(len(close))
        for step in range(1, h + 1):
            shifted_h = np.roll(high, -step)
            shifted_l = np.roll(low, -step)
            mfe_list = np.maximum(mfe_list, (shifted_h - close) / close * 100.0)
            mae_list = np.minimum(mae_list, (shifted_l - close) / close * 100.0)
        mfe_v = mfe_list[valid]
        mae_v = np.abs(mae_list[valid])

        prob_beat_cost_close = float(np.mean(r > 0.030) * 100.0)
        prob_mfe_gt_010 = float(np.mean(mfe_v >= 0.10) * 100.0)
        prob_mfe_gt_015 = float(np.mean(mfe_v >= 0.15) * 100.0)

        stat = {
            "horizon_candles": h,
            "horizon_mins": round(h * 10 / 60.0, 1),
            "mean_ret_pct": round(float(np.mean(r)), 4),
            "std_ret_pct": round(float(np.std(r)), 4),
            "median_abs_ret_pct": round(float(np.median(np.abs(r))), 4),
            "mfe_p50_pct": round(float(np.median(mfe_v)), 4),
            "mfe_p75_pct": round(float(np.percentile(mfe_v, 75)), 4),
            "mae_p50_pct": round(float(np.median(mae_v)), 4),
            "prob_close_beats_3bp": round(prob_beat_cost_close, 2),
            "prob_mfe_reaches_10bp": round(prob_mfe_gt_010, 2),
            "prob_mfe_reaches_15bp": round(prob_mfe_gt_015, 2),
        }
        horizon_stats.append(stat)
        print(
            f"  • 지평 {h:2d}캔들 ({stat['horizon_mins']:4.1f}분) | "
            f"|ΔP| 중앙값: {stat['median_abs_ret_pct']:.3f}% (마찰 0.030%) | "
            f"MFE 중앙값: {stat['mfe_p50_pct']:.3f}% (P75: {stat['mfe_p75_pct']:.3f}%) | "
            f"종가>비용 확률: {stat['prob_close_beats_3bp']:.1f}% | "
            f"MFE>=0.15% 도달률: {stat['prob_mfe_reaches_15bp']:.1f}%"
        )

    # Check IC of top features across horizons (in 2026-08-26 ~ 2026-10-06 vs Holdout 2026-10-07 ~ 2026-10-16)
    candidate_feats = [
        "ref_000660_ret_1c",
        "ref_000660_ret_5c",
        "ref_000660_spread_vs_target",
        "ref_000660_intensity",
        "top2_lead_momentum",
        "semi_duo_lead_delta",
        "kospi_basis_z",
        "kospi_basis_rate",
        "ref_10100_spread_vs_target",
        "ref_10100_ret_1c",
        "ref_11100_spread_vs_target",
        "ref_15000_spread_vs_target",
        "ref_us_tech_100_spread_vs_target",
        "target_vwap_disparity",
        "target_ofi_cumsum",
        "target_trade_bid_ratio",
        "target_trade_frequency",
        "target_mfi",
        "target_ma_ratio",
        "target_macd_hist",
        "target_rsi_curr",
        "target_bb_pctb",
    ]
    candidate_feats = [f for f in candidate_feats if f in df_Aug26.columns]

    print("\n[분석 1-B] 주요 피처의 학습구간(08/26~10/06) vs 홀드아웃(10/07~10/16) IC 비교 (지평 6 vs 18 vs 36)")
    ic_comparison = []
    for h in [6, 18, 36]:
        df_Aug26[f"_fwd_{h}"] = session_forward_return(
            df_Aug26["target_close"].to_numpy(), df_Aug26["timestamp"], h, True
        )
        df_ho[f"_fwd_{h}"] = session_forward_return(
            df_ho["target_close"].to_numpy(), df_ho["timestamp"], h, True
        )

    print(f"  {'Feature':<34} | {'IC(6c,Train)':>12} | {'IC(6c,Hold)':>11} | {'IC(18c,Train)':>13} | {'IC(18c,Hold)':>12} | {'IC(36c,Train)':>13}")
    print("  " + "-" * 105)
    for fid in candidate_feats:
        ic_6_tr = calculate_ic(df_Aug26, fid, "_fwd_6")
        ic_6_ho = calculate_ic(df_ho, fid, "_fwd_6")
        ic_18_tr = calculate_ic(df_Aug26, fid, "_fwd_18")
        ic_18_ho = calculate_ic(df_ho, fid, "_fwd_18")
        ic_36_tr = calculate_ic(df_Aug26, fid, "_fwd_36")
        row = {
            "feature": fid,
            "ic_6_train": round(float(ic_6_tr), 4),
            "ic_6_holdout": round(float(ic_6_ho), 4),
            "ic_18_train": round(float(ic_18_tr), 4),
            "ic_18_holdout": round(float(ic_18_ho), 4),
            "ic_36_train": round(float(ic_36_tr), 4),
        }
        ic_comparison.append(row)
        print(
            f"  {fid:<34} | {ic_6_tr:+12.4f} | {ic_6_ho:+11.4f} | {ic_18_tr:+13.4f} | {ic_18_ho:+12.4f} | {ic_36_tr:+13.4f}"
        )

    return {"horizon_stats": horizon_stats, "ic_comparison": ic_comparison}


def run_single_case(
    case_id: str,
    title: str,
    df_engineered: pd.DataFrame,
    base_cfg: dict,
    features: list,
    predict_ahead: int,
    max_hold: int,
    train_start: str,
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
    """
    단일 케이스를 실제 CAiModelBaker 엔진(process_02_relay_optimization + process_03_pure_backtest)으로 구동하여
    WFA 5-Window 성과 및 Holdout 성과를 정밀 측정
    """
    t0 = time.time()
    work_dir = os.path.join(OUT_DIR, "work_cases", case_id)
    os.makedirs(os.path.join(work_dir, "work", "model"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "work", "reports"), exist_ok=True)

    cfg = copy.deepcopy(base_cfg)
    cfg[project_store.RUN_DIR_KEY] = work_dir
    cfg["TRAIN_START"] = train_start
    cfg["TRAIN_END"] = "2026-10-06"
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
    oc["wfa_end_date"] = "2026-10-06"
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

    opt_res = process_02_relay_optimization(
        cfg,
        train_start,
        "2026-10-06",
        holdout_start,
        holdout_end,
        log_fn=silent_log,
        holdout_start=holdout_start,
        purge_candles=predict_ahead,
        feature_subset=features,
        cached_df=df_slice,
    )

    bt_res = process_03_pure_backtest(
        cfg,
        holdout_start,
        holdout_end,
        log_fn=silent_log,
        session_id=cfg.get("ACTIVE_SESSION_ID", "KODEX200_S10_NEW2"),
        feature_subset=features,
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

    # Count exit types in holdout trade logs
    t_logs = bt_res.get("trade_logs", [])
    n_tp = sum(1 for x in t_logs if "익절" in str(x.get("type", "")))
    n_sl = sum(1 for x in t_logs if "손절" in str(x.get("type", "")))
    n_to = sum(1 for x in t_logs if "시간만료" in str(x.get("type", "")))

    elapsed = round(time.time() - t0, 1)
    summary = {
        "case_id": case_id,
        "title": title,
        "interval": cfg.get("INTERVAL", "10s"),
        "predict_ahead": predict_ahead,
        "max_hold": max_hold,
        "train_start": train_start,
        "features_count": len(features),
        "features": features,
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
        "holdout_tp_count": n_tp,
        "holdout_sl_count": n_sl,
        "holdout_timeout_count": n_to,
        "holdout_timeout_ratio_pct": round(n_to / max(1, len(t_logs)) * 100.0, 1),
        "selected_tp": round(float(saved_params.get("take_profit", 0.0)), 2),
        "selected_sl": round(float(saved_params.get("stop_loss", 0.0)), 2),
        "selected_buy_th": round(float(saved_params.get("buy_threshold", 0.0)), 4),
        "elapsed_sec": elapsed,
    }
    print(
        f"  [{case_id}] {title:<42} | "
        f"WFA: {summary['wfa_valid_windows']}/{summary['wfa_windows']}창 양수 (합계 {summary['wfa_oos_total_pct']:+6.2f}%, Robust={str(summary['wfa_robust']):<5}) | "
        f"Holdout: {summary['holdout_net_profit_pct']:+6.2f}% ({summary['holdout_trades']:2d}회, 승률 {summary['holdout_win_rate']:4.1f}%, PF {summary['holdout_pf']:4.2f}, 만료율 {summary['holdout_timeout_ratio_pct']:4.1f}%) [{elapsed}s]"
    )
    return summary


def main():
    base_cfg = load_base_config()
    snap_10s = os.path.join(ROOT_DIR, "DataVault", "features", "069500", "data_snapshot_10s.csv")
    print(f"Loading 10s snapshot: {snap_10s}")
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
    # Ensure semi_duo_lead_delta and top2_lead_momentum are computed
    df_10s = run_feature_engineering(df_10s, "10s", feature_config=fc, config=base_cfg)
    print(f"Engineered 10s dataframe: {len(df_10s):,} rows, {len(df_10s.columns)} columns")

    snr_report = analyze_fundamental_signal_to_noise(df_10s, base_cfg)

    print("\n" + "=" * 75)
    print("[분석 2] 069500 10초봉(10s) 가설별 케이스 스터디 (C0 ~ C7)")
    print("=" * 75)

    cases_results = []

    # C0: 현재 설정 그대로 재현 (Horizon 6/6, 후행 오실레이터 중심, 07-27 시작, WFA 2x2)
    cases_results.append(
        run_single_case(
            case_id="C0_BASELINE",
            title="현재 설정 재현 (6/6캔들, 후행지표, WFA 2x2)",
            df_engineered=df_10s,
            base_cfg=base_cfg,
            features=["target_mfi", "target_ma_ratio", "target_macd_hist", "target_trade_frequency", "kospi_basis_rate"],
            predict_ahead=6,
            max_hold=6,
            train_start="2026-07-27",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            wfa_outer=2,
            wfa_inner=2,
        )
    )

    # C1: 사정거리 물리 표준 복원 (6/6 -> 12/18캔들 = 2분 예측 / 3분 보유)
    cases_results.append(
        run_single_case(
            case_id="C1_HORIZON_12_18",
            title="사정거리 물리표준 복원 (12/18캔들, 동일피처)",
            df_engineered=df_10s,
            base_cfg=base_cfg,
            features=["target_mfi", "target_ma_ratio", "target_macd_hist", "target_trade_frequency", "kospi_basis_rate"],
            predict_ahead=12,
            max_hold=18,
            train_start="2026-07-27",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            wfa_outer=5,
            wfa_inner=6,
        )
    )

    # C2: 사정거리 장기 확장 (18/30캔들 = 3분 예측 / 5분 보유)
    cases_results.append(
        run_single_case(
            case_id="C2_HORIZON_18_30",
            title="사정거리 3분/5분 확장 (18/30캔들, 동일피처)",
            df_engineered=df_10s,
            base_cfg=base_cfg,
            features=["target_mfi", "target_ma_ratio", "target_macd_hist", "target_trade_frequency", "kospi_basis_rate"],
            predict_ahead=18,
            max_hold=30,
            train_start="2026-07-27",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            wfa_outer=5,
            wfa_inner=6,
        )
    )

    # C3A: 08-26 시작일 정렬로 SK하이닉스(000660) 선행 피처 구제 + 6/12캔들 (1분 예측 / 2분 보유)
    hynix_core_feats = [
        f for f in [
            "ref_000660_ret_1c",
            "ref_000660_spread_vs_target",
            "ref_000660_intensity",
            "ref_000660_ret_5c",
            "top2_lead_momentum",
            "kospi_basis_rate",
            "target_trade_frequency",
        ] if f in df_10s.columns
    ]
    cases_results.append(
        run_single_case(
            case_id="C3A_HYNIX_CORE_6_12",
            title="SK하이닉스 선행 코어 복원 (08-26~, 6/12캔들)",
            df_engineered=df_10s,
            base_cfg=base_cfg,
            features=hynix_core_feats,
            predict_ahead=6,
            max_hold=12,
            train_start="2026-08-26",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            wfa_outer=6,
            wfa_inner=8,
            wfa_train_td=12,
            wfa_opt_td=3,
            wfa_test_td=3,
        )
    )

    # C3: 08-26 시작일 정렬로 SK하이닉스(000660) & 대장주 선행 피처 구제 + 순수 미시구조/리드랙 조합 (12/18캔들)
    lead_lag_feats = [
        f for f in [
            "ref_000660_ret_1c",
            "ref_000660_spread_vs_target",
            "top2_lead_momentum",
            "semi_duo_lead_delta",
            "ref_10100_spread_vs_target",
            "kospi_basis_z",
            "target_vwap_disparity",
            "target_ofi_cumsum",
        ] if f in df_10s.columns
    ]
    cases_results.append(
        run_single_case(
            case_id="C3_LEADLAG_RESCUED_12_18",
            title="대장주/선물 리드랙 복원 (08-26~, 12/18캔들)",
            df_engineered=df_10s,
            base_cfg=base_cfg,
            features=lead_lag_feats,
            predict_ahead=12,
            max_hold=18,
            train_start="2026-08-26",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            wfa_outer=6,
            wfa_inner=8,
            wfa_train_td=12,
            wfa_opt_td=3,
            wfa_test_td=3,
        )
    )

    # C4: 대장주/선물 리드랙 + 36/60캔들 (6분 예측 / 10분 보유, 마이크로 세션 추세)
    cases_results.append(
        run_single_case(
            case_id="C4_LEADLAG_36_60",
            title="대장주/선물 리드랙 + 6분/10분 지평 (36/60캔들)",
            df_engineered=df_10s,
            base_cfg=base_cfg,
            features=lead_lag_feats,
            predict_ahead=36,
            max_hold=60,
            train_start="2026-08-26",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            wfa_outer=6,
            wfa_inner=8,
            wfa_train_td=12,
            wfa_opt_td=3,
            wfa_test_td=3,
        )
    )

    # C5: 극단적 스나이퍼 진입 (상위 1~5% 확률만 진입, 쿨다운 15~30분, 18/30캔들)
    cases_results.append(
        run_single_case(
            case_id="C5_SNIPER_MODE",
            title="초고확률 스나이퍼 (상위 1~5% 진입, 18/30캔들)",
            df_engineered=df_10s,
            base_cfg=base_cfg,
            features=lead_lag_feats,
            predict_ahead=18,
            max_hold=30,
            train_start="2026-08-26",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            wfa_outer=6,
            wfa_inner=8,
            buy_q_range=(0.95, 0.995),
            cooldown_range=(15, 30),
            wfa_train_td=12,
            wfa_opt_td=3,
            wfa_test_td=3,
        )
    )

    # C6: 과거 챔피언(KODEX200_S10_BEST260921B) 피처셋을 현재 5-Window WFA에 그대로 넣었을 때 검증
    champ_feats = [
        f for f in [
            "target_vwap_disparity",
            "target_vwap_slope",
            "target_large_trade_density",
            "target_is_auction",
            "target_hour_cos",
            "krx_session_time",
        ] if f in df_10s.columns
    ]
    cases_results.append(
        run_single_case(
            case_id="C6_OLD_CHAMPION_FEATS",
            title="구 챔피언(BEST260921B) 피처셋 WFA 재검증 (12/18)",
            df_engineered=df_10s,
            base_cfg=base_cfg,
            features=champ_feats,
            predict_ahead=12,
            max_hold=18,
            train_start="2026-07-27",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            wfa_outer=5,
            wfa_inner=6,
        )
    )

    # Now test 1m and 15m intervals for 069500!
    print("\n" + "=" * 75)
    print("[분석 3] 타임프레임 상향 실험: 069500 1분봉(1m) 및 15분봉(15m)")
    print("=" * 75)

    snap_1m = os.path.join(ROOT_DIR, "DataVault", "features", "069500", "data_snapshot_1m.csv")
    if os.path.exists(snap_1m):
        cfg_1m_path = os.path.join(BAKER_DIR, "deploy", "069500_1m", "KODEX200_M1_1004A", "config_snapshot.json")
        with open(cfg_1m_path, "r", encoding="utf-8") as f:
            cfg_1m = json.load(f)
        df_1m = pd.read_csv(snap_1m)
        df_1m["timestamp"] = pd.to_datetime(df_1m["timestamp"])
        df_1m = df_1m.sort_values("timestamp").reset_index(drop=True)
        df_1m = df_1m[df_1m["timestamp"] >= pd.to_datetime("2026-01-01")].copy().reset_index(drop=True)
        register_configured_reference_features(cfg_1m)
        fc_1m = copy.deepcopy(cfg_1m.get("FEATURE_CONFIG", {}))
        fc_1m["enabled_features"] = kept_pool
        fc_1m["active_features"] = kept_pool
        df_1m = run_feature_engineering(df_1m, "1m", feature_config=fc_1m, config=cfg_1m)

        feats_1m = [
            f for f in [
                "kospi_basis_z",
                "kospi_basis_rate",
                "ref_10100_spread_vs_target",
                "top2_lead_momentum",
                "target_vwap_disparity",
                "target_ofi_cumsum",
                "target_ma_ratio",
                "target_mfi",
            ] if f in df_1m.columns
        ]
        cases_results.append(
            run_single_case(
                case_id="C7_069500_1M_6_10",
                title="069500 1분봉 표준 지평 (6/10분 보유)",
                df_engineered=df_1m,
                base_cfg=cfg_1m,
                features=feats_1m,
                predict_ahead=6,
                max_hold=10,
                train_start="2026-05-01",
                holdout_start="2026-09-14",
                holdout_end="2026-10-16",
                wfa_outer=5,
                wfa_inner=6,
                wfa_train_td=41,
                wfa_opt_td=10,
                wfa_test_td=10,
            )
        )
        cases_results.append(
            run_single_case(
                case_id="C8_069500_1M_15_30",
                title="069500 1분봉 중기 지평 (15/30분 보유)",
                df_engineered=df_1m,
                base_cfg=cfg_1m,
                features=feats_1m,
                predict_ahead=15,
                max_hold=30,
                train_start="2026-05-01",
                holdout_start="2026-09-14",
                holdout_end="2026-10-16",
                wfa_outer=5,
                wfa_inner=6,
                wfa_train_td=41,
                wfa_opt_td=10,
                wfa_test_td=10,
            )
        )

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
            f for f in [
                "kospi_basis_z",
                "ref_10100_spread_vs_target",
                "top2_lead_momentum",
                "target_vwap_disparity",
                "target_ma_ratio",
                "target_mfi",
                "target_rsi_daily",
            ] if f in df_15m.columns
        ]
        cases_results.append(
            run_single_case(
                case_id="C9_069500_15M_4_8",
                title="069500 15분봉 (1시간 예측 / 2시간 보유)",
                df_engineered=df_15m,
                base_cfg=cfg_15m,
                features=feats_15m,
                predict_ahead=4,
                max_hold=8,
                train_start="2025-09-01",
                holdout_start="2026-08-15",
                holdout_end="2026-10-16",
                wfa_outer=5,
                wfa_inner=6,
                wfa_train_td=82,
                wfa_opt_td=21,
                wfa_test_td=21,
            )
        )

    out_json = os.path.join(OUT_DIR, "case_study_results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(
            {"snr_report": snr_report, "cases": cases_results},
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\n✅ 전체 실험 결과 저장 완료: {out_json}")


if __name__ == "__main__":
    main()
