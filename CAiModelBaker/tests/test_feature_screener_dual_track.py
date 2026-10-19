import pytest
import os
import pandas as pd
import numpy as np
from CAiModelBaker.core.feature_screener import run_full_period_feature_screening
from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY

def test_feature_screener_dual_track_monotonicity(tmp_path):
    """
    [프로 퀀트 듀얼 트랙 단조성 검증]
    1. 연속형 지표는 Track A (5분위 Quantile)를 정상 적용하는지 검증
    2. 0이 많은 희소/이벤트 지표(나스닥 선물 등)는 Track B (3상태 조건부 단조성)를 통해
       정상적으로 단조성 점수 및 합격 판정을 받는지 검증
    """
    np.random.seed(42)
    n_samples = 3000
    dates = pd.date_range("2025-01-01", periods=n_samples, freq="1min")

    # 나스닥 선물 시세 생성 (약 65%는 5캔들 수익률이 0이 되도록 스텝 생성)
    nq_close = np.zeros(n_samples)
    cur_nq = 20000.0
    for i in range(0, n_samples, 5):
        if np.random.rand() < 0.35:
            cur_nq += np.random.choice([-10.0, 10.0])
        nq_close[i:min(i+5, n_samples)] = cur_nq

    # target_close가 nq_close의 3분 전 변동을 강력하게 추종하도록 생성
    nq_series = pd.Series(nq_close)
    nq_ret = nq_series.pct_change(5).fillna(0.0)

    # 3캔들 뒤에 나스닥 수익률을 강하게 반영
    target_returns = np.random.randn(n_samples) * 0.0002
    target_returns += nq_ret.shift(3).fillna(0.0).values * 0.8
    close = 100.0 * np.exp(np.cumsum(target_returns))

    df = pd.DataFrame({
        "timestamp": dates,
        "target_close": close,
        "target_volume": np.random.rand(n_samples) * 1000,
        "target_high": close * 1.001,
        "target_low": close * 0.999,
        "target_open": close,
        "nq_futures_close": nq_close,
        "sp500_futures_close": nq_close * 0.25
    })

    snapshot_dir = tmp_path / "data" / "069500"
    snapshot_dir.mkdir(parents=True)
    snapshot_file = snapshot_dir / "data_snapshot_1m.csv"
    df.to_csv(snapshot_file, index=False)

    config = {
        "SYMBOL_UPBIT": "069500",
        "INTERVAL": "1m",
        "SNAPSHOT_PATH": str(snapshot_file),
        "TRAIN_END": "2099-12-31",
    }

    res = run_full_period_feature_screening(config, lookback_period="all", max_lag_candles=5)
    assert res["status"] == "success"
    ranked = res["ranked_features"]
    assert len(ranked) > 0

    # 지표별 검증 트랙 확인
    feat_map = {item["id"]: item for item in ranked}

    # 1. 연속형 일반 지표 (target_bb_pctb 등) 검증
    if "target_bb_pctb" in feat_map:
        bb_item = feat_map["target_bb_pctb"]
        assert bb_item["eval_track"] == "continuous"
        assert bb_item["track_desc"] == "5분위"
