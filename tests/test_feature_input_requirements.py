# -*- coding: utf-8 -*-
"""
지표 입력 사전검사 래칫 (2026-10-02)
=====================================
외부/참조 데이터에 의존하는 지표가 입력 컬럼이 없을 때 `df.get(col, 기본값)` 으로 조용히
대체값을 넣고 계산을 강행하던 결함의 재발 방지 테스트다.

사고: 069500(바이낸스 없음) 프로젝트 KODEX200_M1_TEST2 의 enabled_features 에 kimchi_premium 이
들어가 있었다. binance_spot_close 가 0 으로 대체되면서 결과가 `target_close * 1e8 - 1` — 상수도 NaN 도
아닌 **타겟의 선형 복제**가 되어 사후 통계 가드(find_dead_features)를 통과했다.
출력값만으로는 오염을 구별할 수 없으므로, 입력을 계산 **전에** 본다.

테스트는 두 층이다.
1. 구조 래칫: compute 소스가 읽는 외부 컬럼은 전부 FEATURE_INPUT_REQUIREMENTS 에 선언돼 있어야 하고,
   선언된 지표의 compute 는 (별칭 포함) 사전검사 래퍼로 감싸져 있어야 한다.
2. 행동: 입력이 없으면 전부 NaN, 있으면 정상 계산, 실전 수집기의 0.0 채움도 '없음'으로 본다.
"""

import inspect
import os
import re
import sys

import numpy as np
import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.features import feature_registry as fr
from shared.features.feature_registry import (
    DEFAULT_FEATURE_REGISTRY, FEATURE_ALIASES, FEATURE_INPUT_REQUIREMENTS, FEATURE_OPTIONAL_INPUTS,
    find_missing_inputs,
)


# ---------------------------------------------------------------------------
# 1. 구조 래칫
# ---------------------------------------------------------------------------
# 타겟 자신의 컬럼 — 타겟은 항상 결합되므로 원장 선언 대상이 아니다
_TARGET_COL = re.compile(r"^(target_|upbit_)")
_TARGET_PHYSICAL = set(fr._NORMALIZED_STD_COLS.values()) | {"timestamp"}

_COL_PATTERNS = (
    re.compile(r"\bdf\.get\(\s*['\"]([a-z0-9_]+)['\"]"),
    re.compile(r"\bdf\[\s*['\"]([a-z0-9_]+)['\"]\s*\]"),
    re.compile(r"['\"]([a-z0-9_]+)['\"]\s+in\s+df\.columns"),
    re.compile(r"_get_series\(\s*df\s*,\s*['\"]([a-z0-9_]+)['\"]"),
    re.compile(r"_get_series\(\s*df\s*,\s*['\"][a-z0-9_]+['\"]\s*,\s*['\"]([a-z0-9_]+)['\"]"),
)


def _source_with_helpers(fn) -> str:
    """compute 소스 + 그 안에서 df 를 넘겨 부르는 모듈 내 헬퍼 소스 (래퍼는 벗긴다)."""
    src = inspect.getsource(inspect.unwrap(fn))
    for helper in set(re.findall(r"\b(_[a-z0-9_]+)\(\s*df\b", src)):
        if helper in ("_get_series",) or helper.startswith("_compute"):
            continue
        h = getattr(fr, helper, None)
        if callable(h):
            src += inspect.getsource(h)
    return src


def _external_columns(fn) -> set:
    src = _source_with_helpers(fn)
    cols = set()
    for pat in _COL_PATTERNS:
        cols |= set(pat.findall(src))
    return {c for c in cols if not _TARGET_COL.match(c) and c not in _TARGET_PHYSICAL}


def _canonical_features():
    for fid, fdef in DEFAULT_FEATURE_REGISTRY.items():
        if fdef.get("is_alias") or not callable(fdef.get("compute")):
            continue
        if fr.parse_reference_feature_id(fid):
            continue  # 참조 피처(ref_{key}_*)는 입력 부재 시 NaN 을 돌려주도록 이미 구현돼 있다 (T-6)
        yield fid, fdef


def test_every_external_column_read_by_a_compute_is_declared():
    """compute 가 외부 컬럼을 읽으면 그 컬럼은 해당 지표의 입력 원장에 선언돼 있어야 한다.

    새 지표가 `df.get('어떤_외부_컬럼', 0.0)` 을 쓰면서 선언을 빠뜨리면 여기서 걸린다.
    """
    undeclared = {}
    for fid, fdef in _canonical_features():
        ext = _external_columns(fdef["compute"])
        if not ext:
            continue
        declared = set(FEATURE_OPTIONAL_INPUTS.get(fid, ()))
        for need in FEATURE_INPUT_REQUIREMENTS.get(fid, []):
            declared |= set(need.cols)
        missing = ext - declared
        if missing:
            undeclared[fid] = sorted(missing)
    assert not undeclared, (
        "외부 컬럼을 읽지만 FEATURE_INPUT_REQUIREMENTS 에 선언되지 않은 지표: "
        f"{undeclared} — 입력이 없을 때 대체값으로 계산이 강행된다"
    )


def test_declared_features_are_guarded_including_aliases():
    unguarded = []
    for fid, fdef in DEFAULT_FEATURE_REGISTRY.items():
        canon = FEATURE_ALIASES.get(fid, fid) if fdef.get("is_alias") else fid
        if canon in FEATURE_INPUT_REQUIREMENTS and not getattr(fdef["compute"], "input_guarded", False):
            unguarded.append(fid)
    assert not unguarded, f"입력 사전검사 래퍼가 없는 지표: {unguarded}"


def test_requirements_table_is_json_free_of_registry():
    """원장은 레지스트리 항목 밖에 둔다 — 레지스트리 항목은 대시보드 API 로 JSON 직렬화된다."""
    for fid in FEATURE_INPUT_REQUIREMENTS:
        assert "requires" not in DEFAULT_FEATURE_REGISTRY[fid]


# ---------------------------------------------------------------------------
# 2. 행동
# ---------------------------------------------------------------------------
def _krx_like_frame(n=300, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2026-09-01 09:00", periods=n, freq="1min")
    close = 35000 + np.cumsum(rng.normal(0, 20, n))
    return pd.DataFrame({
        "timestamp": ts, "target_close": close, "target_open": close, "target_high": close + 5,
        "target_low": close - 5, "target_volume": rng.integers(100, 1000, n).astype(float),
    })


def test_kimchi_premium_without_binance_is_nan_not_target_clone():
    """사고 재현: 바이낸스가 없으면 예전엔 target*1e8-1 이었다 — 이제는 전부 NaN."""
    df = _krx_like_frame()
    fn = DEFAULT_FEATURE_REGISTRY["kimchi_premium"]["compute"]
    params = {"window_mins": 1440}

    raw = inspect.unwrap(fn)(df, params, "1m")
    clone = df["target_close"] * 1e8 - 1.0
    assert np.allclose(raw, clone, rtol=1e-6), "사고 전제(대체값이 타겟 복제를 만든다)가 바뀌었다"

    guarded = fn(df, params, "1m")
    assert guarded.isna().all()


def test_kimchi_premium_with_binance_is_computed():
    df = _krx_like_frame()
    df["binance_spot_close"] = 25.0 + np.linspace(0, 1, len(df))
    out = DEFAULT_FEATURE_REGISTRY["kimchi_premium"]["compute"](df, {"window_mins": 1440}, "1m")
    assert out.notna().all()


def test_live_collector_zero_fill_counts_as_missing():
    """실전 수집기는 미수신 바이낸스 가격을 0.0 으로 채운다 — 가격 0 은 관측이 아니다."""
    df = _krx_like_frame()
    df["binance_spot_close"] = 0.0
    assert find_missing_inputs("kimchi_premium", df) == ["binance_spot_close"]
    assert find_missing_inputs("binance_ret_5m", df) == ["binance_spot_close"]


def test_alias_id_resolves_to_canonical_requirements():
    df = _krx_like_frame()
    assert find_missing_inputs("upbit_usdt_return", df) == ["crypto_usdt_close|upbit_usdt_close"]


def test_ref_type_requirement_reads_ledger_market_type():
    """KOSPI 베이시스는 원장 유형(KRX_FUTURES/KRX_INDEX) 참조 컬럼으로도 충족된다."""
    from shared.config.config_manager import _read_registry_raw, ref_key
    raw = _read_registry_raw()
    fut = next((s for s, v in raw.items() if isinstance(v, dict) and not v.get("catalog")
                and str(v.get("market_type", "")).startswith("KRX_FUTURES")), None)
    idx = next((s for s, v in raw.items() if isinstance(v, dict) and not v.get("catalog")
                and str(v.get("market_type", "")).startswith("KRX_INDEX")), None)
    if not fut or not idx:
        pytest.skip("원장에 KRX_FUTURES/KRX_INDEX 종목이 없다")
    df = _krx_like_frame()
    assert len(find_missing_inputs("kospi_basis", df)) == 2
    df[f"ref_{ref_key(fut)}_close"] = 350.0
    df[f"ref_{ref_key(idx)}_close"] = 349.0
    assert find_missing_inputs("kospi_basis", df) == []


def test_feature_engine_skips_and_reports_missing_inputs():
    from shared.features.feature_engine import run_feature_engineering
    df = _krx_like_frame()
    fc = {"enabled_features": ["kimchi_premium", "target_ma_ratio"], "feature_params": {}}
    out = run_feature_engineering(df, "1m", feature_config=fc)
    assert out["kimchi_premium"].isna().all()
    assert out["target_ma_ratio"].notna().all()
    assert out.attrs["feature_input_gaps"]["kimchi_premium"]["missing"] == ["binance_spot_close"]


def test_feature_engine_krx_per_day_gap_is_day_scoped():
    """KRX 일별 연산: 바이낸스가 둘째 날에만 있으면 첫째 날만 NaN 이다."""
    from shared.features.feature_engine import run_feature_engineering
    d1 = _krx_like_frame(120, seed=1)
    d2 = _krx_like_frame(120, seed=2)
    d2["timestamp"] = d2["timestamp"] + pd.Timedelta(days=1)
    d1["binance_spot_close"] = np.nan
    d2["binance_spot_close"] = 25.0
    df = pd.concat([d1, d2], ignore_index=True)
    fc = {"enabled_features": ["kimchi_premium"], "feature_params": {}}
    out = run_feature_engineering(df, "1m", feature_config=fc, config={"SYMBOL_UPBIT": "069500"})
    day = pd.to_datetime(out["timestamp"]).dt.date
    first, second = sorted(day.unique())
    assert out.loc[day == first, "kimchi_premium"].isna().all()
    assert out.loc[day == second, "kimchi_premium"].notna().all()
    assert out.attrs["feature_input_gaps"]["kimchi_premium"]["segments"] == 1


def test_dead_feature_reason_names_the_missing_input():
    from shared.features.feature_health import find_dead_features
    df = _krx_like_frame()
    df["kimchi_premium"] = DEFAULT_FEATURE_REGISTRY["kimchi_premium"]["compute"](df, {}, "1m")
    dead = find_dead_features(df, ["kimchi_premium"])
    assert "binance_spot_close" in dead["kimchi_premium"]


# ---------------------------------------------------------------------------
# 3. 행 단위 마스킹 — 일부 구간만 입력이 없는 경우
# ---------------------------------------------------------------------------
def test_leading_zero_fill_rows_become_nan_not_fake_values():
    """실측 재현: KRW-BTC binance_futures_oi 는 수집 시작 전 99% 가 0 채움이었다.
    컬럼 단위 검사는 실값 몇 행 때문에 통과하므로, 0 채움 행의 출력은 행 단위로 NaN 이어야 한다."""
    df = _krx_like_frame(200)
    oi = np.where(np.arange(200) < 150, 0.0, 1e5 + np.arange(200) * 10.0)
    df["binance_futures_oi"] = oi
    fn = DEFAULT_FEATURE_REGISTRY["binance_oi_change_ratio"]["compute"]

    raw = inspect.unwrap(fn)(df, {}, "1m")
    assert (raw.iloc[:150] == 0.0).all(), "사고 전제(0 채움 구간이 0.0 지표값이 된다)가 바뀌었다"

    out = fn(df, {}, "1m")
    assert out.iloc[:150].isna().all()
    assert out.iloc[160:].notna().all()
    assert (df["binance_futures_oi"].iloc[:150] == 0.0).all(), "원본 df 를 수정하면 안 된다"


def test_unobserved_rows_cannot_be_refilled_by_compute_internals():
    """지표 함수 안의 fillna(0) 이 입력 결측 행을 다시 0 으로 지어내지 못한다."""
    df = _krx_like_frame(100)
    px = 25.0 + np.linspace(0, 1, 100)
    px[40:60] = np.nan
    df["binance_spot_close"] = px
    out = DEFAULT_FEATURE_REGISTRY["binance_ret_5m"]["compute"](df, {}, "1m")
    assert out.iloc[40:60].isna().all()
    assert out.iloc[70:].notna().all()


def test_non_level_input_zero_is_a_real_observation():
    """거래량처럼 0 이 실제 관측인 입력은 0 행을 마스킹하지 않는다."""
    df = _krx_like_frame(100)
    df["binance_spot_volume"] = np.where(np.arange(100) % 2 == 0, 0.0, 10.0)
    df["binance_spot_taker_ratio"] = 0.5
    out = DEFAULT_FEATURE_REGISTRY["binance_cvd_momentum"]["compute"](df, {}, "1m")
    assert out.notna().all()


def test_partially_observed_feature_is_caught_by_availability_gate():
    """앞 구간만 비어 있는 지표는 결측이 시기에 몰렸으므로 가용성 게이트가 제외한다."""
    from shared.features.feature_health import find_unstable_availability
    df = _krx_like_frame(2000)
    df["binance_futures_oi"] = np.where(np.arange(2000) < 1800, 0.0, 1e5 + np.arange(2000))
    df["binance_oi_change_ratio"] = DEFAULT_FEATURE_REGISTRY["binance_oi_change_ratio"]["compute"](df, {}, "1m")
    assert "binance_oi_change_ratio" in find_unstable_availability(df, ["binance_oi_change_ratio"])


# ---------------------------------------------------------------------------
# 4. 커버리지 게이트 — 데이터가 학습 기간을 덮는가, 학습과 실전 조건이 같은가
# ---------------------------------------------------------------------------
def test_sparse_feature_that_passes_spread_gate_is_caught_by_coverage_gate():
    """실측 재현: 유효 0.7% 인 OI 지표는 블록마다 똑같이 비어 있어 '시기 편차' 게이트를 통과했다."""
    from shared.features.feature_health import find_unstable_availability, find_insufficient_coverage
    n = 90000
    df = pd.DataFrame({"timestamp": pd.date_range("2024-03-01", periods=n, freq="15min")})
    df["sparse"] = np.where(np.arange(n) >= n - 619, 1.0, np.nan)
    df["full"] = 1.0
    assert "sparse" not in find_unstable_availability(df, ["sparse"]), "전제: 편차 게이트는 이 경우를 못 잡는다"
    out = find_insufficient_coverage(df, ["sparse", "full"])
    assert "sparse" in out and "full" not in out


def test_train_vs_recent_coverage_shift_is_caught():
    """학습 땐 대부분 결측인데 홀드아웃/실전에선 다 있는 지표 — 학습과 실전 조건이 다르다."""
    from shared.features.feature_health import find_insufficient_coverage
    train = pd.DataFrame({"f": np.where(np.arange(1000) < 150, np.nan, 1.0)})   # 85% — 최소 기준은 통과
    recent_same = pd.DataFrame({"f": np.ones(200)})                               # 100% — 차이 15%p
    recent_gone = pd.DataFrame({"f": np.where(np.arange(200) < 100, np.nan, 1.0)})  # 50% — 차이 35%p
    assert find_insufficient_coverage(train, ["f"], recent_df=recent_same) == {}
    assert "f" in find_insufficient_coverage(train, ["f"], recent_df=recent_gone)


def test_coverage_gate_is_wired_into_screening_and_training_entry():
    """스크리닝을 건너뛰는 경로(사용자 선택·단일 실행)도 있으므로 학습 입구에서도 검사해야 한다."""
    root = os.path.join(_ROOT, "CAiModelBaker", "core")
    for fname in ("research_loop.py", "model_engine.py"):
        with open(os.path.join(root, fname), encoding="utf-8") as f:
            assert "find_insufficient_coverage(" in f.read(), f"{fname} 에 커버리지 게이트가 없다"
