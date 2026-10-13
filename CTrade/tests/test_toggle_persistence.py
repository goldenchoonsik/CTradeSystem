import unittest
import json
import os
import tempfile
from core.shared_state import SharedState
from utils.retry import atomic_write_json

class TestTogglePersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "trading_config.json")
        self.config_data = {
            "GLOBAL_STRATEGY": {
                "VERSION": "v1.3.0",
                "SIMULATION_MODE": False,
                "TRADING_ACTIVE": True,
                "SERVER_ACTIVE": True,
                "RADAR_ACTIVE": False
            },
            "SCALPING_TARGETS": []
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config_data, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_restore_saved_trading_active(self):
        """저장된 TRADING_ACTIVE=True 설정이 초기화 시 올바르게 복원되는지 검증"""
        with open(self.config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        
        global_strat = cfg.get("GLOBAL_STRATEGY", {})
        saved_trading_active = global_strat.get("TRADING_ACTIVE")
        self.assertIsNotNone(saved_trading_active)
        self.assertTrue(bool(saved_trading_active))

        # False로 변경 및 atomic_write 저장
        global_strat["TRADING_ACTIVE"] = False
        atomic_write_json(self.config_path, cfg)

        # 다시 로드하여 복원 확인
        with open(self.config_path, "r", encoding="utf-8") as f:
            reloaded = json.load(f)
        
        self.assertFalse(reloaded["GLOBAL_STRATEGY"]["TRADING_ACTIVE"])

if __name__ == "__main__":
    unittest.main()
