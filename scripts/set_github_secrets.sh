#!/usr/bin/env bash
# GitHub Actions 배포 Secrets 일괄 등록 — ~/.bashrc 환경변수 값을 화면에 찍지 않고 gh 로 바로 등록.
#
#   준비(처음 한 번): sudo apt install -y gh && gh auth login   (GitHub.com → HTTPS → 브라우저 로그인, goldenchoonsik)
#   실행:            bash -i scripts/set_github_secrets.sh
#
# KIS_ACC_CODE·KIS_URL_BASE 는 deploy.yml 에 기본값이 있어 환경변수가 있을 때만 등록한다.
set -euo pipefail

REPO="goldenchoonsik/CTradeSystem"
command -v gh >/dev/null || { echo "gh 없음 — sudo apt install -y gh && gh auth login 먼저"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh 로그인 필요 — gh auth login"; exit 1; }

put() {  # put 이름 값
    if [ -z "$2" ]; then echo "  건너뜀  $1 (값 없음)"; return; fi
    printf '%s' "$2" | gh secret set "$1" --repo "$REPO" --body - >/dev/null && echo "  등록    $1"
}

echo "[$REPO] Secrets 등록"
for k in UPBIT_ACCESS_KEY UPBIT_SECRET_KEY KIS_APP_KEY KIS_APP_SECRET KIS_ACC_NO TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID \
         KIS_ACC_CODE KIS_URL_BASE; do
    put "$k" "${!k:-}"
done

read -r -p "Vultr 서버 IP: " ip
put VULTR_IP "$ip"

read -r -p "Vultr SSH 개인키 파일 [~/.ssh/id_rsa]: " key
key="${key:-$HOME/.ssh/id_rsa}"
key="${key/#\~/$HOME}"
if [ -f "$key" ]; then put VULTR_SSH_KEY "$(cat "$key")"; else echo "  건너뜀  VULTR_SSH_KEY (파일 없음: $key)"; fi

echo; gh secret list --repo "$REPO"
