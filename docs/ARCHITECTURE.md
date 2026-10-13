# System Architecture (Core)

> **AI 에이전트 필독 — 이 파일만 먼저 읽는다.** 세부 내용은 작업 영역에 맞는 하위 문서 하나만 골라 읽는다(아래 §8 지도).
> 이 파일은 짧게 유지한다: 불변 규칙·SSOT 위치·파이프라인 순서만 둔다. 세부 사양·UI·실측 수치는 하위 문서로.

## 1. 시스템 구성

| 축 | 역할 | 위치 |
| :--- | :--- | :--- |
| **CTrade** | 실전 매매 엔진(실시간 시세 수신·AI 추론·주문). Vultr VPS에서 systemd로 구동 | `CTrade/` |
| **CAiModelBaker** | 데이터 정규화·Gold 결합·지표 스크리닝·학습(WFA/Optuna)·대시보드(`:8000`) | `CAiModelBaker/` |
| **CCommander** | 텔레그램 원격 관제 데몬(상태 조회, 매매 ON/OFF, 긴급 청산) | `CCommander/` |
| **shared** | 연구·실전 공용 코드(SSOT): `core/`(캔들·집행·청산·시장 유틸), `data_library/`(저장소·결합·감사), `features/`(지표), `config/`(원장·정책·설정) | `shared/` |
| **DataVault** | 데이터 원장(Raw → Normalized → Features)과 데이터 설정 JSON | `DataVault/` |

**증권사 역할 분리(절대 규칙)**: 대신증권 **CREON = 과거 틱/분봉/일봉 수집 전용**(Windows 32-bit COM, WSL에서는 PowerShell 브리지). 실시간 시세·주문·잔고는 **KIS**(국내/미국), 가상자산은 Upbit/Binance/Coinbase, 글로벌 CFD/FX 참조는 cTrader. `live_connection.provider`에 CREON을 지정하면 안 된다.

## 2. 파이프라인 (단계 순서를 우회·역전하지 않는다)

| 단계 | 입력 | 처리 | 출력 |
| :--- | :--- | :--- | :--- |
| 0 설정 | 사용자 | `SourceMappingManager`, `SymbolRelationsManager` | `DataVault/config/source_mappings.json`, `symbol_relations.json` |
| 1 Raw (Bronze) | 거래소 API | `creon_collector`, `scripts/fetch_ctrader_history.py`, crypto/overseas 수집기 | `DataVault/raw/{provider}/...csv` + `.manifest.json` (+ `.days.json`) |
| 2 Normalized (Silver) | Raw CSV | `CAiModelBaker/core/normalized_builder.py` | `DataVault/normalized/candles_{intv}/{symbol}_{intv}.parquet` — **단일 자산, `ref_` 없음** |
| 3 Gold 결합 | Normalized + `symbol_relations` 결합 계획 | `FeatureComposer` → `attach_reference` | `DataVault/features/{symbol}/data_vault_preview_{intv}.csv` |
| 4 Gold 스냅샷 | 3단계 | `CAiModelBaker/core/autolab_bridge.py::build_gold_snapshot` / `ensure_gold_snapshot` | `data_snapshot_{intv}.csv` + `.meta.json`(참조 구성 해시) |
| 5 스크리닝 | Gold 스냅샷 | `feature_screener.py`(1차, 장기 재현성) / `research_loop._run_ic_screening`(2차, 학습구간) | IC 리더보드 |
| 6 학습 | Gold 스냅샷 + 선택 지표 | `research_loop.py`, `model_engine.py`, `backtester.py` | `CAiModelBaker/deploy/{symbol}_{intv}/{project}/runs/<ts>/`, 합격 시에만 `model/` |
| 7 배포·실전 | 합격 모델 | `/api/deploy/shared`(배포 게이트) → `shared/deploy/...` → CTrade | KIS/Upbit 주문, `trade_history.json` |

배포 패키지 = `model_{intv}.txt` + `feature_spec.json`(지표 목록·순서) + `best_params_{intv}.json`(TP/SL/문턱) + `config_snapshot.json`. 실전은 이 패키지만으로 학습과 동일한 피처를 재현한다.

진입점: 수집 `POST /api/raw_store/sync_active` · 정규화 `POST /api/data_vault/smart_normalize_all` · 결합 `POST /api/data_vault/compose_features` · 1~3 일괄 `POST /api/data_vault/run_full_pipeline` · Gold 스냅샷 `POST /api/autolab/build_gold_snapshot` · 스크리닝 `POST /api/features/screener` · 자동연구 `POST /api/auto-research/start` · 실전 `scripts/run_ctrade.sh`.

## 3. 불변 규칙 (위반 = 버그. 대부분 래칫 테스트가 강제)

**계층**
- **L-1 계층 우회 금지**: 스크리닝·학습은 Gold(`data_snapshot`/`data_vault_preview`)만 읽는다. 스냅샷이 없다고 Silver parquet로 폴백하지 않는다.
- **L-2 Silver 순수성**: Normalized 파일엔 `ref_` 컬럼이 없다. `ref_`는 3단계 결합에서 타겟 관점으로만 붙는다.
- **L-3 스냅샷 ≠ 지표 명세**: `data_snapshot`은 결합된 시세 베이스일 뿐. 지표 목록/파라미터는 `model/feature_spec.json`에만 박제한다.
- **L-4 참조 변경 → 스냅샷 무효**: `symbol_relations.json`의 결합 계획이 바뀌면 스냅샷은 stale. `ensure_gold_snapshot`이 재빌드한다.
- **L-5 Gold 단일 작성기**: Gold 스냅샷은 `autolab_bridge.build_gold_snapshot`만 쓴다(`tests/test_gold_layer_contract.py`).

**시점 정합 (Point-in-Time)** — `tests/test_alignment_ratchets.py`, `tests/test_gold_layer_contract.py`
- **PIT-1 `bfill()` 전면 금지**: 미래값이 과거로 역류한다. 최초 관측 이전 결측은 NaN으로 둔다. 가격은 `ffill`만, 수급/비율은 `fillna(0.0)`. (구 문서 "Point-in-Time 무결성 규칙 ①" / "ARCHITECTURE.md:63"이 가리키던 규칙)
- **PIT-2 stale 상한**: 참조/매크로 `ffill`은 자산군별 상한을 넘으면 무효화(가격 NaN, 거래량 0). 상한: CRYPTO 1일 · FX 3일 · GLOBAL_FUTURES 4일 · KRX 7일 · 일봉 7일. 구현 `shared/data_library/reference_staleness.py`(`resolve_stale_cap`, `merge_asof_with_stale_cap`).
- **PIT-3 보간은 무효화를 되살리지 않는다**: 상한으로 무효화한 컬럼은 하류 `apply_fallback_to_dataframe(exclude_cols=…)`에서 제외하고 `INTENTIONAL_NULL`로 기록.
- **PIT-4 참조 결합 단일 진입점**: 모든 참조 결합은 `reference_staleness.attach_reference` 한 곳을 지난다. 결합 코드에서 `pd.merge_asof`를 직접 쓰지 않는다. 이름 충돌 시 ValueError(fail-closed).
- **PIT-5 타임스탬프 규약은 원장에만**: `DataVault/config/timestamp_conventions.json`(BAR_OPEN/BAR_CLOSE). 코드는 `market_policies.timestamp_convention()`으로 읽기만 하고, 미등록 수집처는 예외. 정렬 보정은 **결합 시점에만**(정규화는 타임스탬프를 건드리지 않음). `reference_merge_shift = bar_label_shift(ref) − bar_label_shift(target)`.
- **PIT-6 감사가 빌드를 거부하면 감사를 끄지 말고 데이터를 고친다**: `reference_alignment_audit`(미래참조), `interval_consistency_audit`(상·하위봉 집계), `tick_order_audit`(분 내부 틱 순서).
- **PIT-7 리샘플링은 상향만**: `shared/data_library/candle_resampler.py`. 상위봉→하위봉 변환(`LookaheadInversionError`), 비배수 인터벌, 1분봉을 초봉으로 쪼개기 금지.

**종목·이름** — `shared/tests/test_symbol_ledger.py`, `test_no_symbol_hardcoding.py`(래칫), `test_ref_naming_single_source.py`
- **S-1 원장에 없는 종목은 존재할 수 없다**: `shared/config/symbols_registry.json`이 신원(이름·시장·거래소·`asset_id`·`tradable`)의 유일한 기록. 위성 테이블(수집 대장·참조 관계·매매 설정·프로젝트 설정·파일명)은 심볼 키만 갖고 속성을 복사하지 않는다.
- **S-2 접근은 함수로만**: `config_manager`(`get_symbol_info`/`get_symbol_name`/`ensure_symbol_registered`…), `SourceMappingManager`, `SymbolRelationsManager`. JSON 직접 `json.load` 금지. 현재 타겟은 `market_utils.get_target_symbol()`.
- **S-3 참조 컬럼명은 심볼에서 유도**: `ref_{key}_*`, `key = config_manager.ref_key(symbol)`. 원장/관계 어디에도 `alias` 필드가 없다. 결합 대상 분류는 `SymbolRelationsManager.join_plan(target)` 하나.
- **S-4 운영 코드에 심볼 리터럴을 늘리지 않는다**: 종목 판정 상수는 `shared/config/market_policies.py` 한 곳. 수수료·슬리피지·호가단위는 `DEFAULT_MARKET_POLICIES` 상속 + 원장 `overrides`.
- **S-5 레거시 별칭(`target_*`, `upbit_*`)은 타겟 자산에만**. 참조에 생성·결합하지 않는다.

**연구·배포**
- **R-1 프로젝트 격리**: 단일 진입점 `shared/config/project_store.py`. 설정 원본 = `<프로젝트>/config_snapshot.json` **하나뿐**(자유 모드 없음 — 열린 프로젝트가 없으면 저장 거부, 포인터가 깨지면 오류), 전역엔 `CAiModelBaker/config/active_project.json` 포인터만. 전역 `active.json`은 은퇴(`config/legacy/`). 자동연구 1회 = `runs/<ts>/`, 루트 `model/`은 PASSED 챔피언일 때만 교체(`tests/test_project_isolation.py`).
- **R-2 배포 게이트**: 배포 API는 합격 여부와 실전 공급 가능성(`shared/data_library/live_feed_contract.py`)을 확인한다. 실전 미지원 지표가 든 모델은 CTrade 로드 시 거부(`AIStrategyModule._verify_parity_guard`).
- **R-3 체결·청산 SSOT**: 백테스트와 실전은 같은 엔진을 쓴다 — `shared/core/cexecution.py`(Numba 배치 / 스트림 세션), `shared/core/tbm_executor.py::resolve_exit`. 청산 우선순위: 당일청산(KRX 15:18) > 비상손절(틱 저가) > [캔들 종가 기준] Soft SL > Soft TP > 시간만료.
- **R-4 점수·판정 하한**: 점수 함수는 `model_engine.compute_wfa_score` 하나(PF 상한 20, 절대 최소 거래 10). 홀드아웃은 `resolve_holdout_trade_requirement` + `HOLDOUT_MIN_TRADING_DAYS`(5거래일 하한, 상수는 `model_engine.py` 상단). 체제 백본보다 못한 후보는 승격하지 않는다(`beats_backbone`).

## 4. 설정·데이터 SSOT 위치

| 무엇 | 파일 (원본) | 접근 |
| :--- | :--- | :--- |
| 종목 신원 | `shared/config/symbols_registry.json` | `config_manager` |
| 시장 정책(수수료·호가·봉 규약 판정) | `shared/config/market_policies.py` | 직접 import |
| 수집 대장(출처·주기·`live_connection`·`target_scales`) | `DataVault/config/source_mappings.json` | `shared/data_library/source_mapping_manager.py` |
| 타겟↔참조 관계·팩 | `DataVault/config/symbol_relations.json`, `symbol_groups.json` | `shared/data_library/symbol_relations_manager.py` |
| 타임스탬프 규약 | `DataVault/config/timestamp_conventions.json` | `market_policies.timestamp_convention` |
| 원천 카탈로그(상장일·결측 정책) | `DataVault/config/data_sources_catalog.json` | `shared/data_library/data_catalog_manager.py` |
| 휴장일 | `DataVault/config/market_holidays.json` | `shared/core/market_calendar.py` |
| 전역 공통 설정(수집 기간 `ARCHIVE_*_YM` 등) | `shared/config/common.json` | 병합 순서 common → defaults → 프로젝트 |
| 프로젝트 설정 | `CAiModelBaker/deploy/{sym}_{intv}/{project}/config_snapshot.json` | `project_store`, `config_manager` |
| 지표 정의 | `shared/features/feature_registry.py`(`DEFAULT_FEATURE_REGISTRY`) | `feature_engine.run_feature_engineering` |
| 실전 매매 설정 | `CTrade/config/trading_config.json` (VPS 원격본 보존, 배포 시 덮어쓰지 않음) | CTrade |
| 경로 정의 | `shared/data_library/paths.py` | — |

## 5. 테스트

루트 단일 `.venv`. `pytest.ini`(`pythonpath = . CTrade CCommander shared`) 또는 `./scripts/run_tests.sh`. 래칫 테스트(`tests/test_*_ratchets.py`, `test_project_isolation.py`, `test_gold_layer_contract.py`, `shared/tests/test_no_symbol_hardcoding.py` 등)는 위 규칙의 실제 집행 수단이다 — 실패하면 테스트가 아니라 코드를 고친다.

## 6. 다중 AI 협업 규칙

- 다른 AI가 만든 DAG·불변 규칙을 독단으로 되돌리지 않는다. 이견은 [docs/architecture/adr-log.md](architecture/adr-log.md)에 `[무전 발신: 모델명]`으로 남기고 사용자 승인을 받는다.
- 구조·데이터 키·핵심 로직을 바꾸면 해당 하위 문서를 갱신한다(이 파일은 규칙/SSOT가 바뀔 때만).

## 7. 문서 작성 규칙 (재비대화 방지)

- 이 파일은 **250줄 이하**. 새 내용은 하위 문서에 넣고, 여기엔 규칙 한 줄만.
- 쓰지 않는다: "100% 원천 차단" 같은 수식어, `[NEW]/[UPDATE]` 태그, CSS·픽셀 치수, 행 수·용량 같은 시점 의존 수치(필요하면 날짜와 함께 보고서 `docs/reports/`로).
- 코드에서 문서를 가리킬 땐 줄 번호 대신 규칙 ID(예: `ARCHITECTURE.md PIT-1`)를 쓴다.

## 8. 하위 문서 지도 — 작업 영역에 맞는 것만 읽는다

| 문서 | 읽어야 할 때 |
| :--- | :--- |
| [architecture/data-pipeline.md](architecture/data-pipeline.md) | 수집기, Raw/Normalized/Gold, 매니페스트, 정규화 그리드, 결측·stale 정책, 데이터 감사 |
| [architecture/symbols-and-config.md](architecture/symbols-and-config.md) | 종목 원장, 참조 관계·명명, 시장 정책·수수료, 설정 병합, 프로젝트 격리 |
| [architecture/research-pipeline.md](architecture/research-pipeline.md) | 지표 레지스트리, 스크리닝, 체제 백본, 사정거리, WFA 분할, 점수·홀드아웃 가드, AutoLab 산출물, 대시보드 |
| [architecture/ctrade-runtime.md](architecture/ctrade-runtime.md) | 실전 엔진: 커넥터, 캔들 집계, AI 전략, 주문·청산, 리스크, KRX 세션, C++ 코어, 공유 상태 |
| [architecture/operations.md](architecture/operations.md) | Vultr 배포, CI/CD, systemd, CCommander, 대시보드 2FA, 런타임 환경 |
| [architecture/adr-log.md](architecture/adr-log.md) | 과거 의사결정 기록과 다중 AI 의견 교환 |
