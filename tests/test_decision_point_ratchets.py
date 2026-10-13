# -*- coding: utf-8 -*-
"""
의사결정 시점 래칫 (2026-10-01)
================================
학습·스크리닝 표본은 **실제로 진입할 수 있었던 시점**뿐이어야 한다.

사고 구조
---------
069500 10s 자동학습(KODEX200_S10_NEW1) 후보 모델 gain 의 81% 가 `target_mfi` 였다. 원인은 시장 예측이
아니라 시계였다: 15:19~15:30 동시호가는 봉의 90% 가 거래량 0 → MFI 가 0 으로 채워지고, 같은 구간의
라벨은 장 마감 규칙으로 강제 0 이다. 모델은 'MFI=0 이면 라벨 0' 을 배웠다(그 구간 예측 확률 0.15).
학습은 웜업(09:00~09:09)만 뺐고 15:00 이후는 그대로 넣었다 — 백테스터·실전의 진입 규칙(09:10~15:00)과
학습 표본의 정의가 서로 달랐던 것이 근본 원인이다.

같은 이유로 IC 스크리닝의 전방 수익률 `pct_change(h).shift(-h)` 는 장 막판 봉에서 **밤사이 갭**이 됐다.

불변식
------
1. 의사결정 시점의 정의는 `market_utils.decision_point_mask` 하나다(백테스터의 `is_krx_entry_allowed` 와 일치).
2. 학습 라벨은 `model_engine.label_decision_points` 만 만든다 — 연속 시계열로 라벨링한 **뒤에** 거른다.
3. 스크리닝 전방 수익률은 `market_utils.session_forward_return` 만 쓴다.
"""

import ast
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_BAKER = os.path.join(_ROOT, "CAiModelBaker")
if _BAKER not in sys.path:
    sys.path.insert(0, _BAKER)

from shared.core.market_utils import (
    decision_point_mask, is_krx_entry_allowed, session_forward_return,
)


def _krx_days(n_days=2, step_s=10):
    """09:00~15:30 연속 봉 n일 (평일)."""
    out = []
    for d in pd.bdate_range("2026-09-01", periods=n_days):
        out.append(pd.date_range(d + pd.Timedelta(hours=9), d + pd.Timedelta(hours=15, minutes=30),
                                 freq=f"{step_s}s"))
    return pd.Series(np.concatenate([x.values for x in out]))


def test_mask_matches_backtester_entry_rule():
    ts = _krx_days(1)
    mask = decision_point_mask(ts, intraday_krx=True)
    expected = np.array([is_krx_entry_allowed(pd.Timestamp(t)) for t in ts])
    assert (mask == expected).all()
    assert not decision_point_mask(ts, intraday_krx=False).sum() < len(ts)  # 연속 시장은 전부 허용


def test_session_forward_return_never_spans_overnight_or_closed_hours():
    ts = _krx_days(2)
    close = np.linspace(100.0, 200.0, len(ts))
    h = 6
    fwd = session_forward_return(close, ts, h, intraday_krx=True)
    tsi = pd.DatetimeIndex(ts)
    later = pd.DatetimeIndex(pd.Series(ts).shift(-h))
    crosses = (later.normalize() != tsi.normalize())
    assert np.isnan(fwd[np.asarray(crosses)]).all(), "밤사이 갭이 전방 수익률로 들어갔다"
    assert np.isnan(fwd[~decision_point_mask(ts, True)]).all(), "진입 불가 시점에 전방 수익률이 있다"
    ok = ~np.isnan(fwd)
    assert ok.sum() > 0
    np.testing.assert_allclose(fwd[ok], (close[h:][ok[:-h]] / close[:-h][ok[:-h]]) - 1.0)


def test_label_decision_points_labels_on_full_series_then_filters():
    from CAiModelBaker.core.model_engine import label_decision_points, triple_barrier_labeling
    ts = _krx_days(2)
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 0.05, len(ts)))
    df = pd.DataFrame({"timestamp": ts, "target_close": close, "target_low": close - 0.02,
                       "target_atr": np.full(len(ts), 0.05), "f": rng.normal(size=len(ts))})
    out = label_decision_points(df, 6, 1.2, 2.0, 0.0003, market_type="KRX")
    assert decision_point_mask(out["timestamp"], True).all(), "진입 불가 시점 행이 학습 표본에 남았다"
    # 연속 시계열로 만든 라벨과 같아야 한다 (먼저 거르고 라벨링하면 14:59 의 라벨이 15:00 이후 봉을 못 본다)
    full = triple_barrier_labeling(df, 6, 1.2, 2.0, 0.0003, market_type="KRX")
    keep = decision_point_mask(df["timestamp"], True)
    np.testing.assert_array_equal(out["target"].to_numpy(), full[keep])
    # 크립토(연속 시장)는 행을 거르지 않는다
    assert len(label_decision_points(df, 6, 1.2, 2.0, 0.0003, market_type="CRYPTO")) == len(df)


def _calls(path, func_name):
    tree = ast.parse(open(path, encoding="utf-8").read())
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
                    if name == func_name:
                        hits.append((node.name, sub.lineno))
    return hits


def test_training_labels_only_via_single_entry_point():
    """학습 경로에서 triple_barrier_labeling 직접 호출 금지 — label_decision_points 를 거친다."""
    for rel in ("CAiModelBaker/core/model_engine.py", "CAiModelBaker/core/feature_tuner.py"):
        bad = [(fn, ln) for fn, ln in _calls(os.path.join(_ROOT, rel), "triple_barrier_labeling")
               if fn != "label_decision_points"]
        assert not bad, f"{rel}: 의사결정 시점 마스크를 우회한 라벨링 {bad}"


def test_screeners_do_not_build_raw_forward_returns():
    """스크리너가 `pct_change(h).shift(-h)` / `shift(-lag)` 로 전방 수익률을 직접 만들면 밤사이 갭이 섞인다."""
    files = ("CAiModelBaker/core/research_loop.py", "CAiModelBaker/core/feature_screener.py",
             "CAiModelBaker/core/feature_tuner.py")
    for rel in files:
        tree = ast.parse(open(os.path.join(_ROOT, rel), encoding="utf-8").read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "shift"
                    and node.args and isinstance(node.args[0], ast.UnaryOp) and isinstance(node.args[0].op, ast.USub)):
                inner = node.func.value
                is_price_fwd = (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                                and inner.func.attr == "pct_change")
                assert not is_price_fwd, f"{rel}:{node.lineno} 원시 전방 수익률 — session_forward_return 을 쓸 것"


# ──────────────────────────────────────────────────────────────────────────────
# 가용성 안정성 게이트 — 결측이 시기에 몰린 피처는 기간 표식이 된다
# ──────────────────────────────────────────────────────────────────────────────

def test_availability_gate_rejects_period_confounded_missingness_only():
    from shared.features.feature_health import find_unstable_availability
    ts = _krx_days(10)
    n = len(ts)
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"timestamp": ts, "always": rng.normal(size=n), "late_only": rng.normal(size=n),
                       "daily_warmup_gap": rng.normal(size=n)})
    df.loc[: n // 2, "late_only"] = np.nan                       # 수집 이력 때문에 앞 절반이 비었다
    tod = pd.DatetimeIndex(ts).hour * 60 + pd.DatetimeIndex(ts).minute
    df.loc[np.asarray(tod < 9 * 60 + 15), "daily_warmup_gap"] = np.nan   # 매일 같은 구조적 결측
    out = find_unstable_availability(df, ["always", "late_only", "daily_warmup_gap"],
                                     row_mask=decision_point_mask(ts, True))
    assert set(out) == {"late_only"}, out


def test_ic_screening_applies_availability_gate():
    """연구 루프 IC 스크리닝이 가용성 게이트를 거친다 (호출이 빠지면 게이트가 있어도 무용지물)."""
    src = open(os.path.join(_ROOT, "CAiModelBaker/core/research_loop.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_ic_screening")
    called = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "find_unstable_availability" in called


# ──────────────────────────────────────────────────────────────────────────────
# 진입 문턱 — 모델 자신의 확률 분포에서 환산 (절대 확률 이식 금지)
# ──────────────────────────────────────────────────────────────────────────────

def test_threshold_is_resolved_per_model_from_quantile():
    from CAiModelBaker.core.model_engine import threshold_for_model

    class _M:
        def __init__(self, center):
            self.center = center

        def predict(self, X):
            return self.center + np.linspace(-0.05, 0.05, len(X))

    X = pd.DataFrame({"f": np.arange(1001)})
    params = {"buy_quantile": 0.95, "buy_threshold": 0.55}
    lo, hi = threshold_for_model(_M(0.40), X, params), threshold_for_model(_M(0.50), X, params)
    # 같은 '상위 5%' 가 모델마다 다른 절대 확률로 환산된다 — 분포가 0.40 근처인 모델에 0.55 를 쓰면 진입이 0 건이다
    assert abs(lo - (0.40 + 0.045)) < 1e-9 and abs(hi - (0.50 + 0.045)) < 1e-9
    assert threshold_for_model(_M(0.40), X, {"buy_threshold": 0.55}) == 0.55   # 구 설정은 절대값 그대로


def test_quantile_threshold_defaults_have_a_single_source():
    """분위수 문턱 기본 범위는 DEFAULT_OPTUNA_CONFIG 한 곳에만 있다 — 엔진·화면이 숫자를 따로 들고 있으면 어긋난다."""
    from shared.features.feature_registry import DEFAULT_OPTUNA_CONFIG
    d = DEFAULT_OPTUNA_CONFIG["buy_quantile_range"]
    assert 0.5 < d["user_min"] < d["user_max"] < 1.0 and DEFAULT_OPTUNA_CONFIG["threshold_mode"] == "quantile"

    tree = ast.parse(open(os.path.join(_ROOT, "CAiModelBaker/core/model_engine.py"), encoding="utf-8").read())
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "get_range"
                and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "buy_quantile_range"):
            assert not any(isinstance(a, ast.Constant) and isinstance(a.value, float) for a in n.args[1:]), \
                "엔진이 분위수 기본값을 숫자로 직접 들고 있다"
    js = open(os.path.join(_ROOT, "CAiModelBaker/web_dashboard/static/features.js"), encoding="utf-8").read()
    assert "defaultValue:" not in js, "화면이 엔진 기본값을 숫자로 복사해 들고 있다 — data.optuna_defaults 를 쓸 것"


def test_quantile_threshold_is_calibrated_out_of_sample():
    """분위수 문턱은 표본 밖 예측으로 환산해야 처음 보는 데이터에서도 '상위 q%' 로 동작한다.

    2026-10-07 (069500 15m): 학습 행 자신의 예측으로 '상위 14.6%' 를 잡았더니 홀드아웃에서 2.0% 만 넘었다.
    잡음뿐인 데이터에 과적합한 모델로 재현한다 — 학습 행 기준 문턱은 새 데이터에서 진입이 고갈된다.
    """
    import lightgbm as lgb
    from CAiModelBaker.core.model_engine import threshold_for_model, NUM_BOOST_ROUND
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(4000, 6)), columns=[f"f{i}" for i in range(6)])
    y = pd.Series(rng.integers(0, 2, 4000))
    X_new = pd.DataFrame(rng.normal(size=(4000, 6)), columns=X.columns)
    params = {"objective": "binary", "learning_rate": 0.1, "max_depth": 8, "num_leaves": 63,
              "min_child_samples": 5, "verbose": -1, "seed": 1}
    model = lgb.train(params, lgb.Dataset(X, label=y), num_boost_round=NUM_BOOST_ROUND)
    q = {"buy_quantile": 0.85}
    in_sample = threshold_for_model(model, X, q)                                   # 종전 방식 (대체 경로)
    oof = threshold_for_model(model, X, q, lgb_params=params, y_train=y, purge=6)
    rate = lambda th: float((model.predict(X_new) >= th).mean())
    r_in, r_oof = rate(in_sample), rate(oof)
    assert r_in < 0.12, f"재현 실패: 학습 행 기준 문턱의 새 데이터 진입 비율 {r_in:.3f} 이 의도(15%)보다 낮아야 한다"
    assert abs(r_oof - 0.15) < 0.03, f"표본 밖 문턱의 새 데이터 진입 비율 {r_oof:.3f} 이 의도(15%)와 동떨어짐"
