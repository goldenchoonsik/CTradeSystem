"""
CAiModelBaker - Feature Registry (재수출 래퍼)

실제 구현은 shared/feature_registry.py에 있습니다.
이 파일은 기존 import 호환성을 유지하기 위한 재수출(re-export) 래퍼입니다.
"""
import os
import sys

# shared/ 모듈이 import 가능하도록 CTradeSystem 루트를 sys.path에 추가
_CTRADE_SYSTEM_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CTRADE_SYSTEM_ROOT not in sys.path:
    sys.path.insert(0, _CTRADE_SYSTEM_ROOT)

# ⚠️ 여기서 set_config_root(<Baker 경로>) 를 부르지 않는다 (2026-10-01 제거).
# 부르면 설정 탐색 기준이 CAiModelBaker/ 로 바뀌어 열린 프로젝트 포인터(CAiModelBaker/config/active_project.json)를
# `CAiModelBaker/CAiModelBaker/config` 에서 찾다가 못 찾고, 전역 active.json 의 지표 설정으로 떨어진다.
# 이 모듈을 import 하는 연구 루프·대시보드 전체가 열린 프로젝트가 아니라 전역 설정으로 학습했다.
# 기본 탐색 기준(레포 루트)만으로 프로젝트 파일 → CAiModelBaker/config/active.json 순서가 올바르게 잡힌다.

# shared/feature_registry의 모든 심볼 재수출
from shared.features.feature_registry import *