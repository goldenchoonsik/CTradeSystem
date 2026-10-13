import pytest
"""
shared/tests/test_live_feed_prober.py
실시간 피드 준비성 검증기 (LiveFeedProber) 단위 테스트
"""

from datetime import datetime
import zoneinfo
from shared.data_library.live_feed_prober import LiveFeedProber, KST


class TestLiveFeedProber:
    def test_market_open_logic(self):
        # 1. 화요일 오전 10:00 (장중)
        t_open = datetime(2026, 9, 1, 10, 0, 0, tzinfo=KST)
        assert LiveFeedProber.is_market_open(t_open) is True

        # 2. 화요일 밤 22:00 (장 마감)
        t_night = datetime(2026, 9, 1, 22, 0, 0, tzinfo=KST)
        assert LiveFeedProber.is_market_open(t_night) is False

        # 3. 토요일 오후 14:00 (주말)
        t_weekend = datetime(2026, 9, 5, 14, 0, 0, tzinfo=KST)
        assert LiveFeedProber.is_market_open(t_weekend) is False

    def test_probe_reads_provider_from_the_ledger(self):
        """[T-6] 수집처는 원장이 말한다 — 심볼 이름으로 추측하지 않는다."""
        assert LiveFeedProber.probe_symbol("KRW-BTC", market_type="CRYPTO")["provider"] == "UPBIT"
        assert LiveFeedProber.probe_symbol("US_TECH_100")["provider"] == "CTRADER"
        assert LiveFeedProber.probe_symbol("069500", market_type="KRX_ETF")["provider"] == "KIS"

    def test_probe_verdict_follows_measured_freshness(self, monkeypatch):
        """[T-6] '수신 중' 은 실측 결과여야 한다.

        종전에는 심볼 목록을 보고 확인 없이 "연동 정상 / LIVE_STREAMING / latency 15ms" 를
        돌려줬다. 실시간 구독 코드가 없는 자산까지 초록 배지로 표시된 원인이다.
        """
        from datetime import timedelta

        monkeypatch.setattr(LiveFeedProber, "is_ctrade_running", staticmethod(lambda: False))

        # 방금 들어온 데이터 → 수신 중
        fresh = (datetime.now(KST) - timedelta(seconds=5), "1m")
        monkeypatch.setattr(LiveFeedProber, "_artifact_freshness", staticmethod(lambda s: fresh))
        res = LiveFeedProber.probe_symbol("KRW-BTC", market_type="CRYPTO_SPOT")
        assert res["status_code"] == "LIVE_OK", res
        assert res["grade"] == "LIVE_STREAMING", res

        # 오래된 데이터 → 수신 중이라고 말하지 않는다
        stale = (datetime.now(KST) - timedelta(days=2), "1m")
        monkeypatch.setattr(LiveFeedProber, "_artifact_freshness", staticmethod(lambda s: stale))
        res = LiveFeedProber.probe_symbol("KRW-BTC", market_type="CRYPTO_SPOT")
        assert res["status_code"] == "FEED_STALE", res
        assert res["grade"] != "LIVE_STREAMING", res

    def test_probe_flags_missing_runtime_implementation(self, monkeypatch):
        """원장이 실시간을 선언해도 런타임 구현이 없으면 '연구 전용' 이어야 한다."""
        monkeypatch.setattr(LiveFeedProber, "is_ctrade_running", staticmethod(lambda: False))
        monkeypatch.setattr("shared.data_library.live_feed_contract.implemented_live_providers",
                            lambda *a, **k: {"KIS"})
        res = LiveFeedProber.probe_symbol("US_TECH_100")
        assert res["readiness"] == "RESEARCH_ONLY", res
        assert res["status_code"] == "NO_RUNTIME_FEED", res

    def test_market_open_holiday(self):
        # 4. 2026-05-05 화요일 어린이날 (평일 공휴일) 10:00 KST
        t_holiday = datetime(2026, 5, 5, 10, 0, 0, tzinfo=KST)
        assert LiveFeedProber.is_market_open(t_holiday) is False

    def test_probe_krx_stock_outside_market(self):
        # 장외 시간 가정
        t_night = datetime(2026, 9, 1, 23, 0, 0, tzinfo=KST)
        # monkeypatch is_market_open
        orig_fn = LiveFeedProber.is_market_open
        LiveFeedProber.is_market_open = lambda now=None: False
        try:
            res = LiveFeedProber.probe_symbol("069500", market_type="KRX_ETF")
            assert res["readiness"] == "LIVE_READY"
            assert res["status_code"] == "MARKET_CLOSED_AUTH_OK"
            assert "장외 대기" in res["badge_text"]
        finally:
            LiveFeedProber.is_market_open = orig_fn

    def test_api_probe_feed_endpoints(self):
        from fastapi.testclient import TestClient
        from CAiModelBaker.web_dashboard.main import app
        client = TestClient(app)

        # 1. 단일 주식/ETF 프로브
        res1 = client.post('/api/data_vault/probe_feed', json={'symbol': '069500', 'market_type': 'KRX_ETF'})
        assert res1.status_code == 200
        assert res1.json()['status'] == 'ok'
        assert 'probe' in res1.json()

        # 2. 단일 크립토 프로브 — 판정은 실측에 따르므로 값이 아니라 **형식**을 검증한다
        res2 = client.post('/api/data_vault/probe_feed', json={'symbol': 'KRW-BTC', 'market_type': 'CRYPTO'})
        assert res2.status_code == 200
        probe2 = res2.json()['probe']
        assert probe2['readiness'] in ('LIVE_READY', 'RESEARCH_ONLY', 'NO_DATA')
        assert probe2['provider'] == 'UPBIT'
        assert probe2['latency_ms'] is None, '재지 않은 지연시간이 값으로 나가면 안 된다'

        # 3. 전체 종목 일괄 프로브
        res3 = client.post('/api/data_vault/probe_feed', json={})
        assert res3.status_code == 200
        assert res3.json()['status'] == 'ok'
        assert 'probes' in res3.json()

    def test_live_feed_grading_and_creon_guard(self):
        # 1. 원장 선언은 그대로 실려 나온다
        res_crypto = LiveFeedProber.probe_symbol("KRW-BTC")
        assert res_crypto["live_connection"]["provider"] == "UPBIT"

        # 2. cTrader 자산도 원장 선언 그대로 (등급은 실측에 따라 달라진다)
        res_macro = LiveFeedProber.probe_symbol("US_TECH_100")
        assert res_macro["live_connection"]["provider"] == "CTRADER"
        assert res_macro["live_connection"]["feed_type"] == "PROTOBUF_TCP"
        assert res_macro["grade"] in ("LIVE_STREAMING", "STANDBY_AUTH_OK", "RESEARCH_FEED")

        # 3. CREON provider 배정 시 즉시 차단 (규칙 6)
        from shared.data_library.source_mapping_manager import SourceMappingManager
        valid, msg = SourceMappingManager.validate_live_connection({
            "provider": "CREON",
            "feed_type": "WEBSOCKET",
            "symbol": "069500"
        })
        assert valid is False
        assert "strictly prohibited" in msg

    @pytest.mark.skip(reason="정리된 구 모델/변경된 shared 설정 기준 테스트 - 현행 설정에 맞게 재작성 필요")
    def test_probe_all_and_save_diagnostic_non_blocking(self):
        import os
        report = LiveFeedProber.probe_all_and_save_diagnostic()
        assert report["total_count"] >= 20
        assert report["streaming_count"] > 0
        assert report["failed_count"] == 0
        assert "saved_path" in report
        assert os.path.exists(report["saved_path"])

    @pytest.mark.skip(reason="정리된 구 모델/변경된 shared 설정 기준 테스트 - 현행 설정에 맞게 재작성 필요")
    def test_api_raw_store_probe_live(self):
        from fastapi.testclient import TestClient
        from CAiModelBaker.web_dashboard.main import app
        client = TestClient(app)

        # 단일 자산 probe
        res1 = client.post("/api/raw_store/probe_live", json={"symbol": "069500"})
        assert res1.status_code == 200
        assert res1.json()["status"] == "ok"
        assert "live_connection" in res1.json()["probe"]

        # 전체 진단 리포트 probe
        res2 = client.post("/api/raw_store/probe_live", json={})
        assert res2.status_code == 200
        assert res2.json()["status"] == "ok"
        assert res2.json()["diagnostic_report"]["total_count"] >= 20


