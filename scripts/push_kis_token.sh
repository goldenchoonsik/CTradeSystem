#!/bin/bash
# ==============================================================================
# 📤 로컬 → Vultr 서버 KIS 인증 토큰(token_info.json) 동기화 스크립트
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

LOCAL_TOKEN_FILE="$PROJECT_ROOT/CTrade/config/token_info.json"
REMOTE_CONFIG_DIR="${REMOTE_PROJECT_DIR}/CTrade/config"
REMOTE_TOKEN_FILE="${REMOTE_CONFIG_DIR}/token_info.json"

echo -e "${YELLOW}=====================================================${NC}"
echo -e "${YELLOW} 📤 로컬 → Vultr KIS 토큰 내보내기 시작...${NC}"
echo -e "${YELLOW}=====================================================${NC}"

# 1. 로컬 토큰 파일 존재 여부 검사
if [ ! -f "$LOCAL_TOKEN_FILE" ]; then
    echo -e "${RED}❌ [ERROR] 로컬에 전송할 토큰 파일이 없습니다: CTrade/config/token_info.json${NC}"
    exit 1
fi

# 2. Vultr 서버 SSH 연결 검사
echo -e "🔌 Vultr 서버 (${VULTR_USER}@${VULTR_IP}) 연결 검사 중..."
ssh -o ConnectTimeout=5 "${VULTR_USER}@${VULTR_IP}" "echo 'OK'" > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ [ERROR] Vultr 서버 연결에 실패했습니다.${NC}"
    exit 1
fi

# 3. 원격 디렉토리 확인 및 생성
ssh "${VULTR_USER}@${VULTR_IP}" "mkdir -p ${REMOTE_CONFIG_DIR}"

# 4. 토큰 파일 업로드 전송
echo -e "📤 Vultr 서버로 token_info.json 업로드 중..."
rsync -avz "$LOCAL_TOKEN_FILE" "${VULTR_USER}@${VULTR_IP}:${REMOTE_TOKEN_FILE}"

if [ $? -eq 0 ]; then
    FILE_SIZE=$(wc -c < "$LOCAL_TOKEN_FILE" | tr -d ' ')
    MOD_TIME=$(stat -c '%y' "$LOCAL_TOKEN_FILE" 2>/dev/null || date -r "$LOCAL_TOKEN_FILE")
    echo -e "${GREEN}=====================================================${NC}"
    echo -e "${GREEN} ✓ KIS 토큰 Vultr 업로드 완료! (${FILE_SIZE} bytes)${NC}"
    echo -e "${GREEN} ✓ 원격 경로: ${REMOTE_TOKEN_FILE}${NC}"
    echo -e "${GREEN} ✓ 기준 수정시각: ${MOD_TIME}${NC}"
    echo -e "${GREEN}=====================================================${NC}"
else
    echo -e "${RED}❌ [ERROR] KIS 토큰 업로드 실패${NC}"
    exit 1
fi
