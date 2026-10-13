# -*- coding: utf-8 -*-
"""
CTrade - Yahoo Futures Feeder
Yahoo Finance v8 Chart API를 통해 미국 CME 나스닥 100(NQ=F) 및 S&P 500(ES=F) 선물의
실시간 시세를 비동기로 수신하여 캔들 어그리게이터 및 AI 전략 모듈로 라우팅합니다.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable
import aiohttp
import socket

KST = timezone(timedelta(hours=9))

# 캐시 가격을 폴백으로 돌려줄 수 있는 최대 경과시간(초). 초과 시 None
DEFAULT_MAX_CACHE_AGE_SEC = 900.0

FUTURES_SYMBOL_MAP = {
    "NQ": "NQ=F",      # CME E-mini Nasdaq-100 Futures
    "ES": "ES=F",      # CME E-mini S&P 500 Futures
    "YM": "YM=F",      # CBOT E-mini Dow Futures
    "RTY": "RTY=F"     # CME E-mini Russell 2000 Futures
}


class YahooFuturesFeeder:
    """Yahoo Finance 선물 실시간 시세 비동기 피더"""

    def __init__(self,
                 symbols: Optional[List[str]] = None,
                 poll_interval: float = 12.0,
                 on_tick_callback: Optional[Callable[[str, float, datetime], None]] = None,
                 max_cache_age_sec: float = DEFAULT_MAX_CACHE_AGE_SEC):
        self.max_cache_age_sec = max_cache_age_sec
        self._last_emitted_ts: Dict[str, datetime] = {}
        self._last_emitted_price: Dict[str, float] = {}
        self.symbols = symbols or ["NQ", "ES"]
        self.poll_interval = poll_interval
        self.on_tick_callback = on_tick_callback
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self.last_prices: Dict[str, float] = {}
        self.last_timestamps: Dict[str, datetime] = {}
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=8, connect=4)
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector, headers=self.headers)
        return self._session

    def _cached_price(self, symbol: str) -> Optional[float]:
        """조회 실패 시 캐시 폴백. 타임스탬프가 알려져 있고 유효시간을 넘겼으면 None."""
        sym = symbol.upper()
        price = self.last_prices.get(sym)
        if price is None:
            return None
        ts = self.last_timestamps.get(sym)
        if ts is not None:
            try:
                age = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
            except Exception:
                return None
            if age > self.max_cache_age_sec:
                return None
        return price

    def _emit_if_new(self, sym: str, price: float):
        """캐시 폴백으로 같은/옛 timestamp 틱을 재방출하지 않도록 timestamp 가 전진했을 때만 콜백 호출"""
        sym = sym.upper()
        ts = self.last_timestamps.get(sym)
        if ts is None:
            ts = datetime.now(KST)
        else:
            prev = self._last_emitted_ts.get(sym)
            prev_price = self._last_emitted_price.get(sym)
            # 같은 1분봉 내 가격 갱신은 허용하되, 옛 timestamp/동일 값 재방출은 차단
            if prev is not None and (ts < prev or (ts == prev and price == prev_price)):
                return
        self._last_emitted_ts[sym] = ts
        self._last_emitted_price[sym] = price
        if self.on_tick_callback:
            self.on_tick_callback(sym, price, ts)

    async def fetch_latest_price(self, symbol: str) -> Optional[float]:
        """단일 선물 심볼의 최신 체결가(종가) 비동기 조회"""
        clean_sym = FUTURES_SYMBOL_MAP.get(symbol.upper(), symbol)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_sym}?interval=1m&range=1d"
        session = await self._get_session()

        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logging.warning(f"[YahooFutures] HTTP {resp.status} for {clean_sym}")
                    return self._cached_price(symbol)

                data = await resp.json()
                result = data.get("chart", {}).get("result")
                if not result:
                    return self._cached_price(symbol)

                res = result[0]
                timestamps = res.get("timestamp", [])
                indicators = res.get("indicators", {})
                quote = indicators.get("quote", [{}])[0]
                closes = quote.get("close", [])

                if not timestamps or not closes:
                    return self._cached_price(symbol)

                # 유효한 최신 종가 탐색
                for ts_val, c_val in reversed(list(zip(timestamps, closes))):
                    if c_val is not None and not (isinstance(c_val, float) and c_val != c_val):  # nan check
                        price = float(c_val)
                        ts = datetime.fromtimestamp(ts_val, tz=timezone.utc).astimezone(KST)
                        sym_upper = symbol.upper()
                        self.last_prices[sym_upper] = price
                        self.last_timestamps[sym_upper] = ts
                        return price

        except Exception as e:
            logging.debug(f"[YahooFutures] {clean_sym} 시세 조회 일시 오류 (캐시 유지): {e}")

        return self._cached_price(symbol)

    async def fetch_recent_candles(self, symbol: str, count: int = 200) -> List[Dict]:
        """백필용 최근 1분봉 캔들 조회"""
        clean_sym = FUTURES_SYMBOL_MAP.get(symbol.upper(), symbol)
        range_str = "5d" if count > 390 else "1d"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_sym}?interval=1m&range={range_str}"
        session = await self._get_session()

        candles = []
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("chart", {}).get("result")
                    if result:
                        res = result[0]
                        timestamps = res.get("timestamp", [])
                        quote = res.get("indicators", {}).get("quote", [{}])[0]
                        closes = quote.get("close", [])

                        for ts_val, c_val in zip(timestamps, closes):
                            if c_val is not None and not (isinstance(c_val, float) and c_val != c_val):
                                dt_kst = datetime.fromtimestamp(ts_val, tz=timezone.utc).astimezone(KST)
                                candles.append({
                                    "timestamp": dt_kst,
                                    "close": float(c_val)
                                })
        except Exception as e:
            logging.warning(f"[YahooFutures] {clean_sym} 백필 캔들 조회 실패: {e}")

        if candles:
            candles = candles[-count:]
            if candles:
                self.last_prices[symbol.upper()] = candles[-1]["close"]
                self.last_timestamps[symbol.upper()] = candles[-1]["timestamp"]
        return candles

    async def run(self):
        """실시간 백그라운드 수집 루프"""
        self.is_running = True
        logging.info(f"[YahooFutures] 실시간 선물 피더 시작 (대상: {self.symbols}, 주기: {self.poll_interval}s)")

        # 기동 직후 즉시 1회 초기 시세 조회
        for sym in self.symbols:
            p = await self.fetch_latest_price(sym)
            if p:
                logging.info(f"[YahooFutures] 초기 시세 확보: {sym} = {p:,.2f}")
                self._emit_if_new(sym, p)

        while self.is_running:
            try:
                await asyncio.sleep(self.poll_interval)
                if not self.is_running:
                    break

                for sym in self.symbols:
                    price = await self.fetch_latest_price(sym)
                    if price and price > 0:
                        self._emit_if_new(sym, price)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.warning(f"[YahooFutures] 루프 실행 예외: {e}")
                await asyncio.sleep(3)

        logging.info("[YahooFutures] 실시간 선물 피더 종료")

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> asyncio.Task:
        """비동기 루프에 백그라운드 태스크 등록"""
        if self._task is None or self._task.done():
            self.is_running = True
            if loop:
                self._task = loop.create_task(self.run())
            else:
                self._task = asyncio.create_task(self.run())
        return self._task

    async def stop(self):
        """피더 종료 및 세션 정리"""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
