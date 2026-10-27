# 인수인계 (2026-10-26): 외국인 자금 경로 → 미국 연결 → 동행성 종목 선택 전략

## 핵심 결론
1. 외국인 순매수=실제 해외 자금(국제수지 상관 0.95). 일 단위는 한국 장이 결정, 미국·달러·VIX 설명력 ≈0. 2026 매도(−$141bn)의 상대방은 패시브 ETF 아님(ETF 25개 +$15.8bn), 하이닉스 ADR 신주(약 $26.5bn)는 순매수 밖. → docs/research/20261025_foreign_channels/results.md 9절.
2. 업종 수출은 주가보다 3~6개월 뒤(확인지표). → 20261025_export_leading/results.md.
3. 미국→한국 하루 선행 매매는 불합격(시가 갭에 반영, 16조합). 사슬(NVDA→메모리→소부장)은 동행 강·지연 선행 0. → 20261026_us_kr_pairs, 20261026_causal_chain.
4. **미국(S&P500) 동행성 상위 20% 장기 보유**: 고−저 12개월 +13.4%p(9/11년), 8설정 견고, 전략 환산 연1회 CAGR 9.8%·반기 13.1%(코스피 7.6%, 상위300 5.7%), 2026 사후 +61% vs +51%, 낙폭 −47~−51%. → 20261026_comove_select/results.md (PREREG*.md + sha256).
5. 보조: 사슬 폭 필터(메모리 낙폭 −43→−30%, 샤프 개선 미미·소부장은 자기 50일선이 나음), 엔비디아 실적 하락 갭→한국 메모리 −3.3%, 미국 장비·MU 하락 전환→한국 반도체 1~5일 약 −1%p. 전환 즉시 매도·동행 상관 조건은 불합격.

## 열린 질문(우선순위)
- 업종 중립 버전(같은 업종 안 동행성 비교)·외국인 지분율 통제·수출/대미 노출과의 연결(논리 직접 검증).
- 중심(동행성 포트) + 방어(사슬 폭 필터) 결합 사전 고정 시험(미실시).
- 비용 현실화(연속 보유 종목 재조정·슬리피지), 낙폭 허용 범위.

## 한계·함정
- 소부장 10종(한미반도체·원익IPS·주성엔지니어링·이오테크닉스·피에스케이·테스·유진테크·HPSP·리노공업·이수페타시스)은 사후 선택(PREREG_regime에 '9종'으로 오기, 실제 10종).
- 코스피는 가격지수(배당 제외), 형성 표본 11개 연도, 사후 구간은 반도체 초강세 10개월.
- 외국인 연구 미수집: 뱅가드 VXUS·VEA 이력, 금감원 원문 시계열, 스왑 헤지 귀속, 삼성 GDR 전환.

## 스크립트·데이터 위치
- 외국인: docs/research/20261025_foreign_channels/ (channels.py·bop.py·etf_*.py·recon.py·foreign_state.py, etf/hist·raw_xls·nport), DataVault/staging/kr_investor_daily
- 수출: docs/research/20261025_export_leading/, DataVault/staging/customs_export_monthly (OPEN_DATA_KEY, Itemtrade API)
- 미국 연결: docs/research/20261026_us_kr_sector·_us_kr_pairs·_causal_chain·_comove_select (venv: .venv/bin/python, system python엔 numpy 없음)
- 모든 변경 미커밋(git status 확인 후 사용자 승인 시 커밋).
