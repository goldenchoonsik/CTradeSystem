# -*- coding: utf-8 -*-
"""
단위 테스트: 전체 데이터(80/20) 분할 시 분봉 표준 윈도우 크기 자동 주입 및 0 버그 방지 검증
"""
import pandas as pd
import pytest

from CAiModelBaker.core.split_planner import plan_split
from CAiModelBaker.core.split_standards import (
    STANDARD_SPLITS,
    get_standard_split,
)


def _create_multi_day_dummy_data(days: int = 500):
    """지정된 거래일 수만큼의 가상 캔들 데이터프레임 생성 (주말 제외)"""
    dates = pd.bdate_range(end="2026-10-02", periods=days)
    ts_list = []
    for d in dates:
        ts_list.append(pd.Timestamp(f"{d.strftime('%Y-%m-%d')} 09:00:00"))
        ts_list.append(pd.Timestamp(f"{d.strftime('%Y-%m-%d')} 15:30:00"))
    return pd.DataFrame({
        "timestamp": ts_list,
        "volume": [1000.0] * len(ts_list),
        "close": [100.0] * len(ts_list)
    })


def test_plan_split_full_mode_1m():
    """1m 분봉 기준 full 모드(80/20) 시 train/opt/test가 0이 아닌 41/10/10으로 배정되어야 한다."""
    df = _create_multi_day_dummy_data(500)
    plan = plan_split(df, "1m", market="KRX", mode="full")

    assert plan["ok"] is True
    assert plan["mode"] == "full"
    # train/opt/test 윈도우 크기가 0이 아니어야 함
    assert plan["train_td"] == 41
    assert plan["opt_td"] == 10
    assert plan["test_td"] == 10
    assert plan["expected_wfa_windows"] > 0

    # units 딕셔너리 검증
    units = plan["units"]
    assert units["td"]["train"] == 41
    assert units["td"]["opt"] == 10
    assert units["td"]["test"] == 10
    assert units["bars"]["train"] > 0
    assert units["months"]["train"] > 0


def test_plan_split_full_mode_10s():
    """10s 초봉 기준 full 모드(80/20) 시 sub_minute 표준(21/5/5)이 배정되어야 한다."""
    df = _create_multi_day_dummy_data(100)
    plan = plan_split(df, "10s", market="KRX", mode="full")

    assert plan["ok"] is True
    assert plan["mode"] == "full"
    assert plan["train_td"] == 21
    assert plan["opt_td"] == 5
    assert plan["test_td"] == 5
    assert plan["expected_wfa_windows"] > 0


def test_plan_split_full_mode_15m():
    """15m 분봉 기준 full 모드(80/20) 시 5m 버킷 표준(82/21/21)이 배정되어야 한다."""
    df = _create_multi_day_dummy_data(600)
    plan = plan_split(df, "15m", market="KRX", mode="full")

    assert plan["ok"] is True
    assert plan["mode"] == "full"
    assert plan["train_td"] == 82
    assert plan["opt_td"] == 21
    assert plan["test_td"] == 21
    assert plan["expected_wfa_windows"] > 0


def test_plan_split_full_mode_1d():
    """1d 일봉 기준 full 모드(80/20) 시 1d 버킷 표준(252/63/63)이 배정되어야 한다."""
    df = _create_multi_day_dummy_data(1000)
    plan = plan_split(df, "1d", market="KRX", mode="full")

    assert plan["ok"] is True
    assert plan["mode"] == "full"
    assert plan["train_td"] == 252
    assert plan["opt_td"] == 63
    assert plan["test_td"] == 63
    assert plan["expected_wfa_windows"] > 0


def test_plan_split_smart_mode_1m():
    """1m 분봉 기준 smart 모드 시 표준 룩백(126거래일) 및 윈도우가 정상 유지되어야 한다."""
    df = _create_multi_day_dummy_data(500)
    plan = plan_split(df, "1m", market="KRX", mode="smart")

    assert plan["ok"] is True
    assert plan["mode"] == "standard"
    assert plan["lookback_td"] == 126
    assert plan["train_td"] == 41
    assert plan["opt_td"] == 10
    assert plan["test_td"] == 10
    assert plan["holdout_td"] == 21


def test_api_autolab_smart_split_full_mode():
    """/api/autolab/smart_split?mode=full 엔드포인트 호출 시 train_td가 0이 아닌 유효값으로 반환되어야 한다."""
    from fastapi.testclient import TestClient
    from CAiModelBaker.web_dashboard.main import app

    client = TestClient(app)
    resp = client.get("/api/autolab/smart_split?mode=full")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    if data.get("ok"):
        plan = data["plan"]
        assert plan["train_td"] > 0
        assert plan["opt_td"] > 0
        assert plan["test_td"] > 0
        assert plan["units"]["td"]["train"] > 0


def test_crypto_split_smart_mode():
    """가상화폐(KRW-BTC) 기준 smart 모드 시 크립토 전용 압축 룩백 및 윈도우가 적용되어야 한다."""
    df = _create_multi_day_dummy_data(300)

    # 1m 분봉: 42일 lookback, train 14, opt 4, test 4, holdout 8
    plan_1m = plan_split(df, "1m", market="KRW-BTC", mode="smart")
    assert plan_1m["ok"] is True
    assert plan_1m["lookback_td"] == 42
    assert plan_1m["train_td"] == 14
    assert plan_1m["opt_td"] == 4
    assert plan_1m["test_td"] == 4
    assert plan_1m["holdout_td"] == 8

    # 10s 초봉: 21일 lookback, train 8, opt 2, test 2, holdout 3
    plan_10s = plan_split(df, "10s", market="KRW-BTC", mode="smart")
    assert plan_10s["ok"] is True
    assert plan_10s["lookback_td"] == 21
    assert plan_10s["train_td"] == 8
    assert plan_10s["opt_td"] == 2
    assert plan_10s["test_td"] == 2
    assert plan_10s["holdout_td"] == 3

    # 15m 단기: 84일 lookback, train 28, opt 7, test 7, holdout 14
    plan_15m = plan_split(df, "15m", market="KRW-BTC", mode="smart")
    assert plan_15m["ok"] is True
    assert plan_15m["lookback_td"] == 84
    assert plan_15m["train_td"] == 28
    assert plan_15m["opt_td"] == 7
    assert plan_15m["test_td"] == 7
    assert plan_15m["holdout_td"] == 14

    # 60m (1h) 시간봉: 600일 더미 데이터 기준 540일 lookback, train 180, opt 30, test 30, holdout 90
    df_long = _create_multi_day_dummy_data(600)
    plan_60m = plan_split(df_long, "60m", market="KRW-BTC", mode="smart")
    assert plan_60m["ok"] is True
    assert plan_60m["lookback_td"] == 540
    assert plan_60m["train_td"] == 180
    assert plan_60m["opt_td"] == 30
    assert plan_60m["test_td"] == 30
    assert plan_60m["holdout_td"] == 90


def test_crypto_split_full_mode():
    """가상화폐(KRW-BTC) 기준 full 모드 시 크립토 표준 윈도우가 자동 배정되어야 한다."""
    df = _create_multi_day_dummy_data(300)
    plan = plan_split(df, "1m", market="KRW-BTC", mode="full")

    assert plan["ok"] is True
    assert plan["mode"] == "full"
    assert plan["train_td"] == 14
    assert plan["opt_td"] == 4
    assert plan["test_td"] == 4
    assert plan["expected_wfa_windows"] > 0


def test_api_asrl_timeline_declarative_snap():
    """WFA 횟수와 홀드아웃 일수 기반 선언형 역산 API 검증 (거래일 기반 무결 스냅)"""
    from fastapi.testclient import TestClient
    from CAiModelBaker.web_dashboard.main import app

    client = TestClient(app)
    # 테스트 하네스(10s 초봉 데이터 약 30거래일) 스케일에 맞추어 Train 8, Opt 2, Test 2, Windows 5회, Holdout 3일 호출
    res = client.get("/api/asrl/timeline?train_td=8&opt_td=2&test_td=2&wfa_windows_count=5&holdout_td=3&unit=td")
    assert res.status_code == 200
    data = res.json()

    assert "wfa_start" in data
    assert "wfa_end" in data
    assert "holdout_start" in data
    assert "holdout_end" in data
    assert len(data.get("windows", [])) == 5
    assert data["data_exceeded"] is False
    assert data["wfa_end"] < data["holdout_start"]  # WFA와 홀드아웃 완벽 분리


def test_wfa_window_count_persistence_and_sync():
    """WFA 윈도우 횟수 수정 후 /api/config 및 /api/features/config 호출 시 값 보존 검증"""
    from CAiModelBaker.web_dashboard.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # 1. /api/config로 WFA 횟수 8로 저장
    res = client.post("/api/config", json={
        "OPTUNA_CONFIG": {
            "wfa_window_count": 8,
            "holdout_td": 14
        }
    })
    assert res.status_code == 200
    assert res.json().get("status") == "ok"

    # 2. /api/status 조회하여 8 유지 확인
    status_res = client.get("/api/status")
    assert status_res.status_code == 200
    optuna_cfg = status_res.json().get("config", {}).get("OPTUNA_CONFIG", {})
    assert optuna_cfg.get("wfa_window_count") == 8
    assert optuna_cfg.get("holdout_td") == 14

    # 3. /api/features/config 호출 시에도 8 유지 검증
    feat_res = client.post("/api/features/config", json={
        "enabled_features": [],
        "feature_params": {},
        "optuna_config": {
            "wfa_window_count": 8,
            "holdout_td": 14
        }
    })
    assert feat_res.status_code == 200

    # 4. 재조회 시 여전히 8 유지
    status_res2 = client.get("/api/status")
    assert status_res2.status_code == 200
    optuna_cfg2 = status_res2.json().get("config", {}).get("OPTUNA_CONFIG", {})
    assert optuna_cfg2.get("wfa_window_count") == 8
    assert optuna_cfg2.get("holdout_td") == 14



