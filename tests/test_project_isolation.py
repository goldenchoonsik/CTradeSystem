# -*- coding: utf-8 -*-
"""
프로젝트 격리 래칫 (2026-09-28)
================================
사고: 1분봉 프로젝트 KODEX200_M1_TEST2 를 자동연구했는데 그 폴더의 train_run.log 에 10초봉 세션 로그가 들어갔다.
추적해 보니 '프로젝트' 는 폴더 단위로 격리돼 있지 않았다.

1. 작업 설정은 전역 active.json 한 벌이었고, 새 프로젝트는 직전 프로젝트 설정을 통째로 물려받았다.
2. 프로젝트를 이름만으로 deploy/*/<이름> 에서 찾아 첫 매치를 썼다 (7곳). IC 스크리닝은 못 찾으면
   data/ 아래 아무 종목의 스냅샷을 집었다.
3. 재실행 산출물이 프로젝트 루트에 덮어써져, 탈락(REJECTED) 뒤에도 model/ 에 마지막 재학습 후보가 남았고
   배포 API 는 합격 여부를 확인하지 않았다.
4. 학습 로그는 브라우저가 '로그 저장' 해야 공용 파일에 기록됐고, 학습 종료 시 그 끝부분을 복사했다.

이 테스트는 값이 아니라 그 구조(단일 진입점·경로 식별·실행 폴더·합격 게이트)를 잡는다.
"""

import json
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BAKER = os.path.join(_ROOT, "CAiModelBaker")
for _p in (_ROOT, _BAKER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.config import project_store as ps  # noqa: E402
from shared.config import config_manager as cm  # noqa: E402
from shared.data_library import paths as dpaths  # noqa: E402


@pytest.fixture
def deploy_root(tmp_path, monkeypatch):
    root = tmp_path / "deploy"
    root.mkdir()
    # 테스트 시작 전 현재 열린 프로젝트 포인터를 저장해 두고(배포 폴더를 바꾸기 전에 읽는다 — 바꾼 뒤엔 그 프로젝트가
    # 새 폴더에 없어 깨진 포인터다), 빈 배포 폴더에서는 열린 프로젝트 없이 시작한다. 종료 후 복원한다.
    _original_ref = cm.get_active_project_ref()
    monkeypatch.setattr(dpaths, "DEPLOY_ROOT", str(root))
    cm.set_active_project_ref(None)
    yield root
    # teardown: active_project.json에 원래 ref를 직접 기록한다.
    # 주의: 이 시점에 monkeypatch가 아직 undo되지 않아 DEPLOY_ROOT가 tmp일 수 있으므로
    # get_active_ref()를 사용하지 않고 set_active_ref(config_dir, ref)를 직접 호출한다.
    # set_active_ref는 DEPLOY_ROOT와 무관하게 config_dir/active_project.json에만 쓴다.
    from shared.config.config_manager import get_config_dir
    from shared.config.project_store import set_active_ref
    set_active_ref(get_config_dir("CAiModelBaker"), _original_ref)


def _make_project(root, market_dir, name, config=None, model=False, interval=None):
    pdir = root / market_dir / name
    (pdir / "model").mkdir(parents=True)
    (pdir / "reports").mkdir()
    cfg = {"SYMBOL_UPBIT": market_dir.rsplit("_", 1)[0], "INTERVAL": market_dir.rsplit("_", 1)[1],
           "ACTIVE_SESSION_ID": name, "FEATURE_CONFIG": {"enabled_features": ["target_ma_ratio"]}}
    cfg.update(config or {})
    (pdir / "config_snapshot.json").write_text(json.dumps(cfg), encoding="utf-8")
    if model:
        itv = interval or market_dir.rsplit("_", 1)[1]
        (pdir / "model" / f"model_{itv}.txt").write_text("tree", encoding="utf-8")
    return pdir


# ---------------------------------------------------------------------------
# 1. 식별: 전체 경로, 이름 유일, 모호하면 조용히 고르지 않는다
# ---------------------------------------------------------------------------
def test_ref_rejects_unsafe_segments():
    for bad in ("../x/y", "a/b/c", "069500_1m", "069500_1m/..", "069500_1m/a b"):
        with pytest.raises(ps.ProjectError):
            ps.split_ref(bad)
    assert tuple(ps.split_ref("069500_1m/KODEX200_M1_TEST2")) == ("069500_1m", "KODEX200_M1_TEST2")


def test_resolve_by_name_never_picks_first_match(deploy_root):
    _make_project(deploy_root, "069500_10s", "SAME")
    _make_project(deploy_root, "069500_1m", "SAME")
    with pytest.raises(ps.AmbiguousProjectError):
        ps.resolve("SAME")
    assert ps.resolve("SAME", market_dir="069500_1m") == "069500_1m/SAME"
    assert ps.resolve("SAME", symbol="069500", interval="10s") == "069500_10s/SAME"
    assert ps.resolve_dir("SAME") is None


def test_create_project_dir_rejects_name_used_in_other_interval(deploy_root):
    _make_project(deploy_root, "069500_10s", "M1_TEST1")
    with pytest.raises(ps.ProjectExistsError):
        ps.create_project_dir("069500_1m/M1_TEST1")


# ---------------------------------------------------------------------------
# 2. 설정 원본 = 열린 프로젝트 폴더. 신원(종목·인터벌·이름)은 경로로 고정
# ---------------------------------------------------------------------------
def test_open_project_is_the_config_source_and_identity_is_locked(deploy_root):
    _make_project(deploy_root, "069500_1m", "P1", {"TRAIN_START": "2024-08-01", "ONLY_IN_P1": 1})
    # 열린 프로젝트가 없으면 저장을 거부한다 — 전역 파일로 대신 쓰지 않는다 (2026-10-01 자유 모드 은퇴)
    cm.set_active_project_ref(None)
    free = cm.load_project_config("CAiModelBaker")
    free["LEAK_FROM_PREVIOUS"] = True
    with pytest.raises(ps.NoOpenProjectError):
        cm.save_project_config("CAiModelBaker", free)

    cm.set_active_project_ref("069500_1m/P1")
    cfg = cm.load_project_config("CAiModelBaker")
    assert cfg["INTERVAL"] == "1m" and cfg["ACTIVE_SESSION_ID"] == "P1"
    assert cfg["TRAIN_START"] == "2024-08-01" and cfg["ONLY_IN_P1"] == 1
    assert "LEAK_FROM_PREVIOUS" not in cfg

    # 저장: 프로젝트 파일로, 신원 변경은 무시, 실행용 임시 키(_*)는 버림
    cfg.update({"INTERVAL": "10s", "ACTIVE_SESSION_ID": "OTHER", "TRAIN_START": "2025-01-01", "_RUN_DIR": "/tmp/x"})
    cm.save_project_config("CAiModelBaker", cfg)
    stored = json.loads((deploy_root / "069500_1m" / "P1" / "config_snapshot.json").read_text(encoding="utf-8"))
    assert stored["INTERVAL"] == "1m" and stored["ACTIVE_SESSION_ID"] == "P1"
    assert stored["TRAIN_START"] == "2025-01-01"
    assert "_RUN_DIR" not in stored


def test_feature_config_follows_open_project(deploy_root):
    from shared.features import feature_config
    pdir = _make_project(deploy_root, "069500_1m", "P2")
    cm.set_active_project_ref("069500_1m/P2")
    assert feature_config.get_config_path() == str(pdir / "config_snapshot.json")


def test_base_config_does_not_inherit_open_project(deploy_root):
    _make_project(deploy_root, "069500_1m", "P3", {"ONLY_IN_P3": 1})
    cm.set_active_project_ref("069500_1m/P3")
    assert "ONLY_IN_P3" not in cm.load_base_config("CAiModelBaker")


# ---------------------------------------------------------------------------
# 3. 실행 폴더: 실행마다 새 폴더, 최근 N 개 보관, 산출물은 run/work 로
# ---------------------------------------------------------------------------
def test_new_run_dir_sorts_and_prunes(deploy_root):
    from datetime import datetime, timedelta
    _make_project(deploy_root, "069500_1m", "P4")
    t0 = datetime(2026, 9, 28, 9, 0, 0)
    made = [ps.new_run_dir("069500_1m/P4", keep=3, now=t0 + timedelta(minutes=i)) for i in range(5)]
    runs = ps.list_runs("069500_1m/P4")
    assert runs == made[-3:]
    assert ps.latest_run_dir("069500_1m/P4") == made[-1]
    assert os.path.isdir(os.path.join(made[-1], ps.RUN_WORK_DIRNAME))


def test_artifact_dir_is_run_work_during_autolab(deploy_root):
    cfg = {"SYMBOL_UPBIT": "069500", "INTERVAL": "1m", "ACTIVE_SESSION_ID": "P5"}
    assert ps.artifact_dir(cfg) == os.path.join(str(deploy_root), "069500_1m", "P5")
    cfg[ps.RUN_DIR_KEY] = "/x/runs/20260928_090000"
    assert ps.artifact_dir(cfg) == "/x/runs/20260928_090000/work"
    assert ps.run_log_dir(cfg) == "/x/runs/20260928_090000"


def test_tee_log_writes_server_side(tmp_path):
    seen = []
    log = ps.tee_log(seen.append, str(tmp_path / "run" / "train_run.log"), truncate=True)
    log("hello")
    assert seen == ["hello"]
    assert "hello" in (tmp_path / "run" / "train_run.log").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. 웹 API: 생성·열기·설정 저장·배포 게이트
# ---------------------------------------------------------------------------
@pytest.fixture
def client(deploy_root):
    from fastapi.testclient import TestClient
    from CAiModelBaker.web_dashboard import main as wb
    return TestClient(wb.app), wb


def test_create_rejects_duplicate_name_across_intervals(client, deploy_root):
    c, _ = client
    _make_project(deploy_root, "069500_10s", "DUP")
    r = c.post("/api/project/create", json={"name": "DUP", "symbol": "069500", "interval": "1m"}).json()
    assert r["status"] == "error" and "이미 존재" in r["error"]
    assert not (deploy_root / "069500_1m" / "DUP").exists()


def test_create_starts_from_defaults_and_opens_project(client, deploy_root):
    c, _ = client
    _make_project(deploy_root, "069500_10s", "OLD", {"ONLY_IN_OLD": 1})
    cm.set_active_project_ref("069500_10s/OLD")
    r = c.post("/api/project/create", json={"name": "NEW1", "symbol": "069500", "interval": "1m"}).json()
    assert r["status"] == "ok", r
    assert r["project_ref"] == "069500_1m/NEW1"
    assert cm.get_active_project_ref() == "069500_1m/NEW1"
    stored = json.loads((deploy_root / "069500_1m" / "NEW1" / "config_snapshot.json").read_text(encoding="utf-8"))
    assert "ONLY_IN_OLD" not in stored
    assert stored["INTERVAL"] == "1m" and stored["ACTIVE_SESSION_ID"] == "NEW1"
    assert stored["PROJECT_META"]["created_from"] == "defaults"


def test_config_api_refuses_identity_change_of_open_project(client, deploy_root):
    c, _ = client
    _make_project(deploy_root, "069500_1m", "P6")
    cm.set_active_project_ref("069500_1m/P6")
    r = c.post("/api/config", json={"INTERVAL": "10s"})
    assert r.status_code == 409
    r = c.post("/api/config", json={"TRAIN_START": "2024-09-01"})
    assert r.status_code == 200
    stored = json.loads((deploy_root / "069500_1m" / "P6" / "config_snapshot.json").read_text(encoding="utf-8"))
    assert stored["TRAIN_START"] == "2024-09-01" and stored["INTERVAL"] == "1m"


def test_deploy_gate_blocks_rejected_run_leftover(client, deploy_root):
    _, wb = client
    pdir = _make_project(deploy_root, "069500_1m", "P7", model=True)
    run = ps.new_run_dir("069500_1m/P7")
    with open(os.path.join(run, "leaderboard.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "REJECTED", "evaluated_at": "2026-09-28T14:27:42"}, f)
    ok, why = wb._deploy_gate(str(pdir), "model", "1m")
    assert not ok and "탈락" in why

    # 출처가 합격 챔피언이면 허용
    (pdir / "model" / "model_source.json").write_text(json.dumps({"kind": "autolab_champion", "status": "PASSED"}), encoding="utf-8")
    assert wb._deploy_gate(str(pdir), "model", "1m")[0]

    # 탈락 후보(rank_N)는 차단
    cand = os.path.join(run, "candidates", "rank_1")
    os.makedirs(cand)
    with open(os.path.join(cand, "candidate_detail.json"), "w", encoding="utf-8") as f:
        json.dump({"status": "REJECTED", "passed": False, "final_profit": 1.4, "final_trades": 4}, f)
    assert not wb._deploy_gate(str(pdir), "rank_1", "1m")[0]


def test_sessions_list_shows_project_without_model(client, deploy_root):
    c, _ = client
    _make_project(deploy_root, "069500_1m", "EMPTYMODEL", {"PROJECT_META": {"memo": "메모"}})
    rows = [s for s in c.get("/api/sessions").json() if s["session_id"] == "EMPTYMODEL"]
    assert rows and rows[0]["has_model"] is False and rows[0]["memo"] == "메모"


# ---------------------------------------------------------------------------
# 5. 구조 래칫: 이름으로 deploy 를 뒤지는 코드·공용 로그 복사가 돌아오지 않게
# ---------------------------------------------------------------------------
_NAME_SCAN = re.compile(r"for\s+\w+\s+in\s+(sorted\()?os\.listdir\(\s*(deploy_root|d_root|deploy_path)\s*\)")


@pytest.mark.parametrize("rel", [
    "CAiModelBaker/web_dashboard/main.py",
    "CAiModelBaker/core/feature_screener.py",
    "CAiModelBaker/core/research_loop.py",
    "shared/features/feature_config.py",
])
def test_no_name_scan_of_deploy_outside_project_store(rel):
    src = open(os.path.join(_ROOT, rel), encoding="utf-8").read()
    hits = [m.group(0) for m in _NAME_SCAN.finditer(src)]
    assert not hits, f"{rel}: 프로젝트를 이름으로 deploy/*/ 에서 찾는 코드는 project_store.resolve 로만 — {hits}"


def test_screener_has_no_any_snapshot_fallback():
    src = open(os.path.join(_ROOT, "CAiModelBaker/core/feature_screener.py"), encoding="utf-8").read()
    assert 'startswith("data_snapshot_")' not in src


def test_training_log_is_not_copied_from_shared_log():
    src = open(os.path.join(_ROOT, "CAiModelBaker/core/model_engine.py"), encoding="utf-8").read()
    assert "copy_tail(" not in src and "quant_engine_log.txt" not in src


def test_research_loop_writes_run_folder_not_project_root():
    src = open(os.path.join(_ROOT, "CAiModelBaker/core/research_loop.py"), encoding="utf-8").read()
    assert 'os.path.join(session_dir, "leaderboard.json")' not in src
    assert 'os.path.join(session_dir, "trials")' not in src
    assert 'os.path.join(session_dir, "candidates")' not in src
    assert "project_store.new_run_dir(" in src


def test_importing_baker_registry_keeps_open_project_as_feature_config_source():
    """운영 경로 검증 — conftest 가 set_config_root 를 무력화하므로 **새 프로세스**에서 확인한다.

    2026-10-01: CAiModelBaker.core.feature_registry 가 import 시점에 set_config_root(<Baker 경로>) 를 불러
    지표 설정이 열린 프로젝트가 아니라 전역 active.json 에서 읽혔다. 위 test_feature_config_follows_open_project 는
    conftest 가 그 호출을 막은 상태에서 돌아 이 결함을 보지 못했다.
    """
    import subprocess
    code = (
        "import sys; sys.path[:0]=[%r,%r]\n"
        "from shared.features import feature_config as fc\n"
        "before = fc.get_config_path()\n"
        "import CAiModelBaker.core.feature_registry\n"
        "after = fc.get_config_path()\n"
        "assert fc._CONFIG_ROOT_INTERNAL is None, fc._CONFIG_ROOT_INTERNAL\n"
        "assert before == after, (before, after)\n"
        "print('OK')\n"
    ) % (_ROOT, _BAKER)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=_ROOT, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]


def test_only_test_harness_moves_the_feature_config_root():
    """set_config_root 는 테스트 격리(conftest) 전용 — 운영 코드가 부르면 프로젝트 격리가 깨진다."""
    import ast
    offenders = []
    for base in ("CAiModelBaker", "CTrade", "CCommander", "shared"):
        for dp, dns, fns in os.walk(os.path.join(_ROOT, base)):
            dns[:] = [d for d in dns if d not in (".venv", "__pycache__", "tests", "scratch")]
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dp, fn)
                try:
                    tree = ast.parse(open(path, encoding="utf-8").read())
                except (SyntaxError, UnicodeDecodeError):
                    continue
                for n in ast.walk(tree):
                    if isinstance(n, ast.Call):
                        f = n.func
                        name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
                        if name == "set_config_root":
                            offenders.append(f"{os.path.relpath(path, _ROOT)}:{n.lineno}")
    assert not offenders, f"운영 코드가 설정 탐색 기준을 옮긴다: {offenders}"


# ---------------------------------------------------------------------------
# 자유 모드 은퇴 (2026-10-01) — 설정 원본은 열린 프로젝트 하나뿐, 전역 설정 파일은 없다
# ---------------------------------------------------------------------------
def test_without_open_project_reads_clean_defaults_and_refuses_saves(deploy_root):
    from shared.features import feature_config
    cm.set_active_project_ref(None)
    cfg = cm.load_project_config("CAiModelBaker")
    base = cm.load_base_config("CAiModelBaker")
    assert cfg == base, "열린 프로젝트가 없으면 깨끗한 기본값(common → defaults)만 읽어야 한다"
    with pytest.raises(ps.NoOpenProjectError):
        cm.save_project_config("CAiModelBaker", cfg)
    assert feature_config.get_config_path() is None
    with pytest.raises(ps.NoOpenProjectError):
        feature_config.save_feature_config(["target_ma_ratio"], {})


@pytest.mark.parametrize("pointer_body", ['{"ref": "069500_1m/GONE"}', '{"ref": ""}', "not-json"])
def test_broken_pointer_is_an_error_not_silent_free_mode(deploy_root, pointer_body):
    path = ps.pointer_path(cm.get_config_dir("CAiModelBaker"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(pointer_body)
    with pytest.raises(ps.BrokenProjectPointerError):
        cm.get_active_project_ref()
    with pytest.raises(ps.BrokenProjectPointerError):
        cm.load_project_config("CAiModelBaker")


def test_dashboard_reports_project_errors_instead_of_falling_back(client, deploy_root):
    c, _ = client
    cm.set_active_project_ref(None)
    r = c.post("/api/config", json={"TRAIN_START": "2026-01-01"})
    assert r.status_code == 409 and r.json().get("code") == "PROJECT_NOT_OPEN"


def test_retired_global_config_files_stay_retired():
    """전역 active.json / active_config.json 은 되살아나면 안 된다 (보관본은 config/legacy/)."""
    cfg_dir = os.path.join(_BAKER, "config")
    for name in ("active.json", "active_config.json"):
        assert not os.path.exists(os.path.join(cfg_dir, name)), f"은퇴한 전역 설정 {name} 이 되살아났다"
    # 운영 코드가 이 파일들을 이름으로 다시 찾지 않는다
    offenders = []
    for base in ("CAiModelBaker", "shared"):
        for dp, dns, fns in os.walk(os.path.join(_ROOT, base)):
            dns[:] = [d for d in dns if d not in (".venv", "__pycache__", "tests", "legacy", "scripts")]
            for fn in fns:
                if fn.endswith(".py"):
                    src = open(os.path.join(dp, fn), encoding="utf-8").read()
                    if '"active_config.json"' in src or "'active_config.json'" in src:
                        offenders.append(os.path.relpath(os.path.join(dp, fn), _ROOT))
    assert not offenders, f"은퇴한 active_config.json 을 찾는 코드: {offenders}"


def test_saving_a_project_does_not_write_its_identity_into_common_config():
    """종목·인터벌은 프로젝트 신원이다 — common.json 에 쓰면 다른 화면·프로세스가 '현재 종목' 으로 오해한다."""
    import ast
    src = open(os.path.join(_BAKER, "web_dashboard", "main.py"), encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "save_config")
    lits = {c.value for c in ast.walk(fn) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    assert not ({"SYMBOL_UPBIT", "SYMBOL_BINANCE", "INTERVAL"} & lits)
