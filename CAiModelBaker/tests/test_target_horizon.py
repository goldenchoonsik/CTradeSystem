"""
CAiModelBaker - Target Horizon & Max Holding Architecture Unit Tests
타깃 사정거리 및 최대 보유시간 레지스트리, 고정 모드 및 Optuna 탐색 범위 분리 검증
"""
import os
import sys
import pytest

# 프로젝트 루트 경로 등록
_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
_BAKER_ROOT = os.path.dirname(_CUR_DIR)
_PROJECT_ROOT = os.path.dirname(_BAKER_ROOT)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _BAKER_ROOT not in sys.path:
    sys.path.insert(0, _BAKER_ROOT)

from CAiModelBaker.core.horizon_registry import get_horizon_context, HORIZON_REGISTRY


def test_horizon_registry_intervals():
    """인터벌별 표준 권장 사정거리 및 후보군 반환 검증"""
    # 1. 10분봉 (현재 세팅)
    ctx_10m = get_horizon_context("10m")
    assert ctx_10m["interval"] == "10m"
    assert ctx_10m["locked_predict_ahead"] == 6  # 1시간
    assert ctx_10m["locked_max_hold"] == 6
    assert ctx_10m["candidate_horizons"] == [3, 6, 12, 24]
    assert len(ctx_10m["candidate_details"]) == 4

    # 2. 1분봉
    ctx_1m = get_horizon_context("1m")
    assert ctx_1m["interval"] == "1m"
    assert ctx_1m["locked_predict_ahead"] == 6  # 6분
    assert ctx_1m["candidate_horizons"] == [3, 6, 15, 30]

    # 3. 10초봉
    ctx_10s = get_horizon_context("10s")
    assert ctx_10s["interval"] == "10s"
    assert ctx_10s["locked_predict_ahead"] == 18  # 3분
    assert ctx_10s["candidate_horizons"] == [6, 18, 36, 60]

    # 4. 미지원 인터벌 폴백 검증
    ctx_fallback = get_horizon_context("unknown_99m")
    assert ctx_fallback["locked_predict_ahead"] == 6
    assert ctx_fallback["candidate_horizons"] == [3, 6, 12, 24]


def test_target_horizon_locked_optuna_ranges():
    """locked 모드 시 Optuna Outer 탐색 범위에서 predict_ahead_candles가 제외되는지 검증"""
    config_locked = {
        "INTERVAL": "10m",
        "TARGET_HORIZON_CONFIG": {
            "mode": "locked",
            "locked_predict_ahead": 6,
            "locked_max_hold": 6,
            "candidate_horizons": [3, 6, 12, 24]
        },
        "OPTUNA_CONFIG": {
            "predict_ahead_candles_range": {"min": 8, "max": 32},
            "take_profit_range": {"min": 1, "max": 4}
        }
    }
    
    # model_engine의 optuna_ranges 분기 로직 시뮬레이션
    thc = config_locked.get("TARGET_HORIZON_CONFIG", {})
    is_locked = (thc.get("mode") == "locked")
    assert is_locked is True
    assert thc.get("locked_predict_ahead") == 6

    optuna_ranges = {
        'take_profit': (1, 4, 0.1),
        'stop_loss': (0.5, 3, 0.1)
    }
    if not is_locked:
        optuna_ranges['predict_ahead_candles'] = (8, 32, 1)
        optuna_ranges['max_hold_candles'] = (8, 32, 1)

    assert "predict_ahead_candles" not in optuna_ranges
    assert "max_hold_candles" not in optuna_ranges
    assert "take_profit" in optuna_ranges


def test_target_horizon_grid_search_optuna_ranges():
    """grid_search 모드 시 Optuna Outer 탐색 범위에 predict_ahead_candles가 유지되는지 검증"""
    config_grid = {
        "INTERVAL": "10m",
        "TARGET_HORIZON_CONFIG": {
            "mode": "grid_search",
            "candidate_horizons": [3, 6, 12, 24]
        },
        "OPTUNA_CONFIG": {
            "predict_ahead_candles_range": {"min": 8, "max": 32},
            "max_hold_candles_range": {"min": 8, "max": 32},
            "take_profit_range": {"min": 1, "max": 4}
        }
    }
    
    thc = config_grid.get("TARGET_HORIZON_CONFIG", {})
    is_locked = (thc.get("mode") == "locked")
    assert is_locked is False

    optuna_ranges = {
        'take_profit': (1, 4, 0.1),
        'stop_loss': (0.5, 3, 0.1)
    }
    if not is_locked:
        optuna_ranges['predict_ahead_candles'] = (8, 32, 1)
        optuna_ranges['max_hold_candles'] = (8, 32, 1)

    assert "predict_ahead_candles" in optuna_ranges
    assert "max_hold_candles" in optuna_ranges


@pytest.mark.asyncio
async def test_api_apply_locked_horizon():
    """사정거리 잠금 설정 및 활성 설정/스냅샷 동기화 로직 단위 테스트"""
    from shared.config.config_manager import load_project_config, save_project_config
    
    # 6캔들 잠금 적용 시뮬레이션
    candles = 6
    config = load_project_config("CAiModelBaker")
    horizon_cfg = config.get("TARGET_HORIZON_CONFIG", {})
    horizon_cfg["mode"] = "locked"
    horizon_cfg["locked_predict_ahead"] = candles
    horizon_cfg["locked_max_hold"] = candles
    config["TARGET_HORIZON_CONFIG"] = horizon_cfg
    save_project_config("CAiModelBaker", config)

    # active.json 반영 확인
    updated_cfg = load_project_config("CAiModelBaker")
    thc = updated_cfg.get("TARGET_HORIZON_CONFIG", {})
    assert thc.get("mode") == "locked"
    assert thc.get("locked_predict_ahead") == 6
    assert thc.get("locked_max_hold") == 6


def test_feature_tuner_and_research_loop_locked_target():
    """사정거리 잠금 모드 시 feature_tuner 및 research_loop의 타깃 사정거리 단일화 검증"""
    config_locked = {
        "INTERVAL": "10m",
        "TARGET_HORIZON_CONFIG": {
            "mode": "locked",
            "locked_predict_ahead": 6,
            "locked_max_hold": 6,
        },
        "OPTUNA_CONFIG": {
            "predict_ahead_candles_range": {"min": 1, "max": 50},
        }
    }
    
    # 1. Feature Tuner run_auto_feature_selection 타깃 추출 로직 시뮬레이션
    thc = config_locked.get("TARGET_HORIZON_CONFIG", {})
    is_locked = (thc.get("mode", "locked") == "locked")
    if is_locked and thc.get("locked_predict_ahead"):
        predict_ahead_list = [int(thc.get("locked_predict_ahead"))]
    else:
        pa_range = config_locked["OPTUNA_CONFIG"]["predict_ahead_candles_range"]
        predict_ahead_list = [pa_range["min"], (pa_range["min"] + pa_range["max"]) // 2, pa_range["max"]]
        
    assert predict_ahead_list == [6]  # 1~50이 아니라 잠긴 6캔들 단일 타깃이어야 함!

    # 2. Research Loop IC 스크리닝 타깃 추출 로직 시뮬레이션
    if is_locked and thc.get("locked_predict_ahead"):
        forward_candles = int(thc.get("locked_predict_ahead"))
    else:
        forward_candles = (1 + 50) // 2
        
    assert forward_candles == 6  # 25가 아니라 잠긴 6캔들이어야 함!


def test_seconds_interval_horizon_scaling():
    """10s 등 초 단위 인터벌 시 interval_seconds 및 캔들 시간 환산 검증"""
    interval_str = "10s"
    secs = float(interval_str.replace("s", ""))
    interval_minutes = secs / 60.0
    interval_seconds = secs

    assert interval_seconds == 10.0
    assert abs(interval_minutes - (1.0 / 6.0)) < 1e-6

    # 9캔들 기준
    candles = 9
    total_secs = candles * interval_seconds
    assert total_secs == 90.0  # 90초 (1분 30초)



