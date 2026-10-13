import time
import logging
from core.models import TickData, OrderbookData, OrderbookUnit
import ctrade_core

class KISParser:
    @staticmethod
    def _detect_symbol_offset(fields: list, symbol: str) -> int:
        """
        H0STCNT0/H0STASP0 데이터 본문에서 종목코드 위치를 자동 감지합니다.
        KIS 웹소켓 데이터 형식은 구간에 따라 다를 수 있습니다:
          - '005930^체결시간^현재가^...'        -> 종목코드가 첫 필드 (offset=0)
          - '^005930^체결시간^현재가^...'       -> 첫 필드가 빈 문자열 (offset=1)
          - '^MK1^005930^체결시간^현재가^...'   -> 시장구분 필드 포함 (offset=2)
        종목코드를 찾지 못한 경우 안전 폴백으로 0을 반환합니다.
        """
        if not fields:
            return 0
        for i, f in enumerate(fields[:5]):
            if f == symbol:
                return i
        # 종목코드 미발견 시: 첫 필드가 빈 문자열이면 표준 형식 가정 (offset=1)
        if fields[0] == '':
            return 1
        return 0

    @staticmethod
    def split_records(message: str) -> list:
        """'0|TR_ID|건수|rec^rec^...' 다건 레코드 전문을 단건('...|001|rec') 전문 리스트로 분할.
        건수가 1 이하이거나 필드 수가 건수로 나누어 떨어지지 않으면 원문 그대로 반환한다."""
        parts = message.split('|', 3)
        if len(parts) < 4 or not parts[2].strip().isdigit():
            return [message]
        n = int(parts[2])
        if n <= 1:
            return [message]
        fields = parts[3].split('^')
        if len(fields) % n != 0:
            return [message]
        per = len(fields) // n
        return [
            f"{parts[0]}|{parts[1]}|001|" + '^'.join(fields[i * per:(i + 1) * per])
            for i in range(n)
        ]

    @staticmethod
    def parse_message_str(message: str, symbol: str):
        # 1. 해외주식 TR(HDFSCNT0, HDFSASP0) 처리
        if "|HDFSCNT0|" in message:
            parts = message.split('|')
            if len(parts) >= 4:
                return KISParser.parse_overseas_tick(parts[3], symbol)
        elif "|HDFSASP0|" in message:
            parts = message.split('|')
            if len(parts) >= 4:
                return KISParser.parse_overseas_orderbook(parts[3], symbol)

        # 1-1. 국내선물 TR(H0IFCNT0, H0IFASP0) 처리
        if "|H0IFCNT0|" in message:
            parts = message.split('|')
            if len(parts) >= 4:
                return KISParser.parse_futures_tick(parts[3], symbol)
        elif "|H0IFASP0|" in message:
            parts = message.split('|')
            if len(parts) >= 4:
                return KISParser.parse_futures_orderbook(parts[3], symbol)

        # 1-2. 국내 주식선물 TR(H0ZFCNT0, H0ZFASP0) — 지수선물과 필드 배열이 다르다(현재가 [2], 호가 10단계)
        if "|H0ZFCNT0|" in message:
            parts = message.split('|')
            if len(parts) >= 4:
                return KISParser.parse_stock_futures_tick(parts[3], symbol)
        elif "|H0ZFASP0|" in message:
            parts = message.split('|')
            if len(parts) >= 4:
                return KISParser.parse_stock_futures_orderbook(parts[3], symbol)
        
        # 2. 국내주식 C++ 파서 호출 (H0STCNT0, H0STASP0)
        try:
            return ctrade_core.parse_kis_message(message, symbol)
        except Exception:
            # C++ 파서 실패 시 파이썬 폴백
            parts = message.split('|')
            if len(parts) >= 4:
                tr_id = parts[1]
                data_body = parts[3]
                if tr_id == "H0STCNT0":
                    return KISParser.parse_tick(data_body, symbol)
                elif tr_id == "H0STASP0":
                    return KISParser.parse_orderbook(data_body, symbol)
        return None

    @staticmethod
    def parse_tick(data_body: str, symbol: str) -> TickData:
        """KIS 국내주식 실시간 체결 데이터 (H0STCNT0)"""
        fields = data_body.split('^')
        offset = KISParser._detect_symbol_offset(fields, symbol)
        if len(fields) <= offset + 12:
            logging.debug(f"[KISParser] parse_tick: fields too short ({len(fields)}) for offset={offset}")
            return None
        
        price = float(fields[offset + 2])
        ask1 = float(fields[offset + 10]) if fields[offset + 10] else 0.0
        bid1 = float(fields[offset + 11]) if fields[offset + 11] else 0.0
        
        side = 'BID'
        if price <= bid1 and price < ask1:
            side = 'ASK'
        
        return TickData(
            symbol=symbol,
            exchange="KIS",
            price=price,
            volume=float(fields[offset + 12]),
            side=side,
            timestamp=time.time()
        )

    @staticmethod
    def parse_orderbook(data_body: str, symbol: str) -> OrderbookData:
        """KIS 국내주식 실시간 호가 데이터 (H0STASP0)"""
        fields = data_body.split('^')
        offset = KISParser._detect_symbol_offset(fields, symbol)
        if len(fields) <= offset + 42:
            logging.debug(f"[KISParser] parse_orderbook: fields too short ({len(fields)}) for offset={offset}")
            return None
        
        units = []
        for i in range(10):
            unit = OrderbookUnit(
                ask_price=float(fields[offset + 3 + i]),
                bid_price=float(fields[offset + 13 + i]),
                ask_size=float(fields[offset + 23 + i]),
                bid_size=float(fields[offset + 33 + i])
            )
            units.append(unit)
        
        return OrderbookData(
            symbol=symbol,
            exchange="KIS",
            units=units,
            timestamp=time.time(),
            level=0
        )

    @staticmethod
    def parse_overseas_tick(data_body: str, symbol: str) -> TickData:
        """KIS 해외(미국)주식 실시간 체결 데이터 (HDFSCNT0)"""
        fields = data_body.split('^')
        # fields: [0]=DNASTQQQ, [1]=TQQQ, [11]=현재가, [19]=매도호가, [20]=매수호가, [22]=체결량
        try:
            parsed_sym = fields[1] if len(fields) > 1 and fields[1] else symbol
            price = float(fields[11])
            ask1 = float(fields[19]) if len(fields) > 19 and fields[19] else 0.0
            bid1 = float(fields[20]) if len(fields) > 20 and fields[20] else 0.0
            volume = float(fields[22]) if len(fields) > 22 and fields[22] else 1.0

            side = 'BID'
            if ask1 > 0 and price >= ask1:
                side = 'BID'
            elif bid1 > 0 and price <= bid1:
                side = 'ASK'

            return TickData(
                symbol=parsed_sym,
                exchange="KIS",
                price=price,
                volume=volume,
                side=side,
                timestamp=time.time()
            )
        except Exception as e:
            logging.debug(f"[KISParser] parse_overseas_tick error: {e}")
            return None

    @staticmethod
    def parse_overseas_orderbook(data_body: str, symbol: str) -> OrderbookData:
        """KIS 해외(미국)주식 실시간 호가 데이터 (HDFSASP0)"""
        fields = data_body.split('^')
        # fields: [0]=DNASTQQQ, [1]=TQQQ, [11]=매수호가1, [12]=매도호가1, [13]=매수잔량1, [14]=매도잔량1
        try:
            parsed_sym = fields[1] if len(fields) > 1 and fields[1] else symbol
            units = []
            # 해외 호가는 보통 1단계~수단계 제공
            if len(fields) >= 15:
                bid_price = float(fields[11]) if fields[11] else 0.0
                ask_price = float(fields[12]) if fields[12] else 0.0
                bid_size = float(fields[13]) if fields[13] else 0.0
                ask_size = float(fields[14]) if fields[14] else 0.0
                units.append(OrderbookUnit(
                    ask_price=ask_price,
                    bid_price=bid_price,
                    ask_size=ask_size,
                    bid_size=bid_size
                ))

            return OrderbookData(
                symbol=parsed_sym,
                exchange="KIS",
                units=units,
                timestamp=time.time(),
                level=0
            )
        except Exception as e:
            logging.debug(f"[KISParser] parse_overseas_orderbook error: {e}")
            return None

    @staticmethod
    def parse_futures_tick(data_body: str, symbol: str) -> TickData:
        """KIS 국내 지수선물 실시간 체결 데이터 (H0IFCNT0)
        fields:
          [0]: 단축종목코드 (예: A01612)
          [1]: 영업시간 (HHMMSS)
          [5]: 선물현재가 (FUTS_PRPR)
          [9]: 최종거래량 (LAST_CNQN)
          [34]: 선물매도호가1 (FUTS_ASKP1)
          [35]: 선물매수호가1 (FUTS_BIDP1)
        """
        fields = data_body.split('^')
        offset = 0
        if fields and fields[0] == '' and len(fields) > 1:
            offset = 1
        if len(fields) <= offset + 9:
            logging.debug(f"[KISParser] parse_futures_tick: fields too short ({len(fields)})")
            return None
        try:
            price = float(fields[offset + 5])
            volume = float(fields[offset + 9])
            ask1 = float(fields[offset + 34]) if len(fields) > offset + 34 and fields[offset + 34] else 0.0
            bid1 = float(fields[offset + 35]) if len(fields) > offset + 35 and fields[offset + 35] else 0.0

            side = 'BID'
            if ask1 > 0 and bid1 > 0 and price <= bid1 and price < ask1:
                side = 'ASK'

            return TickData(
                symbol=symbol,
                exchange="KIS",
                price=price,
                volume=volume,
                side=side,
                timestamp=time.time()
            )
        except (ValueError, IndexError) as e:
            logging.debug(f"[KISParser] parse_futures_tick error: {e}")
            return None

    @staticmethod
    def parse_stock_futures_tick(data_body: str, symbol: str) -> TickData:
        """KIS 국내 주식선물 실시간 체결 데이터 (H0ZFCNT0)
        필드 배열은 KIS 공식 예제(open-trading-api domestic_futureoption/stock_futures_realtime_conclusion) 기준:
          [0]: 선물단축종목코드 (예: A11610)   [1]: 영업시간 (HHMMSS)
          [2]: 현재가 (STCK_PRPR)              [9]: 최종거래량 (LAST_CNQN)
          [34]: 매도호가1 (ASKP1)              [35]: 매수호가1 (BIDP1)
        (지수선물 H0IFCNT0 은 [5] 가 현재가라 parse_futures_tick 을 쓰면 [5]=전일대비율을 가격으로 읽는다)
        """
        fields = data_body.split('^')
        if len(fields) <= 9:
            logging.debug(f"[KISParser] parse_stock_futures_tick: fields too short ({len(fields)})")
            return None
        try:
            price = float(fields[2])
            volume = float(fields[9])
            ask1 = float(fields[34]) if len(fields) > 34 and fields[34] else 0.0
            bid1 = float(fields[35]) if len(fields) > 35 and fields[35] else 0.0

            side = 'BID'
            if ask1 > 0 and bid1 > 0 and price <= bid1 and price < ask1:
                side = 'ASK'

            return TickData(
                symbol=symbol,
                exchange="KIS",
                price=price,
                volume=volume,
                side=side,
                timestamp=time.time()
            )
        except (ValueError, IndexError) as e:
            logging.debug(f"[KISParser] parse_stock_futures_tick error: {e}")
            return None

    @staticmethod
    def parse_stock_futures_orderbook(data_body: str, symbol: str) -> OrderbookData:
        """KIS 국내 주식선물 실시간 호가 데이터 (H0ZFASP0) - 10단계 호가
        [2-11] 매도호가1~10, [12-21] 매수호가1~10, [42-51] 매도잔량1~10, [52-61] 매수잔량1~10"""
        fields = data_body.split('^')
        if len(fields) <= 61:
            return None
        try:
            units = []
            for i in range(10):
                ap = float(fields[2 + i]) if fields[2 + i] else 0.0
                bp = float(fields[12 + i]) if fields[12 + i] else 0.0
                as_ = float(fields[42 + i]) if fields[42 + i] else 0.0
                bs_ = float(fields[52 + i]) if fields[52 + i] else 0.0
                units.append(OrderbookUnit(ask_price=ap, bid_price=bp, ask_size=as_, bid_size=bs_))
            return OrderbookData(
                symbol=symbol,
                exchange="KIS",
                units=units,
                timestamp=time.time(),
                level=0
            )
        except (ValueError, IndexError):
            return None

    @staticmethod
    def parse_futures_orderbook(data_body: str, symbol: str) -> OrderbookData:
        """KIS 국내 지수선물 실시간 호가 데이터 (H0IFASP0) - 5단계 호가"""
        fields = data_body.split('^')
        offset = 0
        if fields and fields[0] == '' and len(fields) > 1:
            offset = 1
        if len(fields) <= offset + 31:
            return None
        try:
            units = []
            for i in range(5):
                ap = float(fields[offset + 2 + i]) if fields[offset + 2 + i] else 0.0
                bp = float(fields[offset + 7 + i]) if fields[offset + 7 + i] else 0.0
                as_ = float(fields[offset + 22 + i]) if fields[offset + 22 + i] else 0.0
                bs_ = float(fields[offset + 27 + i]) if fields[offset + 27 + i] else 0.0
                units.append(OrderbookUnit(ask_price=ap, bid_price=bp, ask_size=as_, bid_size=bs_))
            return OrderbookData(
                symbol=symbol,
                exchange="KIS",
                units=units,
                timestamp=time.time(),
                level=0
            )
        except (ValueError, IndexError):
            return None