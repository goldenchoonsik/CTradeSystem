import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.config.config_manager import search_symbols


class TestSymbolSearch(unittest.TestCase):
    def test_search_by_korean_name(self):
        # 1. 삼성 검색
        results = search_symbols("삼성", limit=10)
        self.assertTrue(len(results) > 0)
        symbols = [r["symbol"] for r in results]
        self.assertIn("005930", symbols) # 삼성전자
        names = [r["name"] for r in results]
        self.assertTrue(any("삼성" in n for n in names))

    def test_search_by_korean_etf(self):
        # 2. 레버리지 검색
        results = search_symbols("레버리지", limit=10)
        symbols = [r["symbol"] for r in results]
        self.assertIn("122630", symbols) # KODEX 레버리지

    def test_search_by_crypto_name(self):
        # 3. 리플 / XRP 검색
        res_kor = search_symbols("리플", limit=10)
        symbols_kor = [r["symbol"] for r in res_kor]
        self.assertIn("KRW-XRP", symbols_kor)

        res_eng = search_symbols("XRP", limit=10)
        symbols_eng = [r["symbol"] for r in res_eng]
        self.assertIn("KRW-XRP", symbols_eng)

    def test_search_by_us_etf(self):
        # 4. TQQQ 검색
        results = search_symbols("TQQQ", limit=10)
        symbols = [r["symbol"] for r in results]
        self.assertIn("TQQQ", symbols)

    def test_search_by_symbol_code(self):
        # 5. 종목코드 0059 검색
        results = search_symbols("0059", limit=10)
        symbols = [r["symbol"] for r in results]
        self.assertIn("005930", symbols)

    def test_search_kodex_nasdaq(self):
        # 6. KODEX 미국나스닥100 검색 (공백 포함 및 TR/환헤지 변형)
        res1 = search_symbols("KODEX 미국나스닥100", limit=10)
        self.assertTrue(len(res1) > 0)
        symbols1 = [r["symbol"] for r in res1]
        self.assertIn("379810", symbols1) # KODEX 미국나스닥100TR

        res2 = search_symbols("미국나스닥", limit=10)
        names2 = [r["name"] for r in res2]
        self.assertTrue(any("나스닥" in n for n in names2))

    def test_search_kodex_money_market(self):
        # 7. 0048J0 (KODEX 미국머니마켓액티브) 종목코드 및 명칭 검색 검증
        res_code = search_symbols("0048J0", limit=10)
        self.assertTrue(len(res_code) > 0)
        symbols = [r["symbol"] for r in res_code]
        self.assertIn("0048J0", symbols)

        # 소문자 코드 검색
        res_lower = search_symbols("0048j0", limit=10)
        self.assertIn("0048J0", [r["symbol"] for r in res_lower])

        # 머니마켓 키워드 검색
        res_kw = search_symbols("머니마켓", limit=10)
        self.assertTrue(len(res_kw) > 0)
        self.assertIn("0048J0", [r["symbol"] for r in res_kw])
        self.assertEqual(res_kw[0]["symbol"], "0048J0")
        self.assertEqual(res_kw[0]["name"], "KODEX 미국머니마켓액티브")
        self.assertEqual(res_kw[0]["market_type"], "KRX_ETF")


if __name__ == "__main__":
    unittest.main()
