# -*- coding: utf-8 -*-
"""
테스트: 원천 데이터 품질 감사 증분(Incremental) vs 전수(Full) 모드 및 비동기 엔드포인트 검증
"""
import pytest
from shared.data_library.data_quality import DataQualityAuditor
from shared.data_library.raw_store_health import RawStoreHealthEngine
from fastapi.testclient import TestClient
from CAiModelBaker.web_dashboard.main import app


def test_audit_symbol_incremental_structure():
    """단일 자산의 증분 감사(full=False) 결과가 규격화된 dict 구조를 반환하는지 검증"""
    res = DataQualityAuditor.audit_symbol("069500", force=False, full=False)
    assert isinstance(res, dict)
    for label, a in res.items():
        assert "summary" in a
        assert "days" in a
        assert "bad_days" in a["summary"]
        assert "warn_days" in a["summary"]
        assert "ok" in a["summary"]
        assert "bad" in a["summary"]
        assert "total" in a["summary"]


def test_audit_all_symbols_parallel_and_callback():
    """병렬 감사 엔진이 콜백을 정확히 호출하고 완료 결과를 집계하는지 검증"""
    called_records = []

    def _cb(cur, tot, sym, result):
        called_records.append((cur, tot, sym))

    results = DataQualityAuditor.audit_all_symbols(
        symbols=["069500"],
        full=False,
        max_workers=2,
        progress_cb=_cb
    )
    assert "069500" in results
    assert len(called_records) >= 1
    cur, tot, sym = called_records[0]
    assert cur == 1
    assert tot == 1
    assert sym == "069500"


def test_raw_store_health_engine_clear_cache():
    """메모리 캐시 클리어 메서드가 안전하게 상태를 비우는지 검증"""
    RawStoreHealthEngine._mem_cache["dummy_key"] = {"test": 1}
    RawStoreHealthEngine._file_dates_cache["dummy_file"] = {"test": 2}
    RawStoreHealthEngine._loaded_from_disk = True

    RawStoreHealthEngine.clear_cache()
    assert len(RawStoreHealthEngine._mem_cache) == 0
    assert len(RawStoreHealthEngine._file_dates_cache) == 0
    assert RawStoreHealthEngine._loaded_from_disk is False


def test_quality_audit_api_endpoints():
    """품질 감사 상태 조회 및 일괄 감사 실행 API 엔드포인트 검증"""
    client = TestClient(app)

    # 1. 상태 조회 API
    resp_status = client.get("/api/raw_store/quality_audit_status")
    assert resp_status.status_code == 200
    data = resp_status.json()
    assert data["status"] == "ok"
    assert "state" in data
    assert "running" in data["state"]
    assert "total" in data["state"]
    assert "current" in data["state"]

    # 2. 단일 심볼 감사 API (full 파라미터 지원)
    resp_single = client.get("/api/raw_store/quality_audit?symbol=069500&full=false&only_problems=true")
    assert resp_single.status_code == 200
    single_data = resp_single.json()
    assert single_data["status"] == "ok"
    assert single_data["symbol"] == "069500"
    assert "audits" in single_data
