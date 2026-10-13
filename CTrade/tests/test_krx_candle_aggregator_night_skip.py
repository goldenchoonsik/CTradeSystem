"""
Unit Test: Verify CandleAggregator KRX Session Night Candle Skip
"""
import unittest
import pandas as pd
from datetime import datetime, timezone, timedelta
from core.candle_aggregator import CandleAggregator

KST = timezone(timedelta(hours=9))


class TestKRXCandleAggregatorNightSkip(unittest.TestCase):

    def test_krx_night_candles_skipped(self):
        """KRX 모드에서 15:30 장마감 후 야간 틱/타이머가 들어와도 빈 캔들이 생성되지 않고 익일 09:00으로 깨끗하게 연결되는지 검증"""
        agg = CandleAggregator(interval_minutes=1.0, max_candles=2000, market_type="KRX")

        # 1. 2026-08-28 15:28:00 ~ 15:30:00 (장마감 직전 틱 주입)
        t1 = datetime(2026, 8, 28, 15, 28, 10, tzinfo=KST)
        agg.on_target_tick(35000.0, 10.0, "BID", t1)

        t2 = datetime(2026, 8, 28, 15, 29, 10, tzinfo=KST)
        agg.on_target_tick(35050.0, 20.0, "BID", t2)

        # 15:30:00 정규장 마감 틱
        t3 = datetime(2026, 8, 28, 15, 30, 0, tzinfo=KST)
        agg.on_target_tick(35100.0, 30.0, "BID", t3)

        # 2. 야간 시간대 (18:00:00, 23:00:00, 익일 08:30:00 등) 해외선물/코인 틱 수신
        t_night1 = datetime(2026, 8, 28, 18, 0, 0, tzinfo=KST)
        agg.on_futures_tick("NQ", 20000.0, t_night1)

        t_night2 = datetime(2026, 8, 28, 23, 0, 0, tzinfo=KST)
        agg.on_binance_tick(60000.0, 1.0, False, t_night2)

        t_night3 = datetime(2026, 8, 29, 8, 30, 0, tzinfo=KST)
        agg.check_candle_complete(t_night3)

        # 야간 캔들이 버퍼를 채우지 않았는지 확인
        # 15:28 캔들 1개, 15:29 캔들 1개 완료되어 총 2개여야 함
        df_night = agg.to_dataframe()
        self.assertLessEqual(len(df_night), 3)
        # 야간 시간(18:00, 23:00, 08:30 등) 캔들이 df에 없어야 함
        night_timestamps = df_night[df_night['timestamp'].dt.time > datetime(2026, 8, 28, 15, 30, tzinfo=KST).time()]
        self.assertEqual(len(night_timestamps), 0)

        # 3. 익일 2026-08-29 09:00:05 (개장 첫 틱 주입)
        t_day2_open = datetime(2026, 8, 29, 9, 0, 5, tzinfo=KST)
        agg.on_target_tick(35200.0, 50.0, "BID", t_day2_open)

        # 익일 09:01:05 (09:00 캔들 완성 트리거)
        t_day2_next = datetime(2026, 8, 29, 9, 1, 5, tzinfo=KST)
        agg.on_target_tick(35250.0, 60.0, "BID", t_day2_next)

        df_result = agg.to_dataframe()

        # 익일(08-29) 데이터는 정확히 09:00:00 캔들부터 시작해야 함
        df_day2 = df_result[df_result['timestamp'].dt.date == datetime(2026, 8, 29).date()]
        self.assertEqual(len(df_day2), 1)
        self.assertEqual(df_day2.iloc[0]['timestamp'].hour, 9)
        self.assertEqual(df_day2.iloc[0]['timestamp'].minute, 0)

    def _close_day(self, interval_seconds, day=6, close=(15, 30), open_=(9, 0)):
        """연속장 체결 2건 + 마감 동시호가 체결, 이후 시간외 체결 → 익일 개장."""
        ch, cm = close
        agg = CandleAggregator(interval_seconds=interval_seconds, market_type="KRX", symbol="069500")
        agg.on_target_tick(100.0, 1.0, "BID", datetime(2026, 10, day, ch, cm - 20, 0, tzinfo=KST))
        agg.on_target_tick(101.0, 1.0, "BID", datetime(2026, 10, day, ch, cm - 10, 59, tzinfo=KST) - timedelta(seconds=60))
        agg.on_target_tick(105.0, 999.0, "BID", datetime(2026, 10, day, ch, cm, 1, tzinfo=KST))   # 종가 동시호가
        agg.check_candle_complete(datetime(2026, 10, day, ch, cm + 1, 5, tzinfo=KST))             # 수신 대기 종료
        agg.on_target_tick(105.0, 7.0, "BID", datetime(2026, 10, day, ch, cm + 15, 0, tzinfo=KST))  # 시간외 종가
        agg.on_target_tick(105.0, 3.0, "BID", datetime(2026, 10, day + 1, 8, 35, 0, tzinfo=KST))   # 장전 시간외
        agg.on_target_tick(110.0, 5.0, "BID", datetime(2026, 10, day + 1, 9, 0, 3, tzinfo=KST))
        agg.check_candle_complete(datetime(2026, 10, day + 1, 9, 16, 0, tzinfo=KST))
        df = agg.to_dataframe()
        return df[df.timestamp.dt.day == day], df[df.timestamp.dt.day == day + 1]

    def test_closing_auction_lands_where_training_data_puts_it(self):
        """동시호가는 그날 마지막 봉에 들어가야 한다 — 학습 데이터와 같은 자리·같은 라벨.
        15분봉: 15:15 봉(구간 시작 라벨) / 1분봉: Creon 처럼 15:30 확정 라벨 봉 / 10초봉: 15:30:00 봉."""
        d1, _ = self._close_day(900)
        self.assertEqual(d1.timestamp.iloc[-1].strftime("%H:%M"), "15:15")
        self.assertEqual(d1.target_close.iloc[-1], 105.0)
        self.assertEqual(d1.target_volume.iloc[-1], 1000.0)   # 15:19:59 체결 + 동시호가 (시간외 제외)

        d1, _ = self._close_day(60)
        self.assertEqual(d1.timestamp.iloc[-1].strftime("%H:%M"), "15:30")
        self.assertEqual(d1.target_volume.iloc[-1], 999.0)
        # Creon 1분봉에는 체결 없는 분의 봉이 없다 — 빈 분봉을 만들지 않는다
        self.assertEqual(d1.timestamp.dt.strftime("%H:%M").tolist(), ["15:11", "15:20", "15:30"])

        d1, _ = self._close_day(10)
        self.assertEqual(d1.timestamp.iloc[-1].strftime("%H:%M:%S"), "15:30:00")
        self.assertEqual(d1.target_volume.iloc[-1], 999.0)
        self.assertGreater(len(d1), 100, "초봉은 학습 그리드처럼 빈 봉을 채운다")

    def test_closing_auction_never_leaks_into_next_day_open(self):
        """종전 결함: 마감 후 체결이 빌더에 남아 익일 09:00 봉의 시가·거래량을 오염시켰다."""
        for sec, label in ((10, "09:00:00"), (60, "09:01:00"), (900, "09:00:00")):
            _, d2 = self._close_day(sec)
            first = d2.iloc[0]
            self.assertEqual(first.timestamp.strftime("%H:%M:%S"), label, sec)
            self.assertEqual(first.target_open, 110.0, sec)
            self.assertEqual(first.target_volume, 5.0, sec)

    def test_special_session_from_calendar_ledger(self):
        """수능일(원장: 2025-11-13 10:00~16:30) — 16:30 동시호가가 마지막 봉에 들어가고 15:30 에 닫히지 않는다."""
        agg = CandleAggregator(interval_seconds=900, market_type="KRX", symbol="069500")
        agg.on_target_tick(100.0, 1.0, "BID", datetime(2025, 11, 13, 9, 30, 0, tzinfo=KST))   # 개장 전 — 무시
        agg.on_target_tick(100.0, 2.0, "BID", datetime(2025, 11, 13, 10, 0, 5, tzinfo=KST))
        agg.on_target_tick(101.0, 1.0, "BID", datetime(2025, 11, 13, 15, 40, 0, tzinfo=KST))  # 평일이면 장후
        agg.on_target_tick(102.0, 1.0, "BID", datetime(2025, 11, 13, 16, 19, 0, tzinfo=KST))
        agg.on_target_tick(103.0, 50.0, "BID", datetime(2025, 11, 13, 16, 30, 1, tzinfo=KST))  # 동시호가
        agg.check_candle_complete(datetime(2025, 11, 13, 16, 31, 5, tzinfo=KST))
        df = agg.to_dataframe()
        self.assertEqual(df.timestamp.dt.strftime("%H:%M").tolist(), ["10:00", "15:30", "16:15"])
        self.assertEqual(df.target_volume.tolist(), [2.0, 1.0, 51.0])

    def test_minute_bars_skip_trading_halts(self):
        """VI/서킷브레이커 등 체결 공백 — Creon 1분봉처럼 분봉 이상은 빈 봉을 만들지 않는다."""
        agg = CandleAggregator(interval_seconds=60, market_type="KRX", symbol="069500")
        agg.on_target_tick(100.0, 1.0, "BID", datetime(2026, 10, 6, 14, 13, 30, tzinfo=KST))
        agg.on_target_tick(99.0, 1.0, "BID", datetime(2026, 10, 6, 14, 34, 10, tzinfo=KST))   # 20분 중단 후 재개
        agg.check_candle_complete(datetime(2026, 10, 6, 14, 35, 1, tzinfo=KST))
        self.assertEqual(agg.to_dataframe().timestamp.dt.strftime("%H:%M").tolist(), ["14:14", "14:35"])

    def test_crypto_mode_continuous_candles(self):
        """CRYPTO 모드에서는 24시간 캔들이 끊김없이 ffill로 생성되는지 교차 검증"""
        agg = CandleAggregator(interval_minutes=1.0, max_candles=2000, market_type="CRYPTO")

        t1 = datetime(2026, 8, 28, 23, 58, 0, tzinfo=KST)
        agg.on_target_tick(100000.0, 1.0, "BID", t1)

        # 5분 건너뛴 틱 주입
        t2 = datetime(2026, 8, 29, 0, 3, 0, tzinfo=KST)
        agg.on_target_tick(100100.0, 1.0, "BID", t2)

        df = agg.to_dataframe()
        # 23:58, 23:59, 00:00, 00:01, 00:02 등 5개의 연속 캔들이 모두 ffill로 생성되어 있어야 함
        self.assertEqual(len(df), 5)


if __name__ == "__main__":
    unittest.main()
