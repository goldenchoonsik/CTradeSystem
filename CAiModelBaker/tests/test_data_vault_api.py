# -*- coding: utf-8 -*-
"""
테스트: Web Dashboard Data Vault 3-tier Medallion API
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from CAiModelBaker.web_dashboard.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.real_vault
def test_api_get_medallion_status(client):
    """GET /api/data_vault/medallion_status 엔드포인트 무결성 검증"""
    res = client.get("/api/data_vault/medallion_status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["raw_count"] > 0
    assert data["normalized_count"] > 0
    assert "coverage_summary" in data
    assert data["coverage_summary"]["target"]["symbol"] == "069500"
    assert data["coverage_summary"]["target"]["trading_days"] >= 38


@pytest.mark.real_vault
def test_api_compose_features_v2(client):
    """POST /api/data_vault/compose_features 엔드포인트 검증"""
    import pandas as pd
    from shared.data_library import paths as dpaths
    p10s = dpaths.normalized_path("candles_10s", "069500_10s.parquet")
    expected_rows = len(pd.read_parquet(p10s)) if p10s and os.path.exists(p10s) else 91299

    payload = {
        "symbol": "069500",
        "interval": "10s",
        "save_snapshot": False
    }
    res = client.post("/api/data_vault/compose_features", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["rows"] == expected_rows
    assert "target_close" in data["columns"]


def test_api_raw_store_registry(client):
    """GET /api/raw_store/registry 엔드포인트 검증"""
    res = client.get("/api/raw_store/registry")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["total_count"] >= 5
    assert data["active_count"] > 0
    k200 = next((x for x in data["items"] if x["symbol"] == "069500"), None)
    assert k200 is not None
    assert k200["primary_source"] == "CREON"
    assert k200["fallback_source"] == "KIS"


def test_api_raw_store_toggle_asset(client):
    """POST /api/raw_store/toggle_asset 엔드포인트 검증"""
    res = client.post("/api/raw_store/toggle_asset", json={"symbol": "069500", "is_active": False})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["is_active"] is False

    # 원복
    res_restore = client.post("/api/raw_store/toggle_asset", json={"symbol": "069500", "is_active": True})
    assert res_restore.status_code == 200
    assert res_restore.json()["is_active"] is True


# 실제 DataVault/normalized 의 전 종목 정규화 파일을 다시 만들어 덮어쓰는 실데이터 통합 테스트 — 운영(학습용) 실버를 테스트가 건드리고,
# 크레온 틱 전체를 읽어 RSS 3GB+ 로 OOM 이 난다(2026-10-15). 기본 실행에서는 건너뛰고 CTRADE_REAL_VAULT_TESTS=1 일 때만 돈다.
_REAL_VAULT = pytest.mark.skipif(os.environ.get("CTRADE_REAL_VAULT_TESTS") != "1",
                                 reason="실제 정규화 볼트를 다시 쓰는 통합 테스트 — CTRADE_REAL_VAULT_TESTS=1 로만 실행")


@_REAL_VAULT
def test_api_build_normalized_all(client):
    """POST /api/data_vault/build_normalized_all 엔드포인트 검증"""
    res = client.post("/api/data_vault/build_normalized_all")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "results" in data
    assert data["results"]["success_count"] >= 15
    assert data["results"]["failed_count"] == 0


def test_api_normalized_inventory(client):
    """GET /api/data_vault/normalized_inventory 엔드포인트 검증"""
    import pandas as pd
    from shared.data_library import paths as dpaths
    p10s = dpaths.normalized_path("candles_10s", "069500_10s.parquet")
    expected_rows = len(pd.read_parquet(p10s)) if p10s and os.path.exists(p10s) else 91299

    res = client.get("/api/data_vault/normalized_inventory")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["total_count"] >= 16
    assert data["total_size_mb"] > 0
    k200 = next((x for x in data["items"] if x["symbol"] == "069500" and x["interval"] == "10s"), None)
    assert k200 is not None
    assert k200["exists"] is True
    assert k200["row_count"] == expected_rows
    assert k200["manifest_ok"] is True


def test_api_upsert_normalized_single(client):
    """POST /api/data_vault/upsert_normalized_single 엔드포인트 검증"""
    payload = {"symbol": "069500", "interval": "10s"}
    res = client.post("/api/data_vault/upsert_normalized_single", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["result"]["status"] in ["upserted", "skipped"]


def test_api_normalized_preview(client):
    """GET /api/data_vault/normalized_preview 엔드포인트 검증"""
    # 1. 국내 타겟 ETF (10s)
    res = client.get("/api/data_vault/normalized_preview?symbol=069500&interval=10s&limit=5")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert len(data["records"]) == 5
    assert "vwap" in data["columns"]
    assert "ofi" in data["columns"]
    assert "rv" in data["columns"]

    # 2. 해외 나스닥 선물 NQ (1m macro)
    #    NQ 정규화 파일이 없으면 CFD 대용(US_TECH_100)으로 폴백하는데, 이때 응답은
    #    요청 심볼로 위장하지 않고 실제로 읽은 심볼을 보고해야 한다.
    res_nq = client.get("/api/data_vault/normalized_preview?symbol=NQ&interval=1m&limit=5")
    assert res_nq.status_code == 200
    data_nq = res_nq.json()
    assert data_nq["status"] == "ok"
    assert data_nq["requested_symbol"] == "NQ"
    assert len(data_nq["records"]) == 5
    assert "close" in data_nq["columns"]
    if data_nq["is_proxy"]:
        assert data_nq["symbol"] != "NQ", "프록시 서빙인데 요청 심볼을 그대로 보고하고 있다"
        assert data_nq["symbol"] == "US_TECH_100"
    else:
        assert data_nq["symbol"] == "NQ"

    # 3. 해외 S&P500 선물 ES (1m macro)
    res_es = client.get("/api/data_vault/normalized_preview?symbol=ES&interval=1m&limit=5")
    assert res_es.status_code == 200
    data_es = res_es.json()
    assert data_es["status"] == "ok"
    assert data_es["requested_symbol"] == "ES"
    if data_es["is_proxy"]:
        assert data_es["symbol"] == "US_500"

    # 4. 국내 선물 10100 (1m macro)
    res_fut = client.get("/api/data_vault/normalized_preview?symbol=10100&interval=1m&limit=5")
    assert res_fut.status_code == 200
    assert res_fut.json()["status"] == "ok"


def test_api_features_inventory(client):
    """GET /api/features/inventory 엔드포인트 검증"""
    res = client.get("/api/features/inventory")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok", data.get("message")
    assert "inventory" in data
    assert isinstance(data["inventory"], list)


@pytest.mark.real_vault
def test_api_features_compose(client):
    """POST /api/features/compose 엔드포인트 검증"""
    payload = {
        "target_symbol": "069500",
        "target_interval": "10s",
        "ref_symbols": ["005930"],
        "dataset_name": "test_dataset_compose_api",
        "use_cache": False,
    }
    res = client.post("/api/features/compose", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok", data.get("message")
    assert data["symbol"] == "069500"
    assert data["total_rows"] > 0
    assert "cross_005930_spread" in data["columns"]


@pytest.mark.real_vault
def test_api_features_correlation_matrix(client):
    """GET /api/features/correlation_matrix 엔드포인트 검증"""
    res = client.get("/api/features/correlation_matrix?symbol=069500&interval=10s&max_features=8")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok", data.get("message")
    assert "features" in data
    assert "matrix" in data
    assert len(data["matrix"]) == len(data["features"])
    assert "high_corr_pairs" in data


def test_api_data_vault_run_full_pipeline(client):
    """POST /api/data_vault/run_full_pipeline 엔드포인트 검증 (트리거/응답 계약만 확인).

    TestClient 는 BackgroundTasks 를 응답 반환 전에 동기 실행한다. 워커를 모킹하지 않으면
    이 테스트가 실제 3단계 파이프라인(CREON PowerShell 브릿지 → --count 99999999 실수집)을
    돌려 원천 데이터를 건드린다. 과거에는 워커가 Gate 1 에서 NameError 로 즉사해 이 사실이
    가려져 있었다. 워커 동작 자체는 test_medallion_full_pipeline.py 가 검증한다.
    """
    with patch("CAiModelBaker.web_dashboard.main.run_medallion_full_pipeline_worker",
               new_callable=AsyncMock) as mock_worker:
        # 1. 증분 모드 트리거
        res_inc = client.post("/api/data_vault/run_full_pipeline", json={"mode": "incremental", "target_symbol": "069500", "interval": "10s"})
        assert res_inc.status_code == 200
        data_inc = res_inc.json()
        assert data_inc["status"] == "ok"
        assert data_inc["mode"] == "incremental"
        assert "시작되었습니다" in data_inc["message"]

        # 2. 전면 재구축 모드 트리거
        res_reb = client.post("/api/data_vault/run_full_pipeline", json={"mode": "rebuild", "target_symbol": "069500", "interval": "10s"})
        assert res_reb.status_code == 200
        data_reb = res_reb.json()
        assert data_reb["status"] == "ok"
        assert data_reb["mode"] == "rebuild"

        # 두 모드 모두 워커가 예약·실행되었는지(=엔드포인트가 실제로 파이프라인을 건다) 확인
        assert mock_worker.await_count == 2
        assert [c.args[0] for c in mock_worker.await_args_list] == ["incremental", "rebuild"]





