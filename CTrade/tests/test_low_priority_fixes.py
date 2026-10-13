"""
Low Priority 회귀 테스트 (L-1 ~ L-6)

L-1: portfolio_tracker KST 명시
L-2: _is_futures_symbol 부분문자열 과매칭 제거
L-3: ctrade_core get_stats 총자산 청산수수료 반영
L-4: KISTrader 실현손익 수수료 반영
L-6: engine 경계 트리거(%N) → 경과시간 기반
"""
import os
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tick_router import _is_futures_symbol
from core.kis_trader import KISTrader


# ── L-2 ──
def test_futures_symbol_exact_tokens():
    assert _is_futures_symbol("NQ", "KRW-BTC") is True
    assert _is_futures_symbol("ES", "KRW-BTC") is True
    assert _is_futures_symbol("101V000", "069500") is True
    assert _is_futures_symbol("KOSPI200", "069500") is True
    assert _is_futures_symbol("SPX", "SPY") is True


def test_futures_symbol_no_substring_overmatch():
    # 기존 'ES'/'SP' 부분문자열 오매칭 케이스
    assert _is_futures_symbol("MESX", "KRW-BTC") is False
    assert _is_futures_symbol("TEST", "KRW-BTC") is False
    assert _is_futures_symbol("SPLASH", "KRW-BTC") is False
    assert _is_futures_symbol("105560", "KRW-BTC") is False   # 6자리 국내코드
    assert _is_futures_symbol("KRW-BTC", "KRW-BTC") is False  # 타겟 자신


# ── L-4 ──
def test_kis_realized_profit_deducts_round_trip_fee():
    profit, rate, fee = KISTrader.calc_realized_profit(1000.0, 1100.0, 10, 0.001)
    invest, sell_amt = 10000.0, 11000.0
    expected_fee = (invest + sell_amt) * 0.001
    assert fee == pytest.approx(expected_fee)
    assert profit == pytest.approx((sell_amt - invest) - expected_fee)
    assert rate == pytest.approx(profit / invest * 100.0)
    # 수수료 미반영(기존) 대비 순손익이 더 작아야 한다
    assert profit < (1000.0)


# ── L-3 ──
def test_ctrade_core_stats_fee_adjusted():
    import ctrade_core
    t = ctrade_core.VirtualTrader(10_000_000.0, 0.0005, True)
    t.force_set_position("X", 100.0, 100.0, time.time(), 100.0, False)
    stats = t.get_stats({"X": 100.0})
    expected_mv = 100.0 * 100.0 * (1.0 - 0.0005)
    assert stats["total_asset"] == pytest.approx(t.balance + expected_mv, rel=1e-9)


# ── L-1 (정적) ──
def test_portfolio_tracker_uses_kst():
    src = open(os.path.join(REPO_ROOT, "CTrade/utils/portfolio_tracker.py"), encoding="utf-8").read()
    assert "datetime.now(KST)" in src
    assert "datetime.now()" not in src


# ── L-6 (정적) ──
def test_engine_no_wallclock_modulo_triggers():
    src = open(os.path.join(REPO_ROOT, "CTrade/core/engine.py"), encoding="utf-8").read()
    assert "int(now) % 5 == 0" not in src
    assert "int(now) % 10 == 0" not in src
    assert "int(now) % 30 == 0" not in src
    assert "_last_optimizer_ts" in src
    assert "_last_health_ts" in src
