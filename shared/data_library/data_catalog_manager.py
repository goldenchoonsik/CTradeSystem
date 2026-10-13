# -*- coding: utf-8 -*-
"""
CTradeSystem - Data Catalog Manager (원천 데이터 거버넌스 카탈로그 매니저)
========================================================================
1. shared/config/data_sources_catalog.json 선언적 리소스 중앙 집중 관리
2. 원천 데이터별 제공처(출처), 수집 방식, 가용 시작일/종료일, 휴장일 연동
3. 날짜별 자동 스킵(Smart Skip) 판정 및 기간별 사전 스킵 요약 계산
4. 결측 시 폴백 정책(Fallback Policy) 및 중요도 명세 제공
"""

import os
import json
import tempfile
import logging
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Tuple, Union

from shared.data_library.paths import data_vault_config_path

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LEGACY_CATALOG_PATH = os.path.join(os.path.dirname(_MODULE_DIR), "config", "data_sources_catalog.json")
_VAULT_CATALOG_PATH = data_vault_config_path("data_sources_catalog.json")

# DataVault/config/ 우선, 없으면 레거시 shared/config/ 폴백 (기본값: DataVault/config/)
_CATALOG_FILE_PATH = _VAULT_CATALOG_PATH if (os.path.exists(_VAULT_CATALOG_PATH) or not os.path.exists(_LEGACY_CATALOG_PATH)) else _LEGACY_CATALOG_PATH


class DataCatalogManager:
    """원천 데이터 출처, 가용 범위, 스킵 정책 및 거버넌스를 전담하는 매니저"""

    _cache: Optional[Dict[str, Any]] = None
    _last_mtime: float = 0.0

    @classmethod
    def load_catalog(cls, force_refresh: bool = False) -> Dict[str, Any]:
        """카탈로그 JSON 로드 및 변경 감지 캐싱"""
        if not os.path.exists(_CATALOG_FILE_PATH):
            logger.warning(f"[DataCatalogManager] 카탈로그 파일 없음: {_CATALOG_FILE_PATH}")
            return {}

        try:
            mtime = os.path.getmtime(_CATALOG_FILE_PATH)
            if not force_refresh and cls._cache is not None and mtime == cls._last_mtime:
                return cls._cache

            with open(_CATALOG_FILE_PATH, "r", encoding="utf-8") as f:
                cls._cache = json.load(f)
                cls._last_mtime = mtime
            return cls._cache
        except Exception as e:
            logger.error(f"[DataCatalogManager] 카탈로그 파일 로드 실패: {e}")
            return cls._cache or {}

    @classmethod
    def get_source(cls, source_id: str) -> Optional[Dict[str, Any]]:
        """특정 source_id 메타데이터 조회"""
        catalog = cls.load_catalog()
        return catalog.get(source_id)

    @classmethod
    def get_all_sources(cls) -> List[Dict[str, Any]]:
        """전체 원천 데이터 목록 반환 (리스트 형태)"""
        catalog = cls.load_catalog()
        return list(catalog.values())

    @classmethod
    def resolve_source_id(cls, provider: str, data_key: str) -> Optional[str]:
        """제공처(UPBIT, BINANCE 등)와 데이터 키(KRW-USDT, BTCUSDC, CANDLE 등)로부터 source_id 역산"""
        p_up = str(provider).strip().upper()
        k_up = str(data_key).strip().upper()

        # 1. 직결 매핑 규칙
        if "USDT" in k_up and p_up == "UPBIT":
            return "UPBIT_USDT"
        if "OI" in k_up or "OPENINTEREST" in k_up:
            return "BINANCE_FUTURES_OI"
        if "USDC" in k_up:
            return "BINANCE_USDC"
        if "ETH" in k_up and p_up == "BINANCE":
            return "BINANCE_ETH"
        if "COINBASE" in p_up or "COINBASE" in k_up:
            return "COINBASE_SPOT"
        if p_up == "UPBIT":
            if "TRADE" in k_up or "TICK" in k_up:
                return "UPBIT_TRADE"
            return "UPBIT_CANDLE"
        if p_up == "BINANCE":
            return "BINANCE_SPOT"
        if p_up in ("CREON", "CYBOS"):
            return "CREON_KRX_TICK_CANDLE"
        if p_up in ("KIS", "KRX"):
            return "KIS_KRX_STOCK"
        if p_up == "CME":
            return "CME_FUTURES"
        if p_up in ("CTRADER", "SPOTWARE"):
            return "CTRADER_US_TECH_100"

        catalog = cls.load_catalog()
        if k_up in catalog:
            return k_up
        return None

    @classmethod
    def should_skip(cls, source_id: str, dt_or_str: Union[str, datetime, date]) -> Tuple[bool, Optional[str]]:
        """
        주어진 원천 데이터에 대해 특정 일자가 스킵 대상인지 판정.
        Returns:
            (should_skip: bool, reason: Optional[str])
        """
        src = cls.get_source(source_id)
        if not src:
            return False, None

        avail = src.get("availability", {})
        skip_before = avail.get("skip_before_start", True)
        start_date = avail.get("available_start_date")
        end_date = avail.get("available_end_date")
        default_reason = avail.get("skip_reason", "데이터 미제공 구간")

        # 날짜 표준화 (YYYY-MM-DD)
        if isinstance(dt_or_str, (datetime, date)):
            d_str = dt_or_str.strftime("%Y-%m-%d")
            weekday = dt_or_str.weekday()
        else:
            d_str = str(dt_or_str)[:10].replace(".", "-").replace("/", "-")
            try:
                dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
                weekday = dt_obj.weekday()
            except ValueError:
                weekday = 0

        # 1. 가용 시작일 이전 스킵 검사
        if skip_before and start_date and d_str < start_date:
            return True, f"{default_reason} ({start_date} 이전)"

        # 2. 가용 종료일 이후 스킵 검사
        if end_date and d_str > end_date:
            return True, f"제공 종료일({end_date}) 이후 구간"

        # 3. 주말/휴장일 스킵 검사 (주식/선물)
        schedule = avail.get("schedule_type", "24/7_CONTINUOUS")
        if schedule in ("KRX_WEEKDAY", "US_WEEKDAY") and weekday >= 5:
            return True, "주말 정기 휴장"

        hol_cal = avail.get("holiday_calendar", "NONE")
        if hol_cal in ("KRX", "US"):
            try:
                from shared.core.market_calendar import get_market_holidays
                hols = get_market_holidays(hol_cal)
                if d_str in hols:
                    return True, f"{hol_cal} 증시 법정 휴장일"
            except Exception:
                pass

        return False, None

    @classmethod
    def get_skip_summary_for_range(
        cls, source_id: str, date_range: List[Union[str, datetime, date]]
    ) -> Dict[str, Any]:
        """
        주어진 날짜 리스트에 대해 스킵 대상 일수 및 사유를 사전에 요약 계산
        """
        src = cls.get_source(source_id)
        if not src or not date_range:
            return {"total": len(date_range), "skip_count": 0, "valid_count": len(date_range), "reason": ""}

        skip_count = 0
        first_reason = ""
        for dt in date_range:
            is_skip, reason = cls.should_skip(source_id, dt)
            if is_skip:
                skip_count += 1
                if not first_reason and reason:
                    first_reason = reason

        return {
            "source_id": source_id,
            "name": src.get("name", source_id),
            "total_days": len(date_range),
            "skip_count": skip_count,
            "valid_days": len(date_range) - skip_count,
            "representative_reason": first_reason,
            "start_date": src.get("availability", {}).get("available_start_date"),
            "fallback_policy": src.get("fallback_policy", {}).get("policy", "NONE"),
        }

    @classmethod
    def find_source_by_column(cls, col_name: str) -> Optional[Dict[str, Any]]:
        """컬럼명(예: 'upbit_usdt_close', 'binance_futures_oi' 등)으로부터 매칭되는 원천 데이터 소스 검색"""
        catalog = cls.load_catalog()
        c_clean = str(col_name).strip().lower()
        for src in catalog.values():
            cols = [str(c).lower() for c in src.get("columns", [])]
            if c_clean in cols:
                return src
            # 키워드 fallback 매핑
            if "usdt" in c_clean and src.get("id") == "UPBIT_USDT":
                return src
            if ("oi" in c_clean or "openinterest" in c_clean) and src.get("id") == "BINANCE_FUTURES_OI":
                return src
            if "usdc" in c_clean and src.get("id") == "BINANCE_USDC":
                return src
            if "coinbase" in c_clean and src.get("id") == "COINBASE_SPOT":
                return src
            if "binance_spot" in c_clean and src.get("id") == "BINANCE_SPOT":
                return src
            if "trade" in c_clean and src.get("id") == "UPBIT_TRADE":
                return src
        return None

    @classmethod
    def apply_fallback_to_dataframe(
        cls, df: Any, target_cols: Optional[List[str]] = None,
        exclude_cols: Optional[List[str]] = None
    ) -> Tuple[Any, Dict[str, Any]]:
        """
        데이터프레임의 결측치를 카탈로그에 선언된 fallback_policy에 따라 동적으로 보간 및 결측치 품질 리포트 생성.
        하드코딩을 100% 제거하고 JSON 스펙 기반으로 동작합니다.

        Args:
            df: 대상 데이터프레임
            target_cols: 처리 대상 컬럼 제한 (None 이면 전체)
            exclude_cols: **보간 제외 컬럼** — 의도적으로 무효화한 NaN 을 되채우지 않는다.
                (예: 참조/매크로 stale 상한으로 무효화한 `ref_*` 컬럼)
                제외 컬럼은 status="INTENTIONAL_NULL" 로 리포트에 기록된다.

        ⚠️ [T-3/D-10 Point-in-Time 무결성 정책]
        본 함수는 **bfill(역방향 채움)을 사용하지 않는다.** bfill 은 결측 구간을 *미래* 값으로
        채우므로 학습 데이터에 미래 정보를 유입시키는 Lookahead 다 (ARCHITECTURE.md PIT-1 정책).
        또한 `ffill` 로 채운 결과가 **상류에서 의도적으로 무효화한 값**(stale cap 등)을
        되살리지 않도록, 그러한 컬럼은 `exclude_cols` 로 반드시 제외해야 한다. (D-10)
        head(최초 관측 이전) 결측은 NaN 으로 남는다. 웜업 구간이 필요하면 데이터셋 head 를 자를 것.

        Returns:
            (imputed_df: pd.DataFrame, report: Dict[str, Any])
        """
        import pandas as pd
        import numpy as np

        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return df, {}

        excluded = {str(c) for c in (exclude_cols or [])}

        df = df.copy()
        report: Dict[str, Any] = {}
        total_rows = len(df)
        cols_to_process = target_cols if target_cols is not None else list(df.columns)

        for col in cols_to_process:
            if col not in df.columns:
                continue
            if col in ("timestamp", "date", "time"):
                continue

            # [D-10] 의도적 무효값은 보간하지 않는다 (stale cap 되살림 방지)
            if col in excluded:
                report[col] = {
                    "source_id": "INTENTIONAL",
                    "source_name": col,
                    "total_rows": total_rows,
                    "raw_missing_count": int(df[col].isna().sum()),
                    "imputed_count": 0,
                    "remaining_missing": int(df[col].isna().sum()),
                    "missing_ratio_pct": round(float(df[col].isna().mean() * 100), 2),
                    "policy": "INTENTIONAL_NULL",
                    "status": "SKIPPED_BY_POLICY",
                }
                continue

            src = cls.find_source_by_column(col)
            fallback = src.get("fallback_policy", {}) if src else {}
            policy = fallback.get("policy", "NONE")

            raw_missing = int(df[col].isna().sum())
            if raw_missing == 0:
                if src:
                    report[col] = {
                        "source_id": src["id"],
                        "source_name": src["name"],
                        "total_rows": total_rows,
                        "raw_missing_count": 0,
                        "imputed_count": 0,
                        "remaining_missing": 0,
                        "missing_ratio_pct": 0.0,
                        "policy": policy,
                        "status": "CLEAN"
                    }
                continue

            # 1. 롤링 역산 환율 폴백 (ROLLING_INVERSE_RATE)
            if policy == "ROLLING_INVERSE_RATE":
                window = int(fallback.get("window", 1440))
                t_col = "target_close" if "target_close" in df.columns else ("upbit_close" if "upbit_close" in df.columns else None)
                b_col = "binance_spot_close" if "binance_spot_close" in df.columns else None
                
                if t_col and b_col:
                    t_series = pd.to_numeric(df[t_col], errors="coerce")
                    b_series = pd.to_numeric(df[b_col], errors="coerce")
                    # [T-3] bfill 금지 — 미래값 역류는 Lookahead (ARCHITECTURE.md PIT-1 정책)
                    approx_rate = (t_series / (b_series + 1e-8)).rolling(window, min_periods=1).mean().ffill()
                    df[col] = df[col].fillna(approx_rate)
                df[col] = df[col].ffill()

            # 2. 직전값 홀딩 보간 (FORWARD_FILL)
            elif policy == "FORWARD_FILL":
                df[col] = df[col].ffill()

            # 3. 0값 보간 (ZERO_FILL)
            elif policy == "ZERO_FILL":
                df[col] = df[col].fillna(0.0)

            # 4. 기본 수치 보간 fallback
            #    ⚠️ [T-3] 종전에는 `ffill().bfill()` 이었다. 카탈로그 미등록 수치 컬럼
            #    (069500/1m 기준 수치 48개 중 38개)이 이 분기로 낙하하여, **head 결측이
            #    미래값으로 역류**하는 Lookahead 가 발생했다.
            #      실측: ref_449190_close 가 원천 최초값(2024-08-21, 15,595.0)을
            #            데이터셋 시작(2024-07-26)까지 **26일 역류**시켰다.
            #    bfill 은 Point-in-Time 무결성을 위반하므로 금지한다 (ARCHITECTURE.md PIT-1).
            #    head 결측은 NaN 으로 남기고, 필요한 경우 웜업 구간을 잘라낼 것.
            elif pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].ffill()

            remaining_missing = int(df[col].isna().sum())
            imputed_count = raw_missing - remaining_missing

            report[col] = {
                "source_id": src["id"] if src else "CUSTOM",
                "source_name": src["name"] if src else col,
                "total_rows": total_rows,
                "raw_missing_count": raw_missing,
                "imputed_count": imputed_count,
                "remaining_missing": remaining_missing,
                "missing_ratio_pct": round(float(raw_missing / total_rows * 100), 2) if total_rows > 0 else 0.0,
                "policy": policy,
                "status": "IMPUTED_CLEAN" if remaining_missing == 0 else "PARTIAL_MISSING"
            }

        return df, report

    @classmethod
    def save_catalog(cls, data: Dict[str, Any]) -> bool:
        """원자적(Atomic Write)으로 data_sources_catalog.json 저장"""
        dir_name = os.path.dirname(_CATALOG_FILE_PATH)
        os.makedirs(dir_name, exist_ok=True)
        temp_name = None
        try:
            from shared.core.file_lock import file_locked
            with file_locked(_CATALOG_FILE_PATH):
                with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8", suffix=".tmp") as tf:
                    temp_name = tf.name
                    json.dump(data, tf, indent=2, ensure_ascii=False)
                os.replace(temp_name, _CATALOG_FILE_PATH)
                cls._cache = data
                cls._last_mtime = os.path.getmtime(_CATALOG_FILE_PATH)
            return True
        except Exception as e:
            logger.error(f"[DataCatalogManager] 카탈로그 저장 실패: {e}")
            if temp_name and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
            return False
