# -*- coding: utf-8 -*-
"""
테스트: 가상자산(KRW-BTC) 메달리온 2단계 및 바이낸스 선물/지표 결합 무결성 검증
"""
import pytest
import numpy as np
from CAiModelBaker.core.feature_composer import FeatureComposer
from shared.features.feature_engine import run_feature_engineering


def test_krw_btc_15m_medallion_composition():
    """KRW-BTC 15m 온디맨드 결합 및 바이낸스 선물/파생 컬럼 자동 투영 검증"""
    composer = FeatureComposer()
    df = composer.compose(
        target_symbol="KRW-BTC",
        target_interval="15m",
        ref_symbols=["BTCUSDT", "BTCUSDT-PERP", "KRW-USDT"],
        align_to_common_dates=True,
    )

    assert not df.empty
    assert len(df) >= 20000

    # 1. 타겟 표준 컬럼 검증
    for col in ["open", "high", "low", "close", "volume", "target_close"]:
        assert col in df.columns, f"타겟 컬럼 {col} 누락"

    # 2. 바이낸스 현물/선물/테더 결합 컬럼 검증
    assert "binance_spot_close" in df.columns
    assert "binance_spot_volume" in df.columns
    assert "binance_futures_oi" in df.columns
    assert "binance_funding_rate" in df.columns
    assert "crypto_usdt_close" in df.columns

    # 3. 결합 결측치 무결성 검증 — '모름'은 NaN 으로 남고, 0 으로 위장되지 않는다 (2026-10-02)
    assert not df["binance_spot_close"].isna().any()
    assert not df["binance_funding_rate"].isna().any()
    # 미결제약정은 수집 시작(2026-09-25) 전 이력이 없다: 그 앞은 NaN 이어야 하고(종전엔 0 으로 채워졌다),
    # 수집 시작 이후엔 빈 칸이 없어야 한다.
    oi = df["binance_futures_oi"]
    first = oi.first_valid_index()
    assert first is not None, "미결제약정 실값이 하나도 없다"
    assert oi.loc[:first].iloc[:-1].isna().all()
    assert oi.loc[first:].notna().all()
    assert not (oi == 0).any(), "미결제약정 0 은 관측일 수 없다 — 결측을 0 으로 위장했다"


def test_crypto_feature_engineering_computation():
    """바이낸스 선물 펀딩비, 미결제약정(OI), 김치프리미엄 지표 실제 연산 무결성 검증"""
    composer = FeatureComposer()
    df = composer.compose(
        target_symbol="KRW-BTC",
        target_interval="15m",
        ref_symbols=["BTCUSDT", "BTCUSDT-PERP", "KRW-USDT"],
        align_to_common_dates=True,
    )

    crypto_features = [
        "binance_funding_rate",
        "binance_oi_change_ratio",
        "binance_oi_ma_ratio",
        "binance_leading_momentum",
        "kimchi_premium",
    ]

    feature_cfg = {
        "enabled_features": crypto_features,
        "feature_params": {}
    }

    df_feats = run_feature_engineering(df, interval="15m", feature_config=feature_cfg)

    for fid in crypto_features:
        assert fid in df_feats.columns, f"지표 {fid}가 계산되지 않았습니다."
        assert not df_feats[fid].isna().all(), f"지표 {fid}의 모든 값이 NaN입니다."
        assert not np.isinf(df_feats[fid]).any(), f"지표 {fid}에 Inf가 포함되어 있습니다."

