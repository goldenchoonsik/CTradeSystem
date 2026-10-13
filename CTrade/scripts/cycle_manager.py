import os
import sys
import json
import time
import argparse
import subprocess
import signal
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "trading_config.json")
OPT_HISTORY_PATH = os.path.join(PROJECT_ROOT, "config", "optimization_history.json")

def parse_args():
    parser = argparse.ArgumentParser(description="CTrade HFT Engine Lifecycle & Rollback Manager")
    parser.add_argument("--action", type=str, choices=["start", "stop", "rollover"], default="start", help="Life-cycle action to execute")
    parser.add_argument("--duration", type=str, default="6h", help="Dynamic execution interval. e.g. 6h, 12h, 300s, 10s")
    return parser.parse_args()

def parse_duration_to_seconds(duration_str):
    try:
        if duration_str.endswith("h"):
            return int(duration_str[:-1]) * 3600
        elif duration_str.endswith("m"):
            return int(duration_str[:-1]) * 60
        elif duration_str.endswith("s"):
            return int(duration_str[:-1])
        else:
            return int(duration_str)
    except Exception as e:
        print(f"[CycleManager] Warning: Invalid duration format '{duration_str}'. Fallback to 6 hours. Error: {e}")
        return 21600

# ─── VERSION 관리 ────────────────────────────────────────────────────────────

def read_config() -> dict:
    """trading_config.json 을 파싱하여 dict 로 반환합니다. 실패 시 빈 dict 반환."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[CycleManager] Warning: Could not read trading_config.json: {e}")
        return {}

def get_current_version(cfg: dict = None) -> str:
    """GLOBAL_STRATEGY.VERSION 값을 반환합니다."""
    if cfg is None:
        cfg = read_config()
    return cfg.get("GLOBAL_STRATEGY", {}).get("VERSION", "v1.3.0_opt001")

def increment_version(current: str) -> str:
    """'v1.3.0_opt001' 형식의 VERSION 에서 opt 번호를 1 증가시킵니다."""
    try:
        base, opt_part = current.rsplit("_opt", 1)
        new_num = int(opt_part) + 1
        return f"{base}_opt{str(new_num).zfill(3)}"
    except Exception:
        return current + "_new"

def bump_version_in_config():
    """
    trading_config.json 의 VERSION 을 +1 증가시키고 파일을 저장합니다.
    반환값: (old_version, new_version)
    """
    cfg = read_config()
    old_version = get_current_version(cfg)
    new_version = increment_version(old_version)
    cfg.setdefault("GLOBAL_STRATEGY", {})["VERSION"] = new_version
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
        print(f"[CycleManager] VERSION bumped: {old_version} → {new_version}")
    except Exception as e:
        print(f"[CycleManager] Warning: Failed to write VERSION to config: {e}")
    return old_version, new_version

# ─── 최적화 이력 (optimization_history.json) 관리 ──────────────────────────

def load_opt_history() -> list:
    """config/optimization_history.json 를 읽어 이력 리스트를 반환합니다."""
    if not os.path.exists(OPT_HISTORY_PATH):
        return []
    try:
        with open(OPT_HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def append_opt_history(entry: dict):
    """최적화 이력 엔트리를 config/optimization_history.json 에 추가합니다."""
    history = load_opt_history()
    history.append(entry)
    try:
        with open(OPT_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=4, ensure_ascii=False)
        print(f"[CycleManager] Optimization history updated → {OPT_HISTORY_PATH}")
    except Exception as e:
        print(f"[CycleManager] Warning: Failed to write optimization history: {e}")

def record_rollover_event(old_version: str, new_version: str, status: str, note: str = ""):
    """롤오버 이벤트를 optimization_history.json 에 기록합니다."""
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    cfg = read_config()
    strategy_snapshot = cfg.get("GLOBAL_STRATEGY", {}).copy()
    # VERSION 필드는 이미 기록하므로 중복 제거
    strategy_snapshot.pop("VERSION", None)

    entry = {
        "timestamp_utc": now_utc,
        "from_version": old_version,
        "to_version": new_version,
        "status": status,        # "success" | "rollback"
        "note": note,
        "strategy_params": strategy_snapshot
    }
    append_opt_history(entry)

# ─── 엔진 프로세스 관리 ───────────────────────────────────────────────────────

def kill_running_engine():
    print("[CycleManager] Scanning for any running CTrade engines...")
    try:
        subprocess.run(["pkill", "-9", "-f", "main.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        print("[CycleManager] Running engines cleaned up successfully.")
    except Exception as e:
        print(f"[CycleManager] Error during engine cleanup: {e}")

def run_engine_background():
    main_path = os.path.join(PROJECT_ROOT, "main.py")
    venv_python = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
    if not os.path.exists(venv_python):
        venv_python = "python"  # Fallback

    print(f"[CycleManager] Spawning main.py in background using {venv_python}...")
    try:
        log_file = open(os.path.join(PROJECT_ROOT, "trade_debug.log"), "a")
        subprocess.Popen([venv_python, main_path], stdout=log_file, stderr=log_file, cwd=PROJECT_ROOT)
        print("[CycleManager] HFT Engine successfully spawned in background.")
    except Exception as e:
        print(f"[CycleManager] Critical: Failed to spawn engine: {e}")

def verify_compilation() -> bool:
    print("[CycleManager] Verifying project-wide python code compilation...")
    try:
        venv_python = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
        if not os.path.exists(venv_python):
            venv_python = "python"
        core_dir = os.path.join(PROJECT_ROOT, "core")
        res = subprocess.run([venv_python, "-m", "compileall", "-q", core_dir])
        if res.returncode == 0:
            print("[CycleManager] Compilation verification SUCCESS.")
            return True
        else:
            print("[CycleManager] Compilation verification FAILED with errors.")
            return False
    except Exception as e:
        print(f"[CycleManager] Error during compileall verification: {e}")
        return False

def force_git_rollback():
    print("[CycleManager] 🛡️ Triggering Atomic Rollback to last clean Git commit...")
    try:
        subprocess.run(["git", "checkout", "--", "core/scalping_target_radar.py"], check=True, cwd=PROJECT_ROOT)
        subprocess.run(["git", "checkout", "--", "config/trading_config.json"], check=True, cwd=PROJECT_ROOT)
        print("[CycleManager] Rollback complete. Safe files restored.")
    except Exception as e:
        print(f"[CycleManager] Critical: Git rollback failed: {e}. Attempting hard reset...")
        subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=PROJECT_ROOT)

# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    action = args.action
    duration_sec = parse_duration_to_seconds(args.duration)

    print(f"[CycleManager] Life-cycle action: {action.upper()} | Normalized Duration: {duration_sec} seconds")

    if action == "start":
        kill_running_engine()
        current_ver = get_current_version()
        print(f"[CycleManager] Starting engine with strategy version: {current_ver}")
        run_engine_background()

    elif action == "stop":
        kill_running_engine()

    elif action == "rollover":
        old_ver = get_current_version()

        # 1. 컴파일 체크
        if verify_compilation():
            print("[CycleManager] Safe state verified. Executing rollover...")
            # 2. VERSION 자동 인상 + 이력 기록 (성공)
            old_version, new_version = bump_version_in_config()
            record_rollover_event(
                old_version=old_version,
                new_version=new_version,
                status="success",
                note="Autonomous optimization rollover."
            )
            kill_running_engine()
            run_engine_background()
            print(f"[CycleManager] ✅ Rollover complete. Now running: {new_version}")
        else:
            # 3. 컴파일 실패 → 롤백 트랜잭션
            print("[CycleManager] 🚨 Syntax compilation failure detected! Initiating emergency rollback...")
            force_git_rollback()
            # 롤백 후 VERSION 인상 없이 이력에 rollback 기록
            current_ver = get_current_version()
            record_rollover_event(
                old_version=old_ver,
                new_version=old_ver,  # 버전 동일 (롤백)
                status="rollback",
                note="Compilation failure triggered atomic git rollback."
            )
            kill_running_engine()
            run_engine_background()
            print(f"[CycleManager] ⚠️ Rollback complete. Continuing with version: {old_ver}")
            sys.exit(1)

if __name__ == "__main__":
    main()
