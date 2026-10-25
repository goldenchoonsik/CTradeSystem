#!/bin/bash
# 외국인 자금 일일 모니터 실행 — 서버 .env 의 KIS 키를 stdin 으로 전달(저장하지 않음). 자동 스케줄 등록은 사용자 결정 후.
cd "$(dirname "$0")/../../.." || exit 1
ssh -o ConnectTimeout=8 root@158.247.252.206 "grep -E '^(KIS_APP_KEY|KIS_APP_SECRET)=' /root/projects/CTradeSystem/.env" 2>/dev/null | .venv/bin/python docs/research/20261024_foreign_accum/daily_monitor.py
