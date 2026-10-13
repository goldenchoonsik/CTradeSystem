#!/bin/bash
# ==============================================================================
# CAiModelBaker 학습 대시보드 서버 실행 스크립트
# ==============================================================================
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_ROOT/CAiModelBaker"

echo "🍞 CAiModelBaker 학습 대시보드 서버를 기동합니다..."
PYTHONPATH="$PROJECT_ROOT" "$PROJECT_ROOT/.venv/bin/python3" web_dashboard/main.py
