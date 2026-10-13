# -*- coding: utf-8 -*-
"""일자별 틱 저장소: 병합 규칙(엔진 재사용) · 형성 중인 날 · 봉인일 불변 · 구조 불변식 · 봉인 · 이어받기 · 영구 결손. 모두 tmp_path."""
import json
import os
import stat
from datetime import datetime, timezone, timedelta

import pytest

from CAiModelBaker.core import tick_day_store as tds

HDR = "timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume"
KST = timezone(timedelta(hours=9))


def _rows(day, spec):
    """spec: [(HH:MM:SS, close, vol), ...] → CSV 행"""
    return [f"{day} {t},1,1,1,{c},{v}" for t, c, v in spec]


def _incoming(tmp_path, lines, newest_first=True, name="in.csv"):
    p = tmp_path / name
    body = list(reversed(lines)) if newest_first else lines
    p.write_text(HDR + "\n" + "\n".join(body) + "\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def st(tmp_path):
    root, q = tmp_path / "tick", tmp_path / "q"
    root.mkdir()
    return str(root), str(q)


LATER = datetime(2026, 10, 10, 7, tzinfo=KST)        # 테스트 날짜가 모두 '완결'인 수집 시각(실행 시각과 무관하게)


def _ingest(tmp_path, st, lines, **kw):
    root, q = st
    kw.setdefault("collected_at", LATER)
    return tds.ingest(_incoming(tmp_path, lines, kw.pop("newest_first", True)), "000001", tick_root=root, quarantine_dir=q,
                      log=lambda m: None, **kw)


def _read(st, day):
    with open(tds.day_csv(st[0], "000001", day), encoding="utf-8") as f:
        return f.read().splitlines()[1:]


def test_splits_by_day_into_ascending_files_with_meta(tmp_path, st):
    a = _rows("2026-09-29", [("09:00:00", 10, 1), ("09:00:00", 11, 2), ("15:30:00", 12, 3)])
    b = _rows("2026-09-30", [("09:00:00", 20, 4)])
    rep = _ingest(tmp_path, st, a + b)            # 최신→과거로 수신
    assert rep["touched_days"] == ["2026-09-29", "2026-09-30"]
    assert _read(st, "2026-09-29") == a           # 같은 시각 안의 체결 순서까지 복원
    m = tds.read_meta(st[0], "000001", "2026-09-29")
    assert m["status"] == "OPEN" and m["rows"] == 3 and m["tick_volume"] == 6 and m["last"] == "2026-09-29 15:30:00"
    assert tds.verify(st[0], "000001") == {}


def test_forming_day_takes_fuller_regroup_but_past_day_keeps_old_and_records_conflict(tmp_path, st):
    d1 = _rows("2026-09-29", [("09:00:00", 10, 1), ("15:30:00", 12, 3)])
    d2 = _rows("2026-09-30", [("09:00:00", 20, 4), ("11:25:00", 21, 5)])
    _ingest(tmp_path, st, d1 + d2)
    # 같은 행 수·거래량인데 내용이 다른 재수신: 형성 중인 날(09-30, 최신)은 신규, 과거 날(09-29)은 기존 유지 + 확인 필요
    d1x = _rows("2026-09-29", [("09:00:00", 10, 1), ("15:30:00", 99, 3)])
    d2x = _rows("2026-09-30", [("09:00:00", 20, 4), ("11:25:00", 77, 5)])
    rep = _ingest(tmp_path, st, d1x + d2x, )
    assert _read(st, "2026-09-30")[-1].endswith(",77,5")
    assert _read(st, "2026-09-29")[-1].endswith(",12,3")
    assert rep["conflicts"] == 1
    c = json.load(open(os.path.join(tds.day_dir(st[0], "000001"), "2026-09-29.csv.conflicts.json")))
    assert c["items"][0]["ts"] == "2026-09-29 15:30:00"


def test_partial_last_minute_is_completed_by_next_run(tmp_path, st):
    full = _rows("2026-09-30", [("09:00:00", 1, 1), ("11:25:00", 2, 1), ("11:25:00", 3, 1), ("11:25:00", 4, 1)])
    _ingest(tmp_path, st, full[:2])                 # 11:25 분이 반쯤 수신된 상태
    _ingest(tmp_path, st, full[1:])                 # 다음 수집이 11:25 부터 다시 받음
    assert _read(st, "2026-09-30") == full


def test_past_day_reinforcement_is_accepted(tmp_path, st):
    _ingest(tmp_path, st, _rows("2026-09-29", [("09:00:00", 1, 1)]) + _rows("2026-09-30", [("09:00:00", 1, 1)]))
    more = _rows("2026-09-29", [("09:00:00", 1, 1), ("09:00:00", 2, 2)])          # 기존 행을 모두 포함 + 추가 → 보강
    rep = _ingest(tmp_path, st, more)
    assert rep["replaced_groups"] == 1 and len(_read(st, "2026-09-29")) == 2


def test_sealed_day_is_never_modified(tmp_path, st):
    d1 = _rows("2026-09-29", [("09:00:00", 1, 5)])
    _ingest(tmp_path, st, d1 + _rows("2026-09-30", [("09:00:00", 1, 1)]))
    minute = tmp_path / "m.csv"
    minute.write_text(HDR + "\n2026-09-29 09:01:00,1,1,1,1,5\n", encoding="utf-8")
    rep = tds.seal_closed_days(st[0], "000001", str(minute), now=datetime(2026, 9, 30, 10, tzinfo=KST), log=lambda m: None)
    assert rep["sealed"] == ["2026-09-29"]
    path = tds.day_csv(st[0], "000001", "2026-09-29")
    before = open(path, "rb").read()
    rep = _ingest(tmp_path, st, _rows("2026-09-29", [("09:00:00", 1, 5), ("09:00:00", 2, 9)]))   # 보강이라도 봉인일은 불변
    assert rep["sealed_conflicts"] == ["2026-09-29"] and open(path, "rb").read() == before
    assert os.listdir(os.path.join(st[1], "sealed_day_changes"))
    # 같은 데이터 재수신은 아무 일도 없다
    rep = _ingest(tmp_path, st, d1)
    assert rep["sealed_conflicts"] == [] and rep["touched_days"] == []


def test_seal_quarantine_and_accepted_deviation(tmp_path, st):
    _ingest(tmp_path, st, _rows("2026-09-29", [("09:00:00", 1, 99)]) + _rows("2026-09-30", [("09:00:00", 1, 1)]))
    minute = tmp_path / "m.csv"
    minute.write_text(HDR + "\n2026-09-29 09:01:00,1,1,1,1,100\n", encoding="utf-8")
    now = datetime(2026, 9, 30, 10, tzinfo=KST)
    assert tds.seal_closed_days(st[0], "000001", str(minute), now=now, log=lambda m: None)["quarantined"] == ["2026-09-29"]
    with pytest.raises(ValueError):
        tds.accept_deviation(st[0], "000001", "2026-09-29", str(minute), " ")
    tds.accept_deviation(st[0], "000001", "2026-09-29", str(minute), "복구 불가 확인")
    assert tds.seal_closed_days(st[0], "000001", str(minute), now=now, log=lambda m: None)["sealed"] == ["2026-09-29"]
    m = tds.read_meta(st[0], "000001", "2026-09-29")
    assert m["status"] == "ACCEPTED_DEVIATION" and m["deviation_reason"] == "복구 불가 확인"


def test_incomplete_days_are_not_stored_until_next_day_0600(tmp_path, st):
    """완결된 날만 저장: D 는 D+1 06:00 이후에 시작한 수집에서만 저장된다(당일·당일 밤 수집분은 버린다)."""
    day = _rows("2026-10-02", [("09:00:00", 1, 1), ("15:30:00", 1, 1), ("19:59:00", 1, 1)])
    prev = _rows("2026-10-01", [("09:00:00", 1, 1)])
    for at in (datetime(2026, 10, 2, 12, tzinfo=KST), datetime(2026, 10, 2, 20, 30, tzinfo=KST), datetime(2026, 10, 3, 5, 59, tzinfo=KST)):
        rep = _ingest(tmp_path, st, prev + day, collected_at=at)
        assert rep["skipped_incomplete_days"] == ["2026-10-02"]
        assert not os.path.exists(tds.day_csv(st[0], "000001", "2026-10-02"))
    assert os.path.exists(tds.day_csv(st[0], "000001", "2026-10-01"))
    rep = _ingest(tmp_path, st, day, collected_at=datetime(2026, 10, 3, 6, 0, tzinfo=KST))
    assert rep["touched_days"] == ["2026-10-02"] and len(_read(st, "2026-10-02")) == 3


def test_complete_day_is_sealed_in_the_same_run(tmp_path, st):
    _ingest(tmp_path, st, _rows("2026-10-02", [("09:00:00", 1, 1), ("19:59:00", 1, 1)]), collected_at=datetime(2026, 10, 3, 8, tzinfo=KST))
    minute = tmp_path / "m.csv"
    minute.write_text(HDR + "\n2026-10-02 09:01:00,1,1,1,1,2\n", encoding="utf-8")
    assert tds.seal_closed_days(st[0], "000001", str(minute), now=datetime(2026, 10, 3, 8, tzinfo=KST), log=lambda m: None)["sealed"] == ["2026-10-02"]


def test_open_day_left_from_before_is_sealed_only_after_next_day_0600(tmp_path, st):
    """옛 규칙에서 남은 OPEN(부분 저장) 날: 그 날이 완결되기 전에는 봉인하지 않는다."""
    _ingest(tmp_path, st, _rows("2026-10-02", [("09:00:00", 1, 1), ("18:05:00", 1, 1)]))
    minute = tmp_path / "m.csv"
    minute.write_text(HDR + "\n2026-10-02 09:01:00,1,1,1,1,2\n", encoding="utf-8")
    assert tds.seal_closed_days(st[0], "000001", str(minute), now=datetime(2026, 10, 2, 20, 30, tzinfo=KST), log=lambda m: None)["sealed"] == []
    assert tds.seal_closed_days(st[0], "000001", str(minute), now=datetime(2026, 10, 3, 6, 1, tzinfo=KST), log=lambda m: None)["sealed"] == ["2026-10-02"]


def test_structure_invariants_detected_by_verify(tmp_path, st):
    _ingest(tmp_path, st, _rows("2026-09-29", [("09:00:00", 1, 1), ("10:00:00", 1, 1)]))
    path = tds.day_csv(st[0], "000001", "2026-09-29")
    # 시각 역행 / 다른 날짜 행 — 엔진 밖에서 누가 고친 상황
    open(path, "w").write(HDR + "\n2026-09-29 10:00:00,1,1,1,1,1\n2026-09-29 09:00:00,1,1,1,1,1\n")
    assert "거꾸로" in tds.verify(st[0], "000001")["2026-09-29"][0]
    open(path, "w").write(HDR + "\n2026-09-29 09:00:00,1,1,1,1,1\n2026-09-30 09:00:00,1,1,1,1,1\n")
    assert "다른 날짜" in tds.verify(st[0], "000001")["2026-09-29"][0]


def test_reversed_existing_day_file_blocks_merge_and_keeps_original(tmp_path, st):
    _ingest(tmp_path, st, _rows("2026-09-29", [("09:00:00", 1, 1), ("10:00:00", 1, 1)]))
    path = tds.day_csv(st[0], "000001", "2026-09-29")
    bad = HDR + "\n2026-09-29 10:00:00,1,1,1,1,1\n2026-09-29 09:00:00,1,1,1,1,1\n"
    open(path, "w").write(bad)
    assert _ingest(tmp_path, st, _rows("2026-09-29", [("11:00:00", 1, 1)])) is None      # 역행 파일에는 병합하지 않는다
    assert open(path).read() == bad
    assert os.listdir(os.path.join(st[1], "rejected_merges"))


def test_days_processed_ascending_and_stop_at_first_failure(tmp_path, st):
    _ingest(tmp_path, st, _rows("2026-09-29", [("09:00:00", 1, 1)]))
    path = tds.day_csv(st[0], "000001", "2026-09-29")
    open(path, "w").write("timestamp,other\n2026-09-29 09:00:00,1\n")                     # 헤더가 달라 09-29 병합 실패
    rep = _ingest(tmp_path, st, _rows("2026-09-29", [("10:00:00", 1, 1)]) + _rows("2026-09-30", [("09:00:00", 1, 1)]))
    assert rep is None and not os.path.exists(tds.day_csv(st[0], "000001", "2026-09-30"))  # 더 최신 날은 기록되지 않음


def test_resume_timestamp_backfills_gap_but_skips_lost_days(tmp_path, st):
    _ingest(tmp_path, st, _rows("2026-09-28", [("15:30:00", 1, 1)]) + _rows("2026-09-30", [("11:00:00", 1, 1)]))
    weekday = lambda dt, mkt="KRX": dt.weekday() < 5
    assert tds.resume_timestamp(st[0], "000001", weekday, log=lambda m: None) == "2026-09-28 15:30:00"     # 09-29 갭
    tds.write_meta(st[0], "000001", "2026-09-29", {"date": "2026-09-29", "status": "LOST"})
    assert tds.resume_timestamp(st[0], "000001", weekday, log=lambda m: None) == "2026-09-30 11:00:00"


def test_lost_days_recorded_when_source_exhausted(tmp_path, st):
    rep = _ingest(tmp_path, st, _rows("2026-09-30", [("09:00:00", 1, 1)]), requested_after="2026-09-25 15:30:00",
                  source_exhausted=True, lost_days_fn=lambda a, b: ["2026-09-26", "2026-09-29"])
    assert rep["lost_days"] == ["2026-09-26", "2026-09-29"]
    assert tds.read_meta(st[0], "000001", "2026-09-29")["status"] == "LOST"
    assert tds.verify(st[0], "000001") == {}

def test_real_vault_tick_day_store_ratchet():
    """래칫(실물, 읽기만 함): 틱은 모두 일자 저장소다 — 통합 CSV 가 없고, 모든 종목이 구조 검증을 통과하고,
    마감 후 3일이 지나도록 판정되지 않은(OPEN) 날이 없다(수집을 하루 건너뛴 것은 허용)."""
    from conftest import REPO_ROOT
    root = os.path.join(REPO_ROOT, "DataVault", "raw", "creon", "tick")
    if not os.path.isdir(root):
        pytest.skip("실물 부재")
    assert not [f for f in os.listdir(root) if f.endswith("_tick.csv")], "통합 틱 CSV 가 다시 생겼다 — 틱은 일자 저장소만 쓴다"
    syms = sorted(d[1:] for d in os.listdir(root) if d.startswith("A") and os.path.isdir(os.path.join(root, d)))
    assert syms, "틱 종목이 하나도 없다"
    cutoff = (datetime.now(KST) - timedelta(days=3)).strftime("%Y-%m-%d")
    stale, broken = {}, {}
    for sym in syms:
        issues = tds.verify(root, sym)
        if issues:
            broken[sym] = dict(list(issues.items())[:3])
        left = [d for d, m in tds.all_metas(root, sym).items() if m.get("status") == "OPEN" and d < cutoff]
        if left:
            stale[sym] = left
    assert not broken, f"일자 저장소 검증 실패: {broken}"
    assert not stale, f"마감 후 3일 넘게 판정되지 않은 날: {stale}"


def test_health_matrix_treats_ledger_unrecoverable_days_as_lost_not_missing(tmp_path):
    """원장에 영구 결손으로 기록된 날은 '결측(이상)'이 아니라 'lost' 로 표시한다 (tmp 파일 — 실제 원장 불변)."""
    from shared.data_library.raw_store_health import RawStoreHealthEngine
    from datetime import date, timedelta
    today = date.today()
    days = [today - timedelta(days=i) for i in range(1, 30) if (today - timedelta(days=i)).weekday() < 5]
    days.sort()
    hole = days[10:13]
    lines = [HDR] + [f"{d} 09:00:00,1,1,1,1,1" for d in days if d not in hole]
    csv_path = tmp_path / "X_tick.csv"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    after = f"{days[9]} 15:30:00"
    before = f"{days[13]} 09:00:00"
    (tmp_path / "X_tick.csv.days.json").write_text(json.dumps({"unrecoverable": [{"after": after, "before": before}]}), encoding="utf-8")
    h = RawStoreHealthEngine.compute_asset_health(str(csv_path), "KRX_STOCK", days=25)
    st = {s["date"]: s["status"] for s in h["slots"]}
    for d in hole:
        if str(d) in st:
            assert st[str(d)] in ("lost", "holiday")
    assert h["lost_count"] >= 1 and h["missing_count"] == 0


def test_two_merges_in_the_same_second_keep_both_backups(tmp_path, st, monkeypatch):
    """같은 초에 같은 날을 두 번 병합해도 저장이 멈추지 않고, 최신 백업 = 두 번째 병합 직전 상태다(실패 시 되돌릴 기준)."""
    monkeypatch.setattr(tds.time, "strftime", lambda fmt, *a: "20261002_201604")
    _ingest(tmp_path, st, _rows("2026-10-01", [("09:00:00", 1, 1)]))
    _ingest(tmp_path, st, _rows("2026-10-01", [("09:00:00", 1, 1), ("10:00:00", 1, 1)]))
    rep = _ingest(tmp_path, st, _rows("2026-10-01", [("09:00:00", 1, 1), ("10:00:00", 1, 1), ("11:00:00", 1, 1)]))
    assert rep is not None and len(_read(st, "2026-10-01")) == 3
    bdir = os.path.join(st[1], "merge_backups")
    baks = sorted(f for f in os.listdir(bdir) if f.startswith("A000001_2026-10-01.csv."))
    assert len(baks) == 2
    with open(os.path.join(bdir, baks[-1]), encoding="utf-8") as f:
        assert len(f.read().splitlines()) == 1 + 2            # 최신 백업 = 두 번째 병합 결과(세 번째 병합 직전)
