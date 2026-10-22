# 전진 확인 트랙 목록 (고쳐 쓰지 않음 — 추가만)

기록 파일 `DataVault/staging/global_macro/reading/forward_log.jsonl`(append-only), 채점 `reading_eval.forward(track=…)`, 규칙은 `PREREGISTRATION_reading_engine_D.md`(24개월 1차 / 36개월 최종).

| 트랙 | 점수 | 고정일 | 근거 사전등록 | 고정 시점 과거 재현(판정에 안 씀) | 상태 |
|---|---|---|---|---|---|
| D | z(수요 충격) 만 | 2026-10-22 (판독 2026-10-31~) | PREREGISTRATION_reading_engine_D.md | IC +0.157 t3.5, 상위2−하위2 +5.1%p | 대기 |
| CE | z(EU 경쟁 성분) 만 | 2026-10-22 (판독 2026-10-31~) | PREREGISTRATION_stage4_eu.md E2 조치 | IC +0.040 t0.6 (2017~ +0.001) | 대기 |
| V | z(부가가치 가중 수요 충격 D^VA) — TiVA FFD_DVA 노출(ICIO 승수) | 2026-10-22 (판독 2026-10-31~) | PREREGISTRATION_reading_engine_V.md | IC +0.111 t2.4 (~2016 +0.158 · 2017~ +0.065), D 와 상관 0.77 — D 보다 낮음(results_reading_v.md, 판정에 안 씀) | 대기 |

판정이 나면 이 표에 날짜·결과를 적고, 확정 트랙만 운영 판독(합성)을 교체한다.

- 2026-10-22: 대응표를 `reference/relations_ledger.json`(a4e93d6d…)으로 옮김 — reading.py 파일 해시는 바뀌었지만 D·CE 점수 정의·대응표 값은 동일(테스트 fixture 로 고정). 트랙 판정에 영향 없음.
