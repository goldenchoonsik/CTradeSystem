"""
기관 미참여·중소형(KOSDAQ 중심) '정보 불균형(Information Asymmetry) & CVD 주문흐름' 분봉 단타 끝장 검증
1) 저기관·고변동 중소형주 5개 선정 후 CAiModelBaker (1m + CVD 피처) WFA & Holdout 구동
2) 저기관 중소형주 100개(2025~2026 1분봉 약 50,000 종목-일) 대상 '정보 비대칭/세력 선취매(CVD)' 6대 단타 가설 전수 검증

실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/run_info_asymmetry_intraday.py
"""
import os
import sys
import glob
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

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
CANDLE_1M_ROOT = os.path.join(ROOT_DIR, "DataVault/staging/vol_spike_universe/raw/creon/candle_1m")


def silent_log(*args, **kwargs):
    pass


def select_low_inst_asymmetry_stocks(top_n_baker: int = 5, top_n_universe: int = 120):
    """
    조건:
    - KOSDAQ 보통주 (스팩/우선주/관리종목 제외)
    - 시가총액 400억 ~ 2,500억 (기관 커버리지가 낮아 정보 비대칭이 존재하는 중소형주)
    - 기관 거래 관여도(|orgn| / value) 평균 2.5% 미만 (기관이 거의 참여하지 않는 종목)
    - 일평균 거래대금 40억 이상 & 최대 거래대금 300억 이상 (단타 체결이 가능한 유동성 보유)
    - 일간 변동성(표준편차)이 높은 상위 종목
    """
    univ = pd.read_csv(os.path.join(ROOT_DIR, "DataVault/staging/vol_spike_universe/universe.csv"))
    univ = univ[
        (univ["market"] == "KOSDAQ")
        & (univ["is_preferred"] == 0)
        & (univ["is_spac"] == 0)
        & (univ["status_kind"] == 0)
    ].copy()
    name_map = dict(zip(univ["code"], univ["name"]))

    p_prices = os.path.join(ROOT_DIR, "DataVault/staging/kr_equity/derived/prices_1d.parquet")
    p_flows = os.path.join(ROOT_DIR, "DataVault/staging/vol_spike_universe/derived/flows_panel.parquet")
    p_ext = os.path.join(ROOT_DIR, "DataVault/staging/kr_equity/derived/daily_ext.parquet")

    df_p = pd.read_parquet(p_prices)
    df_p["date"] = pd.to_datetime(df_p["date"])
    df_p = df_p[(df_p["date"] >= pd.Timestamp("2025-01-01")) & (df_p["code"].isin(name_map))].copy()

    df_f = pd.read_parquet(p_flows)
    df_f["date"] = pd.to_datetime(df_f["date"])
    df_f = df_f[df_f["date"] >= pd.Timestamp("2025-01-01")].copy()

    df_e = pd.read_parquet(p_ext, columns=["code", "date", "market_cap"])
    df_e["date"] = pd.to_datetime(df_e["date"])
    df_e = df_e[df_e["date"] >= pd.Timestamp("2025-01-01")].copy()

    df = df_p.merge(df_f, on=["code", "date"], how="left").merge(df_e, on=["code", "date"], how="left")
    df[["orgn", "frgn", "prsn"]] = df[["orgn", "frgn", "prsn"]].fillna(0.0)
    df["inst_abs_ratio"] = np.where(df["value"] > 0, df["orgn"].abs() / df["value"] * 100.0, 0.0)
    df["ret_1d"] = df.groupby("code")["close"].pct_change() * 100.0

    agg = df.groupby("code").agg(
        days=("date", "count"),
        mean_mcap=("market_cap", "median"),
        mean_val=("value", "mean"),
        max_val=("value", "max"),
        mean_close=("close_unadj", "median"),
        inst_share=("inst_abs_ratio", "mean"),
        ret_std=("ret_1d", "std"),
    ).reset_index()

    # Check 1m folder existence
    existing_1m = set(os.listdir(CANDLE_1M_ROOT))
    agg = agg[agg["code"].isin(existing_1m)].copy()

    cand = agg[
        (agg["days"] >= 350)
        & (agg["mean_mcap"].between(4.0e10, 2.5e11))  # 시총 400억 ~ 2,500억 중소형주
        & (agg["mean_close"] >= 2500)                 # 동전주 제외
        & (agg["inst_share"] <= 2.5)                  # 기관 순매매 비중 2.5% 이하 (기관 미참여)
        & (agg["mean_val"] >= 3.5e9)                  # 일평균 거래대금 35억 이상
        & (agg["max_val"] >= 3.0e10)                  # 최소 1회 이상 300억+ 거래대금 폭발 이력
    ].sort_values("ret_std", ascending=False).reset_index(drop=True)

    cand["name"] = cand["code"].map(name_map)
    print("=" * 100)
    print(f"[선정 결과] 조건 충족 저기관·고변동 중소형(KOSDAQ) 후보: 총 {len(cand)}개 종목")
    print("=" * 100)
    for i, r in cand.head(top_n_baker).iterrows():
        print(
            f"  ★ Baker 타겟 {i+1}: {r['name']}({r['code']}) | 시총중앙 {r['mean_mcap']/1e8:,.0f}억 | "
            f"기관비중 {r['inst_share']:.2f}% | 일평균거래대금 {r['mean_val']/1e8:,.1f}억 (최대 {r['max_val']/1e8:,.0f}억) | 일변동성 {r['ret_std']:.2f}%"
        )

    return cand.head(top_n_baker).to_dict("records"), cand.head(top_n_universe).to_dict("records")


def load_stock_1m_dataframe(code: str, start_date: str = "2026-03-01", end_date: str = "2026-10-08") -> pd.DataFrame:
    folder = os.path.join(CANDLE_1M_ROOT, code)
    if not os.path.exists(folder):
        return pd.DataFrame()
    csv_files = sorted(glob.glob(os.path.join(folder, "*.csv")))
    dfs = []
    for fp in csv_files:
        d_str = os.path.basename(fp).replace(".csv", "")
        if d_str < start_date or d_str > end_date:
            continue
        try:
            sub = pd.read_csv(fp)
            if len(sub) > 0:
                dfs.append(sub)
        except Exception:
            continue
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    # Standardize columns for CAiModelBaker
    df["open"] = df["upbit_open"].astype(float)
    df["high"] = df["upbit_high"].astype(float)
    df["low"] = df["upbit_low"].astype(float)
    df["close"] = df["upbit_close"].astype(float)
    df["volume"] = df["upbit_volume"].astype(float)
    df["trade_amount"] = df["upbit_trade_price"].astype(float)
    df["target_open"] = df["open"]
    df["target_high"] = df["high"]
    df["target_low"] = df["low"]
    df["target_close"] = df["close"]
    df["target_volume"] = df["volume"]
    df["target_trade_price"] = df["trade_amount"]

    # Derive intraday trade_bid_ratio from tick-derived CVD delta so CVD features work in CAiModelBaker!
    # cvd in Creon 1m is cumulative volume delta (buy_vol - sell_vol) within the day
    df["date"] = df["timestamp"].dt.date
    d_cvd = df.groupby("date")["cvd"].diff().fillna(df["cvd"])
    # buy_vol - sell_vol = d_cvd, buy_vol + sell_vol = volume => buy_ratio = 0.5 * (1 + d_cvd / volume)
    ratio = np.where(df["volume"] > 0, 0.5 * (1.0 + np.clip(d_cvd / df["volume"], -1.0, 1.0)), 0.5)
    df["upbit_trade_bid_ratio"] = ratio
    df["target_trade_bid_ratio"] = ratio
    return df


def run_baker_on_smallcaps(targets: list) -> list:
    print("\n" + "=" * 100)
    print("[STEP 1] 저기관·정보비대칭 중소형주 5종목 CAiModelBaker (1m + CVD 주문흐름 피처) 테스트")
    print("=" * 100)

    base_cfg = json.load(open(os.path.join(BAKER_DIR, "deploy/005930_1m/SAMSUNG_M1_BEST1/config_snapshot.json"), encoding="utf-8"))
    results = []

    for item in targets:
        code = item["code"]
        sym = code[1:] if code.startswith("A") else code
        name = item["name"]
        df_raw = load_stock_1m_dataframe(code, start_date="2026-03-01", end_date="2026-10-08")
        if len(df_raw) < 10000:
            continue

        cfg = copy.deepcopy(base_cfg)
        cfg["SYMBOL"] = sym
        cfg["ACTIVE_SESSION_ID"] = f"{sym}_M1_ASYM"
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

        dead = _ids(find_dead_features(fit_df, present))
        unstable = _ids(find_unstable_availability(fit_df, present))
        low_cov = _ids(find_insufficient_coverage(fit_df, present))
        healthy = [f for f in present if f not in (dead | unstable | low_cov)]

        for mode_label, pred_h, max_h, fee_pct, tp_r, sl_r, bq_r in [
            ("단타(15~30분, 실전세금 0.21%)", 15, 30, 0.21, (1.0, 3.0), (1.0, 2.0), (0.92, 0.99)),
            ("스나이퍼(60~180분, 상위3%, 실전세금 0.21%)", 60, 180, 0.21, (1.5, 4.5), (1.2, 2.5), (0.96, 0.995)),
            ("스나이퍼(60~180분, 가상무세금 0.01%)", 60, 180, 0.01, (1.5, 4.5), (1.2, 2.5), (0.96, 0.995)),
        ]:
            t0 = time.time()
            cid = f"{sym}_{pred_h}_{int(fee_pct*100)}"
            work_dir = os.path.join(OUT_DIR, "work_asym", cid)
            os.makedirs(os.path.join(work_dir, "work", "model"), exist_ok=True)
            os.makedirs(os.path.join(work_dir, "work", "reports"), exist_ok=True)

            rcfg = copy.deepcopy(cfg)
            rcfg[project_store.RUN_DIR_KEY] = work_dir
            rcfg["TRAIN_START"] = "2026-03-02"
            rcfg["TRAIN_END"] = "2026-09-15"
            rcfg["OPT_START"] = "2026-09-16"
            rcfg["OPT_END"] = "2026-10-08"
            rcfg["FEE_PCT"] = fee_pct
            rcfg["BACKTEST_CONFIG"] = copy.deepcopy(rcfg.get("BACKTEST_CONFIG", {}))
            rcfg["BACKTEST_CONFIG"]["fee_pct"] = fee_pct
            rcfg["TARGET_HORIZON_CONFIG"] = {
                "interval": "1m",
                "mode": "locked",
                "locked_predict_ahead": pred_h,
                "locked_max_hold": max_h,
                "candidate_horizons": [pred_h],
            }

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
            oc["take_profit_range"]["user_min"] = tp_r[0]
            oc["take_profit_range"]["user_max"] = tp_r[1]
            oc["stop_loss_range"]["user_min"] = sl_r[0]
            oc["stop_loss_range"]["user_max"] = sl_r[1]
            oc["buy_quantile_range"]["user_min"] = bq_r[0]
            oc["buy_quantile_range"]["user_max"] = bq_r[1]
            oc["cooldown_minutes_range"]["user_min"] = 10
            oc["cooldown_minutes_range"]["user_max"] = 60
            oc["wfa_input_values"] = {"unit": "td", "train": 40, "opt": 10, "test": 10}
            oc["wfa_train_months"] = round(40 / 21.0, 2)
            oc["wfa_opt_months"] = round(10 / 21.0, 2)
            oc["wfa_test_months"] = round(10 / 21.0, 2)

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
            )
            bp_path = os.path.join(work_dir, "work", "model", "best_params_1m.json")
            t_meta = json.load(open(bp_path, encoding="utf-8")).get("training_meta", {}) if os.path.exists(bp_path) else {}

            row = {
                "stock": f"{name}({sym})",
                "mode": mode_label,
                "wfa_windows": f"{t_meta.get('wfa_valid_windows', 0)}/{t_meta.get('wfa_windows', 0)}",
                "wfa_oos_pct": round(float(t_meta.get("wfa_oos_total_pct", 0.0)), 2),
                "wfa_robust": bool(t_meta.get("wfa_robust", False)),
                "holdout_pct": round(float(bt_res.get("net_profit", 0.0)), 2),
                "holdout_trades": int(bt_res.get("total_trades", 0)),
                "holdout_wr": round(float(bt_res.get("win_rate", 0.0)), 1),
                "holdout_pf": round(float(bt_res.get("profit_factor", 0.0)), 2),
                "elapsed_s": round(time.time() - t0, 1),
            }
            print(
                f"  • {row['stock']:<18} | {mode_label:<36} | WFA {row['wfa_windows']} ({row['wfa_oos_pct']:+6.2f}%, Robust={str(row['wfa_robust']):<5}) | "
                f"Holdout {row['holdout_pct']:+6.2f}% ({row['holdout_trades']:2d}회, 승률 {row['holdout_wr']:4.1f}%, PF {row['holdout_pf']:4.2f})"
            )
            results.append(row)

    shutil.rmtree(os.path.join(OUT_DIR, "work_asym"), ignore_errors=True)
    return results


def run_orderflow_asymmetry_hypotheses(universe_stocks: list) -> list:
    """
    저기관·중소형주 유니버스(상위 80개 종목, 2025-01 ~ 2026-10 1분봉 전체)에서
    '정보 불균형 / 세력 선취매(CVD 주문흐름 괴리)' 6대 장중 단타 가설 전수 검증!
    """
    print("\n" + "=" * 100)
    print("[STEP 2] 저기관 중소형주 80종목 1분봉 전수 — '정보 불균형 & CVD 주문흐름' 6대 단타 가설 검증")
    print("=" * 100)

    target_codes = [x["code"] for x in universe_stocks[:80]]
    events = {k: [] for k in ["IA1_STEALTH_CVD", "IA2_MORNING_BRK", "IA3_AFTERNOON_NEWS", "IA4_DIP_CVD_ABSORB", "IA5_INPLAY_VWAP_PB"]}

    for idx_s, code in enumerate(target_codes):
        folder = os.path.join(CANDLE_1M_ROOT, code)
        if not os.path.exists(folder):
            continue
        csv_files = sorted(glob.glob(os.path.join(folder, "202[56]-*.csv")))
        prev_day_close = None
        prev_day_amt = None
        recent_day_amts = []

        for fp in csv_files:
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

            day_total_amt = float(np.sum(amt))
            avg_day_amt = float(np.mean(recent_day_amts[-20:])) if len(recent_day_amts) >= 5 else day_total_amt
            avg_min_amt = max(1.0, avg_day_amt / 380.0)

            d_cvd = np.diff(cvd, prepend=0.0)
            cum_v = np.cumsum(v)
            cum_pv = np.cumsum(c * v)
            vwap = np.where(cum_v > 0, cum_pv / cum_v, c)

            # Helper to record forward 15m, 30m, 60m, and EOD return from bar i
            def record_event(key, bar_i):
                px = c[bar_i]
                if px <= 0:
                    return
                r15 = (c[min(n - 1, bar_i + 15)] / px - 1.0) * 100.0
                r30 = (c[min(n - 1, bar_i + 30)] / px - 1.0) * 100.0
                r60 = (c[min(n - 1, bar_i + 60)] / px - 1.0) * 100.0
                reod = (c[-1] / px - 1.0) * 100.0
                events[key].append({"date": d_str, "code": code, "r15": r15, "r30": r30, "r60": r60, "reod": reod})

            used_today = set()
            for i in range(20, n - 30):
                vol_15 = np.sum(v[i - 14 : i + 1])
                amt_15 = np.sum(amt[i - 14 : i + 1])
                cvd_15 = cvd[i] - cvd[i - 15]
                ret_15 = (c[i] / c[i - 15] - 1.0) * 100.0
                day_ret = (c[i] / o[0] - 1.0) * 100.0

                # IA1: 은밀한 선취매 (최근 15분 가격 변화는 -0.5%~+1.8%로 조용한데, 거래대금이 평소 4배+ & 순매수 델타 비율이 거래량의 40%+ 유입)
                if "IA1" not in used_today and vol_15 > 0:
                    if (
                        -0.5 <= ret_15 <= 1.8
                        and amt_15 >= (avg_min_amt * 15) * 4.0
                        and amt_15 >= 3.0e8  # 최소 15분간 3억 이상 실수급
                        and (cvd_15 / vol_15) >= 0.40
                        and c[i] >= vwap[i]
                    ):
                        record_event("IA1_STEALTH_CVD", i)
                        used_today.add("IA1")

                # IA2: 장초반(bar 15~45) 정보비대칭 첫 고점 돌파 (갭 과열 없을 때 강한 CVD + 거래대금 돌파)
                if "IA2" not in used_today and 15 <= i <= 45 and vol_15 > 0:
                    prev_hi = np.max(h[:i])
                    if (
                        0.5 <= day_ret <= 6.0
                        and c[i] >= prev_hi
                        and amt[i] >= avg_min_amt * 6.0
                        and amt[i] >= 1.0e8
                        and (cvd_15 / vol_15) >= 0.35
                    ):
                        record_event("IA2_MORNING_BRK", i)
                        used_today.add("IA2")

                # IA3: 오후장(bar 240~320, 13:00~14:20) 조용하던 종목에 갑작스러운 공시/소문 선취매 급습
                if "IA3" not in used_today and 240 <= i <= 320:
                    morning_range = (np.max(h[:200]) / max(1e-9, np.min(l[:200])) - 1.0) * 100.0
                    amt_3 = np.sum(amt[i - 2 : i + 1])
                    vol_3 = np.sum(v[i - 2 : i + 1])
                    cvd_3 = cvd[i] - cvd[i - 3]
                    if (
                        morning_range <= 4.0  # 오전 내내 변동폭 4% 이내로 잠잠
                        and amt_3 >= (avg_min_amt * 3) * 8.0
                        and amt_3 >= 3.0e8
                        and vol_3 > 0
                        and (cvd_3 / vol_3) >= 0.45
                        and 1.0 <= (c[i] / c[i - 3] - 1.0) * 100.0 <= 5.0
                    ):
                        record_event("IA3_AFTERNOON_NEWS", i)
                        used_today.add("IA3")

                # IA4: 장중 투매(-3% 이하 급락) 시 밑에서 스마트머니 시장가 매수 흡수(CVD 역행 다이버전스)
                if "IA4" not in used_today and vol_15 > 0:
                    vol_5 = np.sum(v[i - 4 : i + 1])
                    cvd_5 = cvd[i] - cvd[i - 5]
                    if (
                        ret_15 <= -3.0
                        and vol_5 > 0
                        and (cvd_5 / vol_5) >= 0.30  # 가격은 폭락했는데 최근 5분간 강한 순매수 체결 유입
                        and amt_15 >= 3.0e8
                        and c[i] > o[i]
                    ):
                        record_event("IA4_DIP_CVD_ABSORB", i)
                        used_today.add("IA4")

                # IA5: 당일 이미 거래대금이 터진(+5% 이상 상승) 주도 중소형주의 장중 첫 VWAP 눌림목 재점화
                if "IA5" not in used_today and 30 <= i <= 240:
                    max_ret_today = (np.max(h[:i]) / o[0] - 1.0) * 100.0
                    cum_amt_now = np.sum(amt[:i])
                    if (
                        max_ret_today >= 6.0
                        and cum_amt_now >= max(3.0e9, avg_day_amt * 1.5)
                        and abs(c[i] / vwap[i] - 1.0) <= 0.008  # VWAP ±0.8% 부근 눌림목
                        and c[i] > o[i]
                        and d_cvd[i] > 0
                        and amt[i] >= np.mean(amt[i - 5 : i]) * 1.5
                    ):
                        record_event("IA5_INPLAY_VWAP_PB", i)
                        used_today.add("IA5")

            recent_day_amts.append(day_total_amt)
            prev_day_close = c[-1]
            prev_day_amt = day_total_amt

    cost_pct = 0.28  # 중소형주 단타 왕복 마찰비용 (거래세 0.18% + 수수료 0.03% + 호가스프레드/슬리피지 0.07%)
    summary_list = []

    titles = {
        "IA1_STEALTH_CVD": "IA1. 은밀한 선취매 (가격 횡보 중 거래대금 4배 & CVD +40% 매집)",
        "IA2_MORNING_BRK": "IA2. 장초반(09:15~09:45) 정보비대칭 고점 돌파 + CVD 집중",
        "IA3_AFTERNOON_NEWS": "IA3. 오후장(13:00~14:20) 잠잠하던 종목 급작스런 수급/CVD 폭발",
        "IA4_DIP_CVD_ABSORB": "IA4. 장중 -3% 투매 중 CVD 양(+) 역행 흡수 (투매 받아먹기)",
        "IA5_INPLAY_VWAP_PB": "IA5. 당일 거래대금 폭발 주도주의 첫 VWAP 눌림목 CVD 재점화",
    }

    for k, title in titles.items():
        edf = pd.DataFrame(events[k])
        if len(edf) == 0:
            continue
        row = {"id": k, "title": title, "count": int(len(edf))}
        line = f"  • {title:<58} (n={len(edf):4d}) | "
        for col, h_name in [("r15", "15분"), ("r30", "30분"), ("r60", "60분"), ("reod", "장마감")]:
            gross = float(edf[col].mean())
            net = gross - cost_pct
            wr_net = float((edf[col] > cost_pct).mean() * 100.0)
            daily_net = edf.groupby("date")[col].mean().values - cost_pct
            t_val = float(np.mean(daily_net) / (np.std(daily_net, ddof=1) / np.sqrt(len(daily_net)))) if len(daily_net) > 2 else 0.0
            row[f"{col}_gross_pct"] = round(gross, 3)
            row[f"{col}_net_pct"] = round(net, 3)
            row[f"{col}_wr_pct"] = round(wr_net, 1)
            row[f"{col}_t"] = round(t_val, 2)
            line += f"{h_name}: 세전{gross:+5.2f}%/세후{net:+5.2f}%(t={t_val:+4.1f}) | "
        print(line)
        summary_list.append(row)

    return summary_list


def main():
    top5, top80 = select_low_inst_asymmetry_stocks(top_n_baker=5, top_n_universe=80)
    ia_res = run_orderflow_asymmetry_hypotheses(top80)
    baker_res = run_baker_on_smallcaps(top5)

    out_path = os.path.join(OUT_DIR, "info_asymmetry_intraday_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "selected_top5": top5,
                "orderflow_hypotheses_80stocks": ia_res,
                "baker_smallcap_cases": baker_res,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
