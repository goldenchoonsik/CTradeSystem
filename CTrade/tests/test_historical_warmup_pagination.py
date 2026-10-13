# -*- coding: utf-8 -*-
"""
Unit Test: Historical Warm-up Pagination & Safety Guard
봉 길이 연동 동적 백필 및 지표 웜업 안전 가드 검증
"""

import unittest
import asyncio
import sys
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

# CTrade 디렉토리를 sys.path에 추가
ctrade_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ctrade_dir not in sys.path:
    sys.path.insert(0, ctrade_dir)

from core.candle_aggregator import CandleAggregator

KST = timezone(timedelta(hours=9))


class TestHistoricalWarmupPagination(unittest.IsolatedAsyncioTestCase):

    def test_candle_aggregator_dynamic_threshold(self):
        """CandleAggregator가 min_required_candles에 맞춰 has_enough_data를 정확히 판단하는지 검증"""
        agg = CandleAggregator(interval_minutes=10.0, max_candles=2000, market_type="CRYPTO")
        agg.min_required_candles = 840  # 김치프리미엄 840봉 요구 시나리오

        now = datetime(2026, 9, 3, 10, 0, 0, tzinfo=KST)
        for i in range(200):
            t = now - timedelta(minutes=(200 - i) * 10)
            agg.add_backfilled_candle({"timestamp": t, "target_close": 100000.0, "upbit_close": 100000.0})

        # 200개 캔들만 적재된 상태: 840개 미만이므로 False여야 함
        self.assertFalse(agg.has_enough_data)
        self.assertEqual(len(agg.candles), 200)

        # 640개 추가 적재하여 총 840개 달성
        for i in range(640):
            t = now + timedelta(minutes=(i + 1) * 10)
            agg.add_backfilled_candle({"timestamp": t, "target_close": 100000.0, "upbit_close": 100000.0})

        self.assertTrue(agg.has_enough_data)
        self.assertEqual(len(agg.candles), 840)

    def test_warmup_status_formatting(self):
        """웜업 진행 상태 딕셔너리 포맷팅 및 UI 텍스트 검증"""
        class MockAIStrategy:
            def __init__(self):
                self.symbol = "KRW-BTC"
                self.aggregator = CandleAggregator(interval_minutes=10.0, max_candles=2000)
                self.required_warmup_candles = 840

            @property
            def is_warmed_up(self):
                return len(self.aggregator.candles) >= self.required_warmup_candles

            @property
            def warmup_status(self):
                curr = len(self.aggregator.candles)
                req = self.required_warmup_candles
                pct = round(min(100.0, (curr / req * 100.0)), 1) if req > 0 else 100.0
                ready = curr >= req
                status_text = f"🟢 [READY] {curr}/{req} ({pct}%) - 매매 활성화" if ready else f"⏳ [WARM-UP] {curr}/{req} ({pct}%) - 지표 수렴 중(매매 대기)"
                return {
                    "symbol": self.symbol,
                    "ready": ready,
                    "current_candles": curr,
                    "required_candles": req,
                    "progress_pct": pct,
                    "status_text": status_text
                }

        strat = MockAIStrategy()
        # 1. 200개 상태
        for i in range(200):
            strat.aggregator.add_backfilled_candle({"target_close": 1000.0})

        st = strat.warmup_status
        self.assertFalse(st["ready"])
        self.assertEqual(st["current_candles"], 200)
        self.assertEqual(st["required_candles"], 840)
        self.assertEqual(st["progress_pct"], 23.8)
        self.assertIn("⏳ [WARM-UP]", st["status_text"])

        # 2. 840개 상태
        for i in range(640):
            strat.aggregator.add_backfilled_candle({"target_close": 1000.0})

        st = strat.warmup_status
        self.assertTrue(st["ready"])
        self.assertEqual(st["current_candles"], 840)
        self.assertEqual(st["progress_pct"], 100.0)
        self.assertIn("🟢 [READY]", st["status_text"])

    async def test_upbit_pagination_sequence(self):
        """페이지네이션으로 수신된 복수 배치가 시간 역순 없이 올바르게 정렬되는지 시뮬레이션 검증"""
        # 배치가 200개씩 과거로 거슬러 올라감 (최신 -> 과거)
        batch1 = [{"candle_date_time_kst": f"2026-09-03T10:{i:02d}:00", "candle_date_time_utc": f"2026-09-03T01:{i:02d}:00", "trade_price": 100.0 + i} for i in range(50, 0, -1)]
        batch2 = [{"candle_date_time_kst": f"2026-09-03T09:{i:02d}:00", "candle_date_time_utc": f"2026-09-03T00:{i:02d}:00", "trade_price": 50.0 + i} for i in range(50, 0, -1)]

        raw_batches = []
        raw_batches.extend(batch1)
        raw_batches.extend(batch2)

        # 과거 -> 현재 순으로 반전
        raw_batches.reverse()
        candles = raw_batches[-100:]

        self.assertEqual(len(candles), 100)
        # 가장 첫 번째가 가장 과거(09:01)여야 함
        self.assertEqual(candles[0]["candle_date_time_kst"], "2026-09-03T09:01:00")
        # 가장 마지막이 가장 최신(10:50)이어야 함
        self.assertEqual(candles[-1]["candle_date_time_kst"], "2026-09-03T10:50:00")


if __name__ == "__main__":
    unittest.main()
