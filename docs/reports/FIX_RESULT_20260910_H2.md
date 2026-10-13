# ✅ CTradeSystem 수정 결과 리포트 — H-2 (배포 모델 규격 단일화)

| 항목 | 값 |
| :--- | :--- |
| **리포트 종류** | 수정 결과(Verification Result) 및 크로스체크 기록 |
| **기준 커밋(수정 전)** | `c9fba6a` (2026-09-10) |
| **수정 일자** | 2026-09-10 |
| **수정 범위** | 높음(High) H-2: 배포 모델 규격 단일화 + CTrade 로더 결정론적 정확 매칭 |
| **수정 담당** | Cline |
| **연관 문서** | `CODE_REVIEW_20260910.md`, `CODE_REVIEW_20260910_checklist.csv`, `FIX_RESULT_20260910_C1_C2.md`, `FIX_RESULT_20260910_H1.md` |

---

## 1. 수정 요약

| ID | 심각도 | 제목 | 수정 상태 | 검증 |
| :--- | :---: | :--- | :---: | :---: |
| H-2 | 🟠 높음 | `model_{interval}.txt` 규격 불일치(multiclass 저장) + 로더 `listdir[0]` 임의 선택 | ✅ 완료 | ✅ 로더 3건 + 전체 217건 |

### 핵심 성과
- **규격 확정**: CTrade 소비 모델은 `model_{interval}.txt` **단일 파일**. `_primary`/`_meta`는 Baker 내부(메타 라벨링) 전용으로 고정.
- **결정론적 로더**: `os.listdir()[0]` 임의 선택 제거 → 정확 파일명 매칭 + 명시적 실패.
- **차원 무관 추론**: binary(1D) / multiclass(2D, 상승=class 1) 모두 안전 처리 → 이전에는 multiclass 로드 시 `float(array)` 예외로 **무매매(silent)** 발생.

---

## 2. 근본 원인

1. `CAiModelBaker/core/model_engine.py:1709-1711`의 `confirm_and_deploy_strategy`는
   `model_{interval}.txt`에 **3-class(multiclass) primary 모델**을 저장한다.
2. `CTrade/core/ai_strategy.py:166-170`은
   `model_files = [f for f in os.listdir(model_dir) if f.startswith("model_") and f.endswith(".txt")]`
   후 `model_files[0]`을 선택 → **파일시스템 순서 의존**이며 `_primary`/`_meta`가 선택될 수 있음.
3. 추론이 `float(self.model.predict(latest)[0])`로 **binary 1D를 가정** → multiclass 선택 시 `(n,3)` 반환 → `float(array)` 예외 → `except`로 삼켜져 **추론 항상 실패(무매매)**.

---

## 3. 변경 내역

### 3-1. CTrade 로더 — `CTrade/core/ai_strategy.py`
```python
# 결정론적 파라미터 파일 선택
params_files = sorted(f for f in os.listdir(model_dir)
                      if f.startswith("best_params_") and f.endswith(".json"))

# 모델 파일 결정론적 선택 (신설)
self.model_path = self._resolve_model_file(model_dir)
self.model = lgb.Booster(model_file=self.model_path)
self.model_num_class = self._detect_num_class()
```

- **`_resolve_model_file(model_dir)`** (line 378)
  1. `model_{interval}.txt` 정확 매칭
  2. 없으면 `_primary`/`_meta` 접미사 제외 후 사전순 첫 파일
  3. 그마저 없으면 `FileNotFoundError` (침묵 실패 차단)
- **`_detect_num_class()`** (line 405): `Booster.num_feature()` + probe predict 로 binary=1 / multiclass=N 감지 (feature_spec 로드 이전에도 안전)
- **추론** (line 595)
  ```python
  _raw = np.asarray(self.model.predict(latest))
  probability = float(_raw[0, 1]) if _raw.ndim > 1 else float(_raw[0])
  ```

### 3-2. 백테스터 fallback — `CAiModelBaker/core/backtester.py:176,190`
```python
_raw = primary_model.predict(test_df[features])
preds = _raw[:, 1] if getattr(_raw, "ndim", 1) > 1 else _raw   # multiclass 대응
```
`_primary`/`_meta`가 없어 `model_{interval}.txt`(=multiclass)만 있는 패키지에서 `preds[idx] >= threshold`의 모호한 배열 비교 오류를 방지.

### 3-3. 신규 회귀 테스트 — `CTrade/tests/test_ai_model_loader_spec.py`
| 테스트 | 검증 |
| :--- | :--- |
| `test_loader_picks_canonical_model_file` | `_primary`/`_meta` 공존 시 `model_1m.txt` 선택, binary 1D |
| `test_loader_handles_multiclass_primary` | multiclass 로드/추론, class-1 확률 추출 |
| `test_loader_raises_when_only_meta_artifacts` | 정식 파일 부재 시 명시적 실패 |

---

## 4. 검증 증거

```
$ .venv/bin/python -m pytest CTrade/tests/test_ai_model_loader_spec.py -v
test_loader_picks_canonical_model_file    PASSED
test_loader_handles_multiclass_primary    PASSED
test_loader_raises_when_only_meta_artifacts PASSED
=> 3 passed
```

### 이전(버그) 동작 vs 수정 후
| 시나리오 | 수정 전 | 수정 후 |
| :--- | :--- | :--- |
| `model_X.txt` + `_primary` + `_meta` 공존 | `listdir[0]` 임의 선택(`_primary`/`_meta` 가능) | `model_X.txt` 고정 선택 |
| multiclass 모델 로드 | `float(array)` 예외 → 추론 실패(무매매) | `[:,1]` 상승확률 추출 → 정상 |

---

## 5. 회귀 테스트 결과

| 스위트 | 명령 | 결과 |
| :--- | :--- | :---: |
| 로더 신규 | `pytest CTrade/tests/test_ai_model_loader_spec.py` | **3 passed** |
| 전 영역 | `pytest CTrade/tests shared/tests CCommander/tests` | **169 passed** |
| Baker | `pytest CAiModelBaker/tests` | **48 passed** |
| 합계 | — | **217 passed, 0 failed** |
| 컴파일 | `py_compile ai_strategy.py backtester.py` | OK |

> ⚠️ `pytest CTrade/tests CAiModelBaker/tests`를 **한 프로세스에서** 실행하면 두 앱이 동일 패키지명 `core`를 사용하여 수집 충돌(`ModuleNotFoundError: core.research_loop`)이 발생한다. **개별 실행은 정상**이며, 이는 본 수정과 무관한 기존 테스트 인프라 이슈로 **L-8(OPEN)** 로 등록했다.

---

## 6. 변경 파일 목록 (수정 전 커밋 `c9fba6a` 대비, H-2 관련)

```
 CTrade/core/ai_strategy.py       | 70 +++++++++++++++++----
 CAiModelBaker/core/backtester.py | 108 +++++++++++-----------------  (H-1 포함)
 (신규) CTrade/tests/test_ai_model_loader_spec.py
```

---

## 7. 재현 커맨드 (재검증용)

```bash
cd /home/kuzimo/projects/CTradeSystem

# 1) 로더 규격 회귀 테스트
.venv/bin/python -m pytest CTrade/tests/test_ai_model_loader_spec.py -v

# 2) 전 영역 회귀 (개별 실행)
.venv/bin/python -m pytest CTrade/tests shared/tests CCommander/tests -q
.venv/bin/python -m pytest CAiModelBaker/tests -q

# 3) 실제 배포 패키지 로더 확인 (현 배포본은 binary)
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, 'CTrade')
from core.ai_strategy import AIStrategyModule
m = AIStrategyModule('shared/deploy/BTC_10m/BTC_M10_BEST1', symbol='KRW-BTC', enable_parity_logging=False)
print('model file:', m.model_path)
print('num_class :', m.model_num_class)   # binary => 1
PY
```

---

## 8. 남은 이슈 및 다음 단계

| 우선순위 | ID | 내용 | 상태 |
| :---: | :--- | :--- | :---: |
| 다음 | H-3 | 메타 필터 임계값 통일(0.50) 및 실전 적용 | OPEN |
| 이후 | M-1 ~ M-3 | trade_history writer 일원화 / Lv.1 쿨다운 / 병렬 피처 결정성 | OPEN |
| 이후 | L-1 ~ L-8 | TZ·키워드·수수료·통합 테스트 인프라 등 | OPEN |

### H-3 연계 메모
현재 CTrade는 `_primary`/`_meta`(메타 라벨링 배포 패키지)에서 **primary의 class-1 확률만** 사용하며 메타 승인 필터를 적용하지 않는다(임계값 0.50). H-3에서 실전 메타 필터 적용 + 임계값 단일화를 처리한다.

---

*본 결과 리포트는 2026-09-10 H-2 수정에 대한 검증 기록이며, 상세 발견 내역은 `CODE_REVIEW_20260910.md`를 참조하십시오.*

