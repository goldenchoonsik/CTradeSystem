"""CoinbaseClient 및 가상자산 수집기 Granularity 동적 처리 및 무결성 회귀 테스트."""
import pytest
import pandas as pd
from datetime import datetime
from CAiModelBaker.core.crypto_collector import CoinbaseClient, BinanceClient, UpbitClient
from shared.data_library.candle_resampler import resample_candles, LookaheadInversionError


def test_coinbase_resolve_granularity():
    # 1분봉은 60초 네이티브로 매핑되어 역방향 리샘플링 에러를 방지해야 함
    gran_1m, src_1m = CoinbaseClient.resolve_granularity("1m")
    assert gran_1m == 60
    assert src_1m == "1m"

    # 5분봉은 300초 네이티브
    gran_5m, src_5m = CoinbaseClient.resolve_granularity("5m")
    assert gran_5m == 300
    assert src_5m == "5m"

    # 10분봉은 거래소 미지원이므로 5분봉(300초)을 받아 상위로 정방향 합성해야 함
    gran_10m, src_10m = CoinbaseClient.resolve_granularity("10m")
    assert gran_10m == 300
    assert src_10m == "5m"

    # 15분봉은 900초 네이티브
    gran_15m, src_15m = CoinbaseClient.resolve_granularity("15m")
    assert gran_15m == 900
    assert src_15m == "15m"

    # 1시간봉은 3600초 네이티브
    gran_1h, src_1h = CoinbaseClient.resolve_granularity("1h")
    assert gran_1h == 3600
    assert src_1h == "1h"

    # 1일봉은 86400초 네이티브
    gran_1d, src_1d = CoinbaseClient.resolve_granularity("1d")
    assert gran_1d == 86400
    assert src_1d == "1d"


def test_coinbase_10m_forward_resampling_no_inversion():
    """5분봉 데이터를 받아 10분봉으로 합성할 때 LookaheadInversionError가 발생하지 않고 정방향 합성됨을 검증."""
    rows_5m = [
        {"timestamp": "2026-09-26 10:00:00", "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 10.0},
        {"timestamp": "2026-09-26 10:05:00", "open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0, "volume": 15.0},
    ]
    df_5m = pd.DataFrame(rows_5m)
    df_10m = resample_candles(df_5m, target_interval="10m", source_interval="5m")
    assert len(df_10m) == 1
    assert df_10m.iloc[0]["open"] == 100.0
    assert df_10m.iloc[0]["high"] == 105.0
    assert df_10m.iloc[0]["low"] == 99.0
    assert df_10m.iloc[0]["close"] == 103.0
    assert df_10m.iloc[0]["volume"] == 25.0


def test_inversion_guard_catches_illegal_downsampling():
    """5분봉에서 1분봉으로의 역방향 합성은 엄격히 차단(LookaheadInversionError)되어야 함."""
    rows_5m = [
        {"timestamp": "2026-09-26 10:00:00", "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 10.0},
    ]
    df_5m = pd.DataFrame(rows_5m)
    with pytest.raises(LookaheadInversionError):
        resample_candles(df_5m, target_interval="1m", source_interval="5m")
