"""
CTrade - Tick Router (틱 분배기)

consumer_loop에서 반복되던 틱 분배 로직(캔들 어그리게이터 / AI 전략 모듈)을
단일 책임 함수로 캡슐화합니다.
"""
from datetime import datetime, timezone, timedelta
from typing import Dict

from shared.core import stream_keys as sk
from shared.config.market_policies import BENCHMARK_ASSET_ID

KST = timezone(timedelta(hours=9))


def _extract_tick_context(data):
    """TickData에서 공통 컨텍스트 추출"""
    symbol = getattr(data, 'symbol', '')
    exchange = str(getattr(data, 'exchange', '')).upper()
    price = getattr(data, 'price', 0.0)
    volume = getattr(data, 'volume', 0.0)
    side = str(getattr(data, 'side', 'BID')).upper()
    raw_ts = getattr(data, 'timestamp', None)
    if isinstance(raw_ts, datetime):
        ts = raw_ts if raw_ts.tzinfo else raw_ts.replace(tzinfo=KST)
    elif isinstance(raw_ts, (int, float)) and raw_ts > 0:
        ts = datetime.fromtimestamp(raw_ts, tz=KST)
    else:
        ts = datetime.now(KST)
    return symbol, exchange, price, volume, side, ts


# 선물/글로벌 지수 심볼 정확 토큰 집합 (부분 문자열 오매칭 방지, L-2)
_FUTURES_EXACT_TOKENS = {
    'NQ', 'ES', 'YM', 'RTY', 'VX', 'NAS',
    'TQQQ', 'QQQ', 'SPY', 'SP500', 'SPX', 'S&P', 'S&P500',
    'KOSPI', 'KOSPI200',
    'US10Y', 'WTI', 'GOLD', 'USDKRW', 'NVDA', 'ZN', 'TN',
}
_FUTURES_SUFFIX_TOKENS = ('=F', 'FUT')


def _is_futures_symbol(symbol: str, target_sym: str) -> bool:
    """선물/글로벌 지수 심볼 여부 엄격 판별 (6자리 국내 주식코드 및 부분문자열 오매칭 방지)"""
    if not symbol or symbol == target_sym:
        return False
    clean_sym = str(symbol).strip()
    # 6자리 일반 국내 주식/ETF 코드는 선물로 오인하지 않음 (예: 105560 KB금융, 229200 코스닥150 등)
    if len(clean_sym) == 6 and clean_sym.isdigit():
        return False
    upper = clean_sym.upper()
    # KOSPI200 선물 코드 (예: 101V000, 105V000) — 6자리 주식코드는 위에서 이미 제외
    if upper.startswith('101') or upper.startswith('105'):
        return True
    # 정확 토큰 일치 또는 알려진 접미사만 허용 (부분 문자열 매칭 금지)
    if upper in _FUTURES_EXACT_TOKENS:
        return True
    if any(upper.endswith(t) for t in _FUTURES_SUFFIX_TOKENS):
        return True
    return False


def _route_to_aggregator(agg, symbol, exchange, price, volume, side, ts, agg_sym):
    """단일 캔들 어그리게이터에 틱 분배"""
    if symbol == agg_sym:
        # 타겟 종목 틱 (KIS 국내주식/ETF 또는 업비트)
        agg.on_target_tick(price, volume, side, ts)
        agg.check_candle_complete(ts)
    elif symbol == sk.FX_RATE_SYMBOL and 'UPBIT' in exchange:
        agg.on_upbit_usdt_tick(price, ts)
    elif 'BINANCE' in exchange and 'USDT' in symbol:
        base_coin = agg_sym.split('-')[-1] if '-' in agg_sym else agg_sym
        if symbol == f"{base_coin}USDT":
            is_buyer_maker = side in ('SELL', 'ASK')
            agg.on_binance_tick(price, volume, is_buyer_maker, ts)
        elif symbol == sk.spot_key(BENCHMARK_ASSET_ID):
            # 알트코인용 벤치마크 BTC 틱 라우팅
            if hasattr(agg, 'on_benchmark_btc_tick'):
                agg.on_benchmark_btc_tick(price, ts)
    elif 'BINANCE' in exchange and symbol == sk.USDC_VOLUME_PAIR:
        agg.add_binance_usdc_volume(volume, ts)
    elif 'COINBASE' in exchange or symbol.endswith('-USD'):
        from shared.config.config_manager import get_symbol_info
        sym_info = get_symbol_info(agg_sym)
        expected_cb = sym_info.get("coinbase_symbol") or ""
        if symbol == expected_cb:
            if hasattr(agg, 'on_coinbase_price'):
                agg.on_coinbase_price(price, ts)
    
    # 선물 및 글로벌 지수 틱 라우팅 (KOSPI200 선물, 나스닥, S&P500)
    if _is_futures_symbol(symbol, agg_sym):
        agg.on_futures_tick(symbol, price, ts)

    # 참조 대장주(Reference Symbol) 틱 라우팅
    if hasattr(agg, 'ref_symbol_map') and symbol in agg.ref_symbol_map:
        alias = agg.ref_symbol_map[symbol]
        is_buyer = side not in ('SELL', 'ASK')
        agg.on_ref_tick(alias, price, volume, is_buyer=is_buyer, timestamp=ts)


def _route_to_ai(ai_mod, symbol, exchange, price, volume, side, ts, ai_sym):
    """단일 AI 전략 모듈에 틱 분배"""
    if symbol == ai_sym:
        # 타겟 종목 틱
        if hasattr(ai_mod, 'on_target_tick'):
            ai_mod.on_target_tick(price, volume, side, ts)
        else:
            ai_mod.on_upbit_tick(price, volume, side, ts)
    elif symbol == sk.FX_RATE_SYMBOL and 'UPBIT' in exchange:
        ai_mod.on_upbit_usdt_tick(price, ts)
    elif 'BINANCE' in exchange and 'USDT' in symbol:
        base_coin = ai_sym.split('-')[-1] if '-' in ai_sym else ai_sym
        if symbol == f"{base_coin}USDT":
            is_buyer_maker = side in ('SELL', 'ASK')
            ai_mod.on_binance_tick(price, volume, is_buyer_maker, ts)
        elif symbol == sk.spot_key(BENCHMARK_ASSET_ID):
            if hasattr(ai_mod, 'on_benchmark_btc_tick'):
                ai_mod.on_benchmark_btc_tick(price, ts)
            elif hasattr(ai_mod, 'aggregator') and hasattr(ai_mod.aggregator, 'on_benchmark_btc_tick'):
                ai_mod.aggregator.on_benchmark_btc_tick(price, ts)
    elif 'BINANCE' in exchange and symbol == sk.USDC_VOLUME_PAIR:
        ai_mod.add_binance_usdc_volume(volume, ts)
    elif 'COINBASE' in exchange or symbol.endswith('-USD'):
        from shared.config.config_manager import get_symbol_info
        sym_info = get_symbol_info(ai_sym)
        expected_cb = sym_info.get("coinbase_symbol") or ""
        if symbol == expected_cb:
            if hasattr(ai_mod, 'on_coinbase_price'):
                ai_mod.on_coinbase_price(price, ts)
            elif hasattr(ai_mod, 'aggregator') and hasattr(ai_mod.aggregator, 'on_coinbase_price'):
                ai_mod.aggregator.on_coinbase_price(price, ts)

    # 선물 및 글로벌 지수 틱 라우팅 (KOSPI200 선물, 나스닥, S&P500)
    if _is_futures_symbol(symbol, ai_sym):
        if hasattr(ai_mod, 'on_futures_tick'):
            ai_mod.on_futures_tick(symbol, price, ts)

    # 참조 대장주(Reference Symbol) 틱 라우팅
    if hasattr(ai_mod, 'on_ref_tick'):
        is_buyer = side not in ('SELL', 'ASK')
        ai_mod.on_ref_tick(symbol, price, volume, timestamp=ts, is_buyer=is_buyer)


def route_tick(data, candle_aggregators: Dict, ai_strategies: Dict):
    """하나의 틱 데이터를 캔들 어그리게이터 및 AI 전략 모듈에 분배합니다."""
    if not getattr(data, 'price', 0.0) > 0:
        return

    symbol, exchange, price, volume, side, ts = _extract_tick_context(data)

    if candle_aggregators:
        for agg_sym, agg in candle_aggregators.items():
            _route_to_aggregator(agg, symbol, exchange, price, volume, side, ts, agg_sym)

    if ai_strategies:
        for ai_sym, ai_mod in ai_strategies.items():
            _route_to_ai(ai_mod, symbol, exchange, price, volume, side, ts, ai_sym)
