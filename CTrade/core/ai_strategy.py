"""
CTrade - AI Strategy Module (Baker 전략 실전 실행기)

CAiModelBaker에서 학습/최적화된 LightGBM 모델·파라미터·피처 규격을
deploy/ 패키지에서 로드하여 실전 매매 신호를 생성합니다.

작동 사이클:
  1. 부팅 시 deploy 패키지에서 모델/파라미터/피처 규격을 원샷 로드
  2. CandleAggregator가 캔들을 완성할 때마다 피처 계산 + LightGBM 추론
  3. 확률값이 buy_threshold 이상이면 매수 신호 발생
  4. 보유 중에는 TBMPositionManager가 매 캔들마다 청산 조건을 판정
"""
import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd
import lightgbm as lgb

# shared 모듈 경로 확보
_CTRADE_SYSTEM_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CTRADE_SYSTEM_ROOT not in sys.path:
    sys.path.insert(0, _CTRADE_SYSTEM_ROOT)

from shared.features.feature_engine import run_feature_engineering
from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY
from shared.core.market_utils import parse_interval_minutes, parse_interval_seconds, detect_market_type, is_krx_entry_allowed
from shared.core.tbm_executor import TBMPositionManager
from shared.core.cexecution import CExecution, ExecutionConfig, CExecutionStreamSession
from shared.core.ai_policy import META_APPROVAL_THRESHOLD
from shared.core.trade_costs import round_trip_cost, sell_tax_for_symbol
from core.risk_manager import RiskManager
from core.candle_aggregator import CandleAggregator

KST = timezone(timedelta(hours=9))

__all__ = [
    "AIStrategyModule",
    "Signal",
    "SignalAction",
    "SignalReason",
]

# 실시간 정합성 검증(Parity) 로그에 기록할 원본 캔들 컬럼 정의 (단일 소스)
_RAW_CANDLE_COLUMNS = [
    'timestamp', 'target_open', 'target_high', 'target_low', 'target_close', 'target_volume',
    'target_trade_bid_ratio', 'target_trade_count', 'target_large_trade_count',
    'upbit_close', 'upbit_high', 'upbit_low', 'upbit_volume',
    'upbit_trade_bid_ratio', 'upbit_trade_count', 'upbit_large_trade_count',
    'upbit_avg_trade_size', 'binance_spot_close', 'binance_spot_volume',
    'binance_spot_taker_ratio', 'binance_futures_oi', 'binance_futures_oi_value',
    'binance_usdc_volume', 'coinbase_spot_close', 'kospi_futures_close', 'nq_futures_close', 'sp500_futures_close'
]


class SignalAction(Enum):
    """AI 전략 신호 액션"""
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class SignalReason(Enum):
    """AI 전략 신호 발생 사유"""
    AI_SIGNAL = "AI_SIGNAL"
    SHADOW = "SHADOW"
    TAKE_PROFIT = "익절"
    STOP_LOSS = "손절"
    EMERGENCY_STOP_LOSS = "비상손절"
    TIME_EXIT = "시간만료"
    DAY_EXIT = "당일청산"

    @classmethod
    def from_exit_type(cls, exit_type: str) -> "SignalReason":
        """TBM 청산 사유 문자열(한글) → SignalReason 매핑"""
        mapping = {
            "익절": cls.TAKE_PROFIT,
            "손절": cls.STOP_LOSS,
            "비상손절": cls.EMERGENCY_STOP_LOSS,
            "시간만료": cls.TIME_EXIT,
            "당일청산": cls.DAY_EXIT,
            "장마감청산": cls.DAY_EXIT,
        }
        # 미매핑 시 청산 성격의 TIME_EXIT으로 폴백 (SHADOW는 매수 미진입 신호용)
        return mapping.get(exit_type, cls.TIME_EXIT)



class Signal:
    """AI 전략이 생성하는 매매 신호 (action/reason 은 Enum 기반)"""
    def __init__(self, action: SignalAction, price: float = 0.0,
                 probability: float = 0.0,
                 reason: SignalReason = SignalReason.AI_SIGNAL,
                 net_return: float = 0.0, hold_mins: int = 0,
                 entry_atr: float = 0.0,
                 tp_price: float = 0.0, sl_price: float = 0.0,
                 features_snapshot: Optional[dict] = None):
        self.action = action
        self.price = price
        self.probability = probability
        self.reason = reason
        self.net_return = net_return
        self.hold_mins = hold_mins
        self.entry_atr = entry_atr
        self.tp_price = tp_price
        self.sl_price = sl_price
        self.features_snapshot = features_snapshot or {}

    def __repr__(self):
        return f"Signal({self.action.value}, p={self.probability:.3f}, reason={self.reason.value})"


class AIStrategyModule:
    """Baker 학습 모델 기반 실전 매매 전략 실행기

    사용법:
        strategy = AIStrategyModule(deploy_path, symbol='KRW-BTC')

        # 매 틱 수신 시:
        strategy.on_upbit_tick(price, volume, side, timestamp)
        strategy.on_binance_tick(price, volume, is_buyer_maker, timestamp)

        # 매 틱 or 주기적으로:
        signal = strategy.evaluate(current_price, current_timestamp)
        if signal and signal.action == 'BUY':
            # 매수 주문 집행
        elif signal and signal.action == 'SELL':
            # 매도 주문 집행
    """

    def __init__(self, deploy_path: str, symbol: str = "KRW-BTC",
                 fee_rate: float = 0.0005, slippage_rate: float = 0.0005,
                 enable_parity_logging: bool = True,
                 risk_manager: Optional[RiskManager] = None):
        """
        Args:
            deploy_path: 배포 세션 디렉토리 경로
                         (예: .../deploy/BTC_30m/BTC_20260709_M30_BEST4)
            symbol: 업비트 거래 심볼 (예: KRW-BTC)
            fee_rate: 편도 수수료율
            slippage_rate: 편도 슬리피지율
            enable_parity_logging: 실시간 Parity 검증 로깅 활성화 여부
            risk_manager: 전역 리스크 관리자 인스턴스 (미지정 시 자체 생성)
        """
        self.symbol = symbol
        self.deploy_path = deploy_path
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.enable_parity_logging = enable_parity_logging
        self.sell_tax = sell_tax_for_symbol(symbol)  # 매도세 (국내 주식 증권거래세, 원장 기준)
        self.cost = round_trip_cost(fee_rate, slippage_rate, self.sell_tax)  # 왕복 비용

        # ── 1. deploy 패키지 로드 ──
        model_dir = os.path.join(deploy_path, "model")

        # best_params 로드 (H-2: 결정론적 정렬 — os.listdir 순서 비의존)
        params_files = sorted(
            f for f in os.listdir(model_dir)
            if f.startswith("best_params_") and f.endswith(".json")
        )
        if not params_files:
            raise FileNotFoundError(f"best_params_*.json not found in {model_dir}")
        params_path = os.path.join(model_dir, params_files[0])
        with open(params_path, 'r', encoding='utf-8') as f:
            self.params = json.load(f)

        # interval 자동 감지
        self.interval = params_files[0].replace("best_params_", "").replace(".json", "")
        self.interval_minutes = parse_interval_minutes(self.interval)
        self.interval_seconds = parse_interval_seconds(self.interval)

        # ── LightGBM 모델 로드 (H-2: 결정론적 정확 매칭) ──
        #   정식 CTrade 소비 규격은 model_{interval}.txt 단일 파일이다.
        #   _primary / _meta 는 Baker 내부(메타 라벨링) 검증용이므로 주 모델로 절대 선택하지 않는다.
        self.model_path = self._resolve_model_file(model_dir)
        self.model = lgb.Booster(model_file=self.model_path)
        self.model_num_class = self._detect_num_class()
        logging.info(
            f"[AIStrategy] 모델 파일 선택: {os.path.basename(self.model_path)} "
            f"(num_class={self.model_num_class})"
        )

        # ── 메타(2차) 모델 로드 (H-3: 존재 시에만 실전 메타 필터 적용) ──
        #   메타 라벨링 배포 패키지(model_{interval}_primary.txt + _meta.txt)에서만 존재한다.
        self.meta_model = None
        self.meta_model_path = None
        _meta_candidate = os.path.join(model_dir, f"model_{self.interval}_meta.txt")
        if os.path.exists(_meta_candidate):
            try:
                self.meta_model = lgb.Booster(model_file=_meta_candidate)
                self.meta_model_path = _meta_candidate
                logging.info(
                    f"[AIStrategy] 메타 모델 로드: {os.path.basename(_meta_candidate)} "
                    f"(승인 임계값={META_APPROVAL_THRESHOLD})"
                )
            except Exception as e:
                # 메타 필터는 학습·백테스트가 전제한 승인 게이트이므로, 파일이 있는데 로드 실패면 필터 없이 매매하지 않는다.
                logging.error(f"[AIStrategy] 메타 모델 로드 실패: {e}")
                raise RuntimeError(f"메타 모델 로드 실패({_meta_candidate}): {e}") from e

        # feature_spec 로드
        feature_spec_path = os.path.join(model_dir, "feature_spec.json")
        if os.path.exists(feature_spec_path):
            with open(feature_spec_path, 'r', encoding='utf-8') as f:
                self.feature_spec = json.load(f)
            self.features_order = self.feature_spec['features_order']
            self.feature_params = self.feature_spec.get('feature_params', {})
        else:
            raise FileNotFoundError(f"feature_spec.json not found in {model_dir}")

        # ── 1-1. Parity Guard (배포 모델 피처 정합성 무결성 검증) ──
        self._verify_parity_guard()

        # config_snapshot에서 추가 설정 로드 (USE_MACRO_FILTER 등)
        config_snapshot_path = os.path.join(deploy_path, "config_snapshot.json")
        self.config_snapshot = {}
        if os.path.exists(config_snapshot_path):
            with open(config_snapshot_path, 'r', encoding='utf-8') as f:
                self.config_snapshot = json.load(f)

        # 모델 세션명 및 배포 타임스탬프
        self.session_id = os.path.basename(os.path.normpath(deploy_path))
        deployed_at_str = self.config_snapshot.get('DEPLOYED_AT') or self.config_snapshot.get('CREATED_AT')
        if not deployed_at_str and os.path.exists(feature_spec_path):
            try:
                mtime = os.path.getmtime(feature_spec_path)
                deployed_at_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        self.deployed_at = deployed_at_str or "N/A"

        self.use_macro_filter = self.config_snapshot.get('USE_MACRO_FILTER', False)
        self.macro_rsi_threshold = self.config_snapshot.get('MACRO_RSI_FILTER_THRESHOLD', 50.0)
        self.intraday_square_off = self.config_snapshot.get('INTRADAY_SQUARE_OFF', False)
        self.market_type = detect_market_type({"SYMBOL_UPBIT": self.symbol})

        # ── 2. 캔들 어그리게이터 초기화 (초/분 단위 정밀 지원) ──
        _req_warmup = self._calc_required_warmup_candles()
        self.aggregator = CandleAggregator(
            interval_minutes=self.interval_minutes,
            interval_seconds=self.interval_seconds,
            # 웜업 요구량(rsi_daily 등)이 2000봉을 넘을 수 있으므로 버퍼 확장
            max_candles=max(2000, _req_warmup + 200),
            market_type=self.market_type,
            symbol=self.symbol,   # 봉 라벨 규약(원장)을 종목별로 읽는다
        )
        self.required_warmup_candles = _req_warmup
        self.aggregator.min_required_candles = self.required_warmup_candles

        # ── 3. CExecution 코어 및 TBM 포지션 관리자 초기화 (SSOT) ──
        cfg_dict = self.config_snapshot.copy() if hasattr(self, 'config_snapshot') and self.config_snapshot else {}
        cfg_dict['INTERVAL'] = self.interval
        cfg_dict['INTRADAY_SQUARE_OFF'] = self.intraday_square_off
        self.exec_config = CExecution.create_config_from_params(self.params, cfg_dict)
        self.exec_config.fee_rate = self.fee_rate
        self.exec_config.slip_rate = self.slippage_rate
        self.exec_config.sell_tax = self.sell_tax
        self.exec_config.interval_minutes = self.interval_minutes

        self.exec_session = CExecutionStreamSession(self.exec_config)
        self.axiom_config = self.exec_config
        self.axiom_session = self.exec_session
        self.position_manager = TBMPositionManager(
            params=self.params,
            cost=self.cost,
            interval_minutes=self.interval_minutes,
            intraday_square_off=self.intraday_square_off,
            market_type=self.market_type
        )

        # ── 4. 포지션 상태 ──
        self.has_position = False
        self.is_pending_entry = False  # 매수 주문 집행 중(체결 대기) 플래그
        self.is_pending_exit = False   # 매도 주문 집행 중(체결 대기) 플래그
        self._position_reserved = False  # 리스크 관리자 슬롯 예약 여부 (pending 단계)
        self.entry_price = 0.0
        self.entry_atr = 0.0
        self.last_signal_atr = 0.0
        self.entry_time = None
        self.entry_candle_idx = 0
        self.current_candle_idx = 0  # 총 완성 캔들 수 추적

        # ── 4-B. 참조 대장주(Reference Symbols) 자동 등록 ──
        ref_symbols = self.config_snapshot.get('reference_symbols') or self.config_snapshot.get('REFERENCE_SYMBOLS') or []
        for r_item in ref_symbols:
            if isinstance(r_item, dict):
                r_sym = str(r_item.get('symbol', '')).strip().upper()
                r_alias = str(r_item.get('alias', r_sym)).strip().lower()
            else:
                r_sym = str(r_item).strip().upper()
                r_alias = r_sym.lower()
            if r_sym and r_alias:
                self.aggregator.register_ref_symbol(r_sym, r_alias)

        # 배포 원본 파라미터 보존 (비파괴적 테스트 모드 스위칭 지원)
        self.original_buy_threshold = float(self.params.get('buy_threshold', 0.5))
        self.original_cooldown_minutes = int(self.params.get('cooldown_minutes', 5))
        self.test_level = 0          # 0: OFF, 1: MILD, 2: ACTIVE, 3: TURBO
        self.test_mode_active = False
        self.bypass_krx_time_limit = False

        # 쿨다운 상태
        self._cooldown_until_candle = 0

        # 마지막 예측 확률 (UI 표시용)
        self.last_probability = 0.0
        self.last_signal_time = None

        # ── 5. 리스크 관리자 (전역 인스턴스 주입 지원) ──
        if risk_manager is not None:
            self.risk_manager = risk_manager
        else:
            risk_config = self.config_snapshot.get('RISK_MANAGEMENT', {})
            self.risk_manager = RiskManager(
                daily_loss_limit_pct=risk_config.get('DAILY_LOSS_LIMIT_PCT', -3.0),
                max_consecutive_losses=risk_config.get('MAX_CONSECUTIVE_LOSSES', 3),
                consecutive_loss_cooldown_min=risk_config.get('CONSECUTIVE_LOSS_COOLDOWN_MIN', 60),
                max_concurrent_positions=risk_config.get('MAX_CONCURRENT_POSITIONS', 3)
            )

        # Shadow Mode (신호만 로깅, 실제 주문 발송하지 않음)
        self.shadow_mode = self.config_snapshot.get('SHADOW_MODE', False)

        # ── 6. 정합성 검증용 로깅 설정 (심볼별 파일 분리 — 멀티 AI 전략 동시 구동 시 Parity 로그 오염 방지) ──
        self.log_dir = os.path.join(_CTRADE_SYSTEM_ROOT, "CTrade", "data", "logs")
        os.makedirs(self.log_dir, exist_ok=True)
        _safe_symbol = self.symbol.replace("/", "_").replace("\\", "_")
        self.feature_dump_path = os.path.join(self.log_dir, f"live_feature_dump_{_safe_symbol}.csv")
        self.raw_candles_path = os.path.join(self.log_dir, f"live_raw_candles_{_safe_symbol}.csv")
        self.trades_dump_path = os.path.join(self.log_dir, f"live_trades_{_safe_symbol}.csv")
        self._init_dump_files()

        logging.info(
            f"[AIStrategy] 모델 로드 완료: {self.symbol} | "
            f"interval={self.interval} | features={len(self.features_order)}개 | "
            f"threshold={self.params['buy_threshold']:.4f} | "
            f"TP={self.params['take_profit']}×ATR / SL={self.params['stop_loss']}×ATR | "
            f"session={self.params.get('opt_session_id', 'unknown')}"
        )

    def _live_feature_config(self) -> dict:
        """실전 지표 계산 설정 — 스펙에 기록된 값만 쓴다 (scale_v2 는 정책·확정 파라미터까지 그대로 전달)."""
        fc = {'enabled_features': self.features_order, 'feature_params': self.feature_params}
        spec = getattr(self, 'feature_spec', None) or {}
        if spec.get('param_policy'):
            fc['param_policy'] = spec['param_policy']
            fc['params_resolved'] = bool(spec.get('params_resolved'))
            fc['required_history_bars'] = spec.get('required_history_bars')
        return fc

    def _calc_required_warmup_candles(self) -> int:
        """모델 피처들이 요구하는 최대 롤링 윈도우(봉 수)를 추출하여 웜업 안전선 산출

        shared.feature_registry 의 표준 키(window_mins/window_bars/period_bars 및 레거시
        window/candles/period/period_candles)와 동일한 해석 규칙을 사용한다.
        (window>=60 은 분 단위, 그 미만은 봉 수). 상한 캡은 없으며 호출부가 버퍼를 확장한다.
        """
        # scale_v2 모델: 학습 때 확정·기록한 필요 이력을 그대로 쓴다 (세션을 넘는 연속 계산)
        if getattr(self, 'feature_spec', None) and self.feature_spec.get('param_policy') == 'scale_v2':
            req = int(self.feature_spec.get('required_history_bars') or 0)
            if req > 0:
                return max(req, 30)
        max_win = 50
        feat_params = self.feature_params or {}
        interval_min = max(1.0 / 60.0, float(self.interval_minutes))
        for fid, pdict in feat_params.items():
            if not isinstance(pdict, dict):
                continue
            cands = []
            try:
                if 'window_mins' in pdict:
                    cands.append(round(float(pdict['window_mins']) / interval_min))
                if 'window_bars' in pdict:
                    cands.append(int(pdict['window_bars']))
                if 'candles' in pdict:
                    cands.append(int(pdict['candles']))
                if 'window' in pdict:
                    w = float(pdict['window'])
                    cands.append(round(w / interval_min) if w >= 60 else int(w))
                for k in ('period_bars', 'period', 'period_candles'):
                    if k in pdict:
                        cands.append(int(pdict[k]))
                if 'period_days' in pdict:
                    # 일봉 기준이면 현재 봉길이에 맞춰 변환 (예: 10분봉 = 하루 144개)
                    cands.append(int(round(int(pdict['period_days']) * 1440.0 / interval_min)))
                for k in ('fast_period', 'slow_period'):
                    if k in pdict:
                        cands.append(int(pdict[k]))
            except Exception:
                pass
            for v in cands:
                max_win = max(max_win, int(v))
        # 모델의 최대 윈도우 + 여유 50개 확보 (최소 100개)
        return max(max_win + 50, 100)

    @property
    def is_warmed_up(self) -> bool:
        """지표가 충분히 수렴하여 실전 매매가 가능한지 여부"""
        return len(self.aggregator.candles) >= self.required_warmup_candles

    @property
    def warmup_status(self) -> dict:
        """현재 웜업 상태 딕셔너리 (UI 및 콘솔 출력용)"""
        curr = len(self.aggregator.candles)
        req = self.required_warmup_candles
        pct = round(min(100.0, (curr / req * 100.0)), 1) if req > 0 else 100.0
        ready = curr >= req
        status_text = f"🟢 [READY] {curr}/{req} ({pct}%) - 매매 활성화" if ready else f"⏳ [WARM-UP] {curr}/{req} ({pct}%) - 지표 수렴 중(매매 대기)"
        return {
            "symbol": self.symbol,
            "ready": ready,
            "current_candles": curr,
            "required_candles": req,
            "progress_pct": pct,
            "status_text": status_text
        }

    def on_ref_tick(self, symbol: str, price: float, volume: float,
                    timestamp: Optional[datetime] = None, is_buyer: bool = True):
        """참조 대장주 실시간 체결 틱 수신"""
        sym_clean = str(symbol).strip().upper()
        alias = self.aggregator.ref_symbol_map.get(sym_clean)
        if alias:
            self.aggregator.on_ref_tick(alias, price, volume, is_buyer=is_buyer, timestamp=timestamp)

    def _verify_parity_guard(self):
        """[Parity Guard] 배포 모델의 피처와 실전 데이터 파이프라인 무결성 검증

        실전 파이프라인에서 수집 불가능하거나 비활성화된 지표가 포함된 경우
        침묵하는 실패(Silent Failure)를 원천 차단하고 즉시 예외를 발생시킵니다.
        """
        UNSUPPORTED_LIVE_FEATURES = {
            'binance_futures_spot_vol_ratio',
            'binance_liq_cascade_ratio',
            'eth_btc_relative_momentum',
            'upbit_large_trade_ratio',
        }

        invalid_features = [f for f in self.features_order if f in UNSUPPORTED_LIVE_FEATURES]
        if invalid_features:
            err_msg = (
                f"🛑 [Parity Guard] 모델 '{self.deploy_path}'에 실전 미지원 피처가 감지되었습니다: {invalid_features}\n"
                f"이 모델은 실전에서 Train-Serve Skew(결측치 0.0 왜곡)를 일으키므로 로드가 차단됩니다."
            )
            logging.error(err_msg)
            raise RuntimeError(err_msg)

        # ── 모델 ↔ feature_spec 정합성 (개수/이름/순서) ──
        #   LightGBM은 DataFrame 컬럼 이름으로 재정렬하지 않으므로 순서가 어긋나면 조용히 엉뚱한 확률이 나온다.
        #   개수가 다르면 매 봉 predict 예외로 신호가 영구히 사라진다. 로드 시점에 즉시 차단한다.
        self._assert_model_matches_spec(self.model, "primary")
        if self.meta_model is not None:
            self._assert_model_matches_spec(self.meta_model, "meta")

        spec_interval = self.feature_spec.get('interval') if isinstance(self.feature_spec, dict) else None
        if spec_interval and str(spec_interval) != str(self.interval):
            err_msg = (
                f"🛑 [Parity Guard] interval 불일치: 파일명 기준 '{self.interval}' ≠ feature_spec '{spec_interval}' "
                f"(dir={self.deploy_path})"
            )
            logging.error(err_msg)
            raise RuntimeError(err_msg)

    def _assert_model_matches_spec(self, booster, label: str):
        n_model = booster.num_feature()
        n_spec = len(self.features_order)
        if n_model != n_spec:
            err_msg = (
                f"🛑 [Parity Guard] {label} 모델 피처 수({n_model}) ≠ feature_spec.features_order({n_spec}). "
                f"모델과 스펙이 서로 다른 배포본에서 섞였습니다 (dir={self.deploy_path})"
            )
            logging.error(err_msg)
            raise RuntimeError(err_msg)

        names = list(booster.feature_name())
        # 이름 없이 학습된 모델(Column_0, Column_1, ...)은 개수 검증만 수행
        if all(n == f"Column_{i}" for i, n in enumerate(names)):
            return
        if names != list(self.features_order):
            diff = [(i, m, sp) for i, (m, sp) in enumerate(zip(names, self.features_order)) if m != sp][:3]
            err_msg = (
                f"🛑 [Parity Guard] {label} 모델 feature_name 순서/이름이 feature_spec.features_order와 다릅니다 "
                f"(예: index,model,spec={diff}, dir={self.deploy_path})"
            )
            logging.error(err_msg)
            raise RuntimeError(err_msg)

    def _resolve_model_file(self, model_dir: str) -> str:
        """CTrade 소비용 모델 파일을 결정론적으로 선택한다 (H-2).

        우선순위:
          1) model_{interval}.txt  (정식 규격, CTrade 단일 소비 파일)
          2) _primary/_meta 접미사를 제외한 model_*.txt 중 사전순 첫 파일 (안전 fallback)
        _primary/_meta 는 Baker 내부 검증/메타 라벨링 전용이므로 주 모델로 사용하지 않는다.
        """
        candidates = [f for f in os.listdir(model_dir) if f.startswith("model_") and f.endswith(".txt")]
        if not candidates:
            raise FileNotFoundError(f"model_*.txt not found in {model_dir}")

        preferred = f"model_{self.interval}.txt"
        if preferred in candidates:
            return os.path.join(model_dir, preferred)

        plain = sorted(
            f for f in candidates
            if not f.endswith("_primary.txt") and not f.endswith("_meta.txt")
        )
        if not plain:
            raise FileNotFoundError(
                f"CTrade 소비용 'model_{self.interval}.txt' 를 찾을 수 없습니다. "
                f"_primary/_meta 만 존재: {candidates} (dir={model_dir})"
            )
        return os.path.join(model_dir, plain[0])

    def _detect_num_class(self) -> int:
        """모델 출력 차원 감지 (binary/regression=1, multiclass=N).

        LightGBM Booster 는 multiclass 일 때 predict()가 2D(n, num_class)를 반환한다.
        feature_spec 로드 이전에도 안전하도록 Booster.num_feature()를 사용한다.
        """
        try:
            n_feat = int(self.model.num_feature())
            probe = np.asarray(self.model.predict(np.zeros((1, n_feat), dtype=float)))
            return int(probe.shape[1]) if probe.ndim > 1 else 1
        except Exception as e:
            logging.warning(f"[AIStrategy] num_class 감지 실패, binary(1)로 간주: {e}")
            return 1

    @staticmethod
    def _parse_interval_minutes(interval: str) -> float:
        """인터벌 문자열 파싱 (shared.market_utils 재사용)"""
        return parse_interval_minutes(interval)

    # ─────────────────────────────────────────────
    # 틱 데이터 수신 인터페이스
    # ─────────────────────────────────────────────

    def on_upbit_tick(self, price: float, volume: float, side: str,
                      timestamp: Optional[datetime] = None):
        """업비트 체결 틱 수신 — CandleAggregator에 전달"""
        self.aggregator.on_upbit_tick(price, volume, side, timestamp)

    def on_upbit_usdt_tick(self, price: float,
                           timestamp: Optional[datetime] = None):
        """업비트 KRW-USDT 테더 가격 수신 — CandleAggregator에 전달"""
        self.aggregator.on_upbit_usdt_tick(price, timestamp)

    def on_binance_tick(self, price: float, volume: float, is_buyer_maker: bool,
                        timestamp: Optional[datetime] = None):
        """바이낸스 현물 체결 틱 수신 — CandleAggregator에 전달"""
        self.aggregator.on_binance_tick(price, volume, is_buyer_maker, timestamp)

    def on_binance_futures_oi(self, open_interest: float, open_interest_value: float,
                              timestamp: Optional[datetime] = None):
        """바이낸스 선물 미결제 약정(OI) 수신 — CandleAggregator에 전달"""
        self.aggregator.on_binance_futures_oi(open_interest, open_interest_value, timestamp)

    def on_binance_funding_rate(self, funding_rate: float,
                                timestamp: Optional[datetime] = None):
        """바이낸스 선물 확정 펀딩비 수신 — CandleAggregator에 전달"""
        self.aggregator.on_binance_funding_rate(funding_rate, timestamp)

    def on_target_tick(self, price: float, volume: float, side: str = "BID",
                       timestamp: Optional[datetime] = None, is_buyer: Optional[bool] = None):
        """타겟 종목 (KIS 국내주식/ETF 또는 업비트) 실시간 체결 틱 수신"""
        self.aggregator.on_target_tick(price, volume, side, timestamp, is_buyer)

    def on_futures_tick(self, futures_symbol: str, price: float,
                        timestamp: Optional[datetime] = None):
        """국내/해외 선물 실시간 가격 수신"""
        self.aggregator.on_futures_tick(futures_symbol, price, timestamp)

    def on_index_tick(self, index_symbol: str, price: float,
                      timestamp: Optional[datetime] = None):
        """현물 지수(KOSPI 200 등) 실시간 가격 수신"""
        self.aggregator.on_index_tick(index_symbol, price, timestamp)

    def add_binance_usdc_volume(self, volume: float, timestamp: Optional[datetime] = None):
        """바이낸스 USDC 마켓 거래량 수신 — CandleAggregator에 전달"""
        self.aggregator.add_binance_usdc_volume(volume, timestamp)

    def on_coinbase_price(self, price: float, timestamp: Optional[datetime] = None):
        """코인베이스 BTC-USD 실시간 체결가 수신 — CandleAggregator에 전달"""
        self.aggregator.on_coinbase_price(price, timestamp)


    # ─────────────────────────────────────────────
    # 전략 평가 (매 틱 또는 주기적 호출)
    # ─────────────────────────────────────────────

    def evaluate(self, current_price: float,
                 current_timestamp: Optional[datetime] = None) -> Optional[Signal]:
        """현재 시점의 매매 신호를 평가합니다.

        매 틱마다 호출해도 되며, 내부적으로 캔들 완성 여부를 자동 판단합니다.
        보유 중 포지션의 청산 조건은 매 틱마다 체크합니다.

        Returns:
            Signal 객체 (BUY/SELL) 또는 None
        """
        if current_timestamp is None:
            current_timestamp = datetime.now(KST)
        elif current_timestamp.tzinfo is None:
            current_timestamp = current_timestamp.replace(tzinfo=KST)

        # ── 보유 중: 매 틱마다 Hard Emergency SL 및 당일청산 체크 ──
        if self.has_position and not self.is_pending_exit:
            elapsed_candles = self.current_candle_idx - self.entry_candle_idx
            exit_result = self.position_manager.check_exit(
                entry_price=self.entry_price,
                entry_atr=self.entry_atr,
                current_price=current_price,
                elapsed_candles=elapsed_candles,
                current_time=current_timestamp,
                is_candle_close=False
            )
            if exit_result:
                self.is_pending_exit = True
                sig = Signal(
                    action=SignalAction.SELL,
                    price=current_price,
                    probability=self.last_probability,
                    reason=SignalReason.from_exit_type(exit_result['exit_type']),
                    net_return=exit_result['net_return'],
                    hold_mins=exit_result['hold_mins']
                )
                self._log_trade_dump(current_timestamp, sig)
                return sig

        # ── 캔들 완성 확인 → 피처 계산 + AI 추론 및 Soft TP/SL 판정 ──
        df = self.aggregator.check_candle_complete(current_timestamp)
        if df is not None:
            self.current_candle_idx = self.aggregator.total_completed_candles
            self._log_raw_candle(df.iloc[-1])
            return self._on_candle_complete(df, current_price, current_timestamp)

        return None

    def _on_candle_complete(self, candle_df: pd.DataFrame,
                            current_price: float,
                            current_timestamp: datetime) -> Optional[Signal]:
        """캔들 완성 시: [Step 0] 종가 기준 Soft TP/SL/시간만료 판정 → 피처 계산 → LightGBM 추론 → 매수 신호 판단"""

        # [Step 0] 보유 중인 경우: 캔들 종가 기준 Soft TP / Soft SL / 시간만료 / 당일청산 판정
        if self.has_position and not self.is_pending_exit:
            # 캔들 종가 추출 (백테스트와 100% 완전 패리티)
            close_col = 'target_close' if 'target_close' in candle_df.columns else (
                'upbit_close' if 'upbit_close' in candle_df.columns else 'close'
            )
            candle_close = float(candle_df[close_col].iloc[-1]) if close_col in candle_df.columns else current_price

            elapsed_candles = self.current_candle_idx - self.entry_candle_idx
            exit_result = self.position_manager.check_exit(
                entry_price=self.entry_price,
                entry_atr=self.entry_atr,
                current_price=candle_close,
                elapsed_candles=elapsed_candles,
                current_time=current_timestamp,
                is_candle_close=True
            )
            if exit_result:
                self.is_pending_exit = True
                sig = Signal(
                    action=SignalAction.SELL,
                    price=candle_close,
                    probability=self.last_probability,
                    reason=SignalReason.from_exit_type(exit_result['exit_type']),
                    net_return=exit_result['net_return'],
                    hold_mins=exit_result['hold_mins']
                )
                self._log_trade_dump(current_timestamp, sig)
                return sig


        if not self.is_warmed_up:
            st = self.warmup_status
            logging.info(f"[{self.symbol}] ⏳ 지표 웜업 진행 중: {st['current_candles']}/{st['required_candles']} 캔들 ({st['progress_pct']}%) - 안전을 위해 매수 대기")
            return None

        # [Step 1] 피처 엔지니어링 (Baker와 100% 동일 코드)
        try:
            feature_config = self._live_feature_config()
            config = self.config_snapshot.copy() if hasattr(self, 'config_snapshot') and self.config_snapshot else {}
            config['SYMBOL_UPBIT'] = self.symbol
            df = run_feature_engineering(candle_df, interval=self.interval, feature_config=feature_config, config=config)
        except Exception as e:
            logging.error(f"[AIStrategy] 피처 엔지니어링 오류: {e}", exc_info=True)
            return None

        # [Step 1.5] 피처 게이트 — 실시간 수신 불량 NaN 차단
        # 필수 피처 중 하나라도 NaN이면 절름발이 추론을 방지하기 위해 해당 봉 완전 Skip.
        # 실전-시뮬레이션 불일치의 주요 원인: 백테스트는 100% 완전 데이터, 실전은 NaN 가능.
        try:
            last_row = df[self.features_order].iloc[-1]
            missing_features = [f for f in self.features_order if pd.isna(last_row[f])]
            if missing_features:
                logging.warning(
                    f"[AIStrategy] ⛔ Feature Gate: {len(missing_features)}개 피처 수신 불량 "
                    f"({missing_features[:3]}{'...' if len(missing_features) > 3 else ''}) "
                    f"— 이번 봉 추론 Skip (Train-Serve Skew 방지)"
                )
                return None
        except Exception as e:
            logging.error(f"[AIStrategy] 피처 게이트 검사 오류: {e}", exc_info=True)
            return None

        # [Step 2] LightGBM 추론 (H-2: binary=1D / multiclass=2D 대응 — 상승=class 1)
        try:
            latest = df[self.features_order].iloc[-1:]
            _raw = np.asarray(self.model.predict(latest))
            if _raw.ndim > 1:
                primary_is_buy = (int(np.argmax(_raw[0])) == 1)
                probability = float(_raw[0, 1])
            else:
                probability = float(_raw[0])
                primary_is_buy = True
        except Exception as e:
            logging.error(f"[AIStrategy] LightGBM 추론 오류: {e}", exc_info=True)
            return None

        # [Step 2-1] 메타(2차) 모델 승인 필터 (H-3: 메타 모델 존재 시에만 적용, primary==1 게이트)
        is_meta_allowed = True
        if self.meta_model is not None and primary_is_buy:
            try:
                _mraw = np.asarray(self.meta_model.predict(latest))
                meta_prob = float(_mraw[0, 1]) if _mraw.ndim > 1 else float(_mraw[0])
                is_meta_allowed = meta_prob >= META_APPROVAL_THRESHOLD
                if not is_meta_allowed:
                    logging.info(
                        f"[{self.symbol}] ⛔ 메타 필터 미승인 "
                        f"(meta={meta_prob:.4f} < {META_APPROVAL_THRESHOLD}) → 진입 보류"
                    )
            except Exception as e:
                logging.error(f"[AIStrategy] 메타 모델 추론 오류(진입 보류, fail-closed): {e}")
                is_meta_allowed = False

        self.last_probability = probability
        self.last_signal_time = current_timestamp

        # 실시간 피처 및 예측 결과 덤프 로깅
        self._log_feature_dump(df.iloc[-1], probability)

        # [Step 3] 매크로 필터 (RSI 일봉 기준) - 테스트 모드(Lv.1~3) 시 완화/해제
        is_macro_allowed = True
        if self.test_level == 0:
            rsi_daily_col = 'target_rsi_daily' if 'target_rsi_daily' in df.columns else ('upbit_rsi_daily' if 'upbit_rsi_daily' in df.columns else None)
            if self.use_macro_filter and rsi_daily_col:
                current_rsi = df[rsi_daily_col].iloc[-1]
                if current_rsi < self.macro_rsi_threshold:
                    is_macro_allowed = False

        # [Step 3-1] KRX 장중 진입 시간 제약 (09:10 이전 웜업 및 15:00 이후 진입 금지)
        # 테스트 모드(Lv.2, Lv.3)에서는 시뮬레이션 환경에 한해 야간/장마감 시간 제약 우회 허용
        if self.market_type == "KRX" and self.intraday_square_off:
            if not self.bypass_krx_time_limit and not is_krx_entry_allowed(current_timestamp):
                return None

        # [Step 4] 매수 진입 판단 (이미 보유 중이거나 매수 주문 대기 중이면 진입 차단)
        if (not self.has_position
                and not self.is_pending_entry
                and self.current_candle_idx >= self._cooldown_until_candle
                and primary_is_buy
                and probability >= self.params['buy_threshold']
                and is_macro_allowed
                and is_meta_allowed):

            # 리스크 관리자 검사 (Lv.3 TURBO 모드가 아닐 때만 적용)
            if self.test_level < 3:
                risk_allowed, risk_reason = self.risk_manager.can_open_position()
                if not risk_allowed:
                    logging.warning(f"[AIStrategy] ⚠️ 매수 차단: {risk_reason}")
                    return None

            # Shadow Mode: 신호만 로깅, 실제 진입 안 함
            if self.shadow_mode:
                logging.info(
                    f"[AIStrategy] 👻 SHADOW BUY: {self.symbol} @ {current_price:,.0f} | "
                    f"prob={probability:.4f} (실제 진입 안 함)"
                )
                sig = Signal(action=SignalAction.NONE, price=current_price, probability=probability, reason=SignalReason.SHADOW)
                self._log_trade_dump(current_timestamp, sig)
                return sig

            try:
                signal = self._evaluate_entry(df, probability, current_price, current_timestamp)
            except Exception as e:
                logging.error(f"[AIStrategy] 매수 진입 판단 오류: {e}", exc_info=True)
                return None

            if signal:
                return signal

        return None

    def _evaluate_entry(self, df: pd.DataFrame, probability: float,
                        current_price: float,
                        current_timestamp: datetime) -> Optional[Signal]:
        """매수 진입 조건 추가 검증 및 매수 신호 생성 (실제 포지션 오픈은 체결 콜백 on_buy_filled에서 수행)"""
        # 진입 시점의 ATR 확보
        atr_col = 'target_atr' if 'target_atr' in df.columns else ('upbit_atr' if 'upbit_atr' in df.columns else None)
        entry_atr = df[atr_col].iloc[-1] if (atr_col and not pd.isna(df[atr_col].iloc[-1]) and df[atr_col].iloc[-1] > 0) else self._estimate_restored_atr(current_price, current_timestamp)
        self.last_signal_atr = entry_atr

        # 최소 수익성 검증: TP 기대수익폭이 왕복 수수료(fee_rate * 2 + 매도세) 미만이면 매수 스킵 (Lv.3 TURBO 시 스킵 면제)
        if self.test_level < 3:
            expected_tp_pct = (entry_atr * self.params['take_profit']) / current_price
            round_trip_cost_pct = round_trip_cost(self.fee_rate, 0.0, self.sell_tax)
            if expected_tp_pct < round_trip_cost_pct:
                logging.warning(
                    f"[AIStrategy] ⚠️ 최소 수익성 미달로 매수 스킵: {self.symbol} | "
                    f"기대수익({expected_tp_pct*100:.3f}%) < 왕복비용({round_trip_cost_pct*100:.3f}%)"
                )
                return None

        # 상태를 체결 대기(pending)로 전환 (체결 완료 전까지는 has_position=False 유지하여 조기 가상 청산 방지)
        self.is_pending_entry = True
        # [예약 카운터] pending 진입 즉시 슬롯 점유 → 다른 심볼이 같은 캔들에서 초과 진입하는 것을 차단
        if not self._position_reserved:
            self.risk_manager.on_position_opened()
            self._position_reserved = True

        tp_price = current_price + (entry_atr * self.params['take_profit'])
        sl_price = current_price - (entry_atr * self.params['stop_loss'])
        logging.info(
            f"[AIStrategy] 🎯 BUY SIGNAL: {self.symbol} @ {current_price:,.0f} | "
            f"prob={probability:.4f} | TP={tp_price:,.0f} / SL={sl_price:,.0f}"
        )

        features_snapshot = {
            feat: float(df[feat].iloc[-1])
            for feat in getattr(self, 'features_order', [])
            if feat in df.columns and pd.notna(df[feat].iloc[-1])
        }

        sig = Signal(
            action=SignalAction.BUY,
            price=current_price,
            probability=probability,
            reason=SignalReason.AI_SIGNAL,
            entry_atr=entry_atr,
            tp_price=tp_price,
            sl_price=sl_price,
            features_snapshot=features_snapshot
        )
        self._log_trade_dump(current_timestamp, sig)
        return sig

    # ─────────────────────────────────────────────
    # 포지션 상태 관리
    # ─────────────────────────────────────────────

    def _estimate_restored_atr(self, entry_price: float, entry_time: Optional[datetime] = None) -> float:
        """캔들 히스토리 기반 진입 시점 ATR 역추적 및 동적 Fallback 산출"""
        try:
            candle_df = self.aggregator.to_dataframe()
            if not candle_df.empty and len(candle_df) >= 5:
                feature_config = self._live_feature_config()
                config = self.config_snapshot.copy() if hasattr(self, 'config_snapshot') and self.config_snapshot else {}
                config['SYMBOL_UPBIT'] = self.symbol
                df = run_feature_engineering(candle_df, interval=self.interval, feature_config=feature_config, config=config)

                atr_col = 'target_atr' if 'target_atr' in df.columns else ('upbit_atr' if 'upbit_atr' in df.columns else None)
                if atr_col and not df[atr_col].dropna().empty:
                    # 1. entry_time이 주어진 경우 가장 가까운 캔들의 ATR 매칭
                    if entry_time is not None:
                        if entry_time.tzinfo is None:
                            entry_time = entry_time.replace(tzinfo=KST)
                        time_diffs = (df['timestamp'] - entry_time).abs()
                        best_idx = time_diffs.idxmin()
                        matched_atr = df.loc[best_idx, atr_col]
                        if not pd.isna(matched_atr) and matched_atr > 0:
                            logging.info(f"[AIStrategy] 🎯 진입 시점({entry_time.strftime('%H:%M:%S')}) 캔들 ATR 복원 완료: {matched_atr:,.1f} (종목: {self.symbol})")
                            return float(matched_atr)

                    # 2. entry_time 매칭 실패 또는 미지정 시 최근 10개 캔들 평균 ATR 적용
                    recent_atrs = df[atr_col].dropna().tail(10)
                    if not recent_atrs.empty:
                        avg_atr = float(recent_atrs.mean())
                        if avg_atr > 0:
                            logging.info(f"[AIStrategy] 📊 최근 캔들 평균 ATR 기반 복원: {avg_atr:,.1f} (종목: {self.symbol})")
                            return avg_atr
        except Exception as e:
            logging.warning(f"[AIStrategy] 캔들 히스토리 기반 ATR 복원 실패: {e}")

        # 3. 캔들 히스토리 미비 시 비상 Fallback (0.15% 단타 스케일)
        fallback_atr = entry_price * 0.0015
        logging.warning(f"[AIStrategy] ⚠️ 비상 Fallback ATR 적용: {fallback_atr:,.1f} (0.15% 스케일)")
        return fallback_atr

    def on_buy_filled(self, real_entry_price: float, entry_atr: float = 0.0,
                      timestamp: Optional[datetime] = None, candle_idx: Optional[int] = None):
        """실제 주문 체결 완료 시 호출되어 포지션을 오픈하고 리스크 관리자에 반영합니다."""
        self.is_pending_entry = False
        if entry_atr > 0:
            atr = entry_atr
        elif self.last_signal_atr > 0:
            atr = self.last_signal_atr
        else:
            atr = self._estimate_restored_atr(real_entry_price, timestamp)
        t = timestamp or datetime.now(KST)
        c_idx = candle_idx if candle_idx is not None else self.current_candle_idx
        
        self._open_position(real_entry_price, atr, t, candle_idx=c_idx)
        # _evaluate_entry에서 pending 시점에 이미 예약된 경우 중복 호출 방지
        if not self._position_reserved:
            self.risk_manager.on_position_opened()
        self._position_reserved = False  # 체결 완료 → 예약 플래그 해제
        logging.info(f"[AIStrategy] ✅ Position Opened: {self.symbol} @ {real_entry_price:,.0f} | ATR: {atr:,.1f}")

    def on_buy_cancelled(self, reason: str = "CANCELLED"):
        """매수 주문이 미체결 취소되거나 슬리피지 초과/에러로 중단되었을 때 상태를 롤백합니다."""
        if self.is_pending_entry:
            self.is_pending_entry = False
            # pending 단계에서 예약한 슬롯을 취소 시 롤백
            if self._position_reserved:
                self._release_reserved_slot()
                self._position_reserved = False
            logging.info(f"[AIStrategy] ⚠️ Pending Buy Cancelled for {self.symbol} (Reason: {reason})")

    def _release_reserved_slot(self):
        """예약(pending)한 동시 포지션 슬롯 반환. RiskManager 에 공개 취소 API 가 있으면 사용."""
        rm = self.risk_manager
        for name in ('on_position_cancelled', 'release_position_slot'):
            fn = getattr(rm, name, None)
            if callable(fn):
                fn()
                return
        # 공개 API 부재 시 폴백 (shared/risk_manager.py 에 공개 메서드 추가 전까지)
        rm._current_positions = max(0, rm._current_positions - 1)

    def _open_position(self, price: float, atr: float, timestamp: datetime, candle_idx: Optional[int] = None):
        self.has_position = True
        self.is_pending_entry = False
        self.entry_price = price
        self.entry_atr = atr
        self.entry_time = timestamp
        self.entry_candle_idx = candle_idx if candle_idx is not None else self.current_candle_idx
        if hasattr(self, 'exec_session'):
            self.exec_session.on_buy_filled(price, atr, self.entry_candle_idx, timestamp)
        elif hasattr(self, 'axiom_session'):
            self.axiom_session.on_buy_filled(price, atr, self.entry_candle_idx, timestamp)

    def update_entry_price(self, real_entry_price: float):
        """실제 평균 체결 단가로 entry_price 동기화 (하위 호환 지원)"""
        if self.has_position and real_entry_price > 0:
            logging.info(f"[AIStrategy] {self.symbol} entry_price updated from {self.entry_price:,.0f} to Real Avg Price: {real_entry_price:,.0f}")
            self.entry_price = real_entry_price
            if hasattr(self, 'exec_session'):
                self.exec_session.entry_price = real_entry_price
            elif hasattr(self, 'axiom_session'):
                self.axiom_session.entry_price = real_entry_price
        elif self.is_pending_entry and real_entry_price > 0:
            self.on_buy_filled(real_entry_price)

    def restore_position(self, entry_price: float, entry_atr: float = 0.0, entry_time: Optional[datetime] = None):
        """재기동 시 포지션 자동 복구 (정밀 ATR 역추적 및 시간차 기반 캔들 인덱스 보정)"""
        # 리스크 관리자 동시 포지션 카운트 반영 (on_sell_filled 의 on_position_closed 와 짝 맞춤)
        rm = getattr(self, 'risk_manager', None)
        if rm is not None and not getattr(self, 'has_position', False) and not getattr(self, '_position_reserved', False):
            rm.on_position_opened()
        self._position_reserved = False  # pending 예약이 있었다면 이미 카운트됨
        self.has_position = True
        self.is_pending_entry = False
        self.is_pending_exit = False
        self.entry_price = entry_price
        self.entry_time = entry_time or datetime.now(KST)
        if self.entry_time.tzinfo is None:
            self.entry_time = self.entry_time.replace(tzinfo=KST)

        # 1. ATR 복원 (Tier 1: 인자 전달 > Tier 2: 캔들 역추적 > Tier 3: 최근 캔들 평균 ATR)
        if entry_atr > 0:
            self.entry_atr = float(entry_atr)
        else:
            self.entry_atr = self._estimate_restored_atr(entry_price, self.entry_time)

        # 2. 경과 시간 및 캔들 인덱스 정밀 동기화
        self.current_candle_idx = self.aggregator.total_completed_candles
        interval_sec = max(1.0, float(self.interval_seconds if hasattr(self, 'interval_seconds') and self.interval_seconds else self.interval_minutes * 60.0))
        now_kst = datetime.now(KST)
        elapsed_sec = max(0.0, (now_kst - self.entry_time).total_seconds())
        elapsed_candles = int(elapsed_sec // interval_sec)

        self.entry_candle_idx = max(0, self.current_candle_idx - elapsed_candles)

        if hasattr(self, 'exec_session'):
            self.exec_session.on_buy_filled(self.entry_price, self.entry_atr, self.entry_candle_idx, self.entry_time)
        elif hasattr(self, 'axiom_session'):
            self.axiom_session.on_buy_filled(self.entry_price, self.entry_atr, self.entry_candle_idx, self.entry_time)

        tp_price = self.entry_price + (self.entry_atr * self.params.get('take_profit', 2.0))
        sl_price = self.entry_price - (self.entry_atr * self.params.get('stop_loss', 2.0))
        logging.info(
            f"[AIStrategy] 🔄 Restored position for {self.symbol} @ {entry_price:,.0f} | "
            f"ATR: {self.entry_atr:,.1f} | TP: {tp_price:,.0f} / SL: {sl_price:,.0f} | "
            f"진입: {self.entry_time.strftime('%H:%M:%S')} (경과: {elapsed_candles}캔들, entry_idx: {self.entry_candle_idx})"
        )

    def on_sell_filled(self, net_return: float = 0.0, exit_type: str = "TBM_EXIT"):
        """실제 매도 주문 체결 완료 시 호출되어 포지션을 종료하고 쿨다운 및 리스크 관리자에 보고합니다."""
        self._close_position()
        self._cooldown_until_candle = self.current_candle_idx + self.position_manager.cooldown_candles
        self.risk_manager.on_position_closed(net_return, exit_type)
        logging.info(f"[AIStrategy] 🏁 Position Closed: {self.symbol} | Return: {net_return:+.2f}% | Reason: {exit_type}")

    def on_sell_cancelled(self, reason: str = "CANCELLED"):
        """매도 주문 실패/취소 시 pending 상태 복구 (다음 틱에서 재시도 가능)"""
        self.is_pending_exit = False
        logging.info(f"[AIStrategy] ⚠️ Pending Sell Cancelled/Failed for {self.symbol} (Reason: {reason})")

    def _close_position(self):
        self.has_position = False
        self.is_pending_entry = False
        self.is_pending_exit = False
        self._position_reserved = False
        self.entry_price = 0.0
        self.entry_atr = 0.0
        self.entry_time = None
        self.entry_candle_idx = 0
        if hasattr(self, 'exec_session'):
            self.exec_session.on_sell_completed(self.current_candle_idx)
        elif hasattr(self, 'axiom_session'):
            self.axiom_session.on_sell_completed(self.current_candle_idx)

    # ─────────────────────────────────────────────
    # 테스트 매매 모드 제어 (4단계 레벨 시스템: Lv.0 ~ Lv.3)
    # ─────────────────────────────────────────────

    def set_test_level(self, level: int):
        """4단계 테스트 매매 모드 레벨 전환 (비파괴적 원본 파라미터 보존형)
        - 0 (OFF): 원본 실전 배포 파라미터 복원
        - 1 (MILD): buy_threshold=0.5000, cooldown=1분
        - 2 (ACTIVE): buy_threshold=0.4700, cooldown=0분, 야간/장마감 시간 제약 우회
        - 3 (TURBO): buy_threshold=0.4000, cooldown=0분, 초고빈도 전면 우회
        """
        self.test_level = int(level)
        self.test_mode_active = (self.test_level > 0)

        if self.test_level == 0:
            self.params['buy_threshold'] = self.original_buy_threshold
            self.params['cooldown_minutes'] = self.original_cooldown_minutes
            # cooldown_candles는 '캔들 수' 단위 — 분(minutes)을 인터벌(분)로 나눠 변환
            restored_cooldown_candles = max(1, int(self.original_cooldown_minutes / self.interval_minutes)) if self.interval_minutes > 0 else 1
            self.position_manager.cooldown_candles = restored_cooldown_candles
            self.bypass_krx_time_limit = False
            logging.info(
                f"[AIStrategy] 🛡️ [{self.symbol}] 테스트 매매 비활성화 (Lv.0 OFF, 원본 복원): "
                f"buy_threshold={self.original_buy_threshold:.4f}, "
                f"cooldown={self.original_cooldown_minutes}분"
            )
        elif self.test_level == 1:
            self.params['buy_threshold'] = 0.5000
            self.params['cooldown_minutes'] = 1
            # cooldown_candles는 '캔들 수' 단위 → 1분을 인터벌(분)로 환산 (M-2)
            self.position_manager.cooldown_candles = (
                max(1, int(1 / self.interval_minutes)) if self.interval_minutes > 0 else 1
            )
            self.bypass_krx_time_limit = False
            logging.info(
                f"[AIStrategy] ⚡ [{self.symbol}] 테스트 매매 Lv.1 (MILD): "
                f"buy_threshold=0.5000 (원본 {self.original_buy_threshold:.4f}), "
                f"cooldown=1분 (원본 {self.original_cooldown_minutes}분)"
            )
        elif self.test_level == 2:
            self.params['buy_threshold'] = 0.4700
            self.params['cooldown_minutes'] = 0
            self.position_manager.cooldown_candles = 0
            self.bypass_krx_time_limit = True
            logging.info(
                f"[AIStrategy] 🔥 [{self.symbol}] 테스트 매매 Lv.2 (ACTIVE): "
                f"buy_threshold=0.4700, cooldown=0분 (즉시 재진입), 야간/장마감 시간 제약 우회"
            )
        elif self.test_level >= 3:
            self.params['buy_threshold'] = 0.4000
            self.params['cooldown_minutes'] = 0
            self.position_manager.cooldown_candles = 0
            self.bypass_krx_time_limit = True
            logging.info(
                f"[AIStrategy] 🚀 [{self.symbol}] 테스트 매매 Lv.3 (TURBO): "
                f"buy_threshold=0.4000, cooldown=0분, 초고빈도 체결 모드 활성화"
            )

    def set_test_mode(self, enabled: bool, test_threshold: float = 0.5050, test_cooldown_minutes: int = 2):
        """기존 하위 호환용 래퍼"""
        self.set_test_level(1 if enabled else 0)

    # ─────────────────────────────────────────────
    # 상태 조회 (UI/로깅용)
    # ─────────────────────────────────────────────

    def get_status(self) -> dict:
        """현재 전략 상태를 딕셔너리로 반환 (UI 연동용)"""
        status = {
            "symbol": self.symbol,
            "interval": self.interval,
            "session_id": self.params.get('opt_session_id', 'unknown'),
            "has_position": self.has_position,
            "last_probability": self.last_probability,
            "candle_count": self.aggregator.candle_count,
            "warmup_complete": self.aggregator.has_enough_data,
            "buy_threshold": self.params['buy_threshold'],
            "original_buy_threshold": self.original_buy_threshold,
            "cooldown_minutes": self.params.get('cooldown_minutes', self.original_cooldown_minutes),
            "original_cooldown_minutes": self.original_cooldown_minutes,
            "test_level": self.test_level,
            "test_mode_active": self.test_mode_active,
            "bypass_krx_time_limit": self.bypass_krx_time_limit,
        }
        if self.has_position:
            status.update({
                "entry_price": self.entry_price,
                "entry_atr": self.entry_atr,
                "tp_price": self.entry_price + (self.entry_atr * self.params['take_profit']),
                "sl_price": self.entry_price - (self.entry_atr * self.params['stop_loss']),
                "entry_time": str(self.entry_time) if self.entry_time else None,
                "elapsed_candles": self.current_candle_idx - self.entry_candle_idx,
            })
        status["shadow_mode"] = self.shadow_mode
        status["risk"] = self.risk_manager.get_status()
        return status

    def _get_raw_candle_columns(self) -> list:
        """기본 raw 컬럼에 동적으로 등록된 참조 대장주(ref_*) 컬럼들을 결합하여 반환"""
        cols = list(_RAW_CANDLE_COLUMNS)
        if hasattr(self, 'aggregator') and hasattr(self.aggregator, '_ref_builders'):
            for alias in self.aggregator._ref_builders.keys():
                for suffix in ('_close', '_volume', '_bid_vol'):
                    c_name = f"ref_{alias}{suffix}"
                    if c_name not in cols:
                        cols.append(c_name)
        return cols

    def _init_dump_files(self):
        """정합성 검증용 로그 파일들의 헤더 초기화 (파일이 없을 때만 헤더 생성, 기존 파일은 유지)"""
        self._logged_raw_timestamps = set()
        if self.enable_parity_logging and os.path.exists(self.raw_candles_path):
            try:
                with open(self.raw_candles_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        parts = line.strip().split(',')
                        if parts:
                            self._logged_raw_timestamps.add(parts[0])
            except Exception as e:
                logging.warning(f"[AIStrategy] 기존 raw_candles 타임스탬프 로드 에러: {e}")

        if not self.enable_parity_logging:
            return
        # 1. 피처 덤프 파일 초기화
        raw_columns = self._get_raw_candle_columns()
        feature_columns = self.features_order
        dump_headers = raw_columns + feature_columns + ['predict_prob']

        # 피처 덤프 헤더 작성 (파일이 없거나 헤더 규격이 바뀐 경우 백업 후 재작성)
        write_feat_header = not os.path.exists(self.feature_dump_path)
        if not write_feat_header:
            try:
                with open(self.feature_dump_path, 'r', encoding='utf-8') as f:
                    first_line = f.readline().strip()
                    if not first_line or len(first_line.split(',')) != len(dump_headers):
                        write_feat_header = True
            except Exception:
                write_feat_header = True

        if write_feat_header:
            try:
                if os.path.exists(self.feature_dump_path) and os.path.getsize(self.feature_dump_path) > 0:
                    bak = f"{self.feature_dump_path}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
                    os.rename(self.feature_dump_path, bak)
                with open(self.feature_dump_path, 'w', encoding='utf-8') as f:
                    f.write(','.join(dump_headers) + '\n')
            except Exception as e:
                logging.error(f"[AIStrategy] feature_dump 파일 초기화 실패: {e}")

        # 원본 캔들 헤더 작성 (파일이 없거나 헤더 규격이 바뀐 경우 백업 후 재작성)
        write_raw_header = not os.path.exists(self.raw_candles_path)
        if not write_raw_header:
            try:
                with open(self.raw_candles_path, 'r', encoding='utf-8') as f:
                    first_line = f.readline().strip()
                    if not first_line or len(first_line.split(',')) != len(raw_columns):
                        write_raw_header = True
            except Exception:
                write_raw_header = True

        if write_raw_header:
            try:
                if os.path.exists(self.raw_candles_path) and os.path.getsize(self.raw_candles_path) > 0:
                    bak = f"{self.raw_candles_path}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
                    os.rename(self.raw_candles_path, bak)
                with open(self.raw_candles_path, 'w', encoding='utf-8') as f:
                    f.write(','.join(raw_columns) + '\n')
            except Exception as e:
                logging.error(f"[AIStrategy] raw_candles 파일 초기화 실패: {e}")

        # 매매 로그 헤더 작성 (파일이 없을 때만)
        trade_headers = ['timestamp', 'action', 'price', 'probability', 'reason', 'net_return', 'hold_mins']
        if not os.path.exists(self.trades_dump_path):
            try:
                with open(self.trades_dump_path, 'w', encoding='utf-8') as f:
                    f.write(','.join(trade_headers) + '\n')
            except Exception as e:
                logging.error(f"[AIStrategy] trades_dump 파일 초기화 실패: {e}")

    def _log_feature_dump(self, last_row: pd.Series, probability: float):
        """완성된 캔들의 raw 데이터와 가공 피처, 예측값을 파일에 기록"""
        if not self.enable_parity_logging:
            return
        try:
            raw_columns = self._get_raw_candle_columns()
            row_data = []
            
            # raw 데이터 추출
            for col in raw_columns:
                val = last_row.get(col, None)
                if col == 'timestamp':
                    if isinstance(val, (datetime, pd.Timestamp)):
                        row_data.append(val.strftime('%Y-%m-%d %H:%M:%S'))
                    else:
                        row_data.append(str(val))
                else:
                    row_data.append(f"{val:.8f}" if val is not None else "")

            # 피처 데이터 추출
            for col in self.features_order:
                val = last_row.get(col, None)
                row_data.append(f"{val:.8f}" if val is not None else "")

            # 예측값 추가
            row_data.append(f"{probability:.8f}")

            # feature dump 기록 (매 완성된 시점 1행)
            with open(self.feature_dump_path, 'a', encoding='utf-8') as f:
                f.write(','.join(row_data) + '\n')
        except Exception as e:
            logging.error(f"[AIStrategy] 피처 덤프 기록 오류: {e}")

    def _log_raw_candle(self, last_row: pd.Series):
        """마감된 원본 캔들 한 행을 live_raw_candles.csv에 누적 기록 (워밍업 기간 포함)"""
        if not self.enable_parity_logging:
            return
        try:
            ts_val = last_row.get('timestamp', None)
            ts_str = ""
            if ts_val is not None:
                if isinstance(ts_val, (datetime, pd.Timestamp)):
                    ts_str = ts_val.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    ts_str = str(ts_val).split('.')[0] # 문자열 정규화

            if ts_str and ts_str in self._logged_raw_timestamps:
                # 이미 기록된 타임스탬프인 경우 중복 기록 방지
                return

            raw_columns = self._get_raw_candle_columns()
            raw_row_data = []
            for col in raw_columns:
                val = last_row.get(col, None)
                if col == 'timestamp':
                    raw_row_data.append(ts_str if ts_str else str(val))
                else:
                    raw_row_data.append(f"{val:.8f}" if val is not None else "")

            with open(self.raw_candles_path, 'a', encoding='utf-8') as f:
                f.write(','.join(raw_row_data) + '\n')

            if ts_str:
                self._logged_raw_timestamps.add(ts_str)
        except Exception as e:
            logging.error(f"[AIStrategy] 원본 캔들 기록 오류: {e}")


    def _log_trade_dump(self, ts: datetime, signal: Signal):
        """매매 신호 발생 내역을 파일에 기록"""
        if not self.enable_parity_logging:
            return
        try:
            ts_str = ts.strftime('%Y-%m-%d %H:%M:%S') if isinstance(ts, (datetime, pd.Timestamp)) else str(ts)
            row_data = [
                ts_str,
                signal.action.value,
                f"{signal.price:.8f}",
                f"{signal.probability:.8f}",
                signal.reason.value,
                f"{signal.net_return:.8f}",
                str(signal.hold_mins)
            ]
            with open(self.trades_dump_path, 'a', encoding='utf-8') as f:
                f.write(','.join(row_data) + '\n')
        except Exception as e:
            logging.error(f"[AIStrategy] 매매 로그 기록 오류: {e}")
