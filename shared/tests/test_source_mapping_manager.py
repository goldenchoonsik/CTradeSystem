# -*- coding: utf-8 -*-
import os
import pytest
from shared.data_library.source_mapping_manager import SourceMappingManager

@pytest.mark.skip(reason="정리된 구 모델/변경된 shared 설정 기준 테스트 - 현행 설정에 맞게 재작성 필요")
def test_source_mappings_load_and_active():
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
    assert isinstance(mappings, dict)
    assert len(mappings) >= 5
    assert "069500" in mappings
    assert mappings["069500"]["primary_source"] == "CREON"
    assert mappings["069500"]["fallback_source"] == "KIS"

    active = SourceMappingManager.get_active_mappings()
    assert "069500" in active
    assert "NQ" in active

@pytest.mark.skip(reason="정리된 구 모델/변경된 shared 설정 기준 테스트 - 현행 설정에 맞게 재작성 필요")
def test_feed_registry_status_inspection():
    statuses = SourceMappingManager.get_feed_registry_status()
    assert isinstance(statuses, list)
    assert len(statuses) >= 5
    
    k200 = next((s for s in statuses if s["symbol"] == "069500"), None)
    assert k200 is not None
    assert k200["primary_source"] == "CREON"
    assert k200["fallback_source"] == "KIS"
    # 실물 파일이 존재하므로 file_exists == True 여야 함
    assert k200["file_exists"] is True
    assert k200["rows"] > 0

    nq = next((s for s in statuses if s["symbol"] == "NQ"), None)
    assert nq is not None
    assert nq["file_exists"] is True
    assert nq["status_code"] == "HEALTHY"

    btc = next((s for s in statuses if s["symbol"] == "KRW-BTC"), None)
    assert btc is not None
    assert btc["file_exists"] is True
    assert btc["status_code"] == "HEALTHY"

    binance = next((s for s in statuses if s["symbol"] == "BTCUSDT"), None)
    assert binance is not None
    assert binance["file_exists"] is True
    assert binance["status_code"] == "HEALTHY"

    coinbase = next((s for s in statuses if s["symbol"] == "BTC-USD"), None)
    assert coinbase is not None
    assert coinbase["file_exists"] is True
    assert coinbase["status_code"] == "HEALTHY"

def test_register_and_toggle_asset(tmp_path, monkeypatch):
    # 임시 매핑 파일로 격리 테스트
    test_json = tmp_path / "source_mappings.tmp.json"
    monkeypatch.setattr("shared.data_library.source_mapping_manager._MAPPINGS_FILE_PATH", str(test_json))
    SourceMappingManager._cache = None

    # 신규 등록
    res = SourceMappingManager.register_asset(
        symbol="086520",
        name="에코프로",
        category="KRX_STOCK",
        role="leading_stock",
        primary_source="CREON",
        fallback_source="KIS"
    )
    assert res["status"] == "ok"
    
    loaded = SourceMappingManager.load_all_mappings(force_refresh=True)
    assert "086520" in loaded
    assert loaded["086520"]["name"] == "에코프로"
    assert loaded["086520"]["is_active"] is True

    # 토글 비활성화
    toggle_res = SourceMappingManager.toggle_asset_active("086520")
    assert toggle_res["status"] == "ok"
    assert toggle_res["is_active"] is False

    active = SourceMappingManager.get_active_mappings()
    assert "086520" not in active

