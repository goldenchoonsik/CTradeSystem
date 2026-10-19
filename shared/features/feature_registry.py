"""
CAiModelBaker - Feature Registry (Plugin Architecture v2)

[플러그인 구조 & 멀티 에셋 표준화]
새 지표 추가 방법:
  1. _compute_XXX(df, params, interval) 함수를 아래에 작성
  2. DEFAULT_FEATURE_REGISTRY에 항목 추가 + 'compute': _compute_XXX 등록
  → model_engine, feature_tuner는 레지스트리를 순회하므로 추가 수정 불필요

명명 규칙 표준:
  - target_*: 분석/매매 대상 자산 (ETF, 주식, 가상자산 공통)
  - binance_*, crypto_*: 해외 선행/가상자산 특화 지표
  - kospi_*, etf_*, krx_*: 국내 증시/ETF 특화 지표
  - 레거시(upbit_*) ID 호출 시 FEATURE_ALIASES를 통해 자동 정규화 지원
"""
import json
import logging
import os
import re
import numpy as np
import pandas as pd


# ==============================================================================
# 컬럼 접근 헬퍼 함수 (Tri-Resolution Helper)
# 1. target_* 신규 컬럼 우선 조회
# 2. Normalized Store 순수 물리 컬럼(close, open, vwap 등) 1급 시민 자동 매핑
# 3. upbit_* 레거시 컬럼 폴백
# ==============================================================================

_NORMALIZED_STD_COLS = {
    "target_close": "close",
    "target_open": "open",
    "target_high": "high",
    "target_low": "low",
    "target_volume": "volume",
    "target_trade_amount": "trade_amount",
    "target_trade_count": "trade_count",
    "target_bid_volume": "bid_volume",
    "target_ask_volume": "ask_volume",
    "target_cvd": "cvd",
    "target_spread": "spread",
    "target_mid_price": "mid_price",
    "target_vwap": "vwap",
    "target_ofi": "ofi",
    "target_rv": "rv",
    "target_is_large_trade": "is_large_trade",
    "target_is_auction": "is_auction",
}


def _get_series(df: pd.DataFrame, target_col: str, fallback_col: str = None, default=None) -> pd.Series:
    """
    컬럼 접근 헬퍼 함수:
    1. target_col 직접 조회
    2. Normalized Store 표준 물리 컬럼 조회 (_NORMALIZED_STD_COLS)
    3. fallback_col 직접 조회
    4. fallback_col의 표준 물리 컬럼 조회
    5. default 값 반환 (부재 시 NaN Series)
    """
    if target_col in df.columns:
        return df[target_col]

    std_col = _NORMALIZED_STD_COLS.get(target_col)
    if std_col and std_col in df.columns:
        return df[std_col]

    if fallback_col and fallback_col in df.columns:
        return df[fallback_col]

    if fallback_col:
        fallback_std = _NORMALIZED_STD_COLS.get(fallback_col)
        if fallback_std and fallback_std in df.columns:
            return df[fallback_std]

    if default is not None:
        if isinstance(default, pd.Series):
            return default
        return pd.Series(default, index=df.index)
    return pd.Series(np.nan, index=df.index)


def _get_interval_minutes(interval_str: str) -> float:
    """인터벌 문자열(예: '10m', '15m', '1h', '10s')을 분(Minutes) 단위로 파싱"""
    s = str(interval_str).strip().lower()
    if s.endswith('s') and not s.endswith('ms'):
        sec = float(''.join(filter(str.isdigit, s)) or 10.0)
        return max(0.01666, sec / 60.0)
    elif s.endswith('m') and not s.endswith('month'):
        m = float(''.join(filter(str.isdigit, s)) or 1.0)
        return max(1.0, m)
    elif s.endswith('h'):
        h = float(''.join(filter(str.isdigit, s)) or 1.0)
        return max(1.0, h * 60.0)
    elif s.endswith('d'):
        d = float(''.join(filter(str.isdigit, s)) or 1.0)
        return max(1.0, d * 1440.0)
    return 1.0


def _get_window_bars(params: dict, default_bars: int = 5, default_mins: float = None, interval: str = '1m') -> int:
    """
    물리적 시간/수급 지표용 파라미터 파서:
    1. 'window_mins' (신규 표준): 인터벌로 나누어 현재 캔들 수 자동 도출
    2. 'window_bars' (봉수 명시 표준)
    3. 'candles' 또는 'window' (레거시 호환)
    4. default_mins (기본 분) 제공 시 인터벌 변환, 부재 시 default_bars
    """
    if params:
        if 'window_mins' in params:
            mins = float(params['window_mins'])
            return max(1, round(mins / _get_interval_minutes(interval)))
        if 'window_bars' in params:
            return max(1, int(params['window_bars']))
        if 'candles' in params:
            return max(1, int(params['candles']))
        if 'window' in params:
            w = float(params['window'])
            if w >= 60:
                return max(1, round(w / _get_interval_minutes(interval)))
            return max(1, int(w))
    if default_mins is not None:
        return max(1, round(default_mins / _get_interval_minutes(interval)))
    return max(1, int(default_bars))


def _get_period_bars(params: dict, default_bars: int = 14) -> int:
    """
    통계적 표본 지표(RSI, 볼밴 등)용 파라미터 파서:
    1. 'period_bars' (신규 표준)
    2. 'period', 'period_candles' (레거시 호환)
    3. default_bars
    """
    if params:
        if 'period_bars' in params:
            return max(1, int(params['period_bars']))
        if 'period' in params:
            return max(1, int(params['period']))
        if 'period_candles' in params:
            return max(1, int(params['period_candles']))
    return max(1, int(default_bars))


def _get_period_days(params: dict, default_days: int = 14) -> int:
    """거시 세션 지표용 파라미터 파서"""
    if params:
        if 'period_days' in params:
            return max(1, int(params['period_days']))
    return max(1, int(default_days))


# ==============================================================================
# 지표 계산 함수 (Compute Functions)
# 서명: (df: pd.DataFrame, params: dict, interval: str) -> pd.Series
# ==============================================================================

def _compute_binance_ret_5m(df, params, interval):
    """바이낸스 N캔들 수익률 — 해외 선행 모멘텀"""
    candles = _get_window_bars(params, default_bars=5, default_mins=15, interval=interval)
    return df.get('binance_spot_close', pd.Series(0.0, index=df.index)).pct_change(candles).fillna(0.0)


def _compute_nvda_futures_ret_curr(df, params, interval):
    """엔비디아 24시간 선물 N캔들 수익률 — 글로벌 반도체 선행 모멘텀"""
    candles = _get_window_bars(params, default_bars=5, default_mins=25, interval=interval)
    close = df.get('nvda_futures_close', pd.Series(np.nan, index=df.index)).astype(float)
    if close.isna().all():
        return pd.Series(0.0, index=df.index)
    return close.pct_change(candles).fillna(0.0)


def _compute_us10y_futures_ret_curr(df, params, interval):
    """미국 10년물 국채 선물 N캔들 변동률 — 시장금리 충격 모멘텀"""
    candles = _get_window_bars(params, default_bars=5, default_mins=25, interval=interval)
    close = df.get('us10y_futures_close', pd.Series(np.nan, index=df.index)).astype(float)
    if close.isna().all():
        return pd.Series(0.0, index=df.index)
    return close.pct_change(candles).fillna(0.0)


def _compute_wti_futures_ret_curr(df, params, interval):
    """WTI 원유 선물 N캔들 수익률 — 에너지 인플레 및 지정학적 리스크"""
    candles = _get_window_bars(params, default_bars=5, default_mins=25, interval=interval)
    close = df.get('wti_futures_close', pd.Series(np.nan, index=df.index)).astype(float)
    if close.isna().all():
        return pd.Series(0.0, index=df.index)
    return close.pct_change(candles).fillna(0.0)


def _compute_gold_futures_ret_curr(df, params, interval):
    """골드(금) 선물 N캔들 수익률 — 안전자산 자금 도피(Risk-Off) 지표"""
    candles = _get_window_bars(params, default_bars=5, default_mins=25, interval=interval)
    close = df.get('gold_futures_close', pd.Series(np.nan, index=df.index)).astype(float)
    if close.isna().all():
        return pd.Series(0.0, index=df.index)
    return close.pct_change(candles).fillna(0.0)


def _compute_usdkrw_ret_curr(df, params, interval):
    """원/달러 실시간 환율 N캔들 변동률 — 외국인 수급 충격 지표"""
    candles = _get_window_bars(params, default_bars=5, default_mins=25, interval=interval)
    close = df.get('usd_krw_fx', pd.Series(np.nan, index=df.index)).astype(float)
    if close.isna().all():
        return pd.Series(0.0, index=df.index)
    return close.pct_change(candles).fillna(0.0)


# ==============================================================================
# [동적 참조 대장주 선행 피처 4대 제네릭 함수]
# 문자열 프리캐싱을 통해 런타임 f-string 힙 할당 비용을 0으로 최적화
# ==============================================================================

# [T-6] 참조 계열이 아직 없는 구간은 **NaN 으로 남긴다** — 0 으로 채우지 않는다.
#
# 0 으로 채우면 "참조 종목이 전혀 안 움직였다"는 **거짓 관측**이 되어 모델이 그대로 학습한다.
# 특히 spread_vs_target 은 ref_ret=0 이 되는 순간 `-(타겟 수익률)` 로 퇴화해, 죽은 구간에서도
# 값이 살아 있는 것처럼 보이면서 실제로는 전혀 다른 지표가 된다 (2026-09-28 실측: 069500 스냅샷
# 앞 20거래일에서 ref_000660_spread_vs_target 이 고유값 37,157개 — 전부 타겟 자신의 반전이었다).
#
# LightGBM 은 NaN 을 결측으로 그대로 다루고, 학습 파이프라인은 target 이 NaN 인 행만 버리므로
# (model_engine 의 dropna(subset=['target'])) 참조가 없는 구간도 **행은 살아남고 그 피처만 빠진다**.
# 데이터가 부족할 때 구간을 통째로 버리지 않고 쓰는 방법이 바로 이것이다.


def _compute_ref_ret_1c(df, params, interval, col_close=None):
    """참조 대장주 1캔들 종가 수익률 (즉각 속도/모멘텀). 참조가 없으면 NaN."""
    if col_close is None or col_close not in df.columns:
        return pd.Series(np.nan, index=df.index)
    close = df[col_close].astype(float)
    return close.pct_change(1)


def _compute_ref_ret_5c(df, params, interval, col_close=None):
    """참조 대장주 5캔들 누적 수익률 (단기 추세 방향). 참조가 없으면 NaN."""
    if col_close is None or col_close not in df.columns:
        return pd.Series(np.nan, index=df.index)
    candles = _get_window_bars(params, default_bars=5, default_mins=25, interval=interval)
    close = df[col_close].astype(float)
    return close.pct_change(candles)


def _compute_ref_spread_vs_target(df, params, interval, col_close=None):
    """참조 대장주 1봉 수익률 - 타겟 ETF 1봉 수익률 (선행 괴리율/스프레드). 참조가 없으면 NaN."""
    if col_close is None or col_close not in df.columns:
        return pd.Series(np.nan, index=df.index)
    target_close = _get_series(df, 'target_close', 'upbit_close', np.nan).astype(float)
    ref_close = df[col_close].astype(float)
    return ref_close.pct_change(1) - target_close.pct_change(1)


def _compute_ref_intensity(df, params, interval, col_bid_vol=None, col_vol=None):
    """참조 대장주 실시간 매수 체결비율 (가짜 돌파 필터링). 참조가 없으면 NaN."""
    if col_vol is None or col_vol not in df.columns:
        return pd.Series(np.nan, index=df.index)
    vol = df[col_vol].astype(float)
    if col_bid_vol is None or col_bid_vol not in df.columns:
        return pd.Series(np.nan, index=df.index)
    bid_vol = df[col_bid_vol].astype(float)
    # 거래가 없던 봉(거래량 0)은 '체결강도 없음'이지 '매수 0%' 가 아니다 → NaN
    return (bid_vol / vol.where(vol > 0)).clip(0.0, 1.0)


def _compute_kimchi_premium(df, params, interval):
    """김치 프리미엄 — 국내/해외 가격 괴리율
    KRW-USDT 실거래가가 있으면 이를 직접 사용하고,
    없으면 롤링 역산 환율로 폴백하여 하위 호환성을 보장합니다."""
    window_mins = float(params.get('window_mins', params.get('window', 1440)))
    window_bars = max(1, round(window_mins / _get_interval_minutes(interval)))
    target_close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    binance_close = df.get('binance_spot_close', pd.Series(0.0, index=df.index)).astype(float)
    usdt_krw = _get_series(df, 'crypto_usdt_close', 'upbit_usdt_close', np.nan).astype(float)
    
    valid_mask = usdt_krw.notna() & (usdt_krw > 0)
    # 역산 환율 (폴백용): 롤링 평균으로 안정화
    approx_usdkrw = (target_close / (binance_close + 1e-8)).rolling(window_bars, min_periods=1).mean()
    # 실제 KRW-USDT 데이터가 있는 구간은 직접 사용, 없으면 역산값 사용
    effective_rate = usdt_krw.where(valid_mask, approx_usdkrw)
    return (target_close / (binance_close * effective_rate + 1e-8)) - 1.0


def _compute_target_ma_ratio(df, params, interval):
    """타겟 이격도 — 현재가 vs N캔들 이동평균 괴리율"""
    period = _get_period_bars(params, default_bars=20)
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    return close / (close.rolling(period, min_periods=1).mean() + 1e-8) - 1.0


def _compute_target_rsi_daily(df, params, interval):
    """실제 일봉 기준 종가를 추출하여 14일 일봉 RSI를 계산하고 병합"""
    period_days = _get_period_days(params, default_days=14)
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    
    # 1. 일별 마지막 종가 추출
    temp_df = pd.DataFrame({'timestamp': pd.to_datetime(df['timestamp']), 'close': close}, index=df.index)
    temp_df['date'] = temp_df['timestamp'].dt.date
    
    daily_df = temp_df.groupby('date').last().reset_index()
    daily_df = daily_df.sort_values('date').reset_index(drop=True)
    
    # 2. 일별 데이터 기준으로 RSI 계산
    diff = daily_df['close'].diff()
    u = diff.clip(lower=0)
    d = -diff.clip(upper=0)
    
    min_periods = min(5, period_days)
    rs = u.rolling(window=period_days, min_periods=min_periods).mean() / \
         (d.rolling(window=period_days, min_periods=min_periods).mean() + 1e-8)
    daily_df['rsi_daily'] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)
    
    # 📌 [Look-ahead bias 해결]: 오늘(date)의 피처로는 '어제(T-1)' 최종 결정된 일봉 RSI 값을 맵핑합니다.
    daily_df['rsi_daily_shifted'] = daily_df['rsi_daily'].shift(1).fillna(50.0)
    
    # 3. 원래 df에 날짜(date) 기준으로 매핑
    date_to_rsi = daily_df.set_index('date')['rsi_daily_shifted'].to_dict()
    return temp_df['date'].map(date_to_rsi).fillna(50.0)


def _compute_target_trade_bid_ratio(df, params, interval):
    """타겟 매수 체결 비율 — N캔들 롤링 평균 스무딩"""
    window = _get_window_bars(params, default_bars=1, default_mins=10, interval=interval)
    bid_ratio = _get_series(df, 'target_trade_bid_ratio', 'upbit_trade_bid_ratio', 0.5).astype(float)
    return bid_ratio.rolling(window, min_periods=1).mean().fillna(0.5)


def _compute_binance_spot_taker_ratio(df, params, interval):
    """바이낸스 Taker Buy 비율 — N캔들 롤링 평균 스무딩"""
    window = _get_window_bars(params, default_bars=1, default_mins=10, interval=interval)
    return df.get('binance_spot_taker_ratio', pd.Series(0.5, index=df.index)).astype(float).rolling(window, min_periods=1).mean().fillna(0.5)


def _compute_binance_oi_change_ratio(df, params, interval):
    """바이낸스 선물 미결제 약정 단기 변화율 — N캔들 전 대비 변화율"""
    candles = _get_window_bars(params, default_bars=3, default_mins=30, interval=interval)
    oi = df.get('binance_futures_oi', pd.Series(0.0, index=df.index)).astype(float)
    # 선두 결측은 미래값(bfill)이 아닌 NaN 유지 → 결과 fillna(0.0) 로 중립 처리
    oi_clean = oi.replace(0.0, np.nan).ffill()
    prev_oi = oi_clean.shift(candles)
    ratio = ((oi_clean - prev_oi) / (prev_oi + 1e-8)).fillna(0.0)
    return ratio.replace([np.inf, -np.inf], 0.0)


def _compute_binance_oi_ma_ratio(df, params, interval):
    """바이낸스 선물 미결제 약정 이격도 — 현재 미결제약정 / N캔들 이동평균"""
    period = _get_period_bars(params, default_bars=120)
    oi = df.get('binance_futures_oi', pd.Series(0.0, index=df.index)).astype(float)
    oi_masked = oi.replace(0.0, np.nan).ffill()  # 선두는 bfill 하지 않음 (미래값 누수 방지)
    ma = oi_masked.rolling(period, min_periods=1).mean()
    ratio = ((oi / (ma.fillna(oi) + 1e-8)) - 1.0).where(oi_masked.notna(), 0.0)
    return ratio.fillna(0.0).replace([np.inf, -np.inf], 0.0)


def _compute_binance_cvd_momentum(df, params, interval):
    """바이낸스 현물 CVD 단기 모멘텀 (정규화된 최근 N캔들 순수시장가매수 비율)"""
    candles = _get_window_bars(params, default_bars=3, default_mins=30, interval=interval)
    vol = df.get('binance_spot_volume', pd.Series(0.0, index=df.index)).astype(float)
    taker_ratio = df.get('binance_spot_taker_ratio', pd.Series(0.5, index=df.index)).astype(float)
    delta = vol * (2.0 * taker_ratio - 1.0)
    
    rolling_delta = delta.rolling(window=candles, min_periods=1).sum()
    rolling_vol = vol.rolling(window=candles, min_periods=1).sum()
    return (rolling_delta / (rolling_vol + 1e-8)).fillna(0.0)


def _compute_target_cvd_momentum(df, params, interval):
    """타겟 CVD 단기 모멘텀 (정규화된 최근 N캔들 순수시장가매수 비율)"""
    candles = _get_window_bars(params, default_bars=3, default_mins=30, interval=interval)
    vol = _get_series(df, 'target_volume', 'upbit_volume', 0.0).astype(float)
    bid_ratio = _get_series(df, 'target_trade_bid_ratio', 'upbit_trade_bid_ratio', 0.5).astype(float)
    delta = vol * (2.0 * bid_ratio - 1.0)
    
    rolling_delta = delta.rolling(window=candles, min_periods=1).sum()
    rolling_vol = vol.rolling(window=candles, min_periods=1).sum()
    return (rolling_delta / (rolling_vol + 1e-8)).fillna(0.0)


def _compute_binance_cvd_divergence(df, params, interval):
    """바이낸스 CVD 장단기 다이버전스 (단기 fast캔들 vs 장기 slow캔들 CVD 차이)"""
    fast = _get_period_bars({'period_bars': params.get('fast_period', 15)} if params and 'fast_period' in params else params, default_bars=15)
    slow = _get_period_bars({'period_bars': params.get('slow_period', 120)} if params and 'slow_period' in params else params, default_bars=120)
    if slow <= fast:
        slow = fast + 10
        
    vol = df.get('binance_spot_volume', pd.Series(0.0, index=df.index)).astype(float)
    taker_ratio = df.get('binance_spot_taker_ratio', pd.Series(0.5, index=df.index)).astype(float)
    delta = vol * (2.0 * taker_ratio - 1.0)
    
    cvd = delta.cumsum()
    fast_ma = cvd.rolling(window=fast, min_periods=1).mean()
    slow_ma = cvd.rolling(window=slow, min_periods=1).mean()
    rolling_vol = vol.rolling(window=slow, min_periods=1).sum()
    return ((fast_ma - slow_ma) / (rolling_vol + 1e-8)).fillna(0.0)


def _compute_binance_usdc_volume_share(df, params, interval):
    """바이낸스 USDT 대비 USDC 마켓 거래량 점유율 (최근 N캔들 롤링 평균 점유율)"""
    candles = _get_window_bars(params, default_bars=15, default_mins=60, interval=interval)
    usdt_vol = df.get('binance_spot_volume', pd.Series(0.0, index=df.index)).astype(float)
    usdc_vol = df.get('binance_usdc_volume', pd.Series(0.0, index=df.index)).astype(float)
    
    rolling_usdt = usdt_vol.rolling(window=candles, min_periods=1).sum()
    rolling_usdc = usdc_vol.rolling(window=candles, min_periods=1).sum()
    return (rolling_usdc / (rolling_usdt + rolling_usdc + 1e-8))


def _compute_binance_funding_rate(df, params, interval):
    """바이낸스 펀딩비 — 선물시장 롱/숏 포지션 청산가 (8시간 간격 데이터, ffill 보정)"""
    mode = params.get('mode', 'raw')
    fr = df.get('binance_funding_rate', pd.Series(0.0, index=df.index))
    fr_clean = fr.astype(float).replace(0.0, np.nan).ffill().fillna(0.0)  # 선두는 중립(0)

    if mode == 'raw':
        return (fr_clean * 100.0).fillna(0.0)
    elif mode == 'zscore':
        window = _get_period_bars({'period_bars': params.get('window', 480)} if params and 'window' in params else params, default_bars=480)
        ma = fr_clean.rolling(window, min_periods=20).mean()
        std = fr_clean.rolling(window, min_periods=20).std().replace(0.0, np.nan)
        z = ((fr_clean - ma) / (std + 1e-8)).fillna(0.0)
        return z.replace([np.inf, -np.inf], 0.0)
    elif mode == 'mom':
        candles = _get_window_bars(params, default_bars=16, default_mins=480, interval=interval)
        return (fr_clean - fr_clean.shift(candles)).fillna(0.0) * 1000.0
    return (fr_clean * 100.0).fillna(0.0)


def _compute_binance_futures_spot_vol_ratio(df, params, interval):
    """바이낸스 선물 대 현물 거래량 비율 (레버리지 시장의 활성 강도 포착)"""
    candles = _get_window_bars(params, default_bars=15, default_mins=60, interval=interval)
    f_vol = df.get('binance_futures_volume', pd.Series(0.0, index=df.index)).astype(float)
    s_vol = df.get('binance_spot_volume', pd.Series(0.0, index=df.index)).astype(float)
    
    rolling_f = f_vol.rolling(window=candles, min_periods=1).mean()
    rolling_s = s_vol.rolling(window=candles, min_periods=1).mean()
    return (rolling_f / (rolling_s + 1e-8)).fillna(1.0)


def _compute_target_volatility_ratio(df, params, interval):
    """단기/장기 ATR 비율을 활용한 변동성 정국(Regime) 식별기"""
    fast_period = _get_period_bars({'period_bars': params.get('fast_period', 5)} if params and 'fast_period' in params else params, default_bars=5)
    slow_period = _get_period_bars({'period_bars': params.get('slow_period', 30)} if params and 'slow_period' in params else params, default_bars=30)
    
    atr = _get_series(df, 'target_atr', 'upbit_atr', np.nan).astype(float)
    if atr.isna().all():
        high = _get_series(df, 'target_high', 'upbit_high', 0.0).astype(float)
        low = _get_series(df, 'target_low', 'upbit_low', 0.0).astype(float)
        close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
        prev_close = close.shift(1).fillna(close)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=1).mean()

    atr_clean = atr.replace(0.0, np.nan).ffill()  # 선두 NaN 유지 (rolling 이 무시, 최종 fillna(1.0) 중립)
    fast_atr = atr_clean.rolling(window=fast_period, min_periods=1).mean()
    slow_atr = atr_clean.rolling(window=slow_period, min_periods=1).mean()
    return (fast_atr / (slow_atr + 1e-8)).fillna(1.0)


def _compute_target_hour(df, params, interval):
    """시간대 범주형 피처 (0~23)"""
    return pd.to_datetime(df['timestamp']).dt.hour.astype(int)


def _compute_target_hour_sin(df, params, interval):
    """24시간 원형 싸인(Sin) 주기 인코딩 (-1.0 ~ +1.0) — 23:59와 00:00의 연속성 보장"""
    ts = pd.to_datetime(df['timestamp'])
    hour_float = ts.dt.hour + ts.dt.minute / 60.0 + ts.dt.second / 3600.0
    return pd.Series(np.sin(2.0 * np.pi * hour_float / 24.0), index=df.index).astype(float)


def _compute_target_hour_cos(df, params, interval):
    """24시간 원형 코사인(Cos) 주기 인코딩 (-1.0 ~ +1.0) — 23:59와 00:00의 연속성 보장"""
    ts = pd.to_datetime(df['timestamp'])
    hour_float = ts.dt.hour + ts.dt.minute / 60.0 + ts.dt.second / 3600.0
    return pd.Series(np.cos(2.0 * np.pi * hour_float / 24.0), index=df.index).astype(float)


def _compute_krx_session_progress(df, params, interval):
    """한국거래소 정규장 진행율 (09:00=0.0 ~ 15:30=1.0) — 분 단위 선형 장중 위치"""
    ts = pd.to_datetime(df['timestamp'])
    current_min = ts.dt.hour * 60 + ts.dt.minute
    progress = (current_min - 540) / 390.0  # 09:00(540)부터 15:30(930)까지 총 390분
    return progress.clip(0.0, 1.0).astype(float)


def _compute_krx_opening_decay(df, params, interval):
    """09:00 개장 시초가 변동성 지수 감쇠 지표 (09:00=1.0 → 10:00≈0.05)"""
    ts = pd.to_datetime(df['timestamp'])
    current_min = ts.dt.hour * 60 + ts.dt.minute
    elapsed = (current_min - 540).clip(lower=0)
    decay = np.exp(-elapsed / 20.0)
    return pd.Series(decay, index=df.index).clip(0.0, 1.0).astype(float)


def _compute_target_is_weekend(df, params, interval):
    """주말 여부 이진 피처 (토/일=1, 평일=0)"""
    return (pd.to_datetime(df['timestamp']).dt.dayofweek >= 5).astype(int)


def _compute_target_rsi_curr(df, params, interval):
    """현재 분봉(Interval) 기준 단기 RSI — 과매수/과매도 오실레이터"""
    rsi_win = _get_period_bars(params, default_bars=14)
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    diff_curr = close.diff()
    u_curr = diff_curr.clip(lower=0)
    d_curr = -diff_curr.clip(upper=0)
    rs_curr = u_curr.rolling(rsi_win, min_periods=1).mean() / (d_curr.rolling(rsi_win, min_periods=1).mean() + 1e-8)
    return (100.0 - (100.0 / (1.0 + rs_curr))).fillna(50.0)


def _compute_target_atr_ratio(df, params, interval):
    """ATR 변동성 비율 — 실제 변동폭 / 종가 (횡보장 감지)"""
    atr_win = _get_period_bars(params, default_bars=14)
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    
    atr = _get_series(df, 'target_atr', 'upbit_atr', np.nan).astype(float)
    if not atr.isna().all():
        return (atr / (close + 1e-8)).fillna(0.0)
        
    high = _get_series(df, 'target_high', 'upbit_high', np.nan).astype(float)
    low = _get_series(df, 'target_low', 'upbit_low', np.nan).astype(float)
    if not high.isna().all() and not low.isna().all():
        close_prev = close.shift(1).fillna(close)
        tr = pd.concat([high - low, (high - close_prev).abs(), (low - close_prev).abs()], axis=1).max(axis=1)
        atr_calc = tr.rolling(atr_win, min_periods=1).mean()
        return (atr_calc / (close + 1e-8)).fillna(0.0)
    else:
        std_val = close.rolling(atr_win, min_periods=1).std()
        return (std_val / (close + 1e-8)).fillna(0.0)


def _compute_target_adx(df, params, interval):
    """ADX 추세 강도 지표 (0~100) — 추세/횡보 구분"""
    adx_win = _get_period_bars(params, default_bars=14)
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    high = _get_series(df, 'target_high', 'upbit_high', np.nan).astype(float)
    low = _get_series(df, 'target_low', 'upbit_low', np.nan).astype(float)
    
    if not high.isna().all() and not low.isna().all():
        close_prev = close.shift(1).fillna(close)
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr = pd.concat([high - low, (high - close_prev).abs(), (low - close_prev).abs()], axis=1).max(axis=1)
        tr_smooth = tr.rolling(adx_win, min_periods=1).mean()
        plus_dm_smooth = pd.Series(plus_dm, index=df.index).rolling(adx_win, min_periods=1).mean()
        minus_dm_smooth = pd.Series(minus_dm, index=df.index).rolling(adx_win, min_periods=1).mean()
        plus_di = 100.0 * (plus_dm_smooth / (tr_smooth + 1e-8))
        minus_di = 100.0 * (minus_dm_smooth / (tr_smooth + 1e-8))
        dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-8))
        return dx.rolling(adx_win, min_periods=1).mean().fillna(20.0)
    else:
        return pd.Series(20.0, index=df.index)


def _compute_target_bb_pctb(df, params, interval):
    """볼린저 밴드 %B — 현재 가격의 밴드 내 상대적 위치 (0~1)"""
    period = _get_period_bars(params, default_bars=20)
    std_dev = params.get('std_dev', 2) if params else 2
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    ma = close.rolling(period, min_periods=1).mean()
    std = close.rolling(period, min_periods=1).std()
    upper = ma + std_dev * std
    lower = ma - std_dev * std
    band_width = upper - lower
    pctb = (close - lower) / band_width.replace(0.0, np.nan)
    return pctb.fillna(0.5)


def _compute_target_macd_hist(df, params, interval):
    """MACD 히스토그램 — 추세 전환 속도 가속/감속 탐지"""
    fast = _get_period_bars({'period_bars': params.get('fast_period', 12)} if params and 'fast_period' in params else params, default_bars=12)
    slow = _get_period_bars({'period_bars': params.get('slow_period', 26)} if params and 'slow_period' in params else params, default_bars=26)
    if slow <= fast:
        slow = fast + 5
    signal = _get_period_bars({'period_bars': params.get('signal_period', 9)} if params and 'signal_period' in params else params, default_bars=9)
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    return (macd - macd_signal).fillna(0.0)


def _compute_target_mfi(df, params, interval):
    """자금 흐름 지수 (MFI) — 거래량이 동반된 진짜 가격 변동 세기 (0~100)"""
    period = _get_period_bars(params, default_bars=14)
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    volume = _get_series(df, 'target_volume', 'upbit_volume', 0.0).astype(float)
    high = _get_series(df, 'target_high', 'upbit_high', close).astype(float)
    low = _get_series(df, 'target_low', 'upbit_low', close).astype(float)
        
    tp = (high + low + close) / 3.0
    rmf = tp * volume
    
    tp_diff = tp.diff()
    pmf = np.where(tp_diff > 0, rmf, 0.0)
    nmf = np.where(tp_diff < 0, rmf, 0.0)
    
    pmf_sum = pd.Series(pmf, index=df.index).rolling(period, min_periods=1).sum()
    nmf_sum = pd.Series(nmf, index=df.index).rolling(period, min_periods=1).sum()
    
    mr = pmf_sum / (nmf_sum + 1e-8)
    mfi = 100.0 - (100.0 / (1.0 + mr))
    return mfi.fillna(50.0)


def _compute_target_trade_frequency(df, params, interval):
    """체결 빈도 — 봉당 체결 건수 (시장 참여 활성도)"""
    return _get_series(df, 'target_trade_count', 'upbit_trade_count', 0.0).astype(float).fillna(0.0)


def _compute_target_large_trade_ratio(df, params, interval):
    """대량 체결 비율 — 전체 체결 중 대량(≥1억원) 체결 비율 (세력 매집/매도 감지)"""
    tc = _get_series(df, 'target_trade_count', 'upbit_trade_count', 0.0).astype(float).fillna(0.0)
    lc = _get_series(df, 'target_large_trade_count', 'upbit_large_trade_count', 0.0).astype(float).fillna(0.0)
    return (lc / (tc + 1e-8)).fillna(0.0)


def _compute_target_avg_trade_size_change(df, params, interval):
    """평균 체결 크기 변화율 — N캔들 전 대비 평균 체결 금액 변화 (세력 진입 감지)"""
    candles = _get_window_bars(params, default_bars=5, default_mins=20, interval=interval)
    size = _get_series(df, 'target_avg_trade_size', 'upbit_avg_trade_size', 0.0).astype(float).fillna(0.0)
    prev = size.shift(candles)
    ratio = ((size - prev) / (prev + 1e-8)).fillna(0.0)
    return ratio.clip(-100.0, 100.0).replace([np.inf, -np.inf], 0.0)


def _compute_binance_oi_value_ma_ratio(df, params, interval):
    """미결제약정 가치 이격도 — 현재 OI 달러 가치 / N캔들 이동평균"""
    period = _get_period_bars(params, default_bars=120)
    oi_val = df.get('binance_futures_oi_value', pd.Series(0.0, index=df.index)).astype(float)
    oi_masked = oi_val.replace(0.0, np.nan).ffill()  # 선두는 bfill 하지 않음 (미래값 누수 방지)
    ma = oi_masked.rolling(period, min_periods=1).mean()
    ratio = ((oi_val / (ma.fillna(oi_val) + 1e-8)) - 1.0).where(oi_masked.notna(), 0.0)
    return ratio.fillna(0.0).replace([np.inf, -np.inf], 0.0)


def _compute_binance_leading_momentum(df, params, interval):
    """바이낸스 주도 모멘텀 — 해외(바이낸스) 수익률 - 국내(타겟) 수익률 괴리"""
    candles = _get_window_bars(params, default_bars=2, default_mins=20, interval=interval)
    binance_ret = df.get('binance_spot_close', pd.Series(0.0, index=df.index)).pct_change(candles).fillna(0.0)
    target_close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    target_ret = target_close.pct_change(candles).fillna(0.0)
    return (binance_ret - target_ret).fillna(0.0)


def _compute_target_volume_spike(df, params, interval):
    """거래량 급증 지표 — 현재 거래량 / N캔들 평균 거래량 (돌파 동반 급증 감지)"""
    period = _get_period_bars(params, default_bars=20)
    vol = _get_series(df, 'target_volume', 'upbit_volume', 0.0).astype(float).fillna(0.0)
    ma = vol.rolling(period, min_periods=1).mean()
    return (vol / (ma + 1e-8)).fillna(1.0).replace([np.inf, -np.inf], 1.0)


def _compute_target_candle_body_ratio(df, params, interval):
    """캔들 실체 비율 — |종가-시가| / (고가-저가) 시장 참여자 확신도"""
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    open_ = _get_series(df, 'target_open', 'upbit_open', close.shift(1).fillna(close)).astype(float)
    high = _get_series(df, 'target_high', 'upbit_high', close).astype(float)
    low = _get_series(df, 'target_low', 'upbit_low', close).astype(float)
    
    valid_mask = (high > 0) & (low > 0) & (high >= low)
    body = (close - open_).abs()
    rng = (high - low).abs()
    ratio = pd.Series(0.5, index=df.index)
    ratio.loc[valid_mask] = (body / (rng + 1e-8)).loc[valid_mask]
    return ratio.clip(0.0, 1.0).fillna(0.5)


def _compute_target_obv_momentum(df, params, interval):
    """OBV 모멘텀 — 누적 매수-매도 체결량(OBV)의 N캔들 변화 (정규화 -1.0 ~ 1.0)"""
    candles = _get_window_bars(params, default_bars=5, default_mins=30, interval=interval)
    bid_ratio = _get_series(df, 'target_trade_bid_ratio', 'upbit_trade_bid_ratio', 0.5).astype(float).fillna(0.5)
    vol = _get_series(df, 'target_volume', 'upbit_volume', 0.0).astype(float).fillna(0.0)
    delta = vol * (2.0 * bid_ratio - 1.0)
    obv = delta.cumsum()
    change = (obv - obv.shift(candles)).fillna(0.0)
    rolling_abs = delta.abs().rolling(candles, min_periods=1).sum()
    return (change / (rolling_abs + 1e-8)).fillna(0.0)


def _compute_target_trade_price_spike(df, params, interval):
    """타겟 거래 대금 급증 — 현재 봉 대금 / N캔들 평균 대금 (원화 자본 유입 강도)"""
    period = _get_period_bars(params, default_bars=20)
    tp = _get_series(df, 'target_trade_price', 'upbit_trade_price', 0.0).astype(float).fillna(0.0)
    ma = tp.rolling(period, min_periods=1).mean()
    return (tp / (ma + 1e-8)).fillna(1.0).replace([np.inf, -np.inf], 1.0)


def _compute_binance_spot_quote_spike(df, params, interval):
    """바이낸스 달러 대금 급증 — 현재 봉 대금 / N캔들 평균 대금 (해외 자본 이동)"""
    period = _get_period_bars(params, default_bars=20)
    qv = df.get('binance_spot_quote_volume', pd.Series(0.0, index=df.index)).astype(float).fillna(0.0)
    ma = qv.rolling(period, min_periods=1).mean()
    return (qv / (ma + 1e-8)).fillna(1.0).replace([np.inf, -np.inf], 1.0)


def _compute_binance_spot_trade_frequency(df, params, interval):
    """바이낸스 체결 빈도 — 봉당 체결 건수 (해외 시장 참여 활성도)"""
    return df.get('binance_spot_trade_count', pd.Series(0.0, index=df.index)).astype(float).fillna(0.0)


def _compute_binance_spot_taker_quote_ratio(df, params, interval):
    """바이낸스 대금 기준 Taker 매수 비율 — 수량이 아닌 달러 금액 기준 실제 매수 압력"""
    return df.get('binance_spot_taker_quote_ratio', pd.Series(0.5, index=df.index)).astype(float).fillna(0.5).clip(0.0, 1.0)


def _compute_coinbase_premium_index(df, params, interval):
    """코인베이스 프리미엄 지수 — (코인베이스 타겟 종가 - 바이낸스 타겟 종가) / 바이낸스 타겟 종가 괴리율"""
    candles = _get_window_bars(params, default_bars=1, default_mins=10, interval=interval)
    binance_close = df.get('binance_spot_close', pd.Series(np.nan, index=df.index)).astype(float)
    cb_close = df.get('coinbase_spot_close', pd.Series(np.nan, index=df.index)).astype(float)
    # 코인베이스 시세가 0 이하이거나 결측치인 경우 바이낸스 시세로 ffill 대체 (괴리율 0.0% 중립 보장)
    cb_close = cb_close.where((cb_close > 0) & (~cb_close.isna()), binance_close)
    diff_pct = ((cb_close - binance_close) / (binance_close.where(binance_close > 0, 1.0) + 1e-8)).fillna(0.0)
    if candles > 1:
        return diff_pct.rolling(candles, min_periods=1).mean().fillna(0.0)
    return diff_pct.fillna(0.0)


def _compute_eth_btc_relative_momentum(df, params, interval):
    """
    ETH/BTC 상대강도 모멘텀 — 시장 위험선호도(Risk-on) 지표.
    타겟 자산이 BTC, ETH, 또는 기타 알트코인이더라도 항상 올바른 ETH 수익률과 BTC 수익률의 괴리를 계산.
    1. BTC 시세: 'benchmark_btc_close'가 존재하면 최우선 사용, 없을 시 'binance_spot_close'로 폴백.
    2. ETH 시세: 'binance_eth_close'가 존재하면 최우선 사용, 없을 시(타겟이 ETH인 경우) 'binance_spot_close'로 폴백.
    """
    candles = _get_window_bars(params, default_bars=5, default_mins=25, interval=interval)
    
    # BTC 시세 확보: benchmark_btc_close 우선, 없을 시 binance_spot_close
    if 'benchmark_btc_close' in df.columns and df['benchmark_btc_close'].notna().any():
        btc_close = df['benchmark_btc_close'].astype(float)
    else:
        btc_close = df.get('binance_spot_close', pd.Series(np.nan, index=df.index)).astype(float)
        
    # ETH 시세 확보: binance_eth_close 우선, 없을 시 binance_spot_close
    if 'binance_eth_close' in df.columns and df['binance_eth_close'].notna().any():
        eth_close = df['binance_eth_close'].astype(float)
    else:
        eth_close = df.get('binance_spot_close', pd.Series(np.nan, index=df.index)).astype(float)
        
    eth_ret = eth_close.pct_change(candles).fillna(0.0)
    btc_ret = btc_close.pct_change(candles).fillna(0.0)
    return (eth_ret - btc_ret).fillna(0.0)


def _compute_binance_liq_cascade_ratio(df, params, interval):
    """바이낸스 선물 롱/숏 강제 청산 비율 — (롱 청산액 - 숏 청산액) / 총 청산액"""
    window = _get_window_bars(params, default_bars=3, default_mins=30, interval=interval)
    long_liq = df.get('binance_futures_long_liq', pd.Series(0.0, index=df.index)).astype(float).fillna(0.0)
    short_liq = df.get('binance_futures_short_liq', pd.Series(0.0, index=df.index)).astype(float).fillna(0.0)
    long_roll = long_liq.rolling(window, min_periods=1).sum()
    short_roll = short_liq.rolling(window, min_periods=1).sum()
    total_liq = long_roll + short_roll
    ratio = (long_roll - short_roll) / (total_liq + 1e-8)
    return ratio.fillna(0.0).clip(-1.0, 1.0)


def _compute_mtf_ema_distance_4h(df, params, interval):
    """멀티 타임프레임(4시간봉) 200 EMA 이격도 — 상위 프레임 대추세 필터"""
    period = _get_period_bars(params, default_bars=200)
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    
    if interval == '30m':
        multiplier = 8
    elif interval == '1h':
        multiplier = 4
    elif interval == '15m':
        multiplier = 16
    elif interval == '5m':
        multiplier = 48
    elif interval == '1m':
        multiplier = 240
    else:
        multiplier = 8
        
    effective_span = period * multiplier
    ema_4h = close.ewm(span=effective_span, adjust=False).mean()
    return ((close - ema_4h) / (ema_4h + 1e-8)).fillna(0.0)


def _compute_target_bb_kc_squeeze(df, params, interval):
    """볼린저밴드 - 켈트너채널 변동성 수축(Squeeze) 지표 — 밴드 폭 / ATR 비율"""
    bb_period = _get_period_bars(params, default_bars=20)
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    high = _get_series(df, 'target_high', 'upbit_high', close).astype(float)
    low = _get_series(df, 'target_low', 'upbit_low', close).astype(float)
    
    mid = close.rolling(bb_period, min_periods=1).mean()
    std = close.rolling(bb_period, min_periods=1).std().fillna(0.0)
    bb_width = 4.0 * std
    
    prev_close = close.shift(1).fillna(close)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(bb_period, min_periods=1).mean()
    
    squeeze_ratio = (bb_width / (3.0 * atr + 1e-8)) - 1.0
    return squeeze_ratio.fillna(0.0).clip(-3.0, 3.0)


def _compute_target_kaufman_er(df, params, interval):
    """Kaufman 효율성 비율(Efficiency Ratio) — 순 가격 이동거리 / 총 이동거리 합 (0~1)"""
    period = _get_period_bars(params, default_bars=10)
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    change = (close - close.shift(period)).abs()
    volatility = close.diff().abs().rolling(period, min_periods=1).sum()
    er = change / (volatility + 1e-8)
    return er.fillna(0.0).clip(0.0, 1.0)


def _compute_crypto_usdt_krw_ret(df, params, interval):
    """테더(USDT) N캔들 수익률 — KRW 유동성 쏠림 속도"""
    candles = _get_window_bars(params, default_bars=2, default_mins=10, interval=interval)
    usdt = _get_series(df, 'crypto_usdt_close', 'upbit_usdt_close', np.nan).astype(float)
    usdt_clean = usdt.replace(0.0, np.nan).ffill()  # 선두는 NaN 유지 (가짜 1300 기준값/미래값 배제)
    ret = usdt_clean / usdt_clean.shift(candles) - 1.0
    return ret.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-0.05, 0.05)


def _compute_binance_funding_settle_window(df, params, interval):
    """펀딩비 정산까지 남은 30분봉 캔들 수 — 8시간 주기 정산 전후 유동성 패턴 포착"""
    settle_hours_kst = [1, 9, 17]
    def _minutes_to_next_settle(ts):
        current_min = ts.hour * 60 + ts.minute
        distances = [(h * 60 - current_min) % 480 for h in settle_hours_kst]
        return min(distances) // 30
    return pd.to_datetime(df['timestamp']).apply(_minutes_to_next_settle).astype(float)


def _compute_crypto_0900_sync(df, params, interval):
    """09:00 KST 시초가 동기화 지표 — 국내 단독 오버슈팅 vs 해외 추종 구분"""
    target = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    target_op = _get_series(df, 'target_open', 'upbit_open', target).astype(float)
    binance = df.get('binance_spot_close', pd.Series(np.nan, index=df.index)).astype(float)

    ts = pd.to_datetime(df['timestamp'])
    temp = pd.DataFrame({'date': ts.dt.date, 'hour': ts.dt.hour, 'target_open': target_op.values, 'binance_close': binance.values}, index=df.index)

    # 인과적(causal) 앵커: 각 날짜의 첫 09시대 봉에서만 시초가를 확정하고 이후 봉으로만 전파(ffill)한다.
    # (날짜 단위 map 은 00:00~08:59 봉이 당일 09:00의 미래 값을 참조하는 look-ahead 이므로 사용하지 않는다.
    #  09:00 이전 구간은 전일 앵커를 유지하며, 데이터 선두에서 앵커가 없으면 NaN → 최종 0.0 처리)
    is_anchor = ((temp['hour'] == 9) & ~temp['date'].where(temp['hour'] == 9).duplicated()).values
    target_day_open = pd.Series(np.where(is_anchor, temp['target_open'].values, np.nan), index=df.index).ffill().astype(float)
    binance_day_open = pd.Series(np.where(is_anchor, temp['binance_close'].values, np.nan), index=df.index).ffill().astype(float)

    target_ret = (target.values / (target_day_open.values + 1e-8)) - 1.0
    binance_ret = (binance.values / (binance_day_open.values + 1e-8)) - 1.0
    result = pd.Series(target_ret - binance_ret, index=df.index)
    return result.fillna(0.0).clip(-0.1, 0.1)


def _compute_target_cvd_absorption(df, params, interval):
    """CVD-가격 다이버전스 흡수율 — 세력 매집/분산 강도 포착"""
    candles = params.get('candles', 3)
    bid_ratio = _get_series(df, 'target_trade_bid_ratio', 'upbit_trade_bid_ratio', 0.5).astype(float).fillna(0.5)
    vol = _get_series(df, 'target_volume', 'upbit_volume', 0.0).astype(float).fillna(0.0)

    delta = vol * (2.0 * bid_ratio - 1.0)
    cvd_change = delta.rolling(candles, min_periods=1).sum()
    close = _get_series(df, 'target_close', 'upbit_close', 0.0).astype(float)
    price_change = close.pct_change(candles).fillna(0.0)

    norm_vol = vol.rolling(candles, min_periods=1).sum()
    cvd_norm = cvd_change / (norm_vol + 1e-8)

    price_sign = np.sign(price_change.replace(0, np.nan).fillna(1e-8))
    absorption = cvd_norm * (-price_sign)
    return absorption.fillna(0.0).clip(-1.0, 1.0)


# ==============================================================================
# KOSPI 200 & ETF 전용 지표 계산 함수 (Compute Functions)
# ==============================================================================

def _ref_close_by_type(df, market_types, exclude_field=None):
    """참조 종목 중 원장 market_type 이 일치하는 첫 종목의 종가 시리즈 (없으면 None). 이름/별칭을 보지 않는다.
    exclude_field: 원장에 그 필드가 있는 종목은 제외 (지수선물 베이시스에서 개별주식선물[underlying]을 빼는 용도)."""
    from shared.config.config_manager import find_ref_columns
    cols = find_ref_columns(df.columns, "close", market_types, exclude_field=exclude_field)
    return df[cols[0]].astype(float) if cols else None


def _kospi_futures_and_spot(df):
    """KOSPI200 선물/지수 종가 — 원장 유형(KRX_FUTURES/KRX_INDEX)으로 찾고, 없거나 전부 결측일 때만 라이브 서빙이 만드는 고정 컬럼명으로 폴백.
    (지수가 없으면 타겟 가격으로 대체하지 않는다 — 선물(지수 포인트)과 ETF 가격은 단위가 달라 베이시스가 무의미해진다.)"""
    nan = pd.Series(np.nan, index=df.index)
    fut = _ref_close_by_type(df, ("KRX_FUTURES",), exclude_field="underlying")
    if fut is None or fut.isna().all():
        fut = df.get('futures_close', df.get('kospi_futures_close', nan)).astype(float)
    spot = _ref_close_by_type(df, ("KRX_INDEX",))
    if spot is None or spot.isna().all():
        spot = df.get('spot_close', nan).astype(float)
    return fut, spot


def _compute_kospi_basis(df, params, interval):
    """KOSPI 200 선물 - 현물 베이시스 (F - S)"""
    fut_close, spot_close = _kospi_futures_and_spot(df)
    return (fut_close - spot_close).fillna(0.0)


def _compute_kospi_basis_rate(df, params, interval):
    """KOSPI 200 선물 베이시스 비율 ((F - S) / S * 100)"""
    fut_close, spot_close = _kospi_futures_and_spot(df)
    basis_rate = ((fut_close - spot_close) / (spot_close + 1e-8)) * 100.0
    return basis_rate.fillna(0.0).clip(-5.0, 5.0)


def _compute_etf_nav_disparity(df, params, interval):
    """KODEX 200 ETF 괴리율 ((Price - NAV) / NAV * 100)"""
    price = _get_series(df, 'target_close', 'upbit_close', df.get('close', pd.Series(np.nan, index=df.index))).astype(float)
    nav = df.get('nav', df.get('etf_nav', price)).astype(float)
    disparity = ((price - nav) / (nav + 1e-8)) * 100.0
    return disparity.fillna(0.0).clip(-3.0, 3.0)


def _compute_overnight_gap_pct(df, params, interval):
    """당일 시가 갭 비율 ((Open - PrevClose) / PrevClose * 100)"""
    close = _get_series(df, 'target_close', 'upbit_close', df.get('close', pd.Series(np.nan, index=df.index))).astype(float)
    open_p = _get_series(df, 'target_open', 'upbit_open', df.get('open', close)).astype(float)
    prev_close = close.shift(1).fillna(open_p)
    gap_pct = ((open_p - prev_close) / (prev_close + 1e-8)) * 100.0
    return gap_pct.fillna(0.0).clip(-10.0, 10.0)


def _compute_krx_session_time(df, params, interval):
    """한국거래소 장중 시간대 세션 인코딩 (09:00~10:00=1, 10:00~14:00=2, 14:00~15:30=3, 기타=0)"""
    ts = pd.to_datetime(df['timestamp'])
    hour = ts.dt.hour
    minute = ts.dt.minute
    time_val = hour * 100 + minute
    
    session = pd.Series(0.0, index=df.index)
    session[(time_val >= 900) & (time_val < 1000)] = 1.0  # 개장초 변동성
    session[(time_val >= 1000) & (time_val < 1400)] = 2.0 # 정규 횡보
    session[(time_val >= 1400) & (time_val <= 1530)] = 3.0 # 장마감 집중
    return session


def _compute_kospi_basis_z(df, params, interval):
    """KOSPI 200 선물 - 현물 베이시스 롤링 Z-Score (단위 무차원화 표준화)"""
    fut_close, spot_close = _kospi_futures_and_spot(df)
    basis = fut_close - spot_close
    window = _get_window_bars(params, default_bars=60, default_mins=60.0, interval=interval)
    mean = basis.rolling(window, min_periods=max(5, window // 4)).mean()
    std = basis.rolling(window, min_periods=max(5, window // 4)).std()
    z_score = (basis - mean) / (std + 1e-8)
    return z_score.fillna(0.0).clip(-3.0, 3.0)


_STOCK_FUT_MAX_SPOT_GAP = 0.10   # 선물/현물 중앙값 괴리 허용 한도(10%) — 실제 베이시스는 1% 안팎


def _stock_futures_and_spot(df):
    """개별주식 선물 종가 및 타겟 현물 종가 추출
    - 선물: 도킹된 KRX_FUTURES 중 원장에 기초자산(underlying)이 적힌 개별주식선물 가운데, 타겟 현물과 가격 수준이
      맞는(중앙값 괴리 ≤ 10%) 것 중 가장 가까운 하나. 다른 종목의 주식선물(예: 하이닉스 타겟에 삼성선물)이나
      잘못 등록된 코드(2026-10-07 10600=코스닥150 사고)는 여기서 걸러져 결측이 된다. 지수선물로 대체하지 않는다.
    - 현물: 타겟 자산 종가(target_close / close)
    """
    nan = pd.Series(np.nan, index=df.index)
    spot = _get_series(df, 'target_close', 'upbit_close', df.get('close', nan)).astype(float)
    from shared.config.config_manager import find_ref_columns
    best, best_gap = None, None
    for col in find_ref_columns(df.columns, "close", ("KRX_FUTURES",), require_field="underlying"):
        fut = df[col].astype(float)
        ratio = (fut / spot).replace([np.inf, -np.inf], np.nan)
        ratio = ratio[(fut > 0) & (spot > 0)]
        if ratio.dropna().empty:
            continue
        gap = abs(float(ratio.median()) - 1.0)
        if gap <= _STOCK_FUT_MAX_SPOT_GAP and (best_gap is None or gap < best_gap):
            best, best_gap = fut, gap
    return (best if best is not None else nan), spot


def _compute_stock_basis(df, params, interval):
    """개별주식 선물 - 현물 베이시스 (F_stock - S_stock)"""
    fut_close, spot_close = _stock_futures_and_spot(df)
    return (fut_close - spot_close).fillna(0.0)


def _compute_stock_basis_rate(df, params, interval):
    """개별주식 선물 베이시스 비율 ((F_stock - S_stock) / S_stock * 100)"""
    fut_close, spot_close = _stock_futures_and_spot(df)
    basis_rate = ((fut_close - spot_close) / (spot_close + 1e-8)) * 100.0
    return basis_rate.fillna(0.0).clip(-5.0, 5.0)


def _compute_stock_basis_z(df, params, interval):
    """개별주식 선물 - 현물 베이시스 롤링 Z-Score (단위 무차원화 표준화)"""
    fut_close, spot_close = _stock_futures_and_spot(df)
    basis = fut_close - spot_close
    window = _get_window_bars(params, default_bars=60, default_mins=60.0, interval=interval)
    mean = basis.rolling(window, min_periods=max(5, window // 4)).mean()
    std = basis.rolling(window, min_periods=max(5, window // 4)).std()
    z_score = (basis - mean) / (std + 1e-8)
    return z_score.fillna(0.0).clip(-3.0, 3.0)



def _compute_top2_lead_momentum(df, params, interval):
    """섹터/자산군 선행 대장주(원장 유형이 주식인 참조 종목 중 키 정렬 앞 2개) 가중 선행 모멘텀 롤링 Z-Score"""
    from shared.config.config_manager import find_ref_columns
    nan = pd.Series(np.nan, index=df.index)
    _leaders = find_ref_columns(df.columns, "close", ("KRX_STOCK",))   # 원장 유형(주식) + 키 정렬순 — 학습/서빙이 같은 결과
    lead1_close = df[_leaders[0]].astype(float) if len(_leaders) > 0 else nan
    lead2_close = df[_leaders[1]].astype(float) if len(_leaders) > 1 else nan
    candles = int(params.get('candles', 5))
    window = _get_window_bars(params, default_bars=60, default_mins=60.0, interval=interval)

    has_lead1 = lead1_close.notna().any()
    has_lead2 = lead2_close.notna().any()

    if not has_lead1 and not has_lead2:
        return pd.Series(0.0, index=df.index)

    ret1 = ((lead1_close - lead1_close.shift(candles)) / (lead1_close.shift(candles) + 1e-8)) * 100.0 if has_lead1 else pd.Series(0.0, index=df.index)
    ret2 = ((lead2_close - lead2_close.shift(candles)) / (lead2_close.shift(candles) + 1e-8)) * 100.0 if has_lead2 else pd.Series(0.0, index=df.index)

    # 1위(70%) + 2위(30%) 합성 (한쪽만 있으면 100% 단독 적용)
    if has_lead1 and has_lead2:
        synth = (0.70 * ret1.fillna(0.0)) + (0.30 * ret2.fillna(0.0))
    elif has_lead1:
        synth = ret1.fillna(0.0)
    else:
        synth = ret2.fillna(0.0)

    mean = synth.rolling(window, min_periods=max(5, window // 4)).mean()
    std = synth.rolling(window, min_periods=max(5, window // 4)).std()
    z = (synth - mean) / (std + 1e-8)
    return z.fillna(0.0).clip(-3.0, 3.0)


def _compute_semi_duo_lead_delta(df, params, interval):
    """대장주 듀오(삼성전자 70% + SK하이닉스 30%) 선행 델타 Z-Score"""
    window_bars = int(params.get('window_bars', 1))
    zscore_bars = int(params.get('zscore_bars', 30))
    w_sam = float(params.get('w_sam', 0.70))
    w_hyn = float(params.get('w_hyn', 0.30))

    target_close = _get_series(df, 'target_close', 'close', default=None)
    sam_close = _get_series(df, 'ref_005930_close', 'ref_samsung_close', default=None)
    hyn_close = _get_series(df, 'ref_000660_close', 'ref_hynix_close', default=None)

    if target_close is None:
        return pd.Series(0.0, index=df.index)

    target_close = target_close.astype(float)
    ret_target = ((target_close - target_close.shift(window_bars)) / (target_close.shift(window_bars) + 1e-8)) * 100.0

    has_sam = sam_close is not None and sam_close.notna().any()
    has_hyn = hyn_close is not None and hyn_close.notna().any()

    if not has_sam and not has_hyn:
        return pd.Series(0.0, index=df.index)

    ret_sam = ((sam_close - sam_close.shift(window_bars)) / (sam_close.shift(window_bars) + 1e-8)) * 100.0 if has_sam else None
    ret_hyn = ((hyn_close - hyn_close.shift(window_bars)) / (hyn_close.shift(window_bars) + 1e-8)) * 100.0 if has_hyn else None

    if has_sam and has_hyn:
        semi_ret = (w_sam * ret_sam.fillna(0.0) + w_hyn * ret_hyn.fillna(0.0)) / max(1e-8, (w_sam + w_hyn))
    elif has_sam:
        semi_ret = ret_sam.fillna(0.0)
    else:
        semi_ret = ret_hyn.fillna(0.0)

    delta = semi_ret - ret_target.fillna(0.0)
    delta = delta.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    min_p = max(2, zscore_bars // 4)
    mean = delta.rolling(zscore_bars, min_periods=min_p).mean()
    std = delta.rolling(zscore_bars, min_periods=min_p).std()
    z = (delta - mean) / (std.replace(0, np.nan) + 1e-8)
    return z.fillna(0.0).clip(-3.0, 3.0).astype(float)


def _compute_parkinson_rv_squeeze(df, params, interval):
    """Parkinson 극치 실현변동성(RV) 단기/장기 스퀴즈 비율 (변동성 폭발 국면 포착)"""
    high = _get_series(df, 'target_high', 'upbit_high', df.get('high', pd.Series(np.nan, index=df.index))).astype(float)
    low = _get_series(df, 'target_low', 'upbit_low', df.get('low', pd.Series(np.nan, index=df.index))).astype(float)

    # Parkinson 극치 변동성 공식: ln(H/L)^2 / (4 * ln(2))
    log_hl = np.log(np.maximum(high, 1e-8) / np.maximum(low, 1e-8))
    pv = (log_hl ** 2) / (4.0 * np.log(2.0))

    short_w = int(params.get('short_bars', 5))
    long_w = int(params.get('long_bars', 30))

    rv_short = np.sqrt(pv.rolling(short_w, min_periods=2).mean())
    rv_long = np.sqrt(pv.rolling(long_w, min_periods=5).mean())
    ratio = rv_short / (rv_long + 1e-8)

    # 롤링 Z-Score 정규화
    mean = ratio.rolling(long_w, min_periods=5).mean()
    std = ratio.rolling(long_w, min_periods=5).std()
    z = (ratio - mean) / (std + 1e-8)
    return z.fillna(0.0).clip(-3.0, 3.0)


# ==============================================================================
# 레거시 호환용 함수 심볼 포인팅 (Backward Compatibility Function Aliases)
# ==============================================================================
_compute_upbit_ma_ratio = _compute_target_ma_ratio
_compute_upbit_rsi_daily = _compute_target_rsi_daily
_compute_upbit_trade_bid_ratio = _compute_target_trade_bid_ratio
_compute_upbit_hour = _compute_target_hour
_compute_upbit_is_weekend = _compute_target_is_weekend
_compute_upbit_rsi_curr = _compute_target_rsi_curr
_compute_upbit_atr_ratio = _compute_target_atr_ratio
_compute_upbit_adx = _compute_target_adx
_compute_upbit_bb_pctb = _compute_target_bb_pctb
_compute_upbit_macd_hist = _compute_target_macd_hist
_compute_upbit_mfi = _compute_target_mfi
_compute_upbit_cvd_momentum = _compute_target_cvd_momentum
_compute_upbit_volatility_ratio = _compute_target_volatility_ratio
_compute_upbit_trade_frequency = _compute_target_trade_frequency
_compute_upbit_large_trade_ratio = _compute_target_large_trade_ratio
_compute_upbit_avg_trade_size_change = _compute_target_avg_trade_size_change
_compute_upbit_volume_spike = _compute_target_volume_spike
_compute_upbit_candle_body_ratio = _compute_target_candle_body_ratio
_compute_upbit_obv_momentum = _compute_target_obv_momentum
_compute_upbit_trade_price_spike = _compute_target_trade_price_spike
_compute_upbit_bb_kc_squeeze = _compute_target_bb_kc_squeeze
_compute_upbit_kaufman_er = _compute_target_kaufman_er
_compute_upbit_usdt_krw_ret = _compute_crypto_usdt_krw_ret
_compute_upbit_kst_0900_sync = _compute_crypto_0900_sync
_compute_upbit_cvd_absorption = _compute_target_cvd_absorption


# ==============================================================================
# [마이크로스트럭처 알파 팩터 v2] Normalized Store (Silver) 기반 선계산 팩터
# ==============================================================================

def _compute_target_vwap_disparity(df, params, interval):
    """체결가중평균가격(VWAP) 대비 현재가 괴리율 (%) — 기관 체결 평단 대비 고/저평가 판별"""
    close = _get_series(df, 'target_close', 'close')
    vwap = _get_series(df, 'target_vwap', 'vwap')
    # VWAP 부재 또는 전원 결측 시 (High+Low+Close)/3 폴백
    if vwap.isna().all() or (vwap == 0).all():
        high = _get_series(df, 'target_high', 'high')
        low = _get_series(df, 'target_low', 'low')
        vwap = (high + low + close) / 3.0
    smooth = params.get('smooth_bars', 1) if params else 1
    disp = ((close - vwap) / (vwap.replace(0, np.nan) + 1e-8)) * 100.0
    disp = disp.fillna(0.0).clip(-50.0, 50.0)
    if smooth > 1:
        disp = disp.rolling(smooth, min_periods=1).mean()
    return disp.fillna(0.0).astype(np.float32)


def _compute_target_vwap_slope(df, params, interval):
    """VWAP 기울기 — N바 동안 VWAP의 변화율(%)을 측정하여 추세 탄력도와 지속성 포착"""
    vwap = _get_series(df, 'target_vwap', 'vwap')
    if vwap.isna().all() or (vwap == 0).all():
        high = _get_series(df, 'target_high', 'high')
        low = _get_series(df, 'target_low', 'low')
        close = _get_series(df, 'target_close', 'close')
        vwap = (high + low + close) / 3.0
    window = _get_window_bars(params, default_bars=5, default_mins=5, interval=interval)
    prev = vwap.shift(window)
    slope = ((vwap - prev) / (prev.replace(0, np.nan) + 1e-8)) * 100.0
    return slope.fillna(0.0).clip(-50.0, 50.0).astype(np.float32)


def _compute_target_ofi_cumsum(df, params, interval):
    """Lee-Ready 기반 순주문흐름(Order Flow Imbalance) 지수이동평균 — 주도 세력 실측 수급 모멘텀"""
    ofi = _get_series(df, 'target_ofi', 'ofi')
    if ofi.isna().all():
        # 대용치: 체결 매수/매도비율 기반 OFI 근사
        bid_vol = _get_series(df, 'target_bid_volume', 'bid_volume', default=0.0).astype(float)
        ask_vol = _get_series(df, 'target_ask_volume', 'ask_volume', default=0.0).astype(float)
        tot_vol = bid_vol + ask_vol
        ofi = (bid_vol - ask_vol) / (tot_vol.replace(0, np.nan) + 1e-8)
    window = _get_window_bars(params, default_bars=10, default_mins=10, interval=interval)
    ofi_smooth = ofi.ewm(span=window, min_periods=1).mean()
    return ofi_smooth.fillna(0.0).clip(-1.0, 1.0).astype(np.float32)


def _compute_target_rv_ratio(df, params, interval):
    """Parkinson 극치 실현변동성 단기/장기 비율 — 변동성 수축 ➔ 폭발 전환 탐지"""
    rv = _get_series(df, 'target_rv', 'rv')
    if rv.isna().all() or (rv == 0).all():
        # 폴백: High-Low Parkinson 대용치 계산
        high = _get_series(df, 'target_high', 'high').replace(0, np.nan)
        low = _get_series(df, 'target_low', 'low').replace(0, np.nan)
        hl_ratio = np.log(high / (low + 1e-8))
        rv = np.sqrt((hl_ratio ** 2) / (4.0 * np.log(2.0)))
    short_bars = params.get('short_bars', 5) if params else 5
    long_bars = params.get('long_bars', 30) if params else 30
    rv_short = rv.rolling(short_bars, min_periods=1).mean()
    rv_long = rv.rolling(long_bars, min_periods=1).mean()
    ratio = rv_short / (rv_long.replace(0, np.nan) + 1e-8)
    return ratio.fillna(1.0).clip(0.0, 10.0).astype(np.float32)


def _compute_target_large_trade_density(df, params, interval):
    """대량 체결 밀도 — N바 윈도우 내 대형 주문(스마트 머니) 체결 발생 빈도"""
    large_flag = _get_series(df, 'target_is_large_trade', 'is_large_trade', default=None)
    if large_flag is None or (isinstance(large_flag, pd.Series) and large_flag.isna().all()):
        cnt = _get_series(df, 'target_large_trade_count', default=0.0).astype(float)
        large_flag = (cnt > 0).astype(float)
    else:
        large_flag = large_flag.astype(float)
    window = _get_window_bars(params, default_bars=10, default_mins=10, interval=interval)
    density = large_flag.rolling(window, min_periods=1).mean()
    return density.fillna(0.0).clip(0.0, 1.0).astype(np.float32)


def _compute_target_is_auction(df, params, interval):
    """KRX 15:20~15:30 동시호가 단일가 구간 여부 마스크 (체제/컨텍스트 지표, 0: 정규장, 1: 동시호가)"""
    auction_flag = _get_series(df, 'target_is_auction', 'is_auction', default=0)
    return auction_flag.fillna(0).astype(np.int32)


# ==============================================================================
# 별칭 매핑 테이블 (FEATURE_ALIASES)
# ==============================================================================
FEATURE_ALIASES = {
    "upbit_ma_ratio": "target_ma_ratio",
    "upbit_rsi_curr": "target_rsi_curr",
    "upbit_rsi_daily": "target_rsi_daily",
    "upbit_trade_bid_ratio": "target_trade_bid_ratio",
    "upbit_hour": "target_hour",
    "upbit_is_weekend": "target_is_weekend",
    "upbit_atr_ratio": "target_atr_ratio",
    "upbit_adx": "target_adx",
    "upbit_bb_pctb": "target_bb_pctb",
    "upbit_macd_hist": "target_macd_hist",
    "upbit_mfi": "target_mfi",
    "upbit_cvd_momentum": "target_cvd_momentum",
    "upbit_volatility_ratio": "target_volatility_ratio",
    "upbit_trade_frequency": "target_trade_frequency",
    "upbit_large_trade_ratio": "target_large_trade_ratio",
    "upbit_avg_trade_size_change": "target_avg_trade_size_change",
    "upbit_volume_spike": "target_volume_spike",
    "upbit_candle_body_ratio": "target_candle_body_ratio",
    "upbit_obv_momentum": "target_obv_momentum",
    "upbit_trade_price_spike": "target_trade_price_spike",
    "upbit_bb_kc_squeeze": "target_bb_kc_squeeze",
    "upbit_kaufman_er": "target_kaufman_er",
    "upbit_cvd_absorption": "target_cvd_absorption",
    "upbit_usdt_krw_ret": "crypto_usdt_return",
    "upbit_usdt_return": "crypto_usdt_return",
    "upbit_kst_0900_sync": "crypto_0900_open_sync",
    "upbit_0900_open_sync": "crypto_0900_open_sync",
}


# ==============================================================================
# 기본 지표 레지스트리 (DEFAULT_FEATURE_REGISTRY)
# ==============================================================================
DEFAULT_FEATURE_REGISTRY = {
    # ── [마이크로스트럭처 팩터] Normalized Store (Silver) 기반 고주파 미세구조 팩터 ──
    "target_vwap_disparity": {
        "id": "target_vwap_disparity",
        "name": "타겟 VWAP 괴리율",
        "name_en": "Target VWAP Disparity (%)",
        "description": "체결가중평균가격(VWAP) 대비 현재가 괴리율 (%). 기관 체결 평단 대비 고/저평가 판별.",
        "category": "microstructure",
        "category_name": "마이크로스트럭처",
        "market_scope": "all",
        "params": {
            "smooth_bars": {
                "type": "slider",
                "default": 1,
                "min": 1,
                "max": 10,
                "step": 1,
                "label": "평활 기간(캔들)",
                "description": "괴리율 평활에 사용할 캔들 수 (1: 순수 괴리율)"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_vwap_disparity
    },
    "target_vwap_slope": {
        "id": "target_vwap_slope",
        "name": "타겟 VWAP 기울기",
        "name_en": "Target VWAP Slope (%)",
        "description": "VWAP의 N캔들 변화율(%). 체결가중 중심가격의 이동 방향성과 추세 탄력도 포착.",
        "category": "microstructure",
        "category_name": "마이크로스트럭처",
        "market_scope": "all",
        "params": {
            "window_bars": {
                "type": "slider",
                "default": 5,
                "min": 2,
                "max": 30,
                "step": 1,
                "label": "기울기 기간(캔들)",
                "description": "VWAP 변화율 측정에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_vwap_slope
    },
    "target_ofi_cumsum": {
        "id": "target_ofi_cumsum",
        "name": "타겟 OFI 누적 수급 강도",
        "name_en": "Target Order Flow Imbalance (OFI)",
        "description": "Lee-Ready 틱 체결 흐름 기반 순주문흐름(OFI) 지수평활. 주도 세력의 실측 순매수 강도 포착.",
        "category": "microstructure",
        "category_name": "마이크로스트럭처",
        "market_scope": "all",
        "params": {
            "window_bars": {
                "type": "slider",
                "default": 10,
                "min": 3,
                "max": 50,
                "step": 1,
                "label": "지수평활 기간(캔들)",
                "description": "OFI EMA 평활에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_ofi_cumsum
    },
    "target_rv_ratio": {
        "id": "target_rv_ratio",
        "name": "타겟 Parkinson RV 비율",
        "name_en": "Target Parkinson RV Ratio",
        "description": "Parkinson 극치 실현변동성 단기/장기 비율. 변동성 수축에서 확장으로의 전환 국면 포착.",
        "category": "microstructure",
        "category_name": "마이크로스트럭처",
        "market_scope": "all",
        "params": {
            "short_bars": {
                "type": "slider",
                "default": 5,
                "min": 2,
                "max": 15,
                "step": 1,
                "label": "단기 RV 기간",
                "description": "단기 실현변동성 캔들 수"
            },
            "long_bars": {
                "type": "slider",
                "default": 30,
                "min": 15,
                "max": 60,
                "step": 1,
                "label": "장기 RV 기간",
                "description": "장기 실현변동성 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_rv_ratio
    },
    "target_large_trade_density": {
        "id": "target_large_trade_density",
        "name": "타겟 대량 체결 밀도",
        "name_en": "Target Large Trade Density",
        "description": "N캔들 내 대형 주문(스마트 머니) 체결 발생 빈도 및 밀도.",
        "category": "microstructure",
        "category_name": "마이크로스트럭처",
        "market_scope": "all",
        "params": {
            "window_bars": {
                "type": "slider",
                "default": 10,
                "min": 3,
                "max": 50,
                "step": 1,
                "label": "밀도 측정 기간(캔들)",
                "description": "대량 체결 빈도 측정 윈도우 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_large_trade_density
    },
    "target_is_auction": {
        "id": "target_is_auction",
        "name": "KRX 동시호가 단일가 마스크",
        "name_en": "KRX Auction Session Mask",
        "description": "KRX 15:20~15:30 동시호가 단일가 매매 구간 여부 (0: 정규장, 1: 동시호가). 체제/컨텍스트 지표.",
        "category": "time",
        "category_name": "시간/세션",
        "market_scope": "krx",
        "is_regime": True,
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_target_is_auction
    },

    # ── 타겟 자산 공통 지표 (ETF, 주식, 가상자산) ──
    "target_ma_ratio": {
        "id": "target_ma_ratio",
        "name": "타겟 이격도 [현재 분봉]",
        "name_en": "Target MA Ratio (Curr. Intv)",
        "description": "현재 분봉 기준 현재가 대비 N캔들 이동평균의 괴리율. 단기 과매수/과매도 판단에 사용됩니다.",
        "category": "trend",
        "category_name": "추세",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 15,
                "min": 5,
                "max": 50,
                "step": 1,
                "label": "MA 기간(캔들)",
                "description": "이동평균 계산에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_ma_ratio
    },
    "target_rsi_curr": {
        "id": "target_rsi_curr",
        "name": "타겟 RSI [현재 분봉]",
        "name_en": "Target RSI (Current Interval Basis)",
        "description": "설정된 현재 분봉 종가 기준 RSI. 단기 과매수 및 과매도 국면을 탐색합니다.",
        "category": "oscillator",
        "category_name": "오실레이터",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 14,
                "min": 5,
                "max": 30,
                "step": 1,
                "label": "RSI 기간(캔들)",
                "description": "RSI 계산에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_rsi_curr
    },
    "target_rsi_daily": {
        "id": "target_rsi_daily",
        "name": "타겟 RSI [일봉 기준/어제결과]",
        "name_en": "Target RSI (Daily/Yesterday)",
        "description": "실제 일봉 기준 종가로 계산된 RSI. 미래 참조 방지를 위해 '어제(T-1)' 결정 수치를 매핑합니다.",
        "category": "oscillator",
        "category_name": "오실레이터",
        "params": {
            "period_days": {
                "type": "slider",
                "default": 14,
                "min": 1,
                "max": 30,
                "step": 1,
                "label": "RSI 기간(일)",
                "description": "RSI 계산에 사용할 일 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_rsi_daily
    },
    "target_trade_bid_ratio": {
        "id": "target_trade_bid_ratio",
        "name": "타겟 매수 체결 비율 [현재 분봉]",
        "name_en": "Target Bid Trade Ratio (Curr. Intv)",
        "description": "현재 분봉 기준 체결 중 적극적 매수 주문 비율. 시장 참여자의 매수/매도 우세를 파악합니다.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 10,
                "min": 1,
                "max": 120,
                "step": 1,
                "label": "롤링 윈도우(분)",
                "description": "누적 수급을 집계할 롤링 시간(분)"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_trade_bid_ratio
    },
    "target_cvd_momentum": {
        "id": "target_cvd_momentum",
        "name": "타겟 CVD 모멘텀 [현재 분봉]",
        "name_en": "Target CVD Momentum (Curr. Intv)",
        "description": "설정된 현재 분봉 기준 CVD(누적 체결 델타)의 N캔들 순수시장가매수 비율. 정규화 완료(-1.0 ~ 1.0).",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 30,
                "min": 5,
                "max": 240,
                "step": 5,
                "label": "모멘텀 윈도우(분)",
                "description": "누적 수급을 집계할 롤링 시간(분)"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_cvd_momentum
    },
    "target_atr_ratio": {
        "id": "target_atr_ratio",
        "name": "타겟 ATR 변동성 비율 [현재 분봉]",
        "name_en": "Target ATR Volatility Ratio (Curr. Intv)",
        "description": "현재 분봉 기준 ATR(실제 변동폭) / 종가 비율. 변동성이 극도로 수축한 비추세 횡보장을 감지하여 필터링합니다.",
        "category": "momentum",
        "category_name": "모멘텀",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 14,
                "min": 5,
                "max": 30,
                "step": 1,
                "label": "ATR 기간(캔들)",
                "description": "ATR 계산에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_atr_ratio
    },
    "target_adx": {
        "id": "target_adx",
        "name": "타겟 ADX 추세 강도 [현재 분봉]",
        "name_en": "Target ADX Trend Strength (Curr. Intv)",
        "description": "현재 분봉 기준 ADX(추세 강도 지표). 시장이 박스권 비추세 상태인지 강한 추세 상태인지를 감지합니다.",
        "category": "trend",
        "category_name": "추세",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 14,
                "min": 5,
                "max": 30,
                "step": 1,
                "label": "ADX 기간(캔들)",
                "description": "ADX 계산에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_adx
    },
    "target_bb_pctb": {
        "id": "target_bb_pctb",
        "name": "타겟 볼린저 밴드 %B [현재 분봉]",
        "name_en": "Target Bollinger Band %B (Curr. Intv)",
        "description": "현재 분봉 기준 볼린저 밴드 대비 현재가의 상대 위치(0~1).",
        "category": "trend",
        "category_name": "추세",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 20,
                "min": 5,
                "max": 50,
                "step": 1,
                "label": "이평선 기간(캔들)"
            },
            "std_dev": {
                "type": "slider",
                "default": 2,
                "min": 1,
                "max": 3,
                "step": 0.5,
                "label": "표준편차 배수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_bb_pctb
    },
    "target_macd_hist": {
        "id": "target_macd_hist",
        "name": "타겟 MACD 히스토그램 [현재 분봉]",
        "name_en": "Target MACD Histogram (Curr. Intv)",
        "description": "현재 분봉 기준 MACD선과 시그널선의 차이(히스토그램). 추세의 가속도 및 전환 속도를 판단합니다.",
        "category": "trend",
        "category_name": "추세",
        "params": {
            "fast_period": {
                "type": "slider",
                "default": 12,
                "min": 5,
                "max": 20,
                "step": 1,
                "label": "단기 EMA 기간"
            },
            "slow_period": {
                "type": "slider",
                "default": 26,
                "min": 21,
                "max": 50,
                "step": 1,
                "label": "장기 EMA 기간"
            },
            "signal_period": {
                "type": "slider",
                "default": 9,
                "min": 5,
                "max": 15,
                "step": 1,
                "label": "시그널 EMA 기간"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_macd_hist
    },
    "target_mfi": {
        "id": "target_mfi",
        "name": "타겟 자금 흐름 지수 (MFI) [현재 분봉]",
        "name_en": "Target Money Flow Index (Curr. Intv)",
        "description": "현재 분봉 기준 거래량을 결합한 오실레이터(0~100). 단순 RSI의 단점을 거래량 가중치로 보완합니다.",
        "category": "oscillator",
        "category_name": "오실레이터",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 14,
                "min": 5,
                "max": 30,
                "step": 1,
                "label": "분석 기간(캔들)"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_mfi
    },
    "target_volatility_ratio": {
        "id": "target_volatility_ratio",
        "name": "타겟 변동성 정국 비율 [단기/장기 ATR]",
        "name_en": "Target Volatility Ratio (ATR ratio)",
        "description": "장기 변동성 대비 단기 변동성의 강도로서 돌파 매매의 임계점을 파악합니다.",
        "category": "trend",
        "category_name": "추세",
        "params": {
            "fast_period": {
                "type": "slider",
                "default": 5,
                "min": 2,
                "max": 15,
                "step": 1,
                "label": "단기 변동성 기간"
            },
            "slow_period": {
                "type": "slider",
                "default": 30,
                "min": 20,
                "max": 100,
                "step": 5,
                "label": "장기 변동성 기간"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_volatility_ratio
    },
    "target_trade_frequency": {
        "id": "target_trade_frequency",
        "name": "타겟 체결 빈도 [현재 분봉]",
        "name_en": "Target Trade Frequency (Curr. Intv)",
        "description": "봉당 체결 건수. 거래 참여 활성도를 직접 반영합니다.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": _compute_target_trade_frequency
    },
    "target_large_trade_ratio": {
        "id": "target_large_trade_ratio",
        "name": "타겟 대량 체결 비율 [현재 분봉]",
        "name_en": "Target Large Trade Ratio (Curr. Intv)",
        "description": "전체 체결 중 대량(≥1억원) 체결의 비율. 세력/기관 매집을 감지합니다.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_target_large_trade_ratio
    },
    "target_avg_trade_size_change": {
        "id": "target_avg_trade_size_change",
        "name": "타겟 평균 체결 크기 변화율 [현재 분봉]",
        "name_en": "Target Avg Trade Size Change (Curr. Intv)",
        "description": "N캔들 전 대비 평균 체결 금액의 변화율.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 20,
                "min": 5,
                "max": 120,
                "step": 5,
                "label": "비교 윈도우(분)",
                "description": "몇 분 전과 비교할지 설정"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_avg_trade_size_change
    },
    "target_volume_spike": {
        "id": "target_volume_spike",
        "name": "타겟 거래량 급증 지표 [현재 분봉]",
        "name_en": "Target Volume Spike (Curr. Intv)",
        "description": "현재 거래량 / N캔들 평균 거래량 배율. 돌파 시 거래량 동반 급증 여부를 판별합니다.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 20,
                "min": 5,
                "max": 60,
                "step": 1,
                "label": "평균 기간(캔들)",
                "description": "평균 거래량을 계산할 캔들 수"
            }
        },
        "enabled": True,
        "is_trigger": True,
        "has_params": True,
        "compute": _compute_target_volume_spike
    },
    "target_candle_body_ratio": {
        "id": "target_candle_body_ratio",
        "name": "타겟 캔들 실체 비율 [현재 분봉]",
        "name_en": "Target Candle Body Ratio (Curr. Intv)",
        "description": "|종가-시가| / (고가-저가). 캔들의 실체 비율로 시장 방향 확신도를 측정합니다.",
        "category": "momentum",
        "category_name": "모멘텀",
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": _compute_target_candle_body_ratio
    },
    "target_obv_momentum": {
        "id": "target_obv_momentum",
        "name": "타겟 OBV 모멘텀 [현재 분봉]",
        "name_en": "Target OBV Momentum (Curr. Intv)",
        "description": "누적 매수-매도 체결량(OBV)의 N캔들 변화를 정규화한 지표(-1.0 ~ 1.0).",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 30,
                "min": 5,
                "max": 240,
                "step": 5,
                "label": "모멘텀 윈도우(분)",
                "description": "OBV 변화를 집계할 롤링 시간(분)"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_obv_momentum
    },
    "target_trade_price_spike": {
        "id": "target_trade_price_spike",
        "name": "타겟 거래 대금 급증 [현재 분봉]",
        "name_en": "Target Trade Price Spike (Curr. Intv)",
        "description": "현재 봉 거래 대금 / N캔들 평균 대금 배율. 원화 기준 실제 자본 유입 강도를 측정합니다.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 20,
                "min": 5,
                "max": 60,
                "step": 1,
                "label": "평균 기간(캔들)",
                "description": "평균 거래 대금을 계산할 캔들 수"
            }
        },
        "enabled": True,
        "is_trigger": True,
        "has_params": True,
        "compute": _compute_target_trade_price_spike
    },
    "target_bb_kc_squeeze": {
        "id": "target_bb_kc_squeeze",
        "name": "타겟 변동성 수축 Squeeze [현재 분봉]",
        "name_en": "Target BB-KC Volatility Squeeze",
        "description": "볼린저 밴드가 켈트너 채널 내부로 수축되어 폭발적 추세 분출을 앞둔 상태를 감지합니다.",
        "category": "oscillator",
        "category_name": "오실레이터",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 20,
                "min": 10,
                "max": 40,
                "step": 2,
                "label": "밴드 기간(캔들)",
                "description": "볼린저/켈트너 계산 기간"
            }
        },
        "enabled": True,
        "is_trigger": True,
        "has_params": True,
        "compute": _compute_target_bb_kc_squeeze
    },
    "target_kaufman_er": {
        "id": "target_kaufman_er",
        "name": "타겟 Kaufman 추세 효율성 ER [현재 분봉]",
        "name_en": "Target Kaufman Efficiency Ratio",
        "description": "순 가격 이동거리 대비 총 캔들 변동폭 비율로 횡보 노이즈와 원웨이 추세를 분리합니다.",
        "category": "trend",
        "category_name": "추세",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 10,
                "min": 5,
                "max": 30,
                "step": 1,
                "label": "효율성 기간(캔들)",
                "description": "순 이동거리 계산 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_target_kaufman_er
    },
    "target_hour": {
        "id": "target_hour",
        "name": "시간대 [고정 시간/0~23]",
        "name_en": "Hour of Day",
        "description": "24시간 중 현재 시간대를 정수형 범주(0~23)로 나타낸 피처입니다.",
        "category": "time",
        "category_name": "시간",
        "is_regime": True,
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": _compute_target_hour
    },
    "target_hour_sin": {
        "id": "target_hour_sin",
        "name": "24시간 싸인(Sin) 주기 인코딩 [-1.0~+1.0]",
        "name_en": "Hour of Day (Sin)",
        "description": "24시간을 싸인 함수로 원형 변환하여 23:59와 00:00의 연속성을 보장합니다. (가상자산/해외선물 추천)",
        "category": "time",
        "category_name": "시간",
        "is_regime": True,
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": _compute_target_hour_sin
    },
    "target_hour_cos": {
        "id": "target_hour_cos",
        "name": "24시간 코사인(Cos) 주기 인코딩 [-1.0~+1.0]",
        "name_en": "Hour of Day (Cos)",
        "description": "24시간을 코사인 함수로 원형 변환하여 낮/밤의 주기적 대칭성을 보장합니다. (가상자산/해외선물 추천)",
        "category": "time",
        "category_name": "시간",
        "is_regime": True,
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": _compute_target_hour_cos
    },
    "target_is_weekend": {
        "id": "target_is_weekend",
        "name": "주말 여부 [고정 요일]",
        "name_en": "Weekend Flag",
        "description": "현재 시점이 주말인지 평일인지 나타내는 이진 피처(주말=1, 평일=0)입니다.",
        "category": "time",
        "category_name": "시간",
        "is_regime": True,
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": _compute_target_is_weekend
    },
    "target_cvd_absorption": {
        "id": "target_cvd_absorption",
        "name": "타겟 CVD 흡수율 다이버전스 [수급 세력 매집]",
        "name_en": "Target CVD Absorption Divergence",
        "description": "가격 하락 중 순매수량(CVD) 급증 = 세력 매물 흡수. 가격 상승 중 CVD 급감 = 세력 고점 매도 분산.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "candles": {
                "type": "slider",
                "default": 3,
                "min": 1,
                "max": 10,
                "step": 1,
                "label": "누적 캔들 수",
                "description": "CVD 및 가격 변화 누적 기간(캔들)"
            }
        },
        "enabled": False,
        "has_params": True,
        "compute": _compute_target_cvd_absorption
    },
    "mtf_ema_distance_4h": {
        "id": "mtf_ema_distance_4h",
        "name": "4시간봉 200 EMA 이격도 [상위 추세]",
        "name_en": "4H 200 EMA Distance",
        "description": "상위 타임프레임(4시간봉) 200 EMA와의 이격도로 거시적 강세/약세 레짐을 필터링합니다.",
        "category": "trend",
        "category_name": "추세",
        "params": {
            "period": {
                "type": "slider",
                "default": 200,
                "min": 50,
                "max": 300,
                "step": 10,
                "label": "4H EMA 기간",
                "description": "상위 프레임 EMA 기간"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_mtf_ema_distance_4h
    },

    # ── KOSPI 200 & ETF 전용 지표 ──
    "kospi_basis": {
        "id": "kospi_basis",
        "name": "선물-현물 베이시스 [KOSPI 200]",
        "name_en": "KOSPI 200 Basis (Futures - Spot)",
        "description": "KOSPI 200 선물과 현물 지수의 가격 차이(F - S). 기관/외국인 프로그램 차익거래 유입과 선물 시장의 선행 방향성을 포착합니다.",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        # 체제(느린 맥락) 변수 — 베이시스 '수준'은 몇 주씩 한 값에 머물다 옮겨 간다(2025~26 종가 기준 0 근처 ↔ 9~15,
        # 하루 ±3 이상 이동 40여 회). 짧은 학습창 안에선 '어느 시기냐' 표식이라 선형 IC 가 우연히 나온다
        # (2026-10-20 122630 10s: 09-29 에 0.2→5.9 로 옮겨 간 뒤 하락장과 겹쳐 IC −0.02, 확률 상수 모델).
        # 그래서 IC 대신 블록 일치도 게이트(T-8)로 판정한다. 단기 변화는 kospi_basis_z 가 맡는다.
        "is_regime": True,
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_kospi_basis
    },
    "kospi_basis_rate": {
        "id": "kospi_basis_rate",
        "name": "선물 베이시스 비율 [KOSPI 200]",
        "name_en": "KOSPI 200 Basis Rate (%)",
        "description": "선물과 현물 지수의 괴리율 ((F - S) / S * 100). 콘탱고(강세) vs 백워데이션(약세) 강도를 측정합니다.",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        # 체제(느린 맥락) 변수 — 베이시스 '수준'은 몇 주씩 한 값에 머물다 옮겨 간다(2025~26 종가 기준 0 근처 ↔ 9~15,
        # 하루 ±3 이상 이동 40여 회). 짧은 학습창 안에선 '어느 시기냐' 표식이라 선형 IC 가 우연히 나온다
        # (2026-10-20 122630 10s: 09-29 에 0.2→5.9 로 옮겨 간 뒤 하락장과 겹쳐 IC −0.02, 확률 상수 모델).
        # 그래서 IC 대신 블록 일치도 게이트(T-8)로 판정한다. 단기 변화는 kospi_basis_z 가 맡는다.
        "is_regime": True,
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_kospi_basis_rate
    },
    "kospi_basis_z": {
        "id": "kospi_basis_z",
        "name": "선물-현물 베이시스 Z-Score [KOSPI 200]",
        "name_en": "KOSPI 200 Basis Rolling Z-Score",
        "description": "KOSPI 200 선물-현물 베이시스의 N봉 롤링 표준점수(Z-Score). 단기 극단 괴리 및 프로그램 차익거래 선행 유입을 포착합니다.",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        "market_scope": "all",
        "params": {
            "window": {
                "type": "slider",
                "default": 60,
                "min": 10,
                "max": 300,
                "step": 5,
                "label": "롤링 윈도우(봉)",
                "description": "베이시스 평균/표준편차 산출 봉수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_kospi_basis_z
    },
    "stock_basis": {
        "id": "stock_basis",
        "name": "개별주식 선물-현물 베이시스",
        "name_en": "Single Stock Futures Basis (F - S)",
        "description": "개별주식 선물과 현물 주가의 가격 차이(F - S). 기관 차익거래 및 개별주식 선물 선행 수급을 포착합니다.",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        "market_scope": "all",
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_stock_basis
    },
    "stock_basis_rate": {
        "id": "stock_basis_rate",
        "name": "개별주식 선물 베이시스 비율 (%)",
        "name_en": "Single Stock Futures Basis Rate (%)",
        "description": "개별주식 선물과 현물 주가의 괴리율 ((F - S) / S * 100). 콘탱고/백워데이션 강도를 측정합니다.",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        "market_scope": "all",
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_stock_basis_rate
    },
    "stock_basis_z": {
        "id": "stock_basis_z",
        "name": "개별주식 선물-현물 베이시스 Z-Score",
        "name_en": "Single Stock Futures Basis Rolling Z-Score",
        "description": "개별주식 선물-현물 베이시스의 N봉 롤링 표준점수(Z-Score). 순간적인 차익거래 괴리 및 선물 선행 쏠림을 포착합니다.",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        "market_scope": "all",
        "params": {
            "window": {
                "type": "slider",
                "default": 60,
                "min": 10,
                "max": 300,
                "step": 5,
                "label": "롤링 윈도우(봉)",
                "description": "베이시스 평균/표준편차 산출 봉수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_stock_basis_z
    },
    "top2_lead_momentum": {
        "id": "top2_lead_momentum",
        "name": "섹터 Top2 선행 대장주 합성 모멘텀",
        "name_en": "Top 2 Leaders Synthetic Momentum",
        "description": "도킹된 1위(70%) 및 2위(30%) 대장주 가중 수익률 모멘텀 Z-Score. 대장주 선행 시차 포착 (KOSPI/조선/2차전지/반도체 공통).",
        "category": "reference",
        "category_name": "대장주 선행",
        "market_scope": "all",
        "params": {
            "candles": {
                "type": "slider",
                "default": 5,
                "min": 1,
                "max": 20,
                "step": 1,
                "label": "수익률 캔들수",
                "description": "대장주 가격 변화율 측정 봉수"
            },
            "window": {
                "type": "slider",
                "default": 60,
                "min": 10,
                "max": 300,
                "step": 5,
                "label": "Z-Score 윈도우",
                "description": "모멘텀 표준점수 산출 봉수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_top2_lead_momentum
    },
    "semi_duo_lead_delta": {
        "id": "semi_duo_lead_delta",
        "name": "대장주 듀오(삼전+닉스) 선행 델타 Z-Score",
        "name_en": "Semi-Duo Lead Delta Z-Score",
        "description": "반도체 양대산맥(삼성전자 70% + SK하이닉스 30%)의 선행 모멘텀과 타겟 ETF의 괴리율 Z-Score",
        "category": "reference",
        "category_name": "대장주 선행",
        "market_scope": "krx",
        "params": {
            "window_bars": {
                "type": "slider",
                "default": 1,
                "min": 1,
                "max": 10,
                "step": 1,
                "label": "선행 윈도우 봉수",
                "description": "선행 델타 산출용 캔들 수"
            },
            "zscore_bars": {
                "type": "slider",
                "default": 30,
                "min": 10,
                "max": 60,
                "step": 1,
                "label": "Z-Score 윈도우 봉수",
                "description": "정규화 롤링 윈도우"
            },
            "w_sam": {
                "type": "slider",
                "default": 0.70,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "label": "삼성전자 가중치",
                "description": "삼성전자 반영 비율"
            },
            "w_hyn": {
                "type": "slider",
                "default": 0.30,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "label": "SK하이닉스 가중치",
                "description": "SK하이닉스 반영 비율"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_semi_duo_lead_delta
    },
    "parkinson_rv_squeeze": {
        "id": "parkinson_rv_squeeze",
        "name": "Parkinson 실현변동성(RV) 스퀴즈",
        "name_en": "Parkinson Realized Volatility Squeeze",
        "description": "Parkinson 극치 실현변동성 단기/장기 비율의 Z-Score. 횡보 수축 후 변동성 폭발(Breakout) 국면을 포착합니다.",
        "category": "microstructure",
        "category_name": "마이크로스트럭처",
        "market_scope": "all",
        "params": {
            "short_bars": {
                "type": "slider",
                "default": 5,
                "min": 2,
                "max": 15,
                "step": 1,
                "label": "단기 윈도우(봉)",
                "description": "단기 변동성 측정 봉수"
            },
            "long_bars": {
                "type": "slider",
                "default": 30,
                "min": 15,
                "max": 120,
                "step": 5,
                "label": "장기 윈도우(봉)",
                "description": "기준 장기 변동성 측정 봉수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_parkinson_rv_squeeze
    },
    "etf_nav_disparity": {
        "id": "etf_nav_disparity",
        "name": "ETF 괴리율 [KODEX 200 vs NAV]",
        "name_en": "ETF NAV Disparity Ratio (%)",
        "description": "KODEX 200 시장가와 순자산가치(NAV) 간의 괴리율. LP의 스프레드 왜곡 및 가격 정상화 회귀 반등을 포착합니다.",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_etf_nav_disparity
    },
    "overnight_gap_pct": {
        "id": "overnight_gap_pct",
        "name": "장 시작 시가 갭 비율 [09:00 KST]",
        "name_en": "Overnight Gap Ratio (%)",
        "description": "전일 종가 대비 당일 시초가의 갭 상승/하락률. 갭 메우기 또는 갭 돌파 지속 여부를 판별합니다.",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_overnight_gap_pct
    },
    "krx_session_time": {
        "id": "krx_session_time",
        "name": "한국거래소 장중 시간대 세션 [09:00~15:30]",
        "name_en": "KRX Session Time Factor",
        "description": "개장초 변동성 구간(1), 정규 횡보 구간(2), 장마감 리밸런싱 구간(3)을 인코딩합니다.",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        "is_regime": True,
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_krx_session_time
    },
    "krx_session_progress": {
        "id": "krx_session_progress",
        "name": "한국거래소 정규장 진행율 [0.0~1.0/분 단위]",
        "name_en": "KRX Session Progress (0~1)",
        "description": "09:00(0.0) 개장부터 15:30(1.0) 마감까지의 경과율을 분 단위 연속 실수로 인코딩합니다. (주식/ETF 필수)",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": _compute_krx_session_progress
    },
    "krx_opening_decay": {
        "id": "krx_opening_decay",
        "name": "시초가 변동성 지수 감쇠 [09:00 개장 직후]",
        "name_en": "KRX Opening Rush Decay",
        "description": "09:00 개장 직후 폭발하는 시초가 유동성의 지수적 감쇠(1.0→0.05)를 측정하여 초반 진입 강도를 결정합니다.",
        "category": "kospi_etf",
        "category_name": "ETF/선물",
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": _compute_krx_opening_decay
    },

    # ── 해외/가상자산 특화 지표 ──
    "binance_ret_5m": {
        "id": "binance_ret_5m",
        "name": "바이낸스 선행 모멘텀 [현재 분봉]",
        "name_en": "Binance Leading Momentum (Curr. Intv)",
        "description": "설정된 현재 분봉 기준 바이낸스 현물가격의 N캔들 전 대비 수익률. 해외 선행 모멘텀을 반영합니다.",
        "category": "momentum",
        "category_name": "모멘텀",
        "params": {
            "candles": {
                "type": "slider",
                "default": 5,
                "min": 1,
                "max": 60,
                "step": 1,
                "label": "캔들 수",
                "description": "몇 캔들 전 가격과 비교할지 설정"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_binance_ret_5m
    },
    "binance_funding_rate": {
        "id": "binance_funding_rate",
        "name": "바이낸스 펀딩비 [선물시장 롱/숏 청산가]",
        "name_en": "Binance Funding Rate (Futures)",
        "description": "바이낸스 선물 펀딩비. 롱/숏 쏠림과 과열/과냉각 신호를 포착합니다.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "mode": {
                "type": "select",
                "default": "raw",
                "options": ["raw", "zscore", "mom"],
                "label": "변환 방식",
                "description": "raw=펀딩비(% 스케일), zscore=최근 윈도우 대비 과열/냉각, mom=변화율"
            },
            "window": {
                "type": "slider",
                "default": 480,
                "min": 60,
                "max": 1440,
                "step": 10,
                "label": "Z-Score 윈도우(캔들)",
                "description": "zscore 모드에서 평균/표준편차 계산에 사용할 캔들 수"
            },
            "candles": {
                "type": "slider",
                "default": 16,
                "min": 1,
                "max": 96,
                "step": 1,
                "label": "모멘텀 캔들 수",
                "description": "mom 모드에서 펀딩비 변화를 계산할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_binance_funding_rate
    },
    "kimchi_premium": {
        "id": "kimchi_premium",
        "name": "김치 프리미엄 [현재 분봉/혼합]",
        "name_en": "Kimchi Premium (Curr. Intv/Mixed)",
        "description": "실시간 국내/해외 가격 괴리율.",
        "category": "divergence",
        "category_name": "괴리",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 1440,
                "min": 60,
                "max": 4320,
                "step": 60,
                "label": "롤링 윈도우(분)",
                "description": "환율 추정에 사용할 평균 기간(분)"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_kimchi_premium
    },
    "binance_spot_taker_ratio": {
        "id": "binance_spot_taker_ratio",
        "name": "바이낸스 Taker 비율 [현재 분봉]",
        "name_en": "Binance Taker Ratio (Curr. Intv)",
        "description": "현재 분봉 기준 바이낸스 시장가 체결 중 매수 비율.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 10,
                "min": 1,
                "max": 120,
                "step": 1,
                "label": "롤링 윈도우(분)",
                "description": "누적 수급을 집계할 롤링 시간(분)"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_binance_spot_taker_ratio
    },
    "binance_oi_change_ratio": {
        "id": "binance_oi_change_ratio",
        "name": "바이낸스 미결제약정 변화율 [현재 분봉]",
        "name_en": "Binance OI Change Ratio (Curr. Intv)",
        "description": "바이낸스 선물 미결제 약정의 N캔들 전 대비 변화율.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 30,
                "min": 5,
                "max": 240,
                "step": 5,
                "label": "비교 윈도우(분)",
                "description": "몇 분 전 미결제 약정과 비교할지 설정"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_binance_oi_change_ratio
    },
    "binance_oi_ma_ratio": {
        "id": "binance_oi_ma_ratio",
        "name": "바이낸스 미결제약정 이격도 [현재 분봉]",
        "name_en": "Binance OI MA Ratio (Curr. Intv)",
        "description": "바이낸스 선물 미결제 약정과 N캔들 이동평균의 괴리율.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 120,
                "min": 10,
                "max": 480,
                "step": 10,
                "label": "MA 기간(캔들)",
                "description": "평균 미결제 약정을 계산할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_binance_oi_ma_ratio
    },
    "binance_cvd_momentum": {
        "id": "binance_cvd_momentum",
        "name": "바이낸스 CVD 모멘텀 [현재 분봉]",
        "name_en": "Binance CVD Momentum (Curr. Intv)",
        "description": "바이낸스 현물 CVD의 N캔들 순수시장가매수 비율.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 30,
                "min": 5,
                "max": 240,
                "step": 5,
                "label": "모멘텀 윈도우(분)",
                "description": "누적 수급을 집계할 롤링 시간(분)"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_binance_cvd_momentum
    },
    "binance_cvd_divergence": {
        "id": "binance_cvd_divergence",
        "name": "바이낸스 CVD 다이버전스 [현재 분봉]",
        "name_en": "Binance CVD Divergence (Curr. Intv)",
        "description": "단기 vs 장기 CVD의 이격 및 괴리 수준.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "fast_period": {
                "type": "slider",
                "default": 15,
                "min": 5,
                "max": 30,
                "step": 1,
                "label": "단기 윈도우"
            },
            "slow_period": {
                "type": "slider",
                "default": 120,
                "min": 60,
                "max": 240,
                "step": 10,
                "label": "장기 윈도우"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_binance_cvd_divergence
    },
    "binance_usdc_volume_share": {
        "id": "binance_usdc_volume_share",
        "name": "바이낸스 USDC 거래량 점유율 [현재 분봉]",
        "name_en": "Binance USDC Volume Share (Curr. Intv)",
        "description": "USDT 대비 USDC 마켓의 거래량 점유율 비율.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 60,
                "min": 10,
                "max": 240,
                "step": 10,
                "label": "점유율 윈도우(분)",
                "description": "점유율을 집계할 롤링 시간(분)"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_binance_usdc_volume_share
    },
    "binance_futures_spot_vol_ratio": {
        "id": "binance_futures_spot_vol_ratio",
        "name": "바이낸스 선물/현물 거래량 비율",
        "name_en": "Binance Futures/Spot Vol Ratio",
        "description": "현물 대비 선물 시장의 실시간 거래 대금 배율.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 60,
                "min": 10,
                "max": 240,
                "step": 10,
                "label": "평균 윈도우(분)"
            }
        },
        "enabled": False,
        "has_params": True,
        "compute": _compute_binance_futures_spot_vol_ratio
    },
    "binance_oi_value_ma_ratio": {
        "id": "binance_oi_value_ma_ratio",
        "name": "바이낸스 미결제약정 가치 이격도 [현재 분봉]",
        "name_en": "Binance OI Value MA Ratio (Curr. Intv)",
        "description": "현재 OI 달러 가치와 N캔들 이동평균의 괴리율.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 120,
                "min": 10,
                "max": 480,
                "step": 10,
                "label": "MA 기간(캔들)",
                "description": "평균 OI 가치를 계산할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_binance_oi_value_ma_ratio
    },
    "binance_leading_momentum": {
        "id": "binance_leading_momentum",
        "name": "바이낸스 주도 모멘텀 [현재 분봉]",
        "name_en": "Binance Leading Momentum (Curr. Intv)",
        "description": "바이낸스 수익률 - 국내 타겟 수익률 괴리.",
        "category": "momentum",
        "category_name": "모멘텀",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 20,
                "min": 5,
                "max": 120,
                "step": 5,
                "label": "괴리율 윈도우(분)",
                "description": "몇 분 전과 비교할지 설정"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_binance_leading_momentum
    },
    "binance_spot_quote_spike": {
        "id": "binance_spot_quote_spike",
        "name": "바이낸스 달러 대금 급증 [현재 분봉]",
        "name_en": "Binance Quote Volume Spike (Curr. Intv)",
        "description": "현재 봉 달러 대금 / N캔들 평균 대금 배율.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "period_bars": {
                "type": "slider",
                "default": 20,
                "min": 5,
                "max": 60,
                "step": 1,
                "label": "평균 기간(캔들)",
                "description": "평균 달러 대금을 계산할 캔들 수"
            }
        },
        "enabled": True,
        "is_trigger": True,
        "has_params": True,
        "compute": _compute_binance_spot_quote_spike
    },
    "binance_spot_trade_frequency": {
        "id": "binance_spot_trade_frequency",
        "name": "바이낸스 체결 빈도 [현재 분봉]",
        "name_en": "Binance Trade Frequency (Curr. Intv)",
        "description": "봉당 체결 건수.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": _compute_binance_spot_trade_frequency
    },
    "binance_spot_taker_quote_ratio": {
        "id": "binance_spot_taker_quote_ratio",
        "name": "바이낸스 대금 기준 Taker 매수 비율 [현재 분봉]",
        "name_en": "Binance Taker Quote Ratio (Curr. Intv)",
        "description": "달러 금액 기준 Taker 매수 비율(0~1).",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": _compute_binance_spot_taker_quote_ratio
    },
    "coinbase_premium_index": {
        "id": "coinbase_premium_index",
        "name": "코인베이스 프리미엄 지수 [현재 분봉]",
        "name_en": "Coinbase Premium Index",
        "description": "미국 기관 자금 유입 측정용 코인베이스 BTC/USD vs 바이낸스 BTC/USDT 괴리율.",
        "category": "divergence",
        "category_name": "괴리",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 10,
                "min": 1,
                "max": 60,
                "step": 1,
                "label": "롤링 윈도우(분)",
                "description": "프리미엄 평활화 시간(분)"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_coinbase_premium_index
    },
    "eth_btc_relative_momentum": {
        "id": "eth_btc_relative_momentum",
        "name": "ETH/BTC 상대강도 모멘텀 [현재 분봉]",
        "name_en": "ETH/BTC Relative Momentum",
        "description": "알트코인/이더 대비 비트코인 모멘텀 괴리 (시장 위험선호도).",
        "category": "momentum",
        "category_name": "모멘텀",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 20,
                "min": 5,
                "max": 120,
                "step": 5,
                "label": "모멘텀 윈도우(분)",
                "description": "수익률 비교 시간(분)"
            }
        },
        "enabled": False,
        "has_params": True,
        "compute": _compute_eth_btc_relative_momentum
    },
    "binance_liq_cascade_ratio": {
        "id": "binance_liq_cascade_ratio",
        "name": "선물 강제 청산 비율 [현재 분봉]",
        "name_en": "Futures Liquidation Cascade Ratio",
        "description": "바이낸스 선물 롱/숏 강제 청산 폭포 감지 지표.",
        "category": "orderflow",
        "category_name": "오더플로우",
        "params": {
            "window_mins": {
                "type": "slider",
                "default": 30,
                "min": 5,
                "max": 120,
                "step": 5,
                "label": "청산 누적 윈도우(분)",
                "description": "청산액 롤링 누적 시간(분)"
            }
        },
        "enabled": False,
        "has_params": True,
        "compute": _compute_binance_liq_cascade_ratio
    },
    "crypto_usdt_return": {
        "id": "crypto_usdt_return",
        "name": "테더(USDT) 변화율 [KRW 유동성 쏠림]",
        "name_en": "Crypto USDT Return",
        "description": "KRW-USDT 마켓 실거래가의 N캔들 변화율. 원화 코인 시장 유입 속도를 포착합니다.",
        "category": "divergence",
        "category_name": "괴리",
        "params": {
            "candles": {
                "type": "slider",
                "default": 2,
                "min": 1,
                "max": 10,
                "step": 1,
                "label": "캔들 수",
                "description": "몇 캔들 전 테더 가격과 비교할지 설정"
            }
        },
        "enabled": False,
        "has_params": True,
        "compute": _compute_crypto_usdt_krw_ret
    },
    "binance_funding_settle_window": {
        "id": "binance_funding_settle_window",
        "name": "펀딩비 정산 잔여 캔들 [30분봉]",
        "name_en": "Funding Rate Settle Window (30m)",
        "description": "다음 바이낸스 선물 펀딩비 정산까지 남은 30분봉 캔들 수.",
        "category": "time",
        "category_name": "시간",
        "is_regime": True,
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_binance_funding_settle_window
    },
    "crypto_0900_open_sync": {
        "id": "crypto_0900_open_sync",
        "name": "09:00 KST 시초가 동기화 [국내-해외]",
        "name_en": "09:00 KST Open Sync Index",
        "description": "국내 일봉 시초가(09:00 KST) 대비 현재 수익률과 바이낸스 동일 기준 수익률의 차이.",
        "category": "divergence",
        "category_name": "괴리",
        "params": {},
        "enabled": False,
        "has_params": False,
        "compute": _compute_crypto_0900_sync
    },
    # ── 미국 CME 글로벌 선물 선행 지표 (전 자산 공통 매크로) ──
    "nvda_futures_ret_curr": {
        "id": "nvda_futures_ret_curr",
        "name": "엔비디아 선물 단기 모멘텀 [현재 분봉]",
        "name_en": "NVDA Futures Return (Curr. Intv)",
        "description": "엔비디아 24시간 선물(NVDA-PERP)의 최근 N캔들 수익률. 국내 반도체(SK하이닉스/삼성전자) 선행 방향성을 포착합니다.",
        "category": "global_lead_lag",
        "category_name": "글로벌선행",
        "market_scope": "all",
        "params": {
            "candles": {
                "type": "slider",
                "default": 5,
                "min": 1,
                "max": 30,
                "step": 1,
                "label": "수익률 캔들 수",
                "description": "엔비디아 선물 수익률 계산에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_nvda_futures_ret_curr
    },
    "us10y_futures_ret_curr": {
        "id": "us10y_futures_ret_curr",
        "name": "미 국채 10년물 선물 단기 변동률 [현재 분봉]",
        "name_en": "US 10Y Bond Futures Return (Curr. Intv)",
        "description": "미국 10년물 국채 선물(US10Y)의 최근 N캔들 변동률. 시장금리 발작 및 긴축 충격을 조기 감지합니다.",
        "category": "global_lead_lag",
        "category_name": "글로벌선행",
        "market_scope": "all",
        "params": {
            "candles": {
                "type": "slider",
                "default": 5,
                "min": 1,
                "max": 30,
                "step": 1,
                "label": "변동률 캔들 수",
                "description": "국채 선물 변동률 계산에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_us10y_futures_ret_curr
    },
    "wti_futures_ret_curr": {
        "id": "wti_futures_ret_curr",
        "name": "WTI 유가 선물 단기 모멘텀 [현재 분봉]",
        "name_en": "WTI Crude Oil Futures Return (Curr. Intv)",
        "description": "WTI 원유 선물의 최근 N캔들 수익률. 지정학적 위기 및 인플레이션 급등 블랙스완을 감지합니다.",
        "category": "global_lead_lag",
        "category_name": "글로벌선행",
        "market_scope": "all",
        "params": {
            "candles": {
                "type": "slider",
                "default": 5,
                "min": 1,
                "max": 30,
                "step": 1,
                "label": "수익률 캔들 수",
                "description": "원유 선물 수익률 계산에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_wti_futures_ret_curr
    },
    "gold_futures_ret_curr": {
        "id": "gold_futures_ret_curr",
        "name": "금(Gold) 선물 단기 모멘텀 [현재 분봉]",
        "name_en": "Gold Futures Return (Curr. Intv)",
        "description": "골드(금) 선물의 최근 N캔들 수익률. 글로벌 위험회피(Risk-Off) 안전자산 도피를 포착합니다.",
        "category": "global_lead_lag",
        "category_name": "글로벌선행",
        "market_scope": "all",
        "params": {
            "candles": {
                "type": "slider",
                "default": 5,
                "min": 1,
                "max": 30,
                "step": 1,
                "label": "수익률 캔들 수",
                "description": "금 선물 수익률 계산에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_gold_futures_ret_curr
    },
    "usdkrw_ret_curr": {
        "id": "usdkrw_ret_curr",
        "name": "원/달러 실시간 환율 변동률 [현재 분봉]",
        "name_en": "USD/KRW FX Return (Curr. Intv)",
        "description": "원/달러 실시간 환율의 최근 N캔들 변동률. 외국인 수급 변동 및 원화 급락 충격을 포착합니다.",
        "category": "global_lead_lag",
        "category_name": "글로벌선행",
        "market_scope": "all",
        "params": {
            "candles": {
                "type": "slider",
                "default": 5,
                "min": 1,
                "max": 30,
                "step": 1,
                "label": "변동률 캔들 수",
                "description": "환율 변동률 계산에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": _compute_usdkrw_ret_curr
    },
}


# ==============================================================================
# [입력 유효성 사전검사] 외부/참조 데이터에 의존하는 지표의 입력 원장 (2026-10-02)
#
# 이 지표들은 입력 컬럼이 없으면 `df.get(col, 기본값)` 으로 조용히 대체값을 넣고 계산을 강행한다.
# 결과가 상수/결측이면 사후 검사(find_dead_features)가 잡지만, 대체값이 타겟과 섞이면 잡지 못한다.
# 실측: 069500(바이낸스 없음)에서 kimchi_premium = target_close * 1e8 - 1 — 상수도 NaN 도 아닌 타겟의
# 선형 복제라 사후 가드를 통과했고, KODEX200_M1_TEST2 의 enabled_features 에 실제로 들어가 있었다.
# 출력값만으로는 오염 여부를 구별할 수 없으므로, **계산 전에** 입력이 실제로 있는지 본다.
#
# 규칙 (T-6 규약 — 없는 관측을 0/중립값으로 지어내지 않는다):
#   - 필수 입력이 통째로 없으면 계산하지 않고 전부 NaN → find_dead_features 가 '전부 결측 — 입력 없음' 으로 제외.
#   - 일부 행만 없으면(수집 시작 전 0 채움 등) 그 행의 출력만 NaN → 결측이 시기에 몰리면
#     find_unstable_availability 가 제외하고, 고르게 흩어져 있으면 LightGBM 이 결측으로 다룬다.
# compute 자체를 감싸므로 feature_engine 뿐 아니라 compute 를 직접 부르는 스크리너/볼트/튜너 경로도 같이 막힌다.
# 래칫: tests/test_feature_input_requirements.py 가 compute 소스의 외부 컬럼 참조가 모두 여기 선언됐는지 검사한다.
# ==============================================================================

class Need:
    """필수 입력 하나 — `cols` 중 하나라도 유효하면 충족.

    ref_types: 원장 market_type 접두어로 찾는 참조 종목 컬럼(ref_{key}_{ref_field})도 후보로 인정한다
               (컬럼 이름이 아니라 원장 유형으로 입력을 찾는 지표용).
    zero_is_missing: 값 0 을 '없음'으로 본다. 가격/환율/미결제약정/펀딩비처럼 0 이 실제 관측일 수 없는
                     수준값에 쓴다 (실전 수집기는 미수신 바이낸스 가격을 0.0 으로 채운다).
    """
    __slots__ = ("cols", "ref_field", "ref_types", "ref_requires", "ref_excludes", "zero_is_missing")

    def __init__(self, *cols, ref_field="close", ref_types=(), ref_requires=None, ref_excludes=None, zero_is_missing=False):
        self.cols = tuple(cols)
        self.ref_field = ref_field
        self.ref_types = tuple(ref_types)
        self.ref_requires = ref_requires
        self.ref_excludes = ref_excludes
        self.zero_is_missing = zero_is_missing

    def candidates(self, columns) -> list:
        found = [c for c in self.cols if c in columns]
        if self.ref_types:
            from shared.config.config_manager import find_ref_columns
            found = find_ref_columns(columns, self.ref_field, self.ref_types, require_field=self.ref_requires, exclude_field=self.ref_excludes) + found
        return found

    def describe(self) -> str:
        parts = list(self.cols)
        if self.ref_types:
            parts.insert(0, f"ref_*_{self.ref_field}[{'/'.join(self.ref_types)}]")
        return "|".join(parts)


def _observed_values(s: pd.Series, zero_is_missing: bool) -> pd.Series:
    """관측값만 남기고 '없음'(NaN/inf, zero_is_missing 이면 0 포함)은 NaN 으로 둔 숫자 시리즈."""
    v = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
    return v.where(v != 0) if zero_is_missing else v


def _column_has_data(s: pd.Series, zero_is_missing: bool) -> bool:
    return bool(_observed_values(s, zero_is_missing).notna().any())


# 바이낸스 현물가 — 크립토 지표 대부분의 공통 입력
_BINANCE_PX = Need('binance_spot_close', zero_is_missing=True)

FEATURE_INPUT_REQUIREMENTS = {
    # ── 바이낸스 현물/선물 ──
    "binance_ret_5m":                 [_BINANCE_PX],
    "binance_leading_momentum":       [_BINANCE_PX],
    "crypto_0900_open_sync":          [_BINANCE_PX],
    # KRW-USDT 실거래가는 선택 입력 — 없으면 타겟/바이낸스 롤링 역산 환율로 근사한다(문서화된 폴백).
    # 바이낸스가 없으면 그 근사가 target*1e8 로 붕괴하므로 바이낸스만 필수.
    "kimchi_premium":                 [_BINANCE_PX],
    "coinbase_premium_index":         [_BINANCE_PX, Need('coinbase_spot_close', zero_is_missing=True)],
    # 두 계열이 같은 시리즈(binance_spot_close)로 채워지면 0 상수가 된다 — ETH 또는 BTC 벤치마크 중 하나는 별도 계열이어야 한다
    "eth_btc_relative_momentum":      [Need('binance_eth_close', 'binance_spot_close', zero_is_missing=True),
                                       Need('benchmark_btc_close', 'binance_spot_close', zero_is_missing=True),
                                       Need('binance_eth_close', 'benchmark_btc_close', zero_is_missing=True)],
    "binance_funding_rate":           [Need('binance_funding_rate', zero_is_missing=True)],
    "binance_spot_taker_ratio":       [Need('binance_spot_taker_ratio')],
    "binance_oi_change_ratio":        [Need('binance_futures_oi', zero_is_missing=True)],
    "binance_oi_ma_ratio":            [Need('binance_futures_oi', zero_is_missing=True)],
    "binance_oi_value_ma_ratio":      [Need('binance_futures_oi_value', zero_is_missing=True)],
    "binance_cvd_momentum":           [Need('binance_spot_volume'), Need('binance_spot_taker_ratio')],
    "binance_cvd_divergence":         [Need('binance_spot_volume'), Need('binance_spot_taker_ratio')],
    "binance_usdc_volume_share":      [Need('binance_spot_volume'), Need('binance_usdc_volume')],
    "binance_futures_spot_vol_ratio": [Need('binance_futures_volume'), Need('binance_spot_volume')],
    "binance_spot_quote_spike":       [Need('binance_spot_quote_volume')],
    "binance_spot_trade_frequency":   [Need('binance_spot_trade_count')],
    "binance_spot_taker_quote_ratio": [Need('binance_spot_taker_quote_ratio')],
    "binance_liq_cascade_ratio":      [Need('binance_futures_long_liq'), Need('binance_futures_short_liq')],
    "crypto_usdt_return":             [Need('crypto_usdt_close', 'upbit_usdt_close', zero_is_missing=True)],
    # ── 글로벌 매크로 ──
    "nvda_futures_ret_curr":          [Need('nvda_futures_close', zero_is_missing=True)],
    "us10y_futures_ret_curr":         [Need('us10y_futures_close', zero_is_missing=True)],
    "wti_futures_ret_curr":           [Need('wti_futures_close', zero_is_missing=True)],
    "gold_futures_ret_curr":          [Need('gold_futures_close', zero_is_missing=True)],
    "usdkrw_ret_curr":                [Need('usd_krw_fx', zero_is_missing=True)],
    # ── KOSPI200 선물/지수/ETF/대장주 (원장 유형 우선, 라이브 고정 컬럼명 폴백 — _kospi_futures_and_spot 와 같은 순서) ──
    "kospi_basis":      [Need('futures_close', 'kospi_futures_close', ref_types=("KRX_FUTURES",), ref_excludes="underlying", zero_is_missing=True),
                         Need('spot_close', ref_types=("KRX_INDEX",), zero_is_missing=True)],
    "kospi_basis_rate": [Need('futures_close', 'kospi_futures_close', ref_types=("KRX_FUTURES",), ref_excludes="underlying", zero_is_missing=True),
                         Need('spot_close', ref_types=("KRX_INDEX",), zero_is_missing=True)],
    "kospi_basis_z":    [Need('futures_close', 'kospi_futures_close', ref_types=("KRX_FUTURES",), ref_excludes="underlying", zero_is_missing=True),
                         Need('spot_close', ref_types=("KRX_INDEX",), zero_is_missing=True)],
    "stock_basis":      [Need(ref_types=("KRX_FUTURES",), ref_requires="underlying", zero_is_missing=True)],
    "stock_basis_rate": [Need(ref_types=("KRX_FUTURES",), ref_requires="underlying", zero_is_missing=True)],
    "stock_basis_z":    [Need(ref_types=("KRX_FUTURES",), ref_requires="underlying", zero_is_missing=True)],
    # NAV 가 없으면 NAV=가격으로 대체돼 괴리율이 0 상수가 된다
    "etf_nav_disparity":   [Need('nav', 'etf_nav', zero_is_missing=True)],
    "top2_lead_momentum":  [Need(ref_types=("KRX_STOCK",), zero_is_missing=True)],
    "semi_duo_lead_delta": [Need('ref_005930_close', 'ref_samsung_close', 'ref_000660_close', 'ref_hynix_close',
                                 zero_is_missing=True)],
    # ── 타겟 체결 미세구조 (틱 집계 봉에만 있다 — 069500 1m·KRW-BTC 15m 정규화는 OHLCV 뿐) ──
    # 입력이 없으면 0/0.5 상수가 되어 '상수(값이 하나뿐)'로만 걸러졌다 — 무엇이 빠졌는지 사유에 나오게 선언한다 (2026-10-08)
    "target_ofi_cumsum":            [Need('target_ofi', 'ofi', 'target_bid_volume', 'bid_volume')],
    "target_large_trade_density":   [Need('target_is_large_trade', 'is_large_trade', 'target_large_trade_count')],
    "target_trade_bid_ratio":       [Need('target_trade_bid_ratio', 'upbit_trade_bid_ratio')],
    "target_cvd_momentum":          [Need('target_trade_bid_ratio', 'upbit_trade_bid_ratio')],
    "target_cvd_absorption":        [Need('target_trade_bid_ratio', 'upbit_trade_bid_ratio')],
    "target_obv_momentum":          [Need('target_trade_bid_ratio', 'upbit_trade_bid_ratio')],
    "target_trade_frequency":       [Need('target_trade_count', 'upbit_trade_count')],
    "target_large_trade_ratio":     [Need('target_trade_count', 'upbit_trade_count'),
                                     Need('target_large_trade_count', 'upbit_large_trade_count')],
    "target_avg_trade_size_change": [Need('target_avg_trade_size', 'upbit_avg_trade_size')],
    "target_trade_price_spike":     [Need('target_trade_price', 'upbit_trade_price')],
    "target_is_auction":            [Need('target_is_auction', 'is_auction')],
}

# 없어도 계산 의미가 유지되는 선택 입력 (문서화된 폴백) — 래칫이 '미선언 외부 컬럼'으로 보지 않도록 명시한다.
# 여기 올리려면 폴백이 타겟/다른 입력의 대체값이 아니라 의미 있는 근사라는 근거가 있어야 한다.
FEATURE_OPTIONAL_INPUTS = {
    "kimchi_premium": ("crypto_usdt_close", "upbit_usdt_close"),   # 없으면 타겟/바이낸스 롤링 역산 환율로 근사
}


def find_missing_inputs(feature_id: str, df: pd.DataFrame) -> list:
    """지표의 필수 입력 중 df 에 없거나 값이 전부 비어 있는 것의 설명 목록 (충족하면 빈 리스트, 선언 없으면 빈 리스트)."""
    needs = FEATURE_INPUT_REQUIREMENTS.get(FEATURE_ALIASES.get(feature_id, feature_id))
    if not needs:
        return []
    missing = []
    for need in needs:
        if not any(_column_has_data(df[c], need.zero_is_missing) for c in need.candidates(df.columns)):
            missing.append(need.describe())
    return missing


def _mask_unobserved_input_rows(feature_id: str, df: pd.DataFrame):
    """행 단위 입력 마스킹 — (계산용 df, 입력이 관측된 행 마스크) 를 돌려준다.

    - zero_is_missing 입력의 0 은 계산 전에 NaN 으로 바꾼다 (원본 df 는 건드리지 않는다).
      실측(KRW-BTC 15m): binance_futures_oi 는 수집 시작(2026-09-25) 전 90,076행이 0 으로 채워져 있었고
      컬럼 단위 검사는 실값 619행 때문에 통과시켰다 — 앞 99% 가 0 기반의 가짜 지표값이었다.
    - 필수 입력 중 하나라도 관측되지 않은 행은 출력도 NaN 이다. 지표 함수 안의 fillna(0)/중립값이
      그 행을 다시 지어내지 못하게 출력에서 덮는다.
    - 이어 채우기(as-of)는 하지 않는다. 그 판단(허용 지연 등)은 참조 결합(attach_reference) 계층의 몫이고,
      결합 후에도 비어 있는 행은 데이터 계층이 '없음'으로 판정한 것이다.
    """
    needs = FEATURE_INPUT_REQUIREMENTS.get(FEATURE_ALIASES.get(feature_id, feature_id)) or []
    work = df
    observed = np.ones(len(df), dtype=bool)
    for need in needs:
        need_obs = np.zeros(len(df), dtype=bool)
        for c in need.candidates(df.columns):
            v = _observed_values(df[c], need.zero_is_missing)
            if need.zero_is_missing and v.isna().sum() != df[c].isna().sum():
                if work is df:
                    work = df.copy(deep=False)
                work[c] = v
            need_obs |= v.notna().to_numpy()
        observed &= need_obs
    return work, observed


_INPUT_GAP_LOGGED = set()


def _guard_compute(feature_id: str, compute_fn):
    """compute 를 감싸 입력 유효성을 계산 전에 강제한다.
    필수 입력이 통째로 없으면 계산하지 않고 전부 NaN, 일부 행만 없으면 그 행의 출력만 NaN."""
    import functools

    @functools.wraps(compute_fn)
    def guarded(df, params, interval):
        missing = find_missing_inputs(feature_id, df)
        if missing:
            key = (feature_id, tuple(missing))
            if key not in _INPUT_GAP_LOGGED:
                _INPUT_GAP_LOGGED.add(key)
                logging.warning(f"[FeatureInput] {feature_id}: 필수 입력 없음 {missing} → 계산 생략, 전부 NaN")
            return pd.Series(np.nan, index=df.index)
        work, observed = _mask_unobserved_input_rows(feature_id, df)
        out = compute_fn(work, params, interval)
        if not observed.all():
            out = pd.Series(out, index=df.index).astype(float).where(observed)
        return out

    guarded.input_guarded = True
    return guarded


for _fid in FEATURE_INPUT_REQUIREMENTS:
    _fdef = DEFAULT_FEATURE_REGISTRY[_fid]   # 원장에 있는데 레지스트리에 없으면 import 시점에 바로 실패한다
    if not getattr(_fdef["compute"], "input_guarded", False):
        _fdef["compute"] = _guard_compute(_fid, _fdef["compute"])


# 📌 [하위 호환성 등록]: 레거시 upbit_* 키로 DEFAULT_FEATURE_REGISTRY를 직접 조회해도 동일한 정의를 찾을 수 있도록 자동 주입
for _legacy_id, _canon_id in FEATURE_ALIASES.items():
    if _canon_id in DEFAULT_FEATURE_REGISTRY and _legacy_id not in DEFAULT_FEATURE_REGISTRY:
        _alias_item = DEFAULT_FEATURE_REGISTRY[_canon_id].copy()
        _alias_item["id"] = _legacy_id
        _alias_item["is_alias"] = True
        _alias_item["canonical_id"] = _canon_id
        DEFAULT_FEATURE_REGISTRY[_legacy_id] = _alias_item


# Optuna 기본 설정
DEFAULT_OPTUNA_CONFIG = {
    "outer_trials": 15,
    "inner_trials": 15,
    "predict_ahead_candles_range": {"min": 8, "max": 32, "user_min": 8, "user_max": 32},
    "max_hold_candles_range": {"min": 24, "max": 64, "user_min": 24, "user_max": 64},
    "take_profit_range": {"min": 1.0, "max": 4.0, "step": 0.1, "user_min": 1.5, "user_max": 3.5},
    "stop_loss_range": {"min": 0.5, "max": 3.0, "step": 0.1, "user_min": 0.8, "user_max": 2.0},
    "learning_rate_range": {"min": 0.01, "max": 0.10, "user_min": 0.02, "user_max": 0.08},
    "max_depth_range": {"min": 3, "max": 10, "user_min": 4, "user_max": 7},
    "num_leaves_range": {"min": 15, "max": 127, "user_min": 31, "user_max": 63},
    "min_child_samples_range": {"min": 10, "max": 100, "user_min": 20, "user_max": 80},
    "buy_threshold_range": {"min": 0.50, "max": 0.65, "user_min": 0.52, "user_max": 0.57},   # threshold_mode='absolute' 전용
    "threshold_mode": "quantile",
    "buy_quantile_range": {"min": 0.80, "max": 0.995, "step": 0.005, "user_min": 0.80, "user_max": 0.995},
    "cooldown_minutes_range": {"min": 1, "max": 30, "user_min": 4, "user_max": 16},
    "macro_rsi_threshold_range": {"min": 5.0, "max": 85.0, "user_min": 10.0, "user_max": 80.0},
    "min_trades": 15,
    "min_trades_abs": 10,
    "pf_score_cap": 20.0,
    "min_holdout_trading_days": 5,
    "standard_holdout_trades": 30,
    "min_profit_factor": 1.1,
    "enable_early_stop": True,
    "max_penalty_patience": 5,
    "wfa_enabled": False,
    "wfa_train_months": 12,
    "wfa_opt_months": 3,
    "wfa_test_months": 3,
    "fee_upbit": 0.0005,
    "slippage": 0.0006
}

# 카테고리 정보
CATEGORY_INFO = {
    "target": {"name": "타겟 종목", "icon": "🎯", "description": "매매 대상 종목(티커) 자체 시세/체결/수급 기반 지표"},
    "microstructure": {"name": "마이크로스트럭처", "icon": "🔬", "description": "VWAP, OFI(주문흐름), Parkinson RV, 대량 체결 등 고주파 틱 미세구조 팩터"},
    "momentum": {"name": "모멘텀", "icon": "📊", "description": "가격 변동의 속도와 강도"},
    "divergence": {"name": "괴리", "icon": "💱", "description": "두 자산 간 가격 차이"},
    "trend": {"name": "추세", "icon": "📈", "description": "가격의 방향성"},
    "oscillator": {"name": "오실레이터", "icon": "📉", "description": "과매수/과매도 판단"},
    "orderflow": {"name": "오더플로우", "icon": "💰", "description": "체결 데이터 기반"},
    "time": {"name": "시간", "icon": "🕐", "description": "시간 기반 패턴"},
    "kospi_etf": {"name": "ETF/선물", "icon": "🏛️", "description": "KOSPI200 선물/베이시스/NAV 괴리 기반"},
    "reference": {"name": "대장주 선행", "icon": "⚡", "description": "참조 대장주(Reference Instrument) 선행-지연 괴리율 및 모멘텀"}
}

# 참조 종목마다 자동 등록되는 4대 제네릭 선행 피처의 종류 (피처 id = ref_{key}_{kind}) — 이 튜플이 단일 정의
REFERENCE_FEATURE_KINDS = ("ret_1c", "ret_5c", "spread_vs_target", "intensity")
_REF_FEATURE_ID_RE = re.compile(r"^ref_(?P<key>.+)_(?P<kind>" + "|".join(REFERENCE_FEATURE_KINDS) + r")$")


def parse_reference_feature_id(feature_id: str):
    """참조 피처 id(ref_{key}_{kind})를 (key, kind) 로 분해한다. 참조 피처 id 가 아니면 None."""
    m = _REF_FEATURE_ID_RE.match(str(feature_id or ""))
    return (m.group("key"), m.group("kind")) if m else None


def ensure_reference_feature(feature_id: str, registry: dict = None) -> bool:
    """참조 피처 id 가 레지스트리에 없으면 id 에서 종목 키를 읽어 등록한다 (등록 순서에 의존하지 않기 위함).
    등록되어 있거나 등록에 성공하면 True, 참조 피처 id 가 아니면 False."""
    if registry is None:
        registry = DEFAULT_FEATURE_REGISTRY
    parsed = parse_reference_feature_id(feature_id)
    if not parsed:
        return False
    ref_sym = parsed[0]
    # 🛡️ 유령 접미사 차단: _target 또는 _upbit 로 끝나는 레거시 가짜 심볼 등록 거부
    if ref_sym.endswith("_target") or ref_sym.endswith("_upbit"):
        return False
    if feature_id not in registry:
        register_reference_features(ref_sym, registry)
    return feature_id in registry


def purge_ghost_features(registry: dict = None) -> int:
    """레거시 별칭으로 인해 잘못 등록된 유령 피처(_target, _upbit)를 레지스트리에서 영구 정화"""
    if registry is None:
        registry = DEFAULT_FEATURE_REGISTRY
    ghost_keys = [
        k for k in list(registry.keys())
        if "_target_" in k or "_upbit_" in k or k.endswith("_target") or k.endswith("_upbit")
    ]
    for k in ghost_keys:
        registry.pop(k, None)
    return len(ghost_keys)


def register_configured_reference_features(config: dict = None, registry: dict = None) -> list:
    """설정에 선언된 참조 종목 전부의 참조 피처를 등록한다 (선언 원장이 유일한 출처).

    참조 피처는 지연 등록이라, 등록 루프를 각 화면/모듈이 따로 들고 있으면 "그 프로세스에서
    누가 먼저 돌았는지"가 결과를 바꾼다. 실제로 지표 스크리닝 화면에는 이 루프가 있고
    자동연구에는 없어, 같은 설정인데 화면에서는 참조 피처가 보이고 자동연구에서는 후보에
    들지도 못했다. 그래서 이 함수 하나가 그 루프를 소유한다.
    """
    if registry is None:
        registry = DEFAULT_FEATURE_REGISTRY
    if config is None:
        from shared.config.config_manager import load_project_config
        config = load_project_config("CAiModelBaker")

    declared = config.get("reference_symbols") or config.get("REFERENCE_SYMBOLS") or []
    registered = []
    for ref in declared:
        sym = ref.get("symbol") if isinstance(ref, dict) else ref
        if not sym:
            continue
        name = ref.get("name") if isinstance(ref, dict) else None
        try:
            registered.extend(register_reference_features(sym, registry, display_name=name))
        except Exception as e:
            logging.warning(f"[FeatureRegistry] 참조 종목 {sym} 피처 등록 실패: {e}")
    return registered


def register_reference_features(symbol: str, registry: dict = None, display_name: str = None) -> list:
    """
    참조 종목에 대한 4대 제네릭 선행 피처를 레지스트리에 자동 등록한다.
    컬럼/피처 id 는 원장 심볼에서 유도한 키(config_manager.ref_key)만 쓴다 — 별칭 없음.
    어떤 종목이든 제네릭 4대 함수를 100% 재사용하며 함수 정의(def)는 증가하지 않는다.
    """
    from shared.config.config_manager import ref_key, ref_prefix, symbol_from_ref_key, get_symbol_name
    if registry is None:
        registry = DEFAULT_FEATURE_REGISTRY

    alias_clean = ref_key(symbol)
    # 🛡️ 유령 접미사 차단: _target 또는 _upbit 로 끝나는 레거시 가짜 심볼 등록 거부
    if alias_clean.endswith("_target") or alias_clean.endswith("_upbit"):
        return []

    _pfx = ref_prefix(symbol)
    col_close = f"{_pfx}close"
    col_vol = f"{_pfx}volume"
    col_bid_vol = f"{_pfx}bid_vol"

    # 표시명: 호출자가 준 이름 > 원장 이름 > 키. 이름 사전을 코드에 두지 않는다.
    disp_name = None
    if display_name and str(display_name).strip() and str(display_name).strip().lower() != alias_clean:
        disp_name = str(display_name).strip()
    if not disp_name:
        ledger_sym = symbol_from_ref_key(alias_clean)
        disp_name = (get_symbol_name(ledger_sym) if ledger_sym else None) or alias_clean.upper()

    registered_ids = []
    
    # 1. 1봉 수익률 (속도)
    f1_id = f"ref_{alias_clean}_ret_1c"
    registry[f1_id] = {
        "id": f1_id,
        "name": f"[{disp_name}] 1봉 수익률 (속도)",
        "name_en": f"[{alias_clean.upper()}] 1-Candle Return (Velocity)",
        "description": f"참조 대장주({disp_name})의 직전 1캔들 종가 수익률(%)",
        "category": "reference",
        "category_name": "대장주 선행",
        "market_scope": "all",
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": lambda df, params, interval, _col=col_close: _compute_ref_ret_1c(df, params, interval, col_close=_col)
    }
    registered_ids.append(f1_id)
    
    # 2. 5봉 추세 수익률
    f2_id = f"ref_{alias_clean}_ret_5c"
    registry[f2_id] = {
        "id": f2_id,
        "name": f"[{disp_name}] 5봉 추세 수익률",
        "name_en": f"[{alias_clean.upper()}] 5-Candle Trend Return",
        "description": f"참조 대장주({disp_name})의 최근 5캔들 누적 수익률(%)",
        "category": "reference",
        "category_name": "대장주 선행",
        "market_scope": "all",
        "params": {
            "candles": {
                "type": "slider",
                "default": 5,
                "min": 2,
                "max": 20,
                "step": 1,
                "label": "캔들 수",
                "description": "추세 계산에 사용할 캔들 수"
            }
        },
        "enabled": True,
        "has_params": True,
        "compute": lambda df, params, interval, _col=col_close: _compute_ref_ret_5c(df, params, interval, col_close=_col)
    }
    registered_ids.append(f2_id)
    
    # 3. 선행 괴리율(스프레드) ★ 핵심 알파
    f3_id = f"ref_{alias_clean}_spread_vs_target"
    registry[f3_id] = {
        "id": f3_id,
        "name": f"[{disp_name}] 선행 괴리율 (스프레드)",
        "name_en": f"[{alias_clean.upper()}] Lead-Lag Spread vs Target",
        "description": f"참조 대장주({disp_name}) 1봉 수익률 - 타겟 ETF 1봉 수익률 (선행 파동 포착)",
        "category": "reference",
        "category_name": "대장주 선행",
        "market_scope": "all",
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": lambda df, params, interval, _col=col_close: _compute_ref_spread_vs_target(df, params, interval, col_close=_col)
    }
    registered_ids.append(f3_id)
    
    # 4. 실시간 체결강도
    f4_id = f"ref_{alias_clean}_intensity"
    registry[f4_id] = {
        "id": f4_id,
        "name": f"[{disp_name}] 실시간 체결강도",
        "name_en": f"[{alias_clean.upper()}] Real-time Trade Intensity",
        "description": f"참조 대장주({disp_name})의 매수 체결비율 (가짜 돌파 필터링)",
        "category": "reference",
        "category_name": "대장주 선행",
        "market_scope": "all",
        "params": {},
        "enabled": True,
        "has_params": False,
        "compute": lambda df, params, interval, _cb=col_bid_vol, _cv=col_vol: _compute_ref_intensity(df, params, interval, col_bid_vol=_cb, col_vol=_cv)
    }
    registered_ids.append(f4_id)
    
    return registered_ids


# ==============================================================================
# 자산군 스코프(Market Scope) 판별 헬퍼
# ==============================================================================
def get_market_scope(feature_id: str, feature_info: dict = None) -> str:
    """지표의 대상 자산군 스코프 반환 ('all' | 'crypto' | 'krx' | 'us')"""
    if feature_info and "market_scope" in feature_info:
        return feature_info["market_scope"]
    
    fid = feature_id.lower()
    # ⚡ 미국 CME 글로벌 지수 선물은 전 자산(국내주식/ETF, 코인, 미국주식) 공통 선행 알파 지표
    if fid.startswith(('nq_', 'sp500_')) or 'futures' in fid:
        return "all"
    if fid.startswith(('krx_', 'kospi_', 'etf_')):
        return "krx"
    if fid.startswith(('binance_', 'crypto_', 'coinbase_', 'kimchi_', 'eth_btc')) or 'weekend' in fid:
        return "crypto"
    if fid.startswith('us_'):
        return "us"
    return "all"


# ==============================================================================
# 이벤트 트리거 지표(Trigger / Event Alpha) 판별 헬퍼
# ==============================================================================
def is_trigger_feature(feature_id: str, feature_info: dict = None) -> bool:
    """이벤트/조건부 트리거 지표 여부 판별 (희귀 상태, 스파이크, 스퀴즈 등)"""
    if feature_info and "is_trigger" in feature_info:
        return bool(feature_info["is_trigger"])
    
    defn = DEFAULT_FEATURE_REGISTRY.get(feature_id, {})
    if defn.get("is_trigger"):
        return True
        
    fid = feature_id.lower()
    trigger_keywords = ('squeeze', 'spike', 'large_trade')
    return any(kw in fid for kw in trigger_keywords)


# ==============================================================================
# 지표 적합성 검증 및 사전 경고 가드 (Feature Suitability & Guard)
# ==============================================================================

def validate_feature_registry() -> list:
    """레지스트리 등록 지표들의 필수 속성 및 무결성 전수 검사 (서버 기동 시 호출)"""
    issues = []
    valid_scopes = {'all', 'crypto', 'krx', 'us'}

    for fid, finfo in list(DEFAULT_FEATURE_REGISTRY.items()):
        # 1. 필수 기본 필드 검사
        if not finfo.get("name"):
            issues.append(f"지표 '{fid}': name 누락")
            finfo["name"] = fid

        if not finfo.get("category"):
            issues.append(f"지표 '{fid}': category 누락")
            finfo["category"] = "general"

        # 2. market_scope 검사
        scope = finfo.get("market_scope")
        if not scope or scope not in valid_scopes:
            auto_scope = get_market_scope(fid, finfo)
            issues.append(f"지표 '{fid}': market_scope({scope}) 부적합 -> '{auto_scope}'로 자동 보정")
            finfo["market_scope"] = auto_scope

        # 3. is_regime 속성 보장
        if "is_regime" not in finfo:
            finfo["is_regime"] = False

        # 4. is_trigger 속성 보장
        if "is_trigger" not in finfo:
            finfo["is_trigger"] = is_trigger_feature(fid, finfo)

        # 5. compute 함수 유효성
        if not callable(finfo.get("compute")):
            issues.append(f"지표 '{fid}': compute 함수 부재 또는 호출 불가")

    if issues:
        print(f"[FeatureRegistry Guard] ⚠️ {len(issues)}건의 지표 무결성 보정 완료:")
        for iss in issues[:5]:
            print(f"   • {iss}")
        if len(issues) > 5:
            print(f"   ... 외 {len(issues) - 5}건")
    else:
        print(f"[FeatureRegistry Guard] 🟢 전체 {len(DEFAULT_FEATURE_REGISTRY)}개 지표 레지스트리 무결성 검증 통과 (100% 정상)")

    return issues


def detect_symbol_market_scope(symbol: str, session_id: str = "") -> str:
    """심볼 문자열 또는 세션 ID를 기반으로 자산군 스코프 자동 판별 ('crypto' | 'krx' | 'us')"""
    sym = (symbol or "").strip()
    upper_sym = sym.upper()

    # 1. 거래소 표기 규칙으로 자명한 심볼 (원장 조회 이전에 즉시 판정)
    if upper_sym.startswith(("KRW-", "BTC-", "USDT-", "UPBIT:")):
        return "crypto"

    if len(sym) == 6 and sym.isdigit():
        return "krx"

    # 1-1. 종목 원장 (리스팅 키 → asset_id 순). 'BTC'/'XRP' 같은 티커 단독 표기와 미국 티커도 여기서 해석된다.
    #      종목별 티커 목록을 코드에 두지 않는다 — 새 종목은 원장에 등록하면 된다.
    if sym:
        try:
            from shared.config.config_manager import get_symbol_scope
            scope = get_symbol_scope(sym)
            if scope in ("crypto", "us", "krx"):
                return scope
        except Exception:
            pass

    # 2. 종목 마스터 원장(symbols_registry.json) 조회.
    #    [FIX] 과거 구현은 registry 최상위에 존재하지 않는 "symbols" 래퍼 키를 찾아
    #    항상 빈 dict로 폴백했고(레지스트리 조회가 사실상 무동작), 그 결과 등록된
    #    심볼도 전부 아래 3번 세션ID 부분 문자열 매칭으로 샜다. "0167A0"(신한 SOL
    #    AI반도체TOP2플러스)의 세션ID에 우연히 "SOL"이 포함돼 있어 크립토(SOL=솔라나)
    #    로 오판됐던 사례가 바로 이것. 이제 config_manager의 단일 SSOT 조회 함수를
    #    재사용해 레지스트리를 실제로 참조한다.
    if sym:
        try:
            from shared.config.config_manager import get_symbol_info
            mtype = str(get_symbol_info(sym).get("market_type", "")).upper()
            if "CRYPTO" in mtype:
                return "crypto"
            elif "US_" in mtype:
                return "us"
            elif "KRX" in mtype:
                return "krx"
        except Exception:
            pass

    # 3. 심볼 자체가 비어있을 때만 세션 ID로 최후 추정 (부분 문자열 매칭은 "SOLAI"에
    #    "SOL"이 끼어 있는 경우처럼 상품명 오탐이 쉬우므로, 심볼이 있으면 절대 사용하지 않는다)
    if not sym:
        sess = (session_id or "").strip().upper()
        if any(k in sess for k in ("KRW-", "BTC", "ETH", "XRP", "SOL", "DOGE", "UPBIT", "BINANCE", "COIN")):
            return "crypto"
        if any(k in sess for k in ("069500", "396500", "122630", "KODEX", "TIGER", "KOSPI", "KOSDAQ", "KRX")):
            return "krx"
        if any(k in sess for k in ("QQQ", "SPY", "TQQQ", "SQQQ", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "GOOGL")):
            return "us"

    return "krx"


def get_incompatible_features(symbol: str, active_feature_ids: list, session_id: str = "") -> list:
    """
    현재 대상 심볼(자산군)에 부적합한 활성 지표 목록 추출.
    반환: [{'feature_id': str, 'name': str, 'market_scope': str, 'symbol_scope': str, 'reason': str}, ...]
    """
    sym_scope = detect_symbol_market_scope(symbol, session_id=session_id)
    incompatible = []

    scope_labels = {
        "crypto": "가상자산(코인)",
        "krx": "국내 주식/ETF",
        "us": "미국 주식/ETF",
        "all": "전 자산 공통"
    }

    for fid in active_feature_ids:
        finfo = DEFAULT_FEATURE_REGISTRY.get(fid)
        if not finfo:
            continue

        feat_scope = finfo.get("market_scope", "all")
        if feat_scope == "all":
            continue

        if feat_scope != sym_scope:
            reason = f"현재 프로젝트는 [{scope_labels.get(sym_scope, sym_scope)}]이나, 지표는 [{scope_labels.get(feat_scope, feat_scope)}] 전용입니다."
            incompatible.append({
                "feature_id": fid,
                "name": finfo.get("name", fid),
                "market_scope": feat_scope,
                "symbol_scope": sym_scope,
                "reason": reason
            })

    return incompatible


def filter_compatible_features(symbol: str, feature_ids: list, session_id: str = "") -> list:
    """
    현재 대상 심볼(자산군)과 호환되는 지표 목록만 필터링하여 반환.
    - 입력 리스트의 원래 순서를 유지하며 타 자산군 전용 지표를 제외합니다.
    """
    if not feature_ids:
        return []
    incompatible_ids = {item["feature_id"] for item in get_incompatible_features(symbol, feature_ids, session_id=session_id)}
    return [fid for fid in feature_ids if fid not in incompatible_ids]


# 서버 로드 시 레지스트리 자동 검증 및 레거시 유령 지표 정화 실행
validate_feature_registry()
purge_ghost_features()


# ==============================================================================
# 지표 설정(config) 기능은 shared/feature_config.py에서 관리.
# 아래에서 재수출(Re-export)하여 기존 `from shared.features.feature_registry import ...` 경로를 유지합니다.
# ==============================================================================
from shared.features.feature_config import (  # noqa: E402,F401
    set_config_root,
    get_config_path,
    load_feature_config,
    save_feature_config,
    get_feature_list_with_params,
    get_enabled_feature_names,
    _normalize_feature_list,
)

