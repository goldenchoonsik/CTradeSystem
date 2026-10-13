"""
CTradeSystem Shared - Risk Manager (리스크 관리 레이어)

실전 트레이딩의 안전장치를 제공합니다:
  1. 일일 최대 손실 한도 (Daily Loss Limit)
  2. 연속 손절 차단기 (Consecutive Stop-Loss Circuit Breaker)
  3. 최대 동시 포지션 제한
"""
import time
import logging
from collections import deque
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


class RiskManager:
    """실전 매매 리스크 관리 레이어

    AIStrategyModule의 매수/매도 신호를 최종 필터링하여
    과도한 손실이 발생하기 전에 매매를 자동 중단합니다.
    """

    def __init__(self,
                 daily_loss_limit_pct: float = -3.0,
                 max_consecutive_losses: int = 3,
                 consecutive_loss_cooldown_min: int = 60,
                 max_concurrent_positions: int = 3):
        """
        Args:
            daily_loss_limit_pct: 일일 최대 손실 한도 (%, 음수). 예: -3.0 → 당일 -3% 이상 손실 시 매매 중단
            max_consecutive_losses: 연속 손절 허용 횟수. 예: 3 → 3연속 손절 시 쿨다운 발동
            consecutive_loss_cooldown_min: 연속 손절 쿨다운 시간 (분). 예: 60 → 1시간 매매 금지
            max_concurrent_positions: 최대 동시 포지션 수
        """
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.consecutive_loss_cooldown_min = consecutive_loss_cooldown_min
        self.max_concurrent_positions = max_concurrent_positions

        # 상태 추적
        self._daily_pnl_pct = 0.0          # 당일 누적 수익률 (%)
        self._daily_trades = 0              # 당일 거래 횟수
        self._consecutive_losses = 0        # 현재 연속 손절 횟수
        self._circuit_breaker_until = 0.0   # 차단기 해제 시각 (Unix timestamp)
        self._current_positions = 0         # 현재 보유 포지션 수
        self._last_reset_date = ""          # 마지막 일일 리셋 날짜 (YYYY-MM-DD)
        self._trade_history = deque(maxlen=100)  # 최근 거래 히스토리

        # 플래그
        self.is_daily_limit_hit = False
        self.is_circuit_breaker_active = False

    def _check_daily_reset(self):
        """자정(KST) 기준 일일 카운터 리셋"""
        today = datetime.now(KST).strftime('%Y-%m-%d')
        if today != self._last_reset_date:
            self._daily_pnl_pct = 0.0
            self._daily_trades = 0
            self.is_daily_limit_hit = False
            self._last_reset_date = today
            logging.info(f"[RiskManager] 일일 리셋: {today}")

    def can_open_position(self) -> tuple:
        """매수 가능 여부를 판단합니다.

        Returns:
            (allowed: bool, reason: str)
        """
        self._check_daily_reset()

        # 1. 일일 손실 한도 체크
        if self._daily_pnl_pct <= self.daily_loss_limit_pct:
            self.is_daily_limit_hit = True
            return False, f"일일 손실 한도 도달 ({self._daily_pnl_pct:+.2f}% <= {self.daily_loss_limit_pct}%)"

        # 2. 연속 손절 차단기 체크
        if self._consecutive_losses >= self.max_consecutive_losses:
            now = time.time()
            if now < self._circuit_breaker_until:
                remaining = (self._circuit_breaker_until - now) / 60
                self.is_circuit_breaker_active = True
                return False, f"연속 손절 차단기 발동 ({self._consecutive_losses}연패, 잔여 {remaining:.0f}분)"
            else:
                # 쿨다운 만료 → 차단기 해제
                self._consecutive_losses = 0
                self.is_circuit_breaker_active = False

        # 3. 최대 동시 포지션 체크
        if self._current_positions >= self.max_concurrent_positions:
            return False, f"최대 동시 포지션 초과 ({self._current_positions}/{self.max_concurrent_positions})"

        return True, "OK"

    def on_position_opened(self):
        """포지션 진입 시 호출"""
        self._current_positions += 1

    def on_position_cancelled(self):
        """체결 전 취소된 매수의 슬롯 반환 (손익/연패 통계에 영향 없이 보유 카운트만 감소)."""
        self._current_positions = max(0, self._current_positions - 1)

    def on_position_closed(self, net_return_pct: float, exit_type: str):
        """포지션 청산 시 호출

        Args:
            net_return_pct: 순수익률 (%) — 양수면 이익, 음수면 손실
            exit_type: 청산 사유 ('익절', '손절', '시간만료', '당일청산')
        """
        self._check_daily_reset()
        self._current_positions = max(0, self._current_positions - 1)
        self._daily_pnl_pct += net_return_pct
        self._daily_trades += 1

        self._trade_history.append({
            'time': datetime.now(KST).strftime('%H:%M:%S'),
            'net_return_pct': net_return_pct,
            'exit_type': exit_type,
            'daily_pnl': self._daily_pnl_pct
        })

        # 연속 손절 추적
        if net_return_pct < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.max_consecutive_losses:
                self._circuit_breaker_until = time.time() + (self.consecutive_loss_cooldown_min * 60)
                self.is_circuit_breaker_active = True
                logging.warning(
                    f"[RiskManager] ⚡ 연속 손절 차단기 발동! "
                    f"{self._consecutive_losses}연패 → {self.consecutive_loss_cooldown_min}분 매매 중단"
                )
        else:
            self._consecutive_losses = 0
            self.is_circuit_breaker_active = False

        # 일일 손실 한도 도달 체크
        if self._daily_pnl_pct <= self.daily_loss_limit_pct:
            self.is_daily_limit_hit = True
            logging.warning(
                f"[RiskManager] 🛑 일일 손실 한도 도달! "
                f"당일 누적: {self._daily_pnl_pct:+.2f}% (한도: {self.daily_loss_limit_pct}%) → 당일 매매 중단"
            )

    def get_status(self) -> dict:
        """현재 리스크 관리 상태 반환 (UI 연동용)"""
        self._check_daily_reset()
        return {
            'daily_pnl_pct': self._daily_pnl_pct,
            'daily_trades': self._daily_trades,
            'consecutive_losses': self._consecutive_losses,
            'is_daily_limit_hit': self.is_daily_limit_hit,
            'is_circuit_breaker_active': self.is_circuit_breaker_active,
            'current_positions': self._current_positions,
            'daily_loss_limit_pct': self.daily_loss_limit_pct,
            'max_consecutive_losses': self.max_consecutive_losses,
        }
