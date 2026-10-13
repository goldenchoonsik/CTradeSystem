"""회귀 테스트: C++ 룰 엔진/틱 방향/버킷 경계/Backtester"""
import unittest
import ctrade_core


def _cfg(buy=None, sell=None):
    c = {"symbol": "S", "exchange": "UPBIT"}
    if buy is not None:
        c["buy_conditions"] = buy
    if sell is not None:
        c["sell_conditions"] = sell
    return c


class TestRuleEngineCpp(unittest.TestCase):
    def test_empty_rules_false(self):
        ev = ctrade_core.RuleEvaluator(_cfg(buy={"logic": "AND", "rules": []}))
        self.assertFalse(ev.should_buy({"x": 1.0}))
        ev2 = ctrade_core.RuleEvaluator(_cfg(buy={"logic": "AND", "rules": [{"logic": "AND", "rules": []}]}))
        self.assertFalse(ev2.should_buy({"x": 1.0}))

    def test_unknown_operator_false(self):
        ev = ctrade_core.RuleEvaluator(_cfg(buy={"logic": "AND", "rules": [
            {"target": "x", "operator": "~=", "value": 0.0}]}))
        for _ in range(3):
            self.assertFalse(ev.should_buy({"x": 0.0}))

    def test_missing_key_is_false(self):
        ev = ctrade_core.RuleEvaluator(_cfg(buy={"logic": "AND", "rules": [
            {"target": "missing", "operator": "<", "value": 5.0}]}))
        self.assertFalse(ev.should_buy({"x": 1.0}))
        self.assertTrue(ev.should_buy({"missing": 1.0}))

    def test_python_compiler_parity(self):
        from core.rule_engine import RuleCompiler
        f = RuleCompiler.compile_rule({"target": "m", "operator": "<", "value": 5.0})
        self.assertFalse(f({}))
        self.assertTrue(f({"m": 1.0}))
        self.assertFalse(RuleCompiler.compile_conditions({"logic": "AND", "rules": []})({}))


class TestTickSideCpp(unittest.TestCase):
    def test_b_side_recognised_as_buy(self):
        t = ctrade_core.CoreTracker()
        t.register_symbol("S")
        now = 1000.0
        for i in range(20):
            t.add_history_tick("S", now + i * 0.1, 100.0, 1.0, "B", 0.5, 240)
        m = t.get_analysis_metrics("S", 0.0, now + 2.0, 0.9, 14, 0.5, 240)
        self.assertEqual(m["time_intensity"], 999.0)  # 전부 매수 -> 매도량 0

    def test_backtester_buy_ticks_and_vwap(self):
        from types import SimpleNamespace as NS
        cfg = _cfg(buy={"logic": "AND", "rules": [{"target": "local_time_intensity", "operator": ">=", "value": 999.0}]},
                   sell={"logic": "AND", "rules": [{"target": "profit_rate", "operator": ">=", "value": 50.0}]})
        cfg["budget_per_trade"] = 100000.0
        bt = ctrade_core.Backtester(cfg)
        # 모든 틱이 매수(BID): 수정 전에는 "B" 문자열이 매도로 처리되어 매수 조건이 영원히 거짓
        bt.load_ticks([NS(timestamp=1000.0 + i * 0.2, price=100.0, volume=10.0, side="BID") for i in range(200)])
        bt.load_orderbooks([])
        res = bt.run(1.0, 1.0)
        self.assertGreater(res["total_trades"], 0)


class TestTimeBucketBounds(unittest.TestCase):
    def test_long_gap_clears_stale_and_small_cap(self):
        t = ctrade_core.CoreTracker()
        t.register_symbol("S")
        for i in range(400):
            t.add_history_tick("S", 1000.0 + i * 0.5, 100.0, 1.0, "BID", 0.5, 240)
        # 무틱 1시간 후 -> 오래된 매수 버킷이 모두 밀려나야 함 (링이 가득 찬 상태에서도)
        m = t.get_analysis_metrics("S", 0.0, 1000.0 + 200 + 3600.0, 0.9, 14, 0.5, 240)
        self.assertEqual(m["time_intensity"], 100.0)

    def test_max_buckets_over_ring_capacity(self):
        t = ctrade_core.CoreTracker()
        t.register_symbol("S")
        for i in range(1000):
            t.add_history_tick("S", 1000.0 + i * 0.5, 100.0, 1.0, "BID", 0.5, 1000)
        m = t.get_analysis_metrics("S", 0.0, 1500.0, 0.9, 14, 0.5, 1000)
        self.assertIn("time_intensity", m)  # 크래시/범위초과 없음


if __name__ == "__main__":
    unittest.main()
