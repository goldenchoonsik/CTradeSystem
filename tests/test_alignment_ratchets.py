# -*- coding: utf-8 -*-
"""
정합 래칫 회귀 테스트 (T-6)
============================
2026-09-28 사고의 재발 방지 테스트다. 사고의 본질은 "방어 코드가 없었다"가 아니라
**방어 코드와 테스트가 운영 경로가 아닌 다른 구현을 보고 있었다**는 점이었다.
따라서 이 파일은 두 가지를 모두 검증한다.

  1. 탐지기 자체가 옳게 판정하는가 (합성 데이터로 양성/음성 모두 확인)
  2. **운영 경로가 그 탐지기를 실제로 통과해야만 산출물을 남기는가**
     (2번이 없으면 탐지기가 또 다른 포크 뒤에서 무력해진다)
"""

import os
import re
import sys
import numpy as np
import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.data_library.reference_alignment_audit import (
    ReferenceLookaheadError,
    assert_reference_alignment,
    audit_column,
)
from shared.data_library.interval_consistency_audit import (
    IntervalConsistencyError,
    assert_interval_consistency,
    audit_interval_consistency,
)


# ---------------------------------------------------------------------------
# 합성 데이터 생성기 — 참조가 타겟을 이끄는 실제 구조를 모사한다
# ---------------------------------------------------------------------------
def _make_lead_lag_frames(n_minutes=400, bars_per_minute=6, seed=7):
    """참조(1분봉)가 타겟(10초봉)을 선행하는 합성 시계열.

    참조 분봉 k 는 구간 [k, k+1) 의 움직임을 요약하고, 타겟은 그 움직임을
    같은 구간 안에서 따라간다. 즉 '참조 봉 k 의 정보는 구간 [k, k+1) 이 끝나야 확정된다'.
    """
    rng = np.random.default_rng(seed)
    minute_moves = rng.normal(0, 1.0, n_minutes)

    target = [1000.0]
    for mv in minute_moves:
        for _ in range(bars_per_minute):
            target.append(target[-1] + mv / bars_per_minute + rng.normal(0, 0.05))
    target = pd.Series(target[1:])

    # 참조 분봉 종가: 그 분의 움직임을 모두 반영한 값 (= 분이 끝나야 알 수 있다)
    ref_bar_close = 1000.0 + np.cumsum(minute_moves)

    ts = pd.date_range("2026-01-02 09:00:00", periods=len(target), freq="10s")
    return ts, target, ref_bar_close, bars_per_minute


def _broadcast(ref_bar_close, bars_per_minute, n_rows, *, leaked: bool):
    """분봉 값을 10초 행으로 펼친다.

    leaked=False: 분 k 의 값은 분이 끝난 뒤(= 다음 분 첫 행)부터 보인다 — 올바른 정합
    leaked=True : 분 k 의 값이 분 k 의 첫 행부터 보인다 — 봉 시작 라벨을 그대로 붙인 경우
    """
    out = np.empty(n_rows)
    out[:] = np.nan
    for k, v in enumerate(ref_bar_close):
        start = (k if leaked else k + 1) * bars_per_minute
        if start >= n_rows:
            break
        out[start:start + bars_per_minute] = v
    return pd.Series(out).ffill().bfill()


def _frame(leaked: bool, seed=7):
    ts, target, ref_bar, bpm = _make_lead_lag_frames(seed=seed)
    ref = _broadcast(ref_bar, bpm, len(target), leaked=leaked)
    return pd.DataFrame({"timestamp": ts, "target_close": target.to_numpy(), "ref_test_close": ref.to_numpy()})


# ---------------------------------------------------------------------------
# 1. 참조 타임스탬프 정합 탐지기
# ---------------------------------------------------------------------------
def test_correctly_aligned_reference_passes():
    """봉이 확정된 뒤에 값이 보이는 참조는 정상으로 판정되어야 한다."""
    df = _frame(leaked=False)
    f = audit_column(df["ref_test_close"], df["target_close"], "ref_test_close")
    assert not f.leaked, f.describe()
    assert abs(f.past_fit) > abs(f.future_fit), f.describe()


def test_bar_start_label_reference_is_detected():
    """봉 시작 라벨을 그대로 붙인 참조(= 1봉 미래참조)는 반드시 잡혀야 한다."""
    df = _frame(leaked=True)
    f = audit_column(df["ref_test_close"], df["target_close"], "ref_test_close")
    assert f.leaked, f.describe()
    assert abs(f.future_fit) > abs(f.past_fit), f.describe()


def test_partial_contamination_is_detected():
    """전구간 평균으로는 상쇄돼 보이는 부분 오염도 잡아야 한다.

    2026-09-28 사고에서 타겟의 역순 구간이 참조의 미래참조를 상쇄해
    전구간 판정을 '정상'으로 만들었다. 구간 분할 판정의 회귀 테스트다.
    """
    clean = _frame(leaked=False, seed=1)
    dirty = _frame(leaked=True, seed=2)
    dirty["timestamp"] = clean["timestamp"].iloc[-1] + pd.to_timedelta(
        np.arange(1, len(dirty) + 1) * 10, unit="s"
    )
    mixed = pd.concat([clean, dirty], ignore_index=True)

    f = audit_column(mixed["ref_test_close"], mixed["target_close"], "ref_test_close",
                     labels=mixed["timestamp"])
    assert f.leaked, f.describe()
    assert f.leaked_windows >= 1 and f.n_windows > 1, f.describe()


def test_assert_reference_alignment_raises_and_names_the_column():
    df = _frame(leaked=True)
    with pytest.raises(ReferenceLookaheadError) as exc:
        assert_reference_alignment(df, ref_columns=["ref_test_close"])
    assert "ref_test_close" in str(exc.value)


def test_constant_reference_is_held_not_passed():
    """데이터가 붙지 않아 상수인 참조는 '정상'이 아니라 '판정 보류'여야 한다."""
    df = _frame(leaked=False)
    df["ref_test_close"] = 100.0
    f = audit_column(df["ref_test_close"], df["target_close"], "ref_test_close")
    assert f.skipped_reason is not None
    assert not f.leaked


# ---------------------------------------------------------------------------
# 2. 상·하위 봉 집계 정합 탐지기
# ---------------------------------------------------------------------------
def _fine_coarse(reverse_within_bar: bool, n_minutes=120, bars=6):
    rng = np.random.default_rng(11)
    rows = []
    coarse = []
    t0 = pd.Timestamp("2026-01-02 09:00:00")
    price = 1000.0
    for k in range(n_minutes):
        path = price + np.cumsum(rng.normal(0, 1.0, bars))
        price = path[-1]
        seq = path[::-1] if reverse_within_bar else path
        for i, p in enumerate(seq):
            rows.append({
                "timestamp": t0 + pd.Timedelta(minutes=k, seconds=10 * i),
                "open": p, "high": max(path) + 0.5, "low": min(path) - 0.5, "close": p,
            })
        # 상위 봉은 '봉 확정 시각' 라벨 (하위 봉은 '봉 시작' 라벨)
        coarse.append({
            "timestamp": t0 + pd.Timedelta(minutes=k + 1),
            "open": path[0], "high": max(path) + 0.5, "low": min(path) - 0.5, "close": path[-1],
        })
    return pd.DataFrame(rows), pd.DataFrame(coarse)


def test_interval_consistency_passes_for_ordered_bars():
    fine, coarse = _fine_coarse(reverse_within_bar=False)
    f = audit_interval_consistency(fine, coarse, "TEST", "10s", "1m")
    assert f is not None and f.ok, f.describe() if f else "판정 보류"
    assert f.offset_bars == 1, f.describe()


def test_interval_consistency_detects_reversed_bars():
    """봉 내부 시간 순서 뒤집힘 — 행수·결측 검사로는 절대 드러나지 않는 결함."""
    fine, coarse = _fine_coarse(reverse_within_bar=True)
    f = audit_interval_consistency(fine, coarse, "TEST", "10s", "1m")
    assert f is not None and not f.ok, f.describe() if f else "판정 보류"
    assert f.reversed_rate > f.forward_rate, f.describe()
    assert len(f.bad_dates) > 0, "역순 일자가 보고되어야 한다"
    with pytest.raises(IntervalConsistencyError):
        assert_interval_consistency(fine, coarse, "TEST", "10s", "1m")


def _krx_1m_day(day="2026-10-06"):
    """KRX 하루치 bar_close 1분봉: 09:01~15:20 연속장 + 15:30 종가 동시호가 한 봉."""
    ts = list(pd.date_range(f"{day} 09:01", f"{day} 15:20", freq="1min")) + [pd.Timestamp(f"{day} 15:30")]
    px = np.arange(len(ts), dtype=float) + 1000.0
    return pd.DataFrame({"timestamp": ts, "open": px, "high": px + 0.5, "low": px - 0.5,
                         "close": px, "volume": np.ones(len(ts))})


def test_bar_close_minutes_resample_to_clock_aligned_bars_with_closing_auction():
    """KRX 분봉 집계 규약: [09:00,09:15) … [15:15,15:30] 의 하루 26봉, 시작 시각 라벨, 동시호가는 마지막 봉에 합산.

    bar_close 1분봉(09:01 = 09:00~09:01)을 구간 시작으로 오인해 묶으면 경계가 1분 밀리고
    동시호가가 혼자 27번째 봉(15:30)이 된다 — 2026-10-07 069500 15분봉 첫 빌드의 실제 결함.
    """
    from shared.data_library.candle_resampler import resample_candles
    m = _krx_1m_day()
    out = resample_candles(m, target_interval="15m", source_interval="1m", source_label="bar_close")
    assert len(out) == 26
    assert out.timestamp.iloc[0] == pd.Timestamp("2026-10-06 09:00")
    assert out.timestamp.iloc[-1] == pd.Timestamp("2026-10-06 15:15")
    first = m[m.timestamp <= pd.Timestamp("2026-10-06 09:15")]
    assert out.volume.iloc[0] == len(first) == 15
    assert out.close.iloc[0] == first.close.iloc[-1]
    assert out.close.iloc[-1] == m.close.iloc[-1], "마지막 봉 종가 = 종가 동시호가"

    naive = resample_candles(m, target_interval="15m", source_interval="1m")
    assert len(naive) == 27, "규약을 무시하면 동시호가가 별도 봉이 된다 (이 테스트의 대조군)"


def test_interval_audit_sees_sub_bar_boundary_shift_and_ledger_declares_it():
    """상위 봉 ±1 오프셋만으로는 '하위 봉 1개만큼 밀린 경계'를 못 본다 — 하위 이동까지 재야 한다."""
    from shared.data_library.candle_resampler import resample_candles
    from shared.config.market_policies import bar_bucket_offset, timestamp_convention, BAR_OPEN
    days = pd.concat([_krx_1m_day(d) for d in ("2026-10-05", "2026-10-06")], ignore_index=True)
    rng = np.random.default_rng(7)
    days["close"] = 1000 + np.cumsum(rng.normal(0, 1, len(days)))
    days["open"] = days["close"].shift(1).fillna(1000.0)
    days["high"] = days[["open", "close"]].max(axis=1) + rng.uniform(0, 1, len(days))
    days["low"] = days[["open", "close"]].min(axis=1) - rng.uniform(0, 1, len(days))

    good = resample_candles(days, "15m", source_interval="1m", source_label="bar_close")
    f = audit_interval_consistency(days, good, "TEST", "1m", "15m")
    assert f is not None and f.ok and f.fine_shift_bars == -1 and f.offset_bars == 0, f.describe()

    naive = resample_candles(days, "15m", source_interval="1m")
    f2 = audit_interval_consistency(days, naive, "TEST", "1m", "15m")
    assert f2 is not None and f2.fine_shift_bars == 0, f2.describe()

    # 원장: Creon 1분봉은 bar_close, 우리가 집계한 15분봉은 bar_open → 기대 (하위이동 -1, 오프셋 0)
    assert timestamp_convention("069500", "15m") == BAR_OPEN
    assert bar_bucket_offset("069500", "1m") == pd.Timedelta(minutes=1)
    assert bar_bucket_offset("069500", "15m") == pd.Timedelta(0)


def test_special_krx_sessions_observed_in_data_are_declared():
    """정규화 1분봉에 지연 개장(10시)·연장 마감(16:30 동시호가)이 관측된 날은 장 일정 원장에 선언돼 있어야 한다.

    선언이 없으면 초봉 그리드가 개장 전을 가짜 봉으로 채우고, 실전 집계기는 연장 마감 동시호가를 놓친다.
    """
    import os
    from shared.data_library.paths import normalized_path
    from shared.core.market_utils import krx_session
    path = normalized_path("candles_1m", "069500_1m.parquet")
    if not os.path.exists(path):
        pytest.skip("실데이터 없음")
    m = pd.read_parquet(path, columns=["timestamp"])
    g = pd.to_datetime(m["timestamp"]).groupby(pd.to_datetime(m["timestamp"]).dt.date).agg(["min", "max"])
    undeclared = []
    for d, r in g.iterrows():
        sess = krx_session(d)
        late_open = r["min"].time() >= pd.Timestamp("10:00").time()
        late_close = r["max"].time() > pd.Timestamp("15:30").time()
        if (late_open and sess.open.hour < 10) or (late_close and sess.close <= pd.Timestamp("15:30").time()):
            undeclared.append((str(d), r["min"].strftime("%H:%M"), r["max"].strftime("%H:%M")))
    assert not undeclared, f"krx_session_calendar.json 에 선언되지 않은 특수 거래일: {undeclared}"


# ---------------------------------------------------------------------------
# 3. 운영 경로가 실제로 래칫을 통과해야 한다 (사고의 본질에 대한 회귀 테스트)
# ---------------------------------------------------------------------------
def test_gold_snapshot_build_refuses_to_write_leaked_data(tmp_path, monkeypatch):
    """AutoLab Gold 스냅샷 빌드는 미래참조가 있으면 파일을 남기지 않아야 한다.

    탐지기를 직접 호출하는 테스트만 두면, 새 조립기 포크가 생겼을 때 그 포크는
    다시 그물 밖으로 나간다. 이 테스트는 **운영 writer** 를 통과시켜 그것을 막는다.
    """
    from CAiModelBaker.core import autolab_bridge

    leaked_df = _frame(leaked=True)
    out_path = tmp_path / "data_snapshot_10s.csv"

    class _StubComposer:
        def __init__(self, *a, **k):
            pass

        def compose(self, *a, **k):
            return leaked_df.copy()

    monkeypatch.setattr(autolab_bridge, "FeatureComposer", _StubComposer)
    monkeypatch.setattr(autolab_bridge.SymbolRelationsManager, "join_plan",
                        staticmethod(lambda s: {"refs": ["TEST"], "series": []}))
    monkeypatch.setattr(autolab_bridge.dpaths, "gold_snapshot_path",
                        lambda s, i: str(out_path))

    with pytest.raises(ReferenceLookaheadError):
        autolab_bridge.build_gold_snapshot("069500", "10s", local_log=lambda *_: None)

    assert not out_path.exists(), "미래참조가 감지됐는데 스냅샷이 디스크에 남았다"


# ---------------------------------------------------------------------------
# 4. 구조 래칫 — 새 조립기 포크가 다시 그물 밖으로 나가지 못하게 한다
# ---------------------------------------------------------------------------
import ast

_SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", "legacy", "scratch", "vultr_logs"}
# 정렬을 계산해도 되는 유일한 두 모듈: 규약 판정 지점과 결합 단일 진입점
_ALIGNMENT_OWNERS = {
    os.path.join("shared", "config", "market_policies.py"),
    os.path.join("shared", "data_library", "reference_staleness.py"),
}


def _production_py_files():
    for root, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        if os.path.basename(root) == "tests":
            continue
        for fn in files:
            if fn.endswith(".py") and not fn.startswith("test_"):
                yield os.path.join(root, fn)


def test_every_feature_composer_delegates_reference_attachment():
    """저장소에 있는 **모든** FeatureComposer 는 참조 결합을 단일 진입점에 위임해야 한다.

    2026-09-17 사고: 새 조립기가 추가되면서 `pd.merge_asof` 를 직접 불렀고,
    정렬 방어와 테스트는 옛 조립기만 보고 있었다. 이 테스트는 구현을 **탐색**해서
    검사하므로, 세 번째 포크가 생겨도 자동으로 감시 대상이 된다.
    """
    offenders = []
    found = 0
    for path in _production_py_files():
        try:
            src = open(path, encoding="utf-8").read()
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        has_composer = any(isinstance(n, ast.ClassDef) and n.name == "FeatureComposer"
                           for n in ast.walk(tree))
        if not has_composer:
            continue
        found += 1
        if "attach_reference" not in src:
            offenders.append(os.path.relpath(path, _ROOT))

    assert found >= 1, "FeatureComposer 구현을 하나도 찾지 못했습니다 (탐색 규칙을 확인하세요)"
    assert not offenders, (
        "참조 결합을 단일 진입점(shared.data_library.reference_staleness.attach_reference)에 "
        "위임하지 않는 조립기가 있습니다. 직접 merge 하면 타임스탬프 규약 보정이 빠집니다:\n  "
        + "\n  ".join(offenders)
    )


def test_alignment_shift_is_computed_in_exactly_one_place():
    """정렬 보정값을 계산하는 곳은 규약 판정 지점과 결합 진입점뿐이어야 한다.

    조립기나 빌더가 각자 시프트를 구현하면, 같은 심볼이 경로에 따라 0번/1번/2번 보정된다
    (2026-09-17 이전 상태가 정확히 그랬다).
    """
    offenders = []
    for path in _production_py_files():
        rel = os.path.relpath(path, _ROOT)
        if rel in _ALIGNMENT_OWNERS:
            continue
        try:
            src = open(path, encoding="utf-8").read()
        except OSError:
            continue
        for marker in ("reference_merge_shift", "bar_label_shift"):
            if marker in src:
                offenders.append(f"{rel} ({marker})")

    assert not offenders, (
        "정렬 보정을 직접 계산하는 모듈이 있습니다. 참조 결합은 attach_reference 를 통해서만 하세요:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 5. 학습 피처 ↔ 실전 공급원 계약
# ---------------------------------------------------------------------------
def test_live_provider_list_is_discovered_not_hardcoded():
    """실전이 지원하는 수집처는 CTrade 의 커넥터/파서 모듈에서 유도된다."""
    from shared.data_library.live_feed_contract import implemented_live_providers

    impl = implemented_live_providers()
    assert "KIS" in impl, "KIS 커넥터가 있는데 탐색되지 않았습니다 (탐색 규칙 확인)"
    assert impl, "구현된 수집처를 하나도 찾지 못했습니다"


def test_reference_without_live_implementation_is_refused(monkeypatch):
    """실시간 구현이 없는 참조에 의존하는 피처는 배포가 거부되어야 한다.

    2026-09-28: 연구 중이던 모델의 상위 피처가 cTrader CFD 기반이었는데
    CTrade 에 cTrader 실시간 구독 코드가 없었다. 백테스트만 좋고 실전에서는 죽는 피처다.
    (그 뒤 커넥터를 구현해 지금은 통과한다 — 그래서 '오늘의 구현 목록'이 아니라 **규칙**을 검증한다)
    """
    from shared.data_library.live_feed_contract import (
        LiveFeedContractError, audit_live_feature_support, assert_live_feature_support,
    )

    # 타겟 자체 피처는 매매 대상이므로 항상 공급 가능 (감사 대상 아님)
    assert audit_live_feature_support(["target_vwap_disparity", "krx_session_time"]) == []

    findings = audit_live_feature_support(["ref_005930_ret_1c"])
    assert findings and findings[0].supported, findings[0].describe() if findings else "판정 없음"

    # 그 수집처 구현이 사라진 상황을 만들면 반드시 거부해야 한다
    monkeypatch.setattr("shared.data_library.live_feed_contract.implemented_live_providers",
                        lambda *a, **k: {"KIS"})
    with pytest.raises(LiveFeedContractError):
        assert_live_feature_support(["ref_us_tech_100_ret_5c"])


# ---------------------------------------------------------------------------
# 6. 분 단위 틱의 '분 내부 순서' (10초봉 시간축의 근거)
# ---------------------------------------------------------------------------
def _tick_and_minutes(order: str, n_minutes=60, per_min=12, seed=3):
    """분 단위 타임스탬프 틱 + 그에 대응하는 1분봉(봉 확정 시각 라벨).

    order: 'forward' | 'reversed' | 'shuffled'
    """
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp("2026-01-02 09:00:00")
    ticks, minutes = [], []
    price = 1000.0
    for k in range(n_minutes):
        path = price + np.cumsum(rng.normal(0, 1.0, per_min))
        price = path[-1]
        if order == "reversed":
            seq = path[::-1]
        elif order == "shuffled":
            seq = path.copy()
            rng.shuffle(seq)
        else:
            seq = path
        for p in seq:
            ticks.append({"timestamp": t0 + pd.Timedelta(minutes=k), "upbit_close": p, "upbit_volume": 1.0})
        minutes.append({"timestamp": t0 + pd.Timedelta(minutes=k + 1),
                        "open": path[0], "close": path[-1],
                        "high": path.max(), "low": path.min()})
    return pd.DataFrame(ticks), pd.DataFrame(minutes)


def test_tick_minute_order_passes_for_chronological_ticks(monkeypatch):
    from shared.config import market_policies as mp
    from shared.data_library import tick_order_audit as toa

    monkeypatch.setattr(mp, "timestamp_convention", lambda s, i="": mp.BAR_CLOSE)
    ticks, minutes = _tick_and_minutes("forward")
    findings = toa.audit_tick_minute_order(ticks, minutes, "TEST")
    assert findings and all(f.usable for f in findings), [f.describe() for f in findings]


def test_tick_minute_order_detects_reversal_and_repair_fixes_it(monkeypatch):
    """분 내부 역순은 잡히고, 되돌리면 정상이 되어야 한다 (2026-09-28 069500 20거래일 사고)."""
    from shared.config import market_policies as mp
    from shared.data_library import tick_order_audit as toa

    monkeypatch.setattr(mp, "timestamp_convention", lambda s, i="": mp.BAR_CLOSE)
    ticks, minutes = _tick_and_minutes("reversed")
    findings = toa.audit_tick_minute_order(ticks, minutes, "TEST")
    assert findings and all(f.reversed for f in findings), [f.describe() for f in findings]

    with pytest.raises(toa.TickOrderError):
        toa.assert_tick_minute_order(ticks, minutes, "TEST")

    days = sorted({f.day for f in findings})
    repaired = toa.repair_minute_order(ticks, days)
    assert len(repaired) == len(ticks), "복구가 행 수를 바꾸면 안 된다"
    after = toa.audit_tick_minute_order(repaired, minutes, "TEST")
    assert after and all(f.usable for f in after), [f.describe() for f in after]


def test_scrambled_order_is_reported_not_silently_skipped(monkeypatch):
    """정방향도 역순도 아닌 '섞인' 날을 조용히 넘기면 그게 fail-open 이다.

    실제로 000660/005930 의 일부 날짜가 이 상태였고, 뒤집어도 복구되지 않는다.
    """
    from shared.config import market_policies as mp
    from shared.data_library import tick_order_audit as toa

    monkeypatch.setattr(mp, "timestamp_convention", lambda s, i="": mp.BAR_CLOSE)
    ticks, minutes = _tick_and_minutes("shuffled", per_min=30)
    findings = toa.audit_tick_minute_order(ticks, minutes, "TEST")
    assert findings, "섞인 날이 결과에서 빠지면 안 된다"
    assert all(f.verdict == toa.UNORDERED for f in findings), [f.describe() for f in findings]
    assert not any(f.reversed for f in findings), "섞인 날을 '역순'으로 오판하면 멀쩡한 데이터를 뒤집는다"
    with pytest.raises(toa.TickOrderError):
        toa.assert_tick_minute_order(ticks, minutes, "TEST")


# ---------------------------------------------------------------------------
# 7. 미래값 역류(bfill) 금지 — 결측을 숫자로 위장하지 않는다
# ---------------------------------------------------------------------------
def test_price_columns_are_never_backfilled():
    """참조 데이터가 시작되기 전 구간은 NaN 이어야 한다 (그 종목의 첫 가격이 아니라).

    2026-09-28: 운영 조립기가 가격 컬럼에 `bfill().ffill()` 를 걸어, 069500 스냅샷의
    2026-07-21~08-18 19거래일이 ref_000660_close = 1,773,000 (2026-08-27 의 첫 실제 값)
    으로 가득 차 있었다. 미래의 가격 수준이 과거로 역류한 것이다.

    2026-10-02: 결합 후 정제는 결측을 **아예 채우지 않는다**. 이어 쓰기는 결합 단계(attach_reference)가
    stale 상한 안에서만 한다 — 정제가 다시 ffill 하면 상한이 무효화한 칸이 되살아난다.
    """
    from CAiModelBaker.core.feature_composer import FeatureComposer

    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-02 09:00:00", periods=6, freq="1min"),
        "ref_test_close": [np.nan, np.nan, 100.0, 101.0, np.nan, 102.0],
        "ref_test_volume": [np.nan, np.nan, 5.0, 6.0, np.nan, 7.0],
        "ref_test_oi": [np.nan, np.nan, 9.0, 9.0, 9.0, np.inf],
    })
    out = FeatureComposer.sanitize_post_join(df)

    assert out["ref_test_close"].iloc[:2].isna().all(), \
        "참조 데이터 시작 전 구간이 미래 가격으로 채워졌다 (bfill)"
    assert np.isnan(out["ref_test_close"].iloc[4]), "정제가 결측을 이어 썼다 — stale 상한을 무력화한다"
    assert out["ref_test_volume"].iloc[:2].isna().all(), "수집 전 거래량이 0 으로 위장됐다"
    assert out["ref_test_oi"].iloc[:2].isna().all(), "이름 규칙 밖 수치(미결제약정)가 0 으로 위장됐다"
    assert np.isnan(out["ref_test_oi"].iloc[5]), "무한대는 NaN 이어야 한다"


def test_join_distinguishes_state_flow_and_bar_fields():
    """결합은 필드의 시간 의미로 빈 행을 다룬다 (2026-10-02).

    - 상태값(가격): stale 상한까지 직전 관측을 이어 쓰고, 넘으면 NaN
    - 흐름값(거래량): 원천 봉 하나의 양은 처음 알려진 행에 한 번만 — 빠진 봉 자리에 직전 봉 값을 복제하지 않는다.
      원천이 살아 있는 나머지 행은 0(새 체결 없음), 원천 시작 전·상한 초과는 NaN(모름)
    - 봉 내부 비율값(ofi): 처음 알려진 행에만, 나머지는 NaN
    """
    from shared.data_library.reference_staleness import merge_asof_with_stale_cap

    base = pd.DataFrame({"timestamp": pd.date_range("2026-01-02 09:00", periods=8, freq="1min")})
    ref = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-02 09:02", "2026-01-02 09:03"]),   # 09:04 이후 원천 없음
        "c": [100.0, 101.0], "v": [5.0, 6.0], "o": [0.5, -0.2],
    })
    out = merge_asof_with_stale_cap(base, ref, ["c", "v", "o"], cap=pd.Timedelta(minutes=2),
                                    flow_cols=["v"], bar_cols=["o"])
    # 행: 09:00 09:01 | 09:02 09:03 | 09:04 09:05 (상한 이내) | 09:06 09:07 (상한 초과)
    assert out["c"].iloc[:2].isna().all() and list(out["c"].iloc[2:6]) == [100.0, 101.0, 101.0, 101.0]
    assert out["c"].iloc[6:].isna().all()
    assert out["v"].iloc[:2].isna().all() and list(out["v"].iloc[2:6]) == [5.0, 6.0, 0.0, 0.0]
    assert out["v"].iloc[6:].isna().all()
    assert list(out["o"].iloc[2:4]) == [0.5, -0.2] and out["o"].drop(index=[2, 3]).isna().all()


def test_no_composer_backfills_price_columns():
    """구조 래칫: 어떤 조립기도 가격 컬럼을 bfill 하지 않는다.

    behavior 테스트만 두면 새 조립기 포크가 다시 bfill 을 들고 나타난다 (실제로 그랬다).
    """
    offenders = []
    for path in _production_py_files():
        try:
            src = open(path, encoding="utf-8").read()
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        if not any(isinstance(n, ast.ClassDef) and n.name == "FeatureComposer" for n in ast.walk(tree)):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("bfill", "backfill"):
                offenders.append(f"{os.path.relpath(path, _ROOT)}:{node.lineno}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "fillna"):
                for kw in node.keywords:
                    if (kw.arg == "method" and isinstance(kw.value, ast.Constant)
                            and kw.value.value in ("bfill", "backfill")):
                        offenders.append(f"{os.path.relpath(path, _ROOT)}:{node.lineno}")

    assert not offenders, (
        "조립기가 미래값을 과거로 역류시킵니다 (bfill). 없는 데이터는 NaN 으로 남기세요:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 8. 실시간 피드 준비성 판정은 '측정' 이어야 한다
# ---------------------------------------------------------------------------
def test_live_probe_has_no_hardcoded_symbol_list():
    """프로버가 심볼 이름 목록으로 판정하면 안 된다.

    2026-09-28: cTrader 심볼 7개 이름이 코드에 박혀 있었고, 확인 없이
    "cTrader 연동 정상 / LIVE_STREAMING / latency 15ms" 를 무조건 반환했다.
    실시간 구독 코드가 아예 없는 자산이 UI 에 초록 배지로 표시된 원인이다.
    """
    import shared.data_library.live_feed_prober as prober

    src = open(prober.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    # 종목 코드처럼 보이는 리터럴이 판정 코드에 있으면 안 된다 (문서/주석은 무관)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if re.fullmatch(r"[A-Z]{2,}_[A-Z0-9_]+", v) and v not in {
                "LIVE_READY", "RESEARCH_ONLY", "NO_DATA", "LIVE_STREAMING", "STANDBY_AUTH_OK",
                "CONNECTION_FAILED", "RESEARCH_FEED", "LIVE_OK", "MARKET_CLOSED_AUTH_OK",
                "CTRADE_SAFE_READ", "UNSUPPORTED", "NOT_CONFIGURED", "NO_RUNTIME_FEED",
                "NO_DATA_TRACE", "FEED_STALE", "CONNECTION_ERROR",
            }:
                offenders.append(v)
    assert not offenders, (
        "프로버에 종목/자산 이름 리터럴이 있습니다. 판정은 원장 선언과 실측으로 하세요:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )


def test_live_probe_reports_research_only_when_runtime_cannot_feed(monkeypatch):
    """원장이 실시간을 선언해도 런타임 구현이 없으면 '연구 전용' 이어야 한다."""
    from shared.data_library import live_feed_prober as lp

    monkeypatch.setattr(lp.LiveFeedProber, "is_ctrade_running", staticmethod(lambda: False))
    monkeypatch.setattr("shared.data_library.live_feed_contract.implemented_live_providers",
                        lambda *a, **k: {"KIS", "UPBIT"})

    res = lp.LiveFeedProber.probe_symbol("US_TECH_100", "GLOBAL_INDEX_CFD_PROXY")
    assert res["readiness"] == "RESEARCH_ONLY", res
    assert res["status_code"] == "NO_RUNTIME_FEED", res
    assert "CTRADER" in res["message"]


def test_live_probe_never_fabricates_latency():
    """재지 않은 값을 숫자로 내보내면 UI 가 근거 없는 초록 배지를 그린다."""
    from shared.data_library.live_feed_prober import LiveFeedProber

    for sym, mt in [("069500", "KRX_ETF"), ("US_TECH_100", "GLOBAL_INDEX_CFD_PROXY")]:
        res = LiveFeedProber.probe_symbol(sym, mt)
        assert res["latency_ms"] is None, f"{sym}: 측정하지 않은 지연시간이 값으로 나갔다"
        assert "data_age_sec" in res, f"{sym}: 실측 신선도가 결과에 없다"


# ---------------------------------------------------------------------------
# 9. cTrader 실시간 구독 커넥터
# ---------------------------------------------------------------------------
class _FakeSym:
    def __init__(self, symbol_id, symbol_name):
        self.symbolId = symbol_id
        self.symbolName = symbol_name


class _FakeSymbolsRes:
    def __init__(self, syms):
        self.symbol = syms


class _FakeSpot:
    def __init__(self, symbol_id, bid=None, ask=None):
        self.symbolId = symbol_id
        self.bid = bid or 0
        self.ask = ask or 0
        self._has = {"bid": bid is not None, "ask": ask is not None}

    def HasField(self, name):
        return self._has.get(name, False)


class _FakeClient:
    def __init__(self):
        self.sent = []

    def send(self, req):
        self.sent.append(req)


def test_ctrader_symbol_names_match_without_a_lookup_table():
    """원장 표기와 브로커 표기를 표 없이 이어야 한다.

    종전 `scripts/fetch_ctrader_history.py` 는 symbol_id/digits 를 7종 하드코딩했다.
    브로커가 심볼을 재배치하면 조용히 엉뚱한 종목을 받게 된다.
    """
    from CTrade.connectors.ctrader_connector import normalize_symbol_name as n

    assert n("US_TECH_100") == n("US TECH 100")
    assert n("HONG_KONG_50") == n("HONG KONG 50")
    assert n("US_500") == n("US 500")
    assert n("XAUUSD") == n("XAUUSD")
    assert n("US_TECH_100") != n("US_500")


def test_ctrader_resolves_symbol_ids_at_runtime(monkeypatch):
    from CTrade.connectors import ctrader_connector as cc

    monkeypatch.setenv("CTRADE_ACCOUNT_ID", "12345")
    conn = cc.CTraderConnector(symbols=["US_TECH_100", "US_500", "NOT_THERE"],
                               on_tick_callback=None, log=lambda *_: None)
    client = _FakeClient()
    conn._subscribe_resolved(client, _FakeSymbolsRes([
        _FakeSym(21501, "US TECH 100"),
        _FakeSym(21499, "US 500"),
        _FakeSym(41, "XAUUSD"),
    ]))

    assert conn._subscribed == ["US_TECH_100", "US_500"], conn._subscribed
    assert len(client.sent) == 1, "구독 요청은 한 번만 나가야 한다"
    assert sorted(client.sent[0].symbolId) == [21499, 21501]


def test_ctrader_spot_event_becomes_a_tick(monkeypatch):
    from CTrade.connectors import ctrader_connector as cc

    got = []
    conn = cc.CTraderConnector(symbols=["US_TECH_100"],
                               on_tick_callback=lambda s, p, t: got.append((s, p)),
                               log=lambda *_: None)
    conn._symbol_id_to_ledger = {21501: "US_TECH_100"}

    # 프로토콜 고정 배율(10^5)로 스케일된 정수 → 실제 가격
    conn._handle_spot(_FakeSpot(21501, bid=3064400000, ask=3064430000))
    assert got and got[0][0] == "US_TECH_100"
    assert 30643.0 < got[0][1] < 30645.0, got

    # 한쪽 호가만 와도 버리지 않는다
    got.clear()
    conn._handle_spot(_FakeSpot(21501, bid=3064400000))
    assert got and abs(got[0][1] - 30644.0) < 0.01, got

    # 구독하지 않은 심볼 id 는 무시
    got.clear()
    conn._handle_spot(_FakeSpot(99999, bid=1000))
    assert not got


def test_ctrader_connector_refuses_to_start_without_credentials(monkeypatch):
    """자격 증명이 없으면 조용히 실패해야 한다 — 엔진을 죽이지 않는다."""
    from CTrade.connectors import ctrader_connector as cc

    for key in ("CTRADER_CLIENT_ID", "CTRADER_SECRET", "CTRADER_ACCESS_TOKEN", "CTRADE_ACCOUNT_ID"):
        monkeypatch.delenv(key, raising=False)
    conn = cc.CTraderConnector(symbols=["US_TECH_100"], log=lambda *_: None)
    assert conn.start() is False
    assert conn.is_running is False


def test_ctrader_reference_features_now_pass_the_deploy_gate():
    """실시간 구현이 생겼으므로 cTrader 참조 피처가 배포 게이트를 통과해야 한다."""
    from shared.data_library.live_feed_contract import (
        audit_live_feature_support, implemented_live_providers,
    )

    assert "CTRADER" in implemented_live_providers(), \
        "CTrade 에 cTrader 커넥터가 있는데 탐색되지 않았습니다"
    findings = audit_live_feature_support(["ref_us_tech_100_ret_5c"])
    assert findings and findings[0].supported, findings[0].describe() if findings else "판정 없음"


# ---------------------------------------------------------------------------
# 컬럼 이름 충돌 래칫 (2026-10-01 사고)
# 참조 Normalized 파일에도 target_* 컬럼이 있어, 이름을 바꾸지 않은 채 결합되면 merge 가
# 타겟을 target_close_x 로 밀어내고 다음 참조의 target_close 가 타겟 자리를 차지했다.
# ---------------------------------------------------------------------------
def test_attach_reference_refuses_column_collision():
    from shared.data_library.reference_staleness import attach_reference

    ts = pd.date_range("2026-08-10 09:00", periods=10, freq="10s")
    base = pd.DataFrame({"timestamp": ts, "target_close": np.arange(10.0)})
    ref = pd.DataFrame({"timestamp": ts, "target_close": np.arange(10.0) * 2})
    with pytest.raises(ValueError, match="겹칩니다"):
        attach_reference(base, ref, ref_symbol="005930", ref_interval="10s",
                         target_symbol="069500", target_interval="10s")


def test_composer_keeps_target_columns_when_reference_carries_target_names(monkeypatch):
    from CAiModelBaker.core.feature_composer import FeatureComposer

    ts = pd.date_range("2026-08-10 09:00", periods=50, freq="10s")

    def fake_load(self, symbol, interval, is_target=False):
        px = 100.0 if symbol == "069500" else 200000.0 + (1000.0 if symbol == "005930" else 0.0)
        return pd.DataFrame({"timestamp": ts, "close": px, "target_close": px,
                             "upbit_close": px, "volume": 1.0})

    monkeypatch.setattr(FeatureComposer, "load_normalized", fake_load)
    comp = FeatureComposer(local_log=lambda m: None)
    comp._compose_target_symbol = "069500"
    df = comp.load_normalized("069500", "10s", is_target=True)
    for ref in ("000660", "005930"):
        df = comp._attach_reference(df, ref, "10s", {})

    assert not [c for c in df.columns if c.endswith(("_x", "_y"))]
    assert (df["target_close"] == 100.0).all()
    assert (df["ref_005930_close"] == 201000.0).all()


# ---------------------------------------------------------------------------
# 틱 순서 감사 fail-closed + 빌드 순서 래칫 (2026-10-01 사고)
# Normalized 를 비우고 재빌드하자 10s 가 1m 보다 먼저 만들어졌고, 대조할 1분봉이 없어
# 감사가 조용히 건너뛰어져 순서가 섞인 대장주 날짜가 10초봉에 되살아났다.
# ---------------------------------------------------------------------------
def test_tick_order_audit_refuses_to_skip_without_minute_bars(tmp_path, monkeypatch):
    from CAiModelBaker.core import normalized_builder as nb

    monkeypatch.setattr(nb.dpaths, "normalized_path", lambda *a: str(tmp_path / "missing_1m.parquet"))
    ticks = pd.DataFrame({"timestamp": pd.date_range("2026-08-10 09:00", periods=5, freq="min"),
                          "upbit_close": 1.0})
    with pytest.raises(FileNotFoundError, match="1분봉"):
        nb._drop_unordered_tick_days(ticks, "005930", lambda m: None)


def test_single_asset_normalized_builds_minute_bars_before_10s(monkeypatch):
    from CAiModelBaker.core import normalized_builder as nb
    from shared.data_library.source_mapping_manager import SourceMappingManager

    order = []
    monkeypatch.setattr(SourceMappingManager, "load_all_mappings", staticmethod(
        lambda force_refresh=False: {"005930": {"category": "KRX_STOCK", "target_scales": ["10s", "1m"]}}))
    monkeypatch.setattr(nb, "build_normalized_10s_stock", lambda s, **k: order.append("10s") or "p10s")
    monkeypatch.setattr(nb, "build_normalized_candle_stock", lambda s, interval, **k: order.append(interval) or "p1m")
    monkeypatch.setattr(nb, "_audit_interval_consistency_artifacts", lambda *a: [])

    nb.build_single_asset_normalized("005930", local_log=lambda m: None)
    assert order == ["1m", "10s"]
