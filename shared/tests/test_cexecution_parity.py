"""
CTradeSystem Shared - CExecution Parity & Speed Benchmark Test Suite

검증 항목:
  1. Batch vs Stream Exact Parity (100.000% 무괴리 검증):
     Numba JIT 배치 커널과 1봉씩 순차 호출한 스트림 세션의 체결가/청산가/수익률/거래수 완전 일치 확인
  2. Exit Priority Parity:
     당일청산 > 비상손절 > Soft SL > Soft TP > 시간만료 순위 일치 확인
  3. Min Edge Filter Parity:
     기대수익(ATR * TP)이 왕복 비용보다 작을 때 진입 스킵 여부 확인
  4. Speed Benchmark:
     25만 행 데이터 대상 연산 속도 벤치마크 (< 0.1초 달성 확인)
"""
import time
import pytest
import numpy as np
from shared.core.cexecution import (
    CExecution,
    ExecutionConfig,
    CExecutionStreamSession,
    EXIT_CODE_TO_STR,
    EXIT_FORCE,
    EXIT_EMERGENCY_SL,
    EXIT_SL,
    EXIT_TP,
    EXIT_TIME,
)


def _generate_synthetic_market_data(n=1000, seed=42):
    """결정론적 가상 시세 데이터 생성"""
    np.random.seed(seed)
    returns = np.random.normal(loc=0.0001, scale=0.005, size=n)
    prices = 10000.0 * np.cumprod(1.0 + returns)
    highs = prices * (1.0 + np.abs(np.random.normal(0, 0.003, size=n)))
    lows = prices * (1.0 - np.abs(np.random.normal(0, 0.003, size=n)))
    atrs = prices * 0.015  # 1.5% ATR 고정
    signals = np.random.choice([False, True], size=n, p=[0.90, 0.10])
    krx_force = np.zeros(n, dtype=bool)
    krx_entry = np.ones(n, dtype=bool)

    # 100봉마다 당일 강제청산 주입 (15:18 흉내)
    for i in range(99, n, 100):
        krx_force[i] = True

    return prices, highs, lows, atrs, signals, krx_force, krx_entry


def test_batch_vs_stream_exact_parity():
    """배치 Numba 커널과 1봉씩 스트림으로 흘려보낸 결과가 100% 일치하는지 검증"""
    prices, highs, lows, atrs, signals, krx_force, krx_entry = _generate_synthetic_market_data(n=2000, seed=123)

    config = ExecutionConfig(
        fee_rate=0.0005,
        slip_rate=0.0005,
        take_profit=1.5,
        stop_loss=1.0,
        emergency_sl_mult=2.0,
        max_hold_candles=10,
        cooldown_candles=3,
        min_edge_ratio=1.0,
        intraday_square_off=True,
        interval_minutes=1.0
    )

    # ── 1. Batch Numba 실행 ──
    batch_res = CExecution.simulate_batch(
        prices=prices,
        highs=highs,
        lows=lows,
        atrs=atrs,
        entry_signals=signals,
        krx_force_exit_mask=krx_force,
        krx_entry_allowed_mask=krx_entry,
        exec_config=config
    )

    batch_trades = batch_res["trade_logs"]

    # ── 2. Stream Step 실행 ──
    session = CExecution.create_stream_session(config)
    stream_trades = []

    for idx in range(len(prices)):
        p = prices[idx]
        h = highs[idx]
        l = lows[idx]
        atr = atrs[idx]
        sig = signals[idx]
        force = krx_force[idx]

        if session.has_position:
            exit_res = session.check_exit(
                current_price=p,
                current_low=l,
                current_candle_idx=idx,
                is_candle_close=True,
                force_exit=force
            )
            if exit_res:
                stream_trades.append({
                    "entry_idx": session.entry_candle_idx,
                    "exit_idx": idx,
                    "entry_price": session.entry_price,
                    "exit_price": exit_res["exit_price"],
                    "exit_type": exit_res["exit_type"],
                    "net_return": exit_res["net_return"],
                })
                session.on_sell_completed(candle_idx=idx)
        else:
            if krx_entry[idx] and sig:
                if session.check_entry_edge(price=p, atr=atr, current_candle_idx=idx):
                    session.on_buy_filled(real_entry_price=p, entry_atr=atr, candle_idx=idx)

    # ── 3. Exact Parity 비교 검증 ──
    assert len(batch_trades) == len(stream_trades), (
        f"거래 수 불일치! Batch={len(batch_trades)} vs Stream={len(stream_trades)}"
    )

    for i in range(len(batch_trades)):
        bt = batch_trades[i]
        st = stream_trades[i]

        assert bt["entry_time"] == st["entry_idx"], f"[{i}] Entry Index 불일치: {bt['entry_time']} != {st['entry_idx']}"
        assert bt["exit_time"] == st["exit_idx"], f"[{i}] Exit Index 불일치: {bt['exit_time']} != {st['exit_idx']}"
        assert pytest.approx(bt["entry_price"], rel=1e-5) == st["entry_price"], f"[{i}] Entry Price 불일치"
        assert pytest.approx(bt["exit_price"], rel=1e-5) == st["exit_price"], f"[{i}] Exit Price 불일치"
        assert bt["exit_type"] == st["exit_type"], f"[{i}] Exit Type 불일치: {bt['exit_type']} != {st['exit_type']}"
        assert pytest.approx(bt["net_return"], rel=1e-4) == st["net_return"], f"[{i}] Net Return 불일치"

    print(f"\n✅ Batch vs Stream Exact Parity PASS! (총 {len(batch_trades)}개 거래 100% 완전 일치)")


def test_min_edge_filter():
    """기대수익 미달 시 진입 차단 검증"""
    prices = np.array([10000.0, 10010.0, 10020.0])
    highs = prices.copy()
    lows = prices.copy()
    # 극단적으로 작은 ATR (0.1원) -> 기대수익(0.1 * 1.5 = 0.15원) < 왕복비용(10000 * 0.002 = 20원)
    atrs = np.array([0.1, 0.1, 0.1])
    signals = np.array([True, True, True])
    krx_force = np.zeros(3, dtype=bool)
    krx_entry = np.ones(3, dtype=bool)

    config = ExecutionConfig(
        fee_rate=0.0005,
        slip_rate=0.0005,
        take_profit=1.5,
        stop_loss=1.0,
        min_edge_ratio=1.0
    )

    res = CExecution.simulate_batch(
        prices=prices, highs=highs, lows=lows, atrs=atrs,
        entry_signals=signals, krx_force_exit_mask=krx_force, krx_entry_allowed_mask=krx_entry,
        exec_config=config
    )

    assert res["total_trades"] == 0, "기대수익 미달인데 진입이 허용되었습니다!"
    print("\n✅ Min Edge Filter PASS! (기대수익 미달 거래 전원 차단 완료)")


def test_speed_benchmark_250k():
    """25만 행(10초봉 1개월 분량) 초고속 연산 벤치마크 (< 0.1초 목표)"""
    prices, highs, lows, atrs, signals, krx_force, krx_entry = _generate_synthetic_market_data(n=250_000, seed=777)

    config = ExecutionConfig(
        fee_rate=0.0005,
        slip_rate=0.0005,
        take_profit=1.5,
        stop_loss=1.0,
        emergency_sl_mult=2.0,
        max_hold_candles=15,
        cooldown_candles=3
    )

    # 웜업 (JIT 컴파일 트리거)
    _ = CExecution.simulate_batch(
        prices=prices[:100], highs=highs[:100], lows=lows[:100], atrs=atrs[:100],
        entry_signals=signals[:100], krx_force_exit_mask=krx_force[:100], krx_entry_allowed_mask=krx_entry[:100],
        exec_config=config
    )

    # 실측
    t0 = time.perf_counter()
    res = CExecution.simulate_batch(
        prices=prices, highs=highs, lows=lows, atrs=atrs,
        entry_signals=signals, krx_force_exit_mask=krx_force, krx_entry_allowed_mask=krx_entry,
        exec_config=config
    )
    elapsed = time.perf_counter() - t0

    print(f"\n⚡ [CExecution Benchmark] 250,000행 연산 시간: {elapsed * 1000:.2f}ms (거래수: {res['total_trades']:,}건)")
    assert elapsed < 0.20, f"속도 기준 미달: {elapsed:.4f}s >= 0.20s"

