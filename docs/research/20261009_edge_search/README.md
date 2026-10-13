# 2026-10-09 엣지 탐색 연구 스크립트

122630(KODEX 레버리지) 장중 엣지 탐색 → 일 단위 배분 전환 결론까지의 분석 스크립트 보존본.
모두 저장소 루트에서 `.venv/bin/python docs/research/20261009_edge_search/<파일>` 로 실행(데이터 경로가 루트 기준).
결론 요약은 Claude 메모리(project_daily_allocation_pivot, project_intraday_info_exhausted_122630,
project_overnight_premium_k200, project_close_reversal_122630)에 있음.

| 파일 | 내용 | 결론 |
|---|---|---|
| edge_scan_122630.py | 지평별 손익분기 IC·후보 신호, 장후반 모멘텀 | 마찰 미달 / 15:10 반전 발견 |
| late_reversal_122630.py, late_reversal_robust.py | 하락일 장마감 반전 엄격 검증 | 2년 t3.7 |
| futures_close_reversal_10y.py | 같은 규칙 선물 10년 | 2025~26만 유효(4/11년) |
| structural_scan.py | 갭·계절성·전일효과·급락반등 33개 | 통과 0 |
| cross_asset_scan.py | US500/NQ/HSI/USDJPY/금/BTC 선행 63조합 | 통과 0 |
| overnight_scan_fixed.py, overnight_risk.py | 오버나잇 vs 장중 분해·위험 | 10년 수익 전부 밤사이 |
| premium_scan.py | ETF↔2x선물 괴리 회귀 | 호가 튐(1분 지연 시 소멸) |
| existing_info_scan.py, semidiv_validate.py | 반도체 괴리 등 미사용 정보 | 겹친진입 착시, 실매매 0 |
| ofi_scan.py | Creon cvd 주문흐름 | 예측력 없음 |
| short_side_check.py | 숏 가정 | 미달 |
| ensemble_wf.py, ensemble_wf_long.py | 약신호 31개 결합 전진검증(30m~4h) | 미달 |
| daytype_scan.py | ORB·오전추세·Gao | 통과 0 |
| allocation_10y.py | 변동성목표+추세필터 일단위 배분 | 샤프 0.93, MDD 29% |
| shallow_gbm_vs_rule.py | 얕은 LightGBM vs 규칙 | 규칙 승(GBM 적중 50%) |
| vol_forecast_mix.py | 1분 RV HAR/GBM 변동성 예측 혼용 | 정확도↑ 성과 동일 |
| tg.py, autolab_reporter.py | 텔레그램 보고 도우미(TELEGRAM_BOT_TOKEN/CHAT_ID 환경변수) | 도구 |

주의: 일부 스크립트는 세션 스크래치패드 경로를 하드코딩(short_side_check 이전 버전 등) — 실행 전 경로 확인.
