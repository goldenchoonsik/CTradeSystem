# ✅ CTradeSystem 수정 결과 리포트 — M-1 (trade_history.json writer 충돌 해소)

| 항목 | 값 |
| :--- | :--- |
| **리포트 종류** | 수정 결과(Verification Result) 및 크로스체크 기록 |
| **기준 커밋(수정 전)** | `c9fba6a` (2026-09-10) |
| **수정 일자** | 2026-09-10 |
| **수정 범위** | 중간(Medium) M-1: 다중 writer(UPBIT/KIS) `trade_history.json` 덮어쓰기 충돌 |
| **수정 담당** | Cline |
| **연관 문서** | `CODE_REVIEW_20260910.md`, `CODE_REVIEW_20260910_checklist.csv`, `FIX_RESULT_20260910_H3.md` |

---

## 1. 수정 요약

| ID | 심각도 | 제목 | 수정 상태 | 검증 |
| :--- | :---: | :--- | :---: | :---: |
| M-1 | 🟡 중간 | `trade_history.json` writer 충돌(마지막 writer가 상대 이벤트 소실) | ✅ 완료 | ✅ 신규 4건 + 종단 검증 + 전체 227건 |

### 핵심 성과
- **합집합 병합 저장**: 파일 기존 이력 ∪ 신규 이벤트 → 어떤 writer가 저장해도 이벤트 유실 없음
- **원자적 저장 + 파일 락**: `os.replace` + `fcntl` 배타 락으로 부분쓰기/경쟁 최소화
- **소비자 무변경**: 기존 경로/스키마 유지(CCommander·telegram_bot·api_server·engine reconcile 그대로)

---

## 2. 근본 원인

`trade_history.json`은 VirtualTrader(UPBIT/시뮬)와 KISTrader(KIS)가 **각자 독립적인 in-memory 리스트**를 들고 `open(path, "w")`로 **전체 덮어쓰기**했다.

- KISTrader는 시작 시 파일을 로드하지만, 이후 UPBIT에서 추가된 이벤트는 자신의 리스트에 없다 → 저장 시 그 이벤트가 사라짐
- VirtualTrader(C++)도 KIS 이벤트를 자신의 C++ history에 갖고 있지 않음 → 저장 시 KIS 이벤트가 사라짐
- 결과: 다중 자산 동시 운용 시 매매 이력/통계/텔레그램 집계가 누락·불일치

---

## 3. 변경 내역

### 3-1. 공용 저장소 신설 — `CTrade/utils/history_store.py`
```python
def save_history_merged(path, events) -> bool:
    # 1) *.lock 배타 락 (fcntl, best-effort)
    # 2) 기존 파일 로드 (load_history_raw)
    # 3) 기존 ∪ 신규 이벤트 합집합 병합 (동일 키는 신규 우선)
    # 4) time 오름차순 정렬
    # 5) tmp 파일에 기록 후 os.replace 로 원자적 교체
```
- 중복 키: `(type, symbol, time, price, qty, reason, exchange)` (부가 필드 제외)
- 손상 파일은 무시하고 계속 진행(가용성 우선)

### 3-2. writer 위임
| 파일 | 변경 |
| :--- | :--- |
| `CTrade/core/simulator.py:47` | `_save_history_sync` → `save_history_merged(self.history_file, history_data)` |
| `CTrade/core/kis_trader.py:38` | `_save_history_sync` → `save_history_merged(self.history_file, self.history)` |

> VirtualTrader는 이벤트를 `dict(h)`로 복사 후 `is_real`/`mode`를 보장하여 전달(원본 C++ 객체 비변형).

### 3-3. 신규 회귀 테스트 — `CTrade/tests/test_history_merge.py`
| 테스트 | 검증 |
| :--- | :--- |
| `test_merge_preserves_other_writer_events` | B(KIS) 저장 시 A(UPBIT) 이벤트 보존 |
| `test_merge_idempotent` | 동일 이벤트 반복 저장 시 중복 없음 |
| `test_merge_sorted_by_time` | time 오름차순 정렬 |
| `test_interleaved_writers_no_clobber` | UPBIT/KIS 교차 저장 시 3건 전부 보존 |

---

## 4. 검증 증거

### 4-1. 단위 테스트
```
$ .venv/bin/python -m pytest CTrade/tests/test_history_merge.py -v
test_merge_preserves_other_writer_events PASSED
test_merge_idempotent                   PASSED
test_merge_sorted_by_time               PASSED
test_interleaved_writers_no_clobber     PASSED
=> 4 passed
```

### 4-2. 실제 클래스 종단 검증 (VirtualTrader + KISTrader)
```
events: [('KRW-BTC', 'BUY'), ('069500', 'BUY')]
M-1 OK: 두 writer 이벤트가 모두 보존됨 (합집합 병합)
```

### 수정 전/후
| 시나리오 | 수정 전 | 수정 후 |
| :--- | :--- | :--- |
| UPBIT 저장 → KIS 저장 | UPBIT 이벤트 소실 | 양쪽 보존 |
| KIS 저장 → UPBIT 저장 | KIS 이벤트 소실 | 양쪽 보존 |
| 동일 이벤트 반복 저장 | 중복 누적 | 중복 제거 |

---

## 5. 회귀 테스트 결과

| 스위트 | 명령 | 결과 |
| :--- | :--- | :---: |
| M-1 신규 | `pytest CTrade/tests/test_history_merge.py` | **4 passed** |
| 전 영역 | `pytest CTrade/tests shared/tests CCommander/tests` | **179 passed** |
| Baker | `pytest CAiModelBaker/tests` | **48 passed** |
| 합계 | — | **227 passed, 0 failed** |
| 컴파일 | `py_compile history_store/simulator/kis_trader` | OK |

---

## 6. 변경 파일 목록 (M-1 관련)

```
 CTrade/utils/history_store.py   | (신규) save_history_merged / load_history_raw
 CTrade/core/simulator.py        | _save_history_sync → 병합 저장 위임
 CTrade/core/kis_trader.py       | _save_history_sync → 병합 저장 위임
 (신규) CTrade/tests/test_history_merge.py
```

---

## 7. 재현 커맨드 (재검증용)

```bash
cd /home/kuzimo/projects/CTradeSystem

# 1) M-1 회귀 테스트
.venv/bin/python -m pytest CTrade/tests/test_history_merge.py -v

# 2) 실제 클래스 종단 검증
cd CTrade && ../.venv/bin/python - <<'PY'
import sys, os, json, tempfile
sys.path.insert(0, os.path.abspath('..'))
from core.simulator import VirtualTrader
from core.kis_trader import KISTrader
hp = os.path.join(tempfile.mkdtemp(), 'trade_history.json')
vt = VirtualTrader(1e7, 5e-4, silent=False); vt.history_file = hp; vt.clear_history()
vt.add_history_event({'type':'BUY','symbol':'KRW-BTC','price':50000.0,'qty':0.1,'time':100.0,'exchange':'UPBIT'})
vt._save_history_sync()
class A: url_base='x'; acc_no='00000000'; acc_code='01'; access_token=''; app_key=''; app_secret=''
kt = KISTrader(A(), is_simulation=True); kt.history_file = hp; kt.history = []
kt.history.append({'type':'BUY','symbol':'069500','price':35000.0,'qty':10.0,'time':101.0,'exchange':'KIS'})
kt._save_history_sync()
d = json.load(open(hp, encoding='utf-8'))
assert sorted(e['symbol'] for e in d) == ['069500','KRW-BTC']
print('M-1 OK:', [(e['symbol'], e['type']) for e in d])
PY
```

---

## 8. 남은 이슈 및 다음 단계

| 우선순위 | ID | 내용 | 상태 |
| :---: | :--- | :--- | :---: |
| 다음 | M-2 | 테스트 레벨 Lv.1 쿨다운 캔들 단위(인터벌 무시) | OPEN |
| 이후 | M-3 | 병렬 피처 계산 순서 비결정성 | OPEN |
| 이후 | L-1 ~ L-8 | TZ·키워드·수수료·통합테스트 인프라 등 | OPEN |
| 후보 | H-4 | web_dashboard 백테스트 루프 비상손절 미구현/영문 라벨 | OPEN |

---

*본 결과 리포트는 2026-09-10 M-1 수정에 대한 검증 기록이며, 상세 발견 내역은 `CODE_REVIEW_20260910.md`를 참조하십시오.*

