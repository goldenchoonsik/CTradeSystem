import pytest
import numpy as np
import pandas as pd
from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY, _compute_semi_duo_lead_delta

def test_feature_registry_registration():
    """semi_duo_lead_delta 지표가 레지스트리에 올바르게 등록되었는지 검증"""
    assert "semi_duo_lead_delta" in DEFAULT_FEATURE_REGISTRY
    spec = DEFAULT_FEATURE_REGISTRY["semi_duo_lead_delta"]
    assert spec["id"] == "semi_duo_lead_delta"
    assert spec["market_scope"] == "krx"
    assert spec["category"] == "reference"
    assert callable(spec["compute"])
    assert "window_bars" in spec["params"]
    assert "zscore_bars" in spec["params"]
    assert "w_sam" in spec["params"]
    assert "w_hyn" in spec["params"]

def test_semi_duo_lead_delta_math_correctness():
    """모의 시세를 통해 수식의 부호 및 수학적 정합성 검증"""
    n = 60
    p_etf = [100.0] * n
    p_sam = [100.0] * 40 + [105.0] * (n - 40)
    p_hyn = [100.0] * n

    df = pd.DataFrame({
        "target_close": p_etf,
        "ref_005930_close": p_sam,
        "ref_000660_close": p_hyn,
    })

    params = {"window_bars": 1, "zscore_bars": 20, "w_sam": 0.70, "w_hyn": 0.30}
    z = _compute_semi_duo_lead_delta(df, params, interval="10s")

    assert z.iloc[40] > 1.0, f"Expected positive spike on sam jump, got {z.iloc[40]}"
    assert abs(z.iloc[30]) < 0.1

def test_semi_duo_lead_delta_lookahead_proof():
    """미래 가격 변동이 과거 피처값에 단 1bit도 영향을 주지 않는지 무결성 검증 (Lookahead Zero Leakage)"""
    np.random.seed(42)
    n = 100
    p_etf = 100.0 + np.cumsum(np.random.randn(n) * 0.1)
    p_sam = 200.0 + np.cumsum(np.random.randn(n) * 0.2)
    p_hyn = 300.0 + np.cumsum(np.random.randn(n) * 0.3)

    df_clean = pd.DataFrame({
        "target_close": p_etf.copy(),
        "ref_005930_close": p_sam.copy(),
        "ref_000660_close": p_hyn.copy(),
    })

    params = {"window_bars": 2, "zscore_bars": 25, "w_sam": 0.70, "w_hyn": 0.30}
    z_clean = _compute_semi_duo_lead_delta(df_clean, params, interval="10s")

    df_future_corrupted = df_clean.copy()
    df_future_corrupted.loc[50:, "target_close"] = df_future_corrupted.loc[50:, "target_close"] * 1000.0
    df_future_corrupted.loc[50:, "ref_005930_close"] = df_future_corrupted.loc[50:, "ref_005930_close"] * 5000.0
    df_future_corrupted.loc[50:, "ref_000660_close"] = df_future_corrupted.loc[50:, "ref_000660_close"] * 0.001

    z_corrupted = _compute_semi_duo_lead_delta(df_future_corrupted, params, interval="10s")

    np.testing.assert_allclose(
        z_clean.iloc[:50].values,
        z_corrupted.iloc[:50].values,
        rtol=1e-12,
        atol=1e-12,
        err_msg="Lookahead bias detected! Future changes corrupted past feature values."
    )

def test_semi_duo_lead_delta_fallback_and_nan_safety():
    """참조 종목 결측 및 컬럼 부재 시 graceful fallback 검증"""
    n = 30
    df_empty = pd.DataFrame({
        "target_close": [100.0] * n,
    })
    params = {"window_bars": 1, "zscore_bars": 10}
    z_empty = _compute_semi_duo_lead_delta(df_empty, params, interval="10s")
    assert (z_empty == 0.0).all()

    df_nan = pd.DataFrame({
        "target_close": [np.nan] * 10 + [100.0] * 20,
        "ref_005930_close": [np.nan] * 15 + [200.0] * 15,
        "ref_000660_close": [np.nan] * n,
    })
    z_nan = _compute_semi_duo_lead_delta(df_nan, params, interval="10s")
    assert len(z_nan) == n
    assert not z_nan.isna().any()
    assert (z_nan.abs() <= 3.0).all()
