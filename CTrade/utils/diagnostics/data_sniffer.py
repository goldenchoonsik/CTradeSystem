import os
import time
import json
import threading
from collections import deque
from typing import Dict, Any, List, Optional

def _to_json_serializable(obj: Any) -> Any:
    """dataclass (__slots__ 포함), dict, list 및 기본 타입을 재귀적으로 JSON 직렬화 가능하게 변환"""
    if obj is None or isinstance(obj, (int, float, str, bool)):
        return obj
    if hasattr(obj, '__slots__'):
        return {slot: _to_json_serializable(getattr(obj, slot, None)) for slot in obj.__slots__}
    if hasattr(obj, '__dict__'):
        return {k: _to_json_serializable(v) for k, v in obj.__dict__.items()}
    if isinstance(obj, (list, tuple, deque)):
        return [_to_json_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _to_json_serializable(v) for k, v in obj.items()}
    return str(obj)

class DataSniffer:
    _instance: Optional['DataSniffer'] = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> 'DataSniffer':
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self.is_capturing: bool = False
        self.target_symbol: Optional[str] = None
        self.duration_sec: float = 60.0
        self.max_records: int = 50000
        self.start_time: float = 0.0
        self.end_time: float = 0.0
        self.buffer: deque = deque()
        self.capture_thread: Optional[threading.Thread] = None
        self.last_dump_path: Optional[str] = None
        self.last_capture_info: Dict[str, Any] = {}
        self._buffer_lock = threading.Lock()

    def start_capture(self, symbol: str, duration_sec: float = 60.0, max_records: int = 50000) -> Dict[str, Any]:
        """비동기 실시간 데이터 캡처를 시작합니다."""
        with self._buffer_lock:
            if self.is_capturing:
                return {
                    "status": "already_running",
                    "symbol": self.target_symbol,
                    "elapsed_sec": round(time.time() - self.start_time, 2),
                    "captured_count": len(self.buffer)
                }

            self.target_symbol = symbol.strip()
            self.duration_sec = float(duration_sec)
            self.max_records = int(max_records)
            self.buffer.clear()
            self.start_time = time.time()
            self.end_time = self.start_time + self.duration_sec
            self.is_capturing = True
            self.last_dump_path = None

            self.capture_thread = threading.Thread(target=self._auto_stop_timer, daemon=True)
            self.capture_thread.start()

            return {
                "status": "started",
                "symbol": self.target_symbol,
                "duration_sec": self.duration_sec,
                "start_time": self.start_time
            }

    def _auto_stop_timer(self):
        """지정된 시간 경과 시 자동으로 캡처를 종료하고 덤프합니다."""
        while self.is_capturing:
            now = time.time()
            if now >= self.end_time or len(self.buffer) >= self.max_records:
                self.stop_capture()
                break
            time.sleep(0.5)

    def stop_capture(self) -> Dict[str, Any]:
        """캡처를 즉시 중단하고 수집된 데이터를 파일로 덤프합니다."""
        with self._buffer_lock:
            if not self.is_capturing and not self.buffer:
                return self.last_capture_info or {"status": "idle", "captured_count": 0}

            self.is_capturing = False
            actual_duration = round(time.time() - self.start_time, 3)
            records = list(self.buffer)

            dump_path = self._dump_to_file(records)
            self.last_dump_path = dump_path
            self.last_capture_info = {
                "status": "completed",
                "symbol": self.target_symbol,
                "duration_sec": actual_duration,
                "record_count": len(records),
                "dump_path": dump_path,
                "timestamp": time.time()
            }
            return self.last_capture_info

    def record_event(self, event_type: str, symbol: str, data: Dict[str, Any]):
        """
        초고속 논블로킹 이벤트 수집 훅.
        캡처가 비활성화되어 있거나 타겟 심볼과 불일치할 경우 나노초 단위로 즉시 반환.
        """
        if not self.is_capturing:
            return
        if self.target_symbol and self.target_symbol != "ALL" and self.target_symbol != symbol:
            return

        now = time.time()
        record = {
            "local_ts": now,
            "event_type": event_type,
            "symbol": symbol,
            "data": data
        }

        try:
            self.buffer.append(record)
            if len(self.buffer) >= self.max_records:
                self.is_capturing = False
        except Exception:
            pass

    def get_status(self) -> Dict[str, Any]:
        """현재 캡처 진행 상태를 반환합니다."""
        if self.is_capturing:
            elapsed = round(time.time() - self.start_time, 2)
            return {
                "is_capturing": True,
                "symbol": self.target_symbol,
                "elapsed_sec": elapsed,
                "remaining_sec": max(0.0, round(self.duration_sec - elapsed, 2)),
                "captured_count": len(self.buffer),
                "max_records": self.max_records
            }
        else:
            return {
                "is_capturing": False,
                "last_capture": self.last_capture_info
            }

    def _dump_to_file(self, records: List[Dict[str, Any]]) -> str:
        """수집된 레코드를 JSONL 포맷 파일로 안전하게 저장합니다."""
        base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "captures")
        os.makedirs(base_dir, exist_ok=True)
        
        clean_symbol = (self.target_symbol or "ALL").replace("-", "_")
        time_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(self.start_time))
        filename = f"capture_{clean_symbol}_{time_str}.jsonl"
        filepath = os.path.join(base_dir, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                for r in records:
                    safe_r = _to_json_serializable(r)
                    f.write(json.dumps(safe_r, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[DataSniffer] Dump file write failed: {e}")

        return filepath

