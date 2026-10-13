"""crypto_0900_open_sync 피처의 인과성(look-ahead 없음) 회귀 테스트."""
import numpy as np
import pandas as pd

from shared.features.feature_registry import _compute_crypto_0900_sync


def _frame():
    ts = pd.date_range("2026-01-01 00:00", "2026-01-02 12:00", freq="60min")
    n = len(ts)
    c = np.linspace(100, 150, n)
    return pd.DataFrame({
        "timestamp": ts,
        "target_close": c,
        "target_open": c,
        "binance_spot_close": c * np.linspace(1, 1.03, n),
    })


def test_pre_0900_values_do_not_depend_on_future_0900_open():
    df = _frame()
    base = _compute_crypto_0900_sync(df, {}, "60m")
    changed = df.copy()
    changed.loc[changed.index[9]:, "target_open"] *= 2  # 09:00 이후 값 변경
    after = _compute_crypto_0900_sync(changed, {}, "60m")
    assert np.allclose(base.iloc[:9], after.iloc[:9])


def test_no_anchor_before_first_0900_is_zero():
    out = _compute_crypto_0900_sync(_frame(), {}, "60m")
    assert (out.iloc[:9] == 0.0).all()
