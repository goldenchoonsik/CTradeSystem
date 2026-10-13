# -*- coding: utf-8 -*-
"""
Smart Skip 및 Data Vault 수집률 보정 연동 테스트
================================================
1. download_upbit_usdt_day 상장일 이전 즉시 스킵 검증
2. download_binance_usdc_day 가용일 이전 즉시 스킵 검증
3. validate_downloaded_data 상장 전(pre-listing) 분리 및 유효 수집률 보정 검증
"""

import os
import pytest
import time
from CAiModelBaker.core.data_manager import (
    download_upbit_usdt_day,
    download_binance_usdc_day,
    validate_downloaded_data
)


def test_download_upbit_usdt_smart_skip():
    """상장일 이전 날짜는 네트워크 요청 없이 0.05초 이내에 즉시 None 반환 검증"""
    config = {'SYMBOL_UPBIT': 'KRW-BTC', 'INTERVAL': '10m'}
    
    t0 = time.time()
    # 2023-06-23은 상장일(2024-06-07) 이전
    res = download_upbit_usdt_day(config, "2023-06-23", interval="10m")
    elapsed = time.time() - t0
    
    assert res is None
    # 네트워크 왕복(수백ms) 없이 즉시 스킵되어야 함
    assert elapsed < 0.05, f"스킵 처리가 너무 느림: {elapsed}초"


def test_download_binance_usdc_smart_skip():
    """가용일 이전 날짜는 네트워크 요청 없이 즉시 None 반환 검증"""
    config = {'SYMBOL_BINANCE': 'BTCUSDT', 'INTERVAL': '10m'}
    
    t0 = time.time()
    # 2022-01-01은 바이낸스 USDC 가용일(2023-01-01) 이전
    res = download_binance_usdc_day(config, "2022-01-01", interval="10m")
    elapsed = time.time() - t0
    
    assert res is None
    assert elapsed < 0.05


def test_validate_downloaded_data_governance_metadata():
    """validate_downloaded_data 실행 시 source_meta 및 pre_listing_ranges가 정상 계산되는지 검증"""
    config = {
        'SYMBOL_UPBIT': 'KRW-BTC',
        'ARCHIVE_START_YM': '2024-01',
        'ARCHIVE_END_YM': '2024-06',
        'INTERVAL': '10m'
    }
    
    status = validate_downloaded_data(config, start_ym='2024-01', end_ym='2024-06', interval='10m')
    assert "items" in status
    items = status["items"]
    
    # upbit_usdt 항목 점검
    assert "upbit_usdt" in items
    usdt_item = items["upbit_usdt"]
    
    # 2024-01-01 ~ 2024-06-06 구간은 상장 전 구간으로 감지되어야 함
    assert usdt_item["pre_listing_count"] > 0
    assert "source_meta" in usdt_item
    assert usdt_item["source_meta"]["available_start_date"] == "2024-06-07"
    assert usdt_item["source_meta"]["fallback_policy"] == "ROLLING_INVERSE_RATE"


def test_apply_fallback_dataframe_and_quality_report():
    """데이터프레임에 카탈로그 정책 기반 동적 폴백 적용 및 품질 리포트 산출 검증"""
    import pandas as pd
    import numpy as np
    from shared.data_library.data_catalog_manager import DataCatalogManager

    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=10, freq="10min"),
        "upbit_close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0],
        "binance_spot_close": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
        "upbit_usdt_close": [np.nan, np.nan, np.nan, 1300.0, 1310.0, np.nan, 1320.0, 1330.0, np.nan, 1350.0],
        "binance_futures_oi": [np.nan, 500.0, np.nan, 520.0, np.nan, 550.0, 560.0, np.nan, 580.0, 600.0],
    })

    imputed_df, report = DataCatalogManager.apply_fallback_to_dataframe(df)

    # 1. 보간 결과 확인
    #    · upbit_usdt_close: ROLLING_INVERSE_RATE 로 head 까지 산출 가능 → 전량 보간
    #    · binance_futures_oi: FORWARD_FILL 이므로 **최초 관측 이전(head) 은 NaN 유지**
    #      [T-3] 종전에는 ffill().bfill() 이 head 를 미래값으로 역류시켜 0건이었다 (Lookahead).
    assert imputed_df["upbit_usdt_close"].isna().sum() == 0
    assert imputed_df["binance_futures_oi"].isna().sum() == 1, \
        "FORWARD_FILL 은 head 결측을 미래값으로 채우지 않아야 함 (T-3 Point-in-Time)"
    assert pd.isna(imputed_df["binance_futures_oi"].iloc[0])

    # 2. 품질 리포트 검증
    assert "upbit_usdt_close" in report
    usdt_rep = report["upbit_usdt_close"]
    assert usdt_rep["source_id"] == "UPBIT_USDT"
    assert usdt_rep["raw_missing_count"] == 5
    assert usdt_rep["imputed_count"] == 5
    assert usdt_rep["remaining_missing"] == 0
    assert usdt_rep["missing_ratio_pct"] == 50.0
    assert usdt_rep["policy"] == "ROLLING_INVERSE_RATE"
    assert usdt_rep["status"] == "IMPUTED_CLEAN"

    assert "binance_futures_oi" in report
    oi_rep = report["binance_futures_oi"]
    assert oi_rep["source_id"] == "BINANCE_FUTURES_OI"
    assert oi_rep["raw_missing_count"] == 4
    assert oi_rep["imputed_count"] == 3, "head 1건은 bfill 금지로 보간되지 않음 (T-3)"
    assert oi_rep["remaining_missing"] == 1
    assert oi_rep["policy"] == "FORWARD_FILL"
    assert oi_rep["status"] == "PARTIAL_MISSING"


def test_api_data_vault_quality_endpoint():
    """FastAPI TestClient를 통해 /api/data_vault/quality 엔드포인트 응답 검증"""
    from fastapi.testclient import TestClient
    from CAiModelBaker.web_dashboard.main import app

    client = TestClient(app)
    res = client.get("/api/data_vault/quality")
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "ok", f"API Error: {data}"
    assert "exists" in data
    if data["exists"]:
        assert "quality_report" in data
        assert isinstance(data["quality_report"], dict)

