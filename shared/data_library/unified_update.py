"""통합 업데이트 — 대시보드 버튼 하나로 고른 갱신 항목을 정해진 순서로 실행하고 상태를 남긴다.

순서(의존·자원 기준)
  1. ops      운용 데이터(Creon·선물·cTrader·가상자산 수집 → 정규화 → 피처) — 대시보드 안의 메달리온 워커가 실행(호출부가 넘긴다)
  2. 동시에:  us   미국 전 종목 일봉(Yahoo, 네트워크만 씀)
              kr   한국 전 종목 일봉: Creon 무수정(→원천 적재) → Creon 수정(→기업행위 원장) → [kr_ext] → 패널 → 골드
     Creon 을 쓰는 항목(ops·kr·kr_ext)은 한 줄로만 돈다 — 같은 Creon 세션·요청 한도를 나눠 쓰면 서로 느려지거나 실패한다.
  3. checks   실물 검사(pytest -m real_vault) — 데이터가 바뀐 뒤에 돈다
실패 처리: 한 항목이 실패해도 무관한 항목은 계속, 의존하는 항목(무수정 수집 실패 → 그 뒤 한국 단계)은 건너뛴다.
예상 시간: 항목별 마지막 실측 소요 시간(없으면 아래 기본값, 2026-10-16 실측).
상태: DataVault/temp/unified_update.json — 페이지를 새로 열어도 진행 상황이 보이게.
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from shared.data_library.paths import PROJECT_ROOT, data_vault_path

ITEMS: List[dict] = [
    {"key": "ops", "title": "운용 데이터", "desc": "Creon 틱·1분봉·일봉, 선물, cTrader, 가상자산 수집 → 정규화 → 피처 결합", "est_min": 15},
    {"key": "us", "title": "미국 전 종목 일봉", "desc": "Yahoo 1.3만 종목 증분(최신 종목은 건너뜀) — 며칠 밀리면 더 걸린다", "est_min": 25},
    {"key": "kr", "title": "한국 전 종목 일봉", "desc": "Creon 무수정·수정 일봉 → 원천 적재·기업행위 원장 → 분석 패널 → 골드", "est_min": 70},
    {"key": "kr_ext", "title": "한국 주식수·시총·외국인·기관", "desc": "Creon 일별 보조 지표 증분(패널 빌드 전에 실행)", "est_min": 17},
    {"key": "checks", "title": "실물 검사", "desc": "실제 데이터 상태 검사 29개(래칫·감사) — 마지막에 실행", "est_min": 5},
]
KEYS = [i["key"] for i in ITEMS]
KR_STEPS = ["creon_daily_unadj", "creon_daily"]            # 그 뒤 [creon_daily_ext] → panel → gold
_LOCK = threading.Lock()


def state_path() -> str:
    return data_vault_path("temp", "unified_update.json")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(st: dict) -> None:
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, state_path())


def estimates(st: Optional[dict] = None) -> Dict[str, float]:
    """항목별 예상 분 — 마지막 실측(성공한 실행)이 있으면 그것, 없으면 기본값."""
    last = (st or load()).get("last_minutes", {})
    return {i["key"]: float(last.get(i["key"], i["est_min"])) for i in ITEMS}


def total_estimate(selected: List[str], est: Dict[str, float]) -> float:
    """운용 → (미국 ∥ 한국+보조지표) → 검사 순서로 본 전체 예상 분."""
    pick = lambda k: est[k] if k in selected else 0.0
    return pick("ops") + max(pick("us"), pick("kr") + pick("kr_ext")) + pick("checks")


def overview() -> dict:
    st = load()
    est = estimates(st)
    return {"items": [{**i, "est_min": round(est[i["key"]])} for i in ITEMS], "state": st,
            "total_est_all": round(total_estimate(KEYS, est))}


# ---------------------------------------------------------------- 실행 상태
def begin(selected: List[str]) -> dict:
    with _LOCK:
        st = load()
        if st.get("status") == "running":
            raise RuntimeError(f"통합 업데이트가 이미 실행 중입니다 (시작 {st.get('started_at')})")
        selected = [k for k in KEYS if k in selected]
        if not selected:
            raise ValueError("업데이트할 항목을 하나 이상 고르세요")
        est = estimates(st)
        st = {"status": "running", "started_at": _now(), "finished_at": None, "selected": selected,
              "est_total_min": round(total_estimate(selected, est)),
              "items": {k: {"status": "pending" if k in selected else "off", "est_min": round(est[k])} for k in KEYS},
              "last_minutes": st.get("last_minutes", {}), "history": st.get("history", [])[-19:]}
        _save(st)
        return st


def set_item(key: str, status: str, detail: str = "", **extra) -> None:
    with _LOCK:
        st = load()
        it = st.setdefault("items", {}).setdefault(key, {})
        if status == "running":
            it["started_at"] = _now()
        if status in ("done", "failed", "skipped"):
            it["finished_at"] = _now()
            if status == "done" and it.get("started_at"):
                mins = (datetime.fromisoformat(it["finished_at"]) - datetime.fromisoformat(it["started_at"])).total_seconds() / 60
                st.setdefault("last_minutes", {})[key] = round(max(mins, 0.5), 1)
        it.update(status=status, detail=detail, **extra)
        _save(st)


def finish(interrupted: bool = False) -> None:
    with _LOCK:
        st = load()
        st["status"] = "interrupted" if interrupted else "finished"
        st["finished_at"] = _now()
        failed = [k for k, v in st.get("items", {}).items() if v.get("status") == "failed"]
        st.setdefault("history", []).append({"started_at": st.get("started_at"), "finished_at": st["finished_at"],
                                             "selected": st.get("selected"), "failed": failed})
        _save(st)


def mark_interrupted_if_stale(alive: bool) -> None:
    """대시보드가 재시작돼 실행기가 사라졌는데 상태가 running 으로 남아 있으면 '중단됨'으로 바꾼다."""
    st = load()
    if st.get("status") == "running" and not alive:
        for k, v in st.get("items", {}).items():
            if v.get("status") in ("pending", "running"):
                v.update(status="skipped", detail="대시보드 재시작으로 중단")
        _save(st)
        finish(interrupted=True)


# ---------------------------------------------------------------- 항목 실행(블로킹 — 스레드에서 부른다)
def run_us() -> None:
    from shared.data_library.us_equity import collect
    set_item("us", "running", "Yahoo 증분 수집 중")
    try:
        rc = collect.run_sync("chart")
        job = collect.job_status().get("last") or {}
        detail = f"반영 {job.get('fetched', '?')} · 건너뜀 {job.get('skipped', '?')} · 실패 {job.get('failed_n', '?')}"
        if job.get("behind_latest"):
            detail += f" · 최신 봉 미수신 {job['behind_latest']}"
        set_item("us", "done" if rc == 0 else "failed", detail if rc == 0 else f"종료 코드 {rc} — {detail}")
    except Exception as e:
        set_item("us", "failed", str(e))


def run_kr(selected: List[str]) -> None:
    """한국 일봉 단계들을 차례로. 무수정 수집이 실패하면 뒤 단계(패널·골드)는 건너뛴다(오래된 원천으로 덮어쓰지 않게)."""
    from shared.data_library.kr_equity import runner
    steps = []
    if "kr" in selected:
        steps += KR_STEPS
    if "kr_ext" in selected:
        steps.append("creon_daily_ext")
    if "kr" in selected:
        steps += ["panel", "gold"]
    titles = {"creon_daily_unadj": "Creon 무수정 일봉(+원천 적재)", "creon_daily": "Creon 수정 일봉(+기업행위 원장)",
              "creon_daily_ext": "주식수·시총·외국인·기관", "panel": "분석 패널", "gold": "골드 지표"}
    if "kr" in selected:
        set_item("kr", "running", "")
    broken = False
    done_steps: List[str] = []
    for step in steps:
        owner = "kr_ext" if step == "creon_daily_ext" else "kr"
        if owner == "kr_ext":
            set_item("kr_ext", "running", titles[step])
        elif broken:
            continue
        else:
            set_item("kr", "running", f"{titles[step]} 진행 중 ({len(done_steps) + 1}/{sum(1 for s in steps if s != 'creon_daily_ext')})")
        try:
            rc = runner.run_sync(step)
        except Exception as e:
            rc, err = -1, str(e)
        else:
            err = f"종료 코드 {rc}" if rc else ""
        if owner == "kr_ext":
            set_item("kr_ext", "done" if rc == 0 else "failed", "" if rc == 0 else err)
            continue
        if rc != 0:
            broken = True
            rest = [titles[s] for s in steps[steps.index(step) + 1:] if s != "creon_daily_ext"]
            set_item("kr", "failed", f"{titles[step]} 실패({err})" + (f" — 건너뜀: {', '.join(rest)}" if rest else ""))
            continue
        done_steps.append(step)
    if "kr" in selected and not broken:
        set_item("kr", "done", " → ".join(titles[s] for s in done_steps))


def _test_doc(path: str, name: str) -> str:
    """검사 함수 docstring 의 첫 문장 — 화면에 '무엇을 검사했나'로 보여 준다."""
    import ast
    try:
        with open(os.path.join(PROJECT_ROOT, path), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                return (ast.get_docstring(n) or "").strip().splitlines()[0][:200] if ast.get_docstring(n) else ""
    except (OSError, SyntaxError):
        pass
    return ""


def run_checks() -> None:
    """실물 검사(pytest -m real_vault). 결과는 JUnit XML 로 읽는다(요약 줄은 터미널 폭에서 메시지가 잘린다).
    실패는 코드 결함이 아니라 데이터 상태 신호다 — '확인 필요'로 남긴다."""
    import xml.etree.ElementTree as ET
    set_item("checks", "running", "실제 데이터 상태 검사 중")
    xml_path = data_vault_path("temp", "unified_update_checks.xml")
    try:
        if os.path.exists(xml_path):
            os.remove(xml_path)
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-m", "real_vault", "-p", "no:cacheprovider", f"--junitxml={xml_path}"],
                           cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=3600)
        if not os.path.exists(xml_path):
            set_item("checks", "failed", f"검사가 돌지 못했습니다(pytest 종료 코드 {p.returncode}): {(p.stdout + p.stderr)[-300:]}")
            return
        counts = {"passed": 0, "failed": 0, "skipped": 0}
        failures = []
        for tc in ET.parse(xml_path).getroot().iter("testcase"):
            bad = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
            if bad is not None:
                counts["failed"] += 1
                cls = tc.get("classname", "")
                path = cls.replace(".", "/") + ".py"
                name = tc.get("name", "")
                failures.append({"test": name, "file": path, "about": _test_doc(path, name),
                                 "message": (bad.get("message") or "").strip().splitlines()[0][:300] if bad.get("message") else ""})
            elif tc.find("skipped") is not None:
                counts["skipped"] += 1
            else:
                counts["passed"] += 1
        detail = f"통과 {counts['passed']} · 실패 {counts['failed']} · 건너뜀 {counts['skipped']}"
        set_item("checks", "warn" if failures else "done", detail, failures=failures)
    except Exception as e:
        set_item("checks", "failed", str(e))
