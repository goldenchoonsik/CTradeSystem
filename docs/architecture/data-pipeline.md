# Data Pipeline (Raw → Normalized → Gold)

상위 규칙(L-*, PIT-*)은 [../ARCHITECTURE.md](../ARCHITECTURE.md) §3. 이 문서는 각 계층의 사양이다.

## 1. 계층별 책임

| 계층 | 담당 | 디렉토리 | 규칙 |
| :--- | :--- | :--- | :--- |
| Raw | 수집기 + `shared/data_library/ingestion_manager.py`, 날짜 병합 `CAiModelBaker/core/raw_day_store.py` | `DataVault/raw/{provider}/...` | Append-only, 원본 불변, 파일마다 `.manifest.json` |
| Normalized | `CAiModelBaker/core/normalized_builder.py` | `DataVault/normalized/candles_{intv}/`, `normalized/macro/` | 단일 자산. 타 종목 결합 금지. Parquet(float32/int32) |
| Features (Gold) | `FeatureComposer`(`CAiModelBaker/core/feature_composer.py`, `shared/data_library/feature_composer.py`) → `attach_reference`; 스냅샷은 `autolab_bridge.build_gold_snapshot` | `DataVault/features/{symbol}/` | 인메모리 point-in-time 결합. 감사 통과 시에만 저장 |
| Quarantine | 무결성 엔진 | `DataVault/quarantine/` | 거부된 병합(`rejected_merges/`), 병합 전 백업(`merge_backups/`), 리셋 백업(`reset_backups/`) |
| Temp | 수집 버퍼·런타임 캐시 | `DataVault/temp/{cache,staging,creon}/` | — |

`shared/data_library/feature_vault.py`(FeatureVault)는 **사용 중단**: 생산 경로와 다른 값을 낸다. 대신 `shared/features/feature_engine.run_feature_engineering`.

## 2. Raw (Bronze)

### 디렉토리
```
DataVault/raw/
├── creon/   tick/A{sym}/YYYY-MM-DD.csv(+.meta.json), candle_1m/A{sym}/YYYY-MM-DD.csv(+.meta.json), candle_1d/
├── ctrader/ {symbol}_1m.csv            # 글로벌 CFD/FX 7종
├── overseas/futures_{1m,5m,60m,1d}/    # CME NQ/ES
├── kis/candle_{1m,5m,1d}/              # 미국 직투 주식/ETF
└── crypto/{upbit,binance,coinbase}/    # binance는 {sym}_funding_rate.csv, {sym}_oi.csv 별도
```
**틱 = 일자별 저장소** (통합 CSV 없음, 2026-10-02 전환): 쓰기는 `CAiModelBaker/core/tick_day_store.py`, 읽기는 `shared/data_library/tick_store.py`만 거친다. 날짜 상태는 `.meta.json`의 status(OPEN·VALID·ACCEPTED_DEVIATION·QUARANTINED·LOST). 저장 규칙: 날 D 는 D+1 06:00 KST 이후에 시작한 수집에서만 저장하고 저장과 동시에 봉인한다(당일 데이터는 받아도 버린다). 봉인 기준은 **봉인된 1분봉** 거래량과 정확히 일치(1분봉이 아직 봉인 전이면 대기)이고, 봉인일은 수정하지 않는다. 래칫: `tests/test_tick_day_store.py` (실물: `test_real_vault_tick_day_store_ratchet`). 수동 작업: `scripts/tick_day_store_tool.py verify|seal|accept`.

**Creon 1분봉 = 일자별 저장소** (통합 CSV 없음, 주식·ETF·선물 `10100`·주식선물 `15000`(SK하이닉스 연결)·`11100`(삼성전자 연결)·지수 `U180` — 선물/지수 폴더는 `A10100/`·`A15000/`·`A11100/`·`AU180/`; 주식선물 코드는 Creon `CpCodeMgr.GetStockFutureListByBaseCode` 로 조회하고 가격 수준을 기초자산과 대조해 등록한다 — 앞자리로 유추 금지(`10600`=코스닥150 연결, `11600`=현대차 연결)): 엔진은 틱과 같은 `tick_day_store`(Kind 인자로 파일명·거래량 키만 다름), 1분봉 고유 규칙은 `CAiModelBaker/core/candle_day_store.py`, 읽기는 `shared/data_library/candle_store.py`만 거친다(소비자는 종전의 `candle_1m/A005930_1m.csv` 논리 경로를 `path_has_data`/`read_csv` 로 묻는다 — 파일은 없다). `download_all_krx` 일괄 파이프라인 및 단일 수집(`--type futures`) 모두 `market_type=KRX_FUTURES` 판별을 통해 주식선물 COM 차트로 자동 분기 및 동적 경로 할당. 저장 규칙은 틱과 같다(D+1 06:00 이후 수집분만). **봉인 기준 = 구조(그 날짜·시각 엄격 증가·분 격자·메타 해시) + 일봉 거래량 상한(1분봉 합 ≤ 일봉) + 봉인된 틱이 대조했던 1분봉 거래량과 동일**; 일봉과의 *일치*는 쓰지 않는다(일봉에는 시간외 거래량이 더 들어 520일 중 0~41일만 일치) 그리고 봉 수 하한도 쓰지 않는다(체결 없는 분은 봉이 없어 얇은 ETF 는 정상도 380봉 미만 — 하한을 두면 7,668일 중 1,010일 오탐). 1분봉은 다른 데이터를 기다리지 않는다(일봉·틱이 없으면 그 검사만 건너뜀) — 순서는 일봉 수집 → 1분봉 봉인 → 틱 봉인. 수동 작업: `scripts/candle_day_store_tool.py migrate|verify|seal`. 래칫: `tests/test_candle_day_store.py`.

"대장 등록 자산 수 = 물리 파일 수 = 매니페스트 수".

### 수집처 역량 (`ingestion_sources`)
| 제공처 | 원천 스케일 | 비고 |
| :--- | :--- | :--- |
| UPBIT | TICK, 1S, 1M, 1D | 초봉 공식 지원 |
| BINANCE | TICK, 1S, 1M, 1D | aggTrades |
| COINBASE | TICK, 1M, 1D | 초봉 미지원(가짜 1S 금지). 지원 granularity 60/300/900/3600/86400s, 비지원 주기는 하위봉 받아 상향 리샘플 |
| CTRADER | TICK, 1M, 1D | Protobuf SSL, 포트 5035 |
| CREON | TICK, 1M, 1D | 틱 서버 보존 약 20거래일, 1분봉 약 500일 → 로컬이 영구 아카이브 |
| KIS | TICK, 1M, 5M, 1D | 미국 정규장 |
| CME | 1M, 5M, 1D | NQ, ES |

`ingestion_sources`에 없는 초단기 스케일을 `target_scales`로 고르면 제공처 역량에 맞춰 원천이 자동 추가된다.
- **국내주식 권장 기본값**: `TICK, 1M, 1D` 3종 모두 체크(수집 권장)이며, UI에서 체크 해제 시 백엔드 다운로드에서도 해당 단계(일봉/분봉/틱)가 즉시 스킵된다.
- **가상자산 권장 기본값**: `1M, 1D` 2종 체크(TICK 제외, 수수료 방어 최적화). 업비트/바이낸스/코인베이스 일봉 및 1분봉 공식 수집 지원.
- **해외선물(CME/YAHOO) 및 cTrader(CFD 7종) 권장 기본값**: `1M, 1D` 2종 체크.
  - CME/YAHOO: `overseas_futures_collector.py`의 보조 인터벌(5m, 1d) 강제 호출 하드코딩을 제거하고 `ingestion_sources`를 1:1로 정확히 준수.
  - cTrader: `fetch_ctrader_history.py`에 `--period`(1m, 1d) 지원을 추가하고, 장외 호가 틱(Quote Tick) 대신 AI 매크로 프록시 최적인 1분봉(M1) 및 일봉(D1)으로 표준화.
- **시간 단위별 핀포인트 갱신/삭제**:
  - `POST /api/data_vault/sync_single_scale`: `{ symbol, scale, tier: "raw" | "normalized" }` (지정된 단일 주기만 증분 수집 또는 재합성 — UPBIT/BINANCE/COINBASE, CREON, CME, KIS, CTRADER 전 제공처 지원).
  - `POST /api/data_vault/delete_single_scale`: `{ symbol, scale, tier: "raw" | "normalized" }` (지정된 단일 주기의 물리 파일/매니페스트/원장만 디스크에서 안전 삭제).
  - UI 연동: 자산 결측일 헬스 매트릭스 상세 연도별 잔디 캘린더 아코디언 헤더에 각 주기별 `[🔄 갱신]` 및 `[🗑️ 삭제]` 버튼 연동.

### 원천 무결성
- **날짜 단위 병합** (`raw_day_store.py`, 표준 라이브러리만 — 32-bit 수집기가 import): 파일은 항상 시각 오름차순. 병합 단위는 같은 시각 묶음, 더 온전한 쪽 채택. 임시 파일에 쓴 뒤 불변식(어떤 날짜도 행·거래량 감소 없음, 건드리지 않은 날짜 해시 불변) 통과 시에만 원자 교체. 날짜별 (행 수, 거래량, 해시, 기준 대비 비율)을 `<파일>.days.json`에 기록.
- 검증/복구 CLI: `python scripts/raw_store_integrity.py verify|repair [--apply]|baseline`.
- `shared/data_library/data_quality.py`: `.days.json`과 대조해 엔진을 거치지 않은 변경을 불량 표시.
- **CREON 틱은 초 정보가 없다** → 분 안의 행 순서가 시간축. `shared/data_library/tick_order_audit.py`가 1분봉과 대조해 forward/reversed/unordered 분류. reversed·unordered 날짜는 10초봉에서 자동 제외(원천은 보존). 통합 CSV 시절의 정렬 복구 스크립트는 일자 저장소 전환과 함께 은퇴 — 새로 발견되면 서버 보존 기간(틱 20일) 안에 재수집으로 바로잡는다. CREON 틱 bid/ask 컬럼은 신뢰 불가.
- CREON 증분 수집: 파일 꼬리에서 마지막 시각을 찾고, 비정상 영업일 결측 갭이 있으면 갭 직전부터 역방향 백필.
- 원천 리셋: `POST /api/raw_store/reset_and_refetch`(종목코드 재입력 확인) → `quarantine/reset_backups/`로 백업 후 `archive_start`부터 재수집.

### 사이드카 매니페스트 필드
`schema_version`, `data_file`, `data_tier`·`provider`, `integrity`(sha256, byte_size, row_count, first/last_timestamp), `krx_validation`(trading_days, min_minutes_per_day, fully_intact, anomaly_dates), `source_meta`(notes, `indicator_type: proxy|canonical`, `sampling_mode`). Normalized 매니페스트는 `health_summary`(total_bars, null_ratio, price_range, has_microstructure)도 기록 — 파일을 읽지 않고 상태 판정.

### Raw Store 헬스 매트릭스 & 캐시 정책 (`shared/data_library/raw_store_health.py`)
- **목적**: 대시보드 및 학습 파이프라인에서 원천 29개 자산의 결측일 및 건전성을 스크롤 없이 관찰(최근 120거래일 시계열 및 상세 클릭 시 연도별 2D 잔디 캘린더).
- **Cache-First 로딩**:
  - `GET /api/raw_store/health_matrix?days=120&cache_only=true` (기본값): 수십 초 소요되는 원천 재스캔 없이 디스크 캐시(`DataVault/temp/cache/raw_store_health_cache.json`)에서 1초 내 즉시 응답.
  - **원자적 저장(Atomic Write)**: 임시 파일(`.tmp.{pid}`) 기록 후 `os.replace`로 교체하여 저장 도중 프로세스 강제 종료 시에도 캐시 무결성 보장.
- **원천 변경 감지 (Update Detection)**:
  - 파일 지문/mtime(`_stamp`)을 검사해 캐시의 `_mtime`과 불일치 시 `has_updates: true`, `updated_count`, `updated_symbols`, `cached_at` 반환.
  - 대시보드 상단 배너 노출 또는 `[⚡ 증분 품질 감사]` / `[🔍 전수 품질 감사]` 실행 시 최신 데이터 자동 재스캔 및 반영.
- **연도별 2D 잔디 캘린더**:
  - 자산 행 클릭 시 `/api/raw_store/health_matrix/intervals`를 호출하여 틱/1분봉/일봉 그래뉼러리티별 연도 그리드(53주 x 7일, 주말 제외) 렌더링.
- **원천 데이터 품질 감사(Quality Audit) 체계 (`shared/data_library/data_quality.py`)**:
  - **7대 무결성 감사 항목 (QualityPolicy)**:
    1. `check_time_reversal`: 시간 역행 감지 (`ts[i] < ts[i-1]`) — 데이터 유실/뒤섞임 원천 차단 (`bad`)
    2. `check_duplicate_time`: 중복 타임스탬프 감지 (`ts[i] == ts[i-1]`) (`warn`)
    3. `check_broken_geometry`: 캔들 기하학 파손 감지 (`high < low`, Open/Close 범위 이탈) (`bad`)
    4. `check_bad_price`: 0 이하 / 결측(NaN) / 음수 가격 감지 (`bad`)
    5. `check_missing_bars`: 세션 기대 분봉 대비 결손율 감지 (`warn`/`bad`)
    6. `check_flash_spike`: 이상치 가격 급변 후 순간 복귀 스파이크 감지 (`warn`/`bad`)
    7. `check_cross_reconciliation`: 상위 원천(틱 ↔ 1분봉 ↔ 일봉) 거래량 및 가격 교차 대조 (CREON 등)
  - **전 자산(25개) 포괄 감사**: 교차 대조 대상인 CREON뿐 아니라 가상자산(업비트/바이낸스/코인베이스), CFD 7종, FX, 해외선물 등 단일 원천 파일 전체에 4대 시계열 무결성 감사 자동 적용.
  - **증분/전수 감사 모드**:
    - 증분(`full=false`): 기존 통과 일자는 재사용하고 신규/미검증 일자만 핀포인트 감사 (캐시 적중 시 초고속). 틱/1분봉 일자 저장소는 파일 로드 전 일자 집합 메타 대조로 무변동 시 메모리 적재를 완전 바이패스하고, 신규 일자만 날짜 슬라이싱 로드.
    - 전수(`full=true`): 과거 전체 데이터를 100% 재대조하는 수동 심층 감사.
  - **메모리 안전성 (OOM 방어)**: 8GB 메모리 환경 보호를 위해 전 자산 감사는 `max_workers=1` 직렬 안전 실행하며, 각 자산 감사 완료 시 명시적 `gc.collect()`로 메모리를 즉시 반환.
  - **스레드 안전 캐싱**: `DataVault/temp/cache/data_quality_cache.json`에 원자적 교체(`os.replace`) 및 `threading.Lock` 스냅샷 기록.
  - **대시보드 UI 연동 및 상태 기억**:
    - `[⚙️ 감사 옵션]` 버튼 및 접이식 설정 패널(`hmAuditConfigPanel`): 7대 검사 항목별 개별 체크박스 제공 (기본값: 모두 체크).
    - `localStorage`(`hm_quality_policy_options`): 사용자 선택 상태 영구 기억 및 감사 실행 시 백엔드 파라미터(`policy_options`) 동봉 전송.
    - API: `POST /api/raw_store/quality_audit_all` (`policy_options` 지원), `GET /api/raw_store/quality_policy_defaults` (7대 기본값 및 명칭), `GET /api/raw_store/quality_audit_status`.

### 대장 자동 동기화
- 수집/정규화 스크립트 종료 시 `SourceMappingManager.ensure_asset_registered()` 호출.
- `GET /api/raw_store/registry`가 유효 매니페스트가 있는 미등록 자산을 발굴(`auto_discover_vault_manifests`, mtime 캐시). 매니페스트 없는 임시 파일은 등록하지 않는다.
- `SourceMappingManager.sync_with_symbol_relations`: 참조 관계에 있는 종목이 대장에 없으면 전파.
- 회귀: `tests/test_data_vault_ui_parity.py`.

## 3. Normalized (Silver)

### 시장별 세션 그리드 (`generate_market_grid`)
| 시장 | 세션(KST) | 규칙 |
| :--- | :--- | :--- |
| KRX | 09:00–15:30 (10s 2,341봉 / 1m 391봉) | 15:20–15:30 동시호가 `is_auction=1`, ±7% 이상 틱 스크러빙 |
| US | 22:30–05:00(서머타임) | — |
| CRYPTO | 24/7 | 무거래 구간 close ffill, 거래량 0 |
| OVERSEAS(CME) | 07:00–익일 06:00 | 일일 정산 휴장 제외 |

### 빌더
- `build_normalized_10s_stock`: 틱 → 10초 OHLCV + `vwap`, `ofi`, `rv`, `is_large_trade`, `is_auction`. 1분봉이 없으면 예외(틱 순서 감사에 필요) — `build_single_asset_normalized`가 **상위봉부터** 빌드한다.
- `build_normalized_candle_stock`, `build_normalized_us_stock`, `build_normalized_crypto`(binance 펀딩비·OI를 `merge_asof(backward)`로 결합, ns 정밀도 통일).
- 표준 스키마: `open, high, low, close, volume, trade_amount, trade_count, bid_volume, ask_volume, cvd, spread, mid_price` (+ 마이크로 지표).
- `primary_type == "TICK_AND_1M"` 자산은 10s와 1m 모두 빌드.
- 증분: `upsert_normalized_daily` / `upsert_all_active_normalized` — 마지막 날짜 이후만, `.tmp.parquet` → `os.replace`. 변화 없으면 skip.
- macro 파일도 원장 키로 이름 짓는다(`macro/10100_1m.parquet`, `NQ`, `ES`). 경로는 `paths.macro_raw_path` / `macro_normalized_path` 한 곳.

### 샘플링 모드 (`source_meta.sampling_mode`)
`TICK_RESAMPLED`(틱→10s) · `BAR_NATIVE`(원천 봉 그대로) · `BAR_DOWNSAMPLED`(하위봉 합성) · `POINT_IN_TIME_ASOF`(상위봉을 하위 시점에 과거 확정치로 정렬).

### `target_scales`
자산별로 만들 Silver 봉 목록. 핵심 6종: `10s, 1m, 5m, 15m, 1h, 1d`. 체크 해제 시 해당 파생 parquet는 삭제(`prune_unselected_normalized_files`), Raw는 보존. API: `POST /api/data_vault/update_target_scales`, `batch_update_target_scales`, `prune_orphans`.

### 공용 캔들 코어 (`shared/core/candle_core.py`)
과거 리샘플러와 실시간 `CandleAggregator`가 공유: `get_candle_boundary`(봉 경계 `[T, T+dt)`), `compute_bid_ratio`(무거래 0.5), `compute_avg_trade_size`, 대량체결 기준 `LARGE_TRADE_THRESHOLD_KRW`(1억), `sanitize_candle_ohlcv`(H/L 기하 보정). 회귀: `tests/test_candle_core_parity.py`.

### 리샘플러 (`shared/data_library/candle_resampler.py::resample_candles`)
라벨 = 봉 시작 시각, close = 구간 마지막 소스 close. 집계 규칙 자동 추론(OHLCV first/max/min/last/sum, 비율 mean, 외부 단일 시세 last). 오류: `LookaheadInversionError`, `UnsupportedIntervalError`, `NonDivisibleIntervalError`, `InvalidTimestampError`.

## 4. Gold (Features)

- 결합 계획: `SymbolRelationsManager.join_plan(target)`. role=`leading_stock`은 공통 거래일 교집합으로 구간을 자르고, 매크로/선물/CFD는 자르지 않고 붙인다.
- 결합: 모든 `FeatureComposer`는 `attach_reference`에 위임(구조 래칫이 AST로 강제). 참조 값 컬럼만 넘긴다(`ref_*`) — 10s Normalized의 `target_*` 컬럼이 넘어가 타겟 자리를 탈취한 사고(2026-10-01) 이후 이름 충돌은 ValueError.
- 정제 `sanitize_post_join`: 가격 `ffill`만, 수급/거래량/수익률/비율 `fillna(0.0)`. 참조 파생 피처(`ret_1c` 등)는 결측을 0으로 바꾸지 않고 NaN 유지 — LightGBM이 결측으로 처리.
- 교차 팩터(Composer v2): `cross_{ref}_spread`, `cross_{ref}_zscore`(30바), `cross_{ref}_corr`(20바), `macro_basis_ratio`, 상위봉 MTF 결합 `mtf_{intv}_{col}`.
- 스냅샷 저장 전 감사: `reference_alignment_audit.assert_reference_alignment`(참조가 타겟의 과거/미래 중 어디에 붙는지, **구간 분할** 필수 — 반대 방향 결함이 평균에서 상쇄됨), `interval_consistency_audit`(상·하위 봉 집계 무결성: `step >= 1d` 시 거래소 일봉 관례 00:00/09:00 KST 앵커 자동 적응).
- `data_snapshot_{intv}.meta.json`: 참조 구성 해시 + `data_quality`(컬럼별 결측·보간·잔여 결측).
- 스냅샷 재사용: `ensure_gold_snapshot(force_rebuild=False)` — 결합 계획 일치 시 재사용, 부재/stale 시 재빌드.

## 5. 결측·보간 정책

- 선언적 보간: `DataCatalogManager.apply_fallback_to_dataframe()`이 `DataVault/config/data_sources_catalog.json`의 `fallback_policy`(`ROLLING_INVERSE_RATE`, `FORWARD_FILL`, `ZERO_FILL`, `CRITICAL_ERROR`)를 적용하고 `imputation_report`를 남긴다. 코드에 인라인 `ffill/bfill` 금지.
- Smart Skip: 카탈로그의 `available_start_date`(상장일) 이전·휴장일은 요청하지 않고, 수집률 계산에서 N/A로 분리.
- KRX 휴장일 동기화 (`shared/core/market_calendar.py::sync_market_holidays`): Yahoo Finance 온라인 실거래 일봉뿐 아니라 DataVault 내 장기 일봉 아카이브(1980~현재)를 역산 스캔하여 1980년 이래의 대한민국 법정공휴일/임시공휴일/선거일/연말폐장일을 100% 자동 복원·병합(`market_holidays.json`).
- stale 상한·보간 제외: 상위 문서 PIT-2/PIT-3. 실측 감사: `scripts/audit_reference_staleness.py`.

## 6. 데이터셋 빌드 하드페일 (`CAiModelBaker/core/data_manager.py::validate_snapshot_integrity`)

| 가드 | 기준 |
| :--- | :--- |
| 캔들 밀도 결손 | KRX 일자별 고유 분 < 380 → ValueError |
| 참조 결측 | 일자별 `ref_*_close` 결측률 ≥ 50% → ValueError |
| 가짜 수평선 | 30분(10s 180봉) 가격 불변 & 거래량 0 → ValueError |
| 장중 미완료 당일 | 15:30 이전이면 당일 제외 |
| 중복 원천 | DataVault 우선, 레거시 중복 로드 금지 |
| 크립토 괴리 | 코인베이스/바이낸스 괴리 ±50% 초과, 벤치마크 BTC 결측/음수 → 차단 |

## 7. 실시간 피드 계약 (`source_mappings.json::live_connection`)

필드: `provider`(CREON 금지), `feed_type`(WEBSOCKET|REST_POLLING), `channel`(예: `H0STCNT0`, `aggTrade`), `symbol`, `required_depth`, `timeout_sec`, `is_active`, `endpoint_url`.
`shared/data_library/live_feed_prober.py`는 **측정 기반**: ① `live_connection` 선언 ② 런타임 구현 여부(`CTrade/connectors|parsers` 모듈 탐색) ③ Normalized 매니페스트 신선도. 등급 `LIVE_STREAMING` / `STANDBY_AUTH_OK`(장외, 비차단) / `CONNECTION_FAILED`. 재지 않은 latency는 None. 결과 `logs/live_feed_diagnostic.json`.

## 8. 명명: 정통 vs 대용

- 정통(`macro_futures`): CME NQ/ES 등 거래소 정규 선물.
- 대용(`macro_proxy`): cTrader CFD/FX 7종 `US_TECH_100, US_500, JAPAN_225, HONG_KONG_50, XAUUSD, XTIUSD, USDJPY`. 봉 시작 시각 자산(`is_bar_start_asset`)으로 분류되어 결합 시 시프트된다.

## 9. 관련 테스트
`tests/test_single_scale_actions.py`, `tests/test_scale_pipeline_mapping.py`, `tests/test_alignment_ratchets.py`, `tests/test_gold_layer_contract.py`, `tests/test_data_vault_ui_parity.py`, `tests/test_candle_core_parity.py`, `tests/test_end_to_end_crypto_pipeline.py`, `CAiModelBaker/tests/test_medallion_full_pipeline.py`, `CAiModelBaker/tests/test_medallion_scale_separation.py`, `CAiModelBaker/tests/test_creon_ref_symbols_pipeline.py`, `shared/tests/test_reference_staleness.py`, `shared/tests/test_data_catalog_manager.py`, `shared/tests/test_candle_resampler.py`, `tests/test_raw_store_health_performance.py`.
