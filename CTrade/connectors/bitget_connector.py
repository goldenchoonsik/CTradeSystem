import asyncio
import websockets
import json
import logging
import time
from connectors.base_connector import BaseConnector
from parsers.bitget_parser import BitgetParser
from shared.core import stream_keys as sk

class BitgetConnector(BaseConnector):
    def __init__(self, symbols: list, queue: asyncio.Queue):
        super().__init__(symbols[0] if symbols else "", queue)
        self.all_symbols = symbols
        # Bitget public WS URI (v2)
        self.ws_uri = "wss://ws.bitget.com/v2/ws/public"
        self.ws = None

    async def connect(self):
        logging.info(f"[Bitget] Starting connect loop for {self.all_symbols}")
        self.is_running = True
        
        # _BPERP 접미사로 비트겟 선물을 구분한다고 가정 (engine에서 주입)
        # 예: PROSUSDT (Spot), PROSUSDT_BPERP (Futures)
        spot_symbols = [s.upper() for s in self.all_symbols if not sk.is_bitget_futures_key(s.upper())]
        futures_symbols = [sk.exchange_symbol(s.upper()) for s in self.all_symbols if sk.is_bitget_futures_key(s.upper())]
        
        args = []
        for s in spot_symbols:
            args.append({"instType": "SPOT", "channel": "trade", "instId": s})
            args.append({"instType": "SPOT", "channel": "books5", "instId": s})
            
        for s in futures_symbols:
            args.append({"instType": "USDT-FUTURES", "channel": "trade", "instId": s})
            args.append({"instType": "USDT-FUTURES", "channel": "books5", "instId": s})
            
        if not args:
            return

        subscribe_msg = {
            "op": "subscribe",
            "args": args
        }

        backoff = 1.0
        while self.is_running:
            session_start = time.monotonic()
            keepalive = None
            try:
                async with websockets.connect(self.ws_uri, ping_interval=20) as ws:
                    self.ws = ws
                    logging.info(f"[Bitget] Websocket Connected")
                    await ws.send(json.dumps(subscribe_msg))
                    # Bitget 은 30초마다 텍스트 "ping" 을 보내야 연결이 유지된다
                    keepalive = asyncio.ensure_future(self._keepalive(ws))
                    
                    try:
                        while self.is_running:
                            raw_msg = await ws.recv()
                            
                            # Bitget은 ping 메시지가 'ping' 텍스트로 오고 'pong'으로 응답해야 할 수 있음.
                            if raw_msg == "ping":
                                await ws.send("pong")
                                continue
                            if raw_msg == "pong":
                                continue
                                
                            parsed_data_list = self.parse_message(raw_msg)
                            if parsed_data_list:
                                for parsed_data in parsed_data_list:
                                    await self.queue.put(parsed_data)
                    finally:
                        self.ws = None
            except Exception as e:
                if self.is_running:
                    logging.error(f"[Bitget] Connection Error: {e}")
            finally:
                if keepalive is not None:
                    keepalive.cancel()
            if self.is_running:
                backoff = self.next_backoff(backoff, time.monotonic() - session_start)
                await asyncio.sleep(backoff)

    async def _keepalive(self, ws, interval: float = 30.0):
        """Bitget 텍스트 ping keepalive (30초 주기)"""
        try:
            while True:
                await asyncio.sleep(interval)
                await ws.send("ping")
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def parse_message(self, message: str):
        return BitgetParser.parse_message_str(message)

    async def stop(self):
        self.is_running = False
        if self.ws:
            try:
                await self.ws.close()
                logging.info("[Bitget] Websocket Connection Closed.")
            except Exception as e:
                logging.error(f"[Bitget] Error closing Bitget websocket: {e}")
        if hasattr(super(), 'stop'):
            await super().stop()
