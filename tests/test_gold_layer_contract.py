# -*- coding: utf-8 -*-
"""
Gold Layer Single-Writer Contract Tests (Gold 계층 진실 계약 테스트)
====================================================================
[T-2, 2026-09-15] "어느 경로가 진실인가"를 코드로 강제한다.

고정하는 계약
-------------
1. `FeatureComposer.compose()` 는 순수 함수다 — 파일을 쓰지 않는다.
2. 학습 데이터셋(`data_snapshot_*.csv`) 에 쓰는 프로덕션 코드는
   `CAiModelBaker/core/data_manager.py` (배치 빌더) **하나뿐**이다.
3. `/api/data_vault/compose` (온디맨드 조립) 은 학습 데이터셋을 덮어쓰지 않고
   **미리보기 파일**(`data_vault_preview_*.csv`)에만 기록한다.
4. `FeatureVault` 는 DEPRECATED 이며 사용 시 `DeprecationWarning` 을 발생시킨다.

배경
----
· 종전에는 배치 빌더와 웹 UI 조립기가 **동일한 학습 데이터셋 경로에 이중 writer** 로 작동해,
  UI 버튼 한 번으로 틱 파생 지표·매크로·펀딩비·결측 보간·무결성 게이트가 빠진
  데이터셋으로 조용히 대체될 수 있었다.
· `FeatureVault` 는 피처 간 의존성을 해소하지 못해 생산 경로와 다른 값을 산출하는
  미배선 사본이었다 (실측 target_cvd_momentum 최대 차이 0.1385).

⚠️ 본 테스트는 정적 분석(AST/소스 스캔)만 사용한다 — 네트워크/대용량 데이터 무관, 수 초 내 완료.
"""

import os
import re
import ast

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_DATA_MANAGER_PY = os.path.join(REPO_ROOT, "CAiModelBaker", "core", "data_manager.py")
_FEATURE_COMPOSER_PY = os.path.join(REPO_ROOT, "shared", "data_library", "feature_composer.py")
_DASHBOARD_MAIN_PY = os.path.join(REPO_ROOT, "CAiModelBaker", "web_dashboard", "main.py")

# 학습 데이터셋 쓰기 허용 목록 (Gold Single Writer)
_ALLOWED_SNAPSHOT_WRITERS = {"CAiModelBaker/core/data_manager.py"}

# 레거시/1회성 도구 — **실행 차단 가드가 걸린 경우에만** 등록 허용 (사유 필수)
_RETIRED_SNAPSHOT_WRITERS = {
    "scripts/add_funding_rate.py": (
        "1회성 과거 마이그레이션. 펀딩비 로직은 파이프라인에 통합됨(data_manager.py:581). "
        "기본 실행 차단(--force 필요) 상태로만 유지 허용. (bfill 사용으로 정책 상충)"
    ),
}

# `to_csv(snapshot_path, ...)` / `to_csv(snapshot_csv, ...)` 형태의 쓰기 탐지
_SNAPSHOT_WRITE_RE = re.compile(r"to_csv\(\s*[A-Za-z_]*snap[A-Za-z_]*\s*,", re.IGNORECASE)

# 파일 쓰기 API (compose 가 순수 함수인지 판정)
_FILE_WRITE_RE = re.compile(
    r"\.to_csv\(|\.to_parquet\(|\.to_feather\(|atomic_save_|json\.dump\(|savefig\(",
)

_SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", "build", "dist",
    "scratch", "vultr_logs", "tests", ".pytest_cache", "logs", "deploy",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _iter_production_python_files(root: str = REPO_ROOT):
    """테스트/스크래치를 제외한 프로덕션 .py 파일 순회"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]   # 숨김 폴더(.claude/worktrees 사본) 제외
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def test_production_scan_skips_hidden_tool_dirs(tmp_path):
    """.claude/worktrees/ 아래 다른 체크아웃 사본은 운영 코드가 아니다 — 그 안의 data_manager.py 가 이중 writer 로 잡히면 안 된다."""
    for rel in ("CAiModelBaker/core/data_manager.py", ".claude/worktrees/x/CAiModelBaker/core/data_manager.py"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True)
        p.write_text("", encoding="utf-8")
    found = [os.path.relpath(p, tmp_path).replace(os.sep, "/") for p in _iter_production_python_files(str(tmp_path))]
    assert found == ["CAiModelBaker/core/data_manager.py"]


# ==============================================================================
# 1. FeatureComposer.compose() 는 순수 함수 (파일 쓰기 금지)
# ==============================================================================

def test_feature_composer_is_pure_no_file_write():
    """조인 조립기는 상태를 저장하지 않아야 한다 — 미리보기/검사 용도로만 존재한다."""
    src = _read(_FEATURE_COMPOSER_PY)
    hits = [m.group(0) for m in _FILE_WRITE_RE.finditer(src)]
    assert not hits, (
        f"FeatureComposer 가 파일을 쓰고 있습니다: {hits}. "
        "Gold 조인기는 순수 함수여야 하며, 저장은 호출자(엔드포인트/빌더)가 결정합니다. [T-2]"
    )


# ==============================================================================
# 2. 학습 데이터셋 단일 writer 계약
# ==============================================================================

def test_training_snapshot_has_single_writer():
    """`data_snapshot_*.csv` 에 쓰는 **활성** 프로덕션 코드는 배치 빌더 하나뿐이어야 한다."""
    offenders = []
    for path in _iter_production_python_files():
        rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
        if not _SNAPSHOT_WRITE_RE.search(_read(path)):
            continue
        if rel in _ALLOWED_SNAPSHOT_WRITERS or rel in _RETIRED_SNAPSHOT_WRITERS:
            continue
        offenders.append(rel)

    assert not offenders, (
        "학습 데이터셋(data_snapshot_*.csv) 에 대한 이중 writer 가 발견되었습니다: "
        f"{offenders}. Gold 계층의 학습 데이터셋 생산자는 "
        "CAiModelBaker/core/data_manager.py::run_dataset_build_pipeline 하나뿐이어야 합니다. [T-2]\n"
        "  · 일회성 도구라면 실행 차단 가드(--force)를 추가한 뒤 "
        "tests/test_gold_layer_contract.py 의 _RETIRED_SNAPSHOT_WRITERS 에 사유와 함께 등록하세요."
    )


def test_retired_snapshot_writer_is_guarded():
    """은퇴 등록된 스크립트는 반드시 기본 실행 차단 가드를 갖춰야 한다."""
    for rel, reason in _RETIRED_SNAPSHOT_WRITERS.items():
        path = os.path.join(REPO_ROOT, rel)
        assert os.path.exists(path), f"은퇴 등록 파일이 존재하지 않습니다: {rel}"
        src = _read(path)
        assert "RETIRED" in src, f"{rel} 에 은퇴 표기가 없습니다. (사유: {reason})"
        assert "--force" in src, f"{rel} 에 기본 실행 차단(--force) 가드가 없습니다. (사유: {reason})"
        assert "force: bool" in src or "force=True" in src or "force=" in src, \
            f"{rel} 의 main() 이 force 인자를 받지 않습니다."


def test_single_writer_allowlist_is_still_valid():
    """허용 목록이 유효한지(파일이 실제로 존재하고 쓰기 코드가 있는지) 검증 — 계약의 자기검증."""
    assert os.path.exists(_DATA_MANAGER_PY), "배치 빌더 파일이 존재해야 함"
    assert _SNAPSHOT_WRITE_RE.search(_read(_DATA_MANAGER_PY)), (
        "허용 목록에 등록된 배치 빌더에서 스냅샷 쓰기 코드를 찾지 못했습니다. "
        "경로 해석 방식이 변경되었다면 본 테스트의 패턴도 함께 갱신하세요."
    )


# ==============================================================================
# 3. 온디맨드 조립 엔드포인트는 학습 데이터셋을 덮어쓰지 않는다
# ==============================================================================

def _find_function_node(tree, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def test_compose_endpoint_does_not_overwrite_training_snapshot():
    """`/api/data_vault/compose` 의 저장 대상이 학습 데이터셋이 아니어야 한다."""
    src = _read(_DASHBOARD_MAIN_PY)
    tree = ast.parse(src)

    fn = _find_function_node(tree, "api_compose_features")
    assert fn is not None, "api_compose_features 엔드포인트를 찾지 못했습니다"

    to_csv_calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "to_csv"
    ]
    assert to_csv_calls, "온디맨드 조립 결과를 저장하는 코드가 없습니다(미리보기 계약 위반 검토 필요)"

    _TRAINING_PATH_TOKENS = ("get_snapshot_path", "snap_path", "snapshot_path", "snapshot_csv")
    for call in to_csv_calls:
        target = (ast.get_source_segment(src, call.args[0]) if call.args else "") or ""
        hit = [t for t in _TRAINING_PATH_TOKENS if t in target]
        assert not hit, (
            f"온디맨드 조립이 학습 데이터셋 경로에 기록하고 있습니다: to_csv({target}) "
            f"(탐지 토큰: {hit}) — 틱 파생 지표/매크로/무결성 게이트가 빠진 데이터셋으로 "
            "학습 입력이 조용히 대체됩니다. [T-2]"
        )

    body_src = ast.get_source_segment(src, fn) or ""
    assert "data_vault_preview" in body_src, (
        "온디맨드 조립은 미리보기 파일(data_vault_preview_*.csv)로 분리 기록해야 합니다. [T-2]"
    )


# ==============================================================================
# 4. FeatureVault 폐기 계약
# ==============================================================================

def test_feature_vault_emits_deprecation_warning():
    """미배선 사본(FeatureVault)은 사용 시 명시적 경고를 발생시켜야 한다."""
    from shared.data_library.feature_vault import FeatureVault

    with pytest.warns(DeprecationWarning):
        FeatureVault()   # 실제 cache 디렉토리 생성만 하며 연산은 수행하지 않음


def test_feature_vault_is_not_used_in_production_paths():
    """프로덕션 코드가 FeatureVault 를 **의존(import/생성)** 하지 않아야 한다.

    주석·문서에서의 단순 언급은 허용하되, 실제 의존은 금지한다.
    (하위 호환 재수출 지점인 shared/data_library/__init__.py 만 예외)
    """
    dep_re = re.compile(
        r"from\s+shared\.data_library\.feature_vault\s+import"
        r"|shared\.data_library\.feature_vault\s+import"
        r"|FeatureVault\s*\("
    )
    allowed = {"shared/data_library/__init__.py"}
    definition = "shared/data_library/feature_vault.py"

    offenders = []
    for path in _iter_production_python_files():
        rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
        if rel in allowed or rel == definition:
            continue
        if dep_re.search(_read(path)):
            offenders.append(rel)

    assert not offenders, (
        f"프로덕션 코드가 DEPRECATED FeatureVault 에 의존하고 있습니다: {offenders}. "
        "Gold 피처 계산은 shared.feature_engine.run_feature_engineering 단일 경로를 사용하세요. [T-2]"
    )


def test_composer_does_not_instantiate_feature_vault():
    """FeatureComposer 는 FeatureVault 를 생성하지 않아야 한다 (미배선 사본 제거 계약)."""
    from shared.data_library.feature_composer import FeatureComposer

    import inspect
    src = inspect.getsource(FeatureComposer.__init__)
    assert "FeatureVault(" not in src, (
        "FeatureComposer.__init__ 이 FeatureVault 를 생성하고 있습니다 — 미배선 사본이 부활합니다. [T-2]"
    )
    assert "feature_vault or FeatureVault" not in src


# ==============================================================================
# 5. [D-9] 데이터셋 빌더의 bfill 금지 (Point-in-Time 무결성 규칙 ①)
# ==============================================================================

def test_dataset_builder_has_no_bfill_call():
    """데이터셋 빌더는 결측을 미래값으로 채우지 않아야 한다.

    ARCHITECTURE.md PIT-1 — `bfill()` 전면 금지.
    종전에는 데이터셋 빌더에 `ffill().bfill()` 이 43곳 있었고, 일자별 head / 참조 원천
    공백 구간이 **미래값으로 역류**했다 (실측: ref_449190_close 가 26일 역류).
    주석 내 문자열은 허용하고 실제 호출만 금지한다 (AST 검사).
    """
    src = _read(_DATA_MANAGER_PY)
    tree = ast.parse(src)
    bfill_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "bfill"
    ]
    line_hint = f" (첫 발생 L{bfill_calls[0].lineno})" if bfill_calls else ""
    assert not bfill_calls, (
        f"데이터셋 빌더에 bfill 호출이 재도입되었습니다({len(bfill_calls)}건){line_hint}. "
        "bfill 은 결측을 미래값으로 채워 학습 데이터에 Lookahead 를 유입시킵니다. "
        "head 결측은 NaN 으로 두고, 일자 경계는 연속 프레임에서 ffill 하세요. [D-9]"
    )


def test_data_catalog_manager_has_no_bfill_call():
    """결측 보간 엔진(DataCatalogManager)도 bfill 을 쓰지 않아야 한다 (규칙 ①)."""
    dcm_path = os.path.join(REPO_ROOT, "shared", "data_library", "data_catalog_manager.py")
    tree = ast.parse(_read(dcm_path))
    bfill_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "bfill"
    ]
    assert not bfill_calls, (
        f"DataCatalogManager 에 bfill 호출이 재도입되었습니다({len(bfill_calls)}건). "
        "카탈로그 미등록 컬럼의 head 결측이 미래값으로 역류합니다. [D-8]"
    )
