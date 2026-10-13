# -*- coding: utf-8 -*-
"""
스케일 파이프라인 인과 맵핑 (1단계 원천 ➔ 2단계 캔들) 거버넌스 및 동기화 무결성 테스트
"""
import pytest
from shared.data_library.source_mapping_manager import SourceMappingManager


def test_update_pipeline_scale_mapping_crypto():
    """가상자산(KRW-BTC): 1M 원천 ➔ 15m, 10m 다운스케일 맵핑 원자적 갱신 검증"""
    res = SourceMappingManager.update_pipeline_scale_mapping(
        symbol="KRW-BTC",
        ingestion_sources=["1M"],
        target_scales=["15m", "10m"],
        prune_unselected=False
    )
    assert res["status"] == "ok"
    assert res["ingestion_sources"] == ["1M"]
    assert "15m" in res["target_scales"]
    assert "10m" in res["target_scales"]

    # 원장 리로드 검증
    all_map = SourceMappingManager.load_all_mappings(force_refresh=True)
    info = all_map.get("KRW-BTC")
    assert info is not None
    assert info["ingestion_sources"] == ["1M"]
    assert info["target_scales"] == ["10m", "15m"]


def test_update_pipeline_scale_mapping_krx_subminute_promotion():
    """국내주식: 초단기 봉(10s) 타겟 선택 시 원천에 TICK 자동 보강 검증"""
    res = SourceMappingManager.update_pipeline_scale_mapping(
        symbol="005930",
        ingestion_sources=["1M"],  # TICK 누락 상태로 전달
        target_scales=["10s", "1m"],
        prune_unselected=False
    )
    assert res["status"] == "ok"
    # TICK이 지능적으로 자동 보강되어야 함
    assert "TICK" in res["ingestion_sources"]
    assert "10s" in res["target_scales"]


def test_batch_update_pipeline_scale_mapping_category():
    """동일 카테고리(CRYPTO_SPOT) 일괄 스케일 맵핑 갱신 검증"""
    res = SourceMappingManager.batch_update_pipeline_scale_mapping(
        category="CRYPTO_SPOT",
        ingestion_sources=["1M"],
        target_scales=["15m", "10m", "1m"],
        prune_unselected=False
    )
    assert res["status"] == "ok"
    assert res["updated_count"] >= 2  # KRW-BTC, BTCUSDT 등

    all_map = SourceMappingManager.load_all_mappings(force_refresh=True)
    for sym in ["KRW-BTC", "BTCUSDT"]:
        if sym in all_map:
            assert all_map[sym]["ingestion_sources"] == ["1M"]
            assert "15m" in all_map[sym]["target_scales"]
