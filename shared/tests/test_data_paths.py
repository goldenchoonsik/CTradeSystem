# -*- coding: utf-8 -*-
"""
Data Paths SSOT 테스트 (T-5 / D-3)
===================================
데이터·배포 경로 해석이 **단일 진실 공급원**(`shared/data_library/paths.py`)을 경유하고,
**CWD(실행 위치)에 의존하지 않음**을 고정한다.

배경
----
종전에는 `CAiModelBaker/core/data_manager.py` 등이 `f"data/..."` CWD 상대경로를 사용했다.
운영 스크립트가 `cd CAiModelBaker` 후 실행하므로 정상이었지만, 저장소 루트에서 실행하면
쓰기가 `<repo>/data/...` 로 흘러나가 **데이터 루트가 2개로 분리**되었다.
(실측: 루트 `data/` 에 고아 32파일 3.5MB — Bronze 원천이 두 곳에 쌓임)
"""

import os
import re
import subprocess
import sys

import pytest

from shared.data_library import paths as dpaths

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BAKER_ROOT = os.path.join(REPO_ROOT, "CAiModelBaker")

_SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", "build", "dist",
    "scratch", "vultr_logs", "tests", ".pytest_cache", "logs",
}


def _iter_production_py():
    for dirpath, dirnames, filenames in os.walk(BAKER_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, "shared")):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


# ==============================================================================
# 1. 루트 해석 계약
# ==============================================================================

def test_roots_are_absolute_and_nested():
    assert os.path.isabs(dpaths.PROJECT_ROOT)
    assert os.path.isabs(dpaths.BAKER_ROOT)
    assert os.path.isabs(dpaths.DATA_ROOT)
    assert os.path.isabs(dpaths.DEPLOY_ROOT)

    assert dpaths.BAKER_ROOT == os.path.join(dpaths.PROJECT_ROOT, "CAiModelBaker")
    assert dpaths.DATA_ROOT == os.path.join(dpaths.BAKER_ROOT, "data")
    # 운영 기본값. 테스트 세션은 conftest 가 DEPLOY_ROOT 를 사본 폴더로 돌려 두므로(실제 deploy/ 보호)
    # 현재 값이 아니라 모듈이 정하는 기본값을 확인한다.
    import importlib.util
    spec = importlib.util.spec_from_file_location("_paths_fresh", dpaths.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    if not os.environ.get("CTRADE_DEPLOY_ROOT"):
        assert fresh.DEPLOY_ROOT == os.path.join(dpaths.BAKER_ROOT, "deploy")


def test_data_path_and_deploy_path():
    assert dpaths.data_path("historical", "kis", "candle_1m") == \
        os.path.join(dpaths.DATA_ROOT, "historical", "kis", "candle_1m")
    assert dpaths.deploy_path("069500_10s", "S1", "model") == \
        os.path.join(dpaths.DEPLOY_ROOT, "069500_10s", "S1", "model")
    # 빈/None 조각은 무시된다
    assert dpaths.data_path("a", None, "", "b") == os.path.join(dpaths.DATA_ROOT, "a", "b")


# ==============================================================================
# 2. Gold / Artifact 경로 계약
# ==============================================================================

def test_snapshot_paths_are_absolute():
    snap = dpaths.snapshot_path("069500", "1m")
    assert os.path.isabs(snap)
    assert snap.endswith("069500/data_snapshot_1m.csv")
    expected_dir = (
        os.path.dirname(dpaths.gold_snapshot_path("069500", "1m"))
        if os.path.exists(dpaths.gold_snapshot_path("069500", "1m"))
        else os.path.join(dpaths.DATA_ROOT, "069500")
    )
    assert os.path.dirname(snap) == expected_dir


def test_snapshot_meta_path_is_sidecar_of_snapshot():
    snap = dpaths.snapshot_path("BTC", "10s")
    meta = dpaths.snapshot_meta_path("BTC", "10s")
    assert meta == snap.replace(".csv", ".meta.json")


def test_preview_path_is_separate_from_training_dataset():
    """[T-2 Gold 계약] 미리보기는 학습 데이터셋과 다른 파일이어야 한다."""
    assert dpaths.data_vault_preview_path("BTC", "10m") != dpaths.snapshot_path("BTC", "10m")
    assert "data_vault_preview" in dpaths.data_vault_preview_path("BTC", "10m")


def test_params_and_model_paths_session_vs_legacy():
    coin, intv, sess = "069500", "10s", "KODEX200_S10_BEST1"

    # 세션 지정 → deploy(Artifact) 계층
    assert dpaths.best_params_path(coin, intv, sess) == os.path.join(
        dpaths.DEPLOY_ROOT, f"{coin}_{intv}", sess, "model", f"best_params_{intv}.json")
    assert dpaths.model_file_path(coin, intv, sess) == os.path.join(
        dpaths.DEPLOY_ROOT, f"{coin}_{intv}", sess, "model", f"model_{intv}.txt")

    # 세션 미지정 → data 계층 (레거시 배치 경로)
    assert dpaths.best_params_path(coin, intv) == \
        os.path.join(dpaths.DATA_ROOT, coin, f"best_params_{intv}.json")
    assert dpaths.model_file_path(coin, intv) == \
        os.path.join(dpaths.DATA_ROOT, coin, f"model_{intv}.txt")


def test_session_and_model_dir():
    coin, intv, sess = "069500", "10s", "S1"
    assert dpaths.session_dir(coin, intv, sess) == \
        os.path.join(dpaths.DEPLOY_ROOT, f"{coin}_{intv}", sess)
    assert dpaths.model_dir(coin, intv, sess) == \
        os.path.join(dpaths.DEPLOY_ROOT, f"{coin}_{intv}", sess, "model")


def test_display_renders_project_relative():
    assert dpaths.display(dpaths.snapshot_path("BTC", "1m")) == \
        "CAiModelBaker/data/BTC/data_snapshot_1m.csv"
    # 프로젝트 밖 경로는 원본 유지
    outside = "/tmp/outside/file.csv"
    assert dpaths.display(outside) == outside


# ==============================================================================
# 3. 실제 자산이 해석되는가 (repo 상태 기반)
# ==============================================================================

@pytest.mark.skip(reason="정리된 구 모델/변경된 shared 설정 기준 테스트 - 현행 설정에 맞게 재작성 필요")
def test_known_assets_resolve_to_existing_paths():
    assert os.path.exists(dpaths.model_dir("069500", "10s", "KODEX200_S10_BEST1")), \
        "실제 deploy 세션 디렉토리가 해석되지 않습니다"
    assert os.path.exists(dpaths.data_path("historical", "overseas_futures"))
    if os.path.exists(dpaths.snapshot_path("069500", "1m")):
        assert os.path.isabs(dpaths.snapshot_path("069500", "1m"))


# ==============================================================================
# 4. CWD 독립성 (실행 위치와 무관하게 동일 경로)
# ==============================================================================

@pytest.mark.parametrize("cwd", ["/", "/tmp", str(REPO_ROOT)])
def test_paths_are_cwd_independent(cwd):
    """어느 CWD 에서 실행해도 같은 절대경로를 반환해야 한다 (D-3 핵심)."""
    env = dict(os.environ, PYTHONPATH=REPO_ROOT)
    code = (
        "from shared.data_library import paths as p;"
        "print('SNAP=' + p.snapshot_path('069500','1m'));"
        "print('DATA=' + p.DATA_ROOT)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    lines = dict(l.split("=", 1) for l in proc.stdout.splitlines() if "=" in l and l.startswith(("SNAP=", "DATA=")))
    assert lines["SNAP"] == dpaths.snapshot_path("069500", "1m"), f"CWD={cwd} 에서 경로가 달라졌습니다"
    assert lines["DATA"] == dpaths.DATA_ROOT


def test_data_root_env_override(tmp_path):
    """`CTRADE_DATA_ROOT` 로 데이터 루트를 재정의할 수 있어야 한다 (배포 유연성)."""
    env = dict(os.environ, PYTHONPATH=REPO_ROOT, CTRADE_DATA_ROOT=str(tmp_path))
    proc = subprocess.run(
        [sys.executable, "-c",
         "from shared.data_library import paths as p; print('ROOT=' + p.DATA_ROOT)"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert f"ROOT={tmp_path}" in proc.stdout


# ==============================================================================
# 5. [D-3 재발 방지] 프로덕션 코드에 CWD 상대 'data/' 리터럴 금지
# ==============================================================================

def _cwd_relative_fstrings(src: str, prefixes=("data/", "deploy/")):
    """CWD 상대 경로 f-string 라인을 수집한다 (AST 기반 — docstring 오탐 없음).

    `os.path.join(_BAKER_ROOT, f"data/...")` 처럼 **절대 루트에 결합된 경우는 허용**한다
    (이미 실행 위치와 무관). 반면 `x = f"data/..."` 나 `os.path.join(상대변수, f"deploy/...")`
    처럼 홀로 쓰인 경우는 CWD 상대 → 위반.
    """
    import ast

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    def _starts_rel(node):
        return (isinstance(node, ast.JoinedStr) and node.values
                and isinstance(node.values[0], ast.Constant)
                and isinstance(node.values[0].value, str)
                and node.values[0].value.startswith(prefixes))

    # 절대 루트에 결합된 f-string 을 허용 목록에 등록
    allowed = set()
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not call.args:
            continue
        fn = call.func
        is_os_path_join = (
            isinstance(fn, ast.Attribute) and fn.attr == "join"
            and isinstance(fn.value, ast.Attribute) and fn.value.attr == "path"
        )
        if not is_os_path_join:
            continue
        root_src = ast.get_source_segment(src, call.args[0]) or ""
        looks_absolute_root = (
            "ROOT" in root_src or "dpaths" in root_src
            or root_src.lstrip().startswith(("'/'", '"/'))
        )
        if not looks_absolute_root:
            continue
        for arg in call.args[1:]:
            if _starts_rel(arg):
                allowed.add(id(arg))

    return [(n.lineno, n.values[0].value + "...")
            for n in ast.walk(tree) if _starts_rel(n) and id(n) not in allowed]


def test_no_cwd_relative_data_literal_in_production():
    """`f"data/...` 같은 CWD 상대 경로 f-string 이 남아 있으면 안 된다 (문서 예시는 허용).

    ⚠️ AST 기반 검사 — docstring/주석에 적힌 예시 문자열은 결함이 아니다.
    """
    offenders = []
    for path in _iter_production_py():
        rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                src = f.read()
        except Exception:
            continue
        for ln, snippet in _cwd_relative_fstrings(src):
            offenders.append(f"{rel}:{ln}: {snippet}")

    assert not offenders, (
        "CWD 상대 'data/'·'deploy/' 경로 f-string 이 남아 있습니다 "
        "(실행 위치에 따라 데이터 루트가 분기됨). "
        "`shared.data_library.paths` 로 해석하세요:\n  " + "\n  ".join(offenders[:10])
    )


def test_cwd_relative_detector_catches_old_pattern():
    """탐지기 자기검증 — 종전 패턴을 실제로 잡아내는지 확인."""
    old = 'csv_dir = f"data/historical/binance_extracted/{symbol}"\n'
    assert _cwd_relative_fstrings(old), "탐지기가 종전 CWD 상대 패턴을 놓쳤습니다"
    # docstring 안 예시는 잡지 않아야 한다
    doc_only = '"""예: f"data/foo/bar" 같은 상대경로를 쓰지 말 것"""\n'
    assert not _cwd_relative_fstrings(doc_only), "docstring 예시를 오탐했습니다"


def test_paths_module_is_the_only_resolver():
    """데이터 루트 상수를 직접 조립하지 않고 paths 모듈을 사용하는지(대표 지점) 확인."""
    dm = os.path.join(BAKER_ROOT, "core", "data_manager.py")
    with open(dm, "r", encoding="utf-8") as f:
        src = f.read()
    assert "from shared.data_library import paths as dpaths" in src
    assert "_DATA_ROOT = dpaths.DATA_ROOT" in src
    assert not _cwd_relative_fstrings(src), "data_manager.py 에 CWD 상대 경로 f-string 이 남아 있습니다"


def test_datavault_3tier_paths():
    """DataVault 3단계 (Raw / Normalized / Features) 경로 해석 및 무결성 검증."""
    assert os.path.isabs(dpaths.DATA_VAULT_ROOT)
    assert dpaths.DATA_VAULT_ROOT.endswith("DataVault") or dpaths.DATA_VAULT_ROOT.endswith("data_vault")
    
    # 3단계 표준 루트
    assert os.path.isabs(dpaths.raw_root())
    assert os.path.isabs(dpaths.normalized_root())
    assert os.path.isabs(dpaths.features_root())

    # Raw 경로 (크레온 틱)
    r_path = dpaths.raw_path("creon", "tick", "A069500_tick.csv")
    assert os.path.isabs(r_path)
    assert "raw" in r_path

    # Normalized 경로 (10s 캔들 Parquet)
    n_path = dpaths.normalized_path("candles_10s", "069500_10s.parquet")
    assert os.path.isabs(n_path)
    assert "normalized/candles_10s" in n_path

    # Features 경로
    f_path = dpaths.features_path("069500", "data_snapshot_10s.csv")
    assert os.path.isabs(f_path)
    assert "features/069500" in f_path

    # 하위 호환성 헬퍼 직결 검증 (Bronze/Silver/Gold -> Raw/Normalized/Features)
    assert dpaths.bronze_root() == dpaths.raw_root()
    assert dpaths.silver_root() == dpaths.normalized_root()
    assert dpaths.gold_root() == dpaths.features_root()
    assert dpaths.gold_snapshot_path("069500", "10s") == f_path


