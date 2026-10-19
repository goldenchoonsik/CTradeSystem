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
    cfg = {"SNAPSHOT_PATH": str(p), "INTERVAL": "1m", "SYMBOL_UPBIT": "KRW-ZZZ", "OPTUNA_CONFIG": {"holdout_td": 1}}
    res = fs.run_full_period_feature_screening(cfg, max_lag_candles=3, lookback_period="all")
    last_day = df["timestamp"].dt.normalize().max()
    assert seen["len"] == int((df["timestamp"] < last_day).sum())  # 다운샘플 없이 홀드아웃(마지막 1일) 앞 전체로 계산
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


def test_selection_folds_split_by_trading_days_not_24h_minutes():
    """10초봉 29거래일(장 6.5시간)에서 확장 창 3개가 서로 달라야 한다.
    (2026-10-20: 최소 조각을 '30일×24시간'=259,200봉으로 박아, 세 창 모두 학습 구간 전체 = 검증 0이었다)"""
    days = pd.bdate_range("2026-08-26", periods=29)
    ts = pd.DatetimeIndex(np.concatenate([pd.date_range(d + pd.Timedelta(hours=9), periods=2340, freq="10s").values
                                          for d in days]))
    ends, n_days = ft.split_dates_into_expanding_folds(pd.Series(ts), n_splits=3)
    assert n_days == 29
    assert len(ends) == 3 and ends[0] < ends[1] < ends[2] == len(ts)
    assert ends[0] % 2340 == 0                     # 날짜 경계에서 자른다
    assert ends[0] >= 2 * 2340                     # 첫 창도 최소 2거래일


def test_selection_refuses_too_few_trading_days():
    ts = pd.Series(pd.date_range("2026-10-01 09:00", periods=5 * 100, freq="1min"))  # 1거래일 미만~
    with pytest.raises(ValueError, match="거래일"):
        ft.split_dates_into_expanding_folds(ts)


def test_dead_feature_columns_detected():
    """데이터 없는 지표(전부 NaN·상수·대부분 결측)는 후보·그림자 합격선 계산에서 빠진다."""
    n = 1000
    df = pd.DataFrame({
        "alive": np.random.default_rng(0).normal(size=n),
        "all_nan": np.nan,
        "const": 0.0,
        "mostly_nan": np.r_[np.arange(100.0), np.full(n - 100, np.nan)],
        "with_inf": np.r_[np.full(n - 10, np.inf), np.arange(10.0)],
    })
    dead = ft.find_dead_feature_columns(df, list(df.columns))
    assert set(dead) == {"all_nan", "const", "mostly_nan", "with_inf"}


def test_selection_never_falls_back_to_head_60pct_or_all_features():
    """연구 기간이 없으면 '데이터 앞 60%'로, 합격 2개 미만이면 '등록 지표 전부'로 대신하지 않는다."""
    src = open(ft.__file__, encoding="utf-8").read()
    assert "len(df) * 0.6" not in src
    assert "else all_feature_ids" not in src
    assert "30 * 24 * 60" not in src


def test_duplicate_feature_columns_detected():
    df = pd.DataFrame({"target_x": [1.0, 2.0, 3.0], "upbit_x": [1.0, 2.0, 3.0], "y": [3.0, 2.0, 1.0]})
    assert ft.find_duplicate_feature_columns(df, ["target_x", "upbit_x", "y"]) == {"upbit_x": "target_x"}


def test_shadow_features_reproducible_with_seed():
    df = pd.DataFrame({"a": np.arange(1000.0), "b": np.arange(1000.0)[::-1]})
    s1 = ft.generate_block_shadow_features(df, ["a", "b"], 50, seed=7)
    s2 = ft.generate_block_shadow_features(df, ["a", "b"], 50, seed=7)
    pd.testing.assert_frame_equal(s1, s2)


def test_selection_registers_configured_reference_features():
    """자동 선정은 참조 종목 지표를 스스로 등록한다 — 다른 화면이 먼저 돌았는지에 따라 ref_* 후보가 바뀌면 안 된다."""
    src = open(ft.__file__, encoding="utf-8").read()
    body = src[src.index("def run_auto_feature_selection"):]
    assert "register_configured_reference_features(config" in body


def test_basis_level_features_are_regime_context():
    """베이시스 '수준'은 몇 주씩 머무는 체제 변수 — 선형 IC 가 아니라 블록 일치도 게이트(T-8)로 판정한다.
    (2026-10-20 122630 10s: 09-29 수준 이동이 하락장과 겹쳐 IC 통과 → 베이시스 띠 규칙 모델)"""
    from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY as R
    assert R["kospi_basis"]["is_regime"] and R["kospi_basis_rate"]["is_regime"]
    assert not R["kospi_basis_z"].get("is_regime", False)   # 단기 변화(z)는 일반 지표로 IC 판정


def test_holdout_boundary_is_last_n_trading_days_and_independent_of_wfa():
    """홀드아웃 경계 단일 원천: 마지막 N 유효 거래일. N = 사용자 지정 > 인터벌 표준. WFA 키는 영향 없음."""
    from CAiModelBaker.core.split_planner import resolve_holdout_boundary
    days = pd.bdate_range("2026-08-26", periods=34)
    ts = pd.DatetimeIndex(np.concatenate([pd.date_range(d + pd.Timedelta(hours=9), periods=50, freq="10s").values
                                          for d in days]))
    df = pd.DataFrame({"timestamp": ts, "target_volume": 100.0})
    base = {"INTERVAL": "10s", "SYMBOL_UPBIT": "122630"}
    b = resolve_holdout_boundary(df, {**base, "OPTUNA_CONFIG": {"holdout_td": 5}})
    assert b["holdout_start"] == str(days[-5].date()) and b["research_end"] == str(days[-6].date())
    b2 = resolve_holdout_boundary(df, {**base, "OPTUNA_CONFIG": {"holdout_td": 5, "wfa_window_count": 9, "wfa_train_months": 3},
                                       "TRAIN_START": "2026-09-10", "TRAIN_END": "2026-09-20"})
    assert b2["holdout_start"] == b["holdout_start"]
    std = resolve_holdout_boundary(df, base)
    assert std["source"] == "standard" and std["holdout_td"] >= 1


def test_smart_split_keeps_holdout_fixed():
    """스마트 분할·'홀드아웃 앞 전체' 모두 고정 홀드아웃을 바꾸지 않는다 (예전 'full' 은 80/20 으로 다시 잘랐다)."""
    from CAiModelBaker.core.split_planner import plan_split
    days = pd.bdate_range("2025-01-01", periods=200)
    ts = pd.DatetimeIndex(np.concatenate([pd.date_range(d + pd.Timedelta(hours=9), periods=20, freq="1min").values
                                          for d in days]))
    df = pd.DataFrame({"timestamp": ts, "target_volume": 100.0})
    for mode in ("smart", "full"):
        p = plan_split(df, "1m", market="KRX", mode=mode, holdout_td_override=7)
        assert p["ok"] and p["holdout_td"] == 7 and p["holdout_start"] == str(days[-7].date()), mode


def test_research_loop_holdout_fallback_uses_boundary_not_80pct():
    from CAiModelBaker.core import research_loop as rl
    src = open(rl.__file__, encoding="utf-8").read()
    assert "split_idx = int(len(df_full) * 0.8)" not in src
    assert "resolve_holdout_boundary(df_full, config)" in src
