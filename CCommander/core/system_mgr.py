"""
CCommander - System Operations & Resource Manager
"""
import subprocess
import shutil
import time
import os
import psutil
from datetime import datetime

class SystemManager:
    """리눅스 systemd 서비스 제어 및 시스템 리소스 모니터링 관리자"""

    @staticmethod
    def run_systemctl(action: str, service: str = "ctrade") -> tuple[bool, str]:
        """systemctl start/stop/restart/status 실행 (안전한 서브프로세스)"""
        allowed_actions = ["start", "stop", "restart", "status", "is-active"]
        if action not in allowed_actions:
            return False, f"지원하지 않는 명령입니다: {action}"

        cmd = ["sudo", "systemctl", action, f"{service}.service"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            output = (res.stdout + res.stderr).strip()
            return res.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, "명령 실행 시간이 초과되었습니다 (Timeout)."
        except Exception as e:
            return False, f"명령 실행 실패: {e}"

    @staticmethod
    def is_service_active(service: str = "ctrade") -> bool:
        """서비스 가동 여부 확인"""
        cmd = ["systemctl", "is-active", f"{service}.service"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            return res.stdout.strip() == "active"
        except Exception:
            return False

    @staticmethod
    def get_service_logs(service: str = "ctrade", lines: int = 20) -> str:
        """journalctl 로부터 서비스의 최근 로그 가져오기"""
        lines = min(max(1, lines), 50)  # 최대 50줄 제한
        cmd = ["journalctl", "-u", f"{service}.service", "-n", str(lines), "--no-pager"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            logs = res.stdout.strip()
            if not logs:
                return f"{service} 서비스의 최근 로그가 없습니다."
            return logs
        except Exception as e:
            return f"로그 조회 실패: {e}"

    @staticmethod
    def get_system_metrics() -> dict:
        """서버 CPU, RAM, Disk, Uptime 리소스 현황 수집"""
        cpu_pct = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = shutil.disk_usage("/")
        
        boot_time = psutil.boot_time()
        uptime_sec = int(time.time() - boot_time)
        days, rem = divmod(uptime_sec, 86400)
        hours, rem = divmod(rem, 3600)
        mins, _ = divmod(rem, 60)
        
        uptime_str = f"{days}일 {hours}시간 {mins}분" if days > 0 else f"{hours}시간 {mins}분"

        return {
            "cpu_percent": cpu_pct,
            "ram_used_gb": mem.used / (1024**3),
            "ram_total_gb": mem.total / (1024**3),
            "ram_percent": mem.percent,
            "disk_used_gb": disk.used / (1024**3),
            "disk_total_gb": disk.total / (1024**3),
            "disk_percent": (disk.used / disk.total) * 100,
            "uptime_str": uptime_str,
            "now_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    @staticmethod
    def reboot_system() -> tuple[bool, str]:
        """서버 재부팅"""
        cmd = ["sudo", "reboot"]
        try:
            subprocess.Popen(cmd)
            return True, "서버 재부팅 명령을 전송했습니다. 약 1분 후 부팅 알림이 전송됩니다."
        except Exception as e:
            return False, f"재부팅 실패: {e}"
