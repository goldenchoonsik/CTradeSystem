import unittest
import os
import sys
import json
from unittest.mock import MagicMock, patch

_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

try:
    from CCommander.core.trade_mgr import TradeManager, _fmt_krw, _fmt_pct, _fmt_qty
    from CCommander.main import CommanderBot
except ImportError:
    from core.trade_mgr import TradeManager, _fmt_krw, _fmt_pct, _fmt_qty
    from main import CommanderBot


class TestCCommander(unittest.TestCase):
    def test_format_helpers(self):
        self.assertEqual(_fmt_krw(10000), "10,000원")
        self.assertEqual(_fmt_krw(-500), "-500원")
        self.assertEqual(_fmt_pct(1.234), "+1.23%")
        self.assertEqual(_fmt_pct(-0.456), "-0.46%")
        self.assertEqual(_fmt_qty(141.0), "141주")
        self.assertEqual(_fmt_qty(0.005467), "0.005467")

    def test_trade_manager_reports(self):
        # 1. 잔고 리포트
        balance_rep = TradeManager.get_balance_report()
        self.assertIn("잔고 및 누적 성과", balance_rep)
        self.assertIn("운용 잔고", balance_rep)

        # 2. 체결 내역 리포트
        trades_rep = TradeManager.get_trades_report(limit=3)
        self.assertIn("최근 체결 내역", trades_rep)

        # 3. 요약 리포트
        quick_sum = TradeManager.get_quick_summary()
        self.assertTrue(len(quick_sum) > 0)

        # 4. 실시간 PnL 리포트 (/pnl)
        pnl_rep = TradeManager.get_pnl_report()
        self.assertIn("실시간 실현손익", pnl_rep)
        self.assertIn("시행 횟수", pnl_rep)

    @patch("requests.post")
    def test_commander_trade_control_and_close(self, mock_post):
        # API 모킹
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"status": "success", "message": "성공"}
        mock_post.return_value = mock_resp

        # 1. 매매 제어
        ok, msg = TradeManager.set_trading_active(False)
        self.assertTrue(ok)

        ok, msg = TradeManager.set_trading_active(True)
        self.assertTrue(ok)

        # 2. 종목 청산
        ok, msg = TradeManager.close_position("KRW-BTC")
        self.assertTrue(ok)

        # 3. 전 종목 긴급 청산
        ok, msg = TradeManager.close_all_positions()
        self.assertTrue(ok)

    @patch("requests.post")
    def test_bot_dispatch(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ok": True, "result": []}
        mock_post.return_value = mock_resp

        bot = CommanderBot.__new__(CommanderBot)
        bot.token = "123:ABC"
        bot.chat_id = "999"
        bot.base_url = "https://api.telegram.org/botTEST_TOKEN"
        bot.reboot_confirm_time = 0
        bot.send_message = MagicMock(return_value=True)

        with patch.object(TradeManager, "set_trading_active", return_value=(True, "테스트 성공")), \
             patch.object(TradeManager, "close_position", return_value=(True, "청산 성공")), \
             patch.object(TradeManager, "close_all_positions", return_value=(True, "전량 청산 성공")):
            
            bot.dispatch("/help")
            bot.dispatch("/status")
            bot.dispatch("/pnl")
            bot.dispatch("/balance")
            bot.dispatch("/trades 2")
            bot.dispatch("/pause")
            bot.dispatch("/resume")
            bot.dispatch("/trade off")
            bot.dispatch("/close KRW-BTC")
            bot.dispatch("/closeall")
            bot.dispatch("/resetbase")
            bot.dispatch("/setbase")
            bot.dispatch("/base upbit")

    def test_calculate_trade_metrics_and_duration(self):
        from CCommander.core.trade_mgr import _fmt_duration
        self.assertEqual(_fmt_duration(0), "즉시")
        self.assertEqual(_fmt_duration(45), "45초")
        self.assertEqual(_fmt_duration(1799), "29분 59초")
        self.assertEqual(_fmt_duration(3665), "1시간 1분 5초")

        sample_history = [
            {"type": "BUY", "symbol": "KRW-BTC", "price": 100000000.0, "qty": 0.0001, "amount": 10000.0, "fee": 5.0, "time": 1788705600.0},
            {"type": "SELL", "symbol": "KRW-BTC", "price": 100500000.0, "buy_price": 100000000.0, "sell_price": 100500000.0, "qty": 0.0001, "profit": 40.0, "profit_rate": 0.40, "fee": 5.0, "time": 1788706000.0, "balance": 10000040.0},
            {"type": "BUY", "symbol": "KRW-BTC", "price": 100000000.0, "qty": 0.0001, "amount": 10000.0, "fee": 5.0, "time": 1788707000.0},
            {"type": "SELL", "symbol": "KRW-BTC", "price": 99500000.0, "buy_price": 100000000.0, "sell_price": 99500000.0, "qty": 0.0001, "profit": -60.0, "profit_rate": -0.60, "fee": 5.0, "time": 1788708000.0, "balance": 9999980.0},
        ]

        m = TradeManager.calculate_trade_metrics(sample_history, initial_balance=10000000.0)
        self.assertEqual(m["total_trades"], 2)
        self.assertEqual(m["win_count"], 1)
        self.assertEqual(m["lose_count"], 1)
        self.assertEqual(m["win_rate"], 50.0)
        self.assertEqual(m["total_profit"], -20.0)
        self.assertAlmostEqual(m["profit_factor"], 40.0 / 60.0, places=2)
        self.assertAlmostEqual(m["summed_trade_roi"], -0.20, places=2)
        self.assertAlmostEqual(m["account_roi"], -0.0002, places=4)


if __name__ == "__main__":
    unittest.main()
