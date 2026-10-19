import pytest
import os
import pandas as pd
import numpy as np
from CAiModelBaker.core.feature_screener import run_full_period_feature_screening

def test_feature_screener_lookback_slicing(tmp_path):
    """Feature Store IC 스크리너의 스마트 기간 슬라이싱 옵션(project, 1y, all) 검증"""
    # 임의의 스냅샷 데이터 생성 (2023년 ~ 2026년, 4년 분량)
    dates = pd.date_range("2023-01-01", "2026-09-01", freq="1h")
    df = pd.DataFrame({
        "timestamp": dates,
        "target_close": 100.0 + np.cumsum(np.random.randn(len(dates))),
        "target_volume": np.random.rand(len(dates)) * 100,
        "target_high": 101.0,
        "target_low": 99.0,
        "target_open": 100.0,
    })
    
    # tmp_path에 스냅샷 저장
    snapshot_dir = tmp_path / "data" / "BTC"
    snapshot_dir.mkdir(parents=True)
    snapshot_file = snapshot_dir / "data_snapshot_1h.csv"
    df.to_csv(snapshot_file, index=False)

    config = {
        "SYMBOL_UPBIT": "KRW-BTC",
        "INTERVAL": "1h",
        "TRAIN_START": "2025-01-01",
        "TRAIN_END": "2026-06-01",     # 연구(학습) 구간 끝 — 홀드아웃은 스크리닝에서 제외
        "HOLDOUT_END": "2026-09-01",
        "SNAPSHOT_PATH": str(snapshot_file)
    }

    # 1. 'project' 모드 테스트 (2025-01-01 ~ 2026-06-01, 홀드아웃 끝이 아니라 학습 구간 끝까지)
    res_project = run_full_period_feature_screening(config, lookback_period="project")
    assert res_project["status"] == "success"
    assert res_project["period_mode"] == "project"
    assert "프로젝트 연구 구간" in res_project["period_desc"]
    assert res_project["start_date"] == "2025-01-01"
    assert res_project["end_date"] == "2026-06-01"
    # 슬라이싱된 캔들 수는 전체보다 작아야 함
    assert res_project["total_samples"] < len(df)
    assert res_project["total_samples"] > 0

    # 2. '1y' 모드 테스트 (최근 1년)
    res_1y = run_full_period_feature_screening(config, lookback_period="1y")
    assert res_1y["status"] == "success"
    assert res_1y["period_mode"] == "1y"
    assert "끝에서 1년" in res_1y["period_desc"]
    assert res_1y["end_date"] == "2026-06-01"   # 홀드아웃 제외
    assert res_1y["total_samples"] < res_project["total_samples"]

    # 3. 'all' 모드 테스트 (전체 데이터)
    res_all = run_full_period_feature_screening(config, lookback_period="all")
    assert res_all["status"] == "success"
    assert res_all["period_mode"] == "all"
    # 전체 = 연구 기간 끝(06-01 장 마감)까지 — 홀드아웃(06-02~09-01)은 어느 모드에서도 안 본다
    assert res_all["total_samples"] == int((df["timestamp"] < "2026-06-02").sum())


def test_screener_refuses_without_research_period(tmp_path):
    """연구 기간 미설정이면 전체 아카이브로 넘어가지 않고 멈춘다 (홀드아웃까지 보고 지표를 고르던 경로)."""
    ts = pd.date_range("2025-01-01", periods=500, freq="1h")
    df = pd.DataFrame({"timestamp": ts, "target_close": 100.0 + np.arange(500) * 0.01})
    f = tmp_path / "data_snapshot_1h.csv"
    df.to_csv(f, index=False)
    cfg = {"SYMBOL_UPBIT": "KRW-BTC", "INTERVAL": "1h", "SNAPSHOT_PATH": str(f)}
    for mode in ("project", "1y", "all"):
        with pytest.raises(ValueError, match="연구 기간"):
            run_full_period_feature_screening(cfg, lookback_period=mode)


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
