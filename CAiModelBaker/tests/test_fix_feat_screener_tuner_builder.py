"""회귀: feature_screener 서브샘플링/유의성, feature_tuner 타임스탬프 파싱, normalized_builder 리샘플 실패 전파/is_large."""
import os
import numpy as np
import pandas as pd
import pytest

from CAiModelBaker.core import feature_screener as fs
from CAiModelBaker.core import feature_tuner as ft


def test_screener_computes_on_full_data_and_real_lead_time(tmp_path, monkeypatch):
    n = 130_000
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 0.1, n))
    df = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=n, freq="1min"), "target_close": close})
    p = tmp_path / "snap.parquet"
    df.to_parquet(p)
    seen = {}

    def comp(d, params, interval):
        seen["len"] = len(d)
        return d["target_close"].pct_change(1).fillna(0.0)

    monkeypatch.setattr(fs, "DEFAULT_FEATURE_REGISTRY", {"x": {"compute": comp, "params": {}, "name": "x"}})
    cfg = {"SNAPSHOT_PATH": str(p), "INTERVAL": "1m", "SYMBOL_UPBIT": "KRW-ZZZ"}
    res = fs.run_full_period_feature_screening(cfg, max_lag_candles=3, lookback_period="all")
    assert seen["len"] == n  # 다운샘플 없이 전체로 지표 계산
    r = res["ranked_features"][0]
    assert r["optimal_lag"] <= 3 and r["lead_time"].startswith(f"{r['optimal_lag']}.0분")
    assert 0.0 <= r["p_value"] <= 1.0


def test_parse_valid_ts():
    assert ft._parse_valid_ts("") is None
    assert ft._parse_valid_ts(None) is None
    assert ft._parse_valid_ts("garbage-date") is None
    assert ft._parse_valid_ts("2024-01-02") == pd.Timestamp("2024-01-02")


def test_tuner_source_head_fallback_and_market_type():
    src = open(ft.__file__, encoding="utf-8").read()
    assert "df.tail(tail_candles)" not in src
    assert "market_type=_tb_market_type" in src


def test_builder_resample_failure_propagates(tmp_path):
    from CAiModelBaker.core.normalized_builder import build_normalized_candle_stock
    ts = pd.date_range("2024-01-02 09:00", periods=30, freq="1min")
    raw = pd.DataFrame({"timestamp": ts, "upbit_open": 1.0, "upbit_high": 1.0, "upbit_low": 1.0,
                        "upbit_close": 1.0, "upbit_volume": 1.0})
    rp = tmp_path / "005930_1m.csv"
    raw.to_csv(rp, index=False)
    out = tmp_path / "out.parquet"
    with pytest.raises(RuntimeError):
        build_normalized_candle_stock("005930", interval="7m", raw_candle_path=str(rp),
                                      output_parquet_path=str(out), local_log=lambda m: None)
    assert not out.exists()


def test_is_large_uses_previous_day_threshold(tmp_path):
    from CAiModelBaker.core.normalized_builder import build_normalized_10s_stock
    rows = []
    for i in range(20):  # 1일차: 모두 2억
        rows.append((pd.Timestamp("2024-01-02 09:00") + pd.Timedelta(minutes=i), 1000.0, 200_000.0))
    for i in range(20):  # 2일차: 5천만~1.4억 (당일 q95 기준이면 일부가 large)
        rows.append((pd.Timestamp("2024-01-03 09:00") + pd.Timedelta(minutes=i), 1000.0, 50_000.0 + i * 5_000.0))
    raw = pd.DataFrame(rows, columns=["timestamp", "upbit_close", "upbit_volume"])
    rp = tmp_path / "A005930_tick.csv"
    raw.to_csv(rp, index=False)
    out = tmp_path / "o10.parquet"
    build_normalized_10s_stock("005930", raw_tick_path=str(rp), output_parquet_path=str(out), local_log=lambda m: None)
    df = pd.read_parquet(out)
    day2 = df[df["timestamp"].dt.date == pd.Timestamp("2024-01-03").date()]
    assert day2["is_large_trade"].sum() == 0
