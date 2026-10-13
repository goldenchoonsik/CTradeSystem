#!/bin/bash
set -e

echo "=== WSL2 Chrome & Dependencies Installer (Noble Numbat Edition) ==="
sudo apt-get update

# 1. 필수 라이브러리 설치 (헤드리스/GUI 동작용) - 24.04(Noble) 버전에 맞게 패키지 이름 수정
echo "Installing base libraries..."
sudo apt-get install -y wget curl unzip xvfb libxi6 libnss3 libxss1 libasound2t64 libatk-bridge2.0-0t64 libgtk-3-0t64 libgbm1

# 2. 공식 Google Chrome stable 패키지 설치
echo "Downloading Google Chrome..."
wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/google-chrome-stable_current_amd64.deb

echo "Installing Google Chrome..."
sudo apt-get install -y /tmp/google-chrome-stable_current_amd64.deb

# Cleanup
rm -f /tmp/google-chrome-stable_current_amd64.deb

echo "=== Chrome installation complete! ==="
google-chrome --version
