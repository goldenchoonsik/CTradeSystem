from abc import ABC, abstractmethod
import asyncio

class BaseConnector(ABC):
    def __init__(self, symbol: str, queue: asyncio.Queue):
        self.symbol = symbol
        self.queue = queue # 정제된 데이터가 쌓일 곳
        self.is_running = False

    @abstractmethod
    async def connect(self):
        """웹소켓 연결 및 구독 로직"""
        pass

    @abstractmethod
    def parse_message(self, message):
        """거래소별 전문 파싱 로직"""
        pass

    @staticmethod
    def next_backoff(current: float, session_seconds: float, base: float = 1.0,
                     cap: float = 30.0, stable_seconds: float = 30.0) -> float:
        """재접속 지수 백오프. 세션이 stable_seconds 이상 유지된 경우에만 base 로 리셋한다
        (접속 직후 끊기는 경우 1초 핫루프 방지)."""
        if session_seconds >= stable_seconds:
            return base
        return min(cap, max(base, current * 2.0))

    async def stop(self):
        self.is_running = False