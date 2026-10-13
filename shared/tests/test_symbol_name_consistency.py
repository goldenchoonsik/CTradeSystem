import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.config.config_manager import load_symbols_registry
from shared.data_library.symbol_relations_manager import SymbolRelationsManager
from shared.data_library.source_mapping_manager import SourceMappingManager


def _canonical_names() -> dict:
    """종목 마스터 원장(symbols_registry.json)의 symbol -> name 맵.

    이 저장소에서 종목 표시 이름의 유일한 정답이다.
    """
    registry = load_symbols_registry(include_catalog=True)
    return {
        str(sym).strip().upper(): info["name"]
        for sym, info in registry.items()
        if isinstance(info, dict) and info.get("name")
    }


class TestSymbolNameConsistency(unittest.TestCase):
    """symbol -> name 이 symbols_registry.json(SSOT)과 다른 대장(symbol_relations.json,
    source_mappings.json)에서 서로 다르게 박제되어 있지 않은지 검증한다.

    이 테스트가 실패하면 Data Vault Feed Registry / 종목 검색 등 UI 여러 곳에 서로 다른
    이름이 노출되는 드리프트 버그가 재발한 것이다. 실제 재현 사례: 396500이
    source_mappings.json에서만 "KODEX 미국S&P500TR"로 잘못 박제되고, 원장/
    symbol_relations.json은 정확한 "TIGER 반도체TOP10"이었던 건.
    """

    def test_source_mappings_names_match_registry(self):
        canonical = _canonical_names()
        # source_mapping_manager의 자가치유(name self-healing) 로직을 실제로 태워서
        # 로드 직후 드리프트가 자동 교정되는지까지 함께 검증한다.
        mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
        mismatches = []
        for sym, info in mappings.items():
            if not isinstance(info, dict):
                continue
            expected = canonical.get(str(sym).strip().upper())
            actual = info.get("name")
            if expected and actual and expected != actual:
                mismatches.append((sym, actual, expected))
        self.assertEqual(
            mismatches, [],
            "source_mappings.json에 SSOT(symbols_registry.json)와 다른 "
            f"이름이 남아있습니다 (symbol, 현재값, 기대값): {mismatches}"
        )

    def test_symbol_relations_target_names_match_registry(self):
        canonical = _canonical_names()
        relations = SymbolRelationsManager.load_all_relations()
        mismatches = []
        for target_sym, info in relations.items():
            if not isinstance(info, dict):
                continue
            expected = canonical.get(str(target_sym).strip().upper())
            actual = info.get("name")
            if expected and actual and expected != actual:
                mismatches.append((target_sym, actual, expected))
        self.assertEqual(
            mismatches, [],
            "symbol_relations.json 타겟 이름이 SSOT와 다릅니다 "
            f"(symbol, 현재값, 기대값): {mismatches}"
        )


if __name__ == "__main__":
    unittest.main()
