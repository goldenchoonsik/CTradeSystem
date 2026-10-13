"""cTrader 심볼 과거 봉 → 연구 staging (원장 밖, 운영 raw/ctrader 무관). 읽기 전용 API 조회.

심볼은 브로커 이름으로 찾는다(ID 하드코딩 없음). 시각은 원본 그대로 UTC·봉 시작 라벨(cTrader 규약).
파일이 있으면 마지막 시각 이후만 받아 덧붙인다(이어받기). 가격 = 정수/1e5 (Open API 공통 단위).

  bash -ic ".venv/bin/python docs/research/20261013_flow_kospi/collect_ctrader_staging.py 'MSCI SOUTH KOREA' --period 1m --since 2019-01-01"
  bash -ic ".venv/bin/python docs/research/20261013_flow_kospi/collect_ctrader_staging.py 'MSCI SOUTH KOREA' --period 1d --since 2010-01-01"
"""
import argparse
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.endpoints import EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq, ProtoOAAccountAuthRes, ProtoOAApplicationAuthReq, ProtoOAApplicationAuthRes,
    ProtoOAErrorRes, ProtoOAGetTrendbarsReq, ProtoOAGetTrendbarsRes, ProtoOASymbolsListReq, ProtoOASymbolsListRes)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod
from twisted.internet import reactor

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT = os.path.join(REPO, "DataVault", "staging", "overseas_proxy", "ctrader")
CHUNK = {"1m": timedelta(days=15), "1d": timedelta(days=3000)}
PERIOD = {"1m": ProtoOATrendbarPeriod.M1, "1d": ProtoOATrendbarPeriod.D1}


class Fetch:
    def __init__(self, name, period, since):
        self.name, self.period = name, period
        self.path = os.path.join(OUT, f"{name.replace(' ', '_')}_{period}.csv")
        start = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
        if os.path.exists(self.path):
            last = pd.read_csv(self.path, usecols=["timestamp"]).timestamp.max()
            start = datetime.fromisoformat(last).replace(tzinfo=timezone.utc) + timedelta(minutes=1)
        end = datetime.now(timezone.utc)
        self.chunks = []
        while start < end:
            nxt = min(start + CHUNK[period], end)
            self.chunks.append((start, nxt))
            start = nxt
        self.bars, self.i, self.sid = {}, 0, None
        self.acc = int(os.environ["CTRADE_ACCOUNT_ID"])
        self.c = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self.c.setConnectedCallback(self.on_conn)
        self.c.setMessageReceivedCallback(self.on_msg)

    def run(self):
        print(f"{self.name} {self.period}: 청크 {len(self.chunks)}개 → {self.path}")
        if not self.chunks:
            return
        self.c.startService()
        reactor.run()

    def stop(self):
        if reactor.running:
            reactor.stop()

    def on_conn(self, _):
        r = ProtoOAApplicationAuthReq()
        r.clientId, r.clientSecret = os.environ["CTRADER_CLIENT_ID"], os.environ["CTRADER_SECRET"]
        self.c.send(r)

    def req(self):
        a, b = self.chunks[self.i]
        r = ProtoOAGetTrendbarsReq()
        r.ctidTraderAccountId, r.symbolId, r.period = self.acc, self.sid, PERIOD[self.period]
        r.fromTimestamp, r.toTimestamp = int(a.timestamp() * 1000), int(b.timestamp() * 1000)
        self.c.send(r)

    def on_msg(self, _, m):
        p = Protobuf.extract(m)
        if isinstance(p, ProtoOAApplicationAuthRes):
            r = ProtoOAAccountAuthReq()
            r.ctidTraderAccountId, r.accessToken = self.acc, os.environ["CTRADER_ACCESS_TOKEN"]
            self.c.send(r)
        elif isinstance(p, ProtoOAAccountAuthRes):
            r = ProtoOASymbolsListReq()
            r.ctidTraderAccountId = self.acc
            self.c.send(r)
        elif isinstance(p, ProtoOASymbolsListRes):
            hit = [s.symbolId for s in p.symbol if s.symbolName == self.name]
            if len(hit) != 1:
                print(f"심볼 '{self.name}' 을(를) 하나로 특정 못함: {hit}")
                return self.stop()
            self.sid = hit[0]
            self.req()
        elif isinstance(p, ProtoOAGetTrendbarsRes):
            for b in p.trendbar:
                ts = datetime.fromtimestamp(b.utcTimestampInMinutes * 60, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                self.bars[ts] = (b.low + b.deltaOpen, b.low + b.deltaHigh, b.low, b.low + b.deltaClose, b.volume)
            self.i += 1
            if self.i % 20 == 0 or self.i == len(self.chunks):
                print(f"  {self.i}/{len(self.chunks)} 누적 {len(self.bars):,}봉")
            if self.i < len(self.chunks):
                reactor.callLater(0.25, self.req)
            else:
                self.save()
                self.stop()
        elif isinstance(p, ProtoOAErrorRes):
            print("API 오류", p.errorCode, p.description)
            self.stop()

    def save(self):
        if not self.bars:
            print("새 봉 없음")
            return
        d = pd.DataFrame([(t, *[v / 1e5 for v in x[:4]], float(x[4])) for t, x in sorted(self.bars.items())],
                         columns=["timestamp", "open", "high", "low", "close", "volume"])
        os.makedirs(OUT, exist_ok=True)
        if os.path.exists(self.path):
            old = pd.read_csv(self.path)
            d = pd.concat([old, d]).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
        tmp = self.path + ".tmp"
        d.to_csv(tmp, index=False)
        os.replace(tmp, self.path)
        print(f"저장 {len(d):,}행 {d.timestamp.iloc[0]} ~ {d.timestamp.iloc[-1]} (UTC, 봉 시작)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--period", choices=["1m", "1d"], default="1m")
    ap.add_argument("--since", default="2019-01-01")
    a = ap.parse_args()
    Fetch(a.name, a.period, a.since).run()
