# ADR Log (AI 간 의사결정 기록)

다중 AI(Gemini, Claude, DeepSeek 등)가 아키텍처 결정과 이견을 남기는 채널. 규칙:
1. 다른 AI가 만든 DAG·핵심 로직을 독단으로 되돌리지 않는다.
2. 구조적 결함이나 대안은 아래에 `[무전 발신: 모델명]`으로 남기고 사용자(최종 결정권자) 승인을 받는다.
3. 확정된 결정이 규칙이 되면 [../ARCHITECTURE.md](../ARCHITECTURE.md) §3 또는 해당 하위 문서에 반영한다.

## 기록

* **[2026-09-30 | Gemini (Head)] [합의 확정: 단계별 파일 입출력 DAG 및 4대 헌법 수립]**
  - **결정 배경**: 5단계 지표 스크리닝이 4단계 Gold 스냅샷을 건너뛰고 2단계 실버 Parquet을 읽어 참조 대장주 지표가 누락되는 문제 해결.
  - **합의 원칙**: 
    1. 스크리너/학습기는 오직 대장주가 시점 결합된 골드 데이터(`data_snapshot_*.csv` 또는 `data_vault_preview_*.csv`)만 읽는다.
    2. 실버 Parquet 파일에는 `ref_` 접두사를 절대 붙이지 않는다.
    3. `data_snapshot`은 순수 시세 베이스이며, 개별 지표 명세는 `model/feature_spec.json`에만 박제한다.

* **[2026-09-30 | Gemini] [합의 확정: 타 자산군(Crypto) 지표의 KRX 프로젝트 오염 원천 차단 및 템플릿 정비]**
  - **결정 배경**: KRX ETF(`069500`) 프로젝트 생성 시 `defaults.json`/`active.json`에 잔존하던 코인용 지표(`binance_leading_momentum` 등)가 복제되어 '부적합 지표' 경고가 반복되는 문제 해결.
  - **합의 원칙**:
    1. `defaults.json` 템플릿의 지표 풀은 전 자산 공통 지표(`target_*`)만 유지.
    2. 프로젝트 생성(`api_create_project`) 시 `filter_compatible_features`를 필수 통과시켜 부적합 지표와 해당 파라미터/페널티를 사전 필터링 후 스냅샷 저장.

* **[2026-09-30 | Gemini (Head)] [합의 확정: 타깃 사정거리 및 물리 선험 가이드(Physics Floor) Feature Store 단일 창구 통합 및 지평 왜곡 해소]**
  - **결정 배경**:
    1. Feature Store의 `사정거리 설정`과 AutoLab의 `물리적 표준값 적용`이 동일한 `applyPhysicsDefaultsToUI()` 함수를 공유하면서도 UI가 분리되어 있어 사용자 혼선 발생 및 DOM ID 중복 존재.
    2. 과거 하드코딩된 `Math.max(..., 12)` / `Math.max(..., 18)` 지평 하한으로 인해 저변동성 KRX 지수 ETF(1분봉)의 물리적 적정 사정거리(6봉/10봉)가 왜곡되어 18봉 동안 보유하다 노이즈에 청산되는 전략 왜곡 발생.
  - **합의 원칙**:
    1. **단일 창구화(SSOT)**: 마찰비용 실측치, ATR, TP/SL Floor, First Passage 최소 사정거리를 Feature Store 탭의 `[🎯 타깃 사정거리 & 물리 선험 가이드]` 카드로 완전 통합하고, AutoLab 탭은 안내 배너로 단일화.
    2. **물리 하한치 동적 정밀화**: `physics_constraint.py` 및 `features.js`의 12/18 하드코딩을 제거하고 실측 `min_horizon_candles` 및 비례 지평(`rec_horizon`, `max_hold`)으로 유연하게 연동.
    3. **스냅샷 동기화**: 세션 스냅샷 `TARGET_HORIZON_CONFIG`를 물리 실측치(`predict_ahead: 6`, `max_hold: 10`)로 일치시켜 IC 스크리닝과 모델 라벨링의 완전 일치 보장.

* **[2026-09-30 | Gemini (Head)] [합의 확정: Feature Store 4단계 정규 폭포수 파이프라인 재배치 및 레거시 군더더기 제거]**
  - **결정 배경**:
    1. 기존 UI가 `프리셋(1행) ➔ 참조종목(2행) ➔ 사정거리(3행)`로 배치되어, 사정거리(골대)를 먼저 정하고 스크리닝을 돌려야 하는 퀀트 데이터 인과관계가 역전되고 시선/마우스가 상하로 핑퐁하는 심각한 동선 왜곡 발생.
    2. 매매 집행 옵션(`전략 기본 & 세션 제어`) 카드 및 `전체 지표 추가`, `인터벌 권장값`, `파라미터 최적화` 등 불필요한 사족 버튼이 시각적 노이즈를 유발.
  - **합의 원칙**:
    1. **1 ➔ 2 ➔ 3 ➔ 4 단일 폭포수 재배치**: `1단계: 참조종목 & Gold스냅샷` ➔ `2단계: 물리표준값 & 사정거리(6봉/10봉)` ➔ `3단계: 지표 스크리닝 & Top IC 자동선정/프리셋` ➔ `4단계: 활성 지표 목록/튜닝`으로 완전히 일직선화.
    2. **사족 5대 요소 전면 제거**: 피처스토어 최상단 거시필터/당일청산 카드 제거, 전체 지표 추가/인터벌 권장값/파라미터 최적화 버튼 제거, 사정거리 모드 영역 슬림화.
    3. **UI 무결성 보장**: 지표 전체 해제(`clearAllFeatures()`) 신설 및 DOM null-safe 방어 완료.

* **[2026-09-30 | Gemini (Head)] [합의 확정: VS Code 실행 환경 동적화 및 물리 표준값(max_hold) UI 정합성 일원화]**
  - **결정 배경**:
    1. VS Code 실행 설정(`.vscode/launch.json`, `.vscode/settings.json`)에 과거 분리 가상환경 시절의 `CTrade/.venv` 경로가 하드코딩되어 디버거 구동 시 `FileNotFoundError` 발생.
    2. `features.js`의 `applyPhysicsDefaultsToUI()` 내에서 최대 보유시간 DOM 주입 및 Alert 문구에 `maxHold` 변수 대신 사정거리 변수(`p.min_horizon_candles`)가 오타/하드코딩되어 화면 수치와 알림창 수치 불일치 발생.
  - **합의 원칙**:
    1. **VS Code 인터프리터 동적 상속**: `.vscode/launch.json`의 하드코딩된 `"python"` 속성을 전면 제거하고, `.vscode/settings.json`의 `"python.defaultInterpreterPath"`를 루트 통합 가상환경(`${workspaceFolder}/.venv/bin/python`)으로 일원화하여 워크스페이스 활성 환경을 자동 상속하도록 개선.
    2. **물리 표준값 변수 무결성**: `features.js`의 `max_hold_candles_range` 및 Alert 문구에 동적 계산된 `maxHold` 변수를 100% 바인딩하여 10초봉 기준 타깃 사정거리(6캔들) 및 최대 보유시간(12캔들)이 UI 입력창과 알림창에 완전 동기화되도록 보장.

* **[2026-10-01 | Claude] [문서 구조 개편: ARCHITECTURE.md 분할]**
  - **배경**: 단일 문서가 2,038줄·280KB로 커져 AI 컨텍스트를 과도하게 소모하고, `shared/` 재배치(core/data_library/features) 이후 경로 다수가 낡았으며, 일부 내용이 현재 코드와 모순(예: `sanitize_post_join`의 `bfill().ffill()`, 체제 지표 IC 무검정 면제, 홀드아웃 하한 20일).
  - **결정**: `docs/ARCHITECTURE.md`는 불변 규칙(ID 부여)·SSOT 위치·파이프라인·문서 지도만 담는 코어로 축소. 세부는 `docs/architecture/` 하위 문서로 분리. 수식어·UI 픽셀·시점 의존 수치·[NEW] 태그 제거. 경로·상수는 코드 기준으로 교정. 원문은 git 이력(`660caee`)에 보존.

* **[2026-10-01 | Claude] [사용자 승인 확정: 전역 active.json 은퇴 — '자유 모드' 제거]**
  - **결정 배경**: 전역 `CAiModelBaker/config/active.json`(+복제본 `active_config.json`)이 프로젝트 격리 이후에도 대체 경로로 읽히고 쓰였다. `CAiModelBaker/core/feature_registry.py`의 import 시 `set_config_root(<Baker>)` 때문에 포인터를 못 찾아, 연구 루프·대시보드가 열린 프로젝트가 아닌 전역 파일의 지표 설정으로 학습·저장했다(은퇴 시점 내용: 삭제된 TEST2 세션ID + S10 지표 26개 혼재). conftest 가 그 호출을 막아 테스트는 이를 보지 못했다. 관련 사고: 09-28 로그 혼입, T-7 탐색 풀 드리프트, T-9 지평 되돌림, 10-01 1분봉 연구가 10초봉 지표로 실행.
  - **합의 원칙** (사용자: "AI가 혼란이 없도록 하는 게 더 중요"):
    1. 설정 원본은 열린 프로젝트의 `config_snapshot.json` 하나. 열린 프로젝트가 없으면 저장 거부, 포인터가 깨지면 오류 — 어느 경우에도 전역 파일로 대신 가지 않는다.
    2. 전역 `active.json`·`active_config.json`은 `config/legacy/` 보관본. **되살리거나 대체 경로를 추가하지 말 것** (래칫: `tests/test_project_isolation.py`).
    3. `set_config_root()`는 테스트 전용. 종목·인터벌을 `common.json`에 쓰지 않는다.
    4. 테스트도 운영과 같은 '프로젝트가 열린' 경로로 돈다(conftest 사본 프로젝트).
