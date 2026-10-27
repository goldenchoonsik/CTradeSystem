# 주가 관련 법칙·이상현상 문헌 정리 (2026-10-26)
출처: 제 지식 기반 목록(웹 재검증 안 함). 저자·연도는 기억에 근거하므로 인용 전 확인 필요. "검증"란은 이 저장소에서 이미 시험했는지 여부.

## A. 횡단면 팩터 (종목 간 수익 차이)
| 현상 | 대표 논문 | 내용 | 이 저장소 검증 |
|---|---|---|---|
| 소형주 | Banz 1981 | 시총 작은 주식 초과수익 | 한국 일부 있음(초소형·후반 소멸), 미국 없음 |
| 가치(저PER·저PBR) | Basu 1977, Rosenberg-Reid-Lanstein 1985, Fama-French 1992·1993 | 저평가주 우위 | 한국 약함, 미국 ETF 없음 |
| 모멘텀(12−1) | Jegadeesh-Titman 1993, Carhart 1997 | 과거 승자가 계속 | 한국 대형주만 유효 후보 |
| 단기 반전(1개월) | Jegadeesh 1990, Lehmann 1990 | 지난달 패자가 반등 | 한국 효과 없음 |
| 장기 반전(3~5년) | De Bondt-Thaler 1985 | 장기 패자가 반등 | 미검증 |
| 52주 고가 근접 | George-Hwang 2004 | 고점 근처 종목 우위 | 미검증(gold에 hi_250_ratio 있음) |
| 산업 모멘텀 | Moskowitz-Grinblatt 1999 | 업종 단위 추세 | 미검증 |
| 수익성 | Novy-Marx 2013, Fama-French 2015(5팩터) | 매출총이익/자산 높은 종목 | 미검증(fundamentals_q 있음) |
| 투자·자산성장 | Titman-Wei-Xie 2004, Cooper-Gulen-Schill 2008 | 투자·자산 급증 기업 이후 부진 | 미검증 |
| 발생액(accruals) | Sloan 1996 | 현금 뒷받침 없는 이익 → 이후 부진 | 미검증 |
| 순주식발행 | Loughran-Ritter 1995, Pontiff-Woodgate 2008, Ritter 1991(IPO) | 증자·IPO 기업 이후 부진 | 일부(IPO ETF 성적, 자사주) |
| 자사주 매입 | Ikenberry-Lakonishok-Vermaelen 1995 | 매입 공시 후 초과수익 | 한국 자사주 일부 |
| 저베타·저변동성 | Black-Jensen-Scholes 1972, Ang et al. 2006, Frazzini-Pedersen 2014 | 위험 낮은 주식이 위험조정 우위 | 일부(3년 꾸준한 상승주 연구) |
| 복권형 주식(MAX) | Bali-Cakici-Whitelaw 2011 | 하루 급등 경험 종목 이후 부진 | 미검증 |
| 비유동성 | Amihud 2002 | 거래 안 되는 주식 프리미엄 | 미검증(adv_20 있음) |
| 실적 후 표류(PEAD) | Ball-Brown 1968, Bernard-Thomas 1989 | 서프라이즈 방향 지속 | 한국·미국 시험(비용 후 미달, 미국 약함) |
| 애널리스트 | Womack 1996 | 등급 변경 후 표류 | 시험(표류 없음) |
| 내부자 매수 | Seyhun 1986, Lakonishok-Lee 2001 | 임원 매수 후 초과 | 시험(소형주에서만 남음) |
| 공매도 잔고 | Asquith-Pathak-Ritter 2005 | 잔고 높은 종목 이후 부진 | 미검증(자료 없음) |
| 지수 편입 | Shleifer 1986, Harris-Gurel 1986 | 편입 발표 급등 | 시험(2022~ 사라짐) |

## B. 시장 전체 예측 (시계열)
| 현상 | 대표 논문 | 내용 | 검증 |
|---|---|---|---|
| 평균 회귀 | Fama-French 1988, Poterba-Summers 1988 | 장기 수익률 음의 자기상관 | 폭락 후 시험(추가 이득 없음) |
| 배당수익률·CAPE | Campbell-Shiller 1988, Shiller 1981 | 비싼 시장은 10년 수익 낮음 | 시험(10년 유효, 1년 무효) |
| 표본외 한계 | Welch-Goyal 2008 | 대부분의 예측변수가 표본외 실패 | 일관됨 |
| 시계열 모멘텀 | Moskowitz-Ooi-Pedersen 2012, Faber 2007 | 자산별 12개월·10개월 이평 추세 | 추세필터 시험(낙폭 보험, 수익은 보유 이하) |
| 변동성 목표 | Moreira-Muir 2017 | 변동성 높을 때 비중 축소 | 시험(샤프 개선 제한적) |
| 변동성 프리미엄 | Carr-Wu 2009, Bollerslev-Tauchen-Zhou 2009 | 내재변동성 > 실현변동성 | 미검증 |
| 과잉변동성 | Shiller 1981 | 가격 변동이 배당 변동으로 설명 안 됨 | 간접 |

## C. 캘린더·이벤트
| 현상 | 대표 논문 | 이 저장소 결과 |
|---|---|---|
| 1월 효과 | Rozeff-Kinney 1976, Keim 1983, Reinganum 1983 | 없음 |
| 주말·월요일 | French 1980, Gibbons-Hess 1981 | 2000년 이후 소멸 |
| 월말·월초 | Ariel 1987, Lakonishok-Smidt 1988 | 최근 소멸 |
| 5월에 팔아라 | Bouman-Jacobsen 2002 | 방향 유지·최근 소멸 |
| 대통령 사이클 | Huang 1985, Santa-Clara-Valkanov 2003(민주당 우위) | 3년차 유지, 대선 해 효과 없음 |
| 같은 달 계절성 | Heston-Sadka 2008 | 미검증 |
| 오버나잇 vs 장중 | Lou-Polk-Skouras 2019 | K200 오버나잇 프리미엄 시험 |
| 실적발표 프리미엄 | Savor-Wilson 2013 (거시), Frazzini-Lamont 2007(실적) | 미검증 |
| FOMC 전 드리프트 | Lucca-Moench 2015 | 미검증 |
| 날씨·일조량 | Hirshleifer-Shumway 2003, Kamstra et al. 2003 | 미검증 |
| 전쟁·지정학 | Rigobon-Sack 2005, Schneider-Troeger 2006 | 일관된 반등 없음 |

## D. 행동·미시구조
Kahneman-Tversky 1979(전망이론), De Long et al. 1990(노이즈 트레이더), Shleifer-Vishny 1997(차익거래 한계), Odean 1998(처분 효과), Barber-Odean 2000(과매매는 해롭다), Barberis-Shleifer-Vishny 1998 / Daniel-Hirshleifer-Subrahmanyam 1998 / Hong-Stein 1999(반응 과소·과잉 모형).

## E. 기술적 분석
Brock-Lakonishok-LeBaron 1992(이평 매매 유효), Sullivan-Timmermann-White 1999(자료 탐색 보정 후 표본외 실패), Lo-Mamaysky-Wang 2000(패턴 일부 정보), Park-Irwin 2007(서베이: 비용·표본외에서 대체로 소멸). 페어 트레이딩: Gatev-Goetzmann-Rouwenhorst 2006(1962~2002 유효, 이후 약화).

## F. 메타 연구 (가장 중요)
- Harvey-Liu-Zhu 2016: 316개 팩터 정리, 다중검정 보정 시 t≥3.0이 필요 → 대부분 기준 미달.
- McLean-Pontiff 2016: 97개 이상현상, 표본외 수익 약 26% 감소, 발표 후 약 58% 감소(알려진 뒤 약해짐).
- Hou-Xue-Zhang 2020: 452개 재현 시 65%가 t≥1.96 미달(미세 주식 제외 시 더 심함).
- Jacobs-Müller 2020: 미국은 발표 후 약화, 해외는 덜 약화.
- Chen-Zimmermann 2022: 오픈소스 재현은 이전 평가보다 재현율 높음(반론).
- Welch-Goyal 2008: 시장 예측변수 대부분 표본외 실패.

## 요약 판단
발표 후 약화가 일반적(약 절반)이지만 완전 소멸은 아님. 한국은 문헌 대부분이 미국 기준이라 직접 검증이 필요. 미검증 후보 중 한국 데이터로 바로 시험 가능한 것: 52주 고가 근접, MAX(복권형), 비유동성, 수익성(GP/A), 자산성장, 발생액, 저변동성, 업종 모멘텀.
