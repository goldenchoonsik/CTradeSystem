"""
Low Priority Part 2 회귀 테스트 (L-7, L-8, H-4)

L-7: cross_validation.run_baker_simulation 매크로 필터 반영
L-8: scripts/run_tests.sh 스위트 분리 실행
H-4: web_dashboard 백테스트 루프 resolve_exit 통일(비상손절 포함, 영문 라벨 제거)
"""
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(rel):
    return open(os.path.join(REPO_ROOT, rel), encoding="utf-8").read()


def test_l7_cross_validation_macro_filter():
    src = _read("shared/tests/cross_validation.py")
    assert "_macro_ok" in src
    assert "MACRO_RSI_FILTER_THRESHOLD" in src


def test_l8_run_tests_script_separates_suites():
    src = _read("scripts/run_tests.sh")
    assert "CAiModelBaker/tests" in src
    # 분리 실행 구조 확인 (두 번의 pytest 호출)
    assert src.count('"$PY" -v') >= 2


def test_h4_web_dashboard_uses_resolve_exit_and_korean_labels():
    src = _read("CAiModelBaker/web_dashboard/main.py")
    assert "resolve_exit(" in src
    for en in ("'exit_type': 'StopLoss'", "'exit_type': 'TakeProfit'",
               "'exit_type': 'ForceExit'", "exit_type = 'Timeout'"):
        assert en not in src, f"영문 라벨 잔존: {en}"


def test_h4_web_dashboard_has_low_column():
    src = _read("CAiModelBaker/web_dashboard/main.py")
    assert "low_col = 'target_low'" in src
