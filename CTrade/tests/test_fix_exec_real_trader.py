"""실전 주문 실행 경로(real_trader/kis_trader) 회귀 테스트: 멱등성, 취소 보호, 반환 계약, 체결 집계, 포지션 합산."""
import asyncio
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.real_trader import UpbitRealTrader, PartialSellResult, DustSyncResult
from core.kis_trader import KISTrader, is_domestic_krx_code


class _Auth:
    def get_headers(self, query=None):
        return {"Authorization": "Bearer x"}


def _resp(status, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=payload or {})
    r.text = str(payload)
    return r


def _client_cm(post=None, get=None):
    client = MagicMock()
    client.post = post or AsyncMock()
    client.get = get or AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


def _trader():
    return UpbitRealTrader(_Auth(), silent=True)


class TestIdempotentOrder(unittest.IsolatedAsyncioTestCase):
    async def test_post_timeout_then_lookup_finds_order(self):
        t = _trader()
        cm, client = _client_cm(
            post=AsyncMock(side_effect=asyncio.TimeoutError("timeout")),
            get=AsyncMock(return_value=_resp(200, {"uuid": "abc-uuid"})),
        )
        with patch("core.real_trader.httpx.AsyncClient", return_value=cm):
            uuid = await t.buy_market("KRW-BTC", 100000)
        self.assertEqual(uuid, "abc-uuid")
        # identifier 가 주문 본문과 조회에 동일하게 사용되어야 한다
        sent = client.post.call_args.kwargs["json"]
        self.assertTrue(sent["identifier"].startswith("ctrade-bid-"))
        self.assertEqual(client.get.call_args.kwargs["params"]["identifier"], sent["identifier"])

    async def test_post_timeout_and_not_found_is_failure(self):
        t = _trader()
        cm, _ = _client_cm(
            post=AsyncMock(side_effect=asyncio.TimeoutError("timeout")),
            get=AsyncMock(return_value=_resp(404, {})),
        )
        with patch("core.real_trader.httpx.AsyncClient", return_value=cm), \
                patch("core.real_trader.asyncio.sleep", new=AsyncMock()):
            self.assertIsNone(await t.sell_market("KRW-BTC", 0.5))

    async def test_http_4xx_is_definite_failure_no_lookup(self):
        t = _trader()
        cm, client = _client_cm(post=AsyncMock(return_value=_resp(400, {"error": "x"})))
        with patch("core.real_trader.httpx.AsyncClient", return_value=cm):
            self.assertIsNone(await t.buy_market("KRW-BTC", 100000))
        client.get.assert_not_called()


class TestFetchOrderDetails(unittest.IsolatedAsyncioTestCase):
    async def test_sums_multiple_trades_and_fee(self):
        t = _trader()
        data = {"state": "done", "paid_fee": "25.5", "trades": [
            {"volume": "0.1", "price": "100"}, {"volume": "0.3", "price": "200"}]}
        cm, _ = _client_cm(get=AsyncMock(return_value=_resp(200, data)))
        with patch("core.real_trader.httpx.AsyncClient", return_value=cm):
            d = await t.fetch_order_details("u")
        self.assertAlmostEqual(d["executed_volume"], 0.4)
        self.assertAlmostEqual(d["avg_price"], (10 + 60) / 0.4)
        self.assertAlmostEqual(d["paid_fee"], 25.5)

    async def test_waits_until_done_instead_of_returning_first_partial_trade(self):
        t = _trader()
        wait = {"state": "wait", "trades": [{"volume": "0.1", "price": "100"}]}
        done = {"state": "done", "trades": [{"volume": "0.1", "price": "100"}, {"volume": "0.2", "price": "100"}]}
        cm, _ = _client_cm(get=AsyncMock(side_effect=[_resp(200, wait), _resp(200, done)]))
        with patch("core.real_trader.httpx.AsyncClient", return_value=cm), \
                patch("core.real_trader.asyncio.sleep", new=AsyncMock()):
            d = await t.fetch_order_details("u")
        self.assertAlmostEqual(d["executed_volume"], 0.3)
        self.assertEqual(d["state"], "done")

    async def test_timeout_returns_unknown_not_full_fill(self):
        t = _trader()
        cm, _ = _client_cm(get=AsyncMock(return_value=_resp(500, {})))
        with patch("core.real_trader.httpx.AsyncClient", return_value=cm), \
                patch("core.real_trader.asyncio.sleep", new=AsyncMock()):
            d = await t.fetch_order_details("u")
        self.assertEqual(d["state"], "unknown")
        self.assertEqual(d["executed_volume"], 0.0)


class TestBuyBehaviors(unittest.IsolatedAsyncioTestCase):
    def _patch_buy(self, t, delay=0.0, details=None):
        async def buy_market(symbol, amount):
            await asyncio.sleep(delay)
            return "u1"
        t.buy_market = buy_market
        t.fetch_order_details = AsyncMock(return_value=details or {"avg_price": 100.0, "executed_volume": 10.0, "state": "done", "paid_fee": 0.0})
        t.get_upbit_krw_balance = AsyncMock(return_value=1.0)

    async def test_cancel_after_submit_still_registers_position(self):
        t = _trader()
        self._patch_buy(t, delay=0.1)
        task = asyncio.create_task(t.buy("KRW-BTC", 100.0, 1000.0))
        await asyncio.sleep(0.02)  # 주문 POST 진행 중
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        pos = t.positions.get("KRW-BTC")
        self.assertIsNotNone(pos, "주문 접수 후 취소되어도 포지션이 등록되어야 한다(고아 포지션 방지)")
        self.assertAlmostEqual(pos["qty"], 10.0)

    async def test_cancel_before_submit_places_no_order(self):
        t = _trader()
        self._patch_buy(t)
        t.buy_market = AsyncMock(return_value="u1")
        task = asyncio.create_task(t.buy("KRW-BTC", 100.0, 1000.0))
        task.cancel()  # 태스크가 시작되기 전에 취소
        with self.assertRaises(asyncio.CancelledError):
            await task
        t.buy_market.assert_not_called()
        self.assertNotIn("KRW-BTC", t.positions)

    async def test_unknown_fill_uses_fee_adjusted_quantity(self):
        t = _trader()
        self._patch_buy(t, details={"avg_price": 0.0, "executed_volume": 0.0, "state": "unknown", "paid_fee": 0.0})
        self.assertTrue(await t.buy("KRW-BTC", 100.0, 10000.0))
        expected = 10000.0 * (1 - t.fee_rate) / 100.0
        self.assertAlmostEqual(t.positions["KRW-BTC"]["qty"], expected)

    async def test_second_buy_merges_average_and_quantity(self):
        t = _trader()
        self._patch_buy(t, details={"avg_price": 100.0, "executed_volume": 10.0, "state": "done", "paid_fee": 0.0})
        await t.buy("KRW-BTC", 100.0, 1000.0)
        first_time = t.positions["KRW-BTC"]["time"]
        self._patch_buy(t, details={"avg_price": 200.0, "executed_volume": 10.0, "state": "done", "paid_fee": 0.0})
        await t.buy("KRW-BTC", 200.0, 2000.0)
        pos = t.positions["KRW-BTC"]
        self.assertAlmostEqual(pos["qty"], 20.0)
        self.assertAlmostEqual(pos["price"], 150.0)
        self.assertAlmostEqual(pos["time"], first_time)


class TestSellContract(unittest.IsolatedAsyncioTestCase):
    def _setup(self, local_qty, upbit_qty):
        t = _trader()
        t.force_set_position("KRW-BTC", 100_000.0, local_qty, time.time(), 100_000.0, False)
        t.get_upbit_balance = AsyncMock(return_value=upbit_qty)
        t.sell_market = AsyncMock(return_value="s1")
        t.get_upbit_krw_balance = AsyncMock(return_value=0.0)
        return t

    async def test_partial_fill_returns_partial_result_and_keeps_position(self):
        t = self._setup(1.0, 1.0)
        t.fetch_order_details = AsyncMock(return_value={"avg_price": 110_000.0, "executed_volume": 0.4, "state": "done"})
        res = await t.sell("KRW-BTC", 110_000.0)
        self.assertIsInstance(res, PartialSellResult)
        self.assertTrue(res.partial)
        self.assertIsNot(res, False)
        self.assertAlmostEqual(t.positions["KRW-BTC"]["qty"], 0.6)
        self.assertGreater(float(res), 0)  # 체결분 손익(원), 퍼센트가 아님
        self.assertAlmostEqual(res.remaining_qty, 0.6)

    async def test_dust_returns_dust_sync_not_true(self):
        t = self._setup(1.0, 0.0)
        res = await t.sell("KRW-BTC", 110_000.0)
        self.assertIsInstance(res, DustSyncResult)
        self.assertIsNot(res, True)
        self.assertTrue(res.dust_sync)
        self.assertEqual(float(res), 0.0)
        self.assertNotIn("KRW-BTC", t.positions)

    async def test_full_exit_returns_plain_float_profit(self):
        t = self._setup(1.0, 1.0)
        t.fetch_order_details = AsyncMock(return_value={"avg_price": 110_000.0, "executed_volume": 1.0, "state": "done"})
        res = await t.sell("KRW-BTC", 110_000.0)
        self.assertNotIsInstance(res, (PartialSellResult, DustSyncResult))
        self.assertGreater(res, 0)
        self.assertNotIn("KRW-BTC", t.positions)

    async def test_unknown_fill_with_unchanged_balance_keeps_position(self):
        t = self._setup(1.0, 1.0)
        t.fetch_order_details = AsyncMock(return_value={"avg_price": 0.0, "executed_volume": 0.0, "state": "unknown"})
        t.get_upbit_balance = AsyncMock(side_effect=[1.0, 1.0])
        self.assertIs(await t.sell("KRW-BTC", 110_000.0), False)
        self.assertIn("KRW-BTC", t.positions)

    async def test_unknown_fill_and_balance_unavailable_keeps_position(self):
        t = self._setup(1.0, 1.0)
        t.fetch_order_details = AsyncMock(return_value={"avg_price": 0.0, "executed_volume": 0.0, "state": "unknown"})
        t.get_upbit_balance = AsyncMock(side_effect=[1.0, None])
        self.assertIs(await t.sell("KRW-BTC", 110_000.0), False)
        self.assertIn("KRW-BTC", t.positions)


class TestKisTrader(unittest.IsolatedAsyncioTestCase):
    def test_is_overseas_handles_alphanumeric_krx_codes(self):
        self.assertTrue(is_domestic_krx_code("005930"))
        self.assertTrue(is_domestic_krx_code("0015N0"))
        self.assertFalse(is_domestic_krx_code("AAPL"))
        self.assertFalse(is_domestic_krx_code("DNASAAPL"))
        self.assertFalse(is_domestic_krx_code("ABCDEF"))

    async def test_second_buy_merges_position(self):
        auth = MagicMock()
        auth.url_base = "http://x"
        auth.get_access_token = AsyncMock()
        k = KISTrader(auth)
        k._save_history = lambda: None
        k._buy_domestic = AsyncMock(return_value={"success": True})
        await k.buy("005930", 100.0, 1000.0)   # 10주
        t0 = k.positions["005930"]["time"]
        await k.buy("005930", 200.0, 2000.0)   # 10주
        pos = k.positions["005930"]
        self.assertAlmostEqual(pos["qty"], 20.0)
        self.assertAlmostEqual(pos["price"], 150.0)
        self.assertEqual(pos["time"], t0)
        self.assertFalse(k._is_overseas("0015N0"))


if __name__ == "__main__":
    unittest.main()
