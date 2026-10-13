"""
BinanceFuturesRadar — 바이낸스 선물 Combined Stream 전용 수신기

역할:
  - 업비트 KRW 종목 중 바이낸스 USDT-M 선물이 존재하는 210종목의
    markPrice 를 Combined Stream 1개로 실시간 수신
  - 메인 asyncio.Queue / consumer_loop와 완전히 분리
  - 수신 즉시 공유 딕셔너리(futures_prices)만 갱신: {baseAsset: price_float}
  - ScalpingTargetRadar 가 이 딕셔너리를 읽어 업비트 vs 선물 괴리율 계산

참고:
  - markPrice 스트림은 Binance가 1초마다 push
  - Combined Stream URL: wss://fstream.binance.com/stream?streams=s1@markPrice/s2@markPrice/...
  - 단일 연결에 최대 200 스트림 권장 (Binance docs), 210은 분할 불필요한 수준
"""

import asyncio
import websockets
import json
import logging
import time
import aiohttp

# 스트림당 최대 종목 수 (Binance 권고 200, 부하 분산을 위해 100으로 축소)
_MAX_STREAMS_PER_CONN = 50

class BinanceFuturesRadar:
    """
    바이낸스 선물 Combined markPrice 스트림 수신기.
    단일 WebSocket으로 최대 200종목을 구독하며,
    futures_prices 딕셔너리를 실시간 갱신한다.
    """

    FUTURES_WS_BASE = "wss://fstream.binance.com/market/stream?streams="

    def __init__(self):
        # 외부에서 직접 읽는 공유 테이블 {baseAsset: {"price": float, "ts": float}}
        # 예: "WLD" -> {"price": 1.234, "ts": 1700000000.0}
        self.futures_prices: dict = {}
        self.is_running = False
        self._tasks: list = []
        # 사용 가능한 선물 심볼 캐시 (최초 1회 API 조회)
        self._available_perp_assets: set = set()
        self._last_warn_time: dict = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_available_symbols(self) -> set:
        """바이낸스 선물 거래 가능 심볼 조회 (USDT-M Perpetual만)"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://fapi.binance.com/fapi/v1/exchangeInfo",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    info = await r.json(content_type=None)
            assets = {
                s["baseAsset"]
                for s in info.get("symbols", [])
                if s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"
            }
            logging.info(f"[BinanceFuturesRadar] 선물 거래 가능 종목: {len(assets)}개")
            return assets
        except Exception as e:
            logging.error(f"[BinanceFuturesRadar] exchangeInfo 조회 실패: {e}")
            return set()

    def get_futures_price(self, base_asset: str) -> float:
        """
        종목 기준 자산(base asset)으로 최신 선물 가격 반환.
        예: get_futures_price("WLD") -> 1.234
        """
        entry = self.futures_prices.get(base_asset)
        if entry is None:
            return 0.0
        # 10초 이상 오래된 데이터는 신뢰하지 않음
        if time.time() - entry["ts"] > 10.0:
            return 0.0
        return entry["price"]

    async def start(self, upbit_krw_symbols: list):
        """
        업비트 KRW 종목 리스트를 받아 선물이 존재하는 종목만 구독 시작.
        upbit_krw_symbols: ["KRW-WLD", "KRW-NEAR", ...]
        """
        self._available_perp_assets = await self.fetch_available_symbols()
        if not self._available_perp_assets:
            logging.warning("[BinanceFuturesRadar] 선물 심볼 조회 실패 — 비활성화")
            return

        # KRW-XXX → XXX 추출 후 선물 존재 여부 필터
        candidates = [
            sym.split("-")[1]
            for sym in upbit_krw_symbols
            if sym.startswith("KRW-")
        ]
        targets = [a for a in candidates if a in self._available_perp_assets]
        logging.info(f"[BinanceFuturesRadar] 구독 대상: {len(targets)}종목 (업비트 KRW {len(upbit_krw_symbols)}개 중 선물 보유)")

        if not targets:
            return

        # 200개 단위로 분할하여 연결 (여유 있지만 확장성 확보)
        chunks = [targets[i:i+_MAX_STREAMS_PER_CONN] for i in range(0, len(targets), _MAX_STREAMS_PER_CONN)]
        self.is_running = True
        for chunk in chunks:
            task = asyncio.create_task(self._stream_loop(chunk))
            self._tasks.append(task)

    async def stop(self):
        self.is_running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _stream_loop(self, assets: list):
        """Combined markPrice 스트림 연결 루프 (재연결 및 실시간 지연 측정 포함)"""
        stream_parts = [f"{a.lower()}usdt@markPrice" for a in assets]
        uri = self.FUTURES_WS_BASE + "/".join(stream_parts)
        backoff = 1
        session_label = ",".join(assets[:3]) + f"...({len(assets)}종목)"

        while self.is_running:
            try:
                async with websockets.connect(
                    uri,
                    ping_interval=20,
                    open_timeout=15,
                    max_size=10 * 1024 * 1024   # 10MB
                ) as ws:
                    logging.info(f"[BinanceFuturesRadar] Connected — {len(assets)}종목 markPrice 구독")
                    backoff = 1  # 연결 성공 시 초기화
                    while self.is_running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
                        except asyncio.TimeoutError:
                            logging.warning(f"[BinanceFuturesRadar] [{session_label}] 수신 타임아웃 — 재연결")
                            break

                        res = self._handle_message(raw)
                        if res:
                            base, price, event_time = res
                            if event_time > 0:
                                now_ms = time.time() * 1000
                                latency = now_ms - event_time

                                # 3초(3000ms) 이상 지연 시 10초 쿨다운 적용하여 경고 로그
                                if latency >= 3000.0:
                                    now_sec = time.time()
                                    last_t = self._last_warn_time.get(session_label, 0)
                                    if now_sec - last_t >= 10.0:
                                        self._last_warn_time[session_label] = now_sec
                                        logging.warning(
                                            f"[BinanceFuturesRadar] [{session_label}] 심각한 수신 지연 감지! "
                                            f"종목: {base}, 지연: {latency:.1f}ms"
                                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.is_running:
                    logging.error(f"[BinanceFuturesRadar] [{session_label}] 연결 오류: {e} — {backoff}초 후 재시도")
                    await asyncio.sleep(backoff)
                    backoff = min(60, backoff * 2)

    def _handle_message(self, raw: str):
        """
        markPrice 메시지 파싱 후 futures_prices 갱신 및 (symbol, price, event_time) 반환.
        메시지 형태:
          {"stream":"wldusdt@markPrice","data":{"e":"markPriceUpdate","s":"WLDUSDT","p":"1.234","E":1700000000000,...}}
        """
        try:
            msg = json.loads(raw)
            stream = msg.get("stream", "")
            data = msg.get("data", {})

            if "@markPrice" not in stream:
                return None

            symbol_full = data.get("s", "")       # e.g. "WLDUSDT"
            mark_price_str = data.get("p", "")     # mark price
            event_time = data.get("E", 0)          # event time

            if not symbol_full or not mark_price_str:
                return None

            # "WLDUSDT" → "WLD"
            if symbol_full.endswith("USDT"):
                base = symbol_full[:-4]
            else:
                return None

            price = float(mark_price_str)
            if price > 0:
                self.futures_prices[base] = {"price": price, "ts": time.time()}

            return base, price, event_time

        except Exception:
            return None
