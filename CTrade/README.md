# CTrade: HFT Crypto/Stock Scalping System

CTrade는 거래소 간 시차(Lead-Lag)와 시장 미세 구조(Microstructure) 분석을 활용한 초단타 매매(HFT) 시스템입니다.

## 🚀 Key Features
- **Decoupled Strategy Templates**: 공통 매매 룰(STRATEGY_TEMPLATES)과 개별 종목 타겟(SCALPING_TARGETS)의 분리 아키텍처 및 개별 오버라이드/기본 전략 자동 롤백 하이브리드 매핑 지원 [NEW]
- **Dynamic Rule Engine**: 코드 수정 없이 JSON 설정만으로 매매 전략 즉시 반영
- **Multi-Exchange Sync**: 업비트, 바이낸스, KIS(한국투자증권)의 실시간 데이터 통합 관리
- **Rich Web UI & Instant Display**: 고밀도 호가창 및 실시간 지표 모니터링 대시보드 및 실시간 틱 데이터 수집 전 `PENDING` 상태 즉시 노출 지원 [NEW]
- **Simulator Manager & Full Reset**: 웹 대시보드 상단 `SIMUL` 배지 및 포트폴리오 헤더 클릭을 통한 "모의 매매 관리자(Simulation Manager)" 모달 제공, 초기 시드머니(`SIMULATION_INITIAL_BALANCE`) 설정, 4단계 테스트 레벨 선택 및 가상 포지션/거래기록/잔고를 즉시 0과 초기 자본금으로 복원하는 완전 초기화(`POST /api/simulator/reset`) 지원 [NEW]
- **Simulator Mode w/ Auto-Refill & Real-time Trading Mode**: 실제 자산 투입 전 검증을 위한 모의 시뮬레이션(VirtualTrader) 및 SIMULATION_MODE 비활성화 시 실제 업비트 API 기반 실체결 주문을 집행하는 실거래 모드(UpbitRealTrader) 이중 아키텍처 탑재 [NEW]
- **Tiered Startup Position Reconciliation & Global Risk Manager**: 시스템 재기동 시 `entry_atr` 누락으로 인한 TP/SL 배리어 왜곡 및 시간만료 지연을 원천 차단하기 위한 3계층 무결성 복구 엔진 및 전 종목 동시 포지션/일일 손실을 일원화 통제하는 `GlobalRiskManager` 탑재 [NEW]
- **Continuous Candle Stream & On-Demand Radar**: 틱 유실/장애 시에도 이전 종가 ffill을 통해 빈 캔들을 누락 없이 연속 생성하는 어그리게이터 엔진 및 `RADAR_ACTIVE` 상태에 연동되어 웹소켓 연결을 온디맨드로 관리하는 자원 최적화 구조 탑재 [NEW]
- **Real-time Volume Detector**: 업비트 전 종목 대상 10초 롤링 윈도우 기반 거래량 폭발(RVOL $\ge$ 500.0%, VBP $\ge$ 65.0%, TPS $\ge$ 2.0) 실시간 백그라운드 탐지 및 기동 초기 15초 데이터 왜곡 방지(Warmup) 안전장치 탑재 [NEW]
- **Max Active Target Control & Eviction**: 동시 최대 공략 종목 수(`MAX_ACTIVE_TARGETS`, 기본값 10, **대시보드 STRATEGY SCRIPT에서 실시간 수정 가능**) 전역 제한, 포지션 미보유 유휴 종목 자동 방출(Eviction) 및 6개 진입/이탈 조건 설정 기반의 실시간 60초 쿨다운 자동 이탈(Fadeout) 알고리즘 탑재 [NEW]
- **Volume Scanner UI**: 3단계 네온 에스테틱 상태 감시 보드 및 200ms 초저지연 스위칭(Fast-Focus) 인터랙션 [NEW]
- **HFT Resilience & Resilient v1.3.0 Logging**: 웹소켓 무한 핸드셰이크 차단(`open_timeout=10`), 지수 백오프 기반 리트라이, 포트폴리오 조회 주기(30초) 완화 및 개별 예외 격리를 통한 무장애 HFT 환경 보장 및 `v1.3.0` 버전 식별자 기반 고성능 로깅 탑재 [NEW]
- **C++ DataManager Offloading**: 실시간 지표(Weighted Imbalance, Intensity, VWAP, RSI, Wall Resistance 등) 계산 및 대용량 틱 축적(12H), 시간별 거래량 버킷 관리를 C++ 링 버퍼 기반의 `ctrade_core` 모듈로 오프로드하여 GC 부하 제거 및 μs 단위 연산 극대화 [NEW]
- **C++ RuleEvaluator Integration**: JSON 매매 전략 룰 설정을 C++ AST(추상 구문 트리) 구조로 컴파일하여 매 틱/호가별 평가(should_buy, should_sell)를 C++ 네이티브로 실행함으로써 파이썬 오버헤드를 극소화 [NEW]
- **C++ Simulator Engine**: 가상 포지션 매칭, 평단가 계산, 수수료 산출, ROI 변동 추적 및 모의 잔고 자동 수혈(Auto-Refill) 로직을 C++ `VirtualTrader`로 이관하여 시뮬레이션 매칭 성능을 극대화 [NEW]
- **C++ WebSockets Parser Offloading**: 대용량 웹소켓 원시 메시지(Upbit, Binance, Bitget, KIS)를 C++ 네이티브 `nlohmann/json` 및 문자열 토크나이저로 고속 파싱하고 `pybind11`을 통해 데이터 객체(`TickData`, `OrderbookData`)를 즉시 파이썬으로 반환하는 최적화 구조 탑재 [NEW]
- **C++ Native Backtester Engine**: 파이썬 GIL 및 객체 Boxing 오버헤드가 배제되어 초당 수백만 틱의 역사적 시세/호가 데이터를 고속 순회 분석하고 체결 매칭/지표 계산을 C++ 메모리 상에서 원천 처리하는 백테스팅 모듈 [NEW]


## 📂 Project Structure
- `core/`: 매매 엔진, 시뮬레이터, 실시간 거래량 탐지기 등 핵심 로직
  - `core/data_manager.py`: 실시간 시장 데이터를 C++ `CoreTracker`로 즉시 바이패스하고 지표 분석을 오프로드 연산하는 데이터 관리 모듈 [MODIFY]
  - `core/rule_engine.py`: JSON 기반 전략 룰의 컴파일 및 핫 스왑 관리를 담당하며 C++ `RuleEvaluator`를 래핑하는 룰 엔진 모듈 [MODIFY]
  - `core/simulator.py`: 비동기 디스크 매매 이력 저장과 C++ `VirtualTrader` 네이티브 매칭을 유기적으로 결합한 가상 거래 시뮬레이터 모듈 [MODIFY]
  - `core/cpp/`: C++17 및 `pybind11` 기반의 초저지연 지표 계산, 고성능 링 버퍼, 네이티브 AST 룰 평가, 가상 매칭 및 고속 JSON 파서 소스 코드
    - `ctrade_core.h` / `ctrade_core.cpp`: `SymbolData`, `CoreTracker`, `RuleEvaluator`, `VirtualTrader` 및 전역 JSON/문자열 파서(`parse_upbit_message`, `parse_binance_message`, `parse_bitget_message`, `parse_kis_message`)를 정의하는 C++ 구현부
- `connectors/`: 거래소별 웹소켓/REST API 통신 모듈. 원시 문자열을 가공 없이 C++ 파서로 직접 송신 [MODIFY]
- `parsers/`: 수신 데이터를 공통 규격(TickData)으로 변환. C++ `ctrade_core` 바인딩 파서로 내부 호출을 이관하여 부하 차단 [MODIFY]
- `ui/`: FastAPI 기반의 실시간 웹 대시보드
- `config/`: 매매 전략 및 API 키 설정
- `scripts/`: 운영 및 검증 스크립트 모음
  - `scripts/run_backtest.py`: C++ `Backtester`를 구동해 메모리 데이터 주입 혹은 CSV 시세 데이터를 활용하여 초고속 백테스트 연산 및 ROI 검증을 수행하는 스크립트 [NEW]




## 🛠️ Quick Start
1. **가상환경 활성화**: `source .venv/bin/activate`
2. **의존성 설치**: `pip install -r requirements.txt`
3. **C++ 모듈 빌드**: `.venv/bin/python3 setup.py build_ext --inplace` 실행 (ctrade_core.so 생성)
4. **설정 관리**: `config/trading_config.json`에 API 키 및 전략 작성
5. **백테스트 실행**: `PYTHONPATH=. .venv/bin/python3 scripts/run_backtest.py` 실행하여 속도 및 전략 정합성 검증 [NEW]
6. **시스템 실행**: `python main.py`
7. **대시보드 접속**: 브라우저에서 `http://localhost:56789` 접속
## 📖 Documentation
- [시스템 아키텍처 및 데이터 흐름](docs/ARCHITECTURE.md)
- [매매 전략 작성 가이드](docs/STRATEGY.md)

## 🤖 Browser Automation & QA Guidelines
브라우저 자동화 테스트 및 실시간 화면 검증 시 다음 환경 구성과 프로세스를 준수합니다.

### 1. 테스트 환경 사전 구성 (CDP Port 9222)
- **오류 상황**: `CDP port not responsive` 또는 `Failed to connect` 발생 시 백그라운드 디버깅 브라우저가 없는 상태입니다.
- **해결 및 실행 방법**: 헤드리스 크로미엄 프로세스를 원격 디버깅 활성 상태로 백그라운드에서 기동합니다:
  ```bash
  /usr/bin/chromium-browser --headless --remote-debugging-port=9222 --remote-debugging-address=0.0.0.0 --disable-gpu --no-sandbox > /dev/null 2>&1 &
  ```
- **동작 확인**: CDP JSON API 응답 여부를 체크합니다:
  ```bash
  curl -s http://127.0.0.1:9222/json
  ```

### 2. UI 실시간 정합성 검증 시나리오
- **대상**: 대시보드 메인 포커스 패널 상단의 보라색 전략 배지 (`전략: 1천` 등)
- **절차**:
  1. `http://localhost:56789/` 대시보드에 접속해 포커스 종목의 기본 전략 계산단위 확인.
  2. `POST /config` API 호출을 통해 `SCALPING_TARGETS`의 해당 종목 `orderbook_level` 값을 변경 적용.
  3. 메인 대시보드로 복귀하여 웹소켓 실시간 브로드캐스트를 통해 엔진 재시작 없이 배지 텍스트가 즉시 갱신되는지 시각적 검증 및 캡처 수행.

