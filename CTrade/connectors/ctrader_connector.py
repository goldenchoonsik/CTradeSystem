# -*- coding: utf-8 -*-
"""
CTrade - cTrader Open API 실시간 호가 커넥터
==============================================
cTrader Open API 의 **실시간 스팟 스트림**(`ProtoOASpotEvent`)을 구독해 참조 자산의 틱을
엔진으로 흘려보낸다.

[T-6] 배경
----------
종전에는 cTrader 가 **과거 봉 조회**(`ProtoOAGetTrendbars`)로만 쓰였다. 수집처 원장에는
`"channel": "ProtoOASpotEvent"` 로 실시간 연결이 선언돼 있었지만 그 문자열을 구독하는 코드가
어디에도 없었고, 그 결과 cTrader 참조로 학습한 모델은 실전에서 그 피처를 만들 수 없었다.

설계 원칙
---------
- **심볼 ID 표를 코드에 두지 않는다.** 접속 후 `ProtoOASymbolsListReq` 로 받은 목록에서
  이름을 맞춰 찾는다. 브로커가 심볼을 재배치해도 따라간다.
  (종전 `scripts/fetch_ctrader_history.py` 는 symbol_id/digits 를 7종 하드코딩했다)
- 이름 맞춤은 **정규화 비교**다: 공백·밑줄·하이픈을 지우고 대문자로 비교한다.
  원장의 `US_TECH_100` 과 브로커의 `US TECH 100` 이 같은 것으로 이어진다.
  원장 `live_connection.symbol` 에 브로커 표기를 적어 두면 그쪽이 우선한다.
- 자격 증명이 없거나 접속이 끊겨도 **엔진을 죽이지 않는다** — 로그를 남기고 재시도한다.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional

KST = timezone(timedelta(hours=9))

# cTrader 프로토콜의 고정 상대가격 배율. 종목별 값이 아니라 프로토콜 규약이다
# (ProtoOASpotEvent / ProtoOATrendbar 의 정수 가격은 항상 10^5 로 나눈다).
PRICE_SCALE = 100_000.0

# 재접속 백오프 (초)
_BACKOFF_BASE = 2.0
_BACKOFF_CAP = 60.0

_REQUIRED_ENV = ("CTRADER_CLIENT_ID", "CTRADER_SECRET", "CTRADER_ACCESS_TOKEN", "CTRADE_ACCOUNT_ID")


def normalize_symbol_name(name: str) -> str:
    """브로커 표기와 원장 표기를 맞추기 위한 정규화 (공백/밑줄/하이픈 제거 + 대문자)."""
    return "".join(ch for ch in str(name or "").upper() if ch.isalnum())


def credentials_available() -> bool:
    """cTrader 접속에 필요한 환경변수가 모두 있는지."""
    return all(os.environ.get(k) for k in _REQUIRED_ENV)


class CTraderConnector:
    """cTrader 실시간 호가 구독기.

    Args:
        symbols: 구독할 **원장 심볼** 목록 (예: ["US_TECH_100", "US_500"])
        on_tick_callback: ``(원장심볼, 가격, 시각)`` 으로 호출된다.
            `GlobalMacroFeeder` 와 같은 모양이라 엔진 배선이 대칭이 된다.
        broker_symbol_map: 원장 심볼 → 브로커 표기 (원장 live_connection 에서 넘긴다).
        use_demo: 데모 서버 사용 여부 (기본: 환경변수 CTRADER_HOST_KIND 가 'live' 가 아니면 데모)
    """

    def __init__(self,
                 symbols: Iterable[str],
                 on_tick_callback: Optional[Callable[[str, float, datetime], None]] = None,
                 broker_symbol_map: Optional[Dict[str, str]] = None,
                 use_demo: Optional[bool] = None,
                 log: Optional[Callable[[str], None]] = None):
        self.symbols: List[str] = [str(s).strip().upper() for s in symbols if str(s).strip()]
        self.on_tick_callback = on_tick_callback
        self.broker_symbol_map = {str(k).upper(): str(v) for k, v in (broker_symbol_map or {}).items()}
        self.log = log or logging.info
        self.use_demo = (os.environ.get("CTRADER_HOST_KIND", "demo").lower() != "live") \
            if use_demo is None else bool(use_demo)

        self.is_running = False
        self.is_authenticated = False
        self.last_prices: Dict[str, float] = {}
        self.last_tick_at: Dict[str, datetime] = {}

        self._client = None
        self._thread: Optional[threading.Thread] = None
        self._symbol_id_to_ledger: Dict[int, str] = {}
        self._subscribed: List[str] = []
        self._backoff = _BACKOFF_BASE
        self._connected_at: Optional[float] = None

    # ── 공개 API ────────────────────────────────────────────────────────────
    def start(self) -> bool:
        """백그라운드 스레드에서 접속을 시작한다. 자격 증명이 없으면 False 를 돌려주고 아무것도 하지 않는다."""
        if not self.symbols:
            self.log("[cTrader] 구독할 심볼이 없어 시작하지 않습니다")
            return False
        if not credentials_available():
            missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
            self.log(f"[cTrader] 환경변수 누락으로 실시간 구독을 시작할 수 없습니다: {', '.join(missing)}")
            return False
        if self.is_running:
            return True

        self.is_running = True
        self._thread = threading.Thread(target=self._run_reactor, name="ctrader-feed", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self.is_running = False
        try:
            if self._client is not None:
                self._client.stopService()
        except Exception:
            pass

    def get_latest_price(self, ledger_symbol: str) -> Optional[float]:
        return self.last_prices.get(str(ledger_symbol).upper())

    def health(self) -> Dict[str, object]:
        """실측 상태 — 무엇을 구독 중이고 마지막 틱이 언제였는지."""
        now = datetime.now(KST)
        return {
            "running": self.is_running,
            "authenticated": self.is_authenticated,
            "subscribed": list(self._subscribed),
            "last_tick_age_sec": {
                s: int((now - t).total_seconds()) for s, t in self.last_tick_at.items()
            },
        }

    # ── 내부 구현 ───────────────────────────────────────────────────────────
    def _run_reactor(self):
        """Twisted 리액터를 전용 스레드에서 돌린다 (엔진은 asyncio 라 서로 간섭하지 않게 분리)."""
        try:
            from ctrader_open_api import Client, TcpProtocol
            from ctrader_open_api.endpoints import EndPoints
            from twisted.internet import reactor
        except ImportError as e:
            self.log(f"[cTrader] 라이브러리를 불러올 수 없습니다: {e}")
            self.is_running = False
            return

        host = EndPoints.PROTOBUF_DEMO_HOST if self.use_demo else EndPoints.PROTOBUF_LIVE_HOST
        self._client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)

        self.log(f"[cTrader] 실시간 구독 시작 — {host}:{EndPoints.PROTOBUF_PORT}, 심볼 {self.symbols}")
        self._client.startService()
        if not reactor.running:
            reactor.run(installSignalHandlers=False)

    def _on_connected(self, client):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAApplicationAuthReq
        self._connected_at = time.time()
        req = ProtoOAApplicationAuthReq()
        req.clientId = os.environ["CTRADER_CLIENT_ID"]
        req.clientSecret = os.environ["CTRADER_SECRET"]
        client.send(req)

    def _on_disconnected(self, client, reason):
        self.is_authenticated = False
        session = time.time() - (self._connected_at or time.time())
        # 접속 직후 끊기는 경우의 1초 핫루프를 막는다 (base_connector 와 같은 규칙)
        self._backoff = _BACKOFF_BASE if session >= 30 else min(_BACKOFF_CAP, max(_BACKOFF_BASE, self._backoff * 2))
        self.log(f"[cTrader] 연결 끊김 ({reason}) — {self._backoff:.0f}초 뒤 재시도")

    def _on_message(self, client, message):
        from ctrader_open_api import Protobuf
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAAccountAuthReq, ProtoOAAccountAuthRes, ProtoOAApplicationAuthRes,
            ProtoOASpotEvent, ProtoOASubscribeSpotsReq, ProtoOASymbolsListReq, ProtoOASymbolsListRes,
        )
        try:
            payload = Protobuf.extract(message)
        except Exception:
            return

        if isinstance(payload, ProtoOAApplicationAuthRes):
            req = ProtoOAAccountAuthReq()
            req.ctidTraderAccountId = int(os.environ["CTRADE_ACCOUNT_ID"])
            req.accessToken = os.environ["CTRADER_ACCESS_TOKEN"]
            client.send(req)
            return

        if isinstance(payload, ProtoOAAccountAuthRes):
            self.is_authenticated = True
            req = ProtoOASymbolsListReq()
            req.ctidTraderAccountId = int(os.environ["CTRADE_ACCOUNT_ID"])
            client.send(req)
            return

        if isinstance(payload, ProtoOASymbolsListRes):
            self._subscribe_resolved(client, payload)
            return

        if isinstance(payload, ProtoOASpotEvent):
            self._handle_spot(payload)
            return

    def _subscribe_resolved(self, client, symbols_res):
        """브로커 심볼 목록에서 원장 심볼에 해당하는 id 를 찾아 구독한다 (id 표 없음)."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASubscribeSpotsReq

        by_norm = {}
        for s in symbols_res.symbol:
            by_norm[normalize_symbol_name(s.symbolName)] = s

        wanted_ids, missing = [], []
        for ledger_sym in self.symbols:
            broker_name = self.broker_symbol_map.get(ledger_sym, ledger_sym)
            found = by_norm.get(normalize_symbol_name(broker_name)) or by_norm.get(normalize_symbol_name(ledger_sym))
            if found is None:
                missing.append(ledger_sym)
                continue
            self._symbol_id_to_ledger[int(found.symbolId)] = ledger_sym
            wanted_ids.append(int(found.symbolId))

        if missing:
            self.log(f"[cTrader] 브로커 심볼 목록에서 찾지 못함: {missing} "
                     f"(원장 live_connection.symbol 에 브로커 표기를 적어 주세요)")
        if not wanted_ids:
            self.log("[cTrader] 구독할 심볼을 하나도 찾지 못해 구독하지 않습니다")
            return

        req = ProtoOASubscribeSpotsReq()
        req.ctidTraderAccountId = int(os.environ["CTRADE_ACCOUNT_ID"])
        req.symbolId.extend(wanted_ids)
        client.send(req)
        self._subscribed = [self._symbol_id_to_ledger[i] for i in wanted_ids]
        self.log(f"[cTrader] 실시간 구독 완료: {self._subscribed}")

    def _handle_spot(self, evt):
        ledger_sym = self._symbol_id_to_ledger.get(int(evt.symbolId))
        if not ledger_sym:
            return
        bid = float(evt.bid) / PRICE_SCALE if evt.HasField("bid") else None
        ask = float(evt.ask) / PRICE_SCALE if evt.HasField("ask") else None
        prices = [p for p in (bid, ask) if p]
        if not prices:
            return
        price = sum(prices) / len(prices)          # 양쪽이 다 오면 중간값, 한쪽만 오면 그 값
        now = datetime.now(KST)

        self.last_prices[ledger_sym] = price
        self.last_tick_at[ledger_sym] = now
        if self.on_tick_callback:
            try:
                self.on_tick_callback(ledger_sym, price, now)
            except Exception as e:
                self.log(f"[cTrader] 틱 콜백 오류 ({ledger_sym}): {e}")
