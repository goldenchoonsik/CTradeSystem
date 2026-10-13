"""
CTradeSystem Shared - Universal Candle Resampler
================================================
모든 거래소(업비트, 바이낸스, 코인베이스 등)와 자산군(가상자산, 주식)의
시계열 데이터를 미래 참조(Lookahead Bias) 없이 수학적·시계열적으로
완벽하게 리샘플링하는 공통 표준 엔진.

[핵심 불변 규칙]
1. 하향 리샘플링 차단: 상위봉(예: 15m)으로 하위봉(예: 10m)을 만들려 하면 즉시 LookaheadInversionError 발생.
2. 비표준/비배수 인터벌 차단: 공인 인터벌이 아니거나 정수 배수가 아니면 Fail-Fast 예외 발생.
3. 시계열 경계 불변성: 캔들 Open Time 기준 [T, T+interval) 윈도우를 묶어 T 시점에 T+interval 직전 확정치를 매핑.
4. 지능형 컬럼 집계: 컬럼명을 자동 분석하여 OHLCV 및 커스텀 지표에 최적화된 집계 규칙 자동 적용.
"""

from typing import Dict, Optional, Set, Union
import numpy as np
import pandas as pd
from shared.core.market_utils import parse_interval_seconds


# ==============================================================================
# 1. 공인 인터벌 규격 (Supported Validated Intervals)
# ==============================================================================

# 초봉 (1초~30초)
SUPPORTED_SECOND_INTERVALS: Set[str] = {'1s', '3s', '5s', '10s', '15s', '30s'}

# 분봉 (1분~30분)
SUPPORTED_MINUTE_INTERVALS: Set[str] = {'1m', '3m', '5m', '10m', '15m', '30m'}

# 시봉 (1시간~12시간)
SUPPORTED_HOUR_INTERVALS: Set[str] = {'60m', '1h', '2h', '4h', '6h', '8h', '12h'}

# 일봉 (1일)
SUPPORTED_DAY_INTERVALS: Set[str] = {'1d', 'day'}

# 전체 공인 인터벌 목록
ALL_SUPPORTED_INTERVALS: Set[str] = (
    SUPPORTED_SECOND_INTERVALS |
    SUPPORTED_MINUTE_INTERVALS |
    SUPPORTED_HOUR_INTERVALS |
    SUPPORTED_DAY_INTERVALS
)


# ==============================================================================
# 2. 리샘플러 전용 엄격 예외 클래스 (Fail-Fast Exceptions)
# ==============================================================================

class ResamplerError(Exception):
    """리샘플러 기본 예외"""
    pass


class UnsupportedIntervalError(ResamplerError):
    """지원하지 않는 비표준 인터벌 입력 시 발생"""
    pass


class LookaheadInversionError(ResamplerError):
    """상위 시간봉에서 하위 시간봉으로의 역변환(미래 참조 위험) 시도 시 발생"""
    pass


class NonDivisibleIntervalError(ResamplerError):
    """타겟 인터벌이 소스 인터벌의 정수 배수로 나누어 떨어지지 않을 때 발생"""
    pass


class InvalidTimestampError(ResamplerError):
    """timestamp 컬럼 누락, 결측치, 형식 오류 시 발생"""
    pass


# ==============================================================================
# 3. 판다스 주파수(Frequency) 및 인터벌 유틸리티
# ==============================================================================

def get_resample_freq_str(interval: str) -> str:
    """인터벌 문자열을 pandas resample 규격 문자열로 변환"""
    sec = parse_interval_seconds(interval)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        mins = sec // 60
        return f"{mins}min"
    if sec < 86400:
        hours = sec // 3600
        return f"{hours}h"
    days = sec // 86400
    return f"{days}D"


def infer_source_interval(df: pd.DataFrame, time_col: str = 'timestamp') -> str:
    """DataFrame의 타임스탬프 차이 중앙값(median diff)으로부터 소스 인터벌을 자동 추정"""
    if len(df) < 2:
        return 'auto'
    diffs = df[time_col].diff().dropna()
    if diffs.empty:
        return 'auto'
    med_sec = int(round(diffs.median().total_seconds()))
    if med_sec <= 0:
        return 'auto'

    # 초 단위 일치 확인
    if med_sec < 60:
        cand = f"{med_sec}s"
        return cand if cand in ALL_SUPPORTED_INTERVALS else 'auto'
    if med_sec < 3600:
        mins = med_sec // 60
        cand = f"{mins}m"
        return cand if cand in ALL_SUPPORTED_INTERVALS else 'auto'
    if med_sec < 86400:
        hours = med_sec // 3600
        cand = f"{hours}h"
        return cand if cand in ALL_SUPPORTED_INTERVALS else 'auto'
    return '1d'


# ==============================================================================
# 4. 지능형 컬럼 집계 규칙 추론 (Smart Aggregation Inference)
# ==============================================================================

def infer_column_agg_rules(columns: list, custom_rules: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    컬럼명 패턴을 기반으로 적절한 집계 연산자(first, max, min, last, sum, mean)를 자동 결정.
    custom_rules가 제공되면 최우선 적용.
    """
    rules: Dict[str, str] = {}
    custom_rules = custom_rules or {}

    for col in columns:
        if col in custom_rules:
            rules[col] = custom_rules[col]
            continue

        c_lower = col.lower()

        # 1. Open류 (시가) -> first
        if c_lower == 'open' or c_lower.endswith('_open'):
            rules[col] = 'first'
        # 2. High류 (고가) -> max
        elif c_lower == 'high' or c_lower.endswith('_high'):
            rules[col] = 'max'
        # 3. Low류 (저가) -> min
        elif c_lower == 'low' or c_lower.endswith('_low'):
            rules[col] = 'min'
        # 4. Close/Price류 (종가, 단일시세, 지수, OI 등) -> last
        elif (
            c_lower == 'close' or
            c_lower.endswith('_close') or
            c_lower.endswith('_spot_close') or
            c_lower.endswith('_price') or
            'futures_oi' in c_lower or
            'open_interest' in c_lower or
            c_lower.endswith('_index')
        ):
            rules[col] = 'last'
        # 5. Volume/대금/체결수류 (누적치) -> sum
        elif (
            c_lower == 'volume' or
            c_lower.endswith('_volume') or
            c_lower.endswith('_vol') or
            c_lower.endswith('_amount') or
            c_lower == 'trades' or
            c_lower.endswith('_trade_count') or
            c_lower.endswith('_count') or
            'acc_trade' in c_lower
        ):
            rules[col] = 'sum'
        # 6. Ratio/비율/평균류 -> mean
        elif (
            c_lower.endswith('_ratio') or
            c_lower.endswith('_mean') or
            c_lower.endswith('_pct') or
            c_lower.endswith('_rate')
        ):
            rules[col] = 'mean'
        # 7. 그 외 기타 수치 컬럼 -> last (보수적 시점 연속성)
        else:
            rules[col] = 'last'

    return rules


# ==============================================================================
# 5. 메인 범용 리샘플링 함수
# ==============================================================================

def _is_ohlc_col(col: str) -> bool:
    c = col.lower()
    return any(c == k or c.endswith('_' + k) for k in ('open', 'high', 'low', 'close'))


def _find_volume_col(ratio_col: str, columns) -> Optional[str]:
    """비율 컬럼에 대응하는 거래량 컬럼 탐색 (접두사 일치 우선)."""
    low = ratio_col.lower()
    prefix = ratio_col[: len(ratio_col) - len('trade_bid_ratio')]
    lower_map = {c.lower(): c for c in columns}
    for cand in (prefix.lower() + 'volume', 'target_volume', 'upbit_volume', 'volume'):
        if cand in lower_map and lower_map[cand] != ratio_col:
            return lower_map[cand]
    return None


def resample_candles(
    df: pd.DataFrame,
    target_interval: str,
    source_interval: str = 'auto',
    time_col: str = 'timestamp',
    custom_agg_rules: Optional[Dict[str, str]] = None,
    ffill_limit: Optional[int] = None,
    source_label: str = 'bar_open',
) -> pd.DataFrame:
    """
    모든 가상자산/주식 데이터프레임을 대상 인터벌로 무결 리샘플링한다.

    Parameters:
        df: 원본 DataFrame (time_col 컬럼 필수, 또는 DatetimeIndex)
        target_interval: 목표 인터벌 (예: '10m', '15m', '1h' 등)
        source_interval: 소스 인터벌 (예: '1m', '5m' 또는 'auto'로 자동 판별)
        time_col: 타임스탬프 컬럼명 (기본값: 'timestamp')
        custom_agg_rules: 사용자 정의 컬럼별 집계 함수 dict (선택사항)
        ffill_limit: 리샘플링 후 결측치 ffill 최대 개수 (기본값: None)
        source_label: 원천 봉의 타임스탬프 라벨 규약 ('bar_open' | 'bar_close').
            호출자가 `market_policies.timestamp_convention` 원장에서 읽어 넘긴다.
            'bar_close' 원천(Creon/KIS 분봉: 09:01 라벨 = 09:00~09:01 구간)은 구간 시작으로
            되돌린 뒤 묶는다 — 그대로 묶으면 상위 봉 경계가 원천 1봉만큼 밀린다
            (15분봉 [09:00,09:15) 에 09:14~09:15 체결이 빠지고 다음 봉으로 넘어간다).
            출력은 항상 'bar_open'(구간 시작 라벨)이다.

    Returns:
        target_interval 단위로 완전 정렬 및 리샘플링된 새 DataFrame
    """
    if df is None or df.empty:
        raise ResamplerError("[Resampler 오류] 입력 데이터프레임이 비어있습니다.")

    # 1. 타겟 인터벌 공인 규격 검증
    target_clean = str(target_interval).strip().lower()
    if target_clean not in ALL_SUPPORTED_INTERVALS:
        raise UnsupportedIntervalError(
            f"[Resampler 오류] 지원하지 않는 타겟 인터벌입니다: '{target_interval}'. "
            f"공인 인터벌: {sorted(list(ALL_SUPPORTED_INTERVALS))}"
        )

    target_sec = parse_interval_seconds(target_clean)

    # 2. 타임스탬프 정규화 및 검증
    work_df = df.copy()
    if time_col not in work_df.columns:
        if isinstance(work_df.index, pd.DatetimeIndex):
            work_df[time_col] = work_df.index
        else:
            # 타임스탬프 유사 컬럼 자동 탐색
            possible_cols = [c for c in work_df.columns if 'time' in c.lower() or 'date' in c.lower()]
            if possible_cols:
                time_col = possible_cols[0]
            else:
                raise InvalidTimestampError(f"[Resampler 오류] 타임스탬프 컬럼 '{time_col}'을 찾을 수 없습니다.")

    # datetime64[ns] 변환
    work_df[time_col] = pd.to_datetime(work_df[time_col], errors='coerce')
    if work_df[time_col].isna().any():
        work_df = work_df.dropna(subset=[time_col])
        if work_df.empty:
            raise InvalidTimestampError("[Resampler 오류] 유효한 타임스탬프 데이터가 존재하지 않습니다.")

    work_df = work_df.sort_values(time_col).reset_index(drop=True)

    # 3. 소스 인터벌 확인 및 역변환(하향 룩어헤드) 차단
    if source_interval == 'auto':
        source_interval = infer_source_interval(work_df, time_col=time_col)

    label_clean = str(source_label or '').strip().lower()
    if label_clean not in ('bar_open', 'bar_close'):
        raise ResamplerError(f"[Resampler 오류] 알 수 없는 원천 라벨 규약입니다: {source_label!r} (허용: bar_open, bar_close)")
    if label_clean == 'bar_close':
        # 봉 확정 라벨 → 구간 시작 라벨. 원천 봉 길이를 모르면 되돌릴 수 없으므로 추측하지 않는다.
        if str(source_interval).strip().lower() not in ALL_SUPPORTED_INTERVALS:
            raise ResamplerError("[Resampler 오류] bar_close 원천은 source_interval 을 명시해야 구간 시작으로 되돌릴 수 있습니다.")
        work_df[time_col] = work_df[time_col] - pd.Timedelta(seconds=parse_interval_seconds(str(source_interval).strip().lower()))

    if source_interval != 'auto':
        source_clean = str(source_interval).strip().lower()
        if source_clean in ALL_SUPPORTED_INTERVALS:
            source_sec = parse_interval_seconds(source_clean)

            # 🚨 [치명적 미래 참조 차단기]: 상위봉으로 하위봉을 만들려 할 때 즉시 정지
            if source_sec > target_sec:
                raise LookaheadInversionError(
                    f"🚨 [LookaheadInversionError 치명적 오류] 상위 시간봉({source_clean}, {source_sec}초)을 "
                    f"하위 시간봉({target_clean}, {target_sec}초)으로 역방향 리샘플링할 수 없습니다! "
                    f"미래 시세 참조(Lookahead Bias)가 발생하므로 즉시 중단합니다."
                )

            # 정수 배수 검증
            if target_sec % source_sec != 0:
                raise NonDivisibleIntervalError(
                    f"❌ [NonDivisibleIntervalError 오류] 타겟 인터벌({target_clean}, {target_sec}초)은 "
                    f"소스 인터벌({source_clean}, {source_sec}초)의 정수 배수여야 합니다. (나머지: {target_sec % source_sec}초)"
                )

    # 4. 리샘플링 집계 규칙 구성
    value_cols = [c for c in work_df.columns if c != time_col]
    if not value_cols:
        raise ResamplerError("[Resampler 오류] 집계할 수치 데이터 컬럼이 없습니다.")

    agg_rules = infer_column_agg_rules(value_cols, custom_rules=custom_agg_rules)

    # 5. 시계열 경계 불변 리샘플링 실행
    # 📌 캔들 Open Time 기준: [T, T + target_sec) 구간
    # closed='left', label='left'를 적용하여 [00:00, 00:10) 구간의 first/last/sum을 00:00 라벨로 귀속.
    freq_str = get_resample_freq_str(target_clean)
    resampled = work_df.set_index(time_col).resample(
        freq_str,
        closed='left',
        label='left'
    ).agg(agg_rules)

    # 체결 비율(*_trade_bid_ratio)은 단순 mean 이 아닌 거래량 가중 평균으로 보정
    freq_kw = dict(closed='left', label='left')
    for col in value_cols:
        if not col.lower().endswith('trade_bid_ratio') or agg_rules.get(col) != 'mean':
            continue
        vol_col = _find_volume_col(col, work_df.columns)
        if vol_col is None:
            continue
        idx_df = work_df.set_index(time_col)
        ratio_num = pd.to_numeric(idx_df[col], errors='coerce')
        vol = pd.to_numeric(idx_df[vol_col], errors='coerce').fillna(0.0)
        num = (ratio_num * vol).resample(freq_str, **freq_kw).sum(min_count=1)
        den = vol.where(ratio_num.notna(), 0.0).resample(freq_str, **freq_kw).sum(min_count=1)
        weighted = num / den.where(den > 0)
        # 거래량이 0인 구간은 기존 단순 평균 유지
        resampled[col] = weighted.reindex(resampled.index).where(lambda x: x.notna(), resampled[col])

    # 모든 값이 NaN인 더미 행 제거 (데이터 구간 양 끝 정돈)
    resampled = resampled.dropna(how='all')

    # 거래 없는 구간: OHLC 전부 NaN 인데 volume(sum)=0 만 남은 행 제거
    _ohlc_cols = [c for c in value_cols if _is_ohlc_col(c)]
    if _ohlc_cols:
        resampled = resampled[resampled[_ohlc_cols].notna().any(axis=1)]

    if ffill_limit is not None and ffill_limit > 0:
        resampled = resampled.ffill(limit=ffill_limit)

    resampled = resampled.reset_index()
    # 타임스탬프 정규화: 초 단위 절삭 + datetime64[ns]
    # [T-4] tz-aware 입력은 **tz 를 보존**한다. `astype('datetime64[ns]')` 는 tz-aware 를
    #       UTC 로 변환해 KST 09:00 → 00:00 처럼 시각을 밀어버리므로, naive 일 때만 dtype 을 강제한다.
    _ts = pd.to_datetime(resampled[time_col]).dt.floor('s')
    resampled[time_col] = _ts if _ts.dt.tz is not None else _ts.astype('datetime64[ns]')

    return resampled
