import asyncio
import time
import logging
from typing import Optional, Dict, Any

class SmartOrderExecutor:
    """
    BTC 30분봉 등 시스템 트레이딩 전용 스마트 주문 집행기
    - 슬리피지 한도 통제 및 쫓아가기 매수 금지
    - ATR 기반 최소 수익성 검증 (min_edge_check)
    - 익절/손절 사유별 지정가/시장가 매도 분리
    """

    def __init__(self, engine):
        self.engine = engine

    def min_edge_check(self, entry_price: float, entry_atr: float, take_profit_mult: float, fee_rate: float = 0.0005,
                       sell_tax: float = 0.0) -> bool:
        """
        최소 수익성 검증:
        ATR × take_profit_mult (예상 이익폭)가 왕복 비용 (round_trip_cost: 수수료 2회 + 매도세) 보다 큰지 확인
        """
        from shared.core.trade_costs import round_trip_cost
        if entry_price <= 0:
            return False
        expected_gain_pct = (entry_atr * take_profit_mult) / entry_price
        round_trip_cost_pct = round_trip_cost(fee_rate, 0.0, sell_tax)
        
        if expected_gain_pct < round_trip_cost_pct:
            logging.warning(
                f"[SmartExecutor] ⚠️ 최소 수익성 미달로 신호 스킵: "
                f"기대수익({expected_gain_pct*100:.3f}%) < 왕복비용({round_trip_cost_pct*100:.3f}%)"
            )
            return False
        return True

    async def execute_smart_buy(
        self, 
        symbol: str, 
        trigger_price: float, 
        budget: float, 
        wait_seconds: int = 360, 
        max_slippage_pct: float = 0.06,
        max_drop_pct: Optional[float] = None
    ) -> Optional[float]:
        """
        지정가 대기 매수 집행
        - wait_seconds 동안 틱 감시하며 신호가 이하 진입 시도
        - wait_seconds 경과 시점에 가격이 슬리피지 한도(max_slippage_pct) 이하이면 체결, 초과 시 쫓아가지 않고 취소
        - 낙수 방지: max_drop_pct 초과 하락 시(급락장) 매수를 포기하고 취소
        - 성공 시 실제 평균 체결 단가(avg_price) 반환, 실패/취소 시 None 반환
        """
        logging.info(f"[SmartExecutor] 매수 집행 시작: {symbol} | 신호가: {trigger_price:,.0f} | 예산: {budget:,.0f}원 | 대기: {wait_seconds}초")
        
        start_time = time.time()
        poll_interval = 2.0  # 2초 주기로 시세 감시

        limit_price = trigger_price * (1.0 + (max_slippage_pct / 100.0))
        min_allowed_price = trigger_price * (1.0 - (max_drop_pct / 100.0)) if max_drop_pct else 0.0

        try:
            while time.time() - start_time < wait_seconds:
                # 엔진 정지 또는 AI 전략의 pending_entry 플래그 해제(취소/손절/리셋) 감지 시 즉시 루프 탈출
                if not getattr(self.engine, 'is_running', True):
                    logging.info(f"[SmartExecutor] 🛑 엔진 정지로 {symbol} 매수 대기 종료")
                    return None
                
                ai_mod = self.engine.ai_strategies.get(symbol)
                if ai_mod and not getattr(ai_mod, 'is_pending_entry', True):
                    logging.info(f"[SmartExecutor] 🛑 {symbol} 매수 대기 상태 취소 감지. 대기 루프 즉시 종료")
                    return None

                ticker = self.engine.data_manager.store.get(symbol, {}).get("ticker")
                current_price = ticker.price if ticker else 0.0

                if current_price > 0:
                    # 0. 낙수(폭락) 방지 안전장치
                    if min_allowed_price > 0 and current_price < min_allowed_price:
                        drop_pct = ((current_price / trigger_price) - 1.0) * 100.0
                        logging.warning(
                            f"[SmartExecutor] ⛔ 급락 감지({drop_pct:+.2f}% < -{max_drop_pct}%). "
                            f"낙수 진입 방지를 위해 {symbol} 매수 취소!"
                        )
                        return None

                    # 1. 신호가 이하로 내려오면 즉시 체결 시도 (유리한 가격 진입)
                    if current_price <= trigger_price:
                        logging.info(f"[SmartExecutor] 🎯 유리한 가격 감지 ({current_price:,.0f} <= {trigger_price:,.0f}). 즉시 매수 집행")
                        success = await self.engine.execute_buy(symbol, current_price, budget)
                        if success:
                            pos = self.engine.simulator.positions.get(symbol, {})
                            avg_price = pos.get("price", current_price)
                            return avg_price
                        return None

                await asyncio.sleep(poll_interval)

            # 대기시간 만료 후 최종 가격 확인
            ticker = self.engine.data_manager.store.get(symbol, {}).get("ticker")
            final_price = ticker.price if ticker else 0.0

            if final_price <= 0:
                logging.error(f"[SmartExecutor] 대기 만료 후 시세 수신 실패로 {symbol} 매수 취소")
                return None

            if min_allowed_price > 0 and final_price < min_allowed_price:
                logging.warning(f"[SmartExecutor] ⛔ 대기 만료 시점 급락 상태 감지로 {symbol} 매수 취소")
                return None

            price_diff_pct = ((final_price / trigger_price) - 1.0) * 100.0

            if final_price <= limit_price:
                # 슬리피지 한도 이내이면 매수 집행
                logging.info(f"[SmartExecutor] ⏱️ 대기 만료({wait_seconds}초). 슬리피지 허용 범위 내({price_diff_pct:+.3f}% <= +{max_slippage_pct}%). 매수 체결")
                success = await self.engine.execute_buy(symbol, final_price, budget)
                if success:
                    pos = self.engine.simulator.positions.get(symbol, {})
                    avg_price = pos.get("price", final_price)
                    return avg_price
                return None
            else:
                # 슬리피지 한도 초과 ➔ 쫓아가지 않고 취소 (Abort)
                logging.warning(f"[SmartExecutor] ⛔ 슬리피지 한도 초과({price_diff_pct:+.3f}% > +{max_slippage_pct}%). 쫓아가지 않고 매수 취소!")
                try:
                    from utils.telegram_bot import get_bot
                    if bot := get_bot():
                        bot.send_message(
                            f"⛔ <b>[AI 매수 취소] {symbol}</b>\n"
                            f"━━━━━━━━━━━━━━━━\n"
                            f"신호가: {trigger_price:,.0f}원\n"
                            f"현재가: {final_price:,.0f}원 ({price_diff_pct:+.2f}%)\n"
                            f"⚠️ 허용 슬리피지(+{max_slippage_pct}%) 초과로 진입을 포기하였습니다."
                        )
                except Exception: pass
                return None
        except asyncio.CancelledError:
            logging.info(f"[SmartExecutor] 🛑 {symbol} 매수 대기 태스크가 취소(Cancelled)되었습니다.")
            raise

    async def execute_smart_sell(
        self, 
        symbol: str, 
        trigger_price: float, 
        reason: str = "SIGNAL"
    ) -> Optional[float]:
        """
        사유별 매도 집행
        - 손절(LOSS / SL) / 시간만료 / 긴급: 즉시 시장가 매도
        - 익절(PROFIT / TP): 30초 지정가 대기 후 미체결 시 시장가 매도
        """
        logging.info(f"[SmartExecutor] 매도 집행 시작: {symbol} | 사유: {reason} | 현재가: {trigger_price:,.0f}")
        
        is_loss_or_urgent = any(kw in reason.upper() for kw in [
            "LOSS", "SL", "손절", "EMERGENCY", "TIMEOUT",
            "당일청산", "장마감청산", "장마감", "DAY_EXIT", "FORCE_EXIT", "SQUARE_OFF"
        ])

        if is_loss_or_urgent:
            # 손절, 시간만료 또는 장마감 당일청산 등 긴급 매도는 즉시 시장가 처리
            profit_amt = await self.engine.execute_sell(symbol, trigger_price, reason=reason)
            return profit_amt if profit_amt is not False else None
        else:
            # 익절(TP) 등 정상 청산은 지정가 대기 후 매도
            logging.info(f"[SmartExecutor] 익절({reason}) 지정가 매도 대기 (최대 30초)")
            start_time = time.time()
            
            while time.time() - start_time < 30.0:
                # 엔진 정지 감지 → 즉시 탈출 (매수 루프와 대칭)
                if not getattr(self.engine, 'is_running', True):
                    logging.info(f"[SmartExecutor] 🛑 엔진 정지로 {symbol} 매도 대기 즉시 종료")
                    break
                # AI 전략 모듈의 is_pending_exit 해제 감지 (외부 취소)
                ai_mod = self.engine.ai_strategies.get(symbol)
                if ai_mod and not getattr(ai_mod, 'is_pending_exit', True):
                    logging.info(f"[SmartExecutor] 🛑 {symbol} 매도 대기 상태 취소 감지. 루프 즉시 종료")
                    break

                ticker = self.engine.data_manager.store.get(symbol, {}).get("ticker")
                curr_p = ticker.price if ticker else trigger_price
                if curr_p >= trigger_price:
                    profit_amt = await self.engine.execute_sell(symbol, curr_p, reason=reason)
                    return profit_amt if profit_amt is not False else None
                await asyncio.sleep(1.0)
                
            # 30초 초과 또는 조기 탈출 시 시장가로 최종 청산
            ticker = self.engine.data_manager.store.get(symbol, {}).get("ticker")
            final_p = ticker.price if ticker else trigger_price
            profit_amt = await self.engine.execute_sell(symbol, final_p, reason=reason)
            return profit_amt if profit_amt is not False else None
