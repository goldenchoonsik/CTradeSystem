"""
CTrade - Candle Aggregator (실시간 틱 → 분봉 변환기)

업비트/바이낸스 웹소켓에서 수신하는 실시간 틱 데이터를 지정된 인터벌(예: 30m)의
캔들(OHLCV)로 집계하여, Baker의 피처 엔지니어링과 동일한 DataFrame 형태를 생성합니다.

생성되는 DataFrame 컬럼 (Baker의 data_snapshot_{interval}.csv와 동일):
  - timestamp, upbit_open, upbit_close, upbit_high, upbit_low, upbit_volume, upbit_trade_bid_ratio, upbit_trade_count, upbit_large_trade_count, upbit_avg_trade_size
  - binance_spot_close, binance_spot_volume, binance_spot_taker_ratio, binance_spot_trade_count
  - binance_futures_oi, binance_futures_oi_value, binance_usdc_volume, binance_funding_rate
"""
import math
import time
import json
import logging
from datetime import datetime, timezone, timedelta, time as dtime
from collections import deque
from typing import Dict, Optional
import pandas as pd

from shared.config.config_manager import get_asset_id, get_symbol_info
from shared.data_library.reference_staleness import resolve_stale_cap
from shared.config.market_policies import BENCHMARK_ASSET_ID
from shared.core.market_utils import krx_session
from shared.core.candle_core import (
    get_candle_boundary,
    compute_bid_ratio,
    compute_avg_trade_size,
    sanitize_candle_ohlcv,
    build_standard_candle_dict,
    LARGE_TRADE_THRESHOLD_KRW
)

KST = timezone(timedelta(hours=9))

# KRX 봉 — 학습 데이터(Normalized)와 같은 모양으로 만든다 (2026-10-07)
#   · 세션: 날짜별로 krx_session_calendar.json 원장을 읽는다 (수능일 10:00~16:30 등). 아래 시각은 평일 기준 예시.
#   · 장 마감: 15:20 연속 체결 종료 → 15:30 종가 단일가(동시호가) 체결 한 번.
#     분봉 이상: Creon 1분봉은 동시호가를 15:30 확정 라벨 봉([15:29,15:30))으로 두고, 우리가 집계한 15분봉 등은
#     마지막 정규장 봉([15:15,15:30])에 합친다 → 둘 다 '마감 직전 구간'.
#     초봉: 틱으로 만든 학습 그리드(build_normalized_10s_stock)는 빈 봉을 채우고 15:30:00 동시호가 봉을 따로 둔다.
#   · 빈 봉: Creon 1분봉(과 그 집계)은 체결 없는 구간에 봉이 없다(동시호가 대기·VI·서킷브레이커·무거래 분).
#     분봉 이상은 빈 봉을 만들지 않는다. 초봉은 학습 그리드처럼 직전 가격으로 채운다.
#   · 라벨: timestamp_conventions.json 원장 규약대로 붙인다 (Creon 1분봉 = 봉 확정 시각, 초봉·집계봉 = 구간 시작).
# 종전에는 15:30 이후 틱을 봉에 넣지 않고 빌더도 비우지 않아, 동시호가가 그날 봉에서 빠지고
# 프로세스가 날을 넘기면 익일 09:00 봉의 시가·거래량에 섞였다. 라벨은 항상 구간 시작이라 1분봉 모델의
# 시각 피처가 학습보다 1분 이르게 계산됐다.
_KRX_AUCTION_GRACE = timedelta(seconds=60)   # 동시호가 체결 수신 대기 — 이후 그날 마지막 봉 확정


class _CandleBuilder:
    """단일 심볼·단일 소스(업비트 or 바이낸스)의 진행 중인 캔들을 관리"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.open = 0.0
        self.high = 0.0
        self.low = float('inf')
        self.close = 0.0
        self.volume = 0.0
        self.quote_volume = 0.0
        self.bid_volume = 0.0        # 업비트: BID 체결량, 바이낸스: Taker Buy 체결량
        self.bid_quote_volume = 0.0  # 바이낸스: Taker Buy 달러 대금
        self.total_volume = 0.0      # 비율 계산용 전체 체결량
        self.tick_count = 0
        self.trade_count = 0
        self.large_trade_count = 0

    def update(self, price: float, volume: float, is_buyer: bool):
        if self.tick_count == 0:
            self.open = price
            self.low = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume
        self.total_volume += volume
        quote_vol = price * volume
        self.quote_volume += quote_vol

        if is_buyer:
            self.bid_volume += volume
            self.bid_quote_volume += quote_vol
        
        self.trade_count += 1
        # 체결 대금(price * volume) >= 1억 원 기준 (shared.candle_core SSOT)
        if quote_vol >= LARGE_TRADE_THRESHOLD_KRW:
            self.large_trade_count += 1
            
        self.tick_count += 1

    def to_dict(self, prefix: str) -> dict:
        """prefix: 'target', 'upbit', 또는 'binance_spot'"""
        if self.tick_count == 0:
            return {}

        result = {
            f"{prefix}_close": self.close,
            f"{prefix}_volume": self.volume,
        }

        if prefix in ("upbit", "target"):
            bid_ratio = compute_bid_ratio(self.bid_volume, self.total_volume, fallback=0.5)
            avg_size = compute_avg_trade_size(self.volume, self.trade_count)
            std_dict = build_standard_candle_dict(
                open_p=self.open,
                high_p=self.high,
                low_p=self.low,
                close_p=self.close,
                volume=self.volume,
                bid_ratio=bid_ratio,
                trade_count=self.trade_count,
                large_trade_count=self.large_trade_count,
                avg_trade_size=avg_size,
                prefix=prefix,
                quote_volume=self.quote_volume
            )
            result.update(std_dict)
        elif prefix == "binance_spot":
            result["binance_spot_taker_ratio"] = (
                self.bid_volume / self.total_volume
                if self.total_volume > 0 else 0.5
            )
            result["binance_spot_trade_count"] = float(self.trade_count)
            result["binance_spot_quote_volume"] = self.quote_volume
            result["binance_spot_taker_quote_ratio"] = (
                self.bid_quote_volume / self.quote_volume
                if self.quote_volume > 0 else 0.5
            )

        return result



class _Observed:
    """외부 상태값 하나 — 마지막 관측값과 관측 시각.

    읽을 때 관측이 없거나 stale 상한을 넘었으면 NaN 이다. 상한은 학습 데이터(Gold) 결합과 같은 자산군별 정책
    (reference_staleness.resolve_stale_cap) — 학습과 실전이 같은 규칙으로 '모름'을 판정해야 한다 (train/serve parity).
    """
    __slots__ = ("value", "at", "cap", "positive")

    def __init__(self, cap, positive: bool = True):
        self.value = float("nan")
        self.at: Optional[datetime] = None
        self.cap = cap
        self.positive = positive

    def update(self, value, at: datetime):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(v) or (self.positive and v <= 0):
            return
        self.value, self.at = v, _as_kst(at)

    def alive(self, at: Optional[datetime]) -> bool:
        return self.at is not None and at is not None and (_as_kst(at) - self.at) <= self.cap

    def read(self, at: Optional[datetime]) -> float:
        return self.value if self.alive(at) else float("nan")


def _as_kst(ts: datetime) -> datetime:
    return ts.replace(tzinfo=KST) if ts.tzinfo is None else ts


# 실전 수집기가 내보내는 외부 상태값 컬럼 → (stale 상한을 정하는 원장 market_type, 양수만 유효한가)
# 수신 전·상한 초과는 NaN — 종전의 0.0/1380.0/타겟가/바이낸스가 대체는 학습 데이터에 없는 거짓 관측이었다 (2026-10-02)
_EXTERNAL_STATE_FIELDS = {
    "binance_spot_close": ("CRYPTO", True),
    "binance_futures_oi": ("CRYPTO", True),
    "binance_futures_oi_value": ("CRYPTO", True),
    "binance_funding_rate": ("CRYPTO", False),
    "upbit_usdt_close": ("CRYPTO", True),
    "coinbase_spot_close": ("CRYPTO", True),
    "benchmark_btc_close": ("CRYPTO", True),
    "binance_usdc_volume": ("CRYPTO", False),   # 흐름값 — 원천이 살아 있는지만 추적한다
    "kospi_futures_close": ("KRX_FUTURES", True),
    "spot_close": ("KRX_INDEX", True),
    "nq_futures_close": ("GLOBAL_FUTURES", True),
    "sp500_futures_close": ("GLOBAL_FUTURES", True),
    "nvda_futures_close": ("GLOBAL_FUTURES", True),
    "us10y_futures_close": ("GLOBAL_FUTURES", True),
    "wti_futures_close": ("GLOBAL_FUTURES", True),
    "gold_futures_close": ("GLOBAL_FUTURES", True),
    "usd_krw_fx": ("FX", True),
}


class CandleAggregator:
    """실시간 틱 스트림 → 고정 인터벌 캔들 DataFrame 변환기

    사용법:
        agg = CandleAggregator(interval_minutes=30, max_candles=2000)
        
        # 매 틱 수신 시:
        agg.on_target_tick(price, volume, side, timestamp)
        agg.on_futures_tick('NQ', price, timestamp)
        agg.on_binance_tick(price, volume, is_buyer_maker, timestamp)
        
        # 캔들 완성 확인 및 DataFrame 취득:
        df = agg.check_candle_complete(current_timestamp)
        if df is not None:
            features = run_feature_engineering(df, interval='30m', ...)
    """

    def __init__(self, interval_minutes: float = 1.0, max_candles: int = 2000,
                 interval_seconds: Optional[int] = None, market_type: str = "CRYPTO",
                 symbol: str = "KRW-BTC"):
        if interval_seconds is not None:
            self.interval_seconds = max(1, int(interval_seconds))
            self.interval_minutes = self.interval_seconds / 60.0
        else:
            self.interval_minutes = float(interval_minutes)
            self.interval_seconds = max(1, int(round(self.interval_minutes * 60)))
        self.max_candles = max_candles
        self.market_type = str(market_type).upper()
        self.symbol = str(symbol).upper()
        self._label_offset = self._resolve_label_offset()
        self.min_required_candles: int = 50  # 모델 요구 윈도우에 따라 동적 상향 설정 가능

        # 완성된 캔들 버퍼 (FIFO)
        self.candles: deque = deque(maxlen=max_candles)
        self.candle_seq: int = 0  # 시스템 기동 이후 완성된 총 캔들 수 (단조 증가 카운터)
        self._last_checked_seq: int = 0  # check_candle_complete로 인지/반환된 마지막 시퀀스 번호

        # 현재 진행 중인 미완성 캔들
        self._target_builder = _CandleBuilder()
        self._upbit_builder = self._target_builder  # 호환성 alias
        self._binance_builder = _CandleBuilder()

        # 외부 상태값 — 마지막 관측값 + 관측 시각 (봉 마감 때 stale 상한으로 읽는다)
        self._obs: Dict[str, _Observed] = {
            col: _Observed(resolve_stale_cap(mtype), positive=positive)
            for col, (mtype, positive) in _EXTERNAL_STATE_FIELDS.items()
        }
        self._current_binance_usdc_volume = 0.0

        # 현재 캔들의 시간 경계
        self._current_boundary_start: Optional[datetime] = None
        self._current_boundary_end: Optional[datetime] = None
        self._krx_closed_day = None   # 마지막 봉까지 확정한 KRX 세션 날짜 (이후 그날 틱은 봉에 넣지 않는다)

        # 타겟 마지막 가격 (빈 봉 forward-fill · Bad Tick 필터용)
        self._last_target_price = 0.0
        self._last_upbit_price = 0.0

        # 동적 참조 대장주(Reference Symbols) 실시간 틱 수집 빌더 및 마지막 가격 (alias -> _CandleBuilder)
        self._ref_builders: Dict[str, _CandleBuilder] = {}
        self._last_ref_prices: Dict[str, float] = {}       # Bad Tick 필터용 (실제 틱만)
        self._ref_obs: Dict[str, _Observed] = {}            # alias → 참조 종가 관측 (stale 상한 적용)
        self.ref_symbol_map: Dict[str, str] = {}  # {symbol: alias, 예: "000660": "hynix"}

    def _interval_str(self) -> str:
        s = self.interval_seconds
        return f"{s}s" if s < 60 else (f"{s // 60}m" if s % 60 == 0 else f"{s}s")

    def _resolve_label_offset(self) -> timedelta:
        """봉 라벨 = 구간 시작 + 이 값. 학습 데이터의 라벨 규약(원장)과 같게 붙여 시각 피처가 학습과 같은 값을 갖게 한다."""
        try:
            from shared.config.market_policies import bar_bucket_offset
            return pd.Timedelta(bar_bucket_offset(self.symbol, self._interval_str())).to_pytimedelta()
        except Exception as e:
            if self.market_type == "KRX":
                logging.error(f"[CandleAggregator] {self.symbol} {self._interval_str()} 라벨 규약을 원장에서 읽지 못해 "
                              f"구간 시작 라벨을 씁니다 — 학습 데이터와 라벨이 다를 수 있습니다: {e}")
            return timedelta(0)

    def register_ref_symbol(self, symbol: str, alias: str):
        """참조 대장주 심볼과 alias 매핑 등록 (예: '000660' -> 'hynix')"""
        sym_clean = str(symbol).strip().upper()
        alias_clean = str(alias).strip().lower()
        if sym_clean and alias_clean:
            self.ref_symbol_map[sym_clean] = alias_clean
            if alias_clean not in self._ref_builders:
                self._ref_builders[alias_clean] = _CandleBuilder()

    def _get_candle_boundary(self, ts: datetime) -> tuple:
        """주어진 시각이 속하는 캔들의 시작/끝 경계를 계산 (KST 기준, shared.candle_core SSOT)"""
        return get_candle_boundary(ts, self.interval_seconds)

    def _flush_current_candle(self):
        """현재 빌더의 데이터를 완성된 캔들로 저장"""
        if self._current_boundary_start is None:
            return

        candle = {"timestamp": self._current_boundary_start + self._label_offset}

        target_data = self._target_builder.to_dict("target")
        binance_data = self._binance_builder.to_dict("binance_spot")

        # 기존 캔들이 있으면 직전 캔들에서 ffill 참조용 값들을 갱신
        last_candle = self.candles[-1] if self.candles else None
        last_bid_ratio = last_candle.get("target_trade_bid_ratio", last_candle.get("upbit_trade_bid_ratio", 0.5)) if last_candle else 0.5
        last_binance_taker_ratio = last_candle.get("binance_spot_taker_ratio", 0.5) if last_candle else 0.5
        last_binance_taker_quote_ratio = last_candle.get("binance_spot_taker_quote_ratio", 0.5) if last_candle else 0.5

        if target_data:
            candle.update(target_data)
            self._last_target_price = target_data.get("target_close", self._last_target_price)
            self._last_upbit_price = self._last_target_price
        else:
            # 틱이 없었던 캔들: 마지막 알려진 값들로 forward-fill (거래량 관련 수치는 0.0)
            candle.update({
                "target_open": self._last_target_price,
                "target_close": self._last_target_price,
                "target_high": self._last_target_price,
                "target_low": self._last_target_price,
                "target_volume": 0.0,
                "target_trade_bid_ratio": last_bid_ratio,
                "target_trade_count": 0.0,
                "target_large_trade_count": 0.0,
                "target_avg_trade_size": 0.0,
                "target_vwap": self._last_target_price,
                "target_is_large_trade": 0.0,
                "upbit_open": self._last_target_price,
                "upbit_close": self._last_target_price,
                "upbit_high": self._last_target_price,
                "upbit_low": self._last_target_price,
                "upbit_volume": 0.0,
                "upbit_trade_bid_ratio": last_bid_ratio,
                "upbit_trade_count": 0.0,
                "upbit_large_trade_count": 0.0,
                "upbit_avg_trade_size": 0.0,
                "vwap": self._last_target_price,
                "is_large_trade": 0.0,
            })

        # ── 외부/참조 계열: 학습 데이터(Gold) 결합과 같은 규칙 ──
        #   상태값(가격·OI·펀딩비·환율): 마지막 관측을 자산군별 stale 상한까지, 관측 전·상한 초과는 NaN
        #   흐름값(거래량·체결수): 원천이 살아 있으면 이번 봉 누적(없으면 0), 원천이 없으면 NaN
        #   봉 내부 비율값(taker 비율): 이번 봉 체결로만 정의 — 체결이 없으면 NaN
        # 다른 계열로 대체하지 않는다 (종전: 선물←타겟가, 코인베이스←바이낸스, 환율←1380 — 학습에 없는 거짓 관측)
        at = self._current_boundary_end or self._current_boundary_start
        if binance_data:
            candle.update(binance_data)
        else:
            flow = 0.0 if self._obs["binance_spot_close"].alive(at) else float("nan")
            candle.update({
                "binance_spot_close": self._obs["binance_spot_close"].read(at),
                "binance_spot_volume": flow,
                "binance_spot_taker_ratio": float("nan"),
                "binance_spot_trade_count": flow,
                "binance_spot_quote_volume": flow,
                "binance_spot_taker_quote_ratio": float("nan"),
            })

        for col in _EXTERNAL_STATE_FIELDS:
            if col in ("binance_spot_close", "binance_usdc_volume", "benchmark_btc_close", "spot_close"):
                continue
            candle[col] = self._obs[col].read(at)
        candle["futures_close"] = candle["kospi_futures_close"]
        candle["binance_usdc_volume"] = (self._current_binance_usdc_volume
                                         if self._obs["binance_usdc_volume"].alive(at) else float("nan"))

        # 벤치마크 BTC: 타겟이 벤치마크 자산이면 바이낸스 현물이 곧 벤치마크다 (같은 종목). 아니면 별도 수신값만.
        sym = getattr(self, 'symbol', 'KRW-BTC')
        bench_src = "binance_spot_close" if get_asset_id(sym) == BENCHMARK_ASSET_ID else "benchmark_btc_close"
        candle["benchmark_btc_close"] = self._obs[bench_src].read(at)

        # 동적 참조 대장주(Reference Symbols)
        for alias, r_builder in self._ref_builders.items():
            r_close_col = f"ref_{alias}_close"
            r_vol_col = f"ref_{alias}_volume"
            r_bid_col = f"ref_{alias}_bid_vol"

            if r_builder.tick_count > 0:
                candle[r_close_col] = r_builder.close
                candle[r_vol_col] = r_builder.volume
                candle[r_bid_col] = r_builder.bid_volume
            else:
                obs = self._ref_observed(alias)
                candle[r_close_col] = obs.read(at)
                candle[r_vol_col] = 0.0 if obs.alive(at) else float("nan")
                candle[r_bid_col] = candle[r_vol_col]
            r_builder.reset()

        # KOSPI200 지수 현물 종가: 참조 대장주(ref_u180_close 등) 또는 외부 관측 spot_close 반영
        ref_spot = candle.get("ref_u180_close")
        if ref_spot is not None and not (isinstance(ref_spot, float) and math.isnan(ref_spot)):
            candle["spot_close"] = ref_spot
        else:
            candle["spot_close"] = self._obs["spot_close"].read(at)

        # 2단계 캔들 기하학적 정합성 자동 방역 (Sanitization: shared.candle_core SSOT)
        t_open = candle.get("target_open", 0.0)
        t_high = candle.get("target_high", 0.0)
        t_low = candle.get("target_low", 0.0)
        t_close = candle.get("target_close", 0.0)
        t_vol = candle.get("target_volume", 0.0)
        if t_open > 0 and t_close > 0:
            _, fixed_high, fixed_low, _, fixed_vol = sanitize_candle_ohlcv(t_open, t_high, t_low, t_close, t_vol)
            candle["target_high"] = fixed_high
            candle["target_low"] = fixed_low
            candle["target_volume"] = fixed_vol
            candle["upbit_high"] = fixed_high
            candle["upbit_low"] = fixed_low
            candle["upbit_volume"] = fixed_vol

        self.candles.append(candle)
        self.candle_seq += 1

        # 빌더 및 누적 필드 초기화
        self._target_builder.reset()
        self._binance_builder.reset()
        self._current_binance_usdc_volume = 0.0

    def _discard_current_candle(self):
        """학습 데이터에 없는 봉(KRX 동시호가 대기 구간의 빈 분봉 등)은 만들지 않고 진행 중인 빌더만 비운다."""
        self._target_builder.reset()
        self._binance_builder.reset()
        for r_builder in self._ref_builders.values():
            r_builder.reset()
        self._current_binance_usdc_volume = 0.0

    def _ref_observed(self, alias: str) -> _Observed:
        """참조 종목 종가 관측 — stale 상한은 원장 market_type 으로 정한다 (Gold 결합과 같은 정책)."""
        obs = self._ref_obs.get(alias)
        if obs is None:
            sym = next((s for s, a in self.ref_symbol_map.items() if a == alias), None)
            mtype = str(get_symbol_info(sym).get("market_type", "")) if sym else ""
            obs = self._ref_obs[alias] = _Observed(resolve_stale_cap(mtype or None))
        return obs

    def on_ref_tick(self, alias: str, price: float, volume: float, is_buyer: bool = True,
                    timestamp: Optional[datetime] = None):
        """참조 대장주 (Reference Symbol, 예: hynix, samsung) 실시간 체결 틱 수신"""
        alias_clean = str(alias).strip().lower()
        if not alias_clean or price <= 0 or volume < 0:
            return

        # 2단계 비정상 스파이크 틱(Bad Tick) 검증 (직전 정상가 대비 30% 이상 이상 급변 차단)
        last_ref_p = self._last_ref_prices.get(alias_clean, 0.0)
        if last_ref_p > 0 and abs(price - last_ref_p) / last_ref_p > 0.30:
            logging.warning(f"[CandleAggregator] ⚠️ Bad Ref Tick Dropped: {alias_clean} price={price:,.0f} (last={last_ref_p:,.0f})")
            return

        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        if alias_clean not in self._ref_builders:
            self._ref_builders[alias_clean] = _CandleBuilder()

        # 봉 체결량은 타겟 세션 안에서만 쌓는다 (관측 종가는 언제든 갱신 — 장후 선물 가격도 실제 관측이다)
        if self._accepting_ticks(timestamp):
            self._ref_builders[alias_clean].update(price, volume, is_buyer)
        self._last_ref_prices[alias_clean] = price
        self._ref_observed(alias_clean).update(price, timestamp)
        if alias_clean in ("u180", "kospi200", "krx_index"):
            self._obs["spot_close"].update(price, timestamp)

    def on_target_tick(self, price: float, volume: float, side: str = "BID",
                       timestamp: Optional[datetime] = None, is_buyer: Optional[bool] = None):
        """타겟 종목 (KIS 국내주식/ETF 또는 업비트) 실시간 체결 틱 수신"""
        if price <= 0 or volume < 0:
            return

        # 2단계 비정상 스파이크 틱(Bad Tick) 검증 (직전 정상가 대비 30% 이상 이상 급변 차단)
        if self._last_target_price > 0 and abs(price - self._last_target_price) / self._last_target_price > 0.30:
            logging.warning(f"[CandleAggregator] ⚠️ Bad Target Tick Dropped: {self.symbol} price={price:,.0f} (last={self._last_target_price:,.0f})")
            return

        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        # KRX 장전·마감 확정 이후 체결(시간외 등)은 봉에 넣지 않는다
        if not self._accepting_ticks(timestamp):
            return
        # 지연 도착 틱(현재 캔들 경계 이전 틱)은 현재 캔들 오염 방지를 위해 합산하지 않음
        if self._current_boundary_start and timestamp < self._current_boundary_start:
            return

        if is_buyer is None:
            is_buyer = str(side).upper() in ('BUY', 'BID')
        self._target_builder.update(price, volume, is_buyer)
        self._last_target_price = price
        self._last_upbit_price = price

    def on_futures_tick(self, futures_symbol: str, price: float,
                        timestamp: Optional[datetime] = None):
        """국내/해외 선물 실시간 가격 수신 (KOSPI 선물, NQ 선물, S&P500 선물 등)"""
        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        sym = str(futures_symbol).strip().upper()
        # 6자리 일반 주식코드는 선물 가격으로 오인하지 않음
        if len(sym) == 6 and sym.isdigit():
            return
        if "KOSPI" in sym or sym.startswith("101") or sym.startswith("105"):
            self._obs["kospi_futures_close"].update(price, timestamp)
            for ref_k in (sym.lower(), "10100", "kospi_futures"):
                alias = getattr(self, "ref_symbol_map", {}).get(sym, ref_k)
                if alias in self._ref_builders:
                    self._ref_builders[alias].update(price, 0.0, True)
                    self._ref_observed(alias).update(price, timestamp)
                    break
        elif "NQ" in sym or "NAS" in sym or "TQQQ" in sym or "QQQ" in sym:
            self._obs["nq_futures_close"].update(price, timestamp)
        elif "SP" in sym or "ES" in sym or "SPY" in sym:
            self._obs["sp500_futures_close"].update(price, timestamp)
        elif "NVDA" in sym:
            self._obs["nvda_futures_close"].update(price, timestamp)
        elif "10Y" in sym or "ZN" in sym or "TN" in sym:
            self._obs["us10y_futures_close"].update(price, timestamp)
        elif "WTI" in sym or "CL" in sym or "OIL" in sym:
            self._obs["wti_futures_close"].update(price, timestamp)
        elif "GOLD" in sym or "XAU" in sym or "GC" in sym:
            self._obs["gold_futures_close"].update(price, timestamp)
        elif "USDKRW" in sym or "USD_KRW" in sym or "USD/KRW" in sym:
            self._obs["usd_krw_fx"].update(price, timestamp)

    def on_index_tick(self, index_symbol: str, price: float,
                      timestamp: Optional[datetime] = None):
        """KOSPI 200 등 현물 지수 실시간 가격 수신"""
        if price <= 0:
            return
        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        sym = str(index_symbol).strip().upper()
        if "U180" in sym or "200" in sym or sym == "028":
            self._obs["spot_close"].update(price, timestamp)

    def on_coinbase_price(self, price: float, timestamp: Optional[datetime] = None):
        """코인베이스 타겟 심볼 현재가 수신"""
        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        self._obs["coinbase_spot_close"].update(price, timestamp)

    def on_benchmark_btc_tick(self, price: float, timestamp: Optional[datetime] = None):
        """벤치마크 BTCUSDT 현재가 수신 (알트코인 상대강도용)"""
        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        self._obs["benchmark_btc_close"].update(price, timestamp)

    def _ensure_boundary(self, ts: datetime) -> bool:
        """시간 경계를 확인하고, 캔들 전환이 필요하면 순차 flush (다수 캔들 경과 시 누락 없이 ffill 생성)"""
        start, end = self._get_candle_boundary(ts)

        if self._current_boundary_start is None:
            # 최초 틱 수신
            if self.market_type == "KRX":
                # KRX 장전·장마감 확정 이후 틱은 캔들 경계를 초기화하지 않고 대기
                if not self._accepting_ticks(ts):
                    return False
                if ts.time() >= krx_session(ts.date()).close:   # 동시호가 수신 대기 중 기동
                    start = self._krx_close_bucket_start(ts)
                    end = start + timedelta(seconds=self.interval_seconds)
            self._current_boundary_start = start
            self._current_boundary_end = end
            return False  # 캔들 완성 아님

        # 과거 지연 틱 검사
        if start < self._current_boundary_start:
            return False

        if self.market_type == "KRX":
            return self._ensure_boundary_krx(ts, start, end)

        completed = False
        # 일반(CRYPTO): 건너뛴 모든 빈 캔들을 순차적으로 flush (시계열 연속성 보장)
        while start >= self._current_boundary_end:
            self._flush_current_candle()
            self._current_boundary_start = self._current_boundary_end
            self._current_boundary_end = self._current_boundary_start + timedelta(seconds=self.interval_seconds)
            completed = True

        return completed

    # ── KRX 세션 경계 ──────────────────────────────────────────────────────
    def _krx_close_bucket_start(self, ts: datetime) -> datetime:
        """그날 종가 동시호가 체결이 들어갈 봉의 시작 시각 (학습 데이터와 같은 자리)."""
        close = krx_session(ts.date()).close
        close_at = ts.replace(hour=close.hour, minute=close.minute, second=0, microsecond=0)
        if self.interval_seconds < 60:
            return close_at   # 초봉 학습 그리드는 15:30:00 동시호가 봉을 따로 둔다
        start, _ = self._get_candle_boundary(close_at - timedelta(microseconds=1))
        return start          # 분봉 이상: 15:30 직전 구간(마지막 정규장 봉)에 합산

    def _accepting_ticks(self, ts: datetime) -> bool:
        """이 시각의 체결을 봉에 넣는가. KRX 는 정규장 + 동시호가 대기 구간만 받는다."""
        if self.market_type != "KRX":
            return True
        sess = krx_session(ts.date())
        if ts.time() < sess.open or self._krx_closed_day == ts.date():
            return False
        close_at = ts.replace(hour=sess.close.hour, minute=sess.close.minute, second=0, microsecond=0)
        return ts < close_at + _KRX_AUCTION_GRACE

    def _skip_empty_krx_bar(self) -> bool:
        """체결 없는 분봉은 Creon 1분봉(과 그 집계)에 없다 — 만들지 않는다 (초봉은 학습 그리드처럼 채운다)."""
        return self.interval_seconds >= 60 and self._target_builder.tick_count == 0

    def _advance_krx(self, target_start: datetime) -> bool:
        """현재 봉부터 target_start 직전 봉까지 순서대로 닫는다."""
        completed = False
        while self._current_boundary_start < target_start:
            if self._skip_empty_krx_bar():
                self._discard_current_candle()
            else:
                self._flush_current_candle()
                completed = True
            self._current_boundary_start = self._current_boundary_end
            self._current_boundary_end = self._current_boundary_start + timedelta(seconds=self.interval_seconds)
        return completed

    def _close_current_krx_bar(self) -> bool:
        if self._skip_empty_krx_bar():
            self._discard_current_candle()
            return False
        self._flush_current_candle()
        return True

    def _ensure_boundary_krx(self, ts: datetime, start: datetime, end: datetime) -> bool:
        cur = self._current_boundary_start
        completed = False

        # 1. 날짜가 바뀌었다 — 전날 세션이 확정되지 않은 채라면(이벤트 단절 등) 진행 중인 봉만 닫는다.
        #    빈 봉을 장 마감까지 채우지 않는다. 빌더는 반드시 비운다 (전날 체결이 익일 첫 봉에 섞이지 않게).
        if ts.date() > cur.date():
            if self._krx_closed_day != cur.date():
                if self._target_builder.tick_count > 0:
                    self._flush_current_candle()
                    completed = True
                else:
                    self._discard_current_candle()
                self._krx_closed_day = cur.date()
            if not self._accepting_ticks(ts):   # 장전 — 당일 정규장 첫 봉 경계 진입 대기
                return completed
            self._current_boundary_start, self._current_boundary_end = start, end
            return completed

        # 2. 오늘 세션은 이미 확정됐다 / 장전 — 시간외·야간 이벤트는 봉을 만들지 않는다
        sess = krx_session(ts.date())
        if self._krx_closed_day == ts.date() or ts.time() < sess.open:
            return False

        # 3. 마감(평일 15:30) 이후: 동시호가 봉까지 진행하고, 수신 대기가 끝나면 그날 마지막 봉을 확정한다
        if ts.time() >= sess.close:
            close_bucket = self._krx_close_bucket_start(ts)
            completed |= self._advance_krx(close_bucket)
            if not self._accepting_ticks(ts):
                completed |= self._close_current_krx_bar()
                self._krx_closed_day = ts.date()
            return completed

        # 4. 정규장 중
        return self._advance_krx(start)

    def on_upbit_tick(self, price: float, volume: float, side: str,
                      timestamp: Optional[datetime] = None):
        """업비트/국내주식 체결 틱 수신 (하위 호환)"""
        self.on_target_tick(price, volume, side, timestamp)

    def on_upbit_usdt_tick(self, price: float,
                           timestamp: Optional[datetime] = None):
        """업비트 KRW-USDT 테더 가격 수신 (김치프리미엄 정밀 계산용)"""
        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        self._obs["upbit_usdt_close"].update(price, timestamp)

    def on_binance_tick(self, price: float, volume: float, is_buyer_maker: bool,
                        timestamp: Optional[datetime] = None):
        """바이낸스 현물 체결 틱 수신"""
        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        # Binance: is_buyer_maker=False → Taker가 매수 → is_buyer=True
        is_buyer = not is_buyer_maker
        self._binance_builder.update(price, volume, is_buyer)
        self._obs["binance_spot_close"].update(price, timestamp)

    def on_binance_futures_oi(self, open_interest: float, open_interest_value: float,
                              timestamp: Optional[datetime] = None):
        """바이낸스 선물 미결제 약정(OI) 수신"""
        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        self._obs["binance_futures_oi"].update(open_interest, timestamp)
        self._obs["binance_futures_oi_value"].update(open_interest_value, timestamp)

    def on_binance_funding_rate(self, funding_rate: float,
                                timestamp: Optional[datetime] = None):
        """바이낸스 선물 확정 펀딩비(Funding Rate) 수신 (8시간 주기 확정치)"""
        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        self._obs["binance_funding_rate"].update(funding_rate, timestamp)

    def add_binance_usdc_volume(self, volume: float, timestamp: Optional[datetime] = None):
        """바이낸스 USDC 마켓 거래량 누적"""
        if timestamp is None:
            timestamp = datetime.now(KST)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=KST)

        self._ensure_boundary(timestamp)
        self._current_binance_usdc_volume += volume
        self._obs["binance_usdc_volume"].update(volume, timestamp)


    def check_candle_complete(self, current_timestamp: Optional[datetime] = None) -> Optional[pd.DataFrame]:
        """현재 시각 기준으로 캔들 완성 여부를 확인하고, 완성 시 전체 DataFrame 반환

        Returns:
            캔들이 새로 완성되었으면 전체 캔들 히스토리 DataFrame, 아니면 None
        """
        if current_timestamp is None:
            current_timestamp = datetime.now(KST)
        elif current_timestamp.tzinfo is None:
            current_timestamp = current_timestamp.replace(tzinfo=KST)

        self._ensure_boundary(current_timestamp)
        if self.candle_seq > self._last_checked_seq and len(self.candles) > 0:
            self._last_checked_seq = self.candle_seq
            return self.to_dataframe()
        return None

    def to_dataframe(self) -> pd.DataFrame:
        """현재까지 완성된 캔들 히스토리를 DataFrame으로 반환"""
        if not self.candles:
            return pd.DataFrame()

        df = pd.DataFrame(list(self.candles))
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df

    @property
    def candle_count(self) -> int:
        return len(self.candles)

    @property
    def total_completed_candles(self) -> int:
        """시스템 기동 후 누적 완성된 총 캔들 수 (버퍼 maxlen 제한 없는 단조 증가 카운터)"""
        return self.candle_seq

    @property
    def has_enough_data(self) -> bool:
        """피처 계산 및 지표 수렴에 필요한 캔들 수가 충족되었는지 확인"""
        threshold = max(self.min_required_candles, 30 if self.interval_minutes <= 5 else 50)
        return len(self.candles) >= threshold

    def add_backfilled_candle(self, candle: dict):
        """과거 캔들 백필/워밍업 전용 주입 (버퍼 적재 및 시퀀스 동기화)"""
        self.candles.append(candle)
        self.candle_seq += 1
        self._last_checked_seq = self.candle_seq

        # 마지막 가격·관측 동기화 — 백필 봉의 값은 그 봉이 끝난 시각에 관측된 것으로 본다
        target_p = candle.get("target_close", candle.get("upbit_close", 0.0))
        if target_p and target_p > 0:
            self._last_target_price = float(target_p)
            self._last_upbit_price = float(target_p)

        ts = candle.get("timestamp")
        seen_at = (_as_kst(pd.Timestamp(ts).to_pydatetime()) + timedelta(seconds=self.interval_seconds) - self._label_offset) if ts is not None else None
        if seen_at is not None:
            for col in _EXTERNAL_STATE_FIELDS:
                if col in candle and col != "binance_usdc_volume":
                    self._obs[col].update(candle[col], seen_at)
            if "kospi_futures_close" not in candle and "futures_close" in candle:
                self._obs["kospi_futures_close"].update(candle["futures_close"], seen_at)

        for k, v in candle.items():
            if k.startswith("ref_") and k.endswith("_close") and v is not None and float(v) > 0:
                alias = k[4:-6]
                self._last_ref_prices[alias] = float(v)
                if seen_at is not None:
                    self._ref_observed(alias).update(v, seen_at)

