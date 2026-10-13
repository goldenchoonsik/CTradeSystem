import asyncio
import os
import sys
import json
import logging
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.auth import KISAuthManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

async def main():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    load_dotenv(env_path)

    app_key = os.getenv("KIS_APP_KEY", "").strip("'\" \t\r\n")
    app_secret = os.getenv("KIS_APP_SECRET", "").strip("'\" \t\r\n")
    url_base = (os.getenv("KIS_URL_BASE") or os.getenv("URL_BASE") or "https://openapi.koreainvestment.com:9443").strip("'\" \t\r\n")
    acc_no = os.getenv("KIS_ACC_NO", "").strip("'\" \t\r\n")
    acc_code = os.getenv("KIS_ACC_CODE", "01").strip("'\" \t\r\n")

    auth = KISAuthManager(app_key, app_secret, url_base, acc_no, acc_code)
    # 항상 신규 웹소켓 승인키 발급
    approval_key = await auth.get_approval_key()
    logging.info(f"🔑 New Approval Key: {approval_key[:10]}...")

    import websockets
    is_simulation = "openapivts" in url_base.lower()
    ws_uri = "ws://ops.koreainvestment.com:31000" if is_simulation else "ws://ops.koreainvestment.com:21000"

    logging.info(f"🌐 Connecting to {ws_uri}...")

    # 나스닥 대표 체결 및 호가
    subscriptions = [
        {"tr_id": "HDFSCNT0", "tr_key": "DNASTQQQ"},
        {"tr_id": "HDFSCNT0", "tr_key": "DNASNVDA"},
        {"tr_id": "HDFSCNT0", "tr_key": "DNASSOXL"},
        {"tr_id": "HDFSASP0", "tr_key": "DNASTQQQ"},
    ]

    async with websockets.connect(ws_uri, ping_interval=20, ping_timeout=20) as ws:
        logging.info("✅ Connected to KIS WebSocket.")

        for sub in subscriptions:
            packet = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8"
                },
                "body": {
                    "input": {
                        "tr_id": sub["tr_id"],
                        "tr_key": sub["tr_key"]
                    }
                }
            }
            await ws.send(json.dumps(packet))
            logging.info(f"📤 Sent Sub: {sub['tr_id']} -> {sub['tr_key']}")
            await asyncio.sleep(0.5)

        logging.info("📡 Streaming live ticks for 20 seconds...")
        start_time = asyncio.get_event_loop().time()
        tick_count = 0
        while asyncio.get_event_loop().time() - start_time < 20:
            try:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                if raw_msg.startswith(('0', '1')):
                    tick_count += 1
                    parts = raw_msg.split('|')
                    tr_id = parts[1] if len(parts) > 1 else ""
                    body = parts[3] if len(parts) > 3 else ""
                    fields = body.split('^')
                    # HDFSCNT0 (체결): 0:실시간코드, 1:종목코드, 2:수신일자, 3:수신시간, 11:현재가, 12:체결량
                    logging.info(f"⚡ [TICK #{tick_count}] TR={tr_id} | Raw: {body[:100]}")
                else:
                    msg = json.loads(raw_msg)
                    if msg.get("header", {}).get("tr_id") == "PINGPONG":
                        await ws.send(raw_msg)
                        logging.debug("🏓 Pong sent")
                    else:
                        logging.info(f"📋 Server Response: {msg}")
            except asyncio.TimeoutError:
                logging.info("⏳ Waiting for data...")

        logging.info(f"🎉 Done! Received {tick_count} live tick packets.")

if __name__ == "__main__":
    asyncio.run(main())
