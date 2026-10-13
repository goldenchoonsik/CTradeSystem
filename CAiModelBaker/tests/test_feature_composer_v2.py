# -*- coding: utf-8 -*-
"""
테스트: FeatureComposer v2 (MTF 결합, 교차 자산 합성, Gold 캐시 & 인벤토리 검증)
=============================================================================
1. test_compose_cross_asset_synthesis: 대장주/매크로 기반 교차 팩터(스프레드, Z-Score, 상관계수, 베이시스) 합성 검증
2. test_compose_mtf_join: 상위 타임프레임(1m) Lookahead-Free Point-in-Time Join 검증
3. test_compose_gold_cache_and_manifest: 파라미터 해시 기반 Gold Parquet 캐싱 및 사이드카 매니페스트 검증
4. test_list_gold_inventory: Gold Feature Store 인벤토리 스캔 검증
"""

import os
import shutil
import pytest
import numpy as np
import pandas as pd

from CAiModelBaker.core.feature_composer import FeatureComposer
from shared.data_library import paths as dpaths
from shared.data_library.manifest_manager import ManifestManager


@pytest.fixture
def composer():
    return FeatureComposer()


def test_compose_cross_asset_synthesis(composer):
    """타겟 069500(10s)에 삼성전자(005930) 및 선물/지수 결합 시 교차 팩터 자동 합성 검증"""
    df = composer.compose(
        target_symbol="069500",
        target_interval="10s",
        ref_symbols=["005930"],
        macro_symbols=["10100", "U180"],
        align_to_common_dates=True,
        synthesize_cross_features=True,
    )

    assert not df.empty
    # 교차 팩터 4종 존재 확인
    assert "cross_005930_spread" in df.columns
    assert "cross_005930_zscore" in df.columns
    assert "cross_005930_corr" in df.columns
    assert "macro_basis_ratio" in df.columns

    # 입력(참조 가격)이 있는 행에서만 값이 있고, 없는 행은 NaN — 0('괴리 없음')으로 위장하지 않는다 (2026-10-02)
    ref_ok = df["ref_005930_close"].notna() & df["close"].notna()
    assert df.loc[ref_ok, "cross_005930_spread"].notna().all()
    assert df.loc[~ref_ok, "cross_005930_spread"].isna().all()
    # z-score/상관계수는 분산이 0 인 구간(10초봉에서 30봉 동안 가격이 안 움직임)에서 정의되지 않는다 —
    # 그 칸은 NaN 이 맞다 (종전엔 0 으로 채워 069500 10s 의 약 46% 가 '괴리 없음'으로 위장됐다).
    spread = df["cross_005930_spread"].astype(float)
    sp_std = spread.rolling(30, min_periods=5).std()
    z_undefined = sp_std.isna() | (sp_std < 1e-6)   # 롤링 합의 부동소수 잔차(~1e-8)는 0 으로 본다
    assert df.loc[~z_undefined, "cross_005930_zscore"].notna().all(), "정의되는 칸에 z-score 가 없다"
    for col in ("cross_005930_zscore", "cross_005930_corr", "macro_basis_ratio"):
        assert not np.isinf(df[col]).any()


def test_compose_mtf_join(composer):
    """타겟 069500(10s)에 상위 1분봉(1m) 캔들 MTF Point-in-Time Join 검증"""
    df = composer.compose(
        target_symbol="069500",
        target_interval="10s",
        mtf_intervals=["1m"],
        align_to_common_dates=False,
    )

    assert not df.empty
    assert "mtf_1m_close" in df.columns
    assert "mtf_1m_vwap" in df.columns
    assert not df["mtf_1m_close"].isna().any()


def test_compose_gold_cache_and_manifest(composer):
    """use_cache=True 지정 시 Gold 캐시 생성 및 2차 호출 시 캐시 적중 검증"""
    test_target = "069500"
    test_intv = "10s"

    df1 = composer.compose(
        target_symbol=test_target,
        target_interval=test_intv,
        ref_symbols=["005930"],
        use_cache=True,
    )
    assert not df1.empty

    # 캐시 파일 위치 확인
    cache_key = composer._compute_cache_key(test_target, test_intv, ref_symbols=["005930"])
    gold_file = dpaths.features_path(test_target, "gold", f"{test_target}_{test_intv}_{cache_key}.parquet")
    assert os.path.exists(gold_file)

    # 1:1 사이드카 매니페스트 확인
    ok, msg = ManifestManager.verify_manifest(gold_file)
    assert ok is True

    # 2차 호출 시 캐시 적중 확인
    df2 = composer.compose(
        target_symbol=test_target,
        target_interval=test_intv,
        ref_symbols=["005930"],
        use_cache=True,
    )
    assert len(df1) == len(df2)

    # 임시 캐시 디렉토리 청소
    gold_dir = os.path.dirname(gold_file)
    if os.path.exists(gold_dir):
        shutil.rmtree(gold_dir)


def test_list_gold_inventory(composer):
    """list_gold_inventory 동작 검증"""
    inv = composer.list_gold_inventory()
    assert isinstance(inv, list)

