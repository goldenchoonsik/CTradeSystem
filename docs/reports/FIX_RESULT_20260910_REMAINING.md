# 🛠️ 잔존 이슈 수정 및 검증 리포트 (BUG-1 · INFRA-1 · DOCS-1)

| 항목 | 값 |
| :--- | :--- |
| **작업 일자** | 2026-09-10 |
| **작업 대상** | `CTrade/core/ai_strategy.py`, `CTrade/tests/test_ai_model_loader_spec.py`, `.gitignore`, `docs/ARCHITECTURE.md`, `CAiModelBaker/docs/STRATEGY.md`, `docs/STRATEGY.md` |
| **작업 목적** | 코드 정밀 검증 중 발견된 3대 잔존 결함(매수 진입 게이트 불완전, 락 파일 형상관리 누락, 3대 핵심 문서 동기화) 완전 해결 |
| **수정 담당** | Antigravity |
| **최종 검증 결과** | **247 passed, 0 failed** (CTrade+shared+CCommander 199건, CAiModelBaker 48건) |

---

## 1. 잔존 이슈 정의 및 수정 내역

### 1) BUG-1 (Critical/High) — 다중클래스 모델 매수 진입 시 `primary_is_buy` 조건 누락 및 `Signal` 파라미터 불일치
- **문제점**:
  1. [`CTrade/core/ai_strategy.py:618`](file:///home/kuzimo/projects/CTradeSystem/CTrade/core/ai_strategy.py#L618)에서 다중클래스 모델 추론 시 `primary_is_buy = (int(np.argmax(_raw[0])) == 1)` 및 `probability = float(_raw[0, 1])`을 산출하지만, [Step 4] 최종 매수 진입 조건(line 668)에서 `primary_is_buy` 검사가 누락되어 있었습니다. 이로 인해 클래스 0(하락) 확률이 더 높아도 클래스 1(상승) 확률이 임계값 이상이면 매수로 오진입할 수 있는 엣지 케이스가 존재했습니다.
  2. `Signal` 클래스 생성자에 `tp_price`, `sl_price`, `features_snapshot` 파라미터가 선언되어 있지 않아, 매수 신호 객체 생성 시 잠재적 `TypeError`가 발생할 수 있는 결함이 있었습니다.
- **수정 내용**:
  - `ai_strategy.py`: [Step 4] 매수 진입 판단 조건에 `and primary_is_buy` 조건 추가.
  - `ai_strategy.py`: `Signal.__init__`에 `tp_price`, `sl_price`, `features_snapshot` 인자 추가 및 `_evaluate_entry()` 호출부에서 계산된 `tp_price`, `sl_price`, `features_snapshot` 전달 완결.
  - `CTrade/tests/test_ai_model_loader_spec.py`: `test_multiclass_blocks_entry_when_primary_not_buy` 회귀 테스트에 `required_warmup_candles = 1` 웜업 설정 보정 및 `tp_price`, `sl_price`, `features_snapshot` 보존 검증 단언 완비.

### 2) INFRA-1 (Low) — `.gitignore`에 동시성 제어 락 파일(`*.lock`) 누락
- **문제점**:
  - M-1 수정으로 도입된 `history_store.py`가 동시성 제어를 위해 `trade_history.json.lock` 파일을 생성하나, `.gitignore`에 `*.lock`이 누락되어 런타임 락 파일이 git 추적 대상에 노출될 위험이 있었습니다.
- **수정 내용**:
  - [`.gitignore:64`](file:///home/kuzimo/projects/CTradeSystem/.gitignore#L64)에 `*.lock` 및 `**/*.lock` 추가.

### 3) DOCS-1 (User Rules 1, 2) — 3대 핵심 문서 실시간 최신화 및 동기화 무결성 확보
- **문제점**:
  - 19개 이슈(C-1~C-2, H-1~H-4, M-1~M-3, L-1~L-8)가 코드에 반영되었으나, 사용자 원칙 1번(실시간 최신화) 및 2번(동기화 무결성)에 따른 `docs/ARCHITECTURE.md`, `CAiModelBaker/docs/STRATEGY.md`, `docs/STRATEGY.md` 문서 최신화가 미진했습니다.
- **수정 내용**:
  - [`docs/ARCHITECTURE.md`](file:///home/kuzimo/projects/CTradeSystem/docs/ARCHITECTURE.md):
    - C-1: KIS 실전 매도 시 `VirtualTrader.remove_position(symbol)` C++ API 연동 및 매수(`force_set_position`)↔매도 완전 대칭 명시.
    - M-1: `CTrade/utils/history_store.py` (fcntl 배타 락 + 합집합 병합 + `os.replace` 원자적 교체) 아키텍처 반영.
    - H-1: `shared/tbm_executor.py: resolve_exit()` 단일 함수 기반 5대 청산 우선순위 명시.
    - H-2/H-3: 모델 배포 규격 단일화(`model_{interval}.txt`) 및 메타 승인 상수(`META_APPROVAL_THRESHOLD = 0.50`) 명시.
    - M-3: 피처 엔진 의존성 위상 레벨(Topological Level) 병렬 스케줄링 명시.
  - [`CAiModelBaker/docs/STRATEGY.md`](file:///home/kuzimo/projects/CTradeSystem/CAiModelBaker/docs/STRATEGY.md) & [`docs/STRATEGY.md`](file:///home/kuzimo/projects/CTradeSystem/docs/STRATEGY.md):
    - 청산 우선순위를 `resolve_exit()` 5단계(1. 당일청산 ➡️ 2. 비상손절 ➡️ 3. 일반손절 ➡️ 4. 일반익절 ➡️ 5. 시간만료)로 일원화 명시.
    - 메타 라벨링 최종 매매 판정에 `primary_is_buy == True` 게이트 및 0.50 상수 통일 반영.

---

## 2. 검증 결과 요약

```bash
./scripts/run_tests.sh
```

| 테스트 스위트 | 테스트 수 | 결과 | 소요 시간 |
| :--- | :---: | :---: | :---: |
| `CTrade/tests` + `shared/tests` + `CCommander/tests` | 199 | **199 passed** | 7.19s |
| `CAiModelBaker/tests` | 48 | **48 passed** | 13.95s |
| **전체 합계** | **247** | **247 passed, 0 failed** | **21.14s** |

---

## 3. 최종 결론
- 본 작업으로 커밋 대기 중인 모든 이슈(초기 19건 + 잔존 3건)가 100% 완전 해결 및 실증 검증되었습니다.
- 코드와 3대 핵심 문서 간의 동기화 무결성(100%)을 확보하였으며, 안전하게 커밋할 수 있는 상태입니다.
