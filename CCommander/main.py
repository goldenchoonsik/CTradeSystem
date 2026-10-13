"""
CCommander - Main Telegram Daemon Entry Point
==============================================
스마트폰 텔레그램을 통한 Vultr 서버 및 CTrade 엔진 원격 관제 데몬
"""
import os
import sys
import time
import json
import logging
from datetime import datetime

import requests

# 프로젝트 루트 임포트 경로 확보
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

try:
    from CCommander.config import config
    from CCommander.core.system_mgr import SystemManager
    from CCommander.core.token_mgr import TokenManager
    from CCommander.core.trade_mgr import TradeManager
except ImportError:
    from config import config
    from core.system_mgr import SystemManager
    from core.token_mgr import TokenManager
    from core.trade_mgr import TradeManager

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [CCommander] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("CCommander")


class CommanderBot:
    """CCommander 텔레그램 관제 데몬"""

    POLL_INTERVAL = 2    # getUpdates 폴링 간격 (초)
    API_TIMEOUT   = 10   # HTTP 타임아웃 (초)

    def __init__(self):
        self.token = config.bot_token
        self.chat_id = str(config.chat_id).strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.offset = 0
        self.reboot_confirm_time = 0  # 15초 내 이중 확인용

        if not config.is_valid:
            logger.error("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 설정되지 않았습니다.")
            sys.exit(1)

        logger.info(f"CCommander 초기화 완료 (Chat ID: {self.chat_id})")

    def send_message(self, text: str) -> bool:
        """텔레그램 HTML 메시지 전송"""
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            res = requests.post(url, json=payload, timeout=self.API_TIMEOUT)
            return res.ok
        except Exception as e:
            logger.error(f"sendMessage 에러: {e}")
            return False

    def setup_commands(self):
        """텔레그램 앱의 '/' 메뉴 버튼 등록"""
        commands = [
            {"command": "status", "description": "📊 서버 및 CTrade 종합 현황"},
            {"command": "pnl", "description": "📊 기준점 이후 실시간 실현손익 및 수수료"},
            {"command": "balance", "description": "💰 CTrade 잔고 및 누적 수익률"},
            {"command": "trades", "description": "📋 최근 5건 체결 내역 및 통계"},
            {"command": "setbase", "description": "🔄 지금 시점으로 새 기준점 설정 (0 리셋)"},
            {"command": "auto", "description": "⚡ 종목별 자동매매 제어 (예: /auto KRW-BTC off)"},
            {"command": "pause", "description": "⏸️ 매매 일시 정지 (포지션 유지, 신규진입 차단)"},
            {"command": "resume", "description": "▶️ 매매 재개/시작 (신규진입 활성화)"},
            {"command": "close", "description": "🔴 지정 종목 시장가 수동 청산 (/close KRW-BTC)"},
            {"command": "closeall", "description": "🚨 전 종목 일괄 긴급 청산"},
            {"command": "start", "description": "🟢 CTrade 엔진 서비스 시작 (systemctl)"},
            {"command": "stop", "description": "🛑 CTrade 엔진 서비스 종료 (systemctl)"},
            {"command": "restart", "description": "🔄 CTrade 엔진 서비스 재기동"},
            {"command": "logs", "description": "📄 CTrade 최근 실시간 로그 20줄"},
            {"command": "resetbase", "description": "🔄 당일 기준선(시작잔고) 현재 잔고로 갱신"},
            {"command": "token", "description": "🔑 KIS 인증 토큰 상태 점검"},
            {"command": "security", "description": "🛡️ 2FA 웹 보안 및 차단 IP 조회"},
            {"command": "unblock", "description": "🔓 차단된 IP 잠금 해제 (예: /unblock 1.2.3.4)"},
            {"command": "reboot", "description": "⚡ Vultr 서버 원격 재부팅"},
            {"command": "help", "description": "📖 명령어 도움말"}
        ]
        try:
            requests.post(
                f"{self.base_url}/setMyCommands",
                json={"commands": commands},
                timeout=self.API_TIMEOUT
            )
            logger.info("텔레그램 메뉴(setMyCommands) 등록 완료")
        except Exception as e:
            logger.warning(f"setMyCommands 실패: {e}")

    def notify_boot(self):
        """부팅/기동 완료 시 폰으로 알림 전송"""
        metrics = SystemManager.get_system_metrics()
        c_active = SystemManager.is_service_active("ctrade")
        c_tag = "🟢 RUNNING" if c_active else "⚪ STOPPED"

        text = (
            f"🛡️ <b>[CCommander] 서버 기동 완료</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 <b>Vultr 관제 데몬이 활성화되었습니다.</b>\n\n"
            f"🖥️ <b>서버 Uptime</b>: {metrics['uptime_str']}\n"
            f"🧠 <b>메모리 점유</b>: {metrics['ram_percent']:.1f}% ({metrics['ram_used_gb']:.2f}G / {metrics['ram_total_gb']:.2f}G)\n"
            f"⚙️ <b>CTrade 엔진</b>: {c_tag}\n\n"
            f"💡 <i>명령어 확인: /help</i>\n"
            f"⏰ <code>{metrics['now_str']}</code>"
        )
        self.send_message(text)

    # ── 명령어 핸들러 ────────────────────────────────────────────────────────

    def handle_help(self, args: list):
        text = (
            f"📖 <b>CCommander 관제 명령어</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>/pnl</b> — 기준점 이후 실시간 순실현손익, 총수수료 및 ROI\n"
            f"💰 <b>/balance</b> — CTrade 잔고, 실현손익 및 보유 포지션\n"
            f"📋 <b>/trades</b> <code>[건수]</code> — 최근 매매 체결 상세 및 승률 통계\n"
            f"📊 <b>/status</b> — 서버 자원 및 CTrade 통합 현황\n"
            f"🔄 <b>/setbase</b> (또는 <code>/resetbase</code>, <code>/base</code>) — 지금 시점으로 새 기준점 설정 (0 리셋)\n\n"
            f"🎮 <b>[실전 매매 제어 및 청산]</b>\n"
            f"  • ⚡ <b>/auto</b> <code>[종목코드] [on|off]</code> — 종목별 자동매매 ON/OFF 제어\n"
            f"     (예: <code>/auto</code>, <code>/auto KRW-BTC off</code>, <code>/auto 069500 on</code>)\n"
            f"  • ⏸️ <b>/pause</b> (또는 <code>/trade off</code>) — 전역 매매 일시정지 (포지션 유지, 진입 차단)\n"
            f"  • ▶️ <b>/resume</b> (또는 <code>/trade on</code>) — 전역 매매 재개 (신규 진입 활성화)\n"
            f"  • 🔴 <b>/close</b> <code>[종목코드]</code> — 지정 종목 즉시 청산 (예: <code>/close KRW-BTC</code>)\n"
            f"  • 🚨 <b>/closeall</b> (또는 <code>/liquidate</code>) — 보유 전 종목 일괄 긴급 청산\n\n"
            f"⚙️ <b>[서비스 관리]</b>\n"
            f"  • 🟢 <b>/start</b> <code>[서비스]</code> — CTrade 또는 서비스 프로세스 시작\n"
            f"  • 🛑 <b>/stop</b> <code>[서비스]</code> — CTrade 또는 서비스 프로세스 종료\n"
            f"  • 🔄 <b>/restart</b> <code>[서비스]</code> — CTrade 또는 서비스 프로세스 재기동\n"
            f"  • 📄 <b>/logs</b> <code>[줄수]</code> — CTrade 최근 저널 로그\n\n"
            f"🔐 <b>[시스템/보안]</b>\n"
            f"  • 🔑 <b>/token</b> — KIS 인증 토큰 유효 상태 점검\n"
            f"  • 🛡️ <b>/security</b> — 2FA 보안 현황 및 차단 IP 목록\n"
            f"  • 🔓 <b>/unblock</b> <code>[IP]</code> — 차단 IP 잠금 해제\n"
            f"  • ⚡ <b>/reboot</b> — Vultr 서버 재부팅\n"
            f"  • 📖 <b>/help</b> — 이 도움말"
        )
        self.send_message(text)

    def handle_pnl(self, args: list):
        """/pnl - 특정 기준점(마지막 /setbase 시각) 이후 실시간 실현 성과 리포트"""
        report = TradeManager.get_pnl_report()
        self.send_message(report)

    def handle_balance(self, args: list):
        """/balance - CTrade 잔고 및 누적 수익률 리포트"""
        report = TradeManager.get_balance_report()
        self.send_message(report)

    def handle_trades(self, args: list):
        """/trades [limit] - 최근 체결 내역 리포트"""
        limit = 5
        if args and args[0].isdigit():
            limit = min(max(1, int(args[0])), 20)
        report = TradeManager.get_trades_report(limit=limit)
        self.send_message(report)

    def handle_status(self, args: list):
        metrics = SystemManager.get_system_metrics()
        
        # 관리 서비스 상태
        svc_lines = []
        for svc in config.managed_services:
            active = SystemManager.is_service_active(svc)
            tag = "🟢 <b>ACTIVE (가동 중)</b>" if active else "⚪ <b>INACTIVE (정지됨)</b>"
            svc_lines.append(f"  • {svc}: {tag}")
        svc_str = "\n".join(svc_lines)

        # 트레이딩 요약 정보
        trade_summary = TradeManager.get_quick_summary()

        # 토큰 상태
        tok = TokenManager.get_token_status(config.token_file)

        text = (
            f"📊 <b>[CCommander] 종합 관제 현황</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥️ <b>서버 자원</b>:\n"
            f"  • CPU: <code>{metrics['cpu_percent']:.1f}%</code>\n"
            f"  • RAM: <code>{metrics['ram_percent']:.1f}%</code> ({metrics['ram_used_gb']:.2f}G / {metrics['ram_total_gb']:.2f}G)\n"
            f"  • Disk: <code>{metrics['disk_percent']:.1f}%</code> ({metrics['disk_used_gb']:.1f}G / {metrics['disk_total_gb']:.1f}G)\n"
            f"  • Uptime: <code>{metrics['uptime_str']}</code>\n\n"
            f"⚙️ <b>서비스 상태</b>:\n{svc_str}\n\n"
            f"📈 <b>CTrade 트레이딩 요약</b>:\n{trade_summary}\n\n"
            f"🔑 <b>KIS 토큰</b>: {tok['msg']}\n"
            f"  • 잔여: <code>{tok['remaining_str']}</code> (만료: {tok['expired_at']})\n\n"
            f"⏰ <code>{metrics['now_str']}</code>"
        )
        self.send_message(text)

    def handle_start(self, args: list):
        target = args[0] if args else config.primary_service
        self.send_message(f"⏳ <b>[{target}] 서비스 시작 중...</b>")
        
        ok, out = SystemManager.run_systemctl("start", target)
        time.sleep(1.5)
        active = SystemManager.is_service_active(target)

        if active:
            self.send_message(
                f"🛡️ <b>[CCommander] 서비스 시작 성공</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>{target}</b> 서비스가 정상 가동되었습니다.\n"
                f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
        else:
            self.send_message(
                f"🚨 <b>[CCommander] 서비스 시작 실패</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"❌ <b>{target}</b> 시작 실패:\n<code>{out or '알 수 없는 오류'}</code>"
            )

    def handle_stop(self, args: list):
        target = args[0] if args else config.primary_service
        self.send_message(f"⏳ <b>[{target}] 서비스 종료 중...</b>")
        
        ok, out = SystemManager.run_systemctl("stop", target)
        time.sleep(1.0)
        active = SystemManager.is_service_active(target)

        if not active:
            self.send_message(
                f"🛡️ <b>[CCommander] 서비스 종료 완료</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🛑 <b>{target}</b> 서비스가 안전하게 종료되었습니다.\n"
                f"💡 <i>(로컬 PC 테스트를 위해 KIS 세션이 해제되었습니다.)</i>\n"
                f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
        else:
            self.send_message(
                f"⚠️ <b>[CCommander] 서비스 종료 실패</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"❌ <b>{target}</b> 프로세스가 아직 실행 중입니다.\n<code>{out}</code>"
            )

    def handle_restart(self, args: list):
        target = args[0] if args else config.primary_service
        self.send_message(f"⏳ <b>[{target}] 서비스 재기동 중...</b>")
        
        ok, out = SystemManager.run_systemctl("restart", target)
        time.sleep(2.0)
        active = SystemManager.is_service_active(target)

        if active:
            self.send_message(
                f"🛡️ <b>[CCommander] 서비스 재기동 완료</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔄 <b>{target}</b> 서비스가 성공적으로 재시작되었습니다.\n"
                f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
        else:
            self.send_message(
                f"🚨 <b>[CCommander] 서비스 재기동 실패</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"❌ <code>{out}</code>"
            )

    def handle_logs(self, args: list):
        lines = config.log_default_lines
        target = config.primary_service

        if args:
            if args[0].isdigit():
                lines = int(args[0])
            else:
                target = args[0]
                if len(args) > 1 and args[1].isdigit():
                    lines = int(args[1])

        logs = SystemManager.get_service_logs(target, lines=lines)
        
        # 텔레그램 메시지 길이 제한(4096자) 대응
        if len(logs) > 3500:
            logs = logs[-3500:]

        text = (
            f"📄 <b>[{target}] 실시간 저널 로그 (최근 {lines}줄)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<pre>{logs}</pre>"
        )
        self.send_message(text)

    def handle_token(self, args: list):
        tok = TokenManager.get_token_status(config.token_file)
        text = (
            f"🔑 <b>[CCommander] KIS OAuth 토큰 현황</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>상태</b>: {tok['msg']}\n"
            f"⏰ <b>만료 시각</b>: <code>{tok['expired_at']}</code>\n"
            f"⏳ <b>남은 시간</b>: <code>{tok['remaining_str']}</code>\n"
            f"📂 <b>파일 경로</b>: <code>{config.token_file}</code>"
        )
        self.send_message(text)

    def handle_reboot(self, args: list):
        now = time.time()
        # 15초 이내 재호출 시 실제 재부팅 수행 (안전장치)
        if now - self.reboot_confirm_time < 15:
            self.send_message("⚡ <b>[CCommander] Vultr 서버 재부팅을 시작합니다...</b>")
            time.sleep(1)
            SystemManager.reboot_system()
        else:
            self.reboot_confirm_time = now
            text = (
                f"⚠️ <b>[경고] Vultr 서버 원격 재부팅 확인</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"정말 서버를 재부팅하시겠습니까?\n"
                f"실행하려면 <b>15초 이내에 다시 <code>/reboot</code>를 전송</b>하세요."
            )
            self.send_message(text)

    def handle_security(self, args: list):
        """웹 대시보드 2FA 보안 현황 및 차단된 IP 목록 조회"""
        try:
            from ui.auth_manager import auth_mgr
            sec = auth_mgr.get_security_status()
            
            blocked_list = sec.get("blocked_ips", [])
            if blocked_list:
                b_lines = [f"• <code>{b['ip']}</code> (남은 시간: {b['remaining_sec']}초)" for b in blocked_list]
                b_str = "\n".join(b_lines)
            else:
                b_str = "✅ 현재 차단된 IP가 없습니다."

            text = (
                f"🛡️ <b>[CCommander] 2FA 웹 보안 현황</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔑 <b>활성 세션 수</b>: {sec.get('active_sessions_count', 0)}개\n"
                f"📊 <b>추적 중인 IP</b>: {sec.get('tracked_ips_count', 0)}개\n\n"
                f"🚫 <b>차단된 공격 IP 목록</b>:\n{b_str}\n\n"
                f"💡 <i>차단 해제: <code>/unblock [IP]</code></i>\n"
                f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
            self.send_message(text)
        except Exception as e:
            self.send_message(f"⚠️ 보안 현황 조회 오류: {e}")

    def handle_unblock(self, args: list):
        """특정 IP 차단 해제"""
        if not args:
            self.send_message("사용법: <code>/unblock [해제할 IP]</code>\n예: <code>/unblock 211.123.45.67</code>")
            return
        target_ip = args[0].strip()
        try:
            from ui.auth_manager import auth_mgr
            ok = auth_mgr.unblock_ip(target_ip)
            if ok:
                self.send_message(f"🔓 <b>[CCommander] IP 잠금 해제 완료</b>\n• IP: <code>{target_ip}</code>\n정상 접속이 가능합니다.")
            else:
                self.send_message(f"ℹ️ <code>{target_ip}</code> 는 차단 목록에 없습니다.")
        except Exception as e:
            self.send_message(f"⚠️ 차단 해제 실패: {e}")

    def handle_auto(self, args: list):
        """/auto [symbol] [on|off] - 종목별 자동매매 ON/OFF 제어 및 현황 조회"""
        if not args:
            # 인자 없음: 전체 종목의 자동매매 상태 목록 리포트
            targets = TradeManager.get_targets_auto_trade_status()
            if not targets:
                self.send_message("ℹ️ 등록된 매매 대상 종목이 없습니다.")
                return

            lines = [
                "⚡ <b>[CTrade] 종목별 자동매매(AutoTrade) 현황</b>",
                "━━━━━━━━━━━━━━━━━━━━"
            ]
            for t in targets:
                sym = t.get("symbol", "?")
                sym_name = TradeManager.get_symbol_display_name(sym)
                is_auto = t.get("auto_trade", True)
                is_act = t.get("active", True)
                strat = t.get("strategy_name", "default")
                
                status_icon = "🟢 <b>ON</b>" if is_auto else "🔴 <b>OFF</b>"
                if not is_act:
                    status_icon += " (비활성)"
                    
                lines.append(f"• <b>{sym_name}</b>: {status_icon} | <code>{strat}</code>")

            lines.append("")
            lines.append("💡 <i>변경 방법: <code>/auto [종목코드] [on|off]</code></i>")
            lines.append("<i>예시: <code>/auto KRW-BTC off</code> 또는 <code>/auto 069500 on</code></i>")
            lines.append(f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>")
            self.send_message("\n".join(lines))
            return

        target_sym = args[0].strip().upper()
        target_mode = None
        if len(args) > 1:
            mode_str = args[1].lower().strip()
            if mode_str in ("on", "1", "true", "start", "enable"):
                target_mode = True
            elif mode_str in ("off", "0", "false", "stop", "disable"):
                target_mode = False
            else:
                self.send_message(f"❓ 모드는 <code>on</code> 또는 <code>off</code> 로 입력해 주세요. (예: <code>/auto {target_sym} off</code>)")
                return

        ok, msg, final_val = TradeManager.set_symbol_auto_trade(target_sym, target_mode)
        if ok:
            sym_name = TradeManager.get_symbol_display_name(target_sym)
            state_tag = "🟢 <b>ON (자동매매 활성)</b>" if final_val else "🔴 <b>OFF (자동매매 비활성)</b>"
            notice = (
                f"⚡ <b>[CTrade] 종목 자동매매 설정 변경</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• 종목: <b>{sym_name}</b>\n"
                f"• 상태: {state_tag}\n\n"
                f"💡 <i>{'신규 매수 진입이 허용됩니다.' if final_val else '신규 매수가 차단되며, 기존 보유 포지션 손익절/청산 감시는 유지됩니다.'}</i>\n"
                f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
            self.send_message(notice)
        else:
            self.send_message(f"❌ <b>[{target_sym}] 자동매매 설정 실패</b>:\n<code>{msg}</code>")

    def handle_pause(self, args: list):
        """/pause 또는 /trade off - 매매 일시 정지"""
        ok, msg = TradeManager.set_trading_active(False)
        if ok:
            self.send_message(
                f"⏸️ <b>[CTrade] 매매 일시 정지 완료</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• 현재 포지션 감시 및 유지는 계속됩니다.\n"
                f"• 신규 매수 진입 신호가 차단되었습니다.\n"
                f"💡 <i>매매 재개: /resume 또는 /trade on</i>\n"
                f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
        else:
            self.send_message(f"⚠️ 매매 일시정지 처리 실패: {msg}")

    def handle_resume(self, args: list):
        """/resume 또는 /trade on - 매매 재개"""
        ok, msg = TradeManager.set_trading_active(True)
        if ok:
            self.send_message(
                f"▶️ <b>[CTrade] 매매 재개/시작 완료</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• 신규 매수 진입 신호 감지가 활성화되었습니다.\n"
                f"💡 <i>매매 일시정지: /pause 또는 /trade off</i>\n"
                f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
        else:
            self.send_message(f"⚠️ 매매 재개 처리 실패: {msg}")

    def handle_trade(self, args: list):
        """/trade [on|off|status] - 매매 시작/중단 제어"""
        if not args:
            self.send_message(
                "📖 <b>매매 제어 명령어 사용법</b>\n"
                "• <code>/trade on</code> (또는 <code>/resume</code>) — 매매 시작\n"
                "• <code>/trade off</code> (또는 <code>/pause</code>) — 매매 일시정지"
            )
            return
        sub = args[0].lower()
        if sub in ["on", "start", "resume"]:
            self.handle_resume(args[1:])
        elif sub in ["off", "stop", "pause"]:
            self.handle_pause(args[1:])
        else:
            self.send_message("❓ <code>/trade on</code> 또는 <code>/trade off</code> 로 입력해 주세요.")

    def handle_close(self, args: list):
        """/close [symbol] - 지정 종목 시장가 즉시 청산"""
        if not args:
            self.send_message(
                "❓ <b>수동 청산 명령어 사용법</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "포맷: <code>/close [종목코드]</code>\n"
                "예시:\n"
                "• <code>/close KRW-BTC</code>\n"
                "• <code>/close 069500</code>"
            )
            return
        sym = args[0].strip().upper()
        self.send_message(f"⏳ <b>[{sym}] 시장가 수동 청산 요청 중...</b>")
        ok, msg = TradeManager.close_position(sym)
        if ok:
            self.send_message(
                f"🔴 <b>[CTrade] 수동 청산 주문 접수 완료</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• 종목: <b>{sym}</b>\n"
                f"• 상태: <code>{msg}</code>\n"
                f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
        else:
            self.send_message(f"❌ <b>[{sym}] 청산 실패</b>:\n<code>{msg}</code>")

    def handle_close_all(self, args: list):
        """/closeall 또는 /liquidate - 전 종목 일괄 긴급 청산"""
        self.send_message("🚨 <b>[CTrade] 전 종목 일괄 긴급 청산 요청 중...</b>")
        ok, msg = TradeManager.close_all_positions()
        if ok:
            self.send_message(
                f"🚨 <b>[CTrade] 전 종목 긴급 청산 주문 접수 완료</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• 모든 활성 포지션에 대해 시장가 매도 명령이 전송되었습니다.\n"
                f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
        else:
            self.send_message(f"❌ <b>전량 청산 실패</b>:\n<code>{msg}</code>")

    def handle_reset_baseline(self, args: list):
        """/resetbase 또는 /base - 기준선(account_baseline.json) 현재 잔고로 갱신"""
        broker = args[0].strip().upper() if args else "ALL"
        note = f"CCommander 텔레그램 수동 재설정 ({datetime.now().strftime('%m/%d %H:%M')})"
        self.send_message(f"⏳ <b>[{broker}] 기준선 재설정 요청 중...</b>")
        ok, msg = TradeManager.reset_account_baseline(broker=broker, note=note)
        if ok:
            # 갱신 후 즉시 새 잔고 리포트 전달
            balance_report = TradeManager.get_balance_report()
            self.send_message(
                f"✅ <b>[CTrade] 기준선 갱신 완료</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• {msg}\n\n"
                f"{balance_report}"
            )
        else:
            self.send_message(f"❌ <b>기준선 갱신 실패</b>:\n<code>{msg}</code>")

    # ── 폴링 루프 ────────────────────────────────────────────────────────────

    def dispatch(self, text: str):
        parts = text.strip().split()
        if not parts:
            return
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]

        handlers = {
            "/start": self.handle_start,
            "/stop": self.handle_stop,
            "/restart": self.handle_restart,
            "/status": self.handle_status,
            "/pnl": self.handle_pnl,
            "/balance": self.handle_balance,
            "/trades": self.handle_trades,
            "/auto": self.handle_auto,
            "/pause": self.handle_pause,
            "/resume": self.handle_resume,
            "/trade": self.handle_trade,
            "/close": self.handle_close,
            "/closeall": self.handle_close_all,
            "/liquidate": self.handle_close_all,
            "/resetbase": self.handle_reset_baseline,
            "/setbase": self.handle_reset_baseline,
            "/base": self.handle_reset_baseline,
            "/logs": self.handle_logs,
            "/token": self.handle_token,
            "/security": self.handle_security,
            "/unblock": self.handle_unblock,
            "/reboot": self.handle_reboot,
            "/help": self.handle_help,
        }

        handler = handlers.get(cmd)
        if handler:
            handler(args)
        else:
            self.send_message("❓ 알 수 없는 명령어입니다. <code>/help</code> 로 확인하세요.")

    def run(self):
        """메인 폴링 루프 실행"""
        self.setup_commands()
        self.notify_boot()

        logger.info("CCommander 폴링 루프 시작...")

        while True:
            try:
                url = f"{self.base_url}/getUpdates"
                params = {"offset": self.offset, "timeout": 10, "limit": 10}
                res = requests.get(url, params=params, timeout=self.API_TIMEOUT + 5)
                
                if res.ok:
                    data = res.json()
                    if data.get("ok"):
                        for upd in data.get("result", []):
                            self.offset = upd["update_id"] + 1
                            msg = upd.get("message") or upd.get("edited_message")
                            if not msg:
                                continue
                            
                            # 보안: 허용된 Chat ID 만 수락
                            sender_id = str(msg.get("chat", {}).get("id", ""))
                            if sender_id != self.chat_id:
                                logger.warning(f"비인가 Chat ID 차단: {sender_id}")
                                continue

                            text = msg.get("text", "")
                            if text:
                                logger.info(f"명령어 수신: {text}")
                                self.dispatch(text)
            except Exception as e:
                logger.warning(f"폴링 예외 발생: {e}")

            time.sleep(self.POLL_INTERVAL)


if __name__ == "__main__":
    bot = CommanderBot()
    bot.run()
