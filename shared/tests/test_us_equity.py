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
