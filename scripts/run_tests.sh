#!/bin/bash
# ==============================================================================
# CTradeSystem 통합 테스트 실행 스크립트 (Pytest)
# ==============================================================================
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_ROOT"

if [ ! -d "$PROJECT_ROOT/.venv" ]; then
    echo "❌ 루트 가상환경(.venv)이 존재하지 않습니다. 생성을 먼저 진행해주세요."
    exit 1
fi

PY="$PROJECT_ROOT/.venv/bin/pytest"
RC=0

# L-8: CTrade/CAiModelBaker 는 동일 패키지명 'core' 를 사용하므로 반드시 분리 실행한다.
echo "🧪 [1/2] CTrade + shared + CCommander + root tests 실행..."
"$PY" -v CTrade/tests shared/tests CCommander/tests tests "$@" || RC=1

echo ""
echo "🧪 [2/2] CAiModelBaker 테스트 실행 (core 패키지명 충돌 방지를 위해 분리 실행)..."
"$PY" -v CAiModelBaker/tests "$@" || RC=1

echo ""
if [ "$RC" -eq 0 ]; then
    echo "✅ 전체 테스트 통과"
else
    echo "❌ 일부 테스트 실패"
fi
exit "$RC"
