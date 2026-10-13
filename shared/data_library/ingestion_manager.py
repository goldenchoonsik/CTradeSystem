# -*- coding: utf-8 -*-
"""
CTradeSystem - IngestionManager (원천 데이터 무손실 보존 및 멱등적 누적 축적 엔진)
=============================================================================
1. 원천 데이터 무손실 영구 보존 (Zero-Loss & Immutability)
2. 멱등적 증분 축적 (Idempotent Ingestion):
   - 기존 데이터 + 신규 데이터 Concat
   - 타임스탬프 및 고유 식별 키 기반 정밀 중복 제거 (Deduplication)
   - 시간순 엄격 정렬 (Timestamp Monotonic Sorting)
3. 원자적 디스크 쓰기 (Atomic Write / .tmp -> os.replace):
   - 디스크 쓰기 도중 프로세스 비정상 종료 시에도 기존 원본 파일 손상 원천 방지
"""

import os
import tempfile
import logging
import pandas as pd
import numpy as np
from typing import List, Optional, Union


class IngestionManager:
    """원천 데이터 파일의 안전한 누적 축적 및 중복 제거를 총괄하는 매니저"""

    @staticmethod
    def atomic_save_csv(df: pd.DataFrame, file_path: str, index: bool = False, encoding: str = "utf-8"):
        """임시 파일을 거쳐 안전하게 교체(os.replace)하는 원자적 CSV 저장"""
        dir_name = os.path.dirname(os.path.abspath(file_path))
        os.makedirs(dir_name, exist_ok=True)

        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding=encoding, suffix=".tmp") as tf:
            temp_name = tf.name
            df.to_csv(tf, index=index, encoding=encoding)

        os.replace(temp_name, file_path)

    @staticmethod
    def atomic_save_parquet(df: pd.DataFrame, file_path: str, compression: str = "snappy"):
        """임시 파일을 거쳐 안전하게 교체(os.replace)하는 원자적 Parquet 저장"""
        dir_name = os.path.dirname(os.path.abspath(file_path))
        os.makedirs(dir_name, exist_ok=True)

        with tempfile.NamedTemporaryFile("wb", dir=dir_name, delete=False, suffix=".tmp") as tf:
            temp_name = tf.name

        df.to_parquet(temp_name, compression=compression, index=False)
        os.replace(temp_name, file_path)

    @classmethod
    def load_data(cls, file_path: str) -> pd.DataFrame:
        """CSV 또는 Parquet 파일을 안전하게 로드하고 timestamp를 pd.Timestamp로 정규화"""
        if not os.path.exists(file_path):
            return pd.DataFrame()

        try:
            if file_path.endswith(".parquet") or file_path.endswith(".pq"):
                df = pd.read_parquet(file_path)
            else:
                df = pd.read_csv(file_path)
        except Exception as e:
            logging.error(f"[IngestionManager] 파일 로드 실패 ({file_path}): {e}")
            return pd.DataFrame()

        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values("timestamp").reset_index(drop=True)

        return df

    @classmethod
    def append_and_deduplicate(cls,
                               file_path: str,
                               new_df: pd.DataFrame,
                               dedup_subset: Optional[List[str]] = None,
                               format_type: Optional[str] = None) -> int:
        """
        기존 파일에 신규 데이터를 멱등적으로 누적 축적하고 중복을 완벽히 제거합니다.

        Args:
            file_path: 저장 대상 파일 경로 (.csv 또는 .parquet)
            new_df: 새로 추가할 DataFrame
            dedup_subset: 중복 판단 기준 컬럼 목록 (기본값: ['timestamp'])
            format_type: 'csv' 또는 'parquet' (생략 시 확장자 기준 자동 판별)

        Returns:
            축적 후 최종 총 행(Row) 수
        """
        if new_df is None or new_df.empty:
            existing_df = cls.load_data(file_path)
            return len(existing_df)

        new_data = new_df.copy()
        if "timestamp" in new_data.columns:
            new_data["timestamp"] = pd.to_datetime(new_data["timestamp"])

        if dedup_subset is None:
            dedup_subset = ["timestamp"] if "timestamp" in new_data.columns else list(new_data.columns)

        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            existing_df = cls.load_data(file_path)
            if not existing_df.empty:
                combined = pd.concat([existing_df, new_data], ignore_index=True)
            else:
                combined = new_data
        else:
            combined = new_data

        # 중복 제거 (최신 유입된 행을 우선 보존: keep='last')
        valid_subset = [c for c in dedup_subset if c in combined.columns]
        if valid_subset:
            combined = combined.drop_duplicates(subset=valid_subset, keep="last")
        else:
            combined = combined.drop_duplicates(keep="last")

        if "timestamp" in combined.columns:
            combined = combined.sort_values("timestamp").reset_index(drop=True)
        else:
            combined = combined.reset_index(drop=True)

        # 저장 포맷 결정
        is_parquet = False
        if format_type:
            is_parquet = format_type.lower() in ("parquet", "pq")
        else:
            is_parquet = file_path.endswith(".parquet") or file_path.endswith(".pq")

        if is_parquet:
            cls.atomic_save_parquet(combined, file_path)
        else:
            cls.atomic_save_csv(combined, file_path)

        return len(combined)
