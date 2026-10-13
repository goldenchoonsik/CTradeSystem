# -*- coding: utf-8 -*-
"""CTrade 스트림 키 규약(shared/stream_keys.py)이 기존에 흩어져 있던 f-string 규칙과 동일한지 고정한다."""
import os

from shared.core import stream_keys as sk

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_keys_match_the_legacy_inline_formulas():
    for base in ("BTC", "ETH", "SUI", "PROS"):
        assert sk.spot_key(base) == f"{base}USDT"
        assert sk.binance_futures_key(base) == f"{base}USDT_PERP"
        assert sk.bitget_futures_key(base) == f"{base}USDT_BPERP"


def test_predicates_and_exchange_symbol():
    assert sk.is_binance_futures_key("ETHUSDT_PERP") and not sk.is_binance_futures_key("ETHUSDT")
    assert sk.is_bitget_futures_key("PROSUSDT_BPERP") and not sk.is_bitget_futures_key("PROSUSDT_PERP")
    assert not sk.is_binance_futures_key("ETHUSDT_BPERP")  # _BPERP 는 _PERP 로 끝나지 않는다
    assert sk.exchange_symbol("ETHUSDT_PERP") == "ETHUSDT"
    assert sk.exchange_symbol("PROSUSDT_BPERP") == "PROSUSDT"
    assert sk.exchange_symbol("BTCUSDT") == "BTCUSDT"


def test_ledger_key_conversion_roundtrip():
    assert sk.to_ledger_key("BTCUSDT_PERP") == "BTCUSDT-PERP"
    assert sk.from_ledger_key("BTCUSDT-PERP") == "BTCUSDT_PERP"
    assert sk.to_ledger_key("BTCUSDT") == "BTCUSDT" and sk.from_ledger_key("BTCUSDT") == "BTCUSDT"
    assert sk.to_ledger_key("PROSUSDT_BPERP") is None  # 비트겟 선물은 원장 리스팅이 없다


def test_always_on_default_matches_previous_hardcode():
    assert sk.default_always_on() == ["BTCUSDT_PERP", "BTCUSDT"]


def test_ledger_perp_listings_map_to_binance_stream_keys():
    """원장의 바이낸스 선물 리스팅(-PERP)은 binance_symbol + '_PERP' 스트림 키와 정확히 대응해야 한다."""
    import json
    with open(os.path.join(REPO_ROOT, "shared", "config", "symbols_registry.json"), encoding="utf-8") as f:
        ledger = json.load(f)
    e = ledger["BTCUSDT-PERP"]
    assert e["exchange"] == "BINANCE" and e["market_type"] == "CRYPTO_FUTURES"
    assert sk.from_ledger_key("BTCUSDT-PERP") == sk.binance_futures_key(e["binance_symbol"].replace("USDT", ""))
