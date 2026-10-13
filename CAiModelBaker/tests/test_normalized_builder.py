# -*- coding: utf-8 -*-
"""
테스트: NormalizedBuilder (단일 자산 표준 정규화, 마이크로스트럭처 & 틱 스크러빙 검증)
"""

import os
import pytest
import numpy as np
import pandas as pd

from CAiModelBaker.core.normalized_builder import (
    build_normalized_10s_stock,
    build_normalized_candle_stock,
    build_normalized_macro,
    generate_market_grid,
    build_normalized_us_stock,
    build_normalized_crypto,
    build_all_active_normalized,
    upsert_normalized_daily,
    upsert_all_active_normalized,
)
from shared.data_library import paths as dpaths
from shared.data_library.manifest_manager import ManifestManager


def test_build_normalized_10s_stock_schema_and_microstructure():
    """069500 10초 정규화 Parquet 빌드 및 표준 스키마, 마이크로 지표, 동시호가 플래그 검증"""
    out_path = build_normalized_10s_stock("069500")
    assert os.path.exists(out_path)

    # 1. Parquet 로드
    df = pd.read_parquet(out_path)
    # KRX 10초봉 구조 불변식: 1일치 = 09:00:00~15:30:00 = 2,341행 (하드코딩 금지)
    BARS_PER_DAY = 2341  # 6.5시간 × 3600 / 10 + 1 (시작점 포함)
    trading_days = df["timestamp"].dt.date.nunique()
    assert trading_days >= 1, "최소 1거래일 이상이어야 합니다."
    assert len(df) == BARS_PER_DAY * trading_days, (
        f"총 행 수({len(df)})가 거래일({trading_days}) × {BARS_PER_DAY}행 구조에 맞지 않습니다."
    )

    # 2. 표준 컬럼 존재 검증
    canonical_cols = [
        "timestamp", "open", "high", "low", "close", "volume",
        "trade_amount", "trade_count", "bid_volume", "ask_volume",
        "vwap", "ofi", "rv", "is_large_trade", "is_auction"
    ]
    for c in canonical_cols:
        assert c in df.columns, f"표준 컬럼 {c} 누락"

    # 3. 데이터 타입 검증 (float32, int32 경량화)
    assert df["close"].dtype == np.float32
    assert df["volume"].dtype == np.float32
    assert df["trade_count"].dtype == np.int32
    assert df["is_auction"].dtype == np.int32

    # 4. 마이크로 지표 유효성 검증
    # VWAP: 거래량이 있는 구간에서는 Low <= VWAP <= High 범위
    vol_mask = df["volume"] > 0
    df_vol = df[vol_mask]
    assert (df_vol["vwap"] >= df_vol["low"] * 0.999).all()
    assert (df_vol["vwap"] <= df_vol["high"] * 1.001).all()

    # OFI: [-1.0, 1.0] 범위
    assert (df["ofi"] >= -1.0).all()
    assert (df["ofi"] <= 1.0).all()

    # RV: Parkinson 실현 변동성 >= 0.0
    assert (df["rv"] >= 0.0).all()

    # 5. KRX 15:20~15:30 동시호가 플래그 검증
    auction_times = df[df["is_auction"] == 1]["timestamp"].dt.time
    assert not auction_times.empty
    assert auction_times.min() > pd.Timestamp("15:20:00").time()
    assert auction_times.max() <= pd.Timestamp("15:30:00").time()

    # 6. 하위 호환성 필드 검증
    assert "target_close" in df.columns
    assert "target_volume" in df.columns
    assert "upbit_close" in df.columns

    # 7. 매니페스트 검증
    ok, msg = ManifestManager.verify_manifest(out_path)
    assert ok is True


def test_build_normalized_candle_stock_vwap():
    """005930 1분봉 정규화 및 VWAP, float32 스키마 검증"""
    out_path = build_normalized_candle_stock("005930", interval="1m", end_date="2026-09-02")
    assert os.path.exists(out_path)

    df = pd.read_parquet(out_path)
    assert not df.empty
    assert "vwap" in df.columns
    assert df["close"].dtype == np.float32

    ok, msg = ManifestManager.verify_manifest(out_path)
    assert ok is True


def test_tick_scrubbing_outlier_filter(tmp_path, monkeypatch):
    """비정상 틱(0원/음수/스파이크)이 스크러빙되어 정상 제거되는지 검증"""
    # 이 테스트의 관심사는 스크러빙이다. 틱 순서 감사는 1분봉이 없으면 빌드를 멈추므로(fail-closed) 여기선 통과시킨다.
    from CAiModelBaker.core import normalized_builder as nb
    monkeypatch.setattr(nb, "_drop_unordered_tick_days", lambda df, sym, log: df)
    # 임의의 틱 CSV 생성
    dates = pd.date_range("2026-09-10 09:00:00", periods=20, freq="1s")
    raw_df = pd.DataFrame({
        "timestamp": dates,
        "upbit_close": [
            10000.0, 10010.0, 10020.0,
            0.0,       # 0원 이상치
            -500.0,    # 음수 이상치
            10015.0,
            12000.0,   # +20% 스파이크 이상치
            10010.0, 10020.0, 10025.0,
            10020.0, 10030.0, 10020.0, 10010.0, 10005.0,
            10010.0, 10015.0, 10020.0, 10025.0, 10030.0
        ],
        "upbit_volume": [10.0] * 20,
        "upbit_trade_price": [100000.0] * 20,
    })
    raw_csv = os.path.join(tmp_path, "TEST_tick.csv")
    raw_df.to_csv(raw_csv, index=False)

    out_pq = os.path.join(tmp_path, "TEST_10s.parquet")
    build_normalized_10s_stock("TEST", raw_tick_path=raw_csv, output_parquet_path=out_pq)

    assert os.path.exists(out_pq)
    df_res = pd.read_parquet(out_pq)
    # 스파이크(12000) 및 0원/음수 체결가가 최종 캔들에 반영되지 않아야 함
    assert (df_res["close"] < 11000.0).all()
    assert (df_res["close"] > 0.0).all()


def test_generate_market_grid():
    """KRX, US, CRYPTO 시장별 캘린더 그리드 생성 및 세션 시간 검증"""
    dates = [pd.Timestamp("2026-09-10"), pd.Timestamp("2026-09-11")]

    # 1. KRX 세션 (09:00 ~ 15:30 -> 1일 391개 타임스탬프)
    df_krx = generate_market_grid("KRX", dates, interval="1m")
    assert len(df_krx) == 391 * 2
    assert df_krx["timestamp"].iloc[0] == pd.Timestamp("2026-09-10 09:00:00")
    assert df_krx["timestamp"].iloc[390] == pd.Timestamp("2026-09-10 15:30:00")

    # 2. US 세션 (22:30 ~ 05:00 -> 1일 391개 타임스탬프)
    df_us = generate_market_grid("US", dates, interval="1m")
    assert len(df_us) == 391 * 2
    assert df_us["timestamp"].iloc[0] == pd.Timestamp("2026-09-10 22:30:00")
    assert df_us["timestamp"].iloc[390] == pd.Timestamp("2026-09-11 05:00:00")

    # 3. CRYPTO 세션 (24/7 연중무휴)
    df_crypto = generate_market_grid("CRYPTO", dates, interval="1h")
    # 2026-09-10 00:00 ~ 2026-09-11 23:59 -> 48시간
    assert len(df_crypto) == 48


@pytest.mark.skip(reason="정리된 구 모델/변경된 shared 설정 기준 테스트 - 현행 설정에 맞게 재작성 필요")
def test_build_normalized_us_stock():
    """미국 주식/ETF(QQQ) 정규화 빌드 및 매니페스트 검증"""
    out_path = build_normalized_us_stock("QQQ", interval="1m")
    assert os.path.exists(out_path)

    df = pd.read_parquet(out_path)
    assert not df.empty
    for col in ["timestamp", "open", "high", "low", "close", "volume", "trade_amount", "vwap"]:
        assert col in df.columns
    assert df["close"].dtype == np.float32

    ok, msg = ManifestManager.verify_manifest(out_path)
    assert ok is True


def test_build_normalized_crypto():
    """가상자산(KRW-BTC) 24/7 정규화 빌드 및 매니페스트 검증"""
    out_path = build_normalized_crypto("KRW-BTC", interval="10m")
    assert os.path.exists(out_path)

    df = pd.read_parquet(out_path)
    assert not df.empty
    for col in ["timestamp", "open", "high", "low", "close", "volume", "trade_amount", "vwap"]:
        assert col in df.columns
    assert df["close"].dtype == np.float32

    ok, msg = ManifestManager.verify_manifest(out_path)
    assert ok is True


def test_build_all_active_normalized():
    """SourceMappingManager 연동 17대 전 자산 일괄 정규화 파이프라인 검증"""
    res = build_all_active_normalized()
    assert res["status"] == "ok"
    assert res["success_count"] >= 15
    assert res["failed_count"] == 0
    assert len(res["artifacts"]) >= 16


def test_upsert_normalized_daily(tmp_path):
    """날짜 단위 증분 업서트 무결성 및 스킵 최적화 검증"""
    orig_path = dpaths.normalized_path("candles_10s", "069500_10s.parquet")
    assert os.path.exists(orig_path)

    df_full = pd.read_parquet(orig_path)
    total_len = len(df_full)

    # 1. 마지막 1거래일(2,341행)을 제거한 가상 과거 Parquet 생성
    test_pq = str(tmp_path / "069500_10s.parquet")
    df_truncated = df_full.iloc[:-2341].copy()
    df_truncated.to_parquet(test_pq, index=False)
    ManifestManager.create_manifest(test_pq, provider="test", data_tier="normalized", asset_type="candles_10s", symbol="069500")

    # 2. 증분 업서트 실행 -> 마지막 1거래일이 복원되어야 함
    res = upsert_normalized_daily("069500", interval="10s", output_parquet_path=test_pq)
    assert res["status"] == "upserted"
    assert res["added_rows"] > 0
    assert res["total_rows"] == total_len

    df_restored = pd.read_parquet(test_pq)
    assert len(df_restored) == total_len

    # 3. 매니페스트 감사 검증
    ok, msg = ManifestManager.verify_manifest(test_pq)
    assert ok is True

    # 4. 이미 최신 상태에서 재호출 시 skip 최적화 검증
    res_skip = upsert_normalized_daily("069500", interval="10s", output_parquet_path=test_pq)
    assert res_skip["status"] == "skipped"
    assert res_skip["added_rows"] == 0


def test_manifest_health_summary():
    """사이드카 매니페스트 6대 헬스 메트릭 자동 감사 검증"""
    target_pq = dpaths.normalized_path("candles_10s", "069500_10s.parquet")
    manifest = ManifestManager.load_manifest(target_pq)
    assert manifest is not None
    assert "health_summary" in manifest

    hs = manifest["health_summary"]
    # KRX 10초봉 구조 불변식: total_bars = 2,341 × 거래일 수 (하드코딩 금지)
    BARS_PER_DAY = 2341
    total_bars = hs["total_bars"]
    assert total_bars > 0, "total_bars가 0이면 데이터 없음"
    assert total_bars % BARS_PER_DAY == 0, (
        f"total_bars({total_bars})가 1일치({BARS_PER_DAY}행) 단위의 배수가 아닙니다."
    )
    assert hs["null_ratio"] == 0.0
    assert hs["has_microstructure"] is True
    assert hs["price_range"] is not None
    assert len(hs["price_range"]) == 2
    assert hs["first_timestamp"] is not None
    assert hs["last_timestamp"] is not None

