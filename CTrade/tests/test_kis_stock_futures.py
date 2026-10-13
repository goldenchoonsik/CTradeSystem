# -*- coding: utf-8 -*-
"""
Unit Test: KIS 국내 선물(개별주식선물·지수선물) 실시간 경로
- 최근월 코드 해석(KIS 종목 마스터 fo_stk_code_mts / fo_idx_code_mts) — 만기 지난 월물·스프레드·옵션 제외,
  최종거래일 '당일'부터 다음 월물 (Creon 연결 시계열과 같은 날 교체 — 2026-09-10 실측)
- H0ZFCNT0/H0ZFASP0 파싱 — 지수선물(H0IFCNT0)과 필드 배열이 다르다(현재가 [2])
- 커넥터 구독 — 주식선물은 H0ZF 채널 + 해석된 최근월 코드, 미해석 시 구독 생략
"""
import asyncio
import json
import unittest
from datetime import date, datetime
from unittest import mock

from parsers.kis_parser import KISParser
from core.models import TickData, OrderbookData
from utils import kis_futures_master as kfm

# 2026-10-07 실측 마스터 행 형식 (cp949 디코드 후)
_MASTER = "\n".join([
    "1|1GNW04|KR41GNW40002|금양       F 202504 (  10)| |00000.00|1|001570|금양",          # 상장폐지 잔존 만기물
    "1|A11610|KR4A116A0005|삼성전자   F 202610 (  10)| |00000.00|1|005930|삼성전자",
    "1|A11611|KR4A116B0004|삼성전자   F 202611 (  10)| |00000.00|2|005930|삼성전자",
    "2|D1161001|KR4D116A6BS4|삼성전자   SP 2610-2611 (  10)| |00000.00|1|005930|삼성전자",
    "1|A50610|KR4A506A0003|SK하이닉스 F 202610 (  10)| |00000.00|1|000660|SK하이닉스",
    "1|A50611|KR4A506B0002|SK하이닉스 F 202611 (  10)| |00000.00|2|000660|SK하이닉스",
])
_IDX_MASTER = "\n".join([
    "1|A01612|KR4A016C0004|F 202612| |00000.00|1|2001|KOSPI200",
    "1|A01703|KR4A01730006|F 202703| |00000.00|2|2001|KOSPI200",
    "3|A05611|KR4A056B0001|미니 F 202611| |00000.00|1|2001|KOSPI200",   # 미니 등 다른 상품유형은 제외
])


def _weekdays(d):
    return d.weekday() < 5


class TestFrontMonthResolution(unittest.TestCase):
    def test_front_month_picks_nearest_unexpired(self):
        front = kfm.parse_front_months(_MASTER, "20261007")
        self.assertEqual(front["005930"], "A11610")
        self.assertEqual(front["000660"], "A50610")
        self.assertNotIn("001570", front)     # 만기 지난 월물은 순번 1이어도 제외

    def test_rolls_on_last_trading_day(self):
        # 2026-10 최종거래일 = 둘째 목요일 10-08. Creon 연결은 최종거래일 당일 장 시작부터 다음 월물이다
        self.assertEqual(kfm.parse_front_months(_MASTER, "20261007", _weekdays)["005930"], "A11610")
        self.assertEqual(kfm.parse_front_months(_MASTER, "20261008", _weekdays)["005930"], "A11611")
        self.assertEqual(kfm.parse_front_months(_MASTER, "20261009", _weekdays)["005930"], "A11611")

    def test_last_trading_day_steps_back_over_holiday(self):
        self.assertEqual(kfm.last_trading_day(2026, 10, _weekdays), date(2026, 10, 8))
        self.assertEqual(kfm.last_trading_day(2026, 12, _weekdays), date(2026, 12, 10))
        holiday = lambda d: d.weekday() < 5 and d != date(2026, 10, 8)
        self.assertEqual(kfm.last_trading_day(2026, 10, holiday), date(2026, 10, 7))
        self.assertEqual(kfm.parse_front_months(_MASTER, "20261007", holiday)["005930"], "A11611")

    def test_index_futures_front_month(self):
        self.assertEqual(kfm.parse_front_months(_IDX_MASTER, "20261007", _weekdays), {"2001": "A01612"})
        self.assertEqual(kfm.parse_front_months(_IDX_MASTER, "20261210", _weekdays), {"2001": "A01703"})

    def test_master_base_of(self):
        self.assertEqual(kfm.master_base_of({"underlying": "005930"}), ("stock", "005930"))
        self.assertEqual(kfm.master_base_of({"kis_futures_base": "2001"}), ("index", "2001"))
        self.assertIsNone(kfm.master_base_of({"market_type": "KRX_STOCK"}))

    def test_resolve_uses_master_per_kind(self):
        kfm._cache.clear()
        seen = []
        def fetch(kind):
            seen.append(kind)
            return _IDX_MASTER if kind == "index" else _MASTER
        now = datetime(2026, 10, 7)
        self.assertEqual(kfm.resolve_front_month("2001", kind="index", now=now, fetch=fetch), "A01612")
        self.assertEqual(kfm.resolve_front_month("000660", kind="stock", now=now, fetch=fetch), "A50610")
        self.assertEqual(kfm.resolve_front_month("005930", kind="stock", now=now, fetch=fetch), "A11610")
        self.assertEqual(seen, ["index", "stock"])      # 종류별 하루 한 번만 내려받는다

    def test_resolve_failure_returns_none(self):
        kfm._cache.clear()
        def boom(kind):
            raise OSError("network down")
        self.assertIsNone(kfm.resolve_front_month("005930", now=datetime(2026, 10, 7), fetch=boom))


class TestStockFuturesParser(unittest.TestCase):
    def test_tick_reads_price_from_field_2(self):
        fields = [""] * 49
        fields[0], fields[1] = "A11610", "102000"
        fields[2] = "274000"      # 현재가
        fields[5] = "0.74"        # 전일대비율 — 지수선물 파서라면 이걸 가격으로 읽는다
        fields[9] = "13"
        fields[34], fields[35] = "274500", "274000"
        tick = KISParser.parse_message_str("0|H0ZFCNT0|001|" + "^".join(fields), "11100")
        self.assertIsInstance(tick, TickData)
        self.assertEqual(tick.symbol, "11100")
        self.assertEqual(tick.price, 274000.0)
        self.assertEqual(tick.volume, 13.0)
        self.assertEqual(tick.side, "ASK")

    def test_orderbook_10_levels(self):
        fields = [""] * 68
        fields[0], fields[1] = "A50610", "102000"
        for i in range(10):
            fields[2 + i] = str(1751000 + 1000 * (i + 1))
            fields[12 + i] = str(1751000 - 1000 * i)
            fields[42 + i] = str(10 + i)
            fields[52 + i] = str(20 + i)
        ob = KISParser.parse_message_str("0|H0ZFASP0|001|" + "^".join(fields), "15000")
        self.assertIsInstance(ob, OrderbookData)
        self.assertEqual(len(ob.units), 10)
        self.assertEqual(ob.units[0].ask_price, 1752000.0)
        self.assertEqual(ob.units[0].bid_price, 1751000.0)
        self.assertEqual(ob.units[9].bid_size, 29.0)


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(json.loads(msg))


class TestStockFuturesSubscribe(unittest.TestCase):
    def _connector(self, symbols):
        from connectors.kis_connector import KISConnector
        return KISConnector(symbols, asyncio.Queue(), auth_manager=None)

    def test_subscribes_h0zf_with_resolved_code(self):
        conn = self._connector(["15000"])
        with mock.patch("utils.kis_futures_master.resolve_front_month", return_value="A50610"):
            conn._resolve_futures_front_months()
        self.assertEqual(conn.reverse_symbol_mapping.get("A50610"), "15000")
        ws = _FakeWS()
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            asyncio.run(conn._subscribe(ws, "key", "15000"))
        self.assertEqual([p["body"]["input"]["tr_id"] for p in ws.sent], ["H0ZFCNT0", "H0ZFASP0"])
        self.assertTrue(all(p["body"]["input"]["tr_key"] == "A50610" for p in ws.sent))

    def test_unresolved_code_skips_subscription(self):
        conn = self._connector(["11100"])
        with mock.patch("utils.kis_futures_master.resolve_front_month", return_value=None):
            conn._resolve_futures_front_months()
        ws = _FakeWS()
        asyncio.run(conn._subscribe(ws, "key", "11100"))
        self.assertEqual(ws.sent, [])

    def test_index_futures_subscribe_resolved_front_month(self):
        conn = self._connector(["10100"])
        with mock.patch("utils.kis_futures_master.resolve_front_month", return_value="A01703") as rf:
            conn._resolve_futures_front_months()
        rf.assert_called_once_with("2001", kind="index")
        self.assertEqual(conn.reverse_symbol_mapping.get("A01703"), "10100")
        ws = _FakeWS()
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            asyncio.run(conn._subscribe(ws, "key", "10100"))
        self.assertEqual([p["body"]["input"]["tr_id"] for p in ws.sent], ["H0IFCNT0", "H0IFASP0"])
        self.assertTrue(all(p["body"]["input"]["tr_key"] == "A01703" for p in ws.sent))

    def test_index_futures_unresolved_skips_subscription(self):
        conn = self._connector(["10100"])
        with mock.patch("utils.kis_futures_master.resolve_front_month", return_value=None):
            conn._resolve_futures_front_months()
        ws = _FakeWS()
        asyncio.run(conn._subscribe(ws, "key", "10100"))
        self.assertEqual(ws.sent, [])       # 박제된 옛 코드(A01612)로 대체하지 않는다


if __name__ == "__main__":
    unittest.main()
