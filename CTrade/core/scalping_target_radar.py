import asyncio
import websockets
import json
import logging
import time
import os
from collections import deque, defaultdict
import aiohttp
import math
import ctrade_core
from utils.retry import atomic_write_json

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'trading_config.json')

def _read_current_version():
    """trading_config.json 에서 현재 최적화 세션 VERSION 태그를 읽어옵니다. 실패 시 기본값 반환."""
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg.get('GLOBAL_STRATEGY', {}).get('VERSION', 'v1.3.0_opt001')
    except Exception:
        return 'v1.3.0_opt001'

def _write_json_file(file_path, data):
    """설정 또는 이력 데이터를 파일에 원자적으로 쓰는 블로킹 함수 (Executor용)
    
    atomic_write_json을 사용하여 프로세스 간 Race Condition(빈 파일 파싱)을 방지합니다.
    """
    atomic_write_json(file_path, data)

def _set_shared_item(shared_dict, key, value):
    """multiprocessing.managers.SyncManager 프록시 딕셔너리에 동기식으로 값을 설정하는 블로킹 IPC 함수"""
    try:
        shared_dict[key] = value
    except Exception as e:
        import logging
        logging.error(f"[SyncState] Failed to set {key} in shared_state: {e}")

# Z-Score 콜드스타트 방지: EMA 통계(평균 0 시작)가 수렴하기 전에는 HOT-ALERT/자동이탈 금지
STARTUP_WARMUP_SEC = 120.0        # 기동 직후 유예 (초)
RECONNECT_WARMUP_SEC = 30.0       # 재연결 직후 유예 (초)
MIN_SAMPLES_FOR_ZSCORE = 120      # 종목별 최소 평가 표본 수 (약 1초 주기 -> 2분)
BACKOFF_STABLE_SEC = 30.0         # 접속이 이 시간 이상 유지되어야 백오프 리셋


class ScalpingTargetRadar:
    def __init__(self, shared_state, futures_radar=None):
        self.shared_state = shared_state
        self.http_session = None
        # [NEW] BinanceFuturesRadar 인스턴스 참조 (없으면 기존 shared_state 방식 폴백)
        self.futures_radar = futures_radar
        self.uri = "wss://api.upbit.com/websocket/v1"
        self.is_running = False
        self.krw_symbols = []
        # C++ CoreTracker 고성능 처리기 도입
        self.core_tracker = ctrade_core.CoreTracker()
        self.ws = None
        self.tasks = []
        self.cooldowns = {} # 자동 스위칭 등록 쿨다운용
        self.cooldown_durations = {} # Z-Score 기반 동적 이탈 쿨다운 캐시
        self.start_time = None
        self._warmup_until = None   # 이 시각 이전에는 HOT 탐지/이탈 유예
        self._eval_samples = defaultdict(int)  # {symbol: Z-Score 통계 평가 표본 수}
        self.verification_lock = asyncio.Lock()
        self.last_eval_time = time.time()
        self.last_basis = {} # {symbol: last_basis_pct}
        self.basis_velocity_ema = {} # {symbol: basis_velocity_ema}
        self._radar_update_task = None


    async def get_krw_markets(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.upbit.com/v1/market/all?isDetails=false") as res:
                    if res.status == 200:
                        data = await res.json()
                        symbols = [item['market'] for item in data if item['market'].startswith("KRW-")]
                        logging.info(f"[ScalpingTargetRadar] Dynamically fetched {len(symbols)} KRW markets from Upbit API.")
                        return symbols
            except Exception as e:
                logging.error(f"[ScalpingTargetRadar] Failed to fetch KRW markets: {e}")
        return []

    async def start(self):
        self.krw_symbols = await self.get_krw_markets()
        if not self.krw_symbols:
            # 예비 하드코딩
            self.krw_symbols = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-DOGE", "KRW-ADA"]
            
        self.is_running = True
        self.start_time = time.time()
        self._warmup_until = self.start_time + STARTUP_WARMUP_SEC
        self.tasks.append(asyncio.create_task(self.run_loop()))
        self.tasks.append(asyncio.create_task(self.evaluator_loop()))

    def _is_radar_active(self) -> bool:
        """설정 또는 공유 상태에서 RADAR_ACTIVE 활성화 여부 확인"""
        try:
            ctrl_active = self.shared_state.control.get("radar_active")
            if ctrl_active is not None:
                return bool(ctrl_active)
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "trading_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                return bool(cfg.get("GLOBAL_STRATEGY", {}).get("RADAR_ACTIVE", False))
        except Exception:
            pass
        return False

    async def run_loop(self):
        logging.info("[ScalpingTargetRadar] Web Socket Ticker Stream Consumer Loop Starting...")
        backoff = 0
        session_start = None
        while self.is_running:
            # RADAR_ACTIVE가 false인 경우 웹소켓 연결 없이 1초 대기 (CPU/네트워크 절약)
            if not self._is_radar_active():
                await asyncio.sleep(1.0)
                continue

            try:
                # [Optimization] open_timeout=10 설정으로 핸드셰이크 행(무한 대기)을 차단하고 예외 유발 유도
                async with websockets.connect(self.uri, ping_interval=30, open_timeout=10) as ws:
                    self.ws = ws
                    # Ticker 구독 패킷 구성
                    sub_packet = [
                        {"ticket": "ScalpingTargetRadarPlugin"},
                        {"type": "ticker", "codes": self.krw_symbols}
                    ]
                    await ws.send(json.dumps(sub_packet))
                    logging.info(f"[ScalpingTargetRadar] Subscribed to {len(self.krw_symbols)} tickers successfully.")
                    
                    # 접속 직후에는 백오프를 리셋하지 않는다 (즉시 끊기는 경우 1초 핫루프 방지).
                    # BACKOFF_STABLE_SEC 이상 유지된 뒤 끊길 때만 리셋한다.
                    session_start = time.monotonic()
                    # 재연결 직후 스냅샷/공백으로 통계가 왜곡되므로 웜업 유예 재적용
                    self._warmup_until = max(self._warmup_until or 0.0, time.time() + RECONNECT_WARMUP_SEC)

                    while self.is_running and self._is_radar_active():
                        # 15초 수신 타임아웃 적용하여 좀비/행 소켓 조기 발견 및 안전 재연결 유도
                        raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
                        if isinstance(raw, bytes):
                            raw = raw.decode('utf-8')
                        data = json.loads(raw)
                        
                        symbol = data.get('code')
                        if not symbol:
                            continue

                        # 재연결 직후 Upbit 는 마지막 체결 SNAPSHOT 을 재전송한다 -> 실제 틱이 아니므로 무시
                        if str(data.get('stream_type', '')).upper() == 'SNAPSHOT':
                            continue
                            
                        price = float(data.get('trade_price', 0))
                        volume = float(data.get('trade_volume', 0))
                        side = data.get('ask_bid')
                        acc_24h = float(data.get('acc_trade_price_24h', 0))
                        
                        now = time.time()
                        self.core_tracker.register_symbol(symbol)
                        self.core_tracker.add_tick(symbol, now, price, volume, side)
                        self.core_tracker.update_acc_24h(symbol, acc_24h)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # [Optimization] 무지성 5초 sleep 대신 지수 백오프(Exponential Backoff)를 적용하여 Rate Limit 극복
                if session_start is not None and (time.monotonic() - session_start) >= BACKOFF_STABLE_SEC:
                    backoff = 0  # 충분히 안정적으로 유지된 세션이었다면 리셋
                session_start = None
                backoff = min(60, backoff * 2 if backoff > 0 else 1)
                logging.error(f"[ScalpingTargetRadar] WebSocket connection error: {e}. Retrying in {backoff} seconds...")
                if self.is_running:
                    await asyncio.sleep(backoff)

    def check_upbit_slippage(self, symbol: str, budget_krw: float, max_slippage_pct: float, spread_filter_max_pct: float = 0.15, spread_filter_mult: float = 1.5) -> bool:
        """공유 메모리에 캐시된 업비트 실시간 호가창을 조회하여 스프레드 한도 및 시장가 매수 슬리피지 검증"""
        try:
            market_data = self.shared_state.data.get(symbol, {})
            ob_data = market_data.get("orderbook")
            if not ob_data:
                return False
            
            # OrderbookData 객체 혹은 딕셔너리 호환 지원
            units = getattr(ob_data, "units", None) or ob_data.get("units", [])
            if not units:
                return False
            
            best_ask = float(getattr(units[0], "ask_price", None) or units[0].get("ask_price", 0.0))
            best_bid = float(getattr(units[0], "bid_price", None) or units[0].get("bid_price", 0.0))
            if best_ask <= 0.0 or best_bid <= 0.0:
                return False
                
            # 1. 적응형 호가 스프레드 필터 검증
            spread_pct = ((best_ask - best_bid) / best_ask) * 100
            profile = self.core_tracker.get_profile(symbol)
            mean_spread = profile.get("mean_spread", 0.0)
            max_allowed_spread = max(mean_spread * spread_filter_mult, spread_filter_max_pct)
            
            if spread_pct > max_allowed_spread:
                logging.warning(f"[SlippageCheck] 🚫 Blocked {symbol} due to high spread: {spread_pct:.3f}% > {max_allowed_spread:.3f}% (mean: {mean_spread:.3f}%)")
                return False
                
            # 2. 시장가 매수 슬리피지 검증
            price_limit = best_ask * (1 + max_slippage_pct / 100.0)
            
            accum_krw = 0.0
            accum_size = 0.0
            
            for u in units:
                ask_price = float(getattr(u, "ask_price", None) or u.get("ask_price", 0.0))
                ask_size = float(getattr(u, "ask_size", None) or u.get("ask_size", 0.0))
                
                if ask_price <= price_limit:
                    accum_krw += ask_price * ask_size
                    accum_size += ask_size
                else:
                    break
            
            # 최대 슬리피지 한도 내에 누적된 호가 볼륨이 우리의 1회 매수 단가 이상이면 참(True) 리턴
            return accum_krw >= budget_krw
        except Exception as e:
            logging.error(f"[SlippageCheck] Failed to check slippage/spread for {symbol}: {e}")
        return False

    async def evaluator_loop(self):
        logging.info("[ScalpingTargetRadar] Live Analytics Evaluator Loop Started.")
        import os
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "trading_config.json")
        config = {}  # 루프 최초 실행 시 초기값; 파일 읽기 실패해도 NameError 방지
        
        while self.is_running:
            try:
                await asyncio.sleep(1.0) # 1초마다 평가 수행

                
                # 서버 작동이 꺼진 상태(server_active = False)인 경우 분석을 수행하지 않고 대기
                if not self.shared_state.control.get("server_active", True):
                    continue
                    
                now = time.time()
                delta_t = now - self.last_eval_time
                if delta_t <= 0:
                    delta_t = 1.0
                self.last_eval_time = now
                
                # 설정 파일 실시간 로드 (Race Condition 방어: 빈 파일/손상 파일 시 이전 config 재사용)
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        raw = f.read()
                    if not raw.strip():
                        # Atomic Write 중 타이밍 슬립으로 인해 아직 임시 파일이 교체되지 않은 경우
                        logging.warning("[ScalpingTargetRadar] Config file is empty (write in progress?). Using last known config.")
                        # config 변수는 이전 루프 iteration에서 유지됨
                    else:
                        config = json.loads(raw)
                except json.JSONDecodeError as e:
                    logging.warning(f"[ScalpingTargetRadar] Config JSON decode error (write in progress?): {e}. Using last known config.")
                except Exception as e:
                    logging.error(f"[ScalpingTargetRadar] Failed to read config in evaluator_loop: {e}")

                global_strat = config.get("GLOBAL_STRATEGY", {})
                if not global_strat.get("RADAR_ACTIVE", False):
                    continue

                entry_rvol = float(global_strat.get("DETECTOR_ENTRY_RVOL", 500.0))
                entry_vbp = float(global_strat.get("DETECTOR_ENTRY_VBP", 65.0))
                entry_tps = float(global_strat.get("DETECTOR_ENTRY_TPS", 2.0))
                exit_rvol = float(global_strat.get("DETECTOR_EXIT_RVOL", 100.0))
                exit_vbp = float(global_strat.get("DETECTOR_EXIT_VBP", 50.0))
                exit_tps = float(global_strat.get("DETECTOR_EXIT_TPS", 0.5))
                
                # 추가 파라미터 로드
                spread_filter_max_pct = float(global_strat.get("SPREAD_FILTER_MAX_PCT", 0.15))
                spread_filter_mult = float(global_strat.get("SPREAD_FILTER_MULT", 1.5))
                basis_velocity_min = float(global_strat.get("BASIS_VELOCITY_MIN_LIMIT", 0.02))
                simultaneous_spike_limit = int(global_strat.get("SIMULTANEOUS_SPIKE_LIMIT", 3))
                min_price_slope_3s = float(global_strat.get("MIN_PRICE_SLOPE_3S", 0.05))

                # [NEW] 초단타 유동성 및 괴리율 필터 파라미터 로드
                # 최근 1분 거래액 최소 기준 (기본: 1억원 = 초단타 체결 가능한 최소 유동성)
                min_trade_val_1m = float(global_strat.get("DETECTOR_MIN_TRADE_VAL_1M", 100_000_000.0))
                # 기저 EMA 대비 김치프리미엄 상대 상승 최소 기준 (%, 기본: 0.05%)
                kimchi_delta_min = float(global_strat.get("DETECTOR_KIMCHI_DELTA_MIN", 0.05))
                # 김치프리미엄 기저 EMA 감쇠율 (느릴수록 기저값이 안정적, 기본: 0.002)
                kimchi_ema_alpha = float(global_strat.get("KIMCHI_PREMIUM_EMA_ALPHA", 0.002))
                # USDT/KRW 환율 (공유 상태에서 가져옴)
                usdt_krw = float(self.shared_state.data.get('__global__', {}).get('usdt_krw', 1380.0) or 1380.0)

                # 글로벌 선물 선행 및 패널티 설정 로드
                global_lead_lag_pool = config.get("GLOBAL_LEAD_LAG_POOL", [])
                unlisted_penalty = config.get("UNLISTED_ASSET_PENALTY", {
                    "RVOL_MULTIPLIER": 2.0,
                    "TPS_MULTIPLIER": 1.8,
                    "VBP_MIN_LIMIT": 75.0,
                    "BUDGET_CUT_RATIO": 0.3,
                    "INTENSITY_MIN_LIMIT": 180.0,
                    "TPS_MIN_LIMIT": 3.0
                })
                profiling_strat = config.get("PROFILING_STRATEGY", {
                    "MIN_RVOL_FLOOR": 150.0,
                    "MIN_TPS_FLOOR": 1.0,
                    "Z_SCORE_THRESHOLD": 5.0
                })

                # 현재 시뮬레이터에 진입해 있는 포지션 정보
                sim_data = self.shared_state.data.get('__sim__', {})
                positions = sim_data.get('positions', {})

                # 초기 웜업 기간 체크 (기동/재연결 직후에는 EMA 통계가 수렴하지 않아 Z-Score 가 왜곡되므로 유예)
                is_warmup = (self.start_time is None) or (self._warmup_until is None) or (now < self._warmup_until)

                # ==========================================
                # [PASS 1] 전체 종목 지표 계산 및 1차 스파이크 후보 수집
                # ==========================================
                raw_metrics = {}
                spike_candidates = {}

                for symbol in self.krw_symbols:
                    # 실시간 스프레드 구하기
                    market_data = self.shared_state.data.get(symbol, {})
                    ob_data = market_data.get("orderbook")
                    curr_spread = 0.0
                    has_spread = False
                    if ob_data:
                        units = ob_data.get("units", []) if isinstance(ob_data, dict) else getattr(ob_data, "units", [])
                        if units:
                            first_unit = units[0]
                            best_ask = float(first_unit.get("ask_price", 0.0) if isinstance(first_unit, dict) else getattr(first_unit, "ask_price", 0.0))
                            best_bid = float(first_unit.get("bid_price", 0.0) if isinstance(first_unit, dict) else getattr(first_unit, "bid_price", 0.0))
                            if best_ask > 0 and best_bid > 0:
                                curr_spread = ((best_ask - best_bid) / best_ask) * 100
                                has_spread = True

                    # 바이낸스 선물/현물 레퍼런스 가격 획득
                    base_asset = symbol.split("-")[1] if "-" in symbol else symbol
                    ref_price = 0.0
                    binance_spot_p = 0.0
                    binance_fut_p = 0.0
                    if self.futures_radar is not None:
                        ref_price = self.futures_radar.get_futures_price(base_asset)
                        if ref_price > 0:
                            binance_fut_p = ref_price
                    if ref_price <= 0:
                        sym_data = self.shared_state.data.get(symbol, {})
                        binance_spot_p  = float(sym_data.get('binance_price', 0.0) or 0.0)
                        binance_fut_p   = float(sym_data.get('binance_futures_price', 0.0) or 0.0)
                        ref_price = binance_fut_p if binance_fut_p > 0 else binance_spot_p

                    # C++ 모듈 호출
                    metrics = self.core_tracker.get_metrics(
                        symbol,
                        now,
                        usdt_krw,
                        ref_price,
                        curr_spread,
                        has_spread,
                        kimchi_ema_alpha,
                        0.005 # alpha
                    )

                    if not metrics or metrics.get("price", 0.0) <= 0.0:
                        continue

                    self._eval_samples[symbol] += 1
                    has_enough_samples = self._eval_samples[symbol] >= MIN_SAMPLES_FOR_ZSCORE

                    p_now = metrics["price"]
                    total_value = metrics["total_value"]
                    vbp = metrics["vbp"]
                    tps = metrics["tps"]
                    rvol = metrics["rvol"]
                    trade_val_1m_total = metrics["trade_val_1m"]
                    kimchi_delta = metrics["kimchi_delta"]
                    p_3s = metrics["p_3s"]

                    mean_val = metrics["mean_val"]
                    std_val = metrics["std_val"]

                    has_min_liquidity_1m = (trade_val_1m_total >= min_trade_val_1m)
                    has_kimchi_signal = (kimchi_delta >= kimchi_delta_min)

                    # Z-Score 기반 동적 쿨다운 캐싱
                    z_score_val = (total_value - mean_val) / std_val if std_val > 0 else 0.0
                    self.cooldown_durations[symbol] = max(30.0, min(180.0, 60.0 * (z_score_val / 5.0)))

                    # 2) 해외 메인 마켓 선물 선행 감시 및 2단계 크로스 검증
                    is_unlisted = True
                    is_lead_lag_valid = True
                    leader_info = None
                    
                    for item in global_lead_lag_pool:
                        if item.get("symbol") == symbol:
                            is_unlisted = False
                            leader_info = item
                            break
                            
                    if leader_info:
                        leader_sym = leader_info.get("leader_symbol")
                        lead_z_threshold = float(leader_info.get("lead_threshold_z_score", 6.0))
                        max_slippage = float(leader_info.get("max_entry_slippage_pct", 0.15))
                        
                        leader_data = self.shared_state.data.get(leader_sym, {})
                        futures_intensity = float(leader_data.get("intensity", 0.0) or leader_data.get("binance_futures_intensity", 0.0))
                        futures_price = float(leader_data.get("binance_futures_price", 0.0) or leader_data.get("price", 0.0))
                        
                        has_basis_momentum = False
                        if p_now > 0 and futures_price > 0:
                            basis_pct = ((futures_price - p_now) / p_now) * 100
                            last_basis_val = self.last_basis.get(symbol, basis_pct)
                            basis_velocity = (basis_pct - last_basis_val) / delta_t
                            
                            self.basis_velocity_ema[symbol] = 0.5 * basis_velocity + 0.5 * self.basis_velocity_ema.get(symbol, 0.0)
                            self.last_basis[symbol] = basis_pct
                            has_basis_momentum = (self.basis_velocity_ema[symbol] >= basis_velocity_min)
                        
                        has_lead_vol = (futures_intensity >= 180.0)
                        has_good_slippage = self.check_upbit_slippage(symbol, 1000000.0, max_slippage, spread_filter_max_pct, spread_filter_mult)
                        
                        if has_lead_vol and has_basis_momentum and has_good_slippage:
                            is_lead_lag_valid = True
                            logging.info(f"[LeadLagRadar] 🎯 Cross Validation Success for {symbol}! (Velocity: {self.basis_velocity_ema[symbol]:+.4f}%/s, Futures Intensity: {futures_intensity:.1f}, Slippage: OK)")
                        else:
                            is_lead_lag_valid = False

                    # 3) 가변 임계값 Z-Score 판정 & 패널티 대입
                    z_limit = float(profiling_strat.get("Z_SCORE_THRESHOLD", 5.0))
                    min_rvol = float(profiling_strat.get("MIN_RVOL_FLOOR", 150.0))
                    min_tps = float(profiling_strat.get("MIN_TPS_FLOOR", 1.0))
                    
                    curr_entry_rvol = entry_rvol
                    curr_entry_tps = entry_tps
                    curr_entry_vbp = entry_vbp
                    is_kimchi_valid = True
                    
                    if is_unlisted:
                        rvol_mult = float(unlisted_penalty.get("RVOL_MULTIPLIER", 2.0))
                        tps_mult = float(unlisted_penalty.get("TPS_MULTIPLIER", 1.8))
                        vbp_limit = float(unlisted_penalty.get("VBP_MIN_LIMIT", 75.0))
                        intensity_min = float(unlisted_penalty.get("INTENSITY_MIN_LIMIT", 180.0))
                        tps_min = float(unlisted_penalty.get("TPS_MIN_LIMIT", 3.0))
                        
                        curr_entry_rvol = entry_rvol * rvol_mult
                        curr_entry_tps = entry_tps * tps_mult
                        curr_entry_vbp = max(entry_vbp, vbp_limit)
                        
                        local_intensity = (vbp / (100 - vbp) * 100) if vbp < 100 else 999.0
                        is_kimchi_valid = (local_intensity >= intensity_min) and (tps >= tps_min)

                    # 4) [NEW] 3초 가격 기울기 검증 (MIN_PRICE_SLOPE_3S)
                    has_price_slope = True
                    if p_3s > 0.0:
                        slope_3s = ((p_now / p_3s) - 1) * 100
                        if slope_3s < min_price_slope_3s:
                            has_price_slope = False
                    else:
                        has_price_slope = True

                    # Z-Score 조건 & 하한 장벽 & 가격 모멘텀 필터링
                    is_spike = (total_value >= mean_val + z_limit * std_val) and (rvol >= min_rvol) and (tps >= min_tps) and has_price_slope

                    raw_metrics[symbol] = {
                        "price": p_now,
                        "rvol": rvol,
                        "vbp": vbp,
                        "tps": tps,
                        "total_value": total_value,
                        "is_spike": is_spike,
                        "trade_val_1m": trade_val_1m_total,
                        "kimchi_delta": kimchi_delta
                    }

                    # HOT-ALERT 진입 임시 승인 후보 등재
                    passes_new_filters = has_min_liquidity_1m and (has_kimchi_signal or not (binance_spot_p > 0 or binance_fut_p > 0))
                    if not (binance_spot_p > 0 or binance_fut_p > 0):
                        passes_new_filters = has_min_liquidity_1m

                    if not is_warmup and has_enough_samples and is_spike and rvol >= curr_entry_rvol and vbp >= curr_entry_vbp and tps >= curr_entry_tps and is_kimchi_valid and is_lead_lag_valid and passes_new_filters:
                        spike_candidates[symbol] = {
                            "is_unlisted": is_unlisted,
                            "leader_info": leader_info
                        }

                # ==========================================
                # [PASS 2] 동시성 덤핑 차단 필터링 및 최종 활성화 집행
                # ==========================================
                active_spikes = set(spike_candidates.keys())
                
                if len(spike_candidates) > simultaneous_spike_limit:
                    logging.warning(
                        f"[SpikeRadar] 🚫 Simultaneous Spike limit exceeded ({len(spike_candidates)} > {simultaneous_spike_limit}). "
                        f"Market-wide co-movement dumping suspected. Triggering protective eviction..."
                    )
                    
                    # 해외 선물 선행성이 보장되지 않는 일반 알트코인은 진입 차단 (메이저 코인만 보호 승인)
                    for cand_sym, info in list(spike_candidates.items()):
                        if info["is_unlisted"] or (info["leader_info"] is None):
                            active_spikes.discard(cand_sym)
                            logging.warning(f"[SpikeRadar] 🛡️ Blocked simultaneous altcoin spike: {cand_sym}")

                # 최종 렌더링 및 auto_switch 트리거
                scanner_results = []
                for symbol in self.krw_symbols:
                    if symbol not in raw_metrics:
                        continue
                        
                    metrics = raw_metrics[symbol]
                    state = "SCANNING"
                    
                    if symbol in positions:
                        state = "TRADING"
                    elif symbol in active_spikes:
                        state = "HOT-ALERT"
                        is_unlisted_flag = spike_candidates[symbol]["is_unlisted"]
                        if is_unlisted_flag:
                            logging.warning(f"[SpikeRadar] ⚠️ Penalty Spike verified for unlisted asset {symbol}!")
                        else:
                            logging.info(f"[SpikeRadar] 🚀 Standard Spike verified via Lead-Lag for {symbol}!")
                            
                        # 안전하게 스위처 태스크 스폰
                        asyncio.create_task(self.auto_switch_target(symbol, is_unlisted=is_unlisted_flag))

                    scanner_results.append({
                        "symbol": symbol,
                        "price": metrics["price"],
                        "rvol": metrics["rvol"],
                        "vbp": metrics["vbp"],
                        "tps": metrics["tps"],
                        "state": state,
                        "value_10s": metrics["total_value"]
                    })

                # RVOL을 기준으로 내림차순 정렬 후 Top 5 선별
                scanner_results.sort(key=lambda x: x['rvol'], reverse=True)
                top_5 = scanner_results[:5]

                # shared_state에 실시간 노출 (오프로딩)
                if self._radar_update_task is None or self._radar_update_task.done():
                    loop = asyncio.get_running_loop()
                    self._radar_update_task = loop.run_in_executor(None, _set_shared_item, self.shared_state.data, '__radar_scanner__', top_5)

                # 실시간 자동 이탈(Fadeout) 로직 실행
                if config and not is_warmup:
                    await self.check_and_exit_targets(config, config_path, scanner_results, positions, exit_rvol, exit_vbp, exit_tps)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[ScalpingTargetRadar] Evaluation execution error: {e}")

    async def check_and_exit_targets(self, config, config_path, scanner_results, positions, exit_rvol, exit_vbp, exit_tps):
        """ACTIVE TARGETS 중 이탈 조건을 충족하고 포지션이 없는 종목들을 스캔하여 자동으로 active=False로 전환(이탈) 처리"""
        targets = config.get("SCALPING_TARGETS", [])
        now = time.time()
        
        # 파일 쓰기 동기화 락 활용
        if not hasattr(self, 'file_lock'):
            self.file_lock = asyncio.Lock()
            
        # 스캐너 결과 맵 생성
        stats_map = {s["symbol"]: s for s in scanner_results}
        
        exit_symbols = []
        for t in targets:
            symbol = t.get("symbol")
            if not t.get("active", True) or not t.get("auto_trade", True):
                continue
            
            # 고정 종목(pinned)은 자동 이탈(Fadeout)에서 제외(보호)
            if t.get("pinned", False):
                continue
            
            # KIS 종목 등 Upbit Ticker 감시 대상이 아닌 경우 건너뜀
            if not symbol.startswith("KRW-"):
                continue
                
            # 포지션이 없어야 함
            if symbol in positions:
                continue
                
            # 요동 방지: 등록/수정이 일어난 지 동적 쿨다운(30초~180초)이 경과해야 함
            cooldown_dur = self.cooldown_durations.get(symbol, 60.0)
            if symbol in self.cooldowns and now - self.cooldowns[symbol] < cooldown_dur:
                continue
                
            # 실시간 통계 획득
            stats = stats_map.get(symbol)
            if not stats:
                continue
                
            # 이탈 조건 검사 (RVOL, VBP, TPS 중 하나라도 이탈 임계값 미만으로 소멸했을 때)
            curr_rvol = stats["rvol"]
            curr_vbp = stats["vbp"]
            curr_tps = stats["tps"]
            
            if curr_rvol < exit_rvol or curr_vbp < exit_vbp or curr_tps < exit_tps:
                logging.info(f"[ScalpingTargetRadar] Symbol {symbol} exit conditions met (RVOL: {curr_rvol:.1f}% < {exit_rvol}%, VBP: {curr_vbp:.1f}% < {exit_vbp}%, TPS: {curr_tps:.1f} < {exit_tps}). Initiating automatic exit...")
                exit_symbols.append(symbol)
                
        if exit_symbols:
            async with self.file_lock:
                try:
                    # 락 안에서 최신 config 를 재읽기 후 갱신 (평가 시점의 stale config 로 덮어써
                    # auto_switch_target 등 다른 갱신을 유실하는 read-modify-write 경쟁 방지)
                    with open(config_path, "r", encoding="utf-8") as f:
                        fresh = json.load(f)
                    changed = False
                    for ft in fresh.get("SCALPING_TARGETS", []):
                        fsym = ft.get("symbol")
                        if (fsym in exit_symbols and ft.get("active", True) and ft.get("auto_trade", True)
                                and not ft.get("pinned", False) and fsym not in positions):
                            ft["active"] = False
                            ft["auto_trade"] = False
                            changed = True
                    if not changed:
                        return
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, _write_json_file, config_path, fresh)
                    self.shared_state.cmd_queue.put({"type": "reload_config"})
                    logging.info("[ScalpingTargetRadar] Automated target exit complete. Config reloaded.")
                except Exception as e:
                    logging.error(f"[ScalpingTargetRadar] Failed to write config in check_and_exit_targets: {e}")

    async def stop(self):
        logging.info("[ScalpingTargetRadar] Gracefully shutting down...")
        self.is_running = False
        
        # HTTP 세션 닫기
        if self.http_session:
            try:
                await self.http_session.close()
            except:
                pass
        
        # 웹소켓 닫기
        if self.ws:
            try:
                await self.ws.close()
            except:
                pass
                
        # 모든 비동기 태스크 캔슬
        for t in self.tasks:
            if not t.done():
                t.cancel()
                
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
            self.tasks.clear()
            
        logging.info("[ScalpingTargetRadar] Graceful shutdown complete.")

    async def auto_switch_target(self, symbol: str, is_unlisted: bool = False):
        """HOT 종목 감지 시 config를 갱신하고 엔진에 reload_config를 요청하여 default_scalping으로 자동 매핑 구동"""
        now = time.time()
        # 30초 쿨다운 검사 (과도한 파일 I/O 및 중복 리로드 방지)
        if symbol in self.cooldowns and now - self.cooldowns[symbol] < 30.0:
            return
        self.cooldowns[symbol] = now

        # 최신 가격 획득
        metrics = self.core_tracker.get_metrics(symbol, now, 0.0, 0.0, 0.0, False, 0.0, 0.0)
        trigger_price = metrics.get("price", 0.0)

        # 1. 최대 베팅 가능액(유동성) 추정 및 사전 필터링
        max_slippage = 0.2 # 기본 최대 허용 슬리피지 0.2%
        max_bettable = await self.estimate_max_bettable_amount(symbol, max_slippage)

        # 파일 락 및 설정 파일 로드
        if not hasattr(self, 'file_lock'):
            self.file_lock = asyncio.Lock()
            
        async with self.file_lock:
            try:
                import os
                config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "trading_config.json")
                
                # 파일 읽기
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                
                global_strategy = config.get("GLOBAL_STRATEGY", {})
                max_active = global_strategy.get("MAX_ACTIVE_TARGETS", 10)
                min_liquidity = float(global_strategy.get("DETECTOR_MIN_LIQUIDITY", 500000.0))
                unlisted_penalty = config.get("UNLISTED_ASSET_PENALTY", {
                    "BUDGET_CUT_RATIO": 0.3
                })
                
                # 유동성 사전 필터링 체크
                if max_bettable < min_liquidity:
                    logging.info(f"[ScalpingTargetRadar] 🚫 Pre-filtered {symbol} due to low liquidity: {max_bettable:,.0f} KRW (min required: {min_liquidity:,.0f} KRW)")
                    return
                
                logging.info(f"[ScalpingTargetRadar] HOT Spike detected for {symbol}! Initiating auto target switching...")
                
                targets = config.get("SCALPING_TARGETS", [])
                
                # 종목이 이미 리스트에 있는지 확인
                found = False
                target_node = None
                for t in targets:
                    if t.get("symbol") == symbol:
                        found = True
                        target_node = t
                        break

                # 활성 상태로 만들거나 신규 추가하기 전에, active 개수를 계산하고 초과 시 Eviction 수행
                active_count = sum(1 for t in targets if t.get("active", True))
                
                # 새로 활성화할지 여부 결정 (이미 active=True 인 경우는 증가 없음)
                will_increase = False
                if not found:
                    will_increase = True
                elif target_node and (not target_node.get("active") or not target_node.get("auto_trade")):
                    will_increase = True

                if will_increase and active_count >= max_active:
                    # 현재 포지션을 보유하지 않은 활성 종목을 찾아 비활성화(Evict) 처리
                    sim_data = self.shared_state.data.get('__sim__', {})
                    positions = sim_data.get('positions', {})
                    
                    evicted = False
                    for t in reversed(targets):  # 뒤쪽에서부터 교체 대상 탐색
                        cand_sym = t.get("symbol")
                        if cand_sym != symbol and t.get("active", True) and cand_sym not in positions and not t.get("pinned", False):
                            t["active"] = False
                            t["auto_trade"] = False
                            logging.info(f"[ScalpingTargetRadar] MAX_ACTIVE_TARGETS ({max_active}) limit reached. Evicting {cand_sym} (no active positions) to make room for {symbol}.")
                            evicted = True
                            break
                    if not evicted:
                        logging.warning(f"[ScalpingTargetRadar] MAX_ACTIVE_TARGETS ({max_active}) limit reached but all active targets have positions. Allow exceeding temporarily.")

                default_budget = 1000000
                
                # [NEW] 해외 미상장(김치코인) 자산인 경우 베팅 예산 30% 수준으로 자동 강제 삭감
                if is_unlisted:
                    cut_ratio = float(unlisted_penalty.get("BUDGET_CUT_RATIO", 0.3))
                    default_budget = int(default_budget * cut_ratio)
                    logging.warning(f"[ScalpingTargetRadar] ⚠️ Unlisted Penalty Mode: Auto-scaled default budget to {default_budget:,.0f} KRW.")

                adjusted_budget = int(min(default_budget, max_bettable))

                if found:
                    # 비활성화된 종목 활성화 처리
                    if not target_node.get("active") or not target_node.get("auto_trade"):
                        target_node["active"] = True
                        target_node["auto_trade"] = True
                        # 유동성 제한에 맞게 예산 조절
                        current_budget = target_node.get("budget_per_trade", default_budget)
                        target_node["budget_per_trade"] = int(min(current_budget, max_bettable))
                        logging.info(f"[ScalpingTargetRadar] Reactivating existing target {symbol} for auto-trading with budget: {target_node['budget_per_trade']:,.0f} KRW.")
                else:
                    # 신규 종목을 기본 단타 전략으로 등록하여 삽입
                    new_target = {
                        "symbol": symbol,
                        "exchange": "UPBIT",
                        "active": True,
                        "auto_trade": True,
                        "strategy_name": "default_scalping",
                        "budget_per_trade": adjusted_budget,
                        "reference_market": "BINANCE",
                        "orderbook_level": 0
                    }
                    targets.append(new_target)
                    logging.info(f"[ScalpingTargetRadar] Dynamically registered new HOT target {symbol} to default_scalping strategy with budget: {adjusted_budget:,.0f} KRW.")
                
                config["SCALPING_TARGETS"] = targets
                
                # 파일 쓰기
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _write_json_file, config_path, config)
                
                # 엔진에 핫 리로드 명령 송신
                self.shared_state.cmd_queue.put({"type": "reload_config"})
                logging.info(f"[ScalpingTargetRadar] Target auto-switch complete for {symbol}. Engine reload triggered.")
                
                # 2. 사후 검증 성능 추적 시작 (비동기 비차단 태스크 스폰)
                asyncio.create_task(self.track_spike_performance(symbol, trigger_price))
                
            except Exception as e:
                logging.error(f"[ScalpingTargetRadar] Failed to auto switch target for {symbol}: {e}")

    async def estimate_max_bettable_amount(self, symbol: str, max_slippage_pct: float = 0.2) -> float:
        """
        업비트 호가 API를 실시간 호출하여 지정한 최대 슬리피지 내에서 
        즉시 매수 가능한 최대 원화(KRW) 가치를 추정합니다. (유동성 사전 필터링용)
        """
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession()
            
        try:
            # 업비트 호가 REST API 비동기 호출 (최대 5초 타임아웃 적용하여 데드락 예방)
            async with self.http_session.get(f"https://api.upbit.com/v1/orderbook?markets={symbol}", timeout=aiohttp.ClientTimeout(total=5.0)) as res:
                if res.status == 200:
                    data = await res.json()
                    if data and len(data) > 0:
                        ob_data = data[0]
                        units = ob_data.get('orderbook_units', [])
                        if not units:
                            return 0.0
                        
                        best_ask = float(units[0]['ask_price'])
                        price_limit = best_ask * (1 + max_slippage_pct / 100.0)
                        
                        max_krw = 0.0
                        for u in units:
                            ask_price = float(u['ask_price'])
                            ask_size = float(u['ask_size'])
                            if ask_price <= price_limit:
                                max_krw += ask_price * ask_size
                            else:
                                break # 가격 초과 시 중단
                        
                        logging.info(f"[ScalpingTargetRadar] Liquidity check for {symbol}: Best Ask: {best_ask:,.0f}, Limit Price (+{max_slippage_pct}%): {price_limit:,.0f}, Max Bettable: {max_krw:,.0f} KRW")
                        return max_krw
                else:
                    logging.warning(f"[ScalpingTargetRadar] Upbit orderbook API returned HTTP {res.status} for {symbol}")
        except Exception as e:
            logging.error(f"[ScalpingTargetRadar] Failed to estimate max bettable amount for {symbol}: {type(e).__name__} - {e}")
            # REST API 호출 실패 시 Fallback 로직 가동
            # 1분 누적 거래대금(self.core_tracker)을 기준으로 최소 유동성 만족 여부를 우회 판정
            try:
                now_ts = time.time()
                metrics = self.core_tracker.get_metrics(symbol, now_ts, 0.0, 0.0, 0.0, False, 0.0, 0.0)
                trade_val_1m_total = metrics.get("trade_val_1m", 0.0)
                
                # 최근 1분 거래액이 5천만 원 이상이면, 최소 주문 금액(50만 원)을 소화 가능한 것으로 간주해 임시 100만 원 한도 리턴
                if trade_val_1m_total >= 50000000.0:
                    logging.warning(
                        f"[ScalpingTargetRadar] [Fallback] REST API failed, but 1m trade volume ({trade_val_1m_total:,.0f} KRW) is sufficient. "
                        f"Returning 1,000,000 KRW fallback limit for {symbol}."
                    )
                    return 1000000.0
            except Exception as fe:
                logging.error(f"[ScalpingTargetRadar] Fallback calculation failed for {symbol}: {fe}")
                
        return 0.0

    async def track_spike_performance(self, symbol: str, trigger_price: float):
        """
        HOT 종목 진입 시점 대비 +5초, +15초, +30초, +60초 시점의 가격을 비동기 추적하고 
        최고 상승률 및 최대 낙폭을 검증하여 결과를 저장합니다.
        """
        if trigger_price <= 0:
            return
            
        logging.info(f"[SpikeTracker] 🎯 Starting performance tracking for {symbol} (Trigger Price: {trigger_price:,.0f} KRW)...")
        
        start_time = time.time()
        max_price = trigger_price
        min_price = trigger_price
        
        prices_at = {5: None, 15: None, 30: None, 60: None}
        intervals = [5, 15, 30, 60]
        
        # 60초 동안 지속적으로 가격을 샘플링하고 모니터링
        # 0.1초 주기로 체크하여 최신 가격 갱신 및 Max/Min 측정
        while True:
            now = time.time()
            elapsed = now - start_time
            
            # 최신 가격 획득
            current_price = trigger_price
            metrics = self.core_tracker.get_metrics(symbol, now, 0.0, 0.0, 0.0, False, 0.0, 0.0)
            if metrics and metrics.get("price", 0.0) > 0.0:
                current_price = metrics["price"]
                
            max_price = max(max_price, current_price)
            min_price = min(min_price, current_price)
            
            # 경과 시간에 해당하는 시점의 가격 기록
            for t in intervals:
                if elapsed >= t and prices_at[t] is None:
                    prices_at[t] = current_price
                    
            if elapsed >= 60.0:
                break
                
            await asyncio.sleep(0.1) # 100ms 주기 샘플링
            
        # 미처 채워지지 않은 구간이 있다면 최종 가격으로 채움
        for t in intervals:
            if prices_at[t] is None:
                prices_at[t] = current_price
                
        # 통계 계산
        ret_5s = ((prices_at[5] / trigger_price) - 1) * 100
        ret_15s = ((prices_at[15] / trigger_price) - 1) * 100
        ret_30s = ((prices_at[30] / trigger_price) - 1) * 100
        ret_60s = ((prices_at[60] / trigger_price) - 1) * 100
        max_high = ((max_price / trigger_price) - 1) * 100
        max_drawdown = ((min_price / trigger_price) - 1) * 100
        
        now_ts = time.time()
        timestamp_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now_ts))
        timestamp_kst = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now_ts + 9 * 3600))
        current_version = _read_current_version()

        result_record = {
            "version": current_version,
            "symbol": symbol,
            "timestamp": timestamp_utc,
            "timestamp_kst": timestamp_kst,
            "trigger_price": trigger_price,
            "price_5s": prices_at[5],
            "price_15s": prices_at[15],
            "price_30s": prices_at[30],
            "price_60s": prices_at[60],
            "return_5s": ret_5s,
            "return_15s": ret_15s,
            "return_30s": ret_30s,
            "return_60s": ret_60s,
            "max_high": max_high,
            "max_drawdown": max_drawdown,
            "success_30s": ret_30s > 0.05 # 수수료 감안하여 30초 내에 0.05% 이상 상승 시 성공으로 판정
        }
        
        logging.info(
            f"[SpikeTracker] 📊 {symbol} Spike Verified! "
            f"returns: [+5s: {ret_5s:+.2f}%, +15s: {ret_15s:+.2f}%, +30s: {ret_30s:+.2f}%, +60s: {ret_60s:+.2f}%] "
            f"[Max High: {max_high:+.2f}%, Max Drawdown: {max_drawdown:+.2f}%] "
            f"-> {'SUCCESS' if result_record['success_30s'] else 'FAILED'}"
        )
        
        # 파일 저장 및 통계 업데이트
        await self.save_verification_result(result_record)

    async def save_verification_result(self, record: dict):
        """사후 검증 데이터를 spike_verification_history.json 파일에 비동기 안전하게 저장합니다."""
        import os
        history_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "spike_verification_history.json")
        
        if not hasattr(self, 'verification_lock'):
            self.verification_lock = asyncio.Lock()
            
        async with self.verification_lock:
            history = []
            if os.path.exists(history_path):
                try:
                    with open(history_path, "r", encoding="utf-8") as f:
                        history = json.load(f)
                except Exception as e:
                    logging.error(f"[SpikeTracker] Failed to read verification history: {e}")
                    history = []
            
            # 새 레코드 삽입
            history.append(record)
            
            # 최근 100개의 기록만 유지 (성능 및 파일 크기 제어)
            history = history[-100:]
            
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _write_json_file, history_path, history)
            except Exception as e:
                logging.error(f"[SpikeTracker] Failed to write verification history: {e}")
