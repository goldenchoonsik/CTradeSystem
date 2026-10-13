import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
from CAiModelBaker.core.data_manager import download_coinbase_btc_day

class TestDataManagerTimestamp(unittest.TestCase):
    """CAiModelBaker 데이터 파이프라인 타임스탬프 dtype 일관성 검증 테스트"""

    @patch('CAiModelBaker.core.data_manager.requests.get')
    @patch('CAiModelBaker.core.data_manager.os.path.exists')
    @patch('CAiModelBaker.core.data_manager.os.makedirs')
    @patch('pandas.DataFrame.to_csv')
    def test_coinbase_timestamp_is_datetime64_ns(self, mock_to_csv, mock_makedirs, mock_exists, mock_get):
        """코인베이스 API 수집 시 반환되는 timestamp가 datetime64[ns] 규격을 만족하는지 검증"""
        # 캐시 파일이 존재하지 않는 상황 모킹
        mock_exists.return_value = False

        # Coinbase API 응답 모킹 (time, low, high, open, close, volume)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            [1751414400, 107000.0, 107500.0, 107100.0, 107300.0, 10.5],
            [1751414700, 107100.0, 107550.0, 107200.0, 107350.0, 11.0],
            [1751415000, 107200.0, 107600.0, 107300.0, 107400.0, 12.3],
            [1751415300, 107300.0, 107700.0, 107400.0, 107500.0, 13.0]
        ]
        mock_get.return_value = mock_resp

        config = {'SYMBOL_BINANCE': 'BTCUSDT'}
        df = download_coinbase_btc_day(config, '2025-07-02', interval='10m')

        self.assertIsNotNone(df)
        self.assertEqual(str(df['timestamp'].dtype), 'datetime64[ns]')

        # pd.merge_asof가 datetime64[ns] 기준 프레임과 오류 없이 호환되는지 검증
        base_df = pd.DataFrame({
            'timestamp': pd.date_range('2025-07-02 09:00:00', periods=5, freq='10min').astype('datetime64[ns]'),
            'binance_spot_close': [107100.0, 107200.0, 107300.0, 107400.0, 107500.0]
        })
        merged = pd.merge_asof(base_df, df, on='timestamp', direction='backward')
        self.assertEqual(str(merged['timestamp'].dtype), 'datetime64[ns]')
        self.assertIn('coinbase_spot_close', merged.columns)

    def test_subminute_tick_bucket_distribution_nat_handling(self):
        """서브미닛(10s) 버킷 분배 시 결측치(NaT) 및 빈 그룹이 있어도 정수 변환 예외 없이 정상 처리되는지 검증"""
        # 결측치(NaT) 및 다양한 틱 데이터가 포함된 DataFrame 생성
        raw_data = {
            'timestamp': ['2026-09-07 09:00:00', '2026-09-07 09:00:00', '2026-09-07 09:00:00', None, '2026-09-07 09:01:00'],
            'upbit_close': [100000.0, 100050.0, 100100.0, 100200.0, 100300.0],
            'upbit_volume': [10.0, 20.0, 30.0, 40.0, 50.0]
        }
        df = pd.DataFrame(raw_data)
        
        # 1. 타임스탬프 변환 및 NaT 제거 (data_manager 정제 로직과 동일)
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df = df.dropna(subset=['timestamp']).reset_index(drop=True)
        
        self.assertEqual(len(df), 4)
        self.assertFalse(df['timestamp'].isna().any())

        # 2. 10s 서브미닛 버킷 분배 로직 검증
        interval = '10s'
        sec_step = int(''.join(filter(str.isdigit, interval)) or 10)
        buckets_per_min = max(1, 60 // sec_step)
        
        minute_grp = df.groupby('timestamp')
        cum_count = minute_grp.cumcount()
        total_in_min = minute_grp['upbit_close'].transform('count').replace(0, 1)
        
        ratio = (cum_count / total_in_min) * buckets_per_min
        ratio = ratio.fillna(0.0)
        
        # 정수 변환 시 non-finite 에러 없이 정상 변환되어야 함
        bucket_sec = np.floor(ratio).clip(0, buckets_per_min - 1).astype(int) * sec_step
        df['adjusted_timestamp'] = df['timestamp'] + pd.to_timedelta(bucket_sec, unit='s')
        
        self.assertFalse(df['adjusted_timestamp'].isna().any())
        self.assertEqual(len(df['adjusted_timestamp']), 4)
        # 분당 3개 틱의 버킷 초는 0초 이상 50초 이하여야 함
        self.assertTrue((bucket_sec >= 0).all() and (bucket_sec < 60).all())

if __name__ == '__main__':
    unittest.main()
