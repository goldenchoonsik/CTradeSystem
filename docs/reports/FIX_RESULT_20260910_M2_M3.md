# ✅ CTradeSystem 수정 결과 리포트 — M-2 / M-3

| 항목 | 값 |
| :--- | :--- |
| **리포트 종류** | 수정 결과(Verification Result) 및 크로스체크 기록 |
| **기준 커밋(수정 전)** | `c9fba6a` (2026-09-10) |
| **수정 일자** | 2026-09-10 |
| **수정 범위** | 중간(Medium) M-2: Lv.1 쿨다운 캔들 단위 · M-3: 병렬 피처 계산 비결정성 |
| **수정 담당** | Cline |
| **연관 문서** | `CODE_REVIEW_20260910.md`, `CODE_REVIEW_20260910_checklist.csv`, `FIX_RESULT_20260910_M1.md` |

---

## 1. 수정 요약

| ID | 심각도 | 제목 | 상태 | 검증 |
| :--- | :---: | :--- | :---: | :---: |
| M-2 | 🟡 중간 | Lv.1 쿨다운 `cooldown_candles=1` 고정(인터벌 무시) | ✅ 완료 | ✅ 4건 |
| M-3 | 🟡 중간 | 병렬 피처 계산 순서 비결정성(피처 간 의존성) | ✅ 완료 | ✅ 3건 |

---

## 2. M-2 — Lv.1 쿨다운 인터벌 환산

### 근본 원인
`set_test_level(1)`이 `cooldown_candles = 1`로 고정 → 캔들 수 단위이므로 10s 인터벌에서 10초, 10m에서 10분이 되어 문서상 "1분"과 불일치. (Lv.0 복원 경로만 분→캔들 환산을 했음)

### 변경 — `CTrade/core/ai_strategy.py:916`
```python
elif self.test_level == 1:
    self.params['buy_threshold'] = 0.5000
    self.params['cooldown_minutes'] = 1
    # cooldown_candles는 '캔들 수' 단위 → 1분을 인터벌(분)로 환산 (M-2)
    self.position_manager.cooldown_candles = (
        max(1, int(1 / self.interval_minutes)) if self.interval_minutes > 0 else 1
    )
    self.bypass_krx_time_limit = False
```

| 인터벌 | cooldown_candles | 실제 대기 |
| :--- | :---: | :---: |
| 10s | 6 | 60초 |
| 1m | 1 | 60초 |
| 10m | 1 | 10분(최소 1캔들) |

### 테스트 — `CTrade/tests/test_lv1_cooldown_interval.py`
- `10s→6`, `1m→1`, `10m→1`, `Lv.0 복원 일관성` (4건)

---

## 3. M-3 — 병렬 피처 계산 결정론화

### 근본 원인
`feature_engine` 병렬 경로가 동일 `df` 스냅샷에서 모든 피처를 동시 계산 → 피처 간 의존성이 있으면 순차 경로와 결과가 달라짐. **실제 의존성 7건 확인**:
```
target_cvd_momentum    -> ['target_trade_bid_ratio']
target_obv_momentum    -> ['target_trade_bid_ratio']
target_cvd_absorption  -> ['target_trade_bid_ratio']
upbit_trade_bid_ratio  -> ['target_trade_bid_ratio']
upbit_cvd_momentum     -> ['target_trade_bid_ratio']
upbit_obv_momentum     -> ['target_trade_bid_ratio']
upbit_cvd_absorption   -> ['target_trade_bid_ratio']
```

### 변경 — `shared/feature_engine.py`
- `_raw_feature_refs(compute_fn)`: compute 소스에서 참조 컬럼 집합 추출(캐시)
- `_execute_feature_tasks(df, feature_tasks, interval, parallel)`: **의존성 위상(레벨) 스케줄링**
  - 레벨 산출 → 동일 레벨만 병렬 실행 → 각 레벨 결과를 df 반영 후 다음 레벨
  - 순차/병렬 어느 경로든 **동일 결과**(동일 레벨은 상호 독립)
  - 사이클/미해결 의존성은 사전순으로 안전 처리(무한루프 없음)
- 호출부: `use_parallel = (len(df) >= 3000 and len(feature_tasks) > 4)`

### 테스트 — `shared/tests/test_feature_parallel_determinism.py`
- 의존 피처 포함 순차=병렬 완전 동일 / 사이클 무한루프 방지 / 실제 레지스트리 의존성 검출 (3건)

---

## 4. 검증 증거

```
$ .venv/bin/python -m pytest shared/tests/test_feature_parallel_determinism.py CTrade/tests/test_lv1_cooldown_interval.py -v
test_parallel_equals_sequential_with_dependency                     PASSED
test_cycle_does_not_hang                                            PASSED
test_real_registry_dependency_detected                              PASSED
test_lv1_cooldown_candles_interval_scaled[...069500_10s...-6]       PASSED
test_lv1_cooldown_candles_interval_scaled[...069500_1m...-1]        PASSED
test_lv1_cooldown_candles_interval_scaled[...BTC_10m...-1]          PASSED
test_lv0_restores_original_cooldown_scaled                          PASSED
=> 7 passed
```

---

## 5. 회귀 테스트 결과

| 스위트 | 명령 | 결과 |
| :--- | :--- | :---: |
| M-2/M-3 신규 | `pytest shared/tests/test_feature_parallel_determinism.py CTrade/tests/test_lv1_cooldown_interval.py` | **7 passed** |
| 전 영역 | `pytest CTrade/tests shared/tests CCommander/tests` | **186 passed** |
| Baker | `pytest CAiModelBaker/tests` | **48 passed** |
| 합계 | — | **234 passed, 0 failed** |
| 컴파일 | `py_compile feature_engine.py ai_strategy.py` | OK |

---

## 6. 변경 파일 목록 (M-2/M-3 관련)

```
 shared/feature_engine.py                            | _raw_feature_refs / _execute_feature_tasks (위상 레벨)
 CTrade/core/ai_strategy.py                          | Lv.1 cooldown_candles 인터벌 환산
 (신규) shared/tests/test_feature_parallel_determinism.py
 (신규) CTrade/tests/test_lv1_cooldown_interval.py
```

---

## 7. 재현 커맨드 (재검증용)

```bash
cd /home/kuzimo/projects/CTradeSystem
.venv/bin/python -m pytest shared/tests/test_feature_parallel_determinism.py CTrade/tests/test_lv1_cooldown_interval.py -v

# 의존성 검출 확인
.venv/bin/python - <<'PY'
from shared.features.feature_engine import _raw_feature_refs
from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY as R
print('cvd_momentum refs:', sorted(_raw_feature_refs(R['target_cvd_momentum']['compute'])))
PY
```

---

## 8. 남은 이슈 및 다음 단계

| 우선순위 | ID | 내용 | 상태 |
| :---: | :--- | :--- | :---: |
| 다음 | L-1 ~ L-6 | TZ/키워드 과매칭/수수료/손익 등 Low | OPEN |
| 이후 | L-7 | cross_validation 실전 경로 재현 | OPEN |
| 이후 | L-8 | 통합 pytest 인프라(core 패키지명 충돌) | OPEN |
| 후보 | H-4 | web_dashboard 비상손절 미구현/영문 라벨 | OPEN |

---

*본 결과 리포트는 2026-09-10 M-2/M-3 수정에 대한 검증 기록입니다.*

