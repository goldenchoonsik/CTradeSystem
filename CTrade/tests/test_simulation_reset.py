import unittest
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.simulator import VirtualTrader

class TestSimulationReset(unittest.TestCase):
    def test_simulation_reset_and_seed_balance(self):
        # 1. 초기 자본금 50,000,000원으로 생성
        trader = VirtualTrader(initial_balance=50000000.0, silent=True)
        self.assertEqual(trader.balance, 50000000.0)
        self.assertEqual(trader.initial_balance, 50000000.0)

        # 2. 매수 2건 실행
        asyncio.run(trader.buy("KRW-BTC", 100000000.0, 10000000.0))
        asyncio.run(trader.buy("069500", 35000.0, 5000000.0))

        self.assertGreater(len(trader.positions), 0)
        self.assertLess(trader.balance, 50000000.0)

        # 매도 1건 실행 (수익 및 히스토리 발생)
        asyncio.run(trader.sell("KRW-BTC", 102000000.0, "TAKE_PROFIT"))
        self.assertGreater(len(trader.history), 0)
        self.assertGreater(trader.total_trades, 0)

        # 3. reset(new_initial_balance=30000000.0) 실행
        trader.reset(30000000.0)

        # 4. 검증
        self.assertEqual(trader.balance, 30000000.0)
        self.assertEqual(trader.initial_balance, 30000000.0)
        self.assertEqual(len(trader.positions), 0)
        self.assertEqual(len(trader.history), 0)
        self.assertEqual(trader.total_trades, 0)
        self.assertEqual(trader.win_trades, 0)

        stats = trader.get_stats({})
        self.assertEqual(stats["balance"], 30000000.0)
        self.assertEqual(stats["total_asset"], 30000000.0)
        self.assertEqual(stats["total_roi"], 0.0)
        self.assertEqual(stats["total_trades"], 0)
        self.assertEqual(stats["active_positions"], 0)

        print("✓ VirtualTrader 완전 리셋 및 자본금 동적 변경 검증 통과!")

if __name__ == "__main__":
    unittest.main()
