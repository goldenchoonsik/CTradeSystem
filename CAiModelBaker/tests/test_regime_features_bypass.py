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
        _build_feature_combinations
    )
except ImportError:
    from core.research_loop import (
        _is_regime_feature,
        _run_ic_screening,
        _build_feature_combinations
    )
from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY


class TestRegimeFeaturesBypass:
    """체제/컨텍스트 지표(Regime Feature) 프리패스 및 백본 주입 무결성 테스트"""

    def test_is_regime_feature_identification(self):
        """체제 지표 식별 정확도 검증"""
        # 체제 지표들: True여야 함
        assert _is_regime_feature("target_is_weekend") is True
        assert _is_regime_feature("target_hour") is True
        assert _is_regime_feature("target_hour_sin") is True
        assert _is_regime_feature("target_hour_cos") is True
        assert _is_regime_feature("krx_session_time") is True
        assert _is_regime_feature("binance_funding_settle_window") is True

        # 일반 알파/모멘텀 지표들: False여야 함
        assert _is_regime_feature("target_rsi_curr") is False
        assert _is_regime_feature("target_bb_pctb") is False
        assert _is_regime_feature("coinbase_premium_index") is False
        assert _is_regime_feature("target_ma_ratio") is False

    def _screen(self, weekend_effect, n=6000, seed=42):
        """체제 지표 1개 + 강한 알파 1개 + 잡음 1개로 스크리닝을 돌린다.

        weekend_effect(idx, flag) -> 전방수익률에 더할 값. 이것으로 '구간 넘어 유지되는 체제
        효과' 와 '구간마다 뒤집히는 효과' 를 갈라 만든다.
        """
        rng = np.random.default_rng(seed)
        idx = np.arange(n)
        weekend = (idx % 7 < 2).astype(float)
        step = rng.normal(0, 0.01, n) + weekend_effect(idx, weekend)
        price = 100.0 * np.cumprod(1.0 + step)
        f_strong = np.roll(price, -5) - price + rng.normal(0, 0.1, n)
        df = pd.DataFrame({
            "target_close": price,
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="min"),
            "target_is_weekend": weekend,
            "strong_alpha": f_strong,
            "noise_feat": rng.normal(0, 1, n),
        })
        logs = []
        alpha_ranked, regime_features = _run_ic_screening(
            df=df, forward_candles=5,
            all_feature_ids=["target_is_weekend", "strong_alpha", "noise_feat"],
            log_fn=lambda msg: logs.append(msg), ic_threshold=0.1, min_features=1,
        )
        return alpha_ranked, regime_features, "\n".join(logs)

    def test_regime_feature_with_stable_structure_passes(self):
        """체제 지표의 집단 효과가 구간을 넘어 유지되면 통과한다 (선형 IC 와 무관하게)."""
        # 주말 플래그가 있을 때 수익률이 전 구간 내내 같은 방향으로 높다
        alpha_ranked, regime_features, log_text = self._screen(
            lambda idx, flag: np.where(flag > 0, 0.004, -0.001))

        assert "target_is_weekend" in regime_features
        assert "블록 일치도 통과" in log_text
        # 알파 순위에는 섞이지 않고 분리 관리된다
        assert "target_is_weekend" not in [fid for fid, _ in alpha_ranked]

    def test_regime_feature_that_reverses_across_blocks_is_rejected(self):
        """같은 지표라도 효과가 구간마다 뒤집히면 백본에 올리지 않는다.

        종전에는 **무조건 프리패스**였다. 그 결과 챔피언 모델 gain 의 84.5% 를 시각 지표가
        차지했는데도 어느 단계에서도 드러나지 않았다(2026-09-28 실측).
        """
        n = 6000
        alpha_ranked, regime_features, log_text = self._screen(
            # 전반에는 +, 후반에는 - — 전구간 평균으로는 상쇄돼 보이지 않는다
            lambda idx, flag: np.where(flag > 0, np.where(idx < n // 2, 0.004, -0.004), 0.0), n=n)

        assert "target_is_weekend" not in regime_features
        assert "구간마다 뒤바뀜" in log_text

    def test_regime_feature_with_no_structure_is_rejected(self):
        """효과가 아예 없는 체제 지표도 백본에 올리지 않는다."""
        alpha_ranked, regime_features, log_text = self._screen(lambda idx, flag: 0.0)

        assert "target_is_weekend" not in regime_features
        alpha_ids = [fid for fid, _ in alpha_ranked]
        assert "strong_alpha" in alpha_ids
        assert "noise_feat" not in alpha_ids

    def test_indeterminate_regime_feature_is_not_a_pass(self):
        """판정 불가(표본 부족 등)는 통과가 아니다 — 근거 없이 백본에 올리면 안 된다."""
        rng = np.random.default_rng(1)
        n = 120  # 블록 판정에 필요한 최소 표본 미달
        price = 100.0 + np.cumsum(rng.normal(0, 1, n))
        df = pd.DataFrame({
            "target_close": price,
            "target_is_weekend": (np.arange(n) % 7 < 2).astype(float),
            "strong_alpha": np.roll(price, -5) - price,
        })
        logs = []
        _alpha, regime_features = _run_ic_screening(
            df=df, forward_candles=5,
            all_feature_ids=["target_is_weekend", "strong_alpha"],
            log_fn=lambda m: logs.append(m), ic_threshold=0.1, min_features=1,
        )
        assert "target_is_weekend" not in regime_features
        assert "판정 불가" in "\n".join(logs)

    def test_build_feature_combinations_backbone_attachment(self):
        """체제 지표는 백본으로 탑재되지만, 같은 알파 조합의 **대조군(백본 없음)** 이 짝으로 생긴다.

        백본이 모든 조합에 강제 부착되면 자기 부재와 비교된 적이 없어 기여를 알 수 없다.
        """
        alpha_ranked = [
            ("feat_alpha_1", 0.08),
            ("feat_alpha_2", 0.06),
            ("feat_alpha_3", 0.04),
            ("feat_alpha_4", 0.035),
            ("feat_alpha_5", 0.03),
        ]
        regime_features = ["target_is_weekend"]

        combos = [
            _build_feature_combinations(
                ic_ranked=alpha_ranked, trial_idx=t,
                min_features=3, max_features=5, regime_features=regime_features)
            for t in range(10)
        ]
        for t, combo in enumerate(combos):
            alpha_in_combo = [f for f in combo if f != "target_is_weekend"]
            assert len(alpha_in_combo) >= 3, f"Trial {t}의 알파 지표 수가 최소치 미달: {alpha_in_combo}"

        with_bb = [c for c in combos if "target_is_weekend" in c]
        without_bb = [c for c in combos if "target_is_weekend" not in c]
        assert with_bb, "백본이 탑재된 조합이 하나도 없다"
        assert without_bb, "백본 없는 대조군 조합이 하나도 없다 — 백본 기여를 측정할 수 없다"

        # 짝이어야 한다: 백본만 뺀 동일 알파 조합이 실제로 존재한다
        for c in with_bb:
            bare = tuple(sorted(f for f in c if f != "target_is_weekend"))
            assert bare in {tuple(sorted(x)) for x in without_bb}, \
                f"백본 조합 {c} 의 대조군이 없다"

        # 체제 지표가 없으면 짝을 만들지 않는다 — 연속한 두 Trial 이 같은 조합이 되면
        # (백본을 뺄 게 없으므로) 절반이 중복으로 버려진다.
        no_regime = [
            _build_feature_combinations(ic_ranked=alpha_ranked, trial_idx=t,
                                       min_features=3, max_features=5, regime_features=[])
            for t in range(4)
        ]
        assert no_regime[0] != no_regime[1], "체제 지표가 없는데 짝(중복 Trial)을 만들고 있다"
        assert [len(c) for c in no_regime[:3]] == [3, 4, 5]

    def test_features_config_api_includes_is_regime(self):
        """API /api/features/config 호출 시 registry에 is_regime 속성이 정상 반환되는지 검증"""
        from fastapi.testclient import TestClient
        from web_dashboard.main import app

        client = TestClient(app)
        res = client.get("/api/features/config")
        assert res.status_code == 200
        data = res.json()
        assert "registry" in data
        assert data["registry"]["target_is_weekend"].get("is_regime") is True
        assert data["registry"]["target_hour"].get("is_regime") is True

