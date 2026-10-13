# -*- coding: utf-8 -*-
"""
테스트: 종목 상관관계 및 마스터 검색 관리자 (SymbolRelationsManager) 무결성 검증
"""

import pytest
import os
import json
import tempfile
from shared.data_library.symbol_relations_manager import SymbolRelationsManager


class TestSymbolRelations:

    def test_load_all_relations(self):
        """기본 symbol_relations.json 리소스 로드 검증"""
        data = SymbolRelationsManager.load_all_relations()
        assert isinstance(data, dict)
        assert "069500" in data
        assert "KRW-BTC" in data or "BTC" in data

    @pytest.mark.skip(reason="정리된 구 모델/변경된 shared 설정 기준 테스트 - 현행 설정에 맞게 재작성 필요")
    def test_get_relations_for_target(self):
        """KODEX 200 (069500) 타겟의 대장주 및 매크로 관계 목록 조회 검증"""
        refs = SymbolRelationsManager.get_relations_for_target("069500")
        assert len(refs) >= 2
        symbols = [r["symbol"] for r in refs]
        assert "000660" in symbols  # SK하이닉스
        assert "005930" in symbols  # 삼성전자
        assert "379810" in symbols  # KODEX 미국나스닥100TR

    def test_add_and_remove_reference(self):
        """동적 대장주 부착 및 분리 무결성 검증"""
        target = "069500"
        dummy_ref = {
            "symbol": "TEST99",
            "alias": "test_ref",
            "name": "테스트대장주",
            "role": "leading_stock",
            "default_price": 50000.0,
            "description": "단위테스트용"
        }

        # 1. 추가
        success = SymbolRelationsManager.add_or_update_reference(target, dummy_ref)
        assert success is True

        refs = SymbolRelationsManager.get_relations_for_target(target)
        assert any(r["symbol"] == "TEST99" for r in refs)

        # 2. 제거
        success_del = SymbolRelationsManager.remove_reference(target, "TEST99")
        assert success_del is True

        refs_after = SymbolRelationsManager.get_relations_for_target(target)
        assert not any(r["symbol"] == "TEST99" for r in refs_after)

    def test_search_symbols(self):
        """종목 마스터 원장 검색 검증 (한글, 심볼, 대소문자) — 검색은 원장 하나로 통일"""
        from shared.config.config_manager import search_symbols
        # 하이닉스 검색
        results_kr = search_symbols("하이닉스")
        assert any(r["symbol"] == "000660" for r in results_kr)

        # 코드 검색
        results_code = search_symbols("005930")
        assert any(r["symbol"] == "005930" for r in results_code)

        # 나스닥 검색
        results_nq = search_symbols("NQ")
        assert any(r["symbol"] == "NQ" for r in results_nq)

        # 머니마켓 ETF 검색 (0048J0)
        results_mm = search_symbols("0048J0")
        assert any(r["symbol"] == "0048J0" for r in results_mm)
        results_mm_kw = search_symbols("머니마켓")
        assert any(r["symbol"] == "0048J0" for r in results_mm_kw)


def test_orphan_targets_report_reference_usage(tmp_path, monkeypatch):
    """프로젝트 없는 타겟 선언은 '다른 타겟의 참조로 쓰이는지'를 함께 보고해야 한다 (해제해도 참조 사용은 유지됨)"""
    import shared.data_library.symbol_relations_manager as srm

    rel_file = tmp_path / "symbol_relations.json"
    rel_file.write_text(json.dumps({
        "069500": {"symbol": "069500", "name": "KODEX 200", "market_type": "KRX_ETF",
                   "references": [{"symbol": "122630", "name": "KODEX 레버리지", "role": "leading_stock"}]},
        "122630": {"symbol": "122630", "name": "KODEX 레버리지", "market_type": "KRX_ETF", "references": []},
        "229200": {"symbol": "229200", "name": "KODEX 코스닥150", "market_type": "KRX_ETF", "references": []},
        "US_TECH_100": {"symbol": "US_TECH_100", "name": "나스닥100 CFD", "market_type": "GLOBAL_INDEX_CFD", "references": []},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(srm, "_RELATIONS_FILE_PATH", str(rel_file))

    orphans = {o["target_symbol"]: o for o in SymbolRelationsManager.list_orphan_targets(["069500"])}

    assert set(orphans) == {"122630", "229200"}  # 프로젝트 있는 069500, 매크로 프록시 US_TECH_100 제외
    assert [u["target_symbol"] for u in orphans["122630"]["used_as_reference_by"]] == ["069500"]
    assert orphans["229200"]["used_as_reference_by"] == []


def test_get_available_vault_references():
    """DataVault 가용 수집 자산 조회 및 미등록 자산 우선순위 정렬 검증"""
    res = SymbolRelationsManager.get_available_vault_references("KRW-BTC")
    assert res["target"] == "KRW-BTC"
    assert "unattached" in res
    assert "all_collected" in res
    assert res["counts"]["total_collected"] >= len(res["unattached"])

    # KRW-BTC는 BTC_MASTER_QUANT_PACK이 장착되어 있어 BTCUSDT는 all_collected에 있고 is_attached=True여야 함
    all_syms = [item["symbol"] for item in res["all_collected"]]
    assert "BTCUSDT" in all_syms and "BTCUSDT-PERP" in all_syms
    assert len(res["unattached"]) > 0

    # 미등록 신규 크립토 타겟 기준: 상위 추천 자산에 크립토(바이낸스 선물/현물 등)가 unattached 최상위 배치
    res_clean = SymbolRelationsManager.get_available_vault_references("TEST_CRYPTO")
    unattached_clean = [item["symbol"] for item in res_clean["unattached"]]
    assert "BTCUSDT" in unattached_clean and "BTCUSDT-PERP" in unattached_clean
    top_clean = unattached_clean[:5]
    assert any("BTC" in s or "USDT" in s for s in top_clean)
