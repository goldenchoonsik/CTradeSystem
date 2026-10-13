"""
시장 정책 및 Lookahead 방어 규칙 (Market Policies)
=================================================
종목 신원(이름/거래소/리스팅)의 정답은 종목 마스터 원장(symbols_registry.json)이며
`shared.config.config_manager` 로만 접근한다. 이 모듈은 종목 데이터를 갖지 않는다:

- DEFAULT_MARKET_POLICIES : 시장 분류별 수수료/슬리피지/매도세/호가단위 표준 정책 (원장이 상속)
- timestamp_convention    : 캔들 타임스탬프 라벨 규약 판정 (Lookahead 방어 단일 판정 지점)
                            규약 자체는 DataVault/config/timestamp_conventions.json 원장에만 있다
"""

from typing import Dict, Any, Tuple

# =============================================================================
# 시장 분류별 표준 기본 수수료/슬리피지/호가단위 정책 (Master Market Policies)
# =============================================================================
# default_fee / default_slippage 는 편도(매수·매도 각각) 비율, sell_tax 는 매도 시에만 붙는 세금 비율이다.
#   왕복 비용 = 2 × (fee + slippage) + sell_tax  — 계산은 shared.core.trade_costs.round_trip_cost 한 곳에서만.
#   세금을 수수료에 섞지 않는 이유: 세율은 법이 정하는 값이라 증권사 수수료와 따로 바뀌고, 실거래 수수료 대조가 깨진다.
# ⚠️ 거래 대상(tradable) 종목의 market_type 은 반드시 여기에 명시 정책이 있어야 한다. 없으면 조용히 OTHER
#    (수수료 0.015%, 호가 1.0)로 떨어져 비용이 틀어진다 (KRW-USDT 가 CRYPTO_SPOT 이라 이렇게 0.015% 로 잡혔던 사고).
#    참조 전용(tradable=False) 종목은 비용을 쓰지 않으므로 OTHER 폴백을 허용한다. (shared/tests/test_symbol_ledger.py 가 강제)
DEFAULT_MARKET_POLICIES: Dict[str, Dict[str, Any]] = {
    "KRX_ETF": {
        "market_name": "국내 ETF",
        "default_fee": 0.00005,      # 0.005% (증권거래세 0%)
        "default_slippage": 0.00010, # 0.01%
        "sell_tax": 0.0,          # 국내 ETF 매도분 증권거래세 면제
        "tick_size": 5.0,
        "exchange": "KIS",
        "base_currency": "KRW"
    },
    "KRX_STOCK": {
        "market_name": "국내 주식",
        "default_fee": 0.00015,      # 0.015% (증권거래세는 sell_tax 로 따로)
        "default_slippage": 0.00020, # 0.02%
        "sell_tax": 0.0020,       # 0.20% 매도 시에만 (2026-01-01 양도분~: 코스피 거래세 0.05%+농특세 0.15%, 코스닥 0.20%)
        "tick_size": 100.0,
        "exchange": "KIS",
        "base_currency": "KRW"
    },
    "CRYPTO": {
        "market_name": "가상자산",
        "default_fee": 0.00050,      # 0.05% (업비트 고정)
        "default_slippage": 0.00060, # 0.06%
        "sell_tax": 0.0,          # 거래 건별 세금 없음
        "tick_size": 100.0,
        "exchange": "UPBIT",
        "base_currency": "KRW"
    },
    "US_ETF": {
        "market_name": "해외 ETF/주식",
        "default_fee": 0.00070,      # 0.07% (해외 협의수수료)
        "default_slippage": 0.00050, # 0.05%
        "sell_tax": 0.0,          # 거래 건별 세금 없음 (양도세는 연 단위 — 거래비용 아님)
        "tick_size": 0.01,
        "exchange": "KIS",
        "base_currency": "USD"
    },
    "US_STOCK": {
        "market_name": "해외 주식",
        "default_fee": 0.00070,      # US_ETF 와 동일 (해외 협의수수료)
        "default_slippage": 0.00050,
        "sell_tax": 0.0,
        "tick_size": 0.01,
        "exchange": "KIS",
        "base_currency": "USD"
    },
    # cTrader CFD 대용 상품 — 실제 비용은 브로커 조건이라 표준값이며, 호가 단위가 다른 종목(닛케이/항셍 등)만 override 한다
    "GLOBAL_INDEX_CFD": {
        "market_name": "해외 지수 CFD (대용)",
        "default_fee": 0.00010,
        "default_slippage": 0.00010,
        "sell_tax": 0.0,
        "tick_size": 0.25,
        "exchange": "CTRADER",
        "base_currency": "USD"
    },
    "COMMODITY_CFD": {
        "market_name": "원자재 CFD (대용)",
        "default_fee": 0.00010,
        "default_slippage": 0.00010,
        "sell_tax": 0.0,
        "tick_size": 0.01,
        "exchange": "CTRADER",
        "base_currency": "USD"
    },
    "FX_SPOT": {
        "market_name": "외환 현물 (대용)",
        "default_fee": 0.00010,
        "default_slippage": 0.00010,
        "sell_tax": 0.0,
        "tick_size": 0.001,
        "exchange": "CTRADER",
        "base_currency": "USD"
    },
    "GLOBAL_FUTURES": {
        "market_name": "해외 지수선물",
        "default_fee": 0.00010,
        "default_slippage": 0.00010,
        "sell_tax": 0.0,
        "tick_size": 0.25,
        "exchange": "CME",
        "base_currency": "USD"
    },
    "OTHER": {
        "market_name": "기타",
        "default_fee": 0.00015,
        "default_slippage": 0.00020,
        "sell_tax": 0.0,
        "tick_size": 1.0,
        "exchange": "KIS",
        "base_currency": "KRW"
    }
}

# =============================================================================
# 캔들 타임스탬프 라벨 규약 — Lookahead 방어 단일 판정 지점
# =============================================================================
# 캔들의 타임스탬프가 '봉이 시작하는 시각'인지 '봉이 확정되는 시각'인지는 **수집처의 성질**이지
# 종목의 성질이 아니다. 과거에는 이를 종목 이름/시장 구분으로 추측했고(NQ/ES/YM… 토큰 목록),
# 기본값이 '시프트 안 함'이라 선언이 빠진 신규 자산에서 조용히 미래참조가 재발했다.
#
# 이제 규약은 `DataVault/config/timestamp_conventions.json` 원장 한 곳에만 있다.
# 코드는 읽기만 하며, 선언이 없으면 **추측하지 않고 거부**한다(fail-closed).
#   심볼 → source_mappings.json 의 primary_source → 원장의 provider → 규약

BAR_OPEN = "bar_open"     # 타임스탬프 = 봉 시작 시각 (그 봉의 종가는 1봉 뒤에 확정된다)
BAR_CLOSE = "bar_close"   # 타임스탬프 = 봉 확정 시각 (그 시각에 이미 알 수 있다)
_VALID_CONVENTIONS = (BAR_OPEN, BAR_CLOSE)


class UnknownTimestampConventionError(RuntimeError):
    """수집처의 타임스탬프 규약이 원장에 선언되지 않았을 때 발생.

    이 예외는 '모르면 안전한 쪽으로 가정한다'를 대체한다. 어느 쪽으로 가정해도
    한쪽은 미래참조, 다른 한쪽은 1봉 과지연이라 조용히 틀리기 때문이다.
    """


def _load_timestamp_conventions() -> Dict[str, Any]:
    """규약 원장 로드 (파일 변경 감지 캐싱)."""
    global _TS_CONVENTION_CACHE, _TS_CONVENTION_MTIME
    import json
    import os
    from shared.data_library.paths import data_vault_config_path

    path = data_vault_config_path("timestamp_conventions.json")
    if not os.path.exists(path):
        raise UnknownTimestampConventionError(
            f"타임스탬프 규약 원장이 없습니다: {path}\n"
            "수집처별 캔들 라벨 규약(bar_open/bar_close)을 선언해야 결합을 진행할 수 있습니다."
        )
    mtime = os.path.getmtime(path)
    if _TS_CONVENTION_CACHE is not None and mtime == _TS_CONVENTION_MTIME:
        return _TS_CONVENTION_CACHE
    with open(path, "r", encoding="utf-8") as f:
        _TS_CONVENTION_CACHE = json.load(f).get("providers", {})
    _TS_CONVENTION_MTIME = mtime
    return _TS_CONVENTION_CACHE


_TS_CONVENTION_CACHE: Any = None
_TS_CONVENTION_MTIME: Any = None


def provider_of(symbol: str) -> str:
    """심볼의 1차 수집처. source_mappings 원장이 유일한 근거이며, 모르면 빈 문자열."""
    sym = str(symbol or "").strip()
    if not sym:
        return ""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        maps = SourceMappingManager.load_all_mappings()
        clean = SourceMappingManager.clean_symbol(sym)
        entry = maps.get(sym) or maps.get(clean)
        if entry is None:
            for k, v in maps.items():
                if SourceMappingManager.clean_symbol(k) == clean:
                    entry = v
                    break
        if entry:
            return str(entry.get("primary_source", "")).strip().upper()
    except Exception:
        return ""
    return ""


def timestamp_convention(symbol: str, interval: str = "") -> str:
    """심볼+인터벌 캔들의 타임스탬프 라벨 규약 (``BAR_OPEN`` 또는 ``BAR_CLOSE``).

    같은 수집처라도 인터벌마다 규약이 다를 수 있다 — 수집처가 준 캔들과 우리가 틱에서
    직접 만든 캔들이 섞이기 때문이다. 그래서 인터벌별 선언을 먼저 보고, 없으면 수집처 기본값을 쓴다.

    선언이 없으면 ``UnknownTimestampConventionError`` 를 던진다 — 추측하지 않는다.
    """
    sym = str(symbol or "").strip()
    provider = provider_of(sym)
    if not provider:
        raise UnknownTimestampConventionError(
            f"'{sym}' 의 수집처를 알 수 없습니다 (source_mappings.json 에 primary_source 가 없습니다).\n"
            "종목을 수집처 원장에 등록해야 타임스탬프 규약을 판정할 수 있습니다."
        )
    providers = _load_timestamp_conventions()
    declared = providers.get(provider)
    if not declared:
        raise UnknownTimestampConventionError(
            f"수집처 '{provider}' (심볼 {sym}) 의 타임스탬프 규약이 선언되지 않았습니다.\n"
            f"DataVault/config/timestamp_conventions.json 의 providers 에 "
            f"'{provider}': {{\"convention\": \"bar_open|bar_close\", \"basis\": ..., \"evidence\": ...}} 를 추가하세요."
        )
    override = (declared.get("interval_overrides") or {}).get(str(interval or "").strip().lower())
    source = override if override else declared
    conv = str(source.get("convention", "")).strip().lower()
    if conv not in _VALID_CONVENTIONS:
        raise UnknownTimestampConventionError(
            f"수집처 '{provider}'{f' / {interval}' if override else ''} 의 규약 값이 올바르지 않습니다: {conv!r} "
            f"(허용: {', '.join(_VALID_CONVENTIONS)})"
        )
    return conv


def bar_label_shift(symbol: str, interval: str):
    """그 캔들의 타임스탬프를 '값이 확정되는 시각'으로 바꾸는 보정값 (``pandas.Timedelta``).

    ``bar_open`` 라벨은 1봉 길이만큼 밀어야 확정 시각이 된다. ``bar_close`` 는 이미 확정 시각이라 0.
    """
    import pandas as pd
    if timestamp_convention(symbol, interval) == BAR_CLOSE:
        return pd.Timedelta(0)
    return bar_duration(interval)


def bar_bucket_offset(symbol: str, interval: str):
    """캔들 라벨 → 그 캔들이 담는 구간의 **시작 시각**까지의 거리 (``pandas.Timedelta``).

    ``bar_open`` 라벨은 이미 구간 시작이라 0, ``bar_close`` 라벨은 1봉 앞이 구간 시작이다.
    (원천 틱을 어느 캔들과 맞춰 볼지 같은 '라벨 산술'은 전부 이 모듈에 모은다 —
     규약 해석이 여러 곳에 흩어지면 같은 종목이 코드마다 다른 봉에 대응된다)
    """
    return bar_duration(interval) - bar_label_shift(symbol, interval)


def reference_merge_shift(ref_symbol: str, ref_interval: str,
                          target_symbol: str, target_interval: str):
    """참조 캔들을 타겟 시계열에 붙이기 전에 그 타임스탬프에 더할 보정값.

    기준은 **의사결정 시점**이다. 타겟의 한 행에서 매매 판단을 내릴 수 있는 순간은
    그 행의 봉이 확정되는 시각이고, 참조값은 그 순간까지 확정된 것만 보여야 한다.

        참조 확정시각 = ref_label  + (ref 가 bar_open 이면 1봉, 아니면 0)
        타겟 확정시각 = tgt_label  + (타겟이 bar_open 이면 1봉, 아니면 0)
        결합 키       = 참조 확정시각 − (타겟 확정시각 − tgt_label)

    이렇게 두면 `merge_asof(..., direction="backward")` 가 "그 시점까지 확정된 참조값"만 고른다.
    두 계열의 규약이 같고 인터벌도 같으면 보정은 0 이 되어 기존 동작과 같다.
    """
    return bar_label_shift(ref_symbol, ref_interval) - bar_label_shift(target_symbol, target_interval)


def bar_duration(interval: str):
    """인터벌 문자열('10s'/'1m'/'15m'/'1h'/'1d')의 1봉 길이."""
    import pandas as pd
    s = str(interval or "").strip().lower()
    num = int("".join(ch for ch in s if ch.isdigit()) or 1)
    unit = "".join(ch for ch in s if ch.isalpha()) or "m"
    table = {"s": "s", "sec": "s", "m": "min", "min": "min", "h": "h", "hour": "h", "d": "D", "day": "D"}
    return pd.Timedelta(f"{num}{table.get(unit, 'min')}")


# ==============================================================================
# 가상자산 피처 정책 (종목이 아니라 '자산 그룹(asset_id)' 단위로 정의한다)
# ==============================================================================
# 코인 타겟의 상대 강도/벤치마크 피처는 BTC 를 시장 기준으로 삼고, BTC 자신이 타겟이면 ETH 를
# 상대 모멘텀 자산으로 쓴다. 리스팅 키(KRW-BTC/BTCUSDT/BTC-USD)가 아니라 asset_id 로 판정하므로
# 같은 자산의 어느 리스팅이 타겟이어도 동일하게 동작한다.
BENCHMARK_ASSET_ID = "BTC"
RELATIVE_MOMENTUM_ASSET_ID = "ETH"


# 코인 현물의 기본 정규화 봉: 소스 대장(source_mappings)에 canonical_intervals 가 명시되지 않았을 때의 폴백이자
# 일일 증분 업서트의 기본 봉. 바이낸스 BTCUSDT 는 15m Vision 아카이브가 기준이라 예외다.
# (종목별 기본 봉을 호출부 if 문에 흩어 두지 않고 여기 한 곳에서만 정의한다)
CRYPTO_SPOT_DEFAULT_INTERVAL = "10m"
CRYPTO_SPOT_DEFAULT_INTERVAL_OVERRIDES = {"BTCUSDT": "15m"}


def crypto_spot_default_interval(symbol: str) -> str:
    return CRYPTO_SPOT_DEFAULT_INTERVAL_OVERRIDES.get(str(symbol).strip().upper(), CRYPTO_SPOT_DEFAULT_INTERVAL)
