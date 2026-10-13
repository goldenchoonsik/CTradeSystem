#!/bin/bash
# ==============================================================================
# CTrade HFT 실전 매매 엔진 실행 스크립트
# ==============================================================================
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_ROOT/CTrade"

echo "🚀 CTrade HFT 실전 매매 엔진을 기동합니다..."
PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/CTrade:$PROJECT_ROOT/CAiModelBaker" \
  "$PROJECT_ROOT/.venv/bin/python3" main.py
