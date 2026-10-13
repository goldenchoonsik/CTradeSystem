import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

# 경로 설정
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, root_dir)
sys.path.insert(0, os.path.join(root_dir, 'CTrade'))

from shared.features.feature_engine import run_feature_engineering
from shared.core.market_utils import detect_market_type

KST = timezone(timedelta(hours=9))

class TestKRXFeatureParity(unittest.TestCase):
    def setUp(self):
        # 2일치 캔들 데이터 생성 (D-1일 15:19, 15:20 & D일 09:00, 09:01)
        # 전일 종가 30,000원 -> 당일 시초가 35,000원 (+16.6% 거대 오버나이트 갭)
        records = [
            # D-1일 (2026-08-28)
            {"timestamp": datetime(2026, 8, 28, 15, 19, tzinfo=KST), "target_open": 30000, "target_high": 30050, "target_low": 29950, "target_close": 30000, "target_volume": 1000},
            {"timestamp": datetime(2026, 8, 28, 15, 20, tzinfo=KST), "target_open": 30000, "target_high": 30050, "target_low": 29950, "target_close": 30000, "target_volume": 1000},
            # D일 (2026-08-29) - 갭 상승 개장
            {"timestamp": datetime(2026, 8, 29, 9, 0, tzinfo=KST), "target_open": 35000, "target_high": 35050, "target_low": 34950, "target_close": 35000, "target_volume": 1000},
            {"timestamp": datetime(2026, 8, 29, 9, 1, tzinfo=KST), "target_open": 35000, "target_high": 35050, "target_low": 34950, "target_close": 35000, "target_volume": 1000},
        ]
        self.df = pd.DataFrame(records)
        self.feature_config = {
            'enabled_features': ['target_rsi_curr', 'target_ma_ratio', 'target_bb_pctb', 'target_atr_ratio'],
            'feature_params': {'period_candles': 14, 'period': 20}
        }

    def test_krx_feature_isolation(self):
        # 1. KRX 설정 (SYMBOL_UPBIT = '069500')
        krx_config = {'SYMBOL_UPBIT': '069500'}
        df_krx = run_feature_engineering(self.df, interval='1m', feature_config=self.feature_config, config=krx_config)
        
        # is_warmup 플래그 생성 여부 확인
        self.assertIn('is_warmup', df_krx.columns)
        self.assertTrue(df_krx.iloc[2]['is_warmup']) # 09:00은 웜업 구간
        
        # 2. CRYPTO 설정 (SYMBOL_UPBIT = 'KRW-BTC')
        crypto_config = {'SYMBOL_UPBIT': 'KRW-BTC'}
        df_crypto = run_feature_engineering(self.df, interval='1m', feature_config=self.feature_config, config=crypto_config)
        self.assertNotIn('is_warmup', df_crypto.columns)

        # 3. KRX의 당일 09:00 행 (index 2)에서 전일 갭 오염 격리 확인
        # KRX 모드는 당일 첫 봉(09:00)의 diff가 NaN -> fillna에 의해 50.0(중립)이 됨
        rsi_krx_0900 = df_krx.iloc[2]['target_rsi_curr']
        rsi_crypto_0900 = df_crypto.iloc[2]['target_rsi_curr']
        
        print(f"\n[Test] 09:00 RSI - KRX(세션분리): {rsi_krx_0900:.2f} vs CRYPTO(연속스트림): {rsi_crypto_0900:.2f}")
        # CRYPTO는 전일 30,000 -> 35,000 급등으로 RSI 100.0 과매수 왜곡 발생
        self.assertAlmostEqual(rsi_crypto_0900, 100.0, places=4)
        # KRX는 당일 독립 계산으로 50.0 정상 유지
        self.assertAlmostEqual(rsi_krx_0900, 50.0, places=4)

if __name__ == '__main__':
    unittest.main()
