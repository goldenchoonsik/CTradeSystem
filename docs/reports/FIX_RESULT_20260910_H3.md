# ✅ CTradeSystem 수정 결과 리포트 — H-3 (메타 필터 임계값 단일화 + 실전 적용)

| 항목 | 값 |
| :--- | :--- |
| **리포트 종류** | 수정 결과(Verification Result) 및 크로스체크 기록 |
| **기준 커밋(수정 전)** | `c9fba6a` (2026-09-10) |
| **수정 일자** | 2026-09-10 |
| **수정 범위** | 높음(High) H-3: 메타(2차) 모델 승인 임계값 통일(0.50) 및 실전 미적용 해소 |
| **수정 담당** | Cline |
| **연관 문서** | `CODE_REVIEW_20260910.md`, `CODE_REVIEW_20260910_checklist.csv`, `FIX_RESULT_20260910_H2.md` |

---

## 1. 수정 요약

| ID | 심각도 | 제목 | 수정 상태 | 검증 |
| :--- | :---: | :--- | :---: | :---: |
| H-3 | 🟠 높음 | 메타 임계값 0.35/0.50 혼재 + 실전 미적용 + primary 게이트 누락 | ✅ 완료 | ✅ 신규 6건 + 전체 223건 |

### 핵심 성과
- **단일 상수화**: `shared/ai_policy.META_APPROVAL_THRESHOLD = 0.50` 로 4개 소스 통일
- **실전 적용**: CTrade가 `_meta.txt` 존재 시 메타 승인 필터를 실제 진입 판정에 반영
- **게이트 보정**: web_dashboard가 primary 클래스 게이트 없이 메타를 적용하던 오류 수정
- **저위험**: 현행 binary 배포본(3종)은 메타 산출물이 없어 동작 변화 없음

---

## 2. 근본 원인

메타 라벨링(2차 모델) 승인 임계값이 소스별로 제각각이었고, 실전에는 메타 필터 자체가 없었다.

| 위치 (수정 전) | 임계값 | primary 게이트 |
| :--- | :---: | :---: |
| `CAiModelBaker/core/backtester.py` | 0.35 | primary==1 |
| `CAiModelBaker/core/model_engine.py` | 0.35 | primary==1 |
| `CAiModelBaker/web_dashboard/main.py` | 0.50 | **없음** |
| `docs/.../STRATEGY.md §13` | 0.50 | primary==1 |
| `CTrade/core/ai_strategy.py` | **미적용** | — |

---

## 3. 변경 내역

### 3-1. 단일 상수 신설 — `shared/ai_policy.py`
```python
# 2차(메타) 모델 승인 신뢰 확률 임계값 (문서 정책: 0.50)
META_APPROVAL_THRESHOLD = 0.50
```

### 3-2. 학습 3곳 통일
| 파일 | 변경 |
| :--- | :--- |
| `CAiModelBaker/core/backtester.py:304` | `>= 0.35` → `>= META_APPROVAL_THRESHOLD` |
| `CAiModelBaker/core/model_engine.py:1726` | `>= 0.35` → `>= META_APPROVAL_THRESHOLD` |
| `CAiModelBaker/web_dashboard/main.py:3326` | `>= 0.50` → `>= META_APPROVAL_THRESHOLD` + primary==1 게이트 추가 |

```python
# web_dashboard/main.py (게이트 추가)
if has_double_model:
    _pp = np.asarray(preds_primary)
    primary_is_buy = (np.argmax(_pp, axis=1) == 1) if _pp.ndim > 1 else (_pp >= 0.5)
...
is_meta_allowed = (preds_meta[idx] >= META_APPROVAL_THRESHOLD) if (primary_is_buy is None or primary_is_buy[idx]) else True
```

### 3-3. 실전(CTrade) 메타 필터 신규 — `CTrade/core/ai_strategy.py`
```python
# 로더: _meta.txt 존재 시에만 로드
_meta_candidate = os.path.join(model_dir, f"model_{self.interval}_meta.txt")
if os.path.exists(_meta_candidate):
    self.meta_model = lgb.Booster(model_file=_meta_candidate)

# 추론 [Step 2] + [Step 2-1]
if _raw.ndim > 1:
    primary_is_buy = (int(np.argmax(_raw[0])) == 1)
    probability = float(_raw[0, 1])
...
is_meta_allowed = True
if self.meta_model is not None and primary_is_buy:
    _mraw = np.asarray(self.meta_model.predict(latest))
    meta_prob = float(_mraw[0, 1]) if _mraw.ndim > 1 else float(_mraw[0])
    is_meta_allowed = meta_prob >= META_APPROVAL_THRESHOLD

# [Step 4] 진입 조건에 and is_meta_allowed 추가
```

---

## 4. 검증 증거

```
$ .venv/bin/python -m pytest shared/tests/test_meta_threshold_unification.py CTrade/tests/test_ai_model_loader_spec.py -v
test_meta_threshold_value                                PASSED
test_sources_use_single_constant[backtester.py]          PASSED
test_sources_use_single_constant[model_engine.py]        PASSED
test_sources_use_single_constant[web_dashboard/main.py]  PASSED
test_sources_use_single_constant[CTrade/core/ai_strategy.py] PASSED
test_loader_picks_canonical_model_file                   PASSED
test_loader_handles_multiclass_primary                   PASSED
test_loader_raises_when_only_meta_artifacts              PASSED
test_loader_loads_meta_model_when_present                PASSED
=> 9 passed
```

### 수정 전/후
| 항목 | 수정 전 | 수정 후 |
| :--- | :--- | :--- |
| 임계값 | 0.35 / 0.50 / 미적용 혼재 | `META_APPROVAL_THRESHOLD = 0.50` 단일 |
| web_dashboard 게이트 | 없음(전체 적용) | primary==1 게이트 |
| CTrade 실전 | 메타 필터 없음 | `_meta.txt` 존재 시 primary==1 AND meta>=0.50 |
| 현행 binary 배포본 | — | `meta_model=None` → 동작 불변 |

---

## 5. 회귀 테스트 결과

| 스위트 | 명령 | 결과 |
| :--- | :--- | :---: |
| H-3 신규 | `pytest shared/tests/test_meta_threshold_unification.py CTrade/tests/test_ai_model_loader_spec.py` | **9 passed** |
| 전 영역 | `pytest CTrade/tests shared/tests CCommander/tests` | **175 passed** |
| Baker | `pytest CAiModelBaker/tests` | **48 passed** |
| 합계 | — | **223 passed, 0 failed** |
| 컴파일 | `py_compile` (ai_policy/backtester/model_engine/web_dashboard/ai_strategy) | OK |

---

## 6. 변경 파일 목록 (H-3 관련)

```
 shared/ai_policy.py                 | (신규)
 CAiModelBaker/core/backtester.py    | 임계값 상수화
 CAiModelBaker/core/model_engine.py  | 임계값 상수화
 CAiModelBaker/web_dashboard/main.py | 임계값 상수화 + primary 게이트
 CTrade/core/ai_strategy.py          | 메타 모델 로드 + [Step 2-1] 게이트 + [Step 4] 조건
 (신규) shared/tests/test_meta_threshold_unification.py
 (수정) CTrade/tests/test_ai_model_loader_spec.py (+1 meta loader test)
```

---

## 7. 재현 커맨드 (재검증용)

```bash
cd /home/kuzimo/projects/CTradeSystem

# 1) H-3 회귀 테스트
.venv/bin/python -m pytest shared/tests/test_meta_threshold_unification.py CTrade/tests/test_ai_model_loader_spec.py -v

# 2) 전 영역 회귀 (개별 실행)
.venv/bin/python -m pytest CTrade/tests shared/tests CCommander/tests -q
.venv/bin/python -m pytest CAiModelBaker/tests -q

# 3) 단일 상수 및 잔존 하드코딩 점검
.venv/bin/python - <<'PY'
from shared.core.ai_policy import META_APPROVAL_THRESHOLD
assert META_APPROVAL_THRESHOLD == 0.50
print("H-3 OK: META_APPROVAL_THRESHOLD =", META_APPROVAL_THRESHOLD)
PY
grep -rn "preds_meta\[idx\] >= 0.3\|preds_meta\[idx\] >= 0.5" CAiModelBaker CTrade || echo "구 하드코딩 임계값 없음(정상)"
```

---

## 8. 남은 이슈 및 다음 단계

| 우선순위 | ID | 내용 | 상태 |
| :---: | :--- | :--- | :---: |
| 다음 | M-1 | `trade_history.json` writer 충돌 (KIS vs UPBIT/시뮬) 일원화 | OPEN |
| 이후 | M-2 | 테스트 레벨 Lv.1 쿨다운 캔들 단위 | OPEN |
| 이후 | M-3 | 병렬 피처 계산 순서 비결정성 | OPEN |
| 이후 | L-1 ~ L-8 | TZ·키워드·수수료·통합테스트 인프라 등 | OPEN |
| 후보 | H-4 | web_dashboard 백테스트 루프 비상손절(Emergency SL) 미구현/영문 라벨 | OPEN |

---

*본 결과 리포트는 2026-09-10 H-3 수정에 대한 검증 기록이며, 상세 발견 내역은 `CODE_REVIEW_20260910.md`를 참조하십시오.*

