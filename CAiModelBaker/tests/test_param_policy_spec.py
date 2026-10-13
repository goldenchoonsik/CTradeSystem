# -*- coding: utf-8 -*-
"""scale_v2 스펙 기록 래칫 — 학습에 쓴 확정 파라미터가 배포 스펙에 전부 남아야 실전이 같은 값을 계산한다."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(os.path.dirname(_HERE)),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CAiModelBaker.core import model_engine as me  # noqa: E402

_CFG = {"SYMBOL_UPBIT": "069500", "TARGET_HORIZON_CONFIG": {"mode": "locked", "locked_predict_ahead": 4}}


def test_legacy_project_spec_format_is_unchanged():
    assert me._resolved_spec_fields({"feature_params": {}}, ["target_rsi_curr"], "15m", _CFG) is None


def test_v2_spec_records_every_resolved_param():
    fc = {"param_policy": "scale_v2", "feature_params": {"target_trade_bid_ratio": {"window_mins": 10}}}
    out = me._resolved_spec_fields(fc, ["target_trade_bid_ratio", "target_macd_hist"], "15m", _CFG)
    assert out["param_policy"] == "scale_v2" and out["params_resolved"] is True
    assert out["feature_params"]["target_trade_bid_ratio"]["window_mins"] == 60.0     # H=4봉 × 15분
    assert out["feature_params"]["target_macd_hist"] == {"fast_period": 12, "slow_period": 26, "signal_period": 9}
    assert out["required_history_bars"] >= 35 and out["horizon_bars"] == 4


def test_v2_spec_refuses_unusable_feature():
    import pytest
    fc = {"param_policy": "scale_v2", "feature_params": {"target_trade_bid_ratio": {"window_mins": 15}}}
    with pytest.raises(ValueError):
        me._resolved_spec_fields(fc, ["target_trade_bid_ratio"], "15m", _CFG)
