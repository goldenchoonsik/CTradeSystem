# -*- coding: utf-8 -*-
"""실전 지표 설정은 스펙의 정책·확정 파라미터를 그대로 전달한다 (scale_v2), legacy 스펙은 종전 형식."""
from types import SimpleNamespace

from core.ai_strategy import AIStrategyModule


def _fc(spec):
    obj = SimpleNamespace(features_order=["f"], feature_params=spec.get("feature_params", {}), feature_spec=spec)
    return AIStrategyModule._live_feature_config(obj)


def test_legacy_spec_passes_no_policy():
    assert _fc({"feature_params": {"f": {}}}) == {"enabled_features": ["f"], "feature_params": {"f": {}}}


def test_v2_spec_passes_policy_and_resolved_flag():
    fc = _fc({"feature_params": {"f": {"period_bars": 14}}, "param_policy": "scale_v2",
              "params_resolved": True, "required_history_bars": 410})
    assert fc["param_policy"] == "scale_v2" and fc["params_resolved"] is True
    assert fc["required_history_bars"] == 410


def test_v2_warmup_uses_recorded_history():
    obj = SimpleNamespace(feature_spec={"param_policy": "scale_v2", "required_history_bars": 410},
                          feature_params={}, interval_minutes=15.0)
    assert AIStrategyModule._calc_required_warmup_candles(obj) == 410
