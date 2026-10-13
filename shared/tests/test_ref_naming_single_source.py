# -*- coding: utf-8 -*-
"""참조 종목 컬럼/피처 이름은 원장 심볼에서 기계적으로 유도한다 — 별칭(alias) 없음, 이름을 만드는 곳은 하나.

배경: 같은 종목이 타겟마다 다른 별칭(sk/hynix, 005930/samsung)으로 불려 피처 이름이 갈렸고, 별칭을 composer 와
브리지 두 곳에서 붙이다 컬럼이 36개 중복되었으며, 피처가 찾는 고정 컬럼명(nq_futures_close 등)이 Gold 스냅샷에
없어 후보 피처의 절반이 조용히 상수 0 이 되었다. 이 파일은 그 세 가지가 다시 생기지 않게 막는다.
"""
import os
import re

import numpy as np
import pandas as pd
import pytest

from shared.config import config_manager as cm
from shared.features.feature_health import find_dead_features
from shared.features.feature_registry import (
    DEFAULT_FEATURE_REGISTRY, REFERENCE_FEATURE_KINDS, ensure_reference_feature, parse_reference_feature_id,
    _compute_kospi_basis, _compute_kospi_basis_rate,
)
from shared.data_library.symbol_relations_manager import SymbolRelationsManager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 이름 규칙 ──────────────────────────────────────────────────────────────────
def test_ref_key_is_derived_from_the_symbol_only():
    assert cm.ref_key("005930") == "005930"
    assert cm.ref_key("A005930") == "005930"                 # 크레온 접두사는 이름에 영향을 주지 않는다
    assert cm.ref_key("US_TECH_100") == "us_tech_100"
    assert cm.ref_key("BTCUSDT-PERP") == "btcusdt_perp"      # 하이픈은 밑줄
    assert cm.ref_prefix("10100") == "ref_10100_"
    # 멱등: 이미 키인 값을 다시 넣어도 같다 (피처 id 에서 읽은 키로 재등록해도 이름이 변하지 않는다)
    for sym in ("000660", "US_500", "KRW-BTC", "ES"):
        assert cm.ref_key(cm.ref_key(sym)) == cm.ref_key(sym)


def test_reference_feature_ids_round_trip():
    for kind in REFERENCE_FEATURE_KINDS:
        assert parse_reference_feature_id(f"ref_us_tech_100_{kind}") == ("us_tech_100", kind)
    assert parse_reference_feature_id("target_rsi_curr") is None
    assert parse_reference_feature_id("ref_10100_close") is None      # 컬럼이지 피처 id 가 아니다


def test_symbol_from_ref_key_uses_only_registered_symbols():
    assert cm.symbol_from_ref_key("us_tech_100") == "US_TECH_100"
    assert cm.symbol_from_ref_key("000660") == "000660"
    assert cm.symbol_from_ref_key("hynix") is None and cm.symbol_from_ref_key("sk") is None     # 별칭은 없다


def test_find_ref_columns_filters_by_ledger_market_type():
    cols = ["ref_10100_close", "ref_u180_close", "ref_000660_close", "ref_005930_volume", "ref_nq_close", "target_close"]
    assert cm.find_ref_columns(cols, "close", ("KRX_FUTURES",)) == ["ref_10100_close"]
    assert cm.find_ref_columns(cols, "close", ("KRX_INDEX",)) == ["ref_u180_close"]
    assert cm.find_ref_columns(cols, "close", ("KRX_STOCK",)) == ["ref_000660_close"]     # 키 정렬순
    assert cm.find_ref_columns(cols, "close") == ["ref_000660_close", "ref_10100_close", "ref_nq_close", "ref_u180_close"]


# ── 별칭 폐지 ─────────────────────────────────────────────────────────────────
def test_no_alias_in_ledger_or_relations_files():
    ledger = cm.load_symbols_registry(include_catalog=True)
    assert [s for s, e in ledger.items() if "alias" in e] == []
    rels = SymbolRelationsManager.load_all_relations()
    offenders = [(t, r.get("symbol")) for t, v in rels.items() for r in v.get("references", []) if "alias" in r]
    assert offenders == []


def test_relations_manager_never_persists_a_retired_alias_field():
    data = {"069500": {"symbol": "069500", "references": [{"symbol": "000660", "alias": "sk", "role": "leading_stock"}]}}
    stripped = SymbolRelationsManager._strip_ledger_fields(data)
    assert stripped["069500"]["references"] == [{"symbol": "000660", "role": "leading_stock"}]


ALIAS_READ = re.compile(r"""\.get\(\s*["']alias["']|\[\s*["']alias["']\s*\]|\br_alias\b""")


def test_production_code_does_not_read_aliases():
    """shared/ 와 CAiModelBaker/ 의 운영 코드는 relations/원장의 alias 를 읽지 않는다.
    (CTrade 라이브의 참조 등록은 서빙 컬럼 계약 전환(3단계) 때 같은 규칙으로 옮긴다 — 아직 검사 대상이 아니다.)"""
    offenders = []
    for root in ("shared", "CAiModelBaker"):
        for dp, dn, fn in os.walk(os.path.join(REPO_ROOT, root)):
            dn[:] = [d for d in dn if d not in {"tests", "__pycache__", "deploy", "data", "legacy", "logs", "config"}]
            for name in fn:
                if not name.endswith(".py") or name.startswith("test_"):
                    continue
                path = os.path.join(dp, name)
                for n, line in enumerate(open(path, encoding="utf-8", errors="ignore"), 1):
                    if line.lstrip().startswith("#"):
                        continue
                    if ALIAS_READ.search(line):
                        offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{n}")
    assert offenders == [], f"별칭을 읽는 코드가 남아 있습니다: {offenders}"


# ── 결합 계획 ─────────────────────────────────────────────────────────────────
def test_join_plan_is_the_single_classification(monkeypatch):
    refs = [
        {"symbol": "000660", "role": "leading_stock"},
        {"symbol": "10100", "role": "kospi_futures"},
        {"symbol": "US_500", "role": "macro_futures"},
        {"symbol": "000660", "role": "leading_stock"},      # 중복은 한 번만
    ]
    monkeypatch.setattr(SymbolRelationsManager, "get_relations_for_target", classmethod(lambda cls, t: refs))
    assert SymbolRelationsManager.join_plan("069500") == {"refs": ["000660"], "series": ["10100", "US_500"]}


# ── 피처 해석: 등록 순서에 의존하지 않는다 ───────────────────────────────────────
def test_reference_features_resolve_from_the_id_alone():
    from shared.features.feature_engine import run_feature_engineering
    idx = pd.date_range("2026-04-15 09:00:00", periods=30, freq="10s")
    df = pd.DataFrame({
        "timestamp": idx, "target_close": np.linspace(100, 110, 30), "target_open": 100.0, "target_high": 111.0,
        "target_low": 99.0, "target_volume": 10.0, "ref_000660_close": np.linspace(200, 230, 30),
    })
    fid = "ref_000660_ret_1c"
    DEFAULT_FEATURE_REGISTRY.pop(fid, None)
    assert ensure_reference_feature(fid) and fid in DEFAULT_FEATURE_REGISTRY
    DEFAULT_FEATURE_REGISTRY.pop(fid, None)
    out = run_feature_engineering(df, "10s", feature_config={"enabled_features": [fid], "feature_params": {}})
    assert fid in out.columns and out[fid].abs().sum() > 0


# ── 죽은 피처 검사 (이름을 보지 않는다) ─────────────────────────────────────────
def test_find_dead_features_reports_constant_missing_and_the_missing_input():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "const": [5.0] * 3, "allnan": [np.nan] * 3, "ref_x_close": [1.0, 2.0, 3.0]})
    dead = find_dead_features(df, ["a", "const", "allnan", "missing", "ref_x_ret_1c", "ref_y_ret_1c"])
    assert "a" not in dead
    assert dead["ref_x_ret_1c"] == "컬럼 없음"           # 입력(ref_x_close)은 있으니 입력 탓이라는 힌트는 붙지 않는다
    assert "상수" in dead["const"] and "전부 결측" in dead["allnan"] and "컬럼 없음" in dead["missing"]
    assert "ref_y_close" in dead["ref_y_ret_1c"]          # 어떤 데이터를 붙여야 살아나는지 사유에 나온다


# ── 베이시스: 원장 유형으로 입력을 찾고, 없으면 지어내지 않는다 ─────────────────────
def test_basis_features_find_futures_and_index_by_ledger_type():
    df = pd.DataFrame({"target_close": [100.0] * 4, "ref_10100_close": [362.0, 363.0, 364.0, 365.0],
                       "ref_u180_close": [360.0, 360.0, 360.0, 360.0]})
    assert list(_compute_kospi_basis(df, {}, "10s")) == [2.0, 3.0, 4.0, 5.0]
    assert _compute_kospi_basis_rate(df, {}, "10s").iloc[0] == pytest.approx(2.0 / 360.0 * 100.0, rel=1e-6)


def test_basis_without_an_index_series_is_zero_not_garbage():
    """지수 시리즈가 없을 때 타겟(ETF) 가격을 현물로 쓰면 선물(지수 포인트)과 단위가 달라 베이시스가 무의미해진다."""
    df = pd.DataFrame({"target_close": [100000.0] * 3, "ref_10100_close": [362.0, 363.0, 364.0]})
    assert list(_compute_kospi_basis(df, {}, "10s")) == [0.0, 0.0, 0.0]


# ── 컴포저: 같은 종목은 한 번만 붙이고, 이름 충돌(중복 컬럼)은 즉시 실패 ────────────────────
def _composer(monkeypatch, target_extra=None):
    from CAiModelBaker.core.feature_composer import FeatureComposer

    idx = pd.date_range("2026-04-15 09:00:00", periods=5, freq="10s")

    def fake_load(self, symbol, interval, is_target=False):
        df = pd.DataFrame({"timestamp": idx, "close": np.arange(5.0), "target_close": np.arange(5.0)})
        return df.assign(**(target_extra or {})) if symbol == "069500" else df

    monkeypatch.setattr(FeatureComposer, "load_normalized", fake_load)
    return FeatureComposer(local_log=lambda m: None)


def test_composer_attaches_a_symbol_once_even_if_listed_as_ref_and_series(monkeypatch):
    composer = _composer(monkeypatch)
    out = composer.compose("069500", "10s", ref_symbols=["000660"], macro_symbols=["000660"],
                           align_to_common_dates=False, use_cache=False)
    assert "ref_000660_close" in out.columns and not out.columns.duplicated().any()
    assert not [c for c in out.columns if c.endswith(("_x", "_y"))]      # merge 접미사가 붙은 이름이 생기지 않는다


def test_composer_fails_fast_when_a_projection_collides_with_an_existing_column(monkeypatch):
    """이름을 만드는 곳이 둘이면 충돌이 생긴다 — 충돌은 조용히 넘어가지 않고 빌드를 실패시킨다."""
    composer = _composer(monkeypatch, target_extra={"binance_spot_close": np.arange(5.0)})
    with pytest.raises(ValueError, match="중복 컬럼"):
        composer.compose("069500", "10s", ref_symbols=["BTCUSDT"], align_to_common_dates=False, use_cache=False)
