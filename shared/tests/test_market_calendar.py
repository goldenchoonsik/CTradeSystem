"""
CTradeSystem - Market Calendar Module Unit Tests
"""
import unittest
from datetime import datetime, date
from shared.core.market_calendar import (
    get_market_holidays, is_trading_day, get_trading_days, sync_market_holidays
)


class TestMarketCalendar(unittest.TestCase):
    def test_get_market_holidays_krx(self):
        krx_h = get_market_holidays("KRX")
        self.assertIsInstance(krx_h, set)
        self.assertGreater(len(krx_h), 20)
        # 대표적인 한국 공휴일/휴장일 검증
        self.assertIn("2024-01-01", krx_h)  # 신정
        self.assertIn("2024-12-25", krx_h)  # 성탄절
        self.assertIn("2025-06-03", krx_h)  # 임시공휴일

    def test_get_market_holidays_krx_historical(self):
        """2002년 이전 장기 시계열(1980~2002년) 과거 공휴일 및 거래일 정합성 검증"""
        krx_h = get_market_holidays("KRX")
        self.assertGreater(len(krx_h), 500)
        # 과거 법정 공휴일/임시공휴일 검증
        self.assertIn("1980-05-05", krx_h)  # 1980 어린이날
        self.assertIn("2000-04-13", krx_h)  # 2000 총선 선거일
        self.assertIn("2001-01-24", krx_h)  # 2001 설날 연휴
        self.assertIn("2002-04-05", krx_h)  # 2002 식목일
        self.assertIn("2002-07-01", krx_h)  # 2002 월드컵 임시공휴일

        # 실제 거래 관측일은 휴장일이 아니어야 함
        self.assertNotIn("1980-01-04", krx_h)
        self.assertNotIn("2001-01-22", krx_h)
        self.assertNotIn("2002-01-02", krx_h)

    def test_get_market_holidays_us(self):
        us_h = get_market_holidays("US")
        self.assertIsInstance(us_h, set)
        self.assertGreater(len(us_h), 20)
        # 대표적인 미국 공휴일 검증
        self.assertIn("2024-01-01", us_h)  # New Year's Day
        self.assertIn("2024-07-04", us_h)  # Independence Day
        self.assertIn("2024-12-25", us_h)  # Christmas

    def test_is_trading_day(self):
        # 주말 검증 (2024-06-01 토요일, 2024-06-02 일요일)
        self.assertFalse(is_trading_day("2024-06-01", "KRX"))
        self.assertFalse(is_trading_day("2024-06-02", "KRX"))

        # 정상 평일 영업일 (2024-06-03 월요일)
        self.assertTrue(is_trading_day("2024-06-03", "KRX"))

        # 공휴일 (2024-06-06 현충일)
        self.assertFalse(is_trading_day("2024-06-06", "KRX"))

    def test_get_trading_days_range(self):
        # 2024-06-01 (토) ~ 2024-06-07 (금)
        # 6/1(토), 6/2(일), 6/3(월 O), 6/4(화 O), 6/5(수 O), 6/6(목 현충일 X), 6/7(금 O)
        # 영업일 총 4일이어야 함 (6/3, 6/4, 6/5, 6/7)
        t_days = get_trading_days("2024-06-01", "2024-06-07", "KRX")
        expected = ["2024-06-03", "2024-06-04", "2024-06-05", "2024-06-07"]
        self.assertEqual(t_days, expected)


if __name__ == "__main__":
    unittest.main()
