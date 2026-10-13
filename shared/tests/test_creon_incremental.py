# -*- coding: utf-8 -*-
import unittest
import os
import shutil
import tempfile
import csv
import sys

# 프로젝트 루트 경로 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from CAiModelBaker.core.creon_collector import get_existing_last_timestamp, merge_and_save_data


class TestCreonIncrementalCollector(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_creon_incremental_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_get_existing_last_timestamp(self):
        # 1. 존재하지 않는 파일
        non_existent = os.path.join(self.test_dir, "non_existent.csv")
        self.assertIsNone(get_existing_last_timestamp(non_existent))

        # 2. 빈 파일
        empty_file = os.path.join(self.test_dir, "empty.csv")
        with open(empty_file, "w", encoding="utf-8") as f:
            pass
        self.assertIsNone(get_existing_last_timestamp(empty_file))

        # 3. 헤더만 있는 파일
        header_file = os.path.join(self.test_dir, "header_only.csv")
        with open(header_file, "w", encoding="utf-8") as f:
            f.write("timestamp,upbit_open,upbit_high,upbit_low,upbit_close\n")
        self.assertIsNone(get_existing_last_timestamp(header_file))

        # 4. 데이터가 여러 행 있는 파일
        data_file = os.path.join(self.test_dir, "data.csv")
        with open(data_file, "w", encoding="utf-8") as f:
            f.write("timestamp,upbit_open,upbit_high,upbit_low,upbit_close\n")
            f.write("2025-01-01 09:00:00,30000,30500,29900,30200\n")
            f.write("2025-01-01 09:01:00,30200,30300,30100,30150\n")
            f.write("2025-01-01 09:02:00,30150,30400,30100,30350\n")
        self.assertEqual(get_existing_last_timestamp(data_file), "2025-01-01 09:02:00")

    def test_merge_and_save_data_initial_creation(self):
        # 기존 파일이 없을 때 신규 파일이 타겟 파일로 바로 이동하는지 검증
        tmp_file = os.path.join(self.test_dir, "new.raw.tmp")
        target_file = os.path.join(self.test_dir, "target.csv")

        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write("timestamp,upbit_close\n")
            f.write("2025-01-01 09:00:00,30000\n")

        res_path = merge_and_save_data(tmp_file, target_file, subset_keys=['timestamp'])
        self.assertEqual(res_path, target_file)
        self.assertTrue(os.path.exists(target_file))
        self.assertFalse(os.path.exists(tmp_file))

        with open(target_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("2025-01-01 09:00:00", lines[1])

    def test_merge_and_save_data_incremental_upsert_preserves_old_data(self):
        # 기존 과거 데이터가 100% 보존되고 신규 데이터가 병합되는지 검증
        target_file = os.path.join(self.test_dir, "target.csv")
        with open(target_file, "w", encoding="utf-8") as f:
            f.write("timestamp,upbit_close,upbit_volume,cvd,upbit_trade_price\n")
            # 6개월 전 과거 데이터 (대신증권 서버에서는 이미 삭제된 데이터라 가정)
            f.write("2024-06-01 09:00:00,28000,100,10,2800000\n")
            f.write("2024-06-01 09:01:00,28100,150,20,4215000\n")
            # 어제 데이터
            f.write("2025-01-01 15:30:00,30000,200,30,6000000\n")

        # 신규 수신된 증분 데이터 (중복 1건 + 오늘 신규 2건)
        tmp_file = os.path.join(self.test_dir, "new.raw.tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write("timestamp,upbit_close,upbit_volume,cvd,upbit_trade_price\n")
            # 중복 데이터
            f.write("2025-01-01 15:30:00,30000,200,30,6000000\n")
            # 오늘 신규 데이터
            f.write("2025-01-02 09:00:00,30100,100,10,3010000\n")
            f.write("2025-01-02 09:01:00,30200,120,15,3624000\n")

        dedup_keys = ['timestamp', 'upbit_close', 'upbit_volume', 'cvd', 'upbit_trade_price']
        res_path = merge_and_save_data(tmp_file, target_file, subset_keys=dedup_keys)

        self.assertEqual(res_path, target_file)
        self.assertTrue(os.path.exists(target_file))
        self.assertFalse(os.path.exists(tmp_file))

        with open(target_file, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))

        # 헤더 1행 + 과거 2행 + 어제 1행 + 오늘 2행 = 총 6행 (중복 1건 완벽 제거)
        self.assertEqual(len(reader), 6)
        
        # 첫 번째 데이터가 2024-06-01 (과거 데이터 유실 없이 보존됨)
        self.assertEqual(reader[1][0], "2024-06-01 09:00:00")
        self.assertEqual(reader[2][0], "2024-06-01 09:01:00")
        self.assertEqual(reader[3][0], "2025-01-01 15:30:00")
        self.assertEqual(reader[4][0], "2025-01-02 09:00:00")
        self.assertEqual(reader[5][0], "2025-01-02 09:01:00")


if __name__ == "__main__":
    unittest.main()
