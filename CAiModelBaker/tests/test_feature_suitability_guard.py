import pytest
import sys
import os

_BAKER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BAKER_ROOT not in sys.path:
    sys.path.insert(0, _BAKER_ROOT)

from shared.features.feature_registry import (
    DEFAULT_FEATURE_REGISTRY,
    validate_feature_registry,
    detect_symbol_market_scope,
    get_incompatible_features
)


class TestFeatureSuitabilityGuard:
    """지표 적합성 검증 및 사전 경고 가드 단위 테스트"""

    def test_validate_feature_registry_integrity(self):
        """레지스트리 린터가 모든 지표에 필수 속성을 정상 보장하는지 검증"""
        issues = validate_feature_registry()
        # 모든 지표에 name, category, market_scope, is_regime, is_trigger가 존재해야 함
        for fid, finfo in DEFAULT_FEATURE_REGISTRY.items():
            assert "name" in finfo, f"{fid} name 누락"
            assert "category" in finfo, f"{fid} category 누락"
            assert "market_scope" in finfo, f"{fid} market_scope 누락"
            assert finfo["market_scope"] in ("all", "crypto", "krx", "us"), f"{fid} 잘못된 market_scope"
            assert "is_regime" in finfo, f"{fid} is_regime 누락"
            assert "is_trigger" in finfo, f"{fid} is_trigger 누락"
            assert callable(finfo.get("compute")), f"{fid} compute 함수 누락"

    def test_detect_symbol_market_scope(self):
        """심볼별 자산군 자동 식별 무결성 검증"""
        # KRX 종목 (6자리 숫자)
        assert detect_symbol_market_scope("069500") == "krx"
        assert detect_symbol_market_scope("122630") == "krx"
        assert detect_symbol_market_scope("005930") == "krx"

        # Crypto 가상자산
        assert detect_symbol_market_scope("KRW-BTC") == "crypto"
        assert detect_symbol_market_scope("KRW-ETH") == "crypto"
        assert detect_symbol_market_scope("BTC-USDT") == "crypto"
        assert detect_symbol_market_scope("BTC") == "crypto"
        assert detect_symbol_market_scope("XRP") == "crypto"

        # US 미국 주식/ETF
        assert detect_symbol_market_scope("QQQ") == "us"
        assert detect_symbol_market_scope("TQQQ") == "us"
        assert detect_symbol_market_scope("NVDA") == "us"

        # 세션 ID 기반 연동 판별 (심볼 누락 시에도 세션명으로 정상 판정)
        assert detect_symbol_market_scope("", session_id="BTC_M10_BEST7") == "crypto"
        assert detect_symbol_market_scope("", session_id="KODEX200_M1_BEST3") == "krx"
        assert detect_symbol_market_scope("", session_id="GOOGL_M5_BEST1") == "us"

    def test_get_incompatible_features_krx_target(self):
        """KRX 주식/ETF 프로젝트에서 가상자산 전용 지표가 부적합으로 검출되는지 검증"""
        krx_symbol = "069500"
        active_fids = [
            "target_rsi_curr",             # all: 정상
            "krx_session_time",            # krx: 정상
            "gold_futures_ret_curr",       # all (글로벌 선물 매크로): 정상
            "us10y_futures_ret_curr",      # all (글로벌 선물 매크로): 정상
            "target_is_weekend",           # crypto: ⚠️ 부적합
            "binance_funding_settle_window" # crypto: ⚠️ 부적합
        ]

        incompatible = get_incompatible_features(krx_symbol, active_fids)
        incomp_ids = [item["feature_id"] for item in incompatible]

        assert "target_rsi_curr" not in incomp_ids
        assert "krx_session_time" not in incomp_ids
        assert "gold_futures_ret_curr" not in incomp_ids, "글로벌 선물 모멘텀은 KRX 적합 지표여야 함"
        assert "us10y_futures_ret_curr" not in incomp_ids, "글로벌 선물 모멘텀은 KRX 적합 지표여야 함"
        assert "target_is_weekend" in incomp_ids
        assert "binance_funding_settle_window" in incomp_ids

        # 반환 메타데이터 검증
        item = next(x for x in incompatible if x["feature_id"] == "target_is_weekend")
        assert item["market_scope"] == "crypto"
        assert item["symbol_scope"] == "krx"
        assert "가상자산" in item["reason"]

    def test_get_incompatible_features_crypto_target(self):
        """Crypto 가상자산 프로젝트에서 주식 전용 지표가 부적합으로 검출되는지 검증"""
        crypto_symbol = "KRW-BTC"
        active_fids = [
            "target_rsi_curr",             # all: 정상
            "target_is_weekend",           # crypto: 정상
            "krx_session_time",            # krx: ⚠️ 부적합
            "kospi_basis"                  # krx: ⚠️ 부적합
        ]

        incompatible = get_incompatible_features(crypto_symbol, active_fids)
        incomp_ids = [item["feature_id"] for item in incompatible]

        assert "target_rsi_curr" not in incomp_ids
        assert "target_is_weekend" not in incomp_ids
        assert "krx_session_time" in incomp_ids
        assert "kospi_basis" in incomp_ids

    def test_api_validate_feature_suitability(self):
        """FastAPI 적합성 검증 API 엔드포인트 동작 검증"""
        from fastapi.testclient import TestClient
        from web_dashboard.main import app

        client = TestClient(app)
        
        # 1. KRX 심볼 쿼리
        res = client.get("/api/features/validate_suitability?symbol=069500")
        assert res.status_code == 200
        data = res.json()
        assert data["symbol"] == "069500"
        assert data["symbol_scope"] == "krx"
        assert "is_suitable" in data
        assert "incompatible_features" in data
        assert "total_enabled" in data

        # 2. Crypto 심볼 쿼리
        res2 = client.get("/api/features/validate_suitability?symbol=KRW-BTC")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["symbol"] == "KRW-BTC"
        assert data2["symbol_scope"] == "crypto"
