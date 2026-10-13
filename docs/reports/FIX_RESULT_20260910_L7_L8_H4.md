# ✅ CTradeSystem 수정 결과 리포트 — L-7 / L-8 / H-4

| 항목 | 값 |
| :--- | :--- |
| **리포트 종류** | 수정 결과(Verification Result) 및 크로스체크 기록 |
| **기준 커밋(수정 전)** | `c9fba6a` (2026-09-10) |
| **수정 일자** | 2026-09-10 |
| **수정 범위** | L-7(교차검증 매크로) · L-8(테스트 인프라) · H-4(web_dashboard 비상손절) |
| **수정 담당** | Cline |
| **연관 문서** | `CODE_REVIEW_20260910.md`, `CODE_REVIEW_20260910_checklist.csv`, `FIX_RESULT_20260910_L1_L6.md` |

---

## 1. 수정 요약

| ID | 제목 | 상태 |
| :--- | :--- | :---: |
| L-7 | `cross_validation.run_baker_simulation` 매크로 필터 미적용 | ✅ |
| L-8 | 통합 pytest `core` 패키지명 충돌 | ✅ |
| H-4 | `web_dashboard` 백테스트 비상손절 미구현/영문 라벨 | ✅ |

---

## 2. 변경 내역

### L-7 — 교차검증 매크로 필터 반영
`shared/tests/cross_validation.py` (`run_baker_simulation` 진입 조건)
```python
else:
    # L-7: 매크로 필터(일봉 RSI) 반영 — 실전/백테스터와 정합
    _macro_ok = True
    if deploy_config.get('USE_MACRO_FILTER', True):
        _rsi_col = 'target_rsi_daily' ... ('upbit_rsi_daily' ...)
        if _rsi_col:
            if float(test_df[_rsi_col].iloc[idx]) < float(deploy_config.get('MACRO_RSI_FILTER_THRESHOLD', 50.0)):
                _macro_ok = False
    if idx >= cooldown_until and preds[idx] >= params['buy_threshold'] and _macro_ok:
        holding = True; ...
```
> 청산 로직은 H-1에서 이미 `resolve_exit`로 통일되었고, L-7에서 진입 매크로 게이트를 추가하여 실전 경로 재현도를 높였다.

### L-8 — 테스트 스크립트 스위트 분리
`scripts/run_tests.sh`
```bash
PY="$PROJECT_ROOT/.venv/bin/pytest"; RC=0
# L-8: CTrade/CAiModelBaker 는 동일 패키지명 'core' → 반드시 분리 실행
"$PY" -v CTrade/tests shared/tests CCommander/tests "$@" || RC=1
"$PY" -v CAiModelBaker/tests "$@" || RC=1
exit "$RC"
```

### H-4 — web_dashboard 청산 통일(비상손절 포함)
`CAiModelBaker/web_dashboard/main.py`
- `low_col`(target_low/upbit_low) 추가
- 청산 블록을 `resolve_exit()`로 위임 → **비상손절(Emergency SL) 신규 지원**
- 라벨 통일: `ForceExit/StopLoss/TakeProfit/Timeout` → `당일청산/비상손절/손절/익절/시간만료`
```python
_exit = resolve_exit(entry_price=..., entry_atr=..., current_price=current_price,
                     current_low=current_low, take_profit=bp['take_profit'], stop_loss=bp['stop_loss'],
                     emergency_sl_mult=bp.get('emergency_sl_mult', bp['stop_loss']*2.0),
                     max_hold_candles=bp.get('max_hold_candles', bp['predict_ahead_candles']),
                     elapsed_candles=elapsed, cost=cost, force_exit=krx_force_exit,
                     is_candle_close=True, interval_minutes=int_val)
```

---

## 3. 검증 증거

```
$ .venv/bin/python -m pytest shared/tests/test_low_priority_part2.py -v
test_l7_cross_validation_macro_filter                       PASSED
test_l8_run_tests_script_separates_suites                   PASSED
test_h4_web_dashboard_uses_resolve_exit_and_korean_labels    PASSED
test_h4_web_dashboard_has_low_column                        PASSED
=> 4 passed
```

---

## 4. 회귀 테스트 결과

| 스위트 | 명령 | 결과 |
| :--- | :--- | :---: |
| L-7/L-8/H-4 신규 | `pytest shared/tests/test_low_priority_part2.py` | **4 passed** |
| 전 영역 | `pytest CTrade/tests shared/tests CCommander/tests` | **198 passed** |
| Baker | `pytest CAiModelBaker/tests` | **48 passed** |
| 합계 | — | **246 passed, 0 failed** |
| 스크립트 | `bash -n scripts/run_tests.sh` | OK |

---

## 5. 변경 파일 목록

```
 shared/tests/cross_validation.py            | 매크로 필터 반영
 scripts/run_tests.sh                        | 스위트 분리 실행
 CAiModelBaker/web_dashboard/main.py         | resolve_exit 통일(비상손절+한글 라벨)
 (신규) shared/tests/test_low_priority_part2.py
```

---

## 6. 재현 커맨드 (재검증용)

```bash
cd /home/kuzimo/projects/CTradeSystem

# 신규 테스트
.venv/bin/python -m pytest shared/tests/test_low_priority_part2.py -v

# 전체(분리 실행 스크립트, L-8 확인)
./scripts/run_tests.sh
```

---

## 7. 최종 상태 — 전 이슈 종결

| 심각도 | 이슈 | 상태 |
| :--- | :--- | :---: |
| 🔴 치명 | C-1, C-2 | ✅ VERIFIED |
| 🟠 높음 | H-1, H-2, H-3 | ✅ VERIFIED |
| 🟡 중간 | M-1, M-2, M-3 | ✅ VERIFIED |
| 🟢 낮음 | L-1 ~ L-8, H-4 | ✅ VERIFIED |
| **합계** | **19건** | **전 항목 수정완료** |

---

*본 결과 리포트는 2026-09-10 L-7/L-8/H-4 수정에 대한 검증 기록입니다.*
