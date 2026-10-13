# -*- coding: utf-8 -*-
"""
WFA / Optuna 점수 가드 계약 테스트 (D-11)
========================================
목적
----
`compute_wfa_score` 는 WFA 창 선택과 Optuna 목적함수가 **공용으로 사용하는 단일 점수 함수**다.
본 테스트는 "거래를 거의 하지 않는 해가 최적해가 되는" degenerate 현상의 재발을 기계적으로 차단한다.

종전 결함 (실측: deploy/069500_10s/KODEX200_S10_BEST1____/reports/train_run.log)
-------------------------------------------------------------------------------
    🔥 [WFA Window 12] New Best Score: 14.99 (Trades: 1 [요구: 1.0회], WinRate: 100.0%, MDD: 0.04%)
    🔥 [WFA Window 10] New Best Score: 14.95 (Trades: 10 [요구: 1.0회], WinRate: 80.0%, ...)

· `pf = total_gain / (total_loss + 1e-8)` 에 상한이 없어 **손실 0건(거래 1건·승률 100%)** 후보의
  PF 가 7만점대로 폭발했다 (validation PF 58,747 ~ 84,236).
· `required_min_trades = max(1.0, min_trades_per_month * val_months)` 에서 WFA 창이 2~4일이면
  우변(20회/월 × 0.07개월 ≈ 1.4)이 1.0 에 수렴 → **거래 1건도 "충분"으로 통과**.
→ 옵티마이저가 최저빈도 해를 선택 → 최종 Test 구간(4일) 매매 0건.

고정하는 계약
-------------
1. PF 상한 — 점수 기여도가 `pf_score_cap` 을 넘지 못한다. 원값은 `pf_raw` 로 정직하게 보고한다.
2. 절대 최소 거래 수 — 창 길이와 무관하게 `min_trades_abs` 미만은 `-90 + trades` 로 탈락한다.
3. 최소 거래 수 미달 후보는 **어떤 실질 후보보다도 낮은 점수**를 받아야 한다.
4. 임계값은 `OPTUNA_CONFIG` 로 재정의 가능하다.
5. 4개 호출 지점이 모두 단일 함수를 사용한다 (재발 방지 자기검증).
"""

import ast
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from CAiModelBaker.core.model_engine import (  # noqa: E402
    WFA_MIN_TRADES_ABS,
    WFA_PF_SCORE_CAP,
    compute_wfa_score,
)

# 실측 WFA 창 길이 (2일 / 30.44 ≈ 0.0657개월) — 종전 요구거래량이 1.31 → max(1.0, …) 로 붕괴하던 지점
_VAL_MONTHS_2D = 2.0 / 30.44


# ==============================================================================
# 1. PF 상한 — 손실 0건 후보의 PF 폭발 차단
# ==============================================================================

def test_single_trade_no_loss_pf_is_capped():
    """거래 1건 무손실 → pf_raw 는 폭발하지만 점수 기여 PF 는 상한으로 잘린다."""
    # 순수익 +0.2% 1건, 손실 0건 (실측 validation PF 84,236 재현 조건)
    score, pf, pf_raw, _ = compute_wfa_score(
        total_gain=0.002, total_loss=0.0, trades_v=1, mdd_v=0.0004,
        val_months=_VAL_MONTHS_2D,
    )

    assert pf_raw > 100_000.0, "무손실이면 pf_raw 는 폭발해야 한다 (원값 정직 보고)"
    assert pf == WFA_PF_SCORE_CAP, "점수 기여 PF 는 상한으로 잘려야 한다"
    assert score < 0.0, "최소 거래 수(절대 하한) 미달 후보는 탈락 점수를 받아야 한다"


def test_degenerate_candidate_cannot_beat_realistic_candidate():
    """
    [핵심 회귀 계약] 종전 최고점(14.99)을 차지하던 '거래 1건·승률 100%' 후보가
    실질 후보(14건·승률 50%)보다 **반드시 낮은** 점수를 받아야 한다.
    """
    # (a) degenerate: 거래 1건, 무손실, MDD 0.04% → 종전 14.99 로 최고점 독식
    d_score, _, _, _ = compute_wfa_score(
        total_gain=0.002, total_loss=0.0, trades_v=1, mdd_v=0.0004,
        val_months=_VAL_MONTHS_2D,
    )

    # (b) 실질 후보: 거래 14건, PF 3.0, MDD 1.0% (실측 WFA Window 9 조건)
    r_score, _, _, _ = compute_wfa_score(
        total_gain=0.03, total_loss=0.01, trades_v=14, mdd_v=0.01,
        val_months=_VAL_MONTHS_2D,
    )

    # 종전 점수식 재현 (문서화용 — 소스에 존재하지 않음)
    legacy_degenerate = min(0.002 / (0.0 + 1e-8), 10.0) * (1.0 - 0.0004) * (1.0 + 0.5)
    assert legacy_degenerate == pytest.approx(14.99, abs=0.01), \
        "종전 점수식이 실측 로그(14.99)를 재현해야 회귀 테스트가 유효하다"

    assert d_score < 0.0 < r_score, (
        f"degenerate(거래1건) 후보가 탈락하지 않았습니다: "
        f"degenerate={d_score:.4f} vs 실질={r_score:.4f} [D-11]"
    )
    assert r_score > d_score


# ==============================================================================
# 2. 절대 최소 거래 수 — 창 길이와 무관한 하한
# ==============================================================================

def test_required_trades_has_absolute_floor():
    """
    WFA 창이 짧아도(2일) 요구 거래 수가 1건대로 붕괴하면 안 된다.

    종전: max(1.0, 20회/월 × 0.0657개월 = 1.31) = 1.31 → 거래 1건도 통과 (실측 '요구: 1.0회')
    현재: max(10.0, 1.31) = 10.0 → 창 길이와 무관한 절대 하한이 지배
    """
    _, _, _, required = compute_wfa_score(
        total_gain=0.01, total_loss=0.01, trades_v=1, mdd_v=0.0,
        val_months=_VAL_MONTHS_2D,
        optuna_cfg={"min_trades": 20},
    )

    assert required == WFA_MIN_TRADES_ABS, \
        f"절대 하한({WFA_MIN_TRADES_ABS})이 지배해야 하는데 {required} 가 적용되었습니다"
    assert required > 1.31, "종전 붕괴값(1.31)보다 반드시 커야 한다"


def test_long_window_uses_monthly_rate_when_larger():
    """장기 창에서는 월간 요구량이 절대 하한을 초과할 때 그것이 적용된다."""
    _, _, _, required = compute_wfa_score(
        total_gain=0.1, total_loss=0.05, trades_v=60, mdd_v=0.0,
        val_months=6.0,
        optuna_cfg={"min_trades": 20},
    )
    assert required == pytest.approx(120.0), "6개월 × 20회/월 = 120회 요구"


def test_below_floor_score_formula():
    """미달 점수는 `-90 + trades` (거래가 많아도 실질 후보보다 낮아야 함)."""
    score, _, _, _ = compute_wfa_score(
        total_gain=1.0, total_loss=0.0, trades_v=9, mdd_v=0.0,
        val_months=_VAL_MONTHS_2D,
    )
    assert score == pytest.approx(-81.0)
    assert score < 0.0


# ==============================================================================
# 3. 임계값 설정 가능성 (OPTUNA_CONFIG)
# ==============================================================================

def test_thresholds_are_configurable():
    """`min_trades_abs` / `pf_score_cap` 이 OPTUNA_CONFIG 로 재정의되어야 한다."""
    _, _, _, required = compute_wfa_score(
        total_gain=0.01, total_loss=0.01, trades_v=3, mdd_v=0.0,
        val_months=_VAL_MONTHS_2D,
        optuna_cfg={"min_trades": 20, "min_trades_abs": 3},
    )
    assert required == 3.0, "min_trades_abs=3 이면 요구 거래 수가 3이어야 함"

    _, pf, pf_raw, _ = compute_wfa_score(
        total_gain=0.5, total_loss=0.01, trades_v=20, mdd_v=0.0,
        val_months=1.0,
        optuna_cfg={"min_trades_abs": 10, "pf_score_cap": 5.0},
    )
    assert pf == 5.0, "pf_score_cap=5 이면 점수 기여 PF 가 5로 잘려야 함"
    assert pf_raw == pytest.approx(50.0, rel=1e-3)


def test_defaults_are_conservative():
    """기본 임계값은 degenerate 후보를 차단할 수준이어야 한다."""
    assert WFA_MIN_TRADES_ABS >= 5.0, \
        "절대 하한이 너무 낮으면 거래 1~2건 후보가 통과한다 (종전 결함)"
    assert WFA_PF_SCORE_CAP >= 10.0, \
        "PF 상한이 너무 낮으면 점수가 포화되어 변별력이 사라진다 (종전 10.0 문제)"


# ==============================================================================
# 4. 재발 방지 자기검증 — 모든 점수 지점이 단일 함수를 사용해야 한다
# ==============================================================================

def test_all_score_sites_use_the_single_guard():
    """
    `model_engine.py` 의 모든 점수 산출 지점이 `compute_wfa_score` 를 사용하고,
    종전 degenerate 패턴(`min_trades_per_month` 별도 산정)이 재도입되지 않아야 한다.
    """
    import inspect
    from CAiModelBaker.core import model_engine as me

    src = inspect.getsource(me)
    tree = ast.parse(src)

    call_count = sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "compute_wfa_score"
    )
    assert call_count >= 3, (
        f"compute_wfa_score 호출이 {call_count}건뿐입니다 — "
        "일부 점수 지점이 가드를 우회합니다 [D-11]"
    )

    # 종전 degenerate 패턴 재도입 차단.
    # compute_wfa_score 본문(모듈 함수 → col_offset == 4)만 허용하고,
    # 그 밖(다른 함수/중첩 스코프)의 별도 산정은 금지한다.
    offenders = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "min_trades_per_month"
        and n.col_offset != 4
    ]
    assert not offenders, (
        f"{offenders} 행에 요구 거래 수 별도 산정이 재도입되었습니다 — "
        "요구 거래 수는 compute_wfa_score 단일 지점에서만 산정해야 합니다 [D-11]"
    )

    # 종전 degenerate 표현식 `max(1.0, min_trades_per_month * val_months)` 재도입 차단.
    # (docstring/주석의 과거 서술은 오탐하지 않도록 AST 호출 노드만 검사한다)
    degenerate_calls = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "max"):
            continue
        if not n.args or not isinstance(n.args[0], ast.Constant) or n.args[0].value != 1.0:
            continue
        if "min_trades_per_month" in (ast.get_source_segment(src, n) or ""):
            degenerate_calls.append(n.lineno)
    assert not degenerate_calls, (
        f"{degenerate_calls} 행에 종전 degenerate 표현식이 재도입되었습니다 — "
        "`max(1.0, min_trades_per_month * val_months)` 는 창이 짧으면 1.0 으로 붕괴해 "
        "거래 1건 후보를 통과시킵니다 [D-11]"
    )
