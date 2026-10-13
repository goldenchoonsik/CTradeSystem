# -*- coding: utf-8 -*-
"""
메달리온 아키텍처 1단계(Raw Store)와 2단계(Normalized Store) 스케일 분리 단위/통합 테스트
========================================================================================
- 1단계: 원천 데이터 수집 스케일(ingestion_sources: TICK, 1S, 1M, 5M, 1D 등 복수 선택) 검증
- 2단계: 정규화 타겟 캔들 스케일(target_scales: 6대 핵심 표준 및 15개 전체 퀀트 스펙트럼) 검증
- 제공처별 실제 원천 능력(provider_raw_capabilities) 매트릭스 검증
- FastAPI 엔드포인트 연동 검증
"""

import os
import sys
import pytest
from fastapi.testclient import TestClient

# 프로젝트 루트 경로 보정
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CTRADE_ROOT = os.path.dirname(_PROJECT_ROOT)
if _CTRADE_ROOT not in sys.path:
    sys.path.insert(0, _CTRADE_ROOT)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from shared.data_library.source_mapping_manager import SourceMappingManager
from CAiModelBaker.web_dashboard.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def tmp_vault(tmp_path, monkeypatch):
    """이 모듈의 모든 테스트는 임시 볼트에서만 돈다 — 스케일 갱신 API 는 기본값이 '미선택 파생 파일 자동 삭제'다.

    2026-10-10: 이 파일의 테스트(prune_orphans, update_target_scales API, batch API)가 실제 DataVault/normalized 를
    대상으로 돌아, 테스트를 돌릴 때마다 대장에 없거나 테스트가 잠깐 바꾼 스케일 밖의 실제 정규화 파일
    (069500_1d·122630_1d·069500_15m 등)이 지워졌다."""
    from shared.data_library import paths as dpaths
    monkeypatch.setattr(dpaths, "DATA_VAULT_ROOT", str(tmp_path / "DataVault"))
    return tmp_path / "DataVault"


def test_provider_raw_capabilities_integrity():
    """제공처별 실제 원천 스케일 매트릭스 및 퀀트 6대 표준 무결성 검증"""
    caps = SourceMappingManager.get_provider_capabilities()
    
    assert "provider_raw_capabilities" in caps
    assert "quant_core_standards" in caps
    assert "quant_full_spectrum" in caps
    
    p_caps = caps["provider_raw_capabilities"]
    
    # 1. 코인베이스는 초봉 미지원으로 1S가 없어야 함 (실물 API 검증 반영)
    assert "1S" not in p_caps["COINBASE"]
    assert "1M" in p_caps["COINBASE"]
    assert "TICK" in p_caps["COINBASE"]
    
    # 2. 업비트와 바이낸스는 1S 지원
    assert "1S" in p_caps["UPBIT"]
    assert "1S" in p_caps["BINANCE"]
    
    # 3. cTrader는 TICK과 1M 지원
    assert "TICK" in p_caps["CTRADER"]
    assert "1M" in p_caps["CTRADER"]
    
    # 4. 실무 퀀트 핵심 6대 표준 검증
    core_standards = caps["quant_core_standards"]
    assert core_standards == ["10s", "1m", "5m", "15m", "1h", "1d"]
    
    # 5. 전체 15대 퀀트 스펙트럼 포함 여부
    full_spectrum = caps["quant_full_spectrum"]
    assert len(full_spectrum) == 15
    for std in core_standards:
        assert std in full_spectrum


def test_update_ingestion_sources():
    """1단계 원천 수집 스케일 복수 선택 및 저장 검증"""
    symbol = "005930"
    orig_map = SourceMappingManager.load_all_mappings().get(symbol, {})
    orig_sources = orig_map.get("ingestion_sources", ["TICK", "1M"])

    try:
        # 삼성전자(CREON)에 TICK 및 1M 복수 원천 설정
        res = SourceMappingManager.update_ingestion_sources(symbol, ["TICK", "1M"])
        assert res["status"] == "ok"
        assert res["symbol"] == symbol
        assert set(res["ingestion_sources"]) == {"TICK", "1M"}
        
        # 레지스트리 상태 조회 시 반영 확인
        items = SourceMappingManager.get_feed_registry_status()
        s_item = next((x for x in items if x["symbol"] == symbol), None)
        assert s_item is not None
        assert "TICK" in s_item["ingestion_sources"]
        assert "1M" in s_item["ingestion_sources"]
        assert s_item["ingestion_resolution"] == "TICK"
        assert s_item["available_raw_sources"] == ["TICK", "1M", "1D"]
    finally:
        SourceMappingManager.update_ingestion_sources(symbol, orig_sources)


def test_update_target_scales_and_smart_tick_elevation():
    """2단계 목표 캔들 스케일 저장, 순서 정렬 및 10s 선택 시 지능형 TICK 자동 승격 검증"""
    symbol = "000660"
    orig_map = SourceMappingManager.load_all_mappings().get(symbol, {})
    orig_scales = orig_map.get("target_scales") or orig_map.get("canonical_intervals") or ["10s", "1m"]

    try:
        # 역순 또는 혼합 입력 시 퀀트 스펙트럼 순서대로 정렬되는지 확인 (실물 파일 보호를 위해 prune_unselected=False)
        res = SourceMappingManager.update_target_scales(symbol, ["1d", "10s", "5m", "1m"], prune_unselected=False)
        assert res["status"] == "ok"
        # 순서: 10s -> 1m -> 5m -> 1d
        assert res["target_scales"] == ["10s", "1m", "5m", "1d"]
        assert res["canonical_intervals"] == ["10s", "1m", "5m", "1d"]
        
        # 10s가 포함되었으므로 1단계 ingestion_sources에 TICK이 자동으로 승격 추가되었는지 확인
        all_maps = SourceMappingManager.load_all_mappings()
        assert "TICK" in all_maps[symbol]["ingestion_sources"]
        assert all_maps[symbol]["ingestion_resolution"] == "TICK"
    finally:
        SourceMappingManager.update_target_scales(symbol, orig_scales, prune_unselected=False)


def test_canonical_intervals_backward_compatibility():
    """update_canonical_intervals() 하위 호환성 검증"""
    symbol = "069500"
    orig_map = SourceMappingManager.load_all_mappings().get(symbol, {})
    orig_scales = orig_map.get("target_scales") or orig_map.get("canonical_intervals") or ["10s", "1m"]

    try:
        res = SourceMappingManager.update_canonical_intervals(symbol, ["1m", "15m", "1h"], prune_unselected=False)
        assert res["status"] == "ok"
        assert res["target_scales"] == ["1m", "15m", "1h"]
        assert res["canonical_intervals"] == ["1m", "15m", "1h"]
    finally:
        SourceMappingManager.update_canonical_intervals(symbol, orig_scales, prune_unselected=False)


def test_fastapi_endpoints(client):
    """FastAPI REST 엔드포인트 연동 테스트"""
    # 1. GET /api/raw_store/provider_capabilities
    r_cap = client.get("/api/raw_store/provider_capabilities")
    assert r_cap.status_code == 200
    data_cap = r_cap.json()
    assert data_cap["status"] == "ok"
    assert "UPBIT" in data_cap["provider_raw_capabilities"]
    assert "10s" in data_cap["quant_core_standards"]

    # 원복에 쓸 원래 값을 먼저 확보한다 (하드코딩 복원 금지)
    _orig = SourceMappingManager.load_all_mappings(force_refresh=True).get("KRW-BTC", {})
    orig_sources = list(_orig.get("ingestion_sources") or ["1M"])
    orig_scales = list(_orig.get("target_scales") or _orig.get("canonical_intervals") or ["1m"])

    # 2. POST /api/raw_store/update_sources
    r_src = client.post("/api/raw_store/update_sources", json={
        "symbol": "KRW-BTC",
        "ingestion_sources": ["1S", "1M", "1D"]
    })
    assert r_src.status_code == 200
    data_src = r_src.json()
    assert data_src["status"] == "ok"
    assert "1S" in data_src["ingestion_sources"]

    # 3. POST /api/data_vault/update_target_scales
    r_scale = client.post("/api/data_vault/update_target_scales", json={
        "symbol": "KRW-BTC",
        "target_scales": ["10s", "1m", "15m", "1d"]
    })
    assert r_scale.status_code == 200
    data_scale = r_scale.json()
    assert data_scale["status"] == "ok"
    assert data_scale["target_scales"] == ["10s", "1m", "15m", "1d"]

    # 원복 — 원래 값을 그대로 되돌린다. 하드코딩 값으로 "원복"하면 대장을 조용히 고쳐 놓는다
    # (과거 이 줄이 KRW-BTC 의 target_scales 를 ["10m"] 로 잘라 15m 을 쓰는 테스트들을 깨뜨렸다).
    client.post("/api/raw_store/update_sources", json={"symbol": "KRW-BTC", "ingestion_sources": orig_sources})
    client.post("/api/data_vault/update_target_scales", json={"symbol": "KRW-BTC", "target_scales": orig_scales})


def test_batch_update_sources_and_scales():
    """1단계 및 2단계 일괄(Batch) 갱신 로직 검증"""
    import copy
    all_orig = copy.deepcopy(SourceMappingManager.load_all_mappings())

    try:
        # 1. 특정 제공처(CREON) 일괄 원천 스케일 갱신
        res_batch_src = SourceMappingManager.batch_update_ingestion_sources(
            provider="CREON",
            sources=["TICK", "1M"]
        )
        assert res_batch_src["status"] == "ok"
        assert res_batch_src["updated_count"] >= 3
        
        # 확인: 069500의 원천에 TICK, 1M이 포함되어야 함
        maps_after = SourceMappingManager.load_all_mappings()
        assert "TICK" in maps_after["069500"]["ingestion_sources"]
        assert "1M" in maps_after["069500"]["ingestion_sources"]

        # 2. 전체 자산 대상 타겟 스케일 일괄 갱신 (테스트 실물 파일 보호를 위해 prune_unselected=False)
        res_batch_scale = SourceMappingManager.batch_update_target_scales(
            apply_to_all=True,
            scales=["1m", "5m"],
            prune_unselected=False
        )
        assert res_batch_scale["status"] == "ok"
        assert res_batch_scale["updated_count"] >= 10
        
        maps_scale_after = SourceMappingManager.load_all_mappings()
        for sym, item in maps_scale_after.items():
            assert item["target_scales"] == ["1m", "5m"]
    finally:
        SourceMappingManager.save_all_mappings(all_orig)


def test_batch_api_endpoints(client):
    """FastAPI 일괄(Batch) REST 엔드포인트 연동 검증"""
    import copy
    all_orig = copy.deepcopy(SourceMappingManager.load_all_mappings())

    try:
        # 1. POST /api/raw_store/batch_update_sources
        r1 = client.post("/api/raw_store/batch_update_sources", json={
            "provider": "CREON",
            "ingestion_sources": ["1M"]
        })
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["status"] == "ok"
        assert d1["updated_count"] >= 3

        # 2. POST /api/data_vault/batch_update_target_scales
        r2 = client.post("/api/data_vault/batch_update_target_scales", json={
            "symbols": ["069500", "005930"],
            "target_scales": ["10s", "1m"]
        })
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["status"] == "ok"
        assert d2["updated_count"] == 2
        assert d2["target_scales"] == ["10s", "1m"]
    finally:
        SourceMappingManager.save_all_mappings(all_orig)


def test_auto_prune_unselected_normalized_files(client, tmp_vault):
    """2단계 타겟 스케일에서 제외 시 물리 파생 Parquet 및 매니페스트 즉시 자동 삭제 검증"""
    from shared.data_library import paths as dpaths
    import copy

    symbol = "005930"
    all_orig = copy.deepcopy(SourceMappingManager.load_all_mappings())

    # 가상 파생 파일 생성
    dummy_pq = dpaths.normalized_path("candles_15m", f"{symbol}_15m.parquet")
    dummy_mf = f"{dummy_pq}.manifest.json"
    os.makedirs(os.path.dirname(dummy_pq), exist_ok=True)
    with open(dummy_pq, "w") as f:
        f.write("dummy parquet content")
    with open(dummy_mf, "w") as f:
        f.write("{}")

    assert os.path.exists(dummy_pq)
    assert os.path.exists(dummy_mf)

    try:
        # target_scales를 ["10s", "1m"]으로 저장 (15m 제외)
        res = SourceMappingManager.update_target_scales(symbol, ["10s", "1m"], prune_unselected=True)
        assert res["status"] == "ok"
        
        # 15m 파생 파일 및 매니페스트가 자동 삭제되었는지 검증
        assert not os.path.exists(dummy_pq)
        assert not os.path.exists(dummy_mf)
    finally:
        SourceMappingManager.save_all_mappings(all_orig)
        if os.path.exists(dummy_pq):
            os.remove(dummy_pq)
        if os.path.exists(dummy_mf):
            os.remove(dummy_mf)


def test_api_prune_orphans(client, tmp_vault):
    """POST /api/data_vault/prune_orphans 엔드포인트 연동 검증 (임시 볼트)"""
    from shared.data_library import paths as dpaths
    orphan = dpaths.normalized_path("candles_15m", "NOT_A_REAL_SYMBOL_15m.parquet", fallback=False)
    os.makedirs(os.path.dirname(orphan), exist_ok=True)
    with open(orphan, "w") as f:
        f.write("dummy")
    r = client.post("/api/data_vault/prune_orphans")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "deleted_count" in data
    assert "freed_mb" in data
    assert not os.path.exists(orphan)
    assert str(tmp_vault) in orphan


