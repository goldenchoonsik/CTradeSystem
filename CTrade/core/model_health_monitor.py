"""
CTrade - Model Health Monitor
실전 운용 중인 AI 모델의 성능 감쇠(Model Drift / Edge Decay) 및 수명 도래를 실시간 감지하여
텔레그램 알림 발송 및 웹 대시보드 시각화 데이터를 제공합니다.
"""
import os
import sys
import time
import logging
from collections import deque
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class ModelHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"    # 정상 (표시 없음 또는 🟢)
    AGING = "AGING"        # 수명 도래 (30일 초과 💡)
    DECAY = "DECAY"        # 엣지 감쇠 (승률 급감 / 연속 손절 ⚠️)
    CRITICAL = "CRITICAL"  # 드로다운 위험 / 즉시 점검 필요 🚨


class ModelHealthMonitor:
    """종목별 AI 모델 건강 상태를 실시간 추적하고 알림을 관리하는 싱글톤 모니터"""
    _instance: Optional["ModelHealthMonitor"] = None

    def __init__(self):
        self._models: Dict[str, dict] = {}
        # 종목별 최근 거래 이력: symbol -> deque of bool (True=Win, False=Loss)
        self._trade_history: Dict[str, deque] = {}
        # 종목별 마지막 알림 발송 시각 및 상태
        self._last_alert_time: Dict[str, float] = {}
        self._last_alert_status: Dict[str, ModelHealthStatus] = {}
        self._alert_cooldown_seconds: float = 86400.0  # 24시간

    @classmethod
    def get_instance(cls) -> "ModelHealthMonitor":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_model(
        self,
        symbol: str,
        session_id: str,
        deployed_at: Optional[str] = None,
        strategy_name: str = "ai_baker",
        interval: str = "10m",
        backtest_win_rate: Optional[float] = None,
        backtest_mdd: Optional[float] = None,
    ):
        """AI 전략 모듈 부팅 또는 갱신 시 모델 메타데이터 등록"""
        if symbol not in self._trade_history:
            self._trade_history[symbol] = deque(maxlen=30)

        # 배포/생성 일시 파싱
        deployed_dt = None
        if deployed_at and deployed_at != "N/A":
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d_%H%M%S"):
                try:
                    deployed_dt = datetime.strptime(deployed_at.strip(), fmt)
                    break
                except ValueError:
                    pass

        self._models[symbol] = {
            "session_id": session_id,
            "strategy_name": strategy_name,
            "interval": interval,
            "deployed_at": deployed_at or "N/A",
            "deployed_dt": deployed_dt,
            "backtest_win_rate": backtest_win_rate,
            "backtest_mdd": backtest_mdd or 5.0,
            "registered_at": time.time(),
        }
        logger.info(f"[ModelHealthMonitor] Registered model for {symbol}: session={session_id}, interval={interval}, deployed={deployed_at}")

    def record_trade(self, symbol: str, is_win: bool, net_return: float = 0.0):
        """실전 매매 체결 완료 시 결과 기록"""
        if symbol not in self._trade_history:
            self._trade_history[symbol] = deque(maxlen=30)
        self._trade_history[symbol].append({
            "is_win": bool(is_win),
            "net_return": float(net_return),
            "timestamp": time.time()
        })
        logger.debug(f"[ModelHealthMonitor] Trade recorded for {symbol}: win={is_win}, return={net_return:+.2f}%")

    def evaluate_model_health(self, symbol: str, current_mdd: float = 0.0, notify: bool = True) -> dict:
        """종목의 모델 건강 상태 종합 진단 (notify=False 면 조회만 하고 텔레그램 발송하지 않음)"""
        model_info = self._models.get(symbol)
        if not model_info:
            return {
                "symbol": symbol,
                "status": ModelHealthStatus.HEALTHY.value,
                "badge_text": "",
                "badge_color": "",
                "warning_reasons": [],
                "age_days": 0.0,
                "recent_trades": 0,
                "recent_win_rate": 0.0,
                "consecutive_losses": 0,
                "current_mdd": current_mdd,
            }

        reasons = []
        status = ModelHealthStatus.HEALTHY

        # 1. 모델 나이 (Age) 계산
        now_dt = datetime.now()
        age_days = 0.0
        if model_info["deployed_dt"]:
            diff = now_dt - model_info["deployed_dt"]
            age_days = max(0.0, diff.total_seconds() / 86400.0)
        else:
            diff_sec = time.time() - model_info["registered_at"]
            age_days = diff_sec / 86400.0

        if age_days >= 30.0:
            status = ModelHealthStatus.AGING
            reasons.append(f"운용 {age_days:.1f}일 경과 (권장 수명 30일 초과)")

        # 2. 최근 거래 통계
        history = list(self._trade_history.get(symbol, []))
        recent_trades = len(history)
        recent_win_rate = 0.0
        consecutive_losses = 0

        if recent_trades > 0:
            wins = sum(1 for t in history if t["is_win"])
            recent_win_rate = (wins / recent_trades) * 100.0

            # 최근 연속 손절 계산 (역순 순회)
            for t in reversed(history):
                if not t["is_win"]:
                    consecutive_losses += 1
                else:
                    break

            # 4연속 이상 손절 시 Decay
            if consecutive_losses >= 4:
                status = ModelHealthStatus.DECAY
                reasons.append(f"{consecutive_losses}연속 손절 발생 (타점 불일치)")

            # 최소 5회 이상 거래 시 승률 40% 미만이면 Decay
            if recent_trades >= 5 and recent_win_rate < 40.0:
                status = ModelHealthStatus.DECAY
                reasons.append(f"최근 {recent_trades}회 승률 {recent_win_rate:.1f}% (기준 40% 미달)")

        # 3. MDD 한도 도달 진단
        bt_mdd = model_info.get("backtest_mdd", 5.0) or 5.0
        mdd_threshold = bt_mdd * 0.8
        if current_mdd >= mdd_threshold and current_mdd > 0.0:
            status = ModelHealthStatus.CRITICAL
            reasons.append(f"누적 낙폭 -{current_mdd:.1f}% (허용 한도 -{bt_mdd:.1f}%의 80% 도달)")

        # 뱃지 스타일 매핑
        badge_map = {
            ModelHealthStatus.HEALTHY: {"text": "", "color": ""},
            ModelHealthStatus.AGING: {"text": "💡 RE-TRAIN", "color": "amber"},
            ModelHealthStatus.DECAY: {"text": "⚠️ RE-TRAIN", "color": "yellow"},
            ModelHealthStatus.CRITICAL: {"text": "🚨 DRIFT", "color": "red"},
        }
        b_info = badge_map.get(status, {"text": "", "color": ""})

        report = {
            "symbol": symbol,
            "session_id": model_info.get("session_id", "N/A"),
            "strategy_name": model_info.get("strategy_name", "ai_baker"),
            "interval": model_info.get("interval", "10m"),
            "deployed_at": model_info.get("deployed_at", "N/A"),
            "status": status.value,
            "badge_text": b_info["text"],
            "badge_color": b_info["color"],
            "warning_reasons": reasons,
            "age_days": round(age_days, 1),
            "recent_trades": recent_trades,
            "recent_win_rate": round(recent_win_rate, 1),
            "consecutive_losses": consecutive_losses,
            "current_mdd": round(current_mdd, 2),
            "backtest_win_rate": model_info.get("backtest_win_rate"),
            "backtest_mdd": bt_mdd,
        }

        # 텔레그램 알림 체크 및 발송 (조회 전용 호출은 notify=False; 쿨다운은 _check_and_notify 가 유지)
        if notify:
            self._check_and_notify(report)
        return report

    def _check_and_notify(self, report: dict):
        """상태 전이 또는 24시간 쿨다운 만료 시 텔레그램 알림 발송"""
        status_str = report.get("status")
        if status_str == ModelHealthStatus.HEALTHY.value:
            return

        status = ModelHealthStatus(status_str)
        symbol = report["symbol"]
        now = time.time()

        last_time = self._last_alert_time.get(symbol, 0.0)
        last_status = self._last_alert_status.get(symbol)

        # 상태 심각도 레벨
        severity = {
            ModelHealthStatus.HEALTHY: 0,
            ModelHealthStatus.AGING: 1,
            ModelHealthStatus.DECAY: 2,
            ModelHealthStatus.CRITICAL: 3,
        }

        is_escalated = (last_status is None) or (severity.get(status, 0) > severity.get(last_status, 0))
        is_cooldown_expired = (now - last_time) >= self._alert_cooldown_seconds

        if not (is_escalated or is_cooldown_expired):
            return

        # 텔레그램 메시지 조립
        reasons_text = "\n".join(f"  • {r}" for r in report["warning_reasons"])
        icon = "🚨" if status == ModelHealthStatus.CRITICAL else ("⚠️" if status == ModelHealthStatus.DECAY else "💡")
        title = (
            "<b>[위험] CTrade AI 모델 낙폭 경고</b>" if status == ModelHealthStatus.CRITICAL
            else ("<b>[주의] CTrade AI 모델 엣지 감쇠</b>" if status == ModelHealthStatus.DECAY
                  else "<b>[권장] CTrade AI 모델 재학습 알림</b>")
        )

        msg = (
            f"{icon} {title}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>종목</b>: {symbol} ({report['interval']})\n"
            f"🧠 <b>세션</b>: <code>{report['session_id']}</code>\n"
            f"📅 <b>운용 기간</b>: {report['age_days']}일차 (배포: {report['deployed_at']})\n"
            f"📊 <b>최근 성과</b>: {report['recent_trades']}전 (승률 {report['recent_win_rate']:.1f}%)\n"
            f"📉 <b>현재 낙폭</b>: -{report['current_mdd']:.2f}% (한도: -{report['backtest_mdd']:.1f}%)\n"
            f"📋 <b>진단 사유</b>:\n{reasons_text}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💡 <b>권장 조치</b>: CAiModelBaker에서 최신 데이터를 포함한 신규 모델 재연구(AutoLab)를 권장합니다.\n"
            f"⏰ {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}"
        )

        try:
            from utils.telegram_bot import get_bot
            bot = get_bot()
            if bot:
                bot.send_message(msg)
                logger.info(f"[ModelHealthMonitor] Telegram alert sent for {symbol} ({status.value})")
                self._last_alert_time[symbol] = now
                self._last_alert_status[symbol] = status
        except Exception as e:
            logger.warning(f"[ModelHealthMonitor] Failed to send Telegram alert: {e}")

    def get_all_reports(self, current_mdds: Optional[Dict[str, float]] = None,
                        notify: bool = True) -> Dict[str, dict]:
        """등록된 모든 모델의 최신 건강 진단 리포트 반환.

        notify=False 로 호출하면 순수 조회(텔레그램 발송 없음). 주기 점검 루프(엔진)만
        notify=True(기본)로 호출하고, UI/API 조회는 notify=False 를 사용해야 한다.
        """
        reports = {}
        mdd_map = current_mdds or {}
        for symbol in list(self._models.keys()):
            reports[symbol] = self.evaluate_model_health(symbol, current_mdd=mdd_map.get(symbol, 0.0), notify=notify)
        return reports
