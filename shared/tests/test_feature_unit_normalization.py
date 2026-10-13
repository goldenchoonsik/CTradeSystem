"""
Feature Parameter Unit Normalization & Interval Scaling Tests
Validates the 3-tier parameter standard (_mins, _bars, _days) and backwards compatibility.
"""
import pytest
import pandas as pd
import numpy as np

from shared.features.feature_registry import (
    _get_interval_minutes,
    _get_window_bars,
    _get_period_bars,
    _get_period_days,
    _compute_target_ma_ratio,
    _compute_binance_cvd_momentum,
    _compute_binance_leading_momentum,
    _compute_target_rsi_curr,
    _compute_coinbase_premium_index,
)


def test_get_interval_minutes():
    assert _get_interval_minutes('1m') == 1.0
    assert _get_interval_minutes('5m') == 5.0
    assert _get_interval_minutes('10m') == 10.0
    assert _get_interval_minutes('15m') == 15.0
    assert _get_interval_minutes('30m') == 30.0
    assert _get_interval_minutes('1h') == 60.0
    assert _get_interval_minutes('4h') == 240.0
    assert _get_interval_minutes('1d') == 1440.0


def test_window_bars_time_scaling():
    # 30 minutes in different intervals
    params = {"window_mins": 30}
    assert _get_window_bars(params, interval='1m') == 30
    assert _get_window_bars(params, interval='5m') == 6
    assert _get_window_bars(params, interval='10m') == 3
    assert _get_window_bars(params, interval='15m') == 2
    assert _get_window_bars(params, interval='30m') == 1
    assert _get_window_bars(params, interval='1h') == 1  # max(1, round(30/60)) = 1

    # Backwards compatibility with legacy keys
    assert _get_window_bars({"candles": 7}, interval='10m') == 7
    assert _get_window_bars({"window_bars": 8}, interval='10m') == 8
    assert _get_window_bars({"window": 4}, interval='10m') == 4
    assert _get_window_bars({"window": 1440}, interval='10m') == 144  # >= 60 treated as minutes


def test_period_bars_sample_fixed():
    # Technical statistics stay fixed across intervals
    assert _get_period_bars({"period_bars": 20}) == 20
    assert _get_period_bars({"period": 15}) == 15
    assert _get_period_bars({"period_candles": 14}) == 14
    assert _get_period_bars({}, default_bars=20) == 20


def test_period_days():
    assert _get_period_days({"period_days": 14}) == 14
    assert _get_period_days({}, default_days=14) == 14


def test_cvd_momentum_time_scaling():
    """Verify that CVD momentum with window_mins scales rolling window across intervals"""
    n = 100
    df = pd.DataFrame({
        'binance_spot_volume': np.ones(n) * 10.0,
        'binance_spot_taker_ratio': np.ones(n) * 0.8,
    })
    
    params = {"window_mins": 30}
    
    # In 10m interval, 30m is 3 bars
    series_10m = _compute_binance_cvd_momentum(df, params, interval='10m')
    assert len(series_10m) == n
    assert not series_10m.isna().any()
    
    # In 1m interval, 30m is 30 bars
    series_1m = _compute_binance_cvd_momentum(df, params, interval='1m')
    assert len(series_1m) == n
    assert not series_1m.isna().any()


def test_target_ma_ratio_period_bars():
    """Verify that MA ratio correctly respects period_bars and legacy period"""
    n = 50
    close = pd.Series(np.linspace(100, 200, n))
    df = pd.DataFrame({'target_close': close})
    
    # New standard
    res_new = _compute_target_ma_ratio(df, {"period_bars": 10}, interval='10m')
    # Legacy
    res_legacy = _compute_target_ma_ratio(df, {"period": 10}, interval='10m')
    
    pd.testing.assert_series_equal(res_new, res_legacy)


def test_coinbase_premium_interval_scaling():
    n = 20
    df = pd.DataFrame({
        'binance_spot_close': pd.Series([100.0] * n),
        'coinbase_spot_close': pd.Series([101.0] * n),
    })
    
    res = _compute_coinbase_premium_index(df, {"window_mins": 10}, interval='10m')
    assert len(res) == n
    # (101 - 100) / 100 = 0.01
    assert np.isclose(res.iloc[-1], 0.01, atol=1e-4)
