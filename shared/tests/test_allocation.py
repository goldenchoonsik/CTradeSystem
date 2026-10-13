"""일 단위 배분 엔진: 정책·노출 실현·슬리브 합산·밴드·백테스터 (2026-10-10)"""
import numpy as np
import pandas as pd
import pytest

from shared.allocation.backtest import BacktestConfig, run_backtest, summarize
from shared.allocation.policy import AllocationParams, target_exposure
from shared.allocation.portfolio import Sleeve, account_target_weights, needs_rebalance
from shared.allocation.realize import (Instrument, instruments_from_ledger, realize_exposure,
                                       realize_series)

ONE = Instrument("AAA", 1.0, "KR_EQUITY_ETF")
TWO = Instrument("BBB", 2.0, "KR_OTHER_ETF")


def _prices(n=400, seed=0, drift=0.0004, vol=0.012):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, vol, n))), index=idx)


# ── 정책 ────────────────────────────────────────────────────────────────

def test_exposure_has_no_lookahead():
    """미래 가격을 바꿔도 과거 노출은 그대로여야 한다."""
    c = _prices()
    base = target_exposure(c)
    shocked = c.copy()
    shocked.iloc[300:] *= np.linspace(0.5, 3.0, len(c) - 300)
    alt = target_exposure(shocked)
    pd.testing.assert_series_equal(base.iloc[:300], alt.iloc[:300])
    assert not base.iloc[300:].equals(alt.iloc[300:])


def test_exposure_bounds_and_warmup():
    p = AllocationParams(max_exposure=1.5)
    e = target_exposure(_prices(), p)
    assert (e >= 0).all() and (e <= 1.5 + 1e-12).all()
    assert (e.iloc[: p.trend_window - 1] == 0).all()     # 이동평균 준비 전 = 현금


def test_trend_filter_zeroes_downtrend():
    c = pd.Series(np.linspace(200, 100, 300) * (1 + 0.01 * np.sin(np.arange(300))),
                  index=pd.bdate_range("2020-01-01", periods=300))
    assert (target_exposure(c).iloc[150:] == 0).all()


def test_exposure_matches_formula():
    c = _prices(seed=3)
    p = AllocationParams()
    e = target_exposure(c, p)
    t = c.index[250]
    r = c.pct_change().loc[:t].iloc[-p.vol_window:]
    vol = r.std() * np.sqrt(252)
    on = c.loc[t] > c.loc[:t].iloc[-p.trend_window:].mean()
    assert e.loc[t] == pytest.approx(min(p.vol_target / vol, p.max_exposure) * (1.0 if on else 0.0))


# ── 노출 실현 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("L, w1, w2", [(0, 0, 0), (0.6, 0.6, 0), (1.0, 1.0, 0), (1.5, 0.5, 0.5), (2.0, 0, 1.0)])
def test_realize_minimizes_leverage_product(L, w1, w2):
    w = realize_exposure(L, [TWO, ONE])
    assert w["AAA"] == pytest.approx(w1) and w["BBB"] == pytest.approx(w2)
    assert sum(w.values()) <= 1 + 1e-12
    assert w["AAA"] * 1 + w["BBB"] * 2 == pytest.approx(L)


def test_realize_rejects_unreachable_exposure():
    with pytest.raises(ValueError):
        realize_exposure(1.2, [ONE])


def test_sleeve_rejects_max_exposure_above_instruments():
    with pytest.raises(ValueError):
        Sleeve("K", "AAA", (ONE,), params=AllocationParams(max_exposure=2.0))


def test_instruments_from_ledger():
    inst = instruments_from_ledger(["122630", "069500"])
    assert [(i.symbol, i.leverage, i.tax_class) for i in inst] == [
        ("069500", 1.0, "KR_EQUITY_ETF"), ("122630", 2.0, "KR_OTHER_ETF")]


def test_ledger_symbol_without_tax_fields_is_rejected():
    with pytest.raises(ValueError, match="leverage/tax_class"):
        instruments_from_ledger(["005930"])


# ── 슬리브 합산·밴드 ────────────────────────────────────────────────────

def test_account_weights_combine_sleeves():
    a, b = _prices(seed=1), _prices(seed=2)
    s1 = Sleeve("A", "SA", (ONE,), allocation=0.6, params=AllocationParams(max_exposure=1.0))
    s2 = Sleeve("B", "SB", (ONE,), allocation=0.4, params=AllocationParams(max_exposure=1.0))
    w = account_target_weights([s1, s2], {"SA": a, "SB": b})
    assert list(w.columns) == ["AAA"]                       # 같은 상품은 합산
    assert (w["AAA"] <= 1 + 1e-12).all()
    exp = 0.6 * target_exposure(a, s1.params) + 0.4 * target_exposure(b, s2.params)
    pd.testing.assert_series_equal(w["AAA"], exp.reindex(w.index).fillna(0.0), check_names=False)


def test_allocations_over_one_rejected():
    s1 = Sleeve("A", "SA", (ONE,), allocation=0.7, params=AllocationParams(max_exposure=1.0))
    s2 = Sleeve("B", "SB", (ONE,), allocation=0.4, params=AllocationParams(max_exposure=1.0))
    with pytest.raises(ValueError):
        account_target_weights([s1, s2], {"SA": _prices(), "SB": _prices()})


def test_band():
    assert not needs_rebalance({"A": 0.50}, {"A": 0.54}, 0.05)
    assert needs_rebalance({"A": 0.50}, {"A": 0.56}, 0.05)
    assert needs_rebalance({"A": 0.03}, {"A": 0.0}, 0.05)   # 청산 신호는 밴드가 삼키지 않는다
    assert not needs_rebalance({}, {"A": 0.0}, 0.05)
    assert needs_rebalance({"A": 0.5}, {"A": 0.5000001}, 0.0)


# ── 백테스터 (원장 종목 사용: 비용·세금이 원장에서 와야 한다) ─────────────────

def _flat_target(idx, sym, w):
    return pd.DataFrame({sym: w}, index=idx)


def test_buy_and_hold_tracks_price_minus_entry_cost():
    c = _prices(seed=5)
    res = run_backtest(_flat_target(c.index, "069500", 1.0), c.to_frame("069500"),
                       BacktestConfig(lag=0, account_type="GENERAL"))
    from shared.config.config_manager import get_symbol_info
    info = get_symbol_info("069500")
    one_way = info["default_fee"] + info["default_slippage"]
    expected = 10_000_000 / (1 + one_way) * c.iloc[-1] / c.iloc[0]
    assert res.equity.iloc[-1] == pytest.approx(expected, rel=1e-9)
    assert res.daily["tax"].sum() == 0


def test_lag_delays_execution():
    c = _prices(seed=6)
    tw = _flat_target(c.index, "069500", 0.0)
    tw.iloc[100:] = 1.0
    res = run_backtest(tw, c.to_frame("069500"), BacktestConfig(lag=1))
    assert res.weights["069500"].iloc[100] == 0
    assert res.weights["069500"].iloc[101] > 0.99


def test_gain_tax_on_other_etf_only_in_general_account():
    idx = pd.bdate_range("2021-01-01", periods=3)
    px = pd.DataFrame({"122630": [100.0, 150.0, 150.0]}, index=idx)
    tw = pd.DataFrame({"122630": [1.0, 0.0, 0.0]}, index=idx)
    taxed = run_backtest(tw, px, BacktestConfig(lag=0, account_type="GENERAL"))
    pretax = run_backtest(tw, px, BacktestConfig(lag=0, account_type=None))
    from shared.config.config_manager import get_symbol_info
    info = get_symbol_info("122630")
    one_way = info["default_fee"] + info["default_slippage"]
    q = 10_000_000 / (100 * (1 + one_way))
    assert taxed.daily["tax"].sum() == pytest.approx(q * 50 * 0.154)
    assert pretax.daily["tax"].sum() == 0
    assert pretax.equity.iloc[-1] - taxed.equity.iloc[-1] == pytest.approx(q * 50 * 0.154)


def test_cash_account_never_goes_negative_and_integer_shares():
    a, b = _prices(seed=7), _prices(seed=8) * 0.5
    px = pd.concat({"069500": a, "122630": b}, axis=1)
    rng = np.random.default_rng(9)
    raw = rng.uniform(0, 1, (len(px), 2))
    tw = pd.DataFrame(raw / raw.sum(axis=1, keepdims=True) * 0.99, index=px.index, columns=px.columns)
    res = run_backtest(tw, px, BacktestConfig(lag=1, integer_shares=True, initial_capital=3_000_000))
    assert (res.weights.sum(axis=1) <= 1 + 1e-9).all()
    held = res.trades.groupby("symbol")["qty"].sum()
    assert np.allclose(res.trades["qty"], np.round(res.trades["qty"]))
    assert (held >= -1e-9).all()


def test_band_reduces_turnover():
    c = _prices(seed=10, n=800)
    sleeve = Sleeve("K", "069500", instruments_from_ledger(["069500", "122630"]))
    w = account_target_weights([sleeve], {"069500": c})
    px = pd.concat({"069500": c, "122630": c ** 2 / c.iloc[0]}, axis=1)
    r0 = summarize(run_backtest(w, px, BacktestConfig(band=0.0)))
    r10 = summarize(run_backtest(w, px, BacktestConfig(band=0.10)))
    assert r10["turnover_per_year"] < r0["turnover_per_year"]
    assert r10["rebalances_per_year"] < r0["rebalances_per_year"]


def test_target_on_missing_price_is_an_error():
    idx = pd.bdate_range("2021-01-01", periods=3)
    px = pd.DataFrame({"122630": [np.nan, 100.0, 101.0]}, index=idx)
    tw = pd.DataFrame({"122630": [1.0, 1.0, 1.0]}, index=idx)
    with pytest.raises(ValueError, match="가격이 없는"):
        run_backtest(tw, px, BacktestConfig(lag=0))
