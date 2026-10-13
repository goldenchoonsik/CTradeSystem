#!/usr/bin/env python3
"""
cTrader Open API 연결 및 인증 동작 테스트 스크립트
- 환경변수 기반 자격 증명 로드 (CTRADE_ACCOUNT_ID, CTRADER_CLIENT_ID, CTRADER_SECRET, CTRADER_ACCESS_TOKEN)
- SSL TCP 연결 (demo.ctraderapi.com:5035)
- Application 인증 (ProtoOAApplicationAuthReq)
- Account 인증 (ProtoOAAccountAuthReq)
- Trader 상세 조회 (ProtoOATraderReq: 잔고, 브로커, 레버리지)
- 나스닥 관련 심볼 탐색 (ProtoOASymbolsListReq: NAS100, US100 등)
"""

import os
import sys
from twisted.internet import reactor
from ctrader_open_api import Client, TcpProtocol, Protobuf
from ctrader_open_api.endpoints import EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOATraderReq,
    ProtoOATraderRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
    ProtoOASymbolByIdReq,
    ProtoOASymbolByIdRes,
    ProtoOAGetTrendbarsReq,
    ProtoOAGetTrendbarsRes,
    ProtoOAErrorRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod



def print_step(step: str, text: str, status: str = "INFO"):
    badge = {
        "INFO": "\033[94m[INFO]\033[0m",
        "OK": "\033[92m[SUCCESS]\033[0m",
        "WARN": "\033[93m[WARN]\033[0m",
        "FAIL": "\033[91m[FAIL]\033[0m",
    }.get(status, f"[{status}]")
    print(f"{badge} {step}: {text}")


class CTraderTester:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

        # 환경변수 로드
        self.client_id = os.environ.get("CTRADER_CLIENT_ID")
        self.client_secret = os.environ.get("CTRADER_SECRET")
        self.access_token = os.environ.get("CTRADER_ACCESS_TOKEN")
        self.account_id_str = os.environ.get("CTRADE_ACCOUNT_ID")
        self.trader_no = os.environ.get("CTRADER_NO")

        if not all([self.client_id, self.client_secret, self.access_token, self.account_id_str]):
            print_step("ENV", "필수 환경변수가 누락되었습니다.", "FAIL")
            print("  - CTRADER_CLIENT_ID:", "SET" if self.client_id else "MISSING")
            print("  - CTRADER_SECRET:", "SET" if self.client_secret else "MISSING")
            print("  - CTRADER_ACCESS_TOKEN:", "SET" if self.access_token else "MISSING")
            print("  - CTRADE_ACCOUNT_ID:", "SET" if self.account_id_str else "MISSING")
            sys.exit(1)

        self.account_id = int(self.account_id_str)
        self.client = Client(self.host, self.port, TcpProtocol)
        self.client.setConnectedCallback(self.on_connected)
        self.client.setDisconnectedCallback(self.on_disconnected)
        self.client.setMessageReceivedCallback(self.on_message_received)

        self.results = {
            "connected": False,
            "app_auth": False,
            "account_auth": False,
            "trader_info": None,
            "nasdaq_symbols": [],
        }

    def start(self):
        print("=" * 65)
        print("🚀 cTrader Open API 연결 및 계좌 검증 시작")
        print(f"• 접속 호스트: {self.host}:{self.port}")
        print(f"• 계좌 ID (ctidTraderAccountId): {self.account_id}")
        print(f"• 트레이더 계좌번호 (CTRADER_NO): {self.trader_no}")
        print("=" * 65)
        
        # 타임아웃 타이머 설정 (15초)
        reactor.callLater(15, self.on_timeout)
        self.client.startService()
        reactor.run()

    def stop(self, success: bool = True):
        if reactor.running:
            reactor.stop()
        print("\n" + "=" * 65)
        if success:
            print("🎉 검증 완료: cTrader Open API 정상 연동 확인!")
        else:
            print("❌ 검증 실패: 로그를 확인해 주세요.")
        print("=" * 65)

    def on_timeout(self):
        print_step("TIMEOUT", "15초 타임아웃 초과 - 서버 응답 없음", "FAIL")
        self.stop(success=False)

    def on_connected(self, client):
        self.results["connected"] = True
        print_step("STEP 1", f"SSL 소켓 연결 성공 ({self.host}:{self.port})", "OK")

        # Step 1: Application Authorization 전송
        app_auth_req = ProtoOAApplicationAuthReq()
        app_auth_req.clientId = self.client_id
        app_auth_req.clientSecret = self.client_secret

        d = self.client.send(app_auth_req)
        d.addErrback(self.on_error, "ApplicationAuthReq")

    def on_disconnected(self, client, reason):
        print_step("DISCONNECT", f"서버 연결 해제: {reason}", "WARN")

    def on_error(self, failure, context):
        print_step("ERROR", f"[{context}] 요청 실패: {failure.getErrorMessage()}", "FAIL")
        self.stop(success=False)

    def on_message_received(self, client, message):
        payload = Protobuf.extract(message)
        payload_type = message.payloadType

        # 1. Application Auth 응답
        if isinstance(payload, ProtoOAApplicationAuthRes):
            self.results["app_auth"] = True
            print_step("STEP 2", "Application 인증 완료 (ProtoOAApplicationAuthRes)", "OK")

            # Step 2: Account Authorization 전송
            acc_auth_req = ProtoOAAccountAuthReq()
            acc_auth_req.ctidTraderAccountId = self.account_id
            acc_auth_req.accessToken = self.access_token

            d = self.client.send(acc_auth_req)
            d.addErrback(self.on_error, "AccountAuthReq")

        # 2. Account Auth 응답
        elif isinstance(payload, ProtoOAAccountAuthRes):
            self.results["account_auth"] = True
            print_step("STEP 3", f"계좌 인증 완료 (ctidTraderAccountId: {payload.ctidTraderAccountId})", "OK")

            # Step 3-1: Trader 정보 요청
            trader_req = ProtoOATraderReq()
            trader_req.ctidTraderAccountId = self.account_id
            d1 = self.client.send(trader_req)
            d1.addErrback(self.on_error, "TraderReq")

            # Step 3-2: 심볼 목록 요청
            sym_req = ProtoOASymbolsListReq()
            sym_req.ctidTraderAccountId = self.account_id
            sym_req.includeArchivedSymbols = False
            d2 = self.client.send(sym_req)
            d2.addErrback(self.on_error, "SymbolsListReq")

        # 3. Trader 정보 수신
        elif isinstance(payload, ProtoOATraderRes):
            trader = payload.trader
            money_digits = getattr(trader, "moneyDigits", 2)
            balance = trader.balance / (10 ** money_digits)
            leverage = trader.leverageInCents / 100.0 if trader.leverageInCents else 0.0
            broker = getattr(trader, "brokerName", "cTrader")

            self.results["trader_info"] = {
                "balance": balance,
                "leverage": leverage,
                "broker": broker,
                "traderLogin": trader.traderLogin,
            }
            print_step(
                "STEP 4",
                f"계좌 프로필 조회 성공 | 브로커: {broker} | 계좌번호: {trader.traderLogin} | 잔고: {balance:,.2f} | 레버리지: 1:{leverage:.0f}",
                "OK",
            )

        # 4. 심볼 목록 수신
        elif isinstance(payload, ProtoOASymbolsListRes):
            symbols = list(payload.symbol)
            nasdaq_sym = None

            for sym in symbols:
                if sym.symbolName == "US TECH 100":
                    nasdaq_sym = sym
                    break

            if not nasdaq_sym:
                for sym in symbols:
                    if "TECH" in sym.symbolName.upper() or "NAS" in sym.symbolName.upper():
                        nasdaq_sym = sym
                        break

            if nasdaq_sym:
                print_step("STEP 5", f"전체 {len(symbols)}개 심볼 수신 완료 | 타겟 심볼 식별: [{nasdaq_sym.symbolName}] (ID: {nasdaq_sym.symbolId})", "OK")
                self.target_symbol_id = nasdaq_sym.symbolId
                self.target_symbol_name = nasdaq_sym.symbolName

                # Step 5-1: 심볼 상세 스펙(digits, lotSize 등) 요청
                from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolByIdReq
                sym_spec_req = ProtoOASymbolByIdReq()
                sym_spec_req.ctidTraderAccountId = self.account_id
                sym_spec_req.symbolId.append(self.target_symbol_id)

                d = self.client.send(sym_spec_req)
                d.addErrback(self.on_error, "SymbolByIdReq")
            else:
                print_step("STEP 5", f"전체 {len(symbols)}개 심볼 수신 완료 (나스닥 심볼 미발견)", "WARN")
                self.stop(success=True)

        # 5. 심볼 상세 스펙 수신
        elif isinstance(payload, ProtoOASymbolByIdRes):
            for s in payload.symbol:
                if s.symbolId == self.target_symbol_id:
                    self.target_digits = s.digits
                    self.target_lot_size = s.lotSize
                    self.target_min_vol = s.minVolume
                    print_step(
                        "STEP 6",
                        f"[{self.target_symbol_name}] 스펙 조회 완료 | Digits: {s.digits} | 1 Lot: {s.lotSize:,} | 최소거래량: {s.minVolume}",
                        "OK",
                    )

            # Step 6-1: 최근 1분봉(M1) 데이터 요청
            from datetime import datetime, timezone, timedelta
            from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetTrendbarsReq

            now = datetime.now(timezone.utc)
            to_time = int(now.timestamp() * 1000)
            from_time = int((now - timedelta(hours=6)).timestamp() * 1000)

            tb_req = ProtoOAGetTrendbarsReq()
            tb_req.ctidTraderAccountId = self.account_id
            tb_req.symbolId = self.target_symbol_id
            tb_req.period = ProtoOATrendbarPeriod.M1
            tb_req.fromTimestamp = from_time
            tb_req.toTimestamp = to_time
            tb_req.count = 5

            print_step("STEP 7", f"[{self.target_symbol_name}] 최근 1분봉(M1) 데이터 요청 전송", "INFO")
            d = self.client.send(tb_req)
            d.addErrback(self.on_error, "GetTrendbarsReq")

        # 6. Trendbars 응답 수신
        elif isinstance(payload, ProtoOAGetTrendbarsRes):
            bars = list(payload.trendbar)
            print_step("STEP 8", f"[{self.target_symbol_name}] 1분봉(M1) 데이터 {len(bars)}개 수신 성공!", "OK")

            from datetime import datetime, timezone
            print("\n" + "-" * 75)
            print(f"📈 [{self.target_symbol_name}] 최근 1분봉 OHLCV 실측 데이터:")
            digits = getattr(self, "target_digits", 2)
            # Spotware 공식 Protobuf 가격 스케일은 100,000.0
            price_div = 100000.0

            for b in bars[-5:]:
                bar_time = datetime.fromtimestamp(b.utcTimestampInMinutes * 60, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
                low = b.low / price_div
                open_p = (b.low + b.deltaOpen) / price_div
                high = (b.low + b.deltaHigh) / price_div
                close_p = (b.low + b.deltaClose) / price_div
                vol = b.volume
                print(f"  • [{bar_time}] O: {open_p:,.{digits}f} | H: {high:,.{digits}f} | L: {low:,.{digits}f} | C: {close_p:,.{digits}f} | Vol: {vol}")
            print("-" * 75)

            # 모든 검증 단계 완벽 성공 -> 정상 종료
            self.stop(success=True)


        # 에러 응답
        elif isinstance(payload, ProtoOAErrorRes):
            print_step("SERVER_ERROR", f"에러코드: {payload.errorCode} | 메시지: {payload.description}", "FAIL")
            self.stop(success=False)




if __name__ == "__main__":
    # 기본은 DEMO 호스트 접속, 커맨드라인 인자로 'live' 지정 가능
    host = EndPoints.PROTOBUF_DEMO_HOST
    if len(sys.argv) > 1 and sys.argv[1].lower() == "live":
        host = EndPoints.PROTOBUF_LIVE_HOST

    tester = CTraderTester(host=host, port=EndPoints.PROTOBUF_PORT)
    tester.start()
