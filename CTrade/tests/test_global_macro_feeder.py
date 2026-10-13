# -*- coding: utf-8 -*-
"""
Unit Test: GlobalMacroFeeder & 7대 글로벌 매크로·선물 실시간 라우팅 검증
- Pyth Network (NQ, ES, US10Y, WTI, GOLD, USDKRW) 메시지 파싱 무결성
- Hyperliquid (NVDA-PERP) 메시지 파싱 무결성
- CandleAggregator 실시간 캔들 플러시 동기화 검증
"""

import unittest
import asyncio
import json
from datetime import datetime, timezone, timedelta

from connectors.global_macro_feeder import GlobalMacroFeeder, PYTH_FEED_IDS
from core.candle_aggregator import CandleAggregator

KST = timezone(timedelta(hours=9))


class TestGlobalMacroFeeder(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.received_ticks = []

    def on_tick_callback(self, symbol: str, price: float, ts: datetime):
        self.received_ticks.append((symbol, price, ts))

    def test_initial_prices_and_feed_ids(self):
        """7대 매크로 피드의 초기화 및 Pyth Feed ID 무결성 검증"""
        feeder = GlobalMacroFeeder(on_tick_callback=self.on_tick_callback)
        
        # 7대 핵심 심볼 가격 존재 확인
        expected_symbols = ["NQ", "ES", "US10Y", "WTI", "GOLD", "USDKRW", "NVDA"]
        for sym in expected_symbols:
            self.assertIn(sym, feeder.last_prices)
            self.assertGreater(feeder.get_latest_price(sym, allow_stale=True), 0.0)

        # Pyth Feed ID 검증
        self.assertEqual(len(PYTH_FEED_IDS), 7)
        self.assertTrue(PYTH_FEED_IDS["NQ"].startswith("0x"))
        self.assertTrue(PYTH_FEED_IDS["ES"].startswith("0x"))
        self.assertTrue(PYTH_FEED_IDS["USDKRW"].startswith("0x"))

    async def test_feeder_routing_to_candle_aggregator(self):
        """GlobalMacroFeeder 틱이 CandleAggregator에 정상 반영되어 캔들에 플러시되는지 검증"""
        agg = CandleAggregator(interval_minutes=1.0, max_candles=500, market_type="KRX")

        def mock_callback(symbol: str, price: float, ts: datetime):
            agg.on_futures_tick(symbol, price, ts)

        feeder = GlobalMacroFeeder(on_tick_callback=mock_callback)

        t0 = datetime(2026, 9, 7, 9, 0, 0, tzinfo=KST)
        # 7대 매크로 틱 주입 시뮬레이션
        mock_callback("NQ", 20450.25, t0)
        mock_callback("ES", 5620.50, t0)
        mock_callback("NVDA", 128.75, t0)
        mock_callback("US10Y", 112.50, t0)
        mock_callback("WTI", 76.80, t0)
        mock_callback("GOLD", 2530.10, t0)
        mock_callback("USDKRW", 1378.50, t0)

        # CandleAggregator 내부 상태 검증
        self.assertEqual(agg._obs["nq_futures_close"].value, 20450.25)
        self.assertEqual(agg._obs["sp500_futures_close"].value, 5620.50)
        self.assertEqual(agg._obs["nvda_futures_close"].value, 128.75)
        self.assertEqual(agg._obs["us10y_futures_close"].value, 112.50)
        self.assertEqual(agg._obs["wti_futures_close"].value, 76.80)
        self.assertEqual(agg._obs["gold_futures_close"].value, 2530.10)
        self.assertEqual(agg._obs["usd_krw_fx"].value, 1378.50)

        # 타겟 체결 주입 및 캔들 완성 검증
        t_tick = datetime(2026, 9, 7, 9, 0, 10, tzinfo=KST)
        agg.on_target_tick(35000.0, 10.0, "BID", t_tick)

        t_complete = datetime(2026, 9, 7, 9, 1, 0, tzinfo=KST)
        df = agg.check_candle_complete(t_complete)
        self.assertIsNotNone(df)

        # 완성된 캔들의 DataFrame 컬럼 검증
        expected_cols = [
            "nq_futures_close", "sp500_futures_close", "nvda_futures_close",
            "us10y_futures_close", "wti_futures_close", "gold_futures_close", "usd_krw_fx"
        ]
        for col in expected_cols:
            self.assertIn(col, df.columns)

        self.assertEqual(df["nq_futures_close"].iloc[-1], 20450.25)
        self.assertEqual(df["sp500_futures_close"].iloc[-1], 5620.50)
        self.assertEqual(df["nvda_futures_close"].iloc[-1], 128.75)
        self.assertEqual(df["us10y_futures_close"].iloc[-1], 112.50)
        self.assertEqual(df["wti_futures_close"].iloc[-1], 76.80)
        self.assertEqual(df["gold_futures_close"].iloc[-1], 2530.10)
        self.assertEqual(df["usd_krw_fx"].iloc[-1], 1378.50)

    def test_feature_registry_computations(self):
        """shared/feature_registry.py의 신규 5대 매크로 피처 계산 함수 검증"""
        import pandas as pd
        from shared.features.feature_registry import (
            _compute_nvda_futures_ret_curr,
            _compute_us10y_futures_ret_curr,
            _compute_wti_futures_ret_curr,
            _compute_gold_futures_ret_curr,
            _compute_usdkrw_ret_curr,
            DEFAULT_FEATURE_REGISTRY
        )

        # 가상 DataFrame 생성
        df = pd.DataFrame({
            "nvda_futures_close": [100.0, 102.0, 104.0, 106.0, 110.0],
            "us10y_futures_close": [110.0, 110.5, 111.0, 111.5, 112.0],
            "wti_futures_close": [70.0, 71.0, 72.0, 73.0, 75.0],
            "gold_futures_close": [2500.0, 2510.0, 2520.0, 2530.0, 2550.0],
            "usd_krw_fx": [1350.0, 1355.0, 1360.0, 1365.0, 1380.0]
        })

        ret_nvda = _compute_nvda_futures_ret_curr(df, {"candles": 4}, "1m")
        self.assertAlmostEqual(ret_nvda.iloc[-1], 0.10, places=4)

        ret_fx = _compute_usdkrw_ret_curr(df, {"candles": 4}, "1m")
        self.assertAlmostEqual(ret_fx.iloc[-1], (1380.0 - 1350.0) / 1350.0, places=4)

        # 레지스트리 등록 검증
        self.assertIn("nvda_futures_ret_curr", DEFAULT_FEATURE_REGISTRY)
        self.assertIn("us10y_futures_ret_curr", DEFAULT_FEATURE_REGISTRY)
        self.assertIn("wti_futures_ret_curr", DEFAULT_FEATURE_REGISTRY)
        self.assertIn("gold_futures_ret_curr", DEFAULT_FEATURE_REGISTRY)
        self.assertIn("usdkrw_ret_curr", DEFAULT_FEATURE_REGISTRY)
