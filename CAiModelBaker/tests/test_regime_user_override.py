import pytest
import numpy as np
import pandas as pd
import sys
import os
import json
import tempfile

_BAKER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BAKER_ROOT not in sys.path:
    sys.path.insert(0, _BAKER_ROOT)

try:
    from CAiModelBaker.core.research_loop import (
        _is_regime_feature,
        _run_ic_screening,
        _build_feature_combinations
    )
except ImportError:
    from core.research_loop import (
        _is_regime_feature,
        _run_ic_screening,
        _build_feature_combinations
    )
from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY, get_market_scope
from shared.features.feature_config import load_feature_config, save_feature_config


class TestRegimeUserOverrideAndMarketScope:
    """사용자 체제 지표 커스텀 제어(IC 면제 토글) 및 자산군(market_scope) 무결성 테스트"""

    def test_user_override_regime_feature(self):
        """사용자가 체제 지표를 IC평가로 전환하거나, 알파 지표를 백본으로 승격할 수 있는지 검증"""
        # 1. 기본 레지스트리 동작
        assert _is_regime_feature("target_is_weekend") is True
        assert _is_regime_feature("target_rsi_curr") is False

        # 2. 사용자 override: target_is_weekend를 일반 IC 평가 대상으로 전환 (False)
        user_config_1 = {"target_is_weekend": False}
        assert _is_regime_feature("target_is_weekend", user_config_1) is False

        # 3. 사용자 override: target_rsi_curr를 백본 고정(IC 면제)으로 승격 (True)
        user_config_2 = {"target_rsi_curr": True}
        assert _is_regime_feature("target_rsi_curr", user_config_2) is True

        # 4. 명시되지 않은 지표는 기본값 유지
        assert _is_regime_feature("target_hour", user_config_2) is True
        assert _is_regime_feature("target_bb_pctb", user_config_2) is False

    def test_run_ic_screening_with_user_override(self):
        """사용자 override 는 '어느 트랙에서 평가할지' 를 정한다 — 통과를 보장하지는 않는다.

        종전에는 체제 트랙이 무조건 프리패스였으므로 override=True 가 곧 통과였다. 이제 체제
        트랙도 블록 일치도 검정을 받으므로, override 의 효과는 **선형 IC 대신 블록 검정으로
        평가된다**는 것이다.
        """
        rng = np.random.default_rng(42)
        n = 6000
        idx = np.arange(n)
        weekend = (idx % 7 < 2).astype(float)
        # 구간을 넘어 유지되는 집단 효과 (선형 IC 로는 잘 보이지 않는 계단형)
        step = rng.normal(0, 0.01, n) + np.where(weekend > 0, 0.004, -0.001)
        price = 100.0 * np.cumprod(1.0 + step)

        df = pd.DataFrame({
            "target_close": price,
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="min"),
            "target_is_weekend": weekend,
            "target_rsi_curr": rng.normal(0, 1, n),   # 구조 없음
        })
        ids = ["target_is_weekend", "target_rsi_curr"]

        # 시나리오 A: target_is_weekend 를 사용자가 **알파 트랙**으로 돌린다 (False)
        #   → 체제 목록에 들어가지 않는다 (선형 IC 로 평가된다)
        _alpha_a, regime_a = _run_ic_screening(
            df=df, forward_candles=5, all_feature_ids=ids,
            log_fn=lambda m: None, ic_threshold=0.1,
            user_regime_config={"target_is_weekend": False})
        assert "target_is_weekend" not in regime_a

        # 시나리오 B: target_rsi_curr 를 사용자가 **체제 트랙**으로 승격한다 (True)
        #   → 알파 순위에서 빠지고 체제 트랙에서 평가된다. 구조가 없으므로 통과하지 못한다.
        alpha_b, regime_b = _run_ic_screening(
            df=df, forward_candles=5, all_feature_ids=ids,
            log_fn=lambda m: None, ic_threshold=0.1,
            user_regime_config={"target_rsi_curr": True})
        assert "target_rsi_curr" not in [fid for fid, _ in alpha_b], \
            "체제 트랙으로 승격했는데 알파 순위에도 들어갔다"
        assert "target_rsi_curr" not in regime_b, \
            "구조 없는 지표가 override 만으로 백본에 올랐다"
        # 같은 실행에서, 구조가 있는 체제 지표는 통과한다 (게이트가 전부를 막는 게 아니다)
        assert "target_is_weekend" in regime_b

    def test_market_scope_registry_classification(self):
        """지표 레지스트리의 market_scope 분류 무결성 검증"""
        # 1. 코인 전용 지표 확인
        assert get_market_scope("target_is_weekend", DEFAULT_FEATURE_REGISTRY.get("target_is_weekend", {})) == "crypto"
        assert get_market_scope("kimchi_premium", DEFAULT_FEATURE_REGISTRY.get("kimchi_premium", {})) == "crypto"
        assert get_market_scope("binance_funding_rate", DEFAULT_FEATURE_REGISTRY.get("binance_funding_rate", {})) == "crypto"
        assert get_market_scope("binance_oi_ma_ratio", DEFAULT_FEATURE_REGISTRY.get("binance_oi_ma_ratio", {})) == "crypto"

        # 2. KRX 주식 전용 지표 확인
        assert get_market_scope("krx_session_time", DEFAULT_FEATURE_REGISTRY.get("krx_session_time", {})) == "krx"
        assert get_market_scope("kospi_basis", DEFAULT_FEATURE_REGISTRY.get("kospi_basis", {})) == "krx"

        # 3. 공통 지표 확인
        assert get_market_scope("target_rsi_curr", DEFAULT_FEATURE_REGISTRY.get("target_rsi_curr", {})) == "all"
        assert get_market_scope("target_bb_pctb", DEFAULT_FEATURE_REGISTRY.get("target_bb_pctb", {})) == "all"
        assert get_market_scope("target_macd_hist", DEFAULT_FEATURE_REGISTRY.get("target_macd_hist", {})) == "all"

        # DEFAULT_FEATURE_REGISTRY 모든 항목에 market_scope가 주입되어 있는지 확인
        for fid, info in DEFAULT_FEATURE_REGISTRY.items():
            assert "market_scope" in info
            assert info["market_scope"] in ("all", "crypto", "krx", "us")

    def test_feature_config_regime_persistence(self, tmp_path, monkeypatch):
        """save_feature_config 및 load_feature_config에서 regime_features 설정이 올바르게 저장/로드되는지 검증"""
        test_config_file = tmp_path / "active_test.json"
        monkeypatch.setattr("shared.features.feature_config.get_config_path", lambda: str(test_config_file))

        # 1. regime_features 저장
        test_enabled = ["target_rsi_curr", "target_is_weekend", "target_bb_pctb"]
        test_regime = {
            "target_is_weekend": False,  # 사용자가 IC평가로 전환
            "target_rsi_curr": True      # 사용자가 백본으로 승격
        }

        save_feature_config(
            enabled_features=test_enabled,
            feature_params={},
            active_features=test_enabled,
            regime_features=test_regime
        )

        assert test_config_file.exists()

        # 2. 파일에서 올바르게 로드되는지 확인
        loaded = load_feature_config()
        assert "regime_features" in loaded
        assert loaded["regime_features"].get("target_is_weekend") is False
        assert loaded["regime_features"].get("target_rsi_curr") is True
