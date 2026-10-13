"""
Unit Test: Verify Soft TP/SL Exact Parity using Completed Candle Close
"""
import unittest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from core.ai_strategy import AIStrategyModule, SignalAction, SignalReason

KST = timezone(timedelta(hours=9))


class TestAICandleCloseParity(unittest.TestCase):

    def test_soft_tp_uses_candle_close_parity(self):
        """_on_candle_complete에서 완성된 캔들의 target_close/upbit_close 가격을 기준으로 Soft TP가 트리거되는지 검증"""
        # AIStrategyModule 인스턴스를 가볍게 생성
        strategy = AIStrategyModule.__new__(AIStrategyModule)
        strategy.symbol = "069500"
        strategy.market_type = "KRX"
        strategy.has_position = True
        strategy.entry_price = 10000.0
        strategy.entry_atr = 100.0
        strategy.entry_candle_idx = 10
        strategy.current_candle_idx = 11
        strategy.is_pending_exit = False
        strategy.last_probability = 0.65
        strategy.enable_parity_logging = False
        strategy._trade_dump_file = None
        strategy.aggregator = MagicMock()
        strategy.aggregator.has_enough_data = True

        # PositionManager 설정
        from shared.core.tbm_executor import TBMPositionManager
        params = {
            'take_profit': 2.0,
            'stop_loss': 1.5,
            'emergency_sl_mult': 3.0,
            'max_hold_candles': 20,
            'predict_ahead_candles': 20
        }
        strategy.position_manager = TBMPositionManager(params=params, cost=0.001, interval_minutes=1.0, intraday_square_off=False, market_type="KRX")
        # 완성된 캔들 DataFrame 생성 (종가는 10,250원으로 익절가 도달)
        candle_data = {
            'timestamp': [datetime(2026, 8, 29, 9, 30, tzinfo=KST)],
            'target_open': [10100.0],
            'target_high': [10300.0],
            'target_low': [10050.0],
            'target_close': [10250.0],
            'target_volume': [1000.0],
            'target_trade_bid_ratio': [0.6],
            'target_trade_count': [50.0],
            'target_large_trade_count': [2.0],
            'target_avg_trade_size': [20.0],
        }
        df = pd.DataFrame(candle_data)

        # 신규 캔들의 첫 틱 가격(current_price)은 10,150원 (익절 미달)이지만,
        # 완성된 캔들 종가(target_close)는 10,250원 (익절 도달)인 상황
        tick_current_price = 10150.0
        tick_time = datetime(2026, 8, 29, 9, 31, tzinfo=KST)

        sig = strategy._on_candle_complete(df, current_price=tick_current_price, current_timestamp=tick_time)

        # 검증: 완성 캔들 종가(10,250) 기준으로 Soft TP가 정상 판정되어 매도 시그널 생성
        self.assertIsNotNone(sig)
        self.assertEqual(sig.action, SignalAction.SELL)
        self.assertEqual(sig.reason, SignalReason.TAKE_PROFIT)
        self.assertEqual(sig.price, 10250.0)  # 완성 캔들 종가와 정확히 일치


if __name__ == "__main__":
    unittest.main()
