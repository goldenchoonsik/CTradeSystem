#!/usr/bin/env python3
"""
cTrader Open API 과거 데이터 대량 수집기 (Bronze Ingestor)
- 지원 자산 (6대 대용 지표 + 나스닥):
  1. US_TECH_100: 나스닥 100 지수 CFD (ID: 21501)
  2. US_500: S&P 500 지수 CFD (ID: 21499)
  3. JAPAN_225: 닛케이 225 지수 CFD (ID: 21507)
  4. HONG_KONG_50: 항셍 50 지수 CFD (ID: 21497)
  5. XAUUSD: 골드 현물 CFD (ID: 41)
  6. XTIUSD: WTI 원유 현물 CFD (ID: 10092)
  7. USDJPY: 달러-엔 현물 환율 (ID: 4)
- 방식: 15일 단위 청크 분할 요청 및 Rate-limit 안전 대기
- 산출물: DataVault/raw/ctrader/{symbol}_1m.csv 및 manifest.json
"""

import os
import sys
import time
import hashlib
import json
import argparse

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from twisted.internet import reactor
from ctrader_open_api import Client, TcpProtocol, Protobuf
from ctrader_open_api.endpoints import EndPoints
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAGetTrendbarsReq,
    ProtoOAGetTrendbarsRes,
    ProtoOAErrorRes,
)

# 6대 대용 지표 + 나스닥 심볼 마스터 레지스트리
CTRADER_SYMBOLS = {
    "US_TECH_100": {
        "symbol_id": 21501,
        "name": "US TECH 100",
        "display_name": "나스닥 100 CFD (대용)",
        "digits": 2,
        "price_div": 100000.0,
        "category": "GLOBAL_INDEX_CFD_PROXY",
        "notes": "cTrader Open API ProtoOAGetTrendbars (US TECH 100 나스닥 100 CFD 대용)",
        "proxy_target": "CME NQ 선물",
    },
    "US_500": {
        "symbol_id": 21499,
        "name": "US 500",
        "display_name": "S&P 500 CFD (대용)",
        "digits": 2,
        "price_div": 100000.0,
        "category": "GLOBAL_INDEX_CFD_PROXY",
        "notes": "cTrader Open API ProtoOAGetTrendbars (US 500 S&P 500 CFD 대용)",
        "proxy_target": "CME ES 선물",
    },
    "JAPAN_225": {
        "symbol_id": 21507,
        "name": "JAPAN 225",
        "display_name": "닛케이 225 CFD (대용)",
        "digits": 2,
        "price_div": 100000.0,
        "category": "GLOBAL_INDEX_CFD_PROXY",
        "notes": "cTrader Open API ProtoOAGetTrendbars (JAPAN 225 닛케이 225 CFD 대용)",
        "proxy_target": "OSE 닛케이 225 선물",
    },
    "HONG_KONG_50": {
        "symbol_id": 21497,
        "name": "HONG KONG 50",
        "display_name": "항셍 50 CFD (대용)",
        "digits": 2,
        "price_div": 100000.0,
        "category": "GLOBAL_INDEX_CFD_PROXY",
        "notes": "cTrader Open API ProtoOAGetTrendbars (HONG KONG 50 항셍 50 CFD 대용)",
        "proxy_target": "HKEX 항셍 선물",
    },
    "XAUUSD": {
        "symbol_id": 41,
        "name": "XAUUSD",
        "display_name": "골드 현물 CFD (대용)",
        "digits": 2,
        "price_div": 100000.0,
        "category": "COMMODITY_CFD_PROXY",
        "notes": "cTrader Open API ProtoOAGetTrendbars (XAUUSD 골드 현물 CFD 대용)",
        "proxy_target": "COMEX 골드 선물",
    },
    "XTIUSD": {
        "symbol_id": 10092,
        "name": "XTIUSD",
        "display_name": "WTI 원유 현물 CFD (대용)",
        "digits": 2,
        "price_div": 100000.0,
        "category": "COMMODITY_CFD_PROXY",
        "notes": "cTrader Open API ProtoOAGetTrendbars (XTIUSD WTI 원유 현물 CFD 대용)",
        "proxy_target": "NYMEX WTI 선물",
    },
    "USDJPY": {
        "symbol_id": 4,
        "name": "USDJPY",
        "display_name": "달러-엔화 FX (대용)",
        "digits": 3,
        "price_div": 100000.0,
        "category": "FX_PROXY",
        "notes": "cTrader Open API ProtoOAGetTrendbars (USDJPY 달러-엔 현물 환율 대용)",
        "proxy_target": "CME JPY 통화선물",
    },
}

KST = timezone(timedelta(hours=9))


class CTraderHistoryFetcher:
    def __init__(
        self,
        symbol_key: str = "US_TECH_100",
        days: int = 365,
        chunk_days: int = 15,
        period: str = "1m",
        output_dir: str = "DataVault/raw/ctrader",
        on_complete_callback=None,
    ):
        if symbol_key not in CTRADER_SYMBOLS:
            raise ValueError(f"알 수 없는 cTrader 심볼 키: {symbol_key} (지원: {list(CTRADER_SYMBOLS.keys())})")

        self.symbol_key = symbol_key
        self.meta = CTRADER_SYMBOLS[symbol_key]
        self.symbol_id = self.meta["symbol_id"]
        self.symbol_name = symbol_key
        self.price_div = self.meta["price_div"]
        self.digits = self.meta["digits"]

        self.period = "1d" if str(period).strip().lower() in ("1d", "d1") else "1m"
        self.period_enum = ProtoOATrendbarPeriod.D1 if self.period == "1d" else ProtoOATrendbarPeriod.M1

        self.days = days
        # 일봉(1D)은 데이터 밀도가 낮으므로 1회 요청 청크를 10년치까지 확장 가능
        if self.period == "1d" and chunk_days == 15:
            self.chunk_days = min(days, 3650)
        else:
            self.chunk_days = chunk_days
        self.output_dir = output_dir
        self.on_complete_callback = on_complete_callback

        # 환경변수 로드
        self.client_id = os.environ.get("CTRADER_CLIENT_ID")
        self.client_secret = os.environ.get("CTRADER_SECRET")
        self.access_token = os.environ.get("CTRADER_ACCESS_TOKEN")
        self.account_id_str = os.environ.get("CTRADE_ACCOUNT_ID")

        if not all([self.client_id, self.client_secret, self.access_token, self.account_id_str]):
            print("[ERROR] 필수 cTrader 환경변수가 누락되었습니다.")
            sys.exit(1)

        self.account_id = int(self.account_id_str)
        self.client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self.client.setConnectedCallback(self.on_connected)
        self.client.setMessageReceivedCallback(self.on_message_received)

        # 수집 청크 생성 (과거부터 최신순으로)
        self.chunks: List[Dict[str, Any]] = []
        self.current_chunk_idx = 0
        self.collected_bars: Dict[int, Dict[str, Any]] = {}  # timestamp_sec -> bar dict
        self._prepare_chunks()

    def _prepare_chunks(self):
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=self.days)

        curr = start_dt
        while curr < end_dt:
            nxt = min(curr + timedelta(days=self.chunk_days), end_dt)
            self.chunks.append({
                "from_dt": curr,
                "to_dt": nxt,
                "from_ms": int(curr.timestamp() * 1000),
                "to_ms": int(nxt.timestamp() * 1000),
            })
            curr = nxt

        print(f"\n{'='*65}")
        print(f"📦 [{self.symbol_key}] {self.meta['display_name']} 수집 준비")
        print(f"• 수집 기간: {self.days}일 ({start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')})")
        print(f"• 1회당 {self.chunk_days}일씩 총 {len(self.chunks)}개 청크 분할 수집 (심볼 ID: {self.symbol_id})")
        print(f"{'='*65}")

    def start(self):
        self.client.startService()
        reactor.run()

    def on_connected(self, client):
        print(f"🔗 [1/3] SSL 연결 성공 ({EndPoints.PROTOBUF_DEMO_HOST}:{EndPoints.PROTOBUF_PORT})")
        app_req = ProtoOAApplicationAuthReq()
        app_req.clientId = self.client_id
        app_req.clientSecret = self.client_secret
        self.client.send(app_req)

    def on_message_received(self, client, message):
        payload = Protobuf.extract(message)

        if isinstance(payload, ProtoOAApplicationAuthRes):
            print("🔑 [2/3] Application 인증 성공")
            acc_req = ProtoOAAccountAuthReq()
            acc_req.ctidTraderAccountId = self.account_id
            acc_req.accessToken = self.access_token
            self.client.send(acc_req)

        elif isinstance(payload, ProtoOAAccountAuthRes):
            print(f"👤 [3/3] Account 인증 성공 (Account ID: {self.account_id})")
            print("🚀 [수집 시작] 청크별 순차 다운로드를 진행합니다...")
            self.fetch_next_chunk()

        elif isinstance(payload, ProtoOAGetTrendbarsRes):
            bars = list(payload.trendbar)
            chunk = self.chunks[self.current_chunk_idx]

            for b in bars:
                ts_sec = b.utcTimestampInMinutes * 60
                low = b.low / self.price_div
                open_p = (b.low + b.deltaOpen) / self.price_div
                high = (b.low + b.deltaHigh) / self.price_div
                close_p = (b.low + b.deltaClose) / self.price_div
                vol = float(b.volume)

                if self.period == "1d":
                    dt_val = datetime.fromtimestamp(ts_sec, tz=timezone.utc).astimezone(KST)
                    ts_str = dt_val.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    dt_val = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
                    ts_str = dt_val.strftime("%Y-%m-%d %H:%M:%S")

                self.collected_bars[ts_sec] = {
                    "timestamp": ts_str,
                    "open": open_p,
                    "high": high,
                    "low": low,
                    "close": close_p,
                    "volume": vol,
                }

            pct = (self.current_chunk_idx + 1) / len(self.chunks) * 100
            print(
                f"  [{self.current_chunk_idx + 1:02d}/{len(self.chunks):02d}] ({pct:5.1f}%) "
                f"{chunk['from_dt'].strftime('%Y-%m-%d')} ~ {chunk['to_dt'].strftime('%Y-%m-%d')} | "
                f"수신: {len(bars):,d}개 | 누적: {len(self.collected_bars):,d}개"
            )

            self.current_chunk_idx += 1
            if self.current_chunk_idx < len(self.chunks):
                reactor.callLater(0.25, self.fetch_next_chunk)
            else:
                print(f"🏁 [{self.symbol_key}] 모든 청크 수집 완료! 파일 저장을 시작합니다.")
                self.save_results()
                if self.on_complete_callback:
                    self.on_complete_callback()
                if reactor.running:
                    reactor.stop()

        elif isinstance(payload, ProtoOAErrorRes):
            print(f"❌ [API 에러] {payload.errorCode}: {payload.description}")
            if reactor.running:
                reactor.stop()

    def fetch_next_chunk(self):
        chunk = self.chunks[self.current_chunk_idx]
        req = ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = self.account_id
        req.symbolId = self.symbol_id
        req.period = self.period_enum
        req.fromTimestamp = chunk["from_ms"]
        req.toTimestamp = chunk["to_ms"]
        self.client.send(req)

    def save_results(self):
        """수집한 봉을 모든 원천 공통 저장 절차(raw_day_store.ingest_file)로 저장한 뒤, 최종 파일 기준으로 매니페스트를 쓴다.
        (기존 아카이브와의 병합·정렬·백업·원장·'확인 필요'는 공통 절차가 맡는다 — 여기서 파일을 통째로 다시 쓰지 않는다)"""
        os.makedirs(self.output_dir, exist_ok=True)
        csv_path = os.path.join(self.output_dir, f"{self.symbol_name}_{self.period}.csv")
        manifest_path = f"{csv_path}.manifest.json"

        sorted_ts = sorted(self.collected_bars.keys())
        if not sorted_ts:
            print(f"⚠️ [{self.symbol_key}] 수집된 데이터가 없습니다.")
            return

        import pandas as pd
        from shared.data_library.raw_ingest import ingest_dataframe
        rows = []
        for ts in sorted_ts:
            bar = self.collected_bars[ts]
            rows.append([bar["timestamp"], f"{bar['open']:.{self.digits}f}", f"{bar['high']:.{self.digits}f}",
                         f"{bar['low']:.{self.digits}f}", f"{bar['close']:.{self.digits}f}", f"{bar['volume']:.1f}"])
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        print(f"💾 원천 저장(공통 절차): {csv_path} (수신 {len(df):,d}행)...")
        rep = ingest_dataframe(df, csv_path, provider="ctrader", market="US", interval=self.period,
                               publish_manifest=False, log=print)
        if rep is None or not os.path.exists(csv_path):
            print(f"❌ [{self.symbol_key}] 저장 실패 — 기존 원천은 그대로입니다")
            return
        # 수집 시작(ARCHIVE_START_YM)까지 거슬러 요청했는데 원천이 그보다 늦은 데이터부터 줬다면, 그 앞은 원천에 없다 — 기록해 재시도 방지
        from shared.data_library import raw_ingest
        if datetime.now() - timedelta(days=self.days) <= raw_ingest.archive_start() + timedelta(days=1):
            raw_ingest.note_head_floor(csv_path, raw_ingest._kst(df["timestamp"].iloc[0]), log=print)

        # 매니페스트는 저장이 끝난 최종 파일 기준
        with open(csv_path, "rb") as f:
            content_bytes = f.read()
        body = content_bytes.decode("utf-8").splitlines()[1:]
        body = [l for l in body if l.strip()]
        total_rows = len(body)
        lows = [float(l.split(",")[3]) for l in body]
        highs = [float(l.split(",")[2]) for l in body]
        min_p, max_p = min(lows), max(highs)
        content = content_bytes.decode("utf-8")
        file_bytes = os.path.getsize(csv_path)
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        first_ts = body[0].split(",", 1)[0]
        last_ts = body[-1].split(",", 1)[0]

        # Manifest JSON 생성
        manifest = {
            "schema_version": "1.0",
            "data_file": f"{self.symbol_name}_{self.period}.csv",
            "data_tier": "raw",
            "provider": "ctrader",
            "symbol": self.symbol_name,
            "asset_type": "standard",
            "market": self.meta["category"],
            "timezone": "UTC",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "integrity": {
                "byte_size": file_bytes,
                "row_count": total_rows,
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
                "null_ratio": 0.0,
                "price_range": [round(min_p, self.digits), round(max_p, self.digits)],
                "has_microstructure": False,
                "sha256": sha256,
            },
            "health_summary": {
                "total_bars": total_rows,
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
                "null_ratio": 0.0,
                "price_range": [round(min_p, self.digits), round(max_p, self.digits)],
                "has_microstructure": False,
            },
            "source_meta": {
                "notes": self.meta["notes"],
                "symbol_id": self.symbol_id,
                "period": self.period.upper(),
                "indicator_type": "proxy",
                "proxy_target": self.meta["proxy_target"],
            },
        }

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        print(f"✅ [Bronze 적재 완료] {csv_path}")
        print(f"  • 파일 크기: {file_bytes / (1024 * 1024):.2f} MB")
        print(f"  • 총 캔들 수: {total_rows:,d} 행")
        print(f"  • 수집 기간: {first_ts} ~ {last_ts}")
        print(f"  • 가격 범위: {min_p:,.{self.digits}f} ~ {max_p:,.{self.digits}f}")
        print(f"  • SHA256: {sha256[:16]}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="cTrader 과거 데이터 범용 수집기")
    parser.add_argument(
        "--symbol",
        type=str,
        default="US_TECH_100",
        help=f"수집할 심볼 키 (선택: {list(CTRADER_SYMBOLS.keys())})",
    )
    parser.add_argument("--period", type=str, default="1m", help="수집 주기 (1m, 1d)")
    parser.add_argument("--days", type=int, default=365, help="수집할 일수 (기본값: 365)")
    parser.add_argument("--chunk-days", type=int, default=15, help="청크당 일수 (기본값: 15)")
    parser.add_argument("--out-dir", type=str, default="DataVault/raw/ctrader", help="저장 디렉토리")

    args = parser.parse_args()

    fetcher = CTraderHistoryFetcher(
        symbol_key=args.symbol,
        days=args.days,
        chunk_days=args.chunk_days,
        period=args.period,
        output_dir=args.out_dir
    )
    fetcher.start()
