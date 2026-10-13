# -*- coding: utf-8 -*-
"""원장 전체 보기(shared/symbol_overview.py) — 원장/수집 대장/참조 관계와 어긋나지 않는지 검증."""
from shared.config import config_manager as cm
from shared.data_library.source_mapping_manager import SourceMappingManager
from shared.data_library.symbol_overview import build_ledger_overview


def test_overview_covers_every_ledger_entry_and_states_add_up():
    ov = build_ledger_overview()
    ledger = cm.load_symbols_registry(include_catalog=True)
    assert {i["symbol"] for i in ov["items"]} == set(ledger)
    s = ov["summary"]
    assert s["ledger_total"] == len(ledger) == s["collecting"] + s["registered"] + s["catalog"]
    assert all(i["state"] in ("collecting", "registered", "catalog") for i in ov["items"])


def test_collecting_state_matches_source_mappings():
    ov = build_ledger_overview()
    collecting = {i["symbol"] for i in ov["items"] if i["state"] == "collecting"}
    assert collecting == set(SourceMappingManager.load_all_mappings())


def test_catalog_items_are_never_flagged_and_flags_are_consistent():
    for i in build_ledger_overview()["items"]:
        if i["state"] == "catalog":
            assert i["flags"] == []
        if "no_usage" in i["flags"]:
            assert i["usage"] == [] and i["state"] == "registered"   # 수집 중이면 '수집 대장'이 사용처다
