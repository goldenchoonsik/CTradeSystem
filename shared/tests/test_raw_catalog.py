"""원천 목록(운용 + 유니버스) — 임시 DataVault 로만 검사."""
import json

import pytest

from shared.data_library import paths, raw_catalog as rc


def _uni(vault, sym, last, rows=10, manifest=True, conflicts=0, size_drift=False):
    d = vault / "raw" / "yahoo" / "candle_1d"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sym}_1d.csv"
    p.write_text("timestamp,open,high,low,close,volume\n" + f"{last} 00:00:00,1,1,1,1,1\n")
    if manifest:
        size = p.stat().st_size + (5 if size_drift else 0)
        (d / f"{sym}_1d.csv.manifest.json").write_text(json.dumps(
            {"integrity": {"row_count": rows, "first_timestamp": "2020-01-02 00:00:00", "last_timestamp": f"{last} 00:00:00", "byte_size": size}}))
    if conflicts:
        (d / f"{sym}_1d.csv.conflicts.json").write_text(json.dumps({"items": [{"status": "pending", "ts": last}] * conflicts}))


def _listing(vault, syms):
    d = vault / "raw" / "nasdaqtrader" / "symdir" / "20261014"
    d.mkdir(parents=True, exist_ok=True)
    body = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    body += "".join(f"{s}|{s} Inc.|Q|N|N|100|N|N\n" for s in syms) + "File Creation Time: 1014202600:00|||||||\n"
    (d / "nasdaqlisted.txt").write_text(body)
    (d / "otherlisted.txt").write_text("ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_VAULT_ROOT", str(tmp_path))
    monkeypatch.setattr(rc, "_CACHE_PATH", str(tmp_path / "temp" / "cache" / "raw_catalog.json"))
    monkeypatch.setattr(rc, "_operating_entries", lambda: [
        {"key": "feed:ES", "symbol": "ES", "name": "S&P500 선물", "tier": "운용", "provider": "CME", "market": "US",
         "category": "OVERSEAS_FUTURES", "path": str(tmp_path / "raw" / "overseas" / "ES_1d.csv"), "missing_days": 3,
         "archived": False, "file_exists": True, "last": "2026-10-13", "conflicts": 0}])
    monkeypatch.setattr(rc, "expected_latest", lambda market, now=None: "2026-10-13")   # 오늘 날짜와 무관하게
    rc._MEM["data"] = None
    yield tmp_path
    rc._MEM["data"] = None


def test_issues_and_tier_are_decided_by_file_not_symbol_string(vault):
    for s in ("AAA", "BBB", "CCC", "ES"):
        _uni(vault, s, "2026-10-13")
    _uni(vault, "OLD", "2026-09-01")                 # 최신일 지연
    _uni(vault, "GONE", "2026-10-13")                # 목록에서 사라짐 → 상장 종료(이상 아님)
    _uni(vault, "CNF", "2026-10-13", conflicts=2)
    _uni(vault, "DRF", "2026-10-13", size_drift=True)
    _listing(vault, ["AAA", "BBB", "CCC", "ES", "OLD", "CNF", "DRF"])
    items = {(e["symbol"], e["tier"]): e for e in rc.build(force=True)["items"]}
    assert ("ES", "유니버스") in items and ("ES", "운용") in items          # 같은 문자열이어도 다른 자산
    assert items[("OLD", "유니버스")]["issues"] == ["stale"]
    assert items[("GONE", "유니버스")]["issues"] == ["listing_ended"]
    assert items[("CNF", "유니버스")]["issues"] == ["conflict"] and items[("CNF", "유니버스")]["conflicts"] == 2
    assert items[("DRF", "유니버스")]["issues"] == ["size_mismatch"]
    assert items[("ES", "운용")]["issues"] == ["missing_days"]
    s = rc.summary()
    assert s["anomaly_total"] == 4                     # OLD·CNF·DRF·운용 ES (상장 종료는 빼고 센다)
    uni = next(g for g in s["groups"] if g["tier"] == "유니버스")
    assert uni["ended"] == 1


def test_search_ranks_exact_symbol_first_and_pages(vault):
    for s in ("ES", "ESAB", "XES", "AAA"):
        _uni(vault, s, "2026-10-13")
    _listing(vault, ["ES", "ESAB", "XES", "AAA"])
    rc.build(force=True)
    r = rc.search(q="es")
    assert [(e["symbol"], e["tier"]) for e in r["items"][:4]] == [("ES", "운용"), ("ES", "유니버스"), ("ESAB", "유니버스"), ("XES", "유니버스")]
    only = rc.search(only_anomaly=True)
    assert [e["symbol"] for e in only["items"]] == ["ES"] and only["items"][0]["tier"] == "운용"
    p2 = rc.search(page=2, size=2)
    assert p2["total"] == 5 and len(p2["items"]) == 2


def test_blank_price_bar_is_reported_as_incomplete(vault):
    _uni(vault, "AAA", "2026-10-13")
    with open(vault / "raw" / "yahoo" / "candle_1d" / "AAA_1d.csv", "a") as f:
        f.write("2026-10-14 00:00:00,1,2,0.5,,100\n")                 # 종가 미확정
    _listing(vault, ["AAA"])
    e = next(x for x in rc.build(force=True)["items"] if x["symbol"] == "AAA" and x["tier"] == "유니버스")
    assert "incomplete_bar" in e["issues"] and e["blank_bars"] == 1
