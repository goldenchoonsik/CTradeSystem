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
    approval_key = await auth.get_approval_key()
    logging.info(f"🔑 Approval Key: {approval_key[:10]}...")

    import websockets
    is_simulation = "openapivts" in url_base.lower()
    ws_uri = "ws://ops.koreainvestment.com:31000" if is_simulation else "ws://ops.koreainvestment.com:21000"

    logging.info(f"🌐 WebSocket Server: {ws_uri}")
    
    # 069500 (KODEX 200) 및 122630 (KODEX 레버리지) 실시간 구독
    targets = [
        ("H0STCNT0", "069500"),
        ("H0STASP0", "069500"),
        ("H0STCNT0", "122630"),
        ("H0STASP0", "122630")
    ]

    async with websockets.connect(ws_uri, ping_interval=20, ping_timeout=20) as ws:
        logging.info("✅ Connected to KIS WebSocket.")

        for tr_id, tr_key in targets:
            packet = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8"
                },
                "body": {
                    "input": {
                        "tr_id": tr_id,
                        "tr_key": tr_key
                    }
                }
            }
            await ws.send(json.dumps(packet))
            logging.info(f"📤 Subscribed: {tr_id} -> {tr_key}")
            await asyncio.sleep(0.3)

        logging.info("📡 15초간 국내 ETF 실시간 패킷 수신 대기...")
        start_time = asyncio.get_event_loop().time()
        tick_count = 0
        while asyncio.get_event_loop().time() - start_time < 15:
            try:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                if raw_msg.startswith(('0', '1')):
                    tick_count += 1
                    parts = raw_msg.split('|')
                    tr_id = parts[1] if len(parts) > 1 else ""
                    body = parts[3] if len(parts) > 3 else ""
                    logging.info(f"⚡ [국내 틱 #{tick_count}] TR={tr_id} | Body={body[:120]}")
                else:
                    msg = json.loads(raw_msg)
                    if msg.get("header", {}).get("tr_id") == "PINGPONG":
                        await ws.send(raw_msg)
                    else:
                        logging.info(f"📋 Server Response: {msg.get('body', {}).get('msg1')}")
            except asyncio.TimeoutError:
                pass

        logging.info(f"🎉 국내 ETF 실시간 수신 완료! 총 {tick_count}개 패킷 수신.")

if __name__ == "__main__":
    asyncio.run(main())
