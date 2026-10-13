import os
import shutil
import pytest
from fastapi.testclient import TestClient
from shared.data_library.source_mapping_manager import SourceMappingManager
from shared.data_library.paths import data_vault_path
from CAiModelBaker.web_dashboard.main import app

client = TestClient(app)

def test_reset_asset_data_quarantine_and_deletion():
    test_sym = "TEST_DUMMY_CRYPTO"
    # 1. 등록
    SourceMappingManager.register_asset(
        symbol=test_sym,
        name="테스트 더미 코인",
        category="CRYPTO_SPOT",
        primary_source="BINANCE",
        primary_type="1M_15M",
        canonical_intervals=["1m", "15m"]
    )
    SourceMappingManager.update_target_scales(test_sym, ["1m", "15m"])


    # 2. 더미 원천 파일 및 정규화 파일 생성
    raw_csv = data_vault_path("raw", "crypto", "binance", f"{test_sym}_1m.csv")
    raw_mf = f"{raw_csv}.manifest.json"
    raw_days = f"{raw_csv}.days.json"
    os.makedirs(os.path.dirname(raw_csv), exist_ok=True)
    with open(raw_csv, "w") as f:
        f.write("timestamp,open,high,low,close,volume\n2026-09-26 00:00:00,100,105,95,102,1000\n")
    with open(raw_mf, "w") as f:
        f.write('{"sha256": "dummy"}\n')
    with open(raw_days, "w") as f:
        f.write('{"2026-09-26": {"rows": 1}}\n')

    norm_pq = data_vault_path("normalized", "candles_1m", f"{test_sym}_1m.parquet")
    norm_mf = f"{norm_pq}.manifest.json"
    os.makedirs(os.path.dirname(norm_pq), exist_ok=True)
    with open(norm_pq, "w") as f:
        f.write("DUMMY_PARQUET_CONTENT")
    with open(norm_mf, "w") as f:
        f.write('{"sha256": "dummy_pq"}\n')

    assert os.path.exists(raw_csv)
    assert os.path.exists(norm_pq)

    # 3. reset_asset_data 실행
    res = SourceMappingManager.reset_asset_data(test_sym)
    assert res["status"] == "ok"
    assert res["symbol"] == test_sym
    assert len(res["deleted_raw_files"]) >= 3  # csv, manifest, days
    assert len(res["deleted_norm_files"]) >= 2  # parquet, manifest

    # 원본 파일들은 삭제되어야 함
    assert not os.path.exists(raw_csv)
    assert not os.path.exists(raw_mf)
    assert not os.path.exists(raw_days)
    assert not os.path.exists(norm_pq)
    assert not os.path.exists(norm_mf)

    # quarantine 백업 디렉터리에는 보존되어야 함
    bk_dir = res["quarantine_backup_dir"]
    assert bk_dir is not None
    assert os.path.exists(os.path.join(bk_dir, f"{test_sym}_1m.csv"))

    # 대장은 여전히 살아있어야 함
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
    assert test_sym in mappings

    # 정리 (테스트 자산 및 백업 제거)
    SourceMappingManager.unregister_asset(test_sym, cascade=True)
    if os.path.exists(bk_dir):
        shutil.rmtree(bk_dir)


def test_api_reset_and_refetch_validation():
    # 심볼 미입력
    resp = client.post("/api/raw_store/reset_and_refetch", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"

    # 확인 심볼 불일치
    resp = client.post("/api/raw_store/reset_and_refetch", json={"symbol": "BTCUSDT-PERP", "confirm_symbol": "WRONG"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
    assert "종목코드를 정확히 입력" in resp.json()["message"]
