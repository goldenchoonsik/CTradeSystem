# CTradeSystem Monorepo

통합 알고리즘 트레이딩 시스템 및 AI 모델 베이커 저장소입니다.

## 1. 프로젝트 구조

| 디렉토리/파일 | 구분 | 역할 및 설명 |
| :--- | :--- | :--- |
| [`CTrade/`](file:///home/kuzimo/projects/CTradeSystem/CTrade) | 디렉토리 | 실시간 트레이딩 엔진 및 백테스팅 코어 |
| [`CTrade/connectors/coinbase_connector.py`](file:///home/kuzimo/projects/CTradeSystem/CTrade/connectors/coinbase_connector.py) | 모듈 | 코인베이스 공개 웹소켓(BTC-USD Ticker) 실시간 수신 및 Stale Fallback 커넥터 |
| [`CTrade/connectors/global_macro_feeder.py`](file:///home/kuzimo/projects/CTradeSystem/CTrade/connectors/global_macro_feeder.py) | 모듈 | Pyth Network 및 Hyperliquid 기반 0초 초저지연 글로벌 7대 매크로 선물/외환 실시간 피더 |
| [`CAiModelBaker/`](file:///home/kuzimo/projects/CTradeSystem/CAiModelBaker) | 디렉토리 | 학습 데이터 생성 및 AI 모델 학습/배포 엔진 |
| [`CCommander/`](file:///home/kuzimo/projects/CTradeSystem/CCommander) | 디렉토리 | 🛡️ Vultr 서버 인프라 및 CTrade 원격 텔레그램 관제 데몬 |
| [`shared/config/symbols_registry.json`](file:///home/kuzimo/projects/CTradeSystem/shared/config/symbols_registry.json) | 설정/DB | 중앙 종목 레지스트리 (국내 주식/ETF, 가상자산 메타데이터 마스터) |
| [`shared/config/source_mappings.json`](file:///home/kuzimo/projects/CTradeSystem/shared/config/source_mappings.json) | 설정/DB | 🌐 수집 대상 자산 대장 및 출처 1:N 매핑 선언적 리소스 (Primary/Fallback 정의) |
| [`shared/source_mapping_manager.py`](file:///home/kuzimo/projects/CTradeSystem/shared/source_mapping_manager.py) | 모듈 | 📜 원천 수집 대상 자산 거버넌스 및 물리 파일/매니페스트 무결성 감사 매니저 |
| [`shared/config/market_policies.py`](file:///home/kuzimo/projects/CTradeSystem/shared/config/market_policies.py) | 모듈 | 시장별 수수료·호가단위 표준 정책 및 Lookahead 방어(봉 시작 시각) 판정 |
| [`shared/symbol_relations_manager.py`](file:///home/kuzimo/projects/CTradeSystem/shared/symbol_relations_manager.py) | 모듈 | 타겟-참조 종목/매크로 피드 상관관계 선언적 리소스(`symbol_relations.json`) 관리자 |
| [`shared/live_feed_prober.py`](file:///home/kuzimo/projects/CTradeSystem/shared/live_feed_prober.py) | 모듈 | KIS/웹소켓 실시간 피드 준비 상태 안전 점검 및 세션 충돌 방지기 |
| [`shared/market_calendar.py`](file:///home/kuzimo/projects/CTradeSystem/shared/market_calendar.py) | 모듈 | 자동 마켓 캘린더 엔진 (KRX/미국선물 실거래 기반 휴장일 자동 동기화/판정) |
| [`CAiModelBaker/core/creon_collector.py`](file:///home/kuzimo/projects/CTradeSystem/CAiModelBaker/core/creon_collector.py) | 모듈 | 대신증권(크레온/CYBOS) 주식/ETF 및 참조 종목 과거 틱/1분봉/장기 일봉 증분 수집 및 영구 아카이브 보존기 |
| [`CAiModelBaker/core/overseas_futures_collector.py`](file:///home/kuzimo/projects/CTradeSystem/CAiModelBaker/core/overseas_futures_collector.py) | 모듈 | 미국 CME 해외선물(NQ, ES) 및 미국 주식/ETF(GOOGL, QQQ 등) 다층 타임프레임(1m~10y) 수집기 및 아카이브 임포터 |
| [`CAiModelBaker/core/normalized_builder.py`](file:///home/kuzimo/projects/CTradeSystem/CAiModelBaker/core/normalized_builder.py) | 모듈 | 💎 단일 자산 표준 정규화 Parquet 빌더 (타 종목 결합 배제, 10s/1m 대등 빌드, 샘플링 모드 뱃지 체계) |
| [`CAiModelBaker/core/feature_composer.py`](file:///home/kuzimo/projects/CTradeSystem/CAiModelBaker/core/feature_composer.py) | 모듈 | 🧩 온디맨드 시점 결합 엔진 (Point-in-Time Join, Coverage Guard, 세션 격리 아티팩트) |
| [`shared/data_library/manifest_manager.py`](file:///home/kuzimo/projects/CTradeSystem/shared/data_library/manifest_manager.py) | 모듈 | 📜 원천/정제 시세 데이터 1:1 사이드카 매니페스트(`.manifest.json`) 발행 및 SHA-256 검증기 |
| [`DataVault/`](file:///home/kuzimo/projects/CTradeSystem/DataVault) | 디렉토리 | 🏛️ 3단계(`Raw / Normalized / Features`) 물리 분리 데이터 볼트 (.gitignore 영구 차단) |
| [`scripts/run_tests.sh`](file:///home/kuzimo/projects/CTradeSystem/scripts/run_tests.sh) | 스크립트 | 🧪 전체 단위/통합 테스트 실행 스크립트 (`pytest`) |
| [`scripts/run_ctrade.sh`](file:///home/kuzimo/projects/CTradeSystem/scripts/run_ctrade.sh) | 스크립트 | 실시간 트레이딩 시스템 구동 스크립트 |
| [`scripts/run_baker.sh`](file:///home/kuzimo/projects/CTradeSystem/scripts/run_baker.sh) | 스크립트 | AI 모델 학습 및 굽기 프로세스 구동 스크립트 |
| [`scripts/run_validation.sh`](file:///home/kuzimo/projects/CTradeSystem/scripts/run_validation.sh) | 스크립트 | 전체 시스템 정합성 및 교차 검증 자동화 스크립트 |
| [`scripts/run_live_parity.sh`](file:///home/kuzimo/projects/CTradeSystem/scripts/run_live_parity.sh) | 스크립트 | 실시간 수집 데이터 및 매매 신호 Parity 검증 스크립트 |
| [`scripts/fetch_ctrader_history.py`](file:///home/kuzimo/projects/CTradeSystem/scripts/fetch_ctrader_history.py) | 스크립트 | Spotware cTrader Open API 기반 글로벌 CFD/FX 7대 대용 지표(나스닥, S&P500, 닛케이, 항셍, 금, 원유, 엔화) 과거 1분봉 대량 수집기 |
| [`scripts/normalize_ctrader_data.py`](file:///home/kuzimo/projects/CTradeSystem/scripts/normalize_ctrader_data.py) | 스크립트 | cTrader Bronze CSV ➔ Silver 표준 Parquet 정규화 및 Data Vault UI 자동 연동기 (`--symbol all` 지원) |
| [`scripts/test_ctrader_connection.py`](file:///home/kuzimo/projects/CTradeSystem/scripts/test_ctrader_connection.py) | 스크립트 | cTrader Open API SSL TCP 인증 및 심볼 연결성 진단 도구 |
| [`tests/test_data_vault_ui_parity.py`](file:///home/kuzimo/projects/CTradeSystem/tests/test_data_vault_ui_parity.py) | 테스트 | 🏛️ DataVault 물리 파일/매니페스트 ➔ UI 대장 1:1 동기화(Auto-Parity) 계약 테스트 |
| [`scripts/fetch_vultr_logs.sh`](file:///home/kuzimo/projects/CTradeSystem/scripts/fetch_vultr_logs.sh) | 스크립트 | Vultr 서버 로그 및 이력 격리 수집 스크립트 |
| [`scripts/fetch_kis_token.sh`](file:///home/kuzimo/projects/CTradeSystem/scripts/fetch_kis_token.sh) | 스크립트 | Vultr 서버의 KIS 인증 토큰을 로컬로 가져오기 |
| [`scripts/push_kis_token.sh`](file:///home/kuzimo/projects/CTradeSystem/scripts/push_kis_token.sh) | 스크립트 | 로컬의 KIS 인증 토큰을 Vultr 서버로 내보내기 |

---

## 2. 실행 및 검증 방법

### 🧪 전체 단위/통합 테스트 실행 (Pytest)
- CTrade, CAiModelBaker, CCommander, shared 전 영역 단위/통합 테스트 일괄 검증
```bash
./scripts/run_tests.sh
```

### 시스템 정합성 및 교차 검증 (오프라인)
- Baker 백테스터와 CTrade 실전 전략 모듈 간의 매매 신호 및 순수익률 1:1 교차 검증
```bash
./scripts/run_validation.sh
```

### 실시간 데이터 및 매매 정합성 검증 (라이브/온라인)
- 실시간 틱 수집기(`CandleAggregator`)의 피처 덤프 및 모델 추론 정합성 검증 (Parity Guard 내장)
```bash
./scripts/run_live_parity.sh
```

### 실시간 데이터 무결성 & AI 추론 피드 진단 (Stream Sniffer)
- 웹 대시보드 상단의 **`[🩺 DATA HEALTH]`** 버튼 클릭 또는 REST API를 통해 실시간 웹소켓 지연/누락/호가역전 및 AI 피처 무결성 1클릭 진단
- API 엔드포인트: `POST /api/diagnostics/capture/start`, `GET /api/diagnostics/report/latest`

### 🧪 모의 매매 관리자 & 시뮬레이션 완전 초기화 ([🧪 SIMUL])
- 웹 대시보드 상단의 **`[🧪 SIMUL ON/OFF]`** 버튼 또는 포트폴리오 패널 헤더 클릭 시 **"모의 매매 관리 (Simulation Manager)"** 모달 호출
- **시뮬레이션 초기 자본금 (`SIMULATION_INITIAL_BALANCE`)**: 1,000만, 3,000만, 5,000만, 1억 등 원하는 시드머니 설정 및 ROI 기준금액 연동
- **4단계 테스트 매매 강도 (TEST LEVEL)**: `Lv.0 OFF` ~ `Lv.3 TURBO` 원클릭 선택
- **완전 초기화 (Full Reset)**: 가상 보유 포지션 전량 정리, 매매 이력 0건 비우기, 잔고를 설정된 초기 자본금으로 원터치 복원 (`POST /api/simulator/reset`)
- **종목별 투자금액 설정**: `STRATEGY EDITOR`의 종목 매핑 뷰에서 종목별 1회 진입 예산(`budget_per_trade`) 개별 설정 가능

### 🌐 cTrader 글로벌 CFD/FX 7대 대용 지표 과거 데이터 수집 및 정규화
- Spotware cTrader Open API를 통해 나스닥, S&P 500, 닛케이 225, 항셍 50, 금, 원유, 엔화 과거 1년치 1분봉 대량 수집 및 Parquet 변환:
```bash
# 특정 심볼 수집 (예: US_500, JAPAN_225, HONG_KONG_50, XAUUSD, XTIUSD, USDJPY, US_TECH_100)
python scripts/fetch_ctrader_history.py --symbol US_500 --days 365

# 7대 자산 전체 Silver Parquet 정규화 및 Data Vault UI 자동 연동
python scripts/normalize_ctrader_data.py --symbol all
```

### 트레이딩 시스템 구동
```bash
./scripts/run_ctrade.sh
```

### 환경 변수 설정 (`CTrade/.env`)
| 환경 변수명 | 역할 및 설명 | 기본값 |
| :--- | :--- | :---: |
| **`KIS_APP_KEY`** | 한투 OpenAPI 발급 AppKey | *(필수)* |
| **`KIS_APP_SECRET`** | 한투 OpenAPI 발급 AppSecret | *(필수)* |
| **`KIS_ACC_NO`** | 증권 계좌번호 앞 8자리 (CANO) | *(필수)* |
| **`KIS_ACC_CODE`** | 계좌 상품코드 2자리 | `01` |
| **`KIS_URL_BASE`** | KIS REST API 엔드포인트 URL | `https://openapi.koreainvestment.com:9443` |
| **`UPBIT_ACCESS_KEY`** | 업비트 Open API Access Key | *(선택)* |
| **`UPBIT_SECRET_KEY`** | 업비트 Open API Secret Key | *(선택)* |
| **`TELEGRAM_BOT_TOKEN`** | 텔레그램 봇 토큰 | *(선택)* |
| **`TELEGRAM_CHAT_ID`** | 텔레그램 알림 수신 Chat ID | *(선택)* |

### 글로벌 CFD 과거 데이터 수집용 환경 변수 (`cTrader Open API` / WSL 환경)
| 환경 변수명 | 역할 및 설명 | 비고 |
| :--- | :--- | :---: |
| **`CTRADE_ACCOUNT_ID`** | cTrader 계정 번호 (Account ID / CtidTraderAccountId) | *(수집 시 필수)* |
| **`CTRADER_CLIENT_ID`** | Spotware Open API Client ID (App Client ID) | *(수집 시 필수)* |
| **`CTRADER_SECRET`** | Spotware Open API Client Secret | *(수집 시 필수)* |
| **`CTRADER_ACCESS_TOKEN`** | Spotware Open API 발급 Access Token | *(수집 시 필수)* |
| **`CTRADER_REFRESH_TOKEN`** | Spotware Open API 갱신용 Refresh Token | *(선택)* |

---

## 3. Vultr VPS 실전 운영 및 배포

본 시스템은 **"로컬 AI 모델 학습 및 백테스팅"**과 **"Vultr VPS 기반 24시간 실시간 트레이딩 엔진 구동"** 체계로 나누어 운영합니다.

### 1단계: 로컬 모델 완성 및 내보내기 (Export)
1. 로컬에서 `./scripts/run_baker.sh`를 실행하여 학습 웹 대시보드를 켭니다.
2. 3구간 최적화 및 백테스팅을 진행합니다.
3. 성능이 검증된 모델이 나오면 웹 UI의 **`⚡ [5] 실전 모델 배포 (shared/deploy)`** 버튼을 누릅니다. (모델 핵심 파일들이 `shared/deploy` 경로로 복사됩니다).

### 2단계: Vultr 서버 자동 배포 (GitHub Actions DevOps)
1. **일반 소스 저장 (CI 자동검증만 수행)**:
   ```bash
   git add .
   git commit -m "feat: 소스코드 수정"
   git push origin main
   ```
2. **Vultr 실서버 원격 배포 (Tag 푸시 또는 수동 트리거)**:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
3. *(로컬 쉘 배포 시)* 필요에 따라 직접 `./scripts/deploy_to_vultr.sh` 구동도 지원됩니다.

### 3단계: 📱 스마트폰 텔레그램 원격 관제 (CCommander)
PC를 켜지 않고도 스마트폰 텔레그램 메신저에서 Vultr 서버와 CTrade 엔진을 24시간 원격 제어합니다.
* **`/pnl`**: 마지막 기준점(`/setbase` 시각) 이후 실시간 순실현손익, 총수수료, 승률, 시행횟수, 계좌 ROI 조회
* **`/setbase`** (또는 `/resetbase`): 현재 시점으로 새 기준점(자산 및 타임스탬프) 설정하여 성과 0 리셋
* **`/balance`**: CTrade 운용 잔고, 누적 실현손익, 승률, 현재 보유 포지션 조회
* **`/trades [건수]`**: 최근 체결 내역(단가/수량/수수료/손익/사유) 및 최근 10건 승률 통계 확인
* **`/status`**: 서버 CPU/RAM/디스크 자원, CTrade 가동 여부, 누적 성과 요약, KIS 토큰 유효시간 종합 조회
* **`/pause`** (또는 `/trade off`): 매매 일시 정지 (보유 포지션 유지/감시, 신규 진입 차단)
* **`/resume`** (또는 `/trade on`): 매매 재개 (신규 매수 진입 활성화)
* **`/close <종목>`**: 지정 종목 즉시 시장가 수동 청산 (예: `/close KRW-BTC`)
* **`/closeall`** (또는 `/liquidate`): 보유 전 종목 일괄 긴급 시장가 청산
* **`/stop`**: CTrade 엔진 완전 종료 (로컬 PC 테스트 시 세션 해제)
* **`/start`**: CTrade 매매 엔진 가동
* **`/restart`**: CTrade 엔진 재기동
* **`/logs 30`**: CTrade 최근 실시간 저널 로그 30줄 확인
* **`/token`**: KIS 인증 토큰 상태 점검
* **`/security`**: 2FA 웹 보안 활성 세션 및 차단된 IP 목록 조회
* **`/unblock <IP>`**: 오차단된 특정 IP 즉시 잠금 해제 (예: `/unblock 1.2.3.4`)
* **`/reboot`**: Vultr 서버 원격 재부팅 (15초 내 재확인)

### 4단계: 🔒 웹 대시보드 텔레그램 2FA OTP 보안 로그인
* 대시보드 웹 접속 시 스마트폰 텔레그램으로 발송된 **6자리 일회용 OTP(3분 유효)**를 입력하여 24시간 세션 로그인
* 5회 연속 인증 실패 시 해당 IP 15분간 자동 차단 (Brute-Force 공격 방어)
* 3회 실패 시 스마트폰 비상 침입 경보 발송 및 IP당 1분 1회 발송 쿨다운 (Rate Limiting)

### 5단계: Vultr 프로세스 영속 관리 (systemd)
* **CTrade 엔진 제어**: `sudo systemctl start|stop|restart ctrade`
* **CCommander 관제 데몬 제어**: `sudo systemctl start|stop|restart ccommander`
* **로그 모니터링**: `journalctl -u ctrade -f -n 100` / `journalctl -u ccommander -f -n 50`


