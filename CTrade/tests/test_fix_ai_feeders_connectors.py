"""회귀 테스트: 피더 stale 처리, KIS 다건 레코드, 백오프, Upbit/Binance 파서/커넥터, 레이더"""
import asyncio
import json
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from connectors.global_macro_feeder import GlobalMacroFeeder
from connectors.yahoo_futures_feeder import YahooFuturesFeeder
from connectors.base_connector import BaseConnector
from connectors.upbit_connector import UpbitConnector
from parsers.kis_parser import KISParser
from parsers.binance_parser import BinanceParser

KST = timezone(timedelta(hours=9))


class TestMacroFeederStale(unittest.IsolatedAsyncioTestCase):
    def test_seed_is_stale(self):
        f = GlobalMacroFeeder()
        self.assertTrue(f.is_stale("NQ"))
        self.assertEqual(f.get_latest_price("NQ", fallback=-1.0), -1.0)
        self.assertEqual(f.get_latest_price("NQ", allow_stale=True), 20000.0)

    def test_live_then_expire(self):
        f = GlobalMacroFeeder(max_price_age_sec=60)
        f.last_prices["NQ"] = 21000.0
        f._seed_symbols.discard("NQ")
        f.last_timestamps["NQ"] = datetime.now(KST)
        self.assertEqual(f.get_latest_price("NQ"), 21000.0)
        f.last_timestamps["NQ"] = datetime.now(KST) - timedelta(seconds=300)
        self.assertEqual(f.get_latest_price("NQ", fallback=0.0), 0.0)

    async def test_fetch_returns_none_for_seed(self):
        self.assertIsNone(await GlobalMacroFeeder().fetch_latest_price("NQ"))


class TestYahooNoStaleReemit(unittest.IsolatedAsyncioTestCase):
    async def test_old_cache_returns_none(self):
        y = YahooFuturesFeeder(symbols=["NQ"], max_cache_age_sec=60)
        y.last_prices["NQ"] = 1.0
        y.last_timestamps["NQ"] = datetime.now(KST) - timedelta(hours=1)
        self.assertIsNone(y._cached_price("NQ"))

    def test_emit_dedupes_same_ts_price(self):
        got = []
        y = YahooFuturesFeeder(symbols=["NQ"], on_tick_callback=lambda s, p, t: got.append((s, p, t)))
        ts = datetime.now(KST)
        y.last_timestamps["NQ"] = ts
        y._emit_if_new("NQ", 1.0)
        y._emit_if_new("NQ", 1.0)   # 캐시 재방출 -> 무시
        y._emit_if_new("NQ", 2.0)   # 같은 분봉 가격 갱신 -> 허용
        self.assertEqual([g[1] for g in got], [1.0, 2.0])


class TestKisMultiRecord(unittest.TestCase):
    def _rec(self, sym, price):
        f = [sym, "090000", str(price)] + ["0"] * 7 + [str(price + 1), str(price - 1), "5"] + ["0"] * 30
        return f  # 43 fields

    def test_split_records(self):
        recs = self._rec("005930", 100) + self._rec("005930", 101)
        msg = "0|H0STCNT0|002|" + "^".join(recs)
        out = KISParser.split_records(msg)
        self.assertEqual(len(out), 2)
        self.assertTrue(out[1].startswith("0|H0STCNT0|001|005930^090000^101"))
        self.assertEqual(KISParser.split_records("0|H0STCNT0|001|a^b"), ["0|H0STCNT0|001|a^b"])

    def test_each_record_parsed(self):
        recs = self._rec("005930", 100) + self._rec("005930", 101)
        msg = "0|H0STCNT0|002|" + "^".join(recs)
        prices = [KISParser.parse_message_str(m, "005930").price for m in KISParser.split_records(msg)]
        self.assertEqual(prices, [100.0, 101.0])


class TestBackoffAndBinance(unittest.TestCase):
    def test_backoff(self):
        nb = BaseConnector.next_backoff
        self.assertEqual(nb(1.0, 0.1), 2.0)
        self.assertEqual(nb(20.0, 0.1), 30.0)
        self.assertEqual(nb(16.0, 60.0), 1.0)  # 안정 유지 후에만 리셋

    def test_binance_futures_b_a_keys(self):
        d = {"b": [["10", "1"]], "a": [["11", "2"]]}
        ob = BinanceParser.parse_orderbook(d, "BTCUSDT", "FUTURES")
        self.assertEqual(len(ob.units), 1)
        self.assertEqual(ob.units[0].ask_price, 11.0)

    def test_binance_cpp_futures_depth_keys(self):
        import ctrade_core
        for bk, ak in (("bids", "asks"), ("b", "a")):
            msg = json.dumps({"stream": "btcusdt@depth5@100ms",
                              "data": {bk: [["10", "1"]], ak: [["11", "2"]]}})
            ob = ctrade_core.parse_binance_message(msg, "FUTURES")
            self.assertIsNotNone(ob)
            self.assertEqual(len(ob.units), 1)


class TestUpbitUpdateSymbols(unittest.IsolatedAsyncioTestCase):
    async def test_removed_symbol_levels_dropped(self):
        c = UpbitConnector(["KRW-BTC", "KRW-ETH"], asyncio.Queue())
        await c.update_symbols(["KRW-BTC"])
        self.assertNotIn("KRW-ETH", c.required_levels)


class TestRadar(unittest.IsolatedAsyncioTestCase):
    def _radar(self):
        from core.scalping_target_radar import ScalpingTargetRadar
        return ScalpingTargetRadar(MagicMock())

    async def test_exit_rereads_config_under_lock(self):
        import tempfile, os
        r = self._radar()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            stale = {"SCALPING_TARGETS": [{"symbol": "KRW-A", "active": True, "auto_trade": True}]}
            fresh = {"SCALPING_TARGETS": [{"symbol": "KRW-A", "active": True, "auto_trade": True},
                                          {"symbol": "KRW-NEW", "active": True, "auto_trade": True}]}
            with open(path, "w") as f:
                json.dump(fresh, f)  # 평가 이후 다른 경로가 신규 종목을 추가한 상황
            await r.check_and_exit_targets(
                stale, path, [{"symbol": "KRW-A", "rvol": 0.0, "vbp": 0.0, "tps": 0.0}], {}, 100, 100, 100)
            with open(path) as f:
                out = json.load(f)
            syms = {t["symbol"]: t for t in out["SCALPING_TARGETS"]}
            self.assertIn("KRW-NEW", syms)            # 유실되지 않음
            self.assertFalse(syms["KRW-A"]["active"])  # 이탈은 반영됨


if __name__ == "__main__":
    unittest.main()
