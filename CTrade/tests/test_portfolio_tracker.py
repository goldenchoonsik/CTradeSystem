import os
import json
import unittest
import tempfile
from unittest.mock import patch

from utils.portfolio_tracker import (
    PortfolioTracker,
    load_baseline,
    save_baseline,
    get_symbol_name,
    _get_baseline_path
)


class TestPortfolioTracker(unittest.TestCase):
    def test_symbol_name_mapping(self):
        self.assertEqual(get_symbol_name("069500"), "KODEX 200")
        self.assertEqual(get_symbol_name("005930"), "삼성전자")
        self.assertEqual(get_symbol_name("KRW-BTC"), "비트코인")
        self.assertEqual(get_symbol_name("KRW-ETH"), "이더리움")
        self.assertEqual(get_symbol_name("KRW-CUSTOM"), "CUSTOM")

    def test_calculate_portfolio_and_holdings(self):
        # 1. Mock Raw Data (국내 전용)
        upbit_raw = [
            {"currency": "KRW", "balance": "2000000.0", "locked": "0.0", "avg_buy_price": "0"},
            {"currency": "BTC", "balance": "0.1", "locked": "0.0", "avg_buy_price": "90000000.0"},
            {"currency": "XRP", "balance": "1000.0", "locked": "0.0", "avg_buy_price": "700.0"}
        ]
        kis_raw = {
            "krw_deposit": 5000000.0,
            "stocks": [
                {"pdno": "069500", "prdt_name": "KODEX 200", "hldg_qty": "100", "pchs_avg_pric": "34000.0", "prpr": "35000.0"},
                {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "50", "pchs_avg_pric": "70000.0", "prpr": "72000.0"}
            ]
        }
        live_prices = {
            "KRW-BTC": 95000000.0,
            "KRW-XRP": 800.0,
            "069500": 36000.0,
            "005930": 74000.0
        }
        active_bot = ["KRW-BTC", "069500"]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_baseline = os.path.join(tmpdir, "account_baseline.json")
            with patch("utils.portfolio_tracker._get_baseline_path", return_value=tmp_baseline):
                # First calculation
                res = PortfolioTracker.calculate_portfolio(upbit_raw, kis_raw, live_prices, active_bot)
                
                summary = res["summary"]
                holdings = res["holdings_all"]
                u_holdings = res["upbit_holdings"]
                k_holdings = res["kis_holdings"]
                k_dom = res["kis_domestic_holdings"]
                k_ovrs = res["kis_overseas_holdings"]

                # UPBIT 검증: KRW 2,000,000 + (0.1 * 95,000,000 = 9,500,000) + (1000 * 800 = 800,000) = 12,300,000
                self.assertEqual(summary["upbit_total"], 12300000.0)
                self.assertEqual(summary["upbit_deposit"], 2000000.0)
                self.assertEqual(summary["upbit_coins_eval"], 10300000.0)

                # KIS 검증: 예수금 5,000,000 + (100 * 36,000 = 3,600,000) + (50 * 74,000 = 3,700,000) = 12,300,000
                self.assertEqual(summary["kis_total"], 12300000.0)
                self.assertEqual(summary["kis_deposit"], 5000000.0)
                self.assertEqual(summary["kis_domestic_total"], 12300000.0)
                self.assertEqual(summary["has_overseas_holdings"], False)

                # TOTAL 검증: 12,300,000 + 12,300,000 = 24,600,000
                self.assertEqual(summary["combined_total"], 24600000.0)

                # 계층형 하위 종목 분리 검증
                self.assertEqual(len(holdings), 4)
                self.assertEqual(len(u_holdings), 2)
                self.assertEqual(len(k_holdings), 2)
                self.assertEqual(len(k_dom), 2)
                self.assertEqual(len(k_ovrs), 0)

                # Baseline 파일 생성 확인
                self.assertTrue(os.path.exists(tmp_baseline))
                base_data = load_baseline()
                self.assertIn("TOTAL", base_data)
                self.assertEqual(base_data["TOTAL"]["initial_balance"], 24600000.0)
                self.assertEqual(base_data["TOTAL"]["daily_balance"], 24600000.0)

                # 2. 가격 변동 후 수익률 연산 검증
                live_prices_up = dict(live_prices)
                live_prices_up["KRW-BTC"] = 100000000.0 # +500,000원 상승
                res2 = PortfolioTracker.calculate_portfolio(upbit_raw, kis_raw, live_prices_up, active_bot)
                summary2 = res2["summary"]

                # 총자산 24,600,000 + 500,000 = 25,100,000
                self.assertEqual(summary2["combined_total"], 25100000.0)
                self.assertEqual(summary2["combined_day_pnl"], 500000.0)
                expected_roi = (500000.0 / 24600000.0) * 100.0
                self.assertEqual(round(summary2["combined_day_roi"], 4), round(expected_roi, 4))

    def test_calculate_portfolio_with_overseas_stocks(self):
        """해외주식(USD) 혼합 시 환율 연동 및 계층형 분리 정밀도 검증"""
        upbit_raw = [
            {"currency": "KRW", "balance": "1000000.0", "locked": "0.0", "avg_buy_price": "0"}
        ]
        kis_raw = {
            "krw_deposit": 2000000.0,
            "usd_deposit": 500.0, # $500 외화 예수금
            "stocks": [
                {"pdno": "069500", "prdt_name": "KODEX 200", "hldg_qty": "50", "pchs_avg_pric": "30000.0", "prpr": "32000.0"}
            ],
            "overseas_stocks": [
                {"ovrs_pdno": "QQQ", "ovrs_item_name": "Invesco QQQ", "ovrs_cblc_qty": "10", "pchs_avg_pric": "450.0", "now_pric2": "480.0"},
                {"ovrs_pdno": "TSLA", "ovrs_item_name": "Tesla Inc", "ovrs_cblc_qty": "5", "pchs_avg_pric": "200.0", "now_pric2": "220.0"}
            ]
        }
        live_prices = {
            "069500": 32000.0,
            "QQQ": 500.0, # 실시간 $500
            "TSLA": 220.0 # 실시간 $220
        }
        fx_rate = 1400.0 # 환율 ₩1,400/$

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_baseline = os.path.join(tmpdir, "account_baseline.json")
            with patch("utils.portfolio_tracker._get_baseline_path", return_value=tmp_baseline):
                res = PortfolioTracker.calculate_portfolio(upbit_raw, kis_raw, live_prices, active_bot_symbols=["QQQ"], usd_krw_fx=fx_rate)
                summary = res["summary"]
                k_dom = res["kis_domestic_holdings"]
                k_ovrs = res["kis_overseas_holdings"]

                # 1. UPBIT = 1,000,000
                self.assertEqual(summary["upbit_total"], 1000000.0)

                # 2. KIS 국내: 예수금 2,000,000 + (50 * 32,000 = 1,600,000) = 3,600,000
                self.assertEqual(summary["kis_domestic_total"], 3600000.0)

                # 3. KIS 해외: 외화예수금 $500 + QQQ(10 * $500 = $5,000) + TSLA(5 * $220 = $1,100) = $6,600
                self.assertEqual(summary["kis_overseas_total_usd"], 6600.0)
                # 원화 환산 = $6,600 * 1,400 = ₩9,240,000
                self.assertEqual(summary["kis_overseas_total_krw"], 9240000.0)

                # 4. KIS Total = 3,600,000 + 9,240,000 = 12,840,000
                self.assertEqual(summary["kis_total"], 12840000.0)
                self.assertTrue(summary["has_overseas_holdings"])

                # 5. Grand Total = 1,000,000 + 12,840,000 = 13,840,000
                self.assertEqual(summary["combined_total"], 13840000.0)

                # 6. 해외 종목 BOT / EXTERNAL 태깅 및 메타데이터 확인
                qqq = next(h for h in k_ovrs if h["symbol"] == "QQQ")
                self.assertEqual(qqq["role"], "BOT")
                self.assertEqual(qqq["currency"], "USD")
                self.assertEqual(qqq["eval_amount_usd"], 5000.0)
                self.assertEqual(qqq["eval_amount"], 5000.0 * 1400.0)

                tsla = next(h for h in k_ovrs if h["symbol"] == "TSLA")
                self.assertEqual(tsla["role"], "EXTERNAL")
                self.assertEqual(tsla["currency"], "USD")

    def test_reset_baseline_all_and_individual(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_baseline = os.path.join(tmpdir, "account_baseline.json")
            with patch("utils.portfolio_tracker._get_baseline_path", return_value=tmp_baseline):
                # 1. 전체 리셋
                new_base = PortfolioTracker.reset_baseline(10000000.0, 20000000.0, target_broker="ALL")
                self.assertEqual(new_base["UPBIT"]["initial_balance"], 10000000.0)
                self.assertEqual(new_base["KIS"]["initial_balance"], 20000000.0)
                self.assertEqual(new_base["TOTAL"]["initial_balance"], 30000000.0)

                # 2. UPBIT만 단독 리셋 (UPBIT 자산이 15,000,000으로 입금 증가)
                base_u = PortfolioTracker.reset_baseline(15000000.0, 20000000.0, target_broker="UPBIT")
                self.assertEqual(base_u["UPBIT"]["initial_balance"], 15000000.0)
                self.assertEqual(base_u["KIS"]["initial_balance"], 20000000.0)
                self.assertEqual(base_u["TOTAL"]["initial_balance"], 35000000.0)

                # 3. KIS만 단독 리셋 (KIS 자산이 25,000,000으로 입금 증가)
                base_k = PortfolioTracker.reset_baseline(15000000.0, 25000000.0, target_broker="KIS")
                self.assertEqual(base_k["UPBIT"]["initial_balance"], 15000000.0)
                self.assertEqual(base_k["KIS"]["initial_balance"], 25000000.0)
                self.assertEqual(base_k["TOTAL"]["initial_balance"], 40000000.0)

    def test_baseline_history_restore_and_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_baseline = os.path.join(tmpdir, "account_baseline.json")
            with patch("utils.portfolio_tracker._get_baseline_path", return_value=tmp_baseline):
                # 1. 초기 1차 리셋 (10M + 20M = 30M)
                PortfolioTracker.reset_baseline(10000000.0, 20000000.0, target_broker="ALL", note="1차 세팅")
                history1 = PortfolioTracker.get_baseline_history()
                self.assertEqual(len(history1), 1)
                hist1_id = history1[0]["id"]
                self.assertEqual(history1[0]["total_balance"], 30000000.0)

                # 2. 2차 리셋 (12M + 22M = 34M)
                PortfolioTracker.reset_baseline(12000000.0, 22000000.0, target_broker="ALL", note="2차 세팅")
                history2 = PortfolioTracker.get_baseline_history()
                self.assertEqual(len(history2), 2)
                self.assertEqual(history2[0]["total_balance"], 34000000.0)

                # 3. 1차 시점(30M)으로 복구(Restore)
                restored = PortfolioTracker.restore_baseline(hist1_id)
                self.assertIsNotNone(restored)
                self.assertEqual(restored["TOTAL"]["daily_balance"], 30000000.0)
                self.assertEqual(restored["UPBIT"]["daily_balance"], 10000000.0)
                self.assertEqual(restored["KIS"]["daily_balance"], 20000000.0)

                # 복구 후 이력 수 확인 (3건)
                history3 = PortfolioTracker.get_baseline_history()
                self.assertEqual(len(history3), 3)
                self.assertEqual(history3[0]["action"], "RESTORE")

                # 4. 이력 항목 삭제 (2차 세팅 이력 삭제)
                hist2_id = history2[0]["id"]
                del_ok = PortfolioTracker.delete_baseline_history(hist2_id)
                self.assertTrue(del_ok)
                history4 = PortfolioTracker.get_baseline_history()
                self.assertEqual(len(history4), 2)
                self.assertNotIn(hist2_id, [h["id"] for h in history4])


if __name__ == "__main__":
    unittest.main()
