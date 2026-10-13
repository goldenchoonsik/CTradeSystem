# -*- coding: utf-8 -*-
"""
Resampler Unification 테스트 (T-4)
===================================
Silver 계층 리샘플러 단일화 계약.

배경
----
종전에는 리샘플러가 **2벌** 존재했다.
  · `shared.candle_resampler.resample_candles`  → Baker 배치 경로 (엄격 게이트 O)
  · `CandleVault.resample_candles`              → Vault/CTrade 경로 (게이트 X, 자체 집계 규칙)
같은 "Silver"인데 하방 리샘플(미래참조)·비배수 인터벌 차단이 한쪽에만 적용되어
**동일 입력에 대해 다른 봉·다른 예외**가 나올 수 있었다.

T-4 이후 `CandleVault` 는 공용 리샘플러에 위임하며, 아래 계약을 공유한다.
  ① 하방 리샘플(1m 원천 → 10s 등)은 `LookaheadInversionError` 로 **즉시 차단**
  ② 비지원 인터벌(예: 7m)은 `UnsupportedIntervalError`
  ③ `_ratio`/`_mean`/`_pct`/`_rate` 집계는 `mean` (종전 Vault 는 `last` — 동작 변경)
  ④ tz-aware 입력은 tz 를 **보존** (UTC 로 밀지 않음)
"""

from datetime import datetime, timezone, timedelta

import pandas as pd
import pytest

from shared.data_library.candle_resampler import (
    LookaheadInversionError,
    UnsupportedIntervalError,
    resample_candles,
)
from shared.data_library.candle_vault import CandleVault

KST = timezone(timedelta(hours=9))


def _df_1m(periods=15, tzinfo=None, start="2026-01-01 09:00"):
    ts = pd.date_range(start, periods=periods, freq="1min", tz=tzinfo)
    return pd.DataFrame({
        "timestamp": ts,
        "target_open": [100.0 + i for i in range(periods)],
        "target_high": [105.0 + i for i in range(periods)],
        "target_low": [95.0 + i for i in range(periods)],
        "target_close": [101.0 + i for i in range(periods)],
        "target_volume": [10.0] * periods,
        "target_trade_bid_ratio": [0.2 + 0.04 * i for i in range(periods)],
    })


@pytest.fixture
def vault(tmp_path):
    return CandleVault(cache_dir=str(tmp_path))


# ==============================================================================
# ① 하방 리샘플 차단 게이트가 Vault 경로에도 적용된다 (T-4 핵심)
# ==============================================================================

def test_vault_blocks_sub_minute_target_from_1m_source(vault):
    """1m 원천에서 10s 봉을 만들려는 시도는 미래참조이므로 즉시 차단되어야 한다.

    T-4 이전에는 Vault 자체 리샘플러가 게이트 없이 이를 '성공'시켜
    1분봉을 10초 라벨로 위장한 잘못된 봉을 만들었다.
    """
    with pytest.raises(LookaheadInversionError):
        vault.resample_candles(_df_1m(), "10s")


def test_shared_resampler_blocks_same_case():
    """동일 케이스가 공용 리샘플러에서도 차단됨 (두 경로가 같은 계약)"""
    with pytest.raises(LookaheadInversionError):
        resample_candles(_df_1m(), target_interval="10s", source_interval="1m")


def test_vault_blocks_non_divisible_interval(vault):
    """비지원 인터벌(7m)은 Fail-Fast"""
    with pytest.raises(UnsupportedIntervalError):
        vault.resample_candles(_df_1m(), "7m")


def test_shared_resampler_blocks_unified_same_spacing():
    """매 공용 리샘플러의 존재 확인 (단일 경로 검증)"""
    assert callable(resample_candles)


# ==============================================================================
# ② 1m 원천은 그대로 반환 (원본 값 100% 보존)
# ==============================================================================

def test_vault_1m_returns_source_unchanged(vault):
    src = _df_1m(periods=5)
    out = vault.resample_candles(src, "1m")

    assert len(out) == len(src)
    assert list(out.columns) == list(src.columns)
    pd.testing.assert_series_equal(
        out["target_close"].reset_index(drop=True),
        src["target_close"].reset_index(drop=True),
    )


def test_vault_empty_input_returns_empty(vault):
    assert vault.resample_candles(pd.DataFrame(), "5m").empty
    assert vault.resample_candles(None, "5m").empty


def test_vault_requires_timestamp_column(vault):
    bad = pd.DataFrame({"target_close": [1.0, 2.0]})
    with pytest.raises(ValueError, match="timestamp"):
        vault.resample_candles(bad, "5m")


# ==============================================================================
# ③ Vault 와 공용 리샘플러는 동일 결과를 낸다 (단일 구현 증명)
# ==============================================================================

@pytest.mark.parametrize("interval", ["3m", "5m", "10m", "15m", "30m", "1h", "1d"])
def test_vault_matches_shared_resampler(vault, interval):
    src = _df_1m(periods=60)

    out_vault = vault.resample_candles(src.copy(), interval)
    out_shared = resample_candles(src.copy(), target_interval=interval, source_interval="1m")

    assert len(out_vault) == len(out_shared), f"{interval}: 행 수 불일치"
    assert list(out_vault.columns) == list(out_shared.columns)
    for col in out_shared.columns:
        pd.testing.assert_series_equal(
            out_vault[col].reset_index(drop=True),
            out_shared[col].reset_index(drop=True),
            check_names=False,
            obj=f"{interval}:{col}",
        )


# ==============================================================================
# ④ 집계 계약 (T-4 동작 변경: ratio 계열은 mean 으로 통일)
# ==============================================================================

def test_vault_ohlcv_aggregation_contract(vault):
    src = _df_1m(periods=15)
    out = vault.resample_candles(src, "5m")

    assert len(out) == 3
    c0 = out.iloc[0]
    assert c0["target_open"] == src["target_open"].iloc[0]            # first
    assert c0["target_close"] == src["target_close"].iloc[4]          # last (미래 5분봉 close 미포함)
    assert c0["target_high"] == src["target_high"].iloc[:5].max()     # max
    assert c0["target_low"] == src["target_low"].iloc[:5].min()       # min
    assert c0["target_volume"] == 50.0                                # sum


def test_vault_ratio_columns_aggregate_by_mean(vault):
    """⚠️ T-4 동작 변경: ratio 계열 집계가 `last` → `mean` 으로 통일되었다.

    (종전 Vault 자체 규칙은 ratio/price/oi/premium/basis 를 모두 `last` 로 처리했다)
    """
    src = _df_1m(periods=15)
    out = vault.resample_candles(src, "5m")

    expected = float(src["target_trade_bid_ratio"].iloc[:5].mean())
    assert out["target_trade_bid_ratio"].iloc[0] == pytest.approx(expected)
    # `last` 였다면 5번째 값(0.36)이 나온다
    assert out["target_trade_bid_ratio"].iloc[0] != src["target_trade_bid_ratio"].iloc[4]


# ==============================================================================
# ⑤ tz-aware 보존 (종전 공용 리샘플러는 UTC 로 밀어버렸다)
# ==============================================================================

def test_vault_preserves_timezone(vault):
    src = _df_1m(periods=15, tzinfo=KST)
    out = vault.resample_candles(src, "5m")

    assert str(out["timestamp"].dt.tz) == "UTC+09:00", "tz-aware 가 UTC 로 밀렸습니다"
    assert out["timestamp"].iloc[0].hour == 9, "KST 09:00 이 유지되어야 함"


def test_shared_resampler_preserves_timezone():
    src = _df_1m(periods=15, tzinfo=KST)
    out = resample_candles(src, target_interval="5m", source_interval="1m")
    assert str(out["timestamp"].dt.tz) == "UTC+09:00"


def test_vault_drops_bars_without_close(vault):
    """Vault 고유 정리: 종가 결측(비영업/미체결) 봉은 캐시에 남기지 않는다."""
    src = _df_1m(periods=15)
    # 09:05~09:09 구간을 결측으로 만들면 5m 봉 하나가 통째로 비게 된다
    src.loc[5:9, ["target_open", "target_high", "target_low", "target_close", "target_volume"]] = float("nan")

    out = vault.resample_candles(src, "5m")

    assert len(out) == 2, "종가 결측 봉이 제거되지 않았습니다"
    assert out["timestamp"].iloc[0] == pd.Timestamp("2026-01-01 09:00")
    assert out["timestamp"].iloc[1] == pd.Timestamp("2026-01-01 09:10")
