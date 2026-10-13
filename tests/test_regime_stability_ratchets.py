# -*- coding: utf-8 -*-
"""
체제 지표 게이트 래칫 (T-8, 2026-09-28)
========================================
체제/컨텍스트 지표가 **무검정으로** 모델 백본에 올라가던 구조의 재발 방지 테스트다.

사고 구조
---------
체제 지표는 선형 IC 평가를 면제받았는데, 면제가 곧 **무검정**이었다. 그 결과 챔피언 모델
gain 의 84.5% 를 `target_hour_sin/cos` 가 차지했고(참조 피처 하이닉스는 0.08%), 홀드아웃
6거래일에서만 +0.27% 이고 같은 모델의 WFA 내부 검증은 -0.81% 였다.

면제 자체는 옳았다 — 순위 IC 는 단조성을 가정하므로 주기형·U자형을 원리적으로 못 본다.
틀린 것은 **대신 무엇으로 재는가**였다. 실측으로 확인한 함정:
  · 단조성을 풀고 집단차 검정으로 바꾸면 `binance_funding_settle_window`(암호화폐 펀딩
    정산창)가 국내 ETF 에서 가장 강한 p=7.7e-21 을 낸다. 표본이 크면 무엇이든 유의해진다.
  · 그래서 게이트는 '유의한가' 가 아니라 **'구간을 넘어 유지되는가'** 를 물어야 한다.
"""

import ast
import os
import sys

import numpy as np
import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_BAKER = os.path.join(_ROOT, "CAiModelBaker")
if _BAKER not in sys.path:
    sys.path.insert(0, _BAKER)

from shared.features.regime_stability import (
    VERDICT_INDETERMINATE, VERDICT_STABLE, VERDICT_UNSTABLE,
    audit_regime_feature, audit_regime_features,
)

SHUFFLES = 200


def _frame(effect, n=24000, seed=5, groups=40):
    """집단 라벨 + 전방수익률 프레임. effect(idx, group) 가 구조를 결정한다."""
    rng = np.random.default_rng(seed)
    idx = np.arange(n)
    g = idx % groups
    return pd.DataFrame({
        "f": g.astype(float),
        "_forward_return": effect(idx, g) + rng.normal(0, 0.001, n),
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="min"),
    })


# ---------------------------------------------------------------------------
# 1. 탐지기 자체가 옳게 판정하는가 (양성·음성 모두)
# ---------------------------------------------------------------------------
def test_effect_that_holds_across_blocks_is_stable():
    df = _frame(lambda idx, g: np.where(g < 10, 0.0006, np.where(g < 20, -0.0006, 0.0)))
    f = audit_regime_feature(df, "f", shuffles=SHUFFLES, order_col="timestamp")
    assert f.verdict == VERDICT_STABLE
    assert f.agreement > f.noise_floor


def test_effect_that_reverses_across_blocks_is_unstable():
    """전·후반 부호가 뒤집히면 탈락 — 전구간 평균으로는 상쇄돼 보이지 않는 결함이다."""
    n = 24000
    base = np.where((np.arange(n) % 40) < 10, 0.0008, -0.0008)
    df = _frame(lambda idx, g: np.where(idx < n // 2, base, -base), n=n)
    f = audit_regime_feature(df, "f", shuffles=SHUFFLES, order_col="timestamp")
    assert f.verdict == VERDICT_UNSTABLE


def test_pure_noise_is_unstable():
    f = audit_regime_feature(_frame(lambda idx, g: 0.0), "f", shuffles=SHUFFLES)
    assert f.verdict == VERDICT_UNSTABLE


def test_cyclical_feature_invisible_to_rank_ic_is_still_judged():
    """순위 IC 가 0 에 가까운 주기형 지표도 구조가 있으면 통과해야 한다.

    이것이 '선형 IC 면제' 가 필요했던 이유다. 게이트가 주기형을 못 보면 면제를 없앤 것과 같다.
    """
    from scipy import stats
    n, groups = 24000, 40
    g = np.arange(n) % groups
    df = pd.DataFrame({
        "f": np.sin(2 * np.pi * g / groups),
        "_forward_return": 0.0006 * np.cos(2 * np.pi * g / groups)
                           + np.random.default_rng(11).normal(0, 0.001, n),
    })
    rank_ic = stats.spearmanr(df["f"], df["_forward_return"]).statistic
    assert abs(rank_ic) < 0.02, f"이 테스트는 IC 가 낮은 경우를 다룬다 (실제 {rank_ic:+.4f})"
    assert audit_regime_feature(df, "f", shuffles=SHUFFLES).verdict == VERDICT_STABLE


def test_binary_feature_can_pass_and_can_fail():
    """이진 지표도 판정 가능해야 한다.

    블록 쌍마다 순위상관을 재던 초기 구현은 집단이 2개면 상관이 항상 ±1 이라 잡음바닥이
    +1.0 이 되어 **어떤 이진 지표도 통과할 수 없었다**. 통과와 탈락이 모두 가능해야 한다.
    """
    n = 24000
    flag = ((np.arange(n) % 40) < 3).astype(float)
    rng = np.random.default_rng(9)
    noise = rng.normal(0, 0.001, n)

    ok = pd.DataFrame({"f": flag, "_forward_return": np.where(flag > 0, 0.0015, 0.0) + noise})
    assert audit_regime_feature(ok, "f", shuffles=SHUFFLES).verdict == VERDICT_STABLE

    flip = np.where(np.arange(n) < n // 2, 0.0015, -0.0015)
    bad = pd.DataFrame({"f": flag, "_forward_return": np.where(flag > 0, flip, 0.0) + noise})
    assert audit_regime_feature(bad, "f", shuffles=SHUFFLES).verdict == VERDICT_UNSTABLE


def test_significant_but_unstable_effect_is_rejected():
    """표본이 크면 무엇이든 '유의' 해진다 — 게이트는 유의성이 아니라 재현성을 봐야 한다."""
    from scipy import stats
    n = 60000
    rng = np.random.default_rng(4)
    g = np.arange(n) % 40
    # 블록마다 방향이 무작위로 바뀌는 집단 효과 (전체로는 강하게 유의하지만 재현되지 않는다)
    block = (np.arange(n) * 8) // n
    sign = rng.choice([-1.0, 1.0], size=(8, 40))
    eff = 0.0012 * sign[block, g]
    df = pd.DataFrame({"f": g.astype(float), "_forward_return": eff + rng.normal(0, 0.001, n)})
    groups = [x["_forward_return"].values for _, x in df.groupby("f")]
    assert stats.kruskal(*groups).pvalue < 1e-6, "이 테스트는 '유의하지만 불안정' 한 경우를 다룬다"
    assert audit_regime_feature(df, "f", shuffles=SHUFFLES).verdict == VERDICT_UNSTABLE


def test_indeterminate_is_not_a_pass():
    tiny = pd.DataFrame({"f": [0.0, 1.0] * 30, "_forward_return": np.arange(60) * 1e-5})
    f = audit_regime_feature(tiny, "f", shuffles=10)
    assert f.verdict == VERDICT_INDETERMINATE
    assert not f.stable

    const = pd.DataFrame({"f": np.ones(5000), "_forward_return": np.random.default_rng(1).normal(0, 1e-3, 5000)})
    assert audit_regime_feature(const, "f", shuffles=10).verdict == VERDICT_INDETERMINATE

    assert audit_regime_feature(pd.DataFrame({"a": [1]}), "missing").verdict == VERDICT_INDETERMINATE


def test_batch_audit_returns_one_finding_per_feature():
    df = _frame(lambda idx, g: 0.0)
    df["g"] = df["f"]
    out = audit_regime_features(df, ["f", "g", "nope"], shuffles=20)
    assert [o.feature for o in out] == ["f", "g", "nope"]
    assert out[-1].verdict == VERDICT_INDETERMINATE


# ---------------------------------------------------------------------------
# 2. 운영 경로가 그 판정을 실제로 거치는가 (래칫의 핵심)
# ---------------------------------------------------------------------------
def _research_loop_ast():
    path = os.path.join(_ROOT, "CAiModelBaker", "core", "research_loop.py")
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read()), path


def test_ic_screening_calls_the_stability_gate():
    """체제 지표 분기가 감사기를 호출하지 않으면 '면제 = 무검정' 으로 되돌아간다."""
    tree, path = _research_loop_ast()
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_run_ic_screening"), None)
    assert fn is not None, f"{path} 에서 _run_ic_screening 을 찾지 못했다"
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "audit_regime_feature"]
    assert calls, f"{path} 의 _run_ic_screening 이 audit_regime_feature 를 호출하지 않는다"


def test_no_unconditional_regime_prepass_remains():
    """'IC 평가 면제 프리패스' 문구가 남아 있으면 무검정 경로가 되살아난 신호다."""
    path = os.path.join(_ROOT, "CAiModelBaker", "core", "research_loop.py")
    src = open(path, encoding="utf-8").read()
    assert "IC 평가 면제 프리패스" not in src


def test_stability_gate_has_no_hand_tuned_pass_threshold():
    """합격선은 라벨섞기로 유도한 잡음바닥이어야 한다 — 손으로 정한 상수면 안 된다."""
    path = os.path.join(_ROOT, "shared", "features", "regime_stability.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "audit_regime_feature")
    # 일치도를 무엇과 비교하는지 본다. (agreement != agreement 는 NaN 검사이므로 제외)
    verdict_cmp = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Compare):
            continue
        if not (isinstance(n.left, ast.Name) and n.left.id == "agreement"):
            continue
        rhs = n.comparators[0]
        if isinstance(rhs, ast.Name) and rhs.id == "agreement":
            continue  # NaN 자기비교
        verdict_cmp.append(n)

    assert len(verdict_cmp) == 1, f"판정 비교가 하나가 아니다 (문턱이 늘었다): {len(verdict_cmp)}개"
    rhs = verdict_cmp[0].comparators[0]
    assert isinstance(rhs, ast.Name) and rhs.id == "noise_floor", \
        "합격선이 잡음바닥이 아니다 — 손으로 정한 문턱을 쓰고 있다"
    # 상수와 비교하는 판정문이 생기면 곧 '조정 가능한 문턱' 이다
    assert not any(isinstance(c.comparators[0], ast.Constant)
                   for c in verdict_cmp), "판정을 상수와 비교하고 있다"


# ---------------------------------------------------------------------------
# 3. 백본이 '자리를 벌게' 하는 출구 조건 (승격 게이트)
# ---------------------------------------------------------------------------
def test_promotion_requires_beating_the_backbone_only_baseline():
    """챔피언 승격은 '백본 단독 모델보다 나은가' 를 통과해야 한다.

    체제 백본이 모든 조합에 붙어 있으면 후보는 자기 부재와 비교된 적이 없다. 그래서 gain 의
    84.5% 가 시각 지표인 모델이 챔피언으로 승격됐다 — 알파가 더한 것이 없었는데도.
    """
    tree, path = _research_loop_ast()
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "run_auto_research"), None)
    assert fn is not None

    # 승격 분기가 beats_backbone 을 조건으로 쓰는지
    promo = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if {"champion_cand", "final_passed"} <= names:
            promo.append(names)
    assert promo, "챔피언 승격 분기를 찾지 못했다"
    assert all("beats_backbone" in names for names in promo), \
        "승격 조건에 백본 단독 기준선 비교(beats_backbone)가 없다"

    # 기준선은 체제 백본만으로 학습해서 얻어야 한다
    src = open(path, encoding="utf-8").read()
    assert "backbone_only" in src and "backbone_baseline" in src


# ---------------------------------------------------------------------------
# 4. 자산군이 다른 지표는 탐색 풀에 들어오지 않는다
# ---------------------------------------------------------------------------
def test_search_pool_excludes_features_of_another_market():
    """암호화폐 전용 지표가 KRX ETF 모델의 배포 규격까지 들어갔던 결함의 재발 방지.

    선언(registry.market_scope)이 유일한 근거이며, 종목 목록을 코드에 두지 않는다.
    """
    from shared.features.feature_registry import (
        DEFAULT_FEATURE_REGISTRY, get_incompatible_features,
    )
    pool = [fid for fid, d in DEFAULT_FEATURE_REGISTRY.items() if not d.get("is_alias", False)]
    bad = {x["feature_id"] for x in get_incompatible_features("069500", pool)}
    assert "binance_funding_settle_window" in bad, \
        "암호화폐 펀딩 정산창 지표가 KRX 종목에 부적합으로 잡히지 않는다"

    tree, path = _research_loop_ast()
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_auto_research")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "get_incompatible_features"]
    assert calls, f"{path} 의 run_auto_research 가 자산군 적합성 검사를 하지 않는다"


# ---------------------------------------------------------------------------
# 5. 조건부 알파가 다단 범주형을 평가할 수 있다
# ---------------------------------------------------------------------------
def test_conditional_alpha_handles_multi_level_categorical():
    """값이 모두 0 초과인 다단 범주형(세션 구간 1/2/3)도 평가돼야 한다.

    종전에는 `event = (값 > 0)` 이라 **전체 행이 이벤트**가 되고 기준군이 0개여서
    `krx_session_time` 같은 지표가 영원히 rare_sample 로 판정 불가였다.
    """
    try:
        from CAiModelBaker.core.research_loop import calculate_conditional_alpha
    except ImportError:
        from core.research_loop import calculate_conditional_alpha

    rng = np.random.default_rng(2)
    n = 6000
    level = np.tile([1.0, 2.0, 3.0], n // 3)
    df = pd.DataFrame({
        "f": level,
        "_forward_return": np.where(level == 3, 0.0020, 0.0) + rng.normal(0, 0.001, n),
    })
    res = calculate_conditional_alpha(df, "f", "_forward_return")
    assert res["passed"] is True, f"다단 범주형을 평가하지 못했다: {res.get('reason')}"
    assert 0 < res["n_event"] < res["n_total"], "이벤트가 전체 행이 되었다(기준군 소멸)"

    # 엣지가 없으면 통과하지 않는다
    flat = pd.DataFrame({"f": level, "_forward_return": rng.normal(0, 0.001, n)})
    assert calculate_conditional_alpha(flat, "f", "_forward_return")["passed"] is False


def test_conditional_alpha_binary_behaviour_is_unchanged():
    """이진 0/1 지표의 동작은 종전과 같아야 한다 (수준 1 이 이벤트)."""
    try:
        from CAiModelBaker.core.research_loop import calculate_conditional_alpha
    except ImportError:
        from core.research_loop import calculate_conditional_alpha

    rng = np.random.default_rng(7)
    n = 6000
    flag = (np.arange(n) % 20 == 0).astype(float)
    df = pd.DataFrame({
        "f": flag,
        "_forward_return": np.where(flag > 0, 0.0020, 0.0) + rng.normal(0, 0.001, n),
    })
    res = calculate_conditional_alpha(df, "f", "_forward_return")
    assert res["passed"] is True
    assert res["n_event"] == int(flag.sum())     # 1 인 행만 이벤트
    assert res["spread"] > 0                      # 부호가 뒤집히지 않는다
