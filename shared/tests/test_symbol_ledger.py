# -*- coding: utf-8 -*-
"""종목 마스터 원장(symbols_registry.json) 무결성/접근 규약 테스트.

이 저장소에서 종목 신원(이름/시장/거래소/비용)의 정답은 원장 하나뿐이다. 이 테스트는
과거에 반복된 사고 유형을 구조적으로 막는다:
  - 다른 대장(source_mappings/symbol_relations)에는 있는데 원장에는 없는 심볼 (이름 없는 종목)
  - 원장/대장 파일을 각 모듈이 직접 json.load 해서 스키마 가정이 제각각 어긋나는 사고
  - 시장 정책(수수료/슬리피지/틱)을 종목별로 복사 박제해서 정책 변경이 반영되지 않는 문제
"""
import json
import os
import shutil
from unittest import mock

import pytest

from shared.config import config_manager as cm
from shared.config.market_policies import DEFAULT_MARKET_POLICIES
from shared.data_library.source_mapping_manager import SourceMappingManager
from shared.data_library.symbol_relations_manager import SymbolRelationsManager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def tmp_ledger(tmp_path, monkeypatch):
    """실제 원장 파일을 건드리지 않고 사본 위에서 저장/삭제 동작을 검증하기 위한 격리 픽스처"""
    path = tmp_path / "symbols_registry.json"
    shutil.copy(cm.SYMBOLS_REGISTRY_PATH, path)
    monkeypatch.setattr(cm, "SYMBOLS_REGISTRY_PATH", str(path))
    cm._registry_cache["sig"] = None
    yield path
    cm._registry_cache["sig"] = None


REAL_LEDGER_PATH = os.path.join(REPO_ROOT, "shared", "config", "symbols_registry.json")


def _raw_file():
    """저장소에 실제로 커밋되는 원장 파일 (conftest 가 세션 동안 cm 의 경로를 사본으로 돌려놓기 때문에 직접 읽는다)"""
    with open(REAL_LEDGER_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _real_registry():
    return {k: cm._expand_entry(k, v) for k, v in _raw_file().items()}


# ── 1. 원장 구조/완전성 ──────────────────────────────────────────────────────

def test_ledger_file_is_structurally_valid():
    assert cm.validate_symbols_registry(_raw_file()) == []


def test_ledger_has_no_policy_copies():
    """정책과 같은 비용값을 종목마다 복사해 저장하면 정책 변경이 반영되지 않는다 (박제 금지)."""
    stale = []
    for sym, e in _raw_file().items():
        policy = DEFAULT_MARKET_POLICIES.get(e.get("market_type"), DEFAULT_MARKET_POLICIES["OTHER"])
        for f in cm.LEDGER_COST_FIELDS:
            if f in e:
                stale.append((sym, f))
        for f, v in (e.get("overrides") or {}).items():
            if cm._num_eq(v, policy[f]):
                stale.append((sym, f"overrides.{f} (정책과 동일)"))
    assert stale == [], f"정책 값이 종목에 박제되어 있습니다: {stale}"


def test_every_symbol_used_by_other_ledgers_is_registered():
    """source_mappings / symbol_relations(타겟+참조)가 쓰는 모든 심볼은 원장에 등록되어 있어야 한다."""
    used = set(SourceMappingManager.load_all_mappings(force_refresh=True))
    for target, info in SymbolRelationsManager.load_all_relations().items():
        used.add(target)
        used.update(str(r.get("symbol", "")).strip().upper() for r in info.get("references", []))
    real = _raw_file()
    missing = sorted(s for s in used if s and s not in real)
    assert missing == [], f"원장에 없는 심볼 (이름 없는 종목 버그의 씨앗): {missing}"


def test_registered_names_are_real_names():
    placeholders = [s for s, e in _real_registry().items() if e["name"].strip().upper() == s]
    assert placeholders == [], f"이름이 심볼 코드 그대로인(임시값) 종목: {placeholders}"


def test_same_asset_listings_are_linked():
    listed = {e["symbol"] for e in _real_registry().values() if e["asset_id"] == "BTC"}
    assert {"KRW-BTC", "BTCUSDT"} <= listed, "비트코인의 거래소별 리스팅이 asset_id=BTC 로 묶여야 합니다"


def test_unregistered_symbol_name_is_none_not_invented():
    assert cm.get_symbol_name("NOT_A_REAL_SYMBOL_XYZ") is None
    assert cm.get_symbol_display_name("NOT_A_REAL_SYMBOL_XYZ") == "NOT_A_REAL_SYMBOL_XYZ"


# ── 2. 접근 규약 (직접 파일 읽기 금지) ─────────────────────────────────────────

_GUARDED = {
    "symbols_registry.json": {"shared/config/config_manager.py"},
    "symbol_relations.json": {"shared/data_library/symbol_relations_manager.py"},
    "source_mappings.json": {"shared/data_library/source_mapping_manager.py"},
    "accounts.json": {"shared/config/account_registry.py"},
}
_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "tests", "data", "deploy", "scratch", "vultr_logs", "DataVault"}
_ACCESS_MARKERS = ("open(", "os.path.join", "Path(", "json.load")


def test_ledger_files_are_only_accessed_through_their_managers():
    """원장/대장 파일을 매니저 밖에서 직접 열면(과거 a728b06·2f49159 사고의 공통 원인) 스키마 가정이
    파일마다 어긋난다. 반드시 config_manager / SymbolRelationsManager / SourceMappingManager 를 경유해야 한다."""
    offenders = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, REPO_ROOT).replace(os.sep, "/")
            try:
                lines = open(full, "r", encoding="utf-8").read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for no, line in enumerate(lines, 1):
                code = line.split("#", 1)[0]
                for guarded, allowed in _GUARDED.items():
                    if guarded in code and rel not in allowed and any(m in code for m in _ACCESS_MARKERS):
                        offenders.append(f"{rel}:{no} → {guarded}")
    assert offenders == [], "매니저를 거치지 않고 원장/대장 파일을 직접 접근하는 코드:\n  " + "\n  ".join(offenders)


# ── 3. 동작 (사본 위에서) ────────────────────────────────────────────────────

def test_policy_change_propagates_to_symbols_without_override():
    """수수료는 종목이 아니라 시장 정책에 속한다: 정책을 바꾸면 override 없는 종목이 따라가야 한다."""
    assert "default_fee" not in _raw_file()["069500"].get("overrides", {})
    assert cm.is_symbol_registered("069500")
    with mock.patch.dict(DEFAULT_MARKET_POLICIES["KRX_ETF"], {"default_fee": 0.0123}):
        assert cm.get_symbol_info("069500")["default_fee"] == 0.0123
        assert cm.load_symbols_registry()["069500"]["default_fee"] == 0.0123


def test_save_compacts_and_roundtrips(tmp_ledger):
    reg = cm.load_symbols_registry()
    cm.save_symbols_registry(reg)
    on_disk = json.loads(tmp_ledger.read_text(encoding="utf-8"))
    assert all(f not in e for e in on_disk.values() for f in cm.LEDGER_COST_FIELDS)
    assert cm.load_symbols_registry() == reg, "compact 저장 후 다시 읽은 값이 원래와 같아야 합니다"


def test_market_type_change_does_not_freeze_old_policy(tmp_ledger):
    cm.register_symbol({"symbol": "zz_test1", "name": "테스트종목", "market_type": "KRX_ETF"})
    updated = cm.register_symbol({"symbol": "ZZ_TEST1", "market_type": "KRX_STOCK"})
    policy = DEFAULT_MARKET_POLICIES["KRX_STOCK"]
    assert updated["tick_size"] == policy["tick_size"] and updated["default_fee"] == policy["default_fee"]


def test_invalid_entry_is_rejected_and_file_untouched(tmp_ledger):
    before = tmp_ledger.read_text(encoding="utf-8")
    reg = cm.load_symbols_registry()
    reg["BAD1"] = {"symbol": "BAD1", "name": "", "market_type": "KRX_ETF"}
    with pytest.raises(ValueError):
        cm.save_symbols_registry(reg)
    assert tmp_ledger.read_text(encoding="utf-8") == before


def test_ensure_registered_never_overwrites(tmp_ledger):
    before = cm.get_symbol_info("069500")
    cm.ensure_symbol_registered("069500", name="엉뚱한 이름", market_type="OTHER")
    assert cm.get_symbol_info("069500") == before
    created = cm.ensure_symbol_registered("ZZ_NEW1", name="신규 참조", market_type="OTHER", tradable=False)
    assert created["name"] == "신규 참조" and created["tradable"] is False and cm.is_symbol_registered("ZZ_NEW1")


def test_delete_is_blocked_while_other_ledgers_use_the_symbol(tmp_ledger):
    with pytest.raises(cm.SymbolInUseError):
        cm.delete_symbol("NQ")  # symbol_relations 참조 + source_mappings 등재
    assert cm.is_symbol_registered("NQ")


def test_relations_names_follow_the_ledger(tmp_ledger):
    reg = cm.load_symbols_registry()
    reg["069500"]["name"] = "원장에서 바뀐 이름"
    cm.save_symbols_registry(reg)
    relations = SymbolRelationsManager.load_all_relations()
    assert relations["069500"]["name"] == "원장에서 바뀐 이름"


# ── 4. 이름은 원장 한 곳에만 저장 ──────────────────────────────────────────────

def _disk_json(name):
    vault_p = os.path.join(REPO_ROOT, "DataVault", "config", name)
    if os.path.exists(vault_p):
        with open(vault_p, "r", encoding="utf-8") as f:
            return json.load(f)
    with open(os.path.join(REPO_ROOT, "shared", "config", name), "r", encoding="utf-8") as f:
        return json.load(f)


def test_names_are_persisted_only_in_the_ledger():
    """source_mappings/symbol_relations 파일에는 원장이 이름을 아는 종목의 name 이 저장되어 있으면 안 된다
    (복사 저장하면 원장과 어긋나는 드리프트가 재발한다). 이름은 매니저가 로드 시 원장에서 해석한다."""
    dup = [f"source_mappings:{s}" for s, v in _disk_json("source_mappings.json").items()
           if "name" in v and cm.get_symbol_name(s)]
    for target, info in _disk_json("symbol_relations.json").items():
        if "name" in info and cm.get_symbol_name(target):
            dup.append(f"symbol_relations:{target}")
        dup += [f"symbol_relations:{target}>{r['symbol']}" for r in info.get("references", [])
                if "name" in r and cm.get_symbol_name(r["symbol"])]
    assert dup == [], f"원장 외의 파일에 이름 사본이 저장되어 있습니다: {dup}"


def test_managers_resolve_names_from_the_ledger_on_load():
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
    assert all(v.get("name") for v in mappings.values()), "로드된 대장 항목은 원장에서 해석된 name 을 가져야 합니다"
    relations = SymbolRelationsManager.load_all_relations()
    assert all(v.get("name") for v in relations.values())
    assert all(r.get("name") for v in relations.values() for r in v["references"])


def test_saving_relations_and_mappings_strips_names(tmp_path, monkeypatch):
    import shared.data_library.source_mapping_manager as smm
    import shared.data_library.symbol_relations_manager as srm

    rel = tmp_path / "symbol_relations.json"
    monkeypatch.setattr(srm, "_RELATIONS_FILE_PATH", str(rel))
    SymbolRelationsManager.save_all_relations({"069500": {"symbol": "069500", "name": "손으로 쓴 이름", "market_type": "KRX_ETF",
        "references": [{"symbol": "000660", "name": "다른 이름", "role": "leading_stock"}]}})
    saved = json.loads(rel.read_text(encoding="utf-8"))
    assert "name" not in saved["069500"] and "name" not in saved["069500"]["references"][0]

    mp = tmp_path / "source_mappings.json"
    monkeypatch.setattr(smm, "_MAPPINGS_FILE_PATH", str(mp))
    assert SourceMappingManager.save_all_mappings({"069500": {"symbol": "069500", "name": "손으로 쓴 이름", "category": "KRX_ETF"}})
    assert "name" not in json.loads(mp.read_text(encoding="utf-8"))["069500"]


# ── 5. 원장 하나로 통합 (구 symbols_master 하드코딩 제거) ─────────────────────────

def test_symbols_master_hardcoding_is_gone():
    assert not os.path.exists(os.path.join(REPO_ROOT, "shared", "config", "symbols_master.py"))
    offenders = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if fn.endswith(".py") and fn != "test_symbol_ledger.py":
                full = os.path.join(root, fn)
                text = open(full, "r", encoding="utf-8", errors="ignore").read()
                if "symbols_master" in text or "MASTER_SYMBOLS" in text:
                    offenders.append(os.path.relpath(full, REPO_ROOT))
    assert offenders == [], f"제거된 종목 하드코딩(symbols_master/MASTER_SYMBOLS)을 참조하는 코드: {offenders}"


def test_catalog_entries_are_hidden_by_default_but_searchable():
    catalog = [s for s, e in _real_registry().items() if e["catalog"]]
    assert catalog, "검색 후보(catalog)가 원장에 있어야 합니다"
    sample = catalog[0]
    assert sample not in cm.load_symbols_registry()
    assert sample in cm.load_symbols_registry(include_catalog=True)
    assert not cm.is_symbol_registered(sample)
    assert any(r["symbol"] == sample for r in cm.search_symbols(sample))
    assert cm.get_symbol_name(sample), "후보도 이름 조회는 가능해야 합니다"


def test_registering_a_catalog_symbol_promotes_it_without_losing_info(tmp_ledger):
    sample = next(s for s, e in cm.load_symbols_registry(include_catalog=True).items() if e["catalog"])
    before = cm.get_symbol_info(sample)
    promoted = cm.ensure_symbol_registered(sample)
    assert cm.is_symbol_registered(sample) and promoted["catalog"] is False
    assert promoted["name"] == before["name"] and promoted["market_type"] == before["market_type"]
    assert sample in cm.load_symbols_registry()


def test_save_symbols_registry_keeps_catalog_seeds(tmp_ledger):
    n_catalog = sum(1 for e in cm.load_symbols_registry(include_catalog=True).values() if e["catalog"])
    cm.save_symbols_registry(cm.load_symbols_registry())  # 등록 종목만 읽어 되돌려 저장해도
    assert sum(1 for e in cm.load_symbols_registry(include_catalog=True).values() if e["catalog"]) == n_catalog


def test_symbols_used_outside_the_three_ledgers_are_registered():
    """CTrade 선후행 풀 대상과 배포 스냅샷 참조 심볼도 원장에 있어야 한다.

    기대 심볼 목록을 테스트에 박지 않고 실제 설정 파일에서 읽는다 (설정이 바뀌어도 원칙이 그대로 검증된다).
    """
    real = _raw_file()
    with open(os.path.join(REPO_ROOT, "CTrade", "config", "trading_config.json"), "r", encoding="utf-8") as f:
        pool = json.load(f).get("GLOBAL_LEAD_LAG_POOL", [])
    used = {item["symbol"].strip().upper() for item in pool if item.get("symbol")}
    used.add("101SC000")  # 옛 배포 스냅샷(KODEX200_S10_BEST260921)이 참조
    missing = sorted(s for s in used if s not in real or real[s].get("catalog"))
    assert not missing, f"설정에서 쓰는데 원장에 등록되지 않은 심볼: {missing}"


def test_ledger_has_no_alias_field():
    """별칭(alias)은 폐지되었다 — 참조 컬럼/피처 이름은 심볼에서 기계적으로 유도한다(ref_key). 원장에 alias 가 있으면 검증 실패."""
    real = _real_registry()
    assert [s for s, e in real.items() if "alias" in e] == []
    problems = cm.validate_symbols_registry({"ZZTEST": {"symbol": "ZZTEST", "name": "x", "market_type": "OTHER", "alias": "zz"}})
    assert any("alias" in p for p in problems)
    # macro 시계열의 키는 원장 심볼이다
    assert cm.resolve_macro_symbol("10100") == "10100" and cm.resolve_macro_symbol("futures") is None


def test_asset_id_and_scope_lookups_are_ledger_driven():
    """같은 자산의 리스팅은 asset_id 로 묶이고, 자산군은 원장 market_type 에서 나온다 (티커 목록을 코드에 두지 않는다)."""
    for listing in ("KRW-BTC", "BTCUSDT", "BTCUSDT-PERP", "BTC-USD"):
        assert cm.get_asset_id(listing) == "BTC"
    for listing in ("KRW-ETH", "ETHUSDT", "ETHUSDT-PERP", "ETH-USD"):
        assert cm.get_asset_id(listing) == "ETH"
    # 티커 단독 표기(BTC/XRP)는 asset_id 로 해석된다
    assert cm.get_symbol_scope("BTC") == "crypto" and cm.get_symbol_scope("XRP") == "crypto"
    assert cm.get_symbol_scope("069500") == "krx" and cm.get_symbol_scope("QQQ") == "us"
    assert cm.get_symbol_scope("NQ") == "other"                    # 등록된 매크로/해외선물
    assert cm.get_symbol_scope("ZZ_NOT_REGISTERED") is None        # 미등록은 None (추정하지 않는다)
    # 검색 후보(catalog)는 등록으로 치지 않는다
    catalog_only = next(s for s, e in cm.load_symbols_registry(include_catalog=True).items() if e["catalog"])
    assert cm.get_symbol_scope(catalog_only) is None


def test_is_crypto_symbol_uses_the_ledger_before_shape_rules():
    from shared.core.market_utils import is_crypto_symbol
    assert is_crypto_symbol("KRW-XRP") and is_crypto_symbol("ETH-USD") and is_crypto_symbol("BTC")
    assert not is_crypto_symbol("069500") and not is_crypto_symbol("NVDA-PERP")   # 등록된 비코인 (원장 우선)
    assert not is_crypto_symbol("0167A0")        # 'SOL' 이 세션명에 끼어도 상품이 코인이 되지 않는다
    assert is_crypto_symbol("NEWCOINUSDT")       # 미등록일 때만 거래소 표기 규칙으로 추정
    assert not is_crypto_symbol("")


def test_every_krw_coin_is_grouped_under_its_asset():
    """KRW-XXX 코인은 전부 asset_id=XXX 로 묶여야 한다 (BTC/ETH 만 예외적으로 묶이면 짝짓기 규칙이 또 코드로 샌다)."""
    bad = [s for s, e in _real_registry().items() if s.startswith("KRW-") and not e.get("catalog") and e["asset_id"] != s.split("-", 1)[1]]
    assert bad == [], f"asset_id 가 없는 KRW 코인: {bad}"


# ── 종목 속성은 원장이 소유한다 (이름/시장/설명 사본 금지) + 정책 완전성 ─────────────────────

def test_ledger_owned_attributes_are_not_copied_into_other_ledgers():
    """이름/시장 분류/설명은 원장에만 저장한다. 대장에 사본이 남으면 종목마다 문장이 제각각 어긋난다
    (069500 의 설명이 세 파일에서 모두 달랐다). 수집 대장의 category 는 '수집 파이프라인 분류'라 별개다."""
    dup = []
    for sym, v in _disk_json("source_mappings.json").items():
        dup += [f"source_mappings:{sym}.{f}" for f in ("name", "description") if f in v and cm.get_ledger_fields(sym, (f,))]
    for target, info in _disk_json("symbol_relations.json").items():
        dup += [f"symbol_relations:{target}.{f}" for f in cm.LEDGER_OWNED_FIELDS if f in info and cm.get_ledger_fields(target, (f,))]
        for r in info.get("references", []):
            dup += [f"symbol_relations:{target}>{r['symbol']}.{f}" for f in ("name", "description")
                    if f in r and cm.get_ledger_fields(r["symbol"], (f,))]
    assert dup == [], f"원장이 소유한 속성이 다른 파일에 복사 저장되어 있습니다: {dup}"


def test_managers_hydrate_description_and_market_type_from_the_ledger():
    relations = SymbolRelationsManager.load_all_relations()
    assert all(v.get("market_type") for v in relations.values())
    for target, info in relations.items():
        assert info["market_type"] == cm.get_symbol_info(target)["market_type"]
        assert info.get("description", "") == cm.get_symbol_info(target).get("description", "")
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
    for sym, entry in mappings.items():
        assert entry.get("description", "") == cm.get_symbol_info(sym).get("description", "")


def test_saving_satellites_never_loses_text_the_ledger_does_not_have(tmp_path, monkeypatch):
    """원장에 값이 없는 속성은 지우지 않는다 (정리 과정에서 텍스트가 사라지는 사고 방지)."""
    import shared.data_library.symbol_relations_manager as srm
    monkeypatch.setattr(srm, "_RELATIONS_FILE_PATH", str(tmp_path / "symbol_relations.json"))
    out = SymbolRelationsManager._strip_ledger_fields({
        "ZZ_UNKNOWN": {"symbol": "ZZ_UNKNOWN", "description": "원장에 없는 종목의 설명", "market_type": "OTHER", "references": []},
        "069500": {"symbol": "069500", "description": "대장의 사본", "market_type": "KRX_ETF", "references": []},
    })
    assert out["ZZ_UNKNOWN"]["description"] == "원장에 없는 종목의 설명"      # 원장이 모르면 그대로 둔다
    assert "description" not in out["069500"] and "market_type" not in out["069500"]   # 원장이 알면 제거


def test_ledger_no_longer_carries_the_dead_data_source_field():
    assert [s for s, e in _raw_file().items() if "data_source" in e] == []
    assert all("data_source" not in p for p in DEFAULT_MARKET_POLICIES.values())


def test_every_tradable_symbol_has_an_explicit_market_policy():
    """정책이 없는 market_type 은 조용히 OTHER(수수료 0.015%, 호가 1.0)로 떨어져 비용이 틀어진다.
    (KRW-USDT 가 CRYPTO_SPOT 이라 업비트 종목인데 수수료 0.015% 로 잡혔던 사고) 거래 대상은 명시 정책이 있어야 한다.
    참조 전용(tradable=False)은 비용을 쓰지 않으므로 예외."""
    bad = [(s, e["market_type"]) for s, e in _real_registry().items()
           if not e.get("catalog") and e["tradable"] and e["market_type"] not in DEFAULT_MARKET_POLICIES]
    assert bad == [], f"거래 대상인데 시장 정책이 없는 종목: {bad}"


def test_upbit_krw_markets_resolve_to_the_crypto_policy_cost():
    p = DEFAULT_MARKET_POLICIES["CRYPTO"]
    for s, e in _real_registry().items():
        if s.startswith("KRW-") and not e.get("catalog"):
            assert e["market_type"] == "CRYPTO" and e["default_fee"] == p["default_fee"], f"{s}: 업비트 KRW 마켓은 CRYPTO 정책 수수료여야 합니다"


# ── 배포 세션 스냅샷의 신원 ──────────────────────────────────────────────────────

def test_general_config_save_cannot_overwrite_a_sessions_identity():
    """화면에 남은 다른 프로젝트 설정(SYMBOL_UPBIT=069500)이 일반 설정 저장으로 세션 스냅샷의 종목/봉/세션ID 를
    덮어써 TIGER(396500)/SOLAI(0167A0) 스냅샷이 KODEX 200 으로 기록되던 사고를 막는다."""
    snap = {"SYMBOL_UPBIT": "396500", "SYMBOL_BINANCE": "", "INTERVAL": "10s", "ACTIVE_SESSION_ID": "TIGER", "FEE_RATE": 0.0001}
    merged = cm.merge_into_session_snapshot(snap, {"SYMBOL_UPBIT": "069500", "INTERVAL": "1m", "ACTIVE_SESSION_ID": "OTHER",
                                                   "SYMBOL_BINANCE": "X", "FEE_RATE": 0.0002, "NEW_KEY": 1})
    assert {k: merged[k] for k in cm.SESSION_IDENTITY_KEYS} == {k: snap[k] for k in cm.SESSION_IDENTITY_KEYS}
    assert merged["FEE_RATE"] == 0.0002 and merged["NEW_KEY"] == 1       # 그 외 설정은 정상 병합
    assert snap["SYMBOL_UPBIT"] == "396500"                              # 입력은 변경하지 않는다


def test_deploy_snapshots_match_their_folder_identity():
    """배포 폴더 <종목코드>_<봉>/<세션ID> 와 그 안의 스냅샷 신원 키가 일치해야 한다."""
    import glob
    bad = []
    for cs in glob.glob(os.path.join(REPO_ROOT, "CAiModelBaker", "deploy", "*", "*", "config_snapshot.json")) + \
            glob.glob(os.path.join(REPO_ROOT, "shared", "deploy", "*", "*", "config_snapshot.json")):
        parts = cs.replace(os.sep, "/").split("/")
        folder, session = parts[-3], parts[-2]
        code, _, interval = folder.rpartition("_")
        snap = json.load(open(cs, encoding="utf-8"))
        if str(snap.get("SYMBOL_UPBIT", "")).split("-")[-1] != code:
            bad.append(f"{folder}/{session}: SYMBOL_UPBIT={snap.get('SYMBOL_UPBIT')}")
        if snap.get("INTERVAL") != interval:
            bad.append(f"{folder}/{session}: INTERVAL={snap.get('INTERVAL')}")
        if snap.get("ACTIVE_SESSION_ID") != session:
            bad.append(f"{folder}/{session}: ACTIVE_SESSION_ID={snap.get('ACTIVE_SESSION_ID')}")
    assert bad == [], f"스냅샷 신원 키가 배포 폴더와 다릅니다: {bad}"
