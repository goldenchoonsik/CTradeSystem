import os
import shutil
import pytest
from fastapi.testclient import TestClient
from CTrade.ui.api_server import app, CONFIG_PATH

client = TestClient(app)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEPLOY_DIR = os.path.join(PROJECT_ROOT, "shared", "deploy")

@pytest.fixture
def setup_dummy_model():
    dummy_base = "TEST_BASE_1m"
    dummy_session = "TEST_SESSION_DELETE_ME"
    dummy_path = os.path.join(DEPLOY_DIR, dummy_base, dummy_session)
    
    os.makedirs(dummy_path, exist_ok=True)
    with open(os.path.join(dummy_path, "dummy_model.txt"), "w") as f:
        f.write("test model content")
        
    yield dummy_base, dummy_session, dummy_path
    
    # Clean up if still exists
    if os.path.exists(dummy_path):
        shutil.rmtree(dummy_path, ignore_errors=True)
    base_dir = os.path.join(DEPLOY_DIR, dummy_base)
    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, ignore_errors=True)

def test_delete_model_success(setup_dummy_model):
    base, session, path = setup_dummy_model
    assert os.path.exists(path)
    
    response = client.post("/api/baker/model/delete", json={"base": base, "session": session})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert not os.path.exists(path)

def test_delete_model_active_session_blocked():
    # trading_config.json에 활성 등록된 세션을 동적으로 조회하여 삭제 차단 검증
    import json
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    tmpl = cfg.get("STRATEGY_TEMPLATES", {}).get("ai_baker", {})
    active_session = tmpl.get("deploy_session") or None
    active_base = tmpl.get("deploy_base", "").replace("\\", "/").split("/")[-1] if tmpl.get("deploy_base") else None
    
    if not active_session:
        for t in cfg.get("STRATEGY_TEMPLATES", {}).values():
            if isinstance(t, dict) and t.get("type") == "AI_BAKER" and t.get("deploy_session"):
                active_session = t.get("deploy_session")
                active_base = t.get("deploy_base", "").replace("\\", "/").split("/")[-1]
                break

    response = client.post("/api/baker/model/delete", json={"base": active_base, "session": active_session})
    assert response.status_code == 400
    data = response.json()
    assert data["status"] == "error"
    assert "사용 중인" in data["message"]

def test_delete_model_traversal_blocked():
    response = client.post("/api/baker/model/delete", json={"base": "../", "session": "test"})
    assert response.status_code == 400
    data = response.json()
    assert data["status"] == "error"

def test_delete_model_not_found():
    response = client.post("/api/baker/model/delete", json={"base": "NON_EXISTING_BASE", "session": "NON_EXISTING_SESSION"})
    assert response.status_code == 404
    data = response.json()
    assert data["status"] == "error"
