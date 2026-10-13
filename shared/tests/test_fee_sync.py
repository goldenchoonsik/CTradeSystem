import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.config.config_manager import normalize_fee_keys, get_symbol_info, register_symbol
from shared.config.market_policies import DEFAULT_MARKET_POLICIES


class TestFeeSync(unittest.TestCase):
    def test_normalize_fee_keys_from_top_level(self):
        # 1. 최상위 FEE_RATE 설정 시 하위 Optuna까지 100% 동기화
        cfg = {
            "FEE_RATE": 0.00003,
            "SLIPPAGE": 0.00008,
            "OPTUNA_CONFIG": {"fee_upbit": 0.0005, "slippage": 0.0006}
        }
        normalized = normalize_fee_keys(cfg)
        self.assertEqual(normalized["FEE_RATE"], 0.00003)
        self.assertEqual(normalized["FEE_UPBIT"], 0.00003)
        self.assertEqual(normalized["OPTUNA_CONFIG"]["fee_upbit"], 0.00003)
        self.assertEqual(normalized["SLIPPAGE"], 0.00008)
        self.assertEqual(normalized["OPTUNA_CONFIG"]["slippage"], 0.00008)

    def test_normalize_fee_keys_from_optuna(self):
        # 2. Optuna 내부 설정만 있을 때 최상위로 100% 동기화
        cfg = {
            "OPTUNA_CONFIG": {"fee_upbit": 0.00007, "slippage": 0.00015}
        }
        normalized = normalize_fee_keys(cfg)
        self.assertEqual(normalized["FEE_RATE"], 0.00007)
        self.assertEqual(normalized["FEE_UPBIT"], 0.00007)
        self.assertEqual(normalized["OPTUNA_CONFIG"]["fee_upbit"], 0.00007)
        self.assertEqual(normalized["SLIPPAGE"], 0.00015)
        self.assertEqual(normalized["OPTUNA_CONFIG"]["slippage"], 0.00015)

    def test_market_policies_defaults(self):
        # 3. 4대 시장 분류 표준 수수료 정책 확인
        self.assertEqual(DEFAULT_MARKET_POLICIES["KRX_ETF"]["default_fee"], 0.00005)
        self.assertEqual(DEFAULT_MARKET_POLICIES["KRX_STOCK"]["default_fee"], 0.00015)
        self.assertEqual(DEFAULT_MARKET_POLICIES["CRYPTO"]["default_fee"], 0.00050)
        self.assertEqual(DEFAULT_MARKET_POLICIES["US_ETF"]["default_fee"], 0.00070)

    def test_symbol_custom_fee_override(self):
        # 4. 사용자 커스텀 수수료(이벤트 수수료 등) 오버라이드 보존
        custom_symbol = {
            "symbol": "TEST_PROMO_ETF",
            "name": "프로모션 ETF",
            "market_type": "KRX_ETF",
            "default_fee": 0.00001,  # 0.001% 특별 이벤트 수수료
            "default_slippage": 0.00005
        }
        registered = register_symbol(custom_symbol)
        self.assertEqual(registered["default_fee"], 0.00001)
        self.assertEqual(registered["default_slippage"], 0.00005)


if __name__ == "__main__":
    unittest.main()
