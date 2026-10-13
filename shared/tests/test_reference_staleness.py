# -*- coding: utf-8 -*-
"""
Reference Staleness Policy 테스트 (T-3 / D-1)
==============================================
1. resolve_stale_cap : 자산군/인터벌/override 별 상한 결정
2. apply_stale_cap   : ffill 전파 상한 초과 구간 무효화
3. merge_asof_with_stale_cap : 미래참조 차단 + 무한 ffill 차단 + 대체값 계약

배경 실측 (scripts/audit_reference_staleness.py)
  069500/1m ref_449190_close 가 624시간(26일) 연속 동일값으로 전파되었고,
  069500/10s ref_hynix_close 는 20.5% 행이 7일 이상 stale 이었다.
"""

import pandas as pd
import numpy as np
import pytest

from shared.data_library.reference_staleness import (
    apply_stale_cap,
    merge_asof_with_stale_cap,
    resolve_stale_cap,
)


# ==============================================================================
# 1. 상한 정책 결정
# ==============================================================================

def test_resolve_stale_cap_by_market_type():
    assert resolve_stale_cap("CRYPTO") == pd.Timedelta(days=1)
    assert resolve_stale_cap("GLOBAL_FUTURES") == pd.Timedelta(days=4)
    assert resolve_stale_cap("KRX_ETF") == pd.Timedelta(days=7)
    assert resolve_stale_cap("KRX_STOCK") == pd.Timedelta(days=7)


def test_resolve_stale_cap_daily_interval_widens_cap():
    assert resolve_stale_cap("GLOBAL_FUTURES", interval="1d") == pd.Timedelta(days=7)
    assert resolve_stale_cap("CRYPTO", interval="1w") == pd.Timedelta(days=7)


def test_resolve_stale_cap_prefix_and_fallback():
    # 접두어 매칭 (KRX_ETF_SOMETHING → KRX_ETF)
    assert resolve_stale_cap("KRX_ETF_LEVERAGE") == pd.Timedelta(days=7)
    # 미등록/미지정 → 보수적 기본값
    assert resolve_stale_cap(None) == pd.Timedelta(days=7)
    assert resolve_stale_cap("UNKNOWN_MARKET") == pd.Timedelta(days=7)


def test_resolve_stale_cap_override_wins():
    assert resolve_stale_cap("CRYPTO", override="12h") == pd.Timedelta(hours=12)
    assert resolve_stale_cap("CRYPTO", override=pd.Timedelta(days=2)) == pd.Timedelta(days=2)


# ==============================================================================
# 2. apply_stale_cap
# ==============================================================================

def test_apply_stale_cap_invalidates_beyond_cap():
    ts = pd.to_datetime(["2026-01-01 09:00", "2026-01-02 09:00", "2026-01-10 09:00"])
    src = pd.to_datetime(["2026-01-01 09:00"] * 3)   # 이후로 원천이 끊긴 상황
    values = pd.Series([100.0, 100.0, 100.0])

    out = apply_stale_cap(ts, src, values, cap=pd.Timedelta(days=3))

    assert out.iloc[0] == 100.0                      # age 0
    assert out.iloc[1] == 100.0                      # age 1일 (cap 내)
    assert pd.isna(out.iloc[2]), "cap(3일) 초과 stale 값은 무효화되어야 함 (D-1)"


def test_apply_stale_cap_nat_source_is_invalidated():
    """참조 원천이 아예 없는 구간(NaT)도 무효로 간주한다 (백필 유입 차단)."""
    ts = pd.to_datetime(["2026-01-01 09:00", "2026-01-01 09:01"])
    src = pd.to_datetime([pd.NaT, pd.NaT])
    values = pd.Series([111.0, 111.0])

    out = apply_stale_cap(ts, src, values, cap=pd.Timedelta(days=1))

    assert out.isna().all()


def test_apply_stale_cap_fill_value_for_volume():
    ts = pd.to_datetime(["2026-01-01", "2026-02-01"])
    src = pd.to_datetime(["2026-01-01", "2026-01-01"])
    values = pd.Series([5.0, 5.0])

    out = apply_stale_cap(ts, src, values, cap=pd.Timedelta(days=7), fill_value=0.0)

    assert out.iloc[0] == 5.0
    assert out.iloc[1] == 0.0


# ==============================================================================
# 3. merge_asof_with_stale_cap — D-1 회귀 방지
# ==============================================================================

def _target(periods=20, start="2026-01-01 09:00"):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=periods, freq="1D"),
        "target_close": np.arange(periods, dtype=float) + 100.0,
    })


def test_merge_asof_stale_cap_blocks_unbounded_ffill():
    """참조 원천이 5일에서 끊겨도 cap(7일)을 넘겨 전파되면 안 된다 (D-1 핵심)."""
    target = _target(periods=20)
    ref = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01 09:00", periods=5, freq="1D"),
        "ref_x_close": [10.0, 11.0, 12.0, 13.0, 14.0],
    })

    out = merge_asof_with_stale_cap(target, ref, ["ref_x_close"], cap=pd.Timedelta(days=7))

    # 5일차(마지막 원천) + 7일 = 12일차까지는 유지
    assert out.loc[4, "ref_x_close"] == 14.0
    assert out.loc[11, "ref_x_close"] == 14.0
    # 그 이후는 무효 (종전에는 ffill 로 영구 전파되었다)
    assert pd.isna(out.loc[12, "ref_x_close"]), "cap 초과 후에도 값이 전파됨 — D-1 미해결"
    assert pd.isna(out.loc[19, "ref_x_close"])


def test_merge_asof_stale_cap_keeps_holiday_gaps():
    """정상 휴장(예: 5일 연휴)은 cap 내이므로 값이 유지되어야 한다 (과잉 무효화 방지)."""
    target = _target(periods=12)
    ref = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-01 09:00", "2026-01-07 09:00"]),
        "ref_y_close": [100.0, 200.0],
    })

    out = merge_asof_with_stale_cap(target, ref, ["ref_y_close"], cap=pd.Timedelta(days=7))

    assert out.loc[0, "ref_y_close"] == 100.0
    assert out.loc[5, "ref_y_close"] == 100.0   # 5일 경과 → cap 내 유지
    assert out.loc[6, "ref_y_close"] == 200.0   # 새 값 반영


def test_merge_asof_stale_cap_head_gap_is_nan_not_backfilled():
    """참조가 늦게 시작하는 head 구간은 NaN 이어야 한다 (bfill 금지 = 미래참조 차단)."""
    target = _target(periods=10)
    ref = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-05 09:00", periods=3, freq="1D"),
        "ref_z_close": [500.0, 501.0, 502.0],
    })

    out = merge_asof_with_stale_cap(
        target, ref, ["ref_z_close"],
        cap=pd.Timedelta(days=7),
    )

    assert pd.isna(out.loc[0, "ref_z_close"])
    assert pd.isna(out.loc[3, "ref_z_close"]), "head 결측이 미래값으로 역류하면 안 됨 (T-3)"
    assert out.loc[4, "ref_z_close"] == 500.0   # 최초 관측 시점부터 유효


def test_merge_asof_stale_cap_fill_values_and_column_order():
    target = _target(periods=20)
    ref = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01 09:00", periods=3, freq="1D"),
        "ref_a_close": [1.0, 2.0, 3.0],
        "ref_a_volume": [10.0, 20.0, 30.0],
    })

    out = merge_asof_with_stale_cap(
        target, ref, ["ref_a_close", "ref_a_volume"],
        cap=pd.Timedelta(days=7),
        fill_values={"ref_a_volume": 0.0},
    )

    assert list(out.columns) == ["timestamp", "target_close", "ref_a_close", "ref_a_volume"]
    assert pd.isna(out.loc[19, "ref_a_close"])
    assert out.loc[19, "ref_a_volume"] == 0.0, "거래량류는 0.0 으로 무효화"


def test_merge_asof_stale_cap_missing_column_raises():
    target = _target(periods=3)
    ref = pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=2, freq="1D")})
    with pytest.raises(ValueError, match="결합 대상 컬럼"):
        merge_asof_with_stale_cap(target, ref, ["ref_missing_close"], cap=pd.Timedelta(days=7))


def test_merge_asof_stale_cap_empty_inputs_are_safe():
    target = _target(periods=3)
    empty = pd.DataFrame(columns=["timestamp", "ref_x_close"])
    assert merge_asof_with_stale_cap(target, empty, ["ref_x_close"], cap=pd.Timedelta(days=7)) is target
    assert merge_asof_with_stale_cap(pd.DataFrame(), empty, ["ref_x_close"], cap=pd.Timedelta(days=1)).empty
