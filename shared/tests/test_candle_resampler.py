"""
Unit Tests for Universal Candle Resampler (shared/candle_resampler.py)
"""
import pytest
import pandas as pd
import numpy as np
from shared.data_library.candle_resampler import (
    resample_candles,
    UnsupportedIntervalError,
    LookaheadInversionError,
    NonDivisibleIntervalError,
    InvalidTimestampError,
    ResamplerError
)


def test_resample_5m_to_10m():
    """5분봉 4개(00:00, 00:05, 00:10, 00:15) -> 10분봉 2개(00:00, 00:10) 리샘플링 검증"""
    timestamps = pd.date_range('2026-09-08 00:00:00', periods=4, freq='5min')
    df = pd.DataFrame({
        'timestamp': timestamps,
        'open': [100.0, 102.0, 104.0, 106.0],
        'high': [103.0, 105.0, 107.0, 109.0],
        'low': [99.0, 101.0, 103.0, 105.0],
        'close': [102.0, 104.0, 106.0, 108.0],
        'volume': [10.0, 20.0, 30.0, 40.0],
        'coinbase_spot_close': [102.5, 104.5, 106.5, 108.5]
    })

    res = resample_candles(df, target_interval='10m', source_interval='5m')
    assert len(res) == 2
    assert list(res['timestamp']) == [
        pd.Timestamp('2026-09-08 00:00:00'),
        pd.Timestamp('2026-09-08 00:10:00')
    ]

    # 10m 00:00 캔들 검증 (00:00 + 00:05 봉 집계)
    row0 = res.iloc[0]
    assert row0['open'] == 100.0  # 00:00의 시가
    assert row0['high'] == 105.0  # max(103, 105)
    assert row0['low'] == 99.0    # min(99, 101)
    assert row0['close'] == 104.0 # 00:05의 종가! (미래인 00:10 종가 106.0 절대 미포함)
    assert row0['volume'] == 30.0 # 10 + 20
    assert row0['coinbase_spot_close'] == 104.5  # 00:05의 종가!

    # 10m 00:10 캔들 검증 (00:10 + 00:15 봉 집계)
    row1 = res.iloc[1]
    assert row1['open'] == 104.0  # 00:10의 시가
    assert row1['high'] == 109.0  # max(107, 109)
    assert row1['low'] == 103.0   # min(103, 105)
    assert row1['close'] == 108.0 # 00:15의 종가!
    assert row1['volume'] == 70.0 # 30 + 40
    assert row1['coinbase_spot_close'] == 108.5


def test_lookahead_inversion_error():
    """🚨 상위봉(15m)으로 하위봉(10m) 리샘플링 시도시 LookaheadInversionError 발생 필수 검증"""
    timestamps = pd.date_range('2026-09-08 00:00:00', periods=4, freq='15min')
    df = pd.DataFrame({
        'timestamp': timestamps,
        'close': [100.0, 101.0, 102.0, 103.0]
    })

    with pytest.raises(LookaheadInversionError) as exc_info:
        resample_candles(df, target_interval='10m', source_interval='15m')
    assert "LookaheadInversionError" in str(exc_info.value)
    assert "상위 시간봉" in str(exc_info.value)


def test_unsupported_interval_error():
    """❌ 지원하지 않는 비표준 인터벌(7m) 입력 시 UnsupportedIntervalError 발생 검증"""
    timestamps = pd.date_range('2026-09-08 00:00:00', periods=4, freq='1min')
    df = pd.DataFrame({
        'timestamp': timestamps,
        'close': [100.0, 101.0, 102.0, 103.0]
    })

    with pytest.raises(UnsupportedIntervalError) as exc_info:
        resample_candles(df, target_interval='7m')
    assert "지원하지 않는 타겟 인터벌" in str(exc_info.value)


def test_non_divisible_interval_error():
    """❌ 정수 배수가 아닌 인터벌(5m 소스로 12m 요구) 시 NonDivisibleIntervalError 발생 검증"""
    # 12m는 비표준이므로, 표준 중 비배수 조합: 소스 15m, 타겟 60m는 배수(4).
    # 소스 10m, 타겟 15m는 상위->하위가 아니나(10m < 15m) 15m % 10m != 0.
    timestamps = pd.date_range('2026-09-08 00:00:00', periods=6, freq='10min')
    df = pd.DataFrame({
        'timestamp': timestamps,
        'close': [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    })

    with pytest.raises(NonDivisibleIntervalError) as exc_info:
        resample_candles(df, target_interval='15m', source_interval='10m')
    assert "NonDivisibleIntervalError" in str(exc_info.value)
    assert "정수 배수" in str(exc_info.value)


def test_single_column_coinbase_resample():
    """단일 시세 컬럼(coinbase_spot_close)만 있는 DataFrame의 10분봉 집계 검증"""
    timestamps = pd.date_range('2026-09-08 00:00:00', periods=4, freq='5min')
    df = pd.DataFrame({
        'timestamp': timestamps,
        'coinbase_spot_close': [2500.0, 2510.0, 2520.0, 2530.0]
    })

    res = resample_candles(df, target_interval='10m')
    assert len(res) == 2
    assert res['coinbase_spot_close'].iloc[0] == 2510.0  # 00:05 봉 가격
    assert res['coinbase_spot_close'].iloc[1] == 2530.0  # 00:15 봉 가격


def test_invalid_timestamp_error():
    """타임스탬프 누락 또는 결측치 검증"""
    df = pd.DataFrame({'close': [100.0, 101.0]})
    with pytest.raises(InvalidTimestampError):
        resample_candles(df, target_interval='10m')
