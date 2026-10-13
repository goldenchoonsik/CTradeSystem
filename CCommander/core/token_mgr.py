"""
CCommander - KIS Token Manager
"""
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger("CCommander")

class TokenManager:
    """한국투자증권(KIS) OAuth 토큰 상태 점검 및 관리자"""

    @staticmethod
    def get_token_status(token_file_path: str) -> dict:
        """token_info.json 파일을 읽어 토큰 만료 시각 및 유효 상태 점검"""
        if not os.path.exists(token_file_path):
            return {
                "valid": False,
                "msg": "❌ token_info.json 파일이 존재하지 않습니다.",
                "expired_at": "N/A",
                "remaining_str": "0분"
            }

        try:
            with open(token_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            expired_at_str = data.get("access_token_token_expired", "")
            if not expired_at_str:
                return {
                    "valid": False,
                    "msg": "⚠️ 만료 시간 필드 누락",
                    "expired_at": "N/A",
                    "remaining_str": "알 수 없음"
                }

            # 포맷: 2026-08-25 19:35:38
            expired_time = datetime.strptime(expired_at_str, "%Y-%m-%d %H:%M:%S")
            now = datetime.now()

            diff_sec = (expired_time - now).total_seconds()
            if diff_sec > 0:
                h, rem = divmod(int(diff_sec), 3600)
                m, _ = divmod(rem, 60)
                rem_str = f"{h}시간 {m}분 남음" if h > 0 else f"{m}분 남음"
                return {
                    "valid": True,
                    "msg": "✅ 유효함 (정상)",
                    "expired_at": expired_at_str,
                    "remaining_str": rem_str
                }
            else:
                return {
                    "valid": False,
                    "msg": "❌ 만료됨 (재발급 필요)",
                    "expired_at": expired_at_str,
                    "remaining_str": "만료됨"
                }
        except Exception as e:
            return {
                "valid": False,
                "msg": f"⚠️ 파싱 오류: {e}",
                "expired_at": "N/A",
                "remaining_str": "오류"
            }
