import asyncio
import logging
import os
import sys
import websockets
import json
import time
from datetime import datetime, timedelta, timezone
from connectors.base_connector import BaseConnector
from parsers.kis_parser import KISParser
from utils.auth import KISAuthManager

_KST = timezone(timedelta(hours=9))

_CTRADE_SYSTEM_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CTRADE_SYSTEM_ROOT not in sys.path:
    sys.path.insert(0, _CTRADE_SYSTEM_ROOT)
try:
    from shared.core.market_utils import is_krx
except ImportError:
    is_krx = None

class KISConnector(BaseConnector):
    def __init__(self, symbols: list, queue: asyncio.Queue, auth_manager: KISAuthManager, is_simulation: bool = False):
        super().__init__(symbols[0] if symbols else "KIS", queue)
        self.symbols = symbols
        self.auth_manager = auth_manager
        self.uri = "ws://ops.koreainvestment.com:31000" if is_simulation else "ws://ops.koreainvestment.com:21000"
        self.reconnect_delay = 5
        self.ws = None

        self.symbol_mapping = {}          # {내부심볼: KIS구독키} (예: 10100 -> A01612)
        self.reverse_symbol_mapping = {}  # {KIS구독키: 내부심볼} (예: A01612 -> 10100)
        try:
            from shared.config.config_manager import get_symbol_info
            for sym in self.symbols:
                info = get_symbol_info(sym) or {}
                kis_sym = info.get("kis_symbol")
                if kis_sym:
                    k_str = str(kis_sym).strip()
                    self.symbol_mapping[sym] = k_str
                    self.reverse_symbol_mapping[k_str] = sym
        except Exception:
            pass
        if "U180" in self.symbols and "U180" not in self.symbol_mapping:
            self.symbol_mapping["U180"] = "201"
            self.reverse_symbol_mapping["201"] = "U180"
        self._front_month_day = None      # 선물 최근월을 해석한 KST 일자 (날짜가 바뀌면 재접속해 월물 교체 반영)

    def _resolve_futures_front_months(self):
        """선물(원장에 underlying/kis_futures_base 보유)의 KIS 최근월 코드를 종목 마스터에서 해석해 구독 매핑을 갱신한다.
        선물은 만기마다 코드가 바뀌어 원장에 박제하지 않는다. 해석 실패 시 그 종목은 구독하지 않는다(추측 코드 금지)."""
        from shared.config.config_manager import get_symbol_info
        from utils.kis_futures_master import resolve_front_month, master_base_of
        has_fut = False
        for sym in self.symbols:
            mb = master_base_of(get_symbol_info(sym) or {})
            if not mb:
                continue
            has_fut = True
            kind, base = mb
            old = self.symbol_mapping.pop(sym, None)
            if old:
                self.reverse_symbol_mapping.pop(old, None)
            code = resolve_front_month(base, kind=kind)
            if code:
                self.symbol_mapping[sym] = code
                self.reverse_symbol_mapping[code] = sym
                logging.info(f"[KIS] 선물 최근월 해석: {sym}({kind} 기초 {base}) -> {code}")
            else:
                logging.error(f"[KIS] 선물 {sym}({kind} 기초 {base}) 최근월 코드를 해석하지 못해 구독을 생략합니다")
        self._front_month_day = datetime.now(_KST).date() if has_fut else None

    async def connect(self):
        if not self.symbols:
            logging.info("[KIS] No KIS symbols to subscribe. Connector idle.")
            return

        self.is_running = True
        backoff = 1.0
        while self.is_running:
            session_start = time.monotonic()
            try:
                approval_key = await self.auth_manager.get_approval_key()
                if not approval_key:
                    logging.error("[KIS] Failed to get approval key. Retrying in 10s...")
                    await asyncio.sleep(10)
                    continue

                async with websockets.connect(self.uri, ping_interval=20, ping_timeout=20) as ws:
                    self.ws = ws
                    logging.info(f"[KIS] Connected to WebSocket: {self.uri}")

                    await asyncio.to_thread(self._resolve_futures_front_months)

                    # 모든 종목을 하나의 소켓에서 구독
                    for symbol in self.symbols:
                        await self._subscribe(ws, approval_key, symbol)

                    while self.is_running:
                        if self._front_month_day and datetime.now(_KST).date() != self._front_month_day:
                            logging.info("[KIS] 날짜 변경 — 선물 최근월 재해석을 위해 재접속합니다")
                            break
                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=60.0)
                            if isinstance(raw_msg, (bytes, bytearray)):
                                raw_msg = bytes(raw_msg).decode('utf-8', errors='replace')
                            if raw_msg.startswith(('0', '1')):
                                # 다건 레코드('0|H0STCNT0|건수|rec^rec...')를 레코드 단위로 분할하여 모두 처리
                                for rec_msg in KISParser.split_records(raw_msg):
                                    parts = rec_msg.split('|')
                                    if len(parts) < 4:
                                        continue
                                    fields = parts[3].split('^')
                                    tr_id = parts[1]
                                    if tr_id in ["HDFSCNT0", "HDFSASP0"] and len(fields) > 1:
                                        raw_symbol = fields[1]
                                    elif tr_id in ["H0IFCNT0", "H0IFASP0", "H0ZFCNT0", "H0ZFASP0"]:
                                        raw_symbol = fields[0] if fields[0] != '' else (fields[1] if len(fields) > 1 else '')
                                    else:
                                        raw_symbol = fields[0]

                                    # KIS 실시간 심볼 코드를 내부 등록 심볼(예: A01612 -> 10100)로 역매핑
                                    symbol = self.reverse_symbol_mapping.get(raw_symbol, raw_symbol)

                                    parsed_data = self.parse_message(rec_msg, symbol)
                                    if parsed_data:
                                        await self.queue.put(parsed_data)
                            else:
                                msg_dict = json.loads(raw_msg)
                                if msg_dict.get("header", {}).get("tr_id") == "PINGPONG":
                                    await ws.send(raw_msg)
                                elif "msg1" in msg_dict.get("body", {}):
                                    logging.info(f"[KIS] Server Msg: {msg_dict['body']['msg1']} ({msg_dict['header'].get('tr_key')})")

                        except asyncio.TimeoutError:
                            pass
            except Exception as e:
                logging.error(f"[KIS] Connection Error: {e}", exc_info=True)
            finally:
                self.ws = None
            if not self.is_running:
                break
            backoff = self.next_backoff(backoff, time.monotonic() - session_start,
                                        base=float(self.reconnect_delay), cap=60.0)
            await asyncio.sleep(backoff)

    async def stop(self):
        self.is_running = False
        ws = self.ws
        if ws is not None:
            try:
                await ws.close()
                logging.info("[KIS] Websocket Connection Closed.")
            except Exception as e:
                logging.error(f"[KIS] Error closing KIS websocket: {e}")

    async def _subscribe(self, ws, approval_key: str, symbol: str):
        # 1. 국내 지수선물 여부 판별 (market_type == 'KRX_FUTURES' 또는 10100/A01612 등)
        is_futures = False
        is_stock_futures = False
        needs_front_month = False
        try:
            from shared.config.config_manager import get_symbol_info
            from utils.kis_futures_master import master_base_of
            info = get_symbol_info(symbol) or {}
            if "FUTURES" in str(info.get("market_type", "")).upper():
                is_futures = True
            is_stock_futures = bool(info.get("underlying"))   # 개별주식선물 (원장 underlying 보유)
            needs_front_month = master_base_of(info) is not None
        except Exception:
            pass
        if not is_futures and (symbol in ("10100", "A01612") or symbol.startswith(("101V", "101S", "A01"))):
            is_futures = True

        # 1-1. 국내 지수/업종 여부 판별 (market_type == 'KRX_INDEX' 또는 U180 등)
        is_index = False
        try:
            from shared.config.config_manager import get_symbol_info
            info = get_symbol_info(symbol) or {}
            if "INDEX" in str(info.get("market_type", "")).upper():
                is_index = True
        except Exception:
            pass
        if not is_index and symbol.startswith("U18"):
            is_index = True

        if is_stock_futures:
            tr_key = self.symbol_mapping.get(symbol)
            if not tr_key:
                logging.error(f"[KIS] 주식선물 {symbol}: 최근월 코드 미해석 — 구독 생략")
                return
            tr_list = ["H0ZFCNT0", "H0ZFASP0"]
        elif is_futures:
            tr_key = self.symbol_mapping.get(symbol)
            if not tr_key:
                if needs_front_month:
                    logging.error(f"[KIS] 지수선물 {symbol}: 최근월 코드 미해석 — 구독 생략")
                    return
                tr_key = symbol
            tr_list = ["H0IFCNT0", "H0IFASP0"]
        elif is_index:
            tr_list = ["H0UPCNT0"]
            tr_key = self.symbol_mapping.get(symbol, "201" if symbol in ("U180", "201") else symbol)
        else:
            # 2. 국내 vs 해외 자동 판별 (symbols_registry.json/마스터 조회 기반)
            if is_krx is not None:
                is_overseas = not is_krx({'SYMBOL': symbol})
            else:
                is_overseas = not (symbol.isdigit() and len(symbol) == 6)

            if is_overseas:
                # 해외주식 (미국 나스닥/NYSE)
                tr_list = ["HDFSCNT0", "HDFSASP0"]
                tr_key = symbol if symbol.startswith(("DNAS", "DNYS", "DAMS")) else f"DNAS{symbol}"
            else:
                # 국내주식 (KOSPI200 ETF 등)
                tr_list = ["H0STCNT0", "H0STASP0"]
                tr_key = symbol

        for tr_id in tr_list:
            packet = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8"
                },
                "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}}
            }
            await ws.send(json.dumps(packet))
            logging.info(f"[KIS] Subscribed: {tr_id} -> {tr_key} (mapped from {symbol})")
            await asyncio.sleep(0.2)

    def parse_message(self, message: str, symbol: str):
        return KISParser.parse_message_str(message, symbol)