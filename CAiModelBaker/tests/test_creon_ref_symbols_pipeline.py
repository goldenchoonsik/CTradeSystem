# -*- coding: utf-8 -*-
"""
Test Suite: Creon Collector Reference Symbols Pipeline Unification
==================================================================
검증 목적:
1. download_all_krx 함수가 ref_symbols 인자를 받아 타겟 ETF/선물/지수뿐만 아니라
   부착된 참조 대장주(Reference Symbols)의 1분봉 및 틱 데이터를 일괄 순회 수집하는지 검증
2. ref_symbols가 생략되었을 때 symbol_relations.json 선언적 리소스에서 대장주 목록을 자동 fallback 탐색하는지 검증
3. SymbolRelationsManager에서 '069500' 타겟에 대한 국내 대장주 목록(000660, 005930, 379810, 449190 등) 추출 무결성 검증
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# 프로젝트 루트 경로 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from CAiModelBaker.core.creon_collector import download_all_krx
from shared.config.config_manager import get_symbol_info


def _krx_equity_refs_of(target: str) -> list:
    """관계에 등록된 참조 중 원장이 국내 주식/ETF로 분류한 심볼 목록 (수집기와 같은 판정 기준)"""
    out = []
    for r in SymbolRelationsManager.get_relations_for_target(target):
        sym = str(r.get("symbol", "")).strip().upper()
        clean = sym[1:] if sym.startswith("A") and len(sym) == 7 and sym[1:].isalnum() else sym
        if not clean or clean == target or clean in out:
            continue
        if str((get_symbol_info(clean) or {}).get("market_type", "")).upper() in ("KRX_STOCK", "KRX_ETF"):
            out.append(clean)
    return out
from shared.data_library.symbol_relations_manager import SymbolRelationsManager


class TestCreonRefSymbolsPipeline(unittest.TestCase):

    def setUp(self):
        # download_all_krx 는 끝에 실데이터 품질 감사(DataQualityAuditor.audit_symbol)를 부른다 — 이 테스트는 수집 대상 선별만 본다.
        # 실감사는 크레온 틱을 읽어 RSS ~3GB 라 메모리가 빠듯한 PC 에서 OOM 이 났다(2026-10-15). 감사 자체는 test_data_quality 가 검증한다.
        p = patch("shared.data_library.data_quality.DataQualityAuditor.audit_symbol", return_value={})
        p.start()
        self.addCleanup(p.stop)

    # 수집 주기는 실제 source_mappings.json 이 아니라 고정값으로 준다 — 운영 설정(예: 449190 틱 중단)이 바뀌어도 깨지지 않게
    @patch("CAiModelBaker.core.creon_collector._get_configured_sources", return_value=["TICK", "1M", "1D"])
    @patch("CAiModelBaker.core.creon_collector.download_daily_pair")
    @patch("CAiModelBaker.core.creon_collector.download_tick_data")
    @patch("CAiModelBaker.core.creon_collector.download_index_minute_data")
    @patch("CAiModelBaker.core.creon_collector.download_futures_minute_data")
    @patch("CAiModelBaker.core.creon_collector.download_minute_data")
    def test_download_all_krx_with_explicit_ref_symbols(self, mock_min, mock_fut, mock_idx, mock_tick, _daily, _srcs):
        """명시적으로 전달된 ref_symbols가 타겟과 함께 모두 수집되는지 검증"""
        ref_symbols = ["000660", "449190"]
        logs = []

        download_all_krx(
            target_symbol="A069500",
            futures_symbol="10100",
            index_symbol="U180",
            interval=1,
            count=1000,
            force_full=False,
            ref_symbols=ref_symbols,
            local_log=logs.append
        )

        # 1. 타겟 1분봉 호출 확인
        mock_min.assert_any_call(symbol="A069500", interval=1, count=1000, force_full=False, local_log=logs.append)
        # 2. 선물 1분봉 호출 확인
        mock_fut.assert_called_once_with(symbol="10100", interval=1, count=1000, force_full=False, local_log=logs.append)
        # 3. 지수 1분봉 호출 확인
        mock_idx.assert_called_once_with(symbol="U180", interval=1, count=1000, force_full=False, local_log=logs.append)
        # 4. 타겟 틱 호출 확인
        mock_tick.assert_any_call(symbol="A069500", count=1000, force_full=False, local_log=logs.append)

        # 5. 참조 대장주 1분봉 및 틱 호출 확인
        mock_min.assert_any_call(symbol="A000660", interval=1, count=1000, force_full=False, local_log=logs.append)
        mock_tick.assert_any_call(symbol="A000660", count=1000, force_full=False, local_log=logs.append)
        mock_min.assert_any_call(symbol="A449190", interval=1, count=1000, force_full=False, local_log=logs.append)
        mock_tick.assert_any_call(symbol="A449190", count=1000, force_full=False, local_log=logs.append)

        # 총 호출 횟수 검증:
        # download_minute_data: 타겟 1회 + 대장주 2회 = 3회
        self.assertEqual(mock_min.call_count, 3)
        # download_tick_data: 타겟 1회 + 대장주 2회 = 3회
        self.assertEqual(mock_tick.call_count, 3)

    @patch("CAiModelBaker.core.creon_collector.download_tick_data")
    @patch("CAiModelBaker.core.creon_collector.download_index_minute_data")
    @patch("CAiModelBaker.core.creon_collector.download_futures_minute_data")
    @patch("CAiModelBaker.core.creon_collector.download_minute_data")
    def test_download_all_krx_fallback_to_symbol_relations(self, mock_min, mock_fut, mock_idx, mock_tick):
        """ref_symbols=None 이면 symbol_relations 에 등록된 국내 주식/ETF 참조를 자동 로드하는지 검증.

        기대 심볼은 관계+원장에서 도출한다 — 참조 구성은 UI에서 바뀌는 데이터이므로 고정 목록(과거 '대장주'
        449190/379810 등)을 테스트에 박으면 구성이 변할 때마다 코드가 아니라 테스트가 깨진다.
        """
        expected_refs = _krx_equity_refs_of("069500")
        self.assertTrue(expected_refs, "069500 관계에 국내 주식/ETF 참조가 없어 검증할 대상이 없습니다.")
        logs = []

        download_all_krx(
            target_symbol="A069500",
            futures_symbol="10100",
            index_symbol="U180",
            interval=1,
            count=500,
            force_full=False,
            ref_symbols=None,
            local_log=logs.append
        )

        for ref in expected_refs:
            mock_min.assert_any_call(symbol=f"A{ref}", interval=1, count=500, force_full=False, local_log=logs.append)

        # 선물/지수/CFD 프록시(10100, U180, US_TECH_100 ...)는 별도 경로로 받으므로 참조 분봉 호출에 섞이면 안 된다
        called_syms = {c.kwargs.get("symbol") for c in mock_min.call_args_list}
        self.assertNotIn("A10100", called_syms)
        self.assertNotIn("AUS_TECH_100", called_syms)

    def test_symbol_relations_manager_krx_filter(self):
        """069500 의 참조 중 국내 주식/ETF만 크레온 수집 대상으로 골라지는지 검증 (판정 기준은 원장 market_type)"""
        relations = SymbolRelationsManager.get_relations_for_target("069500")
        self.assertTrue(relations, "069500 에 등록된 참조 관계가 없습니다.")

        krx_refs = _krx_equity_refs_of("069500")
        self.assertTrue(krx_refs, "국내 주식/ETF 참조가 하나도 없습니다.")

        # 원장 기준으로 국내 주식/ETF 가 아닌 참조는 제외되어야 한다 (선물/지수/해외선물/CFD 프록시/가상자산)
        for r in relations:
            sym = str(r.get("symbol", "")).strip().upper()
            market_type = str((get_symbol_info(sym) or {}).get("market_type", "")).upper()
            if market_type not in ("KRX_STOCK", "KRX_ETF"):
                self.assertNotIn(sym, krx_refs, f"{sym}({market_type})는 크레온 참조 목록에 들어가면 안 됩니다.")

        self.assertNotIn("069500", krx_refs, "타겟 자신은 참조 목록에서 제외되어야 합니다.")

    @patch("CAiModelBaker.core.creon_collector.download_daily_pair")
    @patch("CAiModelBaker.core.creon_collector.download_tick_data")
    @patch("CAiModelBaker.core.creon_collector.download_index_minute_data")
    @patch("CAiModelBaker.core.creon_collector.download_futures_minute_data")
    @patch("CAiModelBaker.core.creon_collector.download_minute_data")
    def test_download_all_krx_conditional_sources(self, mock_min, mock_fut, mock_idx, mock_tick, mock_daily):
        """UI 원천 체크박스(sources) 선택에 따라 1D, 1M, TICK 수집이 정확히 선별 실행/스킵되는지 검증"""
        ref_symbols = ["000660"]
        logs = []

        # 케이스 1: 1D만 선택된 경우 -> 1분봉, 틱, 선물, 지수 스킵
        download_all_krx(
            target_symbol="A069500",
            futures_symbol="10100",
            index_symbol="U180",
            ref_symbols=ref_symbols,
            sources=["1D"],
            local_log=logs.append
        )
        self.assertEqual(mock_daily.call_count, 2)  # 타겟(069500) + 참조 대장주(000660)
        mock_daily.assert_any_call(symbol="A069500", count=99999999, force_full=False, local_log=logs.append)
        mock_daily.assert_any_call(symbol="A000660", count=99999999, force_full=False, local_log=logs.append)
        mock_min.assert_not_called()
        mock_tick.assert_not_called()
        mock_fut.assert_not_called()
        mock_idx.assert_not_called()

        mock_daily.reset_mock()
        mock_min.reset_mock()
        mock_tick.reset_mock()
        mock_fut.reset_mock()
        mock_idx.reset_mock()

        # 케이스 2: TICK만 선택된 경우 -> 일봉, 분봉 스킵, TICK만 실행
        download_all_krx(
            target_symbol="A069500",
            futures_symbol="10100",
            index_symbol="U180",
            ref_symbols=ref_symbols,
            sources=["TICK"],
            local_log=logs.append
        )
        mock_daily.assert_not_called()
        mock_min.assert_not_called()
        self.assertEqual(mock_tick.call_count, 2)  # 타겟(069500) + 참조(000660)
        mock_tick.assert_any_call(symbol="A069500", count=99999999, force_full=False, local_log=logs.append)
        mock_tick.assert_any_call(symbol="A000660", count=99999999, force_full=False, local_log=logs.append)

        mock_daily.reset_mock()
        mock_tick.reset_mock()

        # 케이스 3: 1M + 1D 선택된 경우 -> TICK 스킵, 일봉과 분봉 실행
        download_all_krx(
            target_symbol="A069500",
            futures_symbol="10100",
            index_symbol="U180",
            ref_symbols=ref_symbols,
            sources=["1M", "1D"],
            local_log=logs.append
        )
        self.assertEqual(mock_daily.call_count, 2)  # 타겟 일봉 + 참조 일봉
        self.assertEqual(mock_min.call_count, 2)  # 타겟 1분봉 + 참조 1분봉
        mock_fut.assert_called_once()
        mock_idx.assert_called_once()
        mock_tick.assert_not_called()


if __name__ == "__main__":
    unittest.main()
