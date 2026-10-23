# [EXP-KRX-005] 2013~2024 한국 증시 슈퍼 급등주 사후 역추적 실험 로그

> ⚠️ **2026-10-21 정정: EXP-KRX-011·011-OPT·015·016·019 슬롯 스윙 계좌 수익률은 무효입니다**(손익 이중 계산, 시총 오염, MSCI 신규편입 명단 사후 선정). 재계산 결과 전부 불합격: Top5 −48%, MSCI 신규편입 K-Titan +25~269%·슈퍼루키 −28~+33%(같은 종목 그냥 보유 +684%), MSCI100 −43~−82%, 실시간 스크리너 −79~−82% → [K-Titan 감사](../20261021_k_titan_theme_swing/AUDIT_20261021.md) · [MSCI·미국 재계산](../20261021_slot_swing_recompute/README.md)

- **실행 일시**: 2026-10-21
- **실행 스크립트**: [`docs/research/20261021_krx_super_gainers_postmortem/super_gainers_analysis.py`](file:///w:/home/kuzimo/projects/CTradeSystem/docs/research/20261021_krx_super_gainers_postmortem/super_gainers_analysis.py)
- **리포트 산출물**:
  - 급등주 409선 전수 데이터셋: [`docs/research/20261021_krx_super_gainers_postmortem/super_gainers_list_2013_2024.csv`](file:///w:/home/kuzimo/projects/CTradeSystem/docs/research/20261021_krx_super_gainers_postmortem/super_gainers_list_2013_2024.csv)
  - 연구 종합 분석 보고서: [`docs/research/20261021_krx_super_gainers_postmortem/README.md`](file:///w:/home/kuzimo/projects/CTradeSystem/docs/research/20261021_krx_super_gainers_postmortem/README.md)

---

## 핵심 통계 요약

1. **표본 수**: 총 409개 (연간 +200% 이상 또는 연도별 TOP 20)
2. **시장 비중**: KOSDAQ 71.9% (294개), KOSPI 28.1% (115개)
3. **출발 시총 중앙값**: 819억 원 (1,000억 미만 57.7%, 3,000억 미만 82.1%)
4. **출발 시점 200일선 비율 중앙값**: 0.97배 (200일선 아래/걸친 상태에서 출발)
5. **출발 시점 52주 고점 대비 비율 중앙값**: 0.72배 (고점 대비 -28% 수준의 바닥권)
6. **초기 거래대금 폭증 배수**: 평균 4.95배 (3배 이상 폭증 31.7%)
7. **가속도 피크 이후 궤적**:
   - 피크 이후 평균 최대 낙폭(MDD): -33.4%
   - 피크 이후 -20% 이상 폭락 발생 비율: 73.0%
   - 피크 이후 -30% 이상 폭락 발생 비율: 53.0%


---

## [EXP-KRX-011-OPT] 코스피 시총 Top 5 대형주 스윙 파라미터 그리드 최적화 (2013~2024, 12개년)

- **스크립트**: [`docs/research/20261021_krx_super_gainers_postmortem/optimize_top5_swing_params.py`](file:///w:/home/kuzimo/projects/CTradeSystem/docs/research/20261021_krx_super_gainers_postmortem/optimize_top5_swing_params.py)
- **산출물**: [`docs/research/20261021_krx_super_gainers_postmortem/top5_swing_param_grid.csv`](file:///w:/home/kuzimo/projects/CTradeSystem/docs/research/20261021_krx_super_gainers_postmortem/top5_swing_param_grid.csv)
- **탐색 공간**: 총 144개 조합 (TP1: +5%, +7%, +10% / TP2: +10%, +15%, +20% / SL: -4% ~ -10% / Max_Days: 30, 40, 60일)

### 핵심 결과 요약
1. **손절선 스위트스팟: SL = -6% (절대 지배적)**
   - 순합산손익 기준 상위 1~7위가 모두 `SL = -6%`에 집중.
   - `-4%`: 대형주 일간 노이즈에 과다 손절 발생 (600회 이상 매매, 순합산 마이너스 전환).
   - `-10%`: 휩소는 줄어 승률은 48%로 오르나, 하락 사이클 진입 시 타격이 커 순합산손익 -10%~-77%, 누적수익 마이너스 추락.
   - `-6%`: 대형주 노이즈를 적절히 흡수하면서 추세 이탈 시 최소 손실로 방어하는 최적 지점.
2. **익절선 최적 조합: 1차 TP1 = +10%, 2차 TP2 = +20% (또는 +15%)**
   - 1차 +10%에서 절반 차익 실현 후 본전 방어, 잔량 50%는 +20% 도달 또는 피크 대비 -5% 트레일링 스탑.
3. **만기 최적값: 40영업일 (약 2개월)**
   - 대형주의 눌림목 반등 및 단기 상승 파동(평균 1.5~2개월)을 온전히 향유하는 최적 주기.
4. **골디락스 1위 세팅 성과**:
   - 파라미터: **TP1 +10% / TP2 +20% / SL -6% / 만기 40일**
   - 순합산손익: **+153.3%** (손익비 1.08, 승률 39.1%)
   - 계좌 누적수익률: **+223.7% (CAGR 10.4%, MDD -37.7%)**
   - 비교: 코스피 Top 5 단순보유(-47.8%) 및 코스피 지수(+19.4%)를 압도.


---

## [EXP-KRX-016] MSCI 신규 편입 핵심 대장주 대상 2대 전략 실측 비교 (2013~2024, 12개년)

- **스크립트**: [`docs/research/20261021_krx_super_gainers_postmortem/compare_msci_new_inclusions.py`](file:///w:/home/kuzimo/projects/CTradeSystem/docs/research/20261021_krx_super_gainers_postmortem/compare_msci_new_inclusions.py)
- **산출물**: [`docs/research/20261021_krx_super_gainers_postmortem/msci_new_inclusions_strategies.csv`](file:///w:/home/kuzimo/projects/CTradeSystem/docs/research/20261021_krx_super_gainers_postmortem/msci_new_inclusions_strategies.csv)
- **실측 표본**: 역대 MSCI Korea 지수에 신규 편입된 핵심 주도주 40개 종목 (에코프로, 포스코DX, 신풍제약, 한미반도체, 알테오젠, HD현대일렉트릭, HMM, 하이브 등)

### 핵심 결과
1. **K-Titan Pullback Swing (눌림목 고회전 스윙)**:
   - 누적수익률: **+4,543.2% (CAGR 38.3%)**, MDD: **-44.5%**, 샤프: **1.37**, PF: **1.24**, 순합산: **+967.2%**, 매매: 799회
   - 신규 편입주들의 폭발적 상승 파동 속에서 20일선 눌림목마다 +10%~+20%씩 반복 차익실현.
2. **슈퍼루키 (Super Rookie 바닥탈출 선취매 스윙)**:
   - 누적수익률: **+2,878.5% (CAGR 33.2%)**, MDD: **-41.3%**, 샤프: **1.23**, PF: **1.30**, 순합산: **+709.1%**, 매매: 382회
   - 바닥권 거래대금 폭증 + 기관/외인 20일 계단식 매집 초입에 진입하여 +50% 대시세까지 긴 호흡으로 수확.


---

## [EXP-KRX-017] MSCI 한국 지수 신규 편입 전후 개월수별 주가 궤적 전수 실측 (2014~2024, 58개 케이스)

- **스크립트**: [`docs/research/20261021_krx_super_gainers_postmortem/msci_post_inclusion_performance.py`](file:///w:/home/kuzimo/projects/CTradeSystem/docs/research/20261021_krx_super_gainers_postmortem/msci_post_inclusion_performance.py)
- **산출물**: [`docs/research/20261021_krx_super_gainers_postmortem/msci_post_inclusion_stats.csv`](file:///w:/home/kuzimo/projects/CTradeSystem/docs/research/20261021_krx_super_gainers_postmortem/msci_post_inclusion_stats.csv)

### 📊 시점별 주가 궤적 요약
1. **편입 전 (Inflow Window)**:
   - D-3개월: 평균 **+59.8%** (승률 87.0%)
   - D-2개월: 평균 **+34.0%** (승률 80.0%)
   - D-1개월: 평균 **+15.3%** (승률 75.0%)
2. **편입 후 (Post-Inclusion Hangover)**:
   - D+1개월: 평균 **-1.7%** (중앙값 -3.8%, 승률 39.7%)
   - D+3개월: 평균 **-0.3%** (중앙값 -6.2%, 승률 32.8%)
   - D+6개월: 평균 **-4.1%** (중앙값 -9.7%, 승률 34.5%)
   - D+12개월: 평균 **-12.0%** (중앙값 **-17.1%**, **승률 29.3% = 71% 하락**)
3. **편입 이후 평균 MDD**: **-35.1%** (중앙값 -31.5%)
4. **결정적 퀀트 공식**:
   - **진입**: 편입 리밸런싱 D-60일 ~ D-40일 전
   - **청산**: 리밸런싱 D-Day 당일 장마감 동시호가에 전량 매도
   - **금기**: 편입 이후 보유 금지 (71% 확률로 1년간 하락세 지속)


---

## [EXP-KRX-019] 실시간 MSCI 후보 스크리닝 백테스트 및 편입 적중률(Hit Rate) 실측 (미래참조 0%)

- **스크립트**: [`docs/research/20261021_krx_super_gainers_postmortem/realtime_msci_screener_backtest.py`](file:///w:/home/kuzimo/projects/CTradeSystem/docs/research/20261021_krx_super_gainers_postmortem/realtime_msci_screener_backtest.py)
- **탐색 룰**: 매일 아침 전 종목 대상 '시총 2.5조~4.5조 돌파(MSCI 편입 가시권) + 눌림목 + 외인/기관 5일 수급' 조건 실시간 스크리닝

### 📊 실측 결과 요약
1. **MSCI 편입 적중률 (Hit Rate)**:
   - 실시간 스크리닝 진입 종목: 총 138개
   - 실제 MSCI 편입 성공 종목: **33개 (적중률 `23.9%`, 약 4개 중 1개)**
   - 편입 탈락 종목 (False Positive): **105개 (76.1%)**
2. **성과 분해**:
   - **★ 실제 편입 성공주 (204회 매매)**: 승률 33.8%, **손익비 1.29**, **순합산손익 `+303.1%` (대박)**
   - **탈락주 (587회 매매)**: 승률 30.2%, **손익비 0.87**, **순합산손익 `-390.8%` (손절 누적)**
3. **전체 계좌 성과**:
   - 누적수익률 **+262.3% (CAGR 11.5%)**, MDD **-53.1%**, 순합산손익 **-87.7%**
4. **결론**:
   - 사용자의 통찰대로 **"MSCI 편입 적중률"**이 전략의 알파와 생존을 가르는 절대 변수임이 실측으로 입증됨.
