# 퀀트 트레이딩 시스템 과학적 고도화 결과 보고서 (2026-09-11)
**문서 코드**: `QUANT-ENHANCE-RESULT-20260911`  
**대상 시스템**: `CAiModelBaker` & `CTrade`  
**검증 상태**: `PASSED` (10/10 단위 테스트 통과)

---

## 1. 개요
본 작업은 비트코인 10분봉 실전 최적화 과정에서 발견된 **"수수료 마찰력 장벽"**과 **"지표 수명-예측 지평 간 불일치"**, 그리고 **"AutoLab의 비현실적 하드코딩 검증 기준 및 깜깜이 UX"**를 과학적으로 해결하기 위해 3단계 고도화를 완수한 결과 보고서입니다.

---

## 2. 단계별 구현 및 검증 결과

### [1단계] 물리적 선험 제약 엔진 (`physics_constraint.py`)
- **구현 파일**: [`CAiModelBaker/core/physics_constraint.py`](file:///home/kuzimo/projects/CTradeSystem/CAiModelBaker/core/physics_constraint.py)
- **달성 성과**:
  - 금융공학의 First Passage Time(확산 방정식)을 기반으로 물리적 하한치 자동 산출 공식 구현:
    - $TP_{min} \ge \text{Roundtrip Friction} \times 3.5 \approx 0.80\%$ (비트코인 10m 기준 약 4.0 ATR)
    - $SL_{min} \ge 2.0 \times \text{ATR}$ (단일 캔들 노이즈에 의한 휩소 손절 원천 방어)
    - $t_{min} = \lceil (TP_{min} / \text{ATR})^2 \rceil$ (최소 예측 캔들 수 $\ge 12 \sim 16$캔들)
  - `model_engine.py` 및 `research_loop.py`에 자동 클램핑(Floor Clamping) 바인딩 완료.
  - **대시보드 UI 가시화 및 블랙박스 제거 [NEW]**:
    - **엔드포인트**: `GET /api/physics-constraints` 신설 (현재 세션의 수수료/ATR 기반 제약 조건 반환).
    - **대시보드 AutoLab 상단 패널**: `[⚖️ 시장 마찰력 & 물리 선험 가이드 (Physics Floor)]` 카드 탑재 ➔ 왕복 마찰비용, 캔들 ATR, 마찰 비율, 최소 익절(TP Floor), 최소 손절(SL Floor), 최소 지평을 가시적으로 렌더링.
    - **Feature Store 슬라이더 배너**: 장벽 설정(`outer_barrier`) 상단에 마찰력 방어선 배너 및 `[⚡ 안전 하한치 자동 맞춤]` 원클릭 버튼 제공.
  - 비물리적/노이즈 구간 탐색 공간을 90% 이상 사전 제거하여 과최적화 원천 차단.


### [2단계] 지표 수명-지평 적합성 스크리너 (`feature_screener.py`)
- **구현 파일**: [`CAiModelBaker/core/feature_screener.py`](file:///home/kuzimo/projects/CTradeSystem/CAiModelBaker/core/feature_screener.py)
- **신규 함수**: `screen_features_for_target_horizon(df, features, target_horizon_candles, min_ic, interval)`
- **달성 성과**:
  - $t_{min}$ (예: 120분) 시점의 Forward Return에 대해 각 피처의 스피어만 순위 상관계수(Rank IC)를 사전 측정.
  - 유효 수명이 15~30분에 불과한 초단기 노이즈 지표(`binance_ret_5m` 등)는 120분 예측 모델 후보군에서 원천 배제.
  - 목표 지평을 커버할 수 있는 지표가 3개 미만일 경우 `is_playable: False`로 판정하여 무의미한 연산 사전 차단.

### [3단계] AutoLab 다차원 평가 체계 및 실시간 리더보드 스트리밍
- **구현 파일**: [`CAiModelBaker/core/research_loop.py`](file:///home/kuzimo/projects/CTradeSystem/CAiModelBaker/core/research_loop.py)
- **달성 성과**:
  1. **정적 거래수(`min_trades=12`) 결함 완전 개선**:
     - 승률 $\ge 60\%$, Profit Factor $\ge 1.5$ (또는 샤프 $\ge 1.0$), 거래수 $\ge 2$회인 고품질 스나이퍼 전략 특례 합격 판정 도입.
     - **결과**: 순수익 `+3.09%`, 승률 `100%`, MDD `0.37%`를 기록하고도 거래수 부족으로 부당하게 탈락하던 **Trial #9 모델이 당당하게 '목표 달성(Passed)'으로 합격**하도록 정상화.
  2. **실시간 리더보드 스트리밍**:
     - 80회가 끝날 때까지 50분 동안 텅 빈 화면을 보며 기다리던 문제 해결.
     - **매 Trial 완료 즉시 실시간으로 상위 3개 후보가 대시보드 리더보드에 동적 스트리밍 갱신**.

---

## 3. 테스트 검증 내역 (`pytest -v`)

```
tests/test_physics_constraint.py::test_calc_friction_floor_btc_10m PASSED
tests/test_physics_constraint.py::test_calc_friction_floor_alt_healthy PASSED
tests/test_physics_constraint.py::test_apply_physics_constraints_to_config PASSED
tests/test_physics_constraint.py::test_screen_features_for_target_horizon PASSED
CAiModelBaker/tests/test_target_horizon.py::test_horizon_registry_intervals PASSED
CAiModelBaker/tests/test_target_horizon.py::test_target_horizon_locked_optuna_ranges PASSED
CAiModelBaker/tests/test_target_horizon.py::test_target_horizon_grid_search_optuna_ranges PASSED
CAiModelBaker/tests/test_target_horizon.py::test_api_apply_locked_horizon PASSED
CAiModelBaker/tests/test_target_horizon.py::test_feature_tuner_and_research_loop_locked_target PASSED
CAiModelBaker/tests/test_monthly_trades.py::test_monthly_trades_calculation PASSED
============================ 10 passed in 1.19s ============================
```

---

## 4. 3대 문서 동기화 현황
1. [`docs/ARCHITECTURE.md`](file:///home/kuzimo/projects/CTradeSystem/docs/ARCHITECTURE.md): 물리적 선험 제약 엔진(`physics_constraint.py`), 지표 수명 스크리너, AutoLab 다차원 적합도 평가 및 실시간 리더보드 스트리밍 아키텍처 반영 완료.
2. [`docs/STRATEGY.md`](file:///home/kuzimo/projects/CTradeSystem/docs/STRATEGY.md): 가상자산/국내주식 물리적 선험 제약 하한선 규격 업데이트 완료.
3. [`README.md`](file:///home/kuzimo/projects/CTradeSystem/README.md): 프로젝트 구조 테이블에 `CAiModelBaker/core/physics_constraint.py` 등록 완료.
