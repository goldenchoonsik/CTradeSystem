# Symbols, Market Policies & Configuration

상위 규칙 S-*, R-1은 [../ARCHITECTURE.md](../ARCHITECTURE.md) §3.

## 1. 종목 마스터 원장 (`shared/config/symbols_registry.json`)

시스템이 다루는 모든 대상(매매 여부 무관 — 지수·선물·CFD·환율 포함)의 신원 기록. 키는 거래소 표기 심볼(리스팅 키). 같은 실물의 여러 리스팅(KRW-BTC / BTCUSDT / BTC-USD)은 `asset_id`로 묶는다.

- **원장이 소유**: `symbol`, `asset_id`, `name`+`name_source`, `market_type`, `exchange`, `base_currency`, `description`, 시장 정책 `overrides`, `tradable`, `is_active`, `catalog`, 외부 식별자(`binance_symbol`, `coinbase_symbol`).
- `tradable: false` = 참조/매크로 전용(타겟 선택 목록 `/api/markets`에서 숨김). `catalog: true` = 아직 등록 전인 검색 후보.
- 거래 대상의 `market_type`은 `DEFAULT_MARKET_POLICIES`에 명시 정책이 있어야 한다(없으면 OTHER 비용으로 조용히 떨어짐 — 원장 검증이 막음).
- 이름은 코드 사전이 아니라 데이터 원천(Upbit/KIS/CREON 조회)에서 채우고 `name_source` 기록. 못 찾으면 `unresolved`.
- 조회: `get_symbol_info`, `get_symbol_name`, `get_asset_id`, `get_symbol_scope`, `get_ledger_fields`, 판정 `market_utils.is_crypto_symbol`. 검색 `config_manager.search_symbols`(한글 부분일치·티커·코드).
- 원장 개요 UI/API: `/api/symbols/ledger_overview`.

### 위성 테이블 (심볼 키만 보유)
| 테이블 | 파일 | 답하는 질문 |
| :--- | :--- | :--- |
| 수집 대장 | `DataVault/config/source_mappings.json` | 어떻게 수집하나(출처·주기·`live_connection`·`category`) |
| 참조 관계 | `DataVault/config/symbol_relations.json` | 무엇이 무엇을 참조하나(`references[].role`이 Gold 결합 방식 결정) |
| 팩(바스켓) | `DataVault/config/symbol_groups.json` | 재사용 참조 묶음. 타겟에 `groups`로 부착하면 `get_relations_for_target()`이 평면화 |
| 매매 설정 | `CTrade/config/trading_config.json` | 무엇을 매매하나 |
| 프로젝트 설정 | `<프로젝트>/config_snapshot.json` | 무엇을 학습하나 |
| 데이터 파일 | `DataVault/**` 파일명 | 무엇의 데이터인가 |

- **가용 수집 데이터 조회**: `SymbolRelationsManager.get_available_vault_references(target)`가 DataVault 물리 수집/정규화 자산 중 현재 타겟 미등록 자산을 친화도(동일 시장 > 매크로 CFD > 타 시장) 순으로 선별 제공.

### 실시간 스트림 키
CTrade 상태 키는 거래소 차원이 없어 접미사로 구분(`BTCUSDT_PERP` = 바이낸스 선물, `_BPERP` = 비트겟 선물). 변환 규칙은 `shared/core/stream_keys.py` 한 곳.

## 2. 참조 컬럼·피처 명명 (2026-09-24, 별칭 폐지)

- 컬럼 `ref_{key}_*`, 피처 `ref_{key}_{ret_1c|ret_5c|spread_vs_target|intensity}`. `key = config_manager.ref_key(symbol)`(A 접두 제거·소문자·하이픈→밑줄). 이름 생성은 이 함수 하나.
  - `ret_1c` 직전 1봉 수익률 · `ret_5c` 5봉 누적 · `spread_vs_target` 참조 수익률 − 타겟 수익률 · `intensity` 참조 매수 체결비율.
- 원장·관계 어디에도 `alias` 필드가 없다(있으면 원장 검증 실패, 관계 저장 시 제거).
- 피처는 컬럼 이름이 아니라 원장 `market_type`으로 입력을 찾는다(`config_manager.find_ref_columns`, 예: 베이시스 = KRX_FUTURES + KRX_INDEX).
- 죽은 피처(상수/전부 결측)는 `shared/features/feature_health.find_dead_features`가 사유와 함께 스크리닝에서 제외.
- 폐지된 전용 피처: `nq_futures_ret_curr`, `sp500_futures_ret_curr`, `nq_futures_rsi_daily`, `global_lead_lag_divergence` → 범용 `ref_{key}_*`.
- 참조 부착/분리 API는 `reference_symbols`·`REFERENCE_SYMBOLS` 두 키를 같이 갱신한다. 참조를 지우면 해당 `ref_{key}_*` 피처도 활성 목록에서 제거된다.

## 3. 시장 정책 (`shared/config/market_policies.py`)

`DEFAULT_MARKET_POLICIES` 기본값(원장 `overrides`가 덮어씀):

| market_type | 수수료 | 슬리피지 | 매도세 | 호가단위 |
| :--- | ---: | ---: | ---: | ---: |
| KRX_ETF | 0.005% | 0.01% | 0 (면제) | 5 |
| KRX_STOCK | 0.015% | 0.02% | 0.20% | 100 |
| CRYPTO | 0.050% | 0.06% | 0 | 100 |
| US_ETF / US_STOCK | 0.070% | 0.05% | 0 | 0.01 |

- 수수료·슬리피지는 편도(매수·매도 각각), `sell_tax`는 매도 시에만 한 번 붙는 세금(국내 주식 증권거래세, 2026-01-01 양도분부터 코스피·코스닥 0.20%).
- 왕복 비용 = 2×(수수료+슬리피지) + 매도세. 계산은 `shared/core/trade_costs.round_trip_cost` 한 곳뿐이다(라벨·물리 제약·백테스트·CTrade 진입 필터·KIS 실현손익 공통). 직접 `(fee+slip)*2`를 쓰면 래칫 `shared/tests/test_sell_tax.py`가 실패한다.
- 매도세는 프로젝트 설정에 복사하지 않고 원장에서 타겟 종목으로 매번 읽는다(`resolve_sell_tax`). 설정에 `SELL_TAX`가 명시되면 그 값이 우선한다(시험용).

- 그 밖: 봉 시작 시각 자산 판정 `is_bar_start_asset`, 타임스탬프 규약 `timestamp_convention`, 벤치마크·상대 모멘텀 자산·기본 봉 같은 종목 판정 상수.
- 수수료 단일화: `FEE_RATE`(표준), `FEE_UPBIT`(하위호환), `OPTUNA_CONFIG.fee_upbit`는 같은 값으로 동기화. 백테스터·AutoLab·튜너가 같은 값을 쓴다(`shared/tests/test_fee_sync.py`).
- KRX 주문 호가 정렬 `round_to_krx_tick`: ETF는 5원 고정, 주식은 가격대별 단위.

## 4. 시장 유틸·캘린더

- `shared/core/market_utils.py`: 시장 판별(`detect_market_type` — 6자리 숫자 = KRX, `KRW-` = CRYPTO), 세션 상수, `parse_interval_minutes`, `get_target_symbol`.
- `shared/core/market_calendar.py`: `is_trading_day`, `get_trading_days`, `count_trading_days`, `snap_to_trading_day`, `prev/next_trading_day_strict`. 휴장일 캐시 `DataVault/config/market_holidays.json`. pandas 비의존(32-bit 수집기에서 import).

## 5. 설정 병합과 프로젝트 격리

- 병합 순서: `shared/config/common.json`(전역) → `CAiModelBaker/config/defaults.json`(도메인 기본) → 프로젝트 `config_snapshot.json`.
- **설정 원본은 열린 프로젝트의 `config_snapshot.json` 하나뿐이다. '자유 모드'는 없다 (2026-10-01 은퇴).**
  - 열린 프로젝트가 없으면: 읽기 = `common → defaults` 기본값만, 저장 = `NoOpenProjectError`(대시보드 409 `PROJECT_NOT_OPEN`).
  - 포인터가 깨졌으면(폴더 없음·빈 ref·JSON 오류): `BrokenProjectPointerError`(409 `PROJECT_POINTER_BROKEN`). 조용히 기본값으로 넘어가지 않는다.
  - 전역 `CAiModelBaker/config/active.json`·`active_config.json`은 **읽지도 쓰지도 않는다** — 보관본만 `config/legacy/`. 되살리지 말 것.
  - 종목·인터벌은 프로젝트 신원이라 `common.json`에 쓰지 않는다(`common.json`의 종목/인터벌은 새 프로젝트 기본값일 뿐).
  - `set_config_root()`는 테스트 격리 전용. 운영 코드가 부르면 포인터를 못 찾아 다른 설정으로 학습한다(래칫).
- 전역 원장 성격 값(`ARCHIVE_START_YM`, `ARCHIVE_END_YM`)은 `common.json`이 원본. 세션 로드가 이 값을 과거 스냅샷으로 덮어쓰지 않는다.
- 프로젝트(`shared/config/project_store.py`):
  - ref = `"<종목코드>_<인터벌>/<이름>"`, 이름은 전 인터벌에서 유일. 모호하면 `AmbiguousProjectError`.
  - 전역엔 `CAiModelBaker/config/active_project.json` 포인터만. 신원키(`SYMBOL_UPBIT`, `SYMBOL_BINANCE`, `INTERVAL`, `ACTIVE_SESSION_ID`)는 경로가 결정, `_`로 시작하는 실행용 키는 저장하지 않음. 열린 프로젝트의 종목/인터벌 변경은 `/api/config`가 409.
  - 새 프로젝트 = `common + defaults`에서 시작. 복사 원본은 명시적으로만, 모델 가중치는 복사하지 않음. 생성 시 `filter_compatible_features`로 타 자산군 지표를 제거.
  - 자동연구 1회 = `runs/<YYYYmmdd_HHMMSS>/`(leaderboard, result, config_used, train_run.log, trials, candidates, work). 루트 `model/`은 PASSED 챔피언일 때만 교체 + `model_source.json`.
  - 이전 스크립트 `CAiModelBaker/scripts/migrate_project_isolation.py`.
- 설정 쓰기 규약: `/api/features/config`는 보내온 키만 반영. ⚠️ '변경분만 전송'·`client_build()`/409 `STALE_CLIENT`는 2026-09-29 구현 기록이 있으나 **현재 코드에 없다**(커밋 전 유실). 정적 파일 판 번호는 `index.html`의 `features.js?v=` 수동 문자열이다.
- 설정이 이상하게 되돌아가면 `runs/*/config_used.json`과 `config_snapshot.json` mtime으로 언제 누가 썼는지부터 본다.

## 6. 관련 테스트
`shared/tests/test_symbol_ledger.py`, `test_no_symbol_hardcoding.py`(래칫, 기준선 `symbol_hardcode_baseline.json`), `test_ref_naming_single_source.py`, `test_symbol_relations.py`, `test_symbol_groups.py`, `test_stream_keys.py`, `test_fee_sync.py`, `tests/test_project_isolation.py`(자유 모드 은퇴·깨진 포인터·set_config_root 래칫 포함). 테스트 세션은 conftest 가 사본 `deploy/`에 `069500_10s/TEST_HARNESS` 프로젝트를 열고 돈다.
