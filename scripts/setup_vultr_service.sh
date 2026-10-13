#!/bin/bash
# ==============================================================================
# ⚙️ CTradeSystem Vultr 서버 초기 환경 설정 및 서비스 등록 스크립트
# ==============================================================================

# 색상 정의
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}=====================================================${NC}"
echo -e "${YELLOW} 🛠️ Vultr 서버 환경 구축 및 systemd 서비스 등록을 시작합니다.${NC}"
echo -e "${YELLOW}=====================================================${NC}"

# 스크립트가 위치한 곳 기준의 프로젝트 루트 확인
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJECT_ROOT"

# 1. 필수 리눅스 패키지 설치 (Ubuntu 기준)
echo -e "📦 1. 시스템 의존성 패키지 설치 (sudo 권한 필요)..."
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv python3-dev build-essential libatlas-base-dev gfortran rsync

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ [ERROR] 시스템 패키지 설치에 실패했습니다.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ 패키지 설치 완료.${NC}"

# 2. Python 가상환경 구성
echo -e "🐍 2. Python 3 가상환경 (.venv) 생성..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

# pip 업그레이드 및 컴파일에 필요한 빌드 도구(pybind11 포함) 명시적 설치
pip install --upgrade pip setuptools wheel pybind11
if [ -f "CTrade/requirements.txt" ]; then
    echo -e "📄 CTrade/requirements.txt 라이브러리 설치 중..."
    pip install -r CTrade/requirements.txt
fi

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ [ERROR] 파이썬 패키지 설치에 실패했습니다.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ 파이썬 가상환경 빌드 완료.${NC}"

# 3. C++ HFT 코어 모듈 빌드 (ctrade_core.so 컴파일)
echo -e "🛠️ 3. CTrade C++ 초저지연 HFT 코어 컴파일 실행..."
cd "$PROJECT_ROOT/CTrade"
SO_FILE=$(ls ctrade_core*.so 2>/dev/null | head -n 1)
if [ -n "$SO_FILE" ] && [ -z "$(find core/cpp setup.py -newer "$SO_FILE" -print -quit 2>/dev/null)" ]; then
    echo -e "${GREEN}✓ C++ 소스 변경 없음 - 기존 ${SO_FILE} 재사용 (컴파일 생략).${NC}"
elif [ -f "setup.py" ]; then
    python setup.py build_ext --inplace
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ C++ 코어 모듈 컴파일 성공 (ctrade_core.so 생성 완료).${NC}"
    else
        echo -e "${RED}❌ [ERROR] C++ 코어 모듈 컴파일에 실패했습니다.${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}⚠️ [WARNING] setup.py를 찾지 못해 C++ 컴파일을 건너뜁니다.${NC}"
fi
cd "$PROJECT_ROOT"

# 4. systemd 서비스 유닛 파일 등록
echo -e "⚙️ 4. systemd 서비스 유닛 등록 (ctrade.service)..."
SERVICE_FILE="/etc/systemd/system/ctrade.service"
CURRENT_USER=$(whoami)

sudo bash -c "cat > $SERVICE_FILE" <<EOF
[Unit]
Description=CTrade HFT Trading Engine Service
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_ROOT
Environment=PYTHONPATH=$PROJECT_ROOT:$PROJECT_ROOT/CTrade
ExecStart=$PROJECT_ROOT/.venv/bin/python $PROJECT_ROOT/CTrade/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ [ERROR] systemd 서비스 등록에 실패했습니다. sudo 권한을 확인하세요.${NC}"
    exit 1
fi

# systemd 데몬 리로드 및 활성화
sudo systemctl daemon-reload
sudo systemctl enable ctrade

echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN} 🎉 Vultr 서버 초기 환경 설정 완료!${NC}"
echo -e "${GREEN}=====================================================${NC}"
echo -e "💡 이제 다음 단계로 구동을 준비하십시오:"
echo -e "  1) ${YELLOW}CTrade/.env${NC} 파일을 생성하고 거래소 API Key를 기입하세요."
echo -e "  2) ${YELLOW}sudo systemctl start ctrade${NC} 명령으로 엔진을 기동하세요."
echo -e "  3) ${YELLOW}journalctl -u ctrade -f${NC} 명령으로 매매 상태 로그를 실시간 확인할 수 있습니다."
