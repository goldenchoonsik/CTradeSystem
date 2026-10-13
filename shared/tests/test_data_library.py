# -*- coding: utf-8 -*-
"""
Unit Tests for shared/data_library (Data & Feature Library)
==========================================================
1. IngestionManager: Zero-loss atomic writes, continuous ingestion, deduplication
2. CandleVault: Multi-interval resampling (1m -> 5m, 15m, 1d) & caching
3. FeatureVault: Independent feature calculation & caching
4. FeatureComposer: On-demand reference symbols composition within 0.5s
"""

import os
import shutil
import tempfile
import time
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

from shared.data_library.ingestion_manager import IngestionManager
from shared.data_library.candle_vault import CandleVault
from shared.data_library.feature_vault import FeatureVault
from shared.data_library.feature_composer import FeatureComposer

KST = timezone(timedelta(hours=9))


class TestDataLibrary(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ctrade_test_datalib_")
        self.candle_cache_dir = os.path.join(self.test_dir, "cache_candles")
        self.feature_cache_dir = os.path.join(self.test_dir, "cache_features")

        self.candle_vault = CandleVault(cache_dir=self.candle_cache_dir)
        self.feature_vault = FeatureVault(candle_vault=self.candle_vault, cache_dir=self.feature_cache_dir)
        self.composer = FeatureComposer(candle_vault=self.candle_vault, feature_vault=self.feature_vault)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_ingestion_manager_deduplication_and_zero_loss(self):
        """동일 및 중복 구간 신규 데이터 유입 시 무손실 축적 및 정밀 디둡 검증"""
        file_path = os.path.join(self.test_dir, "test_raw.parquet")

        t0 = datetime(2026, 9, 3, 9, 0, 0, tzinfo=KST)
        # 1차 배치: 10개 행 (09:00 ~ 09:09)
        times_1 = [t0 + timedelta(minutes=i) for i in range(10)]
        df_1 = pd.DataFrame({
            "timestamp": times_1,
            "target_close": [35000.0 + i * 10 for i in range(10)],
            "target_volume": [100.0] * 10
        })

        cnt_1 = IngestionManager.append_and_deduplicate(file_path, df_1, dedup_subset=["timestamp"])
        self.assertEqual(cnt_1, 10)

        # 2차 배치: 5개 중복 (09:05 ~ 09:09 수정값) + 5개 신규 (09:10 ~ 09:14)
        times_2 = [t0 + timedelta(minutes=i) for i in range(5, 15)]
        df_2 = pd.DataFrame({
            "timestamp": times_2,
            "target_close": [99999.0] * 5 + [36000.0 + i * 10 for i in range(5)],  # 09:05~09:09 값 갱신
            "target_volume": [200.0] * 10
        })

        cnt_2 = IngestionManager.append_and_deduplicate(file_path, df_2, dedup_subset=["timestamp"])
        # 총 15개여야 함 (기존 10개 중 5개 갱신 + 신규 5개)
        self.assertEqual(cnt_2, 15)

        loaded = IngestionManager.load_data(file_path)
        self.assertEqual(len(loaded), 15)
        # 중복 구간은 신규 값(99999.0)으로 갱신되었는지 확인 (keep='last')
        row_at_0905 = loaded[loaded["timestamp"] == pd.to_datetime(t0 + timedelta(minutes=5))].iloc[0]
        self.assertEqual(row_at_0905["target_close"], 99999.0)
        # 시간순 정렬 확인
        self.assertTrue(loaded["timestamp"].is_monotonic_increasing)

    def test_candle_vault_resampling_integrity(self):
        """1분봉으로부터 5분봉 및 15분봉 리샘플링 시 시고저종(OHLCV) 수학적 무결성 검증"""
        t0 = datetime(2026, 9, 3, 9, 0, 0, tzinfo=KST)
        times = [t0 + timedelta(minutes=i) for i in range(15)]
        # 15분 동안의 분봉 생성
        # 09:00~09:04 (첫 5분): 시가 100, 고가 120, 저가 90, 종가 110, 누적볼륨 500
        closes = [105, 120, 90, 100, 110] + [115, 125, 105, 110, 120] + [122, 130, 118, 125, 128]
        opens = [100, 105, 120, 90, 100] + [110, 115, 125, 105, 110] + [120, 122, 130, 118, 125]
        highs = [max(o, c) + 5 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 5 for o, c in zip(opens, closes)]
        vols = [100.0] * 15

        df_1m = pd.DataFrame({
            "timestamp": times,
            "target_open": opens,
            "target_high": highs,
            "target_low": lows,
            "target_close": closes,
            "target_volume": vols
        })

        # 5분봉 리샘플링
        df_5m = self.candle_vault.resample_candles(df_1m, "5m")
        self.assertEqual(len(df_5m), 3)

        # 첫 5분봉 (09:00) 검증:
        # Open은 09:00의 open(100), Close는 09:04의 close(110), Volume은 합(500)
        c0 = df_5m.iloc[0]
        self.assertEqual(c0["target_open"], 100)
        self.assertEqual(c0["target_close"], 110)
        self.assertEqual(c0["target_volume"], 500.0)
        self.assertEqual(c0["target_high"], max(highs[:5]))
        self.assertEqual(c0["target_low"], min(lows[:5]))

    def test_feature_vault_independent_caching(self):
        """특정 종목의 지표가 독립적으로 계산되고 캐시에서 즉시 반환되는지 검증"""
        # 임의의 캔들 캐시 등록
        t0 = datetime(2026, 9, 3, 9, 0, 0, tzinfo=KST)
        times = [t0 + timedelta(minutes=i) for i in range(30)]
        df_candles = pd.DataFrame({
            "timestamp": times,
            "target_open": [35000.0 + i * 10 for i in range(30)],
            "target_high": [35020.0 + i * 10 for i in range(30)],
            "target_low": [34990.0 + i * 10 for i in range(30)],
            "target_close": [35010.0 + i * 10 for i in range(30)],
            "target_volume": [100.0 + i for i in range(30)]
        })
        self.candle_vault.append_candles("069500", "1m", df_candles)

        # target_bb_pctb 피처 계산 요청
        block = self.feature_vault.get_feature_block(
            symbol="069500",
            interval="1m",
            feature_ids=["target_bb_pctb"]
        )
        self.assertIn("target_bb_pctb", block.columns)
        self.assertEqual(len(block), 30)

        # 두 번째 호출 시 캐시에서 즉시 반환 (계산 없이 초고속)
        t_start = time.perf_counter()
        block_cached = self.feature_vault.get_feature_block(
            symbol="069500",
            interval="1m",
            feature_ids=["target_bb_pctb"]
        )
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        self.assertLess(elapsed_ms, 50.0)  # 50ms 미만 반환
        pd.testing.assert_series_equal(block["target_bb_pctb"], block_cached["target_bb_pctb"])

    def test_feature_composer_on_demand_join_benchmark(self):
        """타겟 베이스 피처셋에 대장주 시세를 결합 시 0.5초(500ms) 이내에 조립 완료되는지 검증"""
        # 1. 타겟 베이스 1,000개 캔들
        t0 = datetime(2026, 9, 3, 9, 0, 0, tzinfo=KST)
        times = [t0 + timedelta(minutes=i) for i in range(1000)]
        target_df = pd.DataFrame({
            "timestamp": times,
            "target_open": [35000.0] * 1000,
            "target_high": [35050.0] * 1000,
            "target_low": [34950.0] * 1000,
            "target_close": [35000.0] * 1000,
            "target_volume": [100.0] * 1000,
            "my_base_feature": np.random.randn(1000)
        })

        # 2. 대장주 (SK하이닉스 000660) 캔들 등록
        ref_df = pd.DataFrame({
            "timestamp": times,
            "target_open": [205000.0] * 1000,
            "target_high": [206000.0] * 1000,
            "target_low": [204000.0] * 1000,
            "target_close": [205000.0] * 1000,
            "target_volume": [50.0] * 1000
        })
        self.candle_vault.append_candles("000660", "1m", ref_df)

        # 3. 온디맨드 조립 실행 및 시간 측정
        ref_specs = [
            {"symbol": "000660", "interval": "1m"}
        ]

        t_start = time.perf_counter()
        composed_df = self.composer.compose(target_df, ref_specs, target_symbol="069500", target_interval="1m")
        elapsed_sec = time.perf_counter() - t_start

        # 500ms(0.5초) 미만인지 확인
        self.assertLess(elapsed_sec, 0.5, f"조립 소요 시간이 0.5초를 초과했습니다: {elapsed_sec:.4f}s")

        # 결합된 컬럼 확인
        self.assertIn("ref_000660_close", composed_df.columns)
        self.assertIn("ref_000660_volume", composed_df.columns)
        self.assertIn("ref_000660_bid_vol", composed_df.columns)
        self.assertIn("my_base_feature", composed_df.columns)
        self.assertEqual(len(composed_df), 1000)
        self.assertEqual(composed_df["ref_000660_close"].iloc[0], 205000.0)


if __name__ == "__main__":
    unittest.main()
