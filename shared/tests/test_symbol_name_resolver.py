# -*- coding: utf-8 -*-
"""종목명은 하드코딩이 아니라 데이터 원천/실시간 접속처에서 찾아 원장에 넣는다 (공급자 동작 검증)"""
import pytest

from shared.data_library import symbol_name_resolver as resolver
from shared.config import config_manager as cm


@pytest.fixture
def tmp_ledger(tmp_path, monkeypatch):
    import shutil
    path = tmp_path / "symbols_registry.json"
    shutil.copy(cm.SYMBOLS_REGISTRY_PATH, path)
    monkeypatch.setattr(cm, "SYMBOLS_REGISTRY_PATH", str(path))
    cm._registry_cache["sig"] = None
    yield path
    cm._registry_cache["sig"] = None


def test_first_provider_that_knows_the_name_wins():
    resolver.register_provider("KIS", lambda s, m="": None)
    resolver.register_provider("CREON", lambda s, m="": "크레온 이름" if s == "111111" else None)
    assert resolver.resolve_symbol_name("111111") == {"name": "크레온 이름", "source": "CREON"}
    resolver.register_provider("KIS", lambda s, m="": "KIS 이름" if s == "111111" else None)  # 같은 source 는 교체
    assert resolver.resolve_symbol_name("111111") == {"name": "KIS 이름", "source": "KIS"}


def test_failing_provider_never_propagates_and_next_one_is_tried():
    def boom(s, m=""):
        raise RuntimeError("접속 실패")
    resolver.register_provider("KIS", boom)
    resolver.register_provider("CREON", lambda s, m="": "정상 이름")
    assert resolver.resolve_symbol_name("222222")["source"] == "CREON"


def test_unknown_symbol_is_not_invented():
    assert resolver.resolve_symbol_name("NOPE_XYZ") is None
    assert resolver.resolve_symbol_name("") is None


def test_upbit_provider_uses_public_market_list(monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self): pass
        def json(self):
            return [{"market": "KRW-XRP", "korean_name": "엑스알피(리플)"}, {"market": "BTC-ETH", "korean_name": "x"}]

    import requests
    monkeypatch.setattr(requests, "get", lambda url, timeout=0: calls.append(url) or Resp())
    monkeypatch.setitem(resolver._upbit_cache, "at", 0.0)
    monkeypatch.setitem(resolver._upbit_cache, "names", {})
    # conftest 가 막아 둔 실제 공급자를 이 테스트에서만 되살린다
    monkeypatch.setattr(resolver, "_upbit_provider", resolver._upbit_provider_real)
    assert resolver.resolve_symbol_name("KRW-XRP") == {"name": "엑스알피(리플)", "source": "UPBIT"}
    assert resolver.resolve_symbol_name("KRW-XRP")["name"] == "엑스알피(리플)"
    assert len(calls) == 1, "마켓 목록은 TTL 동안 캐시되어 한 번만 요청해야 합니다"
    assert resolver.resolve_symbol_name("069500") is None  # KRW- 종목이 아니면 업비트는 관여하지 않음


def test_new_symbol_name_comes_from_source_not_from_the_hint(tmp_ledger):
    resolver.register_provider("KIS", lambda s, m="": "KIS 가 아는 이름" if s == "123450" else None)
    created = cm.ensure_symbol_registered("123450", name="어딘가에 박혀 있던 이름", market_type="KRX_STOCK")
    assert created["name"] == "KIS 가 아는 이름" and created["name_source"] == "KIS"


def test_hint_is_used_only_when_no_source_knows_it(tmp_ledger):
    created = cm.ensure_symbol_registered("ZZ_HINT1", name="호출자가 준 이름", market_type="OTHER")
    assert created["name"] == "호출자가 준 이름" and created["name_source"] == "manual"


def test_unresolvable_symbol_is_flagged_not_disguised(tmp_ledger):
    created = cm.ensure_symbol_registered("ZZ_NONAME", market_type="OTHER")
    assert created["name"] == "ZZ_NONAME" and created["name_source"] == "unresolved"


def test_register_symbol_without_name_asks_the_sources(tmp_ledger):
    resolver.register_provider("CREON", lambda s, m="": "크레온 종목" if s == "234560" else None)
    created = cm.register_symbol({"symbol": "234560", "market_type": "KRX_ETF"})
    assert created["name"] == "크레온 종목" and created["name_source"] == "CREON"


def test_explicit_user_name_wins_and_is_marked_manual(tmp_ledger):
    resolver.register_provider("KIS", lambda s, m="": "원천 이름")
    created = cm.register_symbol({"symbol": "345670", "name": "내가 정한 이름", "market_type": "KRX_STOCK"})
    assert created["name"] == "내가 정한 이름" and created["name_source"] == "manual"


def test_existing_names_are_never_overwritten_by_lookups(tmp_ledger):
    before = cm.get_symbol_info("069500")["name"]
    resolver.register_provider("KIS", lambda s, m="": "다른 이름")
    cm.ensure_symbol_registered("069500")
    cm.register_symbol({"symbol": "069500", "description": "설명만 수정"})
    assert cm.get_symbol_info("069500")["name"] == before


def test_audit_reports_mismatches_without_changing_names(tmp_ledger):
    resolver.register_provider("KIS", lambda s, m="": {"069500": "KODEX 200", "122630": "다른 이름"}.get(s))
    reg = {k: v for k, v in cm.load_symbols_registry().items() if k in ("069500", "122630", "005930")}
    report = resolver.audit_symbol_names(reg)
    assert report["match"] == ["069500"]
    assert report["mismatch"][0]["symbol"] == "122630" and report["mismatch"][0]["source_name"] == "다른 이름"
    assert "005930" in report["unverifiable"]
    assert cm.get_symbol_info("122630")["name"] != "다른 이름"
