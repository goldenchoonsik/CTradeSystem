import pytest
import os
import tempfile
import json
from fastapi.testclient import TestClient
from shared.data_library.source_mapping_manager import SourceMappingManager
from CAiModelBaker.web_dashboard.main import app
from CAiModelBaker.core.normalized_builder import build_single_asset_normalized, build_all_active_normalized
from shared.data_library import paths as dpaths


@pytest.fixture
def client():
    return TestClient(app)


def test_update_canonical_intervals():
    """자산별 canonical_intervals 갱신 및 유효성 검증"""
    # 1. BTCUSDT (1M 원천)에 [1m, 10m, 15m] 갱신
    res = SourceMappingManager.update_canonical_intervals("BTCUSDT", ["15m", "10m", "1m"])
    assert res["status"] == "ok"
    assert res["canonical_intervals"] == ["1m", "10m", "15m"]  # 표준 정렬 확인

    # 2. 10s 등록 시 차단되지 않고 ingestion_resolution이 TICK으로 자동 승격되는지 검증
    res_tick = SourceMappingManager.update_canonical_intervals("BTCUSDT", ["10s", "1m"])
    assert res_tick["status"] == "ok"
    assert "10s" in res_tick["canonical_intervals"]
    all_maps = SourceMappingManager.load_all_mappings()
    assert all_maps["BTCUSDT"]["ingestion_resolution"] == "TICK"

    # 3. 빈 배열 등록 시도 차단
    res_empty = SourceMappingManager.update_canonical_intervals("BTCUSDT", [])
    assert res_empty["status"] == "error"

    # 4. 미등록 심볼 에러
    res_none = SourceMappingManager.update_canonical_intervals("UNKNOWN_SYM_999", ["1m"])
    assert res_none["status"] == "error"

    # 복원
    SourceMappingManager.update_canonical_intervals("BTCUSDT", ["1m", "10m", "15m"])
    all_maps = SourceMappingManager.load_all_mappings()
    all_maps["BTCUSDT"]["ingestion_resolution"] = "1M"
    SourceMappingManager.save_all_mappings(all_maps)


def test_api_update_intervals_and_inventory(client):
    """API 엔드포인트 및 인벤토리 연동 검증"""
    # 1. API를 통해 봉 스케일 업데이트
    resp = client.post("/api/raw_store/update_intervals", json={
        "symbol": "BTCUSDT",
        "canonical_intervals": ["1m", "5m", "15m"]
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["canonical_intervals"] == ["1m", "5m", "15m"]

    # 2. 정규화 인벤토리 조회 시 1m, 5m, 15m 타겟이 모두 등록되었는지 확인
    inv_resp = client.get("/api/data_vault/normalized_inventory")
    assert inv_resp.status_code == 200
    inv_data = inv_resp.json()
    assert inv_data["status"] == "ok"

    btc_items = [it for it in inv_data["items"] if it["symbol"] == "BTCUSDT"]
    btc_intervals = [it["interval"] for it in btc_items]
    assert "1m" in btc_intervals
    assert "5m" in btc_intervals
    assert "15m" in btc_intervals

    # 복원: ["1m", "10m", "15m"]
    SourceMappingManager.update_canonical_intervals("BTCUSDT", ["1m", "10m", "15m"])


@pytest.mark.real_vault
def test_build_single_asset_normalized():
    """단일 자산 일괄 정규화 빌더 실행 검증"""
    raw_btc_csv = dpaths.raw_path("crypto", "binance", "BTCUSDT_15m.csv")
    if not os.path.exists(raw_btc_csv):
        pytest.skip("BTCUSDT_15m.csv 원천 데이터 부재로 스킵")

    # BTCUSDT에 대해 15m 정규화 빌드 검증
    SourceMappingManager.update_canonical_intervals("BTCUSDT", ["15m"])
    res = build_single_asset_normalized("BTCUSDT")
    assert res["status"] == "ok"
    assert len(res["artifacts"]) >= 1
    out_file = res["artifacts"][0]
    assert os.path.exists(out_file)
    assert os.path.exists(f"{out_file}.manifest.json")

    # 복원: ["1m", "10m", "15m"]
    SourceMappingManager.update_canonical_intervals("BTCUSDT", ["1m", "10m", "15m"])
