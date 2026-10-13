import unittest
import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock
import pandas as pd
import numpy as np

from core.candle_aggregator import CandleAggregator
from core.risk_manager import RiskManager
from core.ai_strategy import AIStrategyModule
from core.order_executor import SmartOrderExecutor
from core.real_trader import UpbitRealTrader
from shared.features.feature_registry import _compute_coinbase_premium_index

KST = timezone(timedelta(hours=9))


class TestAICoreIntegrity(unittest.IsolatedAsyncioTestCase):

    def test_3_2_candle_aggregator_ffill_loop_and_delayed_tick_drop(self):
        """3-2 검증: 다수 캔들 건너뜀 시 빈 캔들 순차 생성(ffill) 및 과거 지연 틱 오염 방지"""
        agg = CandleAggregator(interval_minutes=1.0, max_candles=100)
        base_time = datetime(2026, 8, 29, 12, 0, 0, tzinfo=KST)

        # 1. 12:00:10 첫 틱 수신
        agg.on_target_tick(100.0, 1.0, "BID", timestamp=base_time + timedelta(seconds=10))

        # 2. 12:03:05 틱 수신 (3분 갭 발생: 12:00, 12:01, 12:02 총 3개 캔들이 순차 완성되어야 함)
        tick_time_3m_later = base_time + timedelta(minutes=3, seconds=5)
        agg.on_target_tick(105.0, 1.0, "BID", timestamp=tick_time_3m_later)

        # 3개 캔들이 완성되었는지 확인 (누락 없이 ffill)
        self.assertEqual(len(agg.candles), 3, f"3분 경과 시 3개의 캔들이 완성되어야 함 (실제: {len(agg.candles)})")
        self.assertEqual(agg.candles[0]['target_close'], 100.0)
        self.assertEqual(agg.candles[1]['target_close'], 100.0)  # ffill
        self.assertEqual(agg.candles[2]['target_close'], 100.0)  # ffill

        # 3. 과거 지연 틱 수신 (12:01:30 틱이 뒤늦게 도착한 경우)
        past_tick_time = base_time + timedelta(minutes=1, seconds=30)
        agg.on_target_tick(999999.0, 100.0, "BID", timestamp=past_tick_time)

        # 현재 캔들 빌더의 close가 999999.0으로 오염되지 않고 105.0 유지 확인
        self.assertEqual(agg._target_builder.close, 105.0, "과거 지연 틱이 현재 진행 중인 캔들을 오염시키지 않아야 함")

    def test_3_8_coinbase_premium_index_neutral_ffill(self):
        """3-8 검증: coinbase_spot_close 결측 또는 0값 시 binance_spot_close로 ffill되어 괴리율 0.0% 중립 보장"""
        df = pd.DataFrame({
            'binance_spot_close': [100000.0, 101000.0, 102000.0],
            'coinbase_spot_close': [np.nan, 0.0, 102102.0]  # 결측, 0값, 정상값(+0.1%)
        })
        params = {'candles': 1}
        premium_series = _compute_coinbase_premium_index(df, params, '1m')

        # 1행: NaN -> 0.0%
        self.assertAlmostEqual(premium_series.iloc[0], 0.0, places=6)
        # 2행: 0.0 -> 0.0%
        self.assertAlmostEqual(premium_series.iloc[1], 0.0, places=6)
        # 3행: 102102 vs 102000 -> +0.001 (+0.1%)
        self.assertAlmostEqual(premium_series.iloc[2], 0.001, places=4)

    def test_3_7_global_risk_manager_shared_across_symbols(self):
        """3-7 검증: 전역 단일 GlobalRiskManager가 복수 종목의 동시 포지션(3개) 및 일일 손실 한도를 일원화 통제"""
        global_rm = RiskManager(
            daily_loss_limit_pct=-3.0,
            max_concurrent_positions=2
        )

        with patch.object(AIStrategyModule, '__init__', lambda self, *args, **kwargs: None):
            strat_btc = AIStrategyModule("path_btc")
            strat_btc.risk_manager = global_rm
            strat_btc.test_level = 0

            strat_eth = AIStrategyModule("path_eth")
            strat_eth.risk_manager = global_rm
            strat_eth.test_level = 0

            strat_sol = AIStrategyModule("path_sol")
            strat_sol.risk_manager = global_rm
            strat_sol.test_level = 0

        # 1. BTC 진입
        can_open, _ = strat_btc.risk_manager.can_open_position()
        self.assertTrue(can_open)
        strat_btc.risk_manager.on_position_opened()

        # 2. ETH 진입
        can_open, _ = strat_eth.risk_manager.can_open_position()
        self.assertTrue(can_open)
        strat_eth.risk_manager.on_position_opened()

        # 3. SOL 진입 시도 -> 전역 최대 동시 포지션(2개) 초과로 차단
        can_open, reason = strat_sol.risk_manager.can_open_position()
        self.assertFalse(can_open, "전역 포지션 한도 도달 시 차단되어야 함")
        self.assertIn("최대 동시 포지션 초과", reason)

        # 4. BTC 청산 시 손실(-3.5%) 발생 -> 전역 일일 손실 한도 도달
        strat_btc.risk_manager.on_position_closed(-3.5, "손절")
        can_open, reason = strat_sol.risk_manager.can_open_position()
        self.assertFalse(can_open, "전역 일일 손실 한도 도달 시 모든 종목 매수 차단")
        self.assertIn("일일 손실 한도 도달", reason)

    async def test_3_4_smart_order_executor_aborts_on_pending_cancelled(self):
        """3-4 검증: SmartOrderExecutor 대기 중 is_pending_entry 해제 시 즉시 탈출 (유령 체결 방지)"""
        engine_mock = MagicMock()
        engine_mock.is_running = True
        ai_mod_mock = MagicMock()
        ai_mod_mock.is_pending_entry = False  # 이미 취소됨
        engine_mock.ai_strategies = {"KRW-BTC": ai_mod_mock}

        executor = SmartOrderExecutor(engine_mock)
        res = await executor.execute_smart_buy(
            symbol="KRW-BTC",
            trigger_price=100_000_000.0,
            budget=1_000_000.0,
            wait_seconds=60
        )
        self.assertIsNone(res, "pending_entry가 False이면 즉시 None을 반환하고 대기를 중단해야 함")

    async def test_3_5_real_trader_auto_syncs_on_zero_balance(self):
        """3-5 검증: RealTrader 매도 시 실제 거래소 잔고가 0이면 로컬 허수 포지션을 자동 청산(Sync)하여 정합성 복구"""
        trader = UpbitRealTrader(upbit_auth=None)
        trader.force_set_position("KRW-BTC", 100_000_000.0, 1.0, time.time(), 100_000_000.0, False)
        trader.get_upbit_balance = AsyncMock(return_value=0.0)  # 업비트 실제 잔고 0

        res = await trader.sell("KRW-BTC", 105_000_000.0, reason="TP")
        self.assertIsNotNone(res)
        self.assertNotIn("KRW-BTC", trader.positions, "실제 잔고 0이면 로컬 허수 포지션이 자동 청산되어 제거되어야 함")


if __name__ == '__main__':
    unittest.main()
