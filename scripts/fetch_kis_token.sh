#!/bin/bash
# ==============================================================================
# 📥 Vultr 서버 → 로컬 KIS 인증 토큰(token_info.json) 동기화 스크립트
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

LOCAL_CONFIG_DIR="$PROJECT_ROOT/CTrade/config"
LOCAL_TOKEN_FILE="$LOCAL_CONFIG_DIR/token_info.json"
REMOTE_TOKEN_FILE="${REMOTE_PROJECT_DIR}/CTrade/config/token_info.json"

echo -e "${YELLOW}=====================================================${NC}"
echo -e "${YELLOW} 📥 Vultr → 로컬 KIS 토큰 가져오기 시작...${NC}"
echo -e "${YELLOW}=====================================================${NC}"

# 1. 로컬 설정 디렉토리 생성
mkdir -p "$LOCAL_CONFIG_DIR"

# 2. Vultr 서버 SSH 연결 검사
echo -e "🔌 Vultr 서버 (${VULTR_USER}@${VULTR_IP}) 연결 검사 중..."
ssh -o ConnectTimeout=5 "${VULTR_USER}@${VULTR_IP}" "echo 'OK'" > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ [ERROR] Vultr 서버 연결에 실패했습니다.${NC}"
    exit 1
fi

# 3. 토큰 파일 가져오기
echo -e "📥 Vultr 서버에서 token_info.json 다운로드 중..."
rsync -avz "${VULTR_USER}@${VULTR_IP}:${REMOTE_TOKEN_FILE}" "$LOCAL_TOKEN_FILE"

if [ $? -eq 0 ] && [ -f "$LOCAL_TOKEN_FILE" ]; then
    FILE_SIZE=$(wc -c < "$LOCAL_TOKEN_FILE" | tr -d ' ')
    MOD_TIME=$(stat -c '%y' "$LOCAL_TOKEN_FILE" 2>/dev/null || date -r "$LOCAL_TOKEN_FILE")
    echo -e "${GREEN}=====================================================${NC}"
    echo -e "${GREEN} ✓ KIS 토큰 가져오기 완료! (${FILE_SIZE} bytes)${NC}"
    echo -e "${GREEN} ✓ 로컬 파일: CTrade/config/token_info.json${NC}"
    echo -e "${GREEN} ✓ 수정 시각: ${MOD_TIME}${NC}"
    echo -e "${GREEN}=====================================================${NC}"
else
    echo -e "${RED}❌ [ERROR] KIS 토큰 가져오기 실패 (원격지에 파일이 없거나 전송 실패)${NC}"
    exit 1
fi
