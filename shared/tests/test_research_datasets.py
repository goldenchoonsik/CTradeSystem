"""연구 데이터셋(staging) 현황 모듈 — 임시 폴더로만 검사 (실제 DataVault 를 읽지 않는다)."""
import json
import os

from shared.data_library import research_datasets as rd


def _make(tmp_path):
    ds = tmp_path / "demo"
    (ds / "raw" / "creon" / "candle_1m" / "A000001").mkdir(parents=True)
    (ds / "raw" / "creon" / "candle_1m" / "A000002").mkdir(parents=True)
    (ds / "raw" / "creon" / "candle_1d").mkdir(parents=True)
    (ds / "raw" / "creon" / "candle_1d" / "A000001_1d.csv").write_text("timestamp\n")
    (ds / "inbox_1m").mkdir()
    (ds / "inbox_1m" / "A000003.csv.json").write_text("{}")
    (ds / "inbox_1m_failed").mkdir()
    (ds / "inbox_1m_failed" / "A000009.csv").write_text("")
    (ds / "inbox_1m_failed" / "A000009.csv.json").write_text("{}")
    (ds / "dataset.json").write_text(json.dumps({"title": "데모", "purpose": "테스트"}), encoding="utf-8")
    (ds / "universe.csv").write_text("code,market,is_preferred,is_spac\nA000001,KOSPI,0,0\nA000002,KOSDAQ,0,0\nA000005,KOSDAQ,1,0\n",
                                     encoding="utf-8")
    (ds / "targets_all.txt").write_text("A000001\nA000002\nA000003\nA000004\n")
    (ds / "driver_log.txt").write_text(
        "2026-10-11 13:00:00 [minute] 시작 4 종목\n"
        "2026-10-11 13:10:00 [minute] 2/4 성공 2 실패 0 경과 10.0분 (종목당 20.00s)\n"
        "2026-10-11 13:11:00 [daily_unadj] 50/100 성공 49 실패 1 경과 0.3분 (종목당 0.30s)\n", encoding="utf-8")
    (ds / "ingest_log.txt").write_text("a\n2026-10-11 13:10:05 병합 2 (성공 2 실패 0)\n", encoding="utf-8")
    return ds


def test_dataset_status(tmp_path):
    ds = _make(tmp_path)
    st = rd.dataset_status(str(ds), with_size=False)
    assert st["title"] == "데모" and st["registered_in_ledger"] is False
    assert st["universe"] == {"total": 3, "by_market": {"KOSPI": 1, "KOSDAQ": 2}, "preferred_or_spac": 1}
    assert st["stores"] == {"candle_1d": 1, "candle_1m": 2}
    c = st["collect_1m"]
    assert (c["target"], c["done"], c["pct"], c["finished"]) == (4, 2, 50.0, False)
    assert c["eta_min"] == round(2 * 20 / 60, 1)
    assert c["inbox_pending"] == 1 and c["failed"] == ["A000009"]
    assert c["last_ingest"].endswith("병합 2 (성공 2 실패 0)")
    assert st["progress"]["daily_unadj"]["fail"] == 1


def test_finished_marker_stops_eta(tmp_path):
    ds = _make(tmp_path)
    (ds / "inbox_1m" / "DONE").write_text("")
    c = rd.dataset_status(str(ds), with_size=False)["collect_1m"]
    assert c["finished"] is True and c["eta_min"] is None


def test_list_datasets_skips_hidden_and_files(tmp_path):
    _make(tmp_path)
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "note.txt").write_text("x")
    names = [d["name"] for d in rd.list_datasets(str(tmp_path), with_size=False)]
    assert names == ["demo"]


def test_missing_root(tmp_path):
    assert rd.list_datasets(str(tmp_path / "none")) == []


def test_read_only(tmp_path):
    ds = _make(tmp_path)
    before = sorted((p, os.path.getmtime(p)) for p in map(str, ds.rglob("*")))
    rd.dataset_status(str(ds), with_size=True)
    after = sorted((p, os.path.getmtime(p)) for p in map(str, ds.rglob("*")))
    assert before == after


def test_symbols_states(tmp_path):
    ds = _make(tmp_path)
    (ds / "quality").mkdir()
    (ds / "quality" / "quality_by_code.csv").write_text(
        "code,m1_days,halted_days,missing_traded_days,st_QUARANTINED,factor_changes,factor_min,factor_max,issue\n"
        "A000001,480,20,0,0,1,1.0,5.0,\n", encoding="utf-8")
    rows = {r["code"]: r for r in rd.symbols("demo", str(tmp_path))}
    assert rows["A000001"]["state"] == "done" and rows["A000001"]["adjust_events"] == 1 and rows["A000001"]["factor_max"] == 5.0
    assert rows["A000002"]["state"] == "done" and rows["A000002"]["checked"] is False
    assert rows["A000005"]["state"] == "excluded" and rows["A000005"]["preferred"] is True


def test_symbols_rejects_path_escape(tmp_path):
    _make(tmp_path)
    for bad in ("..", "../demo", "demo/../demo", ".hidden", ""):
        assert rd.symbols(bad, str(tmp_path)) is None


def _job(ds, source, **kw):
    (ds / "collect").mkdir(exist_ok=True)
    j = {"source": source, "title": source, "unit": "종목", "target": 10, "done": 4, "fetched": 3, "skipped": 1, "failed": [],
         "started_at": "2026-10-12T10:00:00", "updated_at": "2026-10-12T10:04:00", "finished": False}
    j.update(kw)
    (ds / "collect" / f"{source}.json").write_text(json.dumps(j), encoding="utf-8")


def test_collect_jobs_states(tmp_path):
    from datetime import datetime
    ds = tmp_path / "d"
    ds.mkdir()
    _job(ds, "a_running")
    _job(ds, "b_stale", updated_at="2026-10-12T09:00:00")
    _job(ds, "c_reason", stopped_reason="DART 한도 초과")
    _job(ds, "d_done", done=10, finished=True, failed=["000001", "000002:빈응답"])
    (ds / "collect" / "broken.json").write_text("{", encoding="utf-8")
    jobs = {j["source"]: j for j in rd.collect_jobs(str(ds), now=datetime(2026, 10, 12, 10, 4, 30))}
    assert set(jobs) == {"a_running", "b_stale", "c_reason", "d_done"}
    a = jobs["a_running"]
    assert a["state"] == "running" and a["pct"] == 40.0
    assert a["eta_min"] == round(6 * (270 / 4) / 60, 1)            # 4건에 270초 → 남은 6건
    assert jobs["b_stale"]["state"] == "stopped" and jobs["b_stale"]["eta_min"] is None
    assert jobs["c_reason"]["state"] == "stopped"
    assert jobs["d_done"]["state"] == "done" and jobs["d_done"]["failed_n"] == 2


def test_stores_include_other_providers(tmp_path):
    ds = _make(tmp_path)
    (ds / "raw" / "naver" / "candle_1d").mkdir(parents=True)
    (ds / "raw" / "naver" / "candle_1d" / "000001.xml").write_text("x")
    (ds / "raw" / "naver" / "candle_1d" / "000002.xml.tmp").write_text("x")
    st = rd.dataset_status(str(ds), with_size=False)["stores"]
    assert st == {"candle_1d": 1, "candle_1m": 2, "naver/candle_1d": 1}


def _make_daily(tmp_path):
    ds = tmp_path / "daily"
    (ds / "raw" / "creon" / "candle_1d").mkdir(parents=True)
    (ds / "raw" / "creon" / "candle_1d_unadj").mkdir(parents=True)
    (ds / "raw" / "naver" / "candle_1d").mkdir(parents=True)
    (ds / "universe.csv").write_text(
        "code,name,market,status,is_preferred,is_spac,listed_date,delist_date,delist_reason,price_source\n"
        "A000001,가,KOSPI,listed,0,0,19900101,,,creon\n"
        "A000002,나,KOSDAQ,listed,0,0,20000101,,,creon\n"
        "A000003,다,KOSDAQ,delisted,0,0,,2017-05-08,감사의견 거절,naver\n"
        "A000004,라,KOSPI,delisted,0,0,,2018-01-01,해산,naver\n"
        "A000005,마,KOSPI,listed,0,0,20100101,,,creon\n", encoding="utf-8")
    c = ds / "raw" / "creon" / "candle_1d" / "A000001_1d.csv"
    c.write_text("timestamp\n")
    (ds / "raw" / "creon" / "candle_1d" / "A000001_1d.csv.manifest.json").write_text(json.dumps(
        {"integrity": {"row_count": 3000, "first_timestamp": "2014-01-02 00:00:00", "last_timestamp": "2026-10-08 00:00:00"}}))
    (ds / "raw" / "creon" / "candle_1d_unadj" / "A000001_1d.csv").write_text("timestamp\n")
    (ds / "raw" / "naver" / "candle_1d" / "000003.xml").write_bytes(
        b'<chartdata><item data="20170428|1|1|1|1|5" /><item data="20170502|1|1|1|1|5" /><item data="20170504|1|1|1|1|5" /></chartdata>')
    (ds / "driver_log.txt").write_text("2026-10-12 10:00:00 [daily] 실패 A000005: 서버 응답 없음\n", encoding="utf-8")
    _job(ds, "naver_delisted_1d", failed=["000004:빈응답"])
    return ds


def test_daily_symbols(tmp_path):
    _make_daily(tmp_path)
    rows = {r["code"]: r for r in rd.symbols("daily", str(tmp_path))}
    a = rows["A000001"]
    assert (a["state"], a["first"], a["last"], a["bars"], a["has_unadj"]) == ("done", "2014-01-02", "2026-10-08", 3000, True)
    assert rows["A000002"]["state"] == "waiting" and rows["A000002"]["has_unadj"] is False
    n = rows["A000003"]
    assert (n["state"], n["first"], n["last"], n["bars"], n["status"]) == ("done", "2017-04-28", "2017-05-04", 3, "delisted")
    assert n["has_unadj"] is None and n["delist_reason"] == "감사의견 거절"
    assert rows["A000004"]["state"] == "failed" and rows["A000004"]["failed_by"] == "naver_delisted_1d"
    assert rows["A000005"]["state"] == "failed" and rows["A000005"]["failed_by"] == "creon_daily"


def test_daily_universe_status_counts(tmp_path):
    ds = _make_daily(tmp_path)
    u = rd.dataset_status(str(ds), with_size=False)["universe"]
    assert u["by_status"] == {"listed": 3, "delisted": 2} and u["total"] == 5


def test_daily_range_cache_follows_file_change(tmp_path):
    ds = _make_daily(tmp_path)
    fp = ds / "raw" / "naver" / "candle_1d" / "000003.xml"
    assert {r["code"]: r for r in rd.symbols("daily", str(tmp_path))}["A000003"]["bars"] == 3
    os.chmod(fp, 0o644)
    fp.write_bytes(b'<item data="20170428|1|1|1|1|5" />')
    os.utime(fp, (1, 1))
    assert {r["code"]: r for r in rd.symbols("daily", str(tmp_path))}["A000003"]["bars"] == 1
