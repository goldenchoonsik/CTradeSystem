CTrad# CTrade Runtime (실전 매매 엔진)

상위 규칙 R-2, R-3은 [../ARCHITECTURE.md](../ARCHITECTURE.md) §3.

## 1. 흐름

```
거래소 WS/REST → connectors/ → parsers/(TickData, OrderbookData; C++ 파서) → core/tick_router.py
   → core/candle_aggregator.py (봉 완성) → core/ai_strategy.py (피처 → LightGBM → 신호)
   → shared/core/cexecution.py 스트림 세션 / tbm_executor (청산) → core/engine.py 주문 라우팅
   → Simulator(VirtualTrader, C++) 또는 실주문(KISTrader, UpbitRealTrader)
```
룰 기반 전략(`rule_engine.py`, 템플릿 `default_scalping`, `lead_lag_heavy`)과 AI 전략(`STRATEGY_TEMPLATES`의 `ai_baker`, `ai_kodex200`, `ai_samsung` 등 `type=AI_BAKER`)이 공존하며 `engine.py`가 `SCALPING_TARGETS[].strategy_name`으로 분기한다.

## 2. 커넥터 (`CTrade/connectors/`)

| 커넥터 | 용도 |
| :--- | :--- |
| `kis_connector.py` | 국내 주식(`H0STCNT0`/`H0STASP0`), 지수선물(`H0IFCNT0`/`H0IFASP0`), 주식선물(`H0ZFCNT0`/`H0ZFASP0`), 지수(`H0UPCNT0`), 미국(`HDFSCNT0`/`HDFSASP0`) 실시간. 주문은 REST(`kis_trader.py`) |
| `upbit_connector.py` | 한 종목에 원본(Level 0)·전략용(Level X)·UI용 스트림 동시 구독. 거래소 서버 집계(leveling) 사용 |
| `binance_connector.py`, `binance_futures_radar.py` | 현물/선물 멀티 채널. 선물 레이더는 50개 단위 멀티 소켓 |
| `bitget_connector.py`, `coinbase_connector.py` | 보조 거래소. Coinbase 120초 미수신 시 바이낸스 종가로 폴백(프리미엄 0) |
| `ctrader_connector.py` | `ProtoOASpotEvent` 구독. 원장의 CTRADER 참조만(`engine._ctrader_reference_symbols()`) → `agg.on_ref_tick`. 스팟이라 volume=0 → intensity NaN |
| `global_macro_feeder.py`, `yahoo_futures_feeder.py` | 매크로(Pyth/Hyperliquid WS, Yahoo) |

연결: `open_timeout=10`, 지수 백오프 1→60초. 잔고 조회 30초 주기, 거래소별 예외 격리.

## 3. 캔들 집계 (`core/candle_aggregator.py`)

- 경계는 `shared/core/candle_core.get_candle_boundary`(백테스트와 동일). 초 단위 인터벌 지원 — `feature_spec.json`의 인터벌로 설정.
- 비정상 틱 드롭: price ≤ 0, volume < 0, 직전가 대비 30% 이상 급변.
- 빈 봉은 직전 종가로 순차 생성. KRX는 장외 빈 봉을 만들지 않고 다음 개장에 이어붙인다.
- `candle_seq`/`total_completed_candles`는 버퍼(`maxlen=2000`)와 무관하게 단조 증가(쿨다운·시간만료 계산용). `_last_checked_seq`로 봉 완성 이벤트 유실 방지.
- 기동 시 웜업 백필(Upbit 페이지네이션 등). 모델 최대 윈도우(`required_warmup_candles`) 충족 전 매수 잠금(`is_warmed_up`).

## 4. AI 전략 (`core/ai_strategy.py::AIStrategyModule`)

- 배포 패키지 로드: `model_{interval}.txt` 단일 파일, `feature_spec.json`, `best_params_{interval}.json`. 메타 모델 승인 문턱 `shared/core/ai_policy.py::META_APPROVAL_THRESHOLD`(0.50).
- `_verify_parity_guard`: 실전 미지원 지표가 있으면 `RuntimeError`.
- 봉 완성 시 `run_feature_engineering(..., config)`(학습과 동일) → `predict` → `prob ≥ threshold` → 리스크 검사 → BUY.
- 상태: 매수 `is_pending_entry` → `on_buy_filled` / `on_buy_cancelled`, 매도 `is_pending_exit` → `on_sell_filled` / `on_sell_cancelled`.
- 테스트 레벨(`GLOBAL_STRATEGY.TEST_LEVEL` 0–3): 0=실전, 1 MILD(문턱 0.50), 2 ACTIVE(0.47, 모의 시 장시간 제약 우회), 3 TURBO(0.40, 전부 우회). 실전 매매 중 테스트 모드 불가, 테스트 중 실전 전환 불가. 원본 파라미터 파일은 수정하지 않는다.
- 패리티 덤프: `live_raw_candles.csv`, `live_feature_dump.csv` → `shared/tests/live_parity_check.py`(허용 오차 1e-4).

## 5. 청산 (`shared/core/tbm_executor.py::resolve_exit`)

우선순위: ① 당일청산(KRX 15:18, `INTRADAY_SQUARE_OFF`) ② 비상손절(틱 저가 기준, `entry_price − entry_atr × emergency_sl_mult`, 기본 SL×2) — 이하 **봉 종가 기준**: ③ Soft SL ④ Soft TP ⑤ 시간만료(경과 봉 수). 라벨링·백테스트·실전·교차검증이 같은 함수를 쓴다.

## 6. KRX 세션

- 신규 진입 09:10–15:00(`is_krx_entry_allowed`), 15:18 강제 청산(`is_krx_force_exit`, `KRX_FORCE_EXIT_AT`).
- `INTRADAY_SQUARE_OFF`: KRX만 선택 가능. CRYPTO는 항상 false(백엔드가 고정).

## 7. 주문·리스크

- `engine.execute_buy/execute_sell`: `SIMULATION_MODE`면 `VirtualTrader`(C++, 초기 자본 `SIMULATION_INITIAL_BALANCE`, 주식은 정수 주, 크립토 소수 8자리), 아니면 KIS/Upbit 실주문. 체결 기록의 `is_real`로 실전·가상 성과를 분리.
- KIS 실체결 시 C++ 포지션 동기화: `force_set_position` / `remove_position`.
- `SmartOrderExecutor`(`order_executor.py`): 지정가 대기 = `min(AI_BUY_WAIT_SECONDS, max(30, interval×0.75))`, ATR 기반 급락 시 취소. 2초 주기 상태 확인.
- `trading_active=false`: 신규 매수만 차단, 청산은 계속. `auto_trade=false`(종목별) 동일.
- `RiskManager`(`core/risk_manager.py`, 엔진 단일 인스턴스 공유): 일 손실 한도 −3%, 3연속 손절 시 60분 쿨다운, 동시 포지션 3개, Shadow Mode.
- 재기동 복구: `reconcile_startup_positions`(거래소 잔고 교차검증). `entry_atr` 복원 순서: `trade_history.json` → 백필 캔들 역추적 → 최근 평균 ATR.
- 실시간 대사: `portfolio_loop`(15초)에서 거래소 잔고 0인데 내부 포지션이 남으면 로컬 청산.
- 이력 저장 `utils/history_store.py::save_history_merged`: `fcntl` 락 + 합집합 병합 + `os.replace`.

## 8. 거래량 레이더 (룰 기반 스캘핑, `scalping_target_radar.py`)

1단계: Upbit `TICKER` 단일 스트림으로 전 종목 RVOL·VBP·TPS·Z-score(EMA 분산) 감시 → 2단계: 선별 종목만 `ORDERBOOK`/`TRADE` 구독해 정밀 평가. 바이낸스 선물 강도·베이시스 속도 교차검증, 스프레드 필터, 동시 급등 제한(`SIMULTANEOUS_SPIKE_LIMIT`), 3초 가격 기울기(`MIN_PRICE_SLOPE_3S`). `MAX_ACTIVE_TARGETS` 초과 시 무포지션·비고정(`pinned: false`) 종목부터 방출. `RADAR_ACTIVE=true`일 때만 동작.

## 9. 모델 건강 (`core/model_health_monitor.py`)

`HEALTHY` / `AGING`(30일 경과) / `DECAY`(4연속 손절 또는 최근 5회 이상 중 승률 < 40%) / `CRITICAL`(누적 낙폭이 백테스트 MDD의 80%). 상태 전이 시 텔레그램 1회, 같은 상태 24시간 쿨다운, 격상은 즉시.

## 10. C++ 코어 (`ctrade_core`, pybind11, 소스 `CTrade/core/cpp/`)

- `CoreTracker`: 종목별 고정 크기 링 버퍼(틱 이력 2,000 / 30초 10,000 / 3시간 50,000 / 시간 버킷 240), `get_metrics` 한 번 호출로 RVOL·VBP·TPS·Z-score·기울기 반환. 에너지 맵은 UI 포커스 종목만 요청 시 전송.
- `RuleEvaluator`: JSON 룰 → AST(`SimpleRule`/`RuleGroup`, AND/OR 단축 평가). 전략 변경 시 재생성(핫 스왑).
- `VirtualTrader`: 포지션·손익·수수료, 잔고 부족 시 5천만 원 자동 보충(`REFILL` 기록).
- 파서: `parse_upbit_message`, `parse_binance_message`, `parse_bitget_message`, `parse_kis_message`(국내주식 `H0STCNT0`/`H0STASP0`, 해외 `HDFSCNT0`/`HDFSASP0`, 지수선물 `H0IFCNT0`/`H0IFASP0`). `KISConnector`는 국내 선물을 만기마다 바뀌는 KIS 월물 코드로 구독 후 내부 심볼로 역매핑 전달한다. 월물 코드는 원장에 박제하지 않는다(`validate_symbols_registry` 가 `KRX_FUTURES` 의 `kis_symbol` 을 거부) — 접속 때마다 KIS 공개 마스터에서 최근월을 해석한다: 지수선물(원장 `kis_futures_base`, KOSPI200=`2001`)은 `fo_idx_code_mts`(예: `10100`→`A01612`), 개별주식선물(원장 `underlying`, 예: `15000`·`11100`)은 `fo_stk_code_mts`(예: `A50610`). 해석 실패 시 추측 코드 없이 구독을 생략하고 오류 로그. 주식선물은 `H0ZFCNT0`/`H0ZFASP0`로 구독(`utils/kis_futures_master.py`), KST 날짜가 바뀌면 재접속해 월물 교체를 반영. 교체 시점은 최종거래일(둘째 목요일, 휴장이면 직전 거래일) 당일 장 시작부터 다음 월물 — 학습 데이터인 Creon 연결 시계열이 그날 넘어가기 때문(2026-09-10 만기 실측: 10100·11100·15000 모두 08:46 봉부터 다음 월물과 일치). `H0ZFCNT0`은 현재가가 [2]번 필드(지수선물은 [5])라 파서가 따로 있다.
- `Backtester`: 틱·호가 병합 정렬 이벤트 루프(룰 전략용).

## 11. 비동기 설계

- `SyncManager` 프록시 쓰기는 동기 IPC → `run_in_executor`로 오프로딩, 이전 태스크 완료 시에만 새 작업 큐잉.
- 파일 I/O(`trading_config.json`, 이력)도 executor.
- UI 갱신 주기 `UPDATE_INTERVAL`(기본 0.3초).
- Supervisor(`main.py`): 자식 종료 SIGTERM 1.5초 후 SIGKILL, 크래시 시 exit 1 → systemd 재기동.

## 12. 공유 상태 (`core/shared_state.py`)

| 키 | 내용 |
| :--- | :--- |
| `[SYMBOL]` | price, intensity, imbalance, orderbook, active, auto_trade … |
| `__global__` | usd_krw_fx 등 |
| `__portfolio__` | 통합/브로커별 summary, `holdings_all`(`HoldingsItem`: market, currency, BOT/EXTERNAL 구분), baseline |
| `__sim__` | total_roi, symbol_stats, positions |
| `__system__` | cpu, mem, tps |
| `__logs__` | 최근 100건 |
| `control` | is_running, server_active, trading_active, simulation_mode, restart_engine |

포트폴리오(`utils/portfolio_tracker.py`): 거래소 원천 값(수량·평단)으로 평가액 직접 계산. `EXTERNAL` 보유 종목은 자동 청산 대상이 아니다. 기준선 `account_baseline.json`(일별 롤오버 09:00, 이력·복구 API `/api/portfolio/baseline/restore|delete`).

## 13. API (`CTrade/ui/api_server.py`)

`GET /`, `GET /analytics`, `GET /api/history`, `GET|POST /config`, `GET|POST /api/engine/symbol-auto-trade`, `GET /api/baker/models`, `GET /api/baker/model/spec`(지문·학습 구간·검증 성과), `POST /api/baker/model/delete`(활성 세션 삭제 금지), `POST /api/simulator/reset`, 진단 `/api/diagnostics/capture/*`(`utils/diagnostics`), `WS /ws`.

## 14. 관련 테스트
`CTrade/tests/`, `shared/tests/test_cexecution_parity.py`, `test_exit_priority_parity.py`, `test_intraday_square_off_safety.py`, `test_krx_feature_parity.py`, `test_feature_parity.py`, `test_meta_threshold_unification.py`, `test_stream_keys.py`.
