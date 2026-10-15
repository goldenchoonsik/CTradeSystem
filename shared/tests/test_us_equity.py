"""us_equity — Yahoo 응답 → 무수정 원천. 임시 DataVault 로만 검사(실제 DataVault·네트워크 안 씀)."""
import json

import pandas as pd
import pytest

from shared.data_library import paths
from shared.data_library.us_equity import bronze
from shared.data_library.us_equity.yahoo import parse_chart, yahoo_symbol

ET_0930 = 13 * 3600 + 30 * 60          # 09:30 EDT = 13:30 UTC


def _day(y, m, d):
    return int(pd.Timestamp(year=y, month=m, day=d, tz="UTC").timestamp()) + ET_0930


def _chart(days, closes, splits=(), divs=(), market_time=None, period=None, volumes=None):
    ts = [_day(*d) for d in days]
    q = {"open": list(closes), "high": list(closes), "low": list(closes), "close": list(closes),
         "volume": volumes or [1000 if c is not None else None for c in closes]}
    meta = {"symbol": "TEST", "currency": "USD", "instrumentType": "EQUITY", "exchangeTimezoneName": "America/New_York",
            "regularMarketTime": market_time or ts[-1] + 7 * 3600}
    if period:
        meta["currentTradingPeriod"] = {"regular": {"start": period[0], "end": period[1]}}
    ev = {"splits": {str(_day(*d)): {"date": _day(*d), "numerator": n, "denominator": m} for d, n, m in splits},
          "dividends": {str(_day(*d)): {"date": _day(*d), "amount": a} for d, a in divs}}
    return json.dumps({"chart": {"result": [{"meta": meta, "timestamp": ts, "events": ev,
                                             "indicators": {"quote": [q]}}]}}).encode()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_VAULT_ROOT", str(tmp_path))
    return tmp_path


def test_yahoo_symbol_mapping():
    assert yahoo_symbol("BRK.B") == "BRK-B"
    assert yahoo_symbol("ABR$D") == "ABR-PD"


def test_split_is_reverted_to_traded_prices():
    # 2:1 분할(ex-date 01-05). Yahoo 는 분할 전 가격을 반으로 준다 → 원래 체결가 100 으로 되돌린다
    raw = _chart([(2026, 1, 2), (2026, 1, 5)], [50.0, 51.0], splits=[((2026, 1, 5), 2.0, 1.0)], volumes=[2000, 1000])
    bars, actions, _ = parse_chart(raw)
    assert bars["timestamp"].tolist() == ["2026-01-02 00:00:00", "2026-01-05 00:00:00"]
    assert bars["close"].tolist() == [100.0, 51.0]
    assert bars["volume"].tolist() == [1000.0, 1000.0]
    assert actions.iloc[0][["date", "type", "numerator"]].tolist() == ["2026-01-05", "split", 2.0]


def test_dividend_amount_reverted_by_later_splits():
    raw = _chart([(2026, 1, 2), (2026, 1, 5)], [50.0, 51.0], splits=[((2026, 1, 5), 4.0, 1.0)], divs=[((2026, 1, 2), 0.25)])
    _, actions, _ = parse_chart(raw)
    div = actions[actions["type"] == "dividend"].iloc[0]
    assert div["amount"] == pytest.approx(1.0) and div["yahoo_amount"] == 0.25


def test_null_rows_and_in_progress_session_are_dropped():
    days = [(2026, 1, 2), (2026, 1, 5), (2026, 1, 6)]
    start = _day(2026, 1, 6)
    raw = _chart(days, [10.0, None, 11.0], period=(start, start + 6 * 3600 + 1800), market_time=start + 3600)
    bars, _, info = parse_chart(raw)
    assert bars["timestamp"].tolist() == ["2026-01-02 00:00:00"]
    assert info["dropped_null"] == 1 and info["dropped_incomplete"] == 1


def test_closed_session_is_kept():
    start = _day(2026, 1, 6)
    raw = _chart([(2026, 1, 6)], [11.0], period=(start, start + 6 * 3600 + 1800), market_time=start + 6 * 3600 + 1801)
    assert parse_chart(raw)[0]["timestamp"].tolist() == ["2026-01-06 00:00:00"]


def test_ingest_writes_unadjusted_raw_and_is_idempotent(vault):
    raw = _chart([(2026, 1, 2), (2026, 1, 5)], [50.0, 51.0], splits=[((2026, 1, 5), 2.0, 1.0)])
    q = lambda m: None
    rep = bronze.ingest_response("TEST", raw, full_history=True, log=q)
    assert rep["added_rows"] == 2 and rep["actions_added"] == 1
    df = pd.read_csv(bronze.candle_path("TEST"))
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [100.0, 51.0]
    rep2 = bronze.ingest_response("TEST", raw, full_history=False, log=q)
    assert rep2["added_rows"] == 0 and rep2["conflicts"] == 0 and rep2["actions_added"] == 0


def test_later_split_does_not_rewrite_stored_history(vault):
    q = lambda m: None
    bronze.ingest_response("TEST", _chart([(2026, 1, 2)], [100.0]), full_history=True, log=q)
    # 나중 응답: 01-05 에 2:1 분할 — Yahoo 는 01-02 를 50 으로 다시 주지만 되돌리면 100 으로 기존과 같다
    later = _chart([(2026, 1, 2), (2026, 1, 5)], [50.0, 51.0], splits=[((2026, 1, 5), 2.0, 1.0)])
    rep = bronze.ingest_response("TEST", later, full_history=False, log=q)
    assert rep["conflicts"] == 0
    assert pd.read_csv(bronze.candle_path("TEST"))["close"].tolist() == [100.0, 51.0]


def test_action_conflict_keeps_existing(vault):
    q = lambda m: None
    bronze.ingest_response("TEST", _chart([(2026, 1, 2)], [10.0], divs=[((2026, 1, 2), 0.25)]), full_history=True, log=q)
    rep = bronze.ingest_response("TEST", _chart([(2026, 1, 2)], [10.0], divs=[((2026, 1, 2), 0.30)]), full_history=False, log=q)
    assert rep["action_conflicts"] == 1
    acts = pd.read_csv(bronze.actions_path("TEST"))
    assert acts["amount"].tolist() == [0.25]


def test_all_null_response_gives_empty_frame_with_string_timestamps():
    bars, _, info = parse_chart(_chart([(2026, 1, 2)], [None]))
    assert len(bars) == 0 and list(bars.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert bars["timestamp"].dtype == object and info["dropped_null"] == 1


def test_float32_noise_snaps_to_cents_but_real_subpenny_kept():
    from shared.data_library.us_equity.yahoo import snap_price
    assert snap_price([122.3499984741211 * 20]).tolist() == [2447.0]
    assert snap_price([0.1234, 40.487998962402344]).tolist() == [0.1234, 40.488]


def test_raw_files_outside_ledger_do_not_become_feeds(vault):
    """원천에 매니페스트가 생겨도 원장 밖 종목·Yahoo 원천은 source_mappings 에 자동 등록되지 않는다(유니버스 1.3만 종목 보호)."""
    from shared.data_library.source_mapping_manager import SourceMappingManager
    for prov, sym in (("yahoo", "ZZZZ"), ("binance", "NOTINLEDGER"), ("yahoo", "QQQ")):
        d = vault / "raw" / prov / "candle_1d"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sym}_1d.csv.manifest.json").write_text(json.dumps({"symbol": sym, "provider": prov, "market": "US"}))
    maps, changed = SourceMappingManager.auto_discover_vault_manifests({})
    assert maps == {} and changed is False


def test_us_calendar_from_vault_majority_and_es_cannot_erase_stock_holidays(vault, monkeypatch):
    """미국 거래일은 기준 대형주 다수결. ES 선물이 주식 휴장일에 단축 거래해도 휴장일이 지워지면 안 된다."""
    from shared.core import market_calendar as mc
    d = vault / "raw" / "yahoo" / "candle_1d"
    d.mkdir(parents=True)
    days = ["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09"]          # 01-07(수) 휴장
    for sym in ("IBM", "KO", "GE"):
        (d / f"{sym}_1d.csv").write_text("timestamp,close\n" + "".join(f"{x} 00:00:00,1\n" for x in days))
    # 한 종목만 휴장일에 봉이 있어도(데이터 오류) 다수결로 휴장일 유지
    (d / "XOM_1d.csv").write_text("timestamp,close\n" + "".join(f"{x} 00:00:00,1\n" for x in days + ["2026-01-07"]))
    hol, trade = mc._extract_us_vault_historical_holidays()
    assert "2026-01-07" in hol and "2026-01-07" not in trade and "2026-01-08" in trade

    class _Resp:                                                             # ES 는 01-07 에도 거래(단축)
        def __init__(self):
            ts = [int(__import__("datetime").datetime(2026, 1, x, 12).timestamp()) for x in (5, 6, 7, 8, 9, 12)]
            self.b = json.dumps({"chart": {"result": [{"timestamp": ts}]}}).encode()
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(mc.urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setattr(mc, "_CACHE_FILE", str(vault / "market_holidays.json"))
    monkeypatch.setattr(mc, "_DEFAULT_FALLBACK_HOLIDAYS", {"US": [], "KRX": []})
    monkeypatch.setattr(mc, "_MEM_CACHE", {})                                # 프로세스 공용 캐시 오염 방지
    h = mc.sync_market_holidays("US")
    assert "2026-01-07" in h


def test_listing_changes_are_recorded_once(vault, monkeypatch):
    """직전 목록과 비교해 신규·제외를 원장에 쌓는다 — 같은 날 다시 돌려도 한 번만(상폐 기록을 직접 축적하는 경로)."""
    from shared.data_library.us_equity import collect
    monkeypatch.setattr(collect, "log", lambda m: None)
    hdr = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    for day, syms in (("20261014", ["AAA", "OLD"]), ("20261015", ["AAA", "NEW"])):
        d = vault / "raw" / "nasdaqtrader" / "symdir" / day
        d.mkdir(parents=True)
        (d / "nasdaqlisted.txt").write_text(hdr + "".join(f"{s}|{s} Inc.|Q|N|N|100|N|N\n" for s in syms))
        (d / "otherlisted.txt").write_text("ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n")
    rows = [{"yahoo_symbol": s, "name": f"{s} Inc.", "exchange": "NASDAQ", "etf": "N"} for s in ("AAA", "NEW")]
    assert collect.record_listing_changes("20261015", rows) == {"added": 1, "removed": 1}
    assert collect.record_listing_changes("20261015", rows) == {"added": 0, "removed": 0}
    ch = pd.read_csv(vault / "raw" / "nasdaqtrader" / "listing_changes.csv")
    assert sorted(zip(ch["change"], ch["yahoo_symbol"])) == [("added", "NEW"), ("removed", "OLD")]


def test_finished_child_is_not_reported_running(tmp_path, monkeypatch):
    """끝난 자식 프로세스(회수 전 좀비)를 '실행 중'으로 보면 갱신 버튼이 영영 잠긴다."""
    import subprocess, sys
    from shared.data_library.us_equity import collect
    monkeypatch.setattr(collect, "stage", lambda *p: str(tmp_path.joinpath(*p)))
    (tmp_path / "collect").mkdir()
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    (tmp_path / "collect" / ".running.json").write_text(json.dumps({"pid": proc.pid}))
    import time
    for _ in range(50):                       # 끝나기를 기다리되 wait() 는 하지 않는다(좀비 상태 재현)
        try:
            with open(f"/proc/{proc.pid}/stat") as f:
                if f.read().rsplit(")", 1)[1].split()[0] == "Z":
                    break
        except OSError:
            break
        time.sleep(0.05)
    assert collect.running() is None
    assert not (tmp_path / "collect" / ".running.json").exists()


def test_partially_blank_bar_is_not_stored():
    raw = _chart([(2026, 1, 2), (2026, 1, 5)], [10.0, 11.0])
    j = json.loads(raw)
    j["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None     # 마감 직후: 시가·고가·저가만 있고 종가 없음
    bars, _, info = parse_chart(json.dumps(j).encode())
    assert bars["timestamp"].tolist() == ["2026-01-02 00:00:00"] and info["dropped_partial"] == 1


def test_illiquid_symbol_closed_bar_kept_when_fetched_after_close():
    # 마지막 체결이 마감 전(15:30)이어도 응답을 마감 뒤에 받았으면 그날 봉은 확정이다 (2026-10-15 실측: 2,166종목 누락)
    start = _day(2026, 1, 6)
    end = start + 6 * 3600 + 1800
    raw = _chart([(2026, 1, 5), (2026, 1, 6)], [10.0, 11.0], period=(start, end), market_time=end - 1800)
    bars, _, info = parse_chart(raw, fetched_at=end + 3 * 3600)
    assert bars["timestamp"].tolist() == ["2026-01-05 00:00:00", "2026-01-06 00:00:00"] and info["dropped_incomplete"] == 0
    bars, _, info = parse_chart(raw, fetched_at=end - 60)                      # 장중에 받은 응답은 여전히 뺀다
    assert bars["timestamp"].tolist() == ["2026-01-05 00:00:00"] and info["dropped_incomplete"] == 1


def test_chart_run_skips_symbols_already_at_latest_trading_day(vault, monkeypatch):
    from shared.data_library import raw_catalog
    from shared.data_library.us_equity import collect
    q = lambda m: None
    bronze.ingest_response("DONE", _chart([(2026, 1, 5), (2026, 1, 6)], [10.0, 11.0]), full_history=True, log=q)
    bronze.ingest_response("OLD", _chart([(2026, 1, 5)], [10.0]), full_history=True, log=q)
    monkeypatch.setattr(collect, "log", q)
    monkeypatch.setattr(collect, "PAUSE", 0)
    monkeypatch.setattr(collect, "fetch_lists", lambda day: [{"yahoo_symbol": s, "etf": "N"} for s in ("DONE", "OLD")])
    monkeypatch.setattr(raw_catalog, "expected_latest", lambda market: "2026-01-06")
    asked = []
    monkeypatch.setattr(collect, "fetch_chart", lambda sym, **k: (asked.append(sym), (_chart([(2026, 1, 6)], [11.0]), None))[1])
    collect.run_chart("20260107")
    assert asked == ["OLD"]
    assert raw_ingest_last("OLD") == "2026-01-06 00:00:00"


def raw_ingest_last(sym):
    from shared.data_library import raw_ingest
    return raw_ingest.last_timestamp(bronze.candle_path(sym))


def test_same_day_rerun_refetches_when_saved_response_lacked_latest_bar(vault, monkeypatch):
    # 아침 응답엔 최신일 자리가 비어 있었다(Yahoo 늦은 반영) → 같은 날 다시 누르면 보존 응답을 재사용하지 말고 다시 받아 dayr 에 보존
    from shared.data_library import raw_catalog
    from shared.data_library.us_equity import collect
    q = lambda m: None
    bronze.ingest_response("LATE", _chart([(2026, 1, 5)], [10.0]), full_history=True, log=q)
    bronze.save_response("LATE", _chart([(2026, 1, 5), (2026, 1, 6)], [10.0, None]), "20260107")
    monkeypatch.setattr(collect, "log", q)
    monkeypatch.setattr(collect, "PAUSE", 0)
    monkeypatch.setattr(collect, "fetch_lists", lambda day: [{"yahoo_symbol": "LATE", "etf": "Y"}])
    monkeypatch.setattr(raw_catalog, "expected_latest", lambda market: "2026-01-06")
    monkeypatch.setattr(collect, "fetch_chart", lambda sym, **k: (_chart([(2026, 1, 5), (2026, 1, 6)], [10.0, 11.0]), None))
    collect.run_chart("20260107")
    assert raw_ingest_last("LATE") == "2026-01-06 00:00:00"
    assert bronze.latest_response("LATE", "20260107").endswith("20260107r/LATE.json.gz")
    st = collect.job_status()["last"]
    assert st["done"] == 1 and st["fetched"] == 1 and st["behind_latest"] == 0
