# -*- coding: utf-8 -*-
"""
스케일 파이프라인 시간 단위별 개별 갱신(Sync) 및 안전 삭제(Delete) API 검증 테스트
"""
import os
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from CAiModelBaker.web_dashboard.main import app
from shared.data_library.paths import data_vault_path


@pytest.fixture
def client():
    return TestClient(app)


def test_sync_single_scale_validation(client):
    """필수 파라미터 누락 및 미등록 자산 검증"""
    # 1. symbol 누락
    res = client.post("/api/data_vault/sync_single_scale", json={"scale": "1m"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "error"
    assert "필수" in data["message"]

    # 2. scale 누락
    res = client.post("/api/data_vault/sync_single_scale", json={"symbol": "KRW-BTC"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "error"
    assert "필수" in data["message"]

    # 3. 미등록 자산
    res = client.post("/api/data_vault/sync_single_scale", json={"symbol": "NON_EXISTENT_COIN", "scale": "1m"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "error"
    assert "등록되지 않은 자산" in data["message"]


def test_sync_single_scale_crypto_raw(client):
    """가상자산 원천(Raw) 단일 스케일 핀포인트 수집 검증"""
    with patch("CAiModelBaker.core.crypto_collector.sync_single_crypto_asset", return_value=True) as mock_sync:
        res = client.post("/api/data_vault/sync_single_scale", json={
            "symbol": "KRW-BTC",
            "scale": "1d",
            "tier": "raw"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["scale"] == "1d"
        assert data["tier"] == "raw"
        mock_sync.assert_called_once()
        args, kwargs = mock_sync.call_args
        assert args[0] == "KRW-BTC"
        assert kwargs["target_scale"] == "1d"


def test_sync_single_scale_normalized(client):
    """정규화(Normalized) 단일 스케일 재합성 검증"""
    with patch("CAiModelBaker.core.normalized_builder.build_normalized_crypto", return_value="/dummy/path/KRW-BTC_15m.parquet") as mock_build:
        res = client.post("/api/data_vault/sync_single_scale", json={
            "symbol": "KRW-BTC",
            "scale": "15m",
            "tier": "normalized"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["scale"] == "15m"
        assert data["tier"] == "normalized"
        mock_build.assert_called_once()
        _, kwargs = mock_build.call_args
        assert kwargs["symbol"] == "KRW-BTC"
        assert kwargs["interval"] == "15m"


def test_delete_single_scale_raw(client, tmp_path):
    """원천 파일(CSV/매니페스트/원장) 물리 삭제 검증"""
    test_sym = "KRW-TESTCOIN"
    raw_dir = data_vault_path("raw", "crypto", "upbit")
    os.makedirs(raw_dir, exist_ok=True)
    dummy_csv = os.path.join(raw_dir, f"{test_sym}_1d.csv")
    dummy_mf = dummy_csv + ".manifest.json"

    with open(dummy_csv, "w", encoding="utf-8") as f:
        f.write("timestamp,open,high,low,close,volume\n2026-01-01,100,105,95,102,10\n")
    with open(dummy_mf, "w", encoding="utf-8") as f:
        json.dump({"symbol": test_sym, "interval": "1d"}, f)

    assert os.path.exists(dummy_csv)
    assert os.path.exists(dummy_mf)

    with patch("shared.data_library.source_mapping_manager.SourceMappingManager.load_all_mappings",
               return_value={test_sym: {"symbol": test_sym, "primary_source": "UPBIT", "category": "CRYPTO_SPOT"}}):
        res = client.post("/api/data_vault/delete_single_scale", json={
            "symbol": test_sym,
            "scale": "1d",
            "tier": "raw"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert dummy_csv in data["deleted_files"]
        assert dummy_mf in data["deleted_files"]
        assert not os.path.exists(dummy_csv)
        assert not os.path.exists(dummy_mf)


def test_delete_single_scale_normalized(client):
    """정규화 파일(Parquet/매니페스트) 물리 삭제 검증"""
    test_sym = "KRW-TESTCOIN"
    norm_dir = data_vault_path("normalized", "candles_15m")
    os.makedirs(norm_dir, exist_ok=True)
    dummy_pq = os.path.join(norm_dir, f"{test_sym}_15m.parquet")
    dummy_mf = dummy_pq + ".manifest.json"

    with open(dummy_pq, "w", encoding="utf-8") as f:
        f.write("DUMMY_PARQUET_CONTENT")
    with open(dummy_mf, "w", encoding="utf-8") as f:
        json.dump({"symbol": test_sym, "interval": "15m"}, f)

    assert os.path.exists(dummy_pq)
    assert os.path.exists(dummy_mf)

    with patch("shared.data_library.source_mapping_manager.SourceMappingManager.load_all_mappings",
               return_value={test_sym: {"symbol": test_sym, "primary_source": "UPBIT", "category": "CRYPTO_SPOT"}}):
        res = client.post("/api/data_vault/delete_single_scale", json={
            "symbol": test_sym,
            "scale": "15m",
            "tier": "normalized"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert dummy_pq in data["deleted_files"]
        assert dummy_mf in data["deleted_files"]
        assert not os.path.exists(dummy_pq)
        assert not os.path.exists(dummy_mf)


@pytest.mark.real_vault
def test_health_matrix_intervals_crypto_multi_scale(client):
    """가상자산(UPBIT) 원천 복수 그래뉼러리티(1M, 1D 등) 자동 탐색 검증"""
    from shared.data_library.raw_store_health import RawStoreHealthEngine
    res = client.get("/api/raw_store/health_matrix/intervals?symbol=KRW-BTC&force_refresh=true")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    intervals = data.get("intervals", [])
    labels = [iv.get("label") for iv in intervals]
    # 실제 수집된 1M과 1D가 모두 검색되어야 함
    assert "1M" in labels
    assert "1D" in labels


def test_sync_single_scale_cme_raw(client):
    """CME 해외선물 원천 단일 스케일 수집 검증"""
    with patch("CAiModelBaker.core.overseas_futures_collector.download_and_save_futures", return_value="/dummy/NQ_1d.csv"):
        with patch("os.path.exists", return_value=True):
            res = client.post("/api/data_vault/sync_single_scale", json={
                "symbol": "NQ",
                "scale": "1d",
                "tier": "raw"
            })
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["scale"] == "1d"
            assert data["tier"] == "raw"


def test_sync_single_scale_ctrader_raw(client):
    """cTrader CFD 원천 단일 스케일 수집 검증"""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    with patch("subprocess.run", return_value=mock_proc) as mock_run:
        res = client.post("/api/data_vault/sync_single_scale", json={
            "symbol": "US_TECH_100",
            "scale": "1d",
            "tier": "raw"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["scale"] == "1d"
        assert data["tier"] == "raw"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--period" in cmd
        assert "1d" in cmd


def test_delete_single_scale_ctrader_raw(client):
    """cTrader CFD 원천 파일 안전 삭제 검증"""
    raw_dir = data_vault_path("raw", "ctrader")
    os.makedirs(raw_dir, exist_ok=True)
    test_csv = os.path.join(raw_dir, "US_TECH_100_1d.csv")
    test_mf = test_csv + ".manifest.json"
    with open(test_csv, "w") as f:
        f.write("dummy")
    with open(test_mf, "w") as f:
        f.write("{}")

    assert os.path.exists(test_csv)
    res = client.post("/api/data_vault/delete_single_scale", json={
        "symbol": "US_TECH_100",
        "scale": "1d",
        "tier": "raw"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert not os.path.exists(test_csv)
    assert not os.path.exists(test_mf)


