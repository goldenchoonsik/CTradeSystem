"""회귀: candle_resampler 빈 구간 제거/거래량 가중 비율, feature_registry 선두 결측 미래값 누수 제거."""
import numpy as np
import pandas as pd

from shared.data_library.candle_resampler import resample_candles
from shared.features import feature_registry as fr


def _ohlcv(ts, close, vol, bid=None):
    d = {"timestamp": ts, "open": close, "high": close, "low": close, "close": close, "volume": vol}
    if bid is not None:
        d["target_trade_bid_ratio"] = bid
    return pd.DataFrame(d)


def test_no_trade_bins_dropped():
    ts = list(pd.date_range("2024-01-01 09:00", periods=3, freq="1min")) + \
         list(pd.date_range("2024-01-01 09:30", periods=3, freq="1min"))
    df = _ohlcv(ts, [100.0] * 6, [1.0] * 6)
    out = resample_candles(df, "5m", "1m")
    assert len(out) == 2
    assert out["close"].notna().all()


def test_bid_ratio_volume_weighted():
    ts = pd.date_range("2024-01-01 09:00", periods=2, freq="1min")
    df = _ohlcv(ts, [100.0, 100.0], [9.0, 1.0], bid=[1.0, 0.0])
    out = resample_candles(df, "5m", "1m")
    assert abs(out["target_trade_bid_ratio"].iloc[0] - 0.9) < 1e-9  # 단순 mean 이면 0.5


def test_oi_change_ratio_no_leading_spike():
    n = 20
    oi = pd.Series([0.0] * 10 + [100.0 + i for i in range(10)])
    df = pd.DataFrame({"binance_futures_oi": oi})
    out = fr._compute_binance_oi_change_ratio(df, {"window_bars": 3}, "1m")
    assert out.abs().max() < 1.0
    assert (out.iloc[:10] == 0.0).all()


def test_oi_ma_ratio_leading_neutral():
    oi = pd.Series([0.0] * 5 + [100.0] * 10)
    out = fr._compute_binance_oi_ma_ratio(pd.DataFrame({"binance_futures_oi": oi}), {}, "1m")
    assert (out.iloc[:5] == 0.0).all()


def test_usdt_ret_leading_not_fake_jump():
    u = pd.Series([np.nan] * 5 + [1400.0 + i for i in range(10)])
    out = fr._compute_crypto_usdt_krw_ret(pd.DataFrame({"crypto_usdt_close": u}), {}, "1m")
    assert out.abs().max() < 0.01
