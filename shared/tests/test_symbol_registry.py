import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.config.config_manager import (
    load_symbols_registry, save_symbols_registry,
    get_symbol_info, register_symbol, delete_symbol
)


class TestSymbolRegistry(unittest.TestCase):
    def setUp(self):
        self.test_symbol = "999999"
        self.test_crypto = "KRW-TESTCOIN"

    def tearDown(self):
        delete_symbol(self.test_symbol)
        delete_symbol(self.test_crypto)

    def test_load_symbols(self):
        reg = load_symbols_registry()
        self.assertIsInstance(reg, dict)
        self.assertIn("069500", reg)
        self.assertEqual(reg["069500"]["name"], "KODEX 200")

    def test_get_symbol_info_existing(self):
        info = get_symbol_info("069500")
        self.assertEqual(info["symbol"], "069500")
        self.assertEqual(info["market_type"], "KRX_ETF")
        self.assertEqual(info["exchange"], "KIS")

    def test_get_symbol_info_inference(self):
        # 6자리 숫자 주식 추론
        info_stock = get_symbol_info("035720") # 카카오
        self.assertEqual(info_stock["market_type"], "KRX_STOCK")
        self.assertEqual(info_stock["exchange"], "KIS")

        # KRW- 가상자산 추론
        info_crypto = get_symbol_info("KRW-AVAX")
        self.assertEqual(info_crypto["market_type"], "CRYPTO")
        self.assertEqual(info_crypto["exchange"], "UPBIT")
        self.assertEqual(info_crypto["binance_symbol"], "AVAXUSDT")

    def test_register_and_delete_symbol(self):
        # 신규 등록
        payload = {
            "symbol": self.test_symbol,
            "name": "테스트종목",
            "market_type": "KRX_STOCK",
            "exchange": "KIS",
            "tick_size": 50.0,
            "default_fee": 0.00015,
            "default_slippage": 0.0002,
            "description": "단위 테스트용 종목"
        }
        res = register_symbol(payload)
        self.assertEqual(res["symbol"], self.test_symbol)
        self.assertEqual(res["name"], "테스트종목")

        # 조회 확인
        reg = load_symbols_registry()
        self.assertIn(self.test_symbol, reg)
        self.assertEqual(reg[self.test_symbol]["tick_size"], 50.0)

        # 수정 확인
        payload["name"] = "수정된테스트종목"
        payload["tick_size"] = 100.0
        res_updated = register_symbol(payload)
        self.assertEqual(res_updated["name"], "수정된테스트종목")
        self.assertEqual(res_updated["tick_size"], 100.0)

        # 삭제 확인
        deleted = delete_symbol(self.test_symbol)
        self.assertTrue(deleted)
        reg_after = load_symbols_registry()
        self.assertNotIn(self.test_symbol, reg_after)


if __name__ == "__main__":
    unittest.main()
