# -*- coding: utf-8 -*-
"""
CTradeSystem - Test Manifest Manager
====================================
사이드카 매니페스트 생성 및 0.01초 무결성 검증, 데이터 변조 감지 테스트.
"""

import os
import tempfile
import pandas as pd
import pytest

from shared.data_library.manifest_manager import ManifestManager


def test_manifest_creation_and_verification():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "A069500_tick.csv")
        
        # 1. 2026-09-11 정상 381분 틱 모의 생성 (09:00~15:19 380분 + 15:30 종가 1분 = 381분)
        times_continuous = pd.date_range("2026-09-11 09:00:00", "2026-09-11 15:19:00", freq="1min")
        times_close = pd.DatetimeIndex(["2026-09-11 15:30:00"])
        times = times_continuous.append(times_close)
        df = pd.DataFrame({
            "datetime": times.strftime("%Y-%m-%d %H:%M:%S"),
            "open": 30000,
            "high": 30100,
            "low": 29900,
            "close": 30050,
            "volume": 100
        })
        df.to_csv(test_file, index=False)

        # 2. 매니페스트 생성
        manifest = ManifestManager.create_manifest(
            data_path=test_file,
            provider="creon",
            data_tier="bronze",
            asset_type="tick",
            symbol="069500",
            market="KRX",
            notes="테스트 틱 데이터"
        )

        assert manifest["symbol"] == "069500"
        assert manifest["provider"] == "creon"
        assert manifest["data_tier"] == "bronze"
        assert manifest["integrity"]["row_count"] == 381
        assert manifest["krx_validation"]["fully_intact"] is True
        assert manifest["krx_validation"]["min_minutes_per_day"] == 381
        assert len(manifest["integrity"]["sha256"]) == 64

        # 3. 무결성 검증 통과 확인
        ok, msg = ManifestManager.verify_manifest(test_file, strict_hash=True)
        assert ok is True
        assert msg == "OK"

        # 4. 파일 변조(훼손) 시 즉각 감지 확인
        with open(test_file, "a") as f:
            f.write("2026-09-11 15:31:00,30050,30050,30050,30050,1\n")

        ok_tampered, msg_tampered = ManifestManager.verify_manifest(test_file, strict_hash=True)
        assert ok_tampered is False
        assert "불일치" in msg_tampered


def test_manifest_missing_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "unregistered.csv")
        df = pd.DataFrame({"datetime": ["2026-09-11 09:00:00"], "close": [100]})
        df.to_csv(test_file, index=False)

        ok, msg = ManifestManager.verify_manifest(test_file)
        assert ok is False
        assert "매니페스트 누락" in msg


def test_daily_file_is_not_judged_by_minute_completeness():
    """일봉(하루 1분)에 KRX 380분 검사를 적용하면 모든 날이 '이상'으로 박제된다(삼성전자 일봉 236KB 오판 목록)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        f = os.path.join(tmpdir, "A005930_1d.csv")
        pd.DataFrame({"timestamp": [f"2026-09-{d:02d} 00:00:00" for d in range(1, 21)], "close": range(20)}).to_csv(f, index=False)
        m = ManifestManager.generate_and_save_manifest(f, provider="creon")
        v = m["krx_validation"]
        assert v["applicable"] is False and v["anomaly_dates"] == [] and v["trading_days"] == 20
        assert m["market"] == "KRX" and m["timezone"] == "Asia/Seoul"


def test_us_daily_manifest_records_market_and_timezone():
    with tempfile.TemporaryDirectory() as tmpdir:
        f = os.path.join(tmpdir, "AAPL_1d.csv")
        pd.DataFrame({"timestamp": ["2026-10-12 00:00:00", "2026-10-13 00:00:00"], "close": [1.0, 2.0]}).to_csv(f, index=False)
        m = ManifestManager.generate_and_save_manifest(f, provider="yahoo", market="US", timezone_name="America/New_York")
        assert m["market"] == "US" and m["timezone"] == "America/New_York"
        assert m["krx_validation"]["applicable"] is False and m["krx_validation"]["trading_days"] == 2
