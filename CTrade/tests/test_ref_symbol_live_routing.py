import pytest
# -*- coding: utf-8 -*-
"""
Unit Test: Reference Symbol Live Routing & Silent Subscribe
실시간 엔진(CTrade)에서 타겟 종목(ETF) 매매 시 대장주(Reference Symbol)가
Silent Subscribe로 자동 구독되고, 체결 틱이 타겟 CandleAggregator로 정상 라우팅되는지 검증합니다.
"""

import unittest
import copy
from datetime import datetime, timezone, timedelta
from main import load_trading_config
from core.engine import STradeEngine
from core.models import TickData
from core.tick_router import route_tick

KST = timezone(timedelta(hours=9))


class TestRefSymbolLiveRouting(unittest.TestCase):

    def setUp(self):
        base_config = load_trading_config()
        self.mock_config = copy.deepcopy(base_config)
        self.mock_config["CANDLE_INTERVAL_MINUTES"] = 1
        self.mock_config["SCALPING_TARGETS"] = [
            {
                "symbol": "396500",
                "exchange": "KIS",
                "active": True,
                "auto_trade": True,
                "strategy_name": "default_scalping",
                "reference_symbols": [
                    {"symbol": "000660", "alias": "hynix"},
                    {"symbol": "005930", "alias": "samsung"}
                ]
            }
        ]

    def test_silent_subscribe_and_aggregator_mapping(self):
        """참조 대장주가 stock_symbols에 자동 등록(Silent Subscribe)되고 어그리게이터에 매핑되는지 검증"""
        engine = STradeEngine(self.mock_config)

        # 1. stock_symbols에 타겟 및 참조 대장주가 모두 등록되었는지 확인
        self.assertIn("396500", engine.stock_symbols)
        self.assertIn("000660", engine.stock_symbols)
        self.assertIn("005930", engine.stock_symbols)

        # 2. silent_stock_symbols에 참조 대장주만 정확히 마킹되었는지 확인
        self.assertNotIn("396500", engine.silent_stock_symbols)
        self.assertIn("000660", engine.silent_stock_symbols)
        self.assertIn("005930", engine.silent_stock_symbols)

        # 3. 타겟 396500 어그리게이터에 참조 대장주 심볼-alias 매핑이 정확히 등록되었는지 확인
        agg = engine.candle_aggregators.get("396500")
        self.assertIsNotNone(agg)
        self.assertEqual(agg.ref_symbol_map.get("000660"), "hynix")
        self.assertEqual(agg.ref_symbol_map.get("005930"), "samsung")

        # 4. 참조 전용 대장주는 독립 어그리게이터를 생성하지 않아 자원을 절약하는지 확인
        self.assertNotIn("000660", engine.candle_aggregators)
        self.assertNotIn("005930", engine.candle_aggregators)

    def test_live_tick_routing_to_target_aggregator(self):
        """실시간 대장주 체결 틱이 타겟 CandleAggregator에 실시간 반영되어 캔들에 누적되는지 검증"""
        engine = STradeEngine(self.mock_config)
        agg = engine.candle_aggregators["396500"]

        t0 = datetime(2026, 9, 2, 9, 1, 10, tzinfo=KST)
        t1 = datetime(2026, 9, 2, 9, 1, 20, tzinfo=KST)
        t2 = datetime(2026, 9, 2, 9, 1, 30, tzinfo=KST)
        t_next = datetime(2026, 9, 2, 9, 2, 5, tzinfo=KST)

        # 1. 타겟 ETF(396500) 틱 라우팅
        tick_target = TickData(
            symbol="396500",
            exchange="KIS",
            price=35000.0,
            volume=50.0,
            side="BID",
            timestamp=t0.timestamp()
        )
        route_tick(tick_target, engine.candle_aggregators, engine.ai_strategies)

        # 2. SK하이닉스(000660) 체결 틱 라우팅
        tick_hynix = TickData(
            symbol="000660",
            exchange="KIS",
            price=205000.0,
            volume=120.0,
            side="BID",
            timestamp=t1.timestamp()
        )
        route_tick(tick_hynix, engine.candle_aggregators, engine.ai_strategies)

        # 3. 삼성전자(005930) 체결 틱 라우팅
        tick_samsung = TickData(
            symbol="005930",
            exchange="KIS",
            price=82000.0,
            volume=300.0,
            side="BID",
            timestamp=t2.timestamp()
        )
        route_tick(tick_samsung, engine.candle_aggregators, engine.ai_strategies)

        # 4. 다음 분봉 진입으로 직전 캔들 완성 유도
        tick_target_next = TickData(
            symbol="396500",
            exchange="KIS",
            price=35100.0,
            volume=20.0,
            side="BID",
            timestamp=t_next.timestamp()
        )
        route_tick(tick_target_next, engine.candle_aggregators, engine.ai_strategies)

        candles = agg.candles
        self.assertGreaterEqual(len(candles), 1)
        last_c = candles[0]

        # 5. 타겟 캔들에 대장주들의 close, volume, bid_vol이 정상 결합되었는지 검증
        self.assertEqual(last_c.get("target_close"), 35000.0)
        self.assertEqual(last_c.get("ref_hynix_close"), 205000.0)
        self.assertEqual(last_c.get("ref_hynix_volume"), 120.0)
        self.assertEqual(last_c.get("ref_hynix_bid_vol"), 120.0)

        self.assertEqual(last_c.get("ref_samsung_close"), 82000.0)
        self.assertEqual(last_c.get("ref_samsung_volume"), 300.0)
        self.assertEqual(last_c.get("ref_samsung_bid_vol"), 300.0)

    @pytest.mark.skip(reason="정리된 구 모델/변경된 shared 설정 기준 테스트 - 현행 설정에 맞게 재작성 필요")
    def test_deploy_snapshot_ref_symbol_auto_load(self):
        """배포 세션의 config_snapshot.json에서 reference_symbols를 안전하게 자동 탐색 및 로드하는지 검증"""
        deploy_cfg = copy.deepcopy(self.mock_config)
        deploy_cfg["SCALPING_TARGETS"] = [
            {
                "symbol": "069500",
                "exchange": "KIS",
                "active": True,
                "auto_trade": True,
                "strategy_name": "ai_baker",
                "deploy_base": "069500_1m",
                "deploy_session": "KOSPI200_M1_TEST1",
                "reference_symbols": []  # 비워두고 config_snapshot.json에서 로드되도록 유도
            }
        ]
        engine = STradeEngine(deploy_cfg)
        # KOSPI200_M1_TEST1의 config_snapshot.json에 정의된 000660(hynix)이 자동 등록되었는지 검증
        self.assertIn("069500", engine.stock_symbols)
        self.assertIn("000660", engine.stock_symbols)
        self.assertIn("000660", engine.silent_stock_symbols)

        agg = engine.candle_aggregators.get("069500")
        self.assertIsNotNone(agg)
        self.assertIn("000660", agg.ref_symbol_map)


if __name__ == "__main__":
    unittest.main()
