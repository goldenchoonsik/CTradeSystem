import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from CAiModelBaker.core.data_manager import validate_snapshot_integrity
from shared.features.feature_registry import _compute_coinbase_premium_index, _compute_eth_btc_relative_momentum
from CTrade.core.tick_router import route_tick
from CTrade.core.models import TickData


def test_validate_snapshot_integrity_normal_eth():
    """정상 ETH 데이터셋에 대한 무결성 게이트 통과 검증"""
    df = pd.DataFrame({
        'timestamp': pd.date_range('2026-01-01', periods=10, freq='15min'),
        'upbit_close': [4_000_000.0] * 10,
        'binance_spot_close': [3_000.0] * 10,
        'coinbase_spot_close': [3_005.0] * 10,
        'benchmark_btc_close': [60_000.0] * 10
    })
    assert validate_snapshot_integrity(df, "KRW-ETH") is True


def test_validate_snapshot_integrity_contaminated_eth_fails():
    """과거 버그처럼 타겟(ETH $3,000)에 BTC 코인베이스 시세($60,000)가 오주입된 경우 무결성 게이트에서 즉각 차단되는지 검증"""
    df = pd.DataFrame({
        'timestamp': pd.date_range('2026-01-01', periods=10, freq='15min'),
        'upbit_close': [4_000_000.0] * 10,
        'binance_spot_close': [3_000.0] * 10,
        'coinbase_spot_close': [60_000.0] * 10,  # 20배 오염
        'benchmark_btc_close': [60_000.0] * 10
    })
    with pytest.raises(ValueError, match="심각한 데이터 오염 감지"):
        validate_snapshot_integrity(df, "KRW-ETH")


def test_validate_snapshot_integrity_missing_benchmark_btc_fails():
    """가상자산에 벤치마크 BTC가 누락되거나 모두 NaN인 경우 차단 검증"""
    df = pd.DataFrame({
        'timestamp': pd.date_range('2026-01-01', periods=10, freq='15min'),
        'upbit_close': [4_000_000.0] * 10,
        'binance_spot_close': [3_000.0] * 10,
        'coinbase_spot_close': [3_000.0] * 10,
        'benchmark_btc_close': [np.nan] * 10
    })
    with pytest.raises(ValueError, match="benchmark_btc_close"):
        validate_snapshot_integrity(df, "KRW-ETH")


def test_compute_coinbase_premium_index():
    """코인베이스 프리미엄 지수 계산 검증 (정상 계산 및 결측치 폴백)"""
    df = pd.DataFrame({
        'binance_spot_close': [3000.0, 3000.0, 3000.0],
        'coinbase_spot_close': [3030.0, 2970.0, np.nan]
    })
    res = _compute_coinbase_premium_index(df, {'candles': 1}, '15m')
    # 1행: (3030 - 3000) / 3000 = +0.01 (+1%)
    # 2행: (2970 - 3000) / 3000 = -0.01 (-1%)
    # 3행: NaN 결측치는 binance_close로 폴백되어 0.0
    assert np.isclose(res.iloc[0], 0.01, atol=1e-4)
    assert np.isclose(res.iloc[1], -0.01, atol=1e-4)
    assert np.isclose(res.iloc[2], 0.0, atol=1e-4)


def test_compute_eth_btc_relative_momentum_on_eth_dataset():
    """타겟이 KRW-ETH일 때 eth_btc_relative_momentum이 0.0에 고정되지 않고 정상 계산되는지 검증"""
    # 2개 시점:
    # ETH (binance_spot_close): 3,000 -> 3,300 (+10% 상승)
    # BTC (benchmark_btc_close): 60,000 -> 60,000 (0% 변동)
    df = pd.DataFrame({
        'binance_spot_close': [3000.0, 3300.0],
        'binance_eth_close': [3000.0, 3300.0],
        'benchmark_btc_close': [60000.0, 60000.0]
    })
    res = _compute_eth_btc_relative_momentum(df, {'candles': 1}, '15m')
    # index 1에서 ETH 상승률(+0.1) - BTC 상승률(0.0) = +0.1 (+10%p)
    assert np.isclose(res.iloc[1], 0.1, atol=1e-4)


def test_tick_router_symbol_isolation():
    """TickRouter에서 코인베이스 틱과 벤치마크 BTC 틱이 대상 종목별로 격리 및 정확히 라우팅되는지 검증"""
    class MockAggregator:
        def __init__(self, sym):
            self.symbol = sym
            self.coinbase_prices = []
            self.benchmark_btc_prices = []
            self.binance_prices = []

        def on_coinbase_price(self, price, ts):
            self.coinbase_prices.append(price)

        def on_benchmark_btc_tick(self, price, ts):
            self.benchmark_btc_prices.append(price)

        def on_binance_tick(self, price, vol, is_maker, ts):
            self.binance_prices.append(price)

        def on_target_tick(self, price, vol, side, ts):
            pass

        def check_candle_complete(self, ts):
            pass

    btc_agg = MockAggregator("KRW-BTC")
    eth_agg = MockAggregator("KRW-ETH")
    aggregators = {"KRW-BTC": btc_agg, "KRW-ETH": eth_agg}

    # 1. Coinbase BTC-USD 틱 발생 ($60,000)
    btc_cb_tick = TickData(
        symbol="BTC-USD", exchange="COINBASE", price=60000.0, volume=0.5, side="BUY", timestamp=1000.0
    )
    route_tick(btc_cb_tick, aggregators, {})
    # BTC 어그리게이터에는 주입되고, ETH 어그리게이터에는 절대 주입되지 않아야 함!
    assert btc_agg.coinbase_prices == [60000.0]
    assert eth_agg.coinbase_prices == []

    # 2. Coinbase ETH-USD 틱 발생 ($3,000)
    eth_cb_tick = TickData(
        symbol="ETH-USD", exchange="COINBASE", price=3000.0, volume=2.0, side="BUY", timestamp=1001.0
    )
    route_tick(eth_cb_tick, aggregators, {})
    # ETH 어그리게이터에는 주입되고, BTC 어그리게이터의 coinbase_prices는 여전히 [60000.0]이어야 함!
    assert eth_agg.coinbase_prices == [3000.0]
    assert btc_agg.coinbase_prices == [60000.0]

    # 3. Binance BTCUSDT 틱 발생 ($60,100)
    btc_binance_tick = TickData(
        symbol="BTCUSDT", exchange="BINANCE", price=60100.0, volume=1.0, side="BUY", timestamp=1002.0
    )
    route_tick(btc_binance_tick, aggregators, {})
    # BTC 어그리게이터에는 on_binance_tick으로 주입
    assert btc_agg.binance_prices == [60100.0]
    # ETH 어그리게이터에는 on_benchmark_btc_tick으로 주입!
    assert eth_agg.benchmark_btc_prices == [60100.0]
