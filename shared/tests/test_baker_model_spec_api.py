import os
import shutil
import json
import hashlib
import pytest
from fastapi.testclient import TestClient
from CTrade.ui.api_server import app

client = TestClient(app)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEPLOY_DIR = os.path.join(PROJECT_ROOT, "shared", "deploy")

@pytest.mark.skip(reason="정리된 구 모델/변경된 shared 설정 기준 테스트 - 현행 설정에 맞게 재작성 필요")
def test_get_baker_model_spec_real_session():
    """실제 배포된 KODEX200_M1_BEST3 모델의 스펙 및 SHA-256 지문 조회 검증"""
    response = client.get("/api/baker/model/spec?base=069500_1m&session=KODEX200_M1_BEST3")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    spec = data["spec"]
    
    # 1. 지문 및 기본 스펙 검증
    assert "model_fingerprint" in spec
    assert len(spec["model_fingerprint"]) == 8
    
    # 실제 model_1m.txt 파일의 sha256과 일치하는지 검증
    model_file = os.path.join(DEPLOY_DIR, "069500_1m", "KODEX200_M1_BEST3", "model", "model_1m.txt")
    if os.path.exists(model_file):
        with open(model_file, "rb") as f:
            expected_fp = hashlib.sha256(f.read()).hexdigest()[:8]
        assert spec["model_fingerprint"] == expected_fp

    # 2. 학습 완료 일시 및 학습 데이터 구간 검증
    assert spec.get("created_at") == "2026-08-29 20:47:45"
    assert spec.get("train_start") == "2026-02-25 00:00:00"
    assert spec.get("train_end") == "2026-07-11 23:31:12"
    
    # 3. 검증 메트릭 검증
    val_metrics = spec.get("validation_metrics", {})
    assert val_metrics.get("win_rate") == 100.0
    assert val_metrics.get("trades") == 8

def test_get_baker_model_spec_dummy_session():
    """가상 세션 디렉토리를 생성하여 SHA-256 해시 및 메타데이터 파싱 정합성 검증"""
    dummy_base = "TEST_SPEC_1m"
    dummy_session = "TEST_SPEC_SESSION_01"
    dummy_session_dir = os.path.join(DEPLOY_DIR, dummy_base, dummy_session)
    dummy_model_dir = os.path.join(dummy_session_dir, "model")
    
    os.makedirs(dummy_model_dir, exist_ok=True)
    
    # 가상 model.txt 작성
    dummy_content = b"lightgbm_dummy_model_weights_12345"
    expected_fp = hashlib.sha256(dummy_content).hexdigest()[:8]
    with open(os.path.join(dummy_model_dir, "model_1m.txt"), "wb") as f:
        f.write(dummy_content)
        
    # 가상 best_params.json 작성
    dummy_params = {
        "take_profit": 1.5,
        "stop_loss": 0.8,
        "buy_threshold": 0.55,
        "created_at": "2026-08-30 15:30:00",
        "train_start": "2026-01-01",
        "train_end": "2026-06-30",
        "validation_metrics": {"win_rate": 85.5, "trades": 20, "profit_factor": 2.5}
    }
    with open(os.path.join(dummy_model_dir, "best_params_1m.json"), "w", encoding="utf-8") as f:
        json.dump(dummy_params, f)
        
    # 가상 feature_spec.json 작성
    dummy_spec = {
        "interval": "1m",
        "features_order": ["feature_1", "feature_2"]
    }
    with open(os.path.join(dummy_model_dir, "feature_spec.json"), "w", encoding="utf-8") as f:
        json.dump(dummy_spec, f)

    try:
        response = client.get(f"/api/baker/model/spec?base={dummy_base}&session={dummy_session}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        spec = data["spec"]
        
        assert spec["model_fingerprint"] == expected_fp
        assert spec["created_at"] == "2026-08-30 15:30:00"
        assert spec["tp"] == 1.5
        assert spec["sl"] == 0.8
        assert spec["prob_threshold"] == 0.55
        assert spec["features_count"] == 2
        assert spec["validation_metrics"]["win_rate"] == 85.5
    finally:
        # 정리
        if os.path.exists(dummy_session_dir):
            shutil.rmtree(dummy_session_dir, ignore_errors=True)
        base_dir = os.path.join(DEPLOY_DIR, dummy_base)
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir, ignore_errors=True)

def test_get_baker_model_spec_not_found():
    """존재하지 않는 모델 세션 요청 시 에러 응답 검증"""
    response = client.get("/api/baker/model/spec?base=NON_EXISTENT&session=NOT_FOUND")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "Session directory not found" in data["message"]

def test_baker_dashboard_sessions_and_leaderboard_fingerprint():
    """CAiModelBaker 웹 대시보드 API(/api/sessions, /api/autolab/leaderboard)의 지문 반환 검증"""
    import sys
    baker_root = os.path.join(PROJECT_ROOT, "CAiModelBaker")
    
    # 임시로 sys.path 및 sys.modules 격리
    orig_path = list(sys.path)
    core_modules = {k: v for k, v in sys.modules.items() if k == "core" or k.startswith("core.")}
    for k in core_modules:
        del sys.modules[k]
    
    try:
        sys.path.insert(0, baker_root)
        from CAiModelBaker.web_dashboard.main import app as baker_app
        baker_client = TestClient(baker_app)
        
        # 1. /api/sessions 검증
        res_sess = baker_client.get("/api/sessions")
        assert res_sess.status_code == 200
        sessions = res_sess.json()
        assert isinstance(sessions, list)
        if sessions:
            first = sessions[0]
            assert "model_fingerprint" in first
            if first["model_fingerprint"] != "N/A":
                assert len(first["model_fingerprint"]) == 8

        # 2. /api/autolab/leaderboard 검증
        res_lb = baker_client.get("/api/autolab/leaderboard?session_id=KODEX200_M1_BEST3")
        assert res_lb.status_code == 200
        lb_data = res_lb.json()
        assert lb_data.get("status") == "success"
        lb_list = lb_data.get("data", {}).get("leaderboard", [])
        if lb_list:
            assert "model_fingerprint" in lb_list[0]
            if lb_list[0]["model_fingerprint"] != "N/A":
                assert len(lb_list[0]["model_fingerprint"]) == 8
    finally:
        sys.path = orig_path
        for k in list(sys.modules.keys()):
            if k == "core" or k.startswith("core."):
                del sys.modules[k]
        sys.modules.update(core_modules)

