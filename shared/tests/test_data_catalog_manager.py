# -*- coding: utf-8 -*-
"""
DataCatalogManager 단위 테스트
==============================
1. 카탈로그 JSON 로드 및 기본 항목 무결성 검증
2. UPBIT_USDT(2024-06-07) 상장일 이전/이후 스킵 판정 검증
3. KIS_KRX_STOCK 주말 및 공휴일 스킵 판정 검증
4. resolve_source_id 자동 매핑 검증
5. get_skip_summary_for_range 기간별 요약 계산 검증
"""

import os
import pytest
from datetime import datetime, date
from shared.data_library.data_catalog_manager import DataCatalogManager


def test_load_catalog():
    """카탈로그가 정상 로드되고 핵심 소스들이 포함되어 있는지 검증"""
    catalog = DataCatalogManager.load_catalog(force_refresh=True)
    assert isinstance(catalog, dict)
    assert "UPBIT_USDT" in catalog
    assert "UPBIT_CANDLE" in catalog
    assert "BINANCE_SPOT" in catalog
    assert "BINANCE_FUTURES_OI" in catalog
    assert "KIS_KRX_STOCK" in catalog
    assert "CREON_KRX_TICK_CANDLE" in catalog

    usdt = catalog["UPBIT_USDT"]
    assert usdt["availability"]["available_start_date"] == "2024-06-07"
    assert usdt["fallback_policy"]["policy"] == "ROLLING_INVERSE_RATE"


def test_upbit_usdt_skip_logic():
    """업비트 USDT 상장일(2024-06-07) 전후 스킵 동작 검증"""
    # 상장 이전 (2023-06-23) -> 스킵되어야 함
    should_skip, reason = DataCatalogManager.should_skip("UPBIT_USDT", "2023-06-23")
    assert should_skip is True
    assert "2024-06-07" in reason

    # 상장 전날 (2024-06-06) -> 스킵되어야 함
    should_skip, _ = DataCatalogManager.should_skip("UPBIT_USDT", "2024-06-06")
    assert should_skip is True

    # 상장 당일 (2024-06-07) -> 수집 대상 (스킵 안 됨)
    should_skip, _ = DataCatalogManager.should_skip("UPBIT_USDT", "2024-06-07")
    assert should_skip is False

    # 상장 이후 (2025-01-01) -> 수집 대상
    should_skip, _ = DataCatalogManager.should_skip("UPBIT_USDT", "2025-01-01")
    assert should_skip is False


def test_krx_stock_weekend_skip():
    """국내 주식은 주말(토/일) 및 휴장일에 스킵되어야 함"""
    # 2026-09-05는 토요일
    should_skip, reason = DataCatalogManager.should_skip("KIS_KRX_STOCK", "2026-09-05")
    assert should_skip is True
    assert "주말" in reason

    # 2026-09-06은 일요일
    should_skip, reason = DataCatalogManager.should_skip("KIS_KRX_STOCK", "2026-09-06")
    assert should_skip is True

    # 2026-09-04는 금요일 (정상 평일)
    should_skip, _ = DataCatalogManager.should_skip("KIS_KRX_STOCK", "2026-09-04")
    assert should_skip is False


def test_resolve_source_id():
    """제공처 및 키워드 기반 소스 ID 자동 매핑 검증"""
    assert DataCatalogManager.resolve_source_id("UPBIT", "KRW-USDT") == "UPBIT_USDT"
    assert DataCatalogManager.resolve_source_id("UPBIT", "BTC_CANDLE") == "UPBIT_CANDLE"
    assert DataCatalogManager.resolve_source_id("UPBIT", "BTC_TRADE") == "UPBIT_TRADE"
    assert DataCatalogManager.resolve_source_id("BINANCE", "BTCUSDT") == "BINANCE_SPOT"
    assert DataCatalogManager.resolve_source_id("BINANCE", "OPENINTEREST") == "BINANCE_FUTURES_OI"
    assert DataCatalogManager.resolve_source_id("BINANCE", "BTCUSDC") == "BINANCE_USDC"
    assert DataCatalogManager.resolve_source_id("COINBASE", "BTC-USD") == "COINBASE_SPOT"
    assert DataCatalogManager.resolve_source_id("CREON", "TICK") == "CREON_KRX_TICK_CANDLE"


def test_skip_summary_for_range():
    """기간별 스킵 요약 계산 검증"""
    import pandas as pd
    dates = pd.date_range("2023-06-01", "2024-06-10", freq="D").tolist()
    summary = DataCatalogManager.get_skip_summary_for_range("UPBIT_USDT", dates)

    # 2023-06-01 ~ 2024-06-06: 372일 스킵
    # 2024-06-07 ~ 2024-06-10: 4일 유효
    assert summary["skip_count"] == 372
    assert summary["valid_days"] == 4
    assert summary["total_days"] == 376
    assert "2024-06-07" in summary["representative_reason"]


def test_apply_fallback_to_dataframe():
    """카탈로그 기반 동적 결측치 보간 및 품질 감사 리포트 생성 검증"""
    import pandas as pd
    import numpy as np

    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=10, freq="10min"),
        "target_close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0],
        "binance_spot_close": [50.0, 50.5, 51.0, 51.5, 52.0, 52.5, 53.0, 53.5, 54.0, 54.5],
        "binance_futures_oi": [1000.0, np.nan, np.nan, 1050.0, np.nan, 1100.0, np.nan, np.nan, 1200.0, np.nan],
        "upbit_volume": [10.0, np.nan, 20.0, np.nan, 30.0, np.nan, 40.0, np.nan, 50.0, np.nan],
        "upbit_usdt_close": [np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan]
    })

    imputed_df, report = DataCatalogManager.apply_fallback_to_dataframe(df)

    # 1. binance_futures_oi: FORWARD_FILL로 NaN이 0개가 되어야 함
    assert imputed_df["binance_futures_oi"].isna().sum() == 0
    assert imputed_df["binance_futures_oi"].iloc[1] == 1000.0
    assert imputed_df["binance_futures_oi"].iloc[9] == 1200.0

    # 2. upbit_volume: ZERO_FILL로 NaN이 0.0이 되어야 함
    assert imputed_df["upbit_volume"].isna().sum() == 0
    assert imputed_df["upbit_volume"].iloc[1] == 0.0

    # 3. upbit_usdt_close: ROLLING_INVERSE_RATE로 target/binance 비율(약 2.0)로 자동 보간되어야 함
    assert imputed_df["upbit_usdt_close"].isna().sum() == 0
    assert 1.9 < imputed_df["upbit_usdt_close"].iloc[0] < 2.1

    # 4. 리포트 무결성 검증
    assert "binance_futures_oi" in report
    oi_rep = report["binance_futures_oi"]
    assert oi_rep["raw_missing_count"] == 6
    assert oi_rep["imputed_count"] == 6
    assert oi_rep["remaining_missing"] == 0
    assert oi_rep["missing_ratio_pct"] == 60.0
    assert oi_rep["policy"] == "FORWARD_FILL"
    assert oi_rep["status"] == "IMPUTED_CLEAN"

    assert "upbit_usdt_close" in report
    usdt_rep = report["upbit_usdt_close"]
    assert usdt_rep["raw_missing_count"] == 10
    assert usdt_rep["imputed_count"] == 10
    assert usdt_rep["policy"] == "ROLLING_INVERSE_RATE"


# ==============================================================================
# [T-3] Point-in-Time 무결성 — bfill(미래값 역류) 금지 계약
# ==============================================================================

def test_apply_fallback_never_backfills_future_values():
    """
    head 결측이 **미래값으로 역류(bfill)** 하면 안 된다.

    종전에는 카탈로그 미등록 수치 컬럼이 `ffill().bfill()` 기본 분기로 낙하하여,
    데이터셋 시작 이전 구간이 미래 관측값으로 채워졌다.
      실측 피해: 069500/1m 의 ref_449190_close 가 원천 최초값(2024-08-21, 15,595.0)을
                 데이터셋 시작(2024-07-26)까지 26일 역류 → 학습 데이터에 미래 정보 유입.
    """
    import pandas as pd
    import numpy as np

    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=5, freq="10min"),
        "upbit_close": [100.0, 101.0, 102.0, 103.0, 104.0],
        # 카탈로그 미등록 수치 컬럼 + head 결측 (종전 기본 분기에서 bfill 되던 케이스)
        "ref_unknown_close": [np.nan, np.nan, 300.0, 301.0, 302.0],
    })

    out, _ = DataCatalogManager.apply_fallback_to_dataframe(df)

    assert pd.isna(out["ref_unknown_close"].iloc[0]), \
        "head 결측이 미래값(300.0)으로 역류했습니다 — bfill 금지 위반 [T-3]"
    assert pd.isna(out["ref_unknown_close"].iloc[1])
    assert out["ref_unknown_close"].iloc[2] == 300.0
    assert out["ref_unknown_close"].iloc[4] == 302.0


def test_apply_fallback_forward_fill_policy_keeps_head_nan():
    """FORWARD_FILL 정책도 head 결측을 미래값으로 채우지 않아야 한다."""
    import pandas as pd
    import numpy as np

    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=5, freq="10min"),
        "upbit_close": [100.0, 101.0, 102.0, 103.0, 104.0],
        # binance_futures_oi 는 카탈로그 FORWARD_FILL 정책 컬럼
        "binance_futures_oi": [np.nan, np.nan, 1050.0, np.nan, 1100.0],
    })

    out, report = DataCatalogManager.apply_fallback_to_dataframe(df)

    assert pd.isna(out["binance_futures_oi"].iloc[0]), \
        "FORWARD_FILL 정책에서 head 결측이 미래값으로 역류했습니다 [T-3]"
    assert pd.isna(out["binance_futures_oi"].iloc[1])
    assert out["binance_futures_oi"].iloc[2] == 1050.0
    assert out["binance_futures_oi"].iloc[3] == 1050.0   # ffill 은 유지
    assert out["binance_futures_oi"].iloc[4] == 1100.0
    # 보간 리포트는 잔여 결측을 정직하게 보고해야 함
    assert report["binance_futures_oi"]["remaining_missing"] == 2
    assert report["binance_futures_oi"]["status"] == "PARTIAL_MISSING"


def test_no_bfill_remains_in_data_catalog_manager():
    """모듈 소스에 bfill **호출**이 남아 있지 않아야 한다 (정책 준수 자기검증).

    주석/문서 내 'bfill' 언급은 허용하되, 실제 호출은 금지한다 (AST 기반 검사).
    """
    import ast
    import inspect
    from shared.data_library import data_catalog_manager as dcm

    tree = ast.parse(inspect.getsource(dcm))
    bfill_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "bfill"
    ]
    assert not bfill_calls, (
        f"DataCatalogManager 에 bfill 호출이 재도입되었습니다({len(bfill_calls)}건). "
        "bfill 은 결측을 미래값으로 채워 학습 데이터에 Lookahead 를 유입시킵니다. [T-3]"
    )


# ==============================================================================
# [D-10] stale 상한 무효화 값이 하류 보간(ffill)으로 되살아나지 않아야 한다
# ==============================================================================

def test_apply_fallback_exclude_cols_preserves_intentional_nan():
    """
    `exclude_cols` 로 지정한 컬럼은 보간하지 않는다 (의도적 무효값 유지).

    종전 피해 (실측: 069500/10s data_snapshot_10s.meta.json):
      · 상류에서 stale 상한으로 NaN 무효화한 `ref_hynix_close` 를 하류
        `apply_fallback_to_dataframe` 의 ffill 이 되메워 **상한이 완전히 무력화**되었다.
      · meta: `ref_hynix_close` imputed=11,218행 (policy=NONE)
      · 재감사 결과 stale 최대 317시간(13일), >7d 비율 24.5% — 개선 0.
      · 최종 Test 구간에서 ref 값이 상수(nunique=1)로 고정 → 매매 0건.
    """
    import pandas as pd
    import numpy as np

    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=5, freq="10min"),
        "upbit_close": [100.0, 101.0, 102.0, 103.0, 104.0],
        # 상류 stale 상한으로 무효화된 참조 (중간 2행 NaN = 상한 초과 구간)
        "ref_hynix_close": [1000.0, np.nan, np.nan, 1020.0, 1030.0],
        # 무효화 대상이 아닌 일반 컬럼 (ffill 보간되어야 함)
        "binance_futures_oi": [1000.0, np.nan, np.nan, 1050.0, 1100.0],
    })

    out, report = DataCatalogManager.apply_fallback_to_dataframe(
        df, exclude_cols=["ref_hynix_close"]
    )

    # 1. 무효화 값은 그대로 NaN 유지 (되채움 금지)
    assert out["ref_hynix_close"].isna().sum() == 2, \
        "exclude_cols 컬럼이 보간으로 되채워졌습니다 — stale 상한 무력화 [D-10]"
    assert out["ref_hynix_close"].iloc[0] == 1000.0
    assert pd.isna(out["ref_hynix_close"].iloc[1])
    assert pd.isna(out["ref_hynix_close"].iloc[2])
    assert out["ref_hynix_close"].iloc[4] == 1030.0

    # 2. 제외되지 않은 컬럼은 종전대로 보간
    assert out["binance_futures_oi"].isna().sum() == 0
    assert out["binance_futures_oi"].iloc[1] == 1000.0

    # 3. 리포트는 무효값을 '보간됨'으로 계상하지 않고 정직하게 보고
    rep = report["ref_hynix_close"]
    assert rep["imputed_count"] == 0, "무효화 컬럼을 보간 건수에 계상하면 안 됨"
    assert rep["policy"] == "INTENTIONAL_NULL"
    assert rep["status"] == "SKIPPED_BY_POLICY"
    assert rep["remaining_missing"] == 2


def test_d10_stale_cap_nan_survives_pipeline_imputation():
    """
    [D-10 회귀 계약] 배치 빌더의 **실제 순서**를 재현한다:
        merge_asof_with_stale_cap(무효화)  →  apply_fallback_to_dataframe(보간)

    보간이 무효화를 되살리면 상한 정책(T-3)은 파이프라인에서 무효다.
    빌더는 무효화 컬럼 목록을 `exclude_cols` 로 반드시 전달해야 한다.
    """
    import pandas as pd
    from shared.data_library.reference_staleness import merge_asof_with_stale_cap

    target = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2024-01-01", "2024-01-02", "2024-01-10", "2024-01-20", "2024-01-21"
        ]),
        "upbit_close": [100.0, 101.0, 102.0, 103.0, 104.0],
    })
    # 참조 원천이 2024-01-02 이후 끊김 (실측: KRX 대장주 A000660 원천이 2026-09-02 종료)
    ref = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        "ref_hynix_close": [1000.0, 1010.0],
    })
    value_cols = ["ref_hynix_close"]

    capped = merge_asof_with_stale_cap(
        target, ref, value_cols, cap=pd.Timedelta(days=7)
    )
    assert pd.isna(capped["ref_hynix_close"].iloc[3]), \
        "상한(7일) 초과 구간이 무효화되지 않았습니다 — T-3 위반"

    out, _ = DataCatalogManager.apply_fallback_to_dataframe(
        capped, exclude_cols=value_cols
    )

    assert pd.isna(out["ref_hynix_close"].iloc[3]), (
        "stale 상한으로 무효화한 값이 하류 보간(ffill)으로 되살아났습니다 — "
        "cap 정책이 파이프라인에서 무력화됩니다 [D-10]. "
        "빌더는 무효화 컬럼을 exclude_cols 로 전달해야 합니다."
    )
    # 상한 내 구간은 보존되어야 함 (과잉 무효화 방지)
    assert out["ref_hynix_close"].iloc[1] == 1010.0

