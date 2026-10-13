"""모델 ↔ feature_spec 정합성 가드 회귀 테스트 (개수/순서 불일치는 로드 시점에 차단)."""
import os
import sys
import unittest
from types import SimpleNamespace

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ai_strategy import AIStrategyModule


def _booster(cols):
    rng = np.random.RandomState(0)
    X = pd.DataFrame(rng.rand(200, len(cols)), columns=cols)
    y = (X.iloc[:, 0] > 0.5).astype(int)
    return lgb.train({"objective": "binary", "verbose": -1}, lgb.Dataset(X, y), num_boost_round=3)


def _guard(booster, features_order):
    stub = SimpleNamespace(features_order=features_order, deploy_path="test")
    AIStrategyModule._assert_model_matches_spec(stub, booster, "primary")


class TestModelSpecParityGuard(unittest.TestCase):
    def test_match_passes(self):
        _guard(_booster(["a", "b", "c"]), ["a", "b", "c"])

    def test_count_mismatch_raises(self):
        with self.assertRaises(RuntimeError):
            _guard(_booster(["a", "b"]), ["a", "b", "c"])

    def test_order_mismatch_raises(self):
        with self.assertRaises(RuntimeError):
            _guard(_booster(["a", "b", "c"]), ["a", "c", "b"])


if __name__ == "__main__":
    unittest.main()
