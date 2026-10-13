# -*- coding: utf-8 -*-
"""
단위 테스트: 결측 갭 건너뛰기 허용(allow_gapped_trading_days / require_contiguous) 검증
"""
import os
import pandas as pd
import pytest

from CAiModelBaker.core.split_planner import plan_split, find_contiguous_trading_segments, scan_daily_bars


def _create_gapped_dummy_data():
    """8월 19일~26일 결측이 있는 가상 10초 캔들 데이터프레임 생성"""
    seg1 = pd.date_range("2026-07-21 09:00:00", "2026-08-18 15:30:00", freq="1h")
    seg2 = pd.date_range("2026-08-27 09:00:00", "2026-09-23 15:30:00", freq="1h")
    # 주말 제외
    ts1 = [t for t in seg1 if t.weekday() < 5]
    ts2 = [t for t in seg2 if t.weekday() < 5]
    all_ts = ts1 + ts2
    df = pd.DataFrame({
        "timestamp": all_ts,
        "volume": [1000.0] * len(all_ts),
        "close": [100.0] * len(all_ts)
    })
    return df


def test_require_contiguous_true_drops_older_segment():
    """require_contiguous=True 인 경우 3일 이상 결측 시 최신 연속 구간만 선택해야 한다."""
    df = _create_gapped_dummy_data()
    plan = plan_split(df, "10s", market="KRX", require_contiguous=True)
    assert plan["ok"] is True
    # WFA 시작일이 8월 27일 이후의 최신 구간에 고정되어야 함
    assert str(plan["data"]["first"]) >= "2026-08-27"
    # 경고 메시지에 결측 Gap 감지 포함 확인
    assert any("결측 Gap 감지" in w for w in plan.get("warnings", []))


def test_require_contiguous_false_unifies_all_segments():
    """require_contiguous=False 인 경우 결측일을 건너뛰고 전체 유효 거래일이 통합되어야 한다."""
    df = _create_gapped_dummy_data()
    plan = plan_split(df, "10s", market="KRX", require_contiguous=False)
    assert plan["ok"] is True
    # WFA 시작일이 7월 21일부터 온전히 인식되어야 함
    assert str(plan["data"]["first"]) == "2026-07-21"
    assert str(plan["data"]["last"]) == "2026-09-23"
    # 거래일 수가 35일 이상 풍부하게 확보되어야 함
    assert plan["data"]["trading_days"] >= 35
    # 안내 메시지 확인
    assert any("비연속 유효일 통합 모드" in w for w in plan.get("warnings", []))


def test_real_gold_snapshot_plan_split_if_exists():
    """실제 DataVault/features/069500/data_snapshot_10s.csv 스냅샷이 있는 경우 검증"""
    snap_path = "DataVault/features/069500/data_snapshot_10s.csv"
    if not os.path.exists(snap_path):
        pytest.skip("data_snapshot_10s.csv 없음")

    cols = ["timestamp", "volume"]
    df = pd.read_csv(snap_path, usecols=lambda c: c in ("timestamp", "volume", "target_volume", "upbit_volume"))
    
    # 1) 엄격 모드: 8월 27일로 좁혀짐
    strict_plan = plan_split(df, "10s", market="069500", require_contiguous=True)
    assert strict_plan["ok"] is True
    assert str(strict_plan["data"]["first"]) == "2026-08-27"

    # 2) 갭 건너뛰기 모드: 7월 21일부터 스냅샷에 있는 모든 거래일 확보
    #    (기대 일수는 스냅샷에서 도출한다 — 데이터가 매일 쌓이므로 고정값(과거 40일)을 박으면 테스트가 썩는다)
    gapped_plan = plan_split(df, "10s", market="069500", require_contiguous=False)
    assert gapped_plan["ok"] is True
    assert str(gapped_plan["data"]["first"]) == "2026-07-21"
    snap_days = pd.to_datetime(df["timestamp"]).dt.date.nunique()
    assert gapped_plan["data"]["trading_days"] == snap_days
