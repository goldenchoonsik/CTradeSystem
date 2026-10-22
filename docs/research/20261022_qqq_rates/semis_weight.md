# QQQ 반도체 비중 (2026-10-20~21 기준 추정)

- 원천: 상위 25 = stockanalysis.com QQQ 보유(10-20, 주식 수×가격 검산 일치), 나머지 = Nasdaq API 나스닥100 시총(10-21) × 중형 무리 비중/시총 비율(4.28%/조달러).
- ASML·ARM(ADR)은 상위 25 밖 → 각각 1.00% 미만(지수 주식 수가 본국 전체가 아님으로 보임) → 0~1% 범위.
- **반도체 약 30% (29~31%)**: NVDA 8.15 · MU 4.36 · AMD 3.76 · AVGO 2.48(분기 재조정 때 대형주 묶음으로 감축) · INTC 2.08 · LRCX 1.42 · AMAT 1.39 · TXN 1.09 · MRVL·KLAC·SNDK·ADI·QCOM 각 ~0.8~0.9 · ASML·ARM <1 · 기타 소형 ~1.
- 메모리(MU+SanDisk) ~5.2%, 저장장치(STX·WDC) 포함 ~6.5%. 엔비디아 단독 8%, 상위 3 반도체 16%.
- 스크립트: semis_weight.py (ndx.json 은 scratchpad, 휘발).
