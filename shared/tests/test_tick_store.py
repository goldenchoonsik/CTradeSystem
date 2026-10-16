# -*- coding: utf-8 -*-
"""tick_store(읽기 창구): 일자 저장소의 날짜 상태·범위 읽기·지문, 그리고 경로 기반 소비자 연결. (실물 테스트는 읽기만 한다)"""
import json
import os

import pandas as pd
import pytest

from shared.data_library import tick_store as ts

HDR = "timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume"
from datetime import datetime, timedelta, timezone  # noqa: E402
LATER = datetime.now(timezone(timedelta(hours=9))) + timedelta(days=2)   # 픽스처 날짜가 모두 '완결'인 수집 시각


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A000001: 09-28 VALID · 09-29 OPEN · 09-30 QUARANTINED · 09-25 LOST(파일 없음)"""
    monkeypatch.setattr(ts, "tick_root", lambda: str(tmp_path))
    rows = {"2026-09-28": ["09:00:00,1,1,1,1,5", "09:00:00,1,1,1,2,6", "15:30:00,1,1,1,3,7"],
            "2026-09-29": ["09:00:00,2,2,2,2,1", "11:00:00,2,2,2,3,2"],
            "2026-09-30": ["09:00:00,3,3,3,3,4"]}
    st = {"2026-09-28": "VALID", "2026-09-29": "OPEN", "2026-09-30": "QUARANTINED"}
    d = tmp_path / "A000001"
    d.mkdir()
    for day, r in rows.items():
        (d / f"{day}.csv").write_text("\n".join([HDR] + [f"{day} {x}" for x in r]) + "\n", encoding="utf-8")
        (d / f"{day}.meta.json").write_text(json.dumps({"date": day, "status": st[day], "rows": len(r), "tick_volume": 1.0,
                                                        "sha256": "x", "first": f"{day} {r[0][:8]}", "last": f"{day} {r[-1][:8]}"}), encoding="utf-8")
    (d / "2026-09-25.meta.json").write_text(json.dumps({"date": "2026-09-25", "status": "LOST"}), encoding="utf-8")
    return tmp_path, rows


def test_read_all_days_in_order_and_skips_lost(store):
    tmp_path, rows = store
    df = ts.read("000001")
    assert len(df) == 6 and df["timestamp"].is_monotonic_increasing
    assert df["timestamp"].iloc[0] == "2026-09-28 09:00:00" and df["upbit_close"].iloc[1] == 2     # 같은 시각 안 체결 순서 유지


def test_range_and_columns(store):
    df = ts.read("000001", start="2026-09-29", end="2026-09-29", columns=["timestamp", "upbit_volume"])
    assert list(df.columns) == ["timestamp", "upbit_volume"] and len(df) == 2
    assert ts.read("000001", start="2026-10-05").empty


def test_days_status_dates_last_ts(store):
    d = ts.days("000001")
    assert [d[k]["status"] for k in sorted(d)] == ["LOST", "VALID", "OPEN", "QUARANTINED"]
    assert ts.dates("000001") == {"2026-09-28", "2026-09-29", "2026-09-30"}
    assert "2026-09-25" in ts.dates("000001", include_lost=True)
    assert ts.last_timestamp("000001") == "2026-09-30 09:00:00"
    assert ts.has_data("000001") and not ts.has_data("999999")


def test_signature_changes_when_a_day_file_is_overwritten(store):
    tmp_path, rows = store
    a = ts.signature("000001")
    f = tmp_path / "A000001" / "2026-09-29.csv"
    f.write_text(f.read_text() + "2026-09-29 15:31:00,1,1,1,1,1\n")
    assert ts.signature("000001") != a


def test_logical_path_bridge(store):
    tmp_path, rows = store
    p = ts.logical_path("000001")
    assert not os.path.exists(p) and ts.symbol_from_path(p) == "000001" and ts.path_has_data(p)
    assert ts.symbol_from_path(str(tmp_path / "other" / "A000001_tick.csv")) is None


@pytest.mark.real_vault
def test_real_069500_day_layout_is_consistent():
    """실물(읽기 전용): 069500 은 일자 저장소 — 범위 읽기 행 수가 메타 합과 같고 시각이 오름차순이다."""
    if not ts.has_data("069500"):
        pytest.skip("실물 부재")
    d = {k: v for k, v in ts.days("069500").items() if v["status"] != "LOST"}
    start = sorted(d)[-5]
    got = ts.read("069500", start=start)
    assert len(got) == sum(v["rows"] for k, v in d.items() if k >= start)
    assert got["timestamp"].is_monotonic_increasing


# ---------------------------------------------------------------------------
# 경로 기반 소비자(헬스·품질·원천 정리)가 논리 경로로 일자 저장소를 다루는가
# ---------------------------------------------------------------------------
@pytest.fixture
def day_store(tmp_path, monkeypatch):
    from datetime import date, timedelta
    from CAiModelBaker.core import tick_day_store as tds
    root = tmp_path / "tick"
    root.mkdir()
    monkeypatch.setattr(ts, "tick_root", lambda: str(root))
    today = date.today()
    wd = [today - timedelta(days=i) for i in range(1, 15) if (today - timedelta(days=i)).weekday() < 5][:5]
    wd.sort()
    lines = []
    for d in wd[:2] + wd[3:]:                                   # wd[2] 은 영구 결손
        lines += [f"{d} 09:00:00,1,1,1,1,1", f"{d} 15:30:00,1,1,1,2,1"]
    inc = tmp_path / "in.csv"
    inc.write_text(HDR + "\n" + "\n".join(reversed(lines)) + "\n", encoding="utf-8")
    tds.ingest(str(inc), "000003", tick_root=str(root), quarantine_dir=str(tmp_path / "q"), log=lambda m: None, collected_at=LATER)
    tds.write_meta(str(root), "000003", str(wd[2]), {"date": str(wd[2]), "status": "LOST"})
    return root, wd, tds


def test_day_layout_path_consumers(day_store):
    root, wd, tds = day_store
    path = ts.logical_path("000003")
    assert not os.path.exists(path)                       # 논리 경로 — 파일은 없다
    assert ts.path_has_data(path) and not ts.path_has_data(ts.logical_path("000009"))
    assert len(ts.read("000003")) == 8 and ts.last_timestamp("000003") == f"{wd[-1]} 15:30:00"
    from shared.data_library.raw_store_health import RawStoreHealthEngine
    h = RawStoreHealthEngine.compute_asset_health(path, "KRX_STOCK", days=20)
    st = {s["date"]: s["status"] for s in h["slots"]}
    assert st[str(wd[2])] in ("lost", "holiday") and h["missing_count"] == 0
    from shared.data_library import data_quality as dq
    sig = dq._sig(path)
    assert sig is not None
    df = dq._load_series(path)
    assert len(df) == 8
    # 새 수신 → 지문이 바뀐다(캐시 무효화)
    inc = root.parent / "in2.csv"
    inc.write_text(HDR + f"\n{wd[-1]} 15:30:00,1,1,1,3,5\n", encoding="utf-8")
    tds.ingest(str(inc), "000003", tick_root=str(root), quarantine_dir=str(root.parent / "q"), log=lambda m: None, collected_at=LATER)
    assert dq._sig(path) != sig


def test_day_layout_conflicts_and_ledger_check(day_store):
    root, wd, tds = day_store
    # 과거 날(형성 중이 아닌 날)에 같은 행 수·거래량, 다른 내용 → 확인 필요
    inc = root.parent / "in3.csv"
    inc.write_text(HDR + f"\n{wd[0]} 15:30:00,1,1,1,9,1\n", encoding="utf-8")
    tds.ingest(str(inc), "000003", tick_root=str(root), quarantine_dir=str(root.parent / "q"), log=lambda m: None, collected_at=LATER)
    assert len(ts.pending_conflicts("000003")) == 1
    from shared.data_library import data_quality as dq
    days = {}
    dq._apply_ledger_check(days, ts.logical_path("000003"))
    assert days[str(wd[0])]["checks"]["pending_conflicts"]["value"] == 1.0
    # 엔진 밖 수정 → 메타 불일치로 불량
    p = tds.day_csv(str(root), "000003", str(wd[1]))
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"{wd[1]} 15:31:00,1,1,1,1,1\n")
    days = {}
    dq._apply_ledger_check(days, ts.logical_path("000003"))
    assert "ledger_digest" in days[str(wd[1])]["checks"]


def test_remove_tick_day_store_moves_folder_including_sealed(day_store, tmp_path):
    root, wd, tds = day_store
    os.chmod(tds.day_csv(str(root), "000003", str(wd[0])), 0o444)
    from shared.data_library.source_mapping_manager import SourceMappingManager
    deleted = []
    freed = SourceMappingManager._remove_tick_day_store("000003", deleted, backup_to=str(tmp_path / "bk"))
    assert freed > 0 and deleted and not os.path.isdir(ts.day_dir("000003"))
    assert (tmp_path / "bk" / "A000003").is_dir()
