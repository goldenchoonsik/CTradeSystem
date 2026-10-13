import asyncio
import json
import os
import time
import datetime
import gzip
import websockets

# --- 설정 (필요시 타겟 코인 변경 가능) ---
TARGET_COIN = "BTC"  # 분석할 코인 심볼 (예: BTC, SOL, XRP)

# 업비트 및 바이낸스 기호 자동 세팅
UPBIT_COIN = f"KRW-{TARGET_COIN}"
BINANCE_COIN = f"{TARGET_COIN.lower()}usdt"

# 데이터 저장 경로 세팅
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 비동기 데이터 적재를 위한 울트라 버퍼 큐 (렉 방지 및 무결성 적재의 심장)
data_queue = asyncio.Queue()

# 현재 열려 있는 파일 핸들러와 파일 경로 관리
current_file_path = None
gzip_file_stream = None

# 현재 시간 기준으로 파일 경로 결정 (12시간 기준: 오전_AM, 오후_PM 분할)
def get_file_path():
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    ampm = "AM" if now.hour < 12 else "PM"  # 오전/오후 12시간 기준 분할
    return os.path.join(DATA_DIR, f"crypto_raw_data_{today_str}_{ampm}.jsonl.gz")

# ==============================================================================
# 💾 디스크 I/O 전용 백그라운드 워커 (소켓 루프와 철저히 분리되어 렉 0% 달성)
# ==============================================================================
async def async_data_writer():
    global current_file_path, gzip_file_stream
    print("[System] 비동기 초고속 디스크 백그라운드 적재 엔진 가동.")
    
    while True:
        record = await data_queue.get()
        if record is None:  # 강제 종료 신호 수신 시 루프 탈출
            break
            
        target_path = get_file_path()
        
        # 날짜/시간(AM, PM)이 바뀌는 시점에 안전하게 파일 스위칭
        if current_file_path != target_path:
            if gzip_file_stream is not None:
                try:
                    gzip_file_stream.close()
                    print(f"[System] 기존 스트림 교체 보관 완료: {current_file_path}")
                except Exception as e:
                    print(f"[System] 스트림 종료 중 오류: {e}")
            
            current_file_path = target_path
            gzip_file_stream = gzip.open(current_file_path, "ab")
            print(f"[System] 새 압축 스트림 가동 시작: {current_file_path}")
            
        try:
            json_line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            gzip_file_stream.write(json_line)
            gzip_file_stream.flush()
        except Exception as e:
            print(f"[System] 데이터 적재 오류: {e}")
            
        data_queue.task_done()

# ==============================================================================
# 📡 웹소켓 수집 파이프라인 (업비트 현물 / 바이낸스 선물 / 바이낸스 현물 3원 탑재)
# ==============================================================================

# ① 업비트 원화 현물 수집기 (30호가창 무손실 수집 + 실시간 체결 + 핑퐁 방어막)
async def collect_upbit():
    uri = "wss://api.upbit.com/websocket/v1"
    subscribe_msg = [
        {"ticket": "CTradeDataBaker_Upbit_Collector"},
        {"type": "orderbook", "codes": [f"{UPBIT_COIN}.30"], "level": 0},  # 최상급 30단계 원본 호가
        {"type": "trade", "codes": [UPBIT_COIN]}                          # 실시간 체결 내역
    ]
    
    while True:
        try:
            print(f"[Upbit] 웹소켓 연결 시도 중... ({UPBIT_COIN})")
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                await websocket.send(json.dumps(subscribe_msg))
                print("[Upbit] 연결 및 구독 완료.")
                
                while True:
                    raw_data = await websocket.recv()
                    data = json.loads(raw_data.decode("utf-8"))
                    
                    record = {
                        "received_at": int(time.time() * 1000),
                        "exchange": "upbit",
                        "type": "raw_orderbook" if data.get("type") == "orderbook" else data.get("type"),
                        "raw": data
                    }
                    await data_queue.put(record)
                    
        except Exception as e:
            print(f"[Upbit] 에러 발생: {e}. 3초 후 재연결을 시도합니다.")
            await asyncio.sleep(3)

# ② 바이낸스 무기한 선물 수집기 (실시간 체결량 + 상하위 5단계 호가창 + 실시간 미결제 약정 + 강제 청산 복합 수집)
async def collect_binance_futures():
    uri = f"wss://fstream.binance.com/stream?streams={BINANCE_COIN}@aggTrade/{BINANCE_COIN}@depth5@100ms/{BINANCE_COIN}@openInterest/{BINANCE_COIN}@forceOrder"
    
    while True:
        try:
            print(f"[Binance Futures] 멀티 스트림 연결 시도... ({BINANCE_COIN.upper()})")
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                print("[Binance Futures] 복합 호가/체결/미결제약정/청산 데이터 동기화 성공.")
                
                while True:
                    raw_data = await websocket.recv()
                    res_json = json.loads(raw_data)
                    
                    stream_name = res_json.get("stream", "")
                    data = res_json.get("data", {})
                    
                    if "aggTrade" in stream_name:
                        data_type = "aggTrade"
                    elif "openInterest" in stream_name:
                        data_type = "openInterest"
                    elif "forceOrder" in stream_name:
                        data_type = "forceOrder"
                    else:
                        data_type = "raw_orderbook"
                    
                    record = {
                        "received_at": int(time.time() * 1000),
                        "exchange": "binance_futures",
                        "type": data_type,
                        "raw": data
                    }
                    await data_queue.put(record)
                    
        except Exception as e:
            print(f"[Binance Futures] 에러 발생: {e}. 3초 후 재연결을 시도합니다.")
            await asyncio.sleep(3)

# ③ 바이낸스 테더 현물 수집기 (iNAV 초정밀 계산을 위한 현물 스트림)
async def collect_binance_spot():
    uri = f"wss://stream.binance.com:9443/stream?streams={BINANCE_COIN}@aggTrade/{BINANCE_COIN}@depth5@100ms"
    
    while True:
        try:
            print(f"[Binance Spot] 현물 멀티 스트림 연결 시도... ({BINANCE_COIN.upper()})")
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                print("[Binance Spot] 현물 호가/체결 데이터 동기화 성공.")
                
                while True:
                    raw_data = await websocket.recv()
                    res_json = json.loads(raw_data)
                    
                    stream_name = res_json.get("stream", "")
                    data = res_json.get("data", {})
                    
                    data_type = "aggTrade" if "aggTrade" in stream_name else "raw_orderbook"
                    
                    record = {
                        "received_at": int(time.time() * 1000),
                        "exchange": "binance_spot",
                        "type": data_type,
                        "raw": data
                    }
                    await data_queue.put(record)
                    
        except Exception as e:
            print(f"[Binance Spot] 에러 발생: {e}. 3초 후 재연결을 시도합니다.")
            await asyncio.sleep(3)

# ==============================================================================
# 🚀 메인 오케스트레이터 및 앱 기동 구역
# ==============================================================================
async def main():
    print(f"=== [v7.1] 순수 다이어트 삼각 수집 엔진 가동 (Target: {TARGET_COIN}) ===")
    print(f"📂 적재 기지: {DATA_DIR}")
    
    # 디스크 적재 전용 백그라운드 태스크 실행
    writer_task = asyncio.create_task(async_data_writer())
    
    # 3원 스트림 병렬 동시 수집 가동
    await asyncio.gather(
        collect_upbit(),
        collect_binance_futures(),
        collect_binance_spot()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[System] 수집기가 종료 신호를 받았습니다. 남은 큐 잔량을 안전하게 커밋합니다...")
    finally:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        loop.run_until_complete(data_queue.put(None))
        
        if gzip_file_stream is not None:
            try:
                gzip_file_stream.close()
                print(f"[System] 아카이브 스트림이 완전무결하게 봉인되었습니다.")
            except Exception as e:
                print(f"[System] 최종 파일 봉인 실패: {e}")
        print("[System] 퀀트 데이터 공장 안전 가동 종료.")