"""통합 업데이트 — 대시보드 버튼 하나로 고른 갱신 항목을 정해진 순서로 실행하고 상태를 남긴다.

순서(의존·자원 기준)
  1. ops      3단계 메달리온 빌드 = 운용 데이터(Creon·선물·cTrader·가상자산 수집 → 정규화 → 피처) — 대시보드 안의 메달리온 워커.
              따로 있던 '메달리온 일괄 빌드' 버튼은 이 항목으로 합쳤다(같은 일을 하는 버튼 두 개 → 하나)
  2. 동시에:  us   미국 전 종목 일봉(Yahoo, 네트워크만 씀)
              kr   한국 전 종목 일봉: 종목 목록 갱신(Creon 상장 스냅샷 → KIND 상폐 목록·universe.csv → 새 상폐 종목 네이버 일봉 → KRX 시총)
                   → Creon 무수정(→원천 적재) → Creon 수정(→기업행위 원장) → [kr_ext] → 패널 → 골드
     Creon 을 쓰는 항목(ops·kr·kr_ext)은 한 줄로만 돈다 — 같은 Creon 세션·요청 한도를 나눠 쓰면 서로 느려지거나 실패한다.
  3. checks   실물 검사(pytest -m real_vault) — 데이터가 바뀐 뒤에 돈다
실패 처리: 한 항목이 실패해도 무관한 항목은 계속, 의존하는 항목(무수정 수집 실패 → 그 뒤 한국 단계)은 건너뛴다.
  종목 목록 갱신 단계는 실패해도 이전 목록으로 일봉 수집을 계속하고, 항목을 '확인 필요'로 끝낸다.
  (2026-11-15 까지 이 단계가 빠져 있어 10-13 목록이 고정됐다 — 그 뒤 신규 상장은 안 받히고 새 상폐는 '상장'으로 남았다)
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

# 묶음 — 화면에서 나눠 보여 준다. model = LightGBM 학습·실전에 들어가는 데이터, research = 원장 밖 연구 저장소(모델 학습에 안 쓰임).
GROUPS: List[dict] = [
    {"key": "model", "title": "LightGBM 운용 데이터", "badge": "자주",
     "desc": "AutoLab 학습·CTrade 실전 모델이 읽는 데이터(활성 자산 원천 → 정규화 → 피처). 짧게 자주 돌리는 묶음."},
    {"key": "research", "title": "연구용 전 종목 데이터", "badge": "가끔",
     "desc": "한국·미국 전 종목 일봉·수급(staging 연구 저장소). LightGBM 모델은 읽지 않는다 — 연구할 때나 주 1회 정도. 오래 걸린다."},
    {"key": "check", "title": "점검", "badge": "갱신 뒤",
     "desc": "데이터를 바꾼 뒤 상태 검사."},
]
# warn_days: 마지막 성공 후 이만큼 지나면 '갱신 권장'(노랑), danger_days: '복구 불가 손실 위험'(빨강, 없으면 노랑까지만)
ITEMS: List[dict] = [
    {"key": "ops", "group": "model", "title": "3단계 메달리온 빌드 (운용 데이터)",
     "desc": "1 원천 수집(Creon 틱·1분봉·일봉, 선물, cTrader, 가상자산) → 2 정규화 → 3 피처 결합 — 진행 막대가 이 카드 아래에 뜬다", "est_min": 15,
     "cadence": "매 거래일 권장 · 늦어도 2주 안", "warn_days": 7, "danger_days": 14,
     "why": "Creon 서버는 틱을 20거래일만 보관 — 그보다 오래 안 돌리면 그 사이 틱(10초봉 원천)은 다시 못 받는다"},
    {"key": "us", "group": "research", "title": "미국 전 종목 일봉", "desc": "Yahoo 1.3만 종목 증분(최신 종목은 건너뜀) — 며칠 밀리면 더 걸린다", "est_min": 25,
     "cadence": "연구할 때 · 주 1회 정도", "warn_days": 14, "why": "Yahoo 는 과거를 언제든 다시 준다 — 늦어도 손실 없음"},
    {"key": "kr", "group": "research", "title": "한국 전 종목 일봉",
     "desc": "종목 목록 갱신(신규 상장·상폐) → Creon 무수정·수정 일봉 → 원천 적재·기업행위 원장 → 분석 패널 → 골드", "est_min": 70,
     "cadence": "연구할 때 · 주 1회 정도", "warn_days": 14, "why": "Creon 일봉은 상장 이후 전체를 다시 준다 — 늦어도 손실 없음(상폐 종목은 네이버·KRX 로 보완)"},
    {"key": "kr_ext", "group": "research", "title": "한국 주식수·시총·외국인·기관", "desc": "Creon 일별 보조 지표 증분(패널 빌드 전에 실행)", "est_min": 17,
     "cadence": "한국 일봉과 함께", "warn_days": 14, "why": "Creon 이 과거 일별 값을 다시 준다 — 늦어도 손실 없음"},
    {"key": "checks", "group": "check", "title": "실물 검사", "desc": "실제 데이터 상태 검사 29개(래칫·감사) — 마지막에 실행", "est_min": 5,
     "cadence": "다른 항목 갱신 뒤", "why": "데이터를 바꾼 뒤에만 의미가 있다"},
]
KEYS = [i["key"] for i in ITEMS]
# 종목 목록 갱신 — 일봉 수집 대상(targets_creon.txt)이 여기서 정해진다. 실패해도 치명적이지 않다(이전 목록으로 계속).
#   creon_universe  Creon 현재 상장 보통주 → reference/listed_snapshot.csv
#   kind            KIND 상폐 목록(2014~오늘) + 스냅샷 → universe.csv·targets_creon.txt 재생성
#   naver           목록의 상폐 종목 중 일봉이 아직 없는 것(새로 상폐된 종목) — 패널은 상폐 종목 가격을 여기서 읽는다
#   krx_marcap      상폐 종목 시총 원천(올해 파일은 7일마다만 다시 받음)
KR_PREP_STEPS = ["creon_universe", "kind", "naver", "krx_marcap"]
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
    """운용 → 한국+보조지표 → 미국 → 검사 순서(메모리 때문에 직렬)로 본 전체 예상 분."""
    pick = lambda k: est[k] if k in selected else 0.0
    return pick("ops") + pick("kr") + pick("kr_ext") + pick("us") + pick("checks")


def _tail_lines(path: str, n: int = 8) -> tuple[list[str], str]:
    """파일 마지막 n줄과 수정 시각(HH:MM:SS)을 읽는다."""
    if not path or not os.path.exists(path):
        return [], ""
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%H:%M:%S")
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16384))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        return [ln for ln in lines if ln.strip()][-n:], mtime
    except OSError:
        return [], ""


def _live_progress(st: dict) -> dict:
    """현재 실행 중인 하위 프로세스의 실시간 진행 상태/최근 로그를 수집한다."""
    items = st.get("items") or {}
    running_key = next((k for k, v in items.items() if v.get("status") == "running"), None) if st.get("status") == "running" else None
    live: Dict[str, Any] = {"key": running_key, "lines": [], "updated_at": "", "orphan": None}

    try:
        from shared.data_library.kr_equity import runner as kr_runner
        kr_run = kr_runner.running()
    except Exception:
        kr_run = None

    if running_key in ("kr", "kr_ext") or (running_key is None and kr_run):
        if kr_run:
            lines, mtime = _tail_lines(kr_run.get("log", ""), n=8)
            live["lines"] = lines
            live["updated_at"] = mtime
            if running_key is None:
                live["orphan"] = {
                    "key": "kr_ext" if kr_run.get("step") == "creon_daily_ext" else "kr",
                    "step": kr_run.get("step"),
                    "pid": kr_run.get("pid"),
                    "started_at": kr_run.get("started_at"),
                }
            return live

    if running_key == "us" or running_key is None:
        try:
            from shared.data_library.us_equity import collect as us_collect
            us_job = us_collect.job_status()
            us_run = us_job.get("running")
            last = us_job.get("last") or {}
            if running_key == "us" or us_run:
                if last:
                    done = last.get("done", 0)
                    target = last.get("target", 0)
                    pct = f" ({done * 100 // target}%)" if target else ""
                    summary = f"Yahoo 수집: {done}/{target}{pct} · 반영 {last.get('fetched', 0)} · 건너뜀 {last.get('skipped', 0)} · 실패 {last.get('failed_n', 0)}"
                    live["lines"] = [summary]
                    upd = str(last.get("updated_at") or "")
                    live["updated_at"] = upd[11:19] if len(upd) >= 19 else upd
                if running_key is None and us_run:
                    live["orphan"] = {"key": "us", "step": "yahoo_chart_1d", "pid": us_run.get("pid"), "started_at": us_run.get("started_at")}
                return live
        except Exception:
            pass

    if running_key == "checks":
        lines, mtime = _tail_lines(data_vault_path("temp", "unified_update_checks.log"), n=8)
        live["lines"] = lines
        live["updated_at"] = mtime
        return live

    return live


def last_success(st: dict) -> Dict[str, str]:
    """항목별 마지막 성공(완료·확인 필요) 시각. 기록(last_ok)이 우선, 없으면 예전 실행 이력에서 —
    이력은 항목별 결과가 없어 '골랐고 실패 목록에 없음'을 성공으로 본다(중단된 실행은 status 로 제외)."""
    out: Dict[str, str] = {}
    for h in st.get("history", []):
        if h.get("status") == "interrupted" or not h.get("finished_at"):
            continue
        for k in h.get("selected") or []:
            if k not in (h.get("failed") or []):
                out[k] = max(out.get(k, ""), h["finished_at"])
    for k, at in (st.get("last_ok") or {}).items():
        out[k] = max(out.get(k, ""), at)
    return out


def freshness(item: dict, last_at: Optional[str], now: Optional[datetime] = None) -> dict:
    """마지막 성공 이후 경과일과 단계: ok / warn(갱신 권장) / danger(손실 위험) / never(기록 없음) / none(주기 없음)."""
    if not item.get("warn_days"):
        level = "none"
        days = None
    elif not last_at:
        level, days = "never", None
    else:
        days = ((now or datetime.now()) - datetime.fromisoformat(last_at)).total_seconds() / 86400
        level = "danger" if item.get("danger_days") and days >= item["danger_days"] else ("warn" if days >= item["warn_days"] else "ok")
    return {"last_ok": last_at, "days": None if days is None else round(days, 1), "level": level}


def overview() -> dict:
    st = load()
    est = estimates(st)
    ok = last_success(st)
    return {"groups": GROUPS,
            "items": [{**i, "est_min": round(est[i["key"]]), "fresh": freshness(i, ok.get(i["key"]))} for i in ITEMS], "state": st,
            "total_est_all": round(total_estimate(KEYS, est)), "live": _live_progress(st)}


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
              "last_minutes": st.get("last_minutes", {}), "last_ok": st.get("last_ok", {}), "history": st.get("history", [])[-19:]}
        _save(st)
        return st


def set_item(key: str, status: str, detail: str = "", **extra) -> None:
    with _LOCK:
        st = load()
        it = st.setdefault("items", {}).setdefault(key, {})
        if status == "running" and it.get("status") != "running":     # 단계마다 running 을 다시 부르므로 처음 시작만 기록
            it["started_at"] = _now()
        if status in ("done", "warn", "failed", "skipped"):          # warn(확인 필요)도 끝난 것 — 예전엔 빠져 '걸린 시간'이 계속 늘었다
            it["finished_at"] = _now()
            if status in ("done", "warn"):
                st.setdefault("last_ok", {})[key] = it["finished_at"]
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
                                             "selected": st.get("selected"), "failed": failed, "status": st["status"]})
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


def _universe_status() -> Dict[str, str]:
    """universe.csv 의 {종목코드: listed|delisted} — 목록 갱신 전후 비교용."""
    from shared.data_library.kr_equity import store
    return {r["code"]: r["status"] for r in store.read_universe()}


def universe_change(before: Dict[str, str], after: Dict[str, str]) -> str:
    """목록 갱신 결과 한 줄: 상장·상폐 수와 이번에 새로 들어온 상장·상폐 전환 수."""
    listed = sum(v == "listed" for v in after.values())
    new = sum(1 for c, v in after.items() if v == "listed" and c not in before)
    gone = sum(1 for c, v in after.items() if v == "delisted" and before.get(c) == "listed")
    dropped = sum(1 for c, v in before.items() if v == "listed" and c not in after)   # 상폐 목록에도 없이 사라짐(코드 변경 등)
    out = f"종목 목록 상장 {listed}·상폐 {len(after) - listed} (신규 상장 {new}·상폐 전환 {gone}"
    return out + (f"·목록에서 사라짐 {dropped})" if dropped else ")")


def run_kr(selected: List[str]) -> None:
    """한국 일봉 단계들을 차례로. 종목 목록 갱신은 실패해도 계속(경고), 무수정 수집이 실패하면 뒤 단계(패널·골드)는
    건너뛴다(오래된 원천으로 덮어쓰지 않게)."""
    from shared.data_library.kr_equity import runner
    steps = []
    if "kr" in selected:
        steps += KR_PREP_STEPS + KR_STEPS
    if "kr_ext" in selected:
        steps.append("creon_daily_ext")
    if "kr" in selected:
        steps += ["panel", "gold"]
    titles = {"creon_universe": "종목 목록(Creon 상장 스냅샷)", "kind": "상폐 목록·유니버스(KIND)",
              "naver": "상폐 종목 일봉(네이버)", "krx_marcap": "KRX 일별 시총",
              "creon_daily_unadj": "Creon 무수정 일봉(+원천 적재)", "creon_daily": "Creon 수정 일봉(+기업행위 원장)",
              "creon_daily_ext": "주식수·시총·외국인·기관", "panel": "분석 패널", "gold": "골드 지표"}
    if "kr" in selected:
        set_item("kr", "running", "")
    before = _universe_status() if "kr" in selected else {}
    broken = False
    warnings: List[str] = []
    done_steps: List[str] = []
    for step in steps:
        owner = "kr_ext" if step == "creon_daily_ext" else "kr"
        if owner == "kr_ext":
            set_item("kr_ext", "running", titles[step])
        elif broken:
            continue
        else:
            set_item("kr", "running", f"{titles[step]} 진행 중 ({len(done_steps) + len(warnings) + 1}/{sum(1 for s in steps if s != 'creon_daily_ext')})")
        try:
            rc = runner.run_sync(step)
        except Exception as e:
            rc, err = -1, str(e)
        else:
            err = f"종료 코드 {rc}" if rc else ""
        if owner == "kr_ext":
            set_item("kr_ext", "done" if rc == 0 else "failed", "" if rc == 0 else err)
            continue
        if rc != 0 and step in KR_PREP_STEPS:
            warnings.append(f"{titles[step]} 실패({err})")
            continue
        if rc != 0:
            broken = True
            rest = [titles[s] for s in steps[steps.index(step) + 1:] if s != "creon_daily_ext"]
            set_item("kr", "failed", f"{titles[step]} 실패({err})" + (f" — 건너뜀: {', '.join(rest)}" if rest else "")
                     + (f" · 경고: {'; '.join(warnings)}" if warnings else ""))
            continue
        done_steps.append(step)
    if "kr" in selected and not broken:
        try:
            change = universe_change(before, _universe_status())
        except Exception as e:                                   # 요약 실패가 결과를 가리지 않게
            change = f"종목 목록 요약 실패({e})"
        detail = change + " · " + " → ".join(titles[s] for s in done_steps if s not in KR_PREP_STEPS)
        if warnings:
            set_item("kr", "warn", detail + " · 경고(이전 목록으로 진행): " + "; ".join(warnings))
        else:
            set_item("kr", "done", detail)


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
    log_path = data_vault_path("temp", "unified_update_checks.log")
    try:
        for p_rm in (xml_path, log_path):
            if os.path.exists(p_rm):
                os.remove(p_rm)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        with open(log_path, "wb") as lf:
            p = subprocess.run(
                [sys.executable, "-m", "pytest", "-v", "--tb=short", "-m", "real_vault", "-p", "no:cacheprovider", f"--junitxml={xml_path}"],
                cwd=PROJECT_ROOT, stdout=lf, stderr=subprocess.STDOUT, env=env, timeout=3600,
            )
        if not os.path.exists(xml_path):
            tail, _ = _tail_lines(log_path, n=6)
            set_item("checks", "failed", f"검사가 돌지 못했습니다(pytest 종료 코드 {p.returncode}): {' | '.join(tail)[-300:]}")
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
