"""
Unit tests for CTrade ModelHealthMonitor
AI 모델 건강 상태(Model Health / Drift) 판정 및 텔레그램 알림 쿨다운 검증
"""
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from core.model_health_monitor import ModelHealthMonitor, ModelHealthStatus


class TestModelHealthMonitor(unittest.TestCase):

    def setUp(self):
        # 테스트 격리를 위해 인스턴스 재생성
        self.monitor = ModelHealthMonitor()
        self.symbol = "KRW-BTC"
        self.session_id = "BTC_M10_TEST1"

    def test_healthy_status_initially(self):
        """방금 배포된 모델은 HEALTHY 상태여야 함"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.monitor.register_model(
            symbol=self.symbol,
            session_id=self.session_id,
            deployed_at=now_str,
            interval="10m",
            backtest_win_rate=65.0,
            backtest_mdd=5.0,
        )

        report = self.monitor.evaluate_model_health(self.symbol)
        self.assertEqual(report["status"], ModelHealthStatus.HEALTHY.value)
        self.assertEqual(report["badge_text"], "")
        self.assertEqual(len(report["warning_reasons"]), 0)

    def test_aging_trigger_after_30_days(self):
        """배포일이 30일 경과하면 AGING 상태 및 RE-TRAIN 뱃지 트리거"""
        old_date = (datetime.now() - timedelta(days=32)).strftime("%Y-%m-%d %H:%M:%S")
        self.monitor.register_model(
            symbol=self.symbol,
            session_id=self.session_id,
            deployed_at=old_date,
            interval="10m",
        )

        report = self.monitor.evaluate_model_health(self.symbol)
        self.assertEqual(report["status"], ModelHealthStatus.AGING.value)
        self.assertIn("RE-TRAIN", report["badge_text"])
        self.assertGreaterEqual(report["age_days"], 31.0)
        self.assertTrue(any("30일 초과" in r for r in report["warning_reasons"]))

    def test_consecutive_losses_trigger_decay(self):
        """4연속 손절 발생 시 DECAY 상태 트리거"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.monitor.register_model(
            symbol=self.symbol,
            session_id=self.session_id,
            deployed_at=now_str,
        )

        # 4연속 손절 기록
        for _ in range(4):
            self.monitor.record_trade(self.symbol, is_win=False, net_return=-1.5)

        report = self.monitor.evaluate_model_health(self.symbol)
        self.assertEqual(report["status"], ModelHealthStatus.DECAY.value)
        self.assertEqual(report["consecutive_losses"], 4)
        self.assertTrue(any("4연속 손절" in r for r in report["warning_reasons"]))

    def test_low_win_rate_trigger_decay(self):
        """최근 거래 수가 5회 이상이고 승률이 40% 미만이면 DECAY 트리거"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.monitor.register_model(
            symbol=self.symbol,
            session_id=self.session_id,
            deployed_at=now_str,
        )

        # 1승 5패 (승률 16.7%)
        self.monitor.record_trade(self.symbol, is_win=True, net_return=2.0)
        for _ in range(5):
            self.monitor.record_trade(self.symbol, is_win=False, net_return=-1.0)

        report = self.monitor.evaluate_model_health(self.symbol)
        self.assertEqual(report["status"], ModelHealthStatus.DECAY.value)
        self.assertLess(report["recent_win_rate"], 40.0)
        self.assertTrue(any("미달" in r for r in report["warning_reasons"]))

    def test_critical_mdd_trigger(self):
        """누적 MDD가 백테스트 MDD의 80% 이상 도달 시 CRITICAL 트리거"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.monitor.register_model(
            symbol=self.symbol,
            session_id=self.session_id,
            deployed_at=now_str,
            backtest_mdd=5.0,  # 80% = 4.0%
        )

        report = self.monitor.evaluate_model_health(self.symbol, current_mdd=4.2)
        self.assertEqual(report["status"], ModelHealthStatus.CRITICAL.value)
        self.assertIn("DRIFT", report["badge_text"])
        self.assertTrue(any("낙폭" in r for r in report["warning_reasons"]))

    @patch("utils.telegram_bot.get_bot")
    def test_telegram_alert_and_cooldown(self, mock_get_bot):
        """텔레그램 알림 발송 및 24시간 쿨다운 제어 검증"""
        mock_bot = MagicMock()
        mock_get_bot.return_value = mock_bot

        # 35일 경과 모델 등록 -> 1차 발송 성공해야 함
        old_date = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d %H:%M:%S")
        self.monitor.register_model(
            symbol=self.symbol,
            session_id=self.session_id,
            deployed_at=old_date,
        )

        self.monitor.evaluate_model_health(self.symbol)
        self.assertEqual(mock_bot.send_message.call_count, 1)

        # 바로 다시 호출 시 쿨다운(24h)으로 인해 추가 발송 차단되어야 함
        self.monitor.evaluate_model_health(self.symbol)
        self.assertEqual(mock_bot.send_message.call_count, 1)

        # 하지만 상태가 DECAY(더 심각한 레벨)로 격상되면 쿨다운 무시하고 즉시 재발송되어야 함
        for _ in range(4):
            self.monitor.record_trade(self.symbol, is_win=False, net_return=-1.5)

        self.monitor.evaluate_model_health(self.symbol)
        self.assertEqual(mock_bot.send_message.call_count, 2)


if __name__ == "__main__":
    unittest.main()
