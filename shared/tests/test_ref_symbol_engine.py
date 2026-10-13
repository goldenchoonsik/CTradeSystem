import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from shared.features.feature_registry import (
    register_reference_features,
    DEFAULT_FEATURE_REGISTRY,
    _compute_ref_ret_1c,
    _compute_ref_ret_5c,
    _compute_ref_spread_vs_target,
    _compute_ref_intensity
)
from shared.config.config_manager import ref_key
from CTrade.core.candle_aggregator import CandleAggregator

KST = timezone(timedelta(hours=9))


def test_register_reference_features():
    """참조 종목 등록 시 4대 제네릭 선행 지표가 레지스트리에 정상 등록되는지 검증"""
    alias = "hynix"
    registered_ids = register_reference_features(alias)
    
    assert len(registered_ids) == 4
    expected_ids = [
        "ref_hynix_ret_1c",
        "ref_hynix_ret_5c",
        "ref_hynix_spread_vs_target",
        "ref_hynix_intensity"
    ]
    for fid in expected_ids:
        assert fid in registered_ids
        assert fid in DEFAULT_FEATURE_REGISTRY
        item = DEFAULT_FEATURE_REGISTRY[fid]
        assert item["category"] == "reference"
        assert callable(item["compute"])


def test_register_reference_features_display_names():
    """종목코드(379810, 449190) 및 별칭(samsung, hynix) 등록 시 정식 종목명이 지표명에 들어가는지 검증"""
    reg = {}
    # 1. 379810
    register_reference_features("379810", reg)
    assert reg["ref_379810_ret_1c"]["name"] == "[KODEX 미국나스닥100TR] 1봉 수익률 (속도)"
    assert "KODEX 미국나스닥100TR" in reg["ref_379810_ret_1c"]["description"]

    # 2. 449190
    register_reference_features("449190", reg)
    assert reg["ref_449190_ret_1c"]["name"] == "[KODEX 미국나스닥100(H)] 1봉 수익률 (속도)"
    assert "KODEX 미국나스닥100(H)" in reg["ref_449190_ret_1c"]["description"]

    # 3~6. 별칭 없이 원장 심볼만으로 등록 — 컬럼/피처 id 는 심볼에서 유도한 키(ref_key)를 쓴다
    for sym, ledger_name in (("005930", "삼성전자"), ("000660", "SK하이닉스"),
                             ("ES", "미국 S&P500 선물"), ("NQ", "미국 나스닥100 선물")):
        register_reference_features(sym, reg)
        rid = f"ref_{ref_key(sym)}_ret_1c"
        assert reg[rid]["name"] == f"[{ledger_name}] 1봉 수익률 (속도)"
        assert ledger_name in reg[rid]["description"]


def test_reference_display_names_come_from_the_ledger_not_code():
    """코인 참조 종목의 표시명은 원장 이름과 항상 같아야 하고, 코드에 이름/별칭 사전을 두지 않는다."""
    from shared.config.config_manager import get_symbol_name

    reg = {}
    for sym in ("KRW-BTC", "KRW-XRP", "KRW-USDT"):
        ledger_name = get_symbol_name(sym)
        assert ledger_name, f"{sym} 가 원장에 없음"
        register_reference_features(sym, reg)
        assert reg[f"ref_{ref_key(sym)}_ret_1c"]["name"] == f"[{ledger_name}] 1봉 수익률 (속도)"

    import inspect
    import shared.features.feature_registry as fr
    assert "ALIAS_MAP" not in inspect.getsource(fr), "종목 표시명 별칭 사전(ALIAS_MAP) 하드코딩이 다시 생겼습니다 — 원장을 사용하세요"


def test_ref_generic_feature_computations():
    """4대 제네릭 선행 피처의 수학적 연산 및 **결측을 결측으로 남기는지** 검증.

    [T-6] 종전에는 계산 불가 구간을 0.0 으로 채웠다. 그러면 "참조 종목이 전혀 안 움직였다"는
    거짓 관측이 되어 모델이 그대로 학습한다. 특히 spread_vs_target 은 ref_ret=0 이 되는 순간
    `-(타겟 수익률)` 로 퇴화해, 죽은 구간에서도 값이 살아 있는 것처럼 보인다.
    LightGBM 은 NaN 을 결측으로 다루므로, 모르는 구간은 NaN 으로 남기는 것이 맞다.
    """
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01 09:00:00", periods=10, freq="1min", tz="Asia/Seoul"),
        "target_close": [100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0, 108.0, 107.0, 110.0],
        "ref_hynix_close": [200.0, 206.0, 204.0, 210.0, 212.0, 210.0, 216.0, 220.0, 218.0, 225.0],
        "ref_hynix_volume": [100, 200, 150, 300, 250, 180, 220, 310, 190, 400],
        "ref_hynix_bid_vol": [70, 140, 90, 240, 150, 100, 180, 250, 110, 320]
    })

    # 1. 1봉 수익률 (속도)
    ret_1c = _compute_ref_ret_1c(df, {}, "1m", col_close="ref_hynix_close")
    assert len(ret_1c) == 10
    # 2번째 봉: (206 - 200) / 200 = 0.03
    assert pytest.approx(ret_1c.iloc[1], 1e-5) == 0.03
    assert pd.isna(ret_1c.iloc[0])  # 첫 봉은 직전 값이 없으므로 '모름'(NaN)

    # 2. 5봉 추세 수익률
    ret_5c = _compute_ref_ret_5c(df, {"candles": 5}, "1m", col_close="ref_hynix_close")
    # 6번째 봉(idx 5): (210 - 200) / 200 = 0.05
    assert pytest.approx(ret_5c.iloc[5], 1e-5) == 0.05

    # 3. 괴리율(스프레드) = 하이닉스 수익률 - 타겟 ETF 수익률
    spread = _compute_ref_spread_vs_target(df, {}, "1m", col_close="ref_hynix_close")
    # 2번째 봉: 하이닉스 0.03 - 타겟 (102-100)/100 (0.02) = +0.01 (하이닉스 선행 급등)
    assert pytest.approx(spread.iloc[1], 1e-5) == 0.01

    # 4. 체결강도 = bid_vol / vol
    intensity = _compute_ref_intensity(df, {}, "1m", col_bid_vol="ref_hynix_bid_vol", col_vol="ref_hynix_volume")
    # 1번째 봉: 70 / 100 = 0.70
    assert pytest.approx(intensity.iloc[0], 1e-5) == 0.70

    # 5. 참조 컬럼이 아예 없으면 '모름'(NaN) 이어야 한다.
    #    0.0 / 0.5 같은 그럴듯한 상수로 채우면 데이터가 붙지 않은 피처가 살아 있는 것처럼 보이고,
    #    죽은 피처 검사(find_dead_features)도 통과해 버린다.
    empty_ret = _compute_ref_ret_1c(df, {}, "1m", col_close="non_existent_column")
    assert empty_ret.isna().all()
    empty_spread = _compute_ref_spread_vs_target(df, {}, "1m", col_close="non_existent_column")
    assert empty_spread.isna().all()
    empty_intensity = _compute_ref_intensity(df, {}, "1m", col_bid_vol="none", col_vol="none")
    assert empty_intensity.isna().all()

    # 6. 참조 가격이 비어 있는 구간은 파생 피처도 비어 있어야 한다 (0 으로 위장 금지)
    gap = df.copy()
    gap.loc[:4, "ref_hynix_close"] = np.nan
    gap_spread = _compute_ref_spread_vs_target(gap, {}, "1m", col_close="ref_hynix_close")
    assert gap_spread.iloc[:5].isna().all(), "참조가 없는 구간이 '타겟 반전 지표'로 둔갑했다"


def test_candle_aggregator_ref_tick_flow():
    """CandleAggregator의 참조 대장주 틱 수신 및 ffill 완성 캔들 병합 검증"""
    aggregator = CandleAggregator(interval_seconds=60)
    
    t0 = datetime(2026, 1, 1, 9, 0, 10, tzinfo=KST)
    t1 = datetime(2026, 1, 1, 9, 0, 30, tzinfo=KST)
    t2 = datetime(2026, 1, 1, 9, 1, 5, tzinfo=KST)  # 다음 캔들로 넘어감

    # 1. 타겟 틱 및 하이닉스 참조 틱 수신
    aggregator.on_target_tick(100.0, 10, timestamp=t0)
    aggregator.on_ref_tick("hynix", 200.0, 50, is_buyer=True, timestamp=t0)

    aggregator.on_target_tick(102.0, 20, timestamp=t1)
    aggregator.on_ref_tick("hynix", 205.0, 30, is_buyer=True, timestamp=t1)

    # 2. 다음 캔들 틱으로 이전 캔들 마감 트리거
    aggregator.on_target_tick(103.0, 15, timestamp=t2)

    completed = aggregator.candles
    assert len(completed) >= 1
    candle_0 = completed[0]

    # 첫 번째 캔들에 하이닉스 시세/거래량/체결량이 정확히 누적되었는지 확인
    assert candle_0["target_close"] == 102.0
    assert candle_0["ref_hynix_close"] == 205.0
    assert candle_0["ref_hynix_volume"] == 80.0  # 50 + 30
    assert candle_0["ref_hynix_bid_vol"] == 80.0

    # 3. 2번째 캔들 마감 시 틱 누락 시의 ffill 검증
    t3 = datetime(2026, 1, 1, 9, 2, 10, tzinfo=KST)
    aggregator.on_target_tick(104.0, 10, timestamp=t3)

    assert len(aggregator.candles) >= 2
    candle_1 = aggregator.candles[1]
    
    # 하이닉스 틱이 없었으므로 직전 종가(205.0)로 ffill되고 volume은 0.0이어야 함
    assert candle_1["ref_hynix_close"] == 205.0
    assert candle_1["ref_hynix_volume"] == 0.0
    assert candle_1["ref_hynix_bid_vol"] == 0.0
