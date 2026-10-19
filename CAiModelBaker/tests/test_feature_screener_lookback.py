import pytest
import os
import pandas as pd
import numpy as np
from CAiModelBaker.core.feature_screener import run_full_period_feature_screening

def test_feature_screener_lookback_slicing(tmp_path):
    """스크리닝 구간: 끝은 언제나 홀드아웃 직전(WFA 설정과 무관), 시작은 lookback (2026-10-20)."""
    dates = pd.date_range("2023-01-01", "2026-09-01", freq="1h")
    df = pd.DataFrame({
        "timestamp": dates,
        "target_close": 100.0 + np.cumsum(np.random.randn(len(dates))),
        "target_volume": np.random.rand(len(dates)) * 100 + 1,
        "target_high": 101.0, "target_low": 99.0, "target_open": 100.0,
    })
    snapshot_file = tmp_path / "data_snapshot_1h.csv"
    df.to_csv(snapshot_file, index=False)
    config = {
        "SYMBOL_UPBIT": "KRW-BTC", "INTERVAL": "1h", "SNAPSHOT_PATH": str(snapshot_file),
        "OPTUNA_CONFIG": {"holdout_td": 32},            # 마지막 32일 = 2026-08-01 ~ 09-01
        "TRAIN_START": "2026-03-01", "TRAIN_END": "2026-06-01",   # AutoLab WFA 구간 — 스크리닝 끝과 무관
    }
    holdout_start = pd.Timestamp("2026-08-01")

    res_3y = run_full_period_feature_screening(config, lookback_period="3y")
    assert res_3y["holdout_start"] == "2026-08-01"
    assert res_3y["end_date"] == "2026-07-31"          # TRAIN_END(06-01) 가 아니라 홀드아웃 직전
    assert res_3y["start_date"] == "2023-08-01"

    res_1y = run_full_period_feature_screening(config, lookback_period="1y")
    assert res_1y["end_date"] == "2026-07-31" and res_1y["total_samples"] < res_3y["total_samples"]

    res_all = run_full_period_feature_screening(config, lookback_period="all")
    assert res_all["total_samples"] == int((df["timestamp"] < holdout_start).sum())

    res_project = run_full_period_feature_screening(config, lookback_period="project")   # 2차 확인용
    assert (res_project["start_date"], res_project["end_date"]) == ("2026-03-01", "2026-06-01")


def test_screening_window_never_touches_holdout_and_ignores_wfa(tmp_path):
    """WFA 창 설정(TRAIN_*)이 없어도 스크리닝은 돌고, 어떤 lookback 도 홀드아웃을 보지 않는다."""
    from CAiModelBaker.core.feature_screener import slice_screening_window
    ts = pd.date_range("2025-01-01", periods=24 * 200, freq="1h")
    df = pd.DataFrame({"timestamp": ts, "target_volume": 1.0})
    cfg = {"SYMBOL_UPBIT": "KRW-BTC", "INTERVAL": "1h", "OPTUNA_CONFIG": {"holdout_td": 10}}
    for lb in ("3y", "2y", "1y", "all"):
        out, _, end, _, b = slice_screening_window(df, cfg, lb)
        assert out["timestamp"].max() < pd.Timestamp(b["holdout_start"])
        assert end == b["research_end"]
    with pytest.raises(ValueError, match="AutoLab 학습 구간"):
        slice_screening_window(df, cfg, "project")


def test_locked_horizon_is_the_only_lag_evaluated(tmp_path):
    """사정거리 잠금이면 그 지평 하나로 평가한다 (2026-10-02: 60캔들 잠금에서도 1~10캔들로 지표를 골랐다)."""
    import numpy as np
    import pandas as pd
    from CAiModelBaker.core.feature_screener import run_full_period_feature_screening

    n = 3000
    ts = pd.date_range("2025-01-01", periods=n, freq="1h")
    close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 0.5, n))
    df = pd.DataFrame({"timestamp": ts, "target_close": close, "target_open": close, "target_high": close + 0.3,
                       "target_low": close - 0.3, "target_volume": 1000.0})
    f = tmp_path / "data_snapshot_1h.csv"
    df.to_csv(f, index=False)
    cfg = {"SYMBOL_UPBIT": "KRW-BTC", "INTERVAL": "1h", "SNAPSHOT_PATH": str(f), "TRAIN_END": "2099-12-31",
           "TARGET_HORIZON_CONFIG": {"mode": "locked", "locked_predict_ahead": 24, "locked_max_hold": 24}}
    res = run_full_period_feature_screening(cfg, lookback_period="all")
    assert res["ranked_features"]
    assert all(r["optimal_lag"] == 24 for r in res["ranked_features"])
