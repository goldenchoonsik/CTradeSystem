import pytest
import json
from datetime import datetime, timezone, timedelta
from connectors.coinbase_connector import CoinbaseConnector
from core.candle_aggregator import CandleAggregator
from core.models import TickData
from core.tick_router import route_tick
from shared.features.feature_registry import _compute_coinbase_premium_index
import pandas as pd
import numpy as np

KST = timezone(timedelta(hours=9))

class DummyAgg:
    def __init__(self):
        self.received_cb_price = 0.0
        self.received_cb_ts = None
    def on_coinbase_price(self, price, ts):
        self.received_cb_price = price
        self.received_cb_ts = ts

class DummyAI:
    def __init__(self):
        self.received_cb_price = 0.0
    def on_coinbase_price(self, price, ts):
        self.received_cb_price = price


def test_coinbase_connector_parse_ticker():
    connector = CoinbaseConnector(symbols=["BTC-USD"])
    
    # 1. 정상 ticker 메시지
    raw_ticker = json.dumps({
        "type": "ticker",
        "sequence": 987654321,
        "product_id": "BTC-USD",
        "price": "79850.50",
        "open_24h": "79000.00",
        "volume_24h": "1234.5",
        "low_24h": "78500.00",
        "high_24h": "80500.00",
        "volume_30d": "45000.0",
        "best_bid": "79850.00",
        "best_ask": "79851.00",
        "side": "buy",
        "time": "2026-09-07T02:00:00.000000Z",
        "trade_id": 1234567,
        "last_size": "0.05"
    })
    
    tick = connector.parse_message(raw_ticker)
    assert tick is not None
    assert tick.symbol == "BTC-USD"
    assert tick.exchange == "COINBASE"
    assert tick.price == 79850.50
    assert tick.volume == 0.05
    assert tick.side == "BUY"

    # 2. 비 ticker 메시지 (예: subscriptions 등)
    non_ticker = json.dumps({"type": "subscriptions", "channels": []})
    assert connector.parse_message(non_ticker) is None

    # 3. 비정상 JSON
    assert connector.parse_message("invalid json") is None


def test_candle_aggregator_coinbase_realtime_and_stale_cap():
    """코인베이스 가격은 학습 데이터(Gold)와 같은 stale 상한(CRYPTO 1일)까지 마지막 관측을 쓰고, 넘으면 NaN.

    종전엔 120초 미수신 시 바이낸스 가격으로 대체해 프리미엄을 0 으로 위장했다 — 학습 데이터에는 없는
    거짓 관측이다 (2026-10-02).
    """
    agg = CandleAggregator(interval_minutes=10, max_candles=100)

    now = datetime(2026, 9, 7, 10, 0, 0, tzinfo=KST)
    agg.on_binance_tick(price=80000.0, volume=10.0, is_buyer_maker=False, timestamp=now)

    # 1. 코인베이스 정상 수신
    agg.on_coinbase_price(price=80080.0, timestamp=datetime(2026, 9, 7, 10, 9, 30, tzinfo=KST))
    df = agg.check_candle_complete(datetime(2026, 9, 7, 10, 10, 0, tzinfo=KST))
    assert df is not None
    assert df.iloc[-1]['coinbase_spot_close'] == 80080.0
    assert df.iloc[-1]['binance_spot_close'] == 80000.0

    # 2. 코인베이스 틱이 끊겼지만 상한 이내: 마지막 관측을 쓴다 — 바이낸스 가격으로 바꿔치지 않는다
    agg.on_binance_tick(price=79500.0, volume=5.0, is_buyer_maker=False,
                        timestamp=datetime(2026, 9, 7, 10, 15, 0, tzinfo=KST))
    df2 = agg.check_candle_complete(datetime(2026, 9, 7, 10, 20, 0, tzinfo=KST))
    assert df2.iloc[-1]['coinbase_spot_close'] == 80080.0
    assert df2.iloc[-1]['binance_spot_close'] == 79500.0

    # 3. 상한(1일) 초과: NaN ('모름')
    late = datetime(2026, 9, 8, 10, 30, 0, tzinfo=KST)
    agg.on_binance_tick(price=79000.0, volume=1.0, is_buyer_maker=False, timestamp=late)
    df3 = agg.check_candle_complete(datetime(2026, 9, 8, 10, 40, 0, tzinfo=KST))
    assert np.isnan(df3.iloc[-1]['coinbase_spot_close'])
    assert df3.iloc[-1]['binance_spot_close'] == 79000.0


def test_tick_router_coinbase_dispatch():
    dummy_agg = DummyAgg()
    dummy_ai = DummyAI()
    
    candle_aggregators = {"KRW-BTC": dummy_agg}
    ai_strategies = {"KRW-BTC": dummy_ai}
    
    ts = datetime.now(KST)
    tick = TickData(
        symbol="BTC-USD",
        exchange="COINBASE",
        price=80250.0,
        volume=0.1,
        side="BUY",
        timestamp=ts.timestamp()
    )
    
    route_tick(tick, candle_aggregators, ai_strategies)
    
    assert dummy_agg.received_cb_price == 80250.0
    assert dummy_ai.received_cb_price == 80250.0


def test_coinbase_premium_neutrality_on_fallback():
    # Stale Fallback 시 괴리율이 0.0%가 되는지 검증
    df = pd.DataFrame({
        'binance_spot_close': [80000.0, 79500.0],
        'coinbase_spot_close': [80080.0, 79500.0]  # 두 번째 행은 Stale로 바이낸스와 동일한 값으로 fallback
    })
    
    prem = _compute_coinbase_premium_index(df, {'candles': 1}, '10m')
    
    # 첫 번째 행: (80080 - 80000) / 80000 = +0.001 (+0.1%)
    assert pytest.approx(prem.iloc[0], rel=1e-4) == 0.001
    # 두 번째 행: fallback되어 정확히 0.0% 중립 보장
    assert prem.iloc[1] == 0.0
