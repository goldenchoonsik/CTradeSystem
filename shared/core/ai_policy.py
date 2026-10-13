"""
CTradeSystem Shared - AI 진입 정책 상수 (학습/실전 단일 소스)

메타 라벨링(2차 모델) 승인 임계값 등 학습(Backtester / WFA 검증 / OOS / web_dashboard)과
실전(CTrade)이 반드시 동일하게 사용해야 하는 정책 상수를 한 곳에 정의한다.

정책 근거: docs/CAiModelBaker/STRATEGY.md §13 "삼진 분류 트리플 배리어 및 메타 라벨링"
  - 1차 모델 상승 확률 >= buy_threshold 이고,
  - 2차(메타) 모델 승인 신뢰 확률 >= META_APPROVAL_THRESHOLD (0.50) 를 동시 만족할 때만 진입 승인
"""

# 2차(메타) 모델 승인 신뢰 확률 임계값 (문서 정책: 0.50)
META_APPROVAL_THRESHOLD = 0.50
