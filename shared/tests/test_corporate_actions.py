"""기업행위 원장(1층) — 감지·원장 갱신(추가만)·수정 계산. 합성 데이터 + staging 실데이터(없으면 건너뜀)."""
import json
import os

import numpy as np
import pandas as pd
import pytest

from shared.data_library import corporate_actions as ca

STAGE = os.path.join(os.path.dirname(__file__), "..", "..", "DataVault", "staging", "vol_spike_universe", "raw", "creon")


def _pair(raw_close, events, seed=0):
    """무수정 종가 + 이벤트 {ex_index: r} → (수정 df, 무수정 df). 수정가는 Creon 처럼 원 단위 반올림."""
    dates = pd.bdate_range("2025-01-02", periods=len(raw_close))
    f = np.ones(len(raw_close))
    for i, r in events.items():
        f[:i] *= r
    raw = pd.DataFrame({"timestamp": dates, "upbit_close": raw_close, "upbit_volume": 1000.0})
    adj = pd.DataFrame({"timestamp": dates, "upbit_close": np.round(raw_close * f), "upbit_volume": 1000.0 / f})
    return adj, raw


def _walk(n=200, start=5000, seed=0):
    rng = np.random.default_rng(seed)
    return np.round(start * np.exp(np.cumsum(rng.normal(0, 0.02, n))))


def test_detects_merge_split_and_distribution():
    px = _walk()
    adj, raw = _pair(px, {50: 5.0, 120: 0.5, 170: 0.996})
    ev = ca.detect(adj, raw)
    assert [e["ex_date"] for e in ev] == [raw["timestamp"][i].strftime("%Y-%m-%d") for i in (50, 120, 170)]
    np.testing.assert_allclose([e["price_factor"] for e in ev], [5.0, 0.5, 0.996], rtol=1e-3)
    assert ev[0]["kind_hint"] == "reverse_split_or_capital_reduction" and ev[0]["nominal_ratio"] == "5:1"
    assert ev[1]["kind_hint"] == "split_or_bonus_issue"
    assert ev[2]["kind_hint"] == "small_dilution_or_distribution"


@pytest.mark.parametrize("start", [150, 800, 3000, 60000])
def test_rounding_noise_is_not_an_event(start):
    """수정가 원 단위 반올림 흔들림(저가주일수록 큼)은 이벤트가 아니다."""
    px = _walk(start=start, seed=start)
    adj, raw = _pair(px, {80: 0.97})          # 작은 진짜 이벤트 하나만
    ev = ca.detect(adj, raw)
    assert len(ev) == 1 and ev[0]["price_factor"] == pytest.approx(0.97, rel=2e-3)


def test_reconstruction_within_rounding():
    px = _walk(seed=3)
    adj, raw = _pair(px, {40: 4.0027, 150: 0.8})
    ev = ca.detect(adj, raw)
    assert ca.reconstruction_error(adj, raw, ev).max() <= 2.0


def test_adjust_prices_and_volume_and_basis():
    raw = pd.DataFrame({"timestamp": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]),
                        "upbit_close": [100.0, 100.0, 500.0], "upbit_volume": [50.0, 50.0, 10.0], "upbit_trade_price": [5e3] * 3})
    evs = [{"ex_date": "2025-01-06", "price_factor": 5.0, "status": "detected"}]
    out = ca.adjust_prices(raw, evs)
    assert out["upbit_close"].tolist() == [500.0, 500.0, 500.0]
    assert out["upbit_volume"].tolist() == [10.0, 10.0, 10.0]
    assert out["upbit_trade_price"].tolist() == [5e3] * 3                     # 거래대금 불변
    before = ca.adjust_prices(raw, evs, basis_date="2025-01-03")                # 이벤트 전 기준 = 무수정 그대로
    assert before["upbit_close"].tolist() == [100.0, 100.0, 500.0]


def test_adjust_minute_bars_by_day():
    m1 = pd.DataFrame({"timestamp": pd.to_datetime(["2025-01-03 09:01", "2025-01-03 15:30", "2025-01-06 09:01"]),
                       "upbit_close": [100.0, 101.0, 505.0], "upbit_volume": [5.0, 5.0, 1.0]})
    out = ca.adjust_prices(m1, [{"ex_date": "2025-01-06", "price_factor": 5.0}])
    assert out["upbit_close"].tolist() == [500.0, 505.0, 505.0]


def test_ledger_append_only(tmp_path):
    px = _walk(seed=5)
    adj, raw = _pair(px, {60: 5.0})
    root = str(tmp_path)
    win = (raw["timestamp"].min().strftime("%Y-%m-%d"), raw["timestamp"].max().strftime("%Y-%m-%d"))
    r1 = ca.update(root, "A000001", ca.detect(adj, raw, observed_at="t1"), win, observed_at="t1")
    assert len(r1["added"]) == 1
    r2 = ca.update(root, "A000001", ca.detect(adj, raw, observed_at="t2"), win, observed_at="t2")
    assert r2["added"] == [] and len(r2["seen"]) == 1
    led = ca.load(root, "A000001")
    assert led["events"][0]["first_seen_at"] == "t1" and led["events"][0]["last_seen_at"] == "t2"
    # 출처가 계수를 바꾸면: 덮어쓰지 않고 개정 기록 + 확인 필요
    adj2, raw2 = _pair(px, {60: 4.0})
    r3 = ca.update(root, "A000001", ca.detect(adj2, raw2), win, observed_at="t3")
    e = ca.load(root, "A000001")["events"][0]
    assert r3["conflict"] and e["status"] == "conflict" and e["price_factor"] == pytest.approx(5.0, rel=1e-3)
    assert e["revisions"][0]["price_factor"] == pytest.approx(4.0, rel=1e-3)
    assert ca.events(root, "A000001") == []                                   # 확인 전엔 수정 계산에 안 쓴다
    # 관측 기간 안에서 사라지면 표시만(삭제 안 함)
    r4 = ca.update(root, "A000009", [], win, observed_at="t4")
    assert r4 == {"added": [], "seen": [], "conflict": [], "missing": []}
    assert len(ca.load(root, "A000001")["observations"]) == 3


def test_missing_event_is_flagged_not_deleted(tmp_path):
    px = _walk(seed=6)
    adj, raw = _pair(px, {60: 5.0})
    root = str(tmp_path)
    win = ("2024-12-01", "2026-01-01")
    ca.update(root, "A1", ca.detect(adj, raw), win, observed_at="t1")
    rep = ca.update(root, "A1", [], win, observed_at="t2")
    e = ca.load(root, "A1")["events"][0]
    assert rep["missing"] == [e["ex_date"]] and e["missing_since"] == "t2" and e["status"] == "detected"


# ---------------------------------------------------------------- staging 실데이터
def _real(code):
    a, r = os.path.join(STAGE, "candle_1d", f"{code}_1d.csv"), os.path.join(STAGE, "candle_1d_unadj", f"{code}_1d.csv")
    if not (os.path.exists(a) and os.path.exists(r)):
        pytest.skip(f"{code} staging 일봉 없음")
    return pd.read_csv(a), pd.read_csv(r)


def test_real_merge_kr_motors():
    adj, raw = _real("A000040")
    ev = ca.detect(adj, raw)
    assert [(e["ex_date"], e["nominal_ratio"]) for e in ev] == [("2026-07-28", "5:1")]
    assert ca.reconstruction_error(adj, raw, ev).max() <= 2.0


def test_real_capital_reduction_uses_tick_rounded_base_price():
    """감자 4:1 이지만 기준가가 호가 단위로 반올림돼 실제 계수는 4.0027 — 분수로 맞추면 재구성이 깨진다."""
    adj, raw = _real("A900340")
    ev = ca.detect(adj, raw)
    assert len(ev) == 1 and ev[0]["nominal_ratio"] == "4:1"
    assert ev[0]["price_factor"] == pytest.approx(2970 / 742, rel=2e-4)
    assert ca.reconstruction_error(adj, raw, ev).max() <= 2.0


def test_real_cash_dividend_not_in_creon_adjustment():
    """Creon 수정주가는 일반 주식 현금배당을 반영하지 않는다(삼성전자 분기배당) → 배당금은 2층(KIS)에서."""
    adj, raw = _real("A005930")
    assert ca.detect(adj, raw) == []
