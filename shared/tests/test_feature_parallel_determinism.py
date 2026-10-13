"""
M-3 회귀 테스트: 피처 계산 결정론 (의존성 위상 레벨 스케줄링)

기존 병렬 경로는 피처 간 의존성(예: cvd_momentum ← trade_bid_ratio)을 무시한 채
동일 df 에서 동시 계산하여 순차 경로와 값이 달라질 수 있었다(비결정적).
`_execute_feature_tasks` 는 의존성 위상(레벨) 순으로 실행하여 순차/병렬 결과를
100% 동일하게 만든다.
"""
import numpy as np
import pandas as pd

from shared.features.feature_engine import _execute_feature_tasks, _raw_feature_refs
from shared.features.feature_registry import _get_series, DEFAULT_FEATURE_REGISTRY


# ── 테스트용 가짜 피처 (f_base → f_dep 의존) ──
def _c_base(df, params, interval):
    return df["x"] * 2.0


def _c_dep(df, params, interval):
    return _get_series(df, "f_base") + 1.0


def _tasks():
    return [
        ("f_dep", {"compute": _c_dep}, {}),
        ("f_base", {"compute": _c_base}, {}),
    ]


def test_parallel_equals_sequential_with_dependency():
    df_seq = pd.DataFrame({"x": np.arange(100, dtype=float)})
    df_par = df_seq.copy()
    _execute_feature_tasks(df_seq, _tasks(), "1m", parallel=False)
    _execute_feature_tasks(df_par, _tasks(), "1m", parallel=True)
    assert np.array_equal(df_seq["f_base"].values, df_par["f_base"].values)
    assert np.array_equal(df_seq["f_dep"].values, df_par["f_dep"].values)
    # 의존 피처가 선행 피처를 정상 참조했는지 (NaN 아님)
    assert np.allclose(df_seq["f_dep"].values, df_seq["f_base"].values + 1.0)


def test_cycle_does_not_hang():
    def _a(df, params, interval):
        return _get_series(df, "c") + 1.0

    def _b(df, params, interval):
        return _get_series(df, "a") + 1.0

    def _c(df, params, interval):
        return _get_series(df, "b") + 1.0

    tasks = [(n, {"compute": f}, {}) for n, f in (("a", _a), ("b", _b), ("c", _c))]
    df = pd.DataFrame({"x": np.arange(10, dtype=float)})
    _execute_feature_tasks(df, tasks, "1m", parallel=True)  # 무한루프 없이 종료
    assert "a" in df.columns


def test_real_registry_dependency_detected():
    """실제 레지스트리에서 cvd/obv 계열이 trade_bid_ratio 에 의존함을 확인."""
    for fid in ("target_cvd_momentum", "target_obv_momentum", "target_cvd_absorption"):
        refs = _raw_feature_refs(DEFAULT_FEATURE_REGISTRY[fid]["compute"])
        assert "target_trade_bid_ratio" in refs, f"{fid} 의존성 미검출"
