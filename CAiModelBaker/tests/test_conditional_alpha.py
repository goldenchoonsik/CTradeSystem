import pytest
import numpy as np
import pandas as pd
import sys
import os

_BAKER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BAKER_ROOT not in sys.path:
    sys.path.insert(0, _BAKER_ROOT)

try:
    from CAiModelBaker.core.research_loop import (
        _is_regime_feature,
        _run_ic_screening,
        calculate_conditional_alpha
    )
except ImportError:
    from core.research_loop import (
        _is_regime_feature,
        _run_ic_screening,
        calculate_conditional_alpha
    )
from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY, is_trigger_feature


class TestConditionalAlphaTriggerEvaluation:
    """희귀 이벤트/트리거 지표 조건부 알파(Conditional Alpha) 평가 및 구제 검증"""

    def test_is_trigger_feature_identification(self):
        """트리거 지표 식별 함수 무결성 검증"""
        # 트리거 지표: True
        assert is_trigger_feature("target_bb_kc_squeeze") is True
        assert is_trigger_feature("target_volume_spike") is True
        assert is_trigger_feature("target_trade_price_spike") is True
        assert is_trigger_feature("binance_spot_quote_spike") is True
        assert is_trigger_feature("target_large_trade_ratio") is True

        # 일반 연속형 지표: False
        assert is_trigger_feature("target_rsi_curr") is False
        assert is_trigger_feature("target_ma_ratio") is False
        assert is_trigger_feature("target_bb_pctb") is False

        # 체제 지표: False (체제 지표는 Track 1 백본 프리패스로 처리)
        assert is_trigger_feature("target_is_weekend") is False
        assert is_trigger_feature("target_hour") is False

    def test_calculate_conditional_alpha_synthetic(self):
        """희귀 이벤트(2%)에서 전구간 Spearman IC는 희석되지만 조건부 알파로 정상 통과 검증"""
        np.random.seed(42)
        n = 3000
        
        # 98% 0, 2% 1인 희귀 트리거 지표 (60회 발동)
        trigger = np.zeros(n)
        event_indices = np.random.choice(np.arange(100, n - 20), size=60, replace=False)
        trigger[event_indices] = 1.0

        # 기본 전방 수익률: 평균 0, 표준편차 0.008
        forward_returns = np.random.randn(n) * 0.008
        # 이벤트 발생 시점에만 +0.0025 (+0.25%)의 일관된 알파 부여
        forward_returns[event_indices] += 0.0025

        df = pd.DataFrame({
            "trigger_feat": trigger,
            "_forward_return": forward_returns
        })

        # 1. 일반 Spearman IC는 전구간 희석으로 인해 0.05 미만(탈락 수준)으로 나옴
        try:
            from CAiModelBaker.core.feature_tuner import calculate_ic
        except ImportError:
            from core.feature_tuner import calculate_ic
        spearman_ic = calculate_ic(df, "trigger_feat", "_forward_return")
        assert abs(spearman_ic) < 0.05  # 전구간 IC는 희석됨

        # 2. 조건부 알파 계산: 통과해야 함
        res = calculate_conditional_alpha(df, "trigger_feat", "_forward_return")
        assert res["passed"] is True
        assert res["n_event"] == 60
        assert res["spread"] > 0.0015  # 스프레드 +0.15% 이상
        assert res["win_rate"] > 0.55  # 승률 55% 이상
        assert res["t_stat"] > 1.96    # 통계적 유의성 (p < 0.05)
        assert res["effective_ic"] >= 0.025  # 등가 IC 정상 산출

    def test_calculate_conditional_alpha_noise_rejected(self):
        """알파가 없는 순수 노이즈 트리거는 조건부 평가에서도 엄격하게 탈락하는지 검증"""
        np.random.seed(42)
        n = 2000
        trigger = np.zeros(n)
        event_indices = np.random.choice(n, size=100, replace=False)
        trigger[event_indices] = 1.0

        # 아무런 엣지가 없는 순수 노이즈 전방 수익률
        forward_returns = np.random.randn(n) * 0.01

        df = pd.DataFrame({
            "noise_trigger": trigger,
            "_forward_return": forward_returns
        })

        res = calculate_conditional_alpha(df, "noise_trigger", "_forward_return")
        assert res["passed"] is False  # 스프레드와 t-통계량이 미달하여 탈락해야 함

    def test_run_ic_screening_multi_track_salvation(self):
        """IC 미달 트리거가 조건부 알파로 구제되는지 + 체제 지표가 트랙 분리되는지 통합 검증"""
        np.random.seed(42)
        n = 2000
        # 기본 가격: 변동성 있는 랜덤 워크
        price = 100.0 + np.cumsum(np.random.randn(n) * 0.08)

        # 1. 체제 지표 (target_is_weekend): 체제 트랙에서 블록 일치도로 평가된다
        weekend = np.random.choice([0.0, 1.0], size=n)

        # 2. 강력한 일반 연속형 알파 지표 (strong_alpha): |IC| >= 0.05
        f_strong = np.roll(price, -5) - price + np.random.randn(n) * 0.01

        # 3. 변동성 스퀴즈 트리거 지표 (target_bb_kc_squeeze):
        # 평소에는 0.05 ~ 1.0 사이의 연속 노이즈 (전구간 선형 상관계수는 노이즈로 인해 희석)
        squeeze = np.abs(np.random.randn(n) * 0.3) + 0.05
        # 1.5%의 희귀 구간(30개)에서만 스퀴즈(-1.0) 발생
        sq_idx = np.random.choice(np.arange(50, n - 20), size=30, replace=False)
        squeeze[sq_idx] = -1.0

        for idx in sq_idx:
            price[idx + 5] = price[idx] * 1.0035  # +0.35% 상승 엣지

        # 4. 아무런 엣지 없는 순수 노이즈 지표
        noise = np.random.randn(n)

        df = pd.DataFrame({
            "target_close": price,
            "target_is_weekend": weekend,
            "strong_alpha": f_strong,
            "target_bb_kc_squeeze": squeeze,
            "pure_noise": noise
        })

        logs = []
        alpha_ranked, regime_features = _run_ic_screening(
            df=df,
            forward_candles=5,
            all_feature_ids=["target_is_weekend", "strong_alpha", "target_bb_kc_squeeze", "pure_noise"],
            log_fn=lambda m: logs.append(m),
            ic_threshold=0.05,
            min_features=2
        )

        alpha_fids = [fid for fid, _ in alpha_ranked]

        # 1. 체제 지표는 알파 트랙에 섞이지 않고 체제 트랙에서 따로 평가된다.
        #    (이 합성 데이터의 weekend 는 수익률과 무관한 난수라 블록 일치도 검정을 통과하지
        #     못한다 — 무조건 프리패스가 아니라는 것이 새 계약이다)
        assert "target_is_weekend" not in alpha_fids
        assert "target_is_weekend" not in regime_features

        # 2. 강력한 연속형 알파 지표 통과
        assert "strong_alpha" in alpha_fids

        # 3. 순수 노이즈는 탈락
        assert "pure_noise" not in alpha_fids

        # 4. 희귀 스퀴즈 트리거 지표는 선형 IC(< 0.03) 미달이지만 조건부 알파를 통해 구제 합류해야 함!
        assert "target_bb_kc_squeeze" in alpha_fids

        # 5. 로그에 조건부 알파 통과 문구가 출력되었는지 확인
        log_text = "\n".join(logs)
        assert "조건부 알파 통과" in log_text
