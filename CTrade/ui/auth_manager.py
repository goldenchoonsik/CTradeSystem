"""
CTrade Web Dashboard - Telegram 2FA OTP & Security Manager
===========================================================
- 스마트폰 텔레그램 기반 6자리 일회용 인증번호(OTP) 발급 및 검증
- 24시간 HMAC 서명 세션 쿠키 발급 및 자동 로그인 유지
- 무차별 대입(Brute-force) 방어: 5회 실패 시 15분 IP 자동 차단 (HTTP 403)
- 도배 방지(Rate Limiting): IP당 1분 1회 OTP 발송 제한
- 3회 실패 시 스마트폰 비상 침입 경보 발송
"""
import os
import sys
import time
import hmac
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict

import requests

logger = logging.getLogger("WebAuthManager")

# 프로젝트 루트 및 CTrade .env 로드
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_CTRADE_DIR = os.path.dirname(_CURRENT_DIR)
_PROJECT_ROOT = os.path.dirname(_CTRADE_DIR)


def _load_env():
    """모든 경로의 .env 파일 로드"""
    env_paths = [
        os.path.join(_CTRADE_DIR, ".env"),
        os.path.join(_PROJECT_ROOT, ".env"),
        os.path.join("/root/projects/CTradeSystem/CTrade", ".env"),
        os.path.join("/root/projects/CTradeSystem", ".env"),
    ]
    for p in env_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass


_load_env()


class WebAuthManager:
    """웹 대시보드 텔레그램 OTP 2FA 보안 관리자 (싱글턴)"""

    OTP_EXPIRE_SECONDS = 180      # 3분 유효
    OTP_COOLDOWN_SECONDS = 60    # 1분 쿨다운 (도배 방지)
    MAX_FAILED_ATTEMPTS = 5      # 5회 실패 시 차단
    BLOCK_DURATION_SECONDS = 900 # 15분 차단
    SESSION_EXPIRE_HOURS = 24    # 24시간 세션 유지
    MAX_TRACKED_IPS = 1000       # 메모리 상한선 (1,000개 IP)

    def __init__(self):
        # 세션 서명용 시크릿 키 (부팅 시 안전 난수 생성)
        self.secret_key = os.getenv("WEB_SECRET_KEY", "").strip() or secrets.token_hex(32)
        
        # 인메모리 보안 추적 구조체
        self._ip_records: Dict[str, dict] = {}
        self._active_sessions: Dict[str, dict] = {}

        logger.info("[WebAuthManager] 2FA 보안 매니저 활성화 완료")

    def _get_telegram_creds(self) -> Tuple[str, str]:
        """항상 최신 환경변수에서 텔레그램 토큰/Chat ID 추출"""
        _load_env()
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = str(os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        return bot_token, chat_id

    def _get_client_ip(self, request) -> str:
        """프록시(Nginx/Cloudflare) 또는 직접 연결 IP 추출"""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
        return request.client.host if request.client else "127.0.0.1"

    def _cleanup_expired(self):
        """만료된 IP 기록 및 세션 자동 정리 (메모리 누수 방지)"""
        now = time.time()
        # IP 정리
        expired_ips = [
            ip for ip, data in self._ip_records.items()
            if now > data.get("blocked_until", 0) and now > data.get("otp_expires", 0) + 3600
        ]
        for ip in expired_ips:
            del self._ip_records[ip]

        # 세션 정리
        expired_sessions = [
            tok for tok, data in self._active_sessions.items()
            if now > data.get("expires_at", 0)
        ]
        for tok in expired_sessions:
            del self._active_sessions[tok]

        # 상한선 초과 시 가장 오래된 것 방출
        if len(self._ip_records) > self.MAX_TRACKED_IPS:
            oldest = sorted(self._ip_records.items(), key=lambda x: x[1].get("last_request_time", 0))[:200]
            for ip, _ in oldest:
                del self._ip_records[ip]

    def _send_telegram(self, text: str) -> bool:
        """텔레그램 메시지 발송"""
        bot_token, chat_id = self._get_telegram_creds()
        if not (bot_token and chat_id):
            logger.warning("[WebAuthManager] 텔레그램 토큰/Chat ID 누락으로 메시지 미발송")
            return False
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            res = requests.post(url, json=payload, timeout=5)
            if not res.ok:
                logger.error(f"[WebAuthManager] 텔레그램 API 오류 ({res.status_code}): {res.text}")
            return res.ok
        except Exception as e:
            logger.error(f"[WebAuthManager] 텔레그램 발송 실패: {e}")
            return False

    def is_local_ip(self, ip: str) -> bool:
        """로컬 개발 환경(Loopback IP) 여부 확인"""
        if not ip:
            return False
        clean_ip = ip.strip().lower()
        return clean_ip in ("127.0.0.1", "::1", "localhost", "testclient") or clean_ip.startswith("127.")

    def is_ip_blocked(self, ip: str) -> Tuple[bool, int]:
        """IP 차단 여부 및 남은 차단 시간(초) 확인"""
        self._cleanup_expired()
        rec = self._ip_records.get(ip)
        if not rec:
            return False, 0
        now = time.time()
        blocked_until = rec.get("blocked_until", 0)
        if now < blocked_until:
            rem_sec = int(blocked_until - now)
            return True, rem_sec
        return False, 0

    def request_otp(self, ip: str) -> Tuple[bool, str, int]:
        """6자리 OTP 생성 및 텔레그램 발송 (Rate Limiting 적용)
        Returns: (success: bool, message: str, cooldown_remaining: int)
        """
        self._cleanup_expired()
        now = time.time()
        
        # 1. 차단 확인
        is_blocked, rem_sec = self.is_ip_blocked(ip)
        if is_blocked:
            return False, f"🚨 비정상적인 접근으로 IP가 차단되었습니다. ({rem_sec}초 후 해제)", rem_sec

        rec = self._ip_records.setdefault(ip, {
            "otp": "",
            "otp_expires": 0,
            "last_request_time": 0,
            "failed_count": 0,
            "blocked_until": 0
        })

        # 2. 쿨다운 검사 (1분)
        time_since_last = now - rec.get("last_request_time", 0)
        if time_since_last < self.OTP_COOLDOWN_SECONDS and rec.get("otp"):
            wait_sec = int(self.OTP_COOLDOWN_SECONDS - time_since_last)
            return False, f"⏳ 너무 빠른 요청입니다. {wait_sec}초 후에 다시 시도하세요.", wait_sec

        # 3. 6자리 보안 난수 OTP 생성
        otp_code = f"{secrets.randbelow(1000000):06d}"
        rec["otp"] = otp_code
        rec["otp_expires"] = now + self.OTP_EXPIRE_SECONDS
        rec["last_request_time"] = now

        # 4. 텔레그램으로 즉시 발송
        expire_time_str = datetime.fromtimestamp(rec["otp_expires"]).strftime("%H:%M:%S")
        tg_msg = (
            f"🛡️ <b>[CTrade 보안 로그인] 일회용 인증번호</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 인증번호: <code><b>{otp_code}</b></code>\n\n"
            f"⏳ 유효시간: <b>3분</b> (~{expire_time_str}까지)\n"
            f"🌐 접속 IP: <code>{ip}</code>\n"
            f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
        )
        sent = self._send_telegram(tg_msg)
        if not sent:
            # 발송 실패 시 쿨다운 즉시 롤백
            rec["last_request_time"] = 0
            rec["otp"] = ""
            return False, "❌ 인증번호 메시지 발송에 실패했습니다. (설정을 확인하세요)", 0

        logger.info(f"[WebAuthManager] OTP 발송 완료 (IP: {ip}, 만료: {expire_time_str})")
        return True, "🔑 6자리 일회용 인증번호(OTP)가 발송되었습니다.", self.OTP_COOLDOWN_SECONDS

    def verify_otp(self, ip: str, input_code: str = "") -> Tuple[bool, str, Optional[str]]:
        """OTP 코드 검증 및 세션 토큰 발급 (로컬 PC 접속 시 무인증 원클릭 로그인 지원)
        Returns: (success: bool, message: str, session_token: Optional[str])
        """
        self._cleanup_expired()
        now = time.time()

        # 0. 로컬 개발 환경인 경우: OTP 코드 입력 없이 즉시 로그인 허용
        if self.is_local_ip(ip):
            session_token = self._create_session(ip)
            logger.info(f"[WebAuthManager] 💻 로컬 개발 환경 즉시 로그인 성공 (IP: {ip})")
            return True, "✅ 로컬 개발 모드 로그인 성공", session_token

        # 1. 차단 확인
        is_blocked, rem_sec = self.is_ip_blocked(ip)
        if is_blocked:
            return False, f"🚨 비정상적인 접근으로 IP가 차단되었습니다. ({rem_sec}초 후 해제)", None

        rec = self._ip_records.get(ip)
        if not rec or not rec.get("otp"):
            return False, "❌ 발급된 인증번호가 없습니다. [인증번호 발송]을 먼저 눌러주세요.", None

        # 2. 유효시간 확인
        if now > rec.get("otp_expires", 0):
            return False, "⚠️ 인증번호 유효시간(3분)이 만료되었습니다. 다시 발급받아 주세요.", None

        # 3. 일치 검증
        target_otp = rec.get("otp", "")
        if hmac.compare_digest(target_otp, input_code.strip()):
            # ✅ 인증 성공: 24시간 세션 토큰 발급
            session_token = self._create_session(ip)
            rec["failed_count"] = 0
            rec["otp"] = ""  # 1회용 즉시 폐기

            logger.info(f"[WebAuthManager] ✅ 로그인 성공 (IP: {ip})")
            return True, "✅ 로그인 성공", session_token
        else:
            # ❌ 인증 실패: 카운터 증가
            rec["failed_count"] = rec.get("failed_count", 0) + 1
            fails = rec["failed_count"]
            rem_attempts = self.MAX_FAILED_ATTEMPTS - fails

            # 3회 실패 시 텔레그램 경보
            if fails == 3:
                alert_msg = (
                    f"⚠️ <b>[보안 경보] 로그인 실패 3회 감지</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 의심 IP: <code>{ip}</code>\n"
                    f"⚠️ 2회 추가 실패 시 해당 IP는 15분간 차단됩니다.\n"
                    f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
                )
                self._send_telegram(alert_msg)

            # 5회 실패 시 15분 차단
            if fails >= self.MAX_FAILED_ATTEMPTS:
                rec["blocked_until"] = now + self.BLOCK_DURATION_SECONDS
                rec["otp"] = ""
                block_msg = (
                    f"🚨 <b>[침입 차단] 무차별 대입 공격 IP 잠금</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🚫 <b>차단 IP</b>: <code>{ip}</code>\n"
                    f"⏱️ <b>차단 기간</b>: 15분간 접속 거부\n"
                    f"💡 <i>오차단 해제: /unblock {ip}</i>\n"
                    f"⏰ <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
                )
                self._send_telegram(block_msg)
                logger.warning(f"[WebAuthManager] 🚨 IP 차단 실행: {ip}")
                return False, "🚨 5회 연속 인증 실패로 IP가 15분간 차단되었습니다.", None

            return False, f"❌ 인증번호가 일치하지 않습니다. (남은 기회: {rem_attempts}회)", None

    def _create_session(self, ip: str) -> str:
        """24시간 암호화 세션 토큰 생성"""
        now = time.time()
        expires_at = now + (self.SESSION_EXPIRE_HOURS * 3600)
        random_seed = secrets.token_hex(16)
        
        # payload = ip:expires_at:seed
        raw_data = f"{ip}:{expires_at}:{random_seed}"
        signature = hmac.new(self.secret_key.encode(), raw_data.encode(), hashlib.sha256).hexdigest()
        token = f"{raw_data}:{signature}"

        self._active_sessions[token] = {
            "ip": ip,
            "expires_at": expires_at
        }
        return token

    def verify_session(self, token: Optional[str]) -> bool:
        """세션 토큰 유효성 및 서명 검증"""
        if not token:
            return False

        now = time.time()
        try:
            parts = token.split(":")
            if len(parts) != 4:
                return False
            ip, expires_at_str, random_seed, sig = parts
            expires_at = float(expires_at_str)

            if now > expires_at:
                return False

            raw_data = f"{ip}:{expires_at}:{random_seed}"
            expected_sig = hmac.new(self.secret_key.encode(), raw_data.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected_sig):
                return False

            return True
        except Exception:
            return False

    def revoke_session(self, token: str):
        """로그아웃 시 세션 파기"""
        if token in self._active_sessions:
            del self._active_sessions[token]

    def get_security_status(self) -> dict:
        """텔레그램 /security 명령용 통계"""
        now = time.time()
        blocked_ips = [
            {"ip": ip, "remaining_sec": int(data.get("blocked_until", 0) - now)}
            for ip, data in self._ip_records.items()
            if now < data.get("blocked_until", 0)
        ]
        return {
            "active_sessions_count": len([s for s in self._active_sessions.values() if s.get("expires_at", 0) > now]),
            "blocked_ips": blocked_ips,
            "tracked_ips_count": len(self._ip_records)
        }

    def unblock_ip(self, ip: str) -> bool:
        """텔레그램 /unblock 명령용 IP 잠금 해제"""
        if ip in self._ip_records:
            self._ip_records[ip]["blocked_until"] = 0
            self._ip_records[ip]["failed_count"] = 0
            return True
        return False


# 싱글턴 인스턴스
auth_mgr = WebAuthManager()
