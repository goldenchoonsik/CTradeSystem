# -*- coding: utf-8 -*-
"""
최종 판정(홀드아웃/지정 테스트) 가드 계약 테스트 (D-12 ~ D-17)
============================================================
목적
----
WFA 학습 단계(`compute_wfa_score`, D-11)의 거래 수/PF 가드를 **최종 판정 단계에도** 강제한다.
종전에는 최종 판정이 별도 구현이라 동일한 `max(1, …)` 붕괴를 반복했다.

실측 결함 (2026-09-15 배포 후보, `logs/quant_engine_log.txt`)
------------------------------------------------------------
1. **D-12** 목표 거래 수 붕괴
   지정 테스트가 2일(09-14~09-15)이자 `target_trades = max(1, round(20 × 2/30.4)) = 1`
   → `거래수: 1회 (목표: >= 1회)` 로 **거래 1건이 목표를 충족**해 `passed=True`
   → 배포 후보 rank_1·2·3 이 전부 "거래 1건"(사실상 동일 거래)으로 챔피언 승격

2. **D-13** 스나이퍼 특례 우회
   `is_sniper_pass = (total_trades >= 2 and …)`
   → 실측 `거래수: 4회 (목표: >= 5회 … 충족)` — 거래 4~5건 전승이 목표를 우회

3. **D-14** PF 99.0 오도
   손실 0건이면 PF=99.0 → 거래 1건 후보가 `win_rate=100.0 / PF=99.0` 으로 리더보드에 "완벽" 표시

4. **D-15** 죽은 피처
   참조 원천 중단(09-02)으로 최종 Test 구간에서 전량 NaN 인 `ref_samsung_spread_vs_target` 이
   rank_2/rank_3 후보에 채택됨 (Validation 성과만 보고 선정했기 때문)

고정하는 계약
-------------
1. `resolve_holdout_trade_requirement` — 절대 하한 + 최소 기간 판정
2. `detect_dead_features` — 최종 Test 구간 피처 가용성
3. `backtester` PF 표본 부족 표식 전파
4. 종전 degenerate 패턴 재도입 방지 (AST 자기검증)
"""

import ast
import inspect
import os
import sys

import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from CAiModelBaker.core.model_engine import (  # noqa: E402
    HOLDOUT_MIN_TRADING_DAYS,
    HOLDOUT_MIN_TRADES_ABS,
    HOLDOUT_STANDARD_TRADES,
    evaluate_holdout_standard_met,
    resolve_holdout_trade_requirement,
)
from CAiModelBaker.core.research_loop import detect_dead_features  # noqa: E402


# ==============================================================================
# 1. D-12/D-17 — 목표 거래 수 절대 하한 + 최소 기간
# ==============================================================================

def test_short_holdout_target_does_not_collapse():
    """
    실측 조건 재현: 홀드아웃 **2거래일** × 월 20회 목표.
    종전에는 round(20 × 2/30.4) = 1 로 붕괴했고, 그 1건이 통과해 배포되었다.
    """
    target, sufficient, days = resolve_holdout_trade_requirement(20, 2)

    legacy_target = max(1, int(round(20 * (2 / 30.4))))
    assert legacy_target == 1, "종전 식이 1로 붕괴함을 재현해야 회귀 테스트가 유효하다"

    assert days == 2
    assert target == int(HOLDOUT_MIN_TRADES_ABS), \
        f"절대 하한({HOLDOUT_MIN_TRADES_ABS})이 지배해야 하는데 {target} 이 적용되었습니다"
    assert target > legacy_target, "종전 붕괴값(1)보다 반드시 커야 한다"
    assert sufficient is False, \
        f"홀드아웃 2거래일 < 최소 {HOLDOUT_MIN_TRADING_DAYS}거래일 → 표본 부족으로 판정 불가여야 한다"


def test_adequate_holdout_uses_monthly_rate():
    """충분히 긴 홀드아웃(90거래일)에서는 월간 요구량이 지배한다."""
    target, sufficient, days = resolve_holdout_trade_requirement(20, 90)

    assert days == 90
    assert sufficient is True
    assert target == int(round(20 * (90 / 30.4)))  # ≈ 59
    assert target > int(HOLDOUT_MIN_TRADES_ABS)


def test_floor_configurable_via_optuna_cfg():
    """`min_trades_abs` / `min_holdout_trading_days` 로 정책을 재정의할 수 있어야 한다."""
    target, sufficient, _ = resolve_holdout_trade_requirement(
        20, 5, {"min_trades_abs": 30, "min_holdout_trading_days": 3}
    )
    assert target == 30, "min_trades_abs=30 이면 하한이 30이어야 함"
    assert sufficient is True, "min_holdout_trading_days=3 이면 5거래일은 충분해야 함"


def test_robust_to_malformed_input():
    """비정상 입력(문자열/None)에도 예외 없이 안전한 기본값을 반환해야 한다."""
    target, sufficient, days = resolve_holdout_trade_requirement("bad", "bad")
    assert target == int(HOLDOUT_MIN_TRADES_ABS)
    assert days == 30
    assert isinstance(sufficient, bool)


def test_adequate_holdout_flag_boundary():
    """최소 거래일 경계(정확히 HOLDOUT_MIN_TRADING_DAYS)는 충분으로 판정한다."""
    _, exactly, _ = resolve_holdout_trade_requirement(20, int(HOLDOUT_MIN_TRADING_DAYS))
    _, below, _ = resolve_holdout_trade_requirement(20, int(HOLDOUT_MIN_TRADING_DAYS) - 1)
    assert exactly is True
    assert below is False


# ==============================================================================
# 1b. [P-4] 업계 표준 충족 판정 (기간 OR 거래 횟수)
# ==============================================================================

def test_standard_met_by_trading_days_or_trades():
    """
    표준은 **표본 크기** 관문이다 — 기간과 거래 횟수를 OR 로 결합한다.
    bar 밀도가 높은 초봉(6거래일·42건)은 통과하고, 2거래일·1건은 비표준이다.
    """
    # 기간 충족 (10거래일 = 10s 표준)
    assert evaluate_holdout_standard_met(10, 10, 0) is True
    # 거래 횟수로 충족 (6거래일 < 10 이지만 42건 ≥ 30)
    assert evaluate_holdout_standard_met(6, 10, 42) is True
    # 둘 다 미달 → 비표준 (실측 배포 케이스: 2거래일·1건)
    assert evaluate_holdout_standard_met(2, 10, 1) is False


def test_standard_trades_threshold_configurable():
    """`standard_holdout_trades` 로 표준 거래 횟수를 재정의할 수 있어야 한다."""
    assert evaluate_holdout_standard_met(6, 10, 15) is False
    assert evaluate_holdout_standard_met(6, 10, 15, {"standard_holdout_trades": 10}) is True


def test_standard_met_robust_to_bad_input():
    """비정상 입력은 예외 없이 False(비표준)로 처리한다."""
    assert evaluate_holdout_standard_met(None, 10, 5) is False
    assert evaluate_holdout_standard_met("bad", "bad", "bad") is False


def test_standard_trades_constant_is_reasonable():
    """표준 거래 횟수는 통계적 유의성 기준(≥30)을 따라야 한다."""
    assert HOLDOUT_STANDARD_TRADES >= 30, \
        "표준 거래 횟수가 30 미만이면 CLT 근사 근거가 약해진다"


# ==============================================================================
# 2. D-15 — 최종 Test 구간 피처 가용성 (죽은 피처 탐지)
# ==============================================================================

def _feature_df():
    """피처 엔지니어링 완료 데이터프레임 모사 (2026-09-10 ~ 09-16)"""
    ts = pd.date_range("2026-09-10 09:00", "2026-09-16 15:30", freq="10min")
    n = len(ts)
    return pd.DataFrame({
        "timestamp": ts,
        # 최종 Test 구간에서 전량 NaN (참조 원천 2026-09-02 중단 → 실측 케이스)
        "ref_samsung_spread_vs_target": [float("nan")] * n,
        # 정상 변동 피처
        "ref_es_spread_vs_target": [((i % 50) - 25) / 100.0 for i in range(n)],
        # 상수 피처 (죽음)
        "target_hour_cos": [0.5] * n,
        # 구간 일부만 유효 (커버리지 부족 재현용)
        "ref_partial_spread_vs_target": [((i % 7) - 3) / 10.0 for i in range(n)],
    })


def test_detects_all_nan_feature_in_final_test_window():
    """실측 케이스: 참조 원천 중단으로 최종 Test 구간 전량 NaN 인 피처를 탐지해야 한다."""
    df = _feature_df()

    dead, details = detect_dead_features(
        df, ["ref_es_spread_vs_target", "ref_samsung_spread_vs_target"],
        "2026-09-14", "2026-09-15",
    )

    assert "ref_samsung_spread_vs_target" in dead, \
        "최종 Test 구간 전량 NaN 피처가 탐지되지 않았습니다 [D-15]"
    assert details["ref_samsung_spread_vs_target"]["reason"] == "INSUFFICIENT_COVERAGE"
    assert details["ref_samsung_spread_vs_target"]["valid_ratio"] == 0.0
    assert details["ref_samsung_spread_vs_target"]["rows"] > 0
    assert "ref_es_spread_vs_target" not in dead, "정상 피처는 통과해야 함"


def test_detects_constant_feature():
    """값이 1종뿐인(상수) 피처는 정보량이 0 이므로 탐지되어야 한다."""
    df = _feature_df()
    dead, details = detect_dead_features(df, ["target_hour_cos"], "2026-09-14", "2026-09-15")
    assert dead == ["target_hour_cos"]
    assert details["target_hour_cos"]["reason"] == "CONSTANT"
    assert details["target_hour_cos"]["nunique"] == 1


def test_detects_missing_column():
    """피처 엔지니어링 결과에 존재하지 않는 컬럼도 죽은 피처다."""
    df = _feature_df()
    dead, details = detect_dead_features(df, ["no_such_feature"], "2026-09-14", "2026-09-15")
    assert dead == ["no_such_feature"]
    assert details["no_such_feature"]["reason"] == "MISSING_COLUMN"


def test_no_dead_features_when_window_out_of_range():
    """구간 밖(미래)이면 데이터가 없는 것이므로 죽었다고 단정하지 않는다."""
    df = _feature_df()
    dead, details = detect_dead_features(
        df, ["ref_es_spread_vs_target"], "2027-01-01", "2027-01-02"
    )
    assert dead == []
    assert details == {}


def test_robust_to_null_or_empty_input():
    """입력이 비정상이면 예외 없이 빈 결과를 반환해야 한다."""
    df = _feature_df()
    assert detect_dead_features(None, ["x"], "2026-09-14", "2026-09-15") == ([], {})
    assert detect_dead_features(df, [], "2026-09-14", "2026-09-15") == ([], {})
    assert detect_dead_features(df, ["ref_es_spread_vs_target"], "bad-date", "bad-date") == ([], {})


def test_min_valid_ratio_is_configurable():
    """커버리지 하한을 낮추면 부분 결측 피처가 통과한다."""
    df = _feature_df()
    # 절반을 살짝 넘게 결측 처리 ( 유효 비율 < 0.5 )
    cut = len(df) // 2 - 1
    df.loc[cut:, "ref_partial_spread_vs_target"] = float("nan")

    dead_strict, det = detect_dead_features(
        df, ["ref_partial_spread_vs_target"], "2026-09-10", "2026-09-16"
    )
    dead_loose, _ = detect_dead_features(
        df, ["ref_partial_spread_vs_target"], "2026-09-10", "2026-09-16", min_valid_ratio=0.1
    )
    assert det["ref_partial_spread_vs_target"]["valid_ratio"] < 0.5
    assert dead_strict == ["ref_partial_spread_vs_target"], "커버리지 50% 미만 → 죽은 피처"
    assert dead_loose == [], "하한 10% 면 통과"


# ==============================================================================
# 3. D-14 — PF 표본 부족 표식 전파
# ==============================================================================

def test_backtester_propagates_pf_lossless_flag():
    """
    손실 0건이면 PF 99.0 은 '무손실' 표식일 뿐이므로 `pf_lossless` 로 명시 전파되어야 한다.
    (미전파 시 최종 판정이 'PF 99.0 = 완벽'으로 오독한다 — 실측 거래 1건 후보)
    """
    from CAiModelBaker.core import backtester as bt

    src = inspect.getsource(bt.process_03_pure_backtest)
    tree = ast.parse(src)

    assigned = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "pf_lossless"
    ]
    assert assigned, "backtester 에 pf_lossless 산출이 없습니다 [D-14]"

    keys = {
        k.value for d in ast.walk(tree) if isinstance(d, ast.Dict)
        for k in d.keys if isinstance(k, ast.Constant)
    }
    assert "pf_lossless" in keys, "반환 dict 에 pf_lossless 가 전파되지 않았습니다 [D-14]"


# ==============================================================================
# 4. 재발 방지 자기검증 (AST) — 종전 degenerate 패턴 차단
# ==============================================================================

def test_research_loop_has_no_degenerate_trade_target():
    """
    `research_loop` 에 종전 붕괴 패턴이 재도입되지 않아야 한다.
      ① `max(1, int(round(raw_min_trades * …)))` → 목표 거래 수 1 붕괴 (D-12)
      ② `total_trades >= 2`                       → 스나이퍼 특례 우회 (D-13)
    """
    from CAiModelBaker.core import research_loop as rl

    src = inspect.getsource(rl)
    tree = ast.parse(src)

    collapses = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "max"):
            continue
        if not n.args or not isinstance(n.args[0], ast.Constant) or n.args[0].value != 1:
            continue
        seg = ast.get_source_segment(src, n) or ""
        if "raw_min_trades" in seg and "round" in seg:
            collapses.append(n.lineno)
    assert not collapses, (
        f"{collapses} 행에 목표 거래 수 붕괴 패턴(max(1, round(raw_min_trades …)))이 "
        "재도입되었습니다 — resolve_holdout_trade_requirement 를 사용하세요 [D-12]"
    )

    sniper_literals = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Compare):
            continue
        if getattr(n.left, "id", None) != "total_trades":
            continue
        for c in n.comparators:
            if isinstance(c, ast.Constant) and c.value == 2:
                sniper_literals.append(n.lineno)
    assert not sniper_literals, (
        f"{sniper_literals} 행에 `total_trades >= 2` 형태의 스나이퍼 임계가 재도입되었습니다 "
        f"— HOLDOUT_MIN_TRADES_ABS({HOLDOUT_MIN_TRADES_ABS}) 를 사용하세요 [D-13]"
    )


def test_research_loop_uses_shared_policy_helpers():
    """최종 판정이 공용 정책 함수/상수를 실제로 사용해야 한다."""
    from CAiModelBaker.core import research_loop as rl

    src = inspect.getsource(rl)
    assert "resolve_holdout_trade_requirement(" in src, \
        "목표 거래 수가 단일 정책 함수로 산정되지 않습니다 [D-12]"
    assert "HOLDOUT_MIN_TRADES_ABS" in src, "스나이퍼 임계가 공용 상수를 쓰지 않습니다 [D-13]"
    assert "detect_dead_features(" in src, "최종 Test 피처 가용성 검증이 배선되지 않았습니다 [D-15]"
    assert "sample_sufficient" in src, "표본 충분성 판정이 반영되지 않았습니다 [D-17]"
