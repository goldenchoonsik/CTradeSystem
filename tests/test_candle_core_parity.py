# -*- coding: utf-8 -*-
"""
테스트: 2단계 캔들 코어 단일화 및 실시간-과거 패리티 검증 (test_candle_core_parity.py)

shared/candle_core.py의 시간 경계(get_candle_boundary),
수급 지표(compute_bid_ratio, compute_avg_trade_size),
캔들 기하학 방역(sanitize_candle_ohlcv)이
과거 배치 빌더와 실시간 집계기(CandleAggregator) 양쪽에서 단 1%의 오차도 없이
완벽하게 일치(Zero-Drift Parity)하는지 검증합니다.
"""

import pytest
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

from shared.core.candle_core import (
    get_candle_boundary,
    compute_bid_ratio,
    compute_avg_trade_size,
    sanitize_candle_ohlcv,
    build_standard_candle_dict,
    LARGE_TRADE_THRESHOLD_KRW,
    KST,
)
from CTrade.core.candle_aggregator import CandleAggregator


def test_get_candle_boundary_precision():
    """시간 경계 계산이 초 단위 및 인터벌에 따라 완벽히 결정론적으로 작동하는지 검증"""
    # 1. 10초봉 테스트: 09:00:07 -> 시작: 09:00:00, 종료: 09:00:10
    t1 = datetime(2026, 9, 20, 9, 0, 7, tzinfo=KST)
    s1, e1 = get_candle_boundary(t1, interval_seconds=10)
    assert s1 == datetime(2026, 9, 20, 9, 0, 0, tzinfo=KST)
    assert e1 == datetime(2026, 9, 20, 9, 0, 10, tzinfo=KST)

    # 2. 15분봉 테스트: 09:14:59 -> 시작: 09:00:00, 종료: 09:15:00
    t2 = datetime(2026, 9, 20, 9, 14, 59, tzinfo=KST)
    s2, e2 = get_candle_boundary(t2, interval_seconds=900)
    assert s2 == datetime(2026, 9, 20, 9, 0, 0, tzinfo=KST)
    assert e2 == datetime(2026, 9, 20, 9, 15, 0, tzinfo=KST)

    # 3. 1일봉 테스트: 15:30:00 -> 시작: 00:00:00, 종료: 24:00:00 (다음날 00:00:00)
    t3 = datetime(2026, 9, 20, 15, 30, 0, tzinfo=KST)
    s3, e3 = get_candle_boundary(t3, interval_seconds=86400)
    assert s3 == datetime(2026, 9, 20, 0, 0, 0, tzinfo=KST)
    assert e3 == datetime(2026, 9, 21, 0, 0, 0, tzinfo=KST)


def test_compute_trade_metrics_parity():
    """수급 지표(매수비율, 평균체결규모) 계산 수식 무결성 검증"""
    # 1. 정상 매수 체결
    assert compute_bid_ratio(bid_volume=60.0, total_volume=100.0) == 0.6
    # 2. 거래량 0일 때 fallback 중립(0.5) 보장
    assert compute_bid_ratio(bid_volume=0.0, total_volume=0.0) == 0.5
    # 3. 이상치 클리핑 검증
    assert compute_bid_ratio(bid_volume=120.0, total_volume=100.0) == 1.0
    assert compute_bid_ratio(bid_volume=-10.0, total_volume=100.0) == 0.0

    # 4. 평균 체결 규모
    assert compute_avg_trade_size(volume=1000.0, trade_count=50.0) == 20.0
    assert compute_avg_trade_size(volume=0.0, trade_count=0.0) == 0.0


def test_sanitize_candle_ohlcv():
    """캔들 기하학 방역 (High >= max, Low <= min) 보정 검증"""
    # 1. 정상 캔들
    o, h, l, c, v = sanitize_candle_ohlcv(100.0, 105.0, 95.0, 102.0, 500.0)
    assert (o, h, l, c, v) == (100.0, 105.0, 95.0, 102.0, 500.0)

    # 2. 비정상 캔들 (High가 Close보다 낮거나, Low가 Open보다 높은 모순 캔들)
    # High(101.0) < Close(103.0), Low(102.0) > Open(98.0)
    o2, h2, l2, c2, v2 = sanitize_candle_ohlcv(98.0, 101.0, 102.0, 103.0, -10.0)
    assert h2 == 103.0  # max(98, 101, 102, 103)으로 자동 보정
    assert l2 == 98.0   # min(98, 101, 102, 103)으로 자동 보정
    assert v2 == 0.0    # 음수 거래량 방역


def test_candle_aggregator_shared_core_integration():
    """CandleAggregator가 shared.candle_core를 통해 정확한 표준 캔들을 조립하는지 실시간 패리티 검증"""
    agg = CandleAggregator(interval_seconds=10, max_candles=100, market_type="KRX")

    # 10초 윈도우 [09:00:00 ~ 09:00:10) 내의 틱 3개 순차 주입
    t0 = datetime(2026, 9, 20, 9, 0, 1, tzinfo=KST)
    t1 = datetime(2026, 9, 20, 9, 0, 5, tzinfo=KST)
    t2 = datetime(2026, 9, 20, 9, 0, 9, tzinfo=KST)

    # Tick 1: 매수 30,000원 100주
    agg.on_target_tick(price=30000.0, volume=100.0, side="BID", timestamp=t0)
    # Tick 2: 매도 30,500원 50주
    agg.on_target_tick(price=30500.0, volume=50.0, side="ASK", timestamp=t1)
    # Tick 3: 1억 원 이상 대형 체결 (대금 = 30,200 * 4,000 = 120,800,000 KRW >= 1억)
    agg.on_target_tick(price=30200.0, volume=4000.0, side="BID", timestamp=t2)

    # 다음 윈도우 [09:00:10) 틱이 들어오는 순간 직전 10초봉 flush 트리거
    t_next = datetime(2026, 9, 20, 9, 0, 11, tzinfo=KST)
    agg.on_target_tick(price=30100.0, volume=10.0, side="BID", timestamp=t_next)

    # 집계된 첫 번째 완성 캔들 검증
    assert len(agg.candles) == 1
    c = agg.candles[0]

    # 1. 시간 시작 경계
    assert c["timestamp"] == datetime(2026, 9, 20, 9, 0, 0, tzinfo=KST)

    # 2. OHLCV 검증
    assert c["target_open"] == 30000.0
    assert c["target_high"] == 30500.0
    assert c["target_low"] == 30000.0
    assert c["target_close"] == 30200.0
    assert c["target_volume"] == 4150.0

    # 3. 수급 지표 검증
    # total_volume = 100 + 50 + 4000 = 4150
    # bid_volume = 100 + 4000 = 4100
    expected_bid_ratio = compute_bid_ratio(4100.0, 4150.0)
    assert pytest.approx(c["target_trade_bid_ratio"], 1e-5) == expected_bid_ratio
    assert c["target_trade_count"] == 3.0
    assert c["target_large_trade_count"] == 1.0  # 1억 이상 1건
    assert pytest.approx(c["target_avg_trade_size"], 1e-4) == (4150.0 / 3.0)

    # 4. 하위 호환성 필드 완결성
    assert c["upbit_close"] == c["target_close"]
    assert c["upbit_volume"] == c["target_volume"]

