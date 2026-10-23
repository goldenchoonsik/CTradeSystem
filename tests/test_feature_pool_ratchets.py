# -*- coding: utf-8 -*-
"""
탐색 풀 래칫 (T-7, 2026-09-28)
================================
자동연구가 **참조 피처를 후보로 보지도 못한** 사고의 재발 방지 테스트다.

사고 구조 (두 결함이 겹쳤다)
-----------------------------
1. 참조 피처(`ref_{key}_{kind}`)는 레지스트리에 **지연 등록**된다. 그런데 등록 루프를
   화면/모듈이 각자 들고 있어, "그 프로세스에서 누가 먼저 돌았는지"가 결과를 바꿨다.
   `load_feature_config()` 는 레지스트리에 없는 id 를 조용히 버리므로, 지표 화면을 먼저 연
   대시보드 프로세스에서는 저장된 참조 피처가 살아남고 **새 프로세스에서는 사라졌다**.
2. 컬럼을 만드는 쪽(피처 엔지니어링)과 후보를 고르는 쪽(IC 스크리닝)이 탐색 풀을 각자
   판단했다. '전체 지표 탐색'을 켜도 엔지니어링은 저장된 선택 지표만 계산해서, 전체 88개
   중 **5개만** 평가됐다(그중 3개는 체제 지표라 알파 후보는 2개).

두 결함 모두 "설정을 잘못 읽었다"가 아니라 **같은 결정을 두 곳이 따로 했다**는 문제다.
그래서 테스트는 값이 아니라 그 구조를 잡는다.
"""

import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_BAKER = os.path.join(_ROOT, "CAiModelBaker")
if _BAKER not in sys.path:
    sys.path.insert(0, _BAKER)


# ---------------------------------------------------------------------------
# 1. 참조 피처는 id 만으로 스스로 등록된다 (호출 순서 의존 제거)
# ---------------------------------------------------------------------------
def test_reference_feature_survives_config_load_in_a_cold_registry():
    """레지스트리가 비어 있어도(= 새 프로세스) 설정의 참조 피처가 살아남아야 한다.

    종전에는 이 상황에서 조용히 사라졌고, 그래서 자동연구는 참조 피처를 평가조차 못 했다.
    """
    from shared.features.feature_config import _normalize_feature_list
    from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY

    fid = "ref_000660_ret_1c"
    saved = DEFAULT_FEATURE_REGISTRY.pop(fid, None)  # 아무도 등록해 주지 않은 상태 재현
    try:
        assert fid not in DEFAULT_FEATURE_REGISTRY
        out = _normalize_feature_list([fid, "target_hour_cos"])
        assert fid in out, "참조 피처가 설정 로드 중 조용히 버려졌다"
    finally:
        if saved is not None:
            DEFAULT_FEATURE_REGISTRY[fid] = saved


def test_unknown_non_reference_feature_is_still_dropped():
    """자기등록은 참조 피처에만 적용된다 — 오타/폐기된 id 가 통과하면 안 된다."""
    from shared.features.feature_config import _normalize_feature_list

    assert _normalize_feature_list(["definitely_not_a_feature"]) == []
    assert _normalize_feature_list(["ref_000660_not_a_kind"]) == []


def test_configured_reference_symbols_are_registered_from_the_declaration_only():
    """선언(config.reference_symbols)에 있는 종목만, 그 선언만으로 등록된다 (종목 표 없음)."""
    from shared.features.feature_registry import (
        REFERENCE_FEATURE_KINDS, register_configured_reference_features,
    )

    registry = {}
    cfg = {"reference_symbols": [{"symbol": "000660", "name": "SK하이닉스"},
                                 {"symbol": "US_TECH_100"}]}
    ids = register_configured_reference_features(cfg, registry)

    for kind in REFERENCE_FEATURE_KINDS:
        assert f"ref_000660_{kind}" in registry
        assert f"ref_us_tech_100_{kind}" in registry
    assert len(ids) == 2 * len(REFERENCE_FEATURE_KINDS)
    # 선언하지 않은 종목은 등록되지 않는다
    assert not [k for k in registry if k.startswith("ref_005930_")]

    assert register_configured_reference_features({"reference_symbols": []}, {}) == []


# ---------------------------------------------------------------------------
# 2. 탐색 풀은 한 번만 결정된다 (엔지니어링 ↔ IC 스크리닝 일치)
# ---------------------------------------------------------------------------
def _research_loop_ast():
    path = os.path.join(_ROOT, "CAiModelBaker", "core", "research_loop.py")
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read()), path


def test_feature_engineering_is_given_the_search_pool_explicitly():
    """`run_feature_engineering` 을 feature_config 없이 부르면 저장된 선택 지표만 계산된다.

    그러면 '전체 지표 탐색'이 거짓말이 된다 — 후보 목록은 넓어지지만 컬럼이 없어
    전부 탈락한다. 연구 루프는 탐색 풀을 명시해서 넘겨야 한다.
    """
    tree, path = _research_loop_ast()
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "run_feature_engineering"]
    assert calls, f"{path} 에서 run_feature_engineering 호출을 찾지 못했다"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "feature_config" in kwargs, (
            f"{path}:{call.lineno} — run_feature_engineering 에 feature_config 를 넘기지 않으면 "
            "탐색 풀과 컬럼 생성 목록이 어긋난다")


def test_search_pool_is_decided_once():
    """탐색 풀 결정이 `run_auto_research` 안에서 두 번 일어나면 두 결정이 어긋날 수 있다.

    (연구 종료 후 설정을 저장하는 `_save_best_feature_config` 의 로드는 탐색 풀 결정이 아니라
    저장 전 원본 보존이므로 세지 않는다 — 함수 본문 단위로만 본다.)
    """
    tree, path = _research_loop_ast()
    main_fn = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "run_auto_research"), None)
    assert main_fn is not None, f"{path} 에서 run_auto_research 를 찾지 못했다"
    loads = [n for n in ast.walk(main_fn)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "load_feature_config"]
    assert len(loads) <= 1, (
        f"{path} 의 run_auto_research 가 load_feature_config 를 {len(loads)}번 호출한다 — "
        f"탐색 풀은 한 곳에서 정하고 그 값을 재사용해야 한다 (호출 줄: {[n.lineno for n in loads]})")


def test_only_the_registry_owns_the_configured_reference_registration_loop():
    """'선언된 참조 종목 전부 등록' 루프의 소유자는 feature_registry 한 곳이다.

    이 루프가 화면마다 흩어져 있었던 것이 프로세스 상태 의존의 원인이었다.
    """
    owner = os.path.join("shared", "features", "feature_registry.py")
    offenders = []
    for base, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]   # 숨김 폴더(.claude/worktrees 사본) 제외
        if any(p in base for p in (".git", "node_modules", "__pycache__", ".venv", "tests")):
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(base, fn)
            rel = os.path.relpath(path, _ROOT)
            if rel == owner:
                continue
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.For):
                    continue
                src = ast.dump(node)
                if "register_reference_features" not in src:
                    continue
                # config 선언(reference_symbols)을 순회하는 등록 루프만 문제다.
                # 데이터에 실제로 존재하는 ref_*_close 컬럼을 훑는 루프는 별개의 출처다.
                if "reference_symbols" in src or "REFERENCE_SYMBOLS" in src:
                    offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "선언된 참조 종목 등록 루프를 복제한 곳이 있다 — "
        f"register_configured_reference_features() 에 위임할 것: {offenders}")
