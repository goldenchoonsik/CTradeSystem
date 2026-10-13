"""
CAiModelBaker - Physics & Market Friction Constraint Engine
계량금융(Quantitative Finance) 및 시장 미시구조(Market Microstructure) 기반
선험적 제약 조건(Analytical Pre-Constraints) 산출 모듈

수수료, 슬리피지, 캔들 변동성(ATR)을 기반으로 물리적/수학적 하한치를 강제하여
비물리적인 탐색 공간(Search Space)을 사전에 90% 이상 제거하고 과최적화를 원천 차단합니다.
"""

import math
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd

from shared.core.trade_costs import round_trip_cost, resolve_sell_tax


def resolve_friction_rates(config: Optional[dict], default_fee: float = 0.00015, default_slip: float = 0.0002):
    """[FIX-14] 마찰비용(편도 수수료/슬리피지) 조회 단일 헬퍼.

    우선순위(model_engine/backtester 현행과 동일):
      fee  : config['FEE_RATE'] > config['FEE_UPBIT'] > OPTUNA_CONFIG['fee_upbit'] > default_fee
      slip : config['SLIPPAGE'] > OPTUNA_CONFIG['slippage'] > default_slip
    """
    config = config or {}
    optuna_cfg = config.get('OPTUNA_CONFIG', {}) or {}
    fee = config.get('FEE_RATE', config.get('FEE_UPBIT', optuna_cfg.get('fee_upbit', default_fee)))
    slip = config.get('SLIPPAGE', optuna_cfg.get('slippage', default_slip))
    return float(fee), float(slip)


def resolve_round_trip_cost(config: Optional[dict]) -> float:
    """설정 기준 왕복 총 비용 (수수료·슬리피지 양방향 + 타겟 종목 매도세). 비용 계산은 이 함수로만."""
    fee, slip = resolve_friction_rates(config)
    return round_trip_cost(fee, slip, resolve_sell_tax(config))


def slice_pre_holdout(df: Optional[pd.DataFrame], holdout_start) -> Optional[pd.DataFrame]:
    """[FIX-2] 홀드아웃 시작 이전 구간만 반환 (물리 제약/ATR 산출이 홀드아웃을 보지 않도록).

    holdout_start 가 없거나 timestamp 컬럼이 없으면 원본을 그대로 반환한다.
    """
    if df is None or not holdout_start or 'timestamp' not in df.columns:
        return df
    try:
        cut = pd.to_datetime(holdout_start)
        return df[pd.to_datetime(df['timestamp']) < cut]
    except Exception:
        return df


def calc_mean_atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """
    주어진 데이터프레임에서 ATR(Average True Range)의 평균 퍼센트(%)를 산출합니다.
    """
    if df is None or len(df) < period + 1:
        return 0.0020  # 기본값 0.20% (비트코인 10분봉 표준)

    # 컬럼 대소문자 스마트 매칭
    col_map = {c.lower(): c for c in df.columns}
    high_col = col_map.get("high") or col_map.get("target_high")
    low_col = col_map.get("low") or col_map.get("target_low")
    close_col = col_map.get("close") or col_map.get("target_close")

    if not (high_col and low_col and close_col):
        # 만약 컬럼이 없으면 close 기반 대체
        return 0.0020

    high = df[high_col].astype(float)
    low = df[low_col].astype(float)
    close = df[close_col].astype(float)
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(window=period).mean()
    atr_pct = (atr / close).dropna()

    mean_atr = float(atr_pct.mean()) if len(atr_pct) > 0 else 0.0020
    return max(mean_atr, 0.0001)


def calc_friction_floor(
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0006,
    mean_atr_pct: float = 0.0020,
    market_type: str = "CRYPTO",
    sell_tax: float = 0.0
) -> Dict[str, Any]:
    """
    왕복 거래 비용과 캔들 변동성(ATR)을 기반으로 물리적 배리어 및 시간 하한치를 산출합니다.

    Parameters:
        fee_rate: 편도 거래 수수료 (예: 0.0005 = 0.05%)
        slippage_rate: 편도 예상 슬리피지 (예: 0.0006 = 0.06%)
        mean_atr_pct: 단일 캔들 평균 ATR 비율 (예: 0.0020 = 0.20%)
        market_type: "KRX" (국내 주식/ETF) 또는 "CRYPTO" (가상화폐)

    Returns:
        물리적 제약 조건 딕셔너리
    """
    is_krx = (str(market_type).upper() == "KRX")

    # 1. 왕복 총 마찰 비용 (수수료 2회 + 슬리피지 2회 + 매도세 1회)
    roundtrip_friction = round_trip_cost(fee_rate, slippage_rate, sell_tax)
    roundtrip_friction = max(roundtrip_friction, 0.0001 if is_krx else 0.0002)

    # 2. 변동성 대비 마찰력 비율 (Friction Ratio = ATR / Fee)
    friction_ratio = mean_atr_pct / roundtrip_friction

    # 3. 최소 익절폭 (TP_min): 최소 비용의 3.0~3.5배 이상이어야 통계적 기대값 > 0 성립
    friction_mult = 3.0 if is_krx else 3.5
    tp_pct_min = roundtrip_friction * friction_mult
    # ATR 배수로 환산 (주식 ETF는 1.2 ATR, 코인은 3.0 ATR 하한)
    min_atr_floor = 1.2 if is_krx else 3.0
    tp_atr_min = max(min_atr_floor, round(tp_pct_min / max(mean_atr_pct, 1e-6), 1))

    # 4. 최소 손절폭 (SL_min): 단일 캔들 노이즈 방어선 (KRX 1.0 ATR, CRYPTO 2.0 ATR)
    sl_atr_min = 1.0 if is_krx else 2.0
    sl_pct_min = sl_atr_min * mean_atr_pct

    # 5. 최소 필요 예측 지평 (First Passage Time: t_min = ceil((TP_min / ATR)^2))
    raw_min_horizon = math.ceil((tp_atr_min) ** 2)
    min_cap = 18 if is_krx else 24
    min_horizon_candles = max(6, min(raw_min_horizon, min_cap))
    rec_horizon_candles = max(int(min_horizon_candles * 1.5), min_horizon_candles + 2)
    max_hold_candles = max(int(min_horizon_candles * 2.0), rec_horizon_candles + 2)

    # 6. 시장 마찰력 상태 평가
    if friction_ratio < 1.1:
        friction_status = "EXTREME_FRICTION"
        recommendation = "공략 비추천: 캔들 변동성보다 수수료가 커서 단타 시 계좌 소멸 위험. 타임프레임 상향 권장."
    elif friction_ratio < 2.2:
        friction_status = "MODERATE_FRICTION"
        recommendation = "스나이퍼 모드: 고빈도 매매 불가, 고확률 진입만 허용."
    else:
        friction_status = "HEALTHY"
        recommendation = "공략 양호: 변동성이 수수료를 충분히 상쇄하여 적극적 매매 가능."

    return {
        "method": "analytical_formula",
        "roundtrip_friction_pct": round(roundtrip_friction * 100.0, 4),
        "sell_tax_pct": round(float(sell_tax) * 100.0, 4),
        "mean_atr_pct": round(mean_atr_pct * 100.0, 4),
        "friction_ratio": round(friction_ratio, 2),
        "friction_status": friction_status,
        "recommendation": recommendation,
        "tp_pct_min": round(tp_pct_min * 100.0, 3),
        "tp_atr_min": tp_atr_min,
        "tp_atr_max": round(tp_atr_min + 2.0, 1),
        "sl_pct_min": round(sl_pct_min * 100.0, 3),
        "sl_atr_min": sl_atr_min,
        "sl_atr_max": round(sl_atr_min + 1.5, 1),
        "min_horizon_candles": min_horizon_candles,
        "rec_horizon_candles": rec_horizon_candles,
        "max_hold_candles": max_hold_candles,
    }


def calc_empirical_barrier_bounds(
    df: pd.DataFrame,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0006,
    interval: str = '10s',
    market_type: str = 'CRYPTO',
    mean_atr_pct: Optional[float] = None,
    sell_tax: float = 0.0
) -> Dict[str, Any]:
    """
    [퀀트 실무 표준] 실제 표본 데이터 기반 장벽 보정 (Empirical Barrier Calibration)
    
    이론식 가정에 의존하지 않고, 실제 과거 데이터에서 각 사정거리(H캔들)별
    MFE(최고점 수익)와 MAE(최저점 손실)의 실제 경험적 분포(Quantiles)를 측정하여
    도달 가능한 현실적인 TP, SL 및 적정 홀딩 지평을 도출합니다.
    """
    if df is None or len(df) < 50:
        return calc_friction_floor(fee_rate, slippage_rate, mean_atr_pct or 0.0020, market_type, sell_tax)

    is_krx = (str(market_type).upper() == "KRX")
    roundtrip_friction = round_trip_cost(fee_rate, slippage_rate, sell_tax)
    roundtrip_friction = max(roundtrip_friction, 0.0001 if is_krx else 0.0002)

    if mean_atr_pct is None:
        mean_atr_pct = calc_mean_atr_pct(df)

    col_map = {c.lower(): c for c in df.columns}
    close_col = col_map.get("target_close") or col_map.get("close") or col_map.get("upbit_close")
    high_col = col_map.get("high") or col_map.get("target_high") or close_col
    low_col = col_map.get("low") or col_map.get("target_low") or close_col

    if not close_col:
        return calc_friction_floor(fee_rate, slippage_rate, mean_atr_pct, market_type, sell_tax)

    # 표본 데이터 슬라이싱 (최대 최근 20,000행으로 고속 연산)
    sample_df = df.iloc[-20000:].copy().reset_index(drop=True)
    closes = sample_df[close_col].to_numpy(dtype=float)
    highs = sample_df[high_col].to_numpy(dtype=float)
    lows = sample_df[low_col].to_numpy(dtype=float)
    n_bars = len(closes)

    # 인터벌별 후보 호라이즌 목록 결정
    intv_clean = str(interval).strip().lower()
    if intv_clean.endswith('s'):
        candidate_horizons = [6, 12, 18, 24, 36, 48]
    elif intv_clean in ('1m', '3m'):
        candidate_horizons = [3, 6, 10, 15, 20, 30]
    elif intv_clean in ('5m', '10m', '15m'):
        candidate_horizons = [3, 6, 12, 16, 24]
    elif intv_clean.endswith('d'):
        candidate_horizons = [2, 5, 10, 15, 20]
    else:
        candidate_horizons = [2, 4, 8, 12, 16]

    profiles = {}
    target_profit = roundtrip_friction * (3.0 if is_krx else 3.5)
    selected_min_h = None
    selected_rec_h = None

    high_series = pd.Series(highs)
    low_series = pd.Series(lows)

    for h in candidate_horizons:
        if n_bars <= h + 10:
            continue
        # 미래 h개 봉의 최대/최소
        fwd_max = high_series.iloc[::-1].rolling(window=h, min_periods=1).max().iloc[::-1].shift(-1).to_numpy()
        fwd_min = low_series.iloc[::-1].rolling(window=h, min_periods=1).min().iloc[::-1].shift(-1).to_numpy()

        valid_mask = np.isfinite(fwd_max) & np.isfinite(fwd_min) & (closes > 0)
        valid_mask[-h:] = False

        if np.sum(valid_mask) < 30:
            continue

        mfe = (fwd_max[valid_mask] - closes[valid_mask]) / closes[valid_mask]
        mae = (closes[valid_mask] - fwd_min[valid_mask]) / closes[valid_mask]

        mfe_p50 = float(np.percentile(mfe, 50))
        mfe_p75 = float(np.percentile(mfe, 75))
        mfe_p90 = float(np.percentile(mfe, 90))
        mae_p75 = float(np.percentile(mae, 75))
        mae_p90 = float(np.percentile(mae, 90))

        profiles[h] = {
            "mfe_p50": mfe_p50,
            "mfe_p75": mfe_p75,
            "mfe_p90": mfe_p90,
            "mae_p75": mae_p75,
            "mae_p90": mae_p90
        }

        # 수수료의 3배를 75% 분위수에서 최초로 상회하는 지점을 최소 호라이즌으로 선정
        if selected_min_h is None and mfe_p75 >= target_profit:
            selected_min_h = h

    if selected_min_h is None:
        selected_min_h = candidate_horizons[1] if len(candidate_horizons) > 1 else candidate_horizons[0]

    # 권장 호라이즌은 min_h보다 한 단계 위 또는 2배
    idx_min = candidate_horizons.index(selected_min_h) if selected_min_h in candidate_horizons else 0
    selected_rec_h = candidate_horizons[min(idx_min + 1, len(candidate_horizons) - 1)]
    selected_max_hold = candidate_horizons[min(idx_min + 2, len(candidate_horizons) - 1)]
    if selected_max_hold <= selected_min_h:
        selected_max_hold = selected_min_h * 2

    # 권장 지평 기준 MFE/MAE를 ATR 배수로 환산
    rec_prof = profiles.get(selected_rec_h) or profiles.get(selected_min_h) or {}
    safe_atr = max(mean_atr_pct, 1e-6)

    min_atr_floor = 1.0 if is_krx else 2.5
    sl_atr_floor = 0.8 if is_krx else 1.5

    raw_tp_min = rec_prof.get("mfe_p50", target_profit) / safe_atr
    raw_tp_max = rec_prof.get("mfe_p90", target_profit * 2.0) / safe_atr
    raw_sl_min = rec_prof.get("mae_p75", target_profit * 0.8) / safe_atr
    raw_sl_max = rec_prof.get("mae_p90", target_profit * 1.5) / safe_atr

    tp_atr_min = max(min_atr_floor, round(raw_tp_min, 1))
    tp_atr_max = max(tp_atr_min + 1.2, round(raw_tp_max, 1))
    sl_atr_min = max(sl_atr_floor, round(raw_sl_min, 1))
    sl_atr_max = max(sl_atr_min + 0.8, round(raw_sl_max, 1))

    friction_ratio = mean_atr_pct / roundtrip_friction
    if friction_ratio < 1.1:
        friction_status = "EXTREME_FRICTION"
        recommendation = "공략 비추천: 캔들 변동성보다 수수료가 커서 단타 시 계좌 소멸 위험."
    elif friction_ratio < 2.2:
        friction_status = "MODERATE_FRICTION"
        recommendation = "스나이퍼 모드: 고빈도 매매 불가, 고확률 진입만 허용."
    else:
        friction_status = "HEALTHY"
        recommendation = f"공략 양호: 실측 MFE 기반 권장 사정거리 {selected_rec_h}봉({selected_rec_h * (10 if intv_clean.endswith('s') else 1)}단위) 최적화."

    return {
        "method": "empirical_sampling",
        "roundtrip_friction_pct": round(roundtrip_friction * 100.0, 4),
        "sell_tax_pct": round(float(sell_tax) * 100.0, 4),
        "mean_atr_pct": round(mean_atr_pct * 100.0, 4),
        "friction_ratio": round(friction_ratio, 2),
        "friction_status": friction_status,
        "recommendation": recommendation,
        "tp_pct_min": round(tp_atr_min * mean_atr_pct * 100.0, 3),
        "tp_atr_min": tp_atr_min,
        "tp_atr_max": tp_atr_max,
        "sl_pct_min": round(sl_atr_min * mean_atr_pct * 100.0, 3),
        "sl_atr_min": sl_atr_min,
        "sl_atr_max": sl_atr_max,
        "min_horizon_candles": selected_min_h,
        "rec_horizon_candles": selected_rec_h,
        "max_hold_candles": selected_max_hold,
    }


def apply_physics_constraints_to_config(
    config: dict,
    mean_atr_pct: Optional[float] = None,
    df: Optional[pd.DataFrame] = None
) -> dict:
    """
    설정 객체(config)의 Optuna 탐색 범위를 물리적 하한치 및 실측 변동폭에 맞추어 자동 보정합니다.
    """
    from shared.core.market_utils import detect_market_type
    market_type = detect_market_type(config)

    # [FIX-14] 모듈 간 동일 마찰비용 키 조회 (M/B 우선순위)
    fee, slippage = resolve_friction_rates(config)
    sell_tax = resolve_sell_tax(config)

    if mean_atr_pct is None and df is not None:
        mean_atr_pct = calc_mean_atr_pct(df)
    elif mean_atr_pct is None:
        mean_atr_pct = float(config.get("MEAN_ATR_PCT", 0.0020))

    if df is not None and len(df) >= 100:
        physics = calc_empirical_barrier_bounds(
            df=df,
            fee_rate=fee,
            slippage_rate=slippage,
            interval=config.get("INTERVAL", "1m"),
            market_type=market_type,
            mean_atr_pct=mean_atr_pct,
            sell_tax=sell_tax
        )
    else:
        physics = calc_friction_floor(
            fee_rate=fee,
            slippage_rate=slippage,
            mean_atr_pct=mean_atr_pct,
            market_type=market_type,
            sell_tax=sell_tax
        )

    config["PHYSICS_CONSTRAINTS"] = physics

    optuna_cfg = config.get("OPTUNA_CONFIG", {})
    if not optuna_cfg:
        return config

    # 1. Take Profit (TP) 하한치 및 상한치 보정
    tp_range = optuna_cfg.get("take_profit_range", {})
    if tp_range:
        current_min = float(tp_range.get("user_min", tp_range.get("min", 1.0)))
        if current_min < physics["tp_atr_min"]:
            tp_range["user_min"] = physics["tp_atr_min"]
            tp_range["min"] = min(float(tp_range.get("min", 1.0)), physics["tp_atr_min"])
        rec_tp_max = float(physics.get("tp_atr_max", tp_range.get("user_min", current_min) + 2.0))
        if float(tp_range.get("user_max", 5.0)) <= tp_range.get("user_min", current_min):
            tp_range["user_max"] = max(rec_tp_max, round(tp_range.get("user_min", current_min) + 1.5, 1))

    # 2. Stop Loss (SL) 하한치 및 상한치 보정
    sl_range = optuna_cfg.get("stop_loss_range", {})
    if sl_range:
        current_min = float(sl_range.get("user_min", sl_range.get("min", 0.5)))
        if current_min < physics["sl_atr_min"]:
            sl_range["user_min"] = physics["sl_atr_min"]
            sl_range["min"] = min(float(sl_range.get("min", 0.5)), physics["sl_atr_min"])
        rec_sl_max = float(physics.get("sl_atr_max", sl_range.get("user_min", current_min) + 1.5))
        if float(sl_range.get("user_max", 3.0)) <= sl_range.get("user_min", current_min):
            sl_range["user_max"] = max(rec_sl_max, round(sl_range.get("user_min", current_min) + 1.0, 1))

    # 3. 예측 호라이즌 (Predict Ahead Candles) 하한치 및 상한치 보정
    min_h = int(physics["min_horizon_candles"])
    rec_h = int(physics.get("rec_horizon_candles", min_h * 2))
    pa_range = optuna_cfg.get("predict_ahead_candles_range", {})
    if pa_range:
        current_min = int(pa_range.get("user_min", pa_range.get("min", 4)))
        if current_min < min_h:
            pa_range["user_min"] = min_h
        if int(pa_range.get("user_max", 12)) <= pa_range.get("user_min", current_min):
            pa_range["user_max"] = max(rec_h, pa_range.get("user_min", current_min) + 4)

    # 4. 최대 보유 캔들 수 (Max Hold Candles) — 충분한 익절 도달 시간 보장!
    max_h = int(physics.get("max_hold_candles", min_h * 3))
    hold_range = optuna_cfg.get("max_hold_candles_range", {})
    if hold_range:
        current_min = int(hold_range.get("user_min", hold_range.get("min", 4)))
        if current_min < min_h:
            hold_range["user_min"] = min_h
        if int(hold_range.get("user_max", 12)) <= hold_range.get("user_min", current_min):
            hold_range["user_max"] = max(max_h, hold_range.get("user_min", current_min) * 2)

    return config
