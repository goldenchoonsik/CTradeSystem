"""
CTrade Telegram Bot
====================
- 단방향 알림: 체결/에러/시작/종료 시 텔레그램 메시지 전송
- 양방향 명령: /status /balance /trades /log /pause /resume /help

환경변수 (.env):
    TELEGRAM_BOT_TOKEN : BotFather에서 발급받은 토큰
    TELEGRAM_CHAT_ID   : 봇과 대화한 사용자(또는 그룹)의 Chat ID
"""

import os
import logging
import threading
import time
import json
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 싱글턴: 엔진/시뮬레이터 어디서든 get_bot() 으로 인스턴스 참조
# ─────────────────────────────────────────────────────────────────────────────
_bot_instance: Optional["TelegramBot"] = None

def get_bot() -> Optional["TelegramBot"]:
    return _bot_instance


def _fmt_krw(value: float) -> str:
    """원화 포맷 (예: 142,500,000원)"""
    sign = "-" if value < 0 else ""
    return f"{sign}{abs(value):,.0f}원"


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _fmt_qty(qty: float) -> str:
    """수량 포맷 (주식은 정수주, 코인은 소수점)"""
    if qty <= 0:
        return "0"
    if abs(qty - round(qty)) < 1e-5:
        return f"{int(round(qty)):,}주"
    return f"{qty:.8f}".rstrip("0").rstrip(".")


def _fmt_duration(seconds: float) -> str:
    """초 단위 시간을 '1시간 20분 30초' 또는 '29분 59초' 형태로 포맷"""
    if seconds <= 0:
        return "즉시"
    sec = int(round(seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}시간 {m}분 {s}초" if s > 0 else f"{h}시간 {m}분"
    if m > 0:
        return f"{m}분 {s}초" if s > 0 else f"{m}분"
    return f"{s}초"


def _get_symbol_name(symbol: str) -> str:
    """종목코드에 친절한 종목명 결합 (종목 마스터 원장 경유)"""
    from shared.config.config_manager import get_symbol_display_name
    return get_symbol_display_name(symbol)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



# ─────────────────────────────────────────────────────────────────────────────
# 텔레그램 봇 클래스
# ─────────────────────────────────────────────────────────────────────────────
class TelegramBot:
    """
    CTrade 운영용 양방향 텔레그램 봇.

    - push: send_message() 로 알림 전송
    - pull: _poll_loop() 스레드가 getUpdates 폴링하여 명령어 처리
    """

    POLL_INTERVAL = 3   # getUpdates 폴링 간격 (초)
    API_TIMEOUT   = 8   # HTTP 요청 타임아웃 (초)

    def __init__(self, shared_state=None):
        global _bot_instance

        self.token    = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id  = os.getenv("TELEGRAM_CHAT_ID",  "").strip()
        self.shared   = shared_state  # SharedState (main.py 에서 주입)

        self._enabled = bool(self.token and self.chat_id)
        self._base    = f"https://api.telegram.org/bot{self.token}"
        self._offset  = 0
        self._start_time = time.time()

        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="TelegramPoll"
        )

        _bot_instance = self

        if self._enabled:
            logger.info("[TelegramBot] 활성화: Chat ID = %s", self.chat_id)
        else:
            logger.warning(
                "[TelegramBot] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 미설정 → 비활성화"
            )

    # ── 외부 API ─────────────────────────────────────────────────────────────

    def _setup_bot_commands(self):
        """텔레그램 앱의 '/' 메뉴 버튼 누를 때 명령어 목록 팝업이 뜨도록 등록"""
        if not self._enabled:
            return
        commands = [
            {"command": "status", "description": "📊 엔진 상태 요약"},
            {"command": "balance", "description": "💰 잔고 및 수익률 현황"},
            {"command": "trades", "description": "📋 최근 5건 체결 내역"},
            {"command": "auto", "description": "⚡ 종목별 자동매매 제어 (예: /auto KRW-BTC off)"},
            {"command": "log", "description": "📄 최근 로그 20줄 보기"},
            {"command": "pause", "description": "⏸ 매매 일시 정지"},
            {"command": "resume", "description": "▶️ 매매 재개"},
            {"command": "close", "description": "🔴 지정 종목 수동 청산"},
            {"command": "closeall", "description": "🚨 전 종목 일괄 긴급 청산"},
            {"command": "buy", "description": "🟢 수동 매수 (예: /buy KRW-BTC 6000)"},
            {"command": "sell", "description": "🔴 수동 매도 (예: /sell KRW-BTC)"},
            {"command": "help", "description": "📖 명령어 도움말"}
        ]
        try:
            requests.post(
                f"{self._base}/setMyCommands",
                json={"commands": commands},
                timeout=self.API_TIMEOUT
            )
            logger.info("[TelegramBot] 텔레그램 명령어 메뉴 (setMyCommands) 등록 완료")
        except Exception as e:
            logger.warning("[TelegramBot] setMyCommands 등록 실패: %s", e)

    def start(self):
        """폴링 스레드 시작 (기본값: 비활성화하여 CCommander와 충돌 방지, 필요 시 TELEGRAM_ENABLE_POLLING=true 로 활성화)"""
        enable_polling = os.getenv("TELEGRAM_ENABLE_POLLING", "false").strip().lower() in ("true", "1", "yes")
        if self._enabled and enable_polling:
            self._setup_bot_commands()
            self._poll_thread.start()
            logger.info("[TelegramBot] 폴링 스레드 시작 (양방향 모드)")
        elif self._enabled:
            logger.info("[TelegramBot] 알림 전송 전용 모드로 활성화 (명령어 관제는 CCommander가 전담)")

    def send_message(self, text: str) -> bool:
        """텔레그램으로 메시지 전송. 실패 시 False 반환 (예외 미전파)."""
        if not self._enabled:
            return False
        try:
            r = requests.post(
                f"{self._base}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=self.API_TIMEOUT,
            )
            if not r.ok:
                logger.warning("[TelegramBot] sendMessage 실패: %s", r.text)
                return False
            return True
        except Exception as e:
            logger.warning("[TelegramBot] sendMessage 예외: %s", e)
            return False

    # ── 알림 메서드 (simulator / engine 에서 호출) ───────────────────────────

    def notify_start(self, version: str, mode: str, active_symbols: list):
        symbols_str = ", ".join(active_symbols) if active_symbols else "없음"
        sim_tag = "🔵 시뮬레이션" if "simul" in mode.lower() or mode == "True" else "🔴 실전매매"
        self.send_message(
            f"🚀 <b>CTrade 시작</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📌 버전: {version}\n"
            f"🎯 모드: {sim_tag}\n"
            f"📊 활성 종목: {symbols_str}\n"
            f"⏰ {_now_str()}"
        )

    def notify_stop(self, reason: str = "정상 종료"):
        self.send_message(
            f"🛑 <b>CTrade 종료</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📋 사유: {reason}\n"
            f"⏰ {_now_str()}"
        )

    def _resolve_is_real(self, extra: dict = None, is_real: bool = None) -> bool:
        """실전 매매 여부 판별 (1순위: 파라미터, 2순위: extra['is_real'], 3순위: config)"""
        if is_real is not None:
            return bool(is_real)
        if extra:
            if "is_real" in extra:
                return bool(extra["is_real"])
            if extra.get("mode") == "REAL":
                return True
            if extra.get("mode") == "SIMULATION":
                return False
        # control 또는 config 조회
        shared_obj = getattr(self, "shared", None)
        if shared_obj:
            try:
                ctrl = dict(shared_obj.control)
                if "simulation_mode" in ctrl:
                    return not bool(ctrl["simulation_mode"])
            except Exception:
                pass
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "trading_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    return not bool(cfg.get("GLOBAL_STRATEGY", {}).get("SIMULATION_MODE", True))
        except Exception:
            pass
        return False

    def notify_buy(self, symbol: str, price: float, qty: float, total_cost: float, extra: dict = None, is_real: bool = None):
        real = self._resolve_is_real(extra, is_real)
        fee = extra.get("fee", 0.0) if extra else 0.0
        # 수수료 포함 실제 매입 총액
        net_cost = total_cost + fee
        sym_name = _get_symbol_name(symbol)
        mode_badge = "🔴 실전매매" if real else "🔵 가상매매"
        
        balance_line = ""
        if extra and "balance" in extra:
            bal = float(extra["balance"])
            bal_label = "실제 계좌 잔고" if real else "가상 운용 잔고"
            balance_line = f"\n🏦 <b>{bal_label}</b>: <code>{_fmt_krw(bal)}</code>"

        self.send_message(
            f"🟢 <b>[BUY] {sym_name}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>체결 모드</b>: <b>{mode_badge}</b>\n"
            f"💰 <b>매수 단가</b>: <code>{_fmt_krw(price)}</code>\n"
            f"📦 <b>체결 수량</b>: <code>{_fmt_qty(qty)}</code>\n"
            f"💼 <b>실제 구입액</b>: <code>{_fmt_krw(net_cost)}</code> (수수료: {fee:,.0f}원)"
            f"{balance_line}\n"
            f"⏰ <code>{_now_str()}</code>"
        )

    def notify_sell(self, symbol: str, price: float, profit: float,
                    profit_rate: float, reason: str = "SIGNAL", extra: dict = None, is_real: bool = None):
        real = self._resolve_is_real(extra, is_real)
        buy_p = float(extra.get("buy_price", 0.0)) if extra else 0.0
        sell_p = float(extra.get("sell_price", price)) if extra else float(price)
        fee = float(extra.get("fee", 0.0)) if extra else 0.0
        qty = float(extra.get("qty", 0.0)) if extra else 0.0
        invest_amt = float(extra.get("amount", buy_p * qty)) if extra else (buy_p * qty)
        holding_sec = float(extra.get("holding_time", 0.0)) if extra else 0.0
        holding_str = _fmt_duration(holding_sec) if holding_sec > 0 else "즉시"
        
        # 순수익률(Net ROI %) 계산: profit_rate가 유효하면 사용, 아니면 순손익/실제구입액 기준 산출
        if abs(profit_rate) > 1e-6:
            rate_val = profit_rate
        elif invest_amt > 0:
            rate_val = (profit / (invest_amt + fee)) * 100.0
        elif buy_p > 0:
            rate_val = ((sell_p - buy_p) / buy_p) * 100.0
        else:
            rate_val = 0.0

        # 단순 호가 변동률 (Gross Price Change %)
        price_change_pct = ((sell_p - buy_p) / buy_p * 100.0) if buy_p > 0 else 0.0
        price_change_str = f" ({_fmt_pct(price_change_pct)})" if buy_p > 0 else ""

        emoji = "📈" if profit >= 0 else "📉"
        p_sign = "+" if profit > 0 else ""
        sym_name = _get_symbol_name(symbol)
        mode_badge = "🔴 실전매매" if real else "🔵 가상매매"

        # 통산 누적 성과 실시간 집계 (trade_history.json) - 실전/가상 분리 필터링
        summary_section = ""
        try:
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            hist_path = os.path.join(root_dir, "trade_history.json")
            if not os.path.exists(hist_path):
                hist_path = os.path.join(root_dir, "CTrade", "trade_history.json")
            if os.path.exists(hist_path):
                with open(hist_path, "r", encoding="utf-8") as f:
                    hist = json.load(f)
                
                all_sells = [h for h in hist if h.get("type") == "SELL"]
                if real:
                    sells = [h for h in all_sells if h.get("is_real") is True or h.get("mode") == "REAL"]
                    mode_label = "실전 실시간 성과"
                else:
                    sells = [h for h in all_sells if not (h.get("is_real") is True or h.get("mode") == "REAL")]
                    mode_label = "가상 실시간 성과"

                tot_trades = len(sells)
                wins = len([h for h in sells if float(h.get("profit", 0.0)) > 0])
                losses = len([h for h in sells if float(h.get("profit", 0.0)) < 0])
                win_rate = (wins / tot_trades * 100.0) if tot_trades > 0 else 0.0
                tot_profit = sum(float(h.get("profit", 0.0)) for h in sells)
                tot_gain = sum(float(h.get("profit", 0.0)) for h in sells if float(h.get("profit", 0.0)) > 0)
                tot_loss = abs(sum(float(h.get("profit", 0.0)) for h in sells if float(h.get("profit", 0.0)) < 0))
                pf = (tot_gain / tot_loss) if tot_loss > 0 else (99.9 if tot_gain > 0 else 0.0)
                summed_roi = sum(float(h.get("profit_rate", 0.0)) for h in sells)
                
                # 당일 통계
                today_str = datetime.now().strftime("%Y-%m-%d")
                today_sells = []
                for h in sells:
                    ts = h.get("time", 0.0) or h.get("exit_time", 0.0) or h.get("entry_time", 0.0)
                    if ts > 0:
                        try:
                            if datetime.fromtimestamp(ts).strftime("%Y-%m-%d") == today_str:
                                today_sells.append(h)
                        except Exception:
                            pass
                today_profit = sum(float(h.get("profit", 0.0)) for h in today_sells)
                today_roi = sum(float(h.get("profit_rate", 0.0)) for h in today_sells)
                
                tot_p_sign = "+" if tot_profit >= 0 else ""
                today_p_sign = "+" if today_profit >= 0 else ""

                # 잔고 라인 분리: 실전 매매는 실제 계좌 잔고 표시 (가상 1천만 원 배제)
                bal_val = 0.0
                if extra and "balance" in extra:
                    bal_val = float(extra["balance"])
                elif sells and "balance" in sells[-1]:
                    bal_val = float(sells[-1]["balance"])
                
                bal_label = "실제 계좌 잔고" if real else "가상 운용 잔고"
                bal_line = f"\n  • {bal_label}: <code>{_fmt_krw(bal_val)}</code>" if bal_val > 0 else ""

                summary_section = (
                    f"\n━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>통산 누적 성과 ({mode_label})</b>:\n"
                    f"  • 시행 횟수: <code>{tot_trades}전 {wins}승 {losses}패</code> (승률 <b>{win_rate:.1f}%</b> | PF <b>{pf:.2f}</b>)\n"
                    f"  • 당일 손익: <b>{today_p_sign}{_fmt_krw(today_profit)} ({_fmt_pct(today_roi)})</b>\n"
                    f"  • 통산 손익: <b>{tot_p_sign}{_fmt_krw(tot_profit)}</b> (회전 <b>{_fmt_pct(summed_roi)}</b>)"
                    f"{bal_line}"
                )
        except Exception as e:
            logger.debug(f"[TelegramBot] notify_sell summary calculation exception: {e}")

        self.send_message(
            f"🔴 <b>[SELL] {sym_name}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>체결 모드</b>: <b>{mode_badge}</b>\n"
            f"💰 <b>순 실현손익</b>: {emoji} <b>{p_sign}{_fmt_krw(profit)} ({_fmt_pct(rate_val)})</b>\n"
            f"  • 가격 변동: <code>{_fmt_krw(buy_p)}</code> → <code>{_fmt_krw(sell_p)}</code>{price_change_str}\n"
            f"  • 매수 총액: <code>{_fmt_krw(invest_amt)}</code> ({_fmt_qty(qty)})\n"
            f"  • 거래 수수료: <code>{fee:,.0f}원</code>\n"
            f"  • 보유 시간: <code>{holding_str}</code> | 사유: <code>{reason}</code>"
            f"{summary_section}\n"
            f"⏰ <code>{_now_str()}</code>"
        )

    def notify_error(self, error_msg: str):
        self.send_message(
            f"🚨 <b>CTrade 에러 발생</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"❌ {error_msg}\n"
            f"⏰ {_now_str()}"
        )

    # ── 폴링 루프 ────────────────────────────────────────────────────────────

    def _poll_loop(self):
        """getUpdates 방식으로 텔레그램 명령을 폴링하여 처리"""
        logger.info("[TelegramBot] 폴링 시작 (interval=%ds)", self.POLL_INTERVAL)
        while True:
            try:
                updates = self._get_updates()
                for upd in updates:
                    self._handle_update(upd)
            except Exception as e:
                logger.warning("[TelegramBot] 폴링 예외: %s", e)
            time.sleep(self.POLL_INTERVAL)

    def _get_updates(self) -> list:
        r = requests.get(
            f"{self._base}/getUpdates",
            params={"offset": self._offset, "timeout": 5, "limit": 10},
            timeout=self.API_TIMEOUT + 5,
        )
        if not r.ok:
            return []
        data = r.json()
        if not data.get("ok"):
            return []
        updates = data.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1
        return updates

    def _handle_update(self, update: dict):
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        # 인증: 허용된 Chat ID 에서 온 메시지만 처리
        chat_id_str = str(msg.get("chat", {}).get("id", ""))
        if chat_id_str != self.chat_id:
            logger.warning("[TelegramBot] 허용되지 않은 Chat ID: %s", chat_id_str)
            return

        text = (msg.get("text") or "").strip()
        cmd  = text.split()[0].lower() if text else ""

        dispatch = {
            "/start":     self._cmd_help,
            "/status":    self._cmd_status,
            "/balance":   self._cmd_balance,
            "/trades":    self._cmd_trades,
            "/auto":      self._cmd_auto,
            "/log":       self._cmd_log,
            "/pause":     self._cmd_pause,
            "/resume":    self._cmd_resume,
            "/trade":     self._cmd_trade,
            "/close":     self._cmd_close,
            "/closeall":  self._cmd_closeall,
            "/liquidate": self._cmd_closeall,
            "/buy":       self._cmd_buy,
            "/sell":      self._cmd_sell,
            "/help":      self._cmd_help,
        }
        # BotFather 에 등록된 봇명(@xxx) suffix 제거
        cmd_clean = cmd.split("@")[0]
        handler = dispatch.get(cmd_clean)
        if handler:
            handler(text)
        else:
            if text.startswith("/"):
                self.send_message(f"❓ 알 수 없는 명령입니다.\n/help 로 목록 확인")

    # ── 명령 핸들러 ──────────────────────────────────────────────────────────

    def _cmd_help(self, text: str = ""):
        self.send_message(
            "📖 <b>CTrade 명령어 목록</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "/status   — 엔진 상태 요약\n"
            "/balance  — 잔고 및 수익률\n"
            "/trades   — 최근 5건 체결\n"
            "/auto [종목] [on|off] — 종목별 자동매매 제어 (예: /auto KRW-BTC off)\n"
            "/log      — 최근 로그 20줄\n"
            "/pause    — 전역 매매 일시 정지 (포지션 유지)\n"
            "/resume   — 전역 매매 재개/시작\n"
            "/trade on|off — 전역 매매 켜기/끄기\n"
            "/close [종목] — 지정 종목 시장가 즉시 청산\n"
            "/closeall — 전 종목 일괄 긴급 청산\n"
            "/buy      — 수동 매수 (예: /buy KRW-BTC 6000)\n"
            "/sell     — 수동 매도 (예: /sell KRW-BTC)\n"
            "/help     — 이 도움말"
        )

    def _cmd_auto(self, text: str = ""):
        """/auto [symbol] [on|off]"""
        parts = text.strip().split()
        args = parts[1:]
        
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
        
        if not args:
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                targets = cfg.get("SCALPING_TARGETS", [])
                lines = [
                    "⚡ <b>[CTrade] 종목별 자동매매(AutoTrade) 현황</b>",
                    "━━━━━━━━━━━━━━━━━━━━"
                ]
                for t in targets:
                    sym = t.get("symbol", "?")
                    is_auto = t.get("auto_trade", True)
                    is_act = t.get("active", True)
                    strat = t.get("strategy_name", "default")
                    status_icon = "🟢 <b>ON</b>" if is_auto else "🔴 <b>OFF</b>"
                    if not is_act:
                        status_icon += " (비활성)"
                    lines.append(f"• <b>{sym}</b>: {status_icon} | <code>{strat}</code>")
                lines.append("")
                lines.append("💡 <i>변경 방법: <code>/auto [종목코드] [on|off]</code></i>")
                self.send_message("\n".join(lines))
            except Exception as e:
                self.send_message(f"⚠️ 설정 조회 실패: {e}")
            return

        target_sym = args[0].strip().upper()
        target_mode = None
        if len(args) > 1:
            m = args[1].lower().strip()
            if m in ("on", "1", "true", "start", "enable"):
                target_mode = True
            elif m in ("off", "0", "false", "stop", "disable"):
                target_mode = False
            else:
                self.send_message(f"❓ 모드는 <code>on</code> 또는 <code>off</code> 로 입력해 주세요. (예: <code>/auto {target_sym} off</code>)")
                return

        # 엔진 SharedState 큐에 명령 전송
        if self.shared and hasattr(self.shared, 'cmd_queue'):
            cmd_payload = {
                "type": "set_symbol_auto_trade" if target_mode is not None else "toggle_symbol_auto_trade",
                "symbol": target_sym
            }
            if target_mode is not None:
                cmd_payload["auto_trade"] = target_mode
            self.shared.cmd_queue.put(cmd_payload)

        # config 파일 직접 업데이트
        try:
            from utils.retry import atomic_write_json
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            found = False
            final_val = target_mode
            for t in cfg.get("SCALPING_TARGETS", []):
                if t.get("symbol") == target_sym:
                    if target_mode is not None:
                        t["auto_trade"] = bool(target_mode)
                    else:
                        t["auto_trade"] = not t.get("auto_trade", True)
                    final_val = t["auto_trade"]
                    found = True
                    break
            if not found:
                new_t = {
                    "symbol": target_sym,
                    "exchange": "UPBIT",
                    "active": True,
                    "auto_trade": bool(target_mode) if target_mode is not None else False
                }
                cfg.setdefault("SCALPING_TARGETS", []).append(new_t)
                final_val = new_t["auto_trade"]
            atomic_write_json(config_path, cfg)
            
            state_str = "🟢 <b>ON (자동매매 활성)</b>" if final_val else "🔴 <b>OFF (자동매매 비활성)</b>"
            notice = (
                f"⚡ <b>[CTrade] 종목 자동매매 설정 변경</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• 종목: <b>{target_sym}</b>\n"
                f"• 상태: {state_str}\n\n"
                f"💡 <i>{'신규 매수 진입이 허용됩니다.' if final_val else '신규 매수가 차단되며, 기존 보유 포지션 손익절/청산 감시는 유지됩니다.'}</i>"
            )
            self.send_message(notice)
        except Exception as e:
            self.send_message(f"⚠️ 설정 저장 실패: {e}")

    def _cmd_status(self, text: str = ""):
        uptime_sec = int(time.time() - self._start_time)
        h, rem = divmod(uptime_sec, 3600)
        m, s   = divmod(rem, 60)
        uptime_str = f"{h}시간 {m}분 {s}초"

        ctrl = {}
        if self.shared:
            try:
                ctrl = dict(self.shared.control)
            except Exception:
                pass

        trading = ctrl.get("trading_active", False)
        sim     = ctrl.get("simulation_mode", True)
        symbol  = ctrl.get("ui_focus_symbol", "N/A")
        mode_tag = "🔵 시뮬레이션" if sim else "🔴 실전매매"
        trade_tag = "✅ 매매 활성" if trading else "⏸ 매매 정지"

        # 잔고/수익 정보 (trade_history.json)
        balance_line = ""
        history_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "trade_history.json"
        )
        if os.path.exists(history_path):
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    hist = json.load(f)
                total_profit = sum(h.get("profit", 0) for h in hist if h.get("type") == "SELL")
                trade_count  = sum(1 for h in hist if h.get("type") == "SELL")
                balance_line = f"\n💰 누적 수익: {_fmt_krw(total_profit)} | 체결: {trade_count}건"
            except Exception:
                pass

        self.send_message(
            f"📊 <b>CTrade 상태</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔄 {mode_tag} | {trade_tag}\n"
            f"🎯 포커스: {symbol}\n"
            f"⏱ 업타임: {uptime_str}"
            f"{balance_line}\n"
            f"⏰ {_now_str()}"
        )

    def _cmd_balance(self, text: str = ""):
        # 현재 모드 확인
        ctrl = {}
        if self.shared:
            try:
                ctrl = dict(self.shared.control)
            except Exception:
                pass
        is_sim = ctrl.get("simulation_mode", True)

        if not is_sim:
            # ── 실전 모드: shared_state.__portfolio__ 에서 KIS + UPBIT 통합 잔고 및 전종목 현황 브리핑
            try:
                portfolio = {}
                if self.shared:
                    portfolio = dict(self.shared.data.get('__portfolio__', {}))

                summary = portfolio.get("summary", {})
                holdings = portfolio.get("holdings_all", [])
                
                u_tot = float(summary.get("upbit_total", 0.0))
                u_dep = float(summary.get("upbit_deposit", 0.0))
                u_day_pnl = float(summary.get("upbit_day_pnl", 0.0))
                u_day_roi = float(summary.get("upbit_day_roi", 0.0))

                k_tot = float(summary.get("kis_total", 0.0))
                k_dep = float(summary.get("kis_deposit", 0.0))
                k_day_pnl = float(summary.get("kis_day_pnl", 0.0))
                k_day_roi = float(summary.get("kis_day_roi", 0.0))

                comb_tot = float(summary.get("combined_total", u_tot + k_tot))
                comb_pnl = float(summary.get("combined_day_pnl", 0.0))
                comb_roi = float(summary.get("combined_day_roi", 0.0))
                comb_sign = "+" if comb_pnl >= 0 else ""
                pnl_emoji = "📈" if comb_pnl >= 0 else "📉"

                lines = [
                    "💰 <b>[CTrade] 실전 계좌 및 자산 현황</b>",
                    "━━━━━━━━━━━━━━━━",
                    f"💳 <b>통합 총자산</b>: <code>{_fmt_krw(comb_tot)}</code>",
                    f"{pnl_emoji} <b>통합 당일손익</b>: <b>{comb_sign}{_fmt_krw(comb_pnl)} ({comb_sign}{comb_roi:.2f}%)</b>",
                    "",
                    "🪙 <b>[1] UPBIT 코인 계좌</b>",
                    "━━━━━━━━━━━━━━━━",
                    f"• 계좌 총자산: <code>{_fmt_krw(u_tot)}</code> (KRW: {_fmt_krw(u_dep)})",
                    f"• 당일 성과: <b>{('+' if u_day_pnl>=0 else '')}{_fmt_krw(u_day_pnl)} ({('+' if u_day_roi>=0 else '')}{u_day_roi:.2f}%)</b>",
                ]

                u_items = portfolio.get("upbit_holdings", [])
                if u_items:
                    lines.append(f"• 보유 코인 ({len(u_items)}종목):")
                    for h in u_items:
                        pnl = float(h.get("profit_amount", 0.0))
                        p_rate = float(h.get("profit_rate", 0.0))
                        s_name = h.get("name") or h.get("symbol")
                        p_sign = "+" if pnl >= 0 else ""
                        tag = "🤖 [BOT]" if h.get("role") == "BOT" else "📦 [계좌보유]"
                        lines.append(f"  - {tag} <b>{s_name}</b>: {_fmt_qty(h.get('qty', 0))} (@{_fmt_krw(h.get('avg_price', 0))})\n    평가: {_fmt_krw(h.get('eval_amount', 0))} ({p_sign}{_fmt_krw(pnl)}, {p_sign}{p_rate:.2f}%)")
                else:
                    lines.append("• 보유 코인 없음 (100% KRW 예수금)")

                k_dom_items = portfolio.get("kis_domestic_holdings", portfolio.get("kis_holdings", []))
                k_ovrs_items = portfolio.get("kis_overseas_holdings", [])
                has_ovrs = summary.get("has_overseas_holdings", False) or len(k_ovrs_items) > 0

                lines.extend([
                    "",
                    "🇰🇷 <b>[2] KIS 국내주식 계좌</b>",
                    "━━━━━━━━━━━━━━━━",
                    f"• 계좌 총자산: <code>{_fmt_krw(k_tot if not has_ovrs else summary.get('kis_domestic_total', k_tot))}</code> (예수금: {_fmt_krw(k_dep)})",
                    f"• 당일 성과: <b>{('+' if k_day_pnl>=0 else '')}{_fmt_krw(k_day_pnl)} ({('+' if k_day_roi>=0 else '')}{k_day_roi:.2f}%)</b>",
                ])

                if k_dom_items:
                    lines.append(f"• 보유 국내주식 ({len(k_dom_items)}종목):")
                    for h in k_dom_items:
                        pnl = float(h.get("profit_amount", 0.0))
                        p_rate = float(h.get("profit_rate", 0.0))
                        s_name = h.get("name") or h.get("symbol")
                        p_sign = "+" if pnl >= 0 else ""
                        tag = "🤖 [BOT]" if h.get("role") == "BOT" else "📦 [계좌보유]"
                        lines.append(f"  - {tag} <b>{s_name}</b>: {_fmt_qty(h.get('qty', 0))} (@{_fmt_krw(h.get('avg_price', 0))})\n    평가: {_fmt_krw(h.get('eval_amount', 0))} ({p_sign}{_fmt_krw(pnl)}, {p_sign}{p_rate:.2f}%)")
                else:
                    lines.append("• 보유 국내주식 없음 (100% 현금 예수금)")

                # 해외주식 보유 시에만 스마트 브리핑 추가
                if has_ovrs:
                    ovrs_tot_usd = float(summary.get("kis_overseas_total_usd", 0.0))
                    ovrs_tot_krw = float(summary.get("kis_overseas_total_krw", 0.0))
                    fx = float(summary.get("usd_krw_fx", 1380.0))
                    lines.extend([
                        "",
                        "🇺🇸 <b>[3] KIS 해외주식 계좌</b>",
                        "━━━━━━━━━━━━━━━━",
                        f"• 외화 총자산: <code>${ovrs_tot_usd:,.2f}</code> (환산: {_fmt_krw(ovrs_tot_krw)} / @{fx:,.1f}원)",
                    ])
                    if k_ovrs_items:
                        lines.append(f"• 보유 해외종목 ({len(k_ovrs_items)}종목):")
                        for h in k_ovrs_items:
                            pnl = float(h.get("profit_amount", 0.0))
                            p_rate = float(h.get("profit_rate", 0.0))
                            s_name = h.get("name") or h.get("symbol")
                            p_sign = "+" if pnl >= 0 else ""
                            tag = "🤖 [BOT]" if h.get("role") == "BOT" else "📦 [계좌보유]"
                            eval_usd = float(h.get("eval_amount_usd", 0.0))
                            eval_krw = float(h.get("eval_amount", 0.0))
                            lines.append(f"  - {tag} <b>{s_name}</b>: {float(h.get('qty', 0)):,.0f}주 (@${float(h.get('avg_price', 0)):.2f})\n    평가: ${eval_usd:,.2f} ({_fmt_krw(eval_krw)}) ({p_sign}{_fmt_krw(pnl)}, {p_sign}{p_rate:.2f}%)")

                lines.append(f"\n⏰ {_now_str()}")
                self.send_message("\n".join(lines))

            except Exception as e:
                self.send_message(f"⚠️ 실전 잔고 조회 실패: {e}")
        else:
            # ── 시뮬레이션 모드: trade_history.json 에서 가상 잔고 및 통산 성과 조회
            history_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "trade_history.json"
            )
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    hist = json.load(f)

                sells = [h for h in hist if h.get("type") == "SELL"]
                buys  = [h for h in hist if h.get("type") == "BUY"]
                
                tot_trades   = len(sells)
                wins         = [s for s in sells if float(s.get("profit", 0.0)) > 0]
                losses       = [s for s in sells if float(s.get("profit", 0.0)) < 0]
                win_count    = len(wins)
                lose_count   = len(losses)
                win_rate     = (win_count / tot_trades * 100.0) if tot_trades else 0.0
                
                total_profit = sum(float(s.get("profit", 0.0)) for s in sells)
                total_gain   = sum(float(s.get("profit", 0.0)) for s in wins)
                total_loss   = abs(sum(float(s.get("profit", 0.0)) for s in losses))
                pf           = (total_gain / total_loss) if total_loss > 0 else (99.9 if total_gain > 0 else 0.0)
                avg_profit   = (total_profit / tot_trades) if tot_trades else 0.0
                summed_roi   = sum(float(s.get("profit_rate", 0.0)) for s in sells)
                total_invested = sum(float(b.get("amount", 0.0)) + float(b.get("fee", 0.0)) for b in buys)

                init_bal     = 10000000.0
                last_balance = hist[-1].get("balance", init_bal) if hist else init_bal
                account_roi  = (total_profit / init_bal * 100.0)

                # 당일 통계
                today_str    = datetime.now().strftime("%Y-%m-%d")
                today_sells  = []
                for s in sells:
                    ts = s.get("time", 0.0) or s.get("exit_time", 0.0) or s.get("entry_time", 0.0)
                    if ts > 0:
                        try:
                            if datetime.fromtimestamp(ts).strftime("%Y-%m-%d") == today_str:
                                today_sells.append(s)
                        except Exception:
                            pass
                today_profit = sum(float(s.get("profit", 0.0)) for s in today_sells)
                today_roi    = sum(float(s.get("profit_rate", 0.0)) for s in today_sells)
                today_account_roi = (today_profit / init_bal * 100.0)

                # 미청산 포지션 계산
                unclosed = {}
                for evt in hist:
                    evt_type = evt.get("type")
                    sym = evt.get("symbol")
                    if evt_type == "BUY":
                        unclosed[sym] = evt
                    elif evt_type == "SELL":
                        unclosed.pop(sym, None)

                pos_lines = []
                for sym, pos in unclosed.items():
                    s_name = _get_symbol_name(sym)
                    b_p = float(pos.get("price", 0.0))
                    qty = float(pos.get("qty", 0.0))
                    cost = float(pos.get("amount", b_p * qty)) + float(pos.get("fee", 0.0))
                    pos_lines.append(f"• 🤖 <b>{s_name}</b>: {_fmt_qty(qty)} (@{_fmt_krw(b_p)})\n    매입총액: <code>{_fmt_krw(cost)}</code>")
                pos_str = "\n".join(pos_lines) if pos_lines else "• 포지션 없음 (100% 현금 대기 중)"

                p_emoji = "📈" if total_profit >= 0 else "📉"
                p_sign  = "+" if total_profit >= 0 else ""
                t_emoji = "📈" if today_profit >= 0 else "📉"
                t_sign  = "+" if today_profit >= 0 else ""
                avg_sign = "+" if avg_profit >= 0 else ""

                self.send_message(
                    f"💰 <b>[CTrade] 잔고 및 누적 성과 (통산)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏦 <b>가상 운용 잔고</b>: <code>{_fmt_krw(last_balance)}</code> (시작: {_fmt_krw(init_bal)})\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 <b>수익률 및 손익 증감</b>:\n"
                    f"  • {p_emoji} <b>통산 실현손익</b>: <b>{p_sign}{_fmt_krw(total_profit)}</b> (계좌 ROI: <b>{_fmt_pct(account_roi)}</b>)\n"
                    f"  • 🔄 <b>회전 누적수익</b>: <b>{_fmt_pct(summed_roi)}</b> (총 매수누적: {_fmt_krw(total_invested)})\n"
                    f"  • {t_emoji} <b>당일 실현손익</b>: <b>{t_sign}{_fmt_krw(today_profit)} ({_fmt_pct(today_account_roi)})</b> (오늘 {len(today_sells)}건 청산)\n\n"
                    f"📊 <b>시행 통계</b>:\n"
                    f"  • 시행 횟수: <code>{tot_trades}건 청산</code> (매수 {len(buys)}건)\n"
                    f"  • 승/패 현황: <code>{win_count}승 {lose_count}패</code> (승률 <b>{win_rate:.1f}%</b>)\n"
                    f"  • 손익비(PF): <b>{pf:.2f}</b> | 건당 평균: <b>{avg_sign}{_fmt_krw(avg_profit)}</b>\n\n"
                    f"🤖 <b>현재 보유 포지션 ({len(unclosed)}건)</b>:\n{pos_str}\n\n"
                    f"⏰ <code>{_now_str()}</code>"
                )
            except FileNotFoundError:
                self.send_message("⚠️ trade_history.json 없음 (체결 내역 없음)")
            except Exception as e:
                self.send_message(f"⚠️ 잔고 조회 실패: {e}")

    def _cmd_trades(self, text: str = ""):
        history_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "trade_history.json"
        )
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                hist = json.load(f)

            limit = 5
            tokens = text.strip().split()
            if len(tokens) > 1 and tokens[1].isdigit():
                limit = min(max(1, int(tokens[1])), 20)

            recent = hist[-limit:] if len(hist) >= limit else hist
            recent = list(reversed(recent))  # 최신 순

            lines = [f"📋 <b>[CTrade] 최근 체결 내역 ({len(recent)}건)</b>\n━━━━━━━━━━━━━━━━━━━━"]
            for i, h in enumerate(recent, 1):
                t      = h.get("type", "?")
                sym    = h.get("symbol", "?")
                sym_name = _get_symbol_name(sym)
                qty    = float(h.get("qty", 0.0))
                fee    = float(h.get("fee", 0.0))
                ts     = h.get("time", 0.0)
                if ts <= 0:
                    ts = h.get("exit_time", 0.0) or h.get("entry_time", 0.0)
                ts_str = datetime.fromtimestamp(ts).strftime("%m/%d %H:%M") if ts else "?"

                if t == "BUY":
                    price  = float(h.get("price", 0.0))
                    total_cost = float(h.get("amount", price * qty)) + fee
                    lines.append(
                        f"<b>{i}. 🟢 BUY  {sym_name}</b>\n"
                        f"   • 매입단가: <code>{_fmt_krw(price)}</code> | 수량: <code>{_fmt_qty(qty)}</code>\n"
                        f"   • 실제구입액: <code>{_fmt_krw(total_cost)}</code> (수수료: <code>{fee:,.0f}원</code>)\n"
                        f"   • 체결시간: <code>{ts_str}</code>\n"
                    )
                elif t == "SELL":
                    buy_p  = float(h.get("buy_price", 0.0))
                    sell_p = float(h.get("sell_price", h.get("price", 0.0)))
                    profit = float(h.get("profit", 0.0))
                    profit_rate = float(h.get("profit_rate", 0.0))
                    invest_amt = float(h.get("amount", buy_p * qty))
                    holding_sec = float(h.get("holding_time", 0.0))
                    holding_str = _fmt_duration(holding_sec) if holding_sec > 0 else "즉시"
                    
                    emoji  = "📈" if profit >= 0 else "📉"
                    reason = h.get("reason", "SIGNAL")
                    lines.append(
                        f"<b>{i}. 🔴 SELL {sym_name}</b>\n"
                        f"   • 손익: {emoji} <b>{_fmt_krw(profit)} ({_fmt_pct(profit_rate)})</b> | 투자: <code>{_fmt_krw(invest_amt)}</code>\n"
                        f"   • 단가: <code>{_fmt_krw(buy_p)}</code> → <code>{_fmt_krw(sell_p)}</code>\n"
                        f"   • 수량: <code>{_fmt_qty(qty)}</code> | 수수료: <code>{fee:,.0f}원</code>\n"
                        f"   • 보유: <code>{holding_str}</code> | 사유: <code>{reason}</code> | <code>{ts_str}</code>\n"
                    )
                else:
                    lines.append(f"<b>{i}. ⚪ {t} {sym_name}</b> (시간: <code>{ts_str}</code>)\n")

            # 통산 성과 지표 계산 및 하단 요약 추가
            sells = [h for h in hist if h.get("type") == "SELL"]
            if sells:
                tot_trades = len(sells)
                wins       = [s for s in sells if float(s.get("profit", 0.0)) > 0]
                losses     = [s for s in sells if float(s.get("profit", 0.0)) < 0]
                win_count  = len(wins)
                win_rate   = (win_count / tot_trades * 100.0)
                tot_profit = sum(float(s.get("profit", 0.0)) for s in sells)
                tot_gain   = sum(float(s.get("profit", 0.0)) for s in wins)
                tot_loss   = abs(sum(float(s.get("profit", 0.0)) for s in losses))
                pf         = (tot_gain / tot_loss) if tot_loss > 0 else (99.9 if tot_gain > 0 else 0.0)
                avg_profit = (tot_profit / tot_trades)
                summed_roi = sum(float(s.get("profit_rate", 0.0)) for s in sells)
                avg_roi    = (summed_roi / tot_trades)
                
                p_sign   = "+" if tot_profit >= 0 else ""
                avg_sign = "+" if avg_profit >= 0 else ""
                lines.append(
                    f"📊 <b>통산 매매 통계 요약</b>:\n"
                    f"  • 총 시행: <code>{tot_trades}전 {win_count}승 {len(losses)}패</code> (승률 <b>{win_rate:.1f}%</b> | 손익비 <b>{pf:.2f}</b>)\n"
                    f"  • 평균 손익: <b>{avg_sign}{_fmt_krw(avg_profit)}</b> (평균 순수익률 <b>{_fmt_pct(avg_roi)}</b>)\n"
                    f"  • 통산 손익: <b>{p_sign}{_fmt_krw(tot_profit)}</b> (회전 누적 <b>{_fmt_pct(summed_roi)}</b>)\n"
                )

            lines.append(f"⏰ <code>{_now_str()}</code>")
            self.send_message("\n".join(lines))
        except FileNotFoundError:
            self.send_message("⚠️ 체결 내역 없음")
        except Exception as e:
            self.send_message(f"⚠️ 체결 조회 실패: {e}")

    def _cmd_log(self, text: str = ""):
        # 다중 디렉터리 경로 탐색 (실행 환경에 따른 경로 매핑)
        possible_paths = [
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trade_debug.log"), # CTrade/
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "trade_debug.log"), # CTradeSystem/
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vultr_logs", datetime.now().strftime("%Y%m%d"), "trade_debug.log"), # 수집된 Vultr 로그 경로
            "trade_debug.log"
        ]
        
        log_path = None
        for path in possible_paths:
            if os.path.exists(path):
                log_path = path
                break
                
        if not log_path:
            self.send_message("⚠️ trade_debug.log 파일을 찾을 수 없습니다.")
            return

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            last_lines = lines[-20:]
            log_text   = "".join(last_lines).strip()
            # 텔레그램 최대 4096자 제한 대응
            if len(log_text) > 3800:
                log_text = "...(생략)...\n" + log_text[-3600:]
            self.send_message(f"📄 <b>최근 로그 (20줄) - {os.basename(log_path)}</b>\n<pre>{log_text}</pre>")
        except Exception as e:
            self.send_message(f"⚠️ 로그 조회 실패: {e}")

    def _cmd_pause(self, text: str = ""):
        if self.shared:
            try:
                self.shared.update_control({"trading_active": False})
                try:
                    import json
                    from utils.retry import atomic_write_json
                    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    if "GLOBAL_STRATEGY" in config_data:
                        config_data["GLOBAL_STRATEGY"]["TRADING_ACTIVE"] = False
                    atomic_write_json(config_path, config_data)
                except Exception as ex:
                    logging.error(f"[TelegramBot] Failed to persist TRADING_ACTIVE=False: {ex}")
                self.send_message(
                    "⏸ <b>매매 일시 정지</b>\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "현재 포지션 유지, 신규 진입 차단\n"
                    "/resume 으로 재개"
                )
                return
            except Exception as e:
                self.send_message(f"⚠️ 정지 처리 실패: {e}")
                return
        self.send_message("⚠️ SharedState 연결 없음 — pause 불가")

    def _cmd_resume(self, text: str = ""):
        if self.shared:
            try:
                self.shared.update_control({"trading_active": True})
                try:
                    import json
                    from utils.retry import atomic_write_json
                    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    if "GLOBAL_STRATEGY" in config_data:
                        config_data["GLOBAL_STRATEGY"]["TRADING_ACTIVE"] = True
                    atomic_write_json(config_path, config_data)
                except Exception as ex:
                    logging.error(f"[TelegramBot] Failed to persist TRADING_ACTIVE=True: {ex}")
                self.send_message(
                    "▶️ <b>매매 재개</b>\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "신규 진입 신호 감지 재활성화"
                )
                return
            except Exception as e:
                self.send_message(f"⚠️ 재개 처리 실패: {e}")
                return
        self.send_message("⚠️ SharedState 연결 없음 — resume 불가")

    def _cmd_buy(self, text: str = ""):
        """수동 매수 명령어 처리 (/buy [symbol] [budget])"""
        tokens = text.split()
        if len(tokens) < 3:
            self.send_message(
                "❓ <b>수동 매수 명령어 사용법</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "포맷: <code>/buy [종목코드] [투자금액]</code>\n"
                "예시: <code>/buy KRW-BTC 6000</code>\n"
                "⚠️ 최소 주문 금액은 5,000원 이상이어야 합니다."
            )
            return

        try:
            symbol = tokens[1].upper()
            budget = float(tokens[2])
            if budget < 5000:
                self.send_message("⚠️ 최소 주문 금액은 5,000 KRW 이상이어야 합니다.")
                return

            if self.shared:
                self.shared.cmd_queue.put({"type": "manual_buy", "symbol": symbol, "budget": budget})
                self.send_message(
                    f"📩 <b>수동 매수 주문 접수 완료</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: <b>{symbol}</b>\n"
                    f"💼 예산: {_fmt_krw(budget)}\n"
                    f"⏱️ 엔진 처리 후 진행 결과를 알림해 드립니다."
                )
            else:
                self.send_message("⚠️ SharedState 연결이 유효하지 않습니다.")
        except ValueError:
            self.send_message("❌ 금액 형식이 잘못되었습니다. 숫자로 입력해 주세요.")
        except Exception as e:
            self.send_message(f"❌ 매수 명령 전달 중 오류 발생: {e}")

    def _cmd_sell(self, text: str = ""):
        """수동 매도 명령어 처리 (/sell [symbol])"""
        tokens = text.split()
        if len(tokens) < 2:
            self.send_message(
                "❓ <b>수동 매도 명령어 사용법</b>\n"
                "━━━━━━━━━━━━━━━━\n"
                "포맷: <code>/sell [종목코드]</code>\n"
                "예시: <code>/sell KRW-BTC</code>"
            )
            return

        try:
            symbol = tokens[1].upper()
            if self.shared:
                self.shared.cmd_queue.put({"type": "manual_sell", "symbol": symbol})
                self.send_message(
                    f"📩 <b>수동 매도 주문 접수 완료</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: <b>{symbol}</b>\n"
                    f"⏱️ 엔진 처리 후 진행 결과를 알림해 드립니다."
                )
            else:
                self.send_message("⚠️ SharedState 연결이 유효하지 않습니다.")
        except Exception as e:
            self.send_message(f"❌ 매도 명령 전달 중 오류 발생: {e}")

    def _cmd_close(self, text: str = ""):
        """지정 종목 수동 청산 (/close [symbol])"""
        self._cmd_sell(text)

    def _cmd_trade(self, text: str = ""):
        """매매 시작/중단 제어 (/trade on|off)"""
        tokens = text.split()
        if len(tokens) < 2:
            self.send_message(
                "📖 <b>매매 제어 명령어 사용법</b>\n"
                "• <code>/trade on</code> (또는 <code>/resume</code>) — 매매 시작\n"
                "• <code>/trade off</code> (또는 <code>/pause</code>) — 매매 일시정지"
            )
            return
        sub = tokens[1].lower()
        if sub in ["on", "start", "resume"]:
            self._cmd_resume(text)
        elif sub in ["off", "stop", "pause"]:
            self._cmd_pause(text)
        else:
            self.send_message("❓ <code>/trade on</code> 또는 <code>/trade off</code> 로 입력해 주세요.")

    def _cmd_closeall(self, text: str = ""):
        """전 종목 일괄 긴급 청산 (/closeall 또는 /liquidate)"""
        if self.shared:
            try:
                self.shared.cmd_queue.put({"type": "close_all"})
                self.send_message(
                    "🚨 <b>[전량 긴급 청산]</b>\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "보유 중인 모든 종목에 대해 시장가 청산 명령이 접수되었습니다."
                )
            except Exception as e:
                self.send_message(f"❌ 전량 청산 명령 전달 실패: {e}")
        else:
            self.send_message("⚠️ SharedState 연결이 유효하지 않습니다.")


