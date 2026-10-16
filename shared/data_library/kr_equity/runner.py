"""kr_equity 단계를 대시보드에서 실행 — 한 번에 하나(collect/.running.json 잠금), 출력은 logs/<step>_<시각>.out.

Linux 단계: <이 venv 의 python> -m shared.data_library.kr_equity.cli <step>
Creon 단계: Windows `py -3.10-32 CAiModelBaker/core/universe_driver.py <step> --stage kr_equity ...`
            (WSL 에선 py.exe 직접, 없으면 PowerShell 브릿지 — 기존 Creon 수집과 같은 방식)
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from typing import List, Optional, Tuple

from shared.data_library.paths import PROJECT_ROOT

from . import sources, store

LOCK = ("collect", ".running.json")
CREON_DRIVER_DIR = os.path.join(PROJECT_ROOT, "CAiModelBaker", "core")
CREON_ARGS = {  # universe_driver.py 인자 (cwd = CAiModelBaker/core)
    "creon_universe": ["universe"],
    "creon_daily": ["daily", "--codes-file", "../../DataVault/staging/kr_equity/targets_creon.txt"],
    "creon_daily_unadj": ["daily_unadj", "--codes-file", "../../DataVault/staging/kr_equity/targets_creon.txt"],
    "creon_daily_ext": ["daily_ext", "--codes-file", "../../DataVault/staging/kr_equity/targets_creon.txt"],
    "creon_index": ["index"],
}
# 이 Creon 단계가 끝나면 이어서 원천 적재(creon_bronze)까지 한다 — 착륙 구역(staging)은 받은 그대로의 보존층이고,
# 원천(raw/creon/candle_1d_unadj·corporate_actions)은 적재해야 생긴다. 사람이 두 번 누르지 않게 묶는다(2026-10-16).
THEN_BRONZE = {"creon_daily", "creon_daily_unadj"}


def _pid_alive(pid: int) -> bool:
    """살아 있는 프로세스인가. 대시보드가 띄운 자식은 끝나도 회수 전까지 좀비로 남아 kill(0) 이 성공한다
    (미국 수집기에서 10-15 실측한 결함과 같다) — 우리 자식이면 회수하고, /proc 상태가 Z 면 끝난 것으로 본다."""
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def running() -> Optional[dict]:
    """실행 중인 단계(죽은 프로세스의 잠금은 치운다)."""
    j = store.read_json(store.path(*LOCK))
    if not j:
        return None
    if _pid_alive(int(j.get("pid", -1))):
        return j
    try:
        os.remove(store.path(*LOCK))
    except OSError:
        pass
    return None


def _py_launcher() -> Optional[List[str]]:
    for cand in (shutil.which("py.exe"), "/mnt/c/WINDOWS/py.exe", "/mnt/c/Windows/py.exe"):
        if cand and os.path.exists(cand):
            return [cand, "-3.10-32"]
    for ps in ("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", shutil.which("powershell.exe")):
        if ps and os.path.exists(ps):
            return [ps, "-NonInteractive", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "py", "-3.10-32"]
    return None


def command(step: str, full: bool = False) -> Tuple[List[str], str]:
    """(argv, cwd). 알 수 없는 단계면 KeyError."""
    meta = next(s for s in sources.STEPS if s["key"] == step)
    if meta["runner"] == "linux":
        return [sys.executable, "-m", "shared.data_library.kr_equity.cli", step], PROJECT_ROOT
    py = _py_launcher()
    if not py:
        raise RuntimeError("Windows Python(py.exe)·PowerShell 을 찾지 못했다 — Creon 단계는 WSL/Windows 에서만")
    args = py + ["universe_driver.py"] + CREON_ARGS[step] + ["--stage", store.DATASET]
    if full:
        args.append("--full")
    if step in THEN_BRONZE:
        # Linux 쪽 감싸개가 Windows 수집을 기다렸다가 성공하면 원천 적재까지 이어서 한다(잠금·로그는 감싸개 프로세스 기준)
        return [sys.executable, "-m", "shared.data_library.kr_equity.cli", "creon_then_bronze", step] + (["--full"] if full else []), PROJECT_ROOT
    return args, CREON_DRIVER_DIR


def creon_argv(step: str, full: bool = False) -> Tuple[List[str], str]:
    """Creon 단계의 Windows 명령 그 자체(감싸개 없이)."""
    py = _py_launcher()
    if not py:
        raise RuntimeError("Windows Python(py.exe)·PowerShell 을 찾지 못했다 — Creon 단계는 WSL/Windows 에서만")
    return py + ["universe_driver.py"] + CREON_ARGS[step] + ["--stage", store.DATASET] + (["--full"] if full else []), CREON_DRIVER_DIR


def start(step: str, full: bool = False) -> dict:
    return _spawn(step, full)[1]


def run_sync(step: str, full: bool = False) -> int:
    """단계를 실행하고 끝날 때까지 기다린다(통합 업데이트용). 반환: 종료 코드. 잠금·로그는 start 와 같다."""
    proc, _ = _spawn(step, full)
    rc = proc.wait()
    try:
        os.remove(store.path(*LOCK))
    except OSError:
        pass
    return rc


def _spawn(step: str, full: bool = False):
    cur = running()
    if cur:
        raise RuntimeError(f"이미 실행 중: {cur['step']} (pid {cur['pid']}, {cur['started_at']})")
    argv, cwd = command(step, full)
    os.makedirs(store.path("logs"), exist_ok=True)
    log_path = store.path("logs", f"{step}_{datetime.now():%Y%m%d_%H%M%S}.out")
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", PROJECT_ROOT)
    if step.startswith("dart") and not env.get("OPEN_DART_KEY"):
        try:
            env["OPEN_DART_KEY"] = store.dart_key()
        except SystemExit:
            raise RuntimeError("OPEN_DART_KEY 없음 — 환경변수 또는 .bashrc 에 설정")
    with open(log_path, "ab") as out:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env,
                                start_new_session=True)
    info = {"step": step, "pid": proc.pid, "started_at": datetime.now().isoformat(timespec="seconds"), "log": log_path,
            "full": full, "argv": argv}
    os.makedirs(store.path("collect"), exist_ok=True)
    with open(store.path(*LOCK), "w", encoding="utf-8") as f:
        import json
        json.dump(info, f, ensure_ascii=False)
    return proc, info


def log_tail(n: int = 30) -> List[str]:
    d = store.path("logs")
    if not os.path.isdir(d):
        return []
    outs = sorted((f for f in os.listdir(d) if f.endswith(".out")), key=lambda f: os.path.getmtime(os.path.join(d, f)))
    if not outs:
        return []
    try:
        with open(os.path.join(d, outs[-1]), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16384))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        return [outs[-1]] + [ln for ln in lines if ln.strip()][-n:]
    except OSError:
        return []
