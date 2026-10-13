"""
M-1 회귀 테스트: trade_history.json 다중 writer 충돌 방지 (합집합 병합)

VirtualTrader(UPBIT/시뮬)와 KISTrader(KIS)가 각자 독립적인 in-memory 리스트로
전체 덮어쓰기할 때 상대방 이벤트가 소실되던 문제를, 합집합 병합 저장이
방지하는지 검증한다.
"""
import json
import os

from utils.history_store import save_history_merged, load_history_raw


def _evt(type_, sym, t, price, qty, exchange):
    return {"type": type_, "symbol": sym, "time": t, "price": price, "qty": qty, "exchange": exchange}


def test_merge_preserves_other_writer_events(tmp_path):
    """B(KIS)가 A(UPBIT)의 신규 이벤트를 모르는 상태로 저장해도 A 이벤트가 보존되어야 한다."""
    p = str(tmp_path / "trade_history.json")
    upbit = _evt("BUY", "KRW-BTC", 100.0, 50000.0, 0.1, "UPBIT")
    kis = _evt("BUY", "069500", 101.0, 35000.0, 10.0, "KIS")

    assert save_history_merged(p, [upbit]) is True
    # 독립 writer(KIS) 저장 → 기존 UPBIT 이벤트 보존
    assert save_history_merged(p, [kis]) is True

    data = load_history_raw(p)
    assert sorted(e["symbol"] for e in data) == ["069500", "KRW-BTC"]


def test_merge_idempotent(tmp_path):
    p = str(tmp_path / "h.json")
    e = _evt("BUY", "KRW-BTC", 100.0, 50000.0, 0.1, "UPBIT")
    save_history_merged(p, [e])
    save_history_merged(p, [e])
    assert len(load_history_raw(p)) == 1


def test_merge_sorted_by_time(tmp_path):
    p = str(tmp_path / "h.json")
    a = _evt("BUY", "A", 200.0, 1.0, 1.0, "X")
    b = _evt("SELL", "A", 100.0, 1.0, 1.0, "X")
    save_history_merged(p, [a, b])
    times = [e["time"] for e in load_history_raw(p)]
    assert times == sorted(times)


def test_interleaved_writers_no_clobber(tmp_path):
    """UPBIT/KIS writer 가 교차 저장해도 3개 이벤트가 모두 보존되어야 한다(회귀)."""
    p = str(tmp_path / "h.json")
    w_upbit, w_kis = [], []

    w_upbit.append(_evt("BUY", "KRW-BTC", 100.0, 1.0, 1.0, "UPBIT"))
    save_history_merged(p, w_upbit)

    w_kis.append(_evt("BUY", "069500", 101.0, 1.0, 1.0, "KIS"))
    save_history_merged(p, w_kis)

    w_upbit.append(_evt("SELL", "KRW-BTC", 102.0, 1.0, 1.0, "UPBIT"))
    save_history_merged(p, w_upbit)

    data = load_history_raw(p)
    pairs = {(e["symbol"], e["type"]) for e in data}
    assert ("KRW-BTC", "BUY") in pairs
    assert ("KRW-BTC", "SELL") in pairs
    assert ("069500", "BUY") in pairs
    assert len(data) == 3
