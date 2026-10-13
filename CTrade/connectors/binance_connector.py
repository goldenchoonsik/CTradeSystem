import asyncio
import websockets
import json
import logging
import time
from connectors.base_connector import BaseConnector
from parsers.binance_parser import BinanceParser

class BinanceConnector(BaseConnector):
    def __init__(self, symbols: list, queue: asyncio.Queue):
        super().__init__(symbols[0] if symbols else "", queue)
        self.all_symbols = symbols
        self.spot_base = "wss://stream.binance.com:9443/stream?streams="
        self.futures_base = "wss://fstream.binance.com/stream?streams="
        self.active_sockets = []

    async def connect(self):
        logging.info(f"[Binance] Starting dual connect loop for {self.all_symbols}")
        self.is_running = True
        
        spot_symbols = [s.lower() for s in self.all_symbols if "_perp" not in s.lower()]
        futures_symbols = [s.lower().replace("_perp", "") for s in self.all_symbols if "_perp" in s.lower()]
        
        tasks = []
        if spot_symbols:
            tasks.append(self._connect_loop(self.spot_base, spot_symbols, "SPOT"))
        if futures_symbols:
            tasks.append(self._connect_loop(self.futures_base, futures_symbols, "FUTURES"))
            
        if tasks:
            await asyncio.gather(*tasks)

    async def _connect_loop(self, base_uri, symbols, market_type):
        streams = []
        for s in symbols:
            streams.append(f"{s}@trade")
            streams.append(f"{s}@depth5@100ms")
        
        full_uri = base_uri + "/".join(streams)
        
        backoff = 1.0
        while self.is_running:
            session_start = time.monotonic()
            try:
                async with websockets.connect(full_uri, ping_interval=20) as ws:
                    self.active_sockets.append(ws)
                    logging.info(f"[Binance {market_type}] Websocket Connected")
                    try:
                        while self.is_running:
                            raw_msg = await ws.recv()
                            parsed_data = self.parse_message(raw_msg, market_type)
                            if parsed_data:
                                await self.queue.put(parsed_data)
                    finally:
                        if ws in self.active_sockets:
                            self.active_sockets.remove(ws)
            except Exception as e:
                if self.is_running:
                    logging.error(f"[Binance {market_type}] Connection Error: {e}")
            if self.is_running:
                backoff = self.next_backoff(backoff, time.monotonic() - session_start)
                await asyncio.sleep(backoff)

    def parse_message(self, message: str, market_type: str = "SPOT"):
        return BinanceParser.parse_message_str(message, market_type)

    async def stop(self):
        self.is_running = False
        sockets_to_close = list(self.active_sockets)
        self.active_sockets.clear()
        for ws in sockets_to_close:
            try:
                await ws.close()
                logging.info("[Binance] Websocket Connection Closed.")
            except Exception as e:
                logging.error(f"[Binance] Error closing Binance websocket: {e}")
        if hasattr(super(), 'stop'):
            await super().stop()

