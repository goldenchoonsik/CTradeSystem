# ✅ CTradeSystem 수정 결과 리포트 — H-1 (청산 우선순위 단일화)

| 항목 | 값 |
| :--- | :--- |
| **리포트 종류** | 수정 결과(Verification Result) 및 크로스체크 기록 |
| **기준 커밋(수정 전)** | `c9fba6a` (2026-09-10) |
| **수정 일자** | 2026-09-10 |
| **수정 범위** | 높음(High) H-1: 청산 우선순위(당일청산 vs 비상손절) 단일화 |
| **수정 담당** | Cline |
| **연관 문서** | `CODE_REVIEW_20260910.md`, `CODE_REVIEW_20260910_checklist.csv`, `FIX_RESULT_20260910_C1_C2.md` |

---

## 1. 수정 요약

| ID | 심각도 | 제목 | 수정 상태 | 검증 |
| :--- | :---: | :--- | :---: | :---: |
| H-1 | 🟠 높음 | 학습(백테스트/WFA/OOS) ↔ 실전 청산 우선순위 불일치 | ✅ 완료 | ✅ 패리티 9건 + 전체 214건 |

### 핵심 성과
- **단일 소스 함수(`resolve_exit`) 신설**로 9개 청산 판정 지점의 우선순위/장벽 계산을 100% 통일
- 기존: `backtester`/`model_engine`/`cross_validation`은 **비상손절 → … → 당일청산(맨 마지막)**, 실전 `tbm_executor`는 **당일청산 → 비상손절 → …** 으로 **정반대**였음
- 15:18 캔들에서 Low가 비상손절선을 동시 터치하면 실전=`당일청산(종가)`, 백테스트=`비상손절(장벽가)`로 **청산사유·순수익률 불일치** 발생 → 해소

---

## 2. 근본 원인

동일한 하이브리드 TBM 규칙이 실전/학습에 각각 **복붙된 인라인 코드**로 9곳에 흩어져 있었고, 그중 실전만 "당일청산 최우선" 순서를 지켰다. 단일 함수가 없어 순서 드리프트(Drift)가 발생했다.

| 구현 (수정 전) | 판정 순서 |
| :--- | :--- |
| 실전 `shared/tbm_executor.py` | 1) 당일청산 → 2) 비상손절 → 3) Soft SL → 4) Soft TP → 5) 시간만료 |
| Baker `backtester.py` / `model_engine.py` / `cross_validation.py` | 1) 비상손절 → 2) Soft SL → 3) Soft TP → **4) 당일청산/시간만료** |

---

## 3. 변경 내역

### 3-1. 단일 청산 판정 함수 신설 — `shared/tbm_executor.py`
```python
def resolve_exit(entry_price, entry_atr, current_price, current_low,
                 take_profit, stop_loss, emergency_sl_mult, max_hold_candles,
                 elapsed_candles, cost, force_exit=False,
                 is_candle_close=True, interval_minutes=1.0):
    # 1. 당일청산(force_exit)  → 2. 비상손절(current_low)  → [틱이면 종료]
    # 3. Soft SL(종가)  → 4. Soft TP(종가)  → 5. 시간만료
```
- `TBMPositionManager.check_exit()` 는 `resolve_exit()` 위임으로 리팩터 (실전 동작 100% 보존)

### 3-2. 통일 적용 지점
| 파일 | 지점 | 비고 |
| :--- | :--- | :--- |
| `shared/tbm_executor.py` | `check_exit` (177) | 위임 |
| `CAiModelBaker/core/backtester.py` | 262 | `force_exit=krx_force_exit` |
| `CAiModelBaker/core/model_engine.py` | 659, 767, 873, 1053, 1176, 1468, 1702 | 7개 루프 |
| `shared/tests/cross_validation.py` | 101 | `force_exit=False` (CRYPTO) |
| `CAiModelBaker/web_dashboard/main.py` | 3241 | ForceExit 최우선으로 순서만 동기화 |

### 3-3. 라벨 통일
- 당일청산 사유 문자열을 `당일청산`으로 일원화 (기존 Baker의 `장마감청산` 혼재 제거)
- `CTrade/core/ai_strategy.py:83` 의 매핑(`당일청산`/`장마감청산` → `DAY_EXIT`)과 무충돌

### 3-4. 잔여 확인
```
$ grep -rn 'if current_low <= emergency' CAiModelBaker shared
shared/tbm_executor.py:76:    if current_low <= emergency_sl_price:   # ← 단일 함수 내부만 존재 (정상)
```

---

## 4. 검증 증거 (신규 패리티 테스트)

`shared/tests/test_exit_priority_parity.py` (9건):
```
test_force_exit_beats_emergency_and_soft_barriers PASSED
test_emergency_sl_beats_soft_tp_on_same_candle    PASSED
test_soft_sl_before_soft_tp                       PASSED
test_soft_tp                                      PASSED
test_time_exit_last                               PASSED
test_tick_mode_ignores_soft_barriers              PASSED
test_net_return_formula                           PASSED
test_live_tbm_candle_equals_resolve_exit          PASSED
test_backtest_reference_loop_parity               PASSED
=> 9 passed
```

### 핵심 검증 포인트
- `test_force_exit_beats_emergency_and_soft_barriers`: 15:18 + Low가 비상손절선 강하게 터치 + 종가 Soft SL 동시 충족 → **`당일청산` 반환** (기존 백테스트는 `비상손절`이었음)
- `test_emergency_sl_beats_soft_tp_on_same_candle`: 종가 TP 상회 + Low 비상손절 터치 → **`비상손절`**
- `test_backtest_reference_loop_parity`: 백테스트 공통 규칙과 실전 `TBMPositionManager`의 **청산사유/체결가/순수익률 완전 일치**

---

## 5. 회귀 테스트 결과

| 스위트 | 명령 | 결과 |
| :--- | :--- | :---: |
| 패리티 신규 | `pytest shared/tests/test_exit_priority_parity.py` | **9 passed** |
| 전 영역 | `pytest CTrade/tests shared/tests CCommander/tests` | **166 passed** |
| Baker | `pytest CAiModelBaker/tests` | **48 passed** |
| 합계 | — | **214 passed, 0 failed** |
| 컴파일 | `py_compile` (model_engine/backtester/cross_validation/tbm_executor/web_dashboard) | OK |
| import 스모크 | `from core import model_engine, backtester` | OK |

---

## 6. 변경 파일 목록 (수정 전 커밋 `c9fba6a` 대비, H-1 관련)

```
 CAiModelBaker/core/backtester.py    | 102 +++---------
 CAiModelBaker/core/model_engine.py  | 324 +++++++++++++----------------
 CAiModelBaker/web_dashboard/main.py |  21 ++-
 shared/tbm_executor.py              | 188 +++++++++++++--------
 shared/tests/cross_validation.py    |  43 ++---
 (신규) shared/tests/test_exit_priority_parity.py
```

---

## 7. 재현 커맨드 (재검증용)

```bash
cd /home/kuzimo/projects/CTradeSystem

# 1) 신규 패리티 테스트
.venv/bin/python -m pytest shared/tests/test_exit_priority_parity.py -v

# 2) 전 영역 회귀
.venv/bin/python -m pytest CTrade/tests shared/tests CCommander/tests CAiModelBaker/tests -q

# 3) 단일 함수 직접 확인 (당일청산이 비상손절보다 우선인지)
.venv/bin/python - <<'PY'
from shared.core.tbm_executor import resolve_exit
r = resolve_exit(entry_price=10000.0, entry_atr=100.0,
                 current_price=9000.0, current_low=8000.0,
                 take_profit=2.0, stop_loss=1.5, emergency_sl_mult=3.0,
                 max_hold_candles=20, elapsed_candles=3, cost=0.0022,
                 force_exit=True, is_candle_close=True, interval_minutes=1.0)
assert r["exit_type"] == "당일청산", r
print("H-1 OK:", r["exit_type"], r["exit_price"])
PY
```

---

## 8. 남은 이슈 및 다음 단계

| 우선순위 | ID | 내용 | 상태 |
| :---: | :--- | :--- | :---: |
| 다음 | H-2 | 배포 모델 규격 단일화 + CTrade 로더 정확 매칭(`listdir[0]` 제거) | OPEN |
| 이후 | H-3 | 메타 필터 임계값 통일(0.50) 및 실전 적용 | OPEN |
| 이후 | M-1 ~ M-3 | trade_history writer 일원화 / Lv.1 쿨다운 / 병렬 피처 결정성 | OPEN |
| 이후 | L-1 ~ L-7 | TZ·키워드·수수료 등 | OPEN |

### 신규 발견(잔여, H-1 범위 외)
- `CAiModelBaker/web_dashboard/main.py` 백테스트 루프는 **비상손절(Emergency SL) 자체가 미구현**이며 라벨이 영어(`StopLoss`/`TakeProfit`/`ForceExit`/`Timeout`)로 다른 규격을 사용한다. 이번 수정에서는 **우선순위(ForceExit 최우선)만** 동기화하고, 비상손절 추가 및 라벨 통일은 별도 이슈로 남긴다. (→ H-4 후보)

---

*본 결과 리포트는 2026-09-10 H-1 수정에 대한 검증 기록이며, 상세 발견 내역은 `CODE_REVIEW_20260910.md`를 참조하십시오.*

