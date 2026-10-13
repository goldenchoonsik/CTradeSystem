import time
from core.models import TickData, OrderbookData, OrderbookUnit
import ctrade_core

class UpbitParser:
    @staticmethod
    def parse_message_str(message: str):
        return ctrade_core.parse_upbit_message(message)

    @staticmethod
    def parse_tick(data: dict) -> TickData:
        return TickData(
            symbol=data.get('code'), # 'KRW-BTC' 형태
            exchange="UPBIT",
            price=float(data.get('trade_price', 0)),
            volume=float(data.get('trade_volume', 0)),
            side=data.get('ask_bid', 'BID'),
            timestamp=time.time()
        )

    @staticmethod
    def parse_orderbook(data: dict) -> OrderbookData:
        units = [OrderbookUnit(
            bid_price=float(u['bid_price']), bid_size=float(u['bid_size']),
            ask_price=float(u['ask_price']), ask_size=float(u['ask_size'])
        ) for u in data.get('orderbook_units', [])]
        
        raw_symbol = data.get('code') or data.get('market') or ""
        # 'KRW-BTC.15' 형태에서 심볼과 레벨 분리
        parts = raw_symbol.split('.')
        symbol = parts[0]
        level = float(parts[1]) if len(parts) > 1 else 0.0
        
        return OrderbookData(
            symbol=symbol,
            exchange="UPBIT",
            units=units,
            timestamp=time.time(),
            level=level
        )