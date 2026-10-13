from dataclasses import dataclass
from typing import Optional, List

@dataclass
class TickData:
    __slots__ = ['symbol', 'exchange', 'price', 'volume', 'side', 'timestamp']
    symbol: str
    exchange: str
    price: float
    volume: float
    side: str  # 'BID' or 'ASK'
    timestamp: float

@dataclass
class OrderbookUnit:
    __slots__ = ['bid_price', 'bid_size', 'ask_price', 'ask_size']
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float

@dataclass
class OrderbookData:
    __slots__ = ['symbol', 'exchange', 'units', 'timestamp', 'level']
    symbol: str
    exchange: str
    units: List[OrderbookUnit]
    timestamp: float
    level: float