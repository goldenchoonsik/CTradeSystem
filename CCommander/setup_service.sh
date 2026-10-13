#!/bin/bash
# ==============================================================================
# 🛡️ CCommander Vultr 서버 systemd 서비스 등록 스크립트
# ==============================================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SERVICE_FILE="/etc/systemd/system/ccommander.service"
CURRENT_USER=$(whoami)

echo -e "${YELLOW}=====================================================${NC}"
echo -e "${YELLOW} 🛡️ CCommander systemd 서비스 등록을 시작합니다.${NC}"
echo -e "${YELLOW}=====================================================${NC}"

# 1. ccommander.service 유닛 파일 생성
sudo bash -c "cat > $SERVICE_FILE" <<EOF
[Unit]
Description=CCommander Telegram Remote Control & Infrastructure Daemon
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_ROOT/CCommander
Environment=PYTHONPATH=$PROJECT_ROOT:$PROJECT_ROOT/CCommander
ExecStart=$PROJECT_ROOT/.venv/bin/python $PROJECT_ROOT/CCommander/main.py
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ [ERROR] systemd 서비스 파일 생성 실패. sudo 권한을 확인하세요.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ ccommander.service 파일 생성 완료.${NC}"

# 2. systemd 데몬 리로드 및 활성화
sudo systemctl daemon-reload
sudo systemctl enable ccommander
sudo systemctl restart ccommander

sleep 1

# 3. 가동 상태 확인
IS_ACTIVE=$(systemctl is-active ccommander)
if [ "$IS_ACTIVE" == "active" ]; then
    echo -e "${GREEN}=====================================================${NC}"
    echo -e "${GREEN} 🎉 CCommander 서비스 등록 및 가동 성공!${NC}"
    echo -e "${GREEN}=====================================================${NC}"
    echo -e "💡 스마트폰 텔레그램 봇으로 기동 알림이 전송되었는지 확인하세요."
    echo -e "💡 로그 모니터링: journalctl -u ccommander -f -n 50"
else
    echo -e "${RED}❌ [ERROR] CCommander 서비스 실행 실패. 상태를 확인하세요.${NC}"
    journalctl -u ccommander -n 20 --no-pager
    exit 1
fi
