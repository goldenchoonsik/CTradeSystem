#!/bin/bash

# ==============================================================================
# CTradeSystem - direnv 자동 설치 및 통합 연동 스크립트
# ==============================================================================
set -e

PROJECT_ROOT="/home/kuzimo/projects/CTradeSystem"
COLOR_GREEN='\033[0;32m'
COLOR_BLUE='\033[0;34m'
COLOR_RESET='\033[0m'

echo -e "${COLOR_BLUE}🚀 [direnv 설정] 시스템 도구 설치를 시작합니다...${COLOR_RESET}"

# 1. 시스템에 direnv 설치 (Ubuntu/Debian 기준)
if ! command -v direnv &> /dev/null; then
    echo "  → direnv 설치 중 (sudo 권한 필요)..."
    sudo apt update && sudo apt install direnv -y
else
    echo "  → direnv가 이미 설치되어 있습니다."
fi

# 2. ~/.bashrc 에 셸 훅 등록
BASHRC="$HOME/.bashrc"
HOOK_LINE='eval "$(direnv hook bash)"'

if ! grep -Fxq "$HOOK_LINE" "$BASHRC"; then
    echo "  → ~/.bashrc 에 direnv 셸 등록 중..."
    echo "" >> "$BASHRC"
    echo "# CTradeSystem direnv integration" >> "$BASHRC"
    echo "$HOOK_LINE" >> "$BASHRC"
else
    echo "  → ~/.bashrc 에 이미 셸 등록이 완료되어 있습니다."
fi

# 3. CTrade 프로젝트 설정 (.envrc 생성)
echo -e "${COLOR_BLUE}⚙️ [CTrade] 가상환경 자동 연결 규칙 생성 중...${COLOR_RESET}"
CTRADE_DIR="$PROJECT_ROOT/CTrade"
mkdir -p "$CTRADE_DIR"
cat << 'EOF' > "$CTRADE_DIR/.envrc"
# CTrade 가상환경 및 환경 변수 설정
layout_python-venv .venv

# 실매매용 API KEY 셋업이 필요한 경우 아래 주석을 풀고 등록하세요.
# export UPBIT_ACCESS_KEY="자신의_액세스_키"
# export UPBIT_SECRET_KEY="자신의_시크릿_키"
# export EXCHANGE_RATE_MODE="USDT"
EOF

# 4. CAiModelBaker 프로젝트 설정 (.envrc 생성)
echo -e "${COLOR_BLUE}⚙️ [CAiModelBaker] 가상환경 자동 연결 규칙 생성 중...${COLOR_RESET}"
BAKER_DIR="$PROJECT_ROOT/CAiModelBaker"
mkdir -p "$BAKER_DIR"
cat << 'EOF' > "$BAKER_DIR/.envrc"
# CAiModelBaker 가상환경 및 설정
layout_python-venv .venv
EOF

# 5. 생성한 설정 허용 (allow) 처리
echo -e "${COLOR_BLUE}🔑 보안 권한 승인 (direnv allow) 처리 중...${COLOR_RESET}"
cd "$CTRADE_DIR" && direnv allow || true
cd "$BAKER_DIR" && direnv allow || true

echo -e "${COLOR_GREEN}======================================================================${COLOR_RESET}"
echo -e "${COLOR_GREEN} 🎉 direnv 자동 연동 및 설정이 완료되었습니다!${COLOR_RESET}"
echo -e "  현재 실행 중인 터미널 창을 닫고, '새 터미널'을 열어서 아래 명령어를 쳐보세요."
echo -e "  cd /home/kuzimo/projects/CTradeSystem/CTrade"
echo -e "${COLOR_GREEN}======================================================================${COLOR_RESET}"
