# -*- coding: utf-8 -*-
"""
KRX 데이터 무결성 3대 하드페일 가드 검증 테스트
1. 일자별 고유 분 결손 (< 380분) 차단
2. 참조 대장주 결측률 (>= 50%) 차단
3. 30분 이상 동일 가격 수평선(stale-ffill) 차단
4. 클린 데이터셋 통과
"""
import pytest
import pandas as pd
import numpy as np
from CAiModelBaker.core.data_manager import validate_snapshot_integrity


def _make_clean_krx_day(date_str: str, freq: str = "10s") -> pd.DataFrame:
    """정상 KRX 세션 (09:00:00 ~ 15:20:00, 381분) 합성 데이터"""
    # 09:00 ~ 15:20 = 380분 + 09:00분 = 총 381 고유 분
    ts = pd.date_range(f"{date_str} 09:00:00", f"{date_str} 15:20:00", freq=freq)
    n = len(ts)
    np.random.seed(42)
    # 현실적인 랜덤 워크 가격 및 거래량 생성
    prices = 100000.0 + np.cumsum(np.random.randn(n) * 10)
    vols = np.random.randint(1, 100, size=n).astype(float)
    return pd.DataFrame({
        "timestamp": ts,
        "target_close": prices,
        "target_volume": vols,
        "ref_samsung_close": prices * 0.7,
    })


def test_guard_passes_clean_dataset():
    """381분 완벽하고 결측/수평선 없는 클린 KRX 데이터는 게이트를 통과해야 한다."""
    df = _make_clean_krx_day("2026-09-11", freq="10s")
    assert validate_snapshot_integrity(df, "069500") is True


def test_guard_blocks_low_minute_density():
    """하루 고유 분이 380분 미만(예: 09-14처럼 9분만 존재)이면 즉시 차단되어야 한다."""
    # 9분치만 존재하는 불량 데이터
    ts = pd.date_range("2026-09-14 10:45:00", "2026-09-14 10:53:50", freq="10s")
    # 최소 380행 조건을 채우기 위해 10s로 생성 (54행) + 더미 행
    ts_expanded = pd.date_range("2026-09-14 10:00:00", "2026-09-14 11:00:00", freq="5s") # 60분
    df = pd.DataFrame({
        "timestamp": ts_expanded,
        "target_close": [105000.0] * len(ts_expanded),
        "target_volume": [10.0] * len(ts_expanded),
    })
    with pytest.raises(ValueError, match="고유 분 < 380분 결손"):
        validate_snapshot_integrity(df, "069500")


def test_guard_blocks_high_missing_ref():
    """참조 대장주 컬럼의 일일 결측률이 50% 이상이면 차단되어야 한다."""
    df = _make_clean_krx_day("2026-09-11", freq="10s")
    # ref_samsung_close 의 60%를 NaN으로 오염
    n = len(df)
    df.loc[:int(n * 0.6), "ref_samsung_close"] = np.nan
    with pytest.raises(ValueError, match="참조 지표 심각 결측"):
        validate_snapshot_integrity(df, "069500")


def test_guard_blocks_flatline_stale_ffill():
    """30분(180개 10초 봉) 이상 가격이 불변이고 거래량이 0인 가짜 수평선은 차단되어야 한다."""
    df = _make_clean_krx_day("2026-09-11", freq="10s")
    # 중간 200개 봉(약 33분)을 동일 가격 + 거래량 0으로 수평선 박제
    df.loc[500:700, "target_close"] = 109500.0
    df.loc[500:700, "target_volume"] = 0.0
    with pytest.raises(ValueError, match="비정상 가격 수평선 감지"):
        validate_snapshot_integrity(df, "069500")

