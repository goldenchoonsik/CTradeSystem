# -*- coding: utf-8 -*-
"""
테스트: 3단계 메달리온(Bronze -> Silver -> Gold) 통합 파이프라인 워커 및 Gate 1/2 무결성 검증
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.testclient import TestClient

from CAiModelBaker.web_dashboard.main import app, run_medallion_full_pipeline_worker


@pytest.fixture
def client():
    return TestClient(app)


def test_api_run_full_pipeline_endpoint(client):
    """POST /api/data_vault/run_full_pipeline API 엔드포인트 요청 검증 (단일 스마트 모드 및 하위 호환)"""
    with patch("CAiModelBaker.web_dashboard.main.run_medallion_full_pipeline_worker", new_callable=AsyncMock) as mock_worker, \
         patch("shared.data_library.source_mapping_manager.SourceMappingManager.get_active_mappings", return_value={"069500": {"primary_source": "CREON"}}):
        
        # 1. 기본 스마트 모드 요청
        res = client.post("/api/data_vault/run_full_pipeline", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["mode"] == "smart"
        assert "시작되었습니다" in data["message"]

        # 2. rebuild 모드 요청
        res_reb = client.post("/api/data_vault/run_full_pipeline", json={"mode": "rebuild"})
        assert res_reb.status_code == 200
        assert res_reb.json()["mode"] == "rebuild"


def test_api_smart_normalize_all_endpoint(client):
    """POST /api/data_vault/smart_normalize_all (2단계 단독 스마트 정규화 API) 검증"""
    mock_res = {"status": "ok", "built_count": 2, "upserted_count": 3, "failed_count": 0}
    with patch("CAiModelBaker.core.normalized_builder.smart_normalize_all_active", return_value=mock_res):
        res = client.post("/api/data_vault/smart_normalize_all")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "스마트 정규화 완료" in data["message"]
        assert data["results"]["built_count"] == 2
        assert data["results"]["upserted_count"] == 3


@pytest.mark.anyio
async def test_run_medallion_full_pipeline_worker_success():
    """모든 계층 성공 시: 3단계를 순차적으로 실행하고 Gate 1, Gate 2를 통과하여 완결되는지 검증"""
    broadcast_messages = []

    async def mock_broadcast(msg_str):
        broadcast_messages.append(json.loads(msg_str))

    with patch("CAiModelBaker.web_dashboard.main.manager.broadcast", side_effect=mock_broadcast), \
         patch("shared.data_library.source_mapping_manager.SourceMappingManager.get_active_mappings", return_value={"069500": {"primary_source": "CREON"}}), \
         patch("shared.data_library.source_mapping_manager.SourceMappingManager.find_raw_file_and_manifest", return_value={"exists": True, "file_path": "/fake/raw.csv"}), \
         patch("CAiModelBaker.web_dashboard.main.run_active_feeds_sync_worker", new_callable=AsyncMock) as mock_raw_sync, \
         patch("CAiModelBaker.core.normalized_builder.smart_normalize_all_active", return_value={"status": "ok", "built_count": 1, "upserted_count": 0, "failed_count": 0}) as mock_smart_norm, \
         patch("CAiModelBaker.core.feature_composer.FeatureComposer.compose", return_value=MagicMock()) as mock_compose, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=1024):

        # 스마트 모드로 일괄 파이프라인 워커 실행
        await run_medallion_full_pipeline_worker("smart", {"target_symbol": "069500", "interval": "10s", "all_projects": False})

        # Step 1 (Bronze) 호출 검증
        assert mock_raw_sync.called
        call_kwargs = mock_raw_sync.call_args[1]
        assert call_kwargs["progress_task"] == "medallion_full_pipeline"
        assert call_kwargs["emit_task_done"] is False

        # Step 2 (Silver) 호출 검증
        assert mock_smart_norm.called

        # Step 3 (Gold) 호출 검증
        assert mock_compose.called

        # 브로드캐스트 단계 검증
        task_progresses = [m for m in broadcast_messages if m.get("type") == "task_progress" and m.get("task") == "medallion_full_pipeline"]
        steps = [m.get("step") for m in task_progresses if "step" in m]
        assert 1 in steps
        assert 2 in steps
        assert 3 in steps

        # 최종 완결 메시지 검증
        done_msgs = [m for m in broadcast_messages if m.get("type") == "task_done" and m.get("task") == "medallion_full_pipeline"]
        assert len(done_msgs) == 1
        assert done_msgs[0]["status"] == "success"


@pytest.mark.anyio
async def test_run_medallion_full_pipeline_gate1_fail_stops_execution():
    """Gate 1 실패 시: 1단계 원천 파일 미확보 시 2단계/3단계로 진입하지 않고 즉시 Fail-Fast 중단되는지 검증"""
    broadcast_messages = []

    async def mock_broadcast(msg_str):
        broadcast_messages.append(json.loads(msg_str))

    with patch("CAiModelBaker.web_dashboard.main.manager.broadcast", side_effect=mock_broadcast), \
         patch("shared.data_library.source_mapping_manager.SourceMappingManager.get_active_mappings", return_value={"069500": {"primary_source": "CREON"}}), \
         patch("shared.data_library.source_mapping_manager.SourceMappingManager.find_raw_file_and_manifest", return_value={"exists": False, "file_path": ""}), \
         patch("CAiModelBaker.web_dashboard.main.run_active_feeds_sync_worker", new_callable=AsyncMock) as mock_raw_sync, \
         patch("CAiModelBaker.core.normalized_builder.smart_normalize_all_active") as mock_smart_norm, \
         patch("CAiModelBaker.core.feature_composer.FeatureComposer.compose") as mock_compose:

        # 워커 실행 (Gate 1에서 차단되어야 함)
        await run_medallion_full_pipeline_worker("smart", {"target_symbol": "069500", "interval": "10s"})

        # Step 1은 실행됨
        assert mock_raw_sync.called

        # Gate 1 차단으로 인해 Step 2, Step 3은 절대 호출되지 않아야 함!
        assert not mock_smart_norm.called
        assert not mock_compose.called

        # 실패 메시지 확인
        done_msgs = [m for m in broadcast_messages if m.get("type") == "task_done" and m.get("task") == "medallion_full_pipeline"]
        assert len(done_msgs) == 1
        assert done_msgs[0]["status"] == "error"
        assert "Gate 1 무결성 검증 실패" in done_msgs[0]["error"]


@pytest.mark.anyio
async def test_run_medallion_full_pipeline_gate2_fail_stops_execution():
    """Gate 2 실패 시: 2단계 정규화 파일 누락 시 3단계 피처 결합으로 진입하지 않고 즉시 Fail-Fast 중단되는지 검증"""
    broadcast_messages = []

    async def mock_broadcast(msg_str):
        broadcast_messages.append(json.loads(msg_str))

    # Gate 1은 통과시키고, Gate 2(Parquet 검사)에서 파일이 없다고 판단되도록 모킹
    def mock_exists(path):
        if str(path).endswith(".csv"):
            return True
        return False  # Parquet 파일 부재

    with patch("CAiModelBaker.web_dashboard.main.manager.broadcast", side_effect=mock_broadcast), \
         patch("shared.data_library.source_mapping_manager.SourceMappingManager.get_active_mappings", return_value={"069500": {"primary_source": "CREON"}}), \
         patch("shared.data_library.source_mapping_manager.SourceMappingManager.find_raw_file_and_manifest", return_value={"exists": True, "file_path": "/fake/069500.csv"}), \
         patch("CAiModelBaker.web_dashboard.main.run_active_feeds_sync_worker", new_callable=AsyncMock) as mock_raw_sync, \
         patch("CAiModelBaker.core.normalized_builder.smart_normalize_all_active", return_value={"status": "ok", "built_count": 0, "upserted_count": 0, "failed_count": 0}) as mock_smart_norm, \
         patch("CAiModelBaker.core.feature_composer.FeatureComposer.compose") as mock_compose, \
         patch("os.path.exists", side_effect=mock_exists), \
         patch("os.path.getsize", return_value=1024):

        # 워커 실행 (Gate 2에서 차단되어야 함)
        await run_medallion_full_pipeline_worker("smart", {"target_symbol": "069500", "interval": "10s"})

        # Step 1, Step 2는 실행됨
        assert mock_raw_sync.called
        assert mock_smart_norm.called

        # Gate 2 차단으로 인해 Step 3은 절대 호출되지 않아야 함!
        assert not mock_compose.called

        # 실패 메시지 확인
        done_msgs = [m for m in broadcast_messages if m.get("type") == "task_done" and m.get("task") == "medallion_full_pipeline"]
        assert len(done_msgs) == 1
        assert done_msgs[0]["status"] == "error"
        assert "Gate 2 무결성 검증 실패" in done_msgs[0]["error"]
