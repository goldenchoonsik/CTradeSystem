"""
Unit / Regression Test: Verify CandleAggregator & AIStrategy Candle Sequence Monotonic Increment
Ensures that after 2,000 candles (deque maxlen limit), the sequence counter keeps increasing monotonically,
so cooldowns expire properly and TBM time-based exit (max_hold_candles) triggers accurately.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pandas as pd
import numpy as np

from core.candle_aggregator import CandleAggregator
from shared.core.tbm_executor import TBMPositionManager

KST = timezone(timedelta(hours=9))


class TestAICandleSeqOverflow(unittest.TestCase):

    def test_candle_aggregator_seq_beyond_max_candles(self):
        """2000개 이상의 캔들이 수신되었을 때 candle_seq가 단조 증가하는지 검증"""
        max_buf = 50  # 테스트용 작은 버퍼
        agg = CandleAggregator(interval_minutes=1.0, max_candles=max_buf)

        base_time = datetime(2026, 8, 29, 9, 0, 0, tzinfo=KST)
        
        # 120개의 1분봉 틱 생성 및 전달 (버퍼 50개 초과)
        for i in range(120):
            t = base_time + timedelta(minutes=i)
            agg.on_target_tick(100.0 + i, 10.0, "BID", timestamp=t)
            agg.on_binance_tick(100.0 + i, 10.0, False, timestamp=t)
            # 다음 캔들 경계로 진입시켜 flush 유도
            t_next = t + timedelta(seconds=61)
            agg.check_candle_complete(t_next)

        # deque 크기는 max_candles에 고정되지만, total_completed_candles / candle_seq는 120까지 증가해야 함
        self.assertEqual(len(agg.candles), max_buf)
        self.assertGreaterEqual(agg.total_completed_candles, 120)
        self.assertGreaterEqual(agg.candle_seq, 120)

    def test_cooldown_and_time_expiry_beyond_2000_candles(self):
        """2,000캔들 이후 시점(예: 2500캔들)에서 쿨다운 해제 및 TBM 시간만료 청산이 정상 동작하는지 시뮬레이션 검증"""
        params = {
            'buy_threshold': 0.5,
            'take_profit': 2.0,
            'stop_loss': 1.0,
            'predict_ahead_candles': 10,
            'max_hold_candles': 10,
            'cooldown_minutes': 3
        }
        
        pos_mgr = TBMPositionManager(
            params=params,
            cost=0.001,
            interval_minutes=1.0
        )
        self.assertEqual(pos_mgr.cooldown_candles, 3)
        self.assertEqual(pos_mgr.max_hold_candles, 10)

        # 1. 2500번째 캔들 시점 시뮬레이션
        current_candle_seq = 2500
        entry_candle_idx = 2500

        # 2. 10캔들 경과 (2510번째 캔들)
        current_candle_seq = 2510
        elapsed_candles = current_candle_seq - entry_candle_idx
        self.assertEqual(elapsed_candles, 10)

        # TBM 청산 체크: 시간만료 발동 확인
        exit_res = pos_mgr.check_exit(
            entry_price=1000.0,
            entry_atr=10.0,
            current_price=1000.0,
            elapsed_candles=elapsed_candles,
            current_time=datetime(2026, 8, 29, 12, 0, tzinfo=KST),
            is_candle_close=True
        )
        self.assertIsNotNone(exit_res)
        self.assertEqual(exit_res['exit_type'], "시간만료")
        self.assertEqual(exit_res['hold_candles'], 10)

        # 3. 청산 후 쿨다운 설정 시뮬레이션
        cooldown_until_candle = current_candle_seq + pos_mgr.cooldown_candles  # 2510 + 3 = 2513

        # 2511, 2512 캔들: 쿨다운 미해제 (진입 불가)
        self.assertLess(2511, cooldown_until_candle)
        self.assertLess(2512, cooldown_until_candle)

        # 2513 캔들: 쿨다운 정상 해제 (진입 허용)
        self.assertGreaterEqual(2513, cooldown_until_candle)

    def test_add_backfilled_candle_sync(self):
        """과거 캔들 백필 시 candle_seq 동기화 검증"""
        agg = CandleAggregator(interval_minutes=1.0, max_candles=100)
        for i in range(30):
            agg.add_backfilled_candle({"timestamp": datetime(2026, 8, 29, 9, i, tzinfo=KST), "target_close": 50000.0})

        self.assertEqual(len(agg.candles), 30)
        self.assertEqual(agg.candle_seq, 30)
        self.assertEqual(agg.total_completed_candles, 30)

    def test_check_candle_complete_when_tick_already_flushed_boundary(self):
        """실시간 틱 핸들러가 이미 캔들을 flush했더라도 check_candle_complete가 이벤트를 유실하지 않고 정확히 1회 감지하는지 검증"""
        agg = CandleAggregator(interval_minutes=10.0, max_candles=100)

        # 1. 10:00 캔들 내 틱 수신
        t1 = datetime(2026, 9, 8, 10, 0, 10, tzinfo=KST)
        agg.on_target_tick(100000.0, 1.0, "BID", t1)

        # 아직 10:05 시점: 캔들 미완성 -> None
        t_mid = datetime(2026, 9, 8, 10, 5, 0, tzinfo=KST)
        self.assertIsNone(agg.check_candle_complete(t_mid))

        # 2. 10:10:01에 다음 캔들의 틱이 먼저 도착함 (틱 핸들러 내부에서 _ensure_boundary 호출되어 10:00 캔들 flush됨)
        t2 = datetime(2026, 9, 8, 10, 10, 1, tzinfo=KST)
        agg.on_target_tick(100500.0, 2.0, "BID", t2)

        # 3. 0.3초 뒤 엔진 루프가 check_candle_complete 호출!
        # 기존 버그: 틱에 의해 이미 경계가 넘어가서 _ensure_boundary가 False를 반환하여 None으로 유실됨
        # 수정 후: candle_seq(1) > _last_checked_seq(0) 이므로 정상 반환!
        t_engine = datetime(2026, 9, 8, 10, 10, 2, tzinfo=KST)
        df = agg.check_candle_complete(t_engine)
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[-1]['target_close'], 100000.0)

        # 4. 다음 루프(10:10:03)에서 다시 check_candle_complete 호출 -> 중복 방지 (None 반환)
        t_engine_next = datetime(2026, 9, 8, 10, 10, 3, tzinfo=KST)
        self.assertIsNone(agg.check_candle_complete(t_engine_next))

    def test_backfilled_candles_do_not_trigger_live_complete(self):
        """과거 캔들 백필 주입 직후 라이브 첫 틱에서 과거 데이터가 새 완성 캔들로 오인되지 않는지 검증"""
        agg = CandleAggregator(interval_minutes=10.0, max_candles=100)
        base_t = datetime(2026, 9, 8, 8, 0, tzinfo=KST)
        for i in range(10):
            agg.add_backfilled_candle({
                "timestamp": base_t + timedelta(minutes=i * 10),
                "target_close": 100000.0 + i
            })

        self.assertEqual(agg.candle_seq, 10)
        # 백필 완료 직후 캔들 확인 시 새 라이브 캔들이 아니므로 None 반환
        t_check = datetime(2026, 9, 8, 9, 45, tzinfo=KST)
        self.assertIsNone(agg.check_candle_complete(t_check))


if __name__ == "__main__":
    unittest.main()

