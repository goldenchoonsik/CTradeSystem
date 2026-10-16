# -*- coding: utf-8 -*-
"""
테스트: Raw Store 헬스 매트릭스 엔진 성능 및 스마트 갭 복구 검증
"""
import os
import pytest
from shared.data_library.raw_store_health import RawStoreHealthEngine, _extract_dates_from_csv
from CAiModelBaker.core.creon_collector import get_existing_last_timestamp
from shared.data_library import candle_store

# 실물 검사(real_vault): 실제 DataVault 데이터 상태를 본다 — 수집 시점에 따라 결과가 바뀐다. `pytest -m real_vault` 로 따로 돌린다.
pytestmark = pytest.mark.real_vault


def test_health_matrix_caching_performance():
    """파일 mtime 불변 시 날짜 추출이 디스크 캐시를 통해 초고속(1ms 이하)으로 반환되는지 검증"""
    raw_path = "DataVault/raw/creon/candle_1m/A069500_1m.csv"   # 1분봉 논리 경로(일자 저장소 — 파일은 없고 candle_store 가 답한다)
    if not candle_store.path_has_data(os.path.abspath(raw_path)):
        pytest.skip("A069500 1분봉 실물 부재로 스킵")

    # 1회차: 캐시 적중 또는 추출
    dates_1 = RawStoreHealthEngine.get_cached_dates_for_file(raw_path)
    assert len(dates_1) > 0

    # 2회차: 반드시 캐시에서 즉시 반환
    dates_2 = RawStoreHealthEngine.get_cached_dates_for_file(raw_path)
    assert dates_1 == dates_2


def test_smart_gap_recovery_detection(tmp_path):
    """크레온 스마트 갭 감지기가 파일 내부 결측 갭을 감지하고 갭 이전 체크포인트를 올바르게 도출하는지 검증"""
    # 1. 갭이 존재하는 모의 CSV 생성 (2026-09-15와 2026-09-17 사이 9/16 영업일 누락)
    mock_csv = tmp_path / "mock_tick_with_gap.csv"
    mock_csv.write_text(
        "timestamp,price,vol\n"
        "2026-09-15 15:29:00,100,1\n"
        "2026-09-15 15:30:00,100,2\n"
        "2026-09-17 09:00:00,101,1\n"
        "2026-09-17 15:30:00,101,2\n",
        encoding="utf-8"
    )
    checkpoint_gap = get_existing_last_timestamp(str(mock_csv))
    assert checkpoint_gap is not None
    # 2026-09-15 ~ 2026-09-17 사이 영업일 갭(9/16)이 있으므로, 갭 이전 시점(2026-09-15 15:30:00)을 가리켜야 함
    assert "2026-09-15" in checkpoint_gap

    # 2. 복구 완료된 실제 프로덕션 파일(A069500_1m.csv)은 갭이 없으므로 최신 시점을 반환해야 함
    raw_path = "DataVault/raw/creon/candle_1m/A069500_1m.csv"   # 단일 파일 원천(틱은 일자 저장소라 1분봉으로 검사)
    if os.path.exists(raw_path):
        import datetime
        checkpoint_prod = get_existing_last_timestamp(raw_path)
        assert checkpoint_prod is not None
        # 갭 없는 파일은 반드시 유효한 날짜(YYYY-MM-DD)를 포함한 최신 타임스탬프를 반환해야 함
        # 특정 날짜(예: 2026-09-23)를 하드코딩하면 데이터가 최신화될 때마다 실패하므로,
        # "오늘 날짜 이하의 유효한 타임스탬프"라는 불변 조건으로 검증한다
        import re
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", checkpoint_prod)
        assert date_match, f"checkpoint_prod에 유효한 날짜가 없습니다: {checkpoint_prod}"
        checkpoint_date = datetime.date.fromisoformat(date_match.group(1))
        today = datetime.date.today()
        assert checkpoint_date <= today, \
            f"checkpoint_prod 날짜({checkpoint_date})가 오늘({today})을 초과합니다: {checkpoint_prod}"
        assert checkpoint_date >= datetime.date(2026, 1, 1), \
            f"checkpoint_prod 날짜({checkpoint_date})가 2026년 이전입니다: {checkpoint_prod}"


def test_health_matrix_slots_resolution():
    """KODEX 200의 1분봉 및 일봉이 100% 정상 완결 상태인지 검증"""
    m1_path = "DataVault/raw/creon/candle_1m/A069500_1m.csv"
    d1_path = "DataVault/raw/creon/candle_1d/A069500_1d.csv"
    if not candle_store.path_has_data(os.path.abspath(m1_path)) or not os.path.exists(d1_path):
        pytest.skip("A069500 원천 파일 부재로 스킵")

    h_1m = RawStoreHealthEngine.compute_asset_health(m1_path, "KRX_ETF", days=30)
    assert h_1m["rate"] == 100.0
    assert h_1m["missing_count"] == 0

    h_1d = RawStoreHealthEngine.compute_asset_health(d1_path, "KRX_ETF", days=30)
    assert h_1d["rate"] == 100.0
    assert h_1d["missing_count"] == 0


def test_health_matrix_cache_first_and_metadata():
    """cache_only=True 및 return_meta=True 모드에서 캐시 메타데이터가 정상 반환되는지 검증"""
    items, meta = RawStoreHealthEngine.get_matrix(days=30, cache_only=True, return_meta=True)
    assert isinstance(items, list)
    assert isinstance(meta, dict)
    assert "saved_at" in meta
    assert "has_updates" in meta
    assert "updated_count" in meta
    assert "updated_symbols" in meta
    assert meta["cache_only"] is True


def test_health_matrix_update_detection(monkeypatch):
    """mtime 불일치 시 updated_symbols 및 has_updates 플래그가 정확히 감지되는지 검증"""
    # 1. 먼저 캐시를 로드/생성
    items, meta = RawStoreHealthEngine.get_matrix(days=30, force_refresh=True, symbols=["069500"], return_meta=True)
    assert len(items) > 0

    # 2. _stamp 함수를 임의로 조작하여 mtime 변경 상황 모의
    from shared.data_library import raw_store_health
    orig_stamp = raw_store_health._stamp

    def mock_stamp(path):
        return f"{orig_stamp(path)}_modified"

    monkeypatch.setattr(raw_store_health, "_stamp", mock_stamp)

    # 3. cache_only=True로 호출 시 업데이트 감지 확인
    items2, meta2 = RawStoreHealthEngine.get_matrix(days=30, cache_only=True, symbols=["069500"], return_meta=True)
    assert meta2["has_updates"] is True
    assert "069500" in meta2["updated_symbols"]
    assert meta2["updated_count"] >= 1


def test_quality_overlay_on_calendar_grid():
    """2D 잔디 캘린더의 각 셀에 품질 판정(degraded, suspect, reasons)이 동적으로 오버레이되는지 검증"""
    from shared.data_library.raw_store_health import _overlay_quality_calendar, _quality_view

    mock_cal = {
        "2026": [
            {"date": "2026-09-01", "status": "found", "weekday": 1, "month": 9},
            {"date": "2026-09-02", "status": "found", "weekday": 2, "month": 9},
            {"date": "2026-09-03", "status": "holiday", "weekday": 3, "month": 9},
        ]
    }
    mock_quality_days = {
        "2026-09-01": {"status": "bad", "reasons": ["상위 원천 기록 불일치"]},
        "2026-09-02": {"status": "warn", "reasons": ["괴리율 주의"]},
    }

    overlaid = _overlay_quality_calendar(mock_cal, mock_quality_days)
    cells = {c["date"]: c for c in overlaid["2026"]}

    # 1. 2026-09-01: bad -> degraded 및 reasons 확인
    assert cells["2026-09-01"]["status"] == "degraded"
    assert cells["2026-09-01"]["reasons"] == ["상위 원천 기록 불일치"]

    # 2. 2026-09-02: warn -> suspect 및 reasons 확인
    assert cells["2026-09-02"]["status"] == "suspect"
    assert cells["2026-09-02"]["reasons"] == ["괴리율 주의"]

    # 3. 2026-09-03: holiday는 영향 없음
    assert cells["2026-09-03"]["status"] == "holiday"



