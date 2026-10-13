import asyncio
import websockets
from websockets.protocol import State
import json
import uuid
import logging
import time
from connectors.base_connector import BaseConnector
from parsers.upbit_parser import UpbitParser

class UpbitConnector(BaseConnector):
    def __init__(self, symbols: list, queue: asyncio.Queue):
        super().__init__(symbols[0], queue)
        self.symbols = symbols
        self.uri = "wss://api.upbit.com/websocket/v1"
        self.ws = None
        # 심볼별 필요한 호가 단위들 {symbol: {level: type}}
        # type: 'STRAT', 'UI', 'RAW'
        self.required_levels = {s: {0.0: 'RAW'} for s in symbols}
        self.rest_session = None

    async def connect(self):
        logging.info(f"[Upbit] Starting connect loop for {self.symbols}")
        self.is_running = True
        backoff = 1.0
        
        while self.is_running:
            session_start = time.monotonic()
            try:
                async with websockets.connect(self.uri, ping_interval=30) as ws:
                    self.ws = ws
                    logging.info("[Upbit] Websocket Connected")
                    await self.subscribe()

                    while self.is_running:
                        raw_msg = await ws.recv()
                        if isinstance(raw_msg, bytes):
                            raw_msg = raw_msg.decode('utf-8')
                        
                        parsed_data = self.parse_message(raw_msg)
                        if parsed_data:
                            await self.queue.put(parsed_data)
                            
            except websockets.exceptions.ConnectionClosedOK:
                logging.info("[Upbit] Websocket Connection closed gracefully (1000 OK).")
            except Exception as e:
                logging.error(f"[Upbit] Connection Error: {e}")
            finally:
                self.ws = None
            if not self.is_running:
                break
            # 정상종료(1000) 포함 모든 종료 후 대기 (즉시 재접속 핫루프 방지, 안정 유지 시에만 백오프 리셋)
            backoff = self.next_backoff(backoff, time.monotonic() - session_start)
            await asyncio.sleep(backoff)

    async def subscribe(self):
        if not self.ws: return
        
        # 1. 공통 체결 구독
        packet = [
            {"ticket": str(uuid.uuid4())},
            {"type": "trade", "codes": self.symbols}
        ]
        
        # 2. 단위별 호가 구독 분리
        level_map = {} # level -> [symbols]
        for s, levels in self.required_levels.items():
            for lvl in levels.keys():
                if lvl not in level_map: level_map[lvl] = []
                level_map[lvl].append(s)
        
        for lvl, codes in level_map.items():
            ob_packet = {"type": "orderbook", "codes": codes}
            if lvl > 0:
                ob_packet["level"] = str(int(lvl)) if lvl >= 1 else str(lvl)
            packet.append(ob_packet)

        await self.ws.send(json.dumps(packet))
        logging.info(f"[Upbit] Subscription Sent with levels: {level_map}")

    async def add_strategy_level(self, symbol: str, level: float):
        """전략용 호가 단위 추가 (항상 유지)"""
        if symbol in self.required_levels:
            self.required_levels[symbol][level] = 'STRAT'
            await self.subscribe()

    async def update_symbols(self, new_symbols: list):
        """
        [NEW] 연결을 끊지 않고 구독 종목 목록을 갱신한다.
        기존 연결이 살아있으면 새 subscribe 패킷만 전송.
        신규 종목은 required_levels에 RAW 레벨로 추가.
        """
        added = [s for s in new_symbols if s not in self.symbols]
        self.symbols = new_symbols
        # 제거된 종목의 호가 단위 구독 정보 정리 (재구독 시 남지 않도록)
        for s in list(self.required_levels.keys()):
            if s not in new_symbols:
                del self.required_levels[s]
        # 신규 종목 기본 레벨 등록
        for s in added:
            if s not in self.required_levels:
                self.required_levels[s] = {0.0: 'RAW'}
        if self.ws and self.ws.state == State.OPEN:
            await self.subscribe()
            logging.info(f"[Upbit] 구독 갱신 완료 (재연결 없음): {new_symbols}")
        else:
            logging.warning("[Upbit] WS 없음 — 구독 갱신 스킵, 다음 연결 시 적용됨")

    async def set_ui_level(self, symbol: str, level: float):
        """UI용 호가 단위 설정 (기존 UI 단위가 있으면 교체)"""
        if symbol in self.required_levels:
            # 기존 UI 타입인 레벨 제거
            self.required_levels[symbol] = {l: t for l, t in self.required_levels[symbol].items() if t != 'UI'}
            self.required_levels[symbol][level] = 'UI'
            await self.subscribe()

    def parse_message(self, message: str):
        """
        수신된 메시지를 타입에 따라 적절한 파서로 전달
        """
        return UpbitParser.parse_message_str(message)

    async def stop(self):
        self.is_running = False
        if self.ws:
            try:
                await self.ws.close()
                logging.info("[Upbit] Websocket Connection Closed.")
            except Exception as e:
                logging.error(f"[Upbit] Error closing Upbit websocket: {e}")
        if hasattr(super(), 'stop'):
            await super().stop()