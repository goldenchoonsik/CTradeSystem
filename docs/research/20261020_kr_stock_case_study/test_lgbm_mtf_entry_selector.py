"""
LightGBM 다중 타임프레임(MTF) 진입 타이밍 선별기 (Meta-Labeling) 검증:
1) [실험 1: ETF 장중 MTF 타점 선별 (KODEX 200 / KODEX 레버리지)]
   - 아무 봉이나 다 넣는 기존 방식 vs 1분봉 후보 구간에서 [일봉 추세 + 장초반 20분 수급 + 1분봉 VWAP/거래량/CVD + 5초/10초봉 초단위 가속도] 피처를
     LightGBM에 먹여 '진짜 5분~15분 파도가 터질 상위 확률(Top 20~30%) 타점'만 골라냈을 때 (Walk-Forward OOS 검증, 한투 우대수수료 0.0073% 적용).
2) [실험 2: 국내 개별주 일봉 거래량 폭증/눌림목(G7/G8 & V3) LightGBM 타점 랭킹]
   - 2024~2025년 학습(Train) -> 2026년 완전 미관측 구간(OOS Test)에서 LightGBM이 고른 상위 타점의 실제 세후(0.23% 차감) 수익률 검증.

실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/test_lgbm_mtf_entry_selector.py
"""
import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
import lightgbm as lgb

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_mtf_1m_to_subsec import load_all_valid_tick_days, OUT_DIR, ROOT_DIR

KIS_VIP_FEE = 0.0073  # 한투 우대수수료 왕복 0.0073% (세금 0%)


def build_etf_mtf_dataset(code: str) -> pd.DataFrame:
    days = load_all_valid_tick_days(code)
    if not days:
        return pd.DataFrame()

    d_closes = [float(d["m1"]["close"].iloc[-1]) for d in days]
    d_vols = [float(d["m1"]["volume"].sum()) for d in days]

    rows = []
    for d_idx, d in enumerate(days):
        if d_idx < 5:
            continue
        m1 = d["m1"].copy()
        ticks = d["ticks"].reset_index(drop=True)
        if len(m1) < 120:
            continue

        # 1) Daily context features (Strictly up to yesterday d_idx - 1)
        prev_close = d_closes[d_idx - 1]
        ma5_close = float(np.mean(d_closes[d_idx - 5 : d_idx]))
        prev_ret_1d = (d_closes[d_idx - 1] / d_closes[d_idx - 2] - 1.0) * 100.0
        prev_ret_5d = (d_closes[d_idx - 1] / d_closes[d_idx - 5] - 1.0) * 100.0
        prev_vol_ratio = d_vols[d_idx - 1] / max(1.0, float(np.mean(d_vols[d_idx - 5 : d_idx])))

        day_open = float(m1["open"].iloc[0])
        gap_pct = (day_open / prev_close - 1.0) * 100.0

        # 2) Intraday 1m rolling features
        m1["cum_vol"] = m1["volume"].cumsum()
        m1["cum_pv"] = (m1["close"] * m1["volume"]).cumsum()
        m1["vwap"] = m1["cum_pv"] / np.maximum(1.0, m1["cum_vol"])
        m1["vol_ma15"] = m1["volume"].rolling(15, min_periods=5).mean()
        m1["cvd_ma15"] = m1["d_cvd"].rolling(15, min_periods=5).mean()
        m1["high_so_far"] = m1["high"].cummax()
        m1["low_so_far"] = m1["low"].cummin()

        # Morning 20m stats
        m20_vol = float(m1.iloc[:20]["volume"].sum())
        m20_cvd = float(m1.iloc[:20]["d_cvd"].sum())
        m20_ret = (float(m1.iloc[19]["close"]) / day_open - 1.0) * 100.0
        or_high20 = float(m1.iloc[:20]["high"].max())

        # Scan candidate minutes i from 20 (09:20) to len(m1) - 35 (14:45)
        for i in range(20, len(m1) - 35):
            r_prev = m1.iloc[i - 1]
            r_prev2 = m1.iloc[i - 2]
            r_curr = m1.iloc[i]

            min_ts = r_curr["min_ts"]
            sub_t = ticks[ticks["min_ts"] == min_ts]
            if len(sub_t) < 6:
                continue

            # [중요 무결성 검증]: 원시 틱 CSV의 timestamp는 '분(HH:MM:00)' 단위까지만 기록되어 있어,
            # 한 분봉 내 전체 틱 개수(cnt)의 1/6을 첫 10초로 가정하면 해당 분봉 전체의 틱 폭발 여부(cnt)를 미리 알게 되는 미세 누수가 발생함!
            # 따라서 100% 누수가 없는 두 가지 정밀 측정 방식을 비교:
            # 방식 A (Fixed N-tick trigger): 분봉 시작 후 정확히 첫 K개 틱(예: 15틱)만 보고 진입 (해당 분봉 총 틱수 cnt 참조 0%!)
            # 방식 B (1m Bar Close trigger): 직전 1분봉(i-1)이 완전히 끝난 종가에 진입
            if len(sub_t) < 15:
                continue
            w_fixed = sub_t.iloc[:15]  # 정확히 첫 15틱만 관측 (미래 45초 동안 몇 틱이 더 터질지 전혀 모름!)
            entry_px = float(w_fixed["upbit_close"].iloc[-1])
            w10_ret = (entry_px / float(w_fixed["upbit_close"].iloc[0]) - 1.0) * 100.0
            w10_vol = float(w_fixed["upbit_volume"].sum())
            w10_dcvd = float(w_fixed["d_cvd"].sum())
            w10_cvd_imb = w10_dcvd / max(1.0, w10_vol)
            w5_ret = (float(w_fixed["upbit_close"].iloc[5]) / float(w_fixed["upbit_close"].iloc[0]) - 1.0) * 100.0

            if w10_dcvd <= 0:
                continue

            # Features combining Daily + Morning 20m + 1m Context (up to i-1) + Sub-minute (first 10s of i)
            feat = {
                "date": d["date"],
                "d_idx": d_idx,
                "min_idx": i,
                # Daily context
                "daily_above_ma5": (prev_close / ma5_close - 1.0) * 100.0,
                "prev_ret_1d": prev_ret_1d,
                "prev_ret_5d": prev_ret_5d,
                "prev_vol_ratio": prev_vol_ratio,
                "gap_pct": gap_pct,
                # Morning 20m context
                "m20_cvd_ratio": m20_cvd / max(1.0, m20_vol),
                "m20_ret": m20_ret,
                "dist_or_high20_pct": (entry_px / or_high20 - 1.0) * 100.0,
                # 1-minute context (strictly i-1)
                "m1_dist_vwap_pct": (float(r_prev["close"]) / float(r_prev["vwap"]) - 1.0) * 100.0,
                "m1_dist_day_open_pct": (float(r_prev["close"]) / day_open - 1.0) * 100.0,
                "m1_dist_high_sofar_pct": (float(r_prev["close"]) / float(r_prev["high_so_far"]) - 1.0) * 100.0,
                "m1_vol_ratio15": float(r_prev["volume"]) / max(1.0, float(r_prev["vol_ma15"])),
                "m1_prev_ret": (float(r_prev["close"]) / float(r_prev["open"]) - 1.0) * 100.0,
                "m1_ret_3m": (float(r_prev["close"]) / float(m1.iloc[i - 3]["close"]) - 1.0) * 100.0,
                "m1_cum_cvd_ratio": float(r_prev["cvd_last"]) / max(1.0, float(r_prev["cum_vol"])),
                "m1_dcvd_ratio": float(r_prev["d_cvd"]) / max(1.0, float(r_prev["volume"])),
                # Sub-minute (2s/5s/10s) trigger features inside minute i (0..10s)
                "sub10s_ret": w10_ret,
                "sub5s_ret": w5_ret,
                "sub10s_vol_vs_1m_avg": (w10_vol * 6.0) / max(1.0, float(r_prev["vol_ma15"])),
                "sub10s_cvd_imb": w10_cvd_imb,
                # Forward returns from entry_px (at 10s of minute i)
                "fwd_50s": (float(r_curr["close"]) / entry_px - 1.0) * 100.0,  # end of minute i (+50s)
                "fwd_5m": (float(m1.iloc[i + 5]["close"]) / entry_px - 1.0) * 100.0,
                "fwd_15m": (float(m1.iloc[i + 15]["close"]) / entry_px - 1.0) * 100.0,
                "fwd_30m": (float(m1.iloc[i + 30]["close"]) / entry_px - 1.0) * 100.0,
            }
            rows.append(feat)

    return pd.DataFrame(rows)


def evaluate_etf_lgbm(code: str, name: str):
    df = build_etf_mtf_dataset(code)
    if len(df) < 200:
        return {}

    feature_cols = [
        "daily_above_ma5",
        "prev_ret_1d",
        "prev_ret_5d",
        "prev_vol_ratio",
        "gap_pct",
        "m20_cvd_ratio",
        "m20_ret",
        "dist_or_high20_pct",
        "m1_dist_vwap_pct",
        "m1_dist_day_open_pct",
        "m1_dist_high_sofar_pct",
        "m1_vol_ratio15",
        "m1_prev_ret",
        "m1_ret_3m",
        "m1_cum_cvd_ratio",
        "m1_dcvd_ratio",
        "sub10s_ret",
        "sub5s_ret",
        "sub10s_vol_vs_1m_avg",
        "sub10s_cvd_imb",
    ]

    # Walk-Forward Out-of-Sample Evaluation (Expanding Window by trading day)
    unique_days = sorted(df["d_idx"].unique())
    split_idx = int(len(unique_days) * 0.55)  # First 55% days warmup, last 45% days strictly Out-of-Sample Walk-Forward

    print("\n" + "=" * 125)
    print(f"▶ [LightGBM MTF 진입 타이밍 선별기 (Walk-Forward OOS)] {name} ({code}) | 한투 우대수수료 왕복 {KIS_VIP_FEE:.4f}% 적용")
    print("=" * 125)

    out_horizons = {}
    for target_col, h_label, min_target_pct in [
        ("fwd_50s", "50초(해당 1분봉 종가) 보유", 0.02),
        ("fwd_5m", "5분 보유", 0.04),
        ("fwd_15m", "15분 보유", 0.06),
        ("fwd_30m", "30분 보유", 0.08),
    ]:
        oos_preds = []
        importances = np.zeros(len(feature_cols))
        n_models = 0

        # Step forward every 3 days to retrain LightGBM strictly on past days
        for t_pos in range(split_idx, len(unique_days), 3):
            train_days = unique_days[:t_pos]
            test_days = unique_days[t_pos : t_pos + 3]

            # Purge 1 day gap if needed; intraday horizons (<=30m) don't overlap across days!
            tr_df = df[df["d_idx"].isin(train_days)]
            te_df = df[df["d_idx"].isin(test_days)].copy()
            if len(tr_df) < 100 or len(te_df) == 0:
                continue

            y_tr = (tr_df[target_col] >= min_target_pct).astype(int).values
            X_tr = tr_df[feature_cols].values
            X_te = te_df[feature_cols].values

            dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=feature_cols)
            params = {
                "objective": "binary",
                "metric": "binary_logloss",
                "max_depth": 3,
                "num_leaves": 7,
                "min_data_in_leaf": 40,
                "learning_rate": 0.04,
                "bagging_fraction": 0.8,
                "bagging_freq": 1,
                "feature_fraction": 0.8,
                "seed": 42,
                "verbose": -1,
            }
            booster = lgb.train(params, dtrain, num_boost_round=80)
            tr_preds = booster.predict(X_tr)
            te_preds = booster.predict(X_te)
            te_df["pred_prob"] = te_preds
            te_df["causal_top20"] = te_preds >= float(np.quantile(tr_preds, 0.80))
            te_df["causal_top10"] = te_preds >= float(np.quantile(tr_preds, 0.90))
            te_df["causal_top5"] = te_preds >= float(np.quantile(tr_preds, 0.95))
            oos_preds.append(te_df)
            importances += booster.feature_importance(importance_type="gain")
            n_models += 1

        oos_df = pd.concat(oos_preds, ignore_index=True)

        print(f"\n  [{h_label} 목표 학습 (타겟 >= +{min_target_pct:.2f}%)] (OOS 전체 후보 n={len(oos_df)}):")
        h_res = {}
        # De-cluster helper: enforce cooldown of at least hold_mins minutes within the same day
        hold_mins = 1 if target_col == "fwd_50s" else int(target_col.replace("fwd_", "").replace("m", ""))
        spread_1tick_pct = 0.0064 if code == "A069500" else 0.0130  # 1호가(5원) 시장가 슬리피지

        def decluster(sub_df: pd.DataFrame) -> pd.DataFrame:
            kept = []
            for _, g in sub_df.sort_values(["d_idx", "min_idx"]).groupby("d_idx", sort=False):
                last_m = -999
                for _, r in g.iterrows():
                    if int(r["min_idx"]) - last_m >= hold_mins:
                        kept.append(r)
                        last_m = int(r["min_idx"])
            return pd.DataFrame(kept) if kept else pd.DataFrame()

        for sel_name, sub in [
            ("① 무필터 전체 진입 (Baseline)", oos_df),
            ("② LightGBM 상위 20% 타점 선별 (실시간 임계치)", oos_df[oos_df["causal_top20"]]),
            ("③ LightGBM 상위 10% 타점 선별 (실시간 임계치)", oos_df[oos_df["causal_top10"]]),
            ("④ LightGBM 상위  5% 핵심 타점 (실시간 임계치)", oos_df[oos_df["causal_top5"]]),
            ("⑤ LightGBM 상위  5% (중복제거 + 한투우대 + 시장가 1틱 슬리피지 차감)", decluster(oos_df[oos_df["causal_top5"]])),
        ]:
            if len(sub) == 0:
                continue
            extra_slip = spread_1tick_pct if "슬리피지 차감" in sel_name else 0.0
            total_cost = KIS_VIP_FEE + extra_slip
            gross = float(sub[target_col].mean())
            net = gross - total_cost
            wr_net = float((sub[target_col] > total_cost).mean() * 100.0)
            print(
                f"    • {sel_name:<56} (n={len(sub):4d}) | 세전 {gross:+6.3f}% -> 실전세후 {net:+6.3f}% | 세후승률 {wr_net:5.1f}%"
            )
            h_res[sel_name] = {
                "n": int(len(sub)),
                "gross_pct": round(gross, 4),
                "net_pct": round(net, 4),
                "win_rate_net_pct": round(wr_net, 1),
            }

        top_feat_idx = np.argsort(importances)[::-1][:5]
        top_feats = [f"{feature_cols[idx]}({importances[idx]/max(1,n_models):.1f})" for idx in top_feat_idx]
        print(f"    * 주요 피처 중요도 Top 5: {', '.join(top_feats)}")
        h_res["top_features"] = top_feats
        out_horizons[target_col] = h_res

    return {"code": code, "name": name, "horizons": out_horizons}


def compare_retrain_and_1m_close(code: str, name: str):
    """
    1) 재학습 주기 비교: [1회 학습 후 완전 고정(재학습 없음)] vs [주 1회(5거래일) 재학습] vs [3거래일마다 재학습]
    2) 진입 방식 비교: [방안 A: 기존 앱 호환 1분봉 완성 종가 진입] vs [방안 B: 분봉 내 첫 15틱 확인 후 진입]
    모두 5분 보유(fwd_5m), 실시간 학습 임계치(causal_top5), 중복진입 제거, 한투우대(0.0073%)+시장가 1틱 슬리피지 차감 기준!
    """
    df = build_etf_mtf_dataset(code)
    unique_days = sorted(df["d_idx"].unique())
    split_idx = int(len(unique_days) * 0.55)
    spread_1tick_pct = 0.0064 if code == "A069500" else 0.0130
    total_cost = KIS_VIP_FEE + spread_1tick_pct

    # Also compute pure 1m-close forward 5m return (entering at r_prev["close"], i.e., minute i-1 close -> minute i+4 close)
    # Note: in build_etf_mtf_dataset, m1_dist_day_open_pct etc. are strictly at i-1!
    # Wait: let's compute exact 1m-close dataset directly from days_data
    days = load_all_valid_tick_days(code)
    d_closes = [float(d["m1"]["close"].iloc[-1]) for d in days]
    d_vols = [float(d["m1"]["volume"].sum()) for d in days]
    rows_1m = []
    for d_idx, d in enumerate(days):
        if d_idx < 5:
            continue
        m1 = d["m1"].copy()
        if len(m1) < 120:
            continue
        prev_close = d_closes[d_idx - 1]
        ma5_close = float(np.mean(d_closes[d_idx - 5 : d_idx]))
        prev_ret_1d = (d_closes[d_idx - 1] / d_closes[d_idx - 2] - 1.0) * 100.0
        prev_ret_5d = (d_closes[d_idx - 1] / d_closes[d_idx - 5] - 1.0) * 100.0
        prev_vol_ratio = d_vols[d_idx - 1] / max(1.0, float(np.mean(d_vols[d_idx - 5 : d_idx])))
        day_open = float(m1["open"].iloc[0])
        gap_pct = (day_open / prev_close - 1.0) * 100.0
        m1["cum_vol"] = m1["volume"].cumsum()
        m1["vwap"] = (m1["close"] * m1["volume"]).cumsum() / np.maximum(1.0, m1["cum_vol"])
        m1["vol_ma15"] = m1["volume"].rolling(15, min_periods=5).mean()
        m1["high_so_far"] = m1["high"].cummax()
        m20_vol = float(m1.iloc[:20]["volume"].sum())
        m20_cvd = float(m1.iloc[:20]["d_cvd"].sum())
        m20_ret = (float(m1.iloc[19]["close"]) / day_open - 1.0) * 100.0
        or_high20 = float(m1.iloc[:20]["high"].max())

        for i in range(20, len(m1) - 35):
            r_prev = m1.iloc[i - 1]
            if float(r_prev["d_cvd"]) <= 0:
                continue
            px0 = float(r_prev["close"])
            rows_1m.append({
                "d_idx": d_idx,
                "min_idx": i,
                "daily_above_ma5": (prev_close / ma5_close - 1.0) * 100.0,
                "prev_ret_1d": prev_ret_1d,
                "prev_ret_5d": prev_ret_5d,
                "prev_vol_ratio": prev_vol_ratio,
                "gap_pct": gap_pct,
                "m20_cvd_ratio": m20_cvd / max(1.0, m20_vol),
                "m20_ret": m20_ret,
                "dist_or_high20_pct": (px0 / or_high20 - 1.0) * 100.0,
                "m1_dist_vwap_pct": (px0 / float(r_prev["vwap"]) - 1.0) * 100.0,
                "m1_dist_day_open_pct": (px0 / day_open - 1.0) * 100.0,
                "m1_dist_high_sofar_pct": (px0 / float(r_prev["high_so_far"]) - 1.0) * 100.0,
                "m1_vol_ratio15": float(r_prev["volume"]) / max(1.0, float(r_prev["vol_ma15"])),
                "m1_prev_ret": (px0 / float(r_prev["open"]) - 1.0) * 100.0,
                "m1_ret_3m": (px0 / float(m1.iloc[i - 3]["close"]) - 1.0) * 100.0,
                "m1_cum_cvd_ratio": float(r_prev["cvd_last"]) / max(1.0, float(r_prev["cum_vol"])),
                "m1_dcvd_ratio": float(r_prev["d_cvd"]) / max(1.0, float(r_prev["volume"])),
                "fwd_5m": (float(m1.iloc[i + 4]["close"]) / px0 - 1.0) * 100.0,
            })
    df_1m = pd.DataFrame(rows_1m)

    cols_mtf_15t = [
        "daily_above_ma5", "prev_ret_1d", "prev_ret_5d", "prev_vol_ratio", "gap_pct",
        "m20_cvd_ratio", "m20_ret", "dist_or_high20_pct", "m1_dist_vwap_pct", "m1_dist_day_open_pct",
        "m1_dist_high_sofar_pct", "m1_vol_ratio15", "m1_prev_ret", "m1_ret_3m", "m1_cum_cvd_ratio", "m1_dcvd_ratio",
        "sub10s_ret", "sub5s_ret", "sub10s_vol_vs_1m_avg", "sub10s_cvd_imb",
    ]
    cols_pure_1m = cols_mtf_15t[:16]

    def run_sim(data_df, fcols, step_days):
        oos_list = []
        for t_pos in range(split_idx, len(unique_days), step_days):
            tr_days = unique_days[:t_pos]
            te_days = unique_days[t_pos : t_pos + step_days]
            tr_df = data_df[data_df["d_idx"].isin(tr_days)]
            te_df = data_df[data_df["d_idx"].isin(te_days)].copy()
            if len(tr_df) < 100 or len(te_df) == 0:
                continue
            dtrain = lgb.Dataset(tr_df[fcols].values, label=(tr_df["fwd_5m"] >= 0.04).astype(int).values)
            params = {
                "objective": "binary", "metric": "binary_logloss", "max_depth": 3, "num_leaves": 7,
                "min_data_in_leaf": 40, "learning_rate": 0.04, "bagging_fraction": 0.8, "bagging_freq": 1,
                "feature_fraction": 0.8, "seed": 42, "verbose": -1,
            }
            booster = lgb.train(params, dtrain, num_boost_round=80)
            tr_p = booster.predict(tr_df[fcols].values)
            te_p = booster.predict(te_df[fcols].values)
            te_df["top5"] = te_p >= float(np.quantile(tr_p, 0.95))
            oos_list.append(te_df)
        oos = pd.concat(oos_list, ignore_index=True)
        sub = oos[oos["top5"]]
        kept = []
        for _, g in sub.sort_values(["d_idx", "min_idx"]).groupby("d_idx", sort=False):
            last_m = -999
            for _, r in g.iterrows():
                if int(r["min_idx"]) - last_m >= 5:
                    kept.append(r)
                    last_m = int(r["min_idx"])
        kdf = pd.DataFrame(kept)
        gross = float(kdf["fwd_5m"].mean())
        net = gross - total_cost
        wr = float((kdf["fwd_5m"] > total_cost).mean() * 100.0)
        return len(kdf), gross, net, wr, net * len(kdf)

    print(f"\n=========================================================================================================")
    print(f"▶ {name} ({code}) | 5분 보유 상위 5% (중복제거 + 한투우대 + 시장가 1틱 슬리피지 = 총비용 {total_cost:.4f}% 차감)")
    print(f"=========================================================================================================")
    for lbl, d_in, c_in, stp in [
        ("① [방안B: 1분봉+첫15틱] 1회 학습 후 완전 고정 (재학습 0회, 3주 방치)", df, cols_mtf_15t, 999),
        ("② [방안B: 1분봉+첫15틱] 주 1회(5거래일마다) 주말 재학습", df, cols_mtf_15t, 5),
        ("③ [방안B: 1분봉+첫15틱] 3거래일마다 재학습", df, cols_mtf_15t, 3),
        ("④ [방안A: 순수 1분봉 완성 진입] 1회 학습 후 완전 고정 (재학습 0회)", df_1m, cols_pure_1m, 999),
        ("⑤ [방안A: 순수 1분봉 완성 진입] 주 1회(5거래일마다) 주말 재학습", df_1m, cols_pure_1m, 5),
        ("⑥ [방안A: 순수 1분봉 완성 진입] 3거래일마다 재학습", df_1m, cols_pure_1m, 3),
    ]:
        n, g, net, wr, cum = run_sim(d_in, c_in, stp)
        print(f"  • {lbl:<60} (n={n:3d}) | 세전 {g:+6.3f}% -> 실전세후 {net:+6.3f}% (승률 {wr:5.1f}%, 누적 {cum:+6.2f}%)")


def audit_3day_retrain_consistency(code: str, name: str):
    """
    '3일마다 돌리면 수익이 진짜 꾸준히 유지되는가, 아니면 특정 1~2일 대박 착시인가?'
    1) 재학습 주기 1일 / 2일 / 3일 / 4일 / 5일 연속성 비교
    2) 3일 재학습 시 각 3일 구간(Fold 1~5) 및 개별 거래일(15일) 각각의 손익 내역 100% 해부
    """
    df = build_etf_mtf_dataset(code)
    unique_days = sorted(df["d_idx"].unique())
    split_idx = int(len(unique_days) * 0.55)
    spread_1tick_pct = 0.0064 if code == "A069500" else 0.0130
    total_cost = KIS_VIP_FEE + spread_1tick_pct

    fcols = [
        "daily_above_ma5", "prev_ret_1d", "prev_ret_5d", "prev_vol_ratio", "gap_pct",
        "m20_cvd_ratio", "m20_ret", "dist_or_high20_pct", "m1_dist_vwap_pct", "m1_dist_day_open_pct",
        "m1_dist_high_sofar_pct", "m1_vol_ratio15", "m1_prev_ret", "m1_ret_3m", "m1_cum_cvd_ratio", "m1_dcvd_ratio",
        "sub10s_ret", "sub5s_ret", "sub10s_vol_vs_1m_avg", "sub10s_cvd_imb",
    ]

    print(f"\n=========================================================================================================")
    print(f"▶ [정밀 감사] {name} ({code}) | 총 마찰비용(한투우대+1틱슬리피지) = {total_cost:.4f}% 차감")
    print(f"=========================================================================================================")

    for step_days in [1, 2, 3, 4, 5, 7, 999]:
        oos_list = []
        fold_stats = []
        for f_no, t_pos in enumerate(range(split_idx, len(unique_days), step_days), 1):
            tr_days = unique_days[:t_pos]
            te_days = unique_days[t_pos : t_pos + step_days]
            tr_df = df[df["d_idx"].isin(tr_days)]
            te_df = df[df["d_idx"].isin(te_days)].copy()
            if len(tr_df) < 100 or len(te_df) == 0:
                continue
            dtrain = lgb.Dataset(tr_df[fcols].values, label=(tr_df["fwd_5m"] >= 0.04).astype(int).values)
            params = {
                "objective": "binary", "metric": "binary_logloss", "max_depth": 3, "num_leaves": 7,
                "min_data_in_leaf": 40, "learning_rate": 0.04, "bagging_fraction": 0.8, "bagging_freq": 1,
                "feature_fraction": 0.8, "seed": 42, "verbose": -1,
            }
            booster = lgb.train(params, dtrain, num_boost_round=80)
            tr_p = booster.predict(tr_df[fcols].values)
            te_p = booster.predict(te_df[fcols].values)
            te_df["top5"] = te_p >= float(np.quantile(tr_p, 0.95))
            te_df["fold"] = f_no
            oos_list.append(te_df)

        oos = pd.concat(oos_list, ignore_index=True)
        sub = oos[oos["top5"]]
        kept = []
        for _, g in sub.sort_values(["d_idx", "min_idx"]).groupby("d_idx", sort=False):
            last_m = -999
            for _, r in g.iterrows():
                if int(r["min_idx"]) - last_m >= 5:
                    kept.append(r)
                    last_m = int(r["min_idx"])
        kdf = pd.DataFrame(kept)
        kdf["net"] = kdf["fwd_5m"] - total_cost

        # Day-level aggregation
        day_grp = kdf.groupby("date").agg(n=("net", "count"), sum_net=("net", "sum"), mean_net=("net", "mean")).reset_index()
        win_days = int((day_grp["sum_net"] > 0).sum())
        tot_days = len(day_grp)
        max_day_contrib = float(day_grp["sum_net"].max()) if tot_days > 0 else 0.0
        sum_without_best_day = float(day_grp["sum_net"].sum() - max_day_contrib) if tot_days > 0 else 0.0

        lbl = f"{step_days}거래일마다 재학습" if step_days < 900 else "재학습 없음(고정)"
        print(
            f"  • [{lbl:<15}] 거래 {len(kdf):3d}회 | 세전 {kdf['fwd_5m'].mean():+6.3f}% -> 실전세후 {kdf['net'].mean():+6.3f}% "
            f"| 총누적 {kdf['net'].sum():+6.2f}% | 수익일수 {win_days}/{tot_days}일 "
            f"| 최고 대박 1일({max_day_contrib:+.2f}%) 제외 시 누적: {sum_without_best_day:+6.2f}%"
        )

        if step_days == 3:
            print("    -----------------------------------------------------------------------------------------------------")
            print("    [3거래일마다 재학습 시 일자별(Day-by-Day) 실제 성적표]:")
            for _, dr in day_grp.iterrows():
                print(f"      - {dr['date']}: 거래 {int(dr['n']):2d}회 | 당일 평균 세후 {dr['mean_net']:+6.3f}% | 당일 누적 세후 {dr['sum_net']:+6.2f}%")
            print("    -----------------------------------------------------------------------------------------------------")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "audit3d":
        for code, name in [
            ("A122630", "KODEX 레버리지 (무세금 2배 ETF)"),
            ("A069500", "KODEX 200 (무세금 대표 지수 ETF)"),
        ]:
            audit_3day_retrain_consistency(code, name)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "retrain":
        for code, name in [
            ("A069500", "KODEX 200 (무세금 대표 지수 ETF)"),
            ("A122630", "KODEX 레버리지 (무세금 2배 ETF)"),
        ]:
            compare_retrain_and_1m_close(code, name)
        return
    out = []
    for code, name in [
        ("A069500", "KODEX 200 (무세금 대표 지수 ETF)"),
        ("A122630", "KODEX 레버리지 (무세금 2배 ETF)"),
    ]:
        out.append(evaluate_etf_lgbm(code, name))

    out_path = os.path.join(OUT_DIR, "lgbm_mtf_entry_selector_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
