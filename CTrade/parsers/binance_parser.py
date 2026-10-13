import time
from core.models import TickData, OrderbookData, OrderbookUnit
import ctrade_core

class BinanceParser:
    @staticmethod
    def parse_message_str(message: str, market_type: str = "SPOT"):
        return ctrade_core.parse_binance_message(message, market_type)

    @staticmethod
    def parse_trade(data: dict, symbol: str = None, market_type: str = "SPOT") -> TickData:
        """바이낸스 trade 이벤트 파싱"""
        # 'm'이 True면 체결 주체가 Maker이므로 실제 주문은 시장가 매도(ASK)
        # 'm'이 False면 체결 주체가 Taker이므로 실제 주문은 시장가 매수(BID)
        side = "ASK" if data.get('m') else "BID"
        
        return TickData(
            symbol=symbol or data.get('s'), 
            exchange="BINANCE" if market_type == "SPOT" else "BINANCE_FUTURES",
            price=float(data.get('p', 0)),
            volume=float(data.get('q', 0)),
            side=side,
            timestamp=data.get('T', time.time() * 1000) / 1000.0
        )

    @staticmethod
    def parse_orderbook(data: dict, symbol: str, market_type: str = "SPOT") -> OrderbookData:
        """바이낸스 depth 이벤트 파싱 (모델 규격 통합)"""
        # 현물 partial depth: bids/asks, 선물: b/a
        bids = data.get('bids') if data.get('bids') is not None else data.get('b', [])
        asks = data.get('asks') if data.get('asks') is not None else data.get('a', [])
        
        # 바이낸스 호가를 OrderbookUnit 형식으로 변환 (최대 5단계)
        units = []
        for i in range(min(5, len(bids), len(asks))):
            units.append(OrderbookUnit(
                bid_price=float(bids[i][0]), bid_size=float(bids[i][1]),
                ask_price=float(asks[i][0]), ask_size=float(asks[i][1])
            ))
            
        return OrderbookData(
            symbol=symbol, 
            exchange="BINANCE" if market_type == "SPOT" else "BINANCE_FUTURES",
            units=units,
            timestamp=time.time(),
            level=0
        )

