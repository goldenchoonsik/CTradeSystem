"""
CTradeSystem Shared - Hybrid Triple Barrier Method (TBM) Position Manager

Baker의 backtester.py 청산 로직을 클래스로 추상화하여
학습(백테스트)과 실전(CTrade) 모두에서 동일한 하이브리드 규칙을 적용합니다.

하이브리드 장벽(Hybrid Barrier) 정책 (STRATEGY.md 정책 준수):
  - Hard Emergency SL (실시간 틱 / Low): 플래시 크래시 방어용 비상 손절 (emergency_sl_mult, 기본 stop_loss * 2.0)
  - Soft TP / Soft SL (캔들 종가): 인트라바 꼬리(심지) 노이즈로 인한 휩소 방지를 위해 캔들 완성(종가) 시점에만 판정
  - 시간만료: max_hold_candles 초과 (캔들 완성 시 판정)
  - 당일청산: KRX 15:18 / 가상화폐 08:50 오버나잇 위험 회피

청산 조건 판단 순서:
  1. 당일청산 (KST 08:50 / 15:18)
  2. 비상손절 (Hard Emergency SL) — 틱/종가 상시 판정
  [아래는 is_candle_close=True (캔들 완성) 시에만 판정]
  3. 일반손절 (Soft SL) — 캔들 종가 기준
  4. 일반익절 (Soft TP) — 캔들 종가 기준
  5. 시간만료 — 캔들 완성 시 경과 캔들 수 기준
"""
from datetime import datetime
from typing import Optional


from shared.core.market_utils import is_krx_force_exit


def resolve_exit(entry_price: float, entry_atr: float,
                 current_price: float, current_low: float,
                 take_profit: float, stop_loss: float,
                 emergency_sl_mult: float, max_hold_candles: int,
                 elapsed_candles: int, cost: float,
                 force_exit: bool = False,
                 is_candle_close: bool = True,
                 interval_minutes: float = 1.0) -> Optional[dict]:
    """[단일 청산 판정 함수 — Single Source of Truth]

    학습(백테스트/WFA 검증/OOS)과 실전(CTrade)이 100% 동일한 청산 규칙을
    공유하도록 우선순위와 장벽 계산을 한 곳에 캡슐화합니다.

    우선순위 (docs/STRATEGY.md 청산 정책):
      1. 당일청산 (KRX 15:18, force_exit)  — 캔들/틱 무관 최우선
      2. 비상손절 (Hard Emergency SL)      — 인트라바 저가(current_low) 기준, 캔들/틱 상시
      ── 이하 is_candle_close=True(캔들 완성) 시에만 판정 ──
      3. 일반손절 (Soft SL)                — 캔들 종가(current_price) 기준
      4. 일반익절 (Soft TP)                — 캔들 종가(current_price) 기준
      5. 시간만료                          — 경과 캔들 수 기준

    Args:
        current_price: 캔들 종가 (실시간 틱 판정 시에는 틱가)
        current_low:   인트라바 저가 (실시간 틱 판정 시 current_price와 동일 값 전달)
        force_exit:    당일청산(15:18) 조건 충족 여부 (KRX 전용)
        is_candle_close: True=캔들 완성 종가 판정, False=실시간 틱 판정

    Returns:
        dict(exit_type, exit_price, net_return(%), hold_candles, hold_mins) 또는 None
    """
    def _calc_net_return(exit_price: float) -> float:
        gross = (exit_price - entry_price) / entry_price
        return (gross - cost) * 100.0

    hold_mins = int(elapsed_candles * interval_minutes)

    # 1. 당일청산 (KRX 주식/ETF 전용: 15:18 장마감 강제 청산) — 최우선
    if force_exit:
        return {
            "exit_type": "당일청산",
            "exit_price": current_price,
            "net_return": _calc_net_return(current_price),
            "hold_candles": elapsed_candles,
            "hold_mins": hold_mins,
        }

    # 2. 비상손절 (Hard Emergency SL) — 틱/캔들 상시 최우선 발동 (플래시 크래시 방어)
    emergency_sl_price = entry_price - (entry_atr * emergency_sl_mult)
    if current_low <= emergency_sl_price:
        # 장벽 터치 체결가는 비상손절선으로 통일 (실전/Low 시뮬레이션 동일)
        return {
            "exit_type": "비상손절",
            "exit_price": emergency_sl_price,
            "net_return": _calc_net_return(emergency_sl_price),
            "hold_candles": elapsed_candles,
            "hold_mins": hold_mins,
        }

    # ── 실시간 틱 판정(is_candle_close=False) 시 일반 TP/SL/시간만료는 보류 (휩소 방지) ──
    if not is_candle_close:
        return None

    # 3. 일반손절 (Soft SL) — 캔들 종가 기준
    sl_price = entry_price - (entry_atr * stop_loss)
    if current_price <= sl_price:
        return {
            "exit_type": "손절",
            "exit_price": current_price,
            "net_return": _calc_net_return(current_price),
            "hold_candles": elapsed_candles,
            "hold_mins": hold_mins,
        }

    # 4. 일반익절 (Soft TP) — 캔들 종가 기준
    tp_price = entry_price + (entry_atr * take_profit)
    if current_price >= tp_price:
        return {
            "exit_type": "익절",
            "exit_price": current_price,
            "net_return": _calc_net_return(current_price),
            "hold_candles": elapsed_candles,
            "hold_mins": hold_mins,
        }

    # 5. 시간만료 — 캔들 완성 시 경과 캔들 수 기준
    if elapsed_candles >= max_hold_candles:
        return {
            "exit_type": "시간만료",
            "exit_price": current_price,
            "net_return": _calc_net_return(current_price),
            "hold_candles": elapsed_candles,
            "hold_mins": hold_mins,
        }

    return None  # 청산 조건 미충족 → 계속 보유


class TBMPositionManager:
    """하이브리드 삼중 장벽법(Hybrid Triple Barrier Method) 기반 포지션 관리자

    Baker의 backtester.py 청산 로직과 수학적으로 100% 동일한 규칙을 적용합니다.
    """

    def __init__(self, params: dict, cost: float, interval_minutes: float,
                 intraday_square_off: bool = False, market_type: str = "CRYPTO"):
        """
        Args:
            params: best_params_{interval}.json에서 로드한 하이퍼파라미터
            cost: 왕복 거래 비용 (trade_costs.round_trip_cost — 수수료·슬리피지 왕복 + 매도세)
            interval_minutes: 캔들 인터벌 분 단위 (예: 30.0)
            intraday_square_off: KRX 주식/ETF 당일청산(15:18) 활성화 여부 (가상화폐는 24시간 연속 운용)
            market_type: 자산 유형 ("KRX" 또는 "CRYPTO")
        """
        self.take_profit = float(params['take_profit'])
        self.stop_loss = float(params['stop_loss'])
        self.emergency_sl_mult = float(params.get('emergency_sl_mult', self.stop_loss * 2.0))
        self.max_hold_candles = int(params.get('max_hold_candles', params['predict_ahead_candles']))
        self.cooldown_minutes = float(params.get('cooldown_minutes', 3))
        self.cooldown_candles = max(1, int(self.cooldown_minutes / interval_minutes))
        self.cost = float(cost)
        self.interval_minutes = float(interval_minutes)
        self.intraday_square_off = intraday_square_off
        self.market_type = str(market_type).upper()

    def check_exit(self, entry_price: float, entry_atr: float,
                   current_price: float, elapsed_candles: int,
                   current_time: Optional[datetime] = None,
                   is_candle_close: bool = False) -> Optional[dict]:
        """매 틱(실시간) 또는 캔들 완성 시점에 청산 조건을 판정합니다.

        실제 판정 규칙은 단일 소스 함수 `resolve_exit()`에 위임하여
        학습(Backtester/model_engine)과 실전(CTrade)이 100% 동일 규칙을 공유합니다.

        Args:
            entry_price: 진입가
            entry_atr: 진입 시점의 ATR 값
            current_price: 현재가 (틱 가격 또는 캔들 종가)
            elapsed_candles: 진입 후 경과 캔들 수
            current_time: 현재 KST 시각 (당일청산 판단용)
            is_candle_close: True이면 캔들 완성(종가) 시점 판정, False이면 실시간 틱 판정

        Returns:
            청산 사유 dict (exit_type, exit_price, net_return 등) 또는 None
        """
        # 당일청산(KRX 15:18) 조건 판정
        force_exit = False
        if self.intraday_square_off and self.market_type == "KRX" and current_time is not None:
            force_exit = is_krx_force_exit(current_time)

        return resolve_exit(
            entry_price=entry_price,
            entry_atr=entry_atr,
            current_price=current_price,
            current_low=current_price,  # 실시간 틱 판정: 틱가 == 인트라바 저가
            take_profit=self.take_profit,
            stop_loss=self.stop_loss,
            emergency_sl_mult=self.emergency_sl_mult,
            max_hold_candles=self.max_hold_candles,
            elapsed_candles=elapsed_candles,
            cost=self.cost,
            force_exit=force_exit,
            is_candle_close=is_candle_close,
            interval_minutes=self.interval_minutes,
        )

    def check_tick_exit(self, entry_price: float, entry_atr: float,
                        current_price: float, elapsed_candles: int,
                        current_time: Optional[datetime] = None) -> Optional[dict]:
        """실시간 틱 단위 청산 판정 편의 메서드 (Hard Emergency SL & 당일청산)"""
        return self.check_exit(
            entry_price=entry_price, entry_atr=entry_atr,
            current_price=current_price, elapsed_candles=elapsed_candles,
            current_time=current_time, is_candle_close=False
        )

    def check_candle_exit(self, entry_price: float, entry_atr: float,
                          candle_close: float, elapsed_candles: int,
                          current_time: Optional[datetime] = None) -> Optional[dict]:
        """캔들 완성 시점 청산 판정 편의 메서드 (Hard SL, Soft SL, Soft TP, 시간만료, 당일청산)"""
        return self.check_exit(
            entry_price=entry_price, entry_atr=entry_atr,
            current_price=candle_close, elapsed_candles=elapsed_candles,
            current_time=current_time, is_candle_close=True
        )

