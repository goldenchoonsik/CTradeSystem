"""CAiModelBaker 학습 파이프라인 수정(FIX-2/3/4/9/10/11/12/13/14/16) 회귀 테스트."""
import os
import re
import sys
import inspect

import numpy as np
import pandas as pd
import pytest

_CUR = os.path.dirname(os.path.abspath(__file__))
_BAKER = os.path.dirname(_CUR)
_ROOT = os.path.dirname(_BAKER)
for _p in (_ROOT,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CAiModelBaker.core import model_engine as me  # noqa: E402
from CAiModelBaker.core import physics_constraint as pc  # noqa: E402
from CAiModelBaker.core import backtester as bt  # noqa: E402

_ME_SRC = inspect.getsource(me)
_BT_SRC = inspect.getsource(bt)


def _krx_df(n_days=2, bars=54, close=None, atr_mult=100.0):
    rows = []
    for d in range(n_days):
        base = pd.Timestamp("2026-03-02 09:00") + pd.Timedelta(days=d)
        for i in range(bars):
            rows.append(base + pd.Timedelta(minutes=10 * i))
    ts = pd.Series(rows)
    n = len(ts)
    c = np.full(n, 100.0) if close is None else np.asarray(close, dtype=float)
    return pd.DataFrame({"timestamp": ts, "target_close": c, "target_low": c, "target_atr": np.full(n, 1e-3)})


# ---- FIX-10 : KRX 타임아웃 라벨이 predict_ahead 를 넘지 않는다 ----
def test_krx_timeout_label_capped_by_predict_ahead():
    bars = 30  # 09:00~13:50 (모두 15:18 이전)
    n = 2 * bars
    close = np.full(n, 100.0)
    # 각 일자 마지막 봉만 크게 상승 -> 장마감까지 늘리면 상승 라벨, pa=2 면 미충족
    close[bars - 1] = 200.0
    close[2 * bars - 1] = 200.0
    df = _krx_df(bars=bars, close=close)
    df["target_atr"] = 1e6  # 장벽 도달 불가 (TP 장벽이 매우 멀다, SL 장벽은 매우 낮음)
    # SL 장벽이 close - 1e6*sl 이라 항상 SL 조건이 참이 되므로 stop_loss 를 0 에 가깝게 못 쓰고 ATR 을 작게 둔다
    df["target_atr"] = 1e-9
    # TP/SL 가 ATR 대비 배수라 1e-9*배수는 사실상 0 -> 장벽 즉시 도달. 배수를 크게 줘서 도달 불가로 만든다.
    lab = me.triple_barrier_labeling(df, 2, 1e12, 1e12, 0.001, market_type="KRX")
    assert lab[: bars - 4].sum() == 0  # 장마감 종가(200) 를 참조하면 1 이 되던 구간


def test_krx_timeout_label_afml_capped():
    bars = 30
    close = np.full(2 * bars, 100.0)
    close[bars - 1] = 200.0
    close[2 * bars - 1] = 200.0
    df = _krx_df(bars=bars, close=close)
    df["target_atr"] = 1e-9
    prim, meta = me.triple_barrier_labeling_afml(df, 2, 1e12, 1e12, 0.001, max_hold_candles=2, market_type="KRX")
    assert meta[: bars - 4].sum() == 0


# ---- FIX-9 : 최종 학습셋 purge / market_type 전달 ----
def test_build_final_train_df_purges_tail_and_uses_only_inside_slice():
    n = 100
    ts = pd.date_range("2026-01-01", periods=n, freq="10min")
    c = np.linspace(100, 110, n)
    df = pd.DataFrame({"timestamp": ts, "target_close": c, "target_low": c, "target_atr": np.full(n, 0.5)})
    t_end = ts[60]
    out = me.build_final_train_df(df, ts[0], t_end, 5, 1.0, 1.0, 0.0)
    assert len(out) == 60 - 5
    assert out["timestamp"].max() == ts[54]
    assert out["timestamp"].max() < t_end - pd.Timedelta(minutes=10 * 4)


def test_build_final_train_df_excludes_warmup_when_intraday():
    n = 60
    ts = pd.date_range("2026-03-02 09:00", periods=n, freq="1min")
    c = np.full(n, 100.0)
    df = pd.DataFrame({"timestamp": ts, "target_close": c, "target_low": c, "target_atr": np.full(n, 1.0),
                       "is_warmup": [i < 10 for i in range(n)]})
    out = me.build_final_train_df(df, ts[0], ts[-1] + pd.Timedelta(minutes=1), 3, 1.0, 1.0, 0.0,
                                  market_type="KRX", exclude_warmup=True)
    assert not out["is_warmup"].any()
    assert len(out) == n - 10 - 3


def test_final_label_calls_pass_market_type():
    # 최종 라벨 호출은 build_final_train_df(market_type=...) 경유, 직접 호출 잔존 금지
    assert "master_df['target'] = triple_barrier_labeling(master_df" not in _ME_SRC
    assert _ME_SRC.count("build_final_train_df(") >= 3  # def + WFA + 비WFA


# ---- FIX-3/4 : 라운드 상수 통일 ----
def test_single_boost_round_constant():
    assert me.NUM_BOOST_ROUND == 30
    assert not re.search(r"num_boost_round=\d+", _ME_SRC), "리터럴 라운드 잔존"


# ---- FIX-16 : outer best trial 방어 ----
def test_pick_best_outer_trial_handles_all_minus99():
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    st = optuna.create_study(direction="maximize")
    st.optimize(lambda t: -99.0, n_trials=3)
    assert me.pick_best_outer_trial(st) is None

    def obj(t):
        t.suggest_float("x", 0, 1)
        if t.number == 1:
            t.set_user_attr("inner_best_params", {"a": 1})
        return -99.0 + t.number
    st2 = optuna.create_study(direction="maximize")
    st2.optimize(obj, n_trials=3)
    best = me.pick_best_outer_trial(st2)
    assert best is not None and best.number == 1


def test_bare_except_replaced_with_logging():
    assert not re.search(r"except\s*:\s*return", _ME_SRC)
    assert not re.search(r"except:\s*\n\s*return None", _ME_SRC)
    assert "_logger.exception" in _ME_SRC


# ---- FIX-11 : WFA 집계는 창 정렬 + OOS 미사용 ----
def test_wfa_aggregation_not_using_oos_and_index_aligned():
    assert "raw_weights = window_best_scores" in _ME_SRC
    assert "window_test_scores if any" not in _ME_SRC
    assert "window_best_idx.append(w_idx)" in _ME_SRC
    assert "window_test_profits[w_idx]" in _ME_SRC  # dict(창 인덱스 키)


# ---- FIX-12 / 13 ----
def test_seed_offsets_and_trade_log_label():
    assert "TPESampler(seed=seed + outer_trial.number)" in _ME_SRC
    assert "TPESampler(seed=seed + (w_idx + 1) * 10007 + outer_trial.number)" in _ME_SRC
    assert "'verbose': -1, 'seed': 42, 'n_jobs': -1, 'feature_pre_filter': False" not in _ME_SRC
    assert "NOT OOS" in _ME_SRC


def test_run_eval_backtest_cooldown_candles_and_krx_mask():
    assert "cooldown_until = idx + cd_candles" in _ME_SRC
    assert "cooldown_until = idx + cd\n" not in _ME_SRC
    assert "is_krx_entry_allowed(val_raw_timestamps[idx])" in _ME_SRC


# ---- FIX-14 : 마찰비용 헬퍼 통일 ----
def test_resolve_friction_rates_priority():
    assert pc.resolve_friction_rates({}) == (0.00015, 0.0002)
    cfg = {"OPTUNA_CONFIG": {"fee_upbit": 0.001, "slippage": 0.002}}
    assert pc.resolve_friction_rates(cfg) == (0.001, 0.002)
    cfg["FEE_UPBIT"] = 0.003
    cfg["SLIPPAGE"] = 0.004
    assert pc.resolve_friction_rates(cfg) == (0.003, 0.004)
    cfg["FEE_RATE"] = 0.005
    assert pc.resolve_friction_rates(cfg)[0] == 0.005
    # 왕복 비용은 공통 헬퍼(수수료·슬리피지 + 매도세)로만 계산한다
    assert "resolve_round_trip_cost(config)" in _ME_SRC and "resolve_round_trip_cost(config)" in _BT_SRC


# ---- FIX-2 : 홀드아웃 이전만 사용 ----
def test_slice_pre_holdout():
    ts = pd.date_range("2026-01-01", periods=100, freq="1D")
    df = pd.DataFrame({"timestamp": ts, "x": range(100)})
    out = pc.slice_pre_holdout(df, "2026-02-01")
    assert out["timestamp"].max() < pd.Timestamp("2026-02-01")
    assert len(pc.slice_pre_holdout(df, "")) == 100


def test_apply_physics_without_user_min_no_keyerror():
    n = 300
    rng = np.random.default_rng(0)
    c = 100 + np.cumsum(rng.normal(0, 0.1, n))
    df = pd.DataFrame({"high": c + 0.2, "low": c - 0.2, "close": c})
    cfg = {"INTERVAL": "10m", "OPTUNA_CONFIG": {
        "take_profit_range": {"min": 50.0, "max": 60.0},
        "stop_loss_range": {"min": 50.0},
        "predict_ahead_candles_range": {"min": 500},
        "max_hold_candles_range": {"min": 500},
    }}
    out = pc.apply_physics_constraints_to_config(cfg, df=df)
    assert "PHYSICS_CONSTRAINTS" in out
    # user_min 이 없을 때 임의 생성하지 않는다 (기존 get_range 의미 보존)
    assert "user_min" not in out["OPTUNA_CONFIG"]["take_profit_range"]


def test_physics_uses_pre_holdout_slice_in_callers():
    rl = open(os.path.join(_BAKER, "core", "research_loop.py"), encoding="utf-8").read()
    assert "calc_mean_atr_pct(df_full)" not in rl
    assert "_phys_df" in rl and "_phys_df" in _ME_SRC
    assert "calc_mean_atr_pct(master_df)" not in _ME_SRC


# ---- FIX-16 : 샤프 필드 ----
def test_sharpe_fields_documented():
    assert '"sharpe_basis": "per_trade_sqrt_n"' in _BT_SRC
    assert '"sharpe_annualized"' in _BT_SRC
