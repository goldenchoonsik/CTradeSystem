# -*- coding: utf-8 -*-
"""
tests/test_end_to_end_crypto_pipeline.py
========================================
메달리온 1~3단계 End-to-End 정합성 및 암호화폐 선물(BTCUSDT-PERP) 결합 파이프라인 전수 검증
- 1단계: SourceMappingManager & Raw Store 대장 무결성 (Zero-Drift Registry)
- 2단계: DataVault Normalized 15m Parquet 표준 캔들 무결성
- 3단계: FeatureComposer 온디맨드 결합 및 피처 엔지니어링 (OI, 펀딩비, 김프) 무결성
"""

import os
import pytest
import numpy as np
import pandas as pd
from shared.data_library.source_mapping_manager import SourceMappingManager
from shared.data_library.symbol_relations_manager import SymbolRelationsManager
from CAiModelBaker.core.feature_composer import FeatureComposer
from shared.features.feature_engine import run_feature_engineering

# 실물 검사(real_vault): 실제 DataVault 데이터 상태를 본다 — 수집 시점에 따라 결과가 바뀐다. `pytest -m real_vault` 로 따로 돌린다.
pytestmark = pytest.mark.real_vault


def test_cross_registry_zero_drift():
    """1단계: symbol_relations.json의 참조 종목이 대장에 100% 자동 등재되고 HEALTHY 상태인지 검증"""
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
    rels_mgr = SymbolRelationsManager()
    btc_rels = rels_mgr.get_relations("KRW-BTC")
    assert btc_rels is not None, "KRW-BTC 관계 설정이 존재해야 합니다."

    ref_symbols = [r["symbol"] for r in btc_rels.get("references", [])]
    assert "BTCUSDT-PERP" in ref_symbols, "BTCUSDT-PERP가 KRW-BTC의 참조 종목으로 등록되어 있어야 합니다."

    # 대장 등록 여부 검증
    for ref_sym in ref_symbols:
        clean_sym = SourceMappingManager.clean_symbol(ref_sym)
        assert clean_sym in mappings, f"참조 자산 '{clean_sym}'이 수집 대장(source_mappings)에 자동 등재되어야 합니다."

    # BTCUSDT-PERP 세부 무결성 검증
    perp_info = mappings["BTCUSDT-PERP"]
    assert perp_info["primary_source"] == "BINANCE"
    assert perp_info["category"] == "CRYPTO_FUTURES"
    assert perp_info["role"] == "macro_futures"
    assert perp_info["live_connection"]["provider"] == "BINANCE"
    assert "fstream.binance.com" in perp_info["live_connection"]["endpoint_url"]

    # 실물 파일 및 매니페스트 감사 상태 검증
    statuses = SourceMappingManager.get_feed_registry_status()
    perp_status = next((s for s in statuses if s["symbol"] == "BTCUSDT-PERP"), None)
    assert perp_status is not None, "BTCUSDT-PERP의 대장 감사 상태가 조회되어야 합니다."
    assert perp_status["status_code"] == "HEALTHY", f"상태가 HEALTHY여야 합니다: {perp_status}"
    assert perp_status["manifest_verified"] is True
    assert perp_status["rows"] >= 90000, f"원천 행 수가 9만 행 이상이어야 합니다: {perp_status['rows']}"


def test_normalized_vault_integrity():
    """2단계: DataVault normalized 15m 표준 캔들 Parquet 데이터 무결성 검증"""
    parquet_path = "DataVault/normalized/candles_15m/BTCUSDT-PERP_15m.parquet"
    assert os.path.exists(parquet_path), "Normalized BTCUSDT-PERP_15m.parquet가 존재해야 합니다."

    df = pd.read_parquet(parquet_path)
    assert not df.empty, "Parquet 데이터가 비어있지 않아야 합니다."
    assert "timestamp" in df.columns
    assert "close" in df.columns
    assert "binance_futures_oi" in df.columns or "oi" in df.columns
    assert "binance_funding_rate" in df.columns or "funding_rate" in df.columns
    assert len(df) >= 90000, f"충분한 과거 데이터가 존재해야 합니다: {len(df)}"


def test_feature_composer_e2e_join():
    """3단계: FeatureComposer가 SSOT 기반으로 KRW-BTC와 BTCUSDT-PERP를 자동 결합하고 3단계 지표를 완성하는지 검증"""
    composer = FeatureComposer()
    
    # 3-1. ref_symbols=None & auto_bind_ssot=True로 온디맨드 결합 실행
    df = composer.compose(
        target_symbol="KRW-BTC",
        target_interval="15m",
        ref_symbols=None,  # SSOT에서 자동 로드
        auto_bind_ssot=True,
        use_cache=False,
        synthesize_cross_features=True
    )

    assert not df.empty, "결합 피처셋이 비어있지 않아야 합니다."
    assert len(df) > 10000, f"유효한 결합 행 수가 생성되어야 합니다: {len(df)}"

    # 바이낸스 선물 결합 컬럼 검증
    expected_composed_cols = [
        "close",
        "binance_spot_close",
        "binance_futures_close",
        "binance_futures_oi",
        "binance_funding_rate",
        "crypto_usdt_close",
        "cross_btcusdt_perp_spread",
    ]
    for ecc in expected_composed_cols:
        assert ecc in df.columns, f"결합 컬럼 '{ecc}'가 누락되었습니다."

    # 3-2. feature_engine을 통한 3단계 최종 파생 지표 산출 무결성 검증
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
        assert fid in df_feats.columns, f"최종 지표 '{fid}'가 계산되지 않았습니다."
        assert not df_feats[fid].isna().all(), f"지표 '{fid}'의 모든 값이 NaN입니다."
        assert not np.isinf(df_feats[fid]).any(), f"지표 '{fid}'에 Inf가 포함되어 있습니다."

