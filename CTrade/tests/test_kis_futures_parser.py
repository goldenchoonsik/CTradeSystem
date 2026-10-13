# -*- coding: utf-8 -*-
"""
Unit Test: KIS Domestic Index Futures Parser and Mapping Parity Guard
지수선물 실시간 체결(H0IFCNT0) 및 호가(H0IFASP0) 전문 파싱과 심볼 매핑 무결성을 검증합니다.
"""
import unittest
import time
from parsers.kis_parser import KISParser
from core.models import TickData, OrderbookData

class TestKISFuturesParser(unittest.TestCase):
    def test_parse_futures_tick_success(self):
        """H0IFCNT0 전문이 TickData(symbol, price, volume, side)로 정확히 파싱되는지 검증"""
        # H0IFCNT0 전문 샘플:
        # fields[0]=A01612, [1]=110935, [2]=5.00, [3]=2, [4]=0.45, [5]=1110.25 (price),
        # [6]=1105.00, [7]=1112.00, [8]=1104.00, [9]=3 (volume), [10]=15000,
        # ... [34]=1110.30 (ask1), [35]=1110.25 (bid1)
        fields = [""] * 45
        fields[0] = "A01612"
        fields[1] = "110935"
        fields[5] = "1110.25"
        fields[9] = "3"
        fields[34] = "1110.30"
        fields[35] = "1110.25"
        data_body = "^".join(fields)
        raw_msg = f"0|H0IFCNT0|001|{data_body}"

        # 내부 심볼 '10100'으로 매핑하여 파싱
        tick = KISParser.parse_message_str(raw_msg, "10100")
        self.assertIsNotNone(tick)
        self.assertIsInstance(tick, TickData)
        self.assertEqual(tick.symbol, "10100")
        self.assertEqual(tick.exchange, "KIS")
        self.assertEqual(tick.price, 1110.25)
        self.assertEqual(tick.volume, 3.0)
        self.assertIn(tick.side, ("BID", "ASK"))

    def test_parse_futures_orderbook_success(self):
        """H0IFASP0 전문이 OrderbookData(5단계 호가)로 정확히 파싱되는지 검증"""
        fields = [""] * 38
        fields[0] = "A01612"
        fields[1] = "110935"
        # ask 1~5: index 2~6
        for i in range(5):
            fields[2 + i] = str(1110.30 + i * 0.05)
        # bid 1~5: index 7~11
        for i in range(5):
            fields[7 + i] = str(1110.25 - i * 0.05)
        # ask_size 1~5: index 22~26
        for i in range(5):
            fields[22 + i] = str(10 + i)
        # bid_size 1~5: index 27~31
        for i in range(5):
            fields[27 + i] = str(20 + i)

        data_body = "^".join(fields)
        raw_msg = f"0|H0IFASP0|001|{data_body}"

        ob = KISParser.parse_message_str(raw_msg, "10100")
        self.assertIsNotNone(ob)
        self.assertIsInstance(ob, OrderbookData)
        self.assertEqual(ob.symbol, "10100")
        self.assertEqual(len(ob.units), 5)
        self.assertAlmostEqual(ob.units[0].ask_price, 1110.30)
        self.assertAlmostEqual(ob.units[0].bid_price, 1110.25)
        self.assertEqual(ob.units[0].ask_size, 10.0)
        self.assertEqual(ob.units[0].bid_size, 20.0)

if __name__ == "__main__":
    unittest.main()
