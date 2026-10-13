#!/bin/bash
# ==============================================================================
# 📥 Vultr 실전 서버 전용 로그 및 체결 이력 격리 수집 스크립트
# ==============================================================================

VULTR_USER="root"
VULTR_IP="158.247.252.206"
REMOTE_PROJECT_DIR="/root/projects/CTradeSystem"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_ROOT"

# 오늘 날짜 타임스탬프 (예: 20260721)
TODAY=$(date +"%Y%m%d")
SAVE_DIR="$PROJECT_ROOT/vultr_logs/$TODAY"

echo -e "${YELLOW}=====================================================${NC}"
echo -e "${YELLOW} 📥 Vultr 서버 로그 및 체결 내역 격리 수집 시작...${NC}"
echo -e "${YELLOW}=====================================================${NC}"

# 1. 격리 저장 폴더 생성
mkdir -p "$SAVE_DIR"

# 2. Vultr 서버 로그 및 이력 파일 원격 가져오기 (로컬 데이터 덮어쓰기 방지)
echo -e "🔌 Vultr 서버 (${VULTR_USER}@${VULTR_IP})에서 파일 동기화 다운로드 중..."

# trade_debug.log 가져오기
rsync -avz "${VULTR_USER}@${VULTR_IP}:${REMOTE_PROJECT_DIR}/trade_debug.log" "$SAVE_DIR/trade_debug.log" 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Vultr 디버그 로그 백업 완료: vultr_logs/$TODAY/trade_debug.log${NC}"
else
    echo -e "${RED}⚠️ Vultr 디버그 로그 수집 실패 (파일 없음 혹은 경로 확인 필요)${NC}"
fi

# trade_history.json 가져오기
rsync -avz "${VULTR_USER}@${VULTR_IP}:${REMOTE_PROJECT_DIR}/trade_history.json" "$SAVE_DIR/trade_history.json" 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Vultr 실전 체결 이력 백업 완료: vultr_logs/$TODAY/trade_history.json${NC}"
else
    echo -e "${RED}⚠️ Vultr 체결 이력 수집 실패 (파일 없음 혹은 경로 확인 필요)${NC}"
fi

# CTrade/data/logs/ (실시간 정합성 패리티 덤프 파일들) 가져오기
rsync -avz "${VULTR_USER}@${VULTR_IP}:${REMOTE_PROJECT_DIR}/CTrade/data/logs/" "$SAVE_DIR/data_logs/" 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Vultr 실시간 정합성 패리티 덤프 수집 완료: vultr_logs/$TODAY/data_logs/${NC}"
fi

echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN} 🎉 수집 완수! 로컬 데이터와 격리되어 vultr_logs/$TODAY 에 저장되었습니다.${NC}"
echo -e "${GREEN}=====================================================${NC}"
