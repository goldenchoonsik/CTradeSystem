# Operations (배포·인프라·원격 관제)

## 1. 배포 흐름

로컬 학습 → 합격 모델을 `shared/deploy/{symbol}_{intv}/{session}/`로 승격(배포 게이트) → `scripts/deploy_to_vultr.sh`(rsync, CTrade + shared만) → VPS에서 `systemctl restart ctrade`.

rsync 제외(원격 상태 보존): `CAiModelBaker/` 소스, 학습 데이터, `.env`, `CTrade/config/trading_config.json`, `trade_history.json`, `token_info.json`, `CTrade/data/`, `*.log`, `account_baseline.json`.

## 2. VPS 구성

- systemd: `ctrade.service`, `ccommander.service`(`Restart=always`).
- 최초 설정 `scripts/setup_vultr_service.sh`: 패키지·루트 `.venv`·C++ 모듈(`ctrade_core.so`) 빌드. CCommander는 `CCommander/setup_service.sh`.
- KIS 토큰: `scripts/fetch_kis_token.sh`, `push_kis_token.sh`. 로그 수거 `fetch_vultr_logs.sh`.

## 3. CI/CD (`.github/workflows/deploy.yml`)

모든 푸시: pybind11로 `ctrade_core.so` 빌드 → `pytest`. `main`/`v*`/수동 실행: 테스트 통과 시 Vultr 배포. Secrets(`VULTR_IP`, `VULTR_SSH_KEY`, `UPBIT_ACCESS_KEY`, `KIS_APP_KEY` …)로 원격 `.env`·`version.json` 갱신 후 서비스 재기동.

## 4. CCommander (`CCommander/`)

텔레그램으로 VPS·CTrade 원격 제어. CTrade는 알림 발송(`sendMessage`)만, CCommander는 명령 수신(`getUpdates`)만 — 같은 봇 토큰의 409 충돌 방지. Chat ID 화이트리스트.

명령: `/status`(CPU/RAM/디스크·서비스·성과·토큰), `/balance`, `/trades [n]`, `/pnl`, `/setbase [ALL|UPBIT|KIS]`(별칭 `/resetbase`), `/auto [종목] [on|off]`, `/pause`·`/resume`(= `/trade off|on`), `/close [종목]`, `/closeall`(`/liquidate`), `/start|/stop|/restart [service]`, `/logs [n]`, `/token`, `/security`, `/unblock [IP]`, `/reboot`(15초 이중 확인), `/help`.

성과 지표: 순실현손익(왕복 수수료 차감), 순수익률, 회전 누적 수익률(Σ건별), 계좌 수익률(초기 자본 대비), Profit Factor.

## 5. 대시보드 보안 (`CTrade/ui/auth_manager.py`)

외부 접속 시 텔레그램 6자리 OTP(3분) → 24시간 HMAC 서명 쿠키 `ctrade_session`. 루프백은 OTP 생략. 5회 실패 시 IP 15분 차단, 3회 실패 경보, IP당 분당 1회 발송, 추적 IP 최대 1,000개. `/ws`도 차단 IP 검사.

## 6. 런타임 환경

- 루트 단일 `.venv`(로컬·VPS 동일). 실행 스크립트 `scripts/run_ctrade.sh`, `run_baker.sh`, `run_validation.sh`, `run_live_parity.sh`, `run_tests.sh`.
- `pytest.ini`: `pythonpath = . CTrade CCommander shared`, `norecursedirs`로 scratch/로그/데이터 제외.
- VS Code: `.vscode/settings.json`의 `python.defaultInterpreterPath = ${workspaceFolder}/.venv/bin/python`, `launch.json`엔 인터프리터를 하드코딩하지 않는다.
- CREON 수집은 Windows 호스트의 32-bit Python(`py -3.10-32`)을 WSL에서 PowerShell로 호출.
