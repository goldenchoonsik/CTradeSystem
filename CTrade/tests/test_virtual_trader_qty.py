import unittest
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.simulator import VirtualTrader

class TestVirtualTraderQty(unittest.TestCase):
    def test_stock_and_crypto_qty(self):
        trader = VirtualTrader(initial_balance=10000000.0, silent=True)
        
        # 1. 주식/ETF (069500): 예산 1,000,000원 / 단가 35,450원 -> 28주 (floor)
        asyncio.run(trader.buy("069500", 35450.0, 1000000.0))
        pos_stock = trader.positions.get("069500")
        self.assertIsNotNone(pos_stock)
        self.assertEqual(pos_stock["qty"], 28.0, f"Expected 28 shares integer, got {pos_stock['qty']}")
        
        # 2. 해외 주식 (TQQQ): 예산 1,000,000원 / 단가 82,300원 -> 12주 (floor)
        asyncio.run(trader.buy("TQQQ", 82300.0, 1000000.0))
        pos_tqqq = trader.positions.get("TQQQ")
        self.assertIsNotNone(pos_tqqq)
        self.assertEqual(pos_tqqq["qty"], 12.0, f"Expected 12 shares integer, got {pos_tqqq['qty']}")
        
        # 3. 가상화폐 (KRW-BTC): 예산 1,000,000원 / 단가 109,732,000원 -> 소수점 8자리
        asyncio.run(trader.buy("KRW-BTC", 109732000.0, 1000000.0))
        pos_coin = trader.positions.get("KRW-BTC")
        self.assertIsNotNone(pos_coin)
        self.assertAlmostEqual(pos_coin["qty"], 0.00910855, places=5)
        
        print("✓ VirtualTrader 주식 정수주(floor) 및 코인 소수점 수량 검증 완료!")

if __name__ == "__main__":
    unittest.main()
