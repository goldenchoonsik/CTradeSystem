# -*- coding: utf-8 -*-
"""실전 수집기의 결측 계약 (2026-10-02) — 학습 데이터(Gold)와 같은 규칙으로 '모름'을 판정한다.

종전 실전 수집기는 수신 전·끊김 구간을 거짓 관측으로 채웠다: 환율 1380 고정, KOSPI 선물·지수 ← 타겟 ETF 가격,
코인베이스 ← 바이낸스 가격, OI/펀딩비 0 후 무기한 유지, 빈 봉 taker 비율 복제. 학습 데이터에는 이런 값이 없으므로
모델이 학습 때와 다른 입력을 받는다 (train/serve skew).
"""
import math
from datetime import datetime, timezone, timedelta

from core.candle_aggregator import CandleAggregator

KST = timezone(timedelta(hours=9))


def _flush_one(agg, t_tick, t_close):
    agg.on_target_tick(35000.0, 10.0, "BID", t_tick)
    df = agg.check_candle_complete(t_close)
    assert df is not None
    return df.iloc[-1]


def test_unobserved_external_fields_are_nan_not_placeholders():
    agg = CandleAggregator(interval_minutes=1.0, market_type="KRX", symbol="069500")
    c = _flush_one(agg, datetime(2026, 9, 7, 9, 0, 10, tzinfo=KST), datetime(2026, 9, 7, 9, 1, 0, tzinfo=KST))
    for col in ("usd_krw_fx", "kospi_futures_close", "futures_close", "spot_close", "nq_futures_close",
                "binance_spot_close", "binance_futures_oi", "binance_funding_rate", "coinbase_spot_close"):
        assert math.isnan(c[col]), f"{col} = {c[col]} — 관측 전인데 값이 있다"
    assert math.isnan(c["binance_spot_volume"]), "원천이 없는데 거래량 0 은 '체결 없음'이라는 거짓 관측이다"


def test_spot_index_is_never_the_target_price():
    agg = CandleAggregator(interval_minutes=1.0, market_type="KRX", symbol="069500")
    agg.on_futures_tick("101V3000", 380.0, datetime(2026, 9, 7, 9, 0, 5, tzinfo=KST))
    c = _flush_one(agg, datetime(2026, 9, 7, 9, 0, 10, tzinfo=KST), datetime(2026, 9, 7, 9, 1, 0, tzinfo=KST))
    assert c["kospi_futures_close"] == 380.0 and c["futures_close"] == 380.0
    assert math.isnan(c["spot_close"]), "ETF 가격은 KOSPI200 지수가 아니다 — 베이시스가 가짜가 된다"


def test_binance_empty_bar_flow_is_zero_but_ratio_is_undefined():
    agg = CandleAggregator(interval_minutes=1.0, symbol="KRW-BTC")
    t0 = datetime(2026, 9, 7, 10, 0, 5, tzinfo=KST)
    agg.on_binance_tick(80000.0, 1.0, False, t0)
    agg.check_candle_complete(datetime(2026, 9, 7, 10, 1, 0, tzinfo=KST))
    df = agg.check_candle_complete(datetime(2026, 9, 7, 10, 2, 0, tzinfo=KST))   # 체결 없는 봉
    c = df.iloc[-1]
    assert c["binance_spot_close"] == 80000.0          # 상태값: 마지막 관측
    assert c["binance_spot_volume"] == 0.0             # 흐름값: 원천이 살아 있으니 '체결 없음'
    assert math.isnan(c["binance_spot_taker_ratio"])   # 봉 내부 비율: 체결이 없으면 정의되지 않는다


def test_state_value_expires_after_stale_cap():
    agg = CandleAggregator(interval_minutes=1.0, symbol="KRW-BTC")
    agg.on_binance_futures_oi(95000.0, 1e9, datetime(2026, 9, 7, 10, 0, 5, tzinfo=KST))
    df = agg.check_candle_complete(datetime(2026, 9, 7, 10, 1, 0, tzinfo=KST))
    assert df.iloc[-1]["binance_futures_oi"] == 95000.0
    agg.on_binance_tick(80000.0, 1.0, False, datetime(2026, 9, 8, 11, 0, 5, tzinfo=KST))   # 1일(CRYPTO 상한) 경과
    df = agg.check_candle_complete(datetime(2026, 9, 8, 11, 1, 0, tzinfo=KST))
    assert math.isnan(df.iloc[-1]["binance_futures_oi"]), "상한을 넘은 미결제약정이 무기한 유지됐다"


def test_reference_before_first_tick_is_nan():
    agg = CandleAggregator(interval_minutes=1.0, market_type="KRX", symbol="069500")
    agg.register_ref_symbol("000660", "000660")
    c = _flush_one(agg, datetime(2026, 9, 7, 9, 0, 10, tzinfo=KST), datetime(2026, 9, 7, 9, 1, 0, tzinfo=KST))
    assert math.isnan(c["ref_000660_close"]) and math.isnan(c["ref_000660_volume"])
