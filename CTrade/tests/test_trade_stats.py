import sys
import os
import time
import asyncio
import unittest

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.simulator import VirtualTrader


class TestTradeStats(unittest.TestCase):

    def test_performance_tracking(self):
        # 1. 초기화 (초기 자산 10,000,000원, 수수료 0.05%, silent=True)
        trader = VirtualTrader(initial_balance=10000000.0, fee_rate=0.0005, silent=True)
        symbol = "KRW-BTC"
        
        # 2. 매수 테스트 (50,000,000원 가격에 1,000,000원어치 매수)
        buy_price = 50000000.0
        budget = 1000000.0
        
        success = asyncio.run(trader.buy(symbol, buy_price, budget))
        self.assertTrue(success)
        self.assertIn(symbol, trader.positions)
        self.assertEqual(trader.total_trades, 1)
        
        # 수수료 포함 총 지출 잔액 확인 (budget 내에서 수수료 공제 후 매수)
        expected_balance = 10000000.0 - budget
        self.assertLess(abs(trader.balance - expected_balance), 1.0)

        # 3. 매도 테스트 (5% 수익 상황 시뮬레이션)
        sell_price = buy_price * 1.05
        
        profit = asyncio.run(trader.sell(symbol, sell_price))
        self.assertGreater(profit, 0)
        self.assertNotIn(symbol, trader.positions)
        self.assertEqual(len(trader.history), 2)  # 1 BUY + 1 SELL
        
        # 통계 확인
        stats = trader.get_stats({symbol: sell_price})
        self.assertEqual(stats['total_trades'], 1)
        self.assertEqual(stats['win_rate'], 100.0)
        self.assertIn(symbol, stats['symbol_stats'])
        self.assertGreater(stats['symbol_stats'][symbol]['total_profit'], 0)

        # 4. 손실 매매 시뮬레이션
        asyncio.run(trader.buy(symbol, 10000, 100000))
        asyncio.run(trader.sell(symbol, 9000))
        
        final_stats = trader.get_stats({symbol: 9000})
        self.assertEqual(final_stats['total_trades'], 2)
        self.assertEqual(final_stats['win_rate'], 50.0)  # 1승 1패
        self.assertEqual(final_stats['symbol_stats'][symbol]['trades'], 2)


if __name__ == "__main__":
    unittest.main()
