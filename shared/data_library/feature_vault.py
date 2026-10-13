# -*- coding: utf-8 -*-
"""
CTradeSystem - FeatureVault (종목 × 시간단위별 개별 지표 스토어)

⚠️ [DEPRECATED — Gold 계층 진실 확정 (T-2, 2026-09-15)]
================================================================================
이 모듈은 **더 이상 Gold 계층의 생산 경로가 아니며, 신규 사용을 금지**합니다.
대체 경로: ``shared/feature_engine.run_feature_engineering``

폐기 사유 (실측 근거)
---------------------
동일 입력에 대해 본 클래스와 ``run_feature_engineering`` 이 **다른 피처 값을 산출**한다.
(실측: target_cvd_momentum 최대 절대 차이 0.1385)

1. **피처 간 의존성 미해소**
   모든 피처를 불변 ``candle_df`` 기준으로 계산한다(``compute_fn(candle_df, ...)``).
   따라서 선행 피처가 요구되는 지표가 **원천 컬럼만 보고** 계산된다.
   (예: ``target_cvd_momentum`` ← ``target_trade_bid_ratio`` 의 스무딩 결과)
   생산 경로는 ``_execute_feature_tasks`` 의 **의존성 위상 레벨 스케줄링(M-3)** 으로
   선행 피처를 df 에 반영한 뒤 계산하므로 결과가 다르다.

2. **KRX 세션 분리 연산 부재**
   ``run_feature_engineering`` 은 국내 주식/ETF 를 날짜별로 독립 연산하여
   오버나이트 갭 오염을 차단하고 ``is_warmup`` 플래그를 생성한다. 본 클래스는 이를 수행하지 않는다.

3. **FEATURE_ALIASES 정규화 부재**
   생산 경로는 ``fid`` 와 canonical_id 를 양방향으로 기록한다. 본 클래스는 이를 수행하지 않는다.

4. **호출자 없음 (미배선)**
   ``cache/features/`` 가 비어 있고, ``FeatureComposer.compose()`` 는
   ``self.feature_vault`` 를 생성만 하고 사용하지 않는다. 프로덕션 사용처 0건.

→ 배선하면 Train/Serve Skew 를 새로 만든다. 따라서 **폐기하고 단일 생산 경로로 수렴**한다.
   (참조: docs/reports/FIX_RESULT_20260915_P1_P2_T1.md, CODE_REVIEW_20260915_data_pipeline_checklist.csv D-2)
"""

import os
import warnings
import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Union

from shared.data_library.candle_vault import CandleVault
from shared.data_library.ingestion_manager import IngestionManager
from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY, load_feature_config

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_FEATURE_CACHE_DIR = os.path.join(_MODULE_DIR, "cache", "features")


class FeatureVault:
    """종목 및 시간단위별 지표(Feature)를 독립 계산하고 캐싱하는 볼트

    ⚠️ DEPRECATED: Gold 계층의 생산 경로는 ``run_feature_engineering`` 입니다.
    (모듈 docstring 의 폐기 사유 참조)
    """

    def __init__(self, candle_vault: Optional[CandleVault] = None, cache_dir: Optional[str] = None):
        warnings.warn(
            "FeatureVault 는 DEPRECATED 입니다. Gold 계층 피처 계산은 "
            "shared.feature_engine.run_feature_engineering 를 사용하세요. "
            "(본 클래스는 피처 간 의존성을 해소하지 못해 동일 입력에 다른 값을 산출합니다)",
            DeprecationWarning,
            stacklevel=2,
        )
        self.candle_vault = candle_vault or CandleVault()
        self.cache_dir = cache_dir or _DEFAULT_FEATURE_CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        self._mem_feature_cache: Dict[Tuple[str, str], pd.DataFrame] = {}

    def _get_cache_path(self, symbol: str, interval: str) -> str:
        safe_sym = str(symbol).strip().upper().replace("/", "_").replace("\\", "_")
        safe_int = str(interval).strip().lower()
        return os.path.join(self.cache_dir, f"{safe_sym}_{safe_int}_features.parquet")

    def get_feature(self,
                    symbol: str,
                    interval: str,
                    feature_id: str,
                    params: Optional[dict] = None,
                    use_cache: bool = True) -> pd.Series:
        """단일 피처 시리즈를 (symbol, interval) 단위에서 조회하거나 계산하여 반환"""
        block = self.get_feature_block(
            symbol=symbol,
            interval=interval,
            feature_ids=[feature_id],
            params_dict={feature_id: params} if params else None,
            use_cache=use_cache
        )
        if feature_id in block.columns:
            return block[feature_id]
        return pd.Series(np.nan, index=block.index)

    def get_feature_block(self,
                          symbol: str,
                          interval: str,
                          feature_ids: List[str],
                          params_dict: Optional[Dict[str, dict]] = None,
                          use_cache: bool = True) -> pd.DataFrame:
        """
        지정된 (symbol, interval)에서 복수의 피처들을 일괄 계산하여 DataFrame으로 반환.
        이미 계산된 피처는 캐시에서 즉시 반환하고, 신규 요청된 피처만 증분 계산합니다.
        """
        clean_sym = str(symbol).strip().upper()
        clean_int = str(interval).strip().lower()
        cache_key = (clean_sym, clean_int)
        params_dict = params_dict or {}

        # 1. 캐시 확인
        cached_df = pd.DataFrame()
        if use_cache:
            if cache_key in self._mem_feature_cache:
                cached_df = self._mem_feature_cache[cache_key]
            else:
                cache_path = self._get_cache_path(clean_sym, clean_int)
                if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
                    cached_df = IngestionManager.load_data(cache_path)
                    if not cached_df.empty:
                        self._mem_feature_cache[cache_key] = cached_df

        # 모든 요청 피처가 캐시에 존재하는지 확인
        missing_features = [fid for fid in feature_ids if cached_df.empty or fid not in cached_df.columns]

        if not missing_features and not cached_df.empty:
            cols = ["timestamp"] + [fid for fid in feature_ids if fid in cached_df.columns]
            return cached_df[cols].copy()

        # 2. 캔들 데이터 로드
        candle_df = self.candle_vault.get_candles(clean_sym, clean_int, use_cache=use_cache)
        if candle_df.empty:
            logging.warning(f"[FeatureVault] {clean_sym} ({clean_int}) 캔들 데이터가 없어 피처를 계산할 수 없습니다.")
            return pd.DataFrame()

        # 3. 누락된 피처 계산
        computed_df = cached_df.copy() if not cached_df.empty else pd.DataFrame({"timestamp": candle_df["timestamp"]})
        computed_df["timestamp"] = pd.to_datetime(computed_df["timestamp"])

        feature_config = load_feature_config()
        configured_params = feature_config.get("feature_params", {})

        for fid in missing_features:
            if fid not in DEFAULT_FEATURE_REGISTRY:
                # 참조 종목 선행 피처(ref_{key}_{kind})는 id 만으로 등록된다
                from shared.features.feature_registry import ensure_reference_feature
                ensure_reference_feature(fid)

            entry = DEFAULT_FEATURE_REGISTRY.get(fid)
            if not entry or "compute" not in entry:
                logging.debug(f"[FeatureVault] 레지스트리에 등록되지 않은 피처 무시: {fid}")
                continue

            p = params_dict.get(fid) or configured_params.get(fid) or entry.get("params", {})
            compute_fn = entry["compute"]

            try:
                # compute_fn(df, params, interval)
                s = compute_fn(candle_df, p, clean_int)
                if isinstance(s, pd.Series):
                    computed_df[fid] = s.values
                else:
                    computed_df[fid] = s
            except Exception as e:
                logging.error(f"[FeatureVault] 피처 계산 오류 ({clean_sym}, {clean_int}, {fid}): {e}")
                computed_df[fid] = np.nan

        # 4. 캐시 갱신
        self._mem_feature_cache[cache_key] = computed_df
        cache_path = self._get_cache_path(clean_sym, clean_int)
        IngestionManager.atomic_save_parquet(computed_df, cache_path)

        cols = ["timestamp"] + [fid for fid in feature_ids if fid in computed_df.columns]
        return computed_df[cols].copy()

    def clear_cache(self, symbol: Optional[str] = None, interval: Optional[str] = None):
        """특정 종목/시간단위 또는 전체 피처 캐시 초기화"""
        clean_sym = symbol.strip().upper() if symbol else None
        clean_int = interval.strip().lower() if interval else None

        keys_to_del = []
        for (s, i) in self._mem_feature_cache.keys():
            if (clean_sym is None or s == clean_sym) and (clean_int is None or i == clean_int):
                keys_to_del.append((s, i))
        for k in keys_to_del:
            del self._mem_feature_cache[k]

        if os.path.exists(self.cache_dir):
            for f in os.listdir(self.cache_dir):
                if not f.endswith("_features.parquet"):
                    continue
                match = True
                if clean_sym and not f.startswith(f"{clean_sym}_"):
                    match = False
                if clean_int and not f.startswith(f"{clean_sym or ''}_{clean_int}_"):
                    match = False
                if match:
                    try:
                        os.remove(os.path.join(self.cache_dir, f))
                    except Exception:
                        pass
