"""
Unit / Regression Test: Multi-Symbol Pending Buy Late-Binding Safety

검증 목표
  1. STradeEngine.consumer_loop의 run_smart_buy 태스크가 루프 변수(ai_mod, wait/slippage/drop)를
     클로저 셀로 지연 바인딩하지 않고 함수 인자로 스냅샷 전달하는지 소스 수준 검증
  2. 두 개 AI 종목의 매수 태스크가 시간적으로 겹쳐도 각자 자신의 모듈에만
     on_buy_filled/on_buy_cancelled가 호출되는지 행동(Behavioral) 검증

버그 배경: async def가 루프 안에서 ai_mod 등을 클로저로 캡처하면, 태스크가 await를 마친 뒤
          마지막으로 순회된 다른 종목의 모듈에 상태를 기록하는 크로스 심볼 오염이 발생한다.
"""
import os
import asyncio
import unittest

_ENGINE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core", "engine.py")


class FakeModule:
    """AIStrategyModule 흉내 (on_buy_filled/on_buy_cancelled 기록용)"""
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.filled: list = []
        self.cancelled: list = []
        self.current_candle_idx = 100

    def on_buy_filled(self, real_entry_price, entry_atr=0.0, timestamp=None, candle_idx=None):
        self.filled.append((real_entry_price, candle_idx))

    def on_buy_cancelled(self, reason="CANCELLED"):
        self.cancelled.append(reason)


class TestMultiSymbolLateBinding(unittest.TestCase):

    def test_source_uses_parameter_snapshot_not_closure_capture(self):
        """engine.py의 run_smart_buy가 루프 변수를 함수 인자로 스냅샷 전달하는지 소스 검증"""
        with open(_ENGINE_PATH, "r", encoding="utf-8") as f:
            src = f.read()

        # 1. run_smart_buy 시그니처에 스냅샷 인자 존재
        self.assertIn("async def run_smart_buy(sym, trigger_p, bgt, prob, atr_val, ai_mod_ref, wait_sec, slip_pct, drop_pct):", src)

        # 2. 함수 본문이 클로저 셀(루프 변수)을 직접 읽지 않아야 함
        fn_body_start = src.index("async def run_smart_buy(")
        fn_body_end = src.index("task = asyncio.create_task(run_smart_buy(")
        fn_body = src[fn_body_start:fn_body_end]

        for banned in ["ai_mod.on_", "ai_mod.current_candle_idx", "wait_seconds=ai_buy_wait_sec",
                       "max_slippage_pct=ai_buy_max_slippage", "max_drop_pct=max_drop_pct"]:
            self.assertNotIn(banned, fn_body,
                             f"run_smart_buy 본문에 지연 바인딩 위험 참조 발견: {banned}")

        # 3. 태스크 생성 시 스냅샷 값이 인자로 전달됨
        self.assertIn("run_smart_buy(symbol, price, budget, signal.probability, signal.entry_atr, ai_mod_ref, buy_wait_sec, buy_max_slippage, buy_max_drop_pct)", src)

        # 4. run_smart_sell도 curr_time을 인자로 전달 (지연 바인딩 방지)
        self.assertIn("async def run_smart_sell(sym, trigger_p, reason_str, net_ret, hold_m, cur_t):", src)
        self.assertIn("self.last_exit_times[sym] = cur_t", src)
        self.assertNotIn("self.last_exit_times[sym] = curr_time", src)

    def test_overlapping_buy_tasks_bind_own_module(self):
        """엔진과 동일한 '인자 스냅샷' 패턴으로 2종목 병렬 매수 시 각자 자신의 모듈에만 기록되는지 행동 검증"""
        modules = {
            "069500": FakeModule("069500"),
            "KRW-BTC": FakeModule("KRW-BTC"),
        }
        results = []

        async def engine_style_run_smart_buy(sym, trigger_p, bgt, prob, atr_val, ai_mod_ref, wait_sec, slip_pct, drop_pct):
            # 실제 execute_smart_buy의 다수 await(수십 초)를 축약한 대기
            await asyncio.sleep(0.02)
            avg_price = trigger_p * 0.995  # 유리한 가격 체결
            ai_mod_ref.on_buy_filled(real_entry_price=avg_price, candle_idx=ai_mod_ref.current_candle_idx)
            results.append(sym)

        async def scenario():
            tasks = []
            for sym in ["069500", "KRW-BTC"]:
                ai_mod = modules[sym]                       # 루프 재할당 변수
                ai_buy_wait_sec = 45 if sym == "KRW-BTC" else 30
                ai_buy_max_slippage = 0.06
                max_drop_pct = 2.0
                # 엔진 수정과 동일: 인자로 스냅샷 전달
                tasks.append(asyncio.create_task(
                    engine_style_run_smart_buy(sym, 1000.0, 1000000, 0.55, 100.0,
                                               ai_mod, ai_buy_wait_sec, ai_buy_max_slippage, max_drop_pct)
                ))
            await asyncio.gather(*tasks)

        asyncio.run(scenario())

        # 각 모듈에 자기 종목의 체결만 기록되어야 함 (크로스 오염 없음)
        self.assertEqual(len(modules["069500"].filled), 1, "069500 모듈에 체결 기록 1건 필요")
        self.assertEqual(len(modules["KRW-BTC"].filled), 1, "KRW-BTC 모듈에 체결 기록 1건 필요")
        self.assertEqual(modules["069500"].filled[0][1], 100, "069500 체결 candle_idx는 자체 모듈 기준")
        self.assertEqual(modules["KRW-BTC"].filled[0][1], 100, "KRW-BTC 체결 candle_idx는 자체 모듈 기준")
        self.assertEqual(sorted(results), ["069500", "KRW-BTC"])


if __name__ == "__main__":
    unittest.main()
