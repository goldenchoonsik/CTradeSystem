# -*- coding: utf-8 -*-
"""1분봉 일자 저장소: 일자 분할 · 봉 격자 격리 · 완결된 날만 저장 · 봉인 기준(구조/일봉 상한/틱 대조) · 봉인일 불변. 모두 tmp_path."""
import os
from datetime import datetime, timezone, timedelta

import pytest

from CAiModelBaker.core import candle_day_store as cds
from CAiModelBaker.core import tick_day_store as tds

HDR = "timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume,upbit_trade_price,cvd"
KST = timezone(timedelta(hours=9))
LATER = datetime(2026, 10, 10, 7, tzinfo=KST)
SYM = "000001"



def bars(day, n=381, vol=10, start=(9, 1)):
    t = datetime.strptime(f"{day} {start[0]:02d}:{start[1]:02d}:00", "%Y-%m-%d %H:%M:%S")
    return [f"{(t + timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M:%S')},1,2,1,{i},{vol},0,0" for i in range(n)]


def incoming(tmp_path, lines, name="in.csv"):
    p = tmp_path / name
    p.write_text(HDR + "\n" + "\n".join(reversed(lines)) + "\n", encoding="utf-8")     # 최신→과거 수신
    return str(p)


@pytest.fixture
def st(tmp_path):
    root, q = tmp_path / "candle", tmp_path / "q"
    root.mkdir()
    return str(root), str(q)


def ingest(tmp_path, st, lines, **kw):
    kw.setdefault("collected_at", LATER)
    return cds.ingest(incoming(tmp_path, lines), SYM, candle_root=st[0], quarantine_dir=st[1], log=lambda m: None, **kw)


def seal(st, **kw):
    kw.setdefault("now", LATER)
    return cds.seal_closed_days(st[0], SYM, log=lambda m: None, **kw)


def test_splits_by_day_and_writes_meta_with_volume_key(tmp_path, st):
    rep = ingest(tmp_path, st, bars("2026-09-29") + bars("2026-09-30", vol=20))
    assert rep["touched_days"] == ["2026-09-29", "2026-09-30"]
    m = cds.read_meta(st[0], SYM, "2026-09-30")
    assert m["status"] == "OPEN" and m["rows"] == 381 and m["volume"] == 381 * 20 and "tick_volume" not in m
    assert cds.verify(st[0], SYM) == {}


def test_misaligned_rows_are_quarantined_not_stored(tmp_path, st):
    lines = bars("2026-09-29", 3) + ["2026-09-29 09:03:30,1,1,1,1,5,0,0"]       # 진행 중이던 봉 스냅샷
    ingest(tmp_path, st, lines)
    assert cds.scan_day(cds.day_csv(st[0], SYM, "2026-09-29"), "2026-09-29")["rows"] == 3
    assert any(f.startswith("A000001_1m.") for f in os.listdir(os.path.join(st[1], "misaligned")))


def test_day_after_cutoff_is_not_stored(tmp_path, st):
    early = datetime(2026, 9, 30, 20, tzinfo=KST)                              # 9/30 저녁 수집 → 9/29 까지만 완결
    rep = ingest(tmp_path, st, bars("2026-09-29") + bars("2026-09-30"), collected_at=early)
    assert rep["skipped_incomplete_days"] == ["2026-09-30"] and cds.stored_days(st[0], SYM) == ["2026-09-29"]


def test_seal_valid_and_sealed_day_is_immutable(tmp_path, st):
    ingest(tmp_path, st, bars("2026-09-29"))
    assert seal(st)["sealed"] == ["2026-09-29"]
    m = cds.read_meta(st[0], SYM, "2026-09-29")
    assert m["status"] == "VALID" and m["bars"] == 381 and m["failures"] == []
    changed = bars("2026-09-29")
    changed[5] = changed[5].replace(",10,0,0", ",99,0,0")
    before = open(cds.day_csv(st[0], SYM, "2026-09-29"), encoding="utf-8").read()
    rep = ingest(tmp_path, st, changed, collected_at=LATER)
    assert rep["conflicts"] == 1 and os.path.exists(cds.day_csv(st[0], SYM, "2026-09-29") + ".conflicts.json")
    assert open(cds.day_csv(st[0], SYM, "2026-09-29"), encoding="utf-8").read() == before     # 봉인본은 그대로, 다른 값은 '확인 필요'로만 남는다
    assert cds.verify(st[0], SYM) == {}


def test_sparse_day_is_sealed_and_bar_count_is_only_recorded(tmp_path, st):
    ingest(tmp_path, st, bars("2026-09-29", 175, start=(9, 1)))                # 거래가 얇은 ETF — 체결 없는 분은 봉이 없다
    assert seal(st)["sealed"] == ["2026-09-29"]
    m = cds.read_meta(st[0], SYM, "2026-09-29")
    assert m["status"] == "VALID" and m["bars"] == 175 and m["failures"] == []


def test_futures_and_index_keys_use_the_same_store(tmp_path, st):
    cds.ingest(incoming(tmp_path, bars("2026-09-29", 100)), "10100", candle_root=st[0], quarantine_dir=st[1], collected_at=LATER, log=lambda m: None)
    assert cds.seal_closed_days(st[0], "10100", now=LATER, log=lambda m: None)["sealed"] == ["2026-09-29"]


def test_daily_volume_is_an_upper_bound_only(tmp_path, st):
    d = tmp_path / "1d.csv"
    d.write_text("timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume\n"
                 "2026-09-29 00:00:00,1,1,1,1,4000\n2026-09-30 00:00:00,1,1,1,1,3000\n", encoding="utf-8")
    ingest(tmp_path, st, bars("2026-09-29") + bars("2026-09-30"))                # 합 3810 / 3810
    rep = seal(st, daily_csv=str(d))
    assert rep["sealed"] == ["2026-09-29"] and rep["quarantined"] == ["2026-09-30"]       # 3810 > 3000 만 불합격, 3810<4000 은 합격(일치 불요)
    m = cds.read_meta(st[0], SYM, "2026-09-29")
    assert m["daily_ref_volume"] == 4000 and m["daily_ratio"] == pytest.approx(0.9525)


def test_tick_seal_reference_must_still_match(tmp_path, st):
    ingest(tmp_path, st, bars("2026-09-29"))
    troot = tmp_path / "tick"
    tds.write_meta(str(troot), SYM, "2026-09-29", {"symbol": SYM, "date": "2026-09-29", "status": "VALID", "minute_ref_volume": 3810.0})
    assert seal(st, tick_root=str(troot))["sealed"] == ["2026-09-29"]
    ingest(tmp_path, st, bars("2026-09-30"))
    tds.write_meta(str(troot), SYM, "2026-09-30", {"symbol": SYM, "date": "2026-09-30", "status": "VALID", "minute_ref_volume": 9999.0})
    assert seal(st, tick_root=str(troot))["quarantined"] == ["2026-09-30"]


def test_backups_do_not_collide_with_tick_backups(tmp_path, st):
    ingest(tmp_path, st, bars("2026-09-29", 100))
    ingest(tmp_path, st, bars("2026-09-29", 381))                                # 보강 → 백업 생성
    names = os.listdir(os.path.join(st[1], "merge_backups"))
    assert names and all(n.startswith("A000001_1m_2026-09-29.csv.") for n in names)


# ── 수집기 연결: 1분봉 저장·봉인 → 틱 봉인(1분봉이 봉인된 날만) ───────────────────────────────
@pytest.fixture
def collector(tmp_path, monkeypatch):
    from CAiModelBaker.core import creon_collector as cc
    monkeypatch.setattr(cc, "_DATA_VAULT_ROOT", str(tmp_path / "vault"))
    os.makedirs(cc._candle_day_root(), exist_ok=True)
    return cc


def test_collector_routes_only_creon_1m_logical_paths_to_the_day_store(collector):
    root = collector._candle_day_root()
    assert collector._candle_day_key(os.path.join(root, "A069500_1m.csv"), 1) == "A069500"
    assert collector._candle_day_key(os.path.join(root, "10100_1m.csv"), 1) == "10100"
    assert collector._candle_day_key(os.path.join(root, "A069500_5m.csv"), 5) is None           # 1분봉만
    assert collector._candle_day_key(os.path.join(root, "..", "elsewhere", "A069500_1m.csv"), 1) is None    # --out 직접 지정은 종전 방식


def test_1m_seals_first_then_tick_seals_against_the_sealed_1m_volume(tmp_path, collector):
    cc, root, vault = collector, collector._candle_day_root(), str(tmp_path / "vault")
    tick_root = os.path.join(vault, "raw", "creon", "tick")
    day = "2026-09-29"
    tmp = incoming(tmp_path, bars(day), name="m.csv")
    # 수집 시각이 완결 이후가 되도록 collected_at 을 넘긴다
    assert cc._candle_day_save(tmp, "A000001", None, False, True, LATER, lambda m: None)
    # 틱: 같은 날 거래량 합이 1분봉(381*10=3810)과 같다
    tick_in = tmp_path / "t.csv"
    tick_in.write_text("timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume\n"
                       + "\n".join(f"{day} 09:00:00,1,1,1,1,{v}" for v in (1810, 2000)) + "\n", encoding="utf-8")
    tds.ingest(str(tick_in), "A000001", tick_root=tick_root, quarantine_dir=str(tmp_path / "q2"), collected_at=LATER, log=lambda m: None)
    rep = tds.seal_closed_days(tick_root, "A000001", ref_volumes=lambda d: cds.sealed_volumes(root, "A000001", d), now=LATER, log=lambda m: None)
    assert rep["sealed"] == [day] and tds.read_meta(tick_root, "A000001", day)["status"] == "VALID"
    # 1분봉이 봉인 전이면 틱은 격리가 아니라 대기한다
    day2 = "2026-09-30"
    tds.ingest(str(_write(tmp_path, "t2.csv", day2, (3810,))), "A000001", tick_root=tick_root, quarantine_dir=str(tmp_path / "q2"), collected_at=LATER, log=lambda m: None)
    rep = tds.seal_closed_days(tick_root, "A000001", ref_volumes=lambda d: cds.sealed_volumes(root, "A000001", d), now=LATER, log=lambda m: None)
    assert rep["skipped"] == [day2] and tds.read_meta(tick_root, "A000001", day2)["status"] == "OPEN"


def _write(tmp_path, name, day, vols):
    p = tmp_path / name
    p.write_text("timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume\n"
                 + "\n".join(f"{day} 09:00:00,1,1,1,1,{v}" for v in vols) + "\n", encoding="utf-8")
    return p


def test_real_vault_candle_day_store_ratchet():
    """래칫(실물, 읽기만 함): Creon 1분봉은 모두 일자 저장소다 — 통합 CSV 가 없고, 모든 종목이 구조 검증을 통과하고,
    마감 후 3일이 지나도록 판정되지 않은(OPEN) 날이 없고, 격리(QUARANTINED)된 날이 없다(있으면 원인을 보고 해결해야 한다)."""
    from conftest import REPO_ROOT
    root = os.path.join(REPO_ROOT, "DataVault", "raw", "creon", "candle_1m")
    if not os.path.isdir(root):
        pytest.skip("실물 부재")
    assert not [f for f in os.listdir(root) if f.endswith("_1m.csv")], "통합 1분봉 CSV 가 다시 생겼다 — Creon 1분봉은 일자 저장소만 쓴다"
    syms = sorted(d[1:] for d in os.listdir(root) if d.startswith("A") and os.path.isdir(os.path.join(root, d)))
    assert syms, "1분봉 종목이 하나도 없다"
    cutoff = (datetime.now(KST) - timedelta(days=3)).strftime("%Y-%m-%d")
    broken, stale, quarantined = {}, {}, {}
    for sym in syms:
        issues = cds.verify(root, sym)
        if issues:
            broken[sym] = dict(list(issues.items())[:3])
        metas = cds.all_metas(root, sym)
        left = [d for d, m in metas.items() if m.get("status") == "OPEN" and d < cutoff]
        if left:
            stale[sym] = left
        q = [d for d, m in metas.items() if m.get("status") == "QUARANTINED"]
        if q:
            quarantined[sym] = q
    assert not broken, f"일자 저장소 검증 실패: {broken}"
    assert not stale, f"마감 후 3일 넘게 판정되지 않은 날: {stale}"
    assert not quarantined, f"봉인 검사에서 격리된 날: {quarantined}"


def test_lock_sealed_six_char_futures_folder(tmp_path):
    """선물·지수 폴더(A10100, AU180)는 종목 코드가 6자 — 폴더 이름 'A10100' 을 그대로 넘기면 core_symbol 이 A 를 떼지 않아
    AA10100 을 찾고 아무것도 잠그지 않았다(2026-10-12~14 봉인본 미잠금). 호출부는 폴더 이름에서 A 를 떼고 넘긴다."""
    import json
    for folder in ("A10100", "AU180", "A069500"):
        d = tmp_path / folder
        d.mkdir()
        (d / "2026-01-02.csv").write_text("timestamp,upbit_volume\n")
        (d / "2026-01-02.meta.json").write_text(json.dumps({"date": "2026-01-02", "status": "VALID"}))
        assert cds.lock_sealed(str(tmp_path), folder[1:]) == ["2026-01-02"]
        assert not os.access(d / "2026-01-02.csv", os.W_OK)
