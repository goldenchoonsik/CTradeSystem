import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.diagnostics.data_sniffer import DataSniffer
from utils.diagnostics.data_analyzer import DataAnalyzer
from utils.diagnostics.data_reporter import DataReporter


class TestDataDiagnostics(unittest.TestCase):

    def test_data_sniffer_lifecycle(self):
        sniffer = DataSniffer.instance()
        # 캡처 시작
        res = sniffer.start_capture(symbol="069500", duration_sec=5.0, max_records=100)
        self.assertIn(res["status"], ("started", "already_running"))
        self.assertTrue(sniffer.is_capturing)

        # 이벤트 기록
        sniffer.record_event("ticker", "069500", {"price": 35000.0, "volume": 10.0, "timestamp": time.time()})
        sniffer.record_event("orderbook", "069500", {
            "units": [
                {"bid_price": 34990.0, "bid_size": 100, "ask_price": 35010.0, "ask_size": 120}
            ]
        })
        # 타겟과 다른 심볼 무시 확인
        sniffer.record_event("ticker", "KRW-BTC", {"price": 100000000.0})

        status = sniffer.get_status()
        self.assertTrue(status["is_capturing"])
        self.assertEqual(status["captured_count"], 2)

        # 캡처 중단
        dump_info = sniffer.stop_capture()
        self.assertFalse(sniffer.is_capturing)
        self.assertEqual(dump_info["record_count"], 2)
        self.assertTrue(os.path.exists(dump_info["dump_path"]))

        # 정리
        if os.path.exists(dump_info["dump_path"]):
            os.remove(dump_info["dump_path"])

    def test_data_analyzer_clean_stream(self):
        """정상 시세 데이터 분석 시 EXCELLENT 판정 테스트"""
        base_t = time.time()
        clean_records = []
        for i in range(20):
            t = base_t + i * 0.1
            clean_records.append({
                "local_ts": t,
                "event_type": "ticker",
                "symbol": "069500",
                "data": {"price": 35000.0 + i, "volume": 5.0, "timestamp": t - 0.03}
            })
            clean_records.append({
                "local_ts": t + 0.01,
                "event_type": "orderbook",
                "symbol": "069500",
                "data": {
                    "units": [
                        {"bid_price": 34990.0, "bid_size": 50, "ask_price": 35010.0, "ask_size": 50}
                    ]
                }
            })

        report = DataAnalyzer.analyze_records(clean_records)
        self.assertGreaterEqual(report["health_score"], 95.0)
        self.assertEqual(report["status"], "EXCELLENT")
        self.assertEqual(report["stream_integrity"]["crossed_orderbooks"], 0)
        self.assertEqual(report["stream_integrity"]["zero_or_negative_values"], 0)
        self.assertTrue(report["inference_feed_health"]["feed_ready"])

    def test_data_analyzer_detect_anomalies(self):
        """호가 역전, AI 피처 NaN, 틱 갭, 스파이크 등 인위적 결함 감지 테스트"""
        base_t = time.time()
        dirty_records = [
            # 1. 정상 틱
            {"local_ts": base_t, "event_type": "ticker", "symbol": "069500", "data": {"price": 35000.0, "volume": 1.0}},
            # 2. 5초 틱 갭 후 비정상 스파이크 (+10%)
            {"local_ts": base_t + 5.5, "event_type": "ticker", "symbol": "069500", "data": {"price": 38500.0, "volume": 10.0}},
            # 3. 호가 역전 (매수 39000 >= 매도 38000)
            {"local_ts": base_t + 5.6, "event_type": "orderbook", "symbol": "069500", "data": {
                "units": [{"bid_price": 39000.0, "bid_size": 10, "ask_price": 38000.0, "ask_size": 10}]
            }},
            # 4. AI 피처 NaN 주입
            {"local_ts": base_t + 5.7, "event_type": "ai_inference", "symbol": "069500", "data": {
                "features": {"f1": 1.2, "f2": float("nan"), "f3": 0.5}
            }}
        ]

        report = DataAnalyzer.analyze_records(dirty_records, expected_features=["f1", "f2", "f3", "f4_missing"])

        # 감지 확인
        self.assertEqual(report["stream_integrity"]["crossed_orderbooks"], 1)
        self.assertGreaterEqual(report["stream_integrity"]["tick_gaps_count"], 1)
        self.assertGreaterEqual(report["stream_integrity"]["price_spikes_count"], 1)
        self.assertEqual(report["inference_feed_health"]["nan_inf_features_detected"], 1)
        self.assertEqual(report["inference_feed_health"]["missing_feature_counts"], 1)
        self.assertFalse(report["inference_feed_health"]["feed_ready"])
        self.assertLess(report["health_score"], 70.0)

        # 리포터 포맷팅 확인
        console_out = DataReporter.format_console_report(report, "069500")
        self.assertIn("호가 역전", console_out)
        self.assertIn("NaN / Inf 결측", console_out)


if __name__ == "__main__":
    unittest.main()

