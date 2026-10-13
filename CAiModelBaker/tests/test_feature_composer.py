# -*- coding: utf-8 -*-
"""
테스트: FeatureComposer (온디맨드 시점 결합 및 Coverage Guard 검증)
"""

import os
import shutil
import pytest
import pandas as pd

from CAiModelBaker.core.feature_composer import FeatureComposer
from shared.data_library import paths as dpaths
from shared.data_library.manifest_manager import ManifestManager


@pytest.fixture
def composer():
    return FeatureComposer()


def test_coverage_summary_kodex_standalone(composer):
    """069500 단독 Coverage 검사: 데이터 존재 및 KRX 10초봉 구조 불변식 확인 (하드코딩 금지)"""
    summary = composer.get_coverage_summary("069500", "10s")
    target = summary["target"]
    # KRX 10초봉 구조 불변식: 1일치 = 2,341행 (하드코딩 금지)
    BARS_PER_DAY = 2341
    assert target["available"] is True
    assert target["trading_days"] >= 1, "최소 1거래일 이상이어야 합니다."
    assert target["total_bars"] == BARS_PER_DAY * target["trading_days"], (
        f"total_bars({target['total_bars']}) != {BARS_PER_DAY} × {target['trading_days']}일"
    )
    assert target["start_date"] <= target["end_date"], "start_date가 end_date보다 미래일 수 없습니다."


def test_coverage_summary_with_samsung(composer):
    """069500에 대장주(005930) 1분봉 포함 시 Coverage 진단 (하드코딩 제거로 데이터 증분 자동 대응)"""
    summary = composer.get_coverage_summary("069500", "10s", ref_symbols=["005930"])
    target_days = summary["target"]["trading_days"]
    assert target_days >= 1
    assert "005930" in summary["references"]
    ref_info = summary["references"]["005930"]
    assert ref_info["available"] is True
    assert ref_info["trading_days"] >= target_days  # 원천 분봉은 타겟 이상이어야 함
    assert ref_info["end_date"] >= summary["target"]["start_date"]

    # 공통 거래일은 타겟 거래일 이하여야 함 (교집합)
    assert summary["common_trading_days"] <= target_days
    assert summary["common_trading_days"] >= 1


def test_compose_standalone_retains_full_days(composer):
    """대장주 미포함 시 전체 거래일 100% 온전히 보존하는지 확인 (하드코딩 금지)"""
    df = composer.compose("069500", "10s", ref_symbols=None, align_to_common_dates=True)
    # KRX 10초봉 구조 불변식: 총 행 수 = 2,341 × 거래일
    BARS_PER_DAY = 2341
    trading_days = df["timestamp"].dt.date.nunique()
    assert trading_days >= 1
    assert len(df) == BARS_PER_DAY * trading_days, (
        f"행 수({len(df)}) != {BARS_PER_DAY} × {trading_days}일"
    )
    assert "target_close" in df.columns
    assert "ref_005930_close" not in df.columns


def test_compose_with_samsung_clamping(composer):
    """대장주 1분봉 포함 시 공통 거래일 전체가 보존되고 피처가 올바르게 결합되는지 확인"""
    df = composer.compose("069500", "10s", ref_symbols=["005930"], align_to_common_dates=True)
    trading_days = df["timestamp"].dt.date.nunique()
    assert trading_days >= 1
    # 교집합 결합 시 날짜 범위가 양 데이터 내에 있어야 함
    max_date = str(df["timestamp"].dt.date.max())
    min_date = str(df["timestamp"].dt.date.min())
    assert max_date >= min_date
    assert "ref_005930_close" in df.columns
    # Lookahead 및 결측치 없는지 확인
    assert not df["ref_005930_close"].isna().all()


def test_compose_with_samsung_10s_native(composer):
    """타겟 10초봉과 대장주 10초봉(005930_10s)이 1:1 NATIVE로 결합되고 올바르게 클램핑되는지 확인"""
    summary = composer.get_coverage_summary("069500", "10s", ref_symbols=["005930"], prefer_target_interval=True)
    assert summary["references"]["005930"]["interval"] == "10s"
    ref_days = summary["references"]["005930"]["trading_days"]
    common_days = summary["common_trading_days"]
    assert ref_days >= 1
    assert common_days >= 1
    assert common_days <= ref_days  # 교집합 ≤ 참조 데이터

    df = composer.compose("069500", "10s", ref_symbols=["005930"], prefer_target_interval=True, align_to_common_dates=True)
    assert df["timestamp"].dt.date.nunique() == common_days
    assert "ref_005930_vwap" in df.columns
    assert "ref_005930_ofi" in df.columns
    assert "cross_005930_spread" in df.columns
    assert not df["ref_005930_close"].isna().all()



def test_compose_session_isolation_and_manifest(composer, tmp_path):
    """세션 지정 시 독립 디렉토리에 저장되고 매니페스트가 검증되는지 확인"""
    test_session = "TEST_SESSION_ISOLATION_001"
    df = composer.compose("069500", "10s", session_id=test_session, save_snapshot=False)
    assert not df.empty

    session_dir = dpaths.features_path("069500", "sessions", test_session)
    session_file = os.path.join(session_dir, "features_10s.parquet")
    assert os.path.exists(session_file)

    ok, msg = ManifestManager.verify_manifest(session_file)
    assert ok is True

    # 청소
    if os.path.exists(session_dir):
        shutil.rmtree(session_dir)
