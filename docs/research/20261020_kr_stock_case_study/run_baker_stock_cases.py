"""
국내 개별종목(005930 삼성전자, 000660 SK하이닉스) CAiModelBaker 파이프라인 및 물리적 한계 검증
실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/run_baker_stock_cases.py
"""
import os
import sys
import json
import copy
import time
import shutil
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

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def silent_log(*args, **kwargs):
    pass


def analyze_stock_physics():
    """
    069500(ETF, 왕복비용 0.03%) vs 005930(삼성전자, 왕복비용 0.23%) vs 000660(SK하이닉스, 왕복비용 0.23%)
    타임프레임 및 보유 지평별 물리적 변동폭(|ΔP|, MFE)이 마찰비용을 넘을 확률 비교
    """
    print("=" * 80)
    print("[STEP 1] ETF(069500) vs 개별종목(005930, 000660) 지평별 변동폭 vs 세금/마찰비용 비교")
    print("=" * 80)
    results = {}

    datasets = [
        ("069500_1m (ETF)", os.path.join(ROOT_DIR, "DataVault/features/069500/data_snapshot_1m.csv"), 0.030, [5, 15, 30, 60, 180]),
        ("005930_10s (삼성전자 10초)", os.path.join(ROOT_DIR, "DataVault/features/005930/data_snapshot_10s.csv"), 0.230, [6, 18, 36, 60, 180]),
        ("005930_1m (삼성전자 1분)", os.path.join(ROOT_DIR, "DataVault/features/005930/data_snapshot_1m.csv"), 0.230, [5, 15, 30, 60, 180, 360]),
        ("000660_1m (SK하이닉스 1분)", os.path.join(ROOT_DIR, "DataVault/features/000660/data_snapshot_1m.csv"), 0.230, [5, 15, 30, 60, 180, 360]),
    ]

    for label, path, friction_pct, horizons in datasets:
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df[df["timestamp"] >= pd.Timestamp("2026-01-01")].reset_index(drop=True)
        close_col = "target_close" if "target_close" in df.columns else "close"
        high_col = "target_high" if "target_high" in df.columns else "high"

        close = df[close_col].values.astype(float)
        high = df[high_col].values.astype(float)
        dates = df["timestamp"].dt.date.values

        h_stats = {}
        print(f"\n▶ {label} (데이터 {len(df):,}행, 왕복 마찰비용 = {friction_pct:.3f}%)")
        for h in horizons:
            valid = (np.arange(len(df)) + h < len(df))
            same_day = np.zeros(len(df), dtype=bool)
            same_day[valid] = (dates[np.where(valid)[0]] == dates[np.where(valid)[0] + h])
            idx = np.where(same_day)[0]
            if len(idx) == 0:
                continue
            fwd_ret = (close[idx + h] - close[idx]) / close[idx] * 100.0
            sample_idx = idx[::max(1, len(idx) // 15000)]
            mfe = np.array([(np.max(high[i + 1 : i + h + 1]) - close[i]) / close[i] * 100.0 for i in sample_idx])

            abs_med = float(np.median(np.abs(fwd_ret)))
            mfe_med = float(np.median(mfe))
            mfe_p75 = float(np.percentile(mfe, 75))
            prob_beat_cost = float(np.mean(fwd_ret > friction_pct) * 100.0)
            prob_beat_2x_cost = float(np.mean(fwd_ret > friction_pct * 2.0) * 100.0)
            prob_mfe_3x_cost = float(np.mean(mfe >= friction_pct * 3.0) * 100.0)

            h_stats[str(h)] = {
                "abs_ret_median_pct": round(abs_med, 4),
                "mfe_median_pct": round(mfe_med, 4),
                "mfe_p75_pct": round(mfe_p75, 4),
                "prob_close_beats_cost_pct": round(prob_beat_cost, 2),
                "prob_close_beats_2x_cost_pct": round(prob_beat_2x_cost, 2),
                "prob_mfe_beats_3x_cost_pct": round(prob_mfe_3x_cost, 2),
            }
            print(
                f"  H={h:3d}c | |ΔP|중앙={abs_med:.3f}% | MFE중앙={mfe_med:.3f}%(P75={mfe_p75:.3f}%) | "
                f"종가>비용({friction_pct:.2f}%)={prob_beat_cost:5.1f}% | 종가>2x비용={prob_beat_2x_cost:5.1f}%"
            )
        results[label] = h_stats
    return results


def prepare_engineered_df(symbol: str, interval: str, base_cfg: dict, train_start: str, train_end: str):
    snap = os.path.join(ROOT_DIR, "DataVault", "features", symbol, f"data_snapshot_{interval}.csv")
    df = pd.read_csv(snap)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = df[df["timestamp"] >= pd.to_datetime(train_start)].reset_index(drop=True)

    register_configured_reference_features(base_cfg)
    all_pool = [fid for fid, defn in DEFAULT_FEATURE_REGISTRY.items() if not defn.get("is_alias", False)]
    bad = {x["feature_id"] for x in get_incompatible_features(symbol, all_pool, session_id=base_cfg.get("ACTIVE_SESSION_ID", symbol))}
    kept_pool = [f for f in all_pool if f not in bad]

    fc = copy.deepcopy(base_cfg.get("FEATURE_CONFIG", {}))
    fc["enabled_features"] = kept_pool
    fc["active_features"] = kept_pool
    df_eng = run_feature_engineering(df, interval, feature_config=fc, config=base_cfg)

    fit_df = df_eng[df_eng["timestamp"] <= pd.to_datetime(train_end)].copy()
    present = [f for f in kept_pool if f in fit_df.columns]
    def _extract_ids(items):
        return {x["feature_id"] if isinstance(x, dict) else str(x) for x in items}

    dead = _extract_ids(find_dead_features(fit_df, present))
    unstable = _extract_ids(find_unstable_availability(fit_df, present))
    low_cov = _extract_ids(find_insufficient_coverage(fit_df, present))
    healthy = [f for f in present if f not in (dead | unstable | low_cov)]
    return df_eng, healthy


def run_baker_case(
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
    fee_pct: float = 0.21,  # 왕복 수수료+거래세 % (한국 주식 기본: 거래세 0.18% + 수수료 0.03% = 0.21%)
    tp_range: tuple = (0.6, 2.5),
    sl_range: tuple = (0.6, 2.0),
    buy_q_range: tuple = (0.90, 0.99),
    cooldown_range: tuple = (5, 30),
    wfa_train_td: int = 18,
    wfa_opt_td: int = 4,
    wfa_test_td: int = 4,
    wfa_outer: int = 5,
    wfa_inner: int = 6,
) -> dict:
    t0 = time.time()
    work_dir = os.path.join(OUT_DIR, "work_cases", case_id)
    os.makedirs(os.path.join(work_dir, "work", "model"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "work", "reports"), exist_ok=True)

    cfg = copy.deepcopy(base_cfg)
    cfg[project_store.RUN_DIR_KEY] = work_dir
    cfg["TRAIN_START"] = train_start
    cfg["TRAIN_END"] = train_end
    cfg["OPT_START"] = holdout_start
    cfg["OPT_END"] = holdout_end

    # Fee & slippage override
    cfg["BACKTEST_CONFIG"] = copy.deepcopy(cfg.get("BACKTEST_CONFIG", {}))
    cfg["BACKTEST_CONFIG"]["fee_pct"] = fee_pct
    cfg["FEE_PCT"] = fee_pct

    interval = cfg.get("INTERVAL", "1m")
    cfg["TARGET_HORIZON_CONFIG"] = {
        "interval": interval,
        "mode": "locked",
        "locked_predict_ahead": predict_ahead,
        "locked_max_hold": max_hold,
        "candidate_horizons": [predict_ahead],
    }

    oc = cfg["OPTUNA_CONFIG"]
    oc["wfa_outer_trials"] = wfa_outer
    oc["wfa_inner_trials"] = wfa_inner
    oc["outer_trials"] = max(8, wfa_outer * 2)
    oc["inner_trials"] = max(8, wfa_inner * 2)
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
        (df_engineered["timestamp"] >= pd.to_datetime(train_start))
        & (df_engineered["timestamp"] <= pd.to_datetime(holdout_end) + pd.Timedelta(days=1))
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
        feature_subset=features,
        cached_df=df_slice,
    )

    bt_res = process_03_pure_backtest(
        cfg,
        holdout_start,
        holdout_end,
        log_fn=silent_log,
        session_id=cfg.get("ACTIVE_SESSION_ID", case_id),
        feature_subset=features,
        cached_df=df_slice,
    )

    best_params_path = os.path.join(work_dir, "work", "model", f"best_params_{interval}.json")
    t_meta = {}
    saved_params = {}
    if os.path.exists(best_params_path):
        with open(best_params_path, "r", encoding="utf-8") as f:
            saved_params = json.load(f)
            t_meta = saved_params.get("training_meta", {})

    t_logs = bt_res.get("trade_logs", [])
    n_tp = sum(1 for x in t_logs if "익절" in str(x.get("type", "")))
    n_sl = sum(1 for x in t_logs if "손절" in str(x.get("type", "")))
    n_to = sum(1 for x in t_logs if "시간만료" in str(x.get("type", "")))

    elapsed = round(time.time() - t0, 1)
    summary = {
        "case_id": case_id,
        "title": title,
        "interval": interval,
        "predict_ahead": predict_ahead,
        "max_hold": max_hold,
        "fee_pct": fee_pct,
        "features_count": len(features),
        "wfa_robust": bool(t_meta.get("wfa_robust", False)),
        "wfa_valid_windows": int(t_meta.get("wfa_valid_windows", 0)),
        "wfa_windows": int(t_meta.get("wfa_windows", 0)),
        "wfa_oos_total_pct": round(float(t_meta.get("wfa_oos_total_pct", 0.0)), 2),
        "holdout_net_profit_pct": round(float(bt_res.get("net_profit", 0.0)), 2),
        "holdout_trades": int(bt_res.get("total_trades", 0)),
        "holdout_win_rate": round(float(bt_res.get("win_rate", 0.0)), 1),
        "holdout_pf": round(float(bt_res.get("profit_factor", 0.0)), 2),
        "holdout_timeout_ratio_pct": round(n_to / max(1, len(t_logs)) * 100.0, 1),
        "elapsed_sec": elapsed,
    }
    print(
        f"  [{case_id}] {title:<45} | "
        f"WFA: {summary['wfa_valid_windows']}/{summary['wfa_windows']}창 ({summary['wfa_oos_total_pct']:+6.2f}%, Robust={str(summary['wfa_robust']):<5}) | "
        f"Holdout: {summary['holdout_net_profit_pct']:+6.2f}% ({summary['holdout_trades']:2d}회, 승률 {summary['holdout_win_rate']:4.1f}%, PF {summary['holdout_pf']:4.2f}) [{elapsed}s]"
    )
    return summary


def main():
    phys = analyze_stock_physics()

    print("\n" + "=" * 80)
    print("[STEP 2] 개별종목(005930 삼성전자, 000660 SK하이닉스) CAiModelBaker 케이스별 검증")
    print("=" * 80)

    cfg_005930_10s = json.load(open(os.path.join(BAKER_DIR, "deploy/005930_10s/SAMSUNG_S10_BEST1/config_snapshot.json"), encoding="utf-8"))
    cfg_005930_1m = json.load(open(os.path.join(BAKER_DIR, "deploy/005930_1m/SAMSUNG_M1_BEST1/config_snapshot.json"), encoding="utf-8"))
    cfg_000660_1m = json.load(open(os.path.join(BAKER_DIR, "deploy/000660_1m/SKhynix_M1_BEST1/config_snapshot.json"), encoding="utf-8"))

    print("Preparing engineered features for 005930_10s, 005930_1m, 000660_1m...")
    df_5930_10s, feat_5930_10s = prepare_engineered_df("005930", "10s", cfg_005930_10s, "2026-08-27", "2026-10-06")
    df_5930_1m, feat_5930_1m = prepare_engineered_df("005930", "1m", cfg_005930_1m, "2026-04-01", "2026-09-15")
    df_0660_1m, feat_0660_1m = prepare_engineered_df("000660", "1m", cfg_000660_1m, "2026-04-01", "2026-09-15")

    print(f"Healthy features -> 005930_10s: {len(feat_5930_10s)}, 005930_1m: {len(feat_5930_1m)}, 000660_1m: {len(feat_0660_1m)}")

    cases = []

    # B1: 삼성전자 10초봉 (실전 세금 0.21% 적용)
    cases.append(
        run_baker_case(
            "B1_005930_10S_REALTAX",
            "삼성전자 10s (18/30c=3~5분, 실전세금 0.21%)",
            df_5930_10s,
            cfg_005930_10s,
            feat_5930_10s,
            predict_ahead=18,
            max_hold=30,
            train_start="2026-08-27",
            train_end="2026-10-06",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            fee_pct=0.21,
            tp_range=(0.4, 1.5),
            sl_range=(0.4, 1.2),
            wfa_train_td=12,
            wfa_opt_td=3,
            wfa_test_td=3,
        )
    )

    # B2: 삼성전자 10초봉 (가상 무세금 0.01% 적용 - 오더북 알파 유무 검증)
    cases.append(
        run_baker_case(
            "B2_005930_10S_ZEROTAX",
            "삼성전자 10s (18/30c=3~5분, 가상무세금 0.01%)",
            df_5930_10s,
            cfg_005930_10s,
            feat_5930_10s,
            predict_ahead=18,
            max_hold=30,
            train_start="2026-08-27",
            train_end="2026-10-06",
            holdout_start="2026-10-07",
            holdout_end="2026-10-16",
            fee_pct=0.01,
            tp_range=(0.2, 1.0),
            sl_range=(0.2, 1.0),
            wfa_train_td=12,
            wfa_opt_td=3,
            wfa_test_td=3,
        )
    )

    # B3: 삼성전자 1분봉 단타 (15/30분 보유, 실전 세금 0.21%)
    cases.append(
        run_baker_case(
            "B3_005930_1M_15_30_TAX",
            "삼성전자 1m (15/30분 보유, 실전세금 0.21%)",
            df_5930_1m,
            cfg_005930_1m,
            feat_5930_1m,
            predict_ahead=15,
            max_hold=30,
            train_start="2026-04-01",
            train_end="2026-09-15",
            holdout_start="2026-09-16",
            holdout_end="2026-10-06",
            fee_pct=0.21,
            tp_range=(0.6, 2.0),
            sl_range=(0.6, 1.5),
            wfa_train_td=40,
            wfa_opt_td=10,
            wfa_test_td=10,
        )
    )

    # B4: SK하이닉스 1분봉 단타 (15/30분 보유, 실전 세금 0.21%)
    cases.append(
        run_baker_case(
            "B4_000660_1M_15_30_TAX",
            "SK하이닉스 1m (15/30분 보유, 실전세금 0.21%)",
            df_0660_1m,
            cfg_000660_1m,
            feat_0660_1m,
            predict_ahead=15,
            max_hold=30,
            train_start="2026-04-01",
            train_end="2026-09-15",
            holdout_start="2026-09-16",
            holdout_end="2026-10-02",
            fee_pct=0.21,
            tp_range=(0.8, 2.5),
            sl_range=(0.8, 2.0),
            wfa_train_td=40,
            wfa_opt_td=10,
            wfa_test_td=10,
        )
    )

    # B5: SK하이닉스 1분봉 장중 대파동 스나이퍼 (120/240분 = 2~4시간 보유, 상위 1~5% 타점, 실전 세금 0.21%)
    cases.append(
        run_baker_case(
            "B5_000660_1M_120_240_SNIPER",
            "SK하이닉스 1m 대파동 (2~4시간 보유, 실전세금 0.21%)",
            df_0660_1m,
            cfg_000660_1m,
            feat_0660_1m,
            predict_ahead=120,
            max_hold=240,
            train_start="2026-04-01",
            train_end="2026-09-15",
            holdout_start="2026-09-16",
            holdout_end="2026-10-02",
            fee_pct=0.21,
            tp_range=(1.5, 4.0),
            sl_range=(1.2, 2.5),
            buy_q_range=(0.95, 0.99),
            cooldown_range=(30, 120),
            wfa_train_td=40,
            wfa_opt_td=10,
            wfa_test_td=10,
        )
    )

    # B6: SK하이닉스 1분봉 장중 대파동 (무세금 0.01% 가정 시)
    cases.append(
        run_baker_case(
            "B6_000660_1M_120_240_ZEROTAX",
            "SK하이닉스 1m 대파동 (2~4시간 보유, 가상무세금 0.01%)",
            df_0660_1m,
            cfg_000660_1m,
            feat_0660_1m,
            predict_ahead=120,
            max_hold=240,
            train_start="2026-04-01",
            train_end="2026-09-15",
            holdout_start="2026-09-16",
            holdout_end="2026-10-02",
            fee_pct=0.01,
            tp_range=(1.5, 4.0),
            sl_range=(1.2, 2.5),
            buy_q_range=(0.95, 0.99),
            cooldown_range=(30, 120),
            wfa_train_td=40,
            wfa_opt_td=10,
            wfa_test_td=10,
        )
    )

    work_cases = os.path.join(OUT_DIR, "work_cases")
    if os.path.exists(work_cases):
        shutil.rmtree(work_cases, ignore_errors=True)

    out_path = os.path.join(OUT_DIR, "baker_stock_cases_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"physics": phys, "baker_cases": cases}, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
