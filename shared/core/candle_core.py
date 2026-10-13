# -*- coding: utf-8 -*-
"""
shared/candle_core.py

과거 배치 정규화(normalized_builder)와 실시간 스트리밍 집계(CandleAggregator)의
시간 경계(Time Boundary), 수급 지표(Trade Metrics), 캔들 기하학 방역(Sanitization)
단일 진실 공급원(Single Source of Truth, SSOT) 코어 모듈.
"""

from datetime import datetime, timezone, timedelta
from typing import Tuple, Dict, Any, Union, Optional
import numpy as np

KST = timezone(timedelta(hours=9))

# 대형 체결 판정 기본 임계값: 1억 원 (100,000,000 KRW)
LARGE_TRADE_THRESHOLD_KRW: float = 100000000.0


def get_candle_boundary(ts: datetime, interval_seconds: int) -> Tuple[datetime, datetime]:
    """
    주어진 시각(ts)이 속하는 캔들의 시작/종료 경계를 결정론적으로 계산 (KST 기준).
    - 구간: [start, end)
    - 시간 라벨링 규약: 구간 시작 시각(start)으로 통일하여 미래 참조(Lookahead) 원천 차단.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=KST)

    seconds_since_midnight = ts.hour * 3600 + ts.minute * 60 + ts.second
    candle_index = seconds_since_midnight // interval_seconds
    start_second = candle_index * interval_seconds

    start = ts.replace(
        hour=(start_second // 3600) % 24,
        minute=(start_second % 3600) // 60,
        second=start_second % 60,
        microsecond=0
    )
    end = start + timedelta(seconds=interval_seconds)
    return start, end


def compute_bid_ratio(bid_volume: float, total_volume: float, fallback: float = 0.5) -> float:
    """
    체결 매수 비율 계산: (매수체결량 / 전체체결량)
    - 0.0 ~ 1.0 범위로 엄격 클리핑
    - 전체 체결량이 0이거나 무거래 구간인 경우 fallback(기본 0.5: 중립) 적용
    """
    if total_volume > 0.0:
        ratio = bid_volume / total_volume
        return float(np.clip(ratio, 0.0, 1.0))
    return float(fallback)


def compute_avg_trade_size(volume: float, trade_count: float) -> float:
    """
    평균 체결 규모: 전체 체결량 / 체결 건수
    - 체결 건수가 0인 경우 0.0 반환
    """
    if trade_count > 0.0:
        return float(volume / trade_count)
    return 0.0


def sanitize_candle_ohlcv(
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    volume: float = 0.0
) -> Tuple[float, float, float, float, float]:
    """
    캔들 기하학적 정합성 자동 방역 (Candle Geometry Sanitization).
    - High >= max(Open, High, Low, Close)
    - Low <= min(Open, High, Low, Close)
    - Low > 0 인지 검증
    - Volume >= 0.0 보정
    """
    fixed_vol = max(0.0, float(volume))
    if open_p <= 0.0 and close_p <= 0.0:
        return open_p, high_p, low_p, close_p, fixed_vol

    fixed_high = max(float(open_p), float(high_p), float(low_p), float(close_p))
    fixed_low = min(float(open_p), float(high_p), float(low_p), float(close_p))
    return float(open_p), fixed_high, fixed_low, float(close_p), fixed_vol


def build_standard_candle_dict(
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    volume: float,
    bid_ratio: float,
    trade_count: float,
    large_trade_count: float,
    avg_trade_size: float,
    prefix: str = "target",
    quote_volume: Optional[float] = None
) -> Dict[str, Any]:
    """
    과거 학습 Parquet과 실시간 집계기에서 100% 동일하게 공유하는 표준 캔들 딕셔너리 빌더.
    """
    fixed_open, fixed_high, fixed_low, fixed_close, fixed_vol = sanitize_candle_ohlcv(
        open_p, high_p, low_p, close_p, volume
    )

    if quote_volume is not None and fixed_vol > 0:
        raw_vwap = float(quote_volume) / fixed_vol
        fixed_vwap = max(fixed_low, min(fixed_high, raw_vwap))
    else:
        fixed_vwap = fixed_close

    large_flag = 1.0 if float(large_trade_count) > 0 else 0.0

    result = {
        f"{prefix}_open": fixed_open,
        f"{prefix}_high": fixed_high,
        f"{prefix}_low": fixed_low,
        f"{prefix}_close": fixed_close,
        f"{prefix}_volume": fixed_vol,
        f"{prefix}_trade_bid_ratio": float(bid_ratio),
        f"{prefix}_trade_count": float(trade_count),
        f"{prefix}_large_trade_count": float(large_trade_count),
        f"{prefix}_avg_trade_size": float(avg_trade_size),
        f"{prefix}_vwap": float(fixed_vwap),
        f"{prefix}_is_large_trade": float(large_flag),
    }

    # KRX / 가상자산 하위 호환 매핑
    if prefix in ("target", "upbit"):
        result["upbit_open"] = fixed_open
        result["upbit_high"] = fixed_high
        result["upbit_low"] = fixed_low
        result["upbit_close"] = fixed_close
        result["upbit_volume"] = fixed_vol
        result["upbit_trade_bid_ratio"] = float(bid_ratio)
        result["upbit_trade_count"] = float(trade_count)
        result["upbit_large_trade_count"] = float(large_trade_count)
        result["upbit_avg_trade_size"] = float(avg_trade_size)
        result["vwap"] = float(fixed_vwap)
        result["is_large_trade"] = float(large_flag)

    return result

