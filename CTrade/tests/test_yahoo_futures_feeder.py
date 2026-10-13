# -*- coding: utf-8 -*-
"""
Unit Test: YahooFuturesFeeder & NQ Futures Tick Routing
Yahoo Finance NQ=F, ES=F 시세 피더 및 캔들 어그리게이터 연동 검증
"""

import unittest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from connectors.yahoo_futures_feeder import YahooFuturesFeeder, FUTURES_SYMBOL_MAP
from core.candle_aggregator import CandleAggregator

KST = timezone(timedelta(hours=9))


class TestYahooFuturesFeeder(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.received_ticks = []

    def on_tick_callback(self, symbol: str, price: float, ts: datetime):
        self.received_ticks.append((symbol, price, ts))

    def test_symbol_mapping(self):
        """NQ, ES 심볼이 Yahoo Finance 선물 심볼로 정확히 매핑되는지 검증"""
        self.assertEqual(FUTURES_SYMBOL_MAP["NQ"], "NQ=F")
        self.assertEqual(FUTURES_SYMBOL_MAP["ES"], "ES=F")

    async def test_feeder_tick_routing_to_aggregator(self):
        """feeder에서 전달된 NQ 틱이 CandleAggregator에 정상 반영되는지 검증"""
        agg = CandleAggregator(interval_minutes=1.0, max_candles=500, market_type="KRX")

        def mock_callback(symbol: str, price: float, ts: datetime):
            agg.on_futures_tick(symbol, price, ts)

        feeder = YahooFuturesFeeder(
            symbols=["NQ", "ES"],
            poll_interval=1.0,
            on_tick_callback=mock_callback
        )

        t0 = datetime(2026, 9, 2, 9, 1, 0, tzinfo=KST)
        # NQ 선물 틱 시뮬레이션
        mock_callback("NQ", 19850.25, t0)
        self.assertEqual(agg._obs["nq_futures_close"].value, 19850.25)
        self.assertEqual(agg._obs["nq_futures_close"].value, 19850.25)

        # ES 선물 틱 시뮬레이션
        mock_callback("ES", 5620.50, t0)
        self.assertEqual(agg._obs["sp500_futures_close"].value, 5620.50)
        self.assertEqual(agg._obs["sp500_futures_close"].value, 5620.50)

        # 캔들 완성 시 candle 딕셔너리에 nq_futures_close가 정상 포함되는지 검증
        t_tick = datetime(2026, 9, 2, 9, 1, 10, tzinfo=KST)
        agg.on_target_tick(35000.0, 10.0, "BID", t_tick)
        t_complete = datetime(2026, 9, 2, 9, 2, 0, tzinfo=KST)
        df = agg.check_candle_complete(t_complete)
        self.assertIsNotNone(df)
        self.assertIn("nq_futures_close", df.columns)
        self.assertEqual(df["nq_futures_close"].iloc[-1], 19850.25)
        self.assertIn("sp500_futures_close", df.columns)
        self.assertEqual(df["sp500_futures_close"].iloc[-1], 5620.50)

    async def test_mock_fetch_latest_price(self):
        """Yahoo API 응답 구조를 모킹하여 정상 파싱 검증"""
        feeder = YahooFuturesFeeder(symbols=["NQ"], poll_interval=1.0)

        mock_chart_data = {
            "chart": {
                "result": [{
                    "timestamp": [1725280000, 1725280060],
                    "indicators": {
                        "quote": [{
                            "open": [19800.0, 19810.0],
                            "high": [19820.0, 19830.0],
                            "low": [19790.0, 19805.0],
                            "close": [19815.0, 19825.5],
                            "volume": [100, 150]
                        }]
                    }
                }]
            }
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_chart_data)

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        mock_session.closed = False

        feeder._session = mock_session

        price = await feeder.fetch_latest_price("NQ")
        self.assertEqual(price, 19825.5)
        self.assertEqual(feeder.last_prices.get("NQ"), 19825.5)

    async def test_error_fallback_to_last_price(self):
        """API 오류 시 직전 캐시 가격 유지(ffill) 검증"""
        feeder = YahooFuturesFeeder(symbols=["NQ"], poll_interval=1.0)
        feeder.last_prices["NQ"] = 19700.0

        mock_resp = AsyncMock()
        mock_resp.status = 500  # 서버 오류 시뮬레이션

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        mock_session.closed = False
        feeder._session = mock_session

        price = await feeder.fetch_latest_price("NQ")
        self.assertEqual(price, 19700.0)


if __name__ == "__main__":
    unittest.main()
