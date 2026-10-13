import sys
import os
import json
from unittest.mock import MagicMock

# Tkinter UI 팝업 에러 방지를 위해 모킹 수행
import tkinter as tk
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()

# 프로젝트 루트 경로 설정 (scripts/ 상위 = CAiModelBaker 루트)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
_CTRADE_SYSTEM_ROOT = os.path.dirname(_PROJECT_ROOT)
if _CTRADE_SYSTEM_ROOT not in sys.path:
    sys.path.insert(0, _CTRADE_SYSTEM_ROOT)

# 실제 모듈 임포트
from core.model_engine import process_02_relay_optimization
from core.backtester import process_03_pure_backtest
from shared.config.config_manager import load_project_config

# config_manager 기반 설정 로드
config = load_project_config("CAiModelBaker")

# 로그 기록용 파일 핸들러
log_file_path = "quant_engine_log.txt"
if os.path.exists(log_file_path):
    os.remove(log_file_path)

import argparse

parser = argparse.ArgumentParser(description="CAiModelBaker CLI Pipeline Runner")
parser.add_argument("--feature-only", action="store_true", help="학습 진행 이전에 지표 선정 및 파라미터 사전 최적화만 단독 수행하고 종료합니다.")
args, unknown = parser.parse_known_args()

def log_fn(message):
    print(message)
    with open(log_file_path, "a", encoding="utf-8") as f:
        f.write(message + "\n")

if args.feature_only or "--feature-only" in sys.argv:
    print(">>> [지표 최적화] 지표 선정 및 파라미터 사전 최적화만 수행합니다...")
    from core.model_engine import run_standalone_feature_optimization
    run_standalone_feature_optimization(config, log_fn=log_fn)
    print(">>> [SUCCESS] 지표 선별 및 파라미터 최적화 완료 (열린 프로젝트의 config_snapshot.json 에 저장되었습니다).")
    sys.exit(0)

# 1. 2단계 최적화 가동
t_start = config.get("TRAIN_START", "")
t_end = config.get("TRAIN_END", "")
o_start = config.get("OPT_START", "")
o_end = config.get("OPT_END", "")

print(">>> [1/2] 3구간 최적화(Optuna) 프로세스 가동 시작...")
process_02_relay_optimization(config, t_start, t_end, o_start, o_end, log_fn=log_fn)

# 2. 3단계 지정 구간 백테스트 가동
test_start = config.get("TEST_START", "")
test_end = config.get("TEST_END", "")

print(">>> [2/2] 지정 구간 백테스트(Test Period) 프로세스 가동 시작...")
process_03_pure_backtest(config, test_start, test_end, log_fn=log_fn)

print(">>> [SUCCESS] 전체 파이프라인 수행 및 로그 작성 완료.")
