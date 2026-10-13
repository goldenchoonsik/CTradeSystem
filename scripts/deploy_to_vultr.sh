#!/bin/bash
# ==============================================================================
# 🚀 CTradeSystem Vultr 원클릭 배포 스크립트 (로컬 실행용)
# ==============================================================================

# 설정값 정의 (필요에 따라 서버 환경 정보 수정 가능)
VULTR_USER="root"
VULTR_IP="${VULTR_IP:-}"  # 환경변수 VULTR_IP 로 서버 IP 를 지정하세요. (예: VULTR_IP=1.2.3.4 ./scripts/deploy_to_vultr.sh)
TARGET_DIR="/root/projects/CTradeSystem"

# 색상 정의
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 스크립트 위치 기준 프로젝트 루트 획득
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_ROOT"

echo -e "${YELLOW}=====================================================${NC}"
echo -e "${YELLOW} 🔄 CTradeSystem Vultr 배포 동기화를 시작합니다...${NC}"
echo -e "${YELLOW}=====================================================${NC}"

# IP 주소 입력 확인
if [ -z "$VULTR_IP" ] || [ "$VULTR_IP" == "YOUR_VULTR_IP_HERE" ]; then
    echo -e "${RED}❌ [ERROR] 환경변수 VULTR_IP 가 설정되지 않았습니다. (예: VULTR_IP=1.2.3.4 $0)${NC}"
    exit 1
fi

# 1. 원격 서버 접속 검증
echo -e "🔌 Vultr 서버 (${VULTR_USER}@${VULTR_IP}) 연결 검사 중..."
ssh -o ConnectTimeout=5 "${VULTR_USER}@${VULTR_IP}" "echo 'OK'" > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ [ERROR] SSH 연결에 실패했습니다. IP 설정 및 SSH Key 등록을 확인하세요.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ 원격 서버 연결 검증 완료.${NC}"

# 2. rsync 동기화 실행
# 원격지에 타겟 디렉토리가 없을 수 있으므로 사전 생성
echo -e "📂 원격지 디렉토리 생성 중 (${TARGET_DIR})..."
ssh "${VULTR_USER}@${VULTR_IP}" "mkdir -p ${TARGET_DIR}"

# CTrade 및 shared 폴더만 전송하며, 대용량 데이터 및 가상환경, 키파일 등은 제외
echo -e "📤 파일 동기화 전송 중..."
rsync -avz --delete \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='.cline/' \
    --exclude='.clineignore' \
    --exclude='.gitignore' \
    --exclude='CAiModelBaker/' \
    --exclude='DataVault/' \
    --exclude='vultr_logs/' \
    --exclude='CTrade/data/logs/' \
    --exclude='CTrade/data/*.csv' \
    --exclude='CTrade/data/*.json' \
    --exclude='CTrade/*.so' \
    --exclude='CTrade/build/' \
    --exclude='CTrade/scratch/' \
    --exclude='shared/tests/' \
    --exclude='shared/__pycache__/' \
    --exclude='.env' \
    --exclude='CTrade/.env' \
    --exclude='CTrade/config/token_info.json' \
    --exclude='config/token_info.json' \
    --exclude='CTrade/trade_history.json' \
    --exclude='trade_history.json' \
    --exclude='account_baseline.json' \
    --exclude='CTrade/logs/' \
    --exclude='CTrade/*.log' \
    --exclude='*.log' \
    --exclude='CTrade/config/timer_state.json' \
    --exclude='CTrade/config/optimization_history.json' \
    --exclude='CTrade/spike_verification_history.json' \
    ./ "${VULTR_USER}@${VULTR_IP}:${TARGET_DIR}/"

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ [ERROR] 파일 동기화 중 오류가 발생했습니다.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ 파일 동기화 성공.${NC}"

# 3. version.json 원격 생성
GIT_TAG=$(git describe --tags 2>/dev/null || echo "v1.4.2")
COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "local")
COMMIT_MSG=$(git log -1 --pretty=%B 2>/dev/null | head -n 1 | sed 's/"/\\"/g' || echo "Direct Deploy")
DEPLOY_TIME=$(date +'%Y-%m-%d %H:%M:%S KST')
ssh "${VULTR_USER}@${VULTR_IP}" "cat << 'EOF' > ${TARGET_DIR}/CTrade/version.json
{
  \"version\": \"${GIT_TAG}\",
  \"commit\": \"${COMMIT_HASH}\",
  \"message\": \"${COMMIT_MSG}\",
  \"deployed_at\": \"${DEPLOY_TIME}\"
}
EOF
cp ${TARGET_DIR}/CTrade/version.json ${TARGET_DIR}/version.json"

# 4. C++ 코어 모듈 원격 컴파일 (ctrade_core.so 갱신)
echo -e "⚙️ Vultr 서버 C++ ctrade_core 모듈 컴파일 확인 중..."
# 로컬에 .so가 있으면 전송하고 없으면 원격 빌드 시도
if [ -f "CTrade/ctrade_core.cpython-312-x86_64-linux-gnu.so" ]; then
    echo -e "📦 로컬에서 빌드된 ctrade_core.so 모듈을 원격으로 복사합니다..."
    scp "CTrade/ctrade_core.cpython-312-x86_64-linux-gnu.so" "${VULTR_USER}@${VULTR_IP}:${TARGET_DIR}/CTrade/"
    echo -e "${GREEN}✓ C++ 코어 모듈 전송 완료.${NC}"
else
    echo -e "⚙️ Vultr 서버에서 C++ 컴파일 빌드 실행..."
    ssh "${VULTR_USER}@${VULTR_IP}" "cd ${TARGET_DIR}/CTrade && ${TARGET_DIR}/.venv/bin/python setup.py build_ext --inplace"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ C++ 코어 모듈 컴파일 완료.${NC}"
    else
        echo -e "${RED}⚠️ C++ 모듈 컴파일 중 오류가 발생했습니다.${NC}"
    fi
fi

# 5. CCommander 서비스 셋업 및 서비스 재기동
echo -e "🔄 Vultr 서버의 ctrade 매매 엔진 및 ccommander 관제 데몬 재시작..."
ssh "${VULTR_USER}@${VULTR_IP}" "chmod +x ${TARGET_DIR}/scripts/*.sh ${TARGET_DIR}/CCommander/*.sh && ${TARGET_DIR}/CCommander/setup_service.sh && sudo systemctl restart ctrade ccommander"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}=====================================================${NC}"
    echo -e "${GREEN} 🎉 CTradeSystem Vultr 배포 및 서비스 재시작 완료!${NC}"
    echo -e "${GREEN}=====================================================${NC}"
    echo -e "💡 로그 모니터링: ssh ${VULTR_USER}@${VULTR_IP} 'journalctl -u ctrade -f -n 50'"
else
    echo -e "${RED}⚠️ [WARNING] 파일은 전송되었으나, ctrade 서비스 재시작에 실패했습니다.${NC}"
    echo -e "💡 최초 배포라면 Vultr 서버 내부에서 scripts/setup_vultr_service.sh를 구동해 주세요."
fi
