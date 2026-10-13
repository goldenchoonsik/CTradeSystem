import time
from core.models import TickData, OrderbookData, OrderbookUnit
import ctrade_core

class BitgetParser:
    @staticmethod
    def parse_message_str(message: str):
        return ctrade_core.parse_bitget_message(message)

    @staticmethod
    def parse_trade(data_item: dict, symbol: str, market_type: str = "SPOT") -> TickData:
        """비트겟 trade 이벤트 파싱"""
        # data_item: 'data' 배열 안의 개별 거래 객체
        side_str = data_item.get("side", "buy").lower()
        side = "BID" if side_str == "buy" else "ASK"
        
        # Bitget은 timestamp 필드로 'ts'를 사용 (밀리초 단위)
        ts = int(data_item.get('ts', time.time() * 1000)) / 1000.0
        
        return TickData(
            symbol=symbol,
            exchange="BITGET" if market_type == "SPOT" else "BITGET_FUTURES",
            price=float(data_item.get('price', 0)),
            volume=float(data_item.get('size', 0)),
            side=side,
            timestamp=ts
        )

    @staticmethod
    def parse_orderbook(data_item: dict, symbol: str, market_type: str = "SPOT") -> OrderbookData:
        """비트겟 books 이벤트 파싱"""
        bids = data_item.get('bids', [])
        asks = data_item.get('asks', [])
        
        ts = int(data_item.get('ts', time.time() * 1000)) / 1000.0
        
        units = []
        for i in range(min(5, len(bids), len(asks))):
            units.append(OrderbookUnit(
                bid_price=float(bids[i][0]), bid_size=float(bids[i][1]),
                ask_price=float(asks[i][0]), ask_size=float(asks[i][1])
            ))
            
        return OrderbookData(
            symbol=symbol, 
            exchange="BITGET" if market_type == "SPOT" else "BITGET_FUTURES",
            units=units,
            timestamp=ts,
            level=0
        )
