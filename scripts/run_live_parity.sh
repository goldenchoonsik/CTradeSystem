#!/bin/bash
# ==============================================================================
# CTradeSystem 실시간 정합성 교차 검증 실행 스크립트
# ==============================================================================
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_ROOT"

echo "🔬 CTrade <-> Baker 실시간 데이터 정합성 검증을 시작합니다..."

# PYTHONPATH 환경 변수를 지정하여 파이썬이 shared 및 CTrade 내 모듈들을 정상 탐색하도록 합니다.
# CTrade 작동 중 축적된 live_raw_candles.csv, live_feature_dump.csv, live_trades.csv 데이터를 
# Baker 백테스트 엔진 시뮬레이션 결과와 1:1 대조하여 피처 연산 및 주문 정합성을 검증합니다.
# 인자로 특정 로그 경로가 전달되면 이를 사용하고, 없으면 최신 vultr_logs 또는 로컬 기본 경로 자동 탐색
if [ -n "$1" ]; then
    TARGET_LOG_DIR="$1"
else
    # 1. vultr_logs 에서 가장 최신 날짜의 data_logs 디렉토리 탐색
    LATEST_VULTR_DATA_LOGS=$(find "$PROJECT_ROOT/vultr_logs" -mindepth 2 -maxdepth 2 -type d -name "data_logs" 2>/dev/null | sort -r | head -n 1)
    if [ -n "$LATEST_VULTR_DATA_LOGS" ] && [ -d "$LATEST_VULTR_DATA_LOGS" ]; then
        TARGET_LOG_DIR="$LATEST_VULTR_DATA_LOGS"
        echo "📂 최신 Vultr 수집 로그 디렉토리를 자동 감지했습니다: $TARGET_LOG_DIR"
    else
        TARGET_LOG_DIR="$PROJECT_ROOT/CTrade/data/logs"
        echo "📂 로컬 기본 로그 디렉토리를 사용합니다: $TARGET_LOG_DIR"
    fi
fi

# 가상환경 파이썬 경로 자동 감지 (.venv, CAiModelBaker/.venv, CTrade/.venv, 또는 시스템 python3)
if [ -f "$PROJECT_ROOT/.venv/bin/python3" ]; then
    PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python3"
elif [ -f "$PROJECT_ROOT/CAiModelBaker/.venv/bin/python3" ]; then
    PYTHON_BIN="$PROJECT_ROOT/CAiModelBaker/.venv/bin/python3"
elif [ -f "$PROJECT_ROOT/CTrade/.venv/bin/python3" ]; then
    PYTHON_BIN="$PROJECT_ROOT/CTrade/.venv/bin/python3"
else
    PYTHON_BIN="python3"
fi

PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/CTrade:$PROJECT_ROOT/CAiModelBaker" \
  "$PYTHON_BIN" -u shared/tests/live_parity_check.py --log-dir "$TARGET_LOG_DIR"
