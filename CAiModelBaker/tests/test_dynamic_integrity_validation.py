"""
CAiModelBaker/tests/test_dynamic_integrity_validation.py
참조 대장주(SK하이닉스, 삼성전자 등) 및 글로벌 매크로 선물의 동적 무결성 검증 단위 테스트
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from CAiModelBaker.core.data_manager import validate_downloaded_data
from shared.data_library.symbol_relations_manager import SymbolRelationsManager
from shared.config.config_manager import get_symbol_info, get_symbol_name


class TestDynamicIntegrityValidation:
    """무결성 검증 파이프라인의 동적 참조 종목 연동 테스트"""

    def test_kodex200_dynamic_reference_symbols_in_validation(self):
        """KODEX 200(069500) 검증 시 symbol_relations 에 등록된 참조가 items 에 자동 포함되는지 확인.

        기대 항목은 관계+원장에서 도출한다 — 참조 구성은 데이터라서 고정 심볼(과거 NQ/ES 직접 참조)을 박으면
        구성이 CFD 프록시(US_TECH_100/US_500)로 바뀐 순간 코드가 아니라 테스트가 깨진다.
        """
        config = {
            "SYMBOL_UPBIT": "069500",
            "INTERVAL": "1m",
            "ARCHIVE_START_YM": "2024-07",
            "ARCHIVE_END_YM": "2024-09"
        }
        res = validate_downloaded_data(config)
        items = res.get("items", {})

        # 1. 기본 타겟 캔들 및 선물/지수 포함 검증
        assert "creon_candle" in items
        assert "creon_futures" in items
        assert "creon_spot" in items

        relations = SymbolRelationsManager.get_relations_for_target("069500")
        assert relations, "069500 에 등록된 참조 관계가 없습니다."

        # 2. 국내 주식/ETF 참조 캔들 카드가 원장 이름과 함께 등록되는지 (판정 기준은 원장 market_type)
        equity_refs = [
            str(r.get("symbol", "")).strip().upper() for r in relations
            if str((get_symbol_info(str(r.get("symbol", "")).strip()) or {}).get("market_type", "")).upper()
            in ("KRX_STOCK", "KRX_ETF")
        ]
        assert equity_refs, "국내 주식/ETF 참조가 하나도 없습니다."
        for sym in equity_refs:
            key = f"ref_stock_{sym}"
            assert key in items, f"참조 종목 카드 {key} 가 검증 항목에 없습니다."
            assert get_symbol_name(sym) in items[key]["name"], f"{key} 이름이 원장 이름과 다릅니다: {items[key]['name']}"
            assert items[key]["file_exists"] is True
            assert items[key]["count"] > 0

        # 3. 글로벌 매크로 선물/프록시 참조 카드 동적 로드 검증
        macro_refs = [str(r.get("symbol", "")).strip().upper() for r in relations
                      if str(r.get("role", "")).lower() == "macro_futures"]
        assert macro_refs, "macro_futures 역할의 참조가 하나도 없습니다."
        for sym in macro_refs:
            assert f"cme_{sym.lower()}_1m" in items, f"매크로 선물 카드 cme_{sym.lower()}_1m 가 없습니다."

    def test_performance_under_ten_seconds(self):
        """대용량 틱 및 캔들 CSV 날짜 추출이 고속 캐시 및 라인 스캐너를 통해 10초 이내에 완료되는지 검증"""
        import time
        config = {
            "SYMBOL_UPBIT": "069500",
            "INTERVAL": "1m",
            "ARCHIVE_START_YM": "2024-07",
            "ARCHIVE_END_YM": "2024-09"
        }
        t0 = time.time()
        res = validate_downloaded_data(config)
        elapsed = time.time() - t0

        assert elapsed < 10.0, f"무결성 검증 시간이 {elapsed:.2f}초로 10초를 초과했습니다."
        assert len(res["items"]) >= 10
