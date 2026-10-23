"""
중소형 코스닥 종목(042940 상지건설, 221800 지구홀딩스, 389140 포바이포 등)의
WFA/Holdout 양(+)의 성과가 '진짜 정보비대칭 알파'인지, 아니면
1) 원장 미등록 종목의 SELL_TAX=0 누락 착시 + 2) 호가 스프레드(High/Low 바운스) 착시인지 정밀 검증!

실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/verify_smallcap_reality.py
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
from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY, get_incompatible_features
from shared.features.feature_health import find_dead_features, find_unstable_availability, find_insufficient_coverage
from CAiModelBaker.core.model_engine import run_feature_engineering, process_02_relay_optimization
from CAiModelBaker.core.backtester import process_03_pure_backtest
from CAiModelBaker.core.physics_constraint import resolve_round_trip_cost

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, OUT_DIR)
from run_info_asymmetry_intraday import load_stock_1m_dataframe, silent_log


def main():
    base_cfg = json.load(open(os.path.join(BAKER_DIR, "deploy/005930_1m/SAMSUNG_M1_BEST1/config_snapshot.json"), encoding="utf-8"))

    # Check what cost was actually applied when SELL_TAX was not explicitly set for unregistered symbol
    test_cfg = copy.deepcopy(base_cfg)
    test_cfg["SYMBOL"] = "221800"
    test_cfg["TARGET_SYMBOL"] = "221800"
    print(f"[진단 1] 원장 미등록 종목(221800) 기본 resolve_round_trip_cost: {resolve_round_trip_cost(test_cfg)*100:.3f}% (SELL_TAX 누락 확인!)")

    stocks = [
        ("A042940", "042940", "상지건설"),
        ("A221800", "221800", "지구홀딩스"),
        ("A389140", "389140", "포바이포"),
    ]

    results = []
    for code, sym, name in stocks:
        df_raw = load_stock_1m_dataframe(code, start_date="2026-03-01", end_date="2026-10-08")
        # Analyze 1-minute candle microstructure (zero-volume bars, high-low spread bounce, tick size % of price)
        med_px = float(df_raw["close"].median())
        # KRX tick size for med_px
        if med_px < 2000:
            tick = 1
        elif med_px < 5000:
            tick = 5
        elif med_px < 20000:
            tick = 10
        elif med_px < 50000:
            tick = 50
        else:
            tick = 100
        tick_pct = tick / med_px * 100.0
        hl_range_pct = ((df_raw["high"] - df_raw["low"]) / df_raw["close"] * 100.0).median()
        cc_abs_pct = (df_raw["close"].pct_change().abs() * 100.0).median()
        print(
            f"\n▶ {name}({sym}) 미시구조: 주가중앙={med_px:,.0f}원 | 1틱={tick}원({tick_pct:.3f}%) | "
            f"1분봉 고저폭(H-L) 중앙={hl_range_pct:.3f}% | 1분봉 종가변화(|ΔC|) 중앙={cc_abs_pct:.3f}%"
        )

        cfg = copy.deepcopy(base_cfg)
        cfg["SYMBOL"] = sym
        cfg["TARGET_SYMBOL"] = sym
        cfg["ACTIVE_SESSION_ID"] = f"{sym}_VERIFY"
        cfg["REFERENCE_SYMBOLS"] = []

        all_pool = [fid for fid, defn in DEFAULT_FEATURE_REGISTRY.items() if not defn.get("is_alias", False)]
        bad = {x["feature_id"] if isinstance(x, dict) else str(x) for x in get_incompatible_features(sym, all_pool, session_id=cfg["ACTIVE_SESSION_ID"])}
        kept_pool = [f for f in all_pool if f not in bad and not f.startswith("ref_") and not f.startswith("kospi_")]

        fc = copy.deepcopy(cfg.get("FEATURE_CONFIG", {}))
        fc["enabled_features"] = kept_pool
        fc["active_features"] = kept_pool
        df_eng = run_feature_engineering(df_raw, "1m", feature_config=fc, config=cfg)

        fit_df = df_eng[df_eng["timestamp"] <= pd.to_datetime("2026-09-15")].copy()
        present = [f for f in kept_pool if f in fit_df.columns]

        def _ids(seq):
            return {x["feature_id"] if isinstance(x, dict) else str(x) for x in seq}

        healthy = [f for f in present if f not in (_ids(find_dead_features(fit_df, present)) | _ids(find_unstable_availability(fit_df, present)) | _ids(find_insufficient_coverage(fit_df, present)))]

        for label, pred_h, max_h, fee_r, slip_r, sell_tax in [
            ("① 원장누락 착시비용 (왕복 0.07%, 세금0%)", 15, 30, 0.00015, 0.0002, 0.0),
            ("② 실전 세금+1틱슬리피지 (왕복 0.31%)", 15, 30, 0.00015, 0.0005, 0.0018),
            ("③ 중소형주 실전 호가스프레드 (왕복 0.51%)", 15, 30, 0.00015, 0.0015, 0.0018),
            ("④ 스나이퍼 60~180분 + 실전비용 (왕복 0.41%)", 60, 180, 0.00015, 0.0010, 0.0018),
        ]:
            rcfg = copy.deepcopy(cfg)
            work_dir = os.path.join(OUT_DIR, "work_verify", f"{sym}_{pred_h}_{int((fee_r*2+slip_r*2+sell_tax)*10000)}")
            os.makedirs(os.path.join(work_dir, "work", "model"), exist_ok=True)
            os.makedirs(os.path.join(work_dir, "work", "reports"), exist_ok=True)
            rcfg[project_store.RUN_DIR_KEY] = work_dir
            rcfg["FEE_RATE"] = fee_r
            rcfg["SLIPPAGE"] = slip_r
            rcfg["SELL_TAX"] = sell_tax
            rcfg["OPTUNA_CONFIG"] = copy.deepcopy(rcfg["OPTUNA_CONFIG"])
            rcfg["OPTUNA_CONFIG"]["fee_upbit"] = fee_r
            rcfg["OPTUNA_CONFIG"]["slippage"] = slip_r

            rt_cost_pct = resolve_round_trip_cost(rcfg) * 100.0

            oc = rcfg["OPTUNA_CONFIG"]
            oc["wfa_outer_trials"] = 4
            oc["wfa_inner_trials"] = 5
            oc["outer_trials"] = 8
            oc["inner_trials"] = 8
            oc["wfa_start_date"] = "2026-03-02"
            oc["wfa_end_date"] = "2026-09-15"
            oc["predict_ahead_candles_range"]["user_min"] = pred_h
            oc["predict_ahead_candles_range"]["user_max"] = pred_h
            oc["max_hold_candles_range"]["user_min"] = max_h
            oc["max_hold_candles_range"]["user_max"] = max_h
            oc["take_profit_range"]["user_min"] = 1.2 if pred_h == 15 else 1.8
            oc["take_profit_range"]["user_max"] = 3.5 if pred_h == 15 else 5.0
            oc["stop_loss_range"]["user_min"] = 1.0 if pred_h == 15 else 1.5
            oc["stop_loss_range"]["user_max"] = 2.5 if pred_h == 15 else 3.0
            oc["buy_quantile_range"]["user_min"] = 0.94
            oc["buy_quantile_range"]["user_max"] = 0.995
            oc["cooldown_minutes_range"]["user_min"] = 15
            oc["cooldown_minutes_range"]["user_max"] = 60
            oc["wfa_input_values"] = {"unit": "td", "train": 40, "opt": 10, "test": 10}
            oc["wfa_train_months"] = round(40 / 21.0, 2)
            oc["wfa_opt_months"] = round(10 / 21.0, 2)
            oc["wfa_test_months"] = round(10 / 21.0, 2)

            rcfg["TARGET_HORIZON_CONFIG"] = {
                "interval": "1m",
                "mode": "locked",
                "locked_predict_ahead": pred_h,
                "locked_max_hold": max_h,
                "candidate_horizons": [pred_h],
            }

            process_02_relay_optimization(
                rcfg,
                "2026-03-02",
                "2026-09-15",
                "2026-09-16",
                "2026-10-08",
                log_fn=silent_log,
                holdout_start="2026-09-16",
                purge_candles=pred_h,
                feature_subset=healthy,
                cached_df=df_eng,
            )
            bt_res = process_03_pure_backtest(
                rcfg,
                "2026-09-16",
                "2026-10-08",
                log_fn=silent_log,
                session_id=rcfg["ACTIVE_SESSION_ID"],
                feature_subset=healthy,
                cached_df=df_eng,
                null_permutations=50,
            )
            bp_path = os.path.join(work_dir, "work", "model", "best_params_1m.json")
            t_meta = json.load(open(bp_path, encoding="utf-8")).get("training_meta", {}) if os.path.exists(bp_path) else {}
            null_res = bt_res.get("null_test", {}) or {}

            row = {
                "stock": f"{name}({sym})",
                "case": label,
                "rt_cost_pct": round(rt_cost_pct, 3),
                "wfa_windows": f"{t_meta.get('wfa_valid_windows', 0)}/{t_meta.get('wfa_windows', 0)}",
                "wfa_oos_pct": round(float(t_meta.get("wfa_oos_total_pct", 0.0)), 2),
                "wfa_robust": bool(t_meta.get("wfa_robust", False)),
                "holdout_pct": round(float(bt_res.get("net_profit", 0.0)), 2),
                "holdout_trades": int(bt_res.get("total_trades", 0)),
                "holdout_wr": round(float(bt_res.get("win_rate", 0.0)), 1),
                "holdout_pf": round(float(bt_res.get("profit_factor", 0.0)), 2),
                "null_median_pct": round(float(null_res.get("median", 0.0)), 2) if null_res.get("median") is not None else None,
                "null_p_value": round(float(null_res.get("p_value", 1.0)), 3) if null_res.get("p_value") is not None else None,
            }
            print(
                f"  [{name}] {label:<40} (왕복비용={rt_cost_pct:.2f}%) | "
                f"WFA {row['wfa_windows']} ({row['wfa_oos_pct']:+6.2f}%, Robust={str(row['wfa_robust']):<5}) | "
                f"Holdout {row['holdout_pct']:+6.2f}% ({row['holdout_trades']:2d}회, 승률 {row['holdout_wr']:4.1f}%, PF {row['holdout_pf']:4.2f}) | "
                f"무작위진입 중앙값={row['null_median_pct']}% (p={row['null_p_value']})"
            )
            results.append(row)

    shutil.rmtree(os.path.join(OUT_DIR, "work_verify"), ignore_errors=True)
    out_path = os.path.join(OUT_DIR, "smallcap_reality_check.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
