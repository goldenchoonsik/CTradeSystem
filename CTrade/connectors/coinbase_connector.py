import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import websockets

from connectors.base_connector import BaseConnector
from core.models import TickData

KST = timezone(timedelta(hours=9))
UTC = timezone.utc


class CoinbaseConnector(BaseConnector):
    """코인베이스 공개 웹소켓(BTC-USD Ticker) 실시간 시세 수신 커넥터

    별도의 API Key나 인증 없이 wss://ws-feed.exchange.coinbase.com 에 연결하여
    BTC-USD 실시간 체결가를 TickData 형태로 엔진 큐에 공급합니다.
    """

    def __init__(self, symbols: Optional[List[str]] = None, queue: Optional[asyncio.Queue] = None):
        symbol_list = symbols or ["BTC-USD"]
        super().__init__(symbol_list[0], queue or asyncio.Queue())
        self.symbols = symbol_list
        self.ws_url = "wss://ws-feed.exchange.coinbase.com"
        self.active_socket: Optional[websockets.WebSocketClientProtocol] = None
        self._task: Optional[asyncio.Task] = None

    async def connect(self):
        """웹소켓 연결 및 구독 루프 (자동 재연결 탑재)"""
        self.is_running = True
        logging.info(f"[Coinbase] Starting WebSocket connection loop for {self.symbols} -> {self.ws_url}")

        backoff = 2
        while self.is_running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    open_timeout=10,
                    close_timeout=5
                ) as ws:
                    self.active_socket = ws
                    backoff = 2  # 연결 성공 시 백오프 초기화
                    logging.info("[Coinbase] ✅ WebSocket Connected. Sending subscribe message...")

                    subscribe_msg = {
                        "type": "subscribe",
                        "product_ids": self.symbols,
                        "channels": ["ticker"]
                    }
                    await ws.send(json.dumps(subscribe_msg))

                    while self.is_running:
                        raw_msg = await ws.recv()
                        tick = self.parse_message(raw_msg)
                        if tick and self.queue:
                            await self.queue.put(tick)

            except asyncio.CancelledError:
                logging.info("[Coinbase] WebSocket task cancelled.")
                break
            except Exception as e:
                if self.is_running:
                    logging.warning(f"[Coinbase] WebSocket connection error: {e}. Reconnecting in {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
            finally:
                self.active_socket = None

    def parse_message(self, message: str) -> Optional[TickData]:
        """코인베이스 Ticker 메시지 파싱"""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            if msg_type != "ticker":
                return None

            price_str = data.get("price")
            if not price_str:
                return None

            price = float(price_str)
            if price <= 0.0:
                return None

            symbol = data.get("product_id", self.symbol)
            volume = float(data.get("last_size", 0.0) or 0.0)
            side = str(data.get("side", "BUY")).upper()

            # 타임스탬프 파싱
            time_str = data.get("time")
            if time_str:
                try:
                    dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                    ts = dt.timestamp()
                except Exception:
                    ts = datetime.now(KST).timestamp()
            else:
                ts = datetime.now(KST).timestamp()

            return TickData(
                symbol=symbol,
                exchange="COINBASE",
                price=price,
                volume=volume,
                side=side,
                timestamp=ts
            )
        except Exception as e:
            logging.debug(f"[Coinbase] Parse message error: {e}")
            return None

    async def stop(self):
        """커넥터 안전 종료"""
        self.is_running = False
        if self.active_socket:
            try:
                await self.active_socket.close()
            except Exception:
                pass
        if self._task and not self._task.done():
            self._task.cancel()
        logging.info("[Coinbase] Connector stopped.")
