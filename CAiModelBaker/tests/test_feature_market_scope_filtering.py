import pytest
import os
import json
import shutil
from fastapi.testclient import TestClient
from shared.features.feature_registry import (
    filter_compatible_features,
    get_incompatible_features,
    detect_symbol_market_scope,
    DEFAULT_FEATURE_REGISTRY
)
from CAiModelBaker.web_dashboard.main import app
from shared.config import project_store

client = TestClient(app)


def test_filter_compatible_features_logic():
    """자산군별 지표 호환성 필터링 함수 단위 검증"""
    mixed_features = [
        "target_ma_ratio",              # 공통 (all)
        "binance_leading_momentum",     # 가상자산 (crypto)
        "kimchi_premium",               # 가상자산 (crypto)
        "target_rsi_daily",             # 공통 (all)
        "krx_session_time",             # 국내 주식 (krx)
        "target_is_weekend",            # 가상자산 (crypto)
        "target_cvd_momentum",          # 공통 (all)
    ]

    # 1. 국내 주식/ETF (069500)
    krx_filtered = filter_compatible_features("069500", mixed_features)
    assert "target_ma_ratio" in krx_filtered
    assert "target_rsi_daily" in krx_filtered
    assert "krx_session_time" in krx_filtered
    assert "target_cvd_momentum" in krx_filtered
    assert "binance_leading_momentum" not in krx_filtered
    assert "kimchi_premium" not in krx_filtered
    assert "target_is_weekend" not in krx_filtered

    # 2. 가상자산 (KRW-BTC)
    crypto_filtered = filter_compatible_features("KRW-BTC", mixed_features)
    assert "target_ma_ratio" in crypto_filtered
    assert "binance_leading_momentum" in crypto_filtered
    assert "kimchi_premium" in crypto_filtered
    assert "target_is_weekend" in crypto_filtered
    assert "krx_session_time" not in crypto_filtered


def test_api_create_project_auto_sanitizes_incompatible_features(tmp_path):
    """신규 KRX 프로젝트 생성 시 부적합 지표가 원천 차단되는지 E2E 검증"""
    test_session_id = "TEST_KRX_CLEAN_AUTO_1"
    symbol = "069500"
    interval = "1m"
    market_dir = f"{symbol}_{interval}"
    ref = project_store.make_ref(market_dir, test_session_id)

    # 정리 후 테스트 시작
    test_dir = project_store.project_dir(ref)
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)

    try:
        response = client.post("/api/project/create", json={
            "name": test_session_id,
            "symbol": symbol,
            "interval": interval,
            "memo": "Auto-sanitization test"
        })
        assert response.status_code == 200
        res_data = response.json()
        assert res_data.get("status") == "ok"

        # 생성된 config_snapshot.json 검증
        snap_path = os.path.join(test_dir, "config_snapshot.json")
        assert os.path.exists(snap_path)
        with open(snap_path, "r", encoding="utf-8") as f:
            snap = json.load(f)

        enabled = snap.get("FEATURE_CONFIG", {}).get("enabled_features", [])
        incomp = get_incompatible_features(symbol, enabled, session_id=test_session_id)
        assert len(incomp) == 0, f"Found incompatible features in new project: {incomp}"

        # 생성된 feature_spec.json 검증
        spec_path = os.path.join(test_dir, "model", "feature_spec.json")
        assert os.path.exists(spec_path)
        with open(spec_path, "r", encoding="utf-8") as f:
            spec = json.load(f)

        spec_order = spec.get("features_order", [])
        spec_incomp = get_incompatible_features(symbol, spec_order, session_id=test_session_id)
        assert len(spec_incomp) == 0, f"Found incompatible features in feature_spec: {spec_incomp}"

    finally:
        # 테스트 완료 후 폴더 청소
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)
