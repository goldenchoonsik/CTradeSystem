# -*- coding: utf-8 -*-
"""
CTradeSystem - FeatureComposer (온디맨드 조립식 피처 결합 엔진)
============================================================
1. 타겟 불변 베이스 피처셋에 대장주 및 매크로 지표 블록을 온디맨드로 결합
2. 타겟 고유 지표(RSI, MACD, BB 등)는 1회만 계산하고 영구 재사용 (재빌드 0초)
3. 대장주(선행 지표) 추가/변경 시 타임스탬프 인덱스 기반으로 0.5초 내 경량 결합
"""

import os
import time
import warnings
import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Union

from shared.data_library.candle_vault import CandleVault
from shared.data_library.reference_staleness import (
    attach_reference,
    merge_asof_with_stale_cap,
    resolve_stale_cap,
)
from shared.features.feature_registry import register_reference_features
from shared.config.config_manager import get_symbol_info, ref_key, ref_prefix



class FeatureComposer:
    """타겟 베이스 피처셋과 다중 자산/대장주 지표를 시간단위별로 고속 조립하는 컴포저

    ⚠️ 순수 함수 컨테이너 — 파일을 쓰지 않는다. 저장은 호출자가 결정한다.
    (T-2 Gold 계약: 학습 데이터셋 writer 는 run_dataset_build_pipeline 단일)
    """

    def __init__(self,
                 candle_vault: Optional[CandleVault] = None,
                 feature_vault=None):
        """
        Args:
            candle_vault: 캔들 스토어 (Silver). 미지정 시 기본 캐시 디렉토리 사용.
            feature_vault: ⚠️ DEPRECATED (T-2). 하위 호환을 위해 인자만 수용하며
                compose() 는 이를 사용하지 않는다. Gold 피처 계산은
                ``shared.feature_engine.run_feature_engineering`` 단일 경로를 사용할 것.
        """
        self.candle_vault = candle_vault or CandleVault()

        if feature_vault is not None:
            warnings.warn(
                "FeatureComposer(feature_vault=...) 인자는 DEPRECATED 입니다. "
                "Gold 피처 계산은 shared.feature_engine.run_feature_engineering 를 사용하세요. [T-2]",
                DeprecationWarning,
                stacklevel=2,
            )
        # T-2: FeatureVault 는 피처 간 의존성을 해소하지 못해 생산 경로와 다른 값을 산출하는
        #      미배선 사본이므로 compose() 는 이를 보유/사용하지 않는다.
        self.feature_vault = feature_vault

    def compose(self,
                target_base_df: pd.DataFrame,
                reference_specs: List[Dict],
                macro_specs: Optional[List[Dict]] = None,
                target_symbol: str = "",
                target_interval: str = "") -> pd.DataFrame:
        """
        타겟 종목의 기본 데이터프레임에 참조 대장주 및 매크로 지표를 고속 온디맨드 결합.

        Args:
            target_base_df: 타겟 종목의 베이스 캔들/피처 데이터프레임 (timestamp 포함 필수)
            reference_specs: 참조 대장주 설정 목록
                예: [
                    {"symbol": "000660", "interval": "1m"},
                    {"symbol": "005930", "interval": "1m"}   # -> ref_000660_close, ref_005930_close ...
                ]
            macro_specs: 매크로 선물/지수 설정 목록
                예: [
                    {"symbol": "NQ", "interval": "1m"},      # -> ref_nq_close
                    {"symbol": "ES", "interval": "1m"}       # -> ref_es_close
                ]

        Returns:
            대장주 및 매크로 컬럼/피처가 완전 결합된 최종 DataFrame (0.5초 이내 완료)
        """
        if target_base_df is None or target_base_df.empty:
            return pd.DataFrame()

        t0 = time.perf_counter()
        result_df = target_base_df.copy()
        if "timestamp" not in result_df.columns:
            raise ValueError("target_base_df must contain 'timestamp' column.")

        result_df["timestamp"] = pd.to_datetime(result_df["timestamp"])
        result_df = result_df.sort_values("timestamp").reset_index(drop=True)

        # 1. 참조 대장주(Reference Symbols) 결합
        if reference_specs:
            for r_spec in reference_specs:
                r_sym = str(r_spec.get("symbol", "")).strip().upper()
                r_int = str(r_spec.get("interval", "1m")).strip().lower()

                if not r_sym:
                    continue
                r_key = ref_key(r_sym)      # 컬럼 이름은 원장 심볼에서 유도한다 (별칭 없음)

                ref_candles = self.candle_vault.get_candles(r_sym, r_int)
                if ref_candles.empty:
                    logging.warning(f"[FeatureComposer] 참조 종목 {r_sym} ({r_int}) 캔들 부재 (스킵)")
                    continue

                # 대장주 기본 3대 컬럼 준비 (종가, 거래량, 매수체결량)
                r_close_src = "target_close" if "target_close" in ref_candles.columns else (
                    "upbit_close" if "upbit_close" in ref_candles.columns else "close"
                )
                r_vol_src = "target_volume" if "target_volume" in ref_candles.columns else (
                    "upbit_volume" if "upbit_volume" in ref_candles.columns else "volume"
                )

                sub = pd.DataFrame({"timestamp": pd.to_datetime(ref_candles["timestamp"])})
                sub[f"ref_{r_key}_close"] = ref_candles[r_close_src].astype(float)
                sub[f"ref_{r_key}_volume"] = ref_candles[r_vol_src].astype(float) if r_vol_src in ref_candles.columns else 0.0

                if "target_trade_bid_ratio" in ref_candles.columns:
                    sub[f"ref_{r_key}_bid_vol"] = sub[f"ref_{r_key}_volume"] * ref_candles["target_trade_bid_ratio"].astype(float)
                elif "upbit_trade_bid_ratio" in ref_candles.columns:
                    sub[f"ref_{r_key}_bid_vol"] = sub[f"ref_{r_key}_volume"] * ref_candles["upbit_trade_bid_ratio"].astype(float)
                else:
                    sub[f"ref_{r_key}_bid_vol"] = sub[f"ref_{r_key}_volume"] * 0.5

                sub = sub.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

                # ── [T-6] 참조 결합 단일 진입점 ──
                # 타임스탬프 규약 보정(봉 시작 라벨 → 확정 시각)과 stale 상한을 attach_reference 가 함께 책임진다.
                # 조립기가 정렬을 따로 구현하지 않기 때문에, 새 조립기가 생겨도 같은 규칙을 벗어날 수 없다.
                ref_market = str(get_symbol_info(r_sym).get("market_type", "")) or None
                result_df = attach_reference(
                    result_df, sub,
                    ref_symbol=r_sym, ref_interval=r_int,
                    target_symbol=target_symbol, target_interval=target_interval,
                    value_cols=[f"ref_{r_key}_close", f"ref_{r_key}_volume", f"ref_{r_key}_bid_vol"],
                    cap=resolve_stale_cap(ref_market, r_int),
                    fill_values={
                        f"ref_{r_key}_volume": 0.0,
                        f"ref_{r_key}_bid_vol": 0.0,
                    },
                )

                # 4대 선행 지표 레지스트리 자동 등록
                register_reference_features(r_sym)

        # 2. 매크로 피드(Macro Futures / Indices) 결합
        if macro_specs:
            for m_spec in macro_specs:
                m_sym = str(m_spec.get("symbol", "")).strip().upper()
                target_col = f"{ref_prefix(m_sym)}close"   # 대장주와 같은 이름 규칙 (별칭/임의 컬럼명 없음)
                m_int = str(m_spec.get("interval", "1m")).strip().lower()

                m_candles = self.candle_vault.get_candles(m_sym, m_int)
                if m_candles.empty:
                    continue

                m_close_src = "close" if "close" in m_candles.columns else (
                    "target_close" if "target_close" in m_candles.columns else "upbit_close"
                )

                sub_m = pd.DataFrame({"timestamp": pd.to_datetime(m_candles["timestamp"])})
                sub_m[target_col] = m_candles[m_close_src].astype(float)
                
                sub_m = sub_m.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

                # ── [T-6] 참조 결합 단일 진입점 (대장주와 같은 규칙) ──
                macro_market = str(get_symbol_info(m_sym).get("market_type", "")) or None
                result_df = attach_reference(
                    result_df, sub_m,
                    ref_symbol=m_sym, ref_interval=m_int,
                    target_symbol=target_symbol, target_interval=target_interval,
                    value_cols=[target_col],
                    cap=resolve_stale_cap(macro_market, m_int),
                )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logging.info(f"[FeatureComposer] 온디맨드 피처 조립 완료: {len(result_df)}행 (소요시간: {elapsed_ms:.1f}ms)")
        return result_df
