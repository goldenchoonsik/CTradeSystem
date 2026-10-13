#!/bin/bash
# CAiModelBaker - Web Dashboard 실행 스크립트
cd "$(dirname "$0")"

if [ -f ".venv/bin/python3" ]; then
    echo "============================================="
    echo "🟢 Web Dashboard 실행 중..."
    echo "🌐 http://localhost:8000"
    echo "============================================="
    .venv/bin/python3 web_dashboard/main.py
else
    echo "❌ 가상환경(.venv)이 존재하지 않습니다."
    exit 1
fi