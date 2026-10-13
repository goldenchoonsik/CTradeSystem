"""실전 잔고 조회 실패가 포지션 청산/삭제로 이어지지 않는지 검증 (리뷰 지적 사항 회귀 테스트)."""
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.real_trader import UpbitRealTrader
from utils.portfolio_manager import PortfolioManager


class TestBalanceFetchFailureIsNotZeroBalance(unittest.IsolatedAsyncioTestCase):
    async def test_get_upbit_balance_returns_none_without_auth(self):
        trader = UpbitRealTrader(upbit_auth=None)
        self.assertIsNone(await trader.get_upbit_balance("KRW-BTC"))

    async def test_sell_keeps_local_position_when_balance_lookup_fails(self):
        trader = UpbitRealTrader(upbit_auth=None)
        trader.force_set_position("KRW-BTC", 100_000_000.0, 1.0, time.time(), 100_000_000.0, False)
        trader.get_upbit_balance = AsyncMock(return_value=None)  # 429/타임아웃 등 조회 실패
        trader.sell_market = AsyncMock()

        res = await trader.sell("KRW-BTC", 95_000_000.0, reason="STOP_LOSS")

        self.assertIs(res, False)
        self.assertIn("KRW-BTC", trader.positions, "조회 실패 시 로컬 포지션을 지우면 손절이 무력화된다")
        trader.sell_market.assert_not_called()

    async def test_fetch_upbit_balance_returns_none_on_http_error(self):
        pm = PortfolioManager(kis_auth=None, upbit_auth=MagicMock())
        resp = MagicMock(status_code=429, text="too many requests")
        client = MagicMock()
        client.get = AsyncMock(return_value=resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        with patch("utils.portfolio_manager.httpx.AsyncClient", return_value=cm):
            self.assertIsNone(await pm.fetch_upbit_balance())

    async def test_fetch_upbit_balance_returns_none_without_auth(self):
        pm = PortfolioManager(kis_auth=None, upbit_auth=None)
        self.assertIsNone(await pm.fetch_upbit_balance())


if __name__ == "__main__":
    unittest.main()
