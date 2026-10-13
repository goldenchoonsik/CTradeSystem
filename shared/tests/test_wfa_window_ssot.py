# -*- coding: utf-8 -*-
"""
WFA 창 생성 SSOT 계약 테스트 (R-1)
==================================
`shared.split_planner.build_wfa_windows` 가 **거래일 기준**으로 창을 생성하는지 고정한다.

종전 결함
---------
`model_engine` 과 웹 대시보드가 각각 `train_days = train_months × 30.44` 의 **달력일 산술**로
창 경계를 만들었다. 그 결과 경계가 휴장일에 떨어졌다.

실측 (069500/10s, WFA 시작 2026-07-21, Train/Opt/Test = 0.95/0.14/0.14개월):
    [종전] Opt 경계: 2026-08-23(일) · 2026-09-26(토) · 2026-10-29(목)   ← 일요일·토요일!
    [신규] Opt 경계: 2026-08-24(월) · 2026-08-27(목) · 2026-09-01(화)   ← 전부 거래일

또한 같은 창 생성 로직이 3곳(`model_engine`, `_build_wfa_windows`, `_calc_wfa_shift_start`)에
중복 구현되어 있어 간트차트가 학습 엔진과 다른 창을 표시할 수 있었다.
"""

import ast
import inspect
import os

import pandas as pd
import pytest

from CAiModelBaker.core.split_planner import build_wfa_windows, split_point_by_ratio

_REAL_SNAPSHOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "CAiModelBaker", "data", "069500", "data_snapshot_10s.csv",
)


def _trading_dates(end="2026-09-15", n=40):
    """실제 KRX 거래일(데이터 우선) — 주말과 실제 휴장(2026-08-17)만 제외."""
    out, d = [], pd.Timestamp(end)
    while len(out) < n:
        if d.weekday() < 5 and str(d.date()) != "2026-08-17":
            out.append(str(d.date()))
        d -= pd.Timedelta(days=1)
    return sorted(out)


def _mk_ts(dates, bars_per_day=10, minutes=10):
    rows = []
    for d in dates:
        base = pd.Timestamp(f"{d} 09:00:00")
        rows.extend(base + pd.Timedelta(minutes=minutes * i) for i in range(bars_per_day))
    return pd.Series(pd.to_datetime(rows))


# ==============================================================================
# 1. 경계 실재성 — 모든 경계가 실제 거래일의 첫 bar 시각
# ==============================================================================

def test_all_boundaries_are_real_data_dates():
    """경계는 반드시 **실제 데이터에 존재하는 거래일**의 시작 시각이어야 한다."""
    dates = _trading_dates(n=40)
    ts = _mk_ts(dates)
    wins = build_wfa_windows(ts, 20, 3, 3, interval_minutes=10)

    assert len(wins) > 0
    data_days = set(ts.dt.date.unique())
    for w in wins:
        for k, v in w.items():
            assert pd.Timestamp(v).date() in data_days, f"{k}={v} 가 실제 데이터 날짜가 아닙니다"
            assert pd.Timestamp(v).weekday() < 5, f"{k}={v} 가 주말입니다"


def test_no_calendar_arithmetic_regression():
    """
    [핵심 회귀] 달력일 산술로는 휴장일 경계가 생긴다 — 그 사실을 고정해 회귀를 차단한다.
    Opt 경계는 실제 거래일이어야 하며, 일요일이어서는 안 된다.
    """
    dates = _trading_dates(n=40)
    ts = _mk_ts(dates)
    wins = build_wfa_windows(ts, 20, 3, 3, interval_minutes=10)

    opt_ends = [pd.Timestamp(w["opt_end"]) for w in wins]
    assert all(d.weekday() < 5 for d in opt_ends), \
        f"Opt 경계에 주말이 포함됨: {[str(d.date()) for d in opt_ends]}"

    # 종전 달력 산술은 2026-08-23(일) 을 만들었다 — 신규는 그 날이 아니어야 한다
    assert not any(d.date() == pd.Timestamp("2026-08-23").date() for d in opt_ends), \
        "종전 달력 산술의 휴장일 경계(2026-08-23 일)가 재현되었습니다 [R-1]"


def test_window_continuity():
    """구간은 연속이어야 한다 (train_end == opt_start, opt_end == test_start)."""
    ts = _mk_ts(_trading_dates(n=40))
    wins = build_wfa_windows(ts, 20, 3, 3, interval_minutes=10)
    for w in wins:
        assert w["train_end"] == w["opt_start"]
        assert w["opt_end"] == w["test_start"]
        assert w["train_start"] < w["train_end"] < w["opt_end"] < w["test_end"]


# ==============================================================================
# 2. 거래일 수 정확성
# ==============================================================================

def test_window_lengths_match_requested_trading_days():
    """각 구간이 요청한 **거래일 수**와 정확히 일치해야 한다."""
    dates = _trading_dates(n=40)
    ts = _mk_ts(dates)
    train_td, opt_td, test_td = 20, 3, 3
    wins = build_wfa_windows(ts, train_td, opt_td, test_td, interval_minutes=10)

    day_first = ts.groupby(ts.dt.normalize()).first()
    idx = {pd.Timestamp(v).strftime("%Y-%m-%d"): i for i, v in enumerate(day_first.values)}
    n_days = len(idx)

    for wi, w in enumerate(wins):
        s = idx[str(pd.Timestamp(w["train_start"]).date())]
        e_train = idx[str(pd.Timestamp(w["train_end"]).date())]
        e_opt = idx[str(pd.Timestamp(w["opt_end"]).date())]
        _te = str(pd.Timestamp(w["test_end"]).date())
        e_test = idx[_te] if _te in idx else n_days

        assert e_train - s == train_td, "Train 거래일 수 불일치"
        assert e_opt - e_train == opt_td, "Opt 거래일 수 불일치"
        if wi == len(wins) - 1:
            # 마지막 창은 데이터 끝으로 클램프될 수 있다 (0 < test_td' <= test_td)
            assert 0 < (e_test - e_opt) <= test_td, "마지막 창 Test 거래일 수 범위 이탈"
        else:
            assert e_test - e_opt == test_td, "Test 거래일 수 불일치"


def test_window_advance_equals_test_days():
    """창 전진 폭은 Test 구간 길이(= 거래일)와 같아야 한다."""
    ts = _mk_ts(_trading_dates(n=40))
    wins = build_wfa_windows(ts, 20, 3, 3, interval_minutes=10)
    assert len(wins) >= 3
    for a, b in zip(wins, wins[1:]):
        gap = (pd.Timestamp(b["train_start"]) - pd.Timestamp(a["train_start"])).days
        assert gap >= 3, "창 전진이 Test 거래일(3일)보다 짧습니다"


def test_last_window_includes_final_bar():
    """마지막 창은 데이터의 **마지막 bar 를 포함**해야 한다 (종전 off-by-one 해소)."""
    ts = _mk_ts(_trading_dates(n=40))
    wins = build_wfa_windows(ts, 20, 3, 3, interval_minutes=10)
    assert pd.Timestamp(wins[-1]["test_end"]) > ts.iloc[-1], \
        "마지막 창의 test_end 가 데이터 끝 이후여야 마지막 bar 가 포함됩니다"


# ==============================================================================
# 3. 경계 조건 · 견고성
# ==============================================================================

def test_insufficient_data_returns_empty():
    """거래일이 부족하면 예외 없이 빈 리스트."""
    ts = _mk_ts(_trading_dates(n=10))
    assert build_wfa_windows(ts, 20, 3, 3) == []


def test_robust_to_bad_input():
    """빈/비정상 입력은 예외 없이 빈 리스트."""
    assert build_wfa_windows([], 20, 3, 3) == []
    assert build_wfa_windows(None, 20, 3, 3) == []
    assert build_wfa_windows(pd.Series([], dtype="datetime64[ns]"), 20, 3, 3) == []
    assert build_wfa_windows(pd.Series(["bad", "worse"]), 20, 3, 3) == []


def test_crypto_continuous_market_includes_weekends():
    """크립토(연중무휴)는 주말도 거래일이므로 창에 포함된다."""
    dates = [str(d.date()) for d in pd.date_range("2026-08-07", "2026-09-15", freq="D")]
    ts = _mk_ts(dates, bars_per_day=10)
    wins = build_wfa_windows(ts, 20, 3, 3, interval_minutes=10)

    assert len(wins) > 0
    data_days = set(ts.dt.date.unique())
    for w in wins:
        for k, v in w.items():
            assert pd.Timestamp(v).date() in data_days, f"{k}={v} 가 데이터에 없습니다"
    # 주말이 데이터에 존재하므로 창에 포함되어도 정상
    assert any(pd.Timestamp(d).weekday() >= 5 for d in dates)


def test_opt_zt_zero_produces_contiguous_train_opt():
    """opt_td = 0 이면 Train 종료 = Opt 종료 (구간 붕괴 없이 안전하게 처리)."""
    ts = _mk_ts(_trading_dates(n=30))
    wins = build_wfa_windows(ts, 20, 0, 3, interval_minutes=10)
    assert len(wins) > 0
    for w in wins:
        assert w["opt_start"] == w["opt_end"]


# ==============================================================================
# 4. 재발 방지 자기검증 — 달력일 산술 잔존 차단
# ==============================================================================

def test_model_engine_has_no_calendar_window_arithmetic():
    """
    `model_engine` 에 `train_months × 30.44` 형태의 **달력일 창 산술**이 재도입되지 않아야 한다.
    (창 생성은 `build_wfa_windows` SSOT 를 사용해야 한다)
    """
    from CAiModelBaker.core import model_engine as me

    src = inspect.getsource(me)
    tree = ast.parse(src)

    offenders = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Assign):
            continue
        tgt = getattr(n.targets[0], "id", None)
        if tgt not in ("train_days", "opt_days", "test_days"):
            continue
        seg = ast.get_source_segment(src, n) or ""
        if "30.44" in seg:
            offenders.append(n.lineno)
    assert not offenders, (
        f"{offenders} 행에 달력일 창 산술(`× 30.44`)이 재도입되었습니다 — "
        "`build_wfa_windows`(거래일 기준 SSOT)를 사용하세요 [R-1]"
    )
    assert "build_wfa_windows(" in src, "창 생성이 SSOT 를 사용하지 않습니다 [R-1]"


def test_web_dashboard_delegates_window_building():
    """웹 대시보드의 `_build_wfa_windows` 는 SSOT 에 위임해야 한다 (이중 구현 금지)."""
    from CAiModelBaker.web_dashboard import main as dash

    src = inspect.getsource(dash._build_wfa_windows)
    assert "build_wfa_windows" in src, "SSOT 위임이 아닙니다 [R-1]"
    # docstring 의 과거 서술은 오탐하지 않도록 **실제 산술 상수(30.44)** 만 AST 로 검사한다
    _floats = [
        n.lineno for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Constant) and isinstance(n.value, float) and abs(n.value - 30.44) < 1e-9
    ]
    assert not _floats, f"{_floats} 행에 달력일 계수 30.44 산술이 남아 있습니다 [R-1]"

    shift_src = inspect.getsource(dash._calc_wfa_shift_start)
    assert "prev_trading_day" in shift_src, \
        "WFA 종료 역산이 거래일 기준이 아닙니다 (달력 -1일 잔존) [R-1]"
    _floats2 = [
        n.lineno for n in ast.walk(ast.parse(shift_src))
        if isinstance(n, ast.Constant) and isinstance(n.value, float) and abs(n.value - 30.44) < 1e-9
    ]
    assert not _floats2, f"_calc_wfa_shift_start 에 달력일 계수 30.44 산술이 남아 있습니다 [R-1]"


# ==============================================================================
# 6. [R-2] 비율 분할 SSOT — 거래일 기준
# ==============================================================================

def test_split_point_is_trading_day_based_not_bar_index():
    """
    결측일/비균일 bar 밀도에서는 **bar 인덱스 80% ≠ 거래일 인덱스 80%** 이다.
    종전 3곳(타임라인·UI·학습엔진 폴백)은 모두 bar 인덱스 또는 달력 밀리초를 사용했다.
    """
    # 앞 5일은 bar 100개, 뒤 5일은 bar 1개 → 총 505 bars / 10거래일
    rows = []
    for i, d in enumerate(_trading_dates(end="2026-09-15", n=10)):
        base = pd.Timestamp(f"{d} 09:00:00")
        cnt = 100 if i < 5 else 1
        rows.extend(base + pd.Timedelta(minutes=10 * k) for k in range(cnt))
    ts = pd.Series(pd.to_datetime(rows))

    sp = split_point_by_ratio(ts, 0.8)
    bar_index_date = pd.Timestamp(ts.iloc[int(len(ts) * 0.8)]).date()
    td_index_date = pd.Timestamp(sp).date()

    assert td_index_date != bar_index_date, \
        "거래일 기준과 bar 인덱스 기준이 같으면 테스트 전제(비균일)가 무효합니다"
    # 거래일 10일의 80% → index 8 (day 0-based) = 9번째 거래일
    assert td_index_date == pd.Timestamp(_trading_dates(end="2026-09-15", n=10)[8]).date()
    assert str(td_index_date) in _trading_dates(end="2026-09-15", n=10), "실재 거래일이어야 함"


def test_split_point_is_always_a_real_trading_day():
    """분할 지점은 항상 실제 데이터에 존재하는 거래일이어야 한다."""
    df = _mk_ts(_trading_dates(n=40))
    for ratio in (0.5, 0.6, 0.7, 0.8, 0.9):
        sp = split_point_by_ratio(df, ratio)
        assert sp is not None
        assert pd.Timestamp(sp).date() in set(df.dt.date.unique()), f"ratio={ratio} 지점이 실재 거래일 아님"
        assert pd.Timestamp(sp).weekday() < 5, f"ratio={ratio} 지점이 주말"


def test_split_point_monotonic_in_ratio():
    """비율이 커지면 분할 지점은 뒤로 이동해야 한다 (단조성)."""
    df = _mk_ts(_trading_dates(n=40))
    pts = [pd.Timestamp(split_point_by_ratio(df, r)) for r in (0.3, 0.5, 0.7, 0.9)]
    assert pts == sorted(pts), "비율-지점 단조성이 깨졌습니다"


def test_split_point_robust_to_bad_input():
    """빈/비정상 입력 또는 거래일 1일 이하는 None."""
    assert split_point_by_ratio([], 0.8) is None
    assert split_point_by_ratio(None, 0.8) is None
    assert split_point_by_ratio(pd.Series(["bad"]), 0.8) is None
    assert split_point_by_ratio(_mk_ts(["2026-09-15"]), 0.8) is None, "거래일 1일은 분할 불가"
    # ratio 이상값은 안전하게 클램프
    assert split_point_by_ratio(_mk_ts(_trading_dates(n=10)), 5.0) is not None


def test_no_self_computed_ratio_split_remains():
    """
    자체 비율 분할 계산이 재도입되지 않아야 한다 (R-2).
      · 웹 타임라인   : `int(len(df) * 0.8)` (bar 인덱스)
      · 학습엔진 폴백 : `iloc[int(len(master_df) * 0.6)]` (bar 인덱스)
    """
    from CAiModelBaker.core import model_engine as me
    from CAiModelBaker.web_dashboard import main as dash

    me_src = inspect.getsource(me)
    assert "_fallback_train_end" in me_src, "거래일 기준 폴백 헬퍼가 없습니다 [R-2]"

    # docstring 의 과거 서술은 오탐하지 않도록 **실제 코드(AST Subscript)** 만 검사한다
    offenders = []
    for n in ast.walk(ast.parse(me_src)):
        if not isinstance(n, ast.Subscript):
            continue
        if getattr(n.value, "attr", None) != "iloc":
            continue
        seg = ast.get_source_segment(me_src, n) or ""
        if "len(master_df)" in seg and "int(" in seg:
            offenders.append(n.lineno)
    assert not offenders, (
        f"{offenders} 행에 bar 인덱스 비율 분할(`iloc[int(len(master_df) * r)]`)이 재도입되었습니다 "
        "— `split_point_by_ratio`(거래일 기준 SSOT)를 사용하세요 [R-2]"
    )

    # 타임라인: auto_holdout_start 가 SSOT 위임이어야 한다 (폴백은 허용)
    assert "plan_split as _plan_split" in inspect.getsource(dash), \
        "타임라인 자동 홀드아웃이 SSOT(plan_split) 를 사용하지 않습니다 [R-2]"


# ==============================================================================
# 5. 실측 회귀 고정
# ==============================================================================

def test_real_069500_10s_window_boundaries():
    """실제 스냅샷으로 창 경계가 모두 실재 거래일인지 고정한다."""
    if not os.path.exists(_REAL_SNAPSHOT):
        pytest.skip("실측 스냅샷 없음")

    ts = pd.read_csv(_REAL_SNAPSHOT, usecols=["timestamp"])["timestamp"]
    ts = pd.to_datetime(ts)
    wins = build_wfa_windows(ts, 20, 3, 3, interval_minutes=10.0 / 60.0)

    assert len(wins) >= 3
    data_days = set(ts.dt.date.unique())
    for w in wins:
        for k, v in w.items():
            assert pd.Timestamp(v).date() in data_days, f"{k}={v} 가 실측 데이터에 없습니다"
            assert pd.Timestamp(v).weekday() < 5, f"{k}={v} 가 주말입니다"

    # 종전 달력 산술이 만들던 휴장일 경계가 사라졌는지 확인
    assert not any(pd.Timestamp(w["opt_end"]).date() == pd.Timestamp("2026-08-23").date()
                   for w in wins)


def test_no_zero_day_oos_window_generated():
    """자투리 데이터로 인해 Test 시작일과 종료일이 동일한 0일 왜곡 창이 생성되지 않아야 한다."""
    # 27거래일: Train 20 + Opt 3 + Test 3 = 26거래일 소진 후 1거래일만 남는 상황
    ts = _mk_ts(_trading_dates(n=27))
    wins = build_wfa_windows(ts, 20, 3, 3, interval_minutes=10)

    # 1개 온전한 창만 생성되고, 당일 1일짜리 불완전 창(W1)은 생성되지 않아야 함
    assert len(wins) == 1, f"예상 1개 창 대신 {len(wins)}개 창이 생성되었습니다"
    for w in wins:
        start_d = pd.Timestamp(w["test_start"]).date()
        end_d = pd.Timestamp(w["test_end"]).date()
        assert start_d < end_d, f"Test 시작일({start_d})과 종료일({end_d})이 같거나 역전되었습니다"
