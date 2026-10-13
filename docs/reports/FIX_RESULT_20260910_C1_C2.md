# ✅ CTradeSystem 수정 결과 리포트 — C-1 / C-2

| 항목 | 값 |
| :--- | :--- |
| **리포트 종류** | 수정 결과(Verification Result) 및 크로스체크 기록 |
| **기준 커밋(수정 전)** | `c9fba6a` (2026-09-10) |
| **수정 일자** | 2026-09-10 |
| **수정 범위** | 치명(Critical) 2건: C-1, C-2 |
| **수정 담당** | Cline |
| **연관 문서** | `CODE_REVIEW_20260910.md` (발견 리포트), `CODE_REVIEW_20260910_checklist.csv` (이슈 체크리스트) |

---

## 1. 수정 요약

| ID | 심각도 | 제목 | 수정 상태 | 검증 |
| :--- | :---: | :--- | :---: | :---: |
| C-1 | 🔴 치명 | KIS 실전 매도 후 C++ 포지션 미제거 | ✅ 완료 | ✅ 실증 + 회귀 |
| C-2 | 🔴 치명 | Shadow Optimizer async 미await → 체결 0건 | ✅ 완료 | ✅ 실증 + 회귀 |

---

## 2. C-1 상세 — KIS 실전 매도 후 C++ 포지션 제거

### 2-1. 근본 원인
C++ 바인딩에서 `VirtualTrader.positions`는 `def_property_readonly`이며 접근 시마다
`get_positions()`가 **새 dict 복사본**을 반환한다. 따라서 파이썬에서
`self.simulator.positions.pop(symbol, None)`을 호출해도 임시 복사본만 수정되고
실제 C++ `positions` 맵은 변경되지 않았다(no-op).

### 2-2. 변경 내역
| 파일 | 위치 | 변경 |
| :--- | :--- | :--- |
| `CTrade/core/cpp/ctrade_core.h` | 318 | `void remove_position(const std::string& symbol);` 선언 추가 |
| `CTrade/core/cpp/ctrade_core.cpp` | 881 | `VirtualTrader::remove_position()` 구현 (`positions.erase(symbol)`) |
| `CTrade/core/cpp/ctrade_core.cpp` | 1610 | `.def("remove_position", &VirtualTrader::remove_position, py::arg("symbol"))` 바인딩 |
| `CTrade/core/engine.py` | 541 | `self.simulator.positions.pop(symbol, None)` → `self.simulator.remove_position(symbol)` |

```diff
# CTrade/core/engine.py  (execute_sell, KIS 실전 분기)
             profit_amt = await self.kis_trader.sell(symbol, price, reason=reason, order_type=order_type)
             if profit_amt is not False:
-                self.simulator.positions.pop(symbol, None)
+                # C++ positions 프로퍼티는 매 접근 시 복사본을 반환하므로 직접 변형(.pop)이 무효.
+                # 실제 C++ 포지션 맵을 제거하는 전용 API를 호출한다.
+                self.simulator.remove_position(symbol)
```

### 2-3. 재빌드
```
cd CTrade && ../.venv/bin/python setup.py build_ext --inplace
→ exit 0
→ CTrade/ctrade_core.cpython-312-x86_64-linux-gnu.so 갱신 (2026-09-10 10:51)
```

### 2-4. 검증 증거 (재현)
```
before remove_position: {'069500': {'price': 35000.0, 'qty': 10.0, 'time': 1000.0, 'max_price': 35000.0, 'ts_active': False}}
after  remove_position: {}
C-1 OK: C++ 포지션이 실제로 제거됨
```

### 2-5. 기대 효과
- KIS 실전 모드에서 매도 체결 후 엔진 내부 허수 포지션 제거 → 중복 매도/재청산 시도, 재진입 차단, 리스크·통계 왜곡 해소
- `execute_buy`(force_set_position) ↔ `execute_sell`(remove_position) 비대칭 해소

---

## 3. C-2 상세 — Shadow Optimizer async 전환

### 3-1. 근본 원인
`StrategyOptimizer.evaluate()`가 `ShadowTrader.simulator`(=`core.simulator.VirtualTrader`)의
`async def buy/sell`을 **await 없이** 호출하여, 코루틴이 폐기되고 가상 체결이 단 한 건도
발생하지 않았다. 그 결과 `symbol_stats`가 비어 `check_for_upgrade()` 핫스왑이 영구 미동작.

### 3-2. 변경 내역
| 파일 | 위치 | 변경 |
| :--- | :--- | :--- |
| `CTrade/core/optimizer.py` | 88 | `def evaluate` → `async def evaluate` |
| `CTrade/core/optimizer.py` | 109 | `await shadow.simulator.sell(symbol, price, reason="SIGNAL")` |
| `CTrade/core/optimizer.py` | 113 | `await shadow.simulator.buy(symbol, price, shadow.evaluator.budget)` |
| `CTrade/core/engine.py` | 1660 | `await self.optimizer.evaluate(symbol, price, ctx)` |

```diff
# CTrade/core/optimizer.py
-    def evaluate(self, symbol: str, price: float, ctx: dict):
+    async def evaluate(self, symbol: str, price: float, ctx: dict):
         ...
                 if shadow.evaluator.should_sell(shadow_ctx):
-                    shadow.simulator.sell(symbol, price, reason="SIGNAL")
+                    await shadow.simulator.sell(symbol, price, reason="SIGNAL")
             else:
                 if shadow.evaluator.should_buy(ctx):
-                    shadow.simulator.buy(symbol, price, shadow.evaluator.budget)
+                    await shadow.simulator.buy(symbol, price, shadow.evaluator.budget)
```

### 3-3. 검증 증거 (재현)
```
evaluate is coroutine fn: True
shadow positions after evaluate: {'KRW-BTC': {'price': 100.0, 'qty': 9995.00249875, 'time': 1789005099.42, 'max_price': 100.0, 'ts_active': False}}
C-2 OK: await 경로로 가상 체결 발생
```

### 3-4. 기대 효과
- 8종 Shadow 변종의 가상 체결이 실제로 누적 → `symbol_stats` 기반 승격 판정 정상화
- `RuntimeWarning: coroutine ... was never awaited` 로그 소멸
- 옵티마이저 자동 핫스왑(engine.py:1903, 10초 주기) 재활성화

---

## 4. 회귀 테스트 결과

| 스위트 | 명령 | 결과 |
| :--- | :--- | :---: |
| CTrade | `pytest CTrade/tests -q` | **71 passed** (4.04s) |
| Shared + CCommander | `pytest shared/tests CCommander/tests -q` | **86 passed** (2.91s) |
| 합계 | — | **157 passed, 0 failed** |
| 컴파일 | `py_compile engine.py optimizer.py` | OK |
| C++ 빌드 | `setup.py build_ext --inplace` | exit 0 |

---

## 5. 변경 파일 목록 (수정 전 커밋 `c9fba6a` 대비)

```
 CTrade/core/cpp/ctrade_core.cpp |  5 +++++
 CTrade/core/cpp/ctrade_core.h   |  1 +
 CTrade/core/engine.py           |  6 ++++--
 CTrade/core/optimizer.py        | 19 ++++++++++++-------
 4 files changed, 22 insertions(+), 9 deletions(-)
```

신규 산출물(untracked):
```
 docs/reports/CODE_REVIEW_20260910.md
 docs/reports/CODE_REVIEW_20260910_checklist.csv
 docs/reports/FIX_RESULT_20260910_C1_C2.md   (본 문서)
```

> 참고: `CTrade/core/data_manager.py`는 워킹트리에 이전부터 존재하던 변경(`stop()`/`__del__` 추가, 13줄)으로 **본 수정과 무관**하며 건드리지 않았습니다.


---

## 6. 크로스체크 체크리스트 반영 상태

`docs/reports/CODE_REVIEW_20260910_checklist.csv` 의 `status` 컬럼 갱신:

| ID | status | fix_owner | 검증/비고 |
| :--- | :---: | :--- | :--- |
| C-1 | `VERIFIED` | Cline | `remove_position` 실증 + pytest 157건 통과 |
| C-2 | `VERIFIED` | Cline | `evaluate` async 전환 + 코루틴 실증 체결 + pytest 157건 통과 |
| H-1 ~ L-7 | `OPEN` | — | 미착수 |

### C-1 / C-2 재현 커맨드 (재검증용)

C-1:
```bash
cd CTrade && ../.venv/bin/python - <<'PY'
import ctrade_core
t = ctrade_core.VirtualTrader(10000000.0, 0.0005, True)
t.force_set_position('069500', 35000.0, 10.0, 1000.0, 35000.0, False)
t.remove_position('069500')
assert '069500' not in dict(t.positions)
print('C-1 OK')
PY
```

C-2:
```bash
cd CTrade && ../.venv/bin/python - <<'PY'
import asyncio, inspect
from core.simulator import VirtualTrader
from core.optimizer import StrategyOptimizer
assert inspect.iscoroutinefunction(StrategyOptimizer.evaluate)

class StubEval:
    budget = 1000000.0
    def should_buy(self, ctx): return True
    def should_sell(self, ctx): return False

class FakeShadow:
    variant_id = 'stub'
    evaluator = StubEval()
    simulator = VirtualTrader(10000000.0, 0.0005, True)

async def main():
    opt = StrategyOptimizer(); sh = FakeShadow()
    opt.shadow_targets['KRW-BTC'] = [sh]
    await opt.evaluate('KRW-BTC', 100.0, {})
    assert 'KRW-BTC' in dict(sh.simulator.positions)
    print('C-2 OK')
asyncio.run(main())
PY
```

---

## 7. 남은 이슈 및 다음 단계

| 우선순위 | ID | 내용 | 상태 |
| :---: | :--- | :--- | :---: |
| 다음 | **H-1** | 청산 우선순위(당일청산 vs 비상손절) 단일화 — `shared/tbm_executor`로 통일 후 백테스터/검증 리팩터 | OPEN |
| 이후 | H-2 | 배포 모델 규격 단일화 + CTrade 로더 정확 매칭 | OPEN |
| 이후 | H-3 | 메타 필터 임계값 통일(0.50) 및 실전 적용 | OPEN |
| 이후 | M-1 ~ M-3 | trade_history writer 일원화 / Lv.1 쿨다운 / 병렬 피처 결정성 | OPEN |
| 이후 | L-1 ~ L-7 | TZ·키워드·수수료 등 | OPEN |

> C-1 수정은 C++ 재빌드가 필요하므로, 배포 서버(Vultr) 반영 시 재빌드 또는 `.so` 동봉 배포가 필요합니다.

---

*본 결과 리포트는 2026-09-10 수정(C-1, C-2)에 대한 검증 기록이며, 상세 발견 내역은 `CODE_REVIEW_20260910.md`를 참조하십시오.*

