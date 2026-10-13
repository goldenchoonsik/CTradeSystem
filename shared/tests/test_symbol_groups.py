# -*- coding: utf-8 -*-
"""
Tests for Symbol Groups (Factor Baskets) and Target Group Resolution.
Verifies zero-drift ledger hydration, atomic CRUD, and target flattening.
"""

import pytest
import os
import json
from shared.data_library.symbol_relations_manager import SymbolRelationsManager


def test_load_all_groups_and_ledger_hydration():
    """그룹 로드 시 마스터 원장(symbols_registry.json) 필드가 동적 수화되는지 검증"""
    groups = SymbolRelationsManager.load_all_groups()
    assert isinstance(groups, dict)
    assert "KRX_SEMI_LEADERS" in groups

    semi_group = groups["KRX_SEMI_LEADERS"]
    assert semi_group["name"] == "반도체 대장주 팩"
    assert semi_group["default_role"] == "leading_stock"
    
    # 멤버 종목 검증
    members = semi_group["members"]
    assert len(members) >= 2
    symbols = [m["symbol"] for m in members]
    assert "000660" in symbols
    assert "005930" in symbols

    # 원장에서 이름이 동적으로 주입되었는지 확인 (Zero-Drift)
    hynix = next(m for m in members if m["symbol"] == "000660")
    samsung = next(m for m in members if m["symbol"] == "005930")
    assert hynix.get("name") == "SK하이닉스"
    assert samsung.get("name") == "삼성전자"
    assert hynix.get("role") == "leading_stock"


def test_group_crud_and_disk_purity(tmp_path, monkeypatch):
    """그룹 생성/수정/삭제 및 디스크에 원장 필드가 중복 저장되지 않는지(Purity) 검증"""
    test_file = str(tmp_path / "symbol_groups.json")
    monkeypatch.setattr("shared.data_library.symbol_relations_manager._GROUPS_FILE_PATH", test_file)

    # 1. 신규 팩 생성
    created = SymbolRelationsManager.create_or_update_group("TEST_BATTERY", {
        "name": "2차전지 대장주 팩",
        "category": "SECTOR",
        "default_role": "leading_stock",
        "description": "국내 2차전지 대표 종목",
        "members": [{"symbol": "000660"}]  # 임시로 유효 심볼 사용
    })
    assert created is True

    # 2. 디스크 파일 검사: 이름('SK하이닉스')이 디스크에 박제되지 않고 순수 심볼만 저장되었는지 확인
    with open(test_file, "r", encoding="utf-8") as f:
        disk_json = json.load(f)
    assert "TEST_BATTERY" in disk_json
    member_entry = disk_json["TEST_BATTERY"]["members"][0]
    assert member_entry["symbol"] == "000660"
    assert "name" not in member_entry  # 원장 필드 제거 보장!

    # 3. 로드 검사: 읽을 때는 원장 이름이 붙어서 나오는지 확인
    loaded = SymbolRelationsManager.get_group("TEST_BATTERY")
    assert loaded is not None
    assert loaded["members"][0]["name"] == "SK하이닉스"

    # 4. 멤버 추가
    added = SymbolRelationsManager.add_member_to_group("TEST_BATTERY", {"symbol": "005930"})
    assert added is True
    updated = SymbolRelationsManager.get_group("TEST_BATTERY")
    assert len(updated["members"]) == 2

    # 5. 멤버 제거
    removed = SymbolRelationsManager.remove_member_from_group("TEST_BATTERY", "000660")
    assert removed is True
    updated2 = SymbolRelationsManager.get_group("TEST_BATTERY")
    assert len(updated2["members"]) == 1
    assert updated2["members"][0]["symbol"] == "005930"

    # 6. 그룹 삭제
    success, msg = SymbolRelationsManager.delete_group("TEST_BATTERY")
    assert success is True
    assert SymbolRelationsManager.get_group("TEST_BATTERY") is None


def test_target_group_attachment_and_deduplication(tmp_path, monkeypatch):
    """타겟에 그룹 바인딩 시 평면화 및 중복 제거(Deduplication) 검증"""
    rel_file = str(tmp_path / "symbol_relations.json")
    grp_file = str(tmp_path / "symbol_groups.json")
    monkeypatch.setattr("shared.data_library.symbol_relations_manager._RELATIONS_FILE_PATH", rel_file)
    monkeypatch.setattr("shared.data_library.symbol_relations_manager._GROUPS_FILE_PATH", grp_file)

    # 테스트 팩 2개 구성 (하나는 005930 포함, 다른 하나도 005930 포함)
    SymbolRelationsManager.create_or_update_group("GRP_A", {
        "name": "팩 A",
        "default_role": "leading_stock",
        "members": [{"symbol": "005930"}, {"symbol": "000660"}]
    })
    SymbolRelationsManager.create_or_update_group("GRP_B", {
        "name": "팩 B",
        "default_role": "kospi_futures",
        "members": [{"symbol": "005930"}, {"symbol": "10100"}]
    })

    # 타겟에 GRP_A, GRP_B 장착 및 개별 단독 참조로 10100 추가
    SymbolRelationsManager.attach_group_to_target("069500", "GRP_A")
    SymbolRelationsManager.attach_group_to_target("069500", "GRP_B")
    SymbolRelationsManager.add_or_update_reference("069500", {"symbol": "10100", "role": "kospi_futures"})

    # 평면화된 참조 목록 조회
    resolved = SymbolRelationsManager.get_relations_for_target("069500")
    resolved_symbols = [r["symbol"] for r in resolved]

    # 중복 없이 정확히 3개 종목(005930, 000660, 10100)만 존재해야 함
    assert len(resolved_symbols) == 3
    assert resolved_symbols == ["005930", "000660", "10100"]

    # join_plan 검증 (leading_stock 은 refs, 나머지는 series)
    plan = SymbolRelationsManager.join_plan("069500")
    assert plan["refs"] == ["005930", "000660"]
    assert plan["series"] == ["10100"]

    # 그룹 분리
    SymbolRelationsManager.detach_group_from_target("069500", "GRP_A")
    resolved_after = SymbolRelationsManager.get_relations_for_target("069500")
    symbols_after = [r["symbol"] for r in resolved_after]
    assert "000660" not in symbols_after
    assert "005930" in symbols_after  # GRP_B에 여전히 남아있음
    assert "10100" in symbols_after


def test_api_symbol_groups_and_relations_endpoints():
    """FastAPI REST 엔드포인트 연동 테스트 (/api/symbols/groups, /api/symbols/relations)"""
    from fastapi.testclient import TestClient
    from CAiModelBaker.web_dashboard.main import app

    client = TestClient(app)

    # 1. GET /api/symbols/groups 조회
    resp = client.get("/api/symbols/groups")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert len(data["groups"]) >= 3
    semi_grp = next((g for g in data["groups"] if g["id"] == "KRX_SEMI_LEADERS"), None)
    assert semi_grp is not None
    assert semi_grp["name"] == "반도체 대장주 팩"
    assert any(m["symbol"] == "000660" for m in semi_grp["members"])

    # 2. GET /api/symbols/relations?target=069500 조회
    resp_rel = client.get("/api/symbols/relations?target=069500")
    assert resp_rel.status_code == 200
    rel_data = resp_rel.json()
    assert rel_data["status"] == "ok"
    assert "groups" in rel_data
    assert "references" in rel_data
    group_ids = [g["id"] for g in rel_data["groups"]]
    assert "KRX_SEMI_LEADERS" in group_ids
    ref_symbols = [r["symbol"] for r in rel_data["references"]]
    assert "000660" in ref_symbols
    assert "005930" in ref_symbols

    # 3. GET /api/features/config?target=069500 조회
    resp_cfg = client.get("/api/features/config?target=069500")
    assert resp_cfg.status_code == 200
    cfg_data = resp_cfg.json()
    assert "reference_groups" in cfg_data
    assert len(cfg_data["reference_groups"]) >= 1

