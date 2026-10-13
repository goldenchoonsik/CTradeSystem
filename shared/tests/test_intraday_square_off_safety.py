import os
import sys
import pytest
from httpx import AsyncClient, ASGITransport
from shared.core.market_utils import detect_market_type

# CAiModelBaker 임포트 시 CTrade의 core 네임스페이스 오염 방지
_orig_core = sys.modules.get("core")
_baker_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "CAiModelBaker")
if _baker_dir not in sys.path:
    sys.path.insert(0, _baker_dir)
_temp_popped = {}
for _m in list(sys.modules.keys()):
    if _m == "core" or _m.startswith("core."):
        _mod = sys.modules.get(_m)
        if hasattr(_mod, "__file__") and _mod.__file__ and "CAiModelBaker" not in _mod.__file__:
            _temp_popped[_m] = sys.modules.pop(_m)

from CAiModelBaker.web_dashboard.main import app, load_config, save_config

# CAiModelBaker의 core 패키지 캐시를 정리하여 이후 CTrade core 모듈들이 정상 로드되도록 복원
for _m in list(sys.modules.keys()):
    if _m == "core" or _m.startswith("core."):
        _mod = sys.modules.get(_m)
        if hasattr(_mod, "__file__") and _mod.__file__ and "CAiModelBaker" in _mod.__file__:
            del sys.modules[_m]

sys.modules.update(_temp_popped)
if _orig_core is not None:
    sys.modules["core"] = _orig_core

sys.path = [p for p in sys.path if not (isinstance(p, str) and p.rstrip("/").endswith("CAiModelBaker"))]


@pytest.mark.asyncio
async def test_detect_market_type():
    """자산 식별 함수가 암호화폐와 KRX 주식을 정확히 구분하는지 확인"""
    assert detect_market_type({'SYMBOL_UPBIT': 'KRW-BTC'}) == 'CRYPTO'
    assert detect_market_type({'SYMBOL_UPBIT': 'BTC-ETH'}) == 'CRYPTO'
    assert detect_market_type({'SYMBOL_UPBIT': 'USDT-BTC'}) == 'CRYPTO'
    assert detect_market_type({'SYMBOL_UPBIT': '069500'}) == 'KRX'
    assert detect_market_type({'SYMBOL_UPBIT': '122630'}) == 'KRX'


@pytest.mark.asyncio
async def test_api_status_includes_market_type():
    """/api/status 엔드포인트가 market_type을 정확히 반환하는지 확인"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/status")
        assert res.status_code == 200
        data = res.json()
        assert "market_type" in data
        assert data["market_type"] in ["CRYPTO", "KRX"]


@pytest.mark.asyncio
async def test_api_save_config_crypto_forces_intraday_false():
    """가상자산 프로젝트는 INTRADAY_SQUARE_OFF=True 를 보내도 False 로 강제 고정, KRX 프로젝트는 True 허용.

    종목은 프로젝트 신원(폴더 경로)이라 /api/config 로 바꿀 수 없다 — 종목별로 프로젝트를 열어 검증한다.
    (2026-10-01 자유 모드 은퇴 전에는 프로젝트 없이 종목을 바꿔 저장했다)
    """
    from shared.config import config_manager as cm
    from shared.config import project_store as ps

    original_ref = cm.get_active_project_ref()
    made = []

    def _open(symbol, interval, name):
        ref = ps.make_ref(ps.market_dir_for(symbol, interval), name)
        ps.create_project_dir(ref)
        made.append(ref)
        cfg = cm.load_base_config("CAiModelBaker")
        cfg.update({"SYMBOL_UPBIT": symbol, "INTERVAL": interval, "ACTIVE_SESSION_ID": name})
        ps.write_project_config(ref, cfg)
        cm.set_active_project_ref(ref)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            _open("KRW-BTC", "15m", "T_INTRADAY_CRYPTO")
            res = await ac.post("/api/config", json={"INTRADAY_SQUARE_OFF": True})
            assert res.status_code == 200
            assert load_config().get("INTRADAY_SQUARE_OFF") is False, "CRYPTO should force INTRADAY_SQUARE_OFF to False"

            _open("069500", "1m", "T_INTRADAY_KRX")
            res_krx = await ac.post("/api/config", json={"INTRADAY_SQUARE_OFF": True})
            assert res_krx.status_code == 200
            assert load_config().get("INTRADAY_SQUARE_OFF") is True, "KRX should allow INTRADAY_SQUARE_OFF to be True"
    finally:
        import shutil
        cm.set_active_project_ref(original_ref)
        for ref in made:
            shutil.rmtree(ps.project_dir(ref), ignore_errors=True)
