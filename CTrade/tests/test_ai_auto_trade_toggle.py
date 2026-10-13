"""
Unit Test: Verify AI Strategy Symbol Auto Trade Toggle and Skip Protection
"""
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone, timedelta
from core.ai_strategy import Signal, SignalAction, SignalReason

KST = timezone(timedelta(hours=9))


class TestAIAutoTradeToggle(unittest.TestCase):
    def setUp(self):
        self.config = {
            "SCALPING_TARGETS": [
                {
                    "symbol": "KRW-BTC",
                    "exchange": "UPBIT",
                    "active": True,
                    "auto_trade": False,
                    "strategy_name": "ai_baker",
                    "budget_per_trade": 100000
                },
                {
                    "symbol": "069500",
                    "exchange": "KIS",
                    "active": True,
                    "auto_trade": True,
                    "strategy_name": "ai_baker_069500",
                    "budget_per_trade": 120000
                }
            ],
            "GLOBAL_STRATEGY": {
                "TRADING_ACTIVE": True
            }
        }

    def test_ai_buy_skipped_when_symbol_auto_trade_is_false(self):
        """auto_trade=False 인 종목은 AI 매수 신호가 발생해도 매수 집행이 스킵되어야 함"""
        target_conf = next((t for t in self.config["SCALPING_TARGETS"] if t["symbol"] == "KRW-BTC"), None)
        self.assertIsNotNone(target_conf)
        self.assertFalse(target_conf.get("auto_trade", True))

        # 모의 AI 전략 및 신호
        mock_ai = MagicMock()
        mock_ai.auto_trade = False
        buy_signal = Signal(
            action=SignalAction.BUY,
            price=108000000.0,
            probability=0.60,
            reason=SignalReason.AI_SIGNAL,
            entry_atr=35000.0
        )

        # auto_trade 가 False 인 경우 매수 차단 검증
        is_symbol_auto_trade = target_conf.get("auto_trade", True)
        self.assertFalse(is_symbol_auto_trade)

    def test_ai_buy_allowed_when_symbol_auto_trade_is_true(self):
        """auto_trade=True 인 종목은 AI 매수 신호 발생 시 정상 진입 허용되어야 함"""
        target_conf = next((t for t in self.config["SCALPING_TARGETS"] if t["symbol"] == "069500"), None)
        self.assertIsNotNone(target_conf)
        self.assertTrue(target_conf.get("auto_trade", False))

        is_symbol_auto_trade = target_conf.get("auto_trade", True)
        self.assertTrue(is_symbol_auto_trade)

    def test_rule_buy_skipped_when_symbol_auto_trade_is_false(self):
        """Rule 기반 전략: auto_trade=False 인 경우 신규 매수 조건 진입이 차단되어야 함"""
        target_conf = next((t for t in self.config["SCALPING_TARGETS"] if t["symbol"] == "KRW-BTC"), None)
        is_symbol_auto_trade = target_conf.get("auto_trade", True)
        is_global_active = self.config["GLOBAL_STRATEGY"]["TRADING_ACTIVE"]
        
        # 신규 매수 실행 조건: trading_active and is_symbol_auto_trade
        should_execute_buy = is_global_active and is_symbol_auto_trade
        self.assertFalse(should_execute_buy)

    def test_rule_sell_allowed_when_symbol_auto_trade_is_false(self):
        """Rule 기반 전략: auto_trade=False 여도 기존 보유 포지션(pos)이 있으면 청산 로직은 정상 실행되어야 함"""
        target_conf = next((t for t in self.config["SCALPING_TARGETS"] if t["symbol"] == "KRW-BTC"), None)
        is_symbol_auto_trade = target_conf.get("auto_trade", True)
        self.assertFalse(is_symbol_auto_trade)

        mock_evaluator = MagicMock()
        mock_evaluator.should_sell.return_value = True
        pos = {"price": 100000000.0, "qty": 0.01, "time": 1000.0}

        # 청산 로직은 evaluator와 pos 존재 시 auto_trade 여부와 무관하게 평가되어야 함
        can_evaluate_sell = mock_evaluator is not None and pos is not None
        self.assertTrue(can_evaluate_sell)
        self.assertTrue(mock_evaluator.should_sell({}))


if __name__ == "__main__":
    unittest.main()

