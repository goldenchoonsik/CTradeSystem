"""
H-1 회귀 테스트: 청산 우선순위 단일화 패리티 (Exit Priority Parity)

`shared.tbm_executor.resolve_exit` 을 단일 소스(Single Source of Truth)로 삼아
당일청산 > 비상손절 > Soft SL > Soft TP > 시간만료 우선순위를 고정하고,
실전 TBMPositionManager 와 백테스트 공통 규칙이 동일 결과를 내는지 검증한다.
"""
from datetime import datetime, timezone, timedelta

import pytest

from shared.core.tbm_executor import resolve_exit, TBMPositionManager

KST = timezone(timedelta(hours=9))

ENTRY = 10000.0
ATR = 100.0
TP, SL, EMG, MHC = 2.0, 1.5, 3.0, 20
COST = 0.0022


def _r(**kw):
    base = dict(
        entry_price=ENTRY, entry_atr=ATR, current_price=ENTRY, current_low=ENTRY,
        take_profit=TP, stop_loss=SL, emergency_sl_mult=EMG, max_hold_candles=MHC,
        elapsed_candles=1, cost=COST, force_exit=False, is_candle_close=True,
        interval_minutes=1.0,
    )
    base.update(kw)
    return resolve_exit(**base)


def test_force_exit_beats_emergency_and_soft_barriers():
    """15:18 당일청산 + Low가 비상손절선 강하게 터치 + 종가 Soft SL 터치 → 당일청산 최우선"""
    close = ENTRY - ATR * (SL + 1)
    r = _r(current_low=ENTRY - ATR * (EMG + 5), current_price=close, force_exit=True)
    assert r["exit_type"] == "당일청산"
    assert r["exit_price"] == pytest.approx(close)


def test_emergency_sl_beats_soft_tp_on_same_candle():
    """종가는 TP 상회하나 Low가 비상손절선 터치 → 비상손절 우선"""
    r = _r(current_price=ENTRY + ATR * (TP + 1), current_low=ENTRY - ATR * (EMG + 0.1))
    assert r["exit_type"] == "비상손절"
    assert r["exit_price"] == pytest.approx(ENTRY - ATR * EMG)


def test_soft_sl_before_soft_tp():
    r = _r(current_price=ENTRY - ATR * (SL + 1), current_low=ENTRY - 1)
    assert r["exit_type"] == "손절"


def test_soft_tp():
    r = _r(current_price=ENTRY + ATR * (TP + 1), current_low=ENTRY - 1)
    assert r["exit_type"] == "익절"


def test_time_exit_last():
    r = _r(current_price=ENTRY + 1, current_low=ENTRY - 1, elapsed_candles=MHC)
    assert r["exit_type"] == "시간만료"


def test_tick_mode_ignores_soft_barriers():
    """실시간 틱 판정(is_candle_close=False)에서는 Soft TP/SL/시간만료 무시"""
    r = _r(current_price=ENTRY + ATR * (TP + 1), current_low=ENTRY + ATR * (TP + 1),
           elapsed_candles=MHC, is_candle_close=False)
    assert r is None


def test_net_return_formula():
    close = ENTRY + ATR * (TP + 1)
    r = _r(current_price=close, current_low=ENTRY - 1)
    expected = ((close - ENTRY) / ENTRY - COST) * 100.0
    assert r["net_return"] == pytest.approx(expected)


def test_live_tbm_candle_equals_resolve_exit():
    pm = TBMPositionManager(
        {"take_profit": TP, "stop_loss": SL, "emergency_sl_mult": EMG,
         "max_hold_candles": MHC, "cooldown_minutes": 1, "predict_ahead_candles": MHC},
        cost=COST, interval_minutes=1.0, intraday_square_off=True, market_type="KRX")
    ts = datetime(2026, 9, 10, 15, 18, tzinfo=KST)
    close = ENTRY - ATR * (SL + 1)
    live = pm.check_candle_exit(entry_price=ENTRY, entry_atr=ATR, candle_close=close,
                                elapsed_candles=3, current_time=ts)
    direct = resolve_exit(entry_price=ENTRY, entry_atr=ATR, current_price=close, current_low=close,
                          take_profit=TP, stop_loss=SL, emergency_sl_mult=EMG, max_hold_candles=MHC,
                          elapsed_candles=3, cost=COST, force_exit=True, is_candle_close=True,
                          interval_minutes=1.0)
    assert live["exit_type"] == direct["exit_type"] == "당일청산"
    assert live["exit_price"] == pytest.approx(direct["exit_price"])


def test_backtest_reference_loop_parity():
    """백테스트 공통 규칙(resolve_exit) 참조 루프 == 실전 TBM (청산 사유/체결가/수익률)"""
    pm = TBMPositionManager(
        {"take_profit": TP, "stop_loss": SL, "emergency_sl_mult": EMG,
         "max_hold_candles": MHC, "cooldown_minutes": 1, "predict_ahead_candles": MHC},
        cost=COST, interval_minutes=1.0, intraday_square_off=False, market_type="CRYPTO")

    scenarios = [
        (ENTRY - ATR * (EMG + 1), ENTRY - ATR * (EMG + 1), 1, False),  # 비상손절
        (ENTRY - ATR * (SL + 1), ENTRY - 1, 2, False),                 # Soft SL
        (ENTRY + ATR * (TP + 1), ENTRY - 1, 3, False),                 # Soft TP
        (ENTRY + 1, ENTRY - 1, MHC, False),                            # 시간만료
        (ENTRY + 1, ENTRY - 1, 4, True),                               # 당일청산
    ]
    for close, low, elapsed, fe in scenarios:
        ref = resolve_exit(entry_price=ENTRY, entry_atr=ATR, current_price=close, current_low=low,
                           take_profit=TP, stop_loss=SL, emergency_sl_mult=EMG, max_hold_candles=MHC,
                           elapsed_candles=elapsed, cost=COST, force_exit=fe, is_candle_close=True,
                           interval_minutes=1.0)
        live = pm.check_candle_exit(entry_price=ENTRY, entry_atr=ATR, candle_close=close,
                                    elapsed_candles=elapsed)
        if fe:
            # 실전 TBM 은 current_time 기반 force_exit 이므로 별도 경로로 동일성만 확인
            assert ref["exit_type"] == "당일청산"
            continue
        assert ref["exit_type"] == live["exit_type"]
        assert ref["exit_price"] == pytest.approx(live["exit_price"])
        assert ref["net_return"] == pytest.approx(live["net_return"])
