import unittest
import os
import json
import tempfile
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

from unittest.mock import patch, MagicMock
from core.ai_strategy import AIStrategyModule
from core.candle_aggregator import CandleAggregator
from shared.core.tbm_executor import TBMPositionManager
from core.risk_manager import RiskManager

KST = timezone(timedelta(hours=9))


class TestAIRestorePosition(unittest.TestCase):
    def setUp(self):
        with patch.object(AIStrategyModule, '__init__', lambda self, *args, **kwargs: None):
            self.strategy = AIStrategyModule("dummy_deploy_path")
            self.strategy.symbol = "KRW-BTC"
            self.strategy.interval = "1m"
            self.strategy.interval_minutes = 1.0
            self.strategy.interval_seconds = 60
            self.strategy.fee_rate = 0.0005
            self.strategy.cost = 0.001
            self.strategy.params = {
                'buy_threshold': 0.5,
                'take_profit': 3.0,
                'stop_loss': 2.0,
                'emergency_sl_mult': 4.0,
                'predict_ahead_candles': 10,
                'max_hold_candles': 10,
                'cooldown_minutes': 3
            }
            self.strategy.features_order = ["target_return_5", "target_rsi_14", "target_atr"]
            self.strategy.feature_params = {}
            self.strategy.config_snapshot = {}
            self.strategy.has_position = False
            self.strategy.is_pending_entry = False
            self.strategy.is_pending_exit = False
            self.strategy.entry_price = 0.0
            self.strategy.entry_atr = 0.0
            self.strategy.last_signal_atr = 0.0
            self.strategy.entry_time = None
            self.strategy.entry_candle_idx = 0
            self.strategy.current_candle_idx = 0
            self.strategy._cooldown_until_candle = 0
            self.strategy.test_level = 0
            self.strategy.shadow_mode = False
            self.strategy.last_probability = 0.65
            
            self.strategy.aggregator = CandleAggregator(interval_minutes=1.0, max_candles=2000)
            self.strategy.position_manager = TBMPositionManager(self.strategy.params, self.strategy.cost, 1.0)
            self.strategy.risk_manager = RiskManager()
            self.strategy._log_trade_dump = MagicMock()

    def tearDown(self):
        pass

    def _populate_backfill_candles(self, count=60, base_price=100_000_000.0, start_time=None):
        """과거 백필 캔들 주입 (1분 간격)"""
        if start_time is None:
            start_time = datetime.now(KST) - timedelta(minutes=count)

        candles = []
        for i in range(count):
            c_time = start_time + timedelta(minutes=i)
            # 변동폭 0.1% 수준의 현실적인 1분봉 데이터 (High-Low ~ 100,000원)
            c = {
                "timestamp": c_time,
                "target_open": base_price,
                "target_high": base_price + 50_000.0,
                "target_low": base_price - 50_000.0,
                "target_close": base_price + (i % 3 - 1) * 20_000.0,
                "target_volume": 10.0,
                "target_trade_bid_ratio": 0.5,
                "target_trade_count": 50.0,
                "target_large_trade_count": 0.0,
                "target_avg_trade_size": 0.2,
                "upbit_open": base_price,
                "upbit_close": base_price + (i % 3 - 1) * 20_000.0,
                "upbit_high": base_price + 50_000.0,
                "upbit_low": base_price - 50_000.0,
                "upbit_volume": 10.0,
                "upbit_trade_bid_ratio": 0.5,
                "upbit_trade_count": 50.0,
                "upbit_large_trade_count": 0.0,
                "upbit_avg_trade_size": 0.2,
                "binance_spot_close": base_price / 1380.0,
                "binance_spot_volume": 5.0,
                "binance_spot_taker_ratio": 0.5,
                "binance_futures_oi": 0.0,
                "binance_futures_oi_value": 0.0,
                "binance_usdc_volume": 0.0
            }
            self.strategy.aggregator.add_backfilled_candle(c)
            self.strategy.current_candle_idx = self.strategy.aggregator.total_completed_candles
            candles.append(c)
        return candles

    def test_restore_position_with_candle_history_atr(self):
        """1. entry_atr=0.0 유입 시 백필된 캔들 히스토리에서 실제 ATR(0.1% 수준)을 정밀 역추적 복원하는지 검증"""
        base_price = 100_000_000.0
        now = datetime.now(KST)
        candles = self._populate_backfill_candles(count=60, base_price=base_price, start_time=now - timedelta(minutes=60))

        # 10분 전 진입 타임스탬프
        entry_time = now - timedelta(minutes=10)

        # 재기동 복구 호출 (entry_atr=0.0)
        self.strategy.restore_position(entry_price=base_price, entry_atr=0.0, entry_time=entry_time)

        self.assertTrue(self.strategy.has_position)
        self.assertEqual(self.strategy.entry_price, base_price)

        # 검증: ATR이 기존의 1.5%(1,500,000원)로 과대 왜곡되지 않고, 0.05%~0.3%(50,000~300,000원) 수준으로 정밀 복원되었는가?
        self.assertLess(self.strategy.entry_atr, base_price * 0.005, "ATR이 0.5% 이하(정상 1분봉 변동성)여야 함")
        self.assertGreater(self.strategy.entry_atr, 10_000.0, "ATR이 0보다 큰 정상 값이어야 함")

        # TP 배리어 검증: +3.0×ATR 시 진입가 대비 약 +0.2%~+0.5% 범위인지 확인 (과대 왜곡 +4.5% 방지)
        tp_barrier = self.strategy.entry_price + (self.strategy.entry_atr * self.strategy.params['take_profit'])
        tp_pct = ((tp_barrier / base_price) - 1.0) * 100.0
        self.assertLess(tp_pct, 1.5, f"TP 기대수익률이 1.5% 미만이어야 함 (실제: {tp_pct:.3f}%)")

        # 경과 캔들 인덱스 검증: 10분 전 진입이므로 elapsed_candles가 약 10이어야 함
        elapsed = self.strategy.current_candle_idx - self.strategy.entry_candle_idx
        self.assertAlmostEqual(elapsed, 10, delta=1)

    def test_restore_position_preserves_explicit_atr(self):
        """2. trade_history.json 등에서 명시적인 entry_atr가 전달된 경우 이를 1순위로 즉시 보존하는지 검증"""
        base_price = 100_000_000.0
        explicit_atr = 120_000.0  # 0.12%
        now = datetime.now(KST)
        self._populate_backfill_candles(count=30, base_price=base_price, start_time=now - timedelta(minutes=30))

        entry_time = now - timedelta(minutes=3)
        self.strategy.restore_position(entry_price=base_price, entry_atr=explicit_atr, entry_time=entry_time)

        self.assertEqual(self.strategy.entry_atr, explicit_atr)
        elapsed = self.strategy.current_candle_idx - self.strategy.entry_candle_idx
        self.assertAlmostEqual(elapsed, 3, delta=1)

    def test_restore_position_timeout_exit_trigger(self):
        """3. 재기동 시 이미 max_hold_candles(10분)를 초과한 포지션은 즉시 '시간만료' 청산이 발동되는지 검증"""
        base_price = 100_000_000.0
        now = datetime.now(KST)
        self._populate_backfill_candles(count=60, base_price=base_price, start_time=now - timedelta(minutes=60))

        # 15분 전 진입 (max_hold_candles=10 초과)
        entry_time = now - timedelta(minutes=15)
        self.strategy.restore_position(entry_price=base_price, entry_atr=0.0, entry_time=entry_time)

        # 현재 시점에서 캔들 종가 청산 판정 수행
        elapsed = self.strategy.current_candle_idx - self.strategy.entry_candle_idx
        self.assertGreaterEqual(elapsed, 10, f"경과 캔들({elapsed})이 max_hold_candles(10) 이상이어야 함")

        exit_res = self.strategy.position_manager.check_exit(
            entry_price=self.strategy.entry_price,
            entry_atr=self.strategy.entry_atr,
            current_price=base_price,
            elapsed_candles=elapsed,
            current_time=now,
            is_candle_close=True
        )

        self.assertIsNotNone(exit_res)
        self.assertEqual(exit_res['exit_type'], "시간만료")


if __name__ == '__main__':
    unittest.main()
