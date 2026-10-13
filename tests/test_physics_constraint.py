"""
Test suite for Physics & Market Friction Constraint Engine
"""
import pytest
import pandas as pd
import numpy as np
from CAiModelBaker.core.physics_constraint import (
    calc_mean_atr_pct,
    calc_friction_floor,
    apply_physics_constraints_to_config
)


def test_calc_friction_floor_btc_10m():
    """비트코인 10분봉 환경에서 수수료 마찰력 하한치 산출 검증"""
    res = calc_friction_floor(fee_rate=0.0005, slippage_rate=0.0006, mean_atr_pct=0.0020)
    
    assert res["roundtrip_friction_pct"] == 0.22
    assert res["mean_atr_pct"] == 0.20
    assert res["friction_ratio"] == round(0.0020 / 0.0022, 2)
    assert res["friction_status"] == "EXTREME_FRICTION"  # 0.91 < 1.1
    assert res["tp_atr_min"] >= 3.5
    assert res["sl_atr_min"] == 2.0
    assert res["min_horizon_candles"] >= 12


def test_calc_friction_floor_alt_healthy():
    """변동성이 큰 알트코인 환경에서 건강한 상태 판정 검증"""
    res = calc_friction_floor(fee_rate=0.0005, slippage_rate=0.0006, mean_atr_pct=0.0080)
    
    assert res["friction_ratio"] >= 3.0
    assert res["friction_status"] == "HEALTHY"
    assert res["min_horizon_candles"] >= 6


def _non_physical_config(symbol: str) -> dict:
    """마찰 비용 대비 비물리적으로 좁은 배리어/호라이즌을 담은 config (시장은 반드시 명시한다)"""
    return {
        # 시장 판정은 심볼로 한다. 심볼을 빼면 detect_market_type 이 문서화된 안전 기본값(KRX)으로
        # 떨어져 코인 기준 하한치(3.5 ATR)를 기대하는 단정이 엉뚱하게 깨진다.
        "SYMBOL_UPBIT": symbol,
        "OPTUNA_CONFIG": {
            "fee_upbit": 0.0005,
            "slippage": 0.0006,
            "take_profit_range": {"min": 0.5, "user_min": 1.0, "user_max": 2.0},
            "stop_loss_range": {"min": 0.2, "user_min": 0.77, "user_max": 1.5},
            "predict_ahead_candles_range": {"min": 2, "user_min": 4, "user_max": 6},
            "max_hold_candles_range": {"min": 2, "user_min": 4, "user_max": 6}
        }
    }


def test_apply_physics_constraints_to_config_crypto():
    """코인(24시간·고마찰): 비물리적 config 가 코인 물리 하한치로 자동 바인딩되는지 검증"""
    adjusted = apply_physics_constraints_to_config(_non_physical_config("KRW-BTC"), mean_atr_pct=0.0020)
    opt = adjusted["OPTUNA_CONFIG"]

    # 1. TP 최소치가 왕복 마찰의 3.5배(≈3.8 ATR) 이상으로 상향되었는가?
    assert opt["take_profit_range"]["user_min"] >= 3.5

    # 2. SL 최소치가 2.0 ATR 노이즈 방어선으로 상향되었는가?
    assert opt["stop_loss_range"]["user_min"] >= 2.0

    # 3. 예측 호라이즌이 최소 12캔들 이상으로 상향되었는가?
    assert opt["predict_ahead_candles_range"]["user_min"] >= 12
    assert opt["max_hold_candles_range"]["user_min"] >= 12

    # 4. 물리적 제약 메타데이터가 config에 주입되었는가?
    assert "PHYSICS_CONSTRAINTS" in adjusted


def test_apply_physics_constraints_to_config_krx_floors_are_lower_by_design():
    """국내 ETF: 마찰 배수(3.0)·노이즈 방어선(1.0 ATR)이 코인보다 낮은 것은 의도된 설계다.

    사용자가 넣은 비물리적 값은 반드시 상향되어야 하고, 동시에 KRX 하한치가 코인 하한치보다
    낮게 유지되는지 같이 고정한다 — 두 시장의 상수를 실수로 뒤바꾸면 이 테스트가 잡는다.
    """
    krx = apply_physics_constraints_to_config(_non_physical_config("069500"), mean_atr_pct=0.0020)["OPTUNA_CONFIG"]
    crypto = apply_physics_constraints_to_config(_non_physical_config("KRW-BTC"), mean_atr_pct=0.0020)["OPTUNA_CONFIG"]

    # 1. 비물리적 사용자 입력(TP 1.0 / SL 0.77 / 호라이즌 4)이 상향되었는가?
    assert krx["take_profit_range"]["user_min"] > 1.0
    assert krx["stop_loss_range"]["user_min"] > 0.77
    assert krx["predict_ahead_candles_range"]["user_min"] > 4
    assert krx["max_hold_candles_range"]["user_min"] > 4

    # 2. KRX 하한치는 코인보다 낮아야 한다 (세션장·저마찰)
    assert krx["take_profit_range"]["user_min"] < crypto["take_profit_range"]["user_min"]
    assert krx["stop_loss_range"]["user_min"] < crypto["stop_loss_range"]["user_min"]


def test_screen_features_for_target_horizon():
    """목표 지평에 대한 피처 수명 스크리닝 동작 검증"""
    from CAiModelBaker.core.feature_screener import screen_features_for_target_horizon
    
    # 100개 샘플 합성 데이터프레임 생성
    np.random.seed(42)
    n = 200
    prices = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    
    # 유효한 추세 피처 (미래 가격과 상관관계 높음)
    good_feat = pd.Series(prices).shift(2) + np.random.randn(n) * 0.1
    # 순수 잡음 피처
    noise_feat = pd.Series(np.random.randn(n))
    
    df = pd.DataFrame({
        "target_close": prices,
        "good_trend_feature": good_feat,
        "pure_noise_feature": noise_feat
    })
    
    res = screen_features_for_target_horizon(
        df=df,
        features=["good_trend_feature", "pure_noise_feature"],
        target_horizon_candles=12,
        min_ic=0.015,
        interval="10m"
    )
    
    assert res["status"] == "success"
    assert "good_trend_feature" in res["passed_features"]
    assert len(res["passed_features"]) >= 1


def test_calc_empirical_barrier_bounds_krx_10s():
    """KRX 10초봉 데이터 환경에서 표본 기반 장벽 보정(Empirical Sampling) 동작 검증"""
    from CAiModelBaker.core.physics_constraint import calc_empirical_barrier_bounds
    
    np.random.seed(77)
    n = 300
    walk = np.cumsum(np.random.randn(n) * 50.0)
    close = 100000.0 + walk
    # 실제 KODEX200 10초봉 ATR 수준 (약 100~150원, 0.10~0.15%)
    high = close + np.abs(np.random.randn(n) * 100.0 + 50.0)
    low = close - np.abs(np.random.randn(n) * 100.0 + 50.0)
    
    df = pd.DataFrame({
        "target_close": close,
        "high": high,
        "low": low
    })
    
    res = calc_empirical_barrier_bounds(
        df=df,
        fee_rate=0.00005,
        slippage_rate=0.0001,
        interval="10s",
        market_type="KRX"
    )
    
    assert res["method"] == "empirical_sampling"
    assert res["friction_status"] == "HEALTHY"
    assert res["tp_atr_min"] >= 1.0
    assert res["tp_atr_max"] > res["tp_atr_min"]
    assert res["sl_atr_min"] >= 0.8
    assert res["rec_horizon_candles"] >= res["min_horizon_candles"]
    assert res["max_hold_candles"] >= res["rec_horizon_candles"]


def test_apply_physics_constraints_search_space_guarantee():
    """물리적 제약 적용 시 user_max가 user_min과 동일하게 고정되지 않고 충분한 탐색 공간이 보장되는지 검증"""
    raw_config = {
        "SYMBOL_UPBIT": "069500",
        "INTERVAL": "10s",
        "OPTUNA_CONFIG": {
            "fee_upbit": 0.00005,
            "slippage": 0.0001,
            "take_profit_range": {"min": 0.5, "user_min": 1.0, "user_max": 1.0},
            "stop_loss_range": {"min": 0.2, "user_min": 0.5, "user_max": 0.5},
            "predict_ahead_candles_range": {"min": 2, "user_min": 6, "user_max": 6},
            "max_hold_candles_range": {"min": 2, "user_min": 6, "user_max": 6}
        }
    }
    
    adjusted = apply_physics_constraints_to_config(raw_config, mean_atr_pct=0.0013)
    opt = adjusted["OPTUNA_CONFIG"]
    
    # 1. user_max가 user_min보다 반드시 커서 탐색 공간이 존재하는가?
    assert opt["take_profit_range"]["user_max"] > opt["take_profit_range"]["user_min"]
    assert opt["stop_loss_range"]["user_max"] > opt["stop_loss_range"]["user_min"]
    assert opt["predict_ahead_candles_range"]["user_max"] > opt["predict_ahead_candles_range"]["user_min"]
    assert opt["max_hold_candles_range"]["user_max"] > opt["max_hold_candles_range"]["user_min"]
    
    # 2. 최대 보유 시간이 최소 호라이즌의 2배 이상으로 확보되었는가?
    min_hold = opt["max_hold_candles_range"]["user_min"]
    max_hold = opt["max_hold_candles_range"]["user_max"]
    assert max_hold >= min_hold * 2


def test_parse_interval_minutes_consistency():
    """공용 parse_interval_minutes가 10s 및 다양한 시간봉을 정확히 분 단위로 변환하는지 검증"""
    from shared.core.market_utils import parse_interval_minutes
    
    assert parse_interval_minutes("10s") == pytest.approx(10.0 / 60.0)
    assert parse_interval_minutes("5s") == pytest.approx(5.0 / 60.0)
    assert parse_interval_minutes("1s") == pytest.approx(1.0 / 60.0)
    assert parse_interval_minutes("1m") == 1.0
    assert parse_interval_minutes("15m") == 15.0
    assert parse_interval_minutes("1h") == 60.0


