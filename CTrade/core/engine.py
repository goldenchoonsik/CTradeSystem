import asyncio
import json
import time
import logging
import os
import sys
import psutil
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Union, Set
from utils.auth import KISAuthManager
import aiohttp # aiohttp 임포트 추가

KST = timezone(timedelta(hours=9))
from connectors.kis_connector import KISConnector
from connectors.upbit_connector import UpbitConnector
from connectors.binance_connector import BinanceConnector
from connectors.bitget_connector import BitgetConnector
from connectors.coinbase_connector import CoinbaseConnector
from core.data_manager import DataManager
from utils.portfolio_manager import PortfolioManager
from utils.upbit_auth import UpbitAuth
from core.simulator import VirtualTrader
from core.real_trader import UpbitRealTrader
from core.kis_trader import KISTrader, is_domestic_krx_code
from core.order_executor import SmartOrderExecutor
from core.rule_engine import StrategyManager
from core.optimizer import StrategyOptimizer
from core.models import TickData
from core.tick_router import route_tick

# shared/ AI 전략 모듈 경로 확보
_CTRADE_SYSTEM_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CTRADE_SYSTEM_ROOT not in sys.path:
    sys.path.insert(0, _CTRADE_SYSTEM_ROOT)
try:
    from core.ai_strategy import AIStrategyModule, SignalAction, SignalReason
    from shared.core.market_utils import detect_market_type, parse_interval_minutes, parse_interval_seconds
    AI_STRATEGY_AVAILABLE = True
except ImportError as _e:
    logging.warning(f"[Engine] AI Strategy Module not available: {_e}")
    AI_STRATEGY_AVAILABLE = False
    from shared.core.market_utils import detect_market_type, parse_interval_minutes, parse_interval_seconds

from shared.core import stream_keys as sk  # CTrade 실시간 스트림 키 규약 (접미사 _PERP/_BPERP 정의는 여기 한 곳)
from shared.core.trade_costs import round_trip_cost, sell_tax_for_symbol

DEFAULT_AI_BUDGET = 6000


def _resolve_deploy_config_snapshot(deploy_base: str, deploy_session: str) -> Optional[str]:
    """배포 모델의 config_snapshot.json 파일 경로를 다중 후보 경로로 안전하게 탐색"""
    if not deploy_base or not deploy_session:
        return None
    candidates = []
    if os.path.isabs(deploy_base):
        candidates.append(os.path.join(deploy_base, deploy_session, "config_snapshot.json"))
    else:
        _core_dir = os.path.dirname(os.path.abspath(__file__))
        _ctrade_dir = os.path.dirname(_core_dir)
        _root_dir = os.path.dirname(_ctrade_dir)
        candidates.extend([
            os.path.normpath(os.path.join(_ctrade_dir, deploy_base, deploy_session, "config_snapshot.json")),
            os.path.normpath(os.path.join(_root_dir, deploy_base, deploy_session, "config_snapshot.json")),
            os.path.normpath(os.path.join(_root_dir, "shared", "deploy", deploy_base, deploy_session, "config_snapshot.json")),
            os.path.normpath(os.path.join(_root_dir, "CAiModelBaker", "deploy", deploy_base, deploy_session, "config_snapshot.json")),
        ])
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _resolve_target_reference_symbols(t_conf: dict, templates: dict) -> list:
    """활성 종목(t_conf)이 실시간으로 자동 구독(Silent Subscribe)해야 할 참조 대장주
    (Reference Symbols) 목록을 종목 개별 설정 + 전략 템플릿 + 배포된 AI 모델의
    config_snapshot.json, 세 소스를 모두 종합하여 산출하는 단일 진입점.

    배경(2026-09-22 장애): 이 로직이 engine.py 내에 __init__(구독 목록/캔들 집계기/AI 모듈
    집계기)과 reload_config 에 각각 따로 구현되어 있었고, 그중 일부는 deploy_session/
    deploy_base 를 종목 개별 설정(t_conf)에서만 읽어 STRATEGY_TEMPLATES 쪽에 정의된
    값으로 폴백하지 않거나, config_snapshot.json 조회 자체를 빠뜨리고 있었다. 그 결과
    새로 배포된 모델이 요구하는 참조종목(예: 대장주 스프레드 피처용 삼성전자/SK하이닉스)의
    실시간 시세가 전혀 구독되지 않아, 피처 계산이 매 캔들 KeyError 로 실패하고 전량
    매매가 불가능한 상태가 지속됐다. 향후 어떤 모델을 배포하더라도 구독 누락이 재발하지
    않도록, 참조종목을 사용하는 모든 지점(위 4곳)은 반드시 이 함수 하나만 호출해야 한다.

    Args:
        t_conf: SCALPING_TARGETS 의 원본(raw) 종목 설정 딕셔너리 (merge_strategy 결과든 원본이든 무방)
        templates: STRATEGY_TEMPLATES 딕셔너리 (config.get("STRATEGY_TEMPLATES", {}))

    Returns:
        참조종목 항목 리스트 ({"symbol":..,"alias":..,...} 형태의 dict 또는 문자열 심볼이 섞여 있을 수 있음)
    """
    strat_name = t_conf.get("strategy_name", "")
    strat_tmpl = templates.get(strat_name, {}) if templates else {}

    # 1. 종목 개별 설정 + 전략 템플릿에 직접 선언된 reference_symbols 결합
    ref_list = list(t_conf.get("reference_symbols") or [])
    for r_item in (strat_tmpl.get("reference_symbols") or []):
        if r_item not in ref_list:
            ref_list.append(r_item)

    # 2. deploy_session/deploy_base 해석 — 종목 개별 설정 우선, 없으면 전략 템플릿 값으로 폴백
    #    (AI 모델 로딩 경로(AIStrategyModule 생성부)와 동일한 폴백 순서를 유지해야 한다)
    deploy_session = t_conf.get("deploy_session") or strat_tmpl.get("deploy_session") or ""
    deploy_base = t_conf.get("deploy_base") or strat_tmpl.get("deploy_base") or t_conf.get("symbol", "")

    # 3. 배포된 모델의 config_snapshot.json 에 기록된 학습 당시 reference_symbols 를 병합
    #    (모델이 실제로 입력받는 피처 기준이므로 가장 신뢰도 높은 소스)
    if deploy_session:
        deploy_cfg_path = _resolve_deploy_config_snapshot(deploy_base, deploy_session)
        if deploy_cfg_path and os.path.exists(deploy_cfg_path):
            try:
                with open(deploy_cfg_path, "r", encoding="utf-8") as dcf:
                    d_cfg = json.load(dcf)
                for r_item in (d_cfg.get("reference_symbols") or d_cfg.get("REFERENCE_SYMBOLS") or []):
                    if r_item not in ref_list:
                        ref_list.append(r_item)
            except Exception as e:
                logging.warning(
                    f"[Engine] ⚠️ 배포 모델 config_snapshot 참조종목 로드 실패 "
                    f"(symbol={t_conf.get('symbol')}, deploy={deploy_base}/{deploy_session}): {e} "
                    f"— 해당 모델이 요구하는 참조종목 실시간 구독이 누락될 수 있습니다."
                )
        else:
            logging.warning(
                f"[Engine] ⚠️ deploy_session({deploy_session})이 지정되었지만 config_snapshot.json을 "
                f"찾지 못했습니다 (symbol={t_conf.get('symbol')}, deploy_base={deploy_base}) — "
                f"참조종목 자동구독이 누락될 수 있습니다."
            )

    return ref_list


def _set_shared_item(shared_dict, key, value):
    """multiprocessing.managers.SyncManager 프록시 딕셔너리에 동기식으로 값을 설정하는 블로킹 IPC 함수"""
    try:
        shared_dict[key] = value
    except Exception as e:
        import logging
        logging.error(f"[SyncState] Failed to set {key} in shared_state: {e}")

class STradeEngine:
    def __init__(self, config):
        self.config = config
        self.queue = asyncio.Queue()
        self.ui_levels = {}
        
        # 1. 룰 엔진(StrategyManager) 초기화
        self.strategy_manager = StrategyManager(config)
        self.optimizer = StrategyOptimizer()
        self.start_time = time.time()
        self.warmup_period = 30 # 초기 데이터 수집을 위한 30초 웜업 기간
        self.last_exit_times = {}
        # 5초 확인 지연 진입용: {symbol: {"trigger_time": float, "trigger_price": float}}
        self.entry_pending: dict = {}

        # 1-1. 전역 리스크 관리자 (전 종목 통합 단일 인스턴스)
        from core.risk_manager import RiskManager
        risk_cfg = config.get("GLOBAL_STRATEGY", {}).get("RISK_MANAGEMENT", {})
        self.global_risk_manager = RiskManager(
            daily_loss_limit_pct=risk_cfg.get("DAILY_LOSS_LIMIT_PCT", -3.0),
            max_consecutive_losses=risk_cfg.get("MAX_CONSECUTIVE_LOSSES", 3),
            consecutive_loss_cooldown_min=risk_cfg.get("CONSECUTIVE_LOSS_COOLDOWN_MIN", 60),
            max_concurrent_positions=risk_cfg.get("MAX_CONCURRENT_POSITIONS", 3)
        )

        
        # 2. 설정 파일에서 타겟 심볼 및 외부 참조 심볼 자동 추출 (Auto-Binding & 가상 심볼 맵핑)
        self.stock_symbols = []
        self.silent_stock_symbols: Set[str] = set() # 참조 전용(선행지표 계산용) 대장주 심볼
        self.crypto_symbols = []
        self.binance_symbols = set() # 중복 방지
        self.bitget_symbols = set() # 비트겟 중복 방지
        
        # 가상 심볼 매핑 테이블
        self.ref_mapping = {}          # {가상심볼: 실제거래소심볼}
        self.reverse_ref_mapping = {}  # {실제거래소심볼: [가상심볼1, 가상심볼2]}
        
        from core.rule_engine import merge_strategy
        targets = config.get("SCALPING_TARGETS", [])
        templates = config.get("STRATEGY_TEMPLATES", {})
        for t_orig in targets:
            # 설정 템플릿과 개별 종목 사양 동적 결합
            t = merge_strategy(t_orig, config)
            
            # 시스템 활성화 체크: 꺼진 종목은 구독 목록에서 제외하여 자원 절약
            if not t.get("active", True):
                continue
                
            sym = t["symbol"]
            ref_sym = t.get("reference_symbol") or sym
            self.ref_mapping[sym] = ref_sym
            self.reverse_ref_mapping.setdefault(ref_sym, []).append(sym)

            # AI_BAKER 전략은 룰 기반 Optimizer를 사용하지 않으므로 스킵
            strat_tmpl = templates.get(t.get("strategy_name", ""), {})
            if strat_tmpl.get("type") != "AI_BAKER":
                self.optimizer.init_symbol(sym, t)
            exch = t.get("exchange", "")
            if exch == "UPBIT":
                if ref_sym not in self.crypto_symbols:
                    self.crypto_symbols.append(ref_sym)
                base_coin = ref_sym.split("-")[-1]
                
                # 참조 마켓에 따라 시세 자동 구독 (글로벌 기준가 및 선물 시장가)
                ref_market = t.get("reference_market", "BINANCE")
                if ref_market == "BITGET":
                    self.bitget_symbols.add(sk.spot_key(base_coin))
                    self.bitget_symbols.add(sk.bitget_futures_key(base_coin))
                else:
                    self.binance_symbols.add(sk.spot_key(base_coin))
                    self.binance_symbols.add(sk.binance_futures_key(base_coin))
                
                # 해당 거래소 자체 USDT 마켓 시세 구독 추가 (사용자 요청)
                native_usdt_pair = f"USDT-{base_coin}"
                if native_usdt_pair not in self.crypto_symbols:
                    self.crypto_symbols.append(native_usdt_pair)
            elif exch == "KIS":
                self.stock_symbols.append(sym)

            # 동적 참조 대장주(Reference Symbols) 자동 구독(Silent Subscribe) 등록
            # (종목 설정 + 전략 템플릿 + 배포 모델 config_snapshot.json 종합 — _resolve_target_reference_symbols 참고)
            ref_list = _resolve_target_reference_symbols(t_orig, templates)

            for r_item in ref_list:
                if isinstance(r_item, dict):
                    r_sym = str(r_item.get("symbol", "")).strip().upper()
                else:
                    r_sym = str(r_item).strip().upper()
                if r_sym:
                    if r_sym not in self.stock_symbols:
                        self.stock_symbols.append(r_sym)
                    self.silent_stock_symbols.add(r_sym)
                
            # AI_BAKER 전략은 룰 기반 지표 스캔이 필요 없으므로 스킵
            if strat_tmpl.get("type") == "AI_BAKER":
                continue

            # 룰 내부에 외부 지표(예: ETHUSDT_intensity, bitget_futures_intensity)가 쓰였는지 스캔하여 자동 구독
            rules = (t.get("buy_conditions") or {}).get("rules", []) + (t.get("sell_conditions") or {}).get("rules", [])
            for r in rules:
                target_str = r.get("target", "")
                
                # 비트겟 지표 감지 (예: bitget_futures_intensity)
                if "bitget" in target_str:
                    base_coin = sym.split("-")[-1]
                    if "futures" in target_str:
                        self.bitget_symbols.add(sk.bitget_futures_key(base_coin))
                    else:
                        self.bitget_symbols.add(sk.spot_key(base_coin))
                elif "USDT" in target_str:
                    ref_sym = target_str.split("_")[0] # 예: ETHUSDT_intensity -> ETHUSDT
                    self.binance_symbols.add(ref_sym)
                    
        self.binance_symbols = list(self.binance_symbols)
        self.bitget_symbols = list(self.bitget_symbols)
        
        # GLOBAL_BINANCE_ALWAYS_ON: active 종목과 무관하게 항상 구독할 바이낸스 심볼
        # 용도: BTC 선물을 시장 전체 선행지표로 상시 수신 (active 매매 대상 없어도 유지)
        always_on = config.get("GLOBAL_BINANCE_ALWAYS_ON", sk.default_always_on())
        for sym in always_on:
            if sym not in self.binance_symbols:
                self.binance_symbols.append(sym)
        
        # USDC volume 피처 계산을 위해 BTCUSDC 현물 거래 구독
        if sk.USDC_VOLUME_PAIR not in self.binance_symbols:
            self.binance_symbols.append(sk.USDC_VOLUME_PAIR)
        
        # 실시간 환율(USDT/KRW) 구독 강제 추가
        if sk.FX_RATE_SYMBOL not in self.crypto_symbols:
            self.crypto_symbols.append(sk.FX_RATE_SYMBOL)


            
        all_symbols = self.stock_symbols + self.crypto_symbols + self.binance_symbols + self.bitget_symbols
        
        self.upbit_auth = UpbitAuth(
            config.get('UPBIT_ACCESS_KEY', ''),
            config.get('UPBIT_SECRET_KEY', '')
        )
        
        self.simulation_mode = config.get("GLOBAL_STRATEGY", {}).get("SIMULATION_MODE", True)
        self.simulation_initial_balance = float(config.get("GLOBAL_STRATEGY", {}).get("SIMULATION_INITIAL_BALANCE", 10000000.0))
        if self.simulation_mode:
            self.simulator = VirtualTrader(initial_balance=self.simulation_initial_balance)
            logging.info(f"[Engine] Engine Trader Initialized in SIMULATION Mode (Initial Balance: {self.simulation_initial_balance:,.0f} KRW)")
        else:
            self.simulator = UpbitRealTrader(self.upbit_auth, initial_balance=self.simulation_initial_balance, ref_mapping=self.ref_mapping)
            logging.info("[Engine] Engine Trader Initialized in REAL TRADING Mode")
        
        # KIS 및 업비트 인증 관리자 초기화
        kis_url = config.get('KIS_URL_BASE') or config.get('URL_BASE', '')
        self.auth_manager = KISAuthManager(
            config.get('KIS_APP_KEY', ''), 
            config.get('KIS_APP_SECRET', ''), 
            kis_url,
            config.get('KIS_ACC_NO', ''),
            config.get('KIS_ACC_CODE', '01')
        )
        self.url_base = kis_url
        self.kis_trader = KISTrader(self.auth_manager, is_simulation=self.simulation_mode)
        # 해외주식 예산 변환용 환율 초기값 주입 (data_manager 초기화 후 실시간 갱신)
        self.kis_trader.set_fx_rate(1380.0)
        
        # 통합 데이터 매니저 초기화
        indicators_config = config.get('INDICATORS', {})
        initial_rate_mode = config.get('GLOBAL_STRATEGY', {}).get('EXCHANGE_RATE_MODE', 'USDT')
        self.data_manager = DataManager(all_symbols, indicators_config, initial_rate_mode)
        
        self.is_running = True
        self.is_simulation = "openapivts" in (kis_url or '').lower()
        self.process = psutil.Process(os.getpid())
        self.loop_count = 0
        self.last_stats_time = time.time()
        self.version = "v1.3.0"
        self._last_ui_updates = {}
        self._stagger_index = 0
        # 룰 기반 트레일링 스탑용 엔진 측 포지션 상태 (simulator.positions 는 매 접근 시 복사본이라 직접 수정이 무효)
        self._rule_pos_state: Dict[str, Dict[str, Any]] = {}
        # 시세 stale 경고 throttle {symbol: 마지막 경고 시각}
        self._stale_warned_at: Dict[str, float] = {}
        self.stale_symbols: Set[str] = set()
        self._shared_update_task = None
        self.coinbase_connector = None
        self.init_errors = []

        # ── 캔들 어그리게이터 초기화 (모니터링 & 데이트레이딩용) ──
        from core.candle_aggregator import CandleAggregator
        self.candle_aggregators = {}
        # 기본 15분봉
        global_candle_interval = config.get("CANDLE_INTERVAL_MINUTES", 15)
        
        targets = config.get("SCALPING_TARGETS", [])
        templates = config.get("STRATEGY_TEMPLATES", {})
        ai_template = templates.get("ai_baker", {})
        deploy_base = ai_template.get("deploy_base", "")
        deploy_session = ai_template.get("deploy_session", "")

        for sym in self.crypto_symbols + self.stock_symbols:
            if sym in self.silent_stock_symbols:
                continue  # 참조 전용 대장주는 독립 캔들 집계 불필요
            if "-" in sym or sym in self.stock_symbols:  # 원화 코인 및 KIS 주식/ETF 대상
                interval_minutes = global_candle_interval
                interval_seconds = None
                
                # ai_baker 전략 매핑 체크 및 설정된 모델 스펙에서 interval 분/초단위 자동 파싱
                for t_conf in targets:
                    t_strat = t_conf.get("strategy_name", "")
                    t_tmpl = templates.get(t_strat, {})
                    is_ai = (t_strat == "ai_baker" or t_strat.startswith("ai_baker") or
                             t_tmpl.get("type") == "AI_BAKER" or bool(t_conf.get("deploy_session")))
                    if t_conf.get("symbol") == sym and is_ai and t_conf.get("active", False):
                        session = t_conf.get("deploy_session") or t_tmpl.get("deploy_session", deploy_session)
                        base = t_conf.get("deploy_base") or t_tmpl.get("deploy_base", deploy_base)
                        if not os.path.isabs(base):
                            base = os.path.normpath(os.path.join(
                                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), base
                            ))
                        deploy_path = os.path.join(base, session)
                        
                        try:
                            spec_candidates = [
                                os.path.join(deploy_path, "model", "feature_spec.json"),
                                os.path.join(deploy_path, "feature_spec.json")
                            ]
                            found_spec = None
                            for sc in spec_candidates:
                                if os.path.exists(sc):
                                    found_spec = sc
                                    break

                            if found_spec:
                                with open(found_spec, "r", encoding="utf-8") as f:
                                    spec = json.load(f)
                                    interval_str = spec.get("interval") or spec.get("feature_params", {}).get("interval", "15m")
                                    interval_seconds = parse_interval_seconds(interval_str)
                                    interval_minutes = parse_interval_minutes(interval_str)
                                    logging.info(f"[Engine] Automatically set CandleAggregator interval for {sym} to {interval_str} ({interval_minutes:.2f}m / {interval_seconds}s) from AI model spec ({found_spec}).")
                        except Exception as e:
                            logging.warning(f"[Engine] Failed to parse interval from spec for {sym}: {e}")
                        break
                
                sym_market_type = detect_market_type({"SYMBOL": sym, "SYMBOL_UPBIT": sym})
                agg = CandleAggregator(interval_minutes=interval_minutes, interval_seconds=interval_seconds, max_candles=2000, market_type=sym_market_type, symbol=sym)

                # 참조 대장주 매핑 등록 (종목 설정 + 전략 템플릿 + 배포 모델 config_snapshot.json 종합)
                for t_conf in targets:
                    if t_conf.get("symbol") == sym:
                        agg_ref_list = _resolve_target_reference_symbols(t_conf, templates)

                        for r_item in agg_ref_list:
                            if isinstance(r_item, dict):
                                r_sym = str(r_item.get("symbol", "")).strip().upper()
                                r_alias = str(r_item.get("alias", r_sym)).strip().lower()
                            else:
                                r_sym = str(r_item).strip().upper()
                                r_alias = r_sym.lower()
                            if r_sym and r_alias:
                                agg.register_ref_symbol(r_sym, r_alias)

                self.candle_aggregators[sym] = agg

        # ── [AI_BAKER] AI 전략 모듈 초기화 ──
        # strategy_name의 템플릿이 'AI_BAKER'이거나 deploy_session이 지정된 활성 종목에 대해 AIStrategyModule 생성
        self.ai_strategies = {}  # {symbol: AIStrategyModule}
        if AI_STRATEGY_AVAILABLE:
            templates = config.get("STRATEGY_TEMPLATES", {})
            fee_rate_global = config.get("GLOBAL_STRATEGY", {}).get("FEE_RATE", 0.0005)
            slippage_rate_global = config.get("GLOBAL_STRATEGY", {}).get("SLIPPAGE_RATE", 0.0005)
            enable_parity = config.get("GLOBAL_STRATEGY", {}).get("ENABLE_PARITY_LOGGING", True)

            for t_conf in targets:
                if not t_conf.get("active", False):
                    continue
                strat_name = t_conf.get("strategy_name", "")
                strat_tmpl = templates.get(strat_name, {})
                is_ai_baker = (
                    strat_tmpl.get("type") == "AI_BAKER" or 
                    strat_name.startswith("ai_baker") or 
                    bool(t_conf.get("deploy_session"))
                )

                if is_ai_baker:
                    sym = t_conf["symbol"]
                    deploy_base = t_conf.get("deploy_base") or strat_tmpl.get("deploy_base") or templates.get("ai_baker", {}).get("deploy_base", "")
                    deploy_session = t_conf.get("deploy_session") or strat_tmpl.get("deploy_session") or templates.get("ai_baker", {}).get("deploy_session", "")

                    if not deploy_base or not deploy_session:
                        continue

                    # 상대경로 → 절대경로 변환
                    base = deploy_base
                    if not os.path.isabs(base):
                        base = os.path.normpath(os.path.join(
                            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), base
                        ))
                    deploy_path = os.path.join(base, deploy_session)
                    fee_rate = t_conf.get("fee_rate", fee_rate_global)
                    slippage_rate = t_conf.get("slippage_rate", slippage_rate_global)
                    try:
                        ai_mod = AIStrategyModule(
                            deploy_path=deploy_path,
                            symbol=sym,
                            fee_rate=fee_rate,
                            slippage_rate=slippage_rate,
                            enable_parity_logging=enable_parity,
                            risk_manager=self.global_risk_manager
                        )
                        # 참조 대장주(Reference Symbols)가 설정되어 있으면 ai_mod 어그리게이터에도 자동 등록
                        # (종목 설정 + 전략 템플릿 + 배포 모델 config_snapshot.json 종합)
                        for r_item in _resolve_target_reference_symbols(t_conf, templates):
                            if isinstance(r_item, dict):
                                r_sym = str(r_item.get("symbol", "")).strip().upper()
                                r_alias = str(r_item.get("alias", r_sym)).strip().lower()
                            else:
                                r_sym = str(r_item).strip().upper()
                                r_alias = r_sym.lower()
                            if r_sym and r_alias:
                                ai_mod.aggregator.register_ref_symbol(r_sym, r_alias)

                        self.ai_strategies[sym] = ai_mod
                        logging.info(f"[Engine] AI_BAKER strategy loaded for {sym}: template={strat_name}, session={deploy_session}")
                        
                        # 📌 ModelHealthMonitor에 모델 메타데이터 등록
                        try:
                            from core.model_health_monitor import ModelHealthMonitor
                            val_metrics = getattr(ai_mod, 'params', {}).get("validation_metrics", {}) if hasattr(ai_mod, 'params') else {}
                            ModelHealthMonitor.get_instance().register_model(
                                symbol=sym,
                                session_id=getattr(ai_mod, 'session_id', deploy_session),
                                deployed_at=getattr(ai_mod, 'deployed_at', 'N/A'),
                                strategy_name=strat_name,
                                interval=getattr(ai_mod, 'interval', '10m'),
                                backtest_win_rate=val_metrics.get("win_rate"),
                                backtest_mdd=val_metrics.get("mdd")
                            )
                        except Exception as mhe:
                            logging.warning(f"[Engine] ModelHealthMonitor registration failed for {sym}: {mhe}")
                    except Exception as e:
                        err_msg = f"❌ [Engine] AI_BAKER init failed for {sym}: {e}"
                        logging.error(err_msg, exc_info=True)
                        self.init_errors.append(err_msg)
                            
                        # UI 로그 패널에 명시적인 경고 알림 띄우기
                        # startup 시점에는 아직 loop가 돌기 전이므로 init_errors에 남겨두고, consumer_loop 시작 시점에 출력하도록 함
                        fallback_notice = f"⚠️ {sym} AI 모델 로드 실패! (경로가 없거나 누락됨). 안전을 위해 일반 룰 매매(default_scalping)로 자동 롤백 적용했습니다."
                        self.init_errors.append(fallback_notice)
                        
                        # 롤백 작동 처리 (룰 평가기를 default_scalping으로 강제 갱신)
                        t_conf["strategy_name"] = "default_scalping"
                        self.strategy_manager.update_evaluator(sym, t_conf)

            # GLOBAL_STRATEGY 내 TEST_LEVEL / TEST_MODE 설정 초기 반영
            test_level = int(config.get("GLOBAL_STRATEGY", {}).get("TEST_LEVEL", 1 if config.get("GLOBAL_STRATEGY", {}).get("TEST_MODE", False) else 0))
            if not self.simulation_mode:
                test_level = 0  # 실전 매매 상태에서는 테스트 모드 비활성화 강제
            
            for sym, ai_mod in self.ai_strategies.items():
                ai_mod.set_test_level(test_level)

        # 스마트 주문 집행기 및 비동기 대기 매수 태스크 레지스트리 초기화
        self.order_executor = SmartOrderExecutor(self)
        self.pending_buy_tasks: Dict[str, asyncio.Task] = {}

        # GlobalMacroFeeder 실시간 7대 매크로 선물/외환 (Pyth Hermes + Hyperliquid) 피더 초기화
        from connectors.global_macro_feeder import GlobalMacroFeeder
        self.global_macro_feeder = GlobalMacroFeeder(
            on_tick_callback=self._on_global_macro_tick
        )
        self.yahoo_futures_feeder = self.global_macro_feeder  # 하위 호환성 유지 별칭

        # [T-6] cTrader 실시간 호가 피더 — 원장이 CTRADER 실시간 연결을 선언한 참조만 구독한다.
        # 종전에는 원장에 선언만 있고 구독 코드가 없어, cTrader 참조로 학습한 모델이
        # 실전에서 그 피처를 만들 수 없었다 (백테스트만 좋고 실전은 죽는 상태).
        from connectors.ctrader_connector import CTraderConnector
        ct_symbols, ct_broker_names = self._ctrader_reference_symbols()
        self.ctrader_feeder = CTraderConnector(
            symbols=ct_symbols,
            on_tick_callback=self._on_ctrader_tick,
            broker_symbol_map=ct_broker_names,
        )
        if ct_symbols:
            logging.info(f"[Engine] cTrader 실시간 참조 구독 대상: {ct_symbols}")

    def _ctrader_reference_symbols(self):
        """등록된 참조 중 수집처 원장이 CTRADER 실시간 연결을 선언한 것만 추린다.

        심볼 이름을 코드에 적지 않는다 — 원장이 바뀌면 구독 대상도 따라 바뀐다.
        """
        from shared.data_library.source_mapping_manager import SourceMappingManager

        wanted, broker_names = [], {}
        seen = set()
        for agg in self.candle_aggregators.values():
            for ref_sym in getattr(agg, "ref_symbol_map", {}):
                sym = str(ref_sym).strip().upper()
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                try:
                    conn = SourceMappingManager.get_live_connection(sym) or {}
                except Exception:
                    conn = {}
                if str(conn.get("provider", "")).upper() != "CTRADER" or conn.get("is_active") is False:
                    continue
                wanted.append(sym)
                broker = str(conn.get("symbol", "")).strip()
                if broker:
                    broker_names[sym] = broker
        return wanted, broker_names

    def _on_ctrader_tick(self, symbol: str, price: float, ts: datetime):
        """cTrader 실시간 호가를 그 심볼을 참조로 등록한 어그리게이터에 분배한다.

        스팟 이벤트에는 체결량이 없다 (호가 스트림이다). 거래량 0 으로 넣으면
        체결강도 피처가 '모름'(NaN) 이 되는데, 없는 값을 0.5 로 지어내는 것보다 정확하다.
        """
        sym = str(symbol).strip().upper()
        for agg in self.candle_aggregators.values():
            alias = getattr(agg, "ref_symbol_map", {}).get(sym)
            if alias:
                agg.on_ref_tick(alias, price, 0.0, timestamp=ts)

    def _on_global_macro_tick(self, symbol: str, price: float, ts: datetime):
        """글로벌 매크로 선물/외환 실시간 틱 수신 시 어그리게이터 및 AI 전략 모듈로 분배"""
        for agg in self.candle_aggregators.values():
            agg.on_futures_tick(symbol, price, ts)
        for ai_mod in self.ai_strategies.values():
            if hasattr(ai_mod, 'on_futures_tick'):
                ai_mod.on_futures_tick(symbol, price, ts)
        if symbol in ("USDKRW", "USD/KRW", "USD_KRW") and hasattr(self, 'data_manager') and self.data_manager:
            self.data_manager.global_state["usd_krw_fx"] = price

    def is_kis_symbol(self, symbol: str) -> bool:
        """KIS 주식/ETF 종목 여부 판별"""
        clean_sym = symbol.strip().upper()
        if clean_sym in self.stock_symbols:
            return True
        for t_conf in self.config.get("SCALPING_TARGETS", []):
            if t_conf.get("symbol") == clean_sym and t_conf.get("exchange") == "KIS":
                return True
        if is_domestic_krx_code(clean_sym):
            return True
        return False

    async def execute_buy(self, symbol: str, price: float, budget: float) -> bool:
        """
        통합 매수 주문 라우터
        - 시뮬레이션 모드: VirtualTrader(가상 C++) 실행
        - 실전 모드: KIS(KISTrader) 또는 업비트(UpbitRealTrader) 실전 API 분기
        """
        if self.simulation_mode:
            return await self.simulator.buy(symbol, price, budget)

        # 실전 모드
        if self.is_kis_symbol(symbol):
            success = await self.kis_trader.buy(symbol, price, budget)
            if success:
                # KISTrader 내부에 기록된 주문 단가 기반으로 simulator 포지션 동기화
                pos = self.kis_trader.positions.get(symbol, {})
                qty = pos.get("qty", 1.0)
                # 체결 단가 기준으로 포지션 평단가 설정 (슬리피지 반영)
                exec_price = pos.get("price", price)
                entry_t = pos.get("time", time.time())
                self.simulator.force_set_position(symbol, exec_price, qty, entry_t, max(pos.get("highest_price", exec_price), exec_price), False)
            return success
        else:
            return await self.simulator.buy(symbol, price, budget)

    async def execute_sell(self, symbol: str, price: float, reason: str = "SIGNAL") -> Union[float, bool]:
        """
        통합 매도 주문 라우터
        - 시뮬레이션 모드: VirtualTrader(가상 C++) 실행
        - 실전 모드: KIS(KISTrader) 또는 업비트(UpbitRealTrader) 실전 API 분기
        """
        # 진행 중인 매수 대기 태스크가 있다면 즉시 취소
        if symbol in self.pending_buy_tasks:
            self.pending_buy_tasks[symbol].cancel()
            if symbol in self.ai_strategies:
                self.ai_strategies[symbol].on_buy_cancelled(reason="EXECUTE_SELL_CALLED")

        profit_amt = False
        net_ret = 0.0
        if self.simulation_mode:
            profit_amt = await self.simulator.sell(symbol, price, reason=reason)
        elif self.is_kis_symbol(symbol):
            # 손절, 시간만료, 당일청산 등 긴급 매도는 시장가('01')로 즉시 청산 처리
            is_urgent = any(kw in str(reason).upper() for kw in [
                "LOSS", "SL", "손절", "EMERGENCY", "TIMEOUT",
                "당일청산", "장마감청산", "장마감", "DAY_EXIT", "FORCE_EXIT", "SQUARE_OFF"
            ])
            order_type = "01" if is_urgent else "00"
            profit_amt = await self.kis_trader.sell(symbol, price, reason=reason, order_type=order_type)
            if profit_amt is not False:
                # C++ positions 프로퍼티는 매 접근 시 복사본을 반환하므로 직접 변형(.pop)이 무효.
                # 실제 C++ 포지션 맵을 제거하는 전용 API를 호출한다.
                self.simulator.remove_position(symbol)
        else:
            profit_amt = await self.simulator.sell(symbol, price, reason=reason)

        # 부분 체결(잔여 포지션 유지): 청산 완료가 아니므로 AI 상태를 종료하지 않고 재시도 가능 상태로 되돌린다.
        if profit_amt is not False and getattr(profit_amt, "partial", False):
            logging.warning(f"[Engine] {symbol} 부분 체결 매도({reason}): 잔여 포지션 유지, 다음 신호에서 재청산 시도")
            if symbol in self.ai_strategies and hasattr(self.ai_strategies[symbol], 'on_sell_cancelled'):
                self.ai_strategies[symbol].on_sell_cancelled(reason=f"PARTIAL_FILL_{reason}")
            return False

        # 매도 성공/실패 시 AIStrategyModule 포지션 상태 동기화
        if symbol in self.ai_strategies:
            ai_mod = self.ai_strategies[symbol]
            if profit_amt is not False:
                if hasattr(ai_mod, 'on_sell_filled'):
                    entry_p = ai_mod.entry_price if ai_mod.entry_price > 0 else price
                    net_ret = ((price / entry_p) - 1.0 - (ai_mod.cost or 0.002)) * 100.0 if entry_p > 0 else 0.0
                    ai_mod.on_sell_filled(net_return=net_ret, exit_type=str(reason))
                else:
                    ai_mod._close_position()

                # 📌 ModelHealthMonitor에 매매 결과 기록
                try:
                    from core.model_health_monitor import ModelHealthMonitor
                    # Dust 동기화(DustSyncResult)/bool 은 실제 손익이 아니므로 추정 수익률(net_ret)로 판정
                    if getattr(profit_amt, "dust_sync", False) or isinstance(profit_amt, bool):
                        is_win = net_ret > 0
                    else:
                        is_win = (profit_amt > 0) if isinstance(profit_amt, (int, float)) else (net_ret > 0)
                    ModelHealthMonitor.get_instance().record_trade(symbol, is_win=is_win, net_return=net_ret)
                except Exception as ex:
                    logging.warning(f"[Engine] Failed to record trade in ModelHealthMonitor: {ex}")
            else:
                if hasattr(ai_mod, 'on_sell_cancelled'):
                    ai_mod.on_sell_cancelled(reason=f"SELL_FAILED_{reason}")

        return profit_amt

    async def reconcile_startup_positions(self, shared_state):
        """재기동 시 이전 체결 이력 및 실제 잔고(업비트/KIS)를 교차 검증하여 포지션을 자동 복구합니다."""
        logging.info("[Engine] 🔍 Startup position reconciliation check running...")
        
        # 1. trade_history.json 내 마감되지 않은 포지션 추적
        unclosed_positions = {}
        try:
            # C++ history 는 is_real/mode 를 버리므로, 메타가 복원된 이력을 우선 사용한다
            history = self.simulator.history_annotated() if hasattr(self.simulator, "history_annotated") else self.simulator.history
            for evt in history:
                evt_type = evt.get("type")
                sym = evt.get("symbol")
                # 실전 모드에서는 SIMULATION(가상) 이력으로 실제 포지션을 복원하지 않는다
                if not self.simulation_mode and (evt.get("mode") == "SIMULATION" or evt.get("is_real") is False):
                    continue
                if evt_type == "BUY":
                    unclosed_positions[sym] = {
                        "price": float(evt.get("price", 0.0)),
                        "qty": float(evt.get("qty", 0.0)),
                        "time": float(evt.get("time", time.time())),
                        "entry_atr": float(evt.get("entry_atr", 0.0))
                    }
                elif evt_type == "SELL":
                    unclosed_positions.pop(sym, None)
        except Exception as e:
            logging.error(f"[Engine] Failed to scan history for unclosed positions: {e}")

        # 2. 실전 매매 모드인 경우 실제 거래소 잔고 조회 (5,000원 미만 Dust는 포지션 복구 제외)
        if not self.simulation_mode:
            # 2-1. 업비트 실제 잔고 조회
            if isinstance(self.simulator, UpbitRealTrader):
                # 실제 매매 대상(KRW 마켓 AI/타겟 심볼)만 조회한다. crypto_symbols 에는 시세용 참조 심볼
                # (USDT-<coin>, KRW-USDT)이 섞여 있어 그대로 쓰면 가짜 포지션이 생성되고, 봇과 무관한 보유분도 채택된다.
                target_syms = set(self.ai_strategies.keys())
                for t_conf in self.config.get("SCALPING_TARGETS", []):
                    if t_conf.get("active", True) and t_conf.get("exchange") == "UPBIT":
                        target_syms.add(t_conf.get("symbol"))
                check_syms = [s_ for s_ in sorted(x for x in target_syms if x) if s_.startswith("KRW-")]
                for sym in check_syms:
                    try:
                        upbit_bal = await self.simulator.get_upbit_balance(sym)
                        if upbit_bal is None:
                            logging.warning(f"[Engine] {sym} 업비트 잔고 조회 실패: 포지션 복구 판단을 건너뜁니다.")
                            continue
                        ticker = self.data_manager.store.get(sym, {}).get("ticker")
                        curr_p = ticker.price if ticker else 0.0
                        # 1e-6 이하 또는 평가액 5,000원(업비트 최소주문금액) 미만은 Dust(자투리 잔액)로 취급
                        is_dust = (upbit_bal <= 1e-6) or (curr_p > 0 and (upbit_bal * curr_p) < 5000.0)

                        if is_dust:
                            if sym in unclosed_positions:
                                unclosed_positions.pop(sym, None)
                                logging.info(f"[Engine] 🧹 {sym} 실제 잔고 Dust/0({upbit_bal:.8f}, 평가금 < 5,000원) 감지: 포지션 복구에서 제외")
                        else:
                            if sym not in unclosed_positions:
                                unclosed_positions[sym] = {
                                    "price": curr_p,
                                    "qty": upbit_bal,
                                    "time": time.time(),
                                    "entry_atr": 0.0
                                }
                            else:
                                unclosed_positions[sym]["qty"] = upbit_bal
                    except Exception as e:
                        logging.error(f"[Engine] Upbit balance reconciliation error for {sym}: {e}")

            # 2-2. KIS(한국투자증권) 실제 잔고 조회
            if self.kis_trader:
                for sym in self.stock_symbols:
                    try:
                        kis_bal, kis_avg = await self.kis_trader.get_balance_with_avg_price(sym)
                        if kis_bal <= 0:
                            if sym in unclosed_positions:
                                unclosed_positions.pop(sym, None)
                                logging.info(f"[Engine] 🧹 {sym} KIS 실잔고 0 감지: 포지션 복구에서 제외")
                        else:
                            if sym not in unclosed_positions:
                                ticker = self.data_manager.store.get(sym, {}).get("ticker")
                                curr_p = ticker.price if ticker else 0.0
                                # 실제 진입 평단(KIS 잔고 응답)이 있으면 현재가 대신 사용
                                entry_p = kis_avg if kis_avg and kis_avg > 0 else curr_p
                                unclosed_positions[sym] = {
                                    "price": entry_p,
                                    "qty": kis_bal,
                                    "time": time.time(),
                                    "entry_atr": 0.0
                                }
                            else:
                                unclosed_positions[sym]["qty"] = kis_bal
                    except Exception as e:
                        logging.error(f"[Engine] KIS balance reconciliation error for {sym}: {e}")

        # 3. 포지션 복구 수행
        for sym, pos_data in unclosed_positions.items():
            buy_price = pos_data["price"]
            qty = pos_data["qty"]
            entry_time = pos_data["time"]
            entry_atr = pos_data.get("entry_atr", 0.0)

            if buy_price <= 0 or qty <= 0:
                continue

            # C++ Simulator 포지션 복원
            self.simulator.force_set_position(sym, buy_price, qty, entry_time, buy_price, False)
            # 모의 모드: 복구 포지션의 매입 원가를 가상 잔고에서 차감 (미차감 시 total_asset/ROI 과대 계산)
            if self.simulation_mode and hasattr(self.simulator, "balance"):
                try:
                    cost = buy_price * qty * (1.0 + float(getattr(self.simulator, "fee_rate", 0.0005)))
                    self.simulator.balance = max(0.0, float(self.simulator.balance) - cost)
                except Exception as e:
                    logging.warning(f"[Engine] 복구 포지션 잔고 차감 실패({sym}): {e}")

            # AIStrategyModule 포지션 복원
            if sym in self.ai_strategies:
                from datetime import datetime, timezone, timedelta
                KST = timezone(timedelta(hours=9))
                dt_entry = datetime.fromtimestamp(entry_time, tz=KST)
                self.ai_strategies[sym].restore_position(buy_price, entry_atr=entry_atr, entry_time=dt_entry)

            msg = f"🔄 [재기동 포지션 복구] {sym} @ {buy_price:,.0f}원 | 수량: {qty:.8f}"
            logging.info(f"[Engine] {msg}")
            self.add_log(shared_state, msg, log_type="TRADE")
            try:
                from utils.telegram_bot import get_bot
                if bot := get_bot():
                    bot.send_message(f"🔄 <b>[재기동 포지션 자동 복구 완료]</b>\n종목: {sym}\n평단가: {buy_price:,.0f}원\n수량: {qty:.8f}")
            except Exception: pass

    def add_log(self, shared_state, message, log_type="SYSTEM"):
        """UI 로그 패널에 구조화된 메시지 추가"""
        logs = shared_state.data.get('__logs__', [])
        now_ts = time.time()
        timestamp = time.strftime('%H:%M:%S')
        logs.append({
            "ts": now_ts,
            "time": timestamp,
            "type": log_type,
            "version": self.version,
            "message": message
        })
        shared_state.data['__logs__'] = logs[-100:]

    async def handle_command(self, shared_state, cmd):
        try:
            cmd_type = cmd.get("type")
            if cmd_type == "focus":
                shared_state.update_control({"ui_focus_symbol": cmd.get("symbol", "KRW-ETH")})
                logging.info(f"[Engine] Focus changed to: {cmd.get('symbol')}")
            elif cmd_type == "set_rate_mode":
                mode = cmd.get("mode", "USDT")
                self.data_manager.global_state["exchange_rate_mode"] = mode
                logging.info(f"[Engine] Exchange Rate Mode changed to: {mode}")
            elif cmd_type == "set_ob_level":
                symbol = cmd.get("symbol")
                level = cmd.get("level", 0)
                self.ui_levels[symbol] = level
                if hasattr(self, 'upbit_connector'):
                    await self.upbit_connector.set_ui_level(symbol, level)
                    logging.info(f"[Engine] UI OB Level changed: {symbol} -> {level}")
            elif cmd_type == "reload_config":
                await self.reload_config(shared_state)
            elif cmd_type == "restart":
                logging.info("📩 [Engine] Restart command received. Shutting down for respawn...")
                self.is_running = False
            elif cmd_type == "toggle_server":
                current = shared_state.control.get("server_active", True)
                next_val = not current
                shared_state.update_control({"server_active": next_val})
                try:
                    import os
                    from utils.retry import atomic_write_json
                    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    if "GLOBAL_STRATEGY" in config_data:
                        config_data["GLOBAL_STRATEGY"]["SERVER_ACTIVE"] = next_val
                    atomic_write_json(config_path, config_data)
                    self.config = config_data
                except Exception as ex:
                    logging.error(f"[Engine] Failed to persist SERVER_ACTIVE: {ex}")
                logging.info(f"[Engine] server_active toggled and persisted to: {next_val}")
                self.add_log(shared_state, f"🖥️ 서버 작동 상태가 {'ON' if next_val else 'OFF'}으로 변경되었습니다.", log_type="SYSTEM")
            elif cmd_type == "toggle_trading":
                current = shared_state.control.get("trading_active", False)
                next_val = not current
                shared_state.update_control({"trading_active": next_val})
                try:
                    import os
                    from utils.retry import atomic_write_json
                    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    if "GLOBAL_STRATEGY" in config_data:
                        config_data["GLOBAL_STRATEGY"]["TRADING_ACTIVE"] = next_val
                    atomic_write_json(config_path, config_data)
                    self.config = config_data
                except Exception as ex:
                    logging.error(f"[Engine] Failed to persist TRADING_ACTIVE: {ex}")
                logging.info(f"[Engine] trading_active toggled and persisted to: {next_val}")
                self.add_log(shared_state, f"💸 매매 활성화 상태가 {'ON' if next_val else 'OFF'}으로 변경되었습니다.", log_type="SYSTEM")
            elif cmd_type in ("toggle_symbol_auto_trade", "set_symbol_auto_trade"):
                symbol = cmd.get("symbol")
                explicit_val = cmd.get("auto_trade")
                if symbol:
                    try:
                        import os
                        from utils.retry import atomic_write_json
                        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
                        with open(config_path, 'r', encoding='utf-8') as f:
                            config_data = json.load(f)
                        
                        target_val = None
                        found = False
                        for t in config_data.get("SCALPING_TARGETS", []):
                            if t.get("symbol") == symbol:
                                if explicit_val is not None:
                                    t["auto_trade"] = bool(explicit_val)
                                else:
                                    t["auto_trade"] = not t.get("auto_trade", True)
                                target_val = t["auto_trade"]
                                found = True
                                break
                        
                        # SCALPING_TARGETS에 없는 종목이면 기본 타겟 추가 등록
                        if not found:
                            new_target = {
                                "symbol": symbol,
                                "exchange": "KIS" if (symbol.isdigit() and len(symbol) == 6) or symbol in self.stock_symbols else "UPBIT",
                                "active": True,
                                "auto_trade": bool(explicit_val) if explicit_val is not None else False
                            }
                            config_data.setdefault("SCALPING_TARGETS", []).append(new_target)
                            target_val = new_target["auto_trade"]
                        
                        if target_val is not None:
                            atomic_write_json(config_path, config_data)
                            self.config = config_data
                            
                            # 1. Rule evaluator 동기화
                            evaluator = self.strategy_manager.get_evaluator(symbol)
                            if evaluator:
                                evaluator.auto_trade = target_val
                                
                            # 2. AIStrategyModule 동기화
                            if symbol in self.ai_strategies:
                                self.ai_strategies[symbol].auto_trade = target_val
                                
                            logging.info(f"[Engine] {symbol} auto_trade set to: {target_val}")
                            self.add_log(shared_state, f"⚡ {symbol} 자동매매가 {'ON' if target_val else 'OFF'}으로 변경되었습니다.", log_type="SYSTEM")
                    except Exception as ex:
                        logging.error(f"[Engine] Failed to toggle/set symbol auto_trade for {symbol}: {ex}")
            elif cmd_type == "toggle_radar":
                try:
                    import os
                    from utils.retry import atomic_write_json
                    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    
                    current = config_data.get("GLOBAL_STRATEGY", {}).get("RADAR_ACTIVE", False)
                    next_val = not current
                    
                    if "GLOBAL_STRATEGY" in config_data:
                        config_data["GLOBAL_STRATEGY"]["RADAR_ACTIVE"] = next_val
                        
                    atomic_write_json(config_path, config_data)
                    self.config = config_data
                    
                    shared_state.update_control({"radar_active": next_val})
                    logging.info(f"[Engine] trading_config.json RADAR_ACTIVE updated to {next_val}")
                    self.add_log(shared_state, f"📡 스캘핑 대상 탐지 레이더가 {'ON' if next_val else 'OFF'}으로 변경되었습니다.", log_type="SYSTEM")
                except Exception as ex:
                    logging.error(f"[Engine] Failed to toggle RADAR_ACTIVE: {ex}")
            elif cmd_type == "toggle_simulation":
                current = shared_state.control.get("simulation_mode", True)
                next_val = not current

                # 🔒 상호 배제 안전 잠금 1: 테스트 모드가 켜져있을 때 실전 매매 전환 절대 불가
                current_test_level = int(shared_state.control.get("test_level", 0))
                if not next_val and current_test_level > 0:
                    warn_msg = f"🚨 [전환 차단] 테스트 매매 모드(TEST Lv.{current_test_level})가 활성화되어 있어 실전 매매로 전환할 수 없습니다. 먼저 TEST OFF로 변경하세요."
                    logging.warning(f"[Engine] {warn_msg}")
                    self.add_log(shared_state, warn_msg, log_type="SYSTEM")
                    return

                # 🔒 상호 배제 안전 잠금 2: 보유 포지션/매수 대기 중에는 거래기(simulator)를 교체할 수 없다
                # (교체 시 포지션 추적이 유실되고, 가상 포지션이 실전 계좌로 오인됨)
                open_syms = list(self.simulator.positions.keys()) if hasattr(self.simulator, "positions") else []
                pending_syms = [s for s, t in self.pending_buy_tasks.items() if not t.done()]
                if open_syms or pending_syms:
                    warn_msg = f"🚨 [전환 차단] 보유 포지션({len(open_syms)}) 또는 매수 대기({len(pending_syms)})가 있어 매매 모드를 전환할 수 없습니다. 모두 청산/취소 후 다시 시도하세요."
                    logging.warning(f"[Engine] {warn_msg}")
                    self.add_log(shared_state, warn_msg, log_type="SYSTEM")
                    return

                # 실제 주문 경로(거래기)를 새 모드에 맞게 교체: 플래그만 바꾸면 실주문/가상체결이 뒤바뀐다
                if next_val:
                    self.simulator = VirtualTrader(initial_balance=self.simulation_initial_balance)
                else:
                    self.simulator = UpbitRealTrader(self.upbit_auth, initial_balance=self.simulation_initial_balance, ref_mapping=self.ref_mapping)
                self.kis_trader.is_simulation = next_val

                self.simulation_mode = next_val
                shared_state.update_control({"simulation_mode": next_val})
                logging.info(f"[Engine] simulation_mode toggled to: {next_val}")
                self.add_log(shared_state, f"🧪 모의 매매 상태가 {'ON' if next_val else 'OFF (실거래)'}으로 변경되었습니다.", log_type="SYSTEM")
                
                # config 파일 실시간 업데이트 (Atomic Write로 Race Condition 방지)
                try:
                    import os
                    from utils.retry import atomic_write_json
                    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    
                    if "GLOBAL_STRATEGY" in config_data:
                        config_data["GLOBAL_STRATEGY"]["SIMULATION_MODE"] = next_val
                        
                    atomic_write_json(config_path, config_data)
                    
                    self.config = config_data
                    logging.info(f"[Engine] trading_config.json SIMULATION_MODE updated to {next_val}")
                except Exception as ex:
                    logging.error(f"[Engine] Failed to save SIMULATION_MODE to config: {ex}")

                # 실전 매매(next_val == False)로 스위칭 시 즉시 잔고 동기화 강제 구동
                if not next_val:
                    try:
                        async def force_refresh_balance():
                            from utils.portfolio_manager import PortfolioManager
                            from utils.portfolio_tracker import PortfolioTracker
                            portfolio = PortfolioManager(self.auth_manager, self.upbit_auth, shared_state)
                            u_bal = await portfolio.fetch_upbit_balance() or []
                            k_bal = await portfolio.fetch_kis_balance()
                            
                            # 실시간 시세 추출
                            live_prices = {}
                            for sym in self.stock_symbols + self.crypto_symbols:
                                store_item = self.data_manager.store.get(sym)
                                if store_item and store_item.get("ticker"):
                                    live_prices[sym] = float(store_item["ticker"].price)

                            active_bot = [t.get("symbol") for t in self.config.get("SCALPING_TARGETS", []) if t.get("active", True)]
                            fx = float(shared_state.data.get('__global__', {}).get('usd_krw_fx', 1380.0))
                            calc_data = PortfolioTracker.calculate_portfolio(u_bal, k_bal, live_prices, active_bot, usd_krw_fx=fx)
                            _set_shared_item(shared_state.data, '__portfolio__', calc_data)
                            logging.info("[Engine] 실전 매매 스위칭 감지로 실시간 잔고 및 포트폴리오 갱신 완료")
                        asyncio.create_task(force_refresh_balance())
                    except Exception as fe:
                        logging.error(f"[Engine] Force balance refresh failed: {fe}")

            elif cmd_type == "reset_baseline":
                try:
                    from utils.portfolio_tracker import PortfolioTracker
                    target_broker = cmd.get("broker", "ALL")
                    note = cmd.get("note", "수동 재설정")
                    cur_port = dict(shared_state.data.get('__portfolio__', {}))
                    summary = cur_port.get("summary", {})
                    u_tot = float(summary.get("upbit_total", 0.0))
                    k_tot = float(summary.get("kis_total", 0.0))
                    
                    new_baseline = PortfolioTracker.reset_baseline(u_tot, k_tot, target_broker=target_broker, note=note)
                    
                    # 포트폴리오 재계산 즉시 반영
                    u_bal = cur_port.get("upbit", [])
                    k_bal = cur_port.get("kis", {})
                    live_prices = {}
                    for sym in self.stock_symbols + self.crypto_symbols:
                        store_item = self.data_manager.store.get(sym)
                        if store_item and store_item.get("ticker"):
                            live_prices[sym] = float(store_item["ticker"].price)
                    active_bot = [t.get("symbol") for t in self.config.get("SCALPING_TARGETS", []) if t.get("active", True)]
                    fx = float(shared_state.data.get('__global__', {}).get('usd_krw_fx', 1380.0))
                    calc_data = PortfolioTracker.calculate_portfolio(u_bal, k_bal, live_prices, active_bot, usd_krw_fx=fx)
                    _set_shared_item(shared_state.data, '__portfolio__', calc_data)
                    
                    comb_tot = u_tot + k_tot
                    msg = f"🔄 [기준선 갱신 ({target_broker})] 기준금액이 현재 자산(UPBIT: ₩{u_tot:,.0f} / KIS: ₩{k_tot:,.0f})으로 갱신되었습니다."
                    logging.info(f"[Engine] {msg}")
                    self.add_log(shared_state, msg, log_type="SYSTEM")
                    try:
                        from utils.telegram_bot import get_bot
                        if bot := get_bot():
                            bot.send_message(f"🔄 <b>[기준선 갱신 완료: {target_broker}]</b>\n• UPBIT: <code>₩{u_tot:,.0f}</code>\n• KIS: <code>₩{k_tot:,.0f}</code>\n• 전체: <code>₩{comb_tot:,.0f}</code>\n당일 손익/수익률 기준선이 갱신되었습니다.")
                    except Exception:
                        pass
                except Exception as ex:
                    logging.error(f"[Engine] reset_baseline 실패: {ex}")

            elif cmd_type == "restore_baseline":
                try:
                    from utils.portfolio_tracker import PortfolioTracker
                    history_id = cmd.get("history_id", "")
                    new_baseline = PortfolioTracker.restore_baseline(history_id)
                    if new_baseline:
                        cur_port = dict(shared_state.data.get('__portfolio__', {}))
                        u_bal = cur_port.get("upbit", [])
                        k_bal = cur_port.get("kis", {})
                        live_prices = {}
                        for sym in self.stock_symbols + self.crypto_symbols:
                            store_item = self.data_manager.store.get(sym)
                            if store_item and store_item.get("ticker"):
                                live_prices[sym] = float(store_item["ticker"].price)
                        active_bot = [t.get("symbol") for t in self.config.get("SCALPING_TARGETS", []) if t.get("active", True)]
                        fx = float(shared_state.data.get('__global__', {}).get('usd_krw_fx', 1380.0))
                        calc_data = PortfolioTracker.calculate_portfolio(u_bal, k_bal, live_prices, active_bot, usd_krw_fx=fx)
                        _set_shared_item(shared_state.data, '__portfolio__', calc_data)

                        tot_b = float(new_baseline.get("TOTAL", {}).get("daily_balance", 0.0))
                        msg = f"↩️ [기준선 복구 완료] 과거 기준선(₩{tot_b:,.0f})으로 복구되었습니다."
                        logging.info(f"[Engine] {msg}")
                        self.add_log(shared_state, msg, log_type="SYSTEM")
                except Exception as ex:
                    logging.error(f"[Engine] restore_baseline 실패: {ex}")

            elif cmd_type == "reset_simulation":
                if not self.simulation_mode:
                    warn_msg = "🚨 [초기화 차단] 실전 매매(REAL TRADING) 중에는 시뮬레이션 초기화를 실행할 수 없습니다."
                    logging.warning(f"[Engine] {warn_msg}")
                    self.add_log(shared_state, warn_msg, log_type="SYSTEM")
                    return
                initial_bal = float(self.config.get("GLOBAL_STRATEGY", {}).get("SIMULATION_INITIAL_BALANCE", 10000000.0))
                if hasattr(self.simulator, "reset"):
                    self.simulator.reset(initial_bal)
                if hasattr(self.kis_trader, "positions"):
                    self.kis_trader.positions.clear()
                if hasattr(self.kis_trader, "history"):
                    self.kis_trader.history.clear()
                self.last_exit_times.clear()

                for sym, ai_mod in self.ai_strategies.items():
                    if hasattr(ai_mod, "reset_position"):
                        ai_mod.reset_position()
                    elif hasattr(ai_mod, "has_position"):
                        ai_mod.has_position = False

                current_prices = {
                    sym: (self.data_manager.store.get(sym, {}).get("ticker").price if self.data_manager.store.get(sym, {}).get("ticker") else 0.0)
                    for sym in self.stock_symbols + self.crypto_symbols
                }
                _set_shared_item(shared_state.data, '__sim__', self.simulator.get_stats(current_prices))

                msg = f"🧪 [시뮬레이션 초기화] 가상 포지션과 매매 기록이 초기화되었으며, 잔고가 ₩{initial_bal:,.0f}으로 복원되었습니다."
                logging.info(f"[Engine] {msg}")
                self.add_log(shared_state, msg, log_type="SYSTEM")
                try:
                    from utils.telegram_bot import get_bot
                    if bot := get_bot():
                        bot.send_message(f"🧪 <b>[시뮬레이션 초기화 완료]</b>\n잔고 복원: ₩{initial_bal:,.0f}\n포지션 및 거래 내역이 모두 리셋되었습니다.")
                except Exception:
                    pass

            elif cmd_type in ("toggle_test_mode", "set_test_level"):
                # 🔒 상호 배제 안전 잠금 2: 실전 매매 중에는 테스트 모드 활성화 절대 불가
                if not self.simulation_mode:
                    warn_msg = "🚨 [설정 차단] 실전 매매(REAL TRADING) 중에는 테스트 모드를 켤 수 없습니다. 모의 매매(SIMUL ON)로 전환 후 시도하세요."
                    logging.warning(f"[Engine] {warn_msg}")
                    self.add_log(shared_state, warn_msg, log_type="SYSTEM")
                    shared_state.update_control({"test_mode": False, "test_level": 0})
                    return

                if cmd_type == "set_test_level":
                    target_level = int(cmd.get("level", 0)) % 4
                else:
                    current_level = int(shared_state.control.get("test_level", 0))
                    target_level = (current_level + 1) % 4

                shared_state.update_control({
                    "test_level": target_level,
                    "test_mode": (target_level > 0)
                })

                for sym, ai_mod in self.ai_strategies.items():
                    ai_mod.set_test_level(target_level)

                lvl_info = {
                    0: "OFF (실전 배포 원본 파라미터 복원)",
                    1: "Lv.1 MILD (buy_threshold 0.5000, 쿨다운 1분)",
                    2: "Lv.2 ACTIVE (buy_threshold 0.4700, 쿨다운 0분, 야간/장마감 시간제약 우회)",
                    3: "Lv.3 TURBO (buy_threshold 0.4000, 쿨다운 0분, 초고빈도 체결 모드)"
                }
                desc = lvl_info.get(target_level, f"Lv.{target_level}")
                logging.info(f"[Engine] ⚡ test_level changed to: {target_level} ({desc})")
                self.add_log(
                    shared_state,
                    f"⚡ 테스트 매매 모드가 [{desc}]로 변경되었습니다.",
                    log_type="SYSTEM"
                )

                # config 파일 실시간 업데이트 (Atomic Write)
                try:
                    import os
                    from utils.retry import atomic_write_json
                    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)

                    if "GLOBAL_STRATEGY" in config_data:
                        config_data["GLOBAL_STRATEGY"]["TEST_LEVEL"] = target_level
                        config_data["GLOBAL_STRATEGY"]["TEST_MODE"] = (target_level > 0)

                    atomic_write_json(config_path, config_data)
                    self.config = config_data
                    logging.info(f"[Engine] trading_config.json TEST_LEVEL updated to {target_level}")
                except Exception as ex:
                    logging.error(f"[Engine] Failed to save TEST_LEVEL to config: {ex}")
            elif cmd_type == "manual_buy":
                symbol = cmd.get("symbol")
                budget = cmd.get("budget")
                ticker = self.data_manager.store.get(symbol, {}).get("ticker")
                price = ticker.price if ticker else 0.0
                if price <= 0.0:
                    err_msg = f"❌ [수동 매수 실패] {symbol}의 실시간 시세를 조회할 수 없습니다."
                    logging.error(err_msg)
                    self.add_log(shared_state, err_msg, log_type="SYSTEM")
                    try:
                        from utils.telegram_bot import get_bot
                        if bot := get_bot():
                            bot.send_message(err_msg)
                    except Exception: pass
                else:
                    logging.info(f"📩 [Engine] Manual Buy command executing: {symbol} @ {price:,.0f} | Budget: {budget:,.0f}")
                    # 시간 주기별 최적 주문 집행 적용 (evaluator 생성 유무와 무관하게 타겟 설정을 통해 판별)
                    interval_str = ""
                    target_conf = next((t for t in self.config.get("SCALPING_TARGETS", []) if t["symbol"] == symbol), None)
                    if target_conf:
                        strat_name = target_conf.get("strategy_name")
                        strat_tmpl = self.config.get("STRATEGY_TEMPLATES", {}).get(strat_name or "", {})
                        if strat_tmpl.get("type") == "AI_BAKER":
                            interval_str = "30m"
                    
                    if interval_str == "30m":
                        asyncio.create_task(self.execute_limit_scale_in(shared_state, symbol, price, budget))
                    else:
                        async def run_market_buy():
                            success = await self.execute_buy(symbol, price, budget)
                            if success:
                                self.add_log(shared_state, f"🎯 [수동 매수 성공] {symbol} @ {price:,.0f}", log_type="TRADE")
                            else:
                                err_msg = f"❌ [수동 매수 실패] {symbol} @ {price:,.0f} 주문 처리 오류"
                                self.add_log(shared_state, err_msg, log_type="SYSTEM")
                                try:
                                    from utils.telegram_bot import get_bot
                                    if bot := get_bot(): bot.send_message(err_msg)
                                except Exception: pass
                        asyncio.create_task(run_market_buy())
            elif cmd_type in ["manual_sell", "close_symbol"]:
                symbol = cmd.get("symbol")
                pos = self.simulator.positions.get(symbol)
                ticker = self.data_manager.store.get(symbol, {}).get("ticker")
                price = ticker.price if ticker else 0.0

                # 대기 중인 매수 태스크가 있다면 취소
                if symbol in self.pending_buy_tasks:
                    self.pending_buy_tasks[symbol].cancel()
                    if symbol in self.ai_strategies:
                        self.ai_strategies[symbol].on_buy_cancelled(reason="MANUAL_SELL_COMMAND")

                if not pos:
                    err_msg = f"⚠️ [수동 매도 경고] {symbol}의 보유 포지션이 존재하지 않습니다."
                    logging.warning(err_msg)
                    self.add_log(shared_state, err_msg, log_type="SYSTEM")
                    try:
                        from utils.telegram_bot import get_bot
                        if bot := get_bot(): bot.send_message(err_msg)
                    except Exception: pass
                elif price <= 0.0:
                    err_msg = f"❌ [수동 매도 실패] {symbol}의 실시간 시세를 조회할 수 없습니다."
                    logging.error(err_msg)
                    self.add_log(shared_state, err_msg, log_type="SYSTEM")
                    try:
                        from utils.telegram_bot import get_bot
                        if bot := get_bot(): bot.send_message(err_msg)
                    except Exception: pass
                else:
                    logging.info(f"📩 [Engine] Manual/Close Sell command executing: {symbol} @ {price:,.0f}")
                    async def run_market_sell():
                        profit_amt = await self.execute_sell(symbol, price, reason="MANUAL_CLOSE")
                        if profit_amt is not False:
                            self.last_exit_times[symbol] = time.time()
                            self.add_log(shared_state, f"🔄 [수동 청산 성공] {symbol} @ {price:,.0f} | Profit: {profit_amt:+,.0f}", log_type="TRADE")
                        else:
                            err_msg = f"❌ [수동 청산 실패] {symbol} 주문 처리 오류"
                            self.add_log(shared_state, err_msg, log_type="SYSTEM")
                            try:
                                from utils.telegram_bot import get_bot
                                if bot := get_bot(): bot.send_message(err_msg)
                            except Exception: pass
                    asyncio.create_task(run_market_sell())
            elif cmd_type in ["close_all", "liquidate_all"]:
                logging.info("🚨 [Engine] Close All / Liquidate All command received.")
                active_symbols = list(self.simulator.positions.keys())
                if not active_symbols:
                    msg = "ℹ️ [전량 청산] 현재 보유 중인 활성 포지션이 없습니다."
                    self.add_log(shared_state, msg, log_type="SYSTEM")
                    try:
                        from utils.telegram_bot import get_bot
                        if bot := get_bot(): bot.send_message(msg)
                    except Exception: pass
                else:
                    self.add_log(shared_state, f"🚨 [전량 긴급 청산 시작] {len(active_symbols)}개 종목: {', '.join(active_symbols)}", log_type="WARNING")
                    for sym in active_symbols:
                        ticker = self.data_manager.store.get(sym, {}).get("ticker")
                        price = ticker.price if ticker else 0.0
                        if price > 0.0:
                            async def run_close_target(target_sym, target_p):
                                await self.execute_sell(target_sym, target_p, reason="EMERGENCY_CLOSE_ALL")
                            asyncio.create_task(run_close_target(sym, price))
            elif cmd_type == "start_diagnostics":
                symbol = cmd.get("symbol", "ALL")
                duration_sec = float(cmd.get("duration_sec", 30.0))
                from utils.diagnostics.data_sniffer import DataSniffer
                res = DataSniffer.instance().start_capture(symbol=symbol, duration_sec=duration_sec)
                shared_state.data['__diag_status__'] = DataSniffer.instance().get_status()
                logging.info(f"🩺 [Diagnostics] Engine DataSniffer Started for {symbol} ({duration_sec}s)")
            elif cmd_type == "stop_diagnostics":
                from utils.diagnostics.data_sniffer import DataSniffer
                info = DataSniffer.instance().stop_capture()
                shared_state.data['__diag_status__'] = DataSniffer.instance().get_status()
                logging.info(f"🩺 [Diagnostics] Engine DataSniffer Stopped. Records: {info.get('record_count', 0)}")
        except Exception as e:
            logging.error(f"[Engine] Failed to handle command {cmd}: {e}", exc_info=True)

    async def execute_limit_scale_in(self, shared_state, symbol, trigger_price, budget):
        """30분봉 이상 장기 전략에 대해 지정가 분할 매수 대기 및 슬리피지 통제 Chasing을 수행합니다."""
        logging.info(f"[LimitScaleIn] Start scale-in for {symbol} | Trigger Price: {trigger_price:,.0f} | Budget: {budget:,.0f}")
        self.add_log(shared_state, f"⏳ [지정가 대기 진입] {symbol} @ {trigger_price:,.0f} (3분 대기 시작)", log_type="SYSTEM")
        
        # 3분 대기 (180초)
        await asyncio.sleep(180)
        
        # 3분 뒤 현재 가격 조회
        ticker = self.data_manager.store.get(symbol, {}).get("ticker")
        current_price = ticker.price if ticker else 0.0
        
        if current_price <= 0.0:
            logging.error(f"[LimitScaleIn] Failed to fetch current price for {symbol} after wait. Cancel order.")
            self.add_log(shared_state, f"❌ [대기 주문 취소] {symbol} 시세 미수신으로 취소 처리", log_type="SYSTEM")
            return

        # 가격 변동률 계산
        price_diff_pct = ((current_price / trigger_price) - 1) * 100
        max_chase_slippage = self.config.get("GLOBAL_STRATEGY", {}).get("LIMIT_SCALE_IN_MAX_SLIPPAGE_PCT", 0.1)
        
        if price_diff_pct <= max_chase_slippage:
            # 슬리피지 허용 범위 이내 ➔ 남은 물량 시장가 매수
            logging.info(f"[LimitScaleIn] Current price {current_price:,.0f} is within slippage limit ({price_diff_pct:+.2f}%). Execution Chase.")
            success = await self.execute_buy(symbol, current_price, budget)
            if success:
                self.add_log(shared_state, f"🎯 [수동 매수 성공] {symbol} @ {current_price:,.0f} (지정가 대기 후 체결 | 슬리피지: {price_diff_pct:+.2f}%)", log_type="TRADE")
            else:
                self.add_log(shared_state, f"❌ [수동 매수 실패] {symbol} 주문 처리 실패", log_type="SYSTEM")
        else:
            # 슬리피지 허용 범위 초과 ➔ 진입 포기 및 대기 주문 취소 (Abort)
            logging.warning(f"[LimitScaleIn] Current price {current_price:,.0f} exceeded slippage limit ({price_diff_pct:+.2f}% > {max_chase_slippage}%). Order Aborted.")
            self.add_log(shared_state, f"⛔ [대기 주문 만료] {symbol} @ {current_price:,.0f} | 슬리피지 한도 초과 ({price_diff_pct:+.2f}% > +{max_chase_slippage}%)로 주문 파기", log_type="SYSTEM")
            try:
                from utils.telegram_bot import get_bot
                if bot := get_bot():
                    bot.send_message(
                        f"⛔ <b>[주문 취소] {symbol}</b>\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"현재가: {current_price:,.0f} ({price_diff_pct:+.2f}%)\n"
                        f"⚠️ 허용 슬리피지(+{max_chase_slippage}%) 초과로 매수를 취소하였습니다."
                    )
            except Exception: pass

    async def reload_config(self, shared_state):
        """설정 파일을 다시 읽어 전략과 데이터 스트림을 실시간으로 업데이트합니다."""
        logging.info("[Engine] Hot-reloading configuration and reconnecting WS streams...")
        try:
            import os
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
            with open(config_path, 'r') as f:
                new_config = json.load(f)
            
            # 1. 전략 매니저 업데이트 (룰 컴파일 및 교체)
            self.strategy_manager = StrategyManager(new_config)
            
            # 2. 타겟 심볼 목록 및 포커스 업데이트 (active: true인 종목만 UI에 표출)
            new_targets = [t['symbol'] for t in new_config.get("SCALPING_TARGETS", []) if t.get("active", True)]
            shared_state.update_control({"target_symbols": new_targets})
            
            # 3. 글로벌 전략 설정 업데이트 (수익률 등)
            self.config = new_config
            new_init_bal = float(new_config.get("GLOBAL_STRATEGY", {}).get("SIMULATION_INITIAL_BALANCE", 10000000.0))
            self.simulation_initial_balance = new_init_bal
            if hasattr(self.simulator, "initial_balance"):
                self.simulator.initial_balance = new_init_bal
            
            # 4. 동적 심볼 및 참조 마켓 재구독 (WS Hot-Restart & 가상 심볼 맵핑 갱신)
            from core.rule_engine import merge_strategy
            new_stock_symbols = []
            new_crypto_symbols = []
            new_binance_symbols = set()
            new_bitget_symbols = set()
            
            # 가상 심볼 매핑 테이블 갱신
            # in-place 갱신: UpbitRealTrader 등이 보유한 ref_mapping 참조가 끊기지 않도록 dict 객체를 유지한다
            self.ref_mapping.clear()
            self.reverse_ref_mapping = {}
            
            targets = new_config.get("SCALPING_TARGETS", [])
            new_templates = new_config.get("STRATEGY_TEMPLATES", {})
            for t_orig in targets:
                t = merge_strategy(t_orig, new_config)
                if not t.get("active", True):
                    continue
                    
                sym = t["symbol"]
                ref_sym = t.get("reference_symbol") or sym
                self.ref_mapping[sym] = ref_sym
                self.reverse_ref_mapping.setdefault(ref_sym, []).append(sym)

                exch = t.get("exchange", "")
                if exch == "UPBIT":
                    if ref_sym not in new_crypto_symbols:
                        new_crypto_symbols.append(ref_sym)
                    base_coin = ref_sym.split("-")[-1]
                    ref_market = t.get("reference_market", "BINANCE")
                    if ref_market == "BITGET":
                        new_bitget_symbols.add(sk.spot_key(base_coin))
                        new_bitget_symbols.add(sk.bitget_futures_key(base_coin))
                    else:
                        new_binance_symbols.add(sk.spot_key(base_coin))
                        new_binance_symbols.add(sk.binance_futures_key(base_coin))
                    
                    native_usdt_pair = f"USDT-{base_coin}"
                    if native_usdt_pair not in new_crypto_symbols:
                        new_crypto_symbols.append(native_usdt_pair)
                elif exch == "KIS":
                    new_stock_symbols.append(sym)

                # 동적 참조 대장주(Reference Symbols) 자동 구독(Silent Subscribe) 및 어그리게이터 매핑 갱신
                # (종목 설정 + 전략 템플릿 + 배포 모델 config_snapshot.json 종합 — __init__ 과 동일 로직 공유)
                for r_item in _resolve_target_reference_symbols(t_orig, new_templates):
                    if isinstance(r_item, dict):
                        r_sym = str(r_item.get("symbol", "")).strip().upper()
                        r_alias = str(r_item.get("alias", r_sym)).strip().lower()
                    else:
                        r_sym = str(r_item).strip().upper()
                        r_alias = r_sym.lower()
                    if r_sym:
                        if r_sym not in new_stock_symbols:
                            new_stock_symbols.append(r_sym)
                        self.silent_stock_symbols.add(r_sym)
                        if sym in self.candle_aggregators and r_alias:
                            self.candle_aggregators[sym].register_ref_symbol(r_sym, r_alias)
                    
                buy_cond = t.get("buy_conditions") or {}
                sell_cond = t.get("sell_conditions") or {}
                rules = buy_cond.get("rules", []) + sell_cond.get("rules", [])
                for r in rules:
                    target_str = r.get("target", "")
                    if "bitget" in target_str:
                        base_coin = ref_sym.split("-")[-1]
                        if "futures" in target_str:
                            new_bitget_symbols.add(sk.bitget_futures_key(base_coin))
                        else:
                            new_bitget_symbols.add(sk.spot_key(base_coin))
                    elif "USDT" in target_str:
                        ref_sym_binance = target_str.split("_")[0]
                        new_binance_symbols.add(ref_sym_binance)
                        
            new_binance_symbols = list(new_binance_symbols)
            new_bitget_symbols = list(new_bitget_symbols)
            
            # USDC volume 피처 계산을 위해 BTCUSDC 현물 거래 항상 구독 유지
            if "BTCUSDC" not in new_binance_symbols:
                new_binance_symbols.append("BTCUSDC")

            if sk.FX_RATE_SYMBOL not in new_crypto_symbols:
                new_crypto_symbols.append(sk.FX_RATE_SYMBOL)
                
            # 신규 심볼들을 DataManager에 동적으로 등록
            for sym in new_crypto_symbols + new_stock_symbols + new_binance_symbols + new_bitget_symbols:
                self.data_manager.register_symbol(sym)
                
            # 5. 커넥터 동적 재기동 판정
            # A. Upbit Connector — 재연결 없이 구독 갱신
            if set(self.crypto_symbols) != set(new_crypto_symbols):
                logging.info(f"[Engine] Crypto symbols changed. Hot-restarts Upbit WS... {new_crypto_symbols}")
                if hasattr(self, 'upbit_connector') and self.upbit_connector:
                    # 재연결 없이 구독 패킷만 갱신 (지연 26~108초 → 1초 이내)
                    await self.upbit_connector.update_symbols(new_crypto_symbols)
                    # 전략별 호가 레벨 갱신
                    for sym in new_crypto_symbols:
                        evaluator = self.strategy_manager.get_evaluator(sym)
                        if evaluator and evaluator.orderbook_level > 0:
                            self.upbit_connector.required_levels.setdefault(sym, {0.0: 'RAW'})
                            self.upbit_connector.required_levels[sym][evaluator.orderbook_level] = 'STRAT'
                    await self.upbit_connector.subscribe()
                else:
                    # 커넥터 자체가 없는 경우에만 새로 생성 (최초 기동 시)
                    self.upbit_connector = UpbitConnector(new_crypto_symbols, self.queue)
                    for sym in new_crypto_symbols:
                        evaluator = self.strategy_manager.get_evaluator(sym)
                        if evaluator and evaluator.orderbook_level > 0:
                            self.upbit_connector.required_levels[sym][evaluator.orderbook_level] = 'STRAT'
                    asyncio.create_task(self.upbit_connector.connect())
                self.crypto_symbols = new_crypto_symbols
                
                # 신규 추가된 종목에 대해 CandleAggregator 자동 빌드 및 인터벌 파싱 주입
                from core.candle_aggregator import CandleAggregator
                global_candle_interval = new_config.get("CANDLE_INTERVAL_MINUTES", 15)
                templates = new_config.get("STRATEGY_TEMPLATES", {})
                ai_template = templates.get("ai_baker", {})
                deploy_base = ai_template.get("deploy_base", "")
                deploy_session = ai_template.get("deploy_session", "")

                for sym in new_crypto_symbols:
                    if "-" in sym and sym not in self.candle_aggregators:
                        interval_minutes = global_candle_interval
                        interval_seconds = None
                        for t_conf in targets:
                            if t_conf.get("symbol") == sym and t_conf.get("strategy_name") == "ai_baker" and t_conf.get("active", False):
                                session = t_conf.get("deploy_session", deploy_session)
                                base = t_conf.get("deploy_base", deploy_base)
                                if not os.path.isabs(base):
                                    base = os.path.normpath(os.path.join(
                                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), base
                                    ))
                                deploy_path = os.path.join(base, session)
                                try:
                                    spec_candidates = [
                                        os.path.join(deploy_path, "model", "feature_spec.json"),
                                        os.path.join(deploy_path, "feature_spec.json")
                                    ]
                                    found_spec = None
                                    for sc in spec_candidates:
                                        if os.path.exists(sc):
                                            found_spec = sc
                                            break
                                    if found_spec:
                                        with open(found_spec, "r", encoding="utf-8") as f:
                                            spec = json.load(f)
                                            interval_str = spec.get("interval") or spec.get("feature_params", {}).get("interval", "15m")
                                            interval_seconds = parse_interval_seconds(interval_str)
                                            interval_minutes = parse_interval_minutes(interval_str)
                                            logging.info(f"[Engine] Automatically set dynamic CandleAggregator interval for {sym} to {interval_str} ({interval_minutes:.2f}m / {interval_seconds}s) from AI model spec ({found_spec}).")
                                except Exception as e:
                                    logging.warning(f"[Engine] Failed to parse interval from spec during reload for {sym}: {e}")
                                break
                        sym_market_type = detect_market_type({"SYMBOL": sym, "SYMBOL_UPBIT": sym})
                        self.candle_aggregators[sym] = CandleAggregator(interval_minutes=interval_minutes, interval_seconds=interval_seconds, max_candles=1000, market_type=sym_market_type, symbol=sym)

            # B. Binance Connector — BinanceFuturesRadar가 markPrice 커버하므로
            #    trade/@depth 상세 스트림만 재연결 (필요 시)

            if set(self.binance_symbols) != set(new_binance_symbols):
                logging.info(f"[Engine] Binance symbols changed. Hot-restarts Binance WS... {new_binance_symbols}")
                if hasattr(self, 'binance_connector') and self.binance_connector:
                    await self.binance_connector.stop()
                
                self.binance_symbols = new_binance_symbols
                self.binance_connector = BinanceConnector(self.binance_symbols, self.queue)
                asyncio.create_task(self.binance_connector.connect())
                
            # C. Bitget Connector
            if set(self.bitget_symbols) != set(new_bitget_symbols):
                logging.info(f"[Engine] Bitget symbols changed. Hot-restarts Bitget WS... {new_bitget_symbols}")
                if hasattr(self, 'bitget_connector') and self.bitget_connector:
                    await self.bitget_connector.stop()
                
                self.bitget_symbols = new_bitget_symbols
                self.bitget_connector = BitgetConnector(self.bitget_symbols, self.queue)
                asyncio.create_task(self.bitget_connector.connect())
                
            # D. Stock KIS Connector
            if set(self.stock_symbols) != set(new_stock_symbols):
                logging.info(f"[Engine] Stock symbols changed. Hot-restarts KIS WS... {new_stock_symbols}")
                if hasattr(self, 'kis_connector') and self.kis_connector:
                    await self.kis_connector.stop()
                
                self.stock_symbols = new_stock_symbols
                self.kis_connector = KISConnector(self.stock_symbols, self.queue, self.auth_manager, self.is_simulation)
                asyncio.create_task(self.kis_connector.connect())
            
            # E. AI 전략 모듈 설정 동적 업데이트
            enable_parity = new_config.get("GLOBAL_STRATEGY", {}).get("ENABLE_PARITY_LOGGING", True)
            for ai_mod in self.ai_strategies.values():
                ai_mod.enable_parity_logging = enable_parity

            self.add_log(shared_state, "✅ 전략 및 실시간 웹소켓 구독이 즉시 핫 리로드되었습니다.", log_type="SYSTEM")
            logging.info(f"[Engine] Hot-reload and stream resubscription complete. New targets: {new_targets}")
        except Exception as e:
            logging.error(f"[Engine] Hot-reload failed: {e}", exc_info=True)
            self.add_log(shared_state, f"❌ 전략 리로드 실패: {str(e)}", log_type="SYSTEM")


    async def command_loop(self, shared_state):
        logging.info("[Engine] Command Processor Loop Started")
        while self.is_running:
            try:
                # 큐가 비어있을 때까지 모든 명령 처리
                while True:
                    try:
                        cmd = shared_state.cmd_queue.get_nowait()
                        logging.info(f"📩 [Engine] Command Processing: {cmd}")
                        await self.handle_command(shared_state, cmd)
                    except Exception as inner_e:
                        # queue.Empty는 get_nowait에서 raise됨
                        if "Empty" in type(inner_e).__name__:
                            break
                        logging.error(f"[Engine] Inner Command Loop Error: {inner_e}")
                        break
            except Exception as e:
                logging.error(f"[Engine] Command Loop Outer Error: {e}")
            await asyncio.sleep(0.05)

    async def consumer_loop(self, shared_state):
        logging.info("[Engine] Consumer Loop Started (Dynamic Rules Engine Active)")
        self.add_log(shared_state, "🚀 다이내믹 룰 엔진 기반 시뮬레이션 가동 시작")
        
        # 기동 중 발생한 에러 알림 UI 로그 창에 덤프 출력
        if hasattr(self, 'init_errors') and self.init_errors:
            for err in self.init_errors:
                # ⚠️ 경고 알림 형태로 출력
                if "로드 실패" in err or "롤백" in err:
                    self.add_log(shared_state, err, log_type="WARNING")
                else:
                    self.add_log(shared_state, err, log_type="SYSTEM")
            # 한번 출력 후 클리어
            self.init_errors = []

        # [Startup Reconciliation] 재기동 시 이력 및 업비트 잔고 확인 후 포지션 자동 복원
        await self.reconcile_startup_positions(shared_state)
            
        last_ui_update = 0
        
        while self.is_running:
            try:
                data = await self.queue.get()
                
                # 서버 작동이 꺼진 상태(server_active = False)인 경우 데이터를 버리고 분석을 수행하지 않음
                if not shared_state.control.get("server_active", True):
                    self.queue.task_done()
                    continue
                    
                # ── 가상 종목 데이터 큐 재주입 분배 ──
                data_symbol = getattr(data, 'symbol', '')
                if data_symbol in self.reverse_ref_mapping:
                    from copy import copy
                    for v_sym in self.reverse_ref_mapping[data_symbol]:
                        if v_sym != data_symbol:
                            v_data = copy(data)
                            v_data.symbol = v_sym
                            self.queue.put_nowait(v_data)

                self.data_manager.update_state(data)

                # ── 데이터 분배 (캔들 어그리게이터 + AI 전략 모듈) — TickRouter 캡슐화 ──
                route_tick(data, self.candle_aggregators, self.ai_strategies)


                curr_time = time.time()
                update_interval = self.config.get("UPDATE_INTERVAL", 0.3)
                if curr_time - last_ui_update > update_interval:
                    current_prices = {}
                    
                    # 1. Active 종목 리스트 확인
                    all_store_symbols = list(self.data_manager.store.keys())
                    ui_focus_symbol = shared_state.control.get("ui_focus_symbol", "KRW-ETH")
                    
                    # UI 업데이트 대상 필터링: Focus + 포지션 보유 종목 + 매수 대기 종목 + 10%의 Round-robin 종목군
                    high_priority_symbols = {ui_focus_symbol}
                    for sym in all_store_symbols:
                        evaluator = self.strategy_manager.get_evaluator(sym)
                        if evaluator and evaluator.active:
                            # 포지션 보유 중
                            if self.simulator.positions.get(sym):
                                high_priority_symbols.add(sym)
                            # 매수 확인 펜딩 중
                            elif sym in self.entry_pending:
                                high_priority_symbols.add(sym)
                                
                    # Round-robin 인덱스 계산 (한 번에 약 10%씩 순환 업데이트)
                    chunk_size = max(1, len(all_store_symbols) // 10)
                    start_idx = self._stagger_index
                    end_idx = start_idx + chunk_size
                    round_robin_symbols = all_store_symbols[start_idx:end_idx]
                    
                    # 다음 루프 인덱스 갱신
                    self._stagger_index = end_idx if end_idx < len(all_store_symbols) else 0
                    
                    symbols_to_update = high_priority_symbols.union(round_robin_symbols)
                    
                    # 기존 UI 맵 데이터 복제 및 갱신 대상 병합
                    ui_updates = dict(self._last_ui_updates)
                    
                    for symbol in list(self.data_manager.store):
                        try:
                            if symbol in self.silent_stock_symbols:
                                continue  # 참조 전용 대장주는 매매 평가 스킵
                            s_data = self.data_manager.store[symbol]
                            ticker = s_data.get("ticker")
                            price = ticker.price if ticker else 0
                            current_prices[symbol] = price

                            # 시세 stale 감시: 포지션 보유 종목의 시세 갱신이 끊기면(웹소켓 단절 등) 손절 판단이 마지막 가격에
                            # 멈춰 있으므로 경고 로그/플래그를 남긴다 (자동 매매 판단 자체는 변경하지 않음).
                            try:
                                stale_sec = float(self.config.get("GLOBAL_STRATEGY", {}).get("STALE_PRICE_WARN_SEC", 30))
                                last_upd = float(s_data.get("last_update", 0) or 0)
                                if self.simulator.positions.get(symbol) and last_upd > 0 and (curr_time - last_upd) > stale_sec:
                                    self.stale_symbols.add(symbol)
                                    if curr_time - self._stale_warned_at.get(symbol, 0.0) >= 60.0:
                                        self._stale_warned_at[symbol] = curr_time
                                        logging.warning(f"[Engine] ⚠️ {symbol} 보유 중이나 시세가 {curr_time - last_upd:.0f}초째 갱신되지 않음(stale). 손절/청산 판단이 마지막 가격 기준으로 정지되어 있을 수 있습니다.")
                                else:
                                    self.stale_symbols.discard(symbol)
                            except Exception:
                                pass

                            evaluator = self.strategy_manager.get_evaluator(symbol)

                            # 종목별 타겟 설정 및 활성화 상태 확인
                            target_conf = next((t for t in self.config.get("SCALPING_TARGETS", []) if t.get("symbol") == symbol), None)
                            is_symbol_active = target_conf.get("active", True) if target_conf else (evaluator.active if evaluator else True)
                            is_symbol_auto_trade = target_conf.get("auto_trade", True) if target_conf else (evaluator.auto_trade if evaluator else True)
                        
                            # AIStrategyModule 인스턴스에 auto_trade 속성이 명시되어 있다면 우선 반영
                            if symbol in self.ai_strategies and hasattr(self.ai_strategies[symbol], 'auto_trade'):
                                is_symbol_auto_trade = getattr(self.ai_strategies[symbol], 'auto_trade', is_symbol_auto_trade)

                            if evaluator and not evaluator.active:
                                continue
                            if not is_symbol_active:
                                continue

                            ctx = {}  # 심볼 간 ctx 잔류(스코프 오염) 방지 — 매 루프 초기화

                            if symbol in self.ai_strategies and price > 0:
                                ai_mod = self.ai_strategies[symbol]
                                from datetime import datetime, timezone, timedelta
                                KST = timezone(timedelta(hours=9))
                                signal = ai_mod.evaluate(price, current_timestamp=datetime.now(KST))

                                if signal:
                                    if signal.action == SignalAction.BUY:
                                        # 신규 매수는 전역 trading_active 및 종목별 auto_trade가 모두 ON일 때만 실행
                                        is_global_active = shared_state.control.get("trading_active", False)
                                        if not is_symbol_auto_trade:
                                            logging.info(f"[Engine] ⏸️ [AI_BUY_SKIP] {symbol} 종목별 자동매매 비활성화(auto_trade=False)로 진입 스킵 (prob={signal.probability:.3f})")
                                            if hasattr(ai_mod, 'on_buy_cancelled'):
                                                ai_mod.on_buy_cancelled(reason="SYMBOL_AUTO_TRADE_DISABLED")
                                        elif not is_global_active:
                                            logging.info(f"[Engine] ⏸️ [AI_BUY_SKIP] {symbol} 전역 매매 일시정지(trading_active=False)로 진입 스킵 (prob={signal.probability:.3f})")
                                            if hasattr(ai_mod, 'on_buy_cancelled'):
                                                ai_mod.on_buy_cancelled(reason="GLOBAL_TRADING_PAUSED")
                                        elif not self.simulator.positions.get(symbol):
                                            # 진입 예산(Budget) 산출: evaluator -> SCALPING_TARGETS -> STRATEGY_TEMPLATES -> DEFAULT_AI_BUDGET
                                            budget = DEFAULT_AI_BUDGET
                                            if evaluator and evaluator.budget:
                                                budget = evaluator.budget
                                            elif target_conf:
                                                strat_name = target_conf.get("strategy_name", "default_scalping")
                                                tmpl_budget = self.config.get("STRATEGY_TEMPLATES", {}).get(strat_name, {}).get("budget_per_trade", DEFAULT_AI_BUDGET)
                                                budget = target_conf.get("budget_per_trade") or tmpl_budget
                                        
                                            # [SmartOrderExecutor] 지정가 대기 매수 및 슬리피지 한도 통제 (인터벌 비례 동적 계산)
                                            interval_sec = int(ai_mod.interval_minutes * 60)
                                            default_wait = max(5, int(interval_sec * 0.75))
                                            configured_wait = self.config.get("GLOBAL_STRATEGY", {}).get("AI_BUY_WAIT_SECONDS")
                                            ai_buy_wait_sec = configured_wait if configured_wait is not None else default_wait
                                            ai_buy_wait_sec = min(ai_buy_wait_sec, max(5, interval_sec))
                                            ai_buy_max_slippage = self.config.get("GLOBAL_STRATEGY", {}).get("AI_BUY_MAX_SLIPPAGE_PCT", 0.06)
                                        
                                            # 낙수 방지: 손절선(SL) 비율을 초과하는 폭락 시 매수 취소
                                            sl_multiplier = ai_mod.params.get('stop_loss', 2.0)
                                            entry_atr = signal.entry_atr if signal.entry_atr > 0 else (price * 0.015)
                                            max_drop_pct = (entry_atr * sl_multiplier / price) * 100.0 if price > 0 else 3.0

                                            # ── late-binding(클로저 지연 바인딩) 방지: 루프 변수를 함수 인자로 스냅샷 전달 ──
                                            # ai_mod/wait/slippage/drop 값은 루프마다 재할당되므로, 태스크가 await 이후
                                            # 다른 종목의 값/모듈을 참조하는 크로스 심볼 상태 오염을 원천 차단한다.
                                            ai_mod_ref = ai_mod
                                            buy_wait_sec = ai_buy_wait_sec
                                            buy_max_slippage = ai_buy_max_slippage
                                            buy_max_drop_pct = max_drop_pct

                                            async def run_smart_buy(sym, trigger_p, bgt, prob, atr_val, ai_mod_ref, wait_sec, slip_pct, drop_pct):
                                                try:
                                                    avg_price = await self.order_executor.execute_smart_buy(
                                                        symbol=sym, 
                                                        trigger_price=trigger_p, 
                                                        budget=bgt, 
                                                        wait_seconds=wait_sec, 
                                                        max_slippage_pct=slip_pct,
                                                        max_drop_pct=drop_pct
                                                    )
                                                    if avg_price and avg_price > 0:
                                                        ai_mod_ref.on_buy_filled(
                                                            real_entry_price=avg_price,
                                                            entry_atr=atr_val,
                                                            timestamp=datetime.now(KST),
                                                            candle_idx=ai_mod_ref.current_candle_idx
                                                        )
                                                        self.add_log(shared_state, f"🤖 [AI_BUY] {sym} @ Real Avg: {avg_price:,.0f} | prob={prob:.3f}", log_type="TRADE")
                                                    else:
                                                        ai_mod_ref.on_buy_cancelled(reason="SLIPPAGE_OR_UNFILLED")
                                                        self.add_log(shared_state, f"⚠️ [AI_BUY_CANCEL] {sym} 미체결/슬리피지 취소", log_type="TRADE")
                                                except asyncio.CancelledError:
                                                    # 취소 시점에 주문이 이미 접수되어(shield 보호) 포지션이 등록되었다면 AI 상태를 체결로 동기화(고아 포지션 방지)
                                                    reg_pos = self.simulator.positions.get(sym)
                                                    if reg_pos and float(reg_pos.get("qty", 0.0)) > 0:
                                                        ai_mod_ref.on_buy_filled(
                                                            real_entry_price=float(reg_pos["price"]),
                                                            entry_atr=atr_val,
                                                            timestamp=datetime.now(KST),
                                                            candle_idx=ai_mod_ref.current_candle_idx
                                                        )
                                                        logging.warning(f"[Engine] Pending buy task cancelled for {sym} but order was already filled: position synced.")
                                                    else:
                                                        ai_mod_ref.on_buy_cancelled(reason="TASK_CANCELLED")
                                                        logging.info(f"[Engine] Pending buy task cancelled for {sym}")
                                                except Exception as e:
                                                    ai_mod_ref.on_buy_cancelled(reason=str(e))
                                                    logging.error(f"[Engine] run_smart_buy error for {sym}: {e}", exc_info=True)
                                                finally:
                                                    self.pending_buy_tasks.pop(sym, None)
                                        
                                            # 기존 동일 심볼의 pending 태스크가 있다면 취소 후 재등록
                                            if symbol in self.pending_buy_tasks:
                                                self.pending_buy_tasks[symbol].cancel()
                                        
                                            task = asyncio.create_task(run_smart_buy(symbol, price, budget, signal.probability, signal.entry_atr, ai_mod_ref, buy_wait_sec, buy_max_slippage, buy_max_drop_pct))
                                            self.pending_buy_tasks[symbol] = task

                                    elif signal.action == SignalAction.SELL:
                                        # 매도(손절/익절/시간만료/EOD)는 trading_active와 무관하게 100% 무조건 집행!
                                        # 매수 대기 태스크가 살아있다면 즉시 취소
                                        if symbol in self.pending_buy_tasks:
                                            self.pending_buy_tasks[symbol].cancel()
                                            ai_mod.on_buy_cancelled(reason="SELL_SIGNAL_RECEIVED")

                                        if self.simulator.positions.get(symbol):
                                            exit_reason_str = signal.reason.value
                                            # curr_time도 루프마다 재할당되므로 인자로 스냅샷 전달 (지연 바인딩 방지)
                                            exit_cur_time = curr_time
                                            async def run_smart_sell(sym, trigger_p, reason_str, net_ret, hold_m, cur_t):
                                                try:
                                                    profit_amt = await self.order_executor.execute_smart_sell(
                                                        symbol=sym, 
                                                        trigger_price=trigger_p, 
                                                        reason=reason_str
                                                    )
                                                    if profit_amt is not None and profit_amt is not False:
                                                        self.last_exit_times[sym] = cur_t
                                                        self.add_log(shared_state, f"🤖 [AI_{reason_str.upper()}] {sym} @ {trigger_p:,.0f} | Profit: {profit_amt:+,.0f} ({net_ret:+.2f}%) | Hold: {hold_m}m", log_type="TRADE")
                                                    elif profit_amt is None or profit_amt is False:
                                                        if sym in self.ai_strategies and hasattr(self.ai_strategies[sym], 'on_sell_cancelled'):
                                                            self.ai_strategies[sym].on_sell_cancelled(reason=f"SMART_SELL_FAILED_{reason_str}")
                                                except Exception as e:
                                                    logging.error(f"[Engine] run_smart_sell error for {sym}: {e}", exc_info=True)
                                                    if sym in self.ai_strategies and hasattr(self.ai_strategies[sym], 'on_sell_cancelled'):
                                                        self.ai_strategies[sym].on_sell_cancelled(reason=str(e))
                                        
                                            asyncio.create_task(run_smart_sell(symbol, price, exit_reason_str, signal.net_return, signal.hold_mins, exit_cur_time))
                                        else:
                                            # 시뮬레이터 포지션이 이미 정리된 상태라면 AI 전략 상태 동기화
                                            if hasattr(ai_mod, 'on_sell_filled'):
                                                ai_mod.on_sell_filled(net_return=signal.net_return, exit_type=signal.reason.value)
                                            else:
                                                ai_mod._close_position()

                            if evaluator and price > 0:
                                pos = self.simulator.positions.get(symbol)
                                profit_rate = ((price / pos['price']) - 1) * 100 if pos else 0.0
                                ctx = self.data_manager.get_rule_context(symbol, profit_rate, evaluator.orderbook_level)
                                await self.optimizer.evaluate(symbol, price, ctx)
                                # Rule-based 전략: 포지션 청산(손절/트레일링스톱/모멘텀이탈/타임아웃)은 항상 수행, 신규 매수는 trading_active 및 auto_trade 켜져있을 때만 수행
                                if symbol not in self.ai_strategies:
                                    if pos:
                                        # 트레일링 스톱 상태는 엔진 측 dict 로 추적한다.
                                        # (simulator.positions 는 매 접근 시 C++ 복사본을 반환하므로 pos[...] 수정은 반영되지 않는다)
                                        rp_state = self._rule_pos_state.get(symbol)
                                        if rp_state is None or rp_state.get("entry_time") != pos.get("time"):
                                            rp_state = {
                                                "entry_time": pos.get("time"),
                                                "max_price": max(float(pos["price"]), float(price)),
                                                "ts_active": False,
                                            }
                                            self._rule_pos_state[symbol] = rp_state

                                        # 최고가 실시간 갱신 및 수익률 지표 계산
                                        rp_state["max_price"] = max(rp_state["max_price"], price)
                                        curr_profit_rate = ((price / pos['price']) - 1) * 100
                                        max_profit_rate = ((rp_state["max_price"] / pos['price']) - 1) * 100
                                    
                                        entry_time = pos.get('time', curr_time)
                                        holding_time = curr_time - entry_time
                                    
                                        # 1. 가상 트레일링 스톱 판단 (+0.3% 돌파 시 활성화, 고점 대비 0.1% 양보 시 매도)
                                        if curr_profit_rate >= 0.3:
                                            rp_state["ts_active"] = True
                                        
                                        is_ts_triggered = False
                                        if rp_state["ts_active"] and curr_profit_rate <= max_profit_rate - 0.10:
                                            is_ts_triggered = True

                                        # 2. 5초 모멘텀 검증 이탈 판단 (진입 5~10초 시점에 수익률이 +0.05% 미만인 횡보/손실 코인 강제 이탈)
                                        is_momentum_exit_triggered = False
                                        if 5.0 <= holding_time <= 10.0 and curr_profit_rate < 0.05:
                                            is_momentum_exit_triggered = True

                                        if evaluator.should_sell(ctx):
                                            reason = "PROFIT" if profit_rate > 0 else "LOSS"
                                            profit_amt = await self.execute_sell(symbol, price, reason=reason)
                                            if profit_amt is not False:
                                                 self.last_exit_times[symbol] = curr_time
                                                 pnl_str = f"{((price / pos['price']) - 1) * 100:+.2f}%"
                                                 v_str = f"I:{ctx.get('local_intensity', 100):.0f}% M:{ctx.get('local_imbalance', 0):+.1f}% R:{ctx.get('local_rsi', 50):.0f}"
                                                 self.add_log(shared_state, f"🔄 [{reason}] {symbol} @ {price:,.0f} | Profit: {profit_amt:+,.0f} ({pnl_str}) [{v_str}]", log_type="TRADE")
                                        elif is_ts_triggered:
                                            profit_amt = await self.execute_sell(symbol, price, reason="TRAILING_STOP")
                                            if profit_amt is not False:
                                                 self.last_exit_times[symbol] = curr_time
                                                 pnl_str = f"{curr_profit_rate:+.2f}%"
                                                 self.add_log(shared_state, f"✨ [TRAILING_STOP] {symbol} @ {price:,.0f} | Profit: {profit_amt:+,.0f} ({pnl_str}) | Max: {max_profit_rate:.2f}%", log_type="TRADE")
                                        elif is_momentum_exit_triggered:
                                            profit_amt = await self.execute_sell(symbol, price, reason="5S_MOMENTUM_EXIT")
                                            if profit_amt is not False:
                                                 self.last_exit_times[symbol] = curr_time
                                                 pnl_str = f"{curr_profit_rate:+.2f}%"
                                                 self.add_log(shared_state, f"⏱️ [5S_MOMENTUM_EXIT] {symbol} @ {price:,.0f} | Profit: {profit_amt:+,.0f} ({pnl_str}) | Hold: {holding_time:.1f}s", log_type="TRADE")
                                        else:
                                            timeout = self.config.get("GLOBAL_STRATEGY", {}).get("EMERGENCY_EXIT_TIMEOUT", 60)
                                            fee_rate = getattr(self.simulator, 'fee_rate', 0.0005)
                                            breakeven_p = pos['price'] * (1 + round_trip_cost(fee_rate, 0.0, sell_tax_for_symbol(symbol)))
                                        
                                            if holding_time > timeout and price >= breakeven_p:
                                                profit_amt = await self.execute_sell(symbol, price, reason="TIMEOUT")
                                                if profit_amt is not False:
                                                    self.last_exit_times[symbol] = curr_time
                                                    pnl = ((price / pos['price']) - 1) * 100
                                                    self.add_log(shared_state, f"🚑 [TIME-EXIT] {symbol} @ {price:,.0f} | Profit: {profit_amt:+,.0f} ({pnl:+.2f}%) | Hold: {holding_time:.0f}s", log_type="TRADE")
                                    elif shared_state.control.get("trading_active", False) and is_symbol_auto_trade:
                                        self._rule_pos_state.pop(symbol, None)  # 포지션 없음: 트레일링 상태 정리
                                        if curr_time - self.start_time >= self.warmup_period:
                                            cooldown_sec = self.config.get("GLOBAL_STRATEGY", {}).get("REENTRY_COOLDOWN_SEC", 30)
                                            if curr_time - self.last_exit_times.get(symbol, 0.0) >= cooldown_sec:
                                                if evaluator.should_buy(ctx):
                                                    pending = self.entry_pending.get(symbol)
                                                    if pending is None:
                                                        # 최초 조건 충족: 트리거 시각 및 가격 기록, 아직 매수 안 함
                                                        self.entry_pending[symbol] = {
                                                            "trigger_time": curr_time,
                                                            "trigger_price": price
                                                        }
                                                    else:
                                                        # 5초 경과 후 가격 확인
                                                        elapsed_since_trigger = curr_time - pending["trigger_time"]
                                                        price_since_trigger = ((price / pending["trigger_price"]) - 1) * 100
                                                        confirm_sec = self.config.get("GLOBAL_STRATEGY", {}).get("ENTRY_CONFIRM_SEC", 5)
                                                        confirm_pct = self.config.get("GLOBAL_STRATEGY", {}).get("ENTRY_CONFIRM_PCT", 0.08)
                                                        cancel_pct  = self.config.get("GLOBAL_STRATEGY", {}).get("ENTRY_CANCEL_PCT", -0.05)
                                                        if elapsed_since_trigger >= confirm_sec:
                                                            if price_since_trigger >= confirm_pct:
                                                                # ✅ 5초 모멘텀 확인 → 실제 매수
                                                                if await self.execute_buy(symbol, price, evaluator.budget):
                                                                    v_str = f"I:{ctx.get('local_intensity', 0):.0f}% M:{ctx.get('local_imbalance', 0):+.1f}% R:{ctx.get('local_rsi', 50):.0f} +5s:{price_since_trigger:+.2f}%"
                                                                    self.add_log(shared_state, f"🎯 [BUY] {symbol} @ {price:,.0f} [{v_str}]", log_type="TRADE")
                                                            else:
                                                                # ❌ 5초 후 모멘텀 미달 → 대기 취소 (노이즈 제거)
                                                                self.add_log(shared_state, f"⛔ [ENTRY_CANCEL] {symbol} @ {price:,.0f} | 5s 모멘텀 미달: {price_since_trigger:+.2f}% (기준: +{confirm_pct}%)", log_type="TRADE")
                                                            self.entry_pending.pop(symbol, None)
                                                        elif price_since_trigger <= cancel_pct:
                                                            # ❌ 5초 내 역방향 이탈 → 즉시 대기 취소
                                                            self.add_log(shared_state, f"⛔ [ENTRY_ABORT] {symbol} @ {price:,.0f} | 역방향 이탈: {price_since_trigger:+.2f}%", log_type="TRADE")
                                                            self.entry_pending.pop(symbol, None)
                                                else:
                                                    # 조건이 다시 False가 된 경우 대기 취소
                                                    if symbol in self.entry_pending:
                                                        self.entry_pending.pop(symbol, None)

                            if symbol in symbols_to_update:
                                is_focused = (symbol == ui_focus_symbol)
                                ui_level = self.ui_levels.get(symbol, 0.0)
                                analysis = self.data_manager.get_analysis(symbol, level=ui_level, include_energy_map=is_focused)
                                if ticker or analysis:
                                    exchange_name = "KIS" if (symbol.isdigit() and len(symbol) == 6) or symbol in self.stock_symbols else ("BINANCE" if "USDT" in symbol else "UPBIT")
                                    ref_market = evaluator.reference_market if evaluator else "BINANCE"
                                
                                    # 종목 고유 설정값 직접 검색 (AI_BAKER 및 롤백 호환성 강화)
                                    target_strat = "default_scalping"
                                    target_auto_trade = True
                                    for t_conf in self.config.get("SCALPING_TARGETS", []):
                                        if t_conf.get("symbol") == symbol:
                                            target_strat = t_conf.get("strategy_name", "default_scalping")
                                            target_auto_trade = t_conf.get("auto_trade", True)
                                            break
                                
                                    # 전략 타임프레임(인터벌) 산출
                                    target_interval = ""
                                    if symbol in self.ai_strategies:
                                        target_interval = getattr(self.ai_strategies[symbol], "interval", "")
                                    elif symbol in self.candle_aggregators:
                                        ca = self.candle_aggregators[symbol]
                                        target_interval = getattr(ca, "interval_str", None) or f"{int(ca.interval_minutes)}m" if hasattr(ca, "interval_minutes") else "15m"
                                    if not target_interval:
                                        target_interval = self.config.get("INTERVAL", "15m")

                                    # AI 모델 세션명 및 배포일시 산출
                                    target_deploy_session = ""
                                    target_deployed_at = ""
                                    if symbol in self.ai_strategies:
                                        ai_mod = self.ai_strategies[symbol]
                                        target_deploy_session = getattr(ai_mod, "session_id", "")
                                        target_deployed_at = getattr(ai_mod, "deployed_at", "")

                                    ref_real_sym = self.ref_mapping.get(symbol, symbol)
                                    ui_updates[symbol] = {
                                        "exchange": ticker.exchange if ticker else exchange_name,
                                        "symbol": symbol,
                                        "price": price,
                                        "price_stale": symbol in self.stale_symbols,
                                        "last_update": s_data.get("last_update", time.time()),
                                        "intensity": analysis.get("intensity", 100.0) if analysis else 100.0,
                                        "time_intensity": analysis.get("time_intensity", 100.0) if analysis else 100.0,
                                        "imbalance": analysis.get("imbalance", 0.0) if analysis else 0.0,
                                        "rsi": analysis.get("rsi", 50.0) if analysis else 50.0,
                                        "local_time_intensity": analysis.get("time_intensity", 100.0) if analysis else 100.0,
                                        "local_imbalance": analysis.get("imbalance", 0.0) if analysis else 0.0,
                                        "local_rsi": analysis.get("rsi", 50.0) if analysis else 50.0,
                                        "price_vs_vwap": ((price / analysis["vwap"]) - 1) * 100 if analysis and analysis.get("vwap", 0) > 0 else 0.0,
                                        "binance_futures_intensity": ctx.get("binance_futures_intensity", 100.0) if 'ctx' in locals() else 100.0,
                                        "bitget_futures_intensity": ctx.get("bitget_futures_intensity", 100.0) if 'ctx' in locals() else 100.0,
                                        "binance_price": self.data_manager.get_binance_price(ref_real_sym),
                                        "binance_futures_price": self.data_manager.get_binance_futures_price(ref_real_sym),
                                        "bitget_price": self.data_manager.get_bitget_price(ref_real_sym),
                                        "bitget_futures_price": self.data_manager.get_bitget_futures_price(ref_real_sym),
                                        "native_usdt_price": self.data_manager.get_native_usdt_price(ref_real_sym),
                                        "activity_5s": analysis.get("activity_5s", 0) if analysis else 0,
                                        "kimchi_premium": self.data_manager.get_kimchi_premium(ref_real_sym, ref_market),
                                        "orderbook": self.data_manager.get_orderbook(ref_real_sym, level=ui_level) if is_focused else {},
                                        "auto_trade": target_auto_trade,
                                        "strategy_name": target_strat,
                                        "strategy_interval": target_interval,
                                        "deploy_session": target_deploy_session,
                                        "deployed_at": target_deployed_at,
                                        "reference_market": ref_market,
                                        "orderbook_level": evaluator.orderbook_level if evaluator else 0.0,
                                        "energy_map": analysis.get("energy_map", {}) if is_focused else {},
                                        "energy_map_12h": analysis.get("energy_map_12h", {}) if is_focused else {},
                                        "warmup_status": self.ai_strategies[symbol].warmup_status if symbol in self.ai_strategies else {"ready": True, "status_text": "🟢 [READY]"},
                                        "trades": [t for t in list(self.data_manager.store[symbol].get("recent_trades", [])) if curr_time - t['t'] < 2.0] if is_focused else []
                                    }
                        except Exception as sym_err:
                            # 종목별 예외 격리: 한 종목의 예외가 뒤 종목의 손절/청산 평가까지 건너뛰지 않도록 한다
                            logging.error(f"[Engine] {symbol} 평가 중 예외(다른 종목 평가는 계속): {sym_err}", exc_info=True)
                    
                    # ui_focus_symbol의 캔들 정보 업데이트
                    focused_candles = []
                    if ui_focus_symbol in self.candle_aggregators:
                        from datetime import datetime
                        raw_candles = list(self.candle_aggregators[ui_focus_symbol].candles)
                        for c in raw_candles:
                            c_copy = c.copy()
                            if isinstance(c_copy.get("timestamp"), datetime):
                                c_copy["time"] = int(c_copy["timestamp"].timestamp())
                                del c_copy["timestamp"]
                            focused_candles.append(c_copy)
                    
                    self._last_ui_updates = ui_updates
                    
                    if self._shared_update_task is None or self._shared_update_task.done():
                        loop = asyncio.get_running_loop()
                        self._shared_update_task = asyncio.gather(
                            loop.run_in_executor(None, _set_shared_item, shared_state.data, '__symbols__', ui_updates),
                            loop.run_in_executor(None, _set_shared_item, shared_state.data, '__global__', self.data_manager.global_state),
                            loop.run_in_executor(None, _set_shared_item, shared_state.data, '__sim__', self.simulator.get_stats(current_prices)),
                            loop.run_in_executor(None, _set_shared_item, shared_state.data, '__focused_candles__', focused_candles)
                        )
                    
                    self.loop_count += 1
                    last_ui_update = curr_time
                
                self.queue.task_done()
                
            except Exception as e:
                logging.error(f"[Engine] Consumer Loop Error: {e}", exc_info=True)
                await asyncio.sleep(0.1)

    async def system_stats_loop(self, shared_state):
        """1초 주기로 CPU, 메모리, TPS 정보를 shared_state에 업데이트합니다."""
        logging.info("[Engine] System Stats Loop Started")
        while self.is_running:
            try:
                now = time.time()
                elapsed = now - self.last_stats_time
                if elapsed >= 1.0:
                    tps = self.loop_count / elapsed
                    cpu_pct = self.process.cpu_percent()
                    mem_mb = self.process.memory_info().rss / (1024 * 1024)
                    
                    # 시스템 전체 메모리 정보 추가
                    sys_mem = psutil.virtual_memory()
                    free_gb = sys_mem.available / (1024 * 1024 * 1024)
                    
                    shared_state.data['__system__'] = {
                        "cpu": cpu_pct,
                        "mem": mem_mb,
                        "free_mem": free_gb,
                        "tps": tps
                    }
                    
                    # 실시간 데이터 진단 스니퍼 상태 동기화
                    from utils.diagnostics.data_sniffer import DataSniffer
                    shared_state.data['__diag_status__'] = DataSniffer.instance().get_status()

                    # 5초마다 AI 모델 건강 상태 진단 및 shared_state 동기화 (경과시간 기반, L-6)
                    if now - getattr(self, '_last_health_ts', 0.0) >= 5.0 and self.ai_strategies:
                        self._last_health_ts = now
                        try:
                            from core.model_health_monitor import ModelHealthMonitor
                            shared_state.data['__model_health__'] = ModelHealthMonitor.get_instance().get_all_reports()
                        except Exception as mhe:
                            logging.warning(f"[Engine] Model health update error: {mhe}")
                    
                    # 초기화
                    self.loop_count = 0
                    self.last_stats_time = now
                    
                    # 10초마다 옵티마이저 핫스왑 체크 (경과시간 기반, L-6)
                    if now - getattr(self, '_last_optimizer_ts', 0.0) >= 10.0:
                        self._last_optimizer_ts = now
                        for sym in self.optimizer.shadow_targets.keys():
                            new_cfg = self.optimizer.check_for_upgrade(sym)
                            if new_cfg:
                                if self.strategy_manager.update_evaluator(sym, new_cfg):
                                    self.add_log(shared_state, f"🚀 [Optimizer] {sym} 전략 핫스왑 완료!", log_type="SYSTEM")

                    # 30초마다 주요 종목 지표 로그 (디버깅용, 경과시간 기반, L-6)
                    if now - getattr(self, '_last_symbol_log_ts', 0.0) >= 30.0:
                        self._last_symbol_log_ts = now
                        all_symbols = self.crypto_symbols + self.stock_symbols
                        # 심볼 데이터는 shared_state.data['__symbols__'] 아래에 저장됨
                        symbols_snapshot = shared_state.data.get('__symbols__', {})
                        for sym in all_symbols:
                            if sym in symbols_snapshot:
                                d = symbols_snapshot[sym]
                                # P: Price, I: Intensity, M: Imbalance, BI: Binance Intensity, RSI: RSI, VG: VWAP Gap, A: Activity
                                logging.info(f"💓 [Heartbeat] {sym} | P: {d.get('price', 0):,.0f} | I: {d.get('local_time_intensity', 0):.1f} | M: {d.get('local_imbalance', 0):.1f} | BI: {d.get('binance_futures_intensity', 0):.0f} | RSI: {d.get('local_rsi', 0):.1f} | VG: {d.get('price_vs_vwap', 0):.4f} | A: {d.get('activity_5s', 0)}")
            except Exception as e:
                logging.error(f"[Engine] System Stats Loop Error: {e}")
            
            await asyncio.sleep(1.0)

    async def run(self, shared_state):
        logging.info(f"[Engine] Starting... Mode: {'Simulation' if self.is_simulation else 'Real'}")
        
        # 기동 중 발생한 초기화 에러가 있을 경우 UI 로그에 출력
        for err in self.init_errors:
            self.add_log(shared_state, err, log_type="SYSTEM")
        self.init_errors.clear()
        
        await self.fetch_initial_prices(shared_state)
        
        tasks = []
        
        if self.stock_symbols:
            self.kis_connector = KISConnector(self.stock_symbols, self.queue, self.auth_manager, self.is_simulation)
            tasks.append(asyncio.create_task(self.kis_connector.connect()))
            logging.info(f"[Engine] KIS Auto-Bind: {self.stock_symbols}")

        if self.crypto_symbols:
            self.upbit_connector = UpbitConnector(self.crypto_symbols, self.queue)
            
            # 전략별로 필요한 호가 단위를 커넥터에 미리 등록
            for sym in self.crypto_symbols:
                evaluator = self.strategy_manager.get_evaluator(sym)
                if evaluator and evaluator.orderbook_level > 0:
                    # 비동기 루프 시작 전이므로 직접 셋에 추가 (나중에 connect 시 subscribe됨)
                    self.upbit_connector.required_levels[sym][evaluator.orderbook_level] = 'STRAT'
            
            tasks.append(asyncio.create_task(self.upbit_connector.connect()))
            logging.info(f"[Engine] Upbit Auto-Bind: {self.crypto_symbols} (Multi-Level Active)")

        if self.binance_symbols:
            self.binance_connector = BinanceConnector(self.binance_symbols, self.queue)
            tasks.append(asyncio.create_task(self.binance_connector.connect()))
            logging.info(f"[Engine] Binance Auto-Bind: {self.binance_symbols}")

        if self.bitget_symbols:
            self.bitget_connector = BitgetConnector(self.bitget_symbols, self.queue)
            tasks.append(asyncio.create_task(self.bitget_connector.connect()))
            logging.info(f"[Engine] Bitget Auto-Bind: {self.bitget_symbols}")

        # 코인베이스 실시간 Ticker 피더 구동 (실행 가상자산 종목들의 coinbase_symbol 자동 바인딩)
        from shared.config.config_manager import get_symbol_info
        cb_symbols_set = set()
        for c_sym in (self.crypto_symbols or []):
            s_info = get_symbol_info(c_sym)
            c_cb = s_info.get("coinbase_symbol")
            if c_cb:
                cb_symbols_set.add(c_cb)
        if not cb_symbols_set:
            cb_symbols_set.add("BTC-USD")
        cb_symbols_list = sorted(list(cb_symbols_set))
        self.coinbase_connector = CoinbaseConnector(symbols=cb_symbols_list, queue=self.queue)
        tasks.append(asyncio.create_task(self.coinbase_connector.connect()))
        logging.info(f"[Engine] Coinbase Auto-Bind: {cb_symbols_list}")

        # 8. 글로벌 매크로 실시간 피더 (Pyth + Hyperliquid)
        tasks.append(asyncio.create_task(self.global_macro_feeder.run()))

        # 8-B. cTrader 실시간 호가 피더 (전용 스레드 — 엔진 asyncio 와 분리)
        if getattr(self, "ctrader_feeder", None) is not None:
            if self.ctrader_feeder.start():
                logging.info("[Engine] cTrader 실시간 구독 시작")

        tasks.append(asyncio.create_task(self.command_loop(shared_state)))
        tasks.append(asyncio.create_task(self.consumer_loop(shared_state)))
        tasks.append(asyncio.create_task(self.portfolio_loop(shared_state)))
        tasks.append(asyncio.create_task(self.system_stats_loop(shared_state)))
        tasks.append(asyncio.create_task(self.binance_oi_polling_loop()))


        # 바이낸스 선물 Combined Stream 레이더 (210종목 실시간 괴리 감지용)
        from connectors.binance_futures_radar import BinanceFuturesRadar
        futures_radar = BinanceFuturesRadar()
        # 업비트 KRW 종목 목록은 ScalpingTargetRadar 기동 후 공유 (임시로 빈 리스트 → start() 안에서 재조회)
        # BinanceFuturesRadar는 ScalpingTargetRadar.start() 이후 krw_symbols를 받아 구독 시작
        
        # 실시간 거래량 폭발 탐지 모듈 구동 (futures_radar 전달)
        from core.scalping_target_radar import ScalpingTargetRadar
        scalping_target_radar = ScalpingTargetRadar(shared_state, futures_radar=futures_radar)
        await scalping_target_radar.start()
        
        # ScalpingTargetRadar가 fetch한 krw_symbols로 BinanceFuturesRadar 구독 시작
        await futures_radar.start(scalping_target_radar.krw_symbols)


        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logging.info("[Engine] Tasks Cancelled")
        except Exception as e:
            logging.error(f"[Engine] Runtime Error: {e}", exc_info=True)
        finally:
            self.is_running = False
            if self.coinbase_connector:
                await self.coinbase_connector.stop()
            await self.yahoo_futures_feeder.stop()
            # 선물 레이더 및 스캘핑 탐지기 우아한 정지
            await futures_radar.stop()
            await scalping_target_radar.stop()

    async def fetch_initial_prices(self, shared_state):
        async with aiohttp.ClientSession() as session:
            # KIS Initial Price Fetch
            if self.stock_symbols:
                token = await self.auth_manager.get_access_token()
                headers = self.auth_manager.get_base_headers()
                headers.update({
                    "tr_id": "FHKST01010100",
                    "custtype": "P"
                })
                for symbol in self.stock_symbols:
                    params = {
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_INPUT_ISCD": symbol
                    }
                    try:
                        async with session.get(f"{self.url_base}/uapi/domestic-stock/v1/quotations/inquire-price", headers=headers, params=params) as res:
                            if res.status == 200:
                                data = await res.json()
                                price = float(data.get('output', {}).get('stck_prpr', '0'))
                                self.data_manager.store[symbol]["ticker"] = TickData(symbol=symbol, exchange="KIS", price=price, volume=0, side="BID", timestamp=time.time())
                                self.data_manager.store[symbol]["last_update"] = time.time()
                            else:
                                logging.error(f"[Engine] KIS Price fetch failed for {symbol}: {res.status}")
                    except Exception as e:
                        logging.error(f"[Engine] KIS Price fetch exception for {symbol}: {e}")

            # Upbit Initial Price Fetch
            if self.crypto_symbols:
                upbit_symbols_query = ",".join(self.crypto_symbols)
                try:
                    async with session.get(f"https://api.upbit.com/v1/ticker?markets={upbit_symbols_query}") as res:
                        if res.status == 200:
                            data = await res.json()
                            for item in data:
                                symbol = item['market']
                                price = float(item['trade_price'])
                                self.data_manager.store[symbol]["ticker"] = TickData(symbol=symbol, exchange="UPBIT", price=price, volume=0, side="BID", timestamp=time.time())
                                self.data_manager.store[symbol]["last_update"] = time.time()
                        else:
                            logging.error(f"[Engine] Upbit Price fetch failed: {res.status}")
                                
                    # 호가 모아보기 지원 레벨 조회
                    async with session.get(f"https://api.upbit.com/v1/orderbook/supported_levels?markets={upbit_symbols_query}") as res:
                        if res.status == 200:
                            data = await res.json()
                            levels_map = {}
                            for item in data:
                                levels_map[item['market']] = item.get('supported_levels', [0])
                            self.data_manager.global_state["supported_ob_levels"] = levels_map
                        else:
                            logging.error(f"[Engine] Upbit Supported Levels fetch failed: {res.status}")
                            
                except Exception as e:
                    logging.error(f"[Engine] Upbit Price/Levels fetch exception: {e}")

            # Binance Initial Price Fetch (Spot and Futures)
            if self.binance_symbols:
                for symbol in self.binance_symbols:
                    try:
                        if sk.is_binance_futures_key(symbol):
                            # Binance Futures API endpoint
                            url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sk.exchange_symbol(symbol)}"
                            exchange_type = "BINANCE_FUTURES"
                        else:
                            # Binance Spot API endpoint
                            url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
                            exchange_type = "BINANCE"

                        async with session.get(url) as res:
                            if res.status == 200:
                                data = await res.json()
                                price = float(data['price'])
                                # Store with the original symbol (e.g., ETHUSDT_PERP)
                                tick = TickData(symbol=symbol, exchange=exchange_type, price=price, volume=0, side="BID", timestamp=time.time())
                                self.data_manager.store[symbol]["ticker"] = tick
                                self.data_manager.store[symbol]["last_update"] = time.time()
                                self.data_manager.update_state(tick)
                            else:
                                err_text = await res.text()
                                if "Invalid symbol" in err_text or "-1121" in err_text:
                                    logging.warning(f"[Engine] Binance {exchange_type} price fetch skipped (symbol not listed): {symbol}")
                                else:
                                    logging.error(f"[Engine] Binance {exchange_type} Price fetch failed for {symbol}: {res.status} - {err_text}")
                    except Exception as e:
                        logging.error(f"[Engine] Binance {exchange_type} Price fetch exception for {symbol}: {e}")

            # Bitget Initial Price Fetch (Spot and Futures)
            if self.bitget_symbols:
                for symbol in self.bitget_symbols:
                    try:
                        if sk.is_bitget_futures_key(symbol):
                            # Bitget Futures API endpoint (v2)
                            base_symbol = sk.exchange_symbol(symbol)
                            url = f"https://api.bitget.com/api/v2/mix/market/ticker?productType=USDT-FUTURES&symbol={base_symbol}"
                            exchange_type = "BITGET_FUTURES"
                        else:
                            # Bitget Spot API endpoint (v2)
                            url = f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={symbol}"
                            exchange_type = "BITGET"

                        async with session.get(url) as res:
                            if res.status == 200:
                                data = await res.json()
                                if data.get('code') == '00000' and data.get('data'):
                                    # data['data'] is a list
                                    price_str = data['data'][0].get('lastPr', '0')
                                    if not price_str and exchange_type == "BITGET":
                                        price_str = data['data'][0].get('lastPr', '0') # maybe same
                                    price = float(price_str)
                                    tick = TickData(symbol=symbol, exchange=exchange_type, price=price, volume=0, side="BID", timestamp=time.time())
                                    self.data_manager.store[symbol]["ticker"] = tick
                                    self.data_manager.store[symbol]["last_update"] = time.time()
                                    self.data_manager.update_state(tick) # 글로벌 상태 연동을 위해 호출
                                else:
                                    logging.error(f"[Engine] Bitget returned error or empty data for {symbol}: {data}")
                            else:
                                logging.error(f"[Engine] Bitget {exchange_type} Price fetch failed for {symbol}: {res.status} - {await res.text()}")
                    except Exception as e:
                        logging.error(f"[Engine] Bitget {exchange_type} Price fetch exception for {symbol}: {e}")

            # 실전 정합성을 보장하기 위한 과거 캔들 백필 (Historical Warm-up) 구동
            try:
                await self.fetch_historical_warmup_candles(session)
            except Exception as e:
                logging.error(f"[Engine] Failed to warm-up historical candles: {e}")

    def _load_krx_live_history(self, sym, ai_mod) -> list:
        """scale_v2 KRX 모델의 장 시작 전 이력 (CAiModelBaker/scripts/build_live_history.py 를 별도 프로세스로 실행).

        조건이 하나라도 어긋나면 빈 목록 — 대기(READY 미전환)가 틀린 지표보다 낫다:
          · 스펙이 scale_v2 가 아님 (legacy 는 날짜별 계산이라 과거 봉이 필요 없다)
          · 장중 재기동 — 오늘 이미 지난 봉은 DataVault 에 없어(익일 06:00 이후 저장) 이력에 구멍이 생긴다
          · 조립 실패 / 마지막 거래일이 5일 넘게 오래됨
        """
        import subprocess
        import pandas as pd
        from shared.core.market_utils import krx_session
        spec = getattr(ai_mod, 'feature_spec', None) or {}
        if spec.get('param_policy') != 'scale_v2':
            return []
        KST = timezone(timedelta(hours=9))
        now = datetime.now(KST)
        sess = krx_session(now.date())
        if sess.open <= now.time() < sess.close:
            logging.warning(f"[Engine] {sym} 장중 재기동 — 오늘 지난 봉이 이력에 없어 백필하지 않습니다 (실시간 누적 대기)")
            return []
        bars = int(getattr(ai_mod, 'required_warmup_candles', 0) or spec.get('required_history_bars') or 300)
        system_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script = os.path.join(system_root, "CAiModelBaker", "scripts", "build_live_history.py")
        out = os.path.join(system_root, "DataVault", "features", sym, f"live_history_{ai_mod.interval}.parquet")
        try:
            r = subprocess.run([sys.executable, script, "--symbol", sym, "--interval", str(ai_mod.interval),
                                "--bars", str(bars), "--out", out],
                               cwd=system_root, capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                logging.error(f"[Engine] {sym} 이력 조립 실패: {(r.stderr or r.stdout)[-800:]}")
                return []
            df = pd.read_parquet(out)
        except Exception as e:
            logging.error(f"[Engine] {sym} 이력 조립 실패: {e}")
            return []
        if df.empty:
            return []
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        last_day = df["timestamp"].max().date()
        if (now.date() - last_day).days > 5:
            logging.error(f"[Engine] {sym} 이력의 마지막 거래일 {last_day} 이 너무 오래돼 백필하지 않습니다 (DataVault 수집 확인)")
            return []
        if last_day >= now.date():
            df = df[df["timestamp"].dt.date < now.date()]
        rows = []
        for rec in df.to_dict("records"):
            ts = pd.Timestamp(rec.pop("timestamp")).to_pydatetime().replace(tzinfo=KST)
            rec = {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in rec.items()}
            rec["timestamp"] = ts
            rec.setdefault("upbit_close", rec.get("target_close"))
            rows.append(rec)
        logging.info(f"[Engine] {sym} scale_v2 이력 {len(rows):,}봉 백필 준비 ({rows[0]['timestamp']} ~ {rows[-1]['timestamp']})")
        return rows

    async def fetch_historical_warmup_candles(self, session):
        """AI 전략 모듈에 등록된 종목들을 대상으로 업비트/바이낸스/KIS REST API를 페이지네이션 조회하여 모델 인터벌에 맞는 캔들을 충분히 백필 채워 넣습니다."""
        logging.info("[Engine] Fetching historical candles for Warm-up (Pagination & Deep History)...")
        
        current_ai_symbols = list(self.ai_strategies.keys())
        if not current_ai_symbols:
            return
            
        for sym in current_ai_symbols:
            ai_mod = self.ai_strategies[sym]
            interval_sec = int(round(getattr(ai_mod, 'interval_seconds', 0))) or max(1, int(round(ai_mod.interval_minutes * 60)))
            interval_min = int(round(ai_mod.interval_minutes)) if ai_mod.interval_minutes >= 1.0 else 1
            b_interval = f"{interval_min}m" if interval_min >= 1 else "1m"
            if interval_min == 60:
                b_interval = "1h"
            elif interval_min == 240:
                b_interval = "4h"

            # 모델이 필요로 하는 최대 피처 윈도우 기반 동적 목표 캔들 수 산출 (봉길이/지표 연동)
            req_candles = getattr(ai_mod, 'required_warmup_candles', 50)
            target_candles = min(1500, max(req_candles + 100, 1000))

            is_krx = self.is_kis_symbol(sym) or sym.isdigit() or ("-" not in sym and "_" not in sym and not sym.endswith("USDT"))
            candles_list = []

            from datetime import datetime, timezone, timedelta
            KST = timezone(timedelta(hours=9))

            # 1. 시세 피드 과거 캔들 조회 (KRX vs 업비트)
            if is_krx:
                # 1-A. KRX 국내주식/ETF
                # scale_v2 모델: 학습 Gold 와 같은 함수로 DataVault 에서 조립한 과거 봉을 장 시작 전에 채운다
                #   (지표가 세션을 넘어 연속 계산되므로 첫 봉부터 학습과 같은 값이 나와야 한다).
                # legacy 모델/이력 불가: 가짜 캔들을 넣지 않고 실시간 캔들이 쌓일 때까지 대기한다.
                #   (과거에는 현재가로 평평한 가짜 캔들을 채워 AI 입력과 Bad Tick 필터를 오염시켰다)
                candles_list = await asyncio.to_thread(self._load_krx_live_history, sym, ai_mod)
                if not candles_list:
                    logging.warning(
                        f"[Engine] {sym} KRX 과거 봉 백필 없음 — 실시간 캔들이 필요 수량만큼 누적될 때까지 "
                        f"warmup 은 READY 로 전환되지 않습니다."
                    )
            else:
                # 1-B. 가상자산: 업비트 페이지네이션(200개 x N회)으로 목표 수량 연속 수집
                upbit_interval_param = min(240, max(1, interval_min))
                if upbit_interval_param not in [1, 3, 5, 10, 15, 30, 60, 240]:
                    upbit_interval_param = 1

                raw_upbit_batches = []
                to_cursor = ""
                max_pages = (target_candles // 200) + 2
                logging.info(f"[Engine] Upbit deep warm-up starting for {sym}: target {target_candles} candles (Interval: {interval_min}m)...")

                for page_idx in range(max_pages):
                    batch_size = min(200, target_candles - len(raw_upbit_batches))
                    if batch_size <= 0:
                        break
                    to_q = f"&to={to_cursor}" if to_cursor else ""
                    upbit_url = f"https://api.upbit.com/v1/candles/minutes/{upbit_interval_param}?market={sym}&count={batch_size}{to_q}"
                    try:
                        async with session.get(upbit_url, timeout=aiohttp.ClientTimeout(total=8)) as res:
                            if res.status == 200:
                                b_data = await res.json()
                                if not b_data:
                                    break
                                raw_upbit_batches.extend(b_data)
                                oldest_utc = b_data[-1].get('candle_date_time_utc', '')
                                if oldest_utc:
                                    to_cursor = oldest_utc.replace("T", " ")
                                if len(b_data) < batch_size:
                                    break
                                await asyncio.sleep(0.08)  # 업비트 초당 10회 Rate Limit 준수
                            else:
                                logging.warning(f"[Engine] Upbit historical fetch status {res.status} for {sym}")
                                break
                    except Exception as e:
                        logging.warning(f"[Engine] Upbit historical fetch exception: {e}")
                        break

                if not raw_upbit_batches:
                    logging.warning(f"[Engine] No Upbit historical candles received for {sym}")
                    continue

                # 시간 순서 정렬 (과거 -> 현재)
                raw_upbit_batches.reverse()
                upbit_candles = raw_upbit_batches[-target_candles:]

                # 바이낸스 과거 캔들 동기화 조회 (최대 1,000개 일괄 조회)
                base_coin = sym.split("-")[-1] if "-" in sym else sym
                binance_symbol = sk.spot_key(base_coin)
                binance_limit = min(1000, target_candles)
                binance_url = f"https://api.binance.com/api/v3/klines?symbol={binance_symbol}&interval={b_interval}&limit={binance_limit}"
                binance_candles_map = {}
                try:
                    async with session.get(binance_url, timeout=aiohttp.ClientTimeout(total=8)) as res:
                        if res.status == 200:
                            b_data = await res.json()
                            for item in b_data:
                                ts_ms = int(item[0])
                                dt_kst = datetime.fromtimestamp(ts_ms / 1000.0, tz=KST)
                                time_key = dt_kst.strftime("%Y-%m-%d %H:%M:00")
                                binance_candles_map[time_key] = {
                                    "close": float(item[4]),
                                    "volume": float(item[5]),
                                    "taker_buy_volume": float(item[9]),
                                }
                except Exception as e:
                    logging.warning(f"[Engine] Binance historical fetch exception: {e}")

                # 과거 봉에 '지금' 가격이나 타겟/1380 같은 추정값을 넣지 않는다 — 그 시각의 관측이 없으면 NaN (2026-10-02).
                # 바이낸스 봉이 빠진 자리는 직전 실제 봉 종가를 이어 쓰되, 그 이전(첫 실제 봉 전)은 NaN 이다.
                last_binance_close = float("nan")

                for uc in upbit_candles:
                    dt_str = uc['candle_date_time_kst']
                    dt_kst = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=KST)
                    t_key = dt_kst.strftime("%Y-%m-%d %H:%M:00")
                    b_close = last_binance_close
                    b_volume = 0.0 if b_close == b_close else float("nan")   # 원천이 살아 있으면 체결 없음=0, 아니면 모름
                    b_taker = float("nan")                                   # 봉 내부 비율 — 체결이 없으면 정의되지 않는다
                    if t_key in binance_candles_map:
                        b_close = binance_candles_map[t_key]["close"]
                        b_volume = binance_candles_map[t_key]["volume"]
                        if b_volume > 0:
                            b_taker = binance_candles_map[t_key]["taker_buy_volume"] / b_volume
                        last_binance_close = b_close

                    candles_list.append({
                        "timestamp": dt_kst,
                        "target_open": float(uc.get('opening_price', uc['trade_price'])),
                        "target_high": float(uc['high_price']),
                        "target_low": float(uc['low_price']),
                        "target_close": float(uc['trade_price']),
                        "target_volume": float(uc['candle_acc_trade_volume']),
                        "target_trade_bid_ratio": 0.5,
                        "target_trade_count": float(uc.get('candle_acc_trade_count', 0) or 50.0),
                        "target_large_trade_count": 0.0,
                        "target_avg_trade_size": float(uc['candle_acc_trade_volume'] / (uc.get('candle_acc_trade_count', 1) or 1)),
                        "upbit_open": float(uc.get('opening_price', uc['trade_price'])),
                        "upbit_close": float(uc['trade_price']),
                        "upbit_high": float(uc['high_price']),
                        "upbit_low": float(uc['low_price']),
                        "upbit_volume": float(uc['candle_acc_trade_volume']),
                        "upbit_trade_bid_ratio": 0.5,
                        "upbit_trade_count": float(uc.get('candle_acc_trade_count', 0) or 50.0),
                        "upbit_large_trade_count": 0.0,
                        "upbit_avg_trade_size": float(uc['candle_acc_trade_volume'] / (uc.get('candle_acc_trade_count', 1) or 1)),
                        "binance_spot_close": b_close,
                        "binance_spot_volume": b_volume,
                        "binance_spot_taker_ratio": b_taker,
                        # 아래 계열은 과거 시점 값을 받아올 원천이 없다 → NaN ('모름').
                        # 종전엔 OI 0, 코인베이스←바이낸스가, KOSPI 선물←타겟가, NQ/ES←바이낸스 BTC 가,
                        # NVDA 125·10년물 110·WTI 75·금 2500·환율 1380 상수, 또는 '지금' 가격을 과거 봉 전체에 넣었다
                        # — 학습 데이터에 없는 거짓 관측이고, 지금 가격을 과거에 넣는 것은 미래 정보다.
                        "binance_futures_oi": float("nan"),
                        "binance_futures_oi_value": float("nan"),
                        "binance_usdc_volume": float("nan"),
                        "coinbase_spot_close": float("nan"),
                        "kospi_futures_close": float("nan"),
                        "futures_close": float("nan"),
                        "nq_futures_close": float("nan"),
                        "sp500_futures_close": float("nan"),
                        "nvda_futures_close": float("nan"),
                        "us10y_futures_close": float("nan"),
                        "wti_futures_close": float("nan"),
                        "gold_futures_close": float("nan"),
                        "usd_krw_fx": float("nan"),
                    })

            # 어그리게이터에 백필 주입
            agg = self.candle_aggregators.get(sym)
            for candle in candles_list:
                if agg:
                    agg.add_backfilled_candle(candle)
                    agg._last_target_price = candle.get("target_close", candle["upbit_close"])
                    agg._last_upbit_price = candle["upbit_close"]
                if ai_mod:
                    ai_mod.aggregator.add_backfilled_candle(candle)
                    ai_mod.aggregator._last_target_price = candle.get("target_close", candle["upbit_close"])
                    ai_mod.aggregator._last_upbit_price = candle["upbit_close"]
                    ai_mod.current_candle_idx = ai_mod.aggregator.total_completed_candles
                    if ai_mod.enable_parity_logging:
                        ai_mod._log_raw_candle(candle)

            loaded_count = len(candles_list)
            logging.info(f"[Engine] Successfully backfilled {loaded_count} historical candles for {sym} (Interval: {ai_mod.interval}, Target: {target_candles}).")

            # 웜업 상태 콘솔 & UI 상태 출력
            if ai_mod:
                st = ai_mod.warmup_status
                if st['ready']:
                    logging.info(f"[Engine] 🟢 {st['status_text']} for {sym} ({loaded_count}/{st['required_candles']} candles)")
                else:
                    logging.warning(f"[Engine] ⏳ {st['status_text']} for {sym} ({loaded_count}/{st['required_candles']} candles)")



    async def portfolio_loop(self, shared_state):
        from utils.portfolio_tracker import PortfolioTracker
        portfolio = PortfolioManager(self.auth_manager, self.upbit_auth, shared_state)
        while self.is_running:
            try:
                # 개별 거래소 조회 실패 시 전체 루프가 붕괴하거나 블로킹되지 않도록 예외 격리
                u_bal = []
                upbit_ok = False  # 조회 실패(None/예외)와 '보유 없음([])'을 구분 — 실패 시 잔고 대조 생략
                try:
                    _u_raw = await portfolio.fetch_upbit_balance()
                    if _u_raw is not None:
                        u_bal = _u_raw
                        upbit_ok = True
                except Exception as ue:
                    logging.error(f"[Portfolio] Upbit balance fetch failed: {ue}")
                    
                k_bal = {}
                try:
                    k_bal = await portfolio.fetch_kis_balance()
                except Exception as ke:
                    logging.error(f"[Portfolio] KIS balance fetch failed: {ke}")

                # 실시간 시세 사전 추출
                live_prices = {}
                for sym in self.stock_symbols + self.crypto_symbols:
                    store_item = self.data_manager.store.get(sym)
                    if store_item and store_item.get("ticker"):
                        live_prices[sym] = float(store_item["ticker"].price)

                # 업비트 보유 코인 중 실시간 시세가 없는 외부 종목 시세 일괄 보완
                if isinstance(u_bal, list):
                    missing_coins = []
                    for item in u_bal:
                        cur = str(item.get("currency", "")).upper()
                        if cur != "KRW" and (float(item.get("balance", 0)) + float(item.get("locked", 0))) > 0:
                            sym = f"KRW-{cur}"
                            if sym not in live_prices:
                                missing_coins.append(sym)
                    if missing_coins:
                        try:
                            fetched_prices = await portfolio.fetch_upbit_tickers(missing_coins)
                            live_prices.update(fetched_prices)
                        except Exception as fe:
                            logging.debug(f"[Portfolio] Missing tickers fetch failed: {fe}")

                active_bot = [t.get("symbol") for t in self.config.get("SCALPING_TARGETS", []) if t.get("active", True)]
                fx = float(shared_state.data.get('__global__', {}).get('usd_krw_fx', 1380.0))
                calc_data = PortfolioTracker.calculate_portfolio(u_bal, k_bal, live_prices, active_bot, usd_krw_fx=fx)
                
                # IPC 공유 메모리 동기화 전파
                _set_shared_item(shared_state.data, '__portfolio__', calc_data)

                # ── [Live Reconciliation] 실전 모드 시 실시간 거래소 잔고와 CTrade 포지션 정합성 검증 및 자동 보정 ──
                if not self.simulation_mode:
                    current_positions = dict(self.simulator.positions)
                    for sym, pos_info in current_positions.items():
                        cur_p = live_prices.get(sym, 0.0)
                        if self.is_kis_symbol(sym):
                            # KIS 국내주식 잔고 검증 (조회 성공 시에만 — 실패/미조회를 '잔고 0'으로 오판하지 않음)
                            # 해외주식은 잔고 조회가 NASD 단일 거래소라 누락 가능성이 있어 대조 대상에서 제외
                            if not (sym.isdigit() and len(sym) == 6):
                                continue
                            if not (isinstance(k_bal, dict) and k_bal.get("stocks_ok")):
                                continue
                            k_holdings = k_bal.get("stocks", [])
                            kis_qty = sum(float(h.get("hldg_qty", 0) or 0) for h in k_holdings if str(h.get("pdno", "")).strip() == sym)
                            if kis_qty <= 0 and cur_p > 0:
                                logging.warning(f"[LiveReconciliation] ⚠️ KIS 실잔고 0 감지: {sym}. CTrade 로컬 포지션 자동 청산(Sync) 집행")
                                await self.execute_sell(sym, cur_p, reason="LIVE_RECONCILE_ZERO_BALANCE")
                        else:
                            # 업비트 코인 잔고 검증 (조회 성공 시에만)
                            if upbit_ok and isinstance(u_bal, list):
                                cur_name = sym.split("-")[-1].upper()
                                upbit_qty = sum(
                                    float(acc.get("balance", 0.0)) + float(acc.get("locked", 0.0))
                                    for acc in u_bal if str(acc.get("currency", "")).upper() == cur_name
                                )
                                # 잔여 평가액이 5,000원 미만이거나 잔고 0인 경우 자동 청산 동기화
                                if upbit_qty <= 1e-6 or (cur_p > 0 and (upbit_qty * cur_p) < 5000.0):
                                    logging.warning(f"[LiveReconciliation] ⚠️ 업비트 실잔고 0/Dust 감지: {sym} (잔고: {upbit_qty:.8f}). CTrade 로컬 포지션 자동 청산(Sync) 집행")
                                    await self.execute_sell(sym, cur_p, reason="LIVE_RECONCILE_ZERO_BALANCE")
            except Exception as e:
                logging.error(f"Portfolio Loop Outer Error: {e}")
            
            # 15초 주기로 잔고 신속 반영
            await asyncio.sleep(15)

    async def binance_oi_polling_loop(self):
        """AI 전략 모듈에 등록된 종목들의 바이낸스 선물 Open Interest (OI) 정보를 주기적으로 폴링하여 주입합니다."""
        logging.info("[Engine] Binance OI Polling Loop Started")
        
        # AI 전략 심볼에 대응하는 선물 심볼 매핑 준비 (e.g. KRW-BTC -> BTCUSDT)
        target_symbols = []
        
        # 세션을 루프 전체에서 재사용 (매 30초마다 신규 생성하던 TCP 연결 overhead 제거)
        async with aiohttp.ClientSession() as session:
            while self.is_running:
                try:
                    # 활성화된 AI 전략의 종목 동적 추출
                    current_ai_symbols = list(self.ai_strategies.keys())
                    if not current_ai_symbols:
                        await asyncio.sleep(10)
                        continue
                    
                    for sym in current_ai_symbols:
                        if self.is_kis_symbol(sym):
                            continue  # 국내 주식/ETF는 바이낸스 선물 OI/펀딩비 조회 대상 제외

                        base_coin = sym.split("-")[-1] if "-" in sym else sym
                        binance_futures_symbol = sk.spot_key(base_coin) # Binance API uses WLDUSDT, BTCUSDT
                        
                        # 1. Open Interest 수신
                        url_oi = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={binance_futures_symbol}"
                        oi_data = None
                        for attempt in range(2):
                            try:
                                async with session.get(url_oi, timeout=aiohttp.ClientTimeout(total=5)) as res:
                                    if res.status == 200:
                                        oi_data = await res.json()
                                        break
                                    else:
                                        err_text = await res.text()
                                        logging.warning(f"[Engine] Binance OI fetch returned status {res.status} for {binance_futures_symbol}: {err_text}")
                            except Exception as e:
                                if attempt == 0:
                                    await asyncio.sleep(2)
                                else:
                                    logging.warning(f"[Engine] Binance OI fetch failed for {binance_futures_symbol}: {e}")

                        if oi_data and isinstance(oi_data, dict):
                            oi = float(oi_data.get('openInterest', 0.0))
                            futures_price = self.data_manager.get_binance_futures_price(sym)
                            if futures_price <= 0:
                                futures_price = self.data_manager.get_binance_price(sym)
                            oi_value = oi * futures_price if futures_price > 0 else 0.0

                            from datetime import datetime, timezone, timedelta
                            tick_ts = datetime.now(timezone(timedelta(hours=9)))

                            if sym in self.ai_strategies:
                                self.ai_strategies[sym].on_binance_futures_oi(oi, oi_value, tick_ts)
                            if sym in self.candle_aggregators:
                                self.candle_aggregators[sym].on_binance_futures_oi(oi, oi_value, tick_ts)

                        # 2. 확정 펀딩비(Funding Rate) 수신 (data_manager와 동일한 fapi/v1/fundingRate 엔드포인트)
                        url_fr = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={binance_futures_symbol}&limit=1"
                        fr_data = None
                        for attempt in range(2):
                            try:
                                async with session.get(url_fr, timeout=aiohttp.ClientTimeout(total=5)) as res_fr:
                                    if res_fr.status == 200:
                                        fr_data = await res_fr.json()
                                        break
                            except Exception as fe:
                                if attempt == 0:
                                    await asyncio.sleep(2)
                                else:
                                    logging.warning(f"[Engine] Binance Funding Rate fetch failed for {binance_futures_symbol}: {fe}")

                        if fr_data and isinstance(fr_data, list) and len(fr_data) > 0:
                            funding_rate = float(fr_data[0].get('fundingRate', 0.0))
                            from datetime import datetime, timezone, timedelta
                            tick_ts = datetime.now(timezone(timedelta(hours=9)))

                            if sym in self.ai_strategies:
                                self.ai_strategies[sym].on_binance_funding_rate(funding_rate, tick_ts)
                            if sym in self.candle_aggregators:
                                self.candle_aggregators[sym].on_binance_funding_rate(funding_rate, tick_ts)
                            
                except Exception as e:
                    logging.error(f"[Engine] Binance FAPI Polling Loop Error: {e}")
                    
                # 30초마다 폴링
                await asyncio.sleep(30)