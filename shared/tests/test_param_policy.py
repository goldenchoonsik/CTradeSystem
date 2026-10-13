# -*- coding: utf-8 -*-
"""지표 파라미터 정책(scale_v2) 래칫 — shared/features/param_policy.py

규칙이 깨지면 15분봉 같은 긴 봉에서 1봉짜리 지표·하루 안에 안 차는 창·장중 RSI 를 '일봉'이라 부르는 지표가
다시 조용히 학습에 들어간다 (2026-10-07).
"""
import numpy as np
import pandas as pd
import pytest

from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY as R, FEATURE_ALIASES
from shared.features.param_policy import (
    POLICY_V2, resolve_feature_params, resolve_feature_config, project_horizon_bars, session_bars_per_day,
)


def _resolve(fid, interval, H, user=None, market="KRX"):
    return resolve_feature_params(fid, R[fid].get("params", {}), user or {}, interval, H, market)


def test_stat_params_keep_bar_count_on_every_interval():
    """RSI 14 는 어느 주기에서나 14봉 — 통계 표본 수다."""
    for iv in ("10s", "1m", "15m"):
        p, problem, _ = _resolve("target_rsi_curr", iv, 4)
        assert p["period_bars"] == 14 and problem is None


def test_flow_params_follow_the_prediction_horizon():
    """흐름 지표의 측정 구간은 예측 지평(H봉) — 10분 창이 15분봉에서 1봉으로 무너지지 않는다."""
    p, problem, _ = _resolve("target_trade_bid_ratio", "15m", 4)
    assert problem is None
    assert round(p["window_mins"] / 15) == 4
    p, _, _ = _resolve("target_cvd_absorption", "1m", 6)
    assert p["candles"] == 6


def test_paired_windows_keep_registry_ratio():
    p, _, _ = _resolve("target_rv_ratio", "15m", 4)
    d = R["target_rv_ratio"]["params"]
    assert p["short_bars"] == 4
    assert p["long_bars"] == round(4 * d["long_bars"]["default"] / d["short_bars"]["default"])


def test_krx_horizon_is_capped_at_one_session():
    """장중 청산 모델의 지평은 하루를 넘지 못한다 (예측 범위 중앙 52봉 → 15분봉 하루 26봉)."""
    p, _, _ = _resolve("target_cvd_absorption", "15m", 52)
    assert p["candles"] == session_bars_per_day("15m", "KRX") == 26


def test_explicit_user_value_is_honoured_and_validated():
    p, problem, _ = _resolve("target_trade_bid_ratio", "15m", 4, user={"window_mins": 120})
    assert p["window_mins"] == 120 and problem is None
    _, problem, _ = _resolve("target_trade_bid_ratio", "15m", 4, user={"window_mins": 15})
    assert problem and "최소" in problem, "1봉짜리 흐름 창은 쓸 수 없다고 표시해야 한다"


def test_registry_default_copied_from_another_project_is_not_a_user_choice():
    """1분봉 프로젝트에서 복사된 기본값(window_mins=10)이 15분봉에서 1봉으로 굳지 않는다."""
    p, problem, _ = _resolve("target_trade_bid_ratio", "15m", 4, user={"window_mins": 10})
    assert problem is None and round(p["window_mins"] / 15) == 4


def test_window_key_is_pinned_as_bars():
    """'window' 는 계산 함수에서 60↑=분, 'candles' 뒤에 읽혀 top2 의 z-score 창이 5봉이었다 → window_bars 로 고정."""
    p, _, _ = _resolve("top2_lead_momentum", "15m", 4)
    assert p["window_bars"] == 60


def test_daily_feature_history_budget():
    _, problem, req = _resolve("target_rsi_daily", "15m", 4)
    assert problem is None and req >= 15 * 26
    _, problem, _ = _resolve("target_rsi_daily", "10s", 36)
    assert problem and "예산" in problem


def test_project_horizon_from_config():
    assert project_horizon_bars({"TARGET_HORIZON_CONFIG": {"mode": "locked", "locked_predict_ahead": 4}}) == 4
    assert project_horizon_bars({"OPTUNA_CONFIG": {"predict_ahead_candles_range": {"user_min": 8, "user_max": 96}}}) == 52


def _krx_15m_frame(days=40):
    rng = np.random.default_rng(3)
    idx = []
    for d in pd.bdate_range("2026-08-03", periods=days):
        idx += list(pd.date_range(d + pd.Timedelta("9h"), d + pd.Timedelta("15h15min"), freq="15min"))
    n = len(idx)
    close = 30000 + np.cumsum(rng.normal(0, 30, n))
    open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 20, n)   # 매일 첫 봉은 밤사이 갭을 갖는다
    df = pd.DataFrame({"timestamp": idx, "target_close": close, "target_open": open_,
                       "target_high": np.maximum(open_, close) + 5, "target_low": np.minimum(open_, close) - 5,
                       "target_volume": rng.uniform(1e4, 2e4, n)})
    for c in ("close", "open", "high", "low", "volume"):
        df[f"upbit_{c}"] = df[f"target_{c}"]
    return df


def test_v2_engine_is_continuous_across_sessions():
    """legacy(날짜별)에서 전일 갭은 첫 봉이 늘 0, 일봉 RSI 는 장중 RSI 였다 — v2 는 실제 값."""
    from shared.features.feature_engine import run_feature_engineering
    df = _krx_15m_frame()
    cfg = {"SYMBOL_UPBIT": "069500", "TARGET_HORIZON_CONFIG": {"mode": "locked", "locked_predict_ahead": 4}}
    feats = ["overnight_gap_pct", "target_rsi_curr", "target_macd_hist"]
    v2 = run_feature_engineering(df.copy(), "15m", {"enabled_features": feats, "feature_params": {},
                                                     "param_policy": POLICY_V2}, cfg)
    legacy = run_feature_engineering(df.copy(), "15m", {"enabled_features": feats, "feature_params": {}}, cfg)
    day = pd.to_datetime(v2["timestamp"]).dt.date
    assert (legacy.groupby(day)["overnight_gap_pct"].first() == 0).all()
    assert (v2.groupby(day)["overnight_gap_pct"].first().iloc[1:] != 0).all()
    assert (v2.groupby(day)["target_rsi_daily"].nunique() == 1).all(), "일봉 RSI 는 하루 한 값(전일 확정)"
    assert v2.attrs["required_history_bars"] > 0


def test_v2_marks_unusable_features_nan_with_reason():
    from shared.features.feature_engine import run_feature_engineering
    df = _krx_15m_frame(5)
    fc = {"enabled_features": ["target_trade_bid_ratio"], "feature_params": {"target_trade_bid_ratio": {"window_mins": 15}},
          "param_policy": POLICY_V2}
    out = run_feature_engineering(df, "15m", fc, {"SYMBOL_UPBIT": "069500"})
    assert out["target_trade_bid_ratio"].isna().all()
    assert "target_trade_bid_ratio" in out.attrs["feature_param_invalid"]


def test_resolved_config_is_idempotent_for_live():
    """실전은 스펙의 확정값을 그대로 쓴다 — 확정값을 다시 확정해도 같다."""
    fc = {"enabled_features": ["target_rv_ratio", "target_rsi_curr", "top2_lead_momentum"], "feature_params": {}}
    r1, _, _ = resolve_feature_config(fc, R, "15m", 4, "KRX", FEATURE_ALIASES)
    r2, _, _ = resolve_feature_config({"enabled_features": fc["enabled_features"],
                                       "feature_params": r1["feature_params"]}, R, "15m", 4, "KRX", FEATURE_ALIASES)
    assert r1["feature_params"] == r2["feature_params"]
