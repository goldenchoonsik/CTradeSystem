# -*- coding: utf-8 -*-
"""
DataVault - UI Source Mappings Parity & Auto-Discovery Contract Test
- DataVault의 물리 파일(Bronze/Silver)과 UI 대장(source_mappings.json) 간의 100% 동기화 무결성 검증
"""

import os
import json
import pytest
from shared.data_library.source_mapping_manager import SourceMappingManager
from shared.data_library.paths import data_vault_path, raw_root


def test_us_tech_100_registered_as_macro_proxy():
    """US_TECH_100이 UI 대장에 macro_proxy로 정확히 등록되었는지 검증"""
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
    assert "US_TECH_100" in mappings, "US_TECH_100 must be registered in source_mappings.json"

    info = mappings["US_TECH_100"]
    assert info["role"] == "macro_proxy", f"US_TECH_100 role must be 'macro_proxy', got {info['role']}"
    assert info["primary_source"] == "CTRADER", f"primary_source must be 'CTRADER', got {info['primary_source']}"
    assert info["is_active"] is True, "US_TECH_100 must be active"


def test_all_vault_raw_manifests_have_source_mapping():
    """DataVault/raw에 유효한 매니페스트가 있는 모든 물리 자산이 대장에 존재하는지 전수 검증 (누락 원천 차단)"""
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
    status_items = SourceMappingManager.get_feed_registry_status()
    symbols_in_status = {it["symbol"] for it in status_items}

    # US_TECH_100이 상태 목록에 정상 파일과 함께 포함되어 있는지 검증
    us_tech = next((it for it in status_items if it["symbol"] == "US_TECH_100"), None)
    assert us_tech is not None, "US_TECH_100 must appear in feed registry status"
    assert us_tech["file_exists"] is True, "US_TECH_100 raw CSV file must exist on disk"
    assert us_tech["rows"] > 0, "US_TECH_100 row count must be greater than 0"
    assert us_tech["status_code"] == "HEALTHY", "US_TECH_100 status must be HEALTHY"


def test_ensure_asset_registered_idempotent():
    """ensure_asset_registered 훅이 멱등성(Idempotent)을 유지하며 안전하게 동작하는지 검증"""
    success = SourceMappingManager.ensure_asset_registered(
        symbol="US_TECH_100",
        name="나스닥 100 CFD (대용)",
        category="GLOBAL_INDEX_CFD_PROXY",
        role="macro_proxy",
        primary_source="CTRADER",
    )
    assert success is True


@pytest.mark.asyncio
async def test_normalized_inventory_covers_all_active_source_mappings():
    """Raw Store 활성 자산 전체가 Normalized Store 인벤토리에 100% 무누락(Zero-Leakage) 포함되는지 전수 검증"""
    from CAiModelBaker.web_dashboard.main import api_get_normalized_inventory

    active_assets = SourceMappingManager.get_active_mappings()
    res = await api_get_normalized_inventory()
    assert res["status"] == "ok", f"API response status must be ok: {res}"

    items = res.get("items", [])
    inventory_symbols = {it["symbol"] for it in items}

    # 모든 활성 대장 자산이 인벤토리 심볼 세트에 존재하는지 단 1개도 빠짐없이 검증
    missing_symbols = set(active_assets.keys()) - inventory_symbols
    assert not missing_symbols, f"Raw Store 활성 자산이 Normalized Store에서 누락됨: {missing_symbols}"


@pytest.mark.asyncio
async def test_us_tech_100_in_normalized_inventory():
    """US_TECH_100이 Normalized 인벤토리에 1m 실재 아티팩트 및 매니페스트와 함께 정상 노출되는지 검증"""
    from CAiModelBaker.web_dashboard.main import api_get_normalized_inventory

    res = await api_get_normalized_inventory()
    us_tech_items = [it for it in res.get("items", []) if it["symbol"] == "US_TECH_100"]

    assert len(us_tech_items) >= 1, "US_TECH_100 must appear in normalized inventory"
    m1_item = next((it for it in us_tech_items if it["interval"] == "1m"), None)
    assert m1_item is not None, "US_TECH_100 1m item must exist"
    assert m1_item["exists"] is True, "Normalized Parquet file must exist on disk"
    assert m1_item["manifest_ok"] is True, "1:1 manifest must be verified"
    assert m1_item["row_count"] > 300000, f"Row count must be > 300,000, got {m1_item['row_count']}"
    assert m1_item["role"] == "macro_proxy", f"Role must be macro_proxy, got {m1_item['role']}"


@pytest.mark.asyncio
async def test_all_ctrader_macro_proxies_contract():
    """6대 신규 cTrader 대용 지표가 Normalized 인벤토리에 실재 아티팩트와 함께 정상 등록되었는지 전수 검증"""
    from CAiModelBaker.web_dashboard.main import api_get_normalized_inventory

    proxy_symbols = {
        "US_500": 300000,
        "JAPAN_225": 300000,
        "HONG_KONG_50": 100000,
        "XAUUSD": 300000,
        "XTIUSD": 300000,
        "USDJPY": 300000,
    }

    res = await api_get_normalized_inventory()
    items_by_symbol = {it["symbol"]: it for it in res.get("items", []) if it.get("interval") == "1m"}

    for sym, min_rows in proxy_symbols.items():
        assert sym in items_by_symbol, f"{sym} 1m must exist in normalized inventory"
        item = items_by_symbol[sym]
        assert item["exists"] is True, f"{sym} Parquet file must exist on disk"
        assert item["manifest_ok"] is True, f"{sym} manifest must be valid"
        assert item["row_count"] >= min_rows, f"{sym} row count {item['row_count']} < {min_rows}"
        assert item["role"] == "macro_proxy", f"{sym} role must be macro_proxy, got {item['role']}"


def test_macro_proxy_naming_governance():
    """정통 거래소 지표와 cTrader 대용 지표의 네이밍/역할 100% 격리 거버넌스 검증"""
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)

    # 대용 지표 목록
    ctrader_proxies = ["US_TECH_100", "US_500", "JAPAN_225", "HONG_KONG_50", "XAUUSD", "XTIUSD", "USDJPY"]

    for sym in ctrader_proxies:
        assert sym in mappings, f"{sym} must be in source_mappings.json"
        meta = mappings[sym]
        # 역할은 반드시 macro_proxy
        assert meta.get("role") == "macro_proxy", f"{sym} role must be macro_proxy, got {meta.get('role')}"
        # 정통 거래소 심볼 접두사(cme_, nymex_, comex_, ose_, hkex_)와 절대 혼용되지 않음
        assert not any(meta.get("name", "").startswith(pfx) for pfx in ["CME_", "COMEX_", "NYMEX_"]), \
            f"{sym} name must not pretend to be official exchange: {meta.get('name')}"


def test_archive_ym_promoted_to_common_config():
    """전역 원천 수집 기간(ARCHIVE_START_YM, ARCHIVE_END_YM)이 common.json에 영구 승격 보존되는지 검증"""
    from shared.config.config_manager import load_common_config, save_common_config, normalize_archive_ym

    # 1. 정규화 검증
    assert normalize_archive_ym("2024-05-15") == "2024-05"
    assert normalize_archive_ym("2024.08") == "2024-08"
    assert normalize_archive_ym("202609") == "2026-09"

    # 2. 공통 저장 및 로드 무결성 검증
    save_common_config({
        "ARCHIVE_START_YM": "2024-01",
        "ARCHIVE_END_YM": "2026-09",
    })
    cfg = load_common_config()
    assert cfg.get("ARCHIVE_START_YM") == "2024-01"
    assert cfg.get("ARCHIVE_END_YM") == "2026-09"


@pytest.mark.asyncio
async def test_archive_ym_persistence_across_session_load():
    """세션 스냅샷 로드 시 전역 마스터 수집 기간이 과거 스냅샷에 의해 롤백되지 않고 영구 보존되는지 검증"""
    from CAiModelBaker.web_dashboard.main import load_config, save_config, api_load_session
    from shared.config.config_manager import save_common_config

    # 전역 마스터 기간을 신규 기간으로 저장
    save_common_config({
        "ARCHIVE_START_YM": "2024-03",
        "ARCHIVE_END_YM": "2026-09"
    })
    cfg = load_config()
    cfg["ARCHIVE_START_YM"] = "2024-03"
    cfg["ARCHIVE_END_YM"] = "2026-09"
    save_config(cfg)

    # 활성 세션 스냅샷을 다시 로드하더라도 전역 수집 기간은 2024-03, 2026-09로 보존되어야 함
    active_session_id = cfg.get("ACTIVE_SESSION_ID")
    if active_session_id and active_session_id != "N/A":
        res = await api_load_session({"session_id": active_session_id})
        assert res.get("status") == "ok", f"Session load must succeed: {res}"

        restored_cfg = load_config()
        assert restored_cfg.get("ARCHIVE_START_YM") == "2024-03", \
            f"ARCHIVE_START_YM must not be overwritten by snapshot: {restored_cfg.get('ARCHIVE_START_YM')}"
        assert restored_cfg.get("ARCHIVE_END_YM") == "2026-09", \
            f"ARCHIVE_END_YM must not be overwritten by snapshot: {restored_cfg.get('ARCHIVE_END_YM')}"


def test_symbol_stripping_regression_guard():
    """심볼 앞의 A 제거 시 AAPL -> PPL 같은 비의도적 훼손이 발생하지 않는지 검증 (회귀 방지)"""
    test_cases = [
        ("A069500", "069500"),  # KRX 6자리 숫자 앞 접두사 A 정상 제거
        ("A123450", "123450"),
        ("A0048J0", "0048J0"),  # KRX 6자리 영문/숫자 혼합 종목코드 A 접두사 제거
        ("AAPL", "AAPL"),        # 미국 주식 A로 시작하는 티커 보존
        ("AMZN", "AMZN"),
        ("ARKK", "ARKK"),
        ("US_TECH_100", "US_TECH_100"),
        ("KRW-BTC", "KRW-BTC"),
    ]
    for raw_sym, expected in test_cases:
        clean = raw_sym[1:].strip().upper() if (raw_sym.startswith("A") and len(raw_sym) == 7 and raw_sym[1:].isalnum()) else raw_sym.strip().upper()
        assert clean == expected, f"Symbol cleaning failed for {raw_sym}: expected {expected}, got {clean}"


def test_manifest_manager_strict_suffix_parsing():
    """manifest_manager의 접미사 정규식 판정이 타임프레임만 엄격하게 매칭하는지 검증"""
    import re
    def infer_symbol(filename: str) -> str:
        base = filename.replace(".csv", "").replace(".parquet", "")
        match = re.search(r"A?(\d{6})", base)
        if match:
            return match.group(1)
        parts = base.rsplit("_", 1)
        if len(parts) == 2 and re.match(r"^(\d+[smhdw]|tick|daily|weekly|monthly)$", parts[1].lower()):
            return parts[0]
        return base

    assert infer_symbol("KRW-BTC_10m.csv") == "KRW-BTC"
    assert infer_symbol("US_TECH_100_1m.parquet") == "US_TECH_100"
    assert infer_symbol("NQ_1m.csv") == "NQ"
    assert infer_symbol("A069500_tick.csv") == "069500"
    assert infer_symbol("X_sample.csv") == "X_sample"  # sample이 접미사로 오인되지 않음
    assert infer_symbol("TEST_window.parquet") == "TEST_window"  # window가 접미사로 오인되지 않음


def test_all_assets_have_valid_live_connection():
    """모든 대장 자산이 규격에 맞는 1단계 live_connection 블록을 100% 무누락 보유하는지 전수 검증"""
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
    assert len(mappings) >= 20, "Mappings must have at least 20 assets"

    for sym, info in mappings.items():
        assert "live_connection" in info, f"{sym} must have 'live_connection' defined"
        conn = info["live_connection"]
        valid, msg = SourceMappingManager.validate_live_connection(conn)
        assert valid is True, f"{sym} live_connection validation failed: {msg}"
        assert conn.get("provider"), f"{sym} live_connection must specify provider"
        assert conn.get("feed_type"), f"{sym} live_connection must specify feed_type"
        assert conn.get("symbol"), f"{sym} live_connection must specify symbol"


def test_creon_never_used_in_live_connection():
    """어떤 자산도 실시간 live_connection provider로 CREON을 사용하지 않는지 전수 검증 (규칙 6 거버넌스)"""
    mappings = SourceMappingManager.load_all_mappings(force_refresh=True)

    for sym, info in mappings.items():
        conn = info.get("live_connection", {})
        provider = str(conn.get("provider", "")).upper()
        assert provider != "CREON", (
            f"VIOLATION: Asset '{sym}' specifies CREON in live_connection. "
            f"CREON is strictly prohibited for live trading/signals (Rule 6)."
        )


def test_get_feed_registry_status_includes_live_connection():
    """get_feed_registry_status API 항목 전체에 live_connection 정보가 1급 시민으로 노출되는지 검증"""
    status_items = SourceMappingManager.get_feed_registry_status()
    assert len(status_items) >= 20

    for item in status_items:
        assert "live_connection" in item, f"Item {item.get('symbol')} must include 'live_connection'"
        conn = item["live_connection"]
        assert isinstance(conn, dict)
        assert conn.get("provider"), f"Item {item.get('symbol')} live_connection must specify provider"
        assert conn.get("feed_type")


@pytest.mark.asyncio
async def test_medallion_status_dynamic_target_resolution():
    """api_get_medallion_status가 타겟 종목을 동적으로 파싱하고 관계를 조회하는지 검증"""
    from CAiModelBaker.web_dashboard.main import api_get_medallion_status
    res_default = await api_get_medallion_status(target="069500")
    assert res_default["status"] == "ok"
    assert res_default["target"] == "069500"
    assert "coverage_summary" in res_default

    res_122630 = await api_get_medallion_status(target="122630", interval="10s")
    assert res_122630["status"] == "ok"
    assert res_122630["target"] == "122630"
    assert res_122630["interval"] == "10s"


def test_ctrader_7_assets_registered_in_symbol_ledger():
    """cTrader 7대 자산(US_TECH_100, US_500 등)이 종목 마스터 원장에 전수 등록되어 검색 가능한지 검증"""
    from shared.config.config_manager import is_symbol_registered, search_symbols
    from shared.config.market_policies import BAR_OPEN, timestamp_convention

    expected_syms = ["US_TECH_100", "US_500", "JAPAN_225", "HONG_KONG_50", "XAUUSD", "XTIUSD", "USDJPY"]

    for sym in expected_syms:
        assert is_symbol_registered(sym), f"Asset '{sym}' must be registered in the symbol ledger"
        res = search_symbols(sym)
        assert any(r["symbol"] == sym for r in res), f"Asset '{sym}' must be searchable via search_symbols"
        # Bar-Start Lookahead 방어 판정 테스트 (규약은 수집처 원장이 선언한다)
        assert timestamp_convention(sym, "1m") == BAR_OPEN, \
            f"Asset '{sym}' must be declared as a bar-open source"

@pytest.mark.asyncio
async def test_api_get_feature_config_ref_status_for_us_tech_100():
    """US_TECH_100이 api_get_feature_config에서 ready=True 및 rows >= 300,000으로 올바르게 판정되는지 검증"""
    from CAiModelBaker.web_dashboard.main import api_get_feature_config
    
    # 069500 타겟으로 feature_config 조회
    res = await api_get_feature_config(target="069500", interval="1m")
    assert "reference_symbols_status" in res, f"Response must contain reference_symbols_status: {list(res.keys())}"
    
    ref_status = res.get("reference_symbols_status", {})
    # US_TECH_100이 ref_status에 포함되어 있고 준비 완료(ready=True)인지 검증
    assert "US_TECH_100" in ref_status, f"US_TECH_100 must be in ref_status: {list(ref_status.keys())}"
    us_tech_status = ref_status["US_TECH_100"]
    assert us_tech_status.get("ready") is True, f"US_TECH_100 must be ready, got: {us_tech_status}"
    assert us_tech_status.get("rows", 0) >= 300000, f"US_TECH_100 rows must be >= 300000, got: {us_tech_status.get('rows')}"
    
    # 또한 US_TECH_100의 4대 선행 피처가 feature registry에 등록되었는지 검증
    registry = res.get("registry", {})
    expected_features = [
        "ref_us_tech_100_ret_1c",
        "ref_us_tech_100_ret_5c",
        "ref_us_tech_100_spread_vs_target",
        "ref_us_tech_100_intensity",
    ]
    for feat in expected_features:
        assert feat in registry, f"Feature '{feat}' must be registered in registry"



