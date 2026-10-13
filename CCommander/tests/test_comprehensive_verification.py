"""
Comprehensive Verification Script for CCommander & CTrade Telegram Integration
"""
import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

# 경로 설정
_CMD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_CMD_DIR)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "CCommander"))
sys.path.insert(0, os.path.join(_ROOT, "CTrade"))

from CCommander.core.trade_mgr import TradeManager, _fmt_krw, _fmt_pct, _fmt_qty
from CCommander.main import CommanderBot
from CTrade.utils.telegram_bot import TelegramBot, _get_symbol_name, _fmt_qty as ctrade_fmt_qty
from CTrade.core.kis_trader import KISTrader


class TestComprehensiveIntegration(unittest.TestCase):
    
    # ── 1. 포맷팅 및 심볼 매핑 검증 ──────────────────────────────────────────
    def test_01_formatting_and_symbols(self):
        print("\n[TEST 1] 포맷팅 및 심볼 매핑 검증...")
        self.assertEqual(_fmt_krw(35450), "35,450원")
        self.assertEqual(_fmt_krw(-2500), "-2,500원")
        self.assertEqual(_fmt_pct(0.56), "+0.56%")
        self.assertEqual(_fmt_pct(-1.25), "-1.25%")
        self.assertEqual(_fmt_qty(141.0), "141주")
        self.assertEqual(_fmt_qty(0.005467), "0.005467")
        
        # 심볼 매핑
        sym_name_etf = TradeManager.get_symbol_display_name("069500")
        self.assertIn("KODEX 200", sym_name_etf)
        self.assertIn("069500", sym_name_etf)
        
        sym_name_btc = _get_symbol_name("KRW-BTC")
        self.assertIn("KRW-BTC", sym_name_btc)
        print("  ✓ 포맷팅 및 심볼명 매핑 정상 통과")

    # ── 2. TradeManager 예외 및 다양한 상태 처리 검증 ────────────────────────
    def test_02_trade_manager_scenarios(self):
        print("\n[TEST 2] TradeManager 시나리오별 리포트 검증...")
        
        # 2-1. 빈 내역 처리
        with patch.object(TradeManager, "load_trade_history", return_value=[]):
            empty_bal = TradeManager.get_balance_report()
            self.assertIn("내역이 없습니다", empty_bal)
            empty_trades = TradeManager.get_trades_report()
            self.assertIn("존재하지 않습니다", empty_trades)
            empty_quick = TradeManager.get_quick_summary()
            self.assertIn("내역 없음", empty_quick)
            print("  ✓ 빈 내역(Empty History) 방어 로직 정상 통과")

        # 2-2. 가상/실전 복합 체결 내역 처리 (BUY -> SELL)
        mock_data = [
            {
                "type": "BUY",
                "symbol": "069500",
                "price": 35000.0,
                "qty": 100.0,
                "fee": 50.0,
                "amount": 3500000.0,
                "time": 1700000000,
                "balance": 96500000.0
            },
            {
                "type": "SELL",
                "symbol": "069500",
                "price": 35500.0,
                "buy_price": 35000.0,
                "sell_price": 35500.0,
                "qty": 100.0,
                "fee": 50.0,
                "profit": 49900.0,
                "profit_rate": 1.42,
                "reason": "AI_TP",
                "time": 1700001000,
                "balance": 100049900.0
            },
            {
                "type": "BUY",
                "symbol": "122630",
                "price": 20000.0,
                "qty": 50.0,
                "fee": 30.0,
                "amount": 1000000.0,
                "time": 1700002000,
                "balance": 99049900.0
            }
        ]
        with patch.object(TradeManager, "load_trade_history", return_value=mock_data):
            bal_rep = TradeManager.get_balance_report()
            self.assertIn("99,049,900원", bal_rep)  # 운용 잔고
            self.assertIn("49,900원", bal_rep)     # 누적 실현손익
            self.assertIn("100.0%", bal_rep)       # 승률 (1승 0패)
            self.assertIn("KODEX 레버리지", bal_rep) # 미청산 보유 종목(122630)
            self.assertIn("50주", bal_rep)

            trades_rep = TradeManager.get_trades_report(limit=5)
            self.assertIn("AI_TP", trades_rep)
            self.assertIn("KODEX 200", trades_rep)
            self.assertIn("+1.42%", trades_rep)
            print("  ✓ 복합 매매 내역 및 미청산 포지션 계산 정상 통과")

    # ── 3. CommanderBot 명령어 Dispatch 및 응답 검증 ────────────────────────
    def test_03_commander_bot_dispatch(self):
        print("\n[TEST 3] CCommander 명령어 Dispatch 및 응답 검증...")
        with patch("CCommander.main.config") as mock_config:
            mock_config.bot_token = "TEST_TOKEN"
            mock_config.chat_id = "123456"
            mock_config.is_valid = True
            mock_config.managed_services = ["ctrade"]
            mock_config.primary_service = "ctrade"
            mock_config.token_file = "token_info.json"

            bot = CommanderBot.__new__(CommanderBot)
            bot.token = "TEST_TOKEN"
            bot.chat_id = "123456"
            bot.base_url = "https://api.telegram.org/botTEST_TOKEN"
            bot.reboot_confirm_time = 0
            sent_messages = []
            bot.send_message = MagicMock(side_effect=lambda text: sent_messages.append(text))

            # /help
            bot.dispatch("/help")
            self.assertIn("/balance", sent_messages[-1])
            self.assertIn("/trades", sent_messages[-1])

            # /balance
            bot.dispatch("/balance")
            self.assertIn("잔고 및 누적 성과", sent_messages[-1])

            # /trades 3
            bot.dispatch("/trades 3")
            self.assertIn("최근 체결 내역", sent_messages[-1])

            # /status
            with patch("CCommander.core.system_mgr.SystemManager.get_system_metrics", return_value={
                "cpu_percent": 12.5, "ram_percent": 35.0, "ram_used_gb": 1.4, "ram_total_gb": 4.0,
                "disk_percent": 25.0, "disk_used_gb": 10.0, "disk_total_gb": 40.0,
                "uptime_str": "3일 5시간", "now_str": "2026-08-27 09:55:00"
            }), patch("CCommander.core.system_mgr.SystemManager.is_service_active", return_value=True), \
               patch("CCommander.core.token_mgr.TokenManager.get_token_status", return_value={
                "msg": "🟢 정상", "expired_at": "2026-08-28 00:00:00", "remaining_str": "14시간"
            }):
                bot.dispatch("/status")
                self.assertIn("종합 관제 현황", sent_messages[-1])
                self.assertIn("CTrade 트레이딩 요약", sent_messages[-1])
                self.assertIn("ACTIVE", sent_messages[-1])

            # 알 수 없는 명령어
            bot.dispatch("/unknown_command")
            self.assertIn("알 수 없는 명령어", sent_messages[-1])
            print("  ✓ CCommander Dispatch 전체 명령어 라우팅 정상 통과")

    # ── 4. CTrade 실시간 체결 알림 포맷 렌더링 검증 ─────────────────────────
    def test_04_telegram_notification_rendering(self):
        print("\n[TEST 4] CTrade 실시간 체결 알림 렌더링 검증...")
        bot = TelegramBot.__new__(TelegramBot)
        bot._enabled = True
        bot.token = "TEST_TOKEN"
        bot.chat_id = "123456"
        bot._base = "https://api.telegram.org/botTEST_TOKEN"
        
        sent_notifications = []
        bot.send_message = MagicMock(side_effect=lambda text: sent_notifications.append(text))

        # 4-1. 주식/ETF 매수 알림
        bot.notify_buy("069500", 35450.0, 141.0, 4998450.0, extra={"fee": 0.0})
        buy_msg = sent_notifications[-1]
        self.assertIn("🟢 <b>[BUY] KODEX 200 (069500)</b>", buy_msg)
        self.assertIn("35,450원", buy_msg)
        self.assertIn("141주", buy_msg)
        self.assertIn("4,998,450원", buy_msg)

        # 4-2. 주식/ETF 익절 매도 알림
        bot.notify_sell("069500", 35650.0, 28200.0, 0.56, reason="AI_TP", extra={
            "buy_price": 35450.0, "sell_price": 35650.0, "qty": 141.0, "fee": 0.0
        })
        sell_msg = sent_notifications[-1]
        self.assertIn("🔴 <b>[SELL] KODEX 200 (069500)</b>", sell_msg)
        self.assertIn("📈", sell_msg)
        self.assertIn("+28,200원 (+0.56%)", sell_msg)
        self.assertIn("AI_TP", sell_msg)

        # 4-3. 주식/ETF 손절 매도 알림
        bot.notify_sell("069500", 35100.0, -49350.0, -0.99, reason="AI_SL", extra={
            "buy_price": 35450.0, "sell_price": 35100.0, "qty": 141.0, "fee": 0.0
        })
        loss_msg = sent_notifications[-1]
        self.assertIn("📉", loss_msg)
        self.assertIn("-49,350원 (-0.99%)", loss_msg)
        self.assertIn("AI_SL", loss_msg)
        print("  ✓ BUY / SELL 실시간 알림 포맷 렌더링 정상 통과")

    # ── 5. KISTrader 체결 시 알림 및 이력 저장 연동 검증 ────────────────────
    def test_05_kis_trader_notification_pipeline(self):
        print("\n[TEST 5] KISTrader 체결 파이프라인(알림 + 저장) 검증...")
        from unittest.mock import AsyncMock
        
        mock_auth = MagicMock()
        mock_auth.access_token = "MOCK_TOKEN"
        mock_auth.get_access_token = AsyncMock()
        mock_auth.app_key = "MOCK_KEY"
        mock_auth.app_secret = "MOCK_SECRET"
        mock_auth.acc_no = "12345678-01"
        mock_auth.acc_code = "01"
        mock_auth.url_base = "https://mock.koreainvestment.com"
        
        trader = KISTrader(mock_auth, is_simulation=True, initial_balance=10000000.0)
        trader.history_file = "/tmp/test_trade_history.json"
        trader.history = []
        trader.positions = {}
        
        # 모의 텔레그램 봇 주입
        mock_bot = MagicMock()
        with patch("utils.telegram_bot.get_bot", return_value=mock_bot):
            # 매수 모의 체결
            with patch.object(trader, "_buy_domestic", return_value={"success": True, "odno": "001"}):
                import asyncio
                success = asyncio.run(trader.buy("069500", 35000.0, 3500000.0))
                self.assertTrue(success)
                self.assertIn("069500", trader.positions)
                self.assertEqual(len(trader.history), 1)
                self.assertEqual(trader.history[0]["type"], "BUY")
                mock_bot.notify_buy.assert_called_once()
                print("  ✓ KISTrader 매수 체결 시 notify_buy 호출 및 history 저장 정상")

            # 매도 모의 체결
            with patch.object(trader, "_sell_domestic", return_value={"success": True, "odno": "002"}):
                profit = asyncio.run(trader.sell("069500", 35500.0, reason="AI_TP"))
                # L-4: 왕복 수수료를 반영한 순손익으로 검증
                expected_profit = (35500.0 - 35000.0) * 100 - (35000.0 * 100 + 35500.0 * 100) * trader.fee_rate
                self.assertAlmostEqual(profit, expected_profit)
                self.assertEqual(trader.history[1]["fee"], (35000.0 * 100 + 35500.0 * 100) * trader.fee_rate)
                self.assertNotIn("069500", trader.positions)
                self.assertEqual(len(trader.history), 2)
                self.assertEqual(trader.history[1]["type"], "SELL")
                mock_bot.notify_sell.assert_called_once()
                print("  ✓ KISTrader 매도 체결 시 notify_sell 호출 및 history 저장 정상")

        # 테스트용 임시 파일 정리
        if os.path.exists("/tmp/test_trade_history.json"):
            os.remove("/tmp/test_trade_history.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
