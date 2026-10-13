"""한국 전 종목 연구 저장소(DataVault/staging/kr_equity) — 수집·패널 빌드.

종목 원장(symbols_registry)·동기화·Raw Store 밖의 연구용 저장소다. 층:
  raw/<출처>/<종류>/   받은 원본 그대로(읽기 전용, 덮어쓰기 없음 — 예외: '자료 없음' 응답은 다음 수집에서 갱신)
  reference/          raw 에서 다시 만들 수 있는 작은 표(유니버스·상폐 목록·Creon 상장 스냅샷)
  derived/            분석용 parquet 캐시(지워도 build_panel 로 재생성)
  collect/            수집 작업 상태(<source>.json) — 대시보드 '연구 데이터셋' 패널이 읽는다
  logs/               수집·빌드 로그

단계(STEPS)는 모두 '지금 상태에서 최신까지 채우기'다 — 몇 번을 돌려도 같은 결과, 받은 건 건너뛴다.
Linux 단계: shared.data_library.kr_equity.cli, Windows(Creon) 단계: CAiModelBaker/core/universe_driver.py --stage kr_equity.
"""
