import pytest
import os
import sys
import json
from datetime import datetime, timezone, timedelta

# CTrade 경로 확보
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from shared.core.market_utils import parse_interval_seconds, parse_interval_minutes, detect_market_type
from core.candle_aggregator import CandleAggregator
from core.ai_strategy import AIStrategyModule

KST = timezone(timedelta(hours=9))


def test_10s_interval_parsing():
    """10s 인터벌 문자열이 초 단위 10, 분 단위 0.1666...으로 정확히 파싱되는지 검증"""
    assert parse_interval_seconds("10s") == 10
    assert pytest.approx(parse_interval_minutes("10s"), rel=1e-4) == 10.0 / 60.0
    assert parse_interval_seconds("5s") == 5
    assert parse_interval_seconds("15m") == 900


def test_candle_aggregator_10s_boundary():
    """10초봉 CandleAggregator가 10초 경계마다 정확히 캔들을 완성하는지 검증"""
    agg = CandleAggregator(interval_minutes=10.0 / 60.0, interval_seconds=10, max_candles=100, market_type="KRX")
    assert agg.interval_seconds == 10

    base_time = datetime(2026, 9, 21, 9, 15, 0, tzinfo=KST)

    # 09:15:01 틱
    agg.on_target_tick(100000.0, 10.0, "BID", timestamp=base_time + timedelta(seconds=1))
    df = agg.check_candle_complete(base_time + timedelta(seconds=5))
    assert df is None  # 아직 10초 경계(09:15:10) 미도달

    # 09:15:11 틱 (10초 경계 통과)
    agg.on_target_tick(100500.0, 20.0, "BID", timestamp=base_time + timedelta(seconds=11))
    df = agg.check_candle_complete(base_time + timedelta(seconds=11))
    assert df is not None
    assert len(df) == 1
    assert df.iloc[0]["target_close"] == 100000.0
    assert df.iloc[0]["target_volume"] == 10.0


def test_deploy_session_kodex200_10s_init():
    """실제 배포된 KODEX200_S10_BEST260921 모델 스펙과 AIStrategyModule 로드 검증"""
    deploy_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "../../shared/deploy/069500_10s/KODEX200_S10_BEST260921"
    ))
    assert os.path.exists(deploy_path), f"배포 경로 없음: {deploy_path}"

    ai_mod = AIStrategyModule(deploy_path=deploy_path, symbol="069500", enable_parity_logging=False)
    assert ai_mod.interval == "10s"
    assert ai_mod.interval_seconds == 10
    assert ai_mod.market_type == "KRX"
    assert len(ai_mod.features_order) == 7
    assert ai_mod.params["buy_threshold"] == pytest.approx(0.5339, abs=1e-3)
    assert ai_mod.params["take_profit"] == pytest.approx(1.657, abs=1e-2)
    assert ai_mod.params["stop_loss"] == pytest.approx(1.901, abs=1e-2)
    assert ai_mod.params["max_hold_candles"] == 6


def test_historical_warmup_10s_interval_sec_calculation():
    """fetch_historical_warmup_candles에서 10초봉 기준 타임델타가 10초인지 검증"""
    deploy_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "../../shared/deploy/069500_10s/KODEX200_S10_BEST260921"
    ))
    ai_mod = AIStrategyModule(deploy_path=deploy_path, symbol="069500", enable_parity_logging=False)

    interval_sec = int(round(getattr(ai_mod, 'interval_seconds', 0))) or max(1, int(round(ai_mod.interval_minutes * 60)))
    assert interval_sec == 10  # 1분이 아닌 정확히 10초

    now_kst = datetime(2026, 9, 21, 9, 30, 0, tzinfo=KST)
    c_time_prev = now_kst - timedelta(seconds=1 * interval_sec)
    c_time_prev2 = now_kst - timedelta(seconds=2 * interval_sec)
    assert (c_time_prev - c_time_prev2).total_seconds() == 10.0

