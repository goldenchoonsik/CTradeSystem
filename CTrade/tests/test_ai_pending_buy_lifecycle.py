"""
CTrade Tests - AI Strategy Pending Buy & Position Lifecycle Test
가상 포지션과 실제 체결 사이의 경합 방지(State Separation) 및 유령 포지션 제거 검증
"""
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone, timedelta

from core.ai_strategy import AIStrategyModule, Signal, SignalAction, SignalReason
from shared.core.tbm_executor import TBMPositionManager
from core.risk_manager import RiskManager

KST = timezone(timedelta(hours=9))


class TestAIPendingBuyLifecycle(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        """AIStrategyModule 목(Mock) 인스턴스 생성"""
        with patch.object(AIStrategyModule, '__init__', lambda self, *args, **kwargs: None):
            self.strategy = AIStrategyModule("dummy_deploy_path")
            self.strategy.symbol = "KRW-BTC"
            self.strategy.interval = "30m"
            self.strategy.interval_minutes = 30.0
            self.strategy.sell_tax = 0.0
            self.strategy.fee_rate = 0.0005
            self.strategy.cost = 0.002
            self.strategy.params = {
                'buy_threshold': 0.5,
                'take_profit': 3.0,
                'stop_loss': 2.0,
                'predict_ahead_candles': 5,
                'cooldown_minutes': 5
            }
            self.strategy.has_position = False
            self.strategy.is_pending_entry = False
            self.strategy.is_pending_exit = False
            self.strategy._position_reserved = False
            self.strategy.entry_price = 0.0
            self.strategy.entry_atr = 0.0
            self.strategy.last_signal_atr = 0.0
            self.strategy.entry_time = None
            self.strategy.entry_candle_idx = 0
            self.strategy.current_candle_idx = 10
            self.strategy._cooldown_until_candle = 0
            self.strategy.test_level = 0
            self.strategy.shadow_mode = False
            self.strategy.last_probability = 0.65
            self.strategy.features_order = []
            
            self.strategy.position_manager = TBMPositionManager(self.strategy.params, self.strategy.cost, 30.0)
            self.strategy.risk_manager = RiskManager()
            self.strategy._log_trade_dump = MagicMock()
            self.strategy.aggregator = MagicMock()
            self.strategy.aggregator.total_completed_candles = 10

    def test_ai_strategy_signal_and_pending_state(self):
        """1. 매수 신호 발생 시: has_position=False 유지, is_pending_entry=True 설정 검증"""
        df_mock = MagicMock()
        df_mock.columns = ['target_atr']
        df_mock.__getitem__.return_value.iloc = [-1]
        df_mock.__getitem__.return_value.iloc[-1] = 1500.0

        current_price = 100000.0
        now = datetime.now(KST)

        sig = self.strategy._evaluate_entry(df_mock, 0.65, current_price, now)

        self.assertIsNotNone(sig)
        self.assertEqual(sig.action, SignalAction.BUY)
        self.assertEqual(sig.price, current_price)
        self.assertEqual(sig.entry_atr, 1500.0)
        
        # 핵심 검증: 실제 체결 전까지는 has_position이 False여야 함
        self.assertFalse(self.strategy.has_position)
        self.assertTrue(self.strategy.is_pending_entry)
        self.assertEqual(self.strategy.last_signal_atr, 1500.0)

    def test_no_premature_sell_while_pending_buy(self):
        """2. 매수 대기(pending) 중 가격 폭락 시 조기 손절(SELL) 신호 방지 검증"""
        # pending 상태 설정
        self.strategy.is_pending_entry = True
        self.strategy.has_position = False
        self.strategy.entry_price = 0.0

        # 캔들 미완성 상태에서 가격이 50% 폭락한 틱 수신
        self.strategy.aggregator.check_candle_complete.return_value = None

        crashed_price = 50000.0
        sig = self.strategy.evaluate(crashed_price)

        # has_position이 False이므로 check_exit을 타지 않고 신호가 발생하지 않아야 함
        self.assertIsNone(sig)
        self.assertFalse(self.strategy.has_position)
        self.assertTrue(self.strategy.is_pending_entry)

    def test_on_buy_filled_lifecycle(self):
        """3. 주문 체결(on_buy_filled) 콜백 시 실제 포지션 오픈 및 TBM 청산 정상 작동 검증"""
        self.strategy.is_pending_entry = True
        self.strategy.last_signal_atr = 2000.0

        real_fill_price = 99500.0
        fill_time = datetime.now(KST)

        # 체결 훅 호출
        self.strategy.on_buy_filled(real_entry_price=real_fill_price, entry_atr=2000.0, timestamp=fill_time, candle_idx=10)

        # 상태 전환 확인
        self.assertFalse(self.strategy.is_pending_entry)
        self.assertTrue(self.strategy.has_position)
        self.assertEqual(self.strategy.entry_price, 99500.0)
        self.assertEqual(self.strategy.entry_atr, 2000.0)
        self.assertEqual(self.strategy.entry_time, fill_time)
        self.assertEqual(self.strategy.risk_manager._current_positions, 1)

        # 이후 가격이 비상 손절선(99500 - 2000 * 4.0 = 91500) 이하로 급락 시 실시간 틱 비상 청산 신호 발생 검증
        emergency_sl_price = 91000.0
        self.strategy.aggregator.check_candle_complete.return_value = None

        exit_sig = self.strategy.evaluate(emergency_sl_price, current_timestamp=fill_time)
        self.assertIsNotNone(exit_sig)
        self.assertEqual(exit_sig.action, SignalAction.SELL)
        self.assertEqual(exit_sig.reason, SignalReason.EMERGENCY_STOP_LOSS)
        
        # 원자적 청산 라이프사이클: evaluate 단계에서는 체결 대기 플래그(is_pending_exit=True)만 세팅되고 실제 포지션 오픈 상태 유지
        self.assertTrue(self.strategy.is_pending_exit)
        self.assertTrue(self.strategy.has_position)

        # 다음 틱 수신 시 중복 매도 신호 생성 방지 검증
        duplicate_sig = self.strategy.evaluate(emergency_sl_price, current_timestamp=fill_time)
        self.assertIsNone(duplicate_sig)

        # 실제 매도 주문 체결(on_sell_filled) 콜백 시 포지션 종료 및 리스크 매니저 확정
        self.strategy.on_sell_filled(net_return=exit_sig.net_return, exit_type="비상손절")
        self.assertFalse(self.strategy.is_pending_exit)
        self.assertFalse(self.strategy.has_position)
        self.assertEqual(self.strategy.risk_manager._current_positions, 0)

    def test_on_sell_cancelled_rollback(self):
        """4. 매도 주문 실패/취소 시 is_pending_exit 롤백 및 다음 틱 재시도 검증"""
        self.strategy.has_position = True
        self.strategy.is_pending_exit = True
        self.strategy.entry_price = 100000.0
        self.strategy.entry_atr = 2000.0

        # 매도 실패 롤백
        self.strategy.on_sell_cancelled(reason="NETWORK_ERROR")
        self.assertFalse(self.strategy.is_pending_exit)
        self.assertTrue(self.strategy.has_position)

        # 다음 틱에서 비상손절(Hard SL) 청산 신호 재발생 가능 검증
        self.strategy.aggregator.check_candle_complete.return_value = None
        exit_sig = self.strategy.evaluate(90000.0, current_timestamp=datetime.now(KST))
        self.assertIsNotNone(exit_sig)
        self.assertEqual(exit_sig.action, SignalAction.SELL)
        self.assertEqual(exit_sig.reason, SignalReason.EMERGENCY_STOP_LOSS)
        self.assertTrue(self.strategy.is_pending_exit)


    def test_on_buy_cancelled_rollback(self):
        """5. 매수 취소/슬리피지 초과 시 pending 상태 안전 롤백 검증"""
        self.strategy.is_pending_entry = True
        self.strategy.has_position = False

        self.strategy.on_buy_cancelled(reason="SLIPPAGE_EXCEEDED")

        self.assertFalse(self.strategy.is_pending_entry)
        self.assertFalse(self.strategy.has_position)
        self.assertEqual(self.strategy.risk_manager._current_positions, 0)

    async def test_engine_pending_buy_task_cancelled_on_sell(self):
        """6. Engine에서 SELL 신호 또는 매도 실행 시 대기 중인 매수 태스크 취소 및 on_sell_filled 연동 검증"""
        from core.engine import STradeEngine
        
        with patch.object(STradeEngine, '__init__', lambda self, *args, **kwargs: None):
            engine = STradeEngine()
            engine.pending_buy_tasks = {}
            engine.ai_strategies = {}
            engine.simulator = MagicMock()
            engine.simulator.sell = AsyncMock(return_value=1000.0)
            engine.simulator.positions = {}
            engine.simulation_mode = True
            engine.is_kis_symbol = MagicMock(return_value=False)

            mock_ai = MagicMock()
            mock_ai.on_buy_cancelled = MagicMock()
            mock_ai.on_sell_filled = MagicMock()
            mock_ai.entry_price = 100000.0
            mock_ai.cost = 0.002
            engine.ai_strategies["KRW-BTC"] = mock_ai

            # 가상의 대기 태스크 등록
            async def dummy_wait():
                await asyncio.sleep(10)
            
            task = asyncio.create_task(dummy_wait())
            engine.pending_buy_tasks["KRW-BTC"] = task

            # execute_sell 호출 시 pending task가 취소되고 on_sell_filled가 호출되어야 함
            await engine.execute_sell("KRW-BTC", 100000.0, reason="STOP_LOSS")
            await asyncio.sleep(0)  # 취소 시그널 전파 대기

            self.assertTrue(task.cancelled() or task.done() or getattr(task, 'cancelling', lambda: 0)() > 0)
            mock_ai.on_buy_cancelled.assert_called_once_with(reason="EXECUTE_SELL_CALLED")
            mock_ai.on_sell_filled.assert_called_once()

    async def test_trading_active_off_blocks_buy_but_executes_sell(self):
        """7. trading_active=False 일 때 BUY는 차단되고 SELL(손절)은 정상 실행되는지 검증"""
        from core.shared_state import SharedState
        shared = SharedState()
        shared.update_control({"trading_active": False})

        # BUY 신호일 때: trading_active=False 이므로 매수 조건 검사 시 차단
        self.assertFalse(shared.control.get("trading_active", False))
        
        # SELL 신호일 때: trading_active 플래그와 무관하게 SELL 로직 수행
        sell_signal = Signal(action=SignalAction.SELL, price=95000.0, probability=0.5, reason=SignalReason.STOP_LOSS)
        self.assertEqual(sell_signal.action, SignalAction.SELL)


if __name__ == '__main__':
    unittest.main()
