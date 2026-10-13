# -*- coding: utf-8 -*-
"""KRX 수정주가 일봉 정규화 검사 + 일 단위 배분 검증 도구 (2026-10-10)"""

import os

import numpy as np
import pandas as pd
import pytest

from CAiModelBaker.core.normalized_builder import (build_normalized_krx_daily, check_adjusted_daily,
                                                   session_close_prices)


def _days(n=10):
    return pd.bdate_range("2025-01-06", periods=n)


def test_adjusted_series_with_distribution_step_is_valid():
    """정상 수정주가: 분배락일에 (수정/실제) 비율이 한 번 올라가고 마지막 날은 1."""
    idx = _days()
    actual = pd.Series(np.linspace(100, 110, 10), index=idx)
    adj = actual.copy()
    adj.iloc[:5] *= 0.98                                    # 분배금 2% 만큼 과거가 깎임
    assert check_adjusted_daily(adj, actual) == []


def test_mixed_adjustment_basis_is_detected():
    """증분 수집으로 옛 행(옛 기준)과 새 행(새 기준)이 섞이면 비율이 내려가는 계단이 생긴다."""
    idx = _days()
    actual = pd.Series(np.linspace(100, 110, 10), index=idx)
    adj = actual.copy()
    adj.iloc[5:8] *= 0.98                                   # 새로 받은 행 일부만 새 기준
    problems = check_adjusted_daily(adj, actual)
    assert any("내려감" in p for p in problems)


def test_stale_basis_on_last_day_is_detected():
    idx = _days()
    actual = pd.Series(np.linspace(100, 110, 10), index=idx)
    assert any("마지막 날" in p for p in check_adjusted_daily(actual * 0.97, actual))


def test_session_close_uses_special_session_calendar(monkeypatch):
    """수능일처럼 마감이 늦는 날은 15:30 이 아니라 장 일정 원장의 마감 시각 봉을 쓴다."""
    import datetime as dt
    from shared.core import market_utils as mu
    special = pd.Timestamp("2025-11-13")
    real = mu.krx_session

    def fake(day):
        if pd.Timestamp(day).normalize() == special:
            return mu.KrxSession(open=dt.time(10, 0), close=dt.time(16, 30))
        return real(day)

    monkeypatch.setattr(mu, "krx_session", fake)
    idx = pd.to_datetime(["2025-11-13 15:30", "2025-11-13 16:30", "2025-11-14 15:30"])
    s = pd.Series([1.0, 2.0, 3.0], index=idx)
    out = session_close_prices(s)
    assert out.loc[special] == 2.0 and out.loc[pd.Timestamp("2025-11-14")] == 3.0


def test_builder_rejects_mixed_basis(tmp_path, monkeypatch):
    from shared.data_library import paths as dpaths
    raw = tmp_path / "A999999_1d.csv"
    idx = _days()
    closes = np.linspace(100, 110, 10)
    adj = closes.copy()
    adj[5:8] *= 0.98
    pd.DataFrame({"timestamp": idx.strftime("%Y-%m-%d 00:00:00"), "upbit_open": adj, "upbit_high": adj,
                  "upbit_low": adj, "upbit_close": adj, "upbit_volume": 1, "upbit_trade_price": 1}).to_csv(raw, index=False)
    minute = tmp_path / "candles_1m" / "999999_1m.parquet"
    minute.parent.mkdir()
    pd.DataFrame({"timestamp": idx + pd.Timedelta(hours=15, minutes=30), "close": closes}).to_parquet(minute)
    monkeypatch.setattr(dpaths, "normalized_path",
                        lambda cat, fn=None, fallback=True: str(tmp_path / cat / fn) if fn else str(tmp_path / cat))
    with pytest.raises(ValueError, match="수정주가 일관성"):
        build_normalized_krx_daily("999999", raw_path=str(raw), output_parquet_path=str(tmp_path / "out.parquet"))
    assert not (tmp_path / "out.parquet").exists()


def _have_real_daily(*syms):
    from shared.data_library import paths as dpaths
    return all(os.path.exists(dpaths.normalized_path("candles_1d", f"{s}_1d.parquet", fallback=False)) for s in syms)


@pytest.mark.skipif(not _have_real_daily("069500", "122630"), reason="정규화 일봉 없음")
def test_lab_smoke(tmp_path):
    from CAiModelBaker.core import allocation_lab
    R = allocation_lab.main(["--signal", "069500", "--instruments", "069500,122630",
                             "--out", str(tmp_path), "--skip-grid"])
    assert (tmp_path / "report.md").exists() and (tmp_path / "results.json").exists()
    b = R["baseline"]
    # 국내주식형 1배만 쓰는 규칙은 일반계좌에서 세금이 없고, 2배를 쓰면 세금이 생긴다
    assert b["규칙 1배 상한 세후"]["tax_pct_per_year"] == 0
    assert b["규칙 2배 상한 세후"]["tax_pct_per_year"] > 0
    assert b["규칙 2배 상한 세후"]["cagr"] < b["규칙 2배 상한 세전"]["cagr"]
