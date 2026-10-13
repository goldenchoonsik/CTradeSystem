import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from collections import namedtuple

# 프로젝트 루트 및 CTrade 경로 추가
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, root_dir)
sys.path.insert(0, os.path.join(root_dir, "CTrade"))

from shared.core.market_utils import round_to_krx_tick, get_krx_tick_size, detect_market_type, is_krx_entry_allowed, is_krx_force_exit
from shared.core.tbm_executor import TBMPositionManager
from core.ai_strategy import AIStrategyModule, SignalAction, SignalReason
from core.tick_router import route_tick

KST = timezone(timedelta(hours=9))


class TestStockRoutine(unittest.TestCase):

    def test_krx_tick_size(self):
        # 1. ETF 기본 모드 (is_etf=True): 2,000원 이상은 5원 고정
        self.assertEqual(get_krx_tick_size(1500, is_etf=True), 1)
        self.assertEqual(get_krx_tick_size(3500, is_etf=True), 5)
        self.assertEqual(get_krx_tick_size(15000, is_etf=True), 5)
        self.assertEqual(get_krx_tick_size(35000, is_etf=True), 5)
        self.assertEqual(get_krx_tick_size(150000, is_etf=True), 5)
        self.assertEqual(round_to_krx_tick(35234.5, is_etf=True), 35235)
        self.assertEqual(round_to_krx_tick(15234.5, is_etf=True), 15235)

        # 2. 일반 주식 모드 (is_etf=False)
        self.assertEqual(get_krx_tick_size(1500, is_etf=False), 1)
        self.assertEqual(get_krx_tick_size(3500, is_etf=False), 5)
        self.assertEqual(get_krx_tick_size(15000, is_etf=False), 10)
        self.assertEqual(get_krx_tick_size(35000, is_etf=False), 50)
        self.assertEqual(get_krx_tick_size(150000, is_etf=False), 100)
        self.assertEqual(round_to_krx_tick(35234.5, is_etf=False), 35250)
        self.assertEqual(round_to_krx_tick(15234.5, is_etf=False), 15230)

    def test_tbm_position_manager_market_type(self):
        params = {'take_profit': 2.0, 'stop_loss': 1.0, 'predict_ahead_candles': 10, 'cooldown_minutes': 3}
        tbm_krx = TBMPositionManager(params, cost=0.0003, interval_minutes=1.0, intraday_square_off=True, market_type='KRX')
        tbm_crypto = TBMPositionManager(params, cost=0.001, interval_minutes=30.0, intraday_square_off=True, market_type='CRYPTO')

        # 15:18 시점
        dt_1518 = datetime(2026, 8, 24, 15, 18, 5, tzinfo=KST)
        exit_krx = tbm_krx.check_exit(entry_price=35000, entry_atr=100, current_price=35050, elapsed_candles=3, current_time=dt_1518)
        self.assertIsNotNone(exit_krx)
        self.assertEqual(exit_krx['exit_type'], '당일청산')
        
        exit_crypto = tbm_crypto.check_exit(entry_price=100000000, entry_atr=1000000, current_price=100100000, elapsed_candles=1, current_time=dt_1518)
        self.assertIsNone(exit_crypto)

        # 08:50 시점 (가상화폐는 24시간 연속 시장이므로 당일청산 발동하지 않음)
        dt_0850 = datetime(2026, 8, 24, 8, 50, 0, tzinfo=KST)
        exit_crypto_0850 = tbm_crypto.check_exit(entry_price=100000000, entry_atr=1000000, current_price=100100000, elapsed_candles=1, current_time=dt_0850)
        self.assertIsNone(exit_crypto_0850)

    def test_ai_strategy_and_tick_routing(self):
        deploy_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "069500_1m", "KODEX200_M1_BEST1")
        if not os.path.exists(deploy_path):
            return

        ai_mod = AIStrategyModule(deploy_path=deploy_path, symbol="069500", fee_rate=0.00005, slippage_rate=0.0001)
        self.assertEqual(ai_mod.market_type, "KRX")

        # Mock TickData
        TickData = namedtuple('TickData', ['symbol', 'exchange', 'price', 'volume', 'side'])
        ai_strategies = {"069500": ai_mod}

        # 1. NQ 선물 틱 전달
        route_tick(TickData("NQ_FUT", "GLOBAL", 18500.0, 10.0, "BID"), candle_aggregators={}, ai_strategies=ai_strategies)
        self.assertEqual(ai_mod.aggregator._obs["nq_futures_close"].value, 18500.0)

        # 2. S&P 500 선물 틱 전달
        route_tick(TickData("SP500_FUT", "GLOBAL", 5500.0, 10.0, "BID"), candle_aggregators={}, ai_strategies=ai_strategies)
        self.assertEqual(ai_mod.aggregator._obs["sp500_futures_close"].value, 5500.0)

        # 3. KOSPI 200 선물 틱 전달
        route_tick(TickData("101V3000", "KIS", 380.0, 10.0, "BID"), candle_aggregators={}, ai_strategies=ai_strategies)
        self.assertEqual(ai_mod.aggregator._obs["kospi_futures_close"].value, 380.0)


if __name__ == "__main__":
    unittest.main()
