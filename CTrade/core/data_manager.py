import time
import logging
from collections import deque
import requests
from threading import Thread
from typing import Dict, Any
from core.models import TickData, OrderbookData
from shared.core import stream_keys as sk
import ctrade_core
from utils.diagnostics.data_sniffer import DataSniffer

class DataManager:
    def __init__(self, symbols: list, indicators_config: dict = None, exchange_rate_mode: str = "USDT"):
        if indicators_config is None:
            indicators_config = {}
        
        self.history_tick_limit = indicators_config.get("history_tick_limit", 1000)
        self.rsi_period = indicators_config.get("rsi_period", 14)
        self.imbalance_decay_rate = indicators_config.get("imbalance_decay_rate", 1.0)
        self.time_bucket_size = indicators_config.get("intensity_time_bucket", 0.5)
        self.time_window_size = indicators_config.get("intensity_time_window", 120)
        
        # [Optimization] C++ CoreTracker 초기화 및 심볼 등록
        self.core_tracker = ctrade_core.CoreTracker()
        for symbol in symbols:
            self.core_tracker.register_symbol(symbol)
            
        # 종목별 상태 저장 (UI 연동용 콤팩트 구조)
        self.store: Dict[str, Dict[str, Any]] = {
            symbol: {
                "ticker": None,
                "orderbooks": {},
                "last_update": 0,
                "recent_trades": deque(maxlen=30)
            } for symbol in symbols
        }
        self.global_state = {
            "usdt_krw": 1400.0,
            "usd_krw_fx": 1400.0,
            "exchange_rate_mode": exchange_rate_mode,  # "USDT" or "FX"
            "binance_futures_prices": {}, # 선물 가격 저장용
            "binance_prices": {},
            "bitget_futures_prices": {},
            "bitget_prices": {}
        }
        
        # 외환 환율 업데이트 스레드 시작
        self._stop_fx = False
        self._fx_thread = Thread(target=self._fx_update_loop, daemon=True)
        self._fx_thread.start()

    def _fx_update_loop(self):
        """실시간 외환 환율(USD/KRW)을 가져오는 루프"""
        while not self._stop_fx:
            try:
                response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    krw_rate = data.get("rates", {}).get("KRW")
                    if krw_rate:
                        self.global_state["usd_krw_fx"] = krw_rate
            except Exception as e:
                logging.error(f"[DataManager] FX Rate Fetch Error: {e}")
            
            time.sleep(60)

    def stop(self):
        """FX 환율 업데이트 스레드 정리"""
        self._stop_fx = True
        if self._fx_thread.is_alive():
            self._fx_thread.join(timeout=2.0)

    def __del__(self):
        """소멸자: 스레드 정리 보장"""
        try:
            self.stop()
        except Exception:
            pass

    def get_active_exchange_rate(self) -> float:
        """현재 설정된 모드에 따른 활성 환율 반환"""
        mode = self.global_state.get("exchange_rate_mode", "USDT")
        if mode == "FX":
            return self.global_state.get("usd_krw_fx", 1400.0)
        return self.global_state.get("usdt_krw", 1400.0)

    def get_kimchi_premium(self, symbol: str, ref_market: str = "BINANCE") -> float:
        """현재 활성 환율을 기준으로 김치 프리미엄 계산"""
        ticker = self.get_ticker(symbol)
        
        if ref_market == "BITGET":
            ref_price = self.get_bitget_price(symbol)
        else:
            ref_price = self.get_binance_price(symbol)
            
        if ticker and ref_price > 0:
            active_rate = self.get_active_exchange_rate()
            ref_krw = ref_price * active_rate
            return ((ticker.price / ref_krw) - 1) * 100
        return 0.0

    def register_symbol(self, symbol: str):
        """런타임에 동적으로 새로운 심볼을 등록"""
        if symbol in self.store:
            return
        self.store[symbol] = {
            "ticker": None,
            "orderbooks": {},
            "last_update": 0,
            "recent_trades": deque(maxlen=30)
        }
        self.core_tracker.register_symbol(symbol)

    def update_state(self, data: Any):
        """시장 데이터 업데이트 및 글로벌 상태 갱신"""
        symbol = getattr(data, 'symbol', None)
        if not symbol or symbol not in self.store: return

        # [Diagnostics Hook] 실시간 데이터 캡처기 훅 (비활성화 시 수 나노초 미만 오버헤드)
        DataSniffer.instance().record_event(
            "orderbook" if hasattr(data, 'units') else "ticker",
            symbol,
            data.__dict__ if hasattr(data, '__dict__') else data
        )

        curr_time = time.time()

        # 1. 호가 데이터 (units 속성) 수신 시
        if hasattr(data, 'units') and data.units:
            level = getattr(data, 'level', 0.0)
            self.store[symbol]["orderbooks"][level] = data
            self.store[symbol]["orderbook"] = data 
            
            # C++ Orderbook 구조체 리스트로 명시적 타입 변환
            units_cpp = []
            for u in data.units:
                bp = float(u.bid_price if hasattr(u, 'bid_price') else u.get('bid_price', 0.0))
                bs = float(u.bid_size if hasattr(u, 'bid_size') else u.get('bid_size', 0.0))
                ap = float(u.ask_price if hasattr(u, 'ask_price') else u.get('ask_price', 0.0))
                as_ = float(u.ask_size if hasattr(u, 'ask_size') else u.get('ask_size', 0.0))
                units_cpp.append(ctrade_core.OrderbookUnit(bp, bs, ap, as_))
            
            self.core_tracker.update_orderbook(symbol, float(level), getattr(data, 'timestamp', curr_time), units_cpp)
        
        # 2. 시세/체결 데이터 (price 속성) 수신 시
        elif hasattr(data, 'price'):
            if data.price > 0:
                self.store[symbol]["ticker"] = data
                
                # 매수/매도 방향 표준화
                side = str(data.side).upper()
                if side in ('BUY', 'BID'): side = 'BID'
                if side in ('SELL', 'ASK'): side = 'ASK'
                
                # UI용 최근 체결 내역 업데이트
                self.store[symbol]["recent_trades"].append({
                    "p": data.price,
                    "v": data.volume,
                    "s": side,
                    "t": curr_time
                })
                
                # C++ 모듈에 틱 데이터 수집 격리
                self.core_tracker.add_history_tick(
                    symbol, 
                    curr_time, 
                    data.price, 
                    data.volume, 
                    side, 
                    self.time_bucket_size, 
                    self.time_window_size
                )
                
                # 글로벌 환율 및 타 거래소 시세 캐시 갱신
                if "USDT" in symbol:
                    if symbol == sk.FX_RATE_SYMBOL:
                        self.global_state["usdt_krw"] = data.price
                    elif "-" in symbol:
                        coin = symbol.split("-")[-1]
                        self.global_state[f"native_usdt_{coin}"] = data.price
                    elif sk.is_binance_futures_key(symbol):
                        self.global_state["binance_futures_prices"][symbol] = data.price
                    elif sk.is_bitget_futures_key(symbol):
                        self.global_state["bitget_futures_prices"][symbol] = data.price
                    else:
                        if getattr(data, 'exchange', '').startswith("BITGET"):
                            self.global_state["bitget_prices"][symbol] = data.price
                        else:
                            self.global_state["binance_prices"][symbol] = data.price
            
        self.store[symbol]["last_update"] = curr_time

    def get_binance_price(self, symbol: str) -> float:
        """종목에 대응하는 바이낸스 가격 반환"""
        normalized = symbol.replace(" ", "-")
        if "-" not in normalized: return 0.0
        base_symbol = normalized.split("-")[-1]
        binance_symbol = sk.spot_key(base_symbol)
        return self.global_state["binance_prices"].get(binance_symbol, 0.0)

    def get_binance_futures_price(self, symbol: str) -> float:
        """종목에 대응하는 바이낸스 선물 가격 반환"""
        normalized = symbol.replace(" ", "-")
        if "-" not in normalized: return 0.0
        base_symbol = normalized.split("-")[-1]
        futures_symbol = sk.binance_futures_key(base_symbol)
        return self.global_state["binance_futures_prices"].get(futures_symbol, 0.0)

    def get_bitget_price(self, symbol: str) -> float:
        """종목에 대응하는 비트겟 가격 반환"""
        normalized = symbol.replace(" ", "-")
        if "-" not in normalized: return 0.0
        base_symbol = normalized.split("-")[-1]
        bitget_symbol = sk.spot_key(base_symbol)
        return self.global_state["bitget_prices"].get(bitget_symbol, 0.0)

    def get_bitget_futures_price(self, symbol: str) -> float:
        """종목에 대응하는 비트겟 선물 가격 반환"""
        normalized = symbol.replace(" ", "-")
        if "-" not in normalized: return 0.0
        base_symbol = normalized.split("-")[-1]
        futures_symbol = sk.bitget_futures_key(base_symbol)
        return self.global_state["bitget_futures_prices"].get(futures_symbol, 0.0)

    def get_native_usdt_price(self, symbol: str) -> float:
        """해당 거래소 자체의 USDT 마켓 가격 반환"""
        if "-" not in symbol: return 0.0
        coin = symbol.split("-")[-1]
        return self.global_state.get(f"native_usdt_{coin}", 0.0)

    def get_ticker(self, symbol: str):
        """최신 체결 데이터 반환"""
        store = self.store.get(symbol)
        return store.get("ticker") if store else None

    def get_orderbook(self, symbol: str, level: float = 0.0):
        """최신 호가 데이터 반환 (레벨 지정 가능)"""
        store = self.store.get(symbol)
        if not store: return None
        obs = store.get("orderbooks", {})
        return obs.get(level) or store.get("orderbook")

    def get_analysis(self, symbol: str, level: float = 0.0, include_energy_map: bool = False) -> dict:
        """C++ 모듈을 활용하여 초단타 지표들을 마이크로초 단위로 고속 취합"""
        store = self.store.get(symbol)
        if not store: return {}

        now = time.time()
        # C++ 코어 지표 일괄 획득 호출
        metrics = self.core_tracker.get_analysis_metrics(
            symbol=symbol,
            level=float(level),
            current_time=now,
            imbalance_decay_rate=self.imbalance_decay_rate,
            rsi_period=self.rsi_period,
            bucket_size=self.time_bucket_size,
            max_buckets=self.time_window_size
        )

        return {
            "ticker": self.get_ticker(symbol),
            "orderbook": self.get_orderbook(symbol, level),
            "imbalance": metrics.get("imbalance", 0.0),
            "density_ratio": metrics.get("density_ratio", 1.0),
            "intensity": metrics.get("intensity", 100.0),
            "time_intensity": metrics.get("time_intensity", 100.0),
            "vwap": metrics.get("vwap", 0.0),
            "rsi": metrics.get("rsi", 50.0),
            "activity_5s": metrics.get("activity_5s", 0),
            "energy_map": self.core_tracker.get_energy_map(symbol, 30, now) if include_energy_map else {},
            "energy_map_12h": {}, # 12H 맵은 UI 개별 요청 시점에만 사용하므로 무거운 전송 생략
            "ask_wall_resistance": metrics.get("ask_wall_resistance", 0.0),
            "cumulative_delta_10s": metrics.get("cumulative_delta_10s", 0.0),
            "up_tick_ratio_5t": metrics.get("up_tick_ratio_5t", 0.5)
        }


    def get_rule_context(self, symbol: str, profit_rate: float = 0.0, level: float = 0.0) -> dict:
        """룰 엔진 평가를 위한 단일 컨텍스트(스냅샷) 생성"""
        analysis = self.get_analysis(symbol, level)
        ticker = self.get_ticker(symbol)
        
        ctx = {
            "profit_rate": profit_rate,
            "local_imbalance": 0.0,
            "local_density_ratio": 1.0,
            "local_intensity": 100.0,
            "local_time_intensity": 100.0,
            "local_rsi": 50.0,
            "price_vs_vwap": 0.0,
            "kimchi_premium": 0.0,
            "binance_spot_intensity": 100.0,
            "binance_futures_intensity": 100.0,
            "binance_futures_gap": 0.0,
            "bitget_spot_intensity": 100.0,
            "bitget_futures_intensity": 100.0,
            "bitget_futures_gap": 0.0
        }
        
        if analysis and ticker:
            ctx["local_imbalance"] = analysis.get("imbalance", 0.0)
            ctx["local_density_ratio"] = analysis.get("density_ratio", 1.0)
            ctx["local_intensity"] = analysis.get("intensity", 100.0)
            ctx["local_time_intensity"] = analysis.get("time_intensity", 100.0)
            ctx["local_rsi"] = analysis.get("rsi", 50.0)
            if analysis.get("vwap", 0.0) > 0.0:
                ctx["price_vs_vwap"] = ((ticker.price / analysis["vwap"]) - 1) * 100
            
            ctx["kimchi_premium"] = self.get_kimchi_premium(symbol)
            ctx["ask_wall_resistance"] = analysis.get("ask_wall_resistance", 0.0)
            ctx["cumulative_delta_10s"] = analysis.get("cumulative_delta_10s", 0.0)
            ctx["up_tick_ratio_5t"] = analysis.get("up_tick_ratio_5t", 0.5)

        # 바이낸스 및 비트겟 해외 선행 신호 연동
        if "-" in symbol:
            coin = symbol.split("-")[-1]
            spot_sym = sk.spot_key(coin)
            fut_sym = sk.binance_futures_key(coin)
            
            spot_analysis = self.get_analysis(spot_sym)
            if spot_analysis:
                ctx["binance_spot_intensity"] = spot_analysis.get("intensity", 100.0)
                ctx["binance_spot_time_intensity"] = spot_analysis.get("time_intensity", 100.0)
            
            fut_analysis = self.get_analysis(fut_sym)
            if fut_analysis:
                ctx["binance_futures_intensity"] = fut_analysis.get("intensity", 100.0)
                ctx["binance_futures_time_intensity"] = fut_analysis.get("time_intensity", 100.0)
            
            spot_p = self.get_binance_price(symbol)
            fut_p = self.get_binance_futures_price(symbol)
            if spot_p > 0 and fut_p > 0:
                ctx["binance_futures_gap"] = ((fut_p / spot_p) - 1) * 100
            else:
                ctx["binance_futures_gap"] = 0.0

            b_spot_sym = sk.spot_key(coin)
            b_fut_sym = sk.bitget_futures_key(coin)
            
            b_spot_analysis = self.get_analysis(b_spot_sym)
            if b_spot_analysis:
                ctx["bitget_spot_intensity"] = b_spot_analysis.get("intensity", 100.0)
                ctx["bitget_spot_time_intensity"] = b_spot_analysis.get("time_intensity", 100.0)
            
            b_fut_analysis = self.get_analysis(b_fut_sym)
            if b_fut_analysis:
                ctx["bitget_futures_intensity"] = b_fut_analysis.get("intensity", 100.0)
                ctx["bitget_futures_time_intensity"] = b_fut_analysis.get("time_intensity", 100.0)
            
            b_spot_p = self.get_bitget_price(symbol)
            b_fut_p = self.get_bitget_futures_price(symbol)
            if b_spot_p > 0 and b_fut_p > 0:
                ctx["bitget_futures_gap"] = ((b_fut_p / b_spot_p) - 1) * 100
            else:
                ctx["bitget_futures_gap"] = 0.0
            
        for other_symbol in self.store:
            if other_symbol == symbol: continue
            other_analysis = self.get_analysis(other_symbol)
            if other_analysis:
                ctx[f"{other_symbol}_intensity"] = other_analysis.get("intensity", 100.0)
                ctx[f"{other_symbol}_rsi"] = other_analysis.get("rsi", 50.0)
                
        return ctx