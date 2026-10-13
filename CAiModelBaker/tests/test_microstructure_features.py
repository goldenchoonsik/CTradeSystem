# -*- coding: utf-8 -*-
"""
테스트: 마이크로스트럭처 알파 지표 6종 & Tri-Resolution 표준 컬럼 1급 시민 검증
=============================================================================
1. _get_series Tri-Resolution (close ➔ target_close 자동 조회)
2. target_vwap_disparity (VWAP 괴리율)
3. target_vwap_slope (VWAP 기울기)
4. target_ofi_cumsum (OFI 주문흐름 누적 지수평활)
5. target_rv_ratio (Parkinson 실현변동성 비율)
6. target_large_trade_density (대량 체결 밀도)
7. target_is_auction (KRX 동시호가 단일가 마스크)
8. 실제 069500 10초봉 Parquet 데이터 대상 전수 무결성 검증
"""

import os
import pytest
import numpy as np
import pandas as pd

from shared.features.feature_registry import (
    _get_series,
    DEFAULT_FEATURE_REGISTRY,
    _compute_target_vwap_disparity,
    _compute_target_vwap_slope,
    _compute_target_ofi_cumsum,
    _compute_target_rv_ratio,
    _compute_target_large_trade_density,
    _compute_target_is_auction,
)
from shared.data_library import paths as dpaths


def test_get_series_tri_resolution():
    """target_close 컬럼 없이 순수 물리 컬럼 close만 존재할 때 자동 매핑되는지 검증"""
    df = pd.DataFrame({
        "close": [100.0, 105.0, 110.0],
        "open": [98.0, 101.0, 106.0],
        "vwap": [99.0, 103.0, 108.0],
        "ofi": [0.2, -0.1, 0.5],
    })

    # 1. target_close 조회 시 close가 반환되는지
    s_close = _get_series(df, "target_close", "upbit_close")
    assert (s_close == df["close"]).all()

    # 2. target_vwap 조회 시 vwap이 반환되는지
    s_vwap = _get_series(df, "target_vwap", "vwap")
    assert (s_vwap == df["vwap"]).all()

    # 3. target_ofi 조회 시 ofi가 반환되는지
    s_ofi = _get_series(df, "target_ofi", "ofi")
    assert (s_ofi == df["ofi"]).all()


def test_target_vwap_disparity():
    """VWAP 괴리율 계산 및 평활 정밀도 검증"""
    df = pd.DataFrame({
        "close": [100.0, 102.0, 105.0],
        "vwap": [100.0, 100.0, 100.0],
    })
    # 괴리율 = (close - vwap) / vwap * 100 -> [0.0, 2.0, 5.0]
    res = _compute_target_vwap_disparity(df, {"smooth_bars": 1}, "10s")
    assert np.isclose(res[0], 0.0, atol=1e-4)
    assert np.isclose(res[1], 2.0, atol=1e-4)
    assert np.isclose(res[2], 5.0, atol=1e-4)


def test_target_vwap_slope():
    """VWAP 기울기(변화율) 계산 검증"""
    df = pd.DataFrame({
        "close": [100.0, 102.0, 105.0],
        "vwap": [100.0, 102.0, 105.0],
    })
    # window_bars = 1 -> shift(1): vwap[1]-vwap[0] = 2%, vwap[2]-vwap[1] = 2.94%
    res = _compute_target_vwap_slope(df, {"window_bars": 1}, "10s")
    assert res[0] == 0.0  # 첫 번째는 NaN -> fillna(0.0)
    assert np.isclose(res[1], 2.0, atol=1e-4)


def test_target_ofi_cumsum():
    """OFI 누적 지수평활 및 폴백 검증"""
    df = pd.DataFrame({
        "ofi": [1.0, 0.5, -0.5, -1.0],
    })
    res = _compute_target_ofi_cumsum(df, {"window_bars": 5}, "10s")
    assert len(res) == 4
    assert not res.isna().any()
    assert -1.0 <= res.min() and res.max() <= 1.0


def test_target_rv_ratio():
    """Parkinson RV short/long 비율 검증"""
    df = pd.DataFrame({
        "rv": [0.001, 0.002, 0.005, 0.010, 0.020] * 10,
    })
    res = _compute_target_rv_ratio(df, {"short_bars": 3, "long_bars": 10}, "10s")
    assert len(res) == 50
    assert not res.isna().any()
    assert (res >= 0.0).all()


def test_target_large_trade_density():
    """대량 체결 밀도 검증"""
    df = pd.DataFrame({
        "is_large_trade": [0, 1, 0, 1, 1],
    })
    res = _compute_target_large_trade_density(df, {"window_bars": 2}, "10s")
    assert len(res) == 5
    assert not res.isna().any()
    assert np.isclose(res.iloc[-1], 1.0, atol=1e-4)


def test_target_is_auction():
    """동시호가 단일가 마스크 검증"""
    df = pd.DataFrame({
        "is_auction": [0, 0, 1, 1, 0],
    })
    res = _compute_target_is_auction(df, {}, "10s")
    assert list(res) == [0, 0, 1, 1, 0]
    assert DEFAULT_FEATURE_REGISTRY["target_is_auction"]["is_regime"] is True


def test_real_normalized_parquet_microstructure():
    """실제 DataVault의 069500 10초봉 Parquet 데이터를 읽어서 6종 마이크로 지표가 온전히 계산되는지 실측 검증"""
    parquet_path = dpaths.normalized_path("candles_10s", "069500_10s.parquet")
    if not os.path.exists(parquet_path):
        pytest.skip("069500 10s Parquet 미존재")

    df = pd.read_parquet(parquet_path)
    sample = df.head(500).copy()

    disp = _compute_target_vwap_disparity(sample, {"smooth_bars": 1}, "10s")
    slope = _compute_target_vwap_slope(sample, {"window_bars": 5}, "10s")
    ofi_s = _compute_target_ofi_cumsum(sample, {"window_bars": 10}, "10s")
    rv_r = _compute_target_rv_ratio(sample, {"short_bars": 5, "long_bars": 30}, "10s")
    ltd = _compute_target_large_trade_density(sample, {"window_bars": 10}, "10s")
    auc = _compute_target_is_auction(sample, {}, "10s")

    assert not disp.isna().any(), "VWAP 괴리율에 NaN 발생"
    assert not slope.isna().any(), "VWAP 기울기에 NaN 발생"
    assert not ofi_s.isna().any(), "OFI 지수평활에 NaN 발생"
    assert not rv_r.isna().any(), "RV 비율에 NaN 발생"
    assert not ltd.isna().any(), "대량체결밀도에 NaN 발생"
    assert not auc.isna().any(), "동시호가마스크에 NaN 발생"

