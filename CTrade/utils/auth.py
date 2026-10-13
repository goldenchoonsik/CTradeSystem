import requests
import json
import time
import logging
import os
from datetime import datetime, timedelta
from typing import Dict

class KISAuthManager:
    def __init__(self, app_key: str, app_secret: str, url_base: str, acc_no: str = "", acc_code: str = ""):
        self.app_key = app_key
        self.app_secret = app_secret
        self.url_base = url_base
        self.access_token = ""
        self.approval_key = ""
        self.token_expiry = 0
        self.acc_no = acc_no
        self.acc_code = acc_code
        self.token_file = os.path.join(os.path.dirname(__file__), "..", "config", "token_info.json")

    def get_base_headers(self):
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

    async def get_access_token(self):
        """OAuth 토큰 발급 및 로컬 저장 (async 무중단 지원)
        
        동기 requests는 이벤트 루프를 블로킹하므로 asyncio.to_thread로 오프로드합니다.
        """
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, 'r') as f:
                    token_data = json.load(f)
                file_time = datetime.fromtimestamp(os.path.getmtime(self.token_file))
                if datetime.now() < file_time + timedelta(hours=20):
                    self.access_token = token_data['access_token']
                    logging.info("[Auth] KIS Token loaded from token_info.json")
                    return self.access_token
            except Exception as e:
                logging.warning(f"[Auth] Failed to load token file: {e}")

        # 파일이 없거나 만료된 경우 새로 발급
        headers = {"content-type": "application/json"}
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        
        try:
            import asyncio
            # 동기 requests 호출을 스레드로 오프로드하여 이벤트 루프 블로킹 방지
            res = await asyncio.to_thread(
                requests.post,
                f"{self.url_base}/oauth2/tokenP",
                headers=headers,
                data=json.dumps(payload)
            )
            if res.status_code == 200:
                data = res.json()
                self.access_token = data['access_token']
                with open(self.token_file, 'w') as f:
                    json.dump(data, f)
                logging.info("[Auth] KIS Token issued and saved to token_info.json")
                return self.access_token
            else:
                logging.error(f"[Auth] KIS Token Error: {res.status_code} - {res.text}")
                return ""
        except Exception as e:
            logging.error(f"[Auth] KIS Token Exception: {e}")
            return ""

    async def get_approval_key(self):
        """웹소켓 인증용 키 발급 (async 무중단 지원)"""
        url = f"{self.url_base}/oauth2/Approval"
        headers = {"content-type": "application/json"}
        payload = {
            "grant_type": "client_credentials", 
            "appkey": self.app_key, 
            "secretkey": self.app_secret
        }
        
        try:
            import asyncio
            # 동기 requests 호출을 스레드로 오프로드하여 이벤트 루프 블로킹 방지
            res = await asyncio.to_thread(
                requests.post,
                url,
                headers=headers,
                data=json.dumps(payload)
            )
            if res.status_code == 200:
                self.approval_key = res.json().get("approval_key")
                return self.approval_key
            else:
                logging.error(f"[Auth] KIS Approval Error: {res.status_code} - {res.text}")
                return ""
        except Exception as e:
            logging.error(f"[Auth] KIS Approval Exception: {e}")
            return ""