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
        _build_feature_combinations,
        _run_ic_screening
    )
except ImportError:
    from core.research_loop import (
        _build_feature_combinations,
        _run_ic_screening
    )


class TestAdaptiveFeatureCombinations:
    """소수 지표(1~3개) 선택 시 AutoLab 탐색 유연화 및 Power-Set 조합 생성 검증"""

    def test_build_feature_combinations_single_feature(self):
        """알파 지표가 1개일 때 백본과 함께 안정적으로 단일 조합 생성 검증"""
        ic_ranked = [("alpha_1", 0.08)]
        regime = ["target_is_weekend"]

        c0 = _build_feature_combinations(ic_ranked, trial_idx=0, min_features=4, regime_features=regime)
        assert set(c0) == {"alpha_1", "target_is_weekend"}

    def test_build_feature_combinations_two_features_power_set(self):
        """알파 2개 → Power-Set 3조합. 각 조합이 백본 있음/없음 **짝**으로 생성된다."""
        ic_ranked = [("alpha_1", 0.08), ("alpha_2", 0.05)]
        regime = ["krx_session_time"]

        # 짝이 생기므로 Power-Set 을 다 돌려면 Trial 이 2배 필요하다
        combos = {tuple(sorted(_build_feature_combinations(
            ic_ranked, trial_idx=t, min_features=4, regime_features=regime))) for t in range(6)}

        expected = {
            ("alpha_1", "krx_session_time"), ("alpha_1",),
            ("alpha_2", "krx_session_time"), ("alpha_2",),
            ("alpha_1", "alpha_2", "krx_session_time"), ("alpha_1", "alpha_2"),
        }
        assert combos == expected

    def test_build_feature_combinations_three_features_power_set(self):
        """알파 3개 → Power-Set 7조합이 각각 백본 있음/없음 짝으로 생성된다 (총 14개)."""
        ic_ranked = [("alpha_1", 0.09), ("alpha_2", 0.06), ("alpha_3", 0.04)]
        regime = ["target_hour"]

        combos = [tuple(sorted(_build_feature_combinations(
            ic_ranked, trial_idx=t, min_features=4, regime_features=regime))) for t in range(14)]

        assert len(set(combos)) == 14
        with_bb = {c for c in combos if "target_hour" in c}
        without_bb = {c for c in combos if "target_hour" not in c}
        assert len(with_bb) == 7 and len(without_bb) == 7

        # 백본만 뺀 동일 알파 조합이 반드시 짝으로 존재한다
        for c in with_bb:
            assert tuple(sorted(set(c) - {"target_hour"})) in without_bb

        # Power-Set 구성: 알파 1개 3조합 / 2개 3조합 / 3개 1조합
        alpha_counts = [len(set(c) - {"target_hour"}) for c in with_bb]
        assert sorted(alpha_counts) == [1, 1, 1, 2, 2, 2, 3]

    def test_run_ic_screening_adaptive_min_screening(self):
        """가용 지표가 2개뿐일 때 min_features=4 설정에도 불구하고 에러 없이 정상 스크리닝 검증"""
        np.random.seed(42)
        n = 500
        price = 100.0 + np.cumsum(np.random.randn(n) * 0.05)
        f1 = np.roll(price, -5) - price + np.random.randn(n) * 0.01
        f2 = np.roll(price, -5) - price + np.random.randn(n) * 0.02
        reg = np.random.choice([0.0, 1.0], size=n)

        df = pd.DataFrame({
            "target_close": price,
            "strong_1": f1,
            "strong_2": f2,
            "target_is_weekend": reg
        })

        logs = []
        alpha_ranked, regime_features = _run_ic_screening(
            df=df,
            forward_candles=5,
            all_feature_ids=["target_is_weekend", "strong_1", "strong_2"],
            log_fn=lambda m: logs.append(m),
            ic_threshold=0.03,
            min_features=4  # 기본값이 4지만 가용 알파 지표가 2개뿐인 상황
        )

        # 알파 지표 2개 모두 정상 통과
        alpha_fids = [fid for fid, _ in alpha_ranked]
        assert "strong_1" in alpha_fids
        assert "strong_2" in alpha_fids
        # 체제 지표는 트랙이 분리된다. 이 합성 데이터의 weekend 는 난수라 블록 일치도를 통과하지
        # 못하므로 백본에 오르지 않는다 (무조건 프리패스가 아니다).
        assert "target_is_weekend" not in alpha_fids
        assert "target_is_weekend" not in regime_features

        # 가용 지표 풀(2개)에 맞춰 적응했으므로 "예측력 고갈" 경고가 발생하지 않음
        log_text = "\n".join(logs)
        assert "피처 예측력 고갈 가능성" not in log_text
