"""미국 주식·ETF 일봉 — Yahoo chart API 원천(브론즈) 수집·적재.

층(다른 원천과 같은 규약):
  DataVault/raw/yahoo/responses/chart_1d/<YYYYMMDD>/<심볼>.json.gz  받은 응답 그대로(읽기 전용) — 원천 CSV 를 다시 만들 근거
  DataVault/raw/yahoo/candle_1d/<심볼>_1d.csv                      무수정 일봉(공통 절차 raw_day_store.ingest_file — 날짜 원장·봉인·격리)
  DataVault/raw/yahoo/corporate_actions/<심볼>.csv                 분할·배당(무수정 기준 금액) — 수정주가는 정규화에서 계산
  DataVault/raw/nasdaqtrader/symdir/<YYYYMMDD>/                    미국 상장 종목 목록 원본

Yahoo 의 OHLC·거래량·배당액은 '분할만 반영된 수정값'이다(2026-10-15 실측: AAPL 1980-12-12 종가 0.1283 × 누적 분할 224 = 28.75,
2012-08 배당 0.0946 × 28 = 2.65). 원천에 수정값을 두면 다음 분할 때 과거 전체가 바뀌어 봉인과 충돌하므로, 분할 이력으로 되돌려 저장한다.
"""
