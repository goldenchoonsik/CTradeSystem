# -*- coding: utf-8 -*-
"""
CTradeSystem - CandleVault (종목 × 시간단위별 캔들 스토어)
=========================================================
1. (Symbol, Interval) 키 기반의 표준 캔들(OHLCV) 로드, 리샘플링 및 캐싱
2. 다중 시간단위 지원 (1m, 3m, 5m, 10m, 15m, 30m, 60m, 1d 등)
3. 1분봉 원천 데이터 기반의 온디맨드 리샘플링 (KRX 09:00 장시작 앵커링 지원)
4. 메모리 LRU 및 Parquet 디스크 캐시로 0.01초 내 초고속 반환

[T-4 단일화 (2026-09-15)]
------------------------
리샘플링은 `shared.candle_resampler.resample_candles` **단일 구현**에 위임합니다.
종전에는 본 클래스가 자체 리샘플러(게이트 없음)를 별도로 갖고 있어, 같은 "Silver" 계층인데도
① 하방 리샘플(미래참조) ② 비배수 인터벌 차단이 적용되지 않는 **불변식 불일치**가 있었습니다.
이제 Baker 배치 경로와 Vault/CTrade 경로가 동일한 엄격 게이트를 공유합니다.
"""

import os
import re
import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Union
from datetime import datetime

from shared.data_library.ingestion_manager import IngestionManager

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_MODULE_DIR))
_DEFAULT_CACHE_DIR = os.path.join(_MODULE_DIR, "cache", "candles")

# [T-4] 인터벌 → pandas freq 변환은 `shared.candle_resampler.get_resample_freq_str` 단일 구현을
#       사용한다. (종전 `parse_interval_to_pandas_freq` 는 동일 역할의 중복 구현이었음)


class CandleVault:
    """종목 및 시간단위별 캔들 데이터를 독립 관리하는 볼트(Vault)"""

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        self._mem_cache: Dict[Tuple[str, str], pd.DataFrame] = {}

    def _get_cache_path(self, symbol: str, interval: str) -> str:
        safe_sym = str(symbol).strip().upper().replace("/", "_").replace("\\", "_")
        safe_int = str(interval).strip().lower()
        return os.path.join(self.cache_dir, f"{safe_sym}_{safe_int}.parquet")

    def resample_candles(self, df_1m: pd.DataFrame, target_interval: str, market_type: str = "KRX") -> pd.DataFrame:
        """
        1분봉 캔들 DataFrame을 상위 시간단위(target_interval)로 수학적 무결성을 보장하며 리샘플링.

        - Open: 최초 시가 (first)
        - High: 최고 고가 (max)
        - Low: 최저 저가 (min)
        - Close: 최종 종가 (last)
        - Volume: 누적 거래량 (sum)
        - Bid Volume: 누적 매수 거래량 (sum)
        - Trade Count: 누적 체결 건수 (sum)
        """
        if df_1m is None or df_1m.empty:
            return pd.DataFrame()

        df = df_1m.copy()
        if "timestamp" not in df.columns:
            raise ValueError("DataFrame must contain 'timestamp' column.")

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)

        clean_int = str(target_interval).strip().lower()
        # 1분봉은 원천 그대로 반환 (게이트/리샘플 불필요, 원본 값 100% 보존)
        if clean_int in ("1m", "1min"):
            return df

        # ── [T-4] 단일 리샘플러 위임 ──
        # `shared.candle_resampler` 의 엄격 게이트(UnsupportedIntervalError /
        # NonDivisibleIntervalError / LookaheadInversionError / InvalidTimestampError)를
        # 그대로 공유한다. 종전 자체 구현은 게이트가 없어 잘못된 봉을 조용히 만들었다.
        from shared.data_library.candle_resampler import resample_candles as _shared_resample_candles

        resampled = _shared_resample_candles(
            df,
            target_interval=clean_int,
            source_interval="1m",   # `_find_raw_1m_data` 가 보장하는 원천 해상도
        )

        return self._drop_non_trading_bars(resampled)

    @staticmethod
    def _drop_non_trading_bars(resampled: pd.DataFrame) -> pd.DataFrame:
        """Vault 저장 계층 정리: 비영업/미체결 봉(종가 결측) 제거.

        리샘플링 계약이 아니라 **Vault 고유의 캐시 정리**이므로 이곳에 남긴다.
        """
        close_col = next(
            (c for c in ("target_close", "upbit_close", "close") if c in resampled.columns), None
        )
        if close_col is not None:
            resampled = resampled[resampled[close_col].notna()]
        else:
            resampled = resampled.dropna(how="all")
        return resampled.reset_index(drop=True)

    def get_candles(self,
                    symbol: str,
                    interval: str = "1m",
                    start_date: Optional[Union[str, datetime]] = None,
                    end_date: Optional[Union[str, datetime]] = None,
                    use_cache: bool = True) -> pd.DataFrame:
        """
        특정 종목의 지정된 시간단위(interval) 캔들을 로드합니다.
        캐시 부재 시 1분봉 원천 데이터로부터 자동 리샘플링 후 캐시를 생성합니다.
        """
        clean_sym = str(symbol).strip().upper()
        clean_int = str(interval).strip().lower()
        cache_key = (clean_sym, clean_int)

        # 1. 인메모리 캐시 확인
        if use_cache and cache_key in self._mem_cache:
            df = self._mem_cache[cache_key]
            return self._filter_by_date(df, start_date, end_date)

        # 2. 디스크 Parquet 캐시 확인
        disk_cache_file = self._get_cache_path(clean_sym, clean_int)
        if use_cache and os.path.exists(disk_cache_file) and os.path.getsize(disk_cache_file) > 0:
            df = IngestionManager.load_data(disk_cache_file)
            if not df.empty:
                self._mem_cache[cache_key] = df
                return self._filter_by_date(df, start_date, end_date)

        # 3. 원천 1분봉 데이터 탐색 및 리샘플링
        df_1m = self._find_raw_1m_data(clean_sym)
        if df_1m.empty:
            logging.warning(f"[CandleVault] {clean_sym} 원천 1분봉 데이터를 찾을 수 없습니다.")
            return pd.DataFrame()

        if clean_int in ("1m", "1min"):
            final_df = df_1m
        else:
            final_df = self.resample_candles(df_1m, clean_int)

        # 4. 캐시 저장
        if not final_df.empty:
            IngestionManager.atomic_save_parquet(final_df, disk_cache_file)
            self._mem_cache[cache_key] = final_df

        return self._filter_by_date(final_df, start_date, end_date)

    def append_candles(self, symbol: str, interval: str, new_df: pd.DataFrame) -> int:
        """새로운 캔들 데이터를 멱등적으로 증분 누적 및 중복 제거 저장"""
        clean_sym = str(symbol).strip().upper()
        clean_int = str(interval).strip().lower()
        disk_cache_file = self._get_cache_path(clean_sym, clean_int)

        count = IngestionManager.append_and_deduplicate(
            file_path=disk_cache_file,
            new_df=new_df,
            dedup_subset=["timestamp"],
            format_type="parquet"
        )

        # 메모리 캐시 갱신
        self._mem_cache[(clean_sym, clean_int)] = IngestionManager.load_data(disk_cache_file)
        return count

    def clear_cache(self, symbol: Optional[str] = None, interval: Optional[str] = None):
        """특정 종목/시간단위 또는 전체 캐시 초기화"""
        clean_sym = symbol.strip().upper() if symbol else None
        clean_int = interval.strip().lower() if interval else None

        keys_to_del = []
        for (s, i) in self._mem_cache.keys():
            if (clean_sym is None or s == clean_sym) and (clean_int is None or i == clean_int):
                keys_to_del.append((s, i))
        for k in keys_to_del:
            del self._mem_cache[k]

        if os.path.exists(self.cache_dir):
            for f in os.listdir(self.cache_dir):
                if not f.endswith(".parquet"):
                    continue
                match = True
                if clean_sym and not f.startswith(f"{clean_sym}_"):
                    match = False
                if clean_int and not f.endswith(f"_{clean_int}.parquet"):
                    match = False
                if match:
                    try:
                        os.remove(os.path.join(self.cache_dir, f))
                    except Exception:
                        pass

    def _filter_by_date(self, df: pd.DataFrame, start_date=None, end_date=None) -> pd.DataFrame:
        if df.empty or (start_date is None and end_date is None):
            return df
        filtered = df.copy()
        if start_date:
            filtered = filtered[filtered["timestamp"] >= pd.to_datetime(start_date)]
        if end_date:
            filtered = filtered[filtered["timestamp"] <= pd.to_datetime(end_date)]
        return filtered.reset_index(drop=True)

    def _find_raw_1m_data(self, symbol: str) -> pd.DataFrame:
        """기존 Baker 및 CTrade 데이터 디렉터리에서 1분봉 원천 데이터를 안전하게 검색 및 로드"""
        clean_num = symbol[1:] if str(symbol).startswith("A") and len(str(symbol)) == 7 and str(symbol)[1:].isdigit() else str(symbol)
        variants = {symbol, f"A{symbol}", clean_num, f"A{clean_num}"}

        search_paths = [
            os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", symbol, "data_snapshot_1m.csv"),
            os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", clean_num, "data_snapshot_1m.csv"),
            os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", "historical", "kis", f"{symbol}_1m.csv"),
            os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", "historical", "kis", f"A{symbol}_1m.csv"),
            os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", "historical", "kis", f"{clean_num}_1m.csv"),
            os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", "historical", "kis", f"A{clean_num}_1m.csv"),
            os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", "historical", "upbit", f"{symbol}_1m.csv"),
            os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", "historical", "overseas_futures", f"{symbol}_1m.csv"),
        ]

        # 디렉터리 내 파일 탐색 (historical/kis, historical/overseas_futures)
        search_dirs = [
            os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", "historical", "kis"),
            os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", "historical", "overseas_futures"),
        ]
        for sdir in search_dirs:
            if os.path.exists(sdir):
                for f in os.listdir(sdir):
                    if any(v in f for v in variants) and f.endswith(".csv") and not f.endswith(".bak"):
                        full_p = os.path.join(sdir, f)
                        if os.path.isfile(full_p) and full_p not in search_paths:
                            search_paths.append(full_p)

        found_dfs = []
        for p in search_paths:
            if os.path.exists(p) and os.path.getsize(p) > 0:
                df = IngestionManager.load_data(p)
                if not df.empty:
                    found_dfs.append(df)

        if not found_dfs:
            return pd.DataFrame()

        combined = pd.concat(found_dfs, ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
        combined = combined.sort_values("timestamp").reset_index(drop=True)
        return combined
