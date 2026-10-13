import os
import tempfile
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from CAiModelBaker.core.crypto_collector import sync_binance_funding_rate, sync_binance_oi
from CAiModelBaker.core.normalized_builder import build_normalized_crypto


def test_crypto_futures_derivative_separation_and_normalization():
    """가상자산 선물 1단계 파생 원천 분리 및 2단계 Silver 시점 결합 검증"""
    with tempfile.TemporaryDirectory() as td:
        # 1. 1단계 순수 캔들 원천 CSV 모의 생성
        candle_csv = os.path.join(td, "TEST-PERP_15m.csv")
        ts_range = pd.date_range("2026-09-01 00:00:00", "2026-09-02 00:00:00", freq="15min")
        df_candles = pd.DataFrame({
            "timestamp": ts_range.strftime("%Y-%m-%d %H:%M:%S"),
            "open": 60000.0,
            "high": 60100.0,
            "low": 59900.0,
            "close": 60050.0,
            "volume": 10.0,
            "trade_amount": 600500.0,
        })
        df_candles.to_csv(candle_csv, index=False)

        # 2. 1단계 독립 펀딩비 원천 CSV 모의 생성 (8시간 간격)
        fr_csv = os.path.join(td, "TEST-PERP_funding_rate.csv")
        fr_ts = pd.date_range("2026-08-31 17:00:00", "2026-09-02 17:00:00", freq="8h")
        df_fr = pd.DataFrame({
            "timestamp": fr_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "funding_rate": [0.0001, 0.00015, 0.0002, 0.0001, 0.00008, 0.00012, 0.0001],
            "mark_price": 60000.0,
        })
        df_fr.to_csv(fr_csv, index=False)

        # 3. 1단계 독립 미결제약정 원천 CSV 모의 생성 (5분 간격)
        oi_csv = os.path.join(td, "TEST-PERP_oi.csv")
        oi_ts = pd.date_range("2026-09-01 00:00:00", "2026-09-02 00:00:00", freq="5min")
        df_oi = pd.DataFrame({
            "timestamp": oi_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "oi": 85000.0,
            "oi_value": 510000000.0,
        })
        df_oi.to_csv(oi_csv, index=False)

        # 4. 2단계 Silver 정규화 실행 (asof merge 검증)
        out_parquet = os.path.join(td, "TEST-PERP_15m.parquet")
        res_path = build_normalized_crypto(
            symbol="TEST-PERP",
            interval="15m",
            raw_candle_path=candle_csv,
            output_parquet_path=out_parquet,
        )

        assert os.path.exists(res_path)
        df_res = pd.read_parquet(res_path)

        # 5. 검증: 파생 컬럼들이 완벽하게 결합되었는지 확인
        assert "funding_rate" in df_res.columns
        assert "oi" in df_res.columns
        assert "oi_value" in df_res.columns
        assert len(df_res) == len(df_candles)

        # 펀딩비가 결측 없이 backward asof로 채워졌는지 확인
        assert df_res["funding_rate"].notna().all()
        # 미결제약정이 결측 없이 채워졌는지 확인
        assert df_res["oi"].notna().all()
        assert df_res["oi"].iloc[0] == 85000.0
