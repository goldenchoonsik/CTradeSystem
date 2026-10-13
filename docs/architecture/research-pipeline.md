# Research Pipeline (CAiModelBaker: Feature Store → AutoLab → Deploy)

상위 규칙 L-*, R-*은 [../ARCHITECTURE.md](../ARCHITECTURE.md) §3.

## 1. 대시보드 구조 (`CAiModelBaker/web_dashboard/`)

| 권역 | 탭 | 역할 |
| :--- | :--- | :--- |
| GLOBAL INFRA | Data Vault | 전역 자산 수집·정규화·참조 관계(Raw / Normalized / Features 서브탭). 프로젝트 무관 |
| PROJECT LAB | Feature Store | 참조 종목·Gold 스냅샷 → 사정거리 → 스크리닝 → 활성 지표 (4단계, 아래 §2) |
| PROJECT LAB | AutoLab | WFA/홀드아웃 분할, 자동 연구 실행, 실시간 리더보드 |
| PROJECT LAB | Performance | 모델/후보 백테스트 차트 비교, 배포 |

진행률·로그는 WebSocket `/ws/logs`. 백엔드 `main.py`의 무거운 스캔은 `run_in_executor`로 이벤트 루프 밖에서 실행.

## 2. Feature Store 4단계 (이 순서가 데이터 인과 순서)

1. **참조 종목 & Gold 스냅샷**: 팩/참조 부착(`/api/symbols/relations/attach_group` 등) → `⚡ Gold 스냅샷 빌드`.
2. **사정거리 & 물리 가이드**: `CAiModelBaker/core/physics_constraint.py`가 실측 마찰비·ATR·MFE/MAE 분위수로 최소 TP/SL과 최소 사정거리(first passage)를 산출 → `TARGET_HORIZON_CONFIG` 확정. 하드코딩 하한(12/18) 없음.
3. **스크리닝 & 지표 선택**: 2단계 지평 기준 미래수익률로 IC 측정, Top IC 선정, 프리셋.
4. **활성 지표 목록**: 선택 지표·파라미터 저장 → `config_snapshot.json`(학습 후 `model/feature_spec.json`).

> **🛡️ 미저장 변경사항 가드 (`ensureProjectSavedBeforeAction`)**:
> 스크리닝·스냅샷 빌드·오토랩 등 디스크 프로젝트 파일(`config_snapshot.json`)을 직접 읽는 작업은 UI 미저장(`FeatureState.isDirty`) 상태 시 사전 확인을 강제한다. 확인 시 자동 저장 후 실행, 취소 시 작업을 즉시 중단하여 설정 불일치를 원천 차단한다.

## 3. 지표 레지스트리 (`shared/features/`)

- `feature_registry.py`: `_compute_*` 함수 + `DEFAULT_FEATURE_REGISTRY`(메타: `name`, `category`, `market_scope` = all|crypto|krx|us, `is_regime`, `is_trigger`, 기본 params). `validate_feature_registry`가 기동 시 린트.
- `feature_engine.run_feature_engineering`: 연구·실전 공용. 의존성 위상 레벨별 병렬 계산(결과 결정적). KRX는 날짜별 독립 계산(`_run_feature_engineering_krx`) — 전일 데이터가 당일 롤링에 섞이지 않음. 매일 장 초반은 웜업.
- `feature_config.py`: 활성 지표·파라미터·패널티 로드/저장.
- 지표 파라미터는 학습 중 고정(Optuna 탐색 대상 아님). 단위 접미사: `*_mins`(시간 → 봉수 자동 환산), `*_bars`(봉수 고정), `*_days`(일봉 T-1 확정치). 레거시 `candles/period/window`는 헬퍼가 흡수.
- 입력 컬럼 조회 `_get_series` 우선순위: `target_*` → Normalized 물리 컬럼(`close`, `vwap`, `ofi`, `rv`) → `upbit_*` 레거시 → default.
- 자산군 격리: `filter_compatible_features(symbol, ids, session_id)`, `get_incompatible_features`. 연구 루프 탐색 풀에서도 적용. 사전 점검 모달이 부적합 지표를 경고.
- 대표 지표군: 추세/오실레이터(`target_ma_ratio`, `target_rsi_*`), 오더플로우(`target_cvd_*`, `target_trade_bid_ratio`), 마이크로구조(`target_vwap_disparity`, `target_vwap_slope`, `target_ofi_cumsum`, `target_rv_ratio`, `target_large_trade_density`), KRX/ETF/선물(`kospi_basis*`, `stock_basis*`, `etf_nav_disparity`, `overnight_gap_pct`, `krx_session_*`), 크립토(`kimchi_premium`, `coinbase_premium_index`, `binance_funding_rate`, `binance_oi_change_ratio`), 참조(`ref_{key}_*`, `semi_duo_lead_delta`, `top2_lead_momentum`), 시간·체제(`target_hour*`, `target_is_weekend`, `target_is_auction`, `krx_session_time`). 전체 목록은 레지스트리가 원본.
- 실전 미지원(비활성): `binance_futures_spot_vol_ratio`, `binance_liq_cascade_ratio` 등 — 포함 모델은 CTrade가 로드 거부.

## 4. 스크리닝

두 단계, 규칙은 같고 기간만 다르다(탐색 풀·지평·세션/웜업 라벨 마스킹·홀드아웃 경계 공유).
- **1차** (Feature Store, `CAiModelBaker/core/feature_screener.py`): 학습기간보다 긴 역사에서 **재현성** — 거래일 블록별 IC의 t-검정 + BH(q<0.05). Gold만 읽고 홀드아웃 제외. `lookback_period`: project(기본)/3y/2y/1y/all. 제외 지표는 사유 반환. 표준 기본 파라미터로 평가.
- **2차** (AutoLab, `research_loop._run_ic_screening`): 학습구간 `|IC| ≥ ic_threshold`(기본 0.03) — "지금 국면에서 살아 있나".
- 트랙:
  - 연속형: Rank IC. 0 비율 ≥ 40%면 이벤트형으로 분기(3상태 단조성).
  - 트리거(`is_trigger`): 선형 IC 미달이어도 `calculate_conditional_alpha` — 발동 표본 ≥ 15, 조건부 스프레드·승률·Welch t → 등가 IC. 다단 범주형은 "나머지와 가장 다른 수준"을 이벤트로 본다.
  - 체제(`is_regime`): §5.
- 지표 수가 적으면(K ≤ 3) 공집합 제외 모든 부분집합(2^K − 1)을 Trial로 탐색. `effective_min_screening`은 체제로 분류된 수와 죽은 피처를 뺀 가용 풀로 계산.

## 5. 체제(컨텍스트) 지표 게이트 (T-8)

순위 IC는 주기형·U자형을 원리적으로 못 보므로 체제 지표는 IC 대신 **재현성**으로 검정한다 — 무검정 면제는 폐지.
- `shared/features/regime_stability.py::audit_regime_features`: 지표값을 집단화, 시간 순 8블록, 블록 밖에서 배운 집단 효과가 그 블록에서 맞는지(블록 드리프트 제거 후). 합격선 = 라벨 섞기 분포 95분위(손으로 정한 문턱 금지 — 테스트가 강제). 8블록이 해상도 하한.
- 백본 대조군: 같은 알파 조합을 짝수 Trial = 백본 있음 / 홀수 = 없음으로 실행.
- 백본 단독 기준선: Phase 3에서 체제 백본만으로 학습·최종 Test한 성과. 후보는 이를 넘어야 승격(`beats_backbone`). 통과 백본이 0개면 비교 생략.
- 사용자는 Feature Store에서 지표별로 체제/IC 평가를 지정할 수 있다(`regime_features`, 프로젝트별 저장).
- 회귀: `tests/test_regime_stability_ratchets.py`.

## 6. 타깃 사정거리 3계층 (`TARGET_HORIZON_CONFIG`, `CAiModelBaker/core/horizon_registry.py`)

| Tier | 파라미터 | 탐색 위치 |
| :--- | :--- | :--- |
| 0 전략 헌법 | `predict_ahead_candles`, `max_hold_candles` | WFA 바깥 (Y 라벨 확정) |
| 1 거시 | `take_profit`, `stop_loss` (ATR 배수) | Optuna outer |
| 2 미시 | LightGBM 파라미터, `buy_threshold`, `cooldown_minutes` | Optuna inner |

- `mode: locked`(기본): 라벨을 1회 계산해 모든 Trial 재사용. `grid`: 후보 지평(예: 3/6/10/15)을 바깥 루프로 순회해 비교 후 잠금(`POST /api/features/apply_locked_horizon`).
- 스크리닝·튜너도 잠긴 지평을 기준으로 라벨링한다. 초 단위 인터벌은 `interval_seconds`, 일봉(`1d`)은 86,400초·1,440분(권장 5봉/10봉 스윙)으로 환산 표기.
```json
"TARGET_HORIZON_CONFIG": {"mode": "locked", "locked_predict_ahead": 6, "locked_max_hold": 10, "candidate_horizons": [3, 6, 10, 15]}
```

## 7. WFA·홀드아웃 분할

- SSOT: `CAiModelBaker/core/split_standards.py`(정책·상수) + `split_planner.py`(`plan_split`, `scan_daily_bars`, `find_contiguous_trading_segments`, `build_wfa_windows`). API `GET /api/autolab/smart_split`, `GET /api/asrl/timeline`.
- 단위는 절대값(거래일 TD / 봉 / 개월). 비율 분할 폐지.
- `mode='full'`(전체 데이터 80/20) 선택 시에도 Train/Opt/Test 윈도우 크기를 0으로 비우지 않고 해당 분봉 표준 윈도우(1m: 41/10/10 TD 등)를 자동 배정하여 0 입력 및 1일 슬라이딩 윈도우 폭발 차단.
- 무거래·먼지 거래일(거래량 0 비율 > 90%) 자동 제외. 3영업일 이상 결측 갭: `AUTOLAB_CONFIG.allow_gapped_trading_days`가 ON(기본)이면 결측일만 건너뛰고 통합, OFF면 최신 연속 구간 고정.
- WFA 종료와 홀드아웃 사이 `purge_candles`(≥ 사정거리) 강제.
- 홀드아웃 모드: `full`(홀드아웃 전체를 블라인드 OOS, 기본 표준) / `split`(마지막 `final_test_td` 거래일을 독립 최종 Test, 기본 5거래일로 `HOLDOUT_MIN_TRADING_DAYS` 동기화). 스마트 분할 시 Val/Test 실재 날짜 구간 자동 가시화 및 비율 연동.
- 인터벌별 표준(Lookback / Train / Opt / Test / Holdout, 거래일):

| 인터벌 | Lookback | Train | Opt | Test | Holdout |
| :--- | ---: | ---: | ---: | ---: | ---: |
| 1s–10s | 63 | 21 | 5 | 5 | 10 |
| 1m–3m | 126 | 41 | 10 | 10 | 21 |
| 5m–15m | 252 | 82 | 21 | 21 | 41 |
| 30m | 378 | 126 | 31 | 31 | 63 |
| 1h–4h | 504 | 164 | 42 | 42 | 82 |
| 1d | 756 | 252 | 63 | 63 | 126 |

- 가상화폐(Crypto / 24시간 무휴) 전용 표준: sub_minute 21/8/2/2/3 TD, 1m–3m 42/14/4/4/8 TD, 5m–15m 84/28/7/7/14 TD, 30m 168/56/14/14/28 TD, **1h–4h (60m) 540/180/30/30/90 TD (약 1.5년, 1.3만봉 표본 확보)**, 1d 756/252/63/63/126 TD.
- 데이터 부족 시 버킷별 최솟값(`MINIMUMS_BY_BUCKET`, `MINIMUMS_BY_BUCKET_CRYPTO`, `get_minimums_for_bucket`): sub_minute train 8 (크립토 8) / 1m 10 (크립토 7) / 5m 15 (크립토 10) / 30m 20 (크립토 15) / 1h 30 (크립토 30) / 1d 60 TD, 최소 창 수 2–3. 3-Tier 상태(`standard`/`proportional`/`minimum`/`unavailable`)를 UI에 표시.
- **파라미터 주도형 선언적 역산 (Declarative Parameter-driven Snap) [NEW]**: 사용자는 `Train`, `Opt`, `Test`, `WFA 횟수(wfa_window_count)`, `홀드아웃 기간(holdout_td)` 5대 구조 파라미터만 제어하며, 날짜 달력은 실재 거래일 목록에서 역산된 읽기 전용 결과 뷰(Computed View)로 렌더링된다. 레거시 "구간 자동 정렬" 도구(라디오/미리보기)는 전면 폐기되어 구간 상충 및 날짜 덮어쓰기 왜곡을 원천 차단한다.
- 회귀: `CAiModelBaker/tests/test_split_full_mode_standard.py`.

## 8. 점수·판정 가드 (`CAiModelBaker/core/model_engine.py`)

- `compute_wfa_score`: WFA 창 선택과 Optuna 목적함수의 유일한 점수 함수. PF 상한 `WFA_PF_SCORE_CAP`(20, 원값은 `pf_raw`로 보고), 절대 최소 거래 `WFA_MIN_TRADES_ABS`(10, 미달 시 `-90 + trades`). 설정 키 `OPTUNA_CONFIG.pf_score_cap`, `min_trades_abs`.
- 최종 판정: `resolve_holdout_trade_requirement`, `HOLDOUT_MIN_TRADES_ABS`, `HOLDOUT_MIN_TRADING_DAYS`(5거래일), `HOLDOUT_STANDARD_TRADES`(30). 스나이퍼 특례도 절대 하한 적용. 무손실 PF는 `pf_lossless`로 계산불가 표시.
- 죽은 피처 탐지 `detect_dead_features`(컬럼 없음 / 최종 Test 커버리지 < 50% / 상수) → 후보 무효.
- Trial 결과 기록 판정: 1차 홀드아웃 목표 달성 + WFA 견고성 통과(`wfa_robust==True`) 시 `🏆 후보 통과`, 홀드아웃 달성이나 WFA 미달 시 `⚠️ WFA 미달`(과적합 배제), 목표 미달 시 `미달성`.
- WFA 창별 성적(`training_meta.wfa_window_details`): 윈도우 번호, OOS 구간, 수익률, 거래수, 통과 여부를 보존하여 Trial 클릭 상세 모달에 카드 형태로 시각화.
- Validation 거래 0회 Trial은 점수 −999.
- 월환산 표시: `monthly_profit = net × 30 / D`, `monthly_trades = trades × 30.4 / D`.
- 회귀: `CAiModelBaker/tests/test_wfa_score_guard.py`, `test_final_judgment_guard.py`.

## 9. AutoLab 실행 (`CAiModelBaker/core/research_loop.py::run_auto_research`)

- Phase 0: `ensure_gold_snapshot`. Phase 1/2: IC 스크리닝 → 조합 Trial(WFA/Optuna). Phase 3: Top 후보 재학습·홀드아웃·백본 기준선 비교 → 챔피언.
- 전체 지표를 시작 시 1회 계산해 메모리 캐시. Trial마다 `feature_subset`을 학습기·백테스터에 직접 전달(전역 설정 오염 방지).
- 중단(`_research_state["abort"]`)은 Phase 3 루프에서도 감지하고 빈 리더보드를 저장하지 않는다.
- 세션 격리: 프로젝트 전환 시 `reset_research_state(session_id)`, 리더보드 API는 세션 ID 불일치 시 빈 목록.
- 산출물: `runs/<ts>/leaderboard.json`(요약만), `trials/trial_NNN.json`, `candidates/rank_N/`(파라미터, gain 중요도, 진입 시점 피처, MFE/MAE). 상세는 온디맨드 API(`/api/autolab/trial_detail`, `candidate_detail`).
- 챔피언 확정 시 `model_{intv}.txt`, `best_params_{intv}.json`, `feature_spec.json`, README, `reports/test_trades.csv`를 함께 갱신. 배포된 모델 폴더의 `feature_spec.json`은 설정 저장으로 덮어쓰지 않는다.
- 텔레그램 알림은 시작·종료 시점만.

## 10. 백테스트 = 실전 집행

`backtester.py`와 `model_engine.py`는 `shared/core/cexecution.py`의 Numba 배치 커널(`_simulate_batch_numba_core`)을 쓰고, CTrade는 같은 파일의 `CExecutionStreamSession`을 쓴다. 진입 필터 `min_edge_check`(ATR×TP < 왕복비용이면 진입 안 함). 패리티: `shared/tests/test_cexecution_parity.py`, `test_exit_priority_parity.py`, 오프라인 교차검증 `shared/tests/cross_validation.py`.

라벨링 KRX 규칙: `triple_barrier_labeling(market_type='KRX')`는 15:18 세션 경계를 넘어 탐색하지 않는다.
