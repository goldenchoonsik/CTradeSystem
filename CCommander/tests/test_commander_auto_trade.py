"""
Unit Test: Verify CCommander /auto Command and TradeManager AutoTrade Control
"""
import unittest
from unittest.mock import patch, MagicMock
from CCommander.core.trade_mgr import TradeManager
from CCommander.main import CommanderBot


class TestCommanderAutoTrade(unittest.TestCase):
    def test_get_targets_auto_trade_status_fallback(self):
        """HTTP API 실패 시 trading_config.json fallback 정상 로드 검증"""
        targets = TradeManager.get_targets_auto_trade_status()
        self.assertIsInstance(targets, list)
        if targets:
            first = targets[0]
            self.assertIn("symbol", first)

    @patch("requests.post")
    def test_set_symbol_auto_trade_api_success(self, mock_post):
        """API 호출 성공 시 정상 리턴 검증"""
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "status": "success",
            "symbol": "KRW-BTC",
            "auto_trade": False,
            "message": "KRW-BTC auto_trade is now OFF"
        }
        mock_post.return_value = mock_resp

        ok, msg, final_val = TradeManager.set_symbol_auto_trade("KRW-BTC", False)
        self.assertTrue(ok)
        self.assertFalse(final_val)
        self.assertIn("KRW-BTC", msg)

    @patch.object(CommanderBot, "send_message")
    @patch.object(TradeManager, "get_targets_auto_trade_status")
    def test_handle_auto_status_list(self, mock_status, mock_send):
        """인자 없는 /auto 호출 시 현황 목록 출력 검증"""
        mock_status.return_value = [
            {"symbol": "KRW-BTC", "auto_trade": True, "active": True, "strategy_name": "ai_baker"},
            {"symbol": "069500", "auto_trade": False, "active": True, "strategy_name": "ai_baker_069500"}
        ]
        bot = CommanderBot.__new__(CommanderBot)
        bot.handle_auto([])
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][0]
        self.assertIn("KRW-BTC", sent_text)
        self.assertIn("069500", sent_text)

    @patch.object(CommanderBot, "send_message")
    @patch.object(TradeManager, "set_symbol_auto_trade")
    def test_handle_auto_toggle_command(self, mock_set, mock_send):
        """/auto KRW-BTC off 호출 시 정상 제어 및 메시지 전송 검증"""
        mock_set.return_value = (True, "성공", False)
        bot = CommanderBot.__new__(CommanderBot)
        bot.handle_auto(["KRW-BTC", "off"])
        mock_set.assert_called_once_with("KRW-BTC", False)
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][0]
        self.assertIn("KRW-BTC", sent_text)


if __name__ == "__main__":
    unittest.main()
