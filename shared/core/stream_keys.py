# -*- coding: utf-8 -*-
"""CTrade 실시간 스트림 키 규약 — 정의는 이 파일 한 곳뿐이다.

CTrade 의 실시간 상태(shared_state / data_manager.store)는 심볼 문자열 하나로만 키가 잡히고
거래소 차원이 없다. 그런데 바이낸스는 현물과 무기한 선물의 티커가 둘 다 `BTCUSDT` 라서, 같은 키 공간에서
시장을 구분하려고 접미사를 붙인다:

    BTCUSDT         현물         (바이낸스/비트겟 — 어느 쪽인지는 tick.exchange 필드로 구분)
    BTCUSDT_PERP    바이낸스 USDT-M 무기한 선물
    BTCUSDT_BPERP   비트겟   USDT-M 무기한 선물

종목 원장(symbols_registry.json)은 같은 바이낸스 선물 리스팅을 `BTCUSDT-PERP` 로 부른다
(거래소는 `exchange` 필드, 거래소 API 티커는 `binance_symbol` 필드). 두 표기는 같은 상품이며,
서브시스템이 달라 인코딩만 다르다: 원장/데이터 파일/피처 = '-PERP', CTrade 실시간 스트림 = '_PERP'.
이 모듈이 두 표기와 접미사 규칙을 유일하게 정의하고 서로 변환한다 (f-string/endswith 로 흩뿌리지 말 것).
"""

QUOTE = "USDT"
BINANCE_FUTURES_SUFFIX = "_PERP"
BITGET_FUTURES_SUFFIX = "_BPERP"
LEDGER_PERP_SUFFIX = "-PERP"

# 설정(GLOBAL_BINANCE_ALWAYS_ON)이 비어 있어도 항상 구독하는 시장 전체 선행지표: BTC 현물 + BTC 선물
ALWAYS_ON_BASES = ("BTC",)
# USDC volume 피처 계산용 현물 페어
USDC_VOLUME_PAIR = "BTCUSDC"
# 실시간 원/달러 환율(usdt_krw)과 김치프리미엄 계산에 쓰는 업비트 KRW-USDT 마켓 — 항상 구독한다
FX_RATE_SYMBOL = "KRW-USDT"


def spot_key(base: str) -> str:
    return f"{base}{QUOTE}"


def binance_futures_key(base: str) -> str:
    return f"{base}{QUOTE}{BINANCE_FUTURES_SUFFIX}"


def bitget_futures_key(base: str) -> str:
    return f"{base}{QUOTE}{BITGET_FUTURES_SUFFIX}"


def is_binance_futures_key(key: str) -> bool:
    return str(key).endswith(BINANCE_FUTURES_SUFFIX)


def is_bitget_futures_key(key: str) -> bool:
    return str(key).endswith(BITGET_FUTURES_SUFFIX)


def exchange_symbol(key: str) -> str:
    """스트림 키 -> 거래소 API 가 쓰는 티커 (접미사 제거). 예: ETHUSDT_PERP -> ETHUSDT"""
    key = str(key)
    for suffix in (BINANCE_FUTURES_SUFFIX, BITGET_FUTURES_SUFFIX):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def default_always_on() -> list:
    """설정에 GLOBAL_BINANCE_ALWAYS_ON 이 없을 때의 기본 상시 구독 목록"""
    keys = []
    for base in ALWAYS_ON_BASES:
        keys += [binance_futures_key(base), spot_key(base)]
    return keys


def to_ledger_key(stream_key: str):
    """CTrade 스트림 키 -> 원장 리스팅 키. 바이낸스 선물만 원장 리스팅이 있다 (BTCUSDT_PERP -> BTCUSDT-PERP).
    현물 키는 그대로 돌려주고, 원장에 대응 리스팅이 없는 비트겟 선물은 None."""
    stream_key = str(stream_key)
    if is_bitget_futures_key(stream_key):
        return None
    if is_binance_futures_key(stream_key):
        return exchange_symbol(stream_key) + LEDGER_PERP_SUFFIX
    return stream_key


def from_ledger_key(ledger_key: str) -> str:
    """원장 리스팅 키 -> CTrade 스트림 키 (BTCUSDT-PERP -> BTCUSDT_PERP)"""
    ledger_key = str(ledger_key)
    if ledger_key.endswith(LEDGER_PERP_SUFFIX):
        return ledger_key[: -len(LEDGER_PERP_SUFFIX)] + BINANCE_FUTURES_SUFFIX
    return ledger_key
