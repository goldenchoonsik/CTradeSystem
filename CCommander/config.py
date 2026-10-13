"""
CCommander - Configuration Loader
"""
import os
import json
import logging
from dotenv import load_dotenv

# .env 로드 (프로젝트 루트 및 CTrade/.env 탐색)
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_CURRENT_DIR)

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
load_dotenv(os.path.join(_PROJECT_ROOT, "CTrade", ".env"))

logger = logging.getLogger("CCommander")

class CommanderConfig:
    def __init__(self):
        self.project_root = _PROJECT_ROOT
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        
        # commander_config.json 로드
        cfg_path = os.path.join(_CURRENT_DIR, "commander_config.json")
        self.raw_config = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    self.raw_config = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load commander_config.json: {e}")

        self.primary_service = self.raw_config.get("PRIMARY_SERVICE", "ctrade")
        self.managed_services = self.raw_config.get("MANAGED_SERVICES", ["ctrade"])
        self.log_default_lines = self.raw_config.get("LOG_DEFAULT_LINES", 20)
        self.allow_reboot = self.raw_config.get("ALLOW_REBOOT", True)
        
        # KIS 토큰 파일 경로
        self.token_file = os.path.join(_PROJECT_ROOT, "CTrade", "config", "token_info.json")

    @property
    def is_valid(self) -> bool:
        return bool(self.bot_token and self.chat_id)

config = CommanderConfig()
