"""
CTradeSystem Shared - Feature Parity & Alpha Factor Test Suite

검증 항목:
  1. kospi_basis_z (선물-현물 베이시스 롤링 Z-Score) 연산 및 Z-Score [-3.0, 3.0] 클리핑 검증
  2. top2_lead_momentum (삼성전자+SK하이닉스 시총 가중 선행 모멘텀) Z-Score 연산 검증
  3. parkinson_rv_squeeze (Parkinson 실현변동성 스퀴즈 팩터) 정상 연산 검증
  4. feature_engine.run_feature_engineering 전체 파이프라인 무결성 검증
"""
import pytest
import numpy as np
import pandas as pd
from shared.features.feature_registry import (
    DEFAULT_FEATURE_REGISTRY,
    _compute_kospi_basis_z,
    _compute_stock_basis_z,
    _compute_top2_lead_momentum,
    _compute_parkinson_rv_squeeze,
    find_missing_inputs,
)
from shared.features.feature_engine import run_feature_engineering


def _generate_mock_krx_data(n=200):
    """KODEX 200 + 선물 + 삼성전자 + SK하이닉스가 포함된 2단계 Mock DataFrame 생성"""
    np.random.seed(42)
    timestamps = pd.date_range("2026-09-18 09:00:00", periods=n, freq="1min")
    
    # 현물 KODEX 200 (약 35,000원)
    spot_ret = np.random.normal(0.0001, 0.002, n)
    target_close = 35000.0 * np.cumprod(1.0 + spot_ret)
    target_open = np.roll(target_close, 1)
    target_open[0] = target_close[0]
    target_high = np.maximum(target_open, target_close) * (1.0 + np.abs(np.random.normal(0, 0.001, n)))
    target_low = np.minimum(target_open, target_close) * (1.0 - np.abs(np.random.normal(0, 0.001, n)))
    target_vol = np.random.uniform(1000, 50000, n)

    # KOSPI 200 선물 (약 360.0pt, 현물 대비 베이시스 형성)
    basis_spread = np.random.normal(0.5, 0.2, n)
    futures_close = (target_close / 100.0) + basis_spread

    # 삼성전자 (약 80,000원) & SK하이닉스 (약 200,000원)
    sam_ret = np.random.normal(0.0002, 0.003, n)
    ref_samsung_close = 80000.0 * np.cumprod(1.0 + sam_ret)
    
    hyn_ret = np.random.normal(0.0003, 0.004, n)
    ref_hynix_close = 200000.0 * np.cumprod(1.0 + hyn_ret)

    df = pd.DataFrame({
        "timestamp": timestamps,
        "target_open": target_open,
        "target_high": target_high,
        "target_low": target_low,
        "target_close": target_close,
        "target_volume": target_vol,
        "futures_close": futures_close,
        "spot_close": target_close / 100.0,
        # 참조 컬럼은 원장 키(ref_{ref_key}_close)로만 인식된다 — 옛 별칭(ref_samsung_close)을 쓰면
        # top2_lead_momentum 이 대장주를 못 찾고 0 상수를 돌려주는데도 이 테스트가 통과했다 (2026-10-02)
        "ref_005930_close": ref_samsung_close,
        "ref_000660_close": ref_hynix_close,
    })
    return df


def test_kospi_basis_z():
    """선물-현물 베이시스 롤링 Z-Score 검증"""
    df = _generate_mock_krx_data(n=120)
    params = {"window": 30}
    res = _compute_kospi_basis_z(df, params, interval="1m")

    assert len(res) == len(df)
    assert not res.isna().any(), "NaN 값이 존재합니다!"
    assert (res >= -3.0).all() and (res <= 3.0).all(), "Z-Score 클리핑 범위 [-3.0, 3.0] 초과!"
    print(f"\n✅ [kospi_basis_z] PASS: 평균={res.mean():.4f}, 표준편차={res.std():.4f}, 범위=[{res.min():.2f}, {res.max():.2f}]")


def test_stock_basis_z():
    """개별주식 선물-현물 베이시스 롤링 Z-Score 검증"""
    df = _generate_mock_krx_data(n=120)
    # 개별주식선물(15000) 종가 컬럼 추가 (현물 대비 미세 스프레드 형성)
    df["ref_15000_close"] = df["target_close"] + np.random.normal(500.0, 100.0, len(df))
    params = {"window": 30}
    res = _compute_stock_basis_z(df, params, interval="1m")

    assert len(res) == len(df)
    assert not res.isna().any(), "NaN 값이 존재합니다!"
    assert (res >= -3.0).all() and (res <= 3.0).all(), "Z-Score 클리핑 범위 [-3.0, 3.0] 초과!"
    print(f"\n✅ [stock_basis_z] PASS: 평균={res.mean():.4f}, 표준편차={res.std():.4f}, 범위=[{res.min():.2f}, {res.max():.2f}]")


def test_stock_basis_ignores_index_futures():
    """지수선물만 도킹된 타겟에서 개별주식 베이시스는 결측 처리(0)여야 한다 — 지수선물로 대체 금지"""
    df = _generate_mock_krx_data(n=60)
    fut_cols = [c for c in df.columns if c.startswith("ref_") and c.endswith("_close")]
    res = _compute_stock_basis_z(df.drop(columns=[c for c in fut_cols if "15000" in c]), {"window": 30}, interval="1m")
    assert (res == 0).all(), "개별주식선물이 없는데 지수선물로 베이시스를 계산했다"


def test_stock_basis_rejects_wrong_scale_futures():
    """기초자산과 가격 수준이 다른 '주식선물'은 입력에서 걸러진다 (2026-10-07 사고: 10600=코스닥150 ~1,536pt 가
    SK하이닉스 주식선물로 등록되어 현물 ~1,800,000원과 비교됨 → 베이시스가 −180만 상수가 되었다)"""
    df = _generate_mock_krx_data(n=60)
    df["target_close"] = 1_800_000.0 * (df["target_close"] / df["target_close"].iloc[0])
    df["ref_15000_close"] = 1536.0 + np.random.normal(0.0, 2.0, len(df))
    res = _compute_stock_basis_z(df, {"window": 30}, interval="1m")
    assert (res == 0).all(), "가격 수준이 맞지 않는 선물로 베이시스를 계산했다"


def test_stock_basis_picks_matching_underlying():
    """여러 주식선물이 붙어 있으면 타겟 현물과 가격 수준이 맞는 것만 쓴다 (키 정렬 첫 번째가 아니라)"""
    from shared.features.feature_registry import _stock_futures_and_spot
    df = _generate_mock_krx_data(n=60)
    df["target_close"] = 1_800_000.0 * (df["target_close"] / df["target_close"].iloc[0])   # 하이닉스 타겟
    df["ref_11100_close"] = 272_000.0 + np.random.normal(0.0, 500.0, len(df))           # 삼성선물(키 정렬상 먼저)
    df["ref_15000_close"] = df["target_close"] + 6000.0                                  # 하이닉스선물
    fut, _ = _stock_futures_and_spot(df)
    assert (fut == df["ref_15000_close"]).all()


def test_kospi_basis_excludes_stock_futures():
    """지수선물 베이시스는 개별주식선물(원장 underlying 보유)을 입력으로 삼지 않는다"""
    df = _generate_mock_krx_data(n=60)
    only_stock = df.drop(columns=[c for c in df.columns if c.startswith("ref_") and c.endswith("_close") and "10100" in c], errors="ignore")
    only_stock["ref_15000_close"] = only_stock["target_close"] + 500.0
    fut, _ = __import__("shared.features.feature_registry", fromlist=["x"])._kospi_futures_and_spot(only_stock)
    assert fut.isna().all() or not (fut == only_stock["ref_15000_close"]).any()


def test_top2_lead_momentum():
    """섹터 Top 2 선행 대장주 합성 모멘텀 Z-Score 검증 (하위호환 및 범용 슬롯)"""
    df = _generate_mock_krx_data(n=120)
    params = {"candles": 5, "window": 30}
    
    # 1) 원장 유형(KRX_STOCK) 참조 컬럼으로 대장주를 찾는다
    res = _compute_top2_lead_momentum(df, params, interval="1m")
    assert len(res) == len(df)
    assert not res.isna().any(), "NaN 값이 존재합니다!"
    assert res.nunique() > 1, "대장주를 못 찾아 상수를 돌려줬다"
    assert (res >= -3.0).all() and (res <= 3.0).all(), "Z-Score 클리핑 범위 [-3.0, 3.0] 초과!"
    
    # 2) 원장에 없는 이름 슬롯(lead_1_close, lead_2_close)은 입력이 아니다 — 계산하지 않고 NaN
    df_generic = df.copy()
    df_generic["lead_1_close"] = df["ref_005930_close"] * 1.5
    df_generic["lead_2_close"] = df["ref_000660_close"] * 0.5
    del df_generic["ref_005930_close"]
    del df_generic["ref_000660_close"]

    assert find_missing_inputs("top2_lead_momentum", df_generic)
    res_generic = DEFAULT_FEATURE_REGISTRY["top2_lead_momentum"]["compute"](df_generic, params, "1m")
    assert res_generic.isna().all()


def test_parkinson_rv_squeeze():
    """Parkinson 극치 실현변동성 스퀴즈 비율 Z-Score 검증"""
    df = _generate_mock_krx_data(n=120)
    params = {"short_bars": 5, "long_bars": 20}
    res = _compute_parkinson_rv_squeeze(df, params, interval="1m")

    assert len(res) == len(df)
    assert not res.isna().any(), "NaN 값이 존재합니다!"
    assert (res >= -3.0).all() and (res <= 3.0).all(), "Z-Score 클리핑 범위 [-3.0, 3.0] 초과!"
    print(f"\n✅ [parkinson_rv_squeeze] PASS: 평균={res.mean():.4f}, 표준편차={res.std():.4f}, 범위=[{res.min():.2f}, {res.max():.2f}]")


def test_feature_engine_integration():
    """feature_engine.run_feature_engineering 파이프라인 신규 지표 통합 검증"""
    df = _generate_mock_krx_data(n=100)
    enabled_features = ["kospi_basis_z", "top2_lead_momentum", "parkinson_rv_squeeze"]
    feature_config = {
        "enabled_features": enabled_features,
        "feature_params": {
            "kospi_basis_z": {"window": 30},
            "top2_lead_momentum": {"candles": 5, "window": 30},
            "parkinson_rv_squeeze": {"short_bars": 5, "long_bars": 20}
        }
    }

    df_out = run_feature_engineering(df, interval="1m", feature_config=feature_config)
    for feat in enabled_features:
        assert feat in df_out.columns, f"컬럼 {feat} 누락!"
        assert not df_out[feat].isna().any(), f"컬럼 {feat}에 NaN 존재!"

    print("\n✅ [Feature Engine Integration] 신규 3대 팩터 파이프라인 통합 완료!")

