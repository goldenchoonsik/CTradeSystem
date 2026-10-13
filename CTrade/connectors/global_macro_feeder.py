# -*- coding: utf-8 -*-
"""
CTrade - Global Macro Feeder (0초 초저지연 무인증 글로벌 매크로 피더)
=============================================================================
1. Pyth Network Hermes WebSocket (wss://hermes.pyth.network/ws)
   - NQ (나스닥 100 선물), ES (S&P 500 선물), US10Y (미 10년물 국채),
     WTI (원유 선물), GOLD (금 선물), USDKRW (실시간 환율)
   - 0.4초(400ms) 초저지연 스트리밍, 무인증(No API Key), 비용 0원
2. Hyperliquid WebSocket (wss://api.hyperliquid.xyz/ws)
   - NVDA-PERP (엔비디아 24시간 무기한 선물) 실시간 틱 수신
   - Pyth NVDA 피드로 자동 듀얼 페일오버(Dual Failover) 탑재
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Callable, List, Any
import aiohttp
import websockets

KST = timezone(timedelta(hours=9))

# 실시간 갱신 후 이 시간(초) 이상 지난 값은 stale 로 간주 (get_latest_price 가 fallback 반환)
DEFAULT_MAX_PRICE_AGE_SEC = 600.0

# Pyth Network 공식 Feed ID 매핑 (32-byte Hex)
PYTH_FEED_IDS = {
    # 글로벌 주가지수 선물
    "NQ": "0x7a303e6a6e927a7c20c0211a7db8e7f8e79d16a133f93a9f076c498d5754b2d2",       # CME E-mini Nasdaq 100
    "ES": "0x48124cf38f48c08170c18d361c471c08003f0b2f56ca12e3e5b12852e6977be4",       # CME E-mini S&P 500
    # 국채 및 금리 선물
    "US10Y": "0x12d59ff1e47da5f9f6b4be463d1a3c5a6104866f851eb3232c4b75a40a5a3a29",    # US 10-Year Treasury Note
    # 원자재 & 귀금속 선물
    "WTI": "0x0ebc8574a4cf0f214f4e7c7a5223053dd49980839e5590924e2ec4c979727ff0",      # WTI Crude Oil
    "GOLD": "0x765d2ba906deb3251f9e93b483c66f6004b3a4a75bf5185958619bc301901a18",     # Gold (XAU/USD)
    # 실시간 외환
    "USDKRW": "0x199329088a8d169b3240be562b7045b7fb5591eb3a77fc63321557adfe6882fe",   # USD/KRW FX
    # 백업용 엔비디아 현물 피드
    "NVDA": "0x75d564c784e1b8b603a111a91e1d0f5e714658e45448375e5b32813155169a84",     # NVIDIA Corp
}

# ID -> 심볼 역방향 룩업 테이블
FEED_ID_TO_SYMBOL = {v.lower(): k for k, v in PYTH_FEED_IDS.items()}


class GlobalMacroFeeder:
    """Pyth Network 및 Hyperliquid 기반 0초 초저지연 매크로 피더"""

    def __init__(self,
                 on_tick_callback: Optional[Callable[[str, float, datetime], None]] = None,
                 poll_interval: float = 12.0,
                 max_price_age_sec: float = DEFAULT_MAX_PRICE_AGE_SEC):
        self.on_tick_callback = on_tick_callback
        self.max_price_age_sec = max_price_age_sec
        self.poll_interval = poll_interval
        self.is_running = False

        # 최신 가격 캐시 { "NQ": 20000.0, "ES": 5500.0, ... }
        self.last_prices: Dict[str, float] = {
            "NQ": 20000.0,
            "ES": 5500.0,
            "US10Y": 110.0,
            "WTI": 75.0,
            "GOLD": 2500.0,
            "USDKRW": 1380.0,
            "NVDA": 125.0
        }
        # 하드코딩 초기값(시드)은 실시간 값이 아니므로 별도로 표시 (실틱 수신 시 해제)
        self._seed_symbols = set(self.last_prices.keys())
        # 최신 타임스탬프 캐시
        self.last_timestamps: Dict[str, datetime] = {}
        # 최신 신뢰구간(Confidence Interval) 캐시
        self.last_confs: Dict[str, float] = {}

        self._tasks: List[asyncio.Task] = []
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=8, connect=4)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        clean_sym = symbol.upper().replace("-PERP", "").replace("=F", "")
        if clean_sym in ("XAU", "GC"): clean_sym = "GOLD"
        if clean_sym in ("CL",): clean_sym = "WTI"
        if clean_sym in ("ZN", "TN"): clean_sym = "US10Y"
        if clean_sym in ("USD_KRW", "USD/KRW"): clean_sym = "USDKRW"
        return clean_sym

    def is_stale(self, symbol: str) -> bool:
        """하드코딩 시드값이거나 마지막 실시간 갱신이 max_price_age_sec 을 넘었으면 True"""
        sym = self._normalize_symbol(symbol)
        if sym in self._seed_symbols:
            return True
        ts = self.last_timestamps.get(sym)
        if ts is None:
            return True
        try:
            age = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        except Exception:
            return True
        return age > self.max_price_age_sec

    def get_latest_price(self, symbol: str, fallback: float = 0.0, allow_stale: bool = False) -> float:
        """심볼별 최신 체결가(종가) 즉시 반환 (비차단 메모리 조회).

        시드(하드코딩 초기값)이거나 유효시간을 넘긴 stale 값이면 fallback 을 반환한다
        (allow_stale=True 면 캐시값 그대로 반환).
        """
        clean_sym = self._normalize_symbol(symbol)
        if not allow_stale and self.is_stale(clean_sym):
            return fallback
        return self.last_prices.get(clean_sym, fallback)

    # 기존 YahooFuturesFeeder 호환 인터페이스
    async def fetch_latest_price(self, symbol: str) -> Optional[float]:
        """실시간(비 stale) 가격이 없으면 None"""
        clean_sym = self._normalize_symbol(symbol)
        if self.is_stale(clean_sym):
            return None
        return self.last_prices.get(clean_sym)

    # -------------------------------------------------------------------------
    # 1. Pyth Network Hermes WebSocket 루프
    # -------------------------------------------------------------------------
    async def _run_pyth_ws(self):
        """Pyth Network Hermes WebSocket 스트리밍 수신"""
        ws_url = "wss://hermes.pyth.network/ws"
        sub_ids = list(PYTH_FEED_IDS.values())
        sub_msg = json.dumps({"type": "subscribe", "ids": sub_ids})

        backoff = 2.0
        while self.is_running:
            try:
                logging.info(f"[GlobalMacroFeeder] 🌐 Pyth Hermes WebSocket 연결 시도: {ws_url}")
                async with websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=15,
                    open_timeout=10,
                    close_timeout=5
                ) as ws:
                    await ws.send(sub_msg)
                    logging.info(f"[GlobalMacroFeeder] ✅ Pyth Hermes 구독 성공 ({len(sub_ids)}개 매크로 피드)")
                    backoff = 2.0

                    while self.is_running:
                        msg_str = await ws.recv()
                        try:
                            data = json.loads(msg_str)
                            msg_type = data.get("type")
                            if msg_type == "price_update":
                                pf = data.get("price_feed", {})
                                feed_id = pf.get("id", "").lower()
                                if not feed_id.startswith("0x"):
                                    feed_id = "0x" + feed_id
                                sym = FEED_ID_TO_SYMBOL.get(feed_id)
                                if sym:
                                    p_info = pf.get("price", {})
                                    raw_price = float(p_info.get("price", 0))
                                    expo = int(p_info.get("expo", 0))
                                    conf = float(p_info.get("conf", 0))
                                    pub_time = int(p_info.get("publish_time", time.time()))

                                    price = raw_price * (10 ** expo)
                                    real_conf = conf * (10 ** expo)
                                    dt = datetime.fromtimestamp(pub_time, tz=timezone.utc).astimezone(KST)

                                    self.last_prices[sym] = price
                                    self._seed_symbols.discard(sym)
                                    self.last_confs[sym] = real_conf
                                    self.last_timestamps[sym] = dt

                                    if self.on_tick_callback:
                                        self.on_tick_callback(sym, price, dt)
                        except Exception as parse_e:
                            logging.debug(f"[GlobalMacroFeeder] Pyth 메시지 파싱 예외: {parse_e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.is_running:
                    logging.warning(f"[GlobalMacroFeeder] ⚠️ Pyth WS 오류 ({e}), {backoff:.1f}초 후 재연결...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 1.5, 30.0)

    # -------------------------------------------------------------------------
    # 2. Hyperliquid WebSocket 루프 (NVDA-PERP 24시간 선물)
    # -------------------------------------------------------------------------
    async def _run_hyperliquid_ws(self):
        """Hyperliquid WebSocket allMids 스트리밍 수신 (NVDA-PERP 추출)"""
        ws_url = "wss://api.hyperliquid.xyz/ws"
        sub_msg = json.dumps({
            "method": "subscribe",
            "subscription": {"type": "allMids"}
        })

        backoff = 2.0
        while self.is_running:
            try:
                logging.info(f"[GlobalMacroFeeder] ⚡ Hyperliquid WebSocket 연결 시도: {ws_url}")
                async with websockets.connect(
                    ws_url,
                    ping_interval=25,
                    ping_timeout=20,
                    open_timeout=10,
                    close_timeout=5
                ) as ws:
                    await ws.send(sub_msg)
                    logging.info("[GlobalMacroFeeder] ✅ Hyperliquid allMids 구독 성공 (NVDA-PERP 추적)")
                    backoff = 2.0

                    while self.is_running:
                        msg_str = await ws.recv()
                        try:
                            data = json.loads(msg_str)
                            channel = data.get("channel")
                            if channel == "allMids":
                                mids = data.get("data", {}).get("mids", {})
                                nvda_price_str = mids.get("NVDA")
                                if nvda_price_str:
                                    price = float(nvda_price_str)
                                    dt = datetime.now(KST)
                                    self.last_prices["NVDA"] = price
                                    self._seed_symbols.discard("NVDA")
                                    self.last_timestamps["NVDA"] = dt
                                    if self.on_tick_callback:
                                        self.on_tick_callback("NVDA", price, dt)
                        except Exception as parse_e:
                            logging.debug(f"[GlobalMacroFeeder] Hyperliquid 메시지 파싱 예외: {parse_e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.is_running:
                    logging.warning(f"[GlobalMacroFeeder] ⚠️ Hyperliquid WS 오류 ({e}), {backoff:.1f}초 후 재연결...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 1.5, 30.0)

    # -------------------------------------------------------------------------
    # 3. 라이프사이클 관리
    # -------------------------------------------------------------------------
    async def run(self):
        """백그라운드 피더 비동기 실행"""
        self.is_running = True
        self._tasks = [
            asyncio.create_task(self._run_pyth_ws(), name="feeder_pyth_ws"),
            asyncio.create_task(self._run_hyperliquid_ws(), name="feeder_hyperliquid_ws")
        ]
        logging.info("[GlobalMacroFeeder] 🚀 글로벌 매크로 실시간 피더 가동 시작")
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self):
        """피더 정상 종료"""
        self.is_running = False
        for t in self._tasks:
            if not t.done():
                t.cancel()
        if self._session and not self._session.closed:
            await self._session.close()
        logging.info("[GlobalMacroFeeder] 🛑 글로벌 매크로 실시간 피더 종료 완료")
