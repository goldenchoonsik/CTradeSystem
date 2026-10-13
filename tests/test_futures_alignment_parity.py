# -*- coding: utf-8 -*-
"""
Test Futures Alignment Parity & Lookahead Prevention
=====================================================
해외 선물(NQ, ES) 및 다층 시간단위 캔들 결합 시
Lookahead Bias(미래 종가 참조) 및 bfill 역류 누출이 100% 차단되었는지 검증.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from shared.data_library.feature_composer import FeatureComposer


def test_60m_futures_lookahead_prevention():
    """60분봉 캔들의 09:00 종가가 09:01~09:59 장중에 누출되지 않고 10:00 이후에만 매칭되는지 검증"""
    # 1. 타겟 1분봉 (09:00 ~ 10:30)
    timestamps = pd.date_range("2026-04-15 09:00:00", "2026-04-15 10:30:00", freq="1min")
    target_df = pd.DataFrame({
        "timestamp": timestamps,
        "target_close": np.linspace(100, 110, len(timestamps))
    })

    # 2. 매크로 60분봉 (08:00, 09:00, 10:00)
    # 08:00 봉 종가: 5000 (09:00에 확정)
    # 09:00 봉 종가: 5050 (10:00에 확정)
    macro_timestamps = pd.to_datetime(["2026-04-15 08:00:00", "2026-04-15 09:00:00", "2026-04-15 10:00:00"])
    macro_candles = pd.DataFrame({
        "timestamp": macro_timestamps,
        "close": [5000.0, 5050.0, 5100.0]
    })

    # Mock candle_vault
    class MockCandleVault:
        def get_candles(self, symbol, interval):
            return macro_candles

    composer = FeatureComposer(candle_vault=MockCandleVault())
    result = composer.compose(
        target_base_df=target_df,
        reference_specs=[],
        macro_specs=[{"symbol": "ES", "interval": "60m"}],
        target_symbol="069500", target_interval="1m",
    )

    result_indexed = result.set_index("timestamp")

    # 09:00:00 에는 08:00 봉의 종가(5000.0)가 매칭되어야 함
    assert result_indexed.loc["2026-04-15 09:00:00", "ref_es_close"] == 5000.0
    # 09:01:00 ~ 09:59:00 사이에는 여전히 08:00 봉 종가(5000.0)여야 함! (09:00 봉 종가 5050.0 누출 금지!)
    assert result_indexed.loc["2026-04-15 09:01:00", "ref_es_close"] == 5000.0
    assert result_indexed.loc["2026-04-15 09:30:00", "ref_es_close"] == 5000.0
    assert result_indexed.loc["2026-04-15 09:59:00", "ref_es_close"] == 5000.0

    # 10:00:00 정각이 되어서야 09:00 봉의 종가(5050.0)가 반영되어야 함!
    assert result_indexed.loc["2026-04-15 10:00:00", "ref_es_close"] == 5050.0
    assert result_indexed.loc["2026-04-15 10:01:00", "ref_es_close"] == 5050.0


def test_no_bfill_backward_leakage():
    """데이터 시작 시점 이전의 과거 구간에 미래 첫 데이터가 bfill로 역류하지 않는지 검증"""
    timestamps = pd.date_range("2026-04-15 07:00:00", "2026-04-15 09:30:00", freq="1min")
    target_df = pd.DataFrame({
        "timestamp": timestamps,
        "target_close": 100.0
    })

    # 09:00부터 데이터가 시작되는 해외 선물
    macro_candles = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-04-15 09:00:00"]),
        "close": [5050.0]
    })

    class MockCandleVault:
        def get_candles(self, symbol, interval):
            return macro_candles

    composer = FeatureComposer(candle_vault=MockCandleVault())
    result = composer.compose(
        target_base_df=target_df,
        reference_specs=[],
        macro_specs=[{"symbol": "NQ", "interval": "1m"}],
        target_symbol="069500", target_interval="1m",
    )

    result_indexed = result.set_index("timestamp")

    # 09:00 이전에는 데이터가 없으므로 NaN이어야 함 (bfill로 5050.0이 채워지면 안 됨!)
    assert pd.isna(result_indexed.loc["2026-04-15 08:00:00", "ref_nq_close"])
    assert pd.isna(result_indexed.loc["2026-04-15 08:59:00", "ref_nq_close"])

    # 1m 봉이므로 09:00 봉의 종가는 09:01에 반영
    assert result_indexed.loc["2026-04-15 09:01:00", "ref_nq_close"] == 5050.0


def test_validate_downloaded_data_10s_futures_fallback():
    """초 단위(10s) 검증 시 KOSPI 200 선물/지수가 1m 파일로 자동 매핑되어 결측률 0%로 판정되는지 검증"""
    from CAiModelBaker.core.data_manager import validate_downloaded_data
    config = {
        'SYMBOL_UPBIT': '069500',
        'INTERVAL': '10s',
        'ARCHIVE_START_YM': '2025-01',
        'ARCHIVE_END_YM': '2025-03'
    }
    res = validate_downloaded_data(config=config, start_ym="2025-01", end_ym="2025-03", interval="10s")
    items = res.get("items", {})
    assert "creon_futures" in items
    assert "creon_spot" in items
    assert items["creon_futures"]["file_exists"] is True
    assert items["creon_futures"]["rate"] == 100.0
    assert items["creon_spot"]["file_exists"] is True
    assert items["creon_spot"]["rate"] == 100.0


# ==============================================================================
# T-1: 봉 시작 시각(Bar-Start) 판정 단일화 — Lookahead 누락 결함 회귀 방지
# ==============================================================================

def test_timestamp_convention_comes_from_the_ledger_only():
    """
    [T-6] 캔들 타임스탬프 규약은 원장(DataVault/config/timestamp_conventions.json)에만 있다.

    이전에는 종목 이름 토큰 목록(["NQ","ES","YM","RTY","CL","GC"])과 market_type 추측으로
    판정했고, **기본값이 '시프트 안 함'** 이라 선언이 빠진 신규 자산에서 미래참조가 조용히 재발했다.
    이제는 수집처 선언이 없으면 추측하지 않고 거부한다(fail-closed).
    """
    from shared.config.market_policies import (
        BAR_CLOSE,
        BAR_OPEN,
        UnknownTimestampConventionError,
        timestamp_convention,
    )

    # 1) 봉 시작 시각 원천 (결합 시 확정 시각으로 밀어야 하는 자산)
    for sym in ["NQ", "ES", "US_TECH_100", "US_500", "XAUUSD", "XTIUSD", "USDJPY",
                "JAPAN_225", "HONG_KONG_50", "KRW-BTC", "BTCUSDT", "BTCUSDT-PERP"]:
        assert timestamp_convention(sym, "1m") == BAR_OPEN, f"{sym} 은 봉 시작 시각 원천"

    # 2) 봉 확정 시각 원천 (밀면 오히려 1봉 과지연이 된다)
    for sym in ["069500", "10100", "U180", "005930", "000660"]:
        assert timestamp_convention(sym, "1m") == BAR_CLOSE, f"{sym} 은 봉 확정 시각 원천"

    # 3) 같은 수집처라도 인터벌마다 규약이 다를 수 있다
    #    (CREON 1분봉은 수집처가 준 봉 확정 라벨, 10초봉은 우리가 틱에서 만든 봉 시작 라벨)
    assert timestamp_convention("069500", "10s") == BAR_OPEN
    assert timestamp_convention("069500", "1m") == BAR_CLOSE

    # 4) 선언이 없으면 '아니오'가 아니라 '모른다' — 추측 금지.
    #    NVDA-PERP/US10Y/WTI/GOLD 는 종목 원장에는 있지만 수집처 원장에 없다.
    #    (어떤 결합 계획에도 쓰이지 않는다. 쓰려면 먼저 수집처를 선언해야 한다 — 그게 fail-closed 다)
    for sym in ["NVDA-PERP", "US10Y", "WTI", "GOLD", "AAPL", "SOME-NEW-TICKER", ""]:
        with pytest.raises(UnknownTimestampConventionError):
            timestamp_convention(sym, "1m")


def test_reference_merge_shift_is_decision_point_based():
    """[T-6] 결합 보정값은 '의사결정 시점' 기준으로 계산된다.

    같은 참조라도 타겟의 봉 라벨 규약/길이에 따라 보정값이 달라진다.
    이 계산이 한 곳에 있지 않으면 조립기마다 다른 값이 나온다.
    """
    from shared.config.market_policies import reference_merge_shift

    # 같은 규약·같은 인터벌이면 보정 없음 (기존 동작 보존)
    assert reference_merge_shift("005930", "10s", "069500", "10s") == pd.Timedelta(0)
    # 봉 시작 라벨 1분봉 → 10초봉 타겟: 봉이 닫히는 시점에 맞춰 50초 뒤로
    assert reference_merge_shift("US_TECH_100", "1m", "069500", "10s") == pd.Timedelta(seconds=50)
    # 봉 시작 라벨 1분봉 → 1분봉 타겟(봉 확정 라벨): 1분 뒤로
    assert reference_merge_shift("US_TECH_100", "1m", "069500", "1m") == pd.Timedelta(minutes=1)
    # 봉 확정 라벨 1분봉 → 1분봉 타겟: 보정 없음
    assert reference_merge_shift("10100", "1m", "069500", "1m") == pd.Timedelta(0)


def test_unmapped_symbol_is_refused_not_guessed():
    """
    [T-6 회귀] 수집처가 선언되지 않은 심볼은 결합을 **거부**해야 한다.

    이전 구현은 종목 이름/시장 구분으로 규약을 추측했고 기본값이 '시프트 안 함'이었다.
    그래서 선언이 빠진 자산은 조용히 1봉 미래참조로 학습에 들어갔다.
    (NVDA-PERP 는 종목 원장에는 있으나 수집처 원장에 없다)
    """
    from shared.config.market_policies import UnknownTimestampConventionError

    timestamps = pd.date_range("2026-04-15 09:00:00", "2026-04-15 09:03:00", freq="1min")
    target_df = pd.DataFrame({
        "timestamp": timestamps,
        "target_close": np.linspace(100, 103, len(timestamps)),
    })
    nvda_candles = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-04-15 09:00:00", "2026-04-15 09:01:00", "2026-04-15 09:02:00"]),
        "close": [125.0, 126.0, 127.0],
    })

    class MockCandleVault:
        def get_candles(self, symbol, interval):
            return nvda_candles

    composer = FeatureComposer(candle_vault=MockCandleVault())
    with pytest.raises(UnknownTimestampConventionError):
        composer.compose(
            target_base_df=target_df,
            reference_specs=[{"symbol": "NVDA-PERP", "interval": "1m"}],
            target_symbol="069500", target_interval="1m",
        )
