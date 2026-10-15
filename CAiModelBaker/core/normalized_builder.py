# -*- coding: utf-8 -*-
"""
CAiModelBaker - Normalized Builder (단일 자산 표준 정규화 캔들 빌더)
===================================================================
[메달리온 2단계 / Normalized Layer]
타 종목과 일절 결합하지 않고, 단일 자산 고유의 틱/캔들 데이터를 표준화하여 Parquet 포맷으로 캐싱합니다.
1. 틱 레벨 중복 제거 및 인트라미닛 초 단위 버킷 분배
2. 오더플로우 마이크로 지표 선계산 (CVD, bid_vol, 체결비율, 대량체결)
3. 09:00:00 ~ 15:30:00 정규 거래시간 연속 시계열 그리드 완성
4. 당일(today) 장 마감 전 미완료 데이터 원천 배제
5. 1:1 사이드카 매니페스트(.manifest.json) 자동 발행
"""

import os
import time
import logging
import tempfile
import numpy as np
import pandas as pd
from typing import Optional, Callable, Dict, Any, List

from shared.data_library import paths as dpaths
from shared.data_library import candle_store, tick_store
from shared.data_library.manifest_manager import ManifestManager
from shared.data_library.source_mapping_manager import SourceMappingManager
from shared.core.market_utils import is_crypto_symbol
from shared.config.market_policies import crypto_spot_default_interval


def get_pandas_freq(interval: str) -> str:
    """인터벌 문자열을 pandas resample rule로 변환"""
    i_str = str(interval).strip().lower()
    if i_str.endswith('s') or i_str.endswith('sec'):
        num = ''.join(filter(str.isdigit, i_str)) or '10'
        return f'{num}s'
    elif i_str.endswith('m') or i_str.endswith('min'):
        num = ''.join(filter(str.isdigit, i_str)) or '1'
        return f'{num}min'
    elif i_str.endswith('h'):
        num = ''.join(filter(str.isdigit, i_str)) or '1'
        return f'{num}h'
    elif i_str.endswith('d') or i_str == 'day':
        return '1D'
    return '10s'


def build_normalized_10s_stock(
    symbol: str,
    raw_tick_path: Optional[str] = None,
    output_parquet_path: Optional[str] = None,
    local_log: Optional[Callable[[str], None]] = None
) -> str:
    """
    국내 주식/ETF 틱 데이터를 단일 자산 10초 정제 표준 Parquet으로 빌드합니다.
    (타 종목 결합 절대 금지 - 순수 단일 자산 엔티티)
    [Phase 1 고도화]
    - 이상 틱(0원/음수/스파이크) 스크러빙
    - Lee-Ready 틱 룰 기반 bid/ask volume 및 OFI 산출
    - VWAP, Parkinson RV, 적응형 대량체결(Top 5% quantile) 선계산
    - KRX 15:20~15:30 단일가 동시호가(Auction) 격리 플래그
    - 단일 자산 표준 스키마 및 하위 호환성 필드 동시 보장
    """
    if local_log is None:
        local_log = logging.info

    clean_sym = SourceMappingManager.clean_symbol(symbol)
    
    # 1~2. Raw 틱 로드 — 경로를 직접 받지 않으면 tick_store 창구에서 읽는다(디스크 레이아웃과 무관)
    if raw_tick_path is not None and os.path.exists(raw_tick_path):
        local_log(f"📦 [Normalized Builder] {clean_sym} 10초 정규화 빌드 시작 (원천: {dpaths.display(raw_tick_path)})...")
        df = pd.read_csv(raw_tick_path)
    else:
        if not tick_store.has_data(clean_sym):
            raise FileNotFoundError(f"[NormalizedBuilder] {clean_sym} 원천 틱 데이터를 찾을 수 없습니다.")
        local_log(f"📦 [Normalized Builder] {clean_sym} 10초 정규화 빌드 시작 (원천: 틱 창구 tick_store)...")
        df = tick_store.read(clean_sym)
    if df.empty:
        raise ValueError(f"[NormalizedBuilder] {clean_sym} 원천 틱 데이터가 비어 있습니다.")

    initial_tick_count = len(df)

    # 3. 틱 레벨 복합 중복 제거
    dedup_subset = [c for c in ['timestamp', 'upbit_close', 'upbit_volume', 'cvd', 'upbit_trade_price'] if c in df.columns]
    if dedup_subset:
        df = df.drop_duplicates(subset=dedup_subset).sort_values('timestamp').reset_index(drop=True)
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df.dropna(subset=['timestamp']).reset_index(drop=True)

    t_close_col = 'upbit_close' if 'upbit_close' in df.columns else ('close' if 'close' in df.columns else 'trade_price')
    t_vol_col = 'upbit_volume' if 'upbit_volume' in df.columns else ('volume' if 'volume' in df.columns else 'acc_trade_volume')
    t_tp_col = 'upbit_trade_price' if 'upbit_trade_price' in df.columns else ('trade_price' if 'trade_price' in df.columns else 'trade_amount')

    # 4. 이상 틱 스크러빙 (Tick Scrubbing / Outlier Rejection)
    # 4-1. 0원 및 음수 가격/거래량 필터
    valid_mask = (df[t_close_col] > 0) & (df[t_vol_col] >= 0)
    df = df[valid_mask].reset_index(drop=True)

    # 4-2. 급격한 스파이크 글리치 틱 필터 (직전 유효 체결가 대비 +-7% 초과 변동 틱 배제)
    if len(df) > 1:
        price_series = df[t_close_col].astype(float)
        pct_chg = price_series.pct_change().abs()
        spike_mask = (pct_chg > 0.07) & (price_series.diff().abs() > 0)
        # 1틱 스파이크 이상치만 제거 (지속 추세가 아닌 순간 글리치)
        if spike_mask.any():
            local_log(f"  🧹 [Tick Scrubbing] 비정상 스파이크 이상 틱 {spike_mask.sum():,}건 제거")
            df = df[~spike_mask].reset_index(drop=True)

    local_log(f"  ✨ 원천 틱 정제 완료: 총 {len(df):,}개 틱 보존 (원천 {initial_tick_count:,}개 중)")

    # 5. 틱 레벨 마이크로 지표 선계산
    # 크레온 원천의 십만원 단위 반올림 왜곡을 배제하고 정밀 체결가 * 체결량으로 거래대금 계산
    df['calc_trade_amt'] = df[t_close_col] * df[t_vol_col]
    if 'upbit_open' not in df.columns: df['upbit_open'] = df[t_close_col]
    if 'upbit_high' not in df.columns: df['upbit_high'] = df[t_close_col]
    if 'upbit_low' not in df.columns: df['upbit_low'] = df[t_close_col]

    # 5-1. 적응형 대량체결 (일별 95분위수 또는 5천만 원 하한)
    try:
        # 일중 look-ahead 방지: 당일 전체 틱이 아닌 직전 거래일의 95분위수 사용 (첫날은 하한값만)
        _dates = df['timestamp'].dt.date
        _q95_by_day = df.groupby(_dates)['calc_trade_amt'].quantile(0.95).sort_index()
        _prev_q95 = _q95_by_day.shift(1)
        daily_q95 = _dates.map(_prev_q95).astype(float).fillna(0.0)
        large_thresh = np.maximum(daily_q95, 50_000_000.0)
    except Exception:
        large_thresh = 100_000_000.0
    df['is_large'] = (df['calc_trade_amt'] >= large_thresh).astype(float)

    # 5-2. Lee-Ready 틱 룰 & CVD 기반 매수/매도 거래량 정밀 분해
    if 'cvd' in df.columns and t_vol_col in df.columns:
        delta_vol = df['cvd'].diff().fillna(0.0)
        # CVD 변동이 유효한 경우 CVD 기반 매수/매도량 산출
        df['bid_vol'] = ((df[t_vol_col] + delta_vol) / 2.0).clip(lower=0, upper=df[t_vol_col])
        df['ask_vol'] = (df[t_vol_col] - df['bid_vol']).clip(lower=0)
    else:
        # Lee-Ready 틱 룰: 가격 상승 체결은 매수, 하락 체결은 매도, 동가 체결은 직전 방향 승계
        price_diff = df[t_close_col].diff().fillna(0.0)
        tick_dir = np.where(price_diff > 0, 1.0, np.where(price_diff < 0, -1.0, np.nan))
        tick_dir = pd.Series(tick_dir).ffill().fillna(1.0).values
        df['bid_vol'] = np.where(tick_dir >= 0, df[t_vol_col], 0.0)
        df['ask_vol'] = np.where(tick_dir < 0, df[t_vol_col], 0.0)

    df['trade_count'] = 1

    # 5-9. [T-6] 분 내부 행 순서 감사 — 시간축이 틀린 날은 10초봉으로 만들지 않는다
    # 이 원천은 초(second) 정보가 없어 **분 안의 행 순서가 곧 시간축**이다(아래 6번 참조).
    # 순서가 틀린 날은 값의 집합이 같아 행수·결측·합계 검사로는 드러나지 않으므로,
    # 같은 종목 1분봉(독립 원천)과 대조해 여기서 걸러낸다.
    df = _drop_unordered_tick_days(df, clean_sym, local_log)

    # 6. 인트라미닛 틱 타임스탬프 10초 버킷 분배
    # [주의] 원천 틱 타임스탬프가 분 단위이므로 분 내 순번(cumcount)으로 균등 배분한 합성 시각이다.
    #        실제 체결 시각이 아니며 10초 캔들 경계는 근사치 (정보 손실) — 매니페스트에 명시한다.
    local_log("  ⚠️ [합성 시각] 분 단위 틱 타임스탬프를 분 내 순번으로 10초 버킷에 균등 배분합니다 (실제 체결 시각 아님).")
    sec_step = 10
    buckets_per_min = 6
    minute_grp = df.groupby('timestamp')
    cum_count = minute_grp.cumcount()
    total_in_min = minute_grp[t_close_col].transform('count').replace(0, 1)

    ratio = (cum_count / total_in_min) * buckets_per_min
    ratio = ratio.fillna(0.0)
    bucket_sec = np.floor(ratio).clip(0, buckets_per_min - 1).astype(int) * sec_step
    df['adjusted_timestamp'] = df['timestamp'] + pd.to_timedelta(bucket_sec, unit='s')
    df = df.set_index('adjusted_timestamp')

    # 7. 10초 캔들 리샘플링 집계
    resample_rule = '10s'
    agg_dict = {
        'upbit_open': 'first',
        'upbit_high': 'max',
        'upbit_low': 'min',
        'upbit_close': 'last',
        'upbit_volume': 'sum' if 'upbit_volume' in df.columns else t_vol_col,
        'calc_trade_amt': 'sum',
        'is_large': 'sum',
        'bid_vol': 'sum',
        'ask_vol': 'sum',
        'trade_count': 'sum'
    }
    if 'cvd' in df.columns: agg_dict['cvd'] = 'last'
    if 'bid' in df.columns: agg_dict['bid'] = 'last'
    if 'ask' in df.columns: agg_dict['ask'] = 'last'
    if 'spread' in df.columns: agg_dict['spread'] = 'mean'
    if 'mid_price' in df.columns: agg_dict['mid_price'] = 'last'

    df_resampled = df.resample(resample_rule).agg(agg_dict).dropna(subset=['upbit_close']).reset_index()
    df_resampled.rename(columns={
        'adjusted_timestamp': 'timestamp',
        'calc_trade_amt': 'upbit_trade_price',
        'trade_count': 'target_trade_count',
        'is_large': 'target_large_trade_count'
    }, inplace=True)


    # 8. 정규 거래시간(평일 09:00:00 ~ 15:30:00, 특수 거래일은 krx_session_calendar.json 원장) 완전 시계열 그리드 구축
    from shared.core.market_utils import krx_session
    now_kst = pd.Timestamp.now(tz='Asia/Seoul').tz_localize(None)
    today_date = now_kst.date()

    # 당일 장 마감 이전 수집된 미완료 틱은 제외
    if now_kst.time() < krx_session(today_date).close:
        if (df_resampled['timestamp'].dt.date == today_date).any():
            local_log(f"  ⏳ 당일({today_date}) 장 마감 이전 미완료 틱은 그리드에서 제외합니다.")
            df_resampled = df_resampled[df_resampled['timestamp'].dt.date != today_date].reset_index(drop=True)

    unique_dates = df_resampled['timestamp'].dt.date.unique()
    grid_list = []
    valid_unique_dates = []
    for d in unique_dates:
        day_ts = df_resampled[df_resampled['timestamp'].dt.date == d]['timestamp']
        sess = krx_session(d)
        if not day_ts.empty and day_ts.max().time() < sess.auction_start and d == today_date:
            local_log(f"  ⚠️ 거래일 {d} 데이터 조기 종료({day_ts.max().strftime('%H:%M:%S')}): 그리드 생성 제외")
            continue
        day_start = pd.Timestamp.combine(pd.Timestamp(d).date(), sess.open)
        day_end = pd.Timestamp.combine(pd.Timestamp(d).date(), sess.close)
        day_grid = pd.date_range(start=day_start, end=day_end, freq=resample_rule)
        grid_list.extend(day_grid)
        valid_unique_dates.append(d)

    full_grid_df = pd.DataFrame({'timestamp': grid_list})
    df_norm = pd.merge(full_grid_df, df_resampled[df_resampled['timestamp'].dt.date.isin(valid_unique_dates)], on='timestamp', how='left')

    # 가격 포워드 필 (이전 종가 유지)
    price_cols = ['upbit_open', 'upbit_high', 'upbit_low', 'upbit_close', 'mid_price', 'bid', 'ask']
    for c in price_cols:
        if c in df_norm.columns:
            df_norm[c] = df_norm[c].ffill()

    # 미체결 구간의 OH/L은 종가와 일치
    no_trade_mask = df_norm['upbit_volume'].isna() | (df_norm['upbit_volume'] == 0)
    for c in ['upbit_open', 'upbit_high', 'upbit_low']:
        if c in df_norm.columns:
            df_norm.loc[no_trade_mask, c] = df_norm.loc[no_trade_mask, 'upbit_close']

    # 거래량 및 집계 필드 0 채움
    df_norm['upbit_volume'] = df_norm['upbit_volume'].fillna(0.0)
    df_norm['upbit_trade_price'] = df_norm['upbit_trade_price'].fillna(0.0)
    df_norm['target_trade_count'] = df_norm['target_trade_count'].fillna(0.0)
    df_norm['target_large_trade_count'] = df_norm['target_large_trade_count'].fillna(0.0)
    df_norm['bid_vol'] = df_norm['bid_vol'].fillna(0.0)
    df_norm['ask_vol'] = df_norm['ask_vol'].fillna(0.0) if 'ask_vol' in df_norm.columns else (df_norm['upbit_volume'] - df_norm['bid_vol']).clip(lower=0)
    if 'cvd' in df_norm.columns: df_norm['cvd'] = df_norm['cvd'].ffill().fillna(0.0)
    if 'spread' in df_norm.columns: df_norm['spread'] = df_norm['spread'].fillna(0.0)
    if 'mid_price' in df_norm.columns: df_norm['mid_price'] = df_norm['mid_price'].ffill().fillna(df_norm['upbit_close'])

    # 9-1. VWAP (거래량가중평균가): 거래량 0인 구간은 Close 유지, [Low, High] 경계 엄격 보장
    has_vol = df_norm['upbit_volume'] > 0
    raw_vwap = np.where(has_vol, df_norm['upbit_trade_price'] / (df_norm['upbit_volume'] + 1e-8), df_norm['upbit_close'])
    df_norm['vwap'] = np.clip(raw_vwap, df_norm['upbit_low'], df_norm['upbit_high'])


    # 9-2. OFI (Order Flow Imbalance 강도: [-1.0, 1.0])
    df_norm['ofi'] = np.where(
        has_vol,
        ((df_norm['bid_vol'] - df_norm['ask_vol']) / (df_norm['upbit_volume'] + 1e-8)).clip(-1.0, 1.0),
        0.0
    )

    # 9-3. Parkinson Realized Volatility (인트라바 극치 변동성)
    # sigma^2 = (ln(High/Low))^2 / (4 * ln(2))
    hl_ratio = np.maximum(df_norm['upbit_high'] / np.maximum(df_norm['upbit_low'], 1e-8), 1.0)
    log_hl = np.log(hl_ratio)
    df_norm['rv'] = np.sqrt((log_hl ** 2) / (4.0 * np.log(2.0)))

    # 9-4. KRX 종가 단일가 동시호가(Auction) 격리 플래그 — 평일 15:20:10 ~ 15:30:00 (특수 거래일은 원장 세션 기준)
    _sess = df_norm['timestamp'].dt.date.map({d: krx_session(d) for d in df_norm['timestamp'].dt.date.unique()})
    _tod = df_norm['timestamp'].dt.time
    df_norm['is_auction'] = pd.Series(
        [(t > s.auction_start) and (t <= s.close) for t, s in zip(_tod, _sess)], index=df_norm.index
    ).astype('int32')

    # 10. 단일 자산 표준 컬럼 정립 (Canonical Columns)
    df_norm['open'] = df_norm['upbit_open'].astype('float32')
    df_norm['high'] = df_norm['upbit_high'].astype('float32')
    df_norm['low'] = df_norm['upbit_low'].astype('float32')
    df_norm['close'] = df_norm['upbit_close'].astype('float32')
    df_norm['volume'] = df_norm['upbit_volume'].astype('float32')
    df_norm['trade_amount'] = df_norm['upbit_trade_price'].astype('float32')
    df_norm['trade_count'] = df_norm['target_trade_count'].astype('int32')
    df_norm['bid_volume'] = df_norm['bid_vol'].astype('float32')
    df_norm['ask_volume'] = df_norm['ask_vol'].astype('float32')
    df_norm['vwap'] = df_norm['vwap'].astype('float32')
    df_norm['ofi'] = df_norm['ofi'].astype('float32')
    df_norm['rv'] = df_norm['rv'].astype('float32')
    df_norm['is_large_trade'] = df_norm['target_large_trade_count'].astype('int32')
    if 'cvd' in df_norm.columns: df_norm['cvd'] = df_norm['cvd'].astype('float32')
    if 'spread' in df_norm.columns: df_norm['spread'] = df_norm['spread'].astype('float32')
    if 'mid_price' in df_norm.columns: df_norm['mid_price'] = df_norm['mid_price'].astype('float32')

    # 11. 하위 호환성 필드 보장 및 캔들 기하학 방역 (shared.candle_core SSOT 규격 일치)
    df_norm['target_open'] = df_norm['open']
    df_norm['target_high'] = np.maximum(df_norm['high'], np.maximum(df_norm['open'], df_norm['close'])).astype('float32')
    df_norm['target_low'] = np.minimum(df_norm['low'], np.minimum(df_norm['open'], df_norm['close'])).astype('float32')
    df_norm['target_close'] = df_norm['close']
    df_norm['target_volume'] = df_norm['volume'].clip(lower=0.0).astype('float32')
    df_norm['target_trade_bid_ratio'] = (df_norm['bid_vol'] / (df_norm['upbit_volume'] + 1e-8)).clip(0.0, 1.0).fillna(0.5).astype('float32')
    df_norm['target_avg_trade_size'] = (df_norm['upbit_trade_price'] / (df_norm['target_trade_count'] + 1e-8)).fillna(0.0).astype('float32')

    # 12. Parquet 저장
    if output_parquet_path is None:
        output_parquet_path = dpaths.normalized_path("candles_10s", f"{clean_sym}_10s.parquet")
    os.makedirs(os.path.dirname(os.path.abspath(output_parquet_path)), exist_ok=True)

    df_norm.to_parquet(output_parquet_path, index=False, engine='pyarrow', compression='snappy')
    local_log(f"  💾 [Normalized Parquet 저장] {output_parquet_path} ({len(valid_unique_dates)}개 거래일, 총 {len(df_norm):,}행)")

    # 13. 사이드카 매니페스트 발행
    ManifestManager.create_manifest(
        data_path=output_parquet_path,
        provider="creon",
        data_tier="normalized",
        asset_type="candles_10s",
        symbol=clean_sym,
        notes=f"Single-Asset Normalized 10s Candles ({len(valid_unique_dates)} trading days, {len(df_norm)} bars, Canonical Schema + Microstructure)",
        extra_meta={
            "sampling_mode": "TICK_RESAMPLED",
            "sampling_desc": "🟢 NATIVE 10s (틱 원천 직접 집계)",
            "source_interval": "tick",
            "target_interval": "10s",
            "synthetic_intraminute_timestamps": True,
            "synthetic_timestamp_note": "분 단위 틱 타임스탬프를 분 내 순번(cumcount)으로 10초 버킷에 균등 배분한 합성 시각",
        }
    )
    ok, msg = ManifestManager.verify_manifest(output_parquet_path)
    local_log(f"  📜 [Manifest Verified] {msg}")

    return output_parquet_path



def build_normalized_candle_stock(
    symbol: str,
    interval: str = "1m",
    raw_candle_path: Optional[str] = None,
    output_parquet_path: Optional[str] = None,
    end_date: Optional[str] = None,
    local_log: Optional[Callable[[str], None]] = None
) -> str:
    """
    국내 대장주/종목의 분봉 데이터를 단일 자산 표준 Parquet으로 정규화합니다.
    (삼성전자, SK하이닉스 등)
    """
    if local_log is None:
        local_log = logging.info

    clean_sym = SourceMappingManager.clean_symbol(symbol)

    if raw_candle_path is None or not candle_store.path_has_data(raw_candle_path):
        candidates = [
            dpaths.raw_path("creon", "candle_1m", f"A{clean_sym}_1m.csv"),
            dpaths.raw_path("creon", "candle_1m", f"{clean_sym}_1m.csv"),
            dpaths.bronze_path("creon", "candle_1m", f"A{clean_sym}_1m.csv"),
        ]
        for cand in candidates:
            if cand and candle_store.path_has_data(cand):
                raw_candle_path = cand
                break

    if not raw_candle_path or not candle_store.path_has_data(raw_candle_path):
        raise FileNotFoundError(f"[NormalizedBuilder] {clean_sym} 원천 분봉 파일을 찾을 수 없습니다.")

    local_log(f"📦 [Normalized Builder] {clean_sym} {interval} 정규화 빌드 시작 (원천: {dpaths.display(raw_candle_path)})...")

    df = candle_store.read_csv(raw_candle_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df.dropna(subset=['timestamp']).drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)

    # 지정된 종료일이 있으면 필터링 (불완전/장중 결측 구간 배제)
    if end_date:
        cutoff = pd.Timestamp(f"{end_date} 23:59:59")
        df = df[df['timestamp'] <= cutoff].reset_index(drop=True)

    # KRX 정규장 봉만 — 2026-09-14 NXT 개장 이후 개별 주식 원천에 08:00~20:00 통합 체결이 들어온다(원천은 그대로 보존).
    # 정규화는 정규장 시계열이어야 참조 결합(backward)이 아침 첫 봉에 전날 20:00 값을 끌어오지 않고, 학습 이력(09-14 이전)과 뜻이 같다.
    from shared.core.market_utils import krx_regular_bar_mask
    from shared.config.market_policies import timestamp_convention
    try:
        _label = timestamp_convention(clean_sym, "1m")
    except Exception:
        _label = "BAR_CLOSE"                      # Creon 1분봉 원장 규약
    _reg = krx_regular_bar_mask(df['timestamp'], _label).to_numpy()
    if not _reg.all():
        _out = df.loc[~_reg, 'timestamp']
        local_log(f"  🕘 [KRX 정규장] {clean_sym}: 정규장 밖 봉 {int((~_reg).sum()):,}개 제외 "
                  f"({_out.min():%Y-%m-%d}~{_out.max():%Y-%m-%d}, NXT 프리/애프터마켓)")
        df = df[_reg].reset_index(drop=True)


    # 표준 컬럼 정규화
    close_col = 'upbit_close' if 'upbit_close' in df.columns else ('close' if 'close' in df.columns else 'trade_price')
    vol_col = 'upbit_volume' if 'upbit_volume' in df.columns else ('volume' if 'volume' in df.columns else 'acc_trade_volume')
    open_col = 'upbit_open' if 'upbit_open' in df.columns else ('open' if 'open' in df.columns else close_col)
    high_col = 'upbit_high' if 'upbit_high' in df.columns else ('high' if 'high' in df.columns else close_col)
    low_col = 'upbit_low' if 'upbit_low' in df.columns else ('low' if 'low' in df.columns else close_col)
    tp_col = 'upbit_trade_price' if 'upbit_trade_price' in df.columns else ('trade_price' if 'trade_price' in df.columns else 'trade_amount')

    o_s = pd.to_numeric(df[open_col], errors='coerce').ffill().astype('float32')
    h_s = pd.to_numeric(df[high_col], errors='coerce').ffill().astype('float32')
    l_s = pd.to_numeric(df[low_col], errors='coerce').ffill().astype('float32')
    c_s = pd.to_numeric(df[close_col], errors='coerce').ffill().astype('float32')
    v_s = pd.to_numeric(df[vol_col], errors='coerce').fillna(0.0).astype('float32')

    # VWAP 추정 (trade_price 컬럼이 있으면 우선 활용, 없으면 전형가 HLC/3)
    if tp_col in df.columns:
        amt_s = pd.to_numeric(df[tp_col], errors='coerce').fillna(0.0).astype('float32')
        vwap_s = np.where(v_s > 0, amt_s / (v_s + 1e-8), c_s).astype('float32')
    else:
        amt_s = (c_s * v_s).astype('float32')
        vwap_s = ((h_s + l_s + c_s) / 3.0).astype('float32')

    df_norm = pd.DataFrame({
        'timestamp': df['timestamp'],
        'open': o_s,
        'high': h_s,
        'low': l_s,
        'close': c_s,
        'volume': v_s,
        'trade_amount': amt_s,
        'vwap': vwap_s,
    })

    # 상위 주기(3m, 5m, 10m, 15m 등) 요청 시 리샘플링 집계
    if interval != "1m":
        from shared.data_library.candle_resampler import resample_candles
        try:
            from shared.config.market_policies import timestamp_convention
            # 원천 1분봉의 라벨 규약은 원장에서 읽는다 (Creon/KIS = bar_close). 산출 상위 봉은 구간 시작 라벨이며
            # 원장 interval_overrides 에 bar_open 으로 선언돼 있다 — 실전 CandleAggregator 와 같은 경계·라벨.
            # KRX 종가 동시호가 체결(15:30 확정 라벨)은 이 규약 변환으로 마지막 정규장 봉에 자연히 합쳐진다.
            df_norm = resample_candles(df_norm, target_interval=interval, source_interval="1m",
                                       source_label=timestamp_convention(clean_sym, "1m"))
            local_log(f"  🔄 [Resample] 1m ➔ {interval} 리샘플링 완료 ({len(df_norm):,}행)")
        except Exception as re_err:
            # 1분봉을 target_interval 로 위장 저장하지 않도록 실패를 전파 (호출부가 errors 로 기록)
            local_log(f"  ❌ [리샘플링 실패] 1m ➔ {interval}: {re_err} — 저장하지 않음")
            raise RuntimeError(f"리샘플링 실패 (1m -> {interval}): {re_err}") from re_err

    if output_parquet_path is None:
        output_parquet_path = dpaths.normalized_path(f"candles_{interval}", f"{clean_sym}_{interval}.parquet")
    os.makedirs(os.path.dirname(os.path.abspath(output_parquet_path)), exist_ok=True)

    df_norm.to_parquet(output_parquet_path, index=False, engine='pyarrow', compression='snappy')
    local_log(f"  💾 [Normalized Parquet 저장] {output_parquet_path} (총 {len(df_norm):,}행)")

    ManifestManager.create_manifest(
        data_path=output_parquet_path,
        provider="creon",
        data_tier="normalized",
        asset_type=f"candles_{interval}",
        symbol=clean_sym,
        notes=f"Single-Asset Normalized {interval} Candles ({len(df_norm)} bars, Canonical Schema)",
        extra_meta={
            "sampling_mode": "BAR_NATIVE" if interval == "1m" else "BAR_RESAMPLED",
            "sampling_desc": f"🔵 NATIVE {interval} (원천 정규화)" if interval == "1m" else f"🟣 1m ➔ {interval} 집계 (구간 시작 라벨)",
            "source_interval": "1m",
            "target_interval": interval,
        }
    )
    ok, msg = ManifestManager.verify_manifest(output_parquet_path)
    local_log(f"  📜 [Manifest Verified] {msg}")

    return output_parquet_path


def build_normalized_macro(
    macro_name: str,
    raw_path: str,
    interval: str = "1m",
    output_parquet_path: Optional[str] = None,
    local_log: Optional[Callable[[str], None]] = None
) -> str:
    """
    해외선물(NQ, ES), 국내선물, 지수 등의 매크로 캔들을 정규화 Parquet으로 변환합니다.
    (해외 선물 Lookahead 방지 시프트 반영)
    """
    if local_log is None:
        local_log = logging.info

    # 파일/매니페스트는 종목 원장 키로 이름 짓는다 (옛 별칭 futures/spot 을 받으면 10100/U180 으로 해석)
    from shared.config.config_manager import resolve_macro_symbol
    macro_name = resolve_macro_symbol(macro_name) or macro_name

    if not candle_store.path_has_data(raw_path):
        raise FileNotFoundError(f"[NormalizedBuilder] 매크로 원천 파일 부재: {raw_path}")

    local_log(f"📦 [Normalized Builder] 매크로 {macro_name} {interval} 정규화 빌드 시작 (원천: {dpaths.display(raw_path)})...")

    df = candle_store.read_csv(raw_path)
    if 'date_time_utc' in df.columns:
        df['timestamp'] = pd.to_datetime(df['date_time_utc']) + pd.Timedelta(hours=9)
    elif 'date_time_kst' in df.columns:
        df['timestamp'] = pd.to_datetime(df['date_time_kst'])
    elif 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    else:
        raise ValueError(f"[NormalizedBuilder] {macro_name} 타임스탬프 컬럼을 찾을 수 없습니다.")

    # [T-6] 정규화 단계에서는 타임스탬프를 **건드리지 않는다**.
    # 정렬 보정(봉 시작 라벨 → 확정 시각)은 결합 시점에 단 한 곳
    # (shared.data_library.reference_staleness.attach_reference)에서만 한다.
    #
    # 종전에는 정규화 단계와 결합 단계 양쪽에 시프트가 흩어져 있어, 심볼이 어느 빌드 경로를
    # 타느냐에 따라 보정이 0번/1번/2번 적용됐다. 규약은 원장에 선언하고 보정은 한 곳에서만 한다.

    df = df.dropna(subset=['timestamp']).drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)

    close_col = 'close' if 'close' in df.columns else ('upbit_close' if 'upbit_close' in df.columns else 'trade_price')
    vol_col = 'volume' if 'volume' in df.columns else ('upbit_volume' if 'upbit_volume' in df.columns else 'acc_trade_volume')

    c_s = pd.to_numeric(df[close_col], errors='coerce').ffill().astype('float32')
    v_s = pd.to_numeric(df[vol_col], errors='coerce').fillna(0.0).astype('float32') if vol_col in df.columns else np.zeros(len(df), dtype=np.float32)

    df_norm = pd.DataFrame({
        'timestamp': df['timestamp'],
        'close': c_s,
        'volume': v_s,
    })

    if output_parquet_path is None:
        output_parquet_path = dpaths.normalized_path("macro", f"{macro_name}_{interval}.parquet", fallback=False)
    os.makedirs(os.path.dirname(os.path.abspath(output_parquet_path)), exist_ok=True)

    df_norm.to_parquet(output_parquet_path, index=False, engine='pyarrow', compression='snappy')
    local_log(f"  💾 [Normalized Parquet 저장] {output_parquet_path} (총 {len(df_norm):,}행)")

    ManifestManager.create_manifest(
        data_path=output_parquet_path,
        provider="macro",
        data_tier="normalized",
        asset_type=f"macro_{interval}",
        symbol=macro_name,
        notes=f"Macro Normalized {interval} Candles ({len(df_norm)} bars, Canonical Schema)",
        extra_meta={
            "sampling_mode": "BAR_NATIVE",
            "sampling_desc": f"🔵 NATIVE {interval} (원천 정규화)",
            "source_interval": interval,
            "target_interval": interval,
        }
    )
    ok, msg = ManifestManager.verify_manifest(output_parquet_path)
    local_log(f"  📜 [Manifest Verified] {msg}")

    return output_parquet_path


# =============================================================================
# KRX 일봉 (일 단위 배분 엔진용, 2026-10-10)
# =============================================================================
# 원천: Creon StockChart 'D' + 수정주가 (raw/creon/candle_1d/A{sym}_1d.csv). 분배금·분할이 반영돼 총수익에 가깝다.
# 함정: 수정주가는 '받은 날' 기준으로 과거 전체를 다시 계산한 값이다. 수집기가 증분 모드로 새 행만 이어 붙이면
#       그 사이 분배락이 있었을 때 옛 행(옛 기준)과 새 행(새 기준)이 섞여 가짜 수익률 점프가 생긴다.
#       그래서 빌드할 때 1m 종가(수정 안 된 실제 가격)와 대조해 기준이 섞였는지 검사한다.

# 정상: 분배락일에 (수정 일봉 / 실제 종가) 비율이 '올라가는' 계단만 있다(과거가 분배금만큼 깎임).
# 기준 혼합: 비율이 '내려가는' 계단이 생긴다. 분배 수익률보다 큰 오름 계단도 이상하다.
_ADJ_DOWN_STEP_TOL = 0.0005
_ADJ_UP_STEP_MAX = 0.05


def session_close_prices(minute_close: pd.Series) -> pd.Series:
    """1m 종가(봉 종료 라벨) → 날짜별 실제 장 마감 시각 봉의 종가. 수능일처럼 마감이 늦는 날은 장 일정 원장을 따른다."""
    from shared.core.market_utils import krx_session
    s = minute_close.dropna()
    s.index = pd.to_datetime(s.index)
    out = {}
    for day, grp in s.groupby(s.index.normalize()):
        close_t = krx_session(day).close
        hit = grp[grp.index.time == close_t]
        if len(hit):
            out[day] = float(hit.iloc[-1])
    return pd.Series(out, dtype=float).sort_index()


def check_adjusted_daily(daily_close: pd.Series, actual_close: pd.Series) -> List[str]:
    """수정 일봉이 하나의 수정 기준으로 일관된지 검사. 문제 목록(비면 정상). 겹치는 날이 없으면 검사 불가로 빈 목록."""
    j = pd.concat({"adj": daily_close, "act": actual_close}, axis=1).dropna()
    if len(j) < 2:
        return []
    step = (j["adj"] / j["act"]).pct_change().dropna()
    problems = [f"{d.date()}: 수정 비율이 {v:+.4%} 내려감 — 수정주가 기준이 섞임(증분 수집 의심, 전체 재수집 필요)"
                for d, v in step[step < -_ADJ_DOWN_STEP_TOL].items()]
    problems += [f"{d.date()}: 수정 비율이 {v:+.4%} 올라감 — 분배금으로 보기엔 너무 큼"
                 for d, v in step[step > _ADJ_UP_STEP_MAX].items()]
    last = j.index[-1]
    if abs(j.loc[last, "adj"] / j.loc[last, "act"] - 1) > _ADJ_DOWN_STEP_TOL:
        problems.append(f"{last.date()}: 마지막 날 수정 종가가 실제 종가와 다름 — 수정 기준일이 최신이 아님")
    return problems


# Creon 일봉은 2026-09-14(NXT 대체거래소 개장)부터 개별 주식의 일봉이 08:00~20:00 통합 시세다(시가=프리마켓, 종가=20:00 애프터마켓).
# 정규장 기준이 필요하므로 그날부터는 1분봉 일자 저장소(무수정, 봉 종료 라벨)에서 정규장 OHLCV 를 복원한다.
# ETF 는 NXT 대상이 아니라 일봉=정규장이므로 복원해도 값이 같다(검사로 확인).
KRX_NXT_FROM = "2026-09-14"


def krx_regular_session_bars(clean_sym: str, days) -> pd.DataFrame:
    """1분봉 일자 저장소 → 날짜별 정규장 OHLCV(무수정). 봉 종료 라벨이므로 (개장, 마감] 구간. 파일 없는 날은 빠진다."""
    from shared.core.market_utils import krx_regular_bar_mask
    from shared.data_library.paths import data_vault_path
    rows = []
    for day in days:
        fp = data_vault_path("raw", "creon", "candle_1m", f"A{clean_sym}", f"{pd.Timestamp(day):%Y-%m-%d}.csv")
        if not os.path.exists(fp):
            continue
        m = pd.read_csv(fp, usecols=["timestamp", "upbit_open", "upbit_high", "upbit_low", "upbit_close", "upbit_volume", "upbit_trade_price"])
        m = m[krx_regular_bar_mask(m["timestamp"], "BAR_CLOSE").to_numpy()]     # Creon 1분봉 = 봉 종료 라벨
        if m.empty or m["upbit_volume"].sum() <= 0:
            continue
        rows.append({"timestamp": pd.Timestamp(day).normalize(), "open": float(m["upbit_open"].iloc[0]), "high": float(m["upbit_high"].max()),
                     "low": float(m["upbit_low"].min()), "close": float(m["upbit_close"].iloc[-1]),
                     "volume": float(m["upbit_volume"].sum()), "trade_amount": float(m["upbit_trade_price"].sum())})
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "trade_amount"])


def build_normalized_krx_daily(
    symbol: str,
    raw_path: Optional[str] = None,
    output_parquet_path: Optional[str] = None,
    local_log: Optional[Callable[[str], None]] = None,
) -> str:
    """KRX 일봉 → normalized/candles_1d/{sym}_1d.parquet (수정가). 무수정 원천이 있으면 원장 계수로 수정, 없으면 Creon 수정 일봉.
    어느 쪽이든 1m 실제 종가와 대조해 수정 기준이 섞였으면 실패."""
    if local_log is None:
        local_log = logging.info
    clean_sym = SourceMappingManager.clean_symbol(symbol)
    # 원천 우선순위: 무수정(raw/creon/candle_1d_unadj) × 기업행위 원장 계수 → 없으면 옛 방식(Creon 수정 일봉 그대로)
    from shared.data_library.kr_equity import bronze
    built = None if raw_path else bronze.operating_adjusted(f"A{clean_sym}")
    if built is not None:
        basis = "unadjusted_x_ledger"
        built["timestamp"] = pd.to_datetime(built["timestamp"]).dt.normalize()
        out = pd.DataFrame({"timestamp": built["timestamp"].to_numpy(), **{c: built[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close", "volume")},
                            "trade_amount": built["value"].to_numpy(dtype=float)})
        local_log(f"  🧾 [KRX 일봉] {clean_sym} 무수정 원천 × 기업행위 원장 계수(이벤트 {int((built['factor'].diff() != 0).sum() - 1)}건)")
        # NXT 이후: 정규장 OHLCV(1분봉 복원, 무수정) × 그날 계수 — 종가가 정규장과 다른 종목(NXT 거래 주식)만.
        # ETF 는 가격이 정규장과 100% 같고 거래량만 시간외 단일가만큼 많다(2026-10-16 실측) → 손대지 않는다.
        nxt = out["timestamp"] >= pd.Timestamp(KRX_NXT_FROM)
        reg = krx_regular_session_bars(clean_sym, out.loc[nxt, "timestamp"]).set_index("timestamp")
        cur = out.set_index("timestamp")
        is_nxt = bool(len(reg)) and not np.allclose(cur.loc[reg.index, "close"].to_numpy() / built.set_index(out["timestamp"]).loc[reg.index, "factor"].to_numpy(),
                                                    reg["close"].to_numpy())
        if is_nxt:
            fac = pd.Series(built["factor"].to_numpy(), index=out["timestamp"]).reindex(reg.index).to_numpy()
            idx = out.index[out["timestamp"].isin(reg.index)]
            r = reg.loc[out.loc[idx, "timestamp"]]
            f = pd.Series(fac, index=reg.index).loc[r.index].to_numpy()
            for c in ("open", "high", "low", "close"):
                out.loc[idx, c] = r[c].to_numpy() * f
            out.loc[idx, "volume"] = r["volume"].to_numpy() / f
            out.loc[idx, "trade_amount"] = r["trade_amount"].to_numpy()
            drop = nxt & ~out["timestamp"].isin(reg.index)
            out = out[~drop].reset_index(drop=True)
            local_log(f"  🕘 [KRX 일봉] {clean_sym} NXT 통합 시세 → 정규장 복원 {len(reg)}일"
                      + (f" · 1분봉 아직 없는 {int(drop.sum())}일은 제외(다음 수집 때 채움)" if drop.any() else ""))
    else:
        basis = "creon_adjusted"
        raw_path = raw_path or dpaths.raw_path("creon", "candle_1d", f"A{clean_sym}_1d.csv", fallback=False)
        if not os.path.exists(raw_path):
            raise FileNotFoundError(f"[NormalizedBuilder] KRX 일봉 원천 부재: {raw_path} — Creon 일봉 수집(--type daily --force) 필요")
        df = candle_store.read_csv(raw_path)
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.normalize()
        df = df.dropna(subset=["timestamp"]).drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")
        out = pd.DataFrame({
            "timestamp": df["timestamp"].to_numpy(),
            "open": pd.to_numeric(df["upbit_open"], errors="coerce").to_numpy(dtype=float),
            "high": pd.to_numeric(df["upbit_high"], errors="coerce").to_numpy(dtype=float),
            "low": pd.to_numeric(df["upbit_low"], errors="coerce").to_numpy(dtype=float),
            "close": pd.to_numeric(df["upbit_close"], errors="coerce").to_numpy(dtype=float),
            "volume": pd.to_numeric(df["upbit_volume"], errors="coerce").to_numpy(dtype=float),
            "trade_amount": pd.to_numeric(df["upbit_trade_price"], errors="coerce").to_numpy(dtype=float),
        })
        local_log(f"  ℹ️ [KRX 일봉] {clean_sym} 무수정 원천 없음 — Creon 수정 일봉 그대로 사용(다음 일봉 수집 때 무수정도 받는다)")
    bad = out["close"].isna() | (out["close"] <= 0)
    if bad.any():
        raise ValueError(f"[NormalizedBuilder] {clean_sym} 일봉 종가 결측/0 이하 {int(bad.sum())}행")

    minute_path = dpaths.normalized_path("candles_1m", f"{clean_sym}_1m.parquet", fallback=False)
    checked = "1m 없음 — 검사 생략"
    if os.path.exists(minute_path):
        m = pd.read_parquet(minute_path, columns=["timestamp", "close"])
        actual = session_close_prices(m.set_index(pd.to_datetime(m["timestamp"]))["close"])
        problems = check_adjusted_daily(out.set_index("timestamp")["close"], actual)
        if problems:
            raise ValueError(f"[NormalizedBuilder] {clean_sym} 수정주가 일관성 실패: " + "; ".join(problems[:5]))
        overlap = actual.index.intersection(pd.DatetimeIndex(out["timestamp"]))
        checked = f"{overlap.min().date()}~{overlap.max().date()} {len(overlap)}일 1m 실제 종가와 일치" if len(overlap) else "겹치는 날 없음"
    local_log(f"  🔎 [KRX 일봉] {clean_sym} 수정 기준 검사: {checked}")

    if output_parquet_path is None:
        output_parquet_path = dpaths.normalized_path("candles_1d", f"{clean_sym}_1d.parquet", fallback=False)
    os.makedirs(os.path.dirname(os.path.abspath(output_parquet_path)), exist_ok=True)
    out.to_parquet(output_parquet_path, index=False, engine="pyarrow", compression="snappy")
    local_log(f"  💾 [Normalized Parquet 저장] {output_parquet_path} (총 {len(out):,}행)")
    ManifestManager.create_manifest(
        data_path=output_parquet_path,
        provider="creon",
        data_tier="normalized",
        asset_type="candle_1d",
        symbol=clean_sym,
        notes=f"KRX 수정주가 일봉 ({len(out)} bars, {basis})",
        extra_meta={
            "price_adjustment": basis,
            "adjustment_basis_date": str(out["timestamp"].iloc[-1].date()),
            "adjustment_check": checked,
            "source_interval": "1d",
            "target_interval": "1d",
        },
    )
    return output_parquet_path


def generate_market_grid(
    market: str,
    dates: list,
    interval: str = "1m",
    start_ts: Optional[pd.Timestamp] = None,
    end_ts: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """
    시장별(KRX, US, CRYPTO) 정규 거래시간 세션 캘린더 그리드를 생성합니다.
    """
    mkt = str(market).strip().upper()
    freq = get_pandas_freq(interval)
    grid_list = []

    if mkt == "KRX":
        # KRX 정규장 평일 09:00:00 ~ 15:30:00 (특수 거래일은 krx_session_calendar.json 원장)
        from shared.core.market_utils import krx_session
        for d in dates:
            sess = krx_session(d)
            day_start = pd.Timestamp.combine(pd.Timestamp(d).date(), sess.open)
            day_end = pd.Timestamp.combine(pd.Timestamp(d).date(), sess.close)
            grid_list.extend(pd.date_range(day_start, day_end, freq=freq))

    elif mkt in ["US", "US_EQUITY", "KIS"]:
        # 미국 정규장 KST 기준 22:30:00 ~ 익일 05:00:00 (서머타임)
        for d in dates:
            day_start = pd.Timestamp(d.year, d.month, d.day, 22, 30, 0)
            next_day = day_start + pd.Timedelta(days=1)
            day_end = pd.Timestamp(next_day.year, next_day.month, next_day.day, 5, 0, 0)
            grid_list.extend(pd.date_range(day_start, day_end, freq=freq))

    elif mkt in ["CRYPTO", "24/7"]:
        # 가상자산 24/7/365 연중무휴 완전 그리드
        if dates:
            min_d = min(dates)
            max_d = max(dates)
            s_ts = start_ts or pd.Timestamp(min_d.year, min_d.month, min_d.day, 0, 0, 0)
            e_ts = end_ts or pd.Timestamp(max_d.year, max_d.month, max_d.day, 23, 59, 59)
            grid_list = pd.date_range(s_ts, e_ts, freq=freq).tolist()
        elif start_ts and end_ts:
            grid_list = pd.date_range(start_ts, end_ts, freq=freq).tolist()

    else:
        # 일반 연속 구간
        if start_ts and end_ts:
            grid_list = pd.date_range(start_ts, end_ts, freq=freq).tolist()

    return pd.DataFrame({"timestamp": sorted(list(set(grid_list)))})


def build_normalized_us_stock(
    symbol: str,
    interval: str = "1m",
    raw_candle_path: Optional[str] = None,
    output_parquet_path: Optional[str] = None,
    local_log: Optional[Callable[[str], None]] = None
) -> str:
    """
    미국 직투 주식/ETF(QQQ, GOOGL 등)를 단일 자산 표준 Parquet으로 정규화합니다.
    """
    if local_log is None:
        local_log = logging.info

    clean_sym = str(symbol).strip().upper()

    if raw_candle_path is None or not os.path.exists(raw_candle_path):
        candidates = [
            dpaths.raw_path("yahoo", f"candle_{interval}", f"{clean_sym}_{interval}.csv"),   # 출처 Yahoo (2026-10-15~)
            dpaths.raw_path("kis", f"candle_{interval}", f"{clean_sym}_{interval}.csv"),
            dpaths.raw_path("kis", "candle_1m", f"{clean_sym}_1m.csv"),
            dpaths.data_path("historical", "kis", f"candle_{interval}", f"{clean_sym}_{interval}.csv"),
        ]
        for cand in candidates:
            if cand and os.path.exists(cand):
                raw_candle_path = cand
                break

    if not raw_candle_path or not os.path.exists(raw_candle_path):
        raise FileNotFoundError(f"[NormalizedBuilder] 미국 자산 {clean_sym} 원천 분봉 파일을 찾을 수 없습니다.")

    local_log(f"📦 [Normalized Builder] 미국 {clean_sym} {interval} 정규화 빌드 시작 (원천: {dpaths.display(raw_candle_path)})...")

    df = pd.read_csv(raw_candle_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # 표준 컬럼 식별
    close_col = "close" if "close" in df.columns else ("upbit_close" if "upbit_close" in df.columns else "trade_price")
    vol_col = "volume" if "volume" in df.columns else ("upbit_volume" if "upbit_volume" in df.columns else "acc_trade_volume")
    open_col = "open" if "open" in df.columns else ("upbit_open" if "upbit_open" in df.columns else close_col)
    high_col = "high" if "high" in df.columns else ("upbit_high" if "upbit_high" in df.columns else close_col)
    low_col = "low" if "low" in df.columns else ("upbit_low" if "upbit_low" in df.columns else close_col)
    tp_col = "trade_amount" if "trade_amount" in df.columns else ("upbit_trade_price" if "upbit_trade_price" in df.columns else "trade_price")

    o_s = pd.to_numeric(df[open_col], errors="coerce").ffill().astype("float32")
    h_s = pd.to_numeric(df[high_col], errors="coerce").ffill().astype("float32")
    l_s = pd.to_numeric(df[low_col], errors="coerce").ffill().astype("float32")
    c_s = pd.to_numeric(df[close_col], errors="coerce").ffill().astype("float32")
    v_s = pd.to_numeric(df[vol_col], errors="coerce").fillna(0.0).astype("float32")

    if tp_col in df.columns:
        amt_s = pd.to_numeric(df[tp_col], errors="coerce").fillna(0.0).astype("float32")
        vwap_s = np.where(v_s > 0, amt_s / (v_s + 1e-8), c_s).astype("float32")
    else:
        amt_s = (c_s * v_s).astype("float32")
        vwap_s = ((h_s + l_s + c_s) / 3.0).astype("float32")

    df_norm = pd.DataFrame({
        "timestamp": df["timestamp"],
        "open": o_s,
        "high": h_s,
        "low": l_s,
        "close": c_s,
        "volume": v_s,
        "trade_amount": amt_s,
        "vwap": vwap_s,
    })

    if output_parquet_path is None:
        output_parquet_path = dpaths.normalized_path(f"candles_{interval}", f"{clean_sym}_{interval}.parquet")
    os.makedirs(os.path.dirname(os.path.abspath(output_parquet_path)), exist_ok=True)

    df_norm.to_parquet(output_parquet_path, index=False, engine="pyarrow", compression="snappy")
    local_log(f"  💾 [Normalized Parquet 저장] {output_parquet_path} (총 {len(df_norm):,}행)")

    ManifestManager.create_manifest(
        data_path=output_parquet_path,
        provider="kis",
        data_tier="normalized",
        asset_type=f"candles_{interval}",
        symbol=clean_sym,
        notes=f"US Equity Normalized {interval} Candles ({len(df_norm)} bars, Canonical Schema)",
        extra_meta={
            "sampling_mode": "BAR_NATIVE",
            "sampling_desc": f"🔵 NATIVE {interval} (원천 정규화)",
            "source_interval": interval,
            "target_interval": interval,
        }
    )
    ok, msg = ManifestManager.verify_manifest(output_parquet_path)
    local_log(f"  📜 [Manifest Verified] {msg}")

    return output_parquet_path


def build_normalized_crypto(
    symbol: str,
    interval: str = "10m",
    raw_candle_path: Optional[str] = None,
    output_parquet_path: Optional[str] = None,
    local_log: Optional[Callable[[str], None]] = None
) -> str:
    """
    가상자산(KRW-BTC, KRW-ETH, BTCUSDT, BTC-USD 등)을 24/7 완전 그리드 단일 자산 표준 Parquet으로 정규화합니다.
    """
    if local_log is None:
        local_log = logging.info

    clean_sym = str(symbol).strip().upper()
    detected_source_interval = interval

    if raw_candle_path is None or not os.path.exists(raw_candle_path):
        candidates = [
            # 1. 목표 주기(interval)와 정확히 일치하는 원천 우선 탐색
            (dpaths.raw_path("crypto", "upbit", f"{clean_sym}_{interval}.csv"), interval),
            (dpaths.raw_path("crypto", "binance", f"{clean_sym}_{interval}.csv"), interval),
            (dpaths.raw_path("crypto", "coinbase", f"{clean_sym}_{interval}.csv"), interval),
            # 2. 최고 정밀도 공식 1분봉(1m) 마스터 원천 탐색 ➔ 목표 스케일로 다운스케일 합성
            (dpaths.raw_path("crypto", "upbit", f"{clean_sym}_1m.csv"), "1m"),
            (dpaths.raw_path("crypto", "binance", f"{clean_sym}_1m.csv"), "1m"),
            (dpaths.raw_path("crypto", "coinbase", f"{clean_sym}_1m.csv"), "1m"),
            # 3. 보조 레거시 파일
            (dpaths.raw_path("crypto", "binance", f"{clean_sym}_15m.csv"), "15m"),
            (dpaths.raw_path("crypto", "coinbase", f"{clean_sym}_10m.csv"), "10m"),
        ]
        from shared.data_library.raw_ingest import last_timestamp as _raw_last_ts

        def _stale_vs_1m(path: str) -> bool:
            # 같은 거래소 1분봉 마스터보다 끝 시각이 뒤처진 비-1분봉 원천은 수집이 끊긴 레거시다
            # (2026-10-04부터 가상자산은 1M/1D만 수집 → 10m/15m 파일이 멈춘 채 남아 참조 끝부분을 NaN 으로 만들었다)
            m1 = os.path.join(os.path.dirname(path), f"{clean_sym}_1m.csv")
            own, master = _raw_last_ts(path), _raw_last_ts(m1)
            if not own or not master:
                return False
            return pd.Timestamp(own) < pd.Timestamp(master).floor("D")

        for cand, src_intv in candidates:
            if cand and os.path.exists(cand):
                if src_intv not in ("1m", "1d") and _stale_vs_1m(cand):
                    local_log(f"   ⏭️ [Normalized Builder] {dpaths.display(cand)} 는 1분봉 마스터보다 뒤처진 레거시 원천 → 1분봉에서 합성")
                    continue
                raw_candle_path = cand
                detected_source_interval = src_intv
                break

    if not raw_candle_path or not os.path.exists(raw_candle_path):
        raise FileNotFoundError(f"[NormalizedBuilder] 가상자산 {clean_sym} 원천 파일을 찾을 수 없습니다.")

    is_downscale = (detected_source_interval != interval)
    downscale_desc = f" (원천 {detected_source_interval} ➔ 목표 {interval} 다운스케일)" if is_downscale else ""
    local_log(f"📦 [Normalized Builder] 가상자산 {clean_sym} {interval} 24/7 정규화 빌드 시작 (원천: {dpaths.display(raw_candle_path)}){downscale_desc}...")

    df = pd.read_csv(raw_candle_path)

    # 타임스탬프 파싱 (Epoch 및 datetime 하이브리드 지원 + μs/ms 오차 자동 치유)
    if "timestamp" in df.columns and pd.to_numeric(df["timestamp"], errors="coerce").notna().sum() > len(df) * 0.5:
        ts_num = pd.to_numeric(df["timestamp"], errors="coerce")
        # 16자리 이상(마이크로초) vs 13자리(밀리초) vs 10자리(초) 자동 정규화
        ts_ms = np.where(ts_num >= 1e14, ts_num / 1000.0, np.where(ts_num < 1e11, ts_num * 1000.0, ts_num))
        df["timestamp"] = pd.to_datetime(ts_ms, unit="ms") + pd.Timedelta(hours=9)
    elif "datetime" in df.columns:
        df["timestamp"] = pd.to_datetime(df["datetime"], errors="coerce") + pd.Timedelta(hours=9)
    elif "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    else:
        raise ValueError(f"[NormalizedBuilder] {clean_sym} 타임스탬프 컬럼을 찾을 수 없습니다.")

    df = df.dropna(subset=["timestamp"]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # 표준 컬럼 정규화
    close_col = "close" if "close" in df.columns else ("upbit_close" if "upbit_close" in df.columns else ("coinbase_spot_close" if "coinbase_spot_close" in df.columns else "trade_price"))
    vol_col = "volume" if "volume" in df.columns else ("upbit_volume" if "upbit_volume" in df.columns else "acc_trade_volume")
    open_col = "open" if "open" in df.columns else ("upbit_open" if "upbit_open" in df.columns else close_col)
    high_col = "high" if "high" in df.columns else ("upbit_high" if "upbit_high" in df.columns else close_col)
    low_col = "low" if "low" in df.columns else ("upbit_low" if "upbit_low" in df.columns else close_col)
    tp_col = "trade_amount" if "trade_amount" in df.columns else ("upbit_trade_price" if "upbit_trade_price" in df.columns else "quote_vol")

    o_s = pd.to_numeric(df[open_col], errors="coerce").ffill().astype("float32")
    h_s = pd.to_numeric(df[high_col], errors="coerce").ffill().astype("float32")
    l_s = pd.to_numeric(df[low_col], errors="coerce").ffill().astype("float32")
    c_s = pd.to_numeric(df[close_col], errors="coerce").ffill().astype("float32")
    v_s = pd.to_numeric(df[vol_col], errors="coerce").fillna(0.0).astype("float32") if vol_col in df.columns else np.zeros(len(df), dtype=np.float32)

    if tp_col in df.columns:
        amt_s = pd.to_numeric(df[tp_col], errors="coerce").fillna(0.0).astype("float32")
        vwap_s = np.where(v_s > 0, amt_s / (v_s + 1e-8), c_s).astype("float32")
    else:
        amt_s = (c_s * v_s).astype("float32")
        vwap_s = ((h_s + l_s + c_s) / 3.0).astype("float32")

    df_base = pd.DataFrame({
        "timestamp": df["timestamp"],
        "open": o_s,
        "high": h_s,
        "low": l_s,
        "close": c_s,
        "volume": v_s,
        "trade_amount": amt_s,
        "vwap": vwap_s,
    })

    # 파생 상태량(미결제약정/펀딩비) 하위 호환 로드
    for der_col in ("oi", "oi_value", "funding_rate"):
        if der_col in df.columns:
            df_base[der_col] = pd.to_numeric(df[der_col], errors="coerce").astype("float32")

    # 2단계 Silver 시점 결합: 1단계 독립 원천 펀딩비/미결제약정 파일이 존재하면 asof merge 결합
    raw_dir = os.path.dirname(os.path.abspath(raw_candle_path))
    fr_candidates = [
        os.path.join(raw_dir, f"{clean_sym}_funding_rate.csv"),
        dpaths.raw_path("crypto", "binance", f"{clean_sym}_funding_rate.csv"),
    ]
    fr_path = next((p for p in fr_candidates if p and os.path.exists(p) and os.path.getsize(p) > 0), None)
    if fr_path:
        try:
            fr_df = pd.read_csv(fr_path)
            if "timestamp" in fr_df.columns and "funding_rate" in fr_df.columns:
                fr_df["_ts_merge"] = pd.to_datetime(fr_df["timestamp"]).astype("datetime64[ns]")
                fr_df = fr_df.dropna(subset=["_ts_merge"]).drop_duplicates(subset=["_ts_merge"]).sort_values("_ts_merge")
                fr_df["funding_rate"] = pd.to_numeric(fr_df["funding_rate"], errors="coerce").astype("float32")
                df_base["_ts_merge"] = pd.to_datetime(df_base["timestamp"]).astype("datetime64[ns]")
                if "funding_rate" in df_base.columns:
                    df_base = df_base.drop(columns=["funding_rate"])
                df_base = pd.merge_asof(
                    df_base.sort_values("_ts_merge"),
                    fr_df[["_ts_merge", "funding_rate"]],
                    on="_ts_merge",
                    direction="backward"
                ).drop(columns=["_ts_merge"]).sort_values("timestamp").reset_index(drop=True)
                local_log(f"  📎 [선물 파생 정규화] {clean_sym} 독립 원천 펀딩비 시점 정합 결합 완료 ({len(fr_df):,}건)")
        except Exception as fe:
            local_log(f"  ⚠️ [선물 파생 정규화] 펀딩비 결합 알림: {fe}")

    oi_candidates = [
        os.path.join(raw_dir, f"{clean_sym}_oi.csv"),
        dpaths.raw_path("crypto", "binance", f"{clean_sym}_oi.csv"),
    ]
    oi_path = next((p for p in oi_candidates if p and os.path.exists(p) and os.path.getsize(p) > 0), None)
    if oi_path:
        try:
            oi_df = pd.read_csv(oi_path)
            oi_cols = [c for c in ("oi", "oi_value") if c in oi_df.columns]
            if "timestamp" in oi_df.columns and oi_cols:
                oi_df["_ts_merge"] = pd.to_datetime(oi_df["timestamp"]).astype("datetime64[ns]")
                oi_df = oi_df.dropna(subset=["_ts_merge"]).drop_duplicates(subset=["_ts_merge"]).sort_values("_ts_merge")
                for c in oi_cols:
                    oi_df[c] = pd.to_numeric(oi_df[c], errors="coerce").astype("float32")
                df_base["_ts_merge"] = pd.to_datetime(df_base["timestamp"]).astype("datetime64[ns]")
                for c in oi_cols:
                    if c in df_base.columns:
                        df_base = df_base.drop(columns=[c])
                df_base = pd.merge_asof(
                    df_base.sort_values("_ts_merge"),
                    oi_df[["_ts_merge"] + oi_cols],
                    on="_ts_merge",
                    direction="backward"
                ).drop(columns=["_ts_merge"]).sort_values("timestamp").reset_index(drop=True)
                local_log(f"  📎 [선물 파생 정규화] {clean_sym} 독립 원천 미결제약정({','.join(oi_cols)}) 시점 정합 결합 완료 ({len(oi_df):,}건)")
        except Exception as oe:
            local_log(f"  ⚠️ [선물 파생 정규화] 미결제약정 결합 알림: {oe}")

    _derivative_cols = [c for c in ("oi", "oi_value", "funding_rate") if c in df_base.columns]

    # 소스 캔들이 목표 interval과 다를 경우 무손실 리샘플링 적용
    from shared.data_library.candle_resampler import resample_candles
    try:
        df_base = resample_candles(df_base, target_interval=interval, source_interval="auto")
    except Exception as re_err:
        # 소스 주기 그대로를 target_interval 로 위장 저장하지 않도록 실패를 전파
        local_log(f"  ❌ [리샘플링 실패] -> {interval}: {re_err} — 저장하지 않음")
        raise RuntimeError(f"리샘플링 실패 (-> {interval}): {re_err}") from re_err

    # 24/7 완전 그리드 구축
    if len(df_base) > 1:
        grid_df = generate_market_grid(
            market="CRYPTO",
            dates=[],
            interval=interval,
            start_ts=df_base["timestamp"].min(),
            end_ts=df_base["timestamp"].max(),
        )
        df_norm = pd.merge(grid_df, df_base, on="timestamp", how="left")
        for col in ["open", "high", "low", "close", "vwap"]:
            df_norm[col] = df_norm[col].ffill()
        df_norm["volume"] = df_norm["volume"].fillna(0.0)
        df_norm["trade_amount"] = df_norm["trade_amount"].fillna(0.0)
        # 파생 상태량은 잔량(level)이라 거래 없는 칸에도 직전 값이 유효하다 — 앞으로만 전개하고,
        # 값이 아예 없던 과거 구간(OI REST 30일 밖)은 0 으로 채우지 않고 빈 값으로 남긴다.
        for col in _derivative_cols:
            df_norm[col] = df_norm[col].ffill()
    else:
        df_norm = df_base

    if output_parquet_path is None:
        output_parquet_path = dpaths.normalized_path(f"candles_{interval}", f"{clean_sym}_{interval}.parquet")
    os.makedirs(os.path.dirname(os.path.abspath(output_parquet_path)), exist_ok=True)

    df_norm.to_parquet(output_parquet_path, index=False, engine="pyarrow", compression="snappy")
    local_log(f"  💾 [Normalized Parquet 저장] {output_parquet_path} (총 {len(df_norm):,}행, 24/7 그리드 완결)")

    ManifestManager.create_manifest(
        data_path=output_parquet_path,
        provider="crypto",
        data_tier="normalized",
        asset_type=f"candles_{interval}",
        symbol=clean_sym,
        notes=f"Crypto 24/7 Normalized {interval} Candles ({len(df_norm)} bars, Canonical Schema)",
        extra_meta={
            "sampling_mode": "BAR_RESAMPLED" if is_downscale else "BAR_NATIVE",
            "sampling_desc": f"🔵 RESAMPLED {interval} (원천 {detected_source_interval} 기반 무손실 다운스케일)" if is_downscale else f"🔵 NATIVE {interval} (원천 정규화)",
            "source_interval": detected_source_interval,
            "target_interval": interval,
        }
    )
    ok, msg = ManifestManager.verify_manifest(output_parquet_path)
    local_log(f"  📜 [Manifest Verified] {msg}")

    return output_parquet_path


def _drop_unordered_tick_days(df: "pd.DataFrame", clean_sym: str,
                              local_log: Callable[[str], None]) -> "pd.DataFrame":
    """분 내부 행 순서가 1분봉과 어긋나는 날짜를 틱에서 제외한다.

    대조할 1분봉이 없거나 감사가 실패하면 **빌드를 중단한다** (fail-closed). 원천 틱 파일은
    건드리지 않으므로, 나중에 그 날짜를 다시 받아 오면 재빌드로 되살아난다.

    (2026-10-01 사고: Normalized 저장소를 비우고 재빌드하자 10s 가 1m 보다 먼저 빌드됐고,
     대조할 1분봉이 없어 감사가 조용히 건너뛰어져 순서가 섞인 대장주 28일이 10초봉에 되살아났다)
    """
    from shared.data_library.tick_order_audit import audit_tick_minute_order

    minute_path = dpaths.normalized_path("candles_1m", f"{clean_sym}_1m.parquet")
    if not (minute_path and os.path.exists(minute_path)):
        raise FileNotFoundError(
            f"[틱 순서] {clean_sym}: 대조할 1분봉 Normalized({dpaths.display(minute_path)})가 없습니다 — "
            f"10초봉은 1분봉으로 분 내부 순서를 검증한 뒤에만 만듭니다. 1m 정규화를 먼저 빌드하세요."
        )
    findings = audit_tick_minute_order(df, pd.read_parquet(minute_path), clean_sym)
    if not findings:
        return df

    unusable = [f for f in findings if not f.usable]
    if not unusable:
        local_log(f"  ✅ [틱 순서] {clean_sym}: {len(findings)}일 전부 시간순 정상")
        return df

    bad_days = {f.day for f in unusable}
    local_log(f"  🚨 [틱 순서] {clean_sym}: 시간축이 어긋난 {len(bad_days)}일을 10초봉에서 제외합니다 "
              f"(원천 틱은 보존 — 재수집하면 되살아납니다)")
    for f in unusable[:5]:
        local_log(f"      · {f.describe()}")
    if len(unusable) > 5:
        local_log(f"      · … 외 {len(unusable) - 5}일")

    keep = ~pd.to_datetime(df["timestamp"]).dt.date.astype(str).isin(bad_days)
    return df[keep].reset_index(drop=True)


def _interval_seconds(interval: str) -> float:
    """인터벌 문자열의 1봉 길이(초). 정렬 목적이므로 해석 불가 시 무한대로 보낸다."""
    from shared.data_library.interval_consistency_audit import _interval_to_timedelta
    try:
        return float(_interval_to_timedelta(interval).total_seconds())
    except Exception:
        return float("inf")


def _audit_interval_consistency_artifacts(
    clean_sym: str,
    intervals: List[str],
    local_log: Callable[[str], None],
) -> List[str]:
    """한 종목의 인접한 두 스케일(하위→상위)이 집계 정합을 지키는지 검사하고 위반 사유를 돌려준다."""
    from shared.data_library.interval_consistency_audit import audit_interval_consistency, _interval_to_timedelta

    def _load(intv: str):
        path = (dpaths.normalized_path("candles_10s", f"{clean_sym}_10s.parquet") if intv == "10s"
                else dpaths.normalized_path(f"candles_{intv}", f"{clean_sym}_{intv}.parquet"))
        return pd.read_parquet(path) if os.path.exists(path) else None

    ordered = sorted({str(i) for i in (intervals or [])}, key=_interval_seconds)
    problems: List[str] = []
    for fine, coarse in zip(ordered, ordered[1:]):
        try:
            f_df, c_df = _load(fine), _load(coarse)
            if f_df is None or c_df is None:
                continue
            finding = audit_interval_consistency(f_df, c_df, clean_sym, fine, coarse)
        except Exception as e:
            local_log(f"  ⚠️ [집계 정합] {clean_sym} {fine}→{coarse} 감사 실패: {e}")
            continue
        if finding is None:
            local_log(f"  ⏭️ [집계 정합] {clean_sym} {fine}→{coarse}: 판정 보류 (대조 가능한 봉 부족)")
            continue
        local_log(f"  {'✅' if finding.ok else '🚨'} [집계 정합] {finding.describe()}")
        if not finding.ok:
            problems.append(f"[{clean_sym}] {fine}→{coarse} 집계 정합 위반: {finding.describe()}")
            continue
        # 데이터가 고른 경계가 원장 규약이 말하는 경계와 같은지 대조한다. 두 산출물이 서로 맞더라도
        # 같은 잘못된 경계로 묶였다면(예: bar_close 1분봉을 구간 시작으로 오인) 감사만으로는 통과하기 때문이다.
        try:
            from shared.config.market_policies import bar_bucket_offset
            exp_shift = -int(bar_bucket_offset(clean_sym, fine) / pd.Timedelta(_interval_to_timedelta(fine)))
            exp_off = int(bar_bucket_offset(clean_sym, coarse) / pd.Timedelta(_interval_to_timedelta(coarse)))
        except Exception as e:
            local_log(f"  ⚠️ [집계 정합] {clean_sym} {fine}→{coarse} 원장 규약 대조 생략: {e}")
            continue
        if (finding.fine_shift_bars, finding.offset_bars) != (exp_shift, exp_off):
            msg = (f"[{clean_sym}] {fine}→{coarse} 집계 경계가 원장 라벨 규약과 다릅니다: "
                   f"데이터 (하위이동 {finding.fine_shift_bars:+d}, 오프셋 {finding.offset_bars:+d}) vs "
                   f"원장 (하위이동 {exp_shift:+d}, 오프셋 {exp_off:+d}) — timestamp_conventions.json 또는 집계 경로를 확인하세요")
            local_log(f"  🚨 {msg}")
            problems.append(msg)
    return problems


def build_normalized_ctrader_daily(clean_sym: str, out_pq: str, local_log: Optional[Callable[[str], None]] = None) -> str:
    """cTrader 원천 일봉(raw/ctrader/{sym}_1d.csv, KST 라벨 = 롤오버 시각, 봉 시작) → normalized/candles_1d. 하루 한 봉이 아니면 실패."""
    local_log = local_log or logging.info
    raw_csv = dpaths.raw_path("ctrader", f"{clean_sym}_1d.csv")
    if not os.path.exists(raw_csv):
        raise FileNotFoundError(f"cTrader 일봉 원천 부재: {raw_csv}")
    d = pd.read_csv(raw_csv)
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d = d.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    dup = d["timestamp"].dt.date.duplicated(keep=False)
    if dup.any():
        raise ValueError(f"[{clean_sym}] cTrader 일봉에 같은 날 봉이 여러 개 {int(dup.sum())}행 — 원천 정리 필요 "
                         "(scripts/fix_ctrader_utc_daily_duplicates.py)")
    out = pd.DataFrame({"timestamp": d["timestamp"], **{c: d[c].astype("float64") for c in ("open", "high", "low", "close", "volume")}})
    os.makedirs(os.path.dirname(os.path.abspath(out_pq)), exist_ok=True)
    out.to_parquet(out_pq, index=False, engine="pyarrow", compression="snappy")
    ManifestManager.create_manifest(data_path=out_pq, provider="ctrader", data_tier="normalized", asset_type="candle_1d", symbol=clean_sym,
                                    notes=f"cTrader 일봉 ({len(out)} bars, 뉴욕 17:00 롤오버, KST 라벨)",
                                    extra_meta={"source_interval": "1d", "target_interval": "1d"})
    local_log(f"  💾 [Normalized Parquet 저장] {out_pq} (총 {len(out):,}행, cTrader 원천 일봉)")
    return out_pq


def build_single_asset_normalized(
    symbol: str,
    local_log: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    단일 자산에 대해 source_mappings.json에 선언된 canonical_intervals를
    순회하며 실버(Silver) 정규화 Parquet 및 매니페스트를 일괄 빌드합니다.
    """
    if local_log is None:
        local_log = logging.info

    from shared.data_library.source_mapping_manager import SourceMappingManager
    all_maps = SourceMappingManager.load_all_mappings(force_refresh=True)
    clean_sym = SourceMappingManager.clean_symbol(symbol)

    # 심볼 검색 (A 접두사 매칭 등 유연 지원)
    asset = all_maps.get(clean_sym)
    if not asset:
        for k, v in all_maps.items():
            if SourceMappingManager.clean_symbol(k) == clean_sym:
                asset = v
                clean_sym = k
                break

    if not asset:
        return {
            "status": "error",
            "message": f"대장에 등록되지 않은 자산입니다: {clean_sym}",
            "symbol": clean_sym,
            "artifacts": [],
            "errors": [f"자산 {clean_sym} 미등록"]
        }

    cat = asset.get("category", "")
    name = asset.get("name", clean_sym)
    p_type = asset.get("primary_type", "")

    # 자산별 유효 봉 스케일 결정 (target_scales / canonical_intervals 우선, 없으면 지능적 폴백)
    intervals = asset.get("target_scales") or asset.get("canonical_intervals")
    if not intervals:
        if cat in ["KRX_ETF", "KRX_STOCK"]:
            p10s_path = dpaths.normalized_path("candles_10s", f"{clean_sym}_10s.parquet")
            has_tick = (p_type == "TICK_AND_1M") or os.path.exists(p10s_path) or tick_store.has_data(clean_sym)
            intervals = ["10s", "1m"] if has_tick else ["1m"]
        elif cat == "CRYPTO_SPOT":
            intervals = [crypto_spot_default_interval(clean_sym)]
        else:
            intervals = ["1m"]

    artifacts = []
    errors = []

    local_log(f"\n🚀 [{clean_sym}] {name} ({cat}) 타겟 스케일({', '.join(intervals)}) 정규화 시작...")

    # 상위 봉부터 만든다 — 10초봉 빌더는 같은 종목의 1분봉으로 틱 순서를 검증하므로 1분봉이 먼저 있어야 한다.
    for intv in sorted(intervals, key=_interval_seconds, reverse=True):
        try:
            # 1. 국내 주식/ETF
            if cat in ["KRX_ETF", "KRX_STOCK"]:
                if intv == "10s":
                    p10s = build_normalized_10s_stock(clean_sym, local_log=local_log)
                    artifacts.append(p10s)
                elif intv == "1d":
                    # 일봉은 1분봉 재집계가 아니라 Creon 수정주가 일봉이 원천이다 — 1분봉(실제가·약 2년)으로 만들면
                    # 분배금이 빠지고 20년 이력이 잘린다 (2026-10-10, 일 단위 배분 엔진)
                    artifacts.append(build_normalized_krx_daily(clean_sym, local_log=local_log))
                else:
                    p_cand = build_normalized_candle_stock(clean_sym, interval=intv, local_log=local_log)
                    artifacts.append(p_cand)

            # 2. 국내 선물 및 현물 지수
            elif cat in ["KRX_FUTURES", "KRX_INDEX", "DOMESTIC_FUTURES", "DOMESTIC_INDEX"]:
                raw_p = dpaths.macro_raw_path(clean_sym, "1m")
                out_p = build_normalized_macro(clean_sym, raw_path=raw_p, interval=intv, local_log=local_log)
                artifacts.append(out_p)

            # 3. 미국 해외 선물
            elif cat == "OVERSEAS_FUTURES":
                raw_p = dpaths.raw_path("overseas", "futures_1m", f"{clean_sym}_1m.csv")
                out_p = build_normalized_macro(clean_sym, raw_path=raw_p, interval=intv, local_log=local_log)
                artifacts.append(out_p)

            # 4. 미국 직투 주식/ETF
            elif cat in ["US_ETF", "US_STOCK"]:
                out_p = build_normalized_us_stock(clean_sym, interval=intv, local_log=local_log)
                artifacts.append(out_p)

            # 5. 가상자산 (현물 + 무기한 선물 — 둘 다 DataVault/raw/crypto/{provider}/ 동일 레이아웃)
            elif cat in ("CRYPTO_SPOT", "CRYPTO_FUTURES"):
                out_p = build_normalized_crypto(clean_sym, interval=intv, local_log=local_log)
                artifacts.append(out_p)

            # 6. cTrader 글로벌 지수 CFD
            elif (cat in ["GLOBAL_INDEX_CFD_PROXY", "CFD_PROXY"] or asset.get("primary_source") == "CTRADER") and intv == "1d":
                # cTrader 일봉은 원천 일봉 그대로(뉴욕 17:00 롤오버 봉, 수집기가 KST 라벨로 저장) — 1분봉을 일봉 경로에 쓰면 안 된다
                # (2026-10-16 발견: 종전엔 아래 1m 경로가 모든 주기에 1분봉을 써서 candles_1d/*.parquet 가 1분봉 수백만 행이었다)
                out_pq = dpaths.normalized_path("candles_1d", f"{clean_sym}_1d.parquet")
                artifacts.append(build_normalized_ctrader_daily(clean_sym, out_pq, local_log=local_log))

            elif cat in ["GLOBAL_INDEX_CFD_PROXY", "CFD_PROXY"] or asset.get("primary_source") == "CTRADER":
                if intv != "1m":
                    raise ValueError(f"cTrader 정규화는 1m·1d 만 지원합니다(요청 {intv}) — 1분봉을 다른 주기 이름으로 저장하지 않는다")
                raw_csv = dpaths.raw_path("ctrader", f"{clean_sym}_1m.csv")
                out_pq = dpaths.normalized_path(f"candles_{intv}", f"{clean_sym}_{intv}.parquet")
                if os.path.exists(raw_csv):
                    from scripts.normalize_ctrader_data import normalize_ctrader_data
                    ok = normalize_ctrader_data(symbol_key=clean_sym, raw_csv=raw_csv, out_parquet=out_pq)
                    if ok and os.path.exists(out_pq):
                        artifacts.append(out_pq)
                    else:
                        errors.append(f"[{clean_sym}_{intv}] cTrader 정규화 실패")
                else:
                    errors.append(f"[{clean_sym}_{intv}] cTrader 원천 CSV 부재: {raw_csv}")

            # 7. 일반 자산 폴백
            else:
                raw_cand = dpaths.raw_path("candles_1m", f"{clean_sym}_1m.csv")
                if not os.path.exists(raw_cand):
                    raw_cand = dpaths.raw_path(asset.get("primary_source", "").lower(), f"{clean_sym}_1m.csv")
                if os.path.exists(raw_cand):
                    out_p = build_normalized_candle_stock(clean_sym, interval=intv, raw_candle_path=raw_cand, local_log=local_log)
                    artifacts.append(out_p)
                else:
                    errors.append(f"[{clean_sym}_{intv}] 원천 파일 부재")

            local_log(f"  ✅ [{clean_sym}] {intv} 스케일 정규화 완료")
        except Exception as e:
            err_str = f"[{clean_sym}_{intv}] 오류: {str(e)}"
            errors.append(err_str)
            local_log(f"  ❌ {err_str}")

    # ── [T-6] 상·하위 봉 집계 정합 감사 ──
    # 같은 종목의 하위 봉과 상위 봉은 같은 체결 흐름의 두 표현이므로 집계하면 일치해야 한다.
    # 봉 내부 순서 뒤집힘처럼 "값의 집합은 맞지만 시간축이 틀어진" 결함은 행수·결측 검사로는
    # 절대 드러나지 않으며, 오직 이 대조로만 잡힌다.
    consistency = _audit_interval_consistency_artifacts(clean_sym, intervals, local_log)
    errors.extend(consistency)

    status = "ok" if not errors else ("partial_error" if artifacts else "error")
    return {
        "status": status,
        "symbol": clean_sym,
        "name": name,
        "intervals": intervals,
        "artifacts": artifacts,
        "errors": errors,
        "message": f"{name}({clean_sym}) 정규화 완료 ({len(artifacts)}개 생성)" if status == "ok" else f"{len(artifacts)}개 성공, {len(errors)}개 실패"
    }


def build_all_active_normalized(
    local_log: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    수집 대상 대장(source_mappings.json)에 등록된 모든 활성 자산을 순회하며
    선언된 canonical_intervals에 맞춰 단일 자산 표준 정규화 Parquet 및 매니페스트를 일괄 빌드합니다.
    """
    if local_log is None:
        local_log = logging.info

    from shared.data_library.source_mapping_manager import SourceMappingManager
    active_assets = SourceMappingManager.get_active_mappings()

    local_log(f"🚀 [Normalized Master Pipeline] 총 {len(active_assets)}개 활성 자산 일괄 정규화 시작...")

    results = {
        "status": "ok",
        "total_assets": len(active_assets),
        "success_count": 0,
        "failed_count": 0,
        "artifacts": [],
        "errors": []
    }

    for sym, asset in active_assets.items():
        try:
            res = build_single_asset_normalized(sym, local_log=local_log)
            if res["artifacts"]:
                results["artifacts"].extend(res["artifacts"])
                results["success_count"] += 1
            if res["errors"]:
                results["errors"].extend(res["errors"])
                if not res["artifacts"]:
                    results["failed_count"] += 1
        except Exception as e:
            results["failed_count"] += 1
            err_msg = f"[{sym}] 정규화 실패: {str(e)}"
            results["errors"].append(err_msg)
            local_log(f"  ❌ {err_msg}")

    if results["failed_count"] > 0:
        results["status"] = "partial_error"

    local_log(f"\n🎉 [Normalized Master Pipeline 완료] 성공 {results['success_count']}/{len(active_assets)}, 실패 {results['failed_count']}")
    return results


def upsert_normalized_daily(
    symbol: str,
    interval: str = "10s",
    raw_path: Optional[str] = None,
    output_parquet_path: Optional[str] = None,
    local_log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Normalized Store의 지정 자산 Parquet에 대해 마지막 기록일 이후의 신규 데이터를
    원자적(Atomic)으로 증분 업서트(Incremental Upsert)합니다.
    - 기존 Parquet이 없으면 전체 빌드(Initial Build)를 수행합니다.
    - 기존 Parquet의 마지막 날짜(last_date)를 기준으로 신규 구간만 추출 정제하여 결합합니다.
    - tmp 파일 기록 후 os.replace로 교체하여 원자적 교체 및 1:1 사이드카 매니페스트 갱신을 보장합니다.
    """
    if local_log is None:
        local_log = logging.info

    clean_sym = SourceMappingManager.clean_symbol(symbol)
    macro_info = SourceMappingManager.macro_series_info(clean_sym)   # (원장 심볼, 카테고리) 또는 None
    if macro_info:
        clean_sym = macro_info[0]   # 옛 별칭(FUTURES/spot…)으로 불려도 원장 심볼 키로 통일

    # 틱(10s)은 경로가 아니라 tick_store 창구로 읽는다 (호출자가 raw_path 를 명시한 경우만 그 파일을 쓴다)
    use_tick_store = interval == "10s" and raw_path is None and tick_store.has_data(clean_sym)

    # 1. 출력 경로 결정
    if output_parquet_path is None:
        if interval == "10s":
            output_parquet_path = dpaths.normalized_path("candles_10s", f"{clean_sym}_10s.parquet")
        elif macro_info:
            output_parquet_path = dpaths.macro_normalized_path(clean_sym, interval)
        elif interval in ["10m", "15m"] or is_crypto_symbol(clean_sym):
            output_parquet_path = dpaths.normalized_path(f"candles_{interval}", f"{clean_sym}_{interval}.parquet")
        else:
            output_parquet_path = dpaths.normalized_path(f"candles_{interval}", f"{clean_sym}_{interval}.parquet")

    # 2. 기존 Parquet 파일 부재 시 풀 빌드로 위임
    if not os.path.exists(output_parquet_path) or os.path.getsize(output_parquet_path) == 0:
        local_log(f"📦 [Upsert] {clean_sym} ({interval}) 기존 정규화 파일 부재 -> 전체 빌드 진행")
        if interval == "10s":
            out = build_normalized_10s_stock(clean_sym, raw_tick_path=None if use_tick_store else raw_path, output_parquet_path=output_parquet_path, local_log=local_log)
        elif clean_sym in ["QQQ", "GOOGL"]:
            out = build_normalized_us_stock(clean_sym, interval=interval, raw_candle_path=raw_path, output_parquet_path=output_parquet_path, local_log=local_log)
        elif is_crypto_symbol(clean_sym):
            out = build_normalized_crypto(clean_sym, interval=interval, raw_candle_path=raw_path, output_parquet_path=output_parquet_path, local_log=local_log)
        elif macro_info:
            out = build_normalized_macro(clean_sym, raw_path=raw_path, interval=interval, output_parquet_path=output_parquet_path, local_log=local_log)
        else:
            out = build_normalized_candle_stock(clean_sym, interval=interval, raw_candle_path=raw_path, output_parquet_path=output_parquet_path, local_log=local_log)
        return {"symbol": clean_sym, "interval": interval, "status": "rebuilt", "output_path": out, "added_rows": None}

    # 3. 기존 Parquet의 마지막 시점 파악
    df_existing = pd.read_parquet(output_parquet_path)
    if df_existing.empty:
        local_log(f"📦 [Upsert] {clean_sym} ({interval}) 빈 Parquet -> 전체 빌드 진행")
        out = build_normalized_candle_stock(clean_sym, interval=interval, raw_candle_path=raw_path, output_parquet_path=output_parquet_path, local_log=local_log)
        return {"symbol": clean_sym, "interval": interval, "status": "rebuilt", "output_path": out, "added_rows": None}

    last_ts = pd.to_datetime(df_existing["timestamp"]).max()
    last_date = last_ts.date()
    cutoff_ts = pd.Timestamp(last_date.year, last_date.month, last_date.day, 0, 0, 0)

    # 4. 자산별 원천 파일 식별 (SSOT 출처 매핑 우선 탐색)
    primary_src = ""
    category = ""
    if not use_tick_store and (raw_path is None or not candle_store.path_has_data(raw_path)):
        mappings = SourceMappingManager.load_all_mappings()
        mapping_info = mappings.get(clean_sym)
        if mapping_info:
            primary_src = mapping_info.get("primary_source", "")
            category = mapping_info.get("category", "")
            # 1) 해당 인터벌의 원천 파일 우선 확인
            p_up = primary_src.upper()
            if p_up == "BINANCE":
                cand_intv = dpaths.raw_path("crypto", "binance", f"{clean_sym}_{interval}.csv")
                if os.path.exists(cand_intv):
                    raw_path = cand_intv
            elif p_up == "UPBIT":
                cand_intv = dpaths.raw_path("crypto", "upbit", f"{clean_sym}_{interval}.csv")
                if os.path.exists(cand_intv):
                    raw_path = cand_intv
            elif p_up == "CREON":
                cand_intv = dpaths.raw_path("creon", f"candle_{interval}", f"A{clean_sym}_{interval}.csv")
                if candle_store.path_has_data(cand_intv):
                    raw_path = cand_intv

            # 2) 헬스 매트릭스와 동일한 표준 탐색기 호출
            if raw_path is None or not candle_store.path_has_data(raw_path):
                raw_stat = SourceMappingManager.find_raw_file_and_manifest(clean_sym, primary_src, category)
                if raw_stat.get("exists") and raw_stat.get("file_path") and candle_store.path_has_data(raw_stat["file_path"]):
                    raw_path = raw_stat["file_path"]

    # 레지스트리 검색 실패 시 인터벌별 폴백 탐색
    if not use_tick_store and (raw_path is None or not candle_store.path_has_data(raw_path)):
        if interval == "10s":
            candidates = []         # 틱은 경로가 아니라 tick_store 창구로만 읽는다 (위 use_tick_store)
        elif clean_sym in ["QQQ", "GOOGL"]:
            candidates = [
                dpaths.raw_path("kis", f"candle_{interval}", f"{clean_sym}_{interval}.csv"),
                dpaths.raw_path("kis", "candle_1m", f"{clean_sym}_1m.csv"),
            ]
        elif is_crypto_symbol(clean_sym):
            candidates = [
                dpaths.raw_path("crypto", "upbit", f"{clean_sym}_{interval}.csv"),
                dpaths.raw_path("crypto", "binance", f"{clean_sym}_{interval}.csv"),
                dpaths.raw_path("crypto", "coinbase", f"{clean_sym}_{interval}.csv"),
            ]
        elif macro_info and macro_info[1] != "OVERSEAS_FUTURES":
            candidates = [dpaths.macro_raw_path(clean_sym, "1m")]
        elif macro_info:
            candidates = [dpaths.raw_path("overseas", "futures_1m", f"{clean_sym}_1m.csv")]
        else:
            candidates = [
                dpaths.raw_path("creon", f"candle_{interval}", f"A{clean_sym}_{interval}.csv"),
                dpaths.raw_path("creon", "candle_1m", f"A{clean_sym}_1m.csv"),
            ]
        for cand in candidates:
            if cand and candle_store.path_has_data(cand):
                raw_path = cand
                break

    if not use_tick_store and (not raw_path or not candle_store.path_has_data(raw_path)):
        guide_msg = (
            f"[Upsert Guide] '{clean_sym}' ({interval}) 원천 파일을 찾을 수 없습니다.\n"
            f"  - 확인된 출처: {primary_src or '미등록'}, 범주: {category or '미등록'}\n"
            f"  - 조치 가이드: 1단계 원천 수집([ 📥 1단계 원천 수집 ])을 먼저 실행하거나, "
            f"shared/config/source_mappings.json의 primary_source 및 symbols_registry.json의 market_type 설정을 확인하십시오."
        )
        local_log(f"  ❌ {guide_msg}")
        raise FileNotFoundError(guide_msg)

    # 5. 원천 데이터 로드 및 신규 슬라이스 확인
    if use_tick_store:
        _last = tick_store.last_timestamp(clean_sym)
        raw_max_ts = pd.Timestamp(_last) if _last else pd.NaT
        df_raw = None
    else:
        df_raw = candle_store.read_csv(raw_path)
        time_col = "timestamp" if "timestamp" in df_raw.columns else ("datetime" if "datetime" in df_raw.columns else None)
        if not time_col:
            raise ValueError(f"[Upsert] {clean_sym} 타임스탬프 컬럼을 찾을 수 없습니다.")

        df_raw["_ts"] = pd.to_datetime(df_raw[time_col], errors="coerce")
        raw_max_ts = df_raw["_ts"].max()

    # 이미 최신인 경우
    if raw_max_ts <= last_ts:
        local_log(f"  ⚡ [{clean_sym}] 이미 최신 데이터입니다 (최종 시각: {last_ts}). 스킵.")
        return {"symbol": clean_sym, "interval": interval, "status": "skipped", "output_path": output_parquet_path, "added_rows": 0}

    local_log(f"⚡ [Upsert] {clean_sym} ({interval}) 증분 업서트 시작 (기준일: {last_date}, 신규 데이터: {cutoff_ts} ~ {raw_max_ts})...")

    # 신규 일자 데이터 슬라이싱
    if use_tick_store:
        df_raw_slice = tick_store.read(clean_sym, start=str(cutoff_ts.date()))
    else:
        df_raw_slice = df_raw[df_raw["_ts"] >= cutoff_ts].drop(columns=["_ts"]).copy()
    if df_raw_slice.empty:
        local_log(f"  ⚡ [{clean_sym}] 신규 일자 데이터가 비어 있습니다. 스킵.")
        return {"symbol": clean_sym, "interval": interval, "status": "skipped", "output_path": output_parquet_path, "added_rows": 0}

    # 임시 디렉토리에 신규 슬라이스 CSV 저장 후 빌더 호출
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_csv = os.path.join(tmp_dir, f"{clean_sym}_slice.csv")
        tmp_pq = os.path.join(tmp_dir, f"{clean_sym}_slice.parquet")
        df_raw_slice.to_csv(tmp_csv, index=False)

        if interval == "10s":
            build_normalized_10s_stock(clean_sym, raw_tick_path=tmp_csv, output_parquet_path=tmp_pq, local_log=lambda s: None)
        elif clean_sym in ["QQQ", "GOOGL"]:
            build_normalized_us_stock(clean_sym, interval=interval, raw_candle_path=tmp_csv, output_parquet_path=tmp_pq, local_log=lambda s: None)
        elif is_crypto_symbol(clean_sym):
            build_normalized_crypto(clean_sym, interval=interval, raw_candle_path=tmp_csv, output_parquet_path=tmp_pq, local_log=lambda s: None)
        elif macro_info:
            build_normalized_macro(clean_sym, raw_path=tmp_csv, interval=interval, output_parquet_path=tmp_pq, local_log=lambda s: None)
        else:
            build_normalized_candle_stock(clean_sym, interval=interval, raw_candle_path=tmp_csv, output_parquet_path=tmp_pq, local_log=lambda s: None)

        if not os.path.exists(tmp_pq) or os.path.getsize(tmp_pq) == 0:
            local_log(f"  ⚠️ [{clean_sym}] 신규 슬라이스 정규화 결과가 없습니다.")
            return {"symbol": clean_sym, "interval": interval, "status": "no_new_bars", "output_path": output_parquet_path, "added_rows": 0}

        df_new = pd.read_parquet(tmp_pq)

    # 6. 기존 데이터와 병합 (cutoff_ts 이전 기존 데이터 + 신규 정제 데이터)
    df_existing["_ts"] = pd.to_datetime(df_existing["timestamp"])
    df_keep = df_existing[df_existing["_ts"] < cutoff_ts].drop(columns=["_ts"])

    common_cols = [c for c in df_existing.columns if c in df_new.columns]
    df_merged = pd.concat([df_keep[common_cols], df_new[common_cols]], ignore_index=True)
    df_merged = df_merged.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)

    added_rows = len(df_merged) - len(df_existing)

    # 신규 추가/갱신된 바가 없으면 기존 상태 유지 (스킵)
    if added_rows <= 0 and df_merged["timestamp"].max() == df_existing["timestamp"].max():
        local_log(f"  ⚡ [{clean_sym}] 병합 후 신규 캔들이 없어 기존 상태를 유지합니다. (스킵)")
        return {
            "symbol": clean_sym,
            "interval": interval,
            "status": "skipped",
            "output_path": output_parquet_path,
            "total_rows": len(df_existing),
            "added_rows": 0,
        }

    # 7. 원자적 파일 교체 (Atomic Swap)
    tmp_out = f"{output_parquet_path}.tmp"
    df_merged.to_parquet(tmp_out, index=False, engine="pyarrow", compression="snappy")
    os.replace(tmp_out, output_parquet_path)

    # 8. 매니페스트 갱신
    ManifestManager.create_manifest(
        data_path=output_parquet_path,
        provider="normalized_upsert",
        data_tier="normalized",
        asset_type=f"candles_{interval}",
        symbol=clean_sym,
        notes=f"Incremental Upserted {interval} (Total {len(df_merged)} rows, {added_rows:+} rows change)"
    )

    local_log(f"  ✅ [{clean_sym}] 증분 업서트 완료 (총 {len(df_merged):,}행, 신규/갱신: {added_rows:+}행)")
    return {
        "symbol": clean_sym,
        "interval": interval,
        "status": "upserted",
        "output_path": output_parquet_path,
        "total_rows": len(df_merged),
        "added_rows": added_rows,
    }


def upsert_all_active_normalized(
    local_log: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    SourceMappingManager의 활성 자산 전체를 순회하며 일자별 증분 업서트를 원클릭 집행합니다.
    """
    if local_log is None:
        local_log = logging.info

    from shared.data_library.source_mapping_manager import SourceMappingManager
    active_assets = SourceMappingManager.get_active_mappings()

    local_log(f"\n⚡ [Normalized Master Upsert] 총 {len(active_assets)}개 활성 자산 증분 업서트 시작...")

    results = {
        "status": "ok",
        "total_active": len(active_assets),
        "upserted_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "details": [],
        "errors": []
    }

    for sym, asset in active_assets.items():
        cat = asset.get("category", "")
        try:
            if cat in ["KRX_ETF", "KRX_STOCK"]:
                p_type = asset.get("primary_type", "")
                has_tick = (p_type == "TICK_AND_1M") or tick_store.has_data(sym)

                if has_tick:
                    r1 = upsert_normalized_daily(sym, interval="10s", local_log=local_log)
                    results["details"].append(r1)

                r2 = upsert_normalized_daily(sym, interval="1m", local_log=local_log)
                results["details"].append(r2)
                results["upserted_count"] += 1
            elif cat in ["KRX_FUTURES", "KRX_INDEX", "DOMESTIC_FUTURES", "DOMESTIC_INDEX", "OVERSEAS_FUTURES"]:
                r = upsert_normalized_daily(sym, interval="1m", local_log=local_log)
                results["details"].append(r)
                results["upserted_count"] += 1
            elif cat in ["US_ETF", "US_STOCK"]:
                r = upsert_normalized_daily(sym, interval="1m", local_log=local_log)
                results["details"].append(r)
                results["upserted_count"] += 1
            elif cat == "CRYPTO_SPOT":
                intv = crypto_spot_default_interval(sym)
                r = upsert_normalized_daily(sym, interval=intv, local_log=local_log)
                results["details"].append(r)
                results["upserted_count"] += 1
            elif cat in ["GLOBAL_INDEX_CFD_PROXY", "CFD_PROXY"] or asset.get("primary_source") == "CTRADER":
                raw_csv = dpaths.raw_path("ctrader", f"{sym}_1m.csv")
                out_pq = dpaths.normalized_path("candles_1m", f"{sym}_1m.parquet")
                if os.path.exists(raw_csv):
                    from scripts.normalize_ctrader_data import normalize_ctrader_data
                    ok = normalize_ctrader_data(raw_csv=raw_csv, out_parquet=out_pq)
                    if ok:
                        results["details"].append({"symbol": sym, "interval": "1m", "status": "ok"})
                        results["upserted_count"] += 1
            else:
                raw_cand = dpaths.raw_path("candles_1m", f"{sym}_1m.csv")
                if not os.path.exists(raw_cand):
                    raw_cand = dpaths.raw_path(asset.get("primary_source", "").lower(), f"{sym}_1m.csv")
                if os.path.exists(raw_cand):
                    r = upsert_normalized_daily(sym, interval="1m", raw_path=raw_cand, local_log=local_log)
                    results["details"].append(r)
                    results["upserted_count"] += 1
        except Exception as e:
            results["failed_count"] += 1
            err_msg = f"[{sym}] 증분 업서트 실패: {str(e)}"
            results["errors"].append(err_msg)
            local_log(f"  ❌ {err_msg}")

    if results["failed_count"] > 0:
        results["status"] = "partial_error"

    local_log(f"\n🎉 [Normalized Master Upsert 완료] 성공 {results['upserted_count']}, 실패 {results['failed_count']}")
    return results


def smart_normalize_all_active(
    local_log: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    SourceMappingManager의 활성 자산 전체를 순회하며
    대상 Parquet 파일이 없는 자산은 build_single_asset_normalized(풀 빌드)를 수행하고,
    이미 Parquet 파일이 존재하는 자산은 upsert_all_active_normalized의 증분 로직을 수행하여
    시간 낭비 없이 완벽하게 정규화 인벤토리를 최신화합니다.
    """
    if local_log is None:
        local_log = logging.info

    from shared.data_library.source_mapping_manager import SourceMappingManager
    active_assets = SourceMappingManager.get_active_mappings()

    local_log(f"\n💎 [Smart Normalize Master] 총 {len(active_assets)}개 활성 자산 스마트 표준 정규화 시작...")

    results = {
        "status": "ok",
        "total_assets": len(active_assets),
        "built_count": 0,
        "upserted_count": 0,
        "failed_count": 0,
        "errors": []
    }

    for sym, asset in active_assets.items():
        _t_asset = time.perf_counter()
        intervals = asset.get("canonical_intervals") or ["1m"]
        needs_full_build = False
        for intv in intervals:
            pq_path = dpaths.normalized_path(f"candles_{intv}", f"{sym}_{intv}.parquet")
            if not os.path.exists(pq_path) or os.path.getsize(pq_path) == 0:
                needs_full_build = True
                break

        try:
            if needs_full_build:
                res = build_single_asset_normalized(sym, local_log=local_log)
                if res.get("artifacts"):
                    results["built_count"] += 1
                elif res.get("errors"):
                    results["failed_count"] += 1
                    results["errors"].extend(res["errors"])
            else:
                cat = asset.get("category", "")
                if cat in ["KRX_ETF", "KRX_STOCK"]:
                    p_type = asset.get("primary_type", "")
                    has_tick = (p_type == "TICK_AND_1M") or tick_store.has_data(sym)
                    if has_tick:
                        upsert_normalized_daily(sym, interval="10s", local_log=local_log)
                    upsert_normalized_daily(sym, interval="1m", local_log=local_log)
                    results["upserted_count"] += 1
                elif cat in ["KRX_FUTURES", "KRX_INDEX", "DOMESTIC_FUTURES", "DOMESTIC_INDEX", "OVERSEAS_FUTURES", "US_ETF", "US_STOCK"]:
                    upsert_normalized_daily(sym, interval="1m", local_log=local_log)
                    results["upserted_count"] += 1
                elif cat == "CRYPTO_SPOT":
                    intv = crypto_spot_default_interval(sym)
                    upsert_normalized_daily(sym, interval=intv, local_log=local_log)
                    results["upserted_count"] += 1
                elif cat in ["GLOBAL_INDEX_CFD_PROXY", "CFD_PROXY"] or asset.get("primary_source") == "CTRADER":
                    raw_csv = dpaths.raw_path("ctrader", f"{sym}_1m.csv")
                    out_pq = dpaths.normalized_path("candles_1m", f"{sym}_1m.parquet")
                    if os.path.exists(raw_csv):
                        from scripts.normalize_ctrader_data import normalize_ctrader_data
                        ok = normalize_ctrader_data(raw_csv=raw_csv, out_parquet=out_pq)
                        if ok:
                            results["upserted_count"] += 1
                else:
                    for intv in intervals:
                        upsert_normalized_daily(sym, interval=intv, local_log=local_log)
                    results["upserted_count"] += 1
        except Exception as e:
            results["failed_count"] += 1
            err_msg = f"[{sym}] 스마트 정규화 실패: {e}"
            results["errors"].append(err_msg)
            local_log(f"  ❌ {err_msg}")
        local_log(f"  ⏱️ [{sym}] 정규화 {time.perf_counter() - _t_asset:.1f}초")

    if results["failed_count"] > 0:
        results["status"] = "partial_error"

    local_log(f"🎉 [Smart Normalize Master 완료] 신규빌드 {results['built_count']}개, 증분업서트 {results['upserted_count']}개, 실패 {results['failed_count']}개")
    return results




