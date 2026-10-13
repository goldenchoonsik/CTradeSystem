#!/bin/bash
# ==============================================================================
# CTradeSystem 오프라인 교차 검증 실행 스크립트
# ==============================================================================
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_ROOT/CTrade"

echo "🔬 CTrade <-> CAiModelBaker 오프라인 교차 검증을 시작합니다..."
PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/CTrade:$PROJECT_ROOT/CAiModelBaker" \
  "$PROJECT_ROOT/.venv/bin/python3" "$PROJECT_ROOT/shared/tests/cross_validation.py"
