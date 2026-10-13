# -*- coding: utf-8 -*-
"""
Test Ingestion Resolution & Source Mapping Enhancements
======================================================
1. source_mappings.json의 모든 자산에 ingestion_resolution 및 canonical_intervals 필드 존재 검증
2. 주식 ETF(TICK) vs 가상자산(1M) 해상도 분리 검증
3. 가상자산 primary_type의 시간 스케일 가시화(1M_ARCHIVE 등) 검증
4. SourceMappingManager 감사 결과에 ingestion_resolution 및 canonical_intervals 포함 검증
5. 신규 자산 등록 시 ingestion_resolution 정상 반영 검증
"""

import pytest
from shared.data_library.source_mapping_manager import SourceMappingManager


def test_source_mappings_ingestion_resolution_schema():
    """모든 등록 자산이 ingestion_resolution 및 canonical_intervals 필드를 보유하는지 검증"""
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
    assert len(mappings) > 0, "source_mappings가 비어있지 않아야 함"

    valid_tiers = {"TICK", "1S", "1M", "1M_5M", "1D"}

    for sym, asset in mappings.items():
        assert "ingestion_resolution" in asset, f"{sym}에 ingestion_resolution 필드가 누락됨"
        assert asset["ingestion_resolution"] in valid_tiers, f"{sym}의 ingestion_resolution({asset['ingestion_resolution']})이 유효하지 않음"

        assert "canonical_intervals" in asset, f"{sym}에 canonical_intervals 필드가 누락됨"
        assert isinstance(asset["canonical_intervals"], list), f"{sym}의 canonical_intervals는 리스트여야 함"
        assert len(asset["canonical_intervals"]) > 0, f"{sym}의 canonical_intervals는 최소 1개 이상이어야 함"


def test_asset_specific_ingestion_resolution():
    """주식 ETF는 TICK, 가상자산 및 글로벌 CFD는 1M으로 분리되었는지 검증"""
    mappings = SourceMappingManager.load_all_mappings()

    # 1. 국내 대표 주식 ETF는 TICK
    assert mappings["069500"]["ingestion_resolution"] == "TICK"
    assert "10s" in mappings["069500"]["canonical_intervals"]
    assert mappings["005930"]["ingestion_resolution"] == "TICK"

    # 2. 비트코인 및 가상자산은 1M (불필요한 틱 다운로드 방지)
    assert mappings["KRW-BTC"]["ingestion_resolution"] == "1M"
    assert "1M" in mappings["KRW-BTC"]["primary_type"]
    assert mappings["BTCUSDT"]["ingestion_resolution"] == "1M"
    assert "1M" in mappings["BTCUSDT"]["primary_type"]
    assert mappings["BTC-USD"]["ingestion_resolution"] == "1M"

    # 3. 글로벌 CFD는 1M
    assert mappings["US_TECH_100"]["ingestion_resolution"] == "1M"
    assert mappings["US_500"]["ingestion_resolution"] == "1M"


def test_feed_registry_audit_contains_resolution():
    """SourceMappingManager.get_feed_registry_status() 반환 항목에 해상도 메타데이터 포함 검증"""
    status_items = SourceMappingManager.get_feed_registry_status()
    assert len(status_items) > 0

    for item in status_items:
        assert "ingestion_resolution" in item, f"status item {item['symbol']}에 ingestion_resolution 없음"
        assert "canonical_intervals" in item, f"status item {item['symbol']}에 canonical_intervals 없음"


def test_register_asset_with_custom_resolution():
    """신규 자산 등록 시 ingestion_resolution 및 canonical_intervals가 정상 등록되는지 검증"""
    test_sym = "TEST_ASSET_RES"
    try:
        res = SourceMappingManager.register_asset(
            symbol=test_sym,
            name="테스트 해상도 자산",
            category="CRYPTO_SPOT",
            role="leading_stock",
            primary_source="BINANCE",
            primary_type="1M_VISION",
            ingestion_resolution="1M",
            canonical_intervals=["1m", "15m"]
        )
        assert res["status"] == "ok"
        asset = res["asset"]
        assert asset["ingestion_resolution"] == "1M"
        assert asset["canonical_intervals"] == ["1m", "15m"]

        # 재로드하여 검증
        mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
        assert test_sym in mappings
        assert mappings[test_sym]["ingestion_resolution"] == "1M"
        assert mappings[test_sym]["canonical_intervals"] == ["1m", "15m"]
    finally:
        # 정리
        mappings = SourceMappingManager.load_all_mappings()
        if test_sym in mappings:
            del mappings[test_sym]
            SourceMappingManager.save_all_mappings(mappings)

