import math
# -*- coding: utf-8 -*-
"""
Unit Tests for Audit Bug Fixes:
1. AIStrategyModule __init__ attribute completion (original_buy_threshold, risk_manager, warmup_status)
2. CandleAggregator add_backfilled_candle cache synchronization (prevents 0.0 price crash)
3. Bollinger %B neutral 0.5 for flat/zero volatility price series
4. TickRouter target symbol vs futures collision guard
5. AIStrategyModule reference symbol tick routing
"""
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from CTrade.core.candle_aggregator import CandleAggregator
from CTrade.core.tick_router import route_tick
from CTrade.core.models import TickData
from shared.features.feature_registry import _compute_target_bb_pctb

KST = timezone(timedelta(hours=9))


class TestAuditBugFixes(unittest.TestCase):

    def test_bb_pctb_flat_price_is_half(self):
        """가격 변동이 없는 평탄한 구간(std=0)에서 볼린저 %B가 0.0이 아닌 0.5(중심선)로 계산되는지 검증"""
        df = pd.DataFrame({
            "timestamp": pd.date_range("2026-09-03 09:00:00", periods=30, freq="1min", tz="Asia/Seoul"),
            "target_close": [35000.0] * 30
        })
        pctb = _compute_target_bb_pctb(df, {"period": 20, "std_dev": 2}, "1m")
        # 모든 구간에서 0.5여야 함 (기존 버그: 0.0)
        self.assertTrue((pctb == 0.5).all())

    def test_candle_aggregator_backfill_cache_sync(self):
        """add_backfilled_candle 호출 시 마지막 가격 캐시가 동기화되어 라이브 첫 봉 틱 누락 시 0.0 추락을 방지하는지 검증"""
        agg = CandleAggregator(interval_minutes=1.0, max_candles=100)
        agg.register_ref_symbol("000660", "hynix")

        t0 = datetime(2026, 9, 3, 9, 0, 0, tzinfo=KST)
        agg.add_backfilled_candle({
            "timestamp": t0,
            "target_close": 35200.0,
            "nq_futures_close": 19500.0,
            "sp500_futures_close": 5600.0,
            "ref_hynix_close": 205000.0
        })

        # 캐시가 정상 동기화되었는지 확인
        self.assertEqual(agg._last_target_price, 35200.0)
        self.assertEqual(agg._obs["nq_futures_close"].value, 19500.0)
        self.assertEqual(agg._obs["sp500_futures_close"].value, 5600.0)
        self.assertEqual(agg._last_ref_prices.get("hynix"), 205000.0)

        # 다음 분봉으로 넘어가 틱 없이 flush 유도
        t_next = datetime(2026, 9, 3, 9, 1, 30, tzinfo=KST)
        agg.check_candle_complete(t_next)

        # 첫 라이브 캔들에 ffill된 target_close가 0.0이 아니라 이전 백필 종가 35200.0이어야 함
        self.assertGreaterEqual(len(agg.candles), 1)
        flushed_c = agg.candles[-1]
        self.assertEqual(flushed_c["target_close"], 35200.0)
        self.assertEqual(flushed_c["nq_futures_close"], 19500.0)
        self.assertEqual(flushed_c["sp500_futures_close"], 5600.0)
        self.assertEqual(flushed_c["ref_hynix_close"], 205000.0)

    def test_tick_router_target_not_confused_with_futures(self):
        """심볼명에 '200'이 포함된 타겟 ETF의 틱이 kospi_futures_close로 오염 라우팅되지 않는지 검증"""
        agg = CandleAggregator(interval_minutes=1.0)
        agg_sym = "KODEX_200"

        tick = TickData(
            symbol=agg_sym,
            exchange="KIS",
            price=35000.0,
            volume=10.0,
            side="BID",
            timestamp=datetime(2026, 9, 3, 9, 0, 10, tzinfo=KST).timestamp()
        )

        route_tick(tick, {agg_sym: agg}, {})

        # 타겟 가격은 35000이어야 하지만, 선물 종가는 관측된 적이 없으므로 NaN('모름')이어야 함
        self.assertEqual(agg._target_builder.close, 35000.0)
        self.assertTrue(math.isnan(agg._obs["kospi_futures_close"].value))

    def test_ai_strategy_attributes_and_ref_routing(self):
        """AIStrategyModule의 __init__ 누락 속성 복구 및 on_ref_tick 라우팅 검증"""
        import os
        from CTrade.core.ai_strategy import AIStrategyModule
        _tests_dir = os.path.dirname(os.path.abspath(__file__))
        _project_root = os.path.abspath(os.path.join(_tests_dir, "..", ".."))
        deploy_path = os.path.join(_project_root, "shared", "deploy", "069500_1m", "KODEX200_M1_BEST3")
        if not os.path.exists(deploy_path):
            self.skipTest("테스트 배포 디렉토리 부재로 스킵")

        ai_mod = AIStrategyModule(
            deploy_path=deploy_path,
            symbol="069500"
        )

        # 1. 속성 누락 복구 확인
        self.assertTrue(hasattr(ai_mod, "original_buy_threshold"))
        self.assertGreater(ai_mod.original_buy_threshold, 0.0)
        self.assertTrue(hasattr(ai_mod, "risk_manager"))
        self.assertIsNotNone(ai_mod.risk_manager)
        self.assertTrue(hasattr(ai_mod, "warmup_status"))
        self.assertIn("status_text", ai_mod.warmup_status)
        self.assertFalse(ai_mod.is_warmed_up)

        # 2. on_ref_tick 지원 확인
        ai_mod.aggregator.register_ref_symbol("000660", "hynix")
        t = datetime(2026, 9, 3, 9, 0, 15, tzinfo=KST)
        ai_mod.on_ref_tick("000660", 205000.0, 50.0, timestamp=t, is_buyer=True)

        builder = ai_mod.aggregator._ref_builders.get("hynix")
        self.assertIsNotNone(builder)
        self.assertEqual(builder.close, 205000.0)
        self.assertEqual(builder.volume, 50.0)


if __name__ == "__main__":
    unittest.main()
