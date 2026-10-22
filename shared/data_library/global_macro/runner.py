"""global_macro 단계를 대시보드에서 실행 — 한 번에 하나(collect/.running.json 잠금), 출력은 logs/<step>_<시각>.out.

단계: fetch(전 원천 받기) → panel(발표 시차 정합 패널) → dashboard(화면용 JSON). all = 셋을 차례로.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

from shared.data_library.paths import PROJECT_ROOT

from . import store

LOCK = ("collect", ".running.json")
STEPS: List[dict] = [
    {"key": "all", "title": "전체 갱신", "runner": "linux", "desc": "받기 → 미국 업종 → 한국 업종 → 무역 → 미국 수입 경쟁 → EU 수입 경쟁 → 한국 품목 수출 → 패널 → 시스템 판독 → 화면 JSON (보통 이것만 누르면 됨)"},
    {"key": "fetch", "title": "원천 받기", "runner": "linux", "desc": "FRED·Yahoo·ECOS 전 시계열 + Creon 지수(kr_equity 원천) — 월·주 거시는 받은 날짜별 판 보관"},
    {"key": "industry", "title": "미국 업종 원천", "runner": "linux", "desc": "Ken French 49업종 수익·BE/ME(1926~) + FRED 업종 산업생산·생산능력(1972~)"},
    {"key": "kr_industry", "title": "한국 업종 원천", "runner": "linux", "desc": "코스피 업종 지수 20개 월말(Creon, kr_equity 원천) + 통계청 업종별 생산지수(KOSIS)"},
    {"key": "trade", "title": "무역 연결 원천", "runner": "linux", "desc": "한국 상대국별 수출입(ECOS)·OECD 부가가치 무역(TiVA)·상대국 선행지수·대만 지수"},
    {"key": "us_imports", "title": "미국 수입 경쟁(Census)", "runner": "linux", "desc": "미국의 한국·중국·일본·멕시코·베트남·대만산 품목별 월 수입(2010~), 품목 16개 — 약 2분, CENSUS_KEY 필요"},
    {"key": "eu_imports", "title": "EU 수입 경쟁(Eurostat)", "runner": "linux", "desc": "EU27 의 한국·중국·일본·미국·대만·베트남·멕시코산 품목별 월 수입(2010~, 역외 합계 분모), 품목 16개 — 약 4분, 키 불필요"},
    {"key": "kr_trade_items", "title": "한국 품목×국가 수출(관세청)", "runner": "linux", "desc": "13개 HS 장 × 26개국 × 2000~ 월별(지난 해는 받아 둔 것 재사용, 올해만 다시) — 처음엔 1~2시간, 이후 몇 분"},
    {"key": "reading", "title": "시스템 판독", "runner": "linux", "desc": "판독 엔진을 2008~ 매달 그 시점 자료로 재현 → 최신 판독(근거 사슬)·과거 성적 → derived/dashboard/reading.json"},
    {"key": "panel", "title": "패널 빌드", "runner": "linux", "desc": "일·주 시계열, 월말 시점에 알 수 있던 값(발표 시차 반영), 신선도 점검"},
    {"key": "dashboard", "title": "화면 JSON", "runner": "linux", "desc": "세계 지도·영향력·약세장 기록·현재 상태 → derived/dashboard/stage1.json"},
]


def _pid_alive(pid: int) -> bool:
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


def start(step: str, full: bool = False) -> dict:
    if step not in {s["key"] for s in STEPS}:
        raise RuntimeError(f"알 수 없는 단계: {step}")
    cur = running()
    if cur:
        raise RuntimeError(f"이미 실행 중: {cur['step']} (pid {cur['pid']}, {cur['started_at']})")
    argv = [sys.executable, "-m", "shared.data_library.global_macro.cli", step]
    os.makedirs(store.path("logs"), exist_ok=True)
    log_path = store.path("logs", f"{step}_{datetime.now():%Y%m%d_%H%M%S}.out")
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", PROJECT_ROOT)
    for k in ("ECOS_KEY", "KOSIS_KEY", "CENSUS_KEY", "OPEN_DATA_KEY"):
        if not env.get(k):
            try:
                env[k] = store.key_env(k)
            except RuntimeError:
                pass
    with open(log_path, "ab") as out:
        proc = subprocess.Popen(argv, cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True)
    info = {"step": step, "pid": proc.pid, "started_at": datetime.now().isoformat(timespec="seconds"), "log": log_path, "argv": argv}
    os.makedirs(store.path("collect"), exist_ok=True)
    with open(store.path(*LOCK), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    return info


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
