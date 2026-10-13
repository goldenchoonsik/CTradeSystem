"""회귀 테스트: ai_strategy 웜업/리스크 슬롯, optimizer, model_health_monitor, auto_analyser"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from core.ai_strategy import AIStrategyModule
from core.optimizer import StrategyOptimizer, ShadowTrader
from core.model_health_monitor import ModelHealthMonitor
from core.risk_manager import RiskManager


def _bare(interval_minutes, feature_params):
    with patch.object(AIStrategyModule, '__init__', lambda self, *a, **k: None):
        s = AIStrategyModule("x")
    s.interval_minutes = interval_minutes
    s.feature_params = feature_params
    return s


class TestWarmup(unittest.TestCase):
    def test_window_mins_and_bars(self):
        s = _bare(10.0, {'a': {'window_mins': 1440}, 'b': {'window_bars': 30}})
        self.assertEqual(s._calc_required_warmup_candles(), 144 + 50)

    def test_window_ge_60_is_minutes(self):
        s = _bare(10.0, {'a': {'window': 120}})  # 120분 = 12봉
        self.assertEqual(s._calc_required_warmup_candles(), 100)
        s2 = _bare(1.0, {'a': {'window': 120}})  # 120봉
        self.assertEqual(s2._calc_required_warmup_candles(), 170)

    def test_period_bars(self):
        s = _bare(1.0, {'a': {'period_bars': 300}})
        self.assertEqual(s._calc_required_warmup_candles(), 350)

    def test_rsi_daily_exceeds_2000_not_capped(self):
        s = _bare(1.0, {'rsi_daily': {'period_days': 14}})  # 14*1440 = 20160
        self.assertEqual(s._calc_required_warmup_candles(), 14 * 1440 + 50)


class TestRiskSlot(unittest.TestCase):
    def _strat(self):
        s = _bare(1.0, {})
        s.symbol = "KRW-BTC"
        s.risk_manager = RiskManager(max_concurrent_positions=3)
        s.has_position = False
        s.is_pending_entry = False
        s.is_pending_exit = False
        s._position_reserved = False
        return s

    def test_cancel_releases_slot_via_helper(self):
        s = self._strat()
        s.risk_manager.on_position_opened()
        s._position_reserved = True
        s.is_pending_entry = True
        s.on_buy_cancelled("x")
        self.assertEqual(s.risk_manager._current_positions, 0)

    def test_restore_counts_position_once(self):
        s = self._strat()
        s.interval_seconds = 60
        s.params = {}
        s.aggregator = MagicMock(total_completed_candles=10)
        s.exec_session = MagicMock()
        s._estimate_restored_atr = lambda *a, **k: 1.0
        s.restore_position(100.0, entry_atr=1.0)
        self.assertEqual(s.risk_manager._current_positions, 1)
        s.restore_position(100.0, entry_atr=1.0)  # 재호출해도 중복 카운트 없음
        self.assertEqual(s.risk_manager._current_positions, 1)


class TestOptimizer(unittest.TestCase):
    def _opt_with(self, base_profit, var_profit, trades=5, wins=4):
        opt = StrategyOptimizer()
        cfg = {"symbol": "S", "exchange": "UPBIT", "buy_conditions": {"logic": "AND", "rules": [
            {"target": "local_imbalance", "operator": ">=", "value": 1.0}]}, "sell_conditions": {}}
        base = MagicMock(variant_id="base", config=cfg)
        base.simulator.symbol_stats = {"S": {"total_profit": base_profit, "trades": trades, "wins": wins}}
        var = MagicMock(variant_id="v", config=cfg)
        var.simulator.symbol_stats = {"S": {"total_profit": var_profit, "trades": trades, "wins": wins}}
        opt.shadow_targets["S"] = [base, var]
        return opt

    def test_negative_base(self):
        # 기존 로직: -100*1.1=-110 < -105 -> 교체(부당). 개선폭 10% 미만이면 교체 금지
        self.assertIsNone(self._opt_with(-100.0, -95.0).check_for_upgrade("S"))
        self.assertIsNotNone(self._opt_with(-100.0, -80.0).check_for_upgrade("S"))

    def test_zero_base_requires_positive(self):
        self.assertIsNone(self._opt_with(0.0, 0.0).check_for_upgrade("S"))
        self.assertIsNotNone(self._opt_with(0.0, 10.0).check_for_upgrade("S"))

    def test_modify_thresholds_nested_and_direction(self):
        opt = StrategyOptimizer()
        cfg = {"buy_conditions": {"logic": "AND", "rules": [
            {"logic": "OR", "rules": [
                {"target": "t", "operator": ">=", "value": 10.0},
                {"target": "t", "operator": "<", "value": 10.0}]}]}}
        opt._modify_thresholds(cfg, "buy_conditions", "t", -5)
        inner = cfg["buy_conditions"]["rules"][0]["rules"]
        self.assertEqual(inner[0]["value"], 5.0)   # '>=' : 장벽 완화 -> 값 감소
        self.assertEqual(inner[1]["value"], 15.0)  # '<'  : 장벽 완화 -> 값 증가


class TestHealthMonitorNotify(unittest.TestCase):
    def test_query_does_not_send(self):
        m = ModelHealthMonitor()
        m.register_model("S", "sess", deployed_at="2000-01-01")  # AGING
        with patch.object(m, "_check_and_notify") as chk:
            m.get_all_reports(notify=False)
            chk.assert_not_called()
            m.get_all_reports()
            chk.assert_called_once()


class TestAutoAnalyser(unittest.TestCase):
    def _run(self, payload):
        import core.auto_analyser as aa
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "config"))
            with open(os.path.join(d, "spike_verification_history.json"), "w") as f:
                json.dump(payload, f)
            with patch.object(aa, "default_project_root", d), \
                 patch.object(sys, "argv", ["x", "--duration-sec", "999999999"]):
                aa.main()
            self.assertTrue(os.path.exists(os.path.join(d, "scripts", "analysis_report.md")))

    def test_none_return_and_dict_json(self):
        import time
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        rec = {"timestamp": ts, "symbol": "KRW-A", "return_30s": None, "success_30s": False,
               "max_high": None, "max_drawdown": None}
        self._run([rec])
        self._run({"a": rec})
        self._run({"records": [rec]})


if __name__ == "__main__":
    unittest.main()
