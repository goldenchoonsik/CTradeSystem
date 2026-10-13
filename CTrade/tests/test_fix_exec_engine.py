"""engine/simulator/shared_state 실행 경로 회귀 테스트."""
import asyncio
import inspect
import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import engine as engine_mod
from core.engine import STradeEngine
from core.real_trader import UpbitRealTrader, PartialSellResult, DustSyncResult
from core.simulator import VirtualTrader
from core.shared_state import SharedState


def _engine(sim_mode, simulator):
    e = object.__new__(STradeEngine)
    e.simulation_mode = sim_mode
    e.simulator = simulator
    e.pending_buy_tasks = {}
    e.ai_strategies = {}
    e.stock_symbols = []
    e.crypto_symbols = []
    e.config = {}
    e.kis_trader = None
    e.add_log = MagicMock()
    e.data_manager = SimpleNamespace(store={})
    return e


class TestExecuteSellContract(unittest.IsolatedAsyncioTestCase):
    def _ai(self):
        ai = MagicMock()
        ai.entry_price = 100.0
        ai.cost = 0.002
        return ai

    async def test_partial_fill_is_not_treated_as_full_exit(self):
        sim = MagicMock()
        sim.sell = AsyncMock(return_value=PartialSellResult(1234.0, 0.5))
        e = _engine(False, sim)
        e.ai_strategies["KRW-BTC"] = self._ai()
        res = await e.execute_sell("KRW-BTC", 110.0, reason="TP")
        self.assertIs(res, False)
        e.ai_strategies["KRW-BTC"].on_sell_filled.assert_not_called()
        e.ai_strategies["KRW-BTC"].on_sell_cancelled.assert_called_once()

    async def test_dust_sync_is_not_recorded_as_win_by_bool_truthiness(self):
        sim = MagicMock()
        sim.sell = AsyncMock(return_value=DustSyncResult())
        e = _engine(False, sim)
        ai = self._ai()
        e.ai_strategies["KRW-BTC"] = ai
        monitor = MagicMock()
        with patch("core.model_health_monitor.ModelHealthMonitor.get_instance", return_value=monitor):
            res = await e.execute_sell("KRW-BTC", 90.0, reason="SL")  # 진입가 100 대비 손실
        self.assertIsNot(res, False)
        ai.on_sell_filled.assert_called_once()
        self.assertFalse(monitor.record_trade.call_args.kwargs["is_win"])


class TestReconcile(unittest.IsolatedAsyncioTestCase):
    async def test_real_mode_ignores_simulation_history_and_synthetic_symbols(self):
        sim = UpbitRealTrader(MagicMock(), silent=True)
        sim.add_history_event({"type": "BUY", "symbol": "KRW-ETH", "price": 1000.0, "qty": 1.0, "time": time.time(),
                               "mode": "SIMULATION", "is_real": False})
        e = _engine(False, sim)
        e.crypto_symbols = ["KRW-BTC", "USDT-BTC", "KRW-USDT"]
        e.config = {"SCALPING_TARGETS": [{"symbol": "KRW-BTC", "exchange": "UPBIT", "active": True}]}
        queried = []

        async def bal(sym):
            queried.append(sym)
            return 0.5
        sim.get_upbit_balance = bal
        e.data_manager = SimpleNamespace(store={"KRW-BTC": {"ticker": SimpleNamespace(price=100_000_000.0)}})
        await e.reconcile_startup_positions(MagicMock())
        self.assertEqual(queried, ["KRW-BTC"], "참조용 심볼(USDT-BTC, KRW-USDT)은 조회/채택하면 안 된다")
        self.assertNotIn("KRW-ETH", sim.positions, "실전 모드에서 SIMULATION 이력은 복원되지 않아야 한다")
        self.assertNotIn("USDT-BTC", sim.positions)
        self.assertNotIn("KRW-USDT", sim.positions)

    async def test_kis_adoption_uses_real_average_price(self):
        sim = UpbitRealTrader(MagicMock(), silent=True)
        e = _engine(False, sim)
        e.stock_symbols = ["005930"]
        e.kis_trader = MagicMock()
        e.kis_trader.get_balance_with_avg_price = AsyncMock(return_value=(10.0, 70000.0))
        e.data_manager = SimpleNamespace(store={"005930": {"ticker": SimpleNamespace(price=75000.0)}})
        await e.reconcile_startup_positions(MagicMock())
        self.assertAlmostEqual(sim.positions["005930"]["price"], 70000.0)

    async def test_restored_position_deducts_simulation_balance(self):
        sim = VirtualTrader(initial_balance=10_000_000.0, silent=True)
        sim.add_history_event({"type": "BUY", "symbol": "KRW-BTC", "price": 1_000_000.0, "qty": 2.0, "time": time.time()})
        e = _engine(True, sim)
        await e.reconcile_startup_positions(MagicMock())
        self.assertIn("KRW-BTC", sim.positions)
        self.assertAlmostEqual(sim.balance, 10_000_000.0 - 2_000_000.0 * (1 + sim.fee_rate))
        stats = sim.get_stats({})
        self.assertAlmostEqual(stats["total_asset"], 10_000_000.0 - 2_000_000.0 * 2 * sim.fee_rate - 0, delta=5000)


class TestResetSimulation(unittest.IsolatedAsyncioTestCase):
    async def test_reset_refused_in_real_mode(self):
        sim = MagicMock()
        e = _engine(False, sim)
        e.last_exit_times = {}
        await e.handle_command(MagicMock(), {"type": "reset_simulation"})
        sim.reset.assert_not_called()

    def test_reset_purges_simulation_events_from_history_file_but_keeps_real(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trade_history.json")
            events = [
                {"type": "BUY", "symbol": "KRW-BTC", "price": 1.0, "qty": 1.0, "time": 1.0, "mode": "SIMULATION", "is_real": False},
                {"type": "BUY", "symbol": "KRW-ETH", "price": 1.0, "qty": 1.0, "time": 2.0},
                {"type": "BUY", "symbol": "KRW-XRP", "price": 1.0, "qty": 1.0, "time": 3.0, "mode": "REAL", "is_real": True},
            ]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(events, f)
            t = VirtualTrader(initial_balance=1_000_000.0, silent=True)
            t.silent = False
            t.history_file = path
            with patch.object(VirtualTrader, "_save_history", lambda self: None):
                t.reset(1_000_000.0)
            with open(path, encoding="utf-8") as f:
                left = json.load(f)
            self.assertEqual([x["symbol"] for x in left], ["KRW-XRP"])


class TestKrxWarmupNoFakeBackfill(unittest.IsolatedAsyncioTestCase):
    async def test_krx_symbol_gets_no_fake_candles_or_price_seed(self):
        e = _engine(True, MagicMock())
        agg = MagicMock()
        agg._last_target_price = None
        ai = MagicMock()
        ai.interval_seconds = 10
        ai.interval_minutes = 10 / 60
        ai.required_warmup_candles = 50
        ai.aggregator = MagicMock()
        ai.aggregator._last_target_price = None
        ai.warmup_status = {"ready": False, "status_text": "x", "required_candles": 50}
        e.ai_strategies = {"069500": ai}
        e.candle_aggregators = {"069500": agg}
        e.yahoo_futures_feeder = MagicMock()
        e.global_macro_feeder = MagicMock()
        await e.fetch_historical_warmup_candles(MagicMock())
        agg.add_backfilled_candle.assert_not_called()
        ai.aggregator.add_backfilled_candle.assert_not_called()
        self.assertIsNone(ai.aggregator._last_target_price)


class TestSharedStateUpdateControl(unittest.TestCase):
    def test_update_control_only_writes_given_keys(self):
        class Recorder(dict):
            def __init__(self, *a):
                super().__init__(*a)
                self.updates = []

            def update(self, other=(), **kw):
                self.updates.append(dict(other))
                super().update(other, **kw)

        ss = object.__new__(SharedState)
        ss.control = Recorder({"a": 1, "b": 2})
        ss.update_control({"b": 3})
        self.assertEqual(ss.control.updates, [{"b": 3}], "전체 dict 를 되쓰지 않고 변경 키만 기록해야 한다")
        self.assertEqual(dict(ss.control), {"a": 1, "b": 3})


class TestEngineStructure(unittest.TestCase):
    """consumer_loop/reload_config 는 통합 실행이 어려워 소스 수준으로 핵심 불변식을 고정한다."""

    def test_rule_trailing_state_is_engine_side_not_position_copy(self):
        src = inspect.getsource(STradeEngine.consumer_loop)
        self.assertNotIn('pos["max_price"] =', src)
        self.assertNotIn('pos["ts_active"] =', src)
        self.assertIn("_rule_pos_state", src)

    def test_per_symbol_exception_isolation(self):
        src = inspect.getsource(STradeEngine.consumer_loop)
        self.assertIn("for symbol in list(self.data_manager.store):", src)
        self.assertIn("except Exception as sym_err", src)
        self.assertIn("stale", src)

    def test_reload_config_updates_ref_mapping_in_place(self):
        src = inspect.getsource(STradeEngine.reload_config)
        self.assertNotIn("self.ref_mapping = {}", src)
        self.assertIn("self.ref_mapping.clear()", src)

    def test_cancelled_buy_syncs_ai_state_when_position_registered(self):
        src = inspect.getsource(STradeEngine.consumer_loop)
        self.assertIn("reg_pos", src)


if __name__ == "__main__":
    unittest.main()


class TestHistoryMetaPreserved(unittest.TestCase):
    def test_real_event_label_survives_cpp_history_roundtrip(self):
        t = UpbitRealTrader(MagicMock(), silent=True)
        t.add_history_event({"type": "BUY", "symbol": "KRW-BTC", "price": 1.0, "qty": 1.0, "time": 5.0,
                             "is_real": True, "mode": "REAL", "exchange": "UPBIT"})
        ev = t.history_annotated()[0]
        self.assertTrue(ev["is_real"])
        self.assertEqual(ev["mode"], "REAL")
        self.assertEqual(ev["exchange"], "UPBIT")

    def test_virtual_trader_default_label_is_simulation(self):
        v = VirtualTrader(initial_balance=10_000_000.0, silent=True)
        asyncio.run(v.buy("KRW-BTC", 100_000_000.0, 1_000_000.0))
        ev = v.history_annotated()[-1]
        self.assertFalse(ev["is_real"])
        self.assertEqual(ev["mode"], "SIMULATION")
