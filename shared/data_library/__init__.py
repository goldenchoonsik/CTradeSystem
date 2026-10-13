# -*- coding: utf-8 -*-
"""
CTradeSystem - Data & Feature Library
====================================
종목 및 지표 시간단위별 관리, 원천 데이터 무손실 보존 및 온디맨드 조립 표준 라이브러리

[Gold 계층 진실 계약 (T-2, 2026-09-15)]
---------------------------------------
학습 데이터셋(Gold) 의 **유일한 생산자**와 **유일한 피처 계산 경로**를 다음과 같이 확정한다.

  · Silver→Gold 원천 결합(대장주/매크로) 및 학습 데이터셋 생산 (유일)
      CAiModelBaker.core.data_manager.run_dataset_build_pipeline
      → 출력: data/{SYMBOL}/data_snapshot_{INTERVAL}.csv (+ .meta.json)

  · Gold 피처 계산 (유일, Train/Serve 공용)
      shared.feature_engine.run_feature_engineering
      (KRX 세션 분리 / is_warmup / FEATURE_ALIASES / 의존성 위상 스케줄링 포함)

  · FeatureComposer.compose()
      온디맨드 **미리보기·검사용** 조인기. 학습 산출물을 덮어쓰지 않는다(순수 함수).

  · FeatureVault
      DEPRECATED — 피처 간 의존성을 해소하지 못해 생산 경로와 다른 값을 산출한다.

→ 신규 코드는 위 계약을 따르고, Gold 파일( data_snapshot_*.csv )에 쓰는 코드를 추가하지 말 것.
"""

try:
    from shared.data_library.ingestion_manager import IngestionManager   # Bronze
    from shared.data_library.candle_vault import CandleVault             # Silver
    from shared.data_library.feature_composer import FeatureComposer     # Gold (온디맨드 조인/미리보기)
    # ⚠️ DEPRECATED: Gold 피처 계산은 shared.feature_engine.run_feature_engineering 를 사용하세요.
    #    (하위 호환성을 위한 재수출만 유지 — 신규 코드에서 사용 금지)
    from shared.data_library.feature_vault import FeatureVault
except ImportError:
    IngestionManager = None
    CandleVault = None
    FeatureComposer = None
    FeatureVault = None

from shared.data_library.manifest_manager import ManifestManager     # 사이드카 매니페스트 거버넌스

__all__ = [
    "IngestionManager",
    "CandleVault",
    "FeatureComposer",
    "FeatureVault",   # DEPRECATED
    "ManifestManager",
]
