# ✅ CTradeSystem 수정 결과 리포트 — P-1 / P-2 / T-1 ~ T-5 / D-3 · D-6 ~ D-9

| 항목 | 값 |
| :--- | :--- |
| **리포트 종류** | 사전 준비(P-1/P-2) + 결함 수정(T-1~T-5 / D-6~D-9 / D-3) 결과 및 크로스체크 기록 |
| **기준 커밋(수정 전)** | `27e0501` (backup) |
| **수정 일자** | 2026-09-15 |
| **수정 범위** | 데이터 파이프라인 **선행 준비** + Lookahead 결함 + **Gold 단일화** + **Point-in-Time 무결성(bfill 전면 제거 47곳 + stale 상한)** + **Silver 리샘플러 단일화** + **경로 SSOT(데이터 루트 단일화)** + 테스트 격리 결함 |
| **수정 담당** | Cline |
| **연관 문서** | `CODE_REVIEW_20260915_data_pipeline_checklist.csv`, `GOLDEN_BASELINE.json`, `REFERENCE_STALENESS.json` |

---

## 1. 수정 요약

| ID | 제목 | 성격 | 상태 |
| :--- | :--- | :--- | :---: |
| P-1 | 골든 기준선 동결 (스냅샷/모델/백테스트 지문) | 사전 준비 (측정 기준) | ✅ |
| P-2 | 데이터셋 빌더 특성화 테스트 신설 (기존 테스트 0건) | 사전 준비 (회귀 안전망) | ✅ |
| T-1 | `NVDA-PERP` 외 3종 Lookahead 시프트 누락 수정 | 리스크 (미래참조) | ✅ |
| T-1b | 인터벌→봉 길이 계산 로직 중복 통합 | 정리 (동작 보존) | ✅ |
| T-2 | **Gold 계층 진실 확정** — 이중 writer 제거 + `FeatureVault` 폐기 | 리스크 (학습 데이터 오염) | ✅ |
| T-3 | **Point-in-Time 무결성** — bfill(미래값 역류) 제거 + 참조 stale 상한 도입 | **리스크 (Lookahead)** | ✅ |
| D-6 | 테스트가 실계좌 기준선 `account_baseline.json` 을 덮어쓰는 결함 수정 | 리스크 (프로덕션 상태 오염) | ✅ |
| D-7 | 레거시 스크립트의 학습 데이터셋 직접 덮어쓰기 은퇴 처리 | 리스크 (학습 데이터 오염) | ✅ |
| D-8 | `DataCatalogManager` 의 `bfill()` 로 head 결측이 미래값으로 역류 (26일) | **리스크 (Lookahead)** | ✅ |
| D-9 | `data_manager.py` 의 `ffill().bfill()` **43곳** 제거 (일자별 head · 참조/매크로 · crypto 경로) | **리스크 (Lookahead)** | ✅ |
| T-4 | **Silver 리샘플러 단일화** — Vault 자체 리샘플러 제거, 엄격 게이트 공유 | 리스크 (skew·미래참조) | ✅ |
| T-5 | **경로 SSOT** — 절대경로 단일화 + 고아 데이터 루트 병합·제거 (D-3 해소) | 중간 (데이터 분기) | ✅ |
| D-3 | CWD 상대경로로 인한 데이터 루트 2개 분기 | 중간 (데이터 분기) | ✅ |

> **선행 작업의 이유**: `run_dataset_build_pipeline` / `merge_reference_symbols_into_df`
> (Gold 계층 실질 생산자, 약 640줄)을 검증하는 테스트가 **0건**이었고,
> 파이프라인 리팩터링의 회귀를 판정할 기준선도 없었다.
> 이 상태에서 Gold 단일화/경로 SSOT 작업을 진행하면 회귀를 감지할 수 없다.

---

## 2. 변경 내역

### P-1 — 골든 기준선 동결 (`scripts/freeze_golden_baseline.py`, 신규)

리팩터링 **전** 모델 입력과 산출물의 지문을 SHA256 으로 동결. 원본 데이터는 읽기만 하며,
JSON 리포트 1개만 생성한다.

```bash
.venv/bin/python3 scripts/freeze_golden_baseline.py          # 동결
.venv/bin/python3 scripts/freeze_golden_baseline.py --check  # 회귀 판정 (exit 0=동일, 2=차이)
```

동결 실적: **학습 데이터셋 13개 · 배포 모델 37개 · 백테스트 성과 11개**
(예: `069500/data_snapshot_1m.csv` 196,524행/49컬럼, `BTC_10m/BTC_M10_BEST1` 거래 393건 mean=0.1786)

### P-2 — 특성화 테스트 신설 (`CAiModelBaker/tests/test_dataset_builder_characterization.py`, 신규)

기존 동작을 그대로 고정하여 "동작 보존"을 기계적으로 증명 가능하게 함. **14개 테스트.**

| 고정한 계약 | 내용 |
| :--- | :--- |
| `get_snapshot_path` | `data/{coin_code}/data_snapshot_{interval}.csv` + 키 우선순위(`SYMBOL_UPBIT` > `TARGET_STOCK` > `SYMBOL`) |
| ref 결합 no-op | `ref_symbols` 부재/빈 DF → 원본 객체 불변 |
| 해외선물 시프트 | 09:00 봉 종가는 **09:01 에만** 매칭, 09:00 시점 NaN (Lookahead 차단) |
| 크립토 무시프트 | `binance_extracted` 캔들은 시프트 없음 (24/7 연속 시계열) |
| volume/bid_vol | 결측 시 `0.0`, bid ratio 부재 시 `volume × 0.5` |
| 무결성 게이트 | KRW 데이터셋 `benchmark_btc_close` 누락/전량결측 → 차단 (기존 테스트 미커버 분기) |

### T-1 — 봉 시작 시각 판정 단일화 (`shared/config/symbols_master.py`, `shared/data_library/feature_composer.py`)

**결함**: `feature_composer.py` 가 해외선물 판정을 하드코딩 리스트
`["NQ","ES","YM","RTY","CL","GC"]` 로 수행하여, 레지스트리에 `GLOBAL_FUTURES` 로
정식 등록된 **`NVDA-PERP` / `US10Y` / `WTI` / `GOLD`** 의 캔들 시프트가 누락되었다.
`symbol_relations.json` 에 `NVDA-PERP` 가 396500 타겟의 `macro_futures` 로 등록되어
있어 실사용 경로였고, `web_dashboard/main.py:2182` 가 이를 `compose()` 로 전달한다.

```python
# shared/config/symbols_master.py (신규 — Lookahead 방어 단일 판정 지점)
def is_bar_start_asset(symbol: str) -> bool:
    # 1차: 레지스트리 market_type == "GLOBAL_FUTURES"  → NVDA-PERP, US10Y, WTI, GOLD 편입
    # 2차: FUTURES_BAR_START_TOKENS fallback           → YM, RTY, CL, GC 회귀 방지
```

```python
# shared/data_library/feature_composer.py
- is_overseas_ref = any(k in r_sym for k in ["NQ","ES","YM","RTY","CL","GC"])
+ if is_bar_start_asset(r_sym):
+     sub["timestamp"] = sub["timestamp"] + _bar_duration(r_int)
```

- 두 판정을 **합집합(union)** 으로 사용하는 이유: 마스터 미등록 티커는
  `get_symbol_info` 가 `US_ETF` 로 오분류하므로 레지스트리만 쓰면
  `YM/RTY/CL/GC` 의 기존 시프트가 회귀한다.
- 레거시 토큰 목록은 **원본과 정확히 동일하게 유지**했다. `VX/ZN/TN` 확장은
  `"AMZN" ⊃ "ZN"` 같은 부분문자열 오분류를 유발하므로 금지하고, 신규 자산은
  `market_type="GLOBAL_FUTURES"` 등록으로만 편입하도록 주석에 명시 (기존 L-2 교훈 준수).
- 중복되어 있던 인터벌→봉 길이 계산 로직을 `_bar_duration()` 단일 함수로 통합.

### T-2 — Gold 계층 진실 확정: 이중 writer 제거 + 미배선 사본 폐기

"어느 경로가 진실인가"에 대해 확정한 계약은 **코드**(`shared/data_library/__init__.py`)와
**문서**(`ARCHITECTURE.md` Gold 계층 진실 계약표) 양쪽에 동일하게 선언했다.

| 구분 | 유일한 담당 | 출력 |
| :--- | :--- | :--- |
| Bronze | `IngestionManager` + `download_*` | `data/historical/**` |
| Silver | `shared/candle_resampler` + `CandleVault` | `cache/candles/*.parquet` |
| **Gold (학습 데이터셋 생산)** | **`run_dataset_build_pipeline`** | `data/{SYM}/data_snapshot_{INT}.csv` |
| **Gold (피처 계산)** | **`run_feature_engineering`** | (in-memory) |
| Gold (조인 미리보기) | `FeatureComposer.compose` | `data/{SYM}/data_vault_preview_{INT}.csv` |
| ~~Gold~~ | ~~`FeatureVault`~~ | ⚠️ DEPRECATED |

**조치 1 — 학습 데이터셋 이중 writer 제거**

`POST /api/data_vault/compose` (웹 UI `온디맨드 조립`) 가 `get_snapshot_path()` 결과에
`to_csv` 하여 **학습 데이터셋을 덮어쓰고 있었다.** compose 결과는 틱 파생 지표(`cvd`,
`trade_bid_ratio`), 매크로 선물, 펀딩비, 결측 보간, 무결성 게이트가 없는
조인 미리보기이므로, UI 버튼 한 번으로 49컬럼 학습셋이 ~10컬럼으로 **조용히 대체**될 수 있었다.

```python
# CAiModelBaker/web_dashboard/main.py — 변경 후
preview_path = os.path.join(
    os.path.dirname(get_snapshot_path(config)) or "data",
    f"data_vault_preview_{interval}.csv",
)
composed.to_csv(preview_path, index=False)     # 학습 데이터셋이 아닌 미리보기로 기록
```

**조치 2 — 파생 발견 D-7: 레거시 스크립트 은퇴**

계약 테스트가 실제 이중 writer 를 추가로 탐지했다.
`scripts/add_funding_rate.py` 는 `BTC/30m` 하드코딩 경로로 `data_snapshot_30m.csv` 를
직접 덮어쓰고 초기 구간을 `bfill` 채움한다(문서 정책 'bfill 전면 금지'와 상충).
로직은 이미 파이프라인에 통합되어 있으므로(`data_manager.py:581`) **비파괴 은퇴** 처리:

```python
def main(force: bool = False):
    if not force:      # 기본 실행 차단
        print("🛑 [RETIRED] 학습 데이터셋을 직접 덮어쓰므로 실행이 차단되었습니다. ...")
        return 1
```

**조치 3 — `FeatureVault` 폐기 (실측 근거)**

배선 여부 결정을 위해 동일 입력으로 양쪽을 비교한 결과:

```
생산(run_feature_engineering) target_cvd_momentum 마지막 5개: [-0.029820 -0.002318  0.025605  0.053943  0.082688]
FeatureVault                   target_cvd_momentum 마지막 5개: [ 0.106972  0.135303  0.163785  0.192404  0.221149]
최대 절대 차이: 0.1384615384602246   (동일 여부: False)
```

원인은 구조적이다 — `FeatureVault` 는 모든 피처를 **불변 `candle_df` 기준**으로 계산하므로
`target_cvd_momentum` ← `target_trade_bid_ratio` 같은 **피처 간 의존성이 해소되지 않는다.**
생산 경로는 `_execute_feature_tasks` 의 **의존성 위상 레벨 스케줄링(M-3)** 으로 선행 피처를
df 에 반영한 뒤 계산한다. 여기에 KRX 세션 분리 연산·`is_warmup`·`FEATURE_ALIASES` 정규화도 없다.

→ **배선하면 Train/Serve Skew 를 새로 만든다.** 따라서 폐기하고 단일 경로로 수렴:

- `feature_vault.py`: 모듈 docstring 에 폐기 사유 명시 + `__init__` 에서 `DeprecationWarning`
- `feature_composer.py`: `FeatureVault` import 및 죽은 인스턴스화 **제거**
  (`self.feature_vault` 를 읽는 외부 호출자 0건 확인 후 진행, 하위 호환용 인자는 유지)

**조치 4 — 계약을 코드로 강제** (`tests/test_gold_layer_contract.py`, 8 tests)

| 테스트 | 강제하는 계약 |
| :--- | :--- |
| `test_feature_composer_is_pure_no_file_write` | 조인기는 순수 함수 (파일 쓰기 API 0건) |
| `test_training_snapshot_has_single_writer` | 학습셋 활성 writer 는 배치 빌더 단일 |
| `test_retired_snapshot_writer_is_guarded` | 은퇴 도구는 반드시 `--force` 차단 가드 보유 |
| `test_single_writer_allowlist_is_still_valid` | 허용 목록 자기검증 (계약 부식 방지) |
| `test_compose_endpoint_does_not_overwrite_training_snapshot` | 엔드포인트가 `snap_path` 류에 쓰지 않음 (AST 검사) |
| `test_feature_vault_emits_deprecation_warning` | 폐기 경고 |
| `test_feature_vault_is_not_used_in_production_paths` | 프로덕션 의존 0건 |
| `test_composer_does_not_instantiate_feature_vault` | 미배선 사본 부활 금지 |

### T-3 — Point-in-Time 무결성: bfill(미래값 역류) 제거 + 참조 stale 상한 도입

**측정 → 근거 → 수정** 순서를 지켰다. 먼저 감사 도구를 신설해 실태를 확인했다.

**① 측정 (신규 `scripts/audit_reference_staleness.py`, 읽기 전용)**

동결된 스냅샷에서 "동일값 연속 반복 구간(= stale 전파 하한)"을 측정한 결과:

| 스냅샷 | 참조 컬럼 | 최장 stale(하한) | `>7일` 구간 비율 |
| :--- | :--- | ---: | ---: |
| 069500/1m | `ref_449190_close` | **624.0h (26.0일)** | 3.28% |
| 069500/1m | `ref_samsung_close` | 336.0h (14.0일) | 3.28% |
| 069500/1m | `ref_hynix_close` | 312.0h (13.0일) | 3.24% |
| 069500/10s | `ref_hynix_close` | 221.2h (9.2일) | **20.51%** |
| 069500/10s | `ref_samsung_close` | 216.2h (9.0일) | 18.49% |

**② 원인 규명 — 정책이 아니라 `bfill` 이었다**

원천(`creon/candle_1m/A449190_1m.csv`)은 정상이며 결측갭도 명절 연휴(5~7일)뿐이었다.
그런데 `ref_449190_close` 값이 **원천 최초값(2024-08-21, 15,595.0)과 정확히 일치**하면서
데이터셋 시작(2024-07-26)까지 채워져 있었다 → **미래값 역류(bfill)**.

```
shared/data_catalog_manager.py::apply_fallback_to_dataframe
  # 4. 기본 수치 보간 fallback
  elif pd.api.types.is_numeric_dtype(df[col]):
      df[col] = df[col].ffill().bfill()      ← ref_* 등 카탈로그 미등록 컬럼이 여기로 낙하
```

- `ref_*` 컬럼은 카탈로그에 소스가 없어 `policy="NONE"` → 기본 분기로 낙하
- **069500/1m 수치 컬럼 48개 중 38개**가 이 분기 대상
- `ARCHITECTURE.md:63` 은 "과거 구간 역류를 유발하는 `bfill()`은 전면 금지"를 선언 → **코드가 정책 위반**

**③ 수정**

| 결함 | 수정 |
| :--- | :--- |
| **D-8 (critical)** `DataCatalogManager` bfill 4곳 | 전량 제거 → `ffill()` 만. head 결측은 **NaN 으로 보존**. 설계 의도를 docstring 에 명시 |
| **D-1 (medium)** `tolerance` + `ffill` 조합이 tolerance 무력화 | 공용 모듈 `shared/data_library/reference_staleness.py` 신설. **cap 이 tolerance 를 대체하는 유일 상한** |
| 이중 구현 (배치 7일 vs 온디맨드 3일) | 두 구현이 **동일 함수**(`merge_asof_with_stale_cap`)를 사용 — T-4b 의 핵심도 함께 해소 |

```python
# 자산군별 stale 상한 (정상 휴장은 허용하되 결측 은폐는 차단)
CRYPTO 1일 · FX 3일 · GLOBAL_FUTURES 4일 · KRX 7일 · 일봉계열 7일
```

적용 지점 4곳: 배치 `merge_reference_symbols_into_df`, KIS 빌더 `futures_close`/`spot_close`/NQ·ES,
온디맨드 `FeatureComposer.compose`(참조 + 매크로).

**④ 동작 변경 고지**

본 수정은 **의도된 동작 변경**이다. 다음 데이터셋 재빌드 시 참조/매크로 컬럼 값이 달라진다:

- 참조 원천이 비는 구간이 **frozen price → NaN** 으로 바뀐다 (모델이 '미지'로 인식)
- `DataCatalogManager` 가 채우던 head 결측이 NaN 으로 남는다
- → **모델 재학습 및 백테스트 재검증이 필요**하다. `scripts/freeze_golden_baseline.py --check` 가
  재빌드 전후 비교 수단을 제공한다 (현재는 데이터 미재빌드이므로 drift 0 유지)

**⑤ D-9 — `data_manager.py` 의 `bfill` 43곳 제거 (동일 정책 전면 적용)**

D-8 로 정책을 세운 뒤, AST 전수 조사로 같은 위반이 데이터셋 빌더에도 남아 있음을 확인했다.

| 그룹 | 대상 | 사이트 수 | 조치 |
| :--- | :--- | ---: | :--- |
| **B** 참조/매크로 | `binance_*`, `coinbase_*`, `benchmark_btc_close`, `upbit_usdt_close` 등 | ~33 | `ffill()` 만 (거래량류만 `.ffill().fillna(0.0)`) → head NaN 보존 |
| **A** 타겟 자체 OHLC | `upbit_open/high/low/close` (일자별 그리드 head) | 9 | `ffill()` 로 두고, `pd.concat(dfs)` 이후 **연속 프레임에서 직전 세션 종가를 ffill** |
| 기타 | `binance_funding_rate` | 1 | `.bfill().fillna(0.0)` → `.ffill().fillna(0.0)` |

**그룹 A 를 단순 제거하지 않은 이유**: 일자별 그리드의 head 를 `bfill` 하던 것은
"첫 체결 이전 봉"을 *그 날의 미래 종가*로 채우는 **Lookahead** 였다. 그러나 단순히 지우면
타겟 가격이 NaN 이 되어 라벨까지 오염된다. 정답은 **직전 세션 종가를 상속**하는 것이며,
이는 T 시점에 이미 알려진 값이므로 Point-in-Time 무결성을 지킨다.
(구현: 일자별 `ffill` → 연속 프레임에서 1회 더 `ffill`. 데이터셋 최초 구간만 NaN)

**검증**
```bash
# AST: 실제 bfill 호출 0건 (DataCatalogManager · data_manager.py 모두)
# diff: bfill 제거 43건 / 추가 0건, 그 외 변경은 T-3 stale cap 관련뿐
```
재도입 방지 계약 테스트 2건 신설: `test_dataset_builder_has_no_bfill_call`,
`test_data_catalog_manager_has_no_bfill_call`

### T-4 — Silver 리샘플러 단일화 (Vault 자체 구현 제거)

**문제**: 리샘플러가 2벌이었고, **한쪽에만 엄격 게이트**가 있었다.

| 구현 | 사용처 | 하방 리샘플 차단 | 비배수 차단 |
| :--- | :--- | :---: | :---: |
| `shared.candle_resampler.resample_candles` | Baker 배치 (`data_manager.py`) | ✅ | ✅ |
| `CandleVault.resample_candles` (자체 구현) | Vault / 웹 UI / CTrade | ❌ | ❌ |

→ 같은 "Silver" 계층인데 불변식이 달라, 1m 원천에 `10s` 를 요청하면
**1분봉을 10초 라벨로 위장**한 봉이 조용히 생성되었다 (라벨-확정시각 불일치 = 미세 Lookahead).

**실증 (수정 전/후)**
```
수정 전(시뮬레이션): 10s 라벨로 5행 생성 → ['09:00:00','09:01:00','09:02:00']
수정 후:            LookaheadInversionError → 즉시 차단 ✅
```

**조치**
- `CandleVault.resample_candles` → 공용 리샘플러 **위임**
  (`source_interval="1m"` 고정 — `_find_raw_1m_data` 가 1m 원천을 보장)
- 중복 유틸 `parse_interval_to_pandas_freq` 제거 → `get_resample_freq_str` 단일화
- Vault 고유 정리(종가 결측 봉 제거)는 리샘플링 계약이 아니므로 `_drop_non_trading_bars()` 로 분리 유지
- 1m 요청은 원천 그대로 반환 (원본 값 100% 보존)
- 공용 리샘플러의 **tz-aware 보존 버그 수정** — 종전 `astype('datetime64[ns]')` 가
  KST 09:00 을 UTC 00:00 으로 밀어버렸다

**동작 변경 (T-4)**

| # | 내용 |
| :--- | :--- |
| ① | 하방 리샘플(예: 1m→10s) → 조용한 오작동 → **`LookaheadInversionError` 즉시 차단** |
| ② | `_ratio` / `_mean` / `_pct` / `_rate` 집계 `last` → **`mean`** (공용 규칙이 Silver SSOT) |
| ③ | tz-aware 입력의 tz 보존 |

**검증** (`shared/tests/test_resampler_unification.py`, **19 tests**)

| 테스트군 | 건수 | 고정하는 계약 |
| :--- | ---: | :--- |
| 게이트 활성 | 3 | Vault 경로에서도 하방 리샘플·비지원 인터벌이 차단됨 (**T-4 이전에는 실패**) |
| 1m 항등 | 1 | 1m 요청은 원천 값 100% 보존 |
| 동일 구현 증명 | 7 | 3m/5m/10m/15m/30m/1h/1d 에서 Vault 결과 == 공용 리샘플러 결과 |
| 집계 계약 | 2 | OHLCV(first/max/min/last/sum) + ratio=**mean** (**종전 last → 이 테스트도 사전 실패**) |
| tz 보존 | 2 | KST 09:00 이 UTC 로 밀리지 않음 |
| 기타 | 4 | 결측봉 제거 · timestamp 컬럼 누락 · 빈 입력 |

### T-5 — 경로 SSOT: 데이터 루트 단일화 (D-3 해소)

**문제**: `f"data/..."` CWD 상대경로가 **50곳**. 운영 스크립트(`run_baker.sh`)가 `cd CAiModelBaker` 후
실행하므로 정상이었지만, 저장소 루트에서 실행하면 쓰기가 `<repo>/data/...` 로 흘러나가
**데이터 루트가 2개로 분기**했다.

- 실측: 루트 `data/` 에 고아 **32파일 3.5MB**
- 최악의 시나리오: 파이프라인은 고아 루트에 데이터셋을 쓰고 `CandleVault` 는 정규 루트만 읽어
  **조용한 데이터 불일치** 발생 (T-3 감사에서도 "참조 원천이 왜 비는가"의 배경)

**조치**

| # | 내용 |
| :-- | :--- |
| ① | `shared/data_library/paths.py` 신설 — **절대경로 SSOT** + `CTRADE_DATA_ROOT`/`CTRADE_DEPLOY_ROOT` 환경 재정의 |
| ② | 경로 함수 3개 위임 — `get_snapshot_path` / `get_params_path` / `get_model_path` → `dpaths.*` |
| ③ | CWD 상대 f-string **50곳** 변환 (`data_manager.py` 42, `creon_collector.py` 8) |
| ④ | 중복 해석 4곳 추가 통일 — `feature_screener.py`, `run_5_rounds_research.py`, `legacy/app_day.py` |

> `creon_collector.py` 는 **32-bit Windows + 메모리 <1MB 설계**라 shared 패키지(pandas/numpy)를
> 끌어오지 않고 자체적으로 루트를 해석하도록 처리했다.

**고아 데이터는 "삭제"가 아니라 "비파괴 병합"으로 처리**

Baker 값을 **절대 덮어쓰지 않고**(root 에만 있는 행만 추가하는 union) 병합:

| 결과 | 파일 수 | 내용 |
| :--- | ---: | :--- |
| 신규 생성 | 1 | `coinbase ETH-USD-10m-2026-09-08.csv` (정규 루트에 아예 없던 파일) |
| **행 복원** | 7 | `BTCUSDT-openInterestHist-2026-08-31` 288→**504행**, `...-09-01` 244→**504행**, `BTC-USD-10m` ×5 144→**211행** |
| 동일(중복) | 24 | 변경 없음 |

→ 부분 다운로드로 누락되어 있던 **Bronze 원천 약 928행을 복구**했다.
병합 후 32/32 파일의 모든 행이 정규 루트에 포함됨을 검증한 뒤 고아 트리를 제거했다.

**검증** (`shared/tests/test_data_paths.py`, **16 tests**)

| 테스트군 | 내용 |
| :--- | :--- |
| 루트 계약 | PROJECT/BAKER/DATA/DEPLOY 절대경로·중첩 관계 |
| Gold·Artifact 계약 | 스냅샷·메타 사이드카·세션 vs 레거시 params/model, 미리보기 분리(T-2 계약) |
| **CWD 독립성** | `/`, `/tmp`, 저장소 루트 3곳에서 subprocess 실행 → **동일 경로** |
| 환경 재정의 | `CTRADE_DATA_ROOT` 로 데이터 루트 변경 |
| **D-3 재발 방지** | AST 기반으로 CWD 상대 f-string(`f"data/`, `f"deploy/`)이 프로덕션에 **0건**임을 강제 |


---

## 3. 검증 결과

| 검증 항목 | 명령 | 결과 |
| :--- | :--- | :--- |
| 전체 테스트 스위트 | `pytest CTrade/tests shared/tests CCommander/tests tests` + `pytest CAiModelBaker/tests` | **342 passed / 0 failed** (suite1 274 + suite2 68) |
| 신규 특성화 테스트 (P-2) | `pytest CAiModelBaker/tests/test_dataset_builder_characterization.py -v` | **16 passed** |
| T-1 회귀 테스트 | `pytest tests/test_futures_alignment_parity.py -v` | 5 passed |
| T-2 Gold 계약 테스트 | `pytest tests/test_gold_layer_contract.py -v` | **10 passed** (D-9 bfill 금지 2건 포함) |
| **T-3 stale 상한 정책** | `pytest shared/tests/test_reference_staleness.py -v` | **13 passed** |
| **T-3 PIT 무결성 (bfill 금지)** | `pytest shared/tests/test_data_catalog_manager.py -v` | **9 passed** (신규 PIT 계약 3건 포함) |
| **T-3 실측 감사** | `scripts/audit_reference_staleness.py` | 최장 stale 624h(26일)·10s 20.5% 규명 → `REFERENCE_STALENESS.json` |
| **T-4 리샘플러 단일화** | `pytest shared/tests/test_resampler_unification.py -v` | **19 passed** (게이트 활성 3건은 T-4 이전 실패) |
| **T-5 경로 SSOT** | `pytest shared/tests/test_data_paths.py -v` | **16 passed** (CWD 독립성·env 재정의·D-3 재발 방지) |
| **D-9 bfill 전수 제거** | `grep -c '\.bfill('` + AST | data_manager.py **43건 → 0건**, DataCatalogManager **4건 → 0건** |
| **T-5 고아 데이터 병합** | `merge_orphan_data.py` + 포함관계 검증 | 신규 1건·행 복원 7건(≈928행) → **32/32 포함 검증 후 고아 트리 제거** |
| D-7 은퇴 가드 실증 | `python scripts/add_funding_rate.py` | **exit=1 (차단), 학습 데이터 무변경** |
| 골든 기준선 회귀 | `scripts/freeze_golden_baseline.py --check` | **drift 0** (snapshots 13 / models 37 / backtests 11 전부 동일) |
| 프로덕션 상태 무결성 | 전체 스위트 실행 전후 `account_baseline.json` SHA256 | **무변경** (D-6 수정 효과 확인) |

### T-1 회귀 테스트 유효성 실증 (수정 전/후 비교)

수정 전 판정 로직을 강제 주입하여 결함이 실재했음을 확인:

```
수정 전(레거시)  09:00 ref_nvda_close = 125.0   <- 09:00 봉 종가가 09:00 시점에 누출 (Lookahead)
수정 후(현재)    09:00 ref_nvda_close = nan
수정 후(현재)    09:01 ref_nvda_close = 125.0   <- 봉 확정 시각에 정상 반영
```

> ⚠️ `scripts/run_tests.sh` 는 `CTrade/` 와 `CAiModelBaker/` 가 동일 패키지명 `core` 를
> 사용하므로 **스위트 분리 실행이 필수**다 (기존 L-8). `pytest -q` 단일 실행으로 대체 불가.

---

## 4. 특성화 테스트로 발견된 신규 결함 (미수정 — 인지 필요)

### ⚠️ `tolerance` 파라미터가 `ffill()` 에 의해 무력화됨

`merge_reference_symbols_into_df` (배치) 와 `FeatureComposer.compose` 모두
`merge_asof(..., tolerance=N일)` 직후 `ffill()` 을 적용한다.
`ffill()` 이 tolerance 초과로 생긴 NaN 을 **앞값으로 다시 채우므로
`ref_*_close` 컬럼에서는 tolerance 가 사실상 무한**이 된다.

| 컬럼 | 후처리 | tolerance 실효성 |
| :--- | :--- | :--- |
| `ref_*_close` | `ffill()` | ❌ **무력화** (8일 경과해도 마지막 종가 전파) |
| `ref_*_volume`, `ref_*_bid_vol` | `fillna(0.0)` | ✅ 작동 (초과 시 0.0) |

- 배치: `tolerance=7일` / `FeatureComposer: tolerance=3일` → 값이 다른데도 close 에서는 둘 다 무의미
- **함의**: T-3(tolerance 정책 분기)은 "tolerance 값 조정"만으로 불충분하며,
  ffill 전파에 상한(**stale age**)을 거는 방식으로 접근해야 한다.
- 계약 고정 위치: `test_reference_merge_tolerance_is_nullified_by_ffill`

### ✅ D-6 (high, **수정 완료**) — 테스트가 추적 대상 파일 `account_baseline.json` 을 덮어쓰던 결함

검증 과정에서 전체 스위트 실행 **중** `account_baseline.json` 이 변경되는 것을 발견하여 원인을 특정했다.

| 항목 | 내용 |
| :--- | :--- |
| **유발 테스트** | `CTrade/tests/test_integrated_engine_kis.py::test_integrated_kis` |
| **경로** | 실 `STradeEngine` 을 2초 구동 → `PortfolioTracker.calculate_portfolio()` → `update_baseline_rollover()` → `save_baseline()` |
| **결과** | 프로젝트 루트의 `account_baseline.json` (git 추적 대상) 이 **실계좌 잔고로 덮어써짐** |
| **부작용** | `daily_date` 롤오버가 **09:00 KST 계약을 무력화**하고 테스트 실행 시각(예: 00:39)에 발생 → 계좌 ROI 기준점 시각 왜곡. 또한 테스트가 실 브로커 API 에 연결됨 |

**원인 특정 방법 (통제 실험)**
```bash
# daily_date 를 강제로 과거로 되돌린 뒤 스위트별 실행 → 어느 스위트가 파일을 바꾸는지 판정
→ CTrade/tests  ⚠️ 변경 유발   /  shared·CCommander·root tests  ✅ 변경 없음
# 파일별 재실행으로 단일 테스트 특정
→ CTrade/tests/test_integrated_engine_kis.py  ⚠️ 변경 유발
```
> 피해 파일은 해시 대조 후 `git checkout -- account_baseline.json` 으로 **원복 완료**
> (골든 기준선 `--check` 재확인 결과 drift 0 유지).

**수정 내용** (`CTrade/tests/test_integrated_engine_kis.py`)

```python
# 1) 기준선 저장 경로를 임시 디렉토리로 격리 (load_baseline/save_baseline 의 유일한 경로 병목)
with tempfile.TemporaryDirectory() as tmpdir:
    with patch("utils.portfolio_tracker._get_baseline_path",
               return_value=os.path.join(tmpdir, "account_baseline.json")):
        asyncio.run(_run_integrated_kis())

# 2) 자체 가드 — 프로덕션 기준선이 변경되면 테스트가 즉시 실패
before, after = _sha256_or_none(_REAL_BASELINE_PATH), ...
assert before == after, "테스트가 프로덕션 기준선 파일을 변경했습니다 ... [D-6]"
```

- `__main__` 수동 실행 경로에도 동일 격리를 적용
- `PortfolioTracker` 의 이력은 `account_baseline.json` 내부 `history` 키에 저장되므로,
  `_get_baseline_path()` 단일 병목 격리로 모든 쓰기가 차단됨을 확인

**검증 (통제 실험)**

| 단계 | 방법 | 결과 |
| :--- | :--- | :--- |
| 수정 전 | `daily_date` 강제 과거화 → 해당 테스트 실행 | ⚠️ 파일 변경 유발 (결함 재현) |
| 수정 후 | 동일 실험 재실행 | ✅ SHA256 불변 |
| 가드 유효성 | 격리 patch 를 무력화(no-op) 후 실행 | ✅ `AssertionError` 발생 (가드 작동 실증) |
| 전체 회귀 | suite1 213 + suite2 66 | ✅ **279 passed / 0 failed** |
| 부수 확인 | 전체 스위트 실행 전후 기준선 해시 | ✅ 무변경 (D-6 완전 해소) |

> 참고: 해당 테스트는 실 `STradeEngine` 을 구동하므로 Coinbase/KIS/Hyperliquid **실 연결**이
> 발생한다. 이는 테스트의 검증 의도(틱 수신)이므로 유지했으나,
> CI/기본 스위트에서 live 연결을 피하려면 opt-in 스킵(자격증명 부재 시 skip) 도입을 별도 검토할 것을 권장한다.


---

## 5. 남은 작업 (우선순위)

| ID | 작업 | 성격 | 선행 조건 |
| :--- | :--- | :--- | :--- |
| T-4b | 경계 결합 2벌 단일화 **잔여분** — 배치 `merge_reference_symbols_into_df` 의 해외선물 판정이 아직 파일명 토큰(`es`,`nq`…) 기반 (T-1 의 `is_bar_start_asset` 과 이중) | 리스크(skew) | T-4 ✅ |
| D-5 | 외부 핸드북을 `docs/reference/` 로 이동 + 출처 표기 | 낮음 | — |
| D-6b | 실 연결 테스트의 opt-in 스킵 도입 (자격증명 부재 시 skip) | 정리 | — |
| — | 물리 폴더 `data/{bronze,silver,gold}` 분리 (이제 `paths.py` 한 곳만 수정하면 됨) | 미용 (알파 기여 0) | T-5 ✅ |

> ✅ 본 라운드에서 **T-1 · T-2 · T-3(+D-8·D-9) · T-4 · T-5(+D-3)**, 그리고 **D-6/D-7** 이 완료되었습니다.
> 데이터 파이프라인의 **미래참조·불변식 불일치 경로 5종이 전부 제거**되었습니다:
> ① 하드코딩 시프트 누락(T-1) ② 학습 데이터셋 이중 writer(T-2) ③ `bfill` 미래값 역류(D-8·D-9)
> ④ 무한 `ffill` 전파(D-1) ⑤ Silver 리샘플러 2벌·게이트 부재(T-4)
> 여기에 **데이터 루트가 2개로 분기하던 경로 문제(T-5/D-3)** 까지 해소되어 단일 루트로 수렴했습니다.
>
> ⚠️ **다음 재빌드 시 데이터셋이 이전과 달라지므로 모델 재학습·백테스트 재검증이 필요합니다.**
> (데이터 루트는 `CAiModelBaker/data/` 단일 — 향후 `paths.py` 의 `CTRADE_DATA_ROOT` 로 재배치 가능)

## 6. 미래참조(Lookahead) 표면 전수 점검 결과

`grep` + `AST` 전수 조사 결과, 수정 후 남은 `bfill` 호출은 **0건**입니다.

| 모듈 | 수정 전 | 수정 후 |
| :--- | ---: | ---: |
| `CAiModelBaker/core/data_manager.py` | 43 | **0** |
| `shared/data_catalog_manager.py` | 4 | **0** |
| `shared/data_library/feature_composer.py` | 0 (`ffill` 만 사용) | 0 |

재도입 방지 계약 테스트가 상시 검증합니다:
- `tests/test_gold_layer_contract.py::test_dataset_builder_has_no_bfill_call`
- `tests/test_gold_layer_contract.py::test_data_catalog_manager_has_no_bfill_call`
- `shared/tests/test_data_catalog_manager.py::test_no_bfill_remains_in_data_catalog_manager`

> 참고: `bfill` 문자열이 **주석/문서**에 남아 있는 것은 허용됩니다 (설계 의도 기록 목적).
> 계약 테스트는 AST 로 **실제 호출만** 검사합니다.

### 남은 미래참조·불변식 위험 (0 아님)

| 항목 | 내용 | 상태 |
| :--- | :--- | :--- |
| ~~`CandleVault` 자체 리샘플러~~ | ~~엄격 게이트(하방 리샘플·비배수) 우회~~ | ✅ **T-4 로 제거** (공용 리샘플러 위임) |
| 배치의 해외선물 판정 | `merge_reference_symbols_into_df` 는 파일명 토큰(`es`,`nq`…)으로 시프트를 판정 — T-1 의 `is_bar_start_asset` 과 이중 | **T-4b 잔여분** (기능 결함 아님, 일관성) |
| 일봉 → 분봉 fallback | `combine_first` 로 사후 결합 (stale cap 우회 가능), 영향 소 | 관찰 대상 |
