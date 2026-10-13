import os
import sys
import shutil
import subprocess
import json
import time
import zipfile
import requests
import calendar
import logging
import pandas as pd
import numpy as np
from datetime import datetime

# CTradeSystem 루트 디렉토리를 sys.path에 추가 (shared 패키지 참조 보장)
_root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

# [T-3] 참조/매크로 stale 상한 단일 정책 (배치 빌더 · 온디맨드 조립기 공용)
from shared.data_library.reference_staleness import (  # noqa: E402
    merge_asof_with_stale_cap,
    resolve_stale_cap,
)

# [T-5] 데이터·배포 경로 SSOT — CWD 상대경로 금지 (shared.data_library.paths)
from shared.data_library import paths as dpaths  # noqa: E402
from shared.data_library import candle_store, tick_store  # noqa: E402
from shared.data_library.manifest_manager import ManifestManager  # noqa: E402

from shared.data_library.source_mapping_manager import SourceMappingManager as _SMM  # noqa: E402
from shared.core.market_utils import get_target_symbol  # noqa: E402
from shared.config.config_manager import get_asset_id, ref_key  # noqa: E402
from shared.config.market_policies import BENCHMARK_ASSET_ID, RELATIVE_MOMENTUM_ASSET_ID  # noqa: E402
from shared.core import stream_keys as _sk  # noqa: E402

_DATA_ROOT = dpaths.DATA_ROOT


def _clean_sym(symbol) -> str:
    """크레온 'A'+7자리 접두사만 제거 (AAPL 등 일반 티커 훼손 방지)"""
    return _SMM.clean_symbol(symbol)


def _atomic_write_csv(df, path: str, **kwargs) -> None:
    """tmp 파일에 쓴 뒤 os.replace 로 교체 (쓰기 도중 중단돼도 원본 보존)"""
    import tempfile as _tf
    dir_name = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp = _tf.mkstemp(dir=dir_name, suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp, **kwargs)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _overseas_shift_delta(fn_l: str):
    """파일명 토큰 기반 해외/외부 캔들 시프트 폭 (None = 해외 아님/판정 불가).

    Returns: (is_overseas_by_name, Timedelta|None)
    """
    import re as _re
    tokens = [t for t in _re.split(r"[_.\-\s]+", fn_l) if t]
    tokset = set(tokens)
    name_hit = any(t in tokset for t in ("es", "nq", "ym", "rty", "cl", "gc"))
    delta = None
    if "60m" in tokset or "1h" in tokset:
        delta = pd.Timedelta(hours=1)
    elif "30m" in tokset:
        delta = pd.Timedelta(minutes=30)
    elif "15m" in tokset:
        delta = pd.Timedelta(minutes=15)
    elif "5m" in tokset:
        delta = pd.Timedelta(minutes=5)
    elif "1m" in tokset:
        delta = pd.Timedelta(minutes=1)
    elif "1d" in tokset or "day" in tokset:
        delta = pd.Timedelta(days=1)
    return name_hit, delta


def _to_kst_naive(series):
    """tz-aware 면 KST 로 변환 후 tz 제거, naive 는 그대로."""
    try:
        s = pd.to_datetime(series, errors='coerce')
    except (ValueError, TypeError):
        s = pd.to_datetime(series, errors='coerce', utc=True)
    if s.dtype == object:  # 혼합 오프셋 → UTC 로 통일 후 KST 변환
        s = pd.to_datetime(series, errors='coerce', utc=True)
    if getattr(s.dt, 'tz', None) is not None:
        s = s.dt.tz_convert('Asia/Seoul').dt.tz_localize(None)
    return s
# tkinter messagebox removed - not available in headless web environment

def get_pandas_freq(interval_str: str) -> str:
    """인터벌 문자열(1s~30s, 1m~30m, 1h~4h, 1d, 1w, 1M 등)을 최신 Pandas 규격의 오프셋 문자열로 변환"""
    if not interval_str:
        return "1min"
    s = str(interval_str).strip()
    # 월봉 케이스 (1M, M, 1mo, 1month)
    if s in ['1M', 'M', '1mo', '1month', '1ME', '1MS']:
        return '1ME'
    
    s_lower = s.lower()
    # 주봉 (1w, w, 1week)
    if s_lower in ['1w', 'w', '1week', 'w-mon']:
        return '1W-MON'
    # 일봉 (1d, d, 1day)
    if s_lower in ['1d', 'd', '1day']:
        return '1D'
    # 분봉 (1m, 3m, 5m, 15m, 30m, 1min 등)
    if s_lower.endswith('min'):
        return s_lower
    if s_lower.endswith('m') and not s_lower.endswith('month'):
        val = s_lower[:-1]
        return (val if val else '1') + 'min'
    # 초봉 (1s, 3s, 5s, 10s, 15s, 30s)
    if s_lower.endswith('s') and not s_lower.endswith('ms'):
        val = s_lower[:-1]
        return (val if val else '1') + 's'
    # 시봉 (1h, 2h, 4h)
    if s_lower.endswith('h'):
        return s_lower
    return s_lower


def get_snapshot_path(config):
    """[T-5] Gold 학습 데이터셋 절대경로 (CWD 무관).

    SSOT: `shared.data_library.paths.snapshot_path`
    """
    sym = get_target_symbol(config)
    interval = config.get('INTERVAL', '1m')
    # Gold 작성기(autolab_bridge.build_gold_snapshot)와 같은 폴더 키 — 원장 심볼 그대로(KRW-BTC).
    # (2026-10-02: 여기만 'KRW-BTC'→'BTC' 로 잘라 읽어, 크립토 프로젝트는 방금 빌드한 Gold 를 못 찾았다)
    gold = dpaths.gold_snapshot_path(_SMM.clean_symbol(sym), interval)
    if os.path.exists(gold):
        return gold
    return dpaths.snapshot_path(sym.split('-')[-1], interval)   # 레거시 위치 (data/{COIN}/)

def get_params_path(config):
    """[T-5] 베스트 파라미터 절대경로 — 세션 활성 시 deploy(Artifact) 계층, 아니면 data 계층"""
    sym = get_target_symbol(config)
    coin_code = sym.split('-')[-1]
    interval = config.get('INTERVAL', '1m')
    active_session_id = config.get('ACTIVE_SESSION_ID')
    if active_session_id and active_session_id != "N/A" and active_session_id.strip() != "":
        return dpaths.best_params_path(coin_code, interval, active_session_id)
    return dpaths.best_params_path(coin_code, interval)

def get_model_path(config):
    """[T-5] 모델 가중치 절대경로 (get_params_path 와 동일 규칙)"""
    sym = get_target_symbol(config)
    coin_code = sym.split('-')[-1]
    interval = config.get('INTERVAL', '1m')
    active_session_id = config.get('ACTIVE_SESSION_ID')
    if active_session_id and active_session_id != "N/A" and active_session_id.strip() != "":
        return dpaths.model_file_path(coin_code, interval, active_session_id)
    return dpaths.model_file_path(coin_code, interval)

def fetch_binance_with_retry(url, max_retries=5):
    """바이낸스 Vision 아카이브 다운로드 (대용량 파일 대비 120초 타임아웃 + Rate Limit 처리)

    ⚠️ stream=True를 사용하지 않음! stream=True + .content 조합은
    본문(body) 읽기 타임아웃이 적용되지 않아 서버가 응답을 안 보내면
    무한 대기하게 되는 치명적 버그가 있었음.
    timeout=(connect, read) 튜플로 연결/본문 읽기 타임아웃을 각각 설정.
    """
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=(10, 120))
            if r.status_code == 200:
                return r.content
            if r.status_code == 429:
                # Rate Limit: Retry-After 헤더 확인 후 대기
                retry_after = int(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(retry_after, 30))
                continue
            if r.status_code in (403, 404):
                return None
            if r.status_code >= 500:
                # 서버 오류: 지수 백오프
                time.sleep(min(2 ** attempt, 30))
                continue
        except Exception:
            # 네트워크 타임아웃/연결 오류: 지수 백오프
            wait = min(2 ** attempt, 30)
            time.sleep(wait)
    return None

def fetch_upbit_with_retry(url, max_retries=5):
    """업비트 crix-data 아카이브 다운로드 (대용량 파일 대비 120초 타임아웃 + Rate Limit 처리)

    ⚠️ stream=True를 사용하지 않음! stream=True + .content 조합은
    본문(body) 읽기 타임아웃이 적용되지 않아 서버가 응답을 안 보내면
    무한 대기하게 되는 치명적 버그가 있었음.
    timeout=(connect, read) 튜플로 연결/본문 읽기 타임아웃을 각각 설정.
    """
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=(10, 120))
            if r.status_code == 200:
                return r.content
            if r.status_code == 429:
                # Rate Limit: Retry-After 헤더 확인 후 대기
                retry_after = int(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(retry_after, 30))
                continue
            if r.status_code in (403, 404):
                return None
            if r.status_code >= 500:
                # 서버 오류: 지수 백오프
                time.sleep(min(2 ** attempt, 30))
                continue
        except Exception:
            # 네트워크 타임아웃/연결 오류: 지수 백오프
            wait = min(2 ** attempt, 30)
            time.sleep(wait)
    return None

def _upbit_supported_interval(interval):
    """upbit archive가 직접 지원하는 interval인지 확인. 초봉(10s 등)은 공식 1s fallback, 분봉 미지원 시 1m fallback"""
    supported = {'1s', '1m', '3m', '5m', '10m', '15m', '30m', '60m', '240m', '1d', 'day', 'week'}
    if interval in supported:
        return interval
    if interval.endswith('s') and not interval.endswith('ms'):
        return '1s'
    return '1m'

def _binance_supported_interval(interval):
    """binance archive가 직접 지원하는 interval인지 확인. 지원 안 하면 1m fallback"""
    supported = {'1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M'}
    return interval if interval in supported else '1m'

def download_binance_spot_day(config, date_str, interval='1m'):
    binance_interval = _binance_supported_interval(interval)
    symbol = config['SYMBOL_BINANCE']
    csv_name = f"{symbol}-{binance_interval}-{date_str}.csv"
    csv_dir = f"{_DATA_ROOT}/historical/binance_extracted/{symbol}"
    csv_path = os.path.join(csv_dir, csv_name)
    
    df = None
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                f.seek(0)
                cols = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'q_vol', 'trades', 'taker_base', 'taker_quote', 'ignore']
                if 'open_time' in first_line or 'open' in first_line:
                    df = pd.read_csv(f, header=0)
                    df.columns = cols[:len(df.columns)]
                else:
                    df = pd.read_csv(f, names=cols)
        except:
            df = None

    if df is None:
        url = f"https://data.binance.vision/data/spot/daily/klines/{symbol}/{binance_interval}/{symbol}-{binance_interval}-{date_str}.zip"
        binary_content = fetch_binance_with_retry(url)
        if not binary_content: return None
        
        try:
            os.makedirs(f"{_DATA_ROOT}/historical/binance/{symbol}", exist_ok=True)
            os.makedirs(csv_dir, exist_ok=True)
            
            zip_path = os.path.join(f"{_DATA_ROOT}/historical/binance/{symbol}", f"{symbol}-{binance_interval}-{date_str}.zip")
            with open(zip_path, "wb") as f_zip:
                f_zip.write(binary_content)
                
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extract(csv_name, csv_dir)
                
            if os.path.exists(zip_path):
                os.remove(zip_path)
                
            with open(csv_path, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                f.seek(0)
                cols = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'q_vol', 'trades', 'taker_base', 'taker_quote', 'ignore']
                if 'open_time' in first_line or 'open' in first_line:
                    df = pd.read_csv(f, header=0)
                    df.columns = cols[:len(df.columns)]
                else:
                    df = pd.read_csv(f, names=cols)
        except Exception as e:
            return None

    if df is None or df.empty: return None

    sample_val = int(df['open_time'].iloc[0])
    val_length = len(str(sample_val))
    
    # KST 기준 시간대 정렬 (+9시간 보정)
    if val_length == 13:
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms', errors='coerce') + pd.Timedelta(hours=9)
    elif val_length == 16:
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='us', errors='coerce') + pd.Timedelta(hours=9)
    elif val_length >= 19:
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ns', errors='coerce') + pd.Timedelta(hours=9)
    else:
        df['timestamp'] = pd.to_datetime(df['open_time'], errors='coerce') + pd.Timedelta(hours=9)

    df['timestamp'] = df['timestamp'].dt.floor('s').astype('datetime64[ns]')
    df.dropna(subset=['timestamp'], inplace=True)
    if df.empty: return None

    df['binance_spot_open'] = pd.to_numeric(df['open'])
    df['binance_spot_close'] = pd.to_numeric(df['close'])
    df['binance_spot_high'] = pd.to_numeric(df['high'])
    df['binance_spot_low'] = pd.to_numeric(df['low'])
    df['binance_spot_volume'] = pd.to_numeric(df['volume'])
    df['binance_spot_quote_volume'] = pd.to_numeric(df['q_vol'])
    df['binance_spot_trade_count'] = pd.to_numeric(df['trades'])
    df['binance_spot_taker_ratio'] = pd.to_numeric(df['taker_base']) / (df['binance_spot_volume'] + 1e-8)
    df['binance_spot_taker_quote_ratio'] = pd.to_numeric(df['taker_quote']) / (df['binance_spot_quote_volume'] + 1e-8)
    return df[[
        'timestamp',
        'binance_spot_open', 'binance_spot_close', 'binance_spot_high', 'binance_spot_low',
        'binance_spot_volume', 'binance_spot_quote_volume', 'binance_spot_trade_count',
        'binance_spot_taker_ratio', 'binance_spot_taker_quote_ratio'
    ]]

def download_binance_usdc_day(config, date_str, interval='1m'):
    binance_interval = _binance_supported_interval(interval)
    symbol_usdt = config['SYMBOL_BINANCE']
    coin_code = symbol_usdt.replace('USDT', '')
    symbol = f"{coin_code}USDC"
    
    csv_name = f"{symbol}-{binance_interval}-{date_str}.csv"
    csv_dir = f"{_DATA_ROOT}/historical/binance_extracted/{symbol}"
    csv_path = os.path.join(csv_dir, csv_name)
    
    df = None
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                f.seek(0)
                cols = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'q_vol', 'trades', 'taker_base', 'taker_quote', 'ignore']
                if 'open_time' in first_line or 'open' in first_line:
                    df = pd.read_csv(f, header=0)
                    df.columns = cols[:len(df.columns)]
                else:
                    df = pd.read_csv(f, names=cols)
        except:
            df = None

    if df is None:
        # 원천 데이터 거버넌스: 가용일 이전 자동 스킵
        from shared.data_library.data_catalog_manager import DataCatalogManager
        should_skip_usdc, _ = DataCatalogManager.should_skip("BINANCE_USDC", date_str)
        if should_skip_usdc:
            return None

        url = f"https://data.binance.vision/data/spot/daily/klines/{symbol}/{binance_interval}/{symbol}-{binance_interval}-{date_str}.zip"
        binary_content = fetch_binance_with_retry(url)
        if not binary_content: return None
        
        try:
            os.makedirs(f"{_DATA_ROOT}/historical/binance/{symbol}", exist_ok=True)
            os.makedirs(csv_dir, exist_ok=True)
            
            zip_path = os.path.join(f"{_DATA_ROOT}/historical/binance/{symbol}", f"{symbol}-{binance_interval}-{date_str}.zip")
            with open(zip_path, "wb") as f_zip:
                f_zip.write(binary_content)
                
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extract(csv_name, csv_dir)
                
            if os.path.exists(zip_path):
                os.remove(zip_path)
                
            with open(csv_path, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                f.seek(0)
                cols = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'q_vol', 'trades', 'taker_base', 'taker_quote', 'ignore']
                if 'open_time' in first_line or 'open' in first_line:
                    df = pd.read_csv(f, header=0)
                    df.columns = cols[:len(df.columns)]
                else:
                    df = pd.read_csv(f, names=cols)
        except Exception as e:
            return None

    if df is None or df.empty: return None

    sample_val = int(df['open_time'].iloc[0])
    val_length = len(str(sample_val))
    
    if val_length == 13:
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms', errors='coerce') + pd.Timedelta(hours=9)
    elif val_length == 16:
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='us', errors='coerce') + pd.Timedelta(hours=9)
    elif val_length >= 19:
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ns', errors='coerce') + pd.Timedelta(hours=9)
    else:
        df['timestamp'] = pd.to_datetime(df['open_time'], errors='coerce') + pd.Timedelta(hours=9)

    df['timestamp'] = df['timestamp'].dt.floor('s').astype('datetime64[ns]')
    df.dropna(subset=['timestamp'], inplace=True)
    if df.empty: return None

    df['binance_usdc_volume'] = pd.to_numeric(df['volume'])
    return df[['timestamp', 'binance_usdc_volume']]


def download_binance_klines_symbol_day(symbol, date_str, interval='1m'):
    """바이낸스 특정 심볼의 일별 캔들 다운로드 (공통 함수)"""
    binance_interval = _binance_supported_interval(interval)
    symbol_upper = symbol.upper()
    csv_name = f"{symbol_upper}-{binance_interval}-{date_str}.csv"
    csv_dir = f"{_DATA_ROOT}/historical/binance_extracted/{symbol_upper}"
    csv_path = os.path.join(csv_dir, csv_name)
    
    df = None
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                f.seek(0)
                cols = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'q_vol', 'trades', 'taker_base', 'taker_quote', 'ignore']
                if 'open_time' in first_line or 'open' in first_line:
                    df = pd.read_csv(f, header=0)
                    df.columns = cols[:len(df.columns)]
                else:
                    df = pd.read_csv(f, names=cols)
        except:
            df = None

    if df is None:
        url = f"https://data.binance.vision/data/spot/daily/klines/{symbol_upper}/{binance_interval}/{symbol_upper}-{binance_interval}-{date_str}.zip"
        binary_content = fetch_binance_with_retry(url)
        if binary_content:
            try:
                os.makedirs(f"{_DATA_ROOT}/historical/binance/{symbol_upper}", exist_ok=True)
                os.makedirs(csv_dir, exist_ok=True)
                zip_path = os.path.join(f"{_DATA_ROOT}/historical/binance/{symbol_upper}", f"{symbol_upper}-{binance_interval}-{date_str}.zip")
                with open(zip_path, "wb") as f_zip:
                    f_zip.write(binary_content)
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extract(csv_name, csv_dir)
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                with open(csv_path, 'r', encoding='utf-8') as f:
                    first_line = f.readline()
                    f.seek(0)
                    cols = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'q_vol', 'trades', 'taker_base', 'taker_quote', 'ignore']
                    if 'open_time' in first_line or 'open' in first_line:
                        df = pd.read_csv(f, header=0)
                        df.columns = cols[:len(df.columns)]
                    else:
                        df = pd.read_csv(f, names=cols)
            except:
                return None

    if df is None or df.empty: return None

    sample_val = int(df['open_time'].iloc[0])
    val_length = len(str(sample_val))
    if val_length == 13:
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms', errors='coerce') + pd.Timedelta(hours=9)
    elif val_length == 16:
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='us', errors='coerce') + pd.Timedelta(hours=9)
    elif val_length >= 19:
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ns', errors='coerce') + pd.Timedelta(hours=9)
    else:
        df['timestamp'] = pd.to_datetime(df['open_time'], errors='coerce') + pd.Timedelta(hours=9)

    df['timestamp'] = df['timestamp'].dt.floor('s').astype('datetime64[ns]')
    df.dropna(subset=['timestamp'], inplace=True)
    if df.empty: return None

    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    return df


def download_binance_eth_day(config, date_str, interval='1m'):
    """바이낸스 ETHUSDT 일별 캔들 다운로드 (하위 호환 래퍼)"""
    df = download_binance_klines_symbol_day(_sk.spot_key(RELATIVE_MOMENTUM_ASSET_ID), date_str, interval=interval)
    if df is None or df.empty: return None
    df['binance_eth_close'] = df['close']
    return df[['timestamp', 'binance_eth_close']]


def download_binance_btc_benchmark_day(date_str, interval='1m'):
    """바이낸스 BTCUSDT 벤치마크 일별 캔들 다운로드 (가상자산 시장 공통 대장주 시세)"""
    df = download_binance_klines_symbol_day("BTCUSDT", date_str, interval=interval)
    if df is None or df.empty: return None
    df['benchmark_btc_close'] = df['close']
    return df[['timestamp', 'benchmark_btc_close']]


def download_coinbase_day(config, date_str, symbol="BTC-USD", interval='1m'):
    """코인베이스 일별 데이터 캐시 조회 또는 REST API 수집 (KST 24시간 동기화 및 universal resampler 적용)"""
    if not symbol:
        return None
    sym_clean = str(symbol).strip().upper()
    csv_name = f"{sym_clean}-{interval}-{date_str}.csv"
    csv_dir = f"{_DATA_ROOT}/historical/coinbase_extracted/{sym_clean}"
    csv_path = os.path.join(csv_dir, csv_name)
    
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            df['timestamp'] = pd.to_datetime(df['timestamp']).astype('datetime64[ns]')
            return df[['timestamp', 'coinbase_spot_close']]
        except:
            pass

    # KST 기준 당일 00:00:00 ~ 익일 00:00:00 (24시간)
    # KST = UTC + 9h 이므로, KST 00:00:00은 전일 UTC 15:00:00Z에 해당
    start_kst = datetime.strptime(date_str, "%Y-%m-%d")
    end_kst = start_kst + pd.Timedelta(days=1)
    
    start_utc = start_kst - pd.Timedelta(hours=9)
    end_utc = end_kst - pd.Timedelta(hours=9)
    
    from shared.core.market_utils import parse_interval_seconds
    from shared.data_library.candle_resampler import resample_candles
    
    intv_sec = parse_interval_seconds(interval)
    headers = {"User-Agent": "CTradeSystem/1.0"}
    
    # 1. 분봉/시봉 인터벌 분기:
    # 5m의 배수(5m, 10m, 15m, 30m, 1h, 4h 등): granularity=300(5분봉)으로 1회 호출 수집 (288캔들)
    # 1m, 3m의 경우: granularity=60(1분봉)으로 300캔들씩 분할 수집
    raw_dfs = []
    
    if intv_sec >= 300 and intv_sec % 300 == 0:
        req_granularity = 300
        start_iso = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"https://api.exchange.coinbase.com/products/{sym_clean}/candles?start={start_iso}&end={end_iso}&granularity={req_granularity}"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                raw_candles = r.json()
                if isinstance(raw_candles, list) and len(raw_candles) > 0:
                    cdf = pd.DataFrame(raw_candles, columns=['time', 'low', 'high', 'open', 'close', 'volume'])
                    raw_dfs.append((cdf, '5m'))
        except Exception:
            pass
    else:
        req_granularity = 60
        cur_utc = start_utc
        while cur_utc < end_utc:
            next_utc = min(cur_utc + pd.Timedelta(minutes=290), end_utc)
            start_iso = cur_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_iso = next_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            url = f"https://api.exchange.coinbase.com/products/{sym_clean}/candles?start={start_iso}&end={end_iso}&granularity={req_granularity}"
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    raw_candles = r.json()
                    if isinstance(raw_candles, list) and len(raw_candles) > 0:
                        cdf = pd.DataFrame(raw_candles, columns=['time', 'low', 'high', 'open', 'close', 'volume'])
                        raw_dfs.append((cdf, '1m'))
            except Exception:
                pass
            cur_utc = next_utc

    if not raw_dfs:
        return None

    try:
        combined_cdf = pd.concat([d[0] for d in raw_dfs], ignore_index=True).drop_duplicates(subset=['time'])
        src_intv = raw_dfs[0][1]
        
        # KST 타임스탬프 변환 (+9시간)
        combined_cdf['timestamp'] = pd.to_datetime(combined_cdf['time'], unit='s') + pd.Timedelta(hours=9)
        combined_cdf['timestamp'] = combined_cdf['timestamp'].dt.floor('s').astype('datetime64[ns]')
        combined_cdf['coinbase_spot_close'] = pd.to_numeric(combined_cdf['close'], errors='coerce')
        combined_cdf.sort_values('timestamp', inplace=True)
        
        # 공통 안전 리샘플러 호출 (모든 예외 및 시계열 계약 자동 검증)
        resampled = resample_candles(
            combined_cdf[['timestamp', 'coinbase_spot_close']],
            target_interval=interval,
            source_interval=src_intv
        )
        
        # KST 당일 00:00:00 ~ 23:59:59 범위 필터링
        mask_day = (resampled['timestamp'] >= start_kst) & (resampled['timestamp'] < end_kst)
        resampled = resampled[mask_day].copy().reset_index(drop=True)
        
        if not resampled.empty:
            os.makedirs(csv_dir, exist_ok=True)
            resampled[['timestamp', 'coinbase_spot_close']].to_csv(csv_path, index=False)
            return resampled[['timestamp', 'coinbase_spot_close']]
    except Exception as e:
        pass
        
    return None



def download_coinbase_btc_day(config, date_str, interval='1m'):
    """코인베이스 BTC-USD 일별 데이터 캐시 조회 또는 REST API 수집 (하위 호환 래퍼)"""
    return download_coinbase_day(config, date_str, symbol="BTC-USD", interval=interval)


def download_upbit_usdt_day(config, date_str, interval='1m'):
    """업비트 KRW-USDT 일별 캔들 다운로드 (정밀 김치 프리미엄 환율 기반 계산용)"""
    symbol = "KRW-USDT"
    upbit_interval = _upbit_supported_interval(interval)
    date_str_fmt = date_str.replace("-", "")  # YYYY-MM-DD → YYYYMMDD
    file_yr = date_str[:4]

    file_name = f"{symbol}_candle-{upbit_interval}_{date_str_fmt}.zip"
    csv_name = f"{symbol}_candle-{upbit_interval}_{date_str_fmt}.csv"
    csv_dir = f"{_DATA_ROOT}/historical/upbit/candle_{interval}_extracted"
    csv_path = os.path.join(csv_dir, csv_name)

    # 캐시 조회
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            df['timestamp'] = pd.to_datetime(df['timestamp']).astype('datetime64[ns]')
            if 'upbit_usdt_close' in df.columns:
                return df[['timestamp', 'upbit_usdt_close']]
        except:
            pass

    # 원천 데이터 거버넌스: 상장일(2024-06-07) 이전 구간 자동 스킵 (불필요한 HTTP 통신 원천 차단)
    from shared.data_library.data_catalog_manager import DataCatalogManager
    should_skip_usdt, _ = DataCatalogManager.should_skip("UPBIT_USDT", date_str)
    if should_skip_usdt:
        return None

    # 업비트 아카이브 다운로드
    url = f"https://crix-data.upbit.com/candle/{symbol}/daily/{upbit_interval}/{file_yr}/{file_name}"
    zip_path = os.path.join(f"{_DATA_ROOT}/historical/upbit/candle_{interval}", file_name)

    try:
        binary_content = fetch_upbit_with_retry(url)
        if not binary_content:
            return None

        os.makedirs(os.path.dirname(zip_path), exist_ok=True)
        os.makedirs(csv_dir, exist_ok=True)

        with open(zip_path, "wb") as f:
            f.write(binary_content)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extract(csv_name, csv_dir)
        if os.path.exists(zip_path):
            os.remove(zip_path)

        df_raw = pd.read_csv(csv_path)
        df_raw['timestamp'] = pd.to_datetime(df_raw['date_time_utc']) + pd.Timedelta(hours=9)
        df_raw['timestamp'] = df_raw['timestamp'].dt.floor('s').astype('datetime64[ns]')
        df_raw['upbit_usdt_close'] = pd.to_numeric(df_raw['close'], errors='coerce')
        result = df_raw[['timestamp', 'upbit_usdt_close']].dropna(subset=['timestamp'])

        # 정제된 결과를 캐시에 덮어씌워 저장
        result.to_csv(csv_path, index=False)
        return result

    except Exception as e:
        return None


def _download_binance_funding_rate_range(start_date, end_date, symbol="BTCUSDT"):
    """바이낸스 펀딩비 히스토리 수집 (start_date ~ end_date, KST 기준)
    8시간 간격 데이터. scripts/add_funding_rate.py 로직을 파이프라인 내부로 통합."""
    FUNDING_API = "https://fapi.binance.com/fapi/v1/fundingRate"
    LIMIT = 1000

    try:
        # KST 기준 범위를 UTC ms로 변환
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + pd.Timedelta(days=1)
        start_ms = int((start_dt - pd.Timedelta(hours=9)).timestamp() * 1000)
        end_ms = int((end_dt - pd.Timedelta(hours=9)).timestamp() * 1000)

        all_rows = []
        current_start = start_ms

        while True:
            params = f"symbol={symbol}&limit={LIMIT}&startTime={current_start}&endTime={end_ms}"
            url = f"{FUNDING_API}?{params}"
            binary_content = fetch_binance_with_retry(url)
            if not binary_content:
                break

            rows = json.loads(binary_content.decode('utf-8'))
            if not isinstance(rows, list) or len(rows) == 0:
                break

            all_rows.extend(rows)

            last_time = rows[-1]["fundingTime"]
            if len(rows) < LIMIT or (end_ms and last_time >= end_ms):
                break
            current_start = last_time + 1
            time.sleep(0.3)

        if not all_rows:
            return None

        df = pd.DataFrame(all_rows)
        # fundingTime (ms) → KST datetime
        df['timestamp'] = pd.to_datetime(df['fundingTime'].astype('int64'), unit='ms') + pd.Timedelta(hours=9)
        df['timestamp'] = df['timestamp'].dt.floor('s').astype('datetime64[ns]')
        df['binance_funding_rate'] = df['fundingRate'].astype(float)
        return df[['timestamp', 'binance_funding_rate']].sort_values('timestamp').drop_duplicates('timestamp')

    except Exception as e:
        return None


def download_binance_futures_oi_day(config, date_str, interval='1m'):
    symbol = config['SYMBOL_BINANCE']
    csv_name = f"{symbol}-openInterestHist-{date_str}.csv"
    csv_dir = f"{_DATA_ROOT}/historical/binance_extracted/{symbol}"
    csv_path = os.path.join(csv_dir, csv_name)
    
    df = None
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            # 이미 캐싱된 레거시 파일의 컬럼명을 표준 포맷으로 리네임
            rename_map = {}
            for col in df.columns:
                col_lower = col.lower().replace('_', '')
                if col_lower in ['timestamp', 'opentime']:
                    rename_map[col] = 'timestamp'
                elif col_lower in ['binancefuturesoi', 'sumopeninterest']:
                    rename_map[col] = 'binance_futures_oi'
                elif col_lower in ['binancefuturesoivalue', 'sumopeninterestvalue']:
                    rename_map[col] = 'binance_futures_oi_value'
            if rename_map:
                df.rename(columns=rename_map, inplace=True)
        except:
            df = None

    if df is None:
        # 원천 데이터 거버넌스: 가용일 이전 자동 스킵
        from shared.data_library.data_catalog_manager import DataCatalogManager
        should_skip_oi, _ = DataCatalogManager.should_skip("BINANCE_FUTURES_OI", date_str)
        if should_skip_oi:
            return None

        # [1단계] Binance Vision Metrics 아카이브 시도 (과거 무제한)
        url_metrics = f"https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date_str}.zip"
        binary_content = fetch_binance_with_retry(url_metrics)
        
        if binary_content:
            try:
                os.makedirs(f"{_DATA_ROOT}/historical/binance/{symbol}", exist_ok=True)
                os.makedirs(csv_dir, exist_ok=True)
                
                zip_path_metrics = os.path.join(f"{_DATA_ROOT}/historical/binance/{symbol}", f"{symbol}-metrics-{date_str}.zip")
                with open(zip_path_metrics, "wb") as f_zip:
                    f_zip.write(binary_content)
                
                extracted_csv_name = f"{symbol}-metrics-{date_str}.csv"
                with zipfile.ZipFile(zip_path_metrics, 'r') as zip_ref:
                    zip_ref.extract(extracted_csv_name, csv_dir)
                
                if os.path.exists(zip_path_metrics):
                    os.remove(zip_path_metrics)
                
                raw_csv_path = os.path.join(csv_dir, extracted_csv_name)
                df_raw = pd.read_csv(raw_csv_path)
                
                if os.path.exists(raw_csv_path):
                    os.remove(raw_csv_path)
                
                df_raw.rename(columns={
                    'create_time': 'timestamp',
                    'sum_open_interest': 'binance_futures_oi',
                    'sum_open_interest_value': 'binance_futures_oi_value'
                }, inplace=True)
                
                df = df_raw[['timestamp', 'binance_futures_oi', 'binance_futures_oi_value']].copy()
                
                # Metrics의 create_time은 UTC 기준 날짜 문자열이므로 파싱 후 +9시간 보정
                df['timestamp'] = pd.to_datetime(df['timestamp']) + pd.Timedelta(hours=9)
                df['timestamp'] = df['timestamp'].dt.floor('s').astype('datetime64[ns]')
                df.dropna(subset=['timestamp'], inplace=True)
                
                df['binance_futures_oi'] = pd.to_numeric(df['binance_futures_oi'])
                df['binance_futures_oi_value'] = pd.to_numeric(df['binance_futures_oi_value'])
                
                df.to_csv(csv_path, index=False)
            except Exception as e:
                df = None

        # [2단계] Metrics 아카이브 실패 시 REST API Fallback (최근 30일 이내 라이브용)
        if df is None:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                start_dt = dt - pd.Timedelta(hours=9)
                end_dt = start_dt + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
                
                start_ms = int(start_dt.timestamp() * 1000)
                end_ms = int(end_dt.timestamp() * 1000)
                
                url_fapi = f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=5m&startTime={start_ms}&endTime={end_ms}&limit=500"
                binary_content_fapi = fetch_binance_with_retry(url_fapi)
                if binary_content_fapi:
                    data = json.loads(binary_content_fapi.decode('utf-8'))
                    if isinstance(data, list) and len(data) > 0:
                        df_raw = pd.DataFrame(data)
                        
                        df_raw.rename(columns={
                            'timestamp': 'timestamp',
                            'sumOpenInterest': 'binance_futures_oi',
                            'sumOpenInterestValue': 'binance_futures_oi_value'
                        }, inplace=True)
                        
                        df = df_raw[['timestamp', 'binance_futures_oi', 'binance_futures_oi_value']].copy()
                        
                        # API의 timestamp는 Unix ms이므로 파싱 후 +9시간 보정
                        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms') + pd.Timedelta(hours=9)
                        df['timestamp'] = df['timestamp'].dt.floor('s').astype('datetime64[ns]')
                        df.dropna(subset=['timestamp'], inplace=True)
                        
                        df['binance_futures_oi'] = pd.to_numeric(df['binance_futures_oi'])
                        df['binance_futures_oi_value'] = pd.to_numeric(df['binance_futures_oi_value'])
                        
                        df.to_csv(csv_path, index=False)
            except Exception as e:
                return None

    if df is None or df.empty: return None

    # 시간 타입 및 수치 형식 확실히 보정
    sample_val = df['timestamp'].iloc[0]
    try:
        sample_num = int(float(sample_val))
        if len(str(sample_num)) >= 10:
            if len(str(sample_num)) == 10:
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s', errors='coerce') + pd.Timedelta(hours=9)
            elif len(str(sample_num)) == 13:
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', errors='coerce') + pd.Timedelta(hours=9)
            else:
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', errors='coerce') + pd.Timedelta(hours=9)
        else:
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    except:
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')

    df['timestamp'] = df['timestamp'].dt.floor('s').astype('datetime64[ns]')
    df.dropna(subset=['timestamp'], inplace=True)
    df['binance_futures_oi'] = pd.to_numeric(df['binance_futures_oi'])
    df['binance_futures_oi_value'] = pd.to_numeric(df['binance_futures_oi_value'])
    return df[['timestamp', 'binance_futures_oi', 'binance_futures_oi_value']]
# ==============================================================================
# [NEW] KIS (한국투자증권) ETF / 주식 데이터 수집 및 스냅샷 빌더
# ==============================================================================

def run_kis_download_pipeline(config, mkt, start_ym, end_ym, interval, local_log, callback_on_finish=None, progress_callback=None):
    """한국투자증권(KIS) 과거 시세 수집 파이프라인 (ETF / 국내주식)"""
    try:
        local_log(f"\n🚀 [KIS] 한국투자증권 데이터 다운로드 프로세스 가동: {mkt} ({start_ym} ~ {end_ym}) [단위: {interval}]")
        
        baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ctrade_root = os.path.join(os.path.dirname(baker_root), "CTrade")
        token_path = os.path.join(ctrade_root, "config", "token_info.json")
        
        token = ""
        if os.path.exists(token_path):
            try:
                with open(token_path, "r", encoding="utf-8") as f:
                    t_data = json.load(f)
                    token = t_data.get("access_token", "")
            except Exception as e:
                local_log(f"   ⚠️ 토큰 로드 실패: {e}")

        start_dt = datetime.strptime(f"{start_ym}-01", "%Y-%m-%d")
        end_dt = datetime.strptime(f"{end_ym}-01", "%Y-%m-%d")
        raw_dates = pd.date_range(start=start_dt, end=end_dt + pd.offsets.MonthEnd(0), freq='D')
        curr_now = datetime.now()
        date_range = [dt for dt in raw_dates if dt.date() <= curr_now.date()]
        
        total_days = len(date_range)
        save_dir = os.path.join(baker_root, "data", "historical", "kis", f"candle_{interval}")
        os.makedirs(save_dir, exist_ok=True)
        
        local_log(f"📡 [KIS] {mkt} 시세 조회 시작 (총 {total_days}일 구간)...")
        
        start_str = start_dt.strftime("%Y%m%d")
        end_str = min(end_dt + pd.offsets.MonthEnd(0), curr_now).strftime("%Y%m%d")
        
        url_base = "https://openapi.koreainvestment.com:9443"
        daily_url = f"{url_base}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": "",
            "appsecret": "",
            "tr_id": "FHKST01010400",
            "custtype": "P"
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": mkt,
            "FID_INPUT_DATE_1": start_str,
            "FID_INPUT_DATE_2": end_str,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0"
        }
        
        download_success = False
        if token:
            try:
                res = requests.get(daily_url, headers=headers, params=params, timeout=10)
                if res.status_code == 200:
                    res_json = res.json()
                    output2 = res_json.get("output2", [])
                    if output2:
                        df_rows = []
                        for item in output2:
                            d_str = item.get("stck_bsop_date", "")
                            if not d_str:
                                continue
                            dt_val = datetime.strptime(d_str, "%Y%m%d")
                            df_rows.append({
                                "timestamp": dt_val,
                                "upbit_open": float(item.get("stck_oprc", 0)),
                                "upbit_high": float(item.get("stck_hgpr", 0)),
                                "upbit_low": float(item.get("stck_lwpr", 0)),
                                "upbit_close": float(item.get("stck_clpr", 0)),
                                "upbit_volume": float(item.get("acml_vol", 0)),
                                "upbit_trade_price": float(item.get("acml_tr_pbmn", 0))
                            })
                        if df_rows:
                            df_daily = pd.DataFrame(df_rows).sort_values("timestamp").reset_index(drop=True)
                            out_csv = os.path.join(save_dir, f"{mkt}_daily_{start_ym}_{end_ym}.csv")
                            df_daily.to_csv(out_csv, index=False)
                            local_log(f"   ✅ [KIS] {len(df_daily)}개 일봉 레코드 수집 완료: {os.path.basename(out_csv)}")
                            download_success = True
            except Exception as e:
                local_log(f"   ⚠️ KIS API 일봉 조회 예외: {e}")
        
        if not download_success:
            local_log(f"   ℹ️ [KIS] API 조회 완료 (로컬 틱/캔들 스냅샷 빌드 준비 완료)")
            
        if progress_callback:
            progress_callback(100)
            
        local_log(f"🎉 [KIS] 데이터 다운로드 파이프라인 완료: {mkt}")
    except Exception as e:
        local_log(f"❌ [KIS] 파이프라인 오류: {e}")
    finally:
        if callback_on_finish:
            callback_on_finish()


def merge_reference_symbols_into_df(df, config, interval, baker_root, local_log, capped_cols_out=None):
    """
    타겟 종목 데이터셋에 참조 대장주(reference_symbols)의 과거 캔들을 동적으로 검색하여 병합하고
    shared.feature_registry에 4대 선행 피처를 자동 등록합니다. (주식/가상자산 공통 지원)

    Args:
        capped_cols_out: [D-10] stale 상한으로 **의도적 무효화(NaN)** 한 컬럼명을 받아갈 리스트.
            호출자는 이 리스트를 `DataCatalogManager.apply_fallback_to_dataframe(..., exclude_cols=...)`
            에 전달해야 한다. 그렇지 않으면 하류 ffill 보간이 무효화된 값을 되살린다.
    """
    ref_symbols = config.get('reference_symbols') or config.get('REFERENCE_SYMBOLS') or []
    if not ref_symbols or df is None or df.empty:
        return df

    local_log(f"  📥 [Reference Symbols] {len(ref_symbols)}개 참조 대장주 결합 시작...")
    interval_str = str(interval)
    
    for r_item in ref_symbols:
        r_sym = str(r_item.get('symbol', '') if isinstance(r_item, dict) else r_item).strip()
        if not r_sym:
            continue
        r_key = ref_key(r_sym)      # 컬럼 이름은 원장 심볼에서 유도한다 (별칭 없음)

        candidate_dirs = [
            os.path.join(baker_root, "data", "historical", "kis", f"candle_{interval_str}"),
            os.path.join(baker_root, "data", "historical", "kis", "candle_1m"),
            os.path.join(baker_root, "data", "historical", "krx", "candle"),
            os.path.join(baker_root, "data", "historical", "overseas_futures"),
            os.path.join(baker_root, "data", "historical", "upbit", f"candle_{interval_str}_extracted"),
            os.path.join(baker_root, "data", "historical", "upbit", f"candle_{interval_str}"),
            os.path.join(baker_root, "data", "historical", "upbit", "candle_1m_extracted"),
            os.path.join(baker_root, "data", "historical", "upbit", "candle_1m"),
            os.path.join(baker_root, "data", "historical", "binance_extracted", r_sym),
        ]
        
        r_sym_l = r_sym.lower()
        clean_r_sym = r_sym_l.replace("krw-", "")

        r_candle_files = []
        for d in candidate_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    f_l = f.lower()
                    if (r_sym_l in f_l or clean_r_sym in f_l or f"a{clean_r_sym}" in f_l) and f_l.endswith(".csv") and not f_l.endswith(".bak"):
                        r_candle_files.append(os.path.join(d, f))
            if r_candle_files:
                break
                
        if r_candle_files:
            try:
                # df의 timestamp 범위 파악 (최적화: 필요한 기간의 파일만 필터링)
                t_min = df['timestamp'].min() if 'timestamp' in df.columns else None
                t_max = df['timestamp'].max() if 'timestamp' in df.columns else None
                
                selected_files = []
                if t_min is not None and t_max is not None and len(r_candle_files) > 30:
                    start_d = pd.to_datetime(t_min).strftime('%Y%m%d')
                    end_d = pd.to_datetime(t_max).strftime('%Y%m%d')
                    import re
                    for f in r_candle_files:
                        bn = os.path.basename(f)
                        m = re.search(r'(\d{4}[-_]?\d{2}[-_]?\d{2})', bn)
                        if m:
                            d_str = m.group(1).replace('-', '')
                            if start_d <= d_str <= end_d:
                                selected_files.append(f)
                        else:
                            selected_files.append(f)
                else:
                    selected_files = r_candle_files

                if not selected_files:
                    selected_files = r_candle_files

                r_dfs = []
                for f in selected_files:
                    try:
                        temp = pd.read_csv(f)
                        if 'date_time_utc' in temp.columns:
                            _u = pd.to_datetime(temp['date_time_utc'], errors='coerce')
                            if getattr(_u.dt, 'tz', None) is not None:
                                temp['timestamp'] = _to_kst_naive(_u)  # 오프셋 포함 → KST 변환
                            else:
                                temp['timestamp'] = _u + pd.Timedelta(hours=9)
                        elif 'date_time_kst' in temp.columns:
                            temp['timestamp'] = _to_kst_naive(temp['date_time_kst'])
                        elif 'timestamp' in temp.columns:
                            temp['timestamp'] = _to_kst_naive(temp['timestamp'])
                        else:
                            continue

                        # 📌 [해외 선물/외부 캔들 Lookahead 방지]:
                        # 파일명에 60m, 1h, 5m, 1m 등이 포함되어 있거나 overseas_futures 경로인 경우
                        # 캔들 시작 시간 -> 캔들 완성(확정) 시각으로 시프트
                        fn_l = os.path.basename(f).lower()
                        _name_hit, _delta = _overseas_shift_delta(fn_l)
                        is_overseas = "overseas_futures" in f or _name_hit
                        if is_overseas and _delta is not None:
                            temp['timestamp'] = temp['timestamp'] + _delta

                        r_dfs.append(temp)
                    except Exception:
                        pass

                if not r_dfs:
                    local_log(f"  ⚠️ [Reference Symbols] {r_sym} 캔들 파싱 결과 없음")
                    continue

                r_df = pd.concat(r_dfs, ignore_index=True)
                r_df['timestamp'] = pd.to_datetime(r_df['timestamp'], errors='coerce')
                r_df = r_df.dropna(subset=['timestamp']).drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
                
                r_df['timestamp'] = _to_kst_naive(r_df['timestamp'])
                
                r_close_col = 'target_close' if 'target_close' in r_df.columns else ('upbit_close' if 'upbit_close' in r_df.columns else ('close' if 'close' in r_df.columns else 'trade_price'))
                r_vol_col = 'target_volume' if 'target_volume' in r_df.columns else ('upbit_volume' if 'upbit_volume' in r_df.columns else ('volume' if 'volume' in r_df.columns else 'acc_trade_volume'))
                
                r_sub = pd.DataFrame({'timestamp': r_df['timestamp']})
                r_sub[f"ref_{r_key}_close"] = pd.to_numeric(r_df[r_close_col], errors='coerce')
                r_sub[f"ref_{r_key}_volume"] = pd.to_numeric(r_df[r_vol_col], errors='coerce').fillna(0.0) if r_vol_col in r_df.columns else 0.0
                
                if 'target_trade_bid_ratio' in r_df.columns:
                    r_sub[f"ref_{r_key}_bid_vol"] = r_sub[f"ref_{r_key}_volume"] * pd.to_numeric(r_df['target_trade_bid_ratio'], errors='coerce').fillna(0.5)
                elif 'upbit_trade_bid_ratio' in r_df.columns:
                    r_sub[f"ref_{r_key}_bid_vol"] = r_sub[f"ref_{r_key}_volume"] * pd.to_numeric(r_df['upbit_trade_bid_ratio'], errors='coerce').fillna(0.5)
                else:
                    r_sub[f"ref_{r_key}_bid_vol"] = r_sub[f"ref_{r_key}_volume"] * 0.5
                    
                df['timestamp'] = _to_kst_naive(df['timestamp'])
                
                # ── [T-3] stale 상한 정책 적용 (배치/온디맨드 공용 단일 구현) ──
                # 종전: merge_asof(tolerance=7일) + ffill() → ffill 이 tolerance 초과 NaN 을
                #       되메워 tolerance 가 무력화되었고, 참조 원천이 비는 구간에서
                #       최대 624시간(26일) 전 값이 현재값처럼 전파되었다 (실측).
                from shared.config.config_manager import get_symbol_info as _get_sym_info
                _ref_market = str(_get_sym_info(r_sym).get("market_type", "")) or None
                _cap = resolve_stale_cap(_ref_market, interval_str)
                _capped_cols = [f"ref_{r_key}_close", f"ref_{r_key}_volume", f"ref_{r_key}_bid_vol"]
                df = merge_asof_with_stale_cap(
                    df,
                    r_sub,
                    _capped_cols,
                    cap=_cap,
                    fill_values={
                        f"ref_{r_key}_volume": 0.0,
                        f"ref_{r_key}_bid_vol": 0.0,
                    },
                )
                # [D-10] 무효화 컬럼을 호출자에게 보고 → 하류 보간에서 제외시켜야 한다
                if capped_cols_out is not None:
                    capped_cols_out.extend(_capped_cols)
                local_log(
                    f"  🛡️ [Reference Symbols] {r_sym} stale 상한 적용: "
                    f"{_ref_market or 'UNKNOWN'} / cap={_cap}"
                )
                
                from shared.features.feature_registry import register_reference_features
                register_reference_features(r_sym)
                local_log(f"  ✅ [Reference Symbols] {r_sym} ({r_key}) 캔들 {len(r_df):,}행 병합 및 4대 선행 피처 등록 완료")
            except Exception as e:
                local_log(f"  ⚠️ [Reference Symbols] {r_sym} 병합 중 오류: {e}")
        else:
            local_log(f"  ℹ️ [Reference Symbols] {r_sym} 전용 과거 캔들 부재")

    return df


def run_kis_dataset_build_pipeline(config, mkt, start_ym, end_ym, interval, local_log, status_update_fn=None, callback_on_finish=None, progress_callback=None):
    """한국투자증권(KIS) 및 대신증권(크레온) ETF/주식 통합 스냅샷 빌드 파이프라인"""
    try:
        local_log(f"\n⚙️ [Data Builder] {mkt} 통합 데이터셋 스냅샷 빌드 프로세스 시작 (단위: {interval})...")
        if progress_callback: progress_callback(10, "1단계: 국내 캔들/틱 데이터 로드 중...")
        baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        target_dir = os.path.join(baker_root, "data", mkt)
        os.makedirs(target_dir, exist_ok=True)
        snapshot_csv = os.path.join(target_dir, f"data_snapshot_{interval}.csv")
        
        # [NEW ARCHITECTURE] Normalized Layer 및 FeatureComposer 우선 활용
        clean_mkt = _clean_sym(mkt)
        norm_pq = dpaths.normalized_path(f"candles_{interval}", f"{clean_mkt}_{interval}.parquet")
        if os.path.exists(norm_pq):
            local_log(f"  ⚡ [Normalized Store] 정규화 표준 Parquet 감지: {dpaths.display(norm_pq)}")
            from .feature_composer import FeatureComposer
            composer = FeatureComposer(local_log=local_log)
            
            # 결합할 참조는 symbol_relations 의 결합 계획(join_plan) 전부 — AutoLab 브리지와 같은 계획을 쓴다.
            # (활성 피처에 언급된 종목만 붙이면 피처를 켜는 순간 데이터가 없어 조용히 죽는다)
            from shared.data_library.symbol_relations_manager import SymbolRelationsManager
            plan = SymbolRelationsManager.join_plan(clean_mkt)
            ref_syms, macro_syms = plan["refs"], plan["series"]

            composed_df = composer.compose(
                target_symbol=clean_mkt,
                target_interval=interval,
                ref_symbols=ref_syms if ref_syms else None,
                macro_symbols=macro_syms if macro_syms else None,
                align_to_common_dates=True,
                save_snapshot=True,
                prefer_target_interval=True,
            )

            # [T-5] Gold 학습 데이터셋 저장 — save_snapshot=True는 컴포저 내부적으로
            # 미리보기(preview) CSV만 기록하므로, ASRL 등이 참조하는 실제
            # data_snapshot_{interval}.csv는 여기서 명시적으로 써야 한다.
            _atomic_write_csv(composed_df, snapshot_csv, index=False)
            local_log(f"  💾 [Data Builder] 스냅샷 저장 완료: {snapshot_csv} (총 {len(composed_df):,}행)")

            gold_target = dpaths.gold_snapshot_path(mkt, interval)
            try:
                os.makedirs(os.path.dirname(os.path.abspath(gold_target)), exist_ok=True)
                if os.path.abspath(snapshot_csv) != os.path.abspath(gold_target):
                    shutil.copy2(snapshot_csv, gold_target)
                    local_log(f"  🏆 [Medallion Gold] Gold 볼트 동기화 완료: {gold_target}")
            except Exception as _sync_err:
                local_log(f"  ⚠️ [Medallion Gold] Gold 볼트 복사 예외: {_sync_err}")

            try:
                for target_f in [gold_target, snapshot_csv]:
                    if os.path.exists(target_f):
                        ManifestManager.create_manifest(
                            data_path=target_f,
                            provider="derived",
                            data_tier="gold",
                            asset_type="snapshot",
                            symbol=mkt,
                            notes="Medallion Gold Feature Snapshot (Normalized Store 경유)"
                        )
            except Exception as _man_err:
                local_log(f"  ⚠️ [Manifest] Gold 매니페스트 발행 예외: {_man_err}")

            if progress_callback: progress_callback(100, "완료: 통합 데이터셋 스냅샷 빌드 완공")
            if callback_on_finish: callback_on_finish()
            return composed_df
        
        from shared.config.config_manager import get_symbol_info
        sym_info = get_symbol_info(mkt)
        mtype = sym_info.get("market_type", "")
        is_us_asset = (mtype in ["US_STOCK", "US_ETF"]) or (not str(mkt).isdigit() and not str(mkt).startswith("KRW-") and len(str(mkt)) <= 6)
        
        # 1. 메달리온 아키텍처(data_vault) 및 레거시 historical 디렉토리 검색
        candle_dirs = [
            os.path.join(dpaths.silver_root(), f"candles_{interval}"),
            os.path.join(dpaths.bronze_root(), "kis", f"candle_{interval}"),
            os.path.join(dpaths.bronze_root(), "creon", f"candle_{interval}"),
            os.path.join(baker_root, "data", "historical", "kis", f"candle_{interval}"),
        ]
        
        def _is_matching_symbol_csv(filename: str, target_sym: str) -> bool:
            if not filename.endswith(".csv") or ".bak" in filename or ".deactivated" in filename:
                return False
            stem = filename[:-4]
            clean_tgt = _clean_sym(target_sym)
            parts = stem.split("_")
            head = _clean_sym(parts[0])
            return head == clean_tgt

        candle_files = []
        seen_candle_names = set()
        for d in candle_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    if _is_matching_symbol_csv(f, mkt) and f not in seen_candle_names:
                        candle_files.append(os.path.join(d, f))
                        seen_candle_names.add(f)
            # Creon 1분봉은 일자 저장소 — 디스크에 {종목}_1m.csv 가 없어도 논리 경로로 창구에 묻는다
            for _stem in (f"A{_clean_sym(mkt)}", _clean_sym(mkt)):
                _logical = os.path.join(d, f"{_stem}_{interval}.csv")
                if (_logical not in candle_files and os.path.basename(_logical) not in seen_candle_names
                        and candle_store.symbol_from_path(_logical) and candle_store.path_has_data(_logical)):
                    candle_files.append(_logical)
                    seen_candle_names.add(os.path.basename(_logical))
                    break
                        
        # 틱은 경로가 아니라 tick_store 창구로 읽는다 (디스크 레이아웃과 무관)
        tick_sym = _clean_sym(mkt)
        tick_available = tick_store.has_data(tick_sym)

        # [GOVERNANCE] 사이드카 매니페스트 무결성 1차 검증
        for cand_f in candle_files:
            man_path = ManifestManager.get_manifest_path(cand_f)
            if os.path.exists(man_path):
                ok_man, man_err = ManifestManager.verify_manifest(cand_f, strict_hash=False)
                if not ok_man:
                    local_log(f"  ⚠️ [Manifest Alert] {os.path.basename(cand_f)} 매니페스트 불일치: {man_err}")
            
        # 0. 미국 주식/ETF의 경우 로컬 캔들 파일이 없으면 자동 다운로더 가동
        if is_us_asset and not candle_files:
            try:
                local_log(f"  🌐 [Auto-Fetch] 미국 주식/ETF ({mkt}) 과거 시세 자동 다운로드 시도 중...")
                from .overseas_futures_collector import download_us_market_data
                fetched_df = download_us_market_data(mkt, interval=interval, baker_root=baker_root, local_log=local_log)
                if not fetched_df.empty:
                    f_path = os.path.join(baker_root, "data", "historical", "kis", f"candle_{interval}", f"{mkt}_{interval}.csv")
                    if os.path.exists(f_path):
                        candle_files.append(f_path)
            except Exception as ex_fetch:
                local_log(f"  ⚠️ [Auto-Fetch] {mkt} 시세 자동 다운로드 예외: {ex_fetch}")

        if candle_files:
            # 1순위: 요청된 주기(예: 1m)의 완성된 캔들 원본 파일 직접 로드 (생략/누락 없이 100% 통합)
            local_log(f"  📥 [{interval}] 전용 캔들 데이터 소스 {len(candle_files)}개 로드: {[os.path.basename(f) for f in candle_files]}")
            df_list = [candle_store.read_csv(f) for f in candle_files]
            df = pd.concat(df_list).drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            df = df.dropna(subset=['timestamp']).reset_index(drop=True)
        elif tick_available:
            # 2순위: 전용 캔들 파일이 없을 경우 틱 데이터를 요청된 주기({interval})로 정밀 6단계 리샘플링 집계
            local_log(f"  📥 틱(Tick) 데이터 [{tick_sym}]를 tick_store 에서 로드하여 [{interval}] 캔들로 정밀 변환")
            df_list = [tick_store.read(tick_sym)]
            df = pd.concat(df_list, ignore_index=True)
            
            # [1단계] 틱 레벨 중복 제거 (timestamp 단독이 아닌 핵심 체결 필드 복합 검사로 동일 분 내 틱 보존)
            dedup_subset = [c for c in ['timestamp', 'upbit_close', 'upbit_volume', 'cvd', 'upbit_trade_price'] if c in df.columns]
            if dedup_subset:
                df = df.drop_duplicates(subset=dedup_subset).sort_values('timestamp').reset_index(drop=True)
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            df = df.dropna(subset=['timestamp']).reset_index(drop=True)
            local_log(f"  ✨ 틱 데이터 정제 완료 (총 {len(df):,}개 틱 보존)")
            
            if interval != "tick" and ("open" in df.columns or "upbit_open" in df.columns or "close" in df.columns or "upbit_close" in df.columns):
                # [2단계] 틱 레벨 마이크로 지표 선(先)계산 (봉 압축 전 개별 틱 오더플로우 보존)
                t_close_col = 'upbit_close' if 'upbit_close' in df.columns else 'close'
                t_vol_col = 'upbit_volume' if 'upbit_volume' in df.columns else 'volume'
                t_tp_col = 'upbit_trade_price' if 'upbit_trade_price' in df.columns else 'trade_price'
                
                trade_amt = df[t_tp_col] if t_tp_col in df.columns else (df[t_close_col] * df[t_vol_col])
                df['is_large'] = (trade_amt >= 100_000_000.0).astype(float)
                
                if 'cvd' in df.columns and t_vol_col in df.columns:
                    delta_vol = df['cvd'].diff().fillna(0.0)
                    df['bid_vol'] = ((df[t_vol_col] + delta_vol) / 2.0).clip(lower=0)
                else:
                    df['bid_vol'] = df[t_vol_col] * 0.5
                
                df['trade_count'] = 1
                
                # [3단계] 인트라미닛 틱 타임스탬프 세분화 (초 단위가 00으로 뭉쳐있는 경우 버킷 분배)
                resample_rule = get_pandas_freq(interval)
                is_sub_minute = any(unit in str(interval).lower() for unit in ['s', 'sec']) or interval in ['1s', '5s', '10s', '15s', '30s']
                
                if is_sub_minute:
                    local_log(f"  ⏱️ 인트라미닛 틱 타임스탬프 [{interval}] 정밀 버킷 분배 중...")
                    sec_step = int(''.join(filter(str.isdigit, interval)) or 10)
                    buckets_per_min = max(1, 60 // sec_step)
                    
                    # 분 단위 그룹별 순서 및 개수 계산
                    minute_grp = df.groupby('timestamp')
                    cum_count = minute_grp.cumcount()
                    total_in_min = minute_grp[t_close_col].transform('count').replace(0, 1)
                    
                    ratio = (cum_count / total_in_min) * buckets_per_min
                    ratio = ratio.fillna(0.0)
                    bucket_sec = np.floor(ratio).clip(0, buckets_per_min - 1).astype(int) * sec_step
                    df['adjusted_timestamp'] = df['timestamp'] + pd.to_timedelta(bucket_sec, unit='s')
                    df = df.set_index('adjusted_timestamp')
                else:
                    df = df.set_index('timestamp')
                    
                local_log(f"  🔄 틱 데이터를 [{interval}]({resample_rule}) 캔들로 리샘플링 집계 중...")
                agg_dict = {
                    'upbit_open': 'first' if 'upbit_open' in df.columns else 'first',
                    'upbit_high': 'max' if 'upbit_high' in df.columns else 'max',
                    'upbit_low': 'min' if 'upbit_low' in df.columns else 'min',
                    'upbit_close': 'last' if 'upbit_close' in df.columns else 'last',
                    'upbit_volume': 'sum' if 'upbit_volume' in df.columns else 'sum',
                    'upbit_trade_price': 'sum' if 'upbit_trade_price' in df.columns else 'sum',
                    'is_large': 'sum',
                    'bid_vol': 'sum',
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
                    'trade_count': 'target_trade_count',
                    'is_large': 'target_large_trade_count'
                }, inplace=True)
                
                # [4단계] 정규 거래시간(09:00:00 ~ 15:30:00) 완전 시계열 그리드 구축 및 보정
                if is_sub_minute and not df_resampled.empty:
                    local_log(f"  📐 정규 거래시간(09:00~15:30) [{interval}] 연속 시계열 그리드 완성 및 정규화 중...")
                    
                    # [P0-1] 당일(today) 장 마감(15:30) 이전 수집된 미완료 틱 또는 조기 중단 일자는 스냅샷 그리드에서 원천 제외
                    now_kst = pd.Timestamp.now(tz='Asia/Seoul').tz_localize(None)
                    today_date = now_kst.date()
                    if now_kst.time() < pd.Timestamp("15:30:00").time():
                        if (df_resampled['timestamp'].dt.date == today_date).any():
                            local_log(f"  ⏳ 당일({today_date}) 장 마감(15:30) 이전 수집된 미완료 틱은 스냅샷 그리드에서 제외합니다.")
                            df_resampled = df_resampled[df_resampled['timestamp'].dt.date != today_date].reset_index(drop=True)

                    unique_dates = df_resampled['timestamp'].dt.date.unique()
                    grid_list = []
                    valid_unique_dates = []
                    for d in unique_dates:
                        day_ts = df_resampled[df_resampled['timestamp'].dt.date == d]['timestamp']
                        if not day_ts.empty and day_ts.max().time() < pd.Timestamp("15:20:00").time() and d == today_date:
                            local_log(f"  ⚠️ 거래일 {d} 데이터 조기 종료({day_ts.max().strftime('%H:%M:%S')}) 감지: 그리드 생성 제외")
                            continue
                        day_start = pd.Timestamp(d.year, d.month, d.day, 9, 0, 0)
                        day_end = pd.Timestamp(d.year, d.month, d.day, 15, 30, 0)
                        day_grid = pd.date_range(start=day_start, end=day_end, freq=resample_rule)
                        grid_list.extend(day_grid)
                        valid_unique_dates.append(d)
                        
                    full_grid_df = pd.DataFrame({'timestamp': grid_list})
                    df = pd.merge(full_grid_df, df_resampled[df_resampled['timestamp'].dt.date.isin(valid_unique_dates)], on='timestamp', how='left')
                    
                    # 가격 필드 포워드 필 (이전 종가 유지)
                    price_cols = ['upbit_open', 'upbit_high', 'upbit_low', 'upbit_close', 'mid_price', 'bid', 'ask']
                    for c in price_cols:
                        if c in df.columns:
                            df[c] = df[c].ffill()
                            
                    # 미체결 구간의 Open/High/Low는 종가와 일치시켜 가짜 캔들 왜곡 방지
                    no_trade_mask = df['upbit_volume'].isna() | (df['upbit_volume'] == 0)
                    for c in ['upbit_open', 'upbit_high', 'upbit_low']:
                        if c in df.columns:
                            df.loc[no_trade_mask, c] = df.loc[no_trade_mask, 'upbit_close']
                            
                    # 거래량 및 체결건수 0 채움 (Zero-fill)
                    df['upbit_volume'] = df['upbit_volume'].fillna(0.0)
                    df['upbit_trade_price'] = df['upbit_trade_price'].fillna(0.0)
                    df['target_trade_count'] = df['target_trade_count'].fillna(0.0)
                    df['target_large_trade_count'] = df['target_large_trade_count'].fillna(0.0)
                    df['bid_vol'] = df['bid_vol'].fillna(0.0)
                    
                    if 'cvd' in df.columns: df['cvd'] = df['cvd'].ffill().fillna(0.0)
                    if 'spread' in df.columns: df['spread'] = df['spread'].fillna(0.0)
                    
                    df['target_trade_bid_ratio'] = (df['bid_vol'] / (df['upbit_volume'] + 1e-8)).clip(0.0, 1.0).fillna(0.5)
                    df['target_avg_trade_size'] = (df['upbit_trade_price'] / (df['target_trade_count'] + 1e-8)).fillna(0.0)
                    local_log(f"  ✅ [{interval}] 정규 그리드 완성: {len(unique_dates)}개 거래일, 총 {len(df):,}행 (왜곡 0%)")
                else:
                    df = df_resampled
        else:
            if is_us_asset:
                local_log(f"  🌐 [Auto-Fetch] 미국 주식/ETF ({mkt}) 시세 다운로드 재시도...")
                from .overseas_futures_collector import download_us_market_data
                df = download_us_market_data(mkt, interval=interval, baker_root=baker_root, local_log=local_log)
                if df.empty and os.path.exists(snapshot_csv):
                    df = pd.read_csv(snapshot_csv)
            elif os.path.exists(snapshot_csv):
                local_log(f"  ℹ️ 기존 스냅샷 재활용: {snapshot_csv}")
                df = pd.read_csv(snapshot_csv)
            else:
                local_log("  ⚠️ 수집된 과거 원본이 없어 표준 가상 스냅샷을 생성합니다.")
                start_dt = datetime.strptime(f"{start_ym}-01", "%Y-%m-%d")
                end_dt = datetime.strptime(f"{end_ym}-01", "%Y-%m-%d")
                dates = pd.bdate_range(start=start_dt, end=end_dt + pd.offsets.MonthEnd(0))
                rows = []
                for d in dates:
                    cur_time = datetime(d.year, d.month, d.day, 9, 0, 0)
                    end_time = datetime(d.year, d.month, d.day, 15, 30, 0)
                    while cur_time <= end_time:
                        rows.append({
                            'timestamp': cur_time,
                            'upbit_open': 38700.0,
                            'upbit_high': 38750.0,
                            'upbit_low': 38680.0,
                            'upbit_close': 38720.0,
                            'upbit_volume': 150000.0,
                            'upbit_trade_price': 5800000000.0,
                            'nav': 38715.0,
                            'kospi_futures_close': 387.50,
                            'spot_close': 387.20,
                            'cvd': 5000.0
                        })
                        cur_time += pd.Timedelta(minutes=30)
                df = pd.DataFrame(rows)
                
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df = df.dropna(subset=['timestamp']).reset_index(drop=True)
        
        req_start_date = config.get('dataset_start_date') or config.get('start_date')
        if req_start_date:
            req_start_ts = pd.to_datetime(f"{req_start_date} 00:00:00")
            if (df['timestamp'] < req_start_ts).any():
                local_log(f"  ✂️ 지정된 데이터셋 시작일({req_start_date})에 맞춰 스냅샷 범위를 클램핑합니다.")
                df = df[df['timestamp'] >= req_start_ts].reset_index(drop=True)

        req_end_date = config.get('dataset_end_date') or config.get('end_date')
        if req_end_date:
            req_end_ts = pd.to_datetime(f"{req_end_date} 23:59:59")
            if (df['timestamp'] > req_end_ts).any():
                local_log(f"  ✂️ 지정된 데이터셋 종료일({req_end_date})에 맞춰 스냅샷 범위를 클램핑합니다.")
                df = df[df['timestamp'] <= req_end_ts].reset_index(drop=True)
        
        # ── [멀티 에셋 자동 병합] KOSPI 200 선물 및 지수 캔들 병합 ──
        if progress_callback: progress_callback(40, "2단계: 선물 및 지수 캔들 결합 중...")
        # [D-10] stale 상한으로 무효화한 컬럼 추적 — 하류 ffill 보간이 되살리지 못하게 한다
        _stale_capped_cols: list = []
        # 1. 선물 캔들 파일 검색 및 병합
        futures_files = []
        all_candle_dirs = list(dict.fromkeys(candle_dirs + [
            os.path.join(baker_root, "data", "historical", "kis", "candle_1m")
        ]))
        for d in all_candle_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    if any(k in f for k in ['futures', '101V', '10100', '101_']) and f.endswith(".csv") and not f.endswith(".bak"):
                        futures_files.append(os.path.join(d, f))
        if futures_files:
            try:
                local_log(f"  📥 선물 캔들 소스 {len(futures_files)}개 병합: {[os.path.basename(f) for f in futures_files]}")
                f_dfs = [pd.read_csv(f) for f in futures_files]
                f_df = pd.concat(f_dfs).drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
                f_df['timestamp'] = pd.to_datetime(f_df['timestamp'], errors='coerce')
                f_df = f_df.dropna(subset=['timestamp']).reset_index(drop=True)
                f_close_col = 'upbit_close' if 'upbit_close' in f_df.columns else ('close' if 'close' in f_df.columns else 'target_close')
                f_df_sub = f_df[['timestamp', f_close_col]].rename(columns={f_close_col: 'futures_close'})
                # ── [T-3] stale 상한 정책 (ffill 무한 전파 차단) ──
                df = merge_asof_with_stale_cap(
                    df, f_df_sub, ['futures_close'],
                    cap=resolve_stale_cap('KRX', interval),
                )
                _stale_capped_cols.append('futures_close')
            except Exception as e:
                local_log(f"  ⚠️ 선물 캔들 병합 중 오류: {e}")

        # 2. 현물 지수 캔들 파일 검색 및 병합
        spot_files = []
        for d in all_candle_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    if any(k in f for k in ['spot_', 'U180', 'U201', 'kospi200']) and f.endswith(".csv") and not f.endswith(".bak"):
                        spot_files.append(os.path.join(d, f))
        if spot_files:
            try:
                local_log(f"  📥 지수 캔들 소스 {len(spot_files)}개 병합: {[os.path.basename(f) for f in spot_files]}")
                s_dfs = [pd.read_csv(f) for f in spot_files]
                s_df = pd.concat(s_dfs).drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
                s_df['timestamp'] = pd.to_datetime(s_df['timestamp'], errors='coerce')
                s_df = s_df.dropna(subset=['timestamp']).reset_index(drop=True)
                s_close_col = 'upbit_close' if 'upbit_close' in s_df.columns else ('close' if 'close' in s_df.columns else 'target_close')
                s_df_sub = s_df[['timestamp', s_close_col]].rename(columns={s_close_col: 'spot_close'})
                # ── [T-3] stale 상한 정책 ──
                df = merge_asof_with_stale_cap(
                    df, s_df_sub, ['spot_close'],
                    cap=resolve_stale_cap('KRX', interval),
                )
                _stale_capped_cols.append('spot_close')
            except Exception as e:
                local_log(f"  ⚠️ 지수 캔들 병합 중 오류: {e}")

        # 3. CME 해외 선물 캔들 파일 검색 및 다층 계층형 병합 (대상 심볼은 레지스트리 feature_prefix 보유 여부로 결정)
        if progress_callback: progress_callback(70, "3단계: 해외 선물 결합 중...")
        overseas_dir = os.path.join(baker_root, "data", "historical", "overseas_futures")
        if os.path.exists(overseas_dir):
            from shared.config.config_manager import load_symbols_registry
            _reg = load_symbols_registry()
            _overseas_futures_cols = [
                (sym, info["feature_prefix"])
                for sym, info in _reg.items()
                if info.get("market_type") == "GLOBAL_FUTURES" and info.get("feature_prefix")
            ]
            if not _overseas_futures_cols:
                local_log("  ⚠️ [Futures] registry에 feature_prefix 있는 GLOBAL_FUTURES 없음 — CME 선물 병합 건너뜀")
            for sym, col_prefix in _overseas_futures_cols:
                # 3-1. 일봉 베이스라인 병합 (Daily T-1 전일 확정치: 오늘 장중에는 어제 종가만 참조)
                f_daily = os.path.join(overseas_dir, f"{sym}_1d.csv")
                has_daily = False
                if os.path.exists(f_daily):
                    try:
                        d_df = pd.read_csv(f_daily)
                        # 일봉 캔들은 T일 마감 후 확정되므로 한국 T일 장중에는 T-1 확정치를 사용하기 위해 +1일 시프트
                        d_df['date_dt'] = pd.to_datetime(d_df['timestamp']).dt.floor('D') + pd.Timedelta(days=1)
                        d_sub = d_df[['date_dt', 'close']].rename(columns={'close': f'{col_prefix}_daily_close'}).drop_duplicates(subset=['date_dt'])
                        df['date_dt'] = pd.to_datetime(df['timestamp']).dt.floor('D')
                        df = pd.merge(df, d_sub, on='date_dt', how='left')
                        df.drop(columns=['date_dt'], inplace=True)
                        df[f'{col_prefix}_daily_close'] = df[f'{col_prefix}_daily_close'].ffill()
                        has_daily = True
                        local_log(f"  📥 [Futures] {sym} 일봉 시세(T-1 전일 확정치) 병합 완료 ({col_prefix}_daily_close)")
                    except Exception as e:
                        local_log(f"  ⚠️ [Futures] {sym} 일봉 시세 병합 오류: {e}")

                # 3-2. 단기/중기 분봉/시봉 다층 합성 병합 (1m -> 5m -> 60m)
                # 📌 [Lookahead Bias 원천 차단]:
                # Yahoo Finance의 캔들 타임스탬프는 캔들 '시작 시각(Open Time)'임.
                # 봉의 종가(close)는 봉이 끝나는 시각(Open + Interval)에만 확정되므로,
                # availability_timestamp = timestamp + interval_duration 으로 시프트 후 merge_asof 결합.
                f_candidate_specs = [
                    (os.path.join(overseas_dir, f"{sym}_{interval}.csv"), interval),
                    (os.path.join(overseas_dir, f"{sym}_1m.csv"), "1m"),
                    (os.path.join(overseas_dir, f"{sym}_5m.csv"), "5m"),
                    (os.path.join(overseas_dir, f"{sym}_60m.csv"), "60m"),
                    (os.path.join(overseas_dir, f"{sym}_1h.csv"), "60m")
                ]

                def parse_duration(intv_str):
                    s = str(intv_str).strip().lower()
                    if s.endswith('m') and not s.endswith('month'):
                        mins = int(''.join(filter(str.isdigit, s)) or 1)
                        return pd.Timedelta(minutes=mins)
                    elif s.endswith('h'):
                        hrs = int(''.join(filter(str.isdigit, s)) or 1)
                        return pd.Timedelta(hours=hrs)
                    elif s.endswith('s'):
                        secs = int(''.join(filter(str.isdigit, s)) or 10)
                        return pd.Timedelta(seconds=secs)
                    elif s.endswith('d'):
                        return pd.Timedelta(days=1)
                    return pd.Timedelta(minutes=1)

                merged_futures_df = pd.DataFrame()
                for f_path, f_int in f_candidate_specs:
                    if os.path.exists(f_path):
                        try:
                            temp_o = pd.read_csv(f_path)
                            temp_o['timestamp'] = pd.to_datetime(temp_o['timestamp'])
                            # 봉 시작 시간 -> 봉 완성(확정) 시간으로 시프트 (Lookahead 방지)
                            dur = parse_duration(f_int)
                            temp_o['timestamp'] = temp_o['timestamp'] + dur
                            
                            sub_block = temp_o[['timestamp', 'close', 'volume']]
                            if merged_futures_df.empty:
                                merged_futures_df = sub_block
                            else:
                                merged_futures_df = pd.concat([merged_futures_df, sub_block])
                                merged_futures_df = merged_futures_df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
                        except Exception as ex_sub:
                            local_log(f"  ⚠️ [Futures] {f_path} 로드 예외: {ex_sub}")

                if not merged_futures_df.empty:
                    try:
                        merged_futures_df = merged_futures_df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
                        rename_dict = {'close': f'{col_prefix}_close', 'volume': f'{col_prefix}_volume'}
                        o_sub = merged_futures_df[['timestamp', 'close', 'volume']].rename(columns=rename_dict)
                        
                        df['timestamp'] = pd.to_datetime(df['timestamp'])
                        # ── [T-3] stale 상한 정책 (해외선물: GLOBAL_FUTURES) ──
                        df = merge_asof_with_stale_cap(
                            df, o_sub, [f'{col_prefix}_close', f'{col_prefix}_volume'],
                            cap=resolve_stale_cap('GLOBAL_FUTURES', interval),
                            fill_values={f'{col_prefix}_volume': 0.0},
                        )
                        _stale_capped_cols.extend([f'{col_prefix}_close', f'{col_prefix}_volume'])

                        # 분봉이 없는 아주 오래된 과거는 일봉(T-1 daily_close)으로 fallback 채움
                        if f'{col_prefix}_daily_close' in df.columns:
                            df[f'{col_prefix}_close'] = df[f'{col_prefix}_close'].combine_first(df[f'{col_prefix}_daily_close'])
                        local_log(f"  📥 [Futures] {sym} 선물 시세 무결 결합 완료 (Lookahead 차단, {col_prefix}_close, {col_prefix}_volume)")
                    except Exception as e:
                        local_log(f"  ⚠️ [Futures] {sym} 선물 시세 병합 오류: {e}")
                elif has_daily:
                    # 분봉 파일이 전혀 없을 경우 일봉으로 대체
                    df[f'{col_prefix}_close'] = df[f'{col_prefix}_daily_close'].ffill()
                    df[f'{col_prefix}_volume'] = 0.0
                    local_log(f"  📥 [Futures] {sym} 분봉 부재로 일봉({col_prefix}_daily_close)으로 {col_prefix}_close 대체 생성 완료")

        # ── [멀티 에셋 동적 참조 대장주 자동 병합] reference_symbols 처리 ──
        df = merge_reference_symbols_into_df(df, config, interval, baker_root, local_log,
                                             capped_cols_out=_stale_capped_cols)

        # 3. 틱 데이터가 있고 캔들 파일과 병합해야 하는 경우 (오더플로우 지표용 집계 병합)
        if tick_available and candle_files:
            if progress_callback: progress_callback(85, "4단계: 오더플로우 세부 통계 집계 중...")
            try:
                t_raw_list = [tick_store.read(tick_sym)]
                t_raw = pd.concat(t_raw_list, ignore_index=True)
                t_dedup = [c for c in ['timestamp', 'upbit_close', 'upbit_volume', 'cvd', 'upbit_trade_price'] if c in t_raw.columns]
                if t_dedup:
                    t_raw = t_raw.drop_duplicates(subset=t_dedup).sort_values('timestamp').reset_index(drop=True)
                else:
                    t_raw = t_raw.drop_duplicates().sort_values('timestamp').reset_index(drop=True)
                t_raw['timestamp'] = pd.to_datetime(t_raw['timestamp'], errors='coerce')
                t_raw = t_raw.dropna(subset=['timestamp']).reset_index(drop=True)
                
                # 틱 집계: 체결건수, 대량체결(1억원 이상), 매수체결비율, 평균체결규모
                resample_rule = get_pandas_freq(interval)
                t_raw = t_raw.set_index('timestamp')
                
                t_close_col = 'upbit_close' if 'upbit_close' in t_raw.columns else 'close'
                t_vol_col = 'upbit_volume' if 'upbit_volume' in t_raw.columns else 'volume'
                t_tp_col = 'upbit_trade_price' if 'upbit_trade_price' in t_raw.columns else 'trade_price'
                
                # 틱당 거래대금 추정 (단위: 원)
                if t_tp_col in t_raw.columns:
                    trade_amt = t_raw[t_tp_col]
                else:
                    trade_amt = t_raw[t_close_col] * t_raw[t_vol_col]
                    
                t_raw['is_large'] = (trade_amt >= 100000000.0).astype(float)
                
                # CVD 기반 매수비율 추정 (cvd 증가분 / volume)
                if 'cvd' in t_raw.columns and t_vol_col in t_raw.columns:
                    delta_vol = t_raw['cvd'].diff().fillna(0.0)
                    t_raw['bid_vol'] = ((t_raw[t_vol_col] + delta_vol) / 2.0).clip(lower=0)
                else:
                    t_raw['bid_vol'] = t_raw[t_vol_col] * 0.5

                agg_tick = t_raw.resample(resample_rule).agg({
                    t_close_col: 'count',
                    'is_large': 'sum',
                    'bid_vol': 'sum',
                    t_vol_col: 'sum',
                    t_tp_col: 'sum' if t_tp_col in t_raw.columns else 'first'
                }).rename(columns={
                    t_close_col: 'target_trade_count',
                    'is_large': 'target_large_trade_count'
                }).reset_index()

                agg_tick['target_trade_bid_ratio'] = (agg_tick['bid_vol'] / (agg_tick[t_vol_col] + 1e-8)).clip(0.0, 1.0).fillna(0.5)
                agg_tick['target_avg_trade_size'] = (agg_tick[t_tp_col] / (agg_tick['target_trade_count'] + 1e-8)).fillna(0.0)
                
                tick_merge_cols = ['timestamp', 'target_trade_count', 'target_large_trade_count', 'target_trade_bid_ratio', 'target_avg_trade_size']
                df = pd.merge(df, agg_tick[tick_merge_cols], on='timestamp', how='left')
                df['target_trade_count'] = df['target_trade_count'].fillna(0.0)
                df['target_large_trade_count'] = df['target_large_trade_count'].fillna(0.0)
                df['target_trade_bid_ratio'] = df['target_trade_bid_ratio'].fillna(0.5)
                df['target_avg_trade_size'] = df['target_avg_trade_size'].fillna(0.0)
                local_log("  ✅ 틱 오더플로우 세부 집계 병합 완료")
            except Exception as e:
                local_log(f"  ⚠️ 틱 오더플로우 집계 병합 오류: {e}")

        # ── 기본 컬럼 폴백 및 무결성 보장 ──
        if 'nav' not in df.columns:
            df['nav'] = df.get('target_close', df.get('upbit_close', 0.0))
        if 'futures_close' not in df.columns and 'kospi_futures_close' not in df.columns:
            df['futures_close'] = df.get('target_close', df.get('upbit_close', 0.0))
        if 'spot_close' not in df.columns:
            df['spot_close'] = df.get('target_close', df.get('upbit_close', 0.0))
        if 'cvd' not in df.columns:
            df['cvd'] = 0.0
            
        # target_* 및 upbit_* 양방향 동기화
        for t_col, l_col in [
            ('target_open', 'upbit_open'), ('target_high', 'upbit_high'),
            ('target_low', 'upbit_low'), ('target_close', 'upbit_close'),
            ('target_volume', 'upbit_volume'), ('target_trade_price', 'upbit_trade_price'),
            ('target_trade_bid_ratio', 'upbit_trade_bid_ratio'), ('target_trade_count', 'upbit_trade_count'),
            ('target_large_trade_count', 'upbit_large_trade_count'), ('target_avg_trade_size', 'upbit_avg_trade_size')
        ]:
            if t_col in df.columns and l_col not in df.columns:
                df[l_col] = df[t_col]
            elif l_col in df.columns and t_col not in df.columns:
                df[t_col] = df[l_col]
            
        # [GOVERNANCE] 원천 데이터 거버넌스 카탈로그 기반 동적 결측치 처리 (탈하드코딩)
        from shared.data_library.data_catalog_manager import DataCatalogManager
        # [D-10] stale 상한으로 무효화한 참조/매크로 컬럼은 보간에서 제외한다 (되채움 금지)
        df, imputation_report = DataCatalogManager.apply_fallback_to_dataframe(
            df, exclude_cols=_stale_capped_cols
        )
        if _stale_capped_cols:
            local_log(
                f"  🛡️ [D-10] stale 무효화 컬럼 {len(_stale_capped_cols)}개는 보간 제외 "
                f"(NaN 유지): {sorted(set(_stale_capped_cols))[:6]}"
            )

        # [무결성 게이트] 불량/결손 데이터셋 파일 생성 원천 차단
        validate_snapshot_integrity(df, mkt, local_log)

        _atomic_write_csv(df, snapshot_csv, index=False)
        local_log(f"  💾 [Data Builder] 스냅샷 저장 완료: {snapshot_csv} (총 {len(df):,}행)")

        # [GOLD] 메달리온 아키텍처 Gold 볼트 미러링 저장 및 매니페스트 발행
        gold_target = dpaths.gold_snapshot_path(mkt, interval)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(gold_target)), exist_ok=True)
            if os.path.abspath(snapshot_csv) != os.path.abspath(gold_target):
                shutil.copy2(snapshot_csv, gold_target)
                local_log(f"  🏆 [Medallion Gold] Gold 볼트 동기화 완료: {gold_target}")
        except Exception as _sync_err:
            local_log(f"  ⚠️ [Medallion Gold] Gold 볼트 복사 예외: {_sync_err}")

        # [META] 빌드 메타파일 자동 기록
        meta_path = snapshot_csv.replace('.csv', '.meta.json')
        try:
            import json as _json
            from datetime import datetime as _dt
            meta = {
                "built_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": mkt,
                "interval": interval,
                "start_ym": start_ym,
                "end_ym": end_ym,
                "rows": len(df),
                "timestamp_min": str(df['timestamp'].min()),
                "timestamp_max": str(df['timestamp'].max()),
                "columns": list(df.columns),
                "data_quality": imputation_report
            }
            with open(meta_path, 'w', encoding='utf-8') as _mf:
                _json.dump(meta, _mf, ensure_ascii=False, indent=2)
            local_log(f"  📋 [META] 빌드 품질 메타파일 저장 완료: {meta_path}")

            if os.path.exists(meta_path) and os.path.abspath(snapshot_csv) != os.path.abspath(gold_target):
                gold_meta = gold_target.replace('.csv', '.meta.json')
                shutil.copy2(meta_path, gold_meta)
        except Exception as _me:
            pass

        # [MANIFEST] Gold 스냅샷 사이드카 매니페스트 발행
        try:
            for target_f in [gold_target, snapshot_csv]:
                if os.path.exists(target_f):
                    ManifestManager.create_manifest(
                        data_path=target_f,
                        provider="derived",
                        data_tier="gold",
                        asset_type="snapshot",
                        symbol=mkt,
                        notes="Medallion Gold Feature Snapshot"
                    )
            local_log(f"  📜 [Manifest] Gold 스냅샷 사이드카 매니페스트 발행 완료")
        except Exception as _man_err:
            local_log(f"  ⚠️ [Manifest] Gold 매니페스트 발행 예외: {_man_err}")

        if progress_callback: progress_callback(100, "통합 데이터셋 빌드 완료")
        if status_update_fn:
            try:
                status_update_fn(len(df), os.path.getsize(snapshot_csv))
            except TypeError:
                status_update_fn()
    except Exception as e:
        local_log(f"❌ [Data Builder] 스냅샷 빌드 오류: {e}")
    finally:
        if callback_on_finish:
            callback_on_finish()


def run_stock_download_pipeline(config, mkt, start_ym, end_ym, interval, local_log, callback_on_finish=None, progress_callback=None):
    """국내 주식/ETF 통합 다운로드 파이프라인 (미국 종목은 전용 수집기, 국내 종목은 크레온/KIS 연동)"""
    try:
        local_log(f"\n🚀 [Stock/ETF] 과거 데이터 다운로드 프로세스 가동: {mkt} ({start_ym} ~ {end_ym}) [단위: {interval}]")
        baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        from shared.config.config_manager import get_symbol_info
        sym_info = get_symbol_info(mkt)
        mtype = sym_info.get("market_type", "")
        is_us_asset = (mtype in ["US_STOCK", "US_ETF"]) or (not str(mkt).isdigit() and not str(mkt).startswith("KRW-") and len(str(mkt)) <= 6)
        if is_us_asset:
            local_log(f"🇺🇸 [US Market] 미국 시장 종목 감지 ➔ 전용 다운로더 가동: {mkt} ({interval})")
            from .overseas_futures_collector import download_us_market_data
            df_res = download_us_market_data(mkt, interval=interval, baker_root=baker_root, local_log=local_log)
            if not df_res.empty:
                local_log(f"🎉 [US Market] {mkt} 데이터 다운로드 성공 ({len(df_res):,}행)")
            if progress_callback:
                progress_callback(100)
            return

        # 1. 네이티브 Windows 환경에서의 직접 COM 호출 시도
        is_windows_native = sys.platform.startswith("win")
        is_wsl = not is_windows_native and (os.path.exists("/mnt/c/Windows/System32/cmd.exe") or shutil.which("cmd.exe") is not None)
        
        executed = False
        clean_sym = f"A{mkt}" if len(str(mkt)) == 6 and str(mkt).isdigit() else str(mkt)
        int_min = 1
        if str(interval).endswith("m"):
            try:
                int_min = int(str(interval).replace("m", ""))
            except ValueError:
                int_min = 1

        # 0. 타겟에 부착된 국내 참조 대장주(Symbol Relations) 자동 조회 (동적 SSOT 연동)
        ref_symbols_list = []
        try:
            from shared.data_library.symbol_relations_manager import SymbolRelationsManager
            relations = SymbolRelationsManager.get_relations_for_target(mkt)
            clean_target = str(mkt).strip().upper().replace("KRW-", "")
            for r in relations:
                r_s = str(r.get("symbol", "")).strip().upper()
                c_s = r_s[1:] if r_s.startswith("A") and len(r_s) == 7 and r_s[1:].isalnum() else r_s
                if c_s.isalnum() and len(c_s) == 6 and c_s != clean_target and not c_s.startswith("KRW-") and not c_s.startswith("US"):
                    if c_s not in ref_symbols_list:
                        ref_symbols_list.append(c_s)
            if ref_symbols_list:
                local_log(f"   🔗 [SymbolRelations] 연결된 국내 참조 대장주 감지 ({len(ref_symbols_list)}개): {', '.join(ref_symbols_list)}")
        except Exception as e:
            local_log(f"   ⚠️ [SymbolRelations] 대장주 목록 조회 예외: {e}")

        if is_windows_native:
            try:
                from core.creon_collector import check_creon_connection, download_all_krx, download_tick_data, download_minute_data
                is_creon_ok, creon_msg = check_creon_connection()
                if is_creon_ok:
                    local_log("   ✅ [Creon] Windows 네이티브 환경 감지 ➔ KODEX200/선물/지수/틱 및 대장주 일괄 다운로더 가동 (증분 모드)")
                    download_all_krx(
                        target_symbol=clean_sym,
                        futures_symbol="101V000",
                        index_symbol="U180",
                        interval=int_min,
                        count=99999999,
                        force_full=False,
                        ref_symbols=ref_symbols_list,
                        local_log=local_log
                    )
                    executed = True
            except Exception as e:
                local_log(f"   ⚠️ [Creon] 네이티브 호출 실패: {e}")

        # 2. WSL 환경일 경우: Windows powershell.exe 브릿지를 통해 32-bit Python 호출
        if not executed and is_wsl:
            local_log("   🌐 [WSL Bridge] WSL 환경 감지 ➔ Windows PowerShell 브릿지를 통해 대신증권 증분 수집기(all_krx) 가동...")
            ref_arg = f' --ref_symbols {",".join(ref_symbols_list)}' if ref_symbols_list else ''
            cmd_str = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "py -3.10-32 core\\creon_collector.py --type all_krx --symbol {clean_sym} --futures_symbol 10100 --index_symbol U180 --interval {int_min} --count 99999999{ref_arg}"'
            local_log(f"   📡 명령 실행: {cmd_str}")
            
            try:
                proc = subprocess.Popen(
                    cmd_str,
                    shell=True,
                    cwd=baker_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1
                )
                
                for raw_line in iter(proc.stdout.readline, b''):
                    if raw_line:
                        try:
                            line_str = raw_line.decode('utf-8')
                        except UnicodeDecodeError:
                            try:
                                line_str = raw_line.decode('cp949', errors='replace')
                            except Exception:
                                line_str = raw_line.decode('latin1', errors='ignore')
                        cleaned = line_str.strip()
                        if cleaned:
                            local_log(f"   [Creon-Win] {cleaned}")
                            
                proc.stdout.close()
                proc.wait()
                
                if proc.returncode == 0:
                    local_log(f"🎉 [WSL Bridge] 대신증권 전체 데이터(KODEX/선물/지수/틱) 수집 완료: {mkt}")
                    executed = True
                    
                else:
                    local_log(f"   ⚠️ [WSL Bridge] 프로세스 종료 코드: {proc.returncode}")
            except Exception as e:
                local_log(f"   ⚠️ [WSL Bridge] 실행 예외: {e}")

        # 3. 크레온 미지원/실패 시 KIS OpenAPI로 폴백
        if not executed:
            local_log(f"   ℹ️ 대신증권 미사용 환경 ➔ KIS OpenAPI 다운로드 시도")
            run_kis_download_pipeline(config, mkt, start_ym, end_ym, interval, local_log, callback_on_finish=None, progress_callback=progress_callback)
            
    except Exception as e:
        local_log(f"❌ [Stock/ETF] 다운로드 파이프라인 예외: {e}")
    finally:
        if callback_on_finish:
            callback_on_finish()


def run_archive_download_pipeline(config, target_exchange, mkt, start_ym, end_ym, interval, local_log, callback_on_finish=None, progress_callback=None):
    # 해외 선물(CME NQ, ES) 전용 다운로드 요청 분기
    if target_exchange.lower() in ["futures", "cme", "overseas_futures"]:
        try:
            local_log(f"\n🚀 [CME Futures] 미국 나스닥 100(NQ) 및 S&P 500(ES) 선물 데이터 수집 시작...")
            from .overseas_futures_collector import download_all_major_futures
            baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            download_all_major_futures(baker_root=baker_root, local_log=local_log)
            if progress_callback:
                progress_callback(100)
            local_log(f"🎉 [CME Futures] 해외 선물 다운로드 완료!")
        except Exception as e:
            local_log(f"❌ [CME Futures] 다운로드 오류: {e}")
        finally:
            if callback_on_finish:
                callback_on_finish()
        return

    # 국내 주식/ETF 종목 판별 시 주식/ETF 파이프라인으로 즉시 분기 (종목 레지스트리 메타데이터 연동)
    from shared.config.config_manager import get_symbol_info
    sym_info = get_symbol_info(mkt)
    mtype = sym_info.get("market_type", "")
    exchange = sym_info.get("exchange", "")
    is_stock_market = (exchange in ("KIS", "CREON")) or (sym_info.get("asset_type") in ("STOCK", "ETF")) or ("ETF" in mtype) or ("STOCK" in mtype) or (not str(mkt).startswith("KRW-") and str(mkt).isdigit() and len(str(mkt)) == 6)
    if is_stock_market:
        return run_stock_download_pipeline(config, mkt, start_ym, end_ym, interval, local_log, callback_on_finish, progress_callback)

    try:
        local_log(f"\n🚀 {target_exchange.upper()} 다운로드 프로세스 가동: {mkt} ({start_ym} ~ {end_ym}) [단위: {interval}]")

        
        # 시작년월 ~ 종료년월에 매칭되는 모든 일자(Daily) 리스트 생성 (다년도 완벽 대응)
        start_dt = datetime.strptime(f"{start_ym}-01", "%Y-%m-%d")
        end_dt = datetime.strptime(f"{end_ym}-01", "%Y-%m-%d")
        
        raw_dates = pd.date_range(start=start_dt, end=end_dt + pd.offsets.MonthEnd(0), freq='D')
        curr_now = datetime.now()
        
        date_range = []
        for dt in raw_dates:
            if dt.date() >= curr_now.date():
                continue
            date_range.append(dt)
            
        download_count = 0
        skip_count = 0
        
        total_days = len(date_range)
        
        if target_exchange == "upbit":
            os.makedirs(f"{_DATA_ROOT}/historical/upbit/candle_{interval}", exist_ok=True)
            os.makedirs(f"{_DATA_ROOT}/historical/upbit/candle_{interval}_extracted", exist_ok=True)
            os.makedirs(f"{_DATA_ROOT}/historical/upbit/trade_{interval}", exist_ok=True)
            os.makedirs(f"{_DATA_ROOT}/historical/upbit/trade_{interval}_extracted", exist_ok=True)
            
            # 원천 수집 해상도(ingestion_resolution) 기반 최적화
            from shared.data_library.source_mapping_manager import SourceMappingManager
            target_map_info = SourceMappingManager.get_mapping(mkt) or {}
            ingest_res = str(target_map_info.get("ingestion_resolution", "1M")).upper()
            is_tick_mode = (ingest_res == "TICK") or interval.endswith("s")

            total_steps = total_days * 3 if is_tick_mode else total_days * 2  # 틱 모드일 때만 3단계, 1M 모드는 2단계
            step_idx = 0
            
            upbit_interval = _upbit_supported_interval(interval)
            need_fallback = (upbit_interval != interval)
            
            local_log(f"📡 [업비트 1단계] {upbit_interval} 캔들 데이터 수집 시작 (총 {total_days}일, 모드: {ingest_res})...")
            if need_fallback:
                local_log(f"   ℹ️ 업비트는 {interval} 초봉 아카이브를 직접 제공하지 않으므로 {upbit_interval} 캔들을 다운로드하여 틱 체결 데이터와 함께 정밀 합성합니다.")
            for dt in date_range:
                date_str = dt.strftime("%Y%m%d")
                file_yr = str(dt.year)
                file_name = f"{mkt}_candle-{upbit_interval}_{date_str}.zip"
                csv_name = f"{mkt}_candle-{upbit_interval}_{date_str}.csv"
                url = f"https://crix-data.upbit.com/candle/{mkt}/daily/{upbit_interval}/{file_yr}/{file_name}"
                
                zip_path = os.path.join(f"{_DATA_ROOT}/historical/upbit/candle_{interval}", file_name)
                csv_path = os.path.join(f"{_DATA_ROOT}/historical/upbit/candle_{interval}_extracted", csv_name)
                
                step_idx += 1
                if progress_callback:
                    progress_callback(int(step_idx / total_steps * 100))
                    
                if os.path.exists(csv_path):
                    skip_count += 1
                    continue
                    
                local_log(f"   [{step_idx}/{total_steps}] 호출 중: {file_name}...")
                try:
                    binary_content = fetch_upbit_with_retry(url)
                    if binary_content:
                        with open(zip_path, "wb") as f:
                            f.write(binary_content)
                        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                            zip_ref.extract(csv_name, os.path.dirname(csv_path))
                        if os.path.exists(zip_path):
                            os.remove(zip_path)
                        local_log(f"      ✅ 완료: {csv_name} 추출 성공")
                        download_count += 1
                    else:
                        local_log(f"      ⚠️ 데이터 없음 또는 통신 실패: {url}")
                except Exception as e:
                    local_log(f"      ❌ 에러: {e}")
                time.sleep(0.5)
                
            if is_tick_mode:
                local_log("\n📡 [업비트 2단계] 틱 체결 데이터 수집 시작 (TICK 모드)...")
                for dt in date_range:
                    date_str = dt.strftime("%Y%m%d")
                    file_yr = str(dt.year)
                    file_name = f"{mkt}_trade_{date_str}.zip"
                    csv_name = f"{mkt}_trade_{date_str}.csv"
                    url = f"https://crix-data.upbit.com/trade/{mkt}/daily/{file_yr}/{file_name}"
                    
                    zip_path = os.path.join(f"{_DATA_ROOT}/historical/upbit/trade_{interval}", file_name)
                    csv_path = os.path.join(f"{_DATA_ROOT}/historical/upbit/trade_{interval}_extracted", csv_name)
                    
                    step_idx += 1
                    if progress_callback:
                        progress_callback(int(step_idx / total_steps * 100))
                        
                    if os.path.exists(csv_path):
                        skip_count += 1
                        continue
                        
                    local_log(f"   [{step_idx}/{total_steps}] 호출 중: {file_name}...")
                    try:
                        binary_content = fetch_upbit_with_retry(url)
                        if binary_content:
                            with open(zip_path, "wb") as f:
                                f.write(binary_content)
                            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                                zip_ref.extract(csv_name, os.path.dirname(csv_path))
                            if os.path.exists(zip_path):
                                os.remove(zip_path)
                            local_log(f"      ✅ 완료: {csv_name} 추출 성공")
                            download_count += 1
                        else:
                            local_log(f"      ⚠️ 데이터 없음 또는 통신 실패: {url}")
                    except Exception as e:
                        local_log(f"      ❌ 에러: {e}")
                    time.sleep(0.5)
            else:
                local_log(f"\n📡 [업비트 2단계] 틱 체결 수집 스킵 (ingestion_resolution='{ingest_res}': 불필요한 대용량 틱 다운로드를 생략하고 캔들 및 USDT 환율만 고속 수집)")
                
            local_log("\n📡 [업비트 3단계] KRW-USDT 환율 캔들 수집 시작 (정밀 김프 계산용)...")
            from shared.data_library.data_catalog_manager import DataCatalogManager
            usdt_summary = DataCatalogManager.get_skip_summary_for_range("UPBIT_USDT", date_range)
            if usdt_summary["skip_count"] > 0:
                local_log(f"   ℹ️ [원천데이터 거버넌스] {usdt_summary['start_date']} 이전 총 {usdt_summary['skip_count']}일은 상장 전 미제공 구간으로 자동 생략합니다. (소요시간 단축)")

            skipped_by_catalog = 0
            for dt in date_range:
                date_str_usdt = dt.strftime("%Y-%m-%d")
                csv_name_usdt = f"KRW-USDT_candle-{upbit_interval}_{dt.strftime('%Y%m%d')}.csv"
                csv_path_usdt = os.path.join(f"{_DATA_ROOT}/historical/upbit/candle_{interval}_extracted", csv_name_usdt)
                
                step_idx += 1
                if progress_callback:
                    progress_callback(int(step_idx / total_steps * 100))

                if os.path.exists(csv_path_usdt):
                    skip_count += 1
                    continue

                # 원천 데이터 카탈로그 기반 상장 전 자동 생략 (HTTP 요청 및 대기 배제)
                should_skip_day, _ = DataCatalogManager.should_skip("UPBIT_USDT", date_str_usdt)
                if should_skip_day:
                    skipped_by_catalog += 1
                    continue

                try:
                    res_df = download_upbit_usdt_day(config, date_str_usdt, interval=interval)
                    if res_df is not None and not res_df.empty:
                        local_log(f"      ✅ 완료: {csv_name_usdt} 추출 성공")
                        download_count += 1
                    else:
                        local_log(f"      ⚠️ KRW-USDT 데이터 없음 또는 통신 실패: {date_str_usdt}")
                except Exception as e:
                    local_log(f"      ❌ 에러: {e}")
                time.sleep(0.5)

            if progress_callback:
                progress_callback(100)

            local_log(f"\n🟢 업비트 데이터 동기화 완료! (신규 다운로드: {download_count}개, 기존 캐시 스킵: {skip_count}개, 상장 전 자동 생략: {skipped_by_catalog}개)")
            local_log(f"✅ 업비트 작업 완료 - 다운로드: {download_count}, 캐시: {skip_count}, 상장전생략: {skipped_by_catalog}")
            
        elif target_exchange == "binance":
            coin_code = mkt.split("-")[-1]
            symbol_binance = f"{coin_code}USDT"
            config['SYMBOL_BINANCE'] = symbol_binance
            binance_interval = _binance_supported_interval(interval)
            need_fallback = (binance_interval != interval)
            
            os.makedirs(f"{_DATA_ROOT}/historical/binance/{symbol_binance}", exist_ok=True)
            os.makedirs(f"{_DATA_ROOT}/historical/binance_extracted/{symbol_binance}", exist_ok=True)
            
            local_log(f"📡 [바이낸스] {symbol_binance} {binance_interval} Klines 수집 시작 (총 {total_days}일)")
            if need_fallback:
                local_log(f"   ⚠️ {interval} 미지원 → {binance_interval}로 대체 다운로드 (빌드 시 resample)")
            
            for idx, dt in enumerate(date_range):
                step_pct = int((idx + 1) / total_days * 100)
                if progress_callback:
                    progress_callback(step_pct)
                    
                date_str = dt.strftime("%Y-%m-%d")
                
                # 1. 현물 Klines 다운로드
                file_name = f"{symbol_binance}-{binance_interval}-{date_str}.zip"
                csv_name = f"{symbol_binance}-{binance_interval}-{date_str}.csv"
                url = f"https://data.binance.vision/data/spot/daily/klines/{symbol_binance}/{binance_interval}/{file_name}"
                
                zip_path = os.path.join(f"{_DATA_ROOT}/historical/binance/{symbol_binance}", file_name)
                csv_path = os.path.join(f"{_DATA_ROOT}/historical/binance_extracted/{symbol_binance}", csv_name)
                
                spot_downloaded = False
                if os.path.exists(csv_path):
                    skip_count += 1
                else:
                    local_log(f"   [{idx+1}/{total_days}] 바이낸스 현물 Klines 호출 중: {file_name}...")
                    try:
                        binary_content = fetch_binance_with_retry(url)
                        if binary_content:
                            with open(zip_path, "wb") as f:
                                f.write(binary_content)
                            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                                zip_ref.extract(csv_name, os.path.dirname(csv_path))
                            if os.path.exists(zip_path):
                                os.remove(zip_path)
                            local_log(f"      ✅ 완료: {csv_name} 추출 성공")
                            download_count += 1
                            spot_downloaded = True
                        else:
                            local_log(f"      ⚠️ 데이터 없음 또는 통신 실패: {url}")
                    except Exception as e:
                        local_log(f"      ❌ 에러: {e}")
                    time.sleep(0.5)
                
                # 2. 선물 미결제 약정(Open Interest) 다운로드
                csv_name_oi = f"{symbol_binance}-openInterestHist-{date_str}.csv"
                csv_path_oi = os.path.join(f"{_DATA_ROOT}/historical/binance_extracted/{symbol_binance}", csv_name_oi)
                
                if os.path.exists(csv_path_oi):
                    if not spot_downloaded:
                        pass
                else:
                    local_log(f"   [{idx+1}/{total_days}] 바이낸스 선물 미결제약정 API 수집 중... ({date_str})")
                    try:
                        res_df = download_binance_futures_oi_day(config, date_str, interval=interval)
                        if res_df is not None and not res_df.empty:
                            local_log(f"      ✅ 완료: {csv_name_oi} API 수집 및 저장 성공")
                            download_count += 1
                        else:
                            local_log(f"      ⚠️ 데이터 없음 또는 API 수집 실패")
                    except Exception as e:
                        local_log(f"      ❌ 에러: {e}")
                    time.sleep(0.5)

                # 3. BTCUSDC 현물 Klines 다운로드
                symbol_usdc = f"{coin_code}USDC"
                file_name_usdc = f"{symbol_usdc}-{binance_interval}-{date_str}.zip"
                csv_name_usdc = f"{symbol_usdc}-{binance_interval}-{date_str}.csv"
                csv_path_usdc = os.path.join(f"{_DATA_ROOT}/historical/binance_extracted/{symbol_usdc}", csv_name_usdc)
                
                if os.path.exists(csv_path_usdc):
                    if not spot_downloaded:
                        pass
                else:
                    local_log(f"   [{idx+1}/{total_days}] 바이낸스 USDC Klines 호출 중: {file_name_usdc}...")
                    try:
                        res_df = download_binance_usdc_day(config, date_str, interval=interval)
                        if res_df is not None and not res_df.empty:
                            local_log(f"      ✅ 완료: {csv_name_usdc} 다운로드 및 추출 성공")
                            download_count += 1
                        else:
                            local_log(f"      ⚠️ 데이터 없음 또는 USDC 다운로드 실패")
                    except Exception as e:
                        local_log(f"      ❌ 에러: {e}")
                    time.sleep(0.5)

                # 4. 가상자산 벤치마크 및 상대 모멘텀용 보조 Klines 다운로드
                target_sym_info = get_symbol_info(mkt)
                symbol_aux = _sk.spot_key(RELATIVE_MOMENTUM_ASSET_ID if get_asset_id(mkt) == BENCHMARK_ASSET_ID else BENCHMARK_ASSET_ID)
                csv_name_aux = f"{symbol_aux}-{binance_interval}-{date_str}.csv"
                csv_path_aux = os.path.join(f"{_DATA_ROOT}/historical/binance_extracted/{symbol_aux}", csv_name_aux)
                if not os.path.exists(csv_path_aux):
                    local_log(f"   [{idx+1}/{total_days}] 바이낸스 벤치마크({symbol_aux}) Klines 호출 중...")
                    try:
                        res_df = download_binance_klines_symbol_day(symbol_aux, date_str, interval=interval)
                        if res_df is not None and not res_df.empty:
                            local_log(f"      ✅ 완료: {csv_name_aux} 다운로드 성공")
                            download_count += 1
                    except Exception as e:
                        local_log(f"      ❌ 에러: {e}")
                    time.sleep(0.5)

                # 5. 코인베이스 타겟 심볼 다운로드 (Coinbase Premium용)
                cb_symbol = target_sym_info.get("coinbase_symbol") or ""
                if cb_symbol:
                    csv_name_cb = f"{cb_symbol}-{interval}-{date_str}.csv"
                    csv_path_cb = os.path.join(f"{_DATA_ROOT}/historical/coinbase_extracted/{cb_symbol}", csv_name_cb)
                    if not os.path.exists(csv_path_cb):
                        local_log(f"   [{idx+1}/{total_days}] 코인베이스 {cb_symbol} 데이터 수집 중...")
                        try:
                            res_df = download_coinbase_day(config, date_str, symbol=cb_symbol, interval=interval)
                            if res_df is not None and not res_df.empty:
                                local_log(f"      ✅ 완료: {csv_name_cb} 수집 성공")
                                download_count += 1
                        except Exception as e:
                            local_log(f"      ❌ 에러: {e}")
                        time.sleep(0.5)
                
            local_log(f"\n🟢 바이낸스 및 글로벌 데이터 동기화 완료! (신규 다운로드: {download_count}개, 기존 캐시 스킵: {skip_count}개)")
            local_log(f"✅ 바이낸스 작업 완료 - 다운로드: {download_count}, 캐시: {skip_count}")
            
    except Exception as outer_e:
        local_log(f"❌ 치명적 오류 발생: {outer_e}")
    finally:
        if callback_on_finish:
            callback_on_finish()

def validate_snapshot_integrity(df: pd.DataFrame, mkt: str, local_log=None) -> bool:
    """
    [데이터 무결성 검증 게이트 (Integrity Gate)]
    타겟 심볼과 외부 데이터 간의 정합성을 검증합니다.
    1. Coinbase vs Binance 괴리 검증:
       동일 자산(예: ETH vs ETH-USD, BTC vs BTC-USD) 간 비율이 비정상적으로 왜곡(±50% 초과)되는 현상 방지.
    2. 벤치마크 BTC 시세 검증:
       가상자산 데이터셋에 benchmark_btc_close 컬럼이 존재하고 유효한 양수 값을 가지는지 검증.
    3. 타겟 시세 정합성 검증:
       타겟 종가(upbit_close 또는 target_close)의 유효성 검증.
    """
    if local_log is None:
        local_log = logging.info
        
    errors = []
    # 1. Coinbase vs Binance 괴리율 검증
    if 'coinbase_spot_close' in df.columns and 'binance_spot_close' in df.columns:
        valid_mask = df['coinbase_spot_close'].notna() & df['binance_spot_close'].notna() & (df['binance_spot_close'] > 0)
        if valid_mask.any():
            ratio = float((df.loc[valid_mask, 'coinbase_spot_close'] / df.loc[valid_mask, 'binance_spot_close']).median())
            if ratio < 0.5 or ratio > 2.0:
                errors.append(
                    f"심각한 데이터 오염 감지: coinbase_spot_close와 binance_spot_close 간의 중앙값 배율이 {ratio:.2f}배입니다. "
                    f"(타겟 {mkt}에 타 종목 시세 오주입 가능성)"
                )

    # 2. 벤치마크 BTC 시세 검증
    if str(mkt).startswith("KRW-"):
        if 'benchmark_btc_close' not in df.columns:
            errors.append("가상자산 데이터셋에 'benchmark_btc_close' 컬럼이 누락되었습니다.")
        else:
            null_cnt = int(df['benchmark_btc_close'].isna().sum())
            if null_cnt == len(df):
                errors.append("'benchmark_btc_close' 컬럼의 모든 값이 결측치(NaN)입니다.")
            elif (df['benchmark_btc_close'].dropna() <= 0).any():
                errors.append("'benchmark_btc_close'에 0 이하의 비정상 가격이 존재합니다.")

    # 3. [P0-2-1] KRX 일자별 캔들/틱 밀도 검증 (고유 분 결손 < 380분 차단)
    is_krx_stock = not str(mkt).startswith("KRW-") and str(mkt).isdigit() and len(str(mkt)) == 6
    if is_krx_stock and 'timestamp' in df.columns and len(df) >= 380:
        ts_s = pd.to_datetime(df['timestamp'], errors='coerce').dropna()
        if not ts_s.empty:
            df_temp = pd.DataFrame({'date': ts_s.dt.date, 'minute': ts_s.dt.floor('min')})
            daily_mins = df_temp.groupby('date')['minute'].nunique()
            bad_days = daily_mins[daily_mins < 380]
            if not bad_days.empty:
                bad_details = [f"{d} ({cnt}분)" for d, cnt in bad_days.items()]
                errors.append(
                    f"일자별 캔들 밀도 붕괴 (고유 분 < 380분 결손): {', '.join(bad_details[:5])} "
                    f"— 원천 틱/캔들 데이터 누락 의심 (정상 세션: 381분)"
                )

    # 4. [P0-2-2] 참조 대장주(ref_*_close) 일자 단위 결측률 검증 (>= 50% 결측 차단)
    ref_close_cols = [c for c in df.columns if c.startswith("ref_") and c.endswith("_close")]
    if ref_close_cols and 'timestamp' in df.columns and len(df) >= 380:
        ts_s = pd.to_datetime(df['timestamp'], errors='coerce')
        valid_dates = ts_s.dt.date
        for r_col in ref_close_cols:
            daily_missing = df.groupby(valid_dates)[r_col].apply(lambda s: s.isna().mean())
            bad_ref_days = daily_missing[daily_missing >= 0.5]
            if not bad_ref_days.empty:
                bad_ref_details = [f"{d} ({r*100:.1f}% NaN)" for d, r in bad_ref_days.items()]
                errors.append(
                    f"참조 지표 심각 결측 (일일 결측률 >= 50%): {r_col} 컬럼의 {', '.join(bad_ref_details[:3])}"
                )

    # 5. [P0-2-3] 하루 내 동일 가격(stale-ffill) 비정상 수평선 검증 (>= 30분 지속 차단)
    close_col = 'target_close' if 'target_close' in df.columns else ('upbit_close' if 'upbit_close' in df.columns else None)
    vol_col = 'target_volume' if 'target_volume' in df.columns else ('upbit_volume' if 'upbit_volume' in df.columns else None)
    if is_krx_stock and close_col and vol_col and 'timestamp' in df.columns and len(df) >= 380:
        ts_s = pd.to_datetime(df['timestamp'], errors='coerce')
        valid_dates = ts_s.dt.date
        diff_secs = ts_s.diff().dt.total_seconds().median()
        threshold_bars = 180 if (diff_secs and diff_secs <= 15) else 30  # 10s=180봉(30분), 1m=30봉(30분)
        for d, grp in df.groupby(valid_dates):
            is_zero_vol_flat = (grp[vol_col] == 0) & (grp[close_col].diff() == 0)
            flat_runs = (~is_zero_vol_flat).cumsum()
            run_lengths = is_zero_vol_flat.groupby(flat_runs).sum()
            max_flat = run_lengths.max() if not run_lengths.empty else 0
            if max_flat >= threshold_bars:
                errors.append(
                    f"비정상 가격 수평선 감지: {d}에 {max_flat}개 연속 봉 동안 거래량 0 및 가격 무변동 "
                    f"(>= {threshold_bars}봉, 30분 이상 가짜 ffill 그리드 의심)"
                )

    if errors:
        for err in errors:
            local_log(f"  ❌ [무결성 게이트 에러] {err}")
        raise ValueError(f"데이터셋 무결성 검증 실패 ({mkt}): " + " | ".join(errors))
    
    local_log(f"  🛡️ [데이터 무결성 게이트 통과] {mkt} 데이터셋 시세/벤치마크/품질 정합성 검증 완료")
    return True

def run_dataset_build_pipeline(config, mkt, start_ym, end_ym, interval, local_log, status_update_fn=None, callback_on_finish=None, progress_callback=None):
    # KIS 종목 판별 시 KIS 스냅샷 빌더로 즉시 분기 (종목 레지스트리 메타데이터 연동)
    from shared.config.config_manager import get_symbol_info
    sym_info = get_symbol_info(mkt)
    mtype = sym_info.get("market_type", "")
    exchange = sym_info.get("exchange", "")
    is_kis_market = (exchange in ("KIS", "CREON")) or (sym_info.get("asset_type") in ("STOCK", "ETF")) or ("ETF" in mtype) or ("STOCK" in mtype) or (not str(mkt).startswith("KRW-") and str(mkt).isdigit() and len(str(mkt)) == 6)
    if is_kis_market:
        return run_kis_dataset_build_pipeline(config, mkt, start_ym, end_ym, interval, local_log, status_update_fn, callback_on_finish, progress_callback)

    try:
        local_log(f"\n⚙️ 통합 데이터셋 빌드 및 바이낸스 결합 프로세스 시작 (단위: {interval})...")
        if progress_callback: progress_callback(5, "1단계: 국내 캔들/틱 체결 데이터 처리 준비...")
        
        start_dt = datetime.strptime(f"{start_ym}-01", "%Y-%m-%d")
        end_dt = datetime.strptime(f"{end_ym}-01", "%Y-%m-%d")
        
        raw_dates = pd.date_range(start=start_dt, end=end_dt + pd.offsets.MonthEnd(0), freq='D')
        curr_now = datetime.now()
        
        date_range = []
        for dt in raw_dates:
            if dt.date() >= curr_now.date():
                continue
            date_range.append(dt)

        dfs = []
        upbit_interval = _upbit_supported_interval(interval)
        total_upbit_days = max(1, len(date_range))
        for i, dt in enumerate(date_range):
            if progress_callback and (i % max(1, total_upbit_days // 10) == 0 or i == total_upbit_days - 1):
                pct = 5 + int((i / total_upbit_days) * 35)
                progress_callback(pct, f"1단계: 국내 캔들/틱 처리 중 ({i+1}/{total_upbit_days}일)")
            date_str = dt.strftime("%Y%m%d")
            candle_csv = os.path.join(f"{_DATA_ROOT}/historical/upbit/candle_{interval}_extracted", f"{mkt}_candle-{upbit_interval}_{date_str}.csv")
            trade_csv = os.path.join(f"{_DATA_ROOT}/historical/upbit/trade_{interval}_extracted", f"{mkt}_trade_{date_str}.csv")
            
            if not os.path.exists(candle_csv):
                local_log(f"  ⚠️ 캔들 파일 누락: {os.path.basename(candle_csv)} - 이후 데이터 빌드 스킵 (현재까지 수집된 데이터 결합)")
                break
            
            if not os.path.exists(trade_csv):
                local_log(f"  ⚠️ 체결 파일 누락: {os.path.basename(trade_csv)} - 이후 데이터 빌드 스킵 (현재까지 수집된 데이터 결합)")
                break
            
            local_log(f"  📖 데이터 처리 중: {date_str} ...")
            try:
                # 1. 캔들 데이터 KST 변환 (+9시간)
                c_df = pd.read_csv(candle_csv)
                c_df['timestamp'] = pd.to_datetime(c_df['date_time_utc']) + pd.Timedelta(hours=9)
                c_df['timestamp'] = c_df['timestamp'].dt.floor('s').astype('datetime64[ns]')
                c_df['upbit_open'] = pd.to_numeric(c_df['open'])
                c_df['upbit_close'] = pd.to_numeric(c_df['close'])
                c_df['upbit_high'] = pd.to_numeric(c_df['high'])
                c_df['upbit_low'] = pd.to_numeric(c_df['low'])
                c_df['upbit_volume'] = pd.to_numeric(c_df['acc_trade_volume'])
                c_df['upbit_trade_price'] = pd.to_numeric(c_df['acc_trade_price'])
                c_df = c_df[['timestamp', 'upbit_open', 'upbit_close', 'upbit_high', 'upbit_low', 'upbit_volume', 'upbit_trade_price']]
                
                # 2. 체결 데이터 KST 변환 (+9시간) 및 Resample
                t_df = pd.read_csv(trade_csv)
                t_df['timestamp_dt'] = pd.to_datetime(t_df['timestamp'], unit='ms') + pd.Timedelta(hours=9)
                
                if interval == '1s':
                    t_df['dt_group'] = t_df['timestamp_dt'].dt.floor('s').astype('datetime64[ns]')
                else:
                    t_df['dt_group'] = t_df['timestamp_dt'].dt.floor(get_pandas_freq(interval)).astype('datetime64[ns]')
                
                # 체결 금액 = 수량 × 가격
                t_df['trade_amount'] = t_df['volume'] * t_df['price']
                
                total_vol = t_df.groupby('dt_group')['volume'].sum()
                bid_vol = t_df[t_df['ask_bid'] == 'BID'].groupby('dt_group')['volume'].sum()
                trade_count = t_df.groupby('dt_group').size()
                # 📌 [대량 체결 기준 수정]: 수량(≥10,000) → 금액(≥1억 원) 기준
                # 기존 기준은 BTC처럼 고가 코인에서 수량 1만개를 넘는 체결이 없어 전부 0이 되는 문제가 있었음
                large_trade_count = t_df[t_df['trade_amount'] >= 100000000].groupby('dt_group')['trade_amount'].count()
                avg_trade_size = t_df.groupby('dt_group')['volume'].mean()
                avg_trade_amount = t_df.groupby('dt_group')['trade_amount'].mean()
                
                resampled = pd.DataFrame(index=total_vol.index)
                resampled['upbit_trade_bid_ratio'] = bid_vol / (total_vol + 1e-8)
                resampled['upbit_trade_count'] = trade_count
                resampled['upbit_large_trade_count'] = large_trade_count.reindex(total_vol.index, fill_value=0)
                resampled['upbit_avg_trade_size'] = avg_trade_size
                resampled['upbit_avg_trade_amount'] = avg_trade_amount
                resampled.index.name = 'timestamp'
                resampled.reset_index(inplace=True)
                    
                # 3. 결합 (초봉 vs 1분봉 vs 상위 분/시봉)
                is_sub_minute = interval.endswith('s') and not interval.endswith('ms')
                if is_sub_minute:
                    start_ts = pd.Timestamp(dt).replace(hour=0, minute=0, second=0)
                    end_ts = pd.Timestamp(dt).replace(hour=23, minute=59, second=59)
                    full_idx = pd.date_range(start=start_ts, end=end_ts, freq=get_pandas_freq(interval))
                    
                    if not t_df.empty:
                        # 틱 체결 데이터 기반 OHLCV 정밀 집계
                        tick_ohlcv = t_df.groupby('dt_group').agg(
                            upbit_open=('price', 'first'),
                            upbit_high=('price', 'max'),
                            upbit_low=('price', 'min'),
                            upbit_close=('price', 'last'),
                            upbit_volume=('volume', 'sum'),
                            upbit_trade_price=('trade_amount', 'sum')
                        )
                        c_df_sec = tick_ohlcv.reindex(full_idx)
                        c_df_sec['upbit_close'] = c_df_sec['upbit_close'].ffill()
                        c_df_sec['upbit_open'] = c_df_sec['upbit_open'].fillna(c_df_sec['upbit_close'])
                        c_df_sec['upbit_high'] = c_df_sec['upbit_high'].fillna(c_df_sec['upbit_close'])
                        c_df_sec['upbit_low'] = c_df_sec['upbit_low'].fillna(c_df_sec['upbit_close'])
                        c_df_sec['upbit_volume'] = c_df_sec['upbit_volume'].fillna(0.0)
                        c_df_sec['upbit_trade_price'] = c_df_sec['upbit_trade_price'].fillna(0.0)
                    else:
                        # 틱 데이터 누락 시 1m 캔들 기반 초 단위 확장 (fallback)
                        c_df_sec = c_df.set_index('timestamp').reindex(full_idx)
                        c_df_sec['upbit_open'] = c_df_sec['upbit_open'].ffill()
                        c_df_sec['upbit_close'] = c_df_sec['upbit_close'].ffill()
                        c_df_sec['upbit_high'] = c_df_sec['upbit_high'].ffill()
                        c_df_sec['upbit_low'] = c_df_sec['upbit_low'].ffill()
                        sec_num = int(interval[:-1]) if interval[:-1].isdigit() and int(interval[:-1]) > 0 else 1
                        sec_divider = 60.0 / sec_num
                        c_df_sec['upbit_volume'] = (c_df_sec['upbit_volume'].ffill() / sec_divider).fillna(0.0)
                        c_df_sec['upbit_trade_price'] = (c_df_sec['upbit_trade_price'].ffill() / sec_divider).fillna(0.0)
                        
                    c_df_sec.index.name = 'timestamp'
                    c_df_sec.reset_index(inplace=True)
                    
                    day_merged = pd.merge(c_df_sec, resampled, on='timestamp', how='left')
                    day_merged['upbit_trade_bid_ratio'] = day_merged['upbit_trade_bid_ratio'].fillna(0.5)
                    day_merged['upbit_trade_count'] = day_merged['upbit_trade_count'].fillna(0.0)
                    day_merged['upbit_large_trade_count'] = day_merged['upbit_large_trade_count'].fillna(0.0)
                    day_merged['upbit_avg_trade_size'] = day_merged['upbit_avg_trade_size'].fillna(0.0)
                    day_merged['upbit_avg_trade_amount'] = day_merged['upbit_avg_trade_amount'].fillna(0.0)
                elif interval == '1m':
                    day_merged = pd.merge(c_df, resampled, on='timestamp', how='left')
                    day_merged['upbit_trade_bid_ratio'] = day_merged['upbit_trade_bid_ratio'].fillna(0.5)
                    day_merged['upbit_trade_count'] = day_merged['upbit_trade_count'].fillna(0.0)
                    day_merged['upbit_large_trade_count'] = day_merged['upbit_large_trade_count'].fillna(0.0)
                    day_merged['upbit_avg_trade_size'] = day_merged['upbit_avg_trade_size'].fillna(0.0)
                    day_merged['upbit_avg_trade_amount'] = day_merged['upbit_avg_trade_amount'].fillna(0.0)
                else:
                    freq = get_pandas_freq(interval)
                    c_df_resampled = c_df.set_index('timestamp').resample(freq).agg({
                        'upbit_open': 'first',
                        'upbit_close': 'last',
                        'upbit_high': 'max',
                        'upbit_low': 'min',
                        'upbit_volume': 'sum',
                        'upbit_trade_price': 'sum'
                    })
                    c_df_resampled['upbit_open'] = c_df_resampled['upbit_open'].ffill()
                    c_df_resampled['upbit_close'] = c_df_resampled['upbit_close'].ffill()
                    c_df_resampled['upbit_high'] = c_df_resampled['upbit_high'].ffill()
                    c_df_resampled['upbit_low'] = c_df_resampled['upbit_low'].ffill()
                    c_df_resampled['upbit_volume'] = c_df_resampled['upbit_volume'].fillna(0.0)
                    c_df_resampled['upbit_trade_price'] = c_df_resampled['upbit_trade_price'].fillna(0.0)
                    c_df_resampled.reset_index(inplace=True)
                    
                    day_merged = pd.merge(c_df_resampled, resampled, on='timestamp', how='left')
                    day_merged['upbit_trade_bid_ratio'] = day_merged['upbit_trade_bid_ratio'].fillna(0.5)
                    day_merged['upbit_trade_count'] = day_merged['upbit_trade_count'].fillna(0.0)
                    day_merged['upbit_large_trade_count'] = day_merged['upbit_large_trade_count'].fillna(0.0)
                    day_merged['upbit_avg_trade_size'] = day_merged['upbit_avg_trade_size'].fillna(0.0)
                    day_merged['upbit_avg_trade_amount'] = day_merged['upbit_avg_trade_amount'].fillna(0.0)
                        
                dfs.append(day_merged)
            except Exception as e:
                local_log(f"    ❌ 가공 중 오류 (스킵): {e}")
                
        if not dfs:
            local_log("❌ 가공할 수 있는 데이터가 없습니다. 먼저 다운로드를 실행해 주세요."); return
            
        upbit_total = pd.concat(dfs).drop_duplicates(subset=['timestamp'])
        upbit_total.sort_values('timestamp', inplace=True)

        # ── [D-9] 타겟 자체 가격의 일자 경계 보정 (Point-in-Time) ──
        # 종전에는 일자별 head 를 `bfill()`(미래값)로 채웠다 → 해당 시점에 **미래 가격**이 학습에 유입.
        # 이제 일자별 head 는 NaN 으로 두고, **연속 프레임에서 직전 세션 종가를 ffill** 한다.
        # (직전 세션 종가는 T 시점에 이미 알려진 값이므로 Lookahead 가 아니다)
        # 데이터셋 최초 구간만 NaN 으로 남는다.
        for _c in ('upbit_open', 'upbit_high', 'upbit_low', 'upbit_close', 'mid_price', 'bid', 'ask'):
            if _c in upbit_total.columns:
                upbit_total[_c] = upbit_total[_c].ffill()
        
        start_date = upbit_total['timestamp'].min().strftime('%Y-%m-%d')
        end_date = upbit_total['timestamp'].max().strftime('%Y-%m-%d')
        
        local_log(f" 📥 바이낸스 일별 아카이브 동기화 및 결합 ({start_date} ~ {end_date})...")
        
        coin_code = mkt.split("-")[-1]
        symbol_binance = f"{coin_code}USDT"
        config['SYMBOL_BINANCE'] = symbol_binance
        
        binance_interval = _binance_supported_interval(interval)
        date_range_binance = pd.date_range(start=start_date, end=end_date)
        binance_dfs = []
        total_binance_days = max(1, len(date_range_binance))
        for j, dt in enumerate(date_range_binance):
            if progress_callback and (j % max(1, total_binance_days // 10) == 0 or j == total_binance_days - 1):
                pct = 40 + int((j / total_binance_days) * 40)
                progress_callback(pct, f"2단계: 바이낸스 데이터 결합 중 ({j+1}/{total_binance_days}일)")
            date_str = dt.strftime('%Y-%m-%d')
            df_b = download_binance_spot_day(config, date_str, interval=interval)
            df_oi = download_binance_futures_oi_day(config, date_str, interval=interval)
            df_usdc = download_binance_usdc_day(config, date_str, interval=interval)
            
            if df_b is not None:
                df_b['timestamp'] = pd.to_datetime(df_b['timestamp']).dt.floor('s').astype('datetime64[ns]')
                df_b.sort_values('timestamp', inplace=True)
                
                # 1. 미결제약정 병합
                if df_oi is not None:
                    df_oi['timestamp'] = pd.to_datetime(df_oi['timestamp']).dt.floor('s').astype('datetime64[ns]')
                    df_oi.sort_values('timestamp', inplace=True)
                    merged_day = pd.merge_asof(
                        df_b,
                        df_oi,
                        on='timestamp',
                        direction='backward'
                    )
                    merged_day['binance_futures_oi'] = merged_day['binance_futures_oi'].ffill()
                    merged_day['binance_futures_oi_value'] = merged_day['binance_futures_oi_value'].ffill()
                else:
                    merged_day = df_b.copy()
                    merged_day['binance_futures_oi'] = np.nan
                    merged_day['binance_futures_oi_value'] = np.nan
                
                # 2. USDC 거래량 병합
                if df_usdc is not None:
                    df_usdc['timestamp'] = pd.to_datetime(df_usdc['timestamp']).dt.floor('s').astype('datetime64[ns]')
                    df_usdc.sort_values('timestamp', inplace=True)
                    merged_day = pd.merge_asof(
                        merged_day,
                        df_usdc,
                        on='timestamp',
                        direction='backward'
                    )
                    merged_day['binance_usdc_volume'] = merged_day['binance_usdc_volume'].ffill().fillna(0.0)
                else:
                    merged_day['binance_usdc_volume'] = np.nan

                # 3. 가상자산 벤치마크 및 상대 모멘텀용 시세 병합 (benchmark_btc_close, binance_eth_close)
                if get_asset_id(mkt) == BENCHMARK_ASSET_ID:
                    merged_day['benchmark_btc_close'] = merged_day['binance_spot_close']
                    df_eth = download_binance_eth_day(config, date_str, interval=interval)
                    if df_eth is not None and not df_eth.empty:
                        df_eth['timestamp'] = pd.to_datetime(df_eth['timestamp']).dt.floor('s').astype('datetime64[ns]')
                        df_eth.sort_values('timestamp', inplace=True)
                        merged_day = pd.merge_asof(
                            merged_day,
                            df_eth,
                            on='timestamp',
                            direction='backward'
                        )
                        merged_day['binance_eth_close'] = merged_day['binance_eth_close'].ffill()
                    else:
                        merged_day['binance_eth_close'] = np.nan
                else:
                    # 알트코인인 경우: 벤치마크 BTCUSDT 시세를 가져와 benchmark_btc_close에 병합
                    df_btc = download_binance_btc_benchmark_day(date_str, interval=interval)
                    if df_btc is not None and not df_btc.empty:
                        df_btc['timestamp'] = pd.to_datetime(df_btc['timestamp']).dt.floor('s').astype('datetime64[ns]')
                        df_btc.sort_values('timestamp', inplace=True)
                        merged_day = pd.merge_asof(
                            merged_day,
                            df_btc,
                            on='timestamp',
                            direction='backward'
                        )
                        merged_day['benchmark_btc_close'] = merged_day['benchmark_btc_close'].ffill()
                    else:
                        merged_day['benchmark_btc_close'] = np.nan
                        
                    if get_asset_id(mkt) == RELATIVE_MOMENTUM_ASSET_ID:
                        merged_day['binance_eth_close'] = merged_day['binance_spot_close']
                    else:
                        df_eth = download_binance_eth_day(config, date_str, interval=interval)
                        if df_eth is not None and not df_eth.empty:
                            df_eth['timestamp'] = pd.to_datetime(df_eth['timestamp']).dt.floor('s').astype('datetime64[ns]')
                            df_eth.sort_values('timestamp', inplace=True)
                            merged_day = pd.merge_asof(
                                merged_day,
                                df_eth,
                                on='timestamp',
                                direction='backward'
                            )
                            merged_day['binance_eth_close'] = merged_day['binance_eth_close'].ffill()
                        else:
                            merged_day['binance_eth_close'] = np.nan

                # 4. 코인베이스 타겟 심볼 병합 (Coinbase Premium용)
                target_sym_info = get_symbol_info(mkt)
                cb_symbol = target_sym_info.get("coinbase_symbol") or ""
                if cb_symbol:
                    df_cb = download_coinbase_day(config, date_str, symbol=cb_symbol, interval=interval)
                    if df_cb is not None and not df_cb.empty:
                        df_cb['timestamp'] = pd.to_datetime(df_cb['timestamp']).dt.floor('s').astype('datetime64[ns]')
                        df_cb.sort_values('timestamp', inplace=True)
                        merged_day = pd.merge_asof(
                            merged_day,
                            df_cb,
                            on='timestamp',
                            direction='backward'
                        )
                        merged_day['coinbase_spot_close'] = merged_day['coinbase_spot_close'].ffill()
                    else:
                        merged_day['coinbase_spot_close'] = np.nan
                else:
                    merged_day['coinbase_spot_close'] = np.nan

                # 5. 업비트 KRW-USDT 병합 (정밀 김치 프리미엄용)
                df_usdt = download_upbit_usdt_day(config, date_str, interval=interval)
                if df_usdt is not None and not df_usdt.empty:
                    df_usdt['timestamp'] = pd.to_datetime(df_usdt['timestamp']).dt.floor('s').astype('datetime64[ns]')
                    df_usdt.sort_values('timestamp', inplace=True)
                    merged_day = pd.merge_asof(
                        merged_day,
                        df_usdt,
                        on='timestamp',
                        direction='backward'
                    )
                    merged_day['upbit_usdt_close'] = merged_day['upbit_usdt_close'].ffill()
                else:
                    merged_day['upbit_usdt_close'] = np.nan

                binance_dfs.append(merged_day)
                
        if not binance_dfs:
            local_log("❌ 바이낸스 데이터 병합 실패"); return
            
        df_binance = pd.concat(binance_dfs).drop_duplicates(subset=['timestamp'])
        df_binance.sort_values('timestamp', inplace=True)
        
        # 바이낸스 데이터 소실 구간 검사 및 경고 로그 출력 (학습 전 결측 인지용)
        try:
            loss_cols = [
                'binance_spot_close', 'binance_spot_volume', 
                'binance_spot_taker_ratio', 'binance_futures_oi',
                'binance_usdc_volume'
            ]
            # 검사 대상 컬럼 중 존재하지 않는 것은 제외
            existing_cols = [col for col in loss_cols if col in df_binance.columns]
            
            is_missing = df_binance[existing_cols].isna().any(axis=1) | (df_binance[existing_cols] == 0).any(axis=1)
            if is_missing.any():
                df_binance_reset = df_binance.reset_index(drop=True)
                is_missing_reset = df_binance_reset[existing_cols].isna().any(axis=1) | (df_binance_reset[existing_cols] == 0).any(axis=1)
                loss_indices_reset = df_binance_reset[is_missing_reset].index
                
                loss_ranges = []
                if len(loss_indices_reset) > 0:
                    start_idx = loss_indices_reset[0]
                    prev_idx = loss_indices_reset[0]
                    for idx in loss_indices_reset[1:]:
                        if idx > prev_idx + 1:
                            loss_ranges.append((start_idx, prev_idx))
                            start_idx = idx
                        prev_idx = idx
                    loss_ranges.append((start_idx, prev_idx))
                    
                    # 1. 굵직한 결측 구간만 상세 기록 출력 (10행 이상 연속 누락만)
                    local_log("\n  ⚠️ [데이터 유실 경고] 바이낸스 주요 소실 구간 감지 (10행 이상 연속 누락):")
                    local_log("  " + "-" * 95)
                    printed_count = 0
                    for start, end in loss_ranges:
                        rows = end - start + 1
                        if rows >= 10:
                            printed_count += 1
                            s_t = pd.to_datetime(df_binance_reset['timestamp'].iloc[start]).strftime('%Y-%m-%d %H:%M:%S')
                            e_t = pd.to_datetime(df_binance_reset['timestamp'].iloc[end]).strftime('%Y-%m-%d %H:%M:%S')
                            
                            # 해당 블록에서 실제로 결측/누락이 발생한 컬럼 수집
                            block_df = df_binance_reset.iloc[start:end+1]
                            missing_cols = []
                            for col in existing_cols:
                                if block_df[col].isna().any() or (block_df[col] == 0).any():
                                    missing_cols.append(col)
                            
                            cols_str = ", ".join(missing_cols)
                            local_log(f"    - {s_t} ~ {e_t} ({rows:>5}행 누락) [항목: {cols_str}]")
                    
                    if printed_count == 0:
                        local_log("    * 10행 이상의 연속된 장기 누락 구간은 발견되지 않았습니다. (자잘한 단편 누락만 존재)")
                    local_log("  " + "-" * 95)

                    # 2. 항목(컬럼)별 최종 요약 집계 리포트 출력
                    total_len = len(df_binance)
                    local_log("\n  📊 [바이낸스 데이터 결측 요약 집계 리포트]")
                    local_log("  " + "=" * 95)
                    for col in existing_cols:
                        col_missing = df_binance_reset[col].isna() | (df_binance_reset[col] == 0)
                        missing_count = col_missing.sum()
                        ratio = (missing_count / total_len) * 100
                        
                        # 이 컬럼의 최대 연속 누락 구간 찾기
                        max_loss_period_str = "없음"
                        if missing_count > 0:
                            col_missing_indices = df_binance_reset[col_missing].index
                            c_start = col_missing_indices[0]
                            c_prev = col_missing_indices[0]
                            max_len = 0
                            max_range = (c_start, c_prev)
                            for idx in col_missing_indices[1:]:
                                if idx > c_prev + 1:
                                    c_len = c_prev - c_start + 1
                                    if c_len > max_len:
                                        max_len = c_len
                                        max_range = (c_start, c_prev)
                                    c_start = idx
                                c_prev = idx
                            # 마지막 블록
                            c_len = c_prev - c_start + 1
                            if c_len > max_len:
                                max_len = c_len
                                max_range = (c_start, c_prev)
                            
                            m_s = pd.to_datetime(df_binance_reset['timestamp'].iloc[max_range[0]]).strftime('%Y-%m-%d %H:%M:%S')
                            m_e = pd.to_datetime(df_binance_reset['timestamp'].iloc[max_range[1]]).strftime('%Y-%m-%d %H:%M:%S')
                            max_loss_period_str = f"{m_s} ~ {m_e} ({max_len}행)"
                            
                        local_log(f"    * {col:<26}: 누락 {missing_count:>7,}행 ({ratio:>6.2f}%) | 최대 결측대역: {max_loss_period_str}")
                    local_log("  " + "=" * 95)
                    local_log("  → 위 요약 리포트의 결측 비율 및 대역을 확인하시고 학습 기간 설정에 참조하시기 바랍니다.\n")
            else:
                local_log("\n  ✅ 바이낸스 데이터 무결성 검증 통과 (소실 구간 없음)")
        except Exception as check_e:
            logging.warning(f"[BuildPipeline] Failed to check data loss: {check_e}")

        # [신규 바이낸스 컬럼 방어 처리 헬퍼]
        _binance_new_cols = [
            'binance_spot_open', 'binance_spot_high', 'binance_spot_low',
            'binance_spot_quote_volume', 'binance_spot_trade_count',
            'binance_spot_taker_quote_ratio'
        ]

        if interval == '1s':
            upbit_total['timestamp'] = pd.to_datetime(upbit_total['timestamp']).astype('datetime64[ns]')
            df_binance['timestamp'] = pd.to_datetime(df_binance['timestamp']).astype('datetime64[ns]')
            upbit_total.sort_values('timestamp', inplace=True)
            final_df = pd.merge_asof(
                upbit_total, 
                df_binance, 
                on='timestamp', 
                direction='backward'
            )
            final_df['binance_spot_close'] = final_df['binance_spot_close'].ffill()
            final_df['binance_spot_volume'] = (final_df['binance_spot_volume'].ffill() / 60.0).fillna(0.0)
            final_df['binance_spot_taker_ratio'] = final_df['binance_spot_taker_ratio'].fillna(0.5)
            final_df['binance_futures_oi'] = final_df['binance_futures_oi'].ffill()
            final_df['binance_futures_oi_value'] = final_df['binance_futures_oi_value'].ffill()
            final_df['binance_usdc_volume'] = (final_df['binance_usdc_volume'].ffill() / 60.0)
            if 'benchmark_btc_close' in final_df.columns:
                final_df['benchmark_btc_close'] = final_df['benchmark_btc_close'].ffill()
            if 'coinbase_spot_close' in final_df.columns:
                final_df['coinbase_spot_close'] = final_df['coinbase_spot_close'].ffill()
            if 'binance_eth_close' in final_df.columns:
                final_df['binance_eth_close'] = final_df['binance_eth_close'].ffill()
            for col in _binance_new_cols:
                if col in final_df.columns:
                    if col in ('binance_spot_open', 'binance_spot_high', 'binance_spot_low'):
                        final_df[col] = final_df[col].ffill()
                    elif col in ('binance_spot_quote_volume', 'binance_spot_trade_count'):
                        final_df[col] = (final_df[col].ffill() / 60.0).fillna(0.0)
                    else:
                        final_df[col] = final_df[col].fillna(0.5)
        elif binance_interval == interval:
            final_df = pd.merge(upbit_total, df_binance, on='timestamp', how='inner')
            final_df['binance_futures_oi'] = final_df['binance_futures_oi'].ffill()
            final_df['binance_futures_oi_value'] = final_df['binance_futures_oi_value'].ffill()
            final_df['binance_usdc_volume'] = final_df['binance_usdc_volume'].ffill().fillna(0.0)
            if 'benchmark_btc_close' in final_df.columns:
                final_df['benchmark_btc_close'] = final_df['benchmark_btc_close'].ffill()
            if 'coinbase_spot_close' in final_df.columns:
                final_df['coinbase_spot_close'] = final_df['coinbase_spot_close'].ffill()
            if 'binance_eth_close' in final_df.columns:
                final_df['binance_eth_close'] = final_df['binance_eth_close'].ffill()
            for col in _binance_new_cols:
                if col in final_df.columns:
                    if col in ('binance_spot_open', 'binance_spot_high', 'binance_spot_low'):
                        final_df[col] = final_df[col].ffill()
                    elif col in ('binance_spot_quote_volume', 'binance_spot_trade_count'):
                        final_df[col] = final_df[col].fillna(0.0)
                    else:
                        final_df[col] = final_df[col].fillna(0.5)
        else:
            freq = get_pandas_freq(interval)
            agg_dict = {
                'binance_spot_open': 'first',
                'binance_spot_close': 'last',
                'binance_spot_high': 'max',
                'binance_spot_low': 'min',
                'binance_spot_volume': 'sum',
                'binance_spot_quote_volume': 'sum',
                'binance_spot_trade_count': 'sum',
                'binance_spot_taker_ratio': 'mean',
                'binance_spot_taker_quote_ratio': 'mean',
                'binance_futures_oi': 'last',
                'binance_futures_oi_value': 'last',
                'binance_usdc_volume': 'sum',
                'binance_eth_close': 'last',
                'coinbase_spot_close': 'last',
                'upbit_usdt_close': 'last'
            }
            if 'benchmark_btc_close' in df_binance.columns:
                agg_dict['benchmark_btc_close'] = 'last'
            actual_agg = {k: v for k, v in agg_dict.items() if k in df_binance.columns}
            df_binance_resampled = df_binance.set_index('timestamp').resample(freq).agg(actual_agg).reset_index()
            df_binance_resampled['binance_spot_open'] = df_binance_resampled['binance_spot_open'].ffill()
            df_binance_resampled['binance_spot_close'] = df_binance_resampled['binance_spot_close'].ffill()
            df_binance_resampled['binance_spot_high'] = df_binance_resampled['binance_spot_high'].ffill()
            df_binance_resampled['binance_spot_low'] = df_binance_resampled['binance_spot_low'].ffill()
            df_binance_resampled['binance_spot_volume'] = df_binance_resampled['binance_spot_volume'].fillna(0.0)
            df_binance_resampled['binance_spot_quote_volume'] = df_binance_resampled['binance_spot_quote_volume'].fillna(0.0)
            df_binance_resampled['binance_spot_trade_count'] = df_binance_resampled['binance_spot_trade_count'].fillna(0.0)
            df_binance_resampled['binance_spot_taker_ratio'] = df_binance_resampled['binance_spot_taker_ratio'].fillna(0.5)
            df_binance_resampled['binance_spot_taker_quote_ratio'] = df_binance_resampled['binance_spot_taker_quote_ratio'].fillna(0.5)
            df_binance_resampled['binance_futures_oi'] = df_binance_resampled['binance_futures_oi'].ffill()
            df_binance_resampled['binance_futures_oi_value'] = df_binance_resampled['binance_futures_oi_value'].ffill()
            if 'binance_eth_close' in df_binance_resampled.columns:
                df_binance_resampled['binance_eth_close'] = df_binance_resampled['binance_eth_close'].ffill()
            if 'coinbase_spot_close' in df_binance_resampled.columns:
                df_binance_resampled['coinbase_spot_close'] = df_binance_resampled['coinbase_spot_close'].ffill()
            if 'benchmark_btc_close' in df_binance_resampled.columns:
                df_binance_resampled['benchmark_btc_close'] = df_binance_resampled['benchmark_btc_close'].ffill()
            if 'upbit_usdt_close' in df_binance_resampled.columns:
                df_binance_resampled['upbit_usdt_close'] = df_binance_resampled['upbit_usdt_close'].ffill()
            # binance_usdc_volume의 리샘플링 합계에서 NaN 전파 보장 (모두 NaN인 날은 합계도 NaN이 되게 처리)
            # pandas resample sum은 기본적으로 min_count=1을 주지 않으면 결측치를 0.0으로 만듭니다.
            df_binance_resampled_nan_check = df_binance.set_index('timestamp').resample(freq).agg({
                'binance_usdc_volume': lambda x: x.sum(min_count=1)
            }).reset_index()
            df_binance_resampled['binance_usdc_volume'] = df_binance_resampled_nan_check['binance_usdc_volume']
            
            final_df = pd.merge(upbit_total, df_binance_resampled, on='timestamp', how='inner')

        # 결측치 최종 보장 (USDC / USDT 컬럼 존재 여부 체크)
        if 'binance_usdc_volume' not in final_df.columns:
            final_df['binance_usdc_volume'] = np.nan
        if 'upbit_usdt_close' not in final_df.columns:
            final_df['upbit_usdt_close'] = np.nan

        final_df.sort_values('timestamp', inplace=True)

        # [펀딩비 병합] 8시간 간격 데이터 → merge_asof backward (Look-ahead bias 없음)
        if progress_callback: progress_callback(85, "3단계: 바이낸스 펀딩비 병합 중...")
        local_log("  🔗 바이낸스 펀딩비 병합 중 (8시간 간격, API 수집)...")
        try:
            coin_code_fr = mkt.split("-")[-1]
            fr_symbol = f"{coin_code_fr}USDT"
            df_fr = _download_binance_funding_rate_range(
                start_date=final_df['timestamp'].min().strftime('%Y-%m-%d'),
                end_date=final_df['timestamp'].max().strftime('%Y-%m-%d'),
                symbol=fr_symbol
            )
            if df_fr is not None and not df_fr.empty:
                final_df['timestamp'] = pd.to_datetime(final_df['timestamp']).astype('datetime64[ns]')
                df_fr['timestamp'] = pd.to_datetime(df_fr['timestamp']).astype('datetime64[ns]')
                final_df = pd.merge_asof(
                    final_df.sort_values('timestamp'),
                    df_fr,
                    on='timestamp',
                    direction='backward'
                )
                final_df['binance_funding_rate'] = final_df['binance_funding_rate'].ffill().fillna(0.0)
                local_log(f"      ✅ 펀딩비 병합 완료: {len(df_fr):,}건")
            else:
                local_log("      ⚠️ 펀딩비 API 수집 실패 - 0.0으로 채움")
                if 'binance_funding_rate' not in final_df.columns:
                    final_df['binance_funding_rate'] = 0.0
        except Exception as fr_e:
            local_log(f"      ❌ 펀딩비 병합 오류: {fr_e}")
            if 'binance_funding_rate' not in final_df.columns:
                final_df['binance_funding_rate'] = 0.0

        if final_df.empty:
            local_log("❌ 최종 병합된 행이 없습니다. (타임스탬프 불일치)"); return

        # target_* 및 upbit_* 양방향 동기화
        for t_col, l_col in [
            ('target_open', 'upbit_open'), ('target_high', 'upbit_high'),
            ('target_low', 'upbit_low'), ('target_close', 'upbit_close'),
            ('target_volume', 'upbit_volume'), ('target_trade_price', 'upbit_trade_price'),
            ('target_trade_bid_ratio', 'upbit_trade_bid_ratio'), ('target_trade_count', 'upbit_trade_count'),
            ('target_large_trade_count', 'upbit_large_trade_count'), ('target_avg_trade_size', 'upbit_avg_trade_size')
        ]:
            if t_col in final_df.columns and l_col not in final_df.columns:
                final_df[l_col] = final_df[t_col]
            elif l_col in final_df.columns and t_col not in final_df.columns:
                final_df[t_col] = final_df[l_col]

        # ── [멀티 에셋 동적 참조 대장주 자동 병합] reference_symbols 처리 ──
        baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # [D-10] stale 상한으로 무효화한 참조 컬럼 추적 — 하류 ffill 보간이 되살리지 못하게 한다
        _ref_capped_cols: list = []
        final_df = merge_reference_symbols_into_df(final_df, config, interval, baker_root, local_log,
                                                   capped_cols_out=_ref_capped_cols)

        # [GOVERNANCE] 원천 데이터 거버넌스 카탈로그 기반 동적 결측치 처리 (탈하드코딩)
        from shared.data_library.data_catalog_manager import DataCatalogManager
        # [D-10] 참조 stale 무효화 컬럼은 보간에서 제외한다 (되채움 금지 → NaN 유지)
        final_df, imputation_report = DataCatalogManager.apply_fallback_to_dataframe(
            final_df, exclude_cols=_ref_capped_cols
        )
        if _ref_capped_cols:
            local_log(
                f"  🛡️ [D-10] 참조 stale 무효화 컬럼 {len(_ref_capped_cols)}개는 보간 제외 "
                f"(NaN 유지): {sorted(set(_ref_capped_cols))[:6]}"
            )
        if imputation_report:
            imputed_summary = [f"{c}({r['policy']}: {r['imputed_count']}행 보간)" for c, r in imputation_report.items() if r['imputed_count'] > 0]
            if imputed_summary:
                local_log(f"  🛡️ [원천데이터 결측 거버넌스] 자동 보간 완료: {', '.join(imputed_summary[:5])}")

        # [INTEGRITY GATE] 저장 직전 데이터 무결성 검증 (오염된 데이터셋 파일 생성 방지)
        validate_snapshot_integrity(final_df, mkt, local_log)

        snapshot_path = get_snapshot_path(config)
        os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
        
        if progress_callback: progress_callback(90, f"4단계: 대용량 데이터셋 파일 저장 중 ({len(final_df):,}행)...")
        local_log(f"\n  💾 [4/4단계] 대용량 통합 데이터셋 파일 저장 중 ({len(final_df):,}행, {snapshot_path})...")
        final_df.to_csv(snapshot_path, index=False)

        local_log(f"\n🟢 [빌드 완료] 성공적으로 {snapshot_path}를 생성했습니다!")
        local_log(f"   - 총 데이터 범위: {final_df['timestamp'].min()} ~ {final_df['timestamp'].max()}")
        local_log(f"   - 총 결집 라인 수: {len(final_df):,}행")
        
        local_log(f"✅ 아카이브 최종 빌드 완료! 총 {len(final_df):,}행이 {snapshot_path}에 결합되었습니다.")
        if progress_callback: progress_callback(100, f"✅ 데이터셋 빌드 완료 (총 {len(final_df):,}행)")

        # [META] 빌드 메타파일 자동 기록 — AutoLab Pre-flight Check에서 기간 충돌 0.01초 감지에 사용
        try:
            import json as _json
            from datetime import datetime as _dt
            meta_path = snapshot_path.replace('.csv', '.meta.json')
            meta = {
                "built_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": mkt,
                "interval": interval,
                "start_ym": start_ym,
                "end_ym": end_ym,
                "rows": len(final_df),
                "timestamp_min": str(final_df['timestamp'].min()),
                "timestamp_max": str(final_df['timestamp'].max()),
                "columns": list(final_df.columns),
                "data_quality": imputation_report
            }
            with open(meta_path, 'w', encoding='utf-8') as _mf:
                _json.dump(meta, _mf, ensure_ascii=False, indent=2)
            local_log(f"📋 [META] 빌드 메타파일 저장 완료: {meta_path}")
        except Exception as _me:
            local_log(f"⚠️ [META] 메타파일 저장 실패 (비치명적): {_me}")

        if status_update_fn:
            status_update_fn()

    except Exception as build_e:
        local_log(f"❌ 빌드 중 치명적 오류 발생: {build_e}")
    finally:
        if callback_on_finish:
            callback_on_finish()


def validate_downloaded_data(config, start_ym=None, end_ym=None, interval=None):
    """수집된 과거 데이터 파일들의 날짜별 무결성 및 커버리지 검증"""
    if start_ym is None:
        start_ym = config.get('ARCHIVE_START_YM', '2025-01')
    if end_ym is None:
        end_ym = config.get('ARCHIVE_END_YM', '2026-05')
    if interval is None:
        interval = config.get('INTERVAL', '15m')
        
    mkt = get_target_symbol(config)
    from shared.config.config_manager import get_symbol_info
    sym_info = get_symbol_info(mkt)
    mtype = sym_info.get("market_type", "")
    exchange = sym_info.get("exchange", "")
    is_us_asset = (mtype in ["US_STOCK", "US_ETF"]) or (not str(mkt).isdigit() and not str(mkt).startswith("KRW-") and len(str(mkt)) <= 6)
    is_krx_stock = not is_us_asset and not str(mkt).startswith("KRW-") and (str(mkt).isdigit() and len(str(mkt)) == 6 or exchange in ("KIS", "CREON"))
    is_stock_market = is_us_asset or is_krx_stock
    
    start_dt = datetime.strptime(f"{start_ym}-01", "%Y-%m-%d")
    end_dt = datetime.strptime(f"{end_ym}-01", "%Y-%m-%d")
    raw_dates = pd.date_range(start=start_dt, end=end_dt + pd.offsets.MonthEnd(0), freq='D')
    curr_now = datetime.now()
    
    # 주식/ETF 시장은 평일(월~금) 기준, 암호화폐는 전체 일자 기준
    if is_stock_market:
        date_range = [dt for dt in raw_dates if dt.date() < curr_now.date() and dt.weekday() < 5]
    else:
        date_range = [dt for dt in raw_dates if dt.date() < curr_now.date()]
    total_days = len(date_range)
    
    items = {}
    all_complete = True
    NUM_SLOTS = 40  # 미니 타임라인 바 슬롯 수
    
    def _group_ranges(date_strings):
        if not date_strings:
            return []
        ranges = []
        start = date_strings[0]
        prev = date_strings[0]
        count = 1
        for d_str in date_strings[1:]:
            d_curr = datetime.strptime(d_str, "%Y-%m-%d")
            d_prev = datetime.strptime(prev, "%Y-%m-%d")
            if (d_curr - d_prev).days <= 3:  # 주말 건너뜀도 연속 구간으로 허용
                prev = d_str
                count += 1
            else:
                ranges.append({"start": start, "end": prev, "count": count})
                start = d_str
                prev = d_str
                count = 1
        ranges.append({"start": start, "end": prev, "count": count})
        return ranges

    _dates_cache = {}
    def _fast_extract_dates_from_csv(file_path):
        if not file_path or not candle_store.path_has_data(file_path):
            return set()
        if file_path in _dates_cache:
            return _dates_cache[file_path]
        _logical_sym = candle_store.symbol_from_path(file_path)
        if _logical_sym and candle_store.is_day_layout(_logical_sym):   # Creon 1분봉 — 일자 저장소가 있으면 창구가 날짜를 답한다(디스크에 그 파일은 없다)
            _dates_cache[file_path] = set(candle_store.dates(_logical_sym))
            return _dates_cache[file_path]
        
        dates_set = set()
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                header = f.readline()
                cols = [c.strip().replace('"', '') for c in header.split(',')]
                ts_idx = cols.index('timestamp') if 'timestamp' in cols else 0
                for line in f:
                    if not line.strip():
                        continue
                    if ts_idx == 0:
                        val = line[:10]
                    else:
                        parts = line.split(',', ts_idx + 1)
                        val = parts[ts_idx][:10] if len(parts) > ts_idx else ""
                    if len(val) == 10 and val[4] == '-' and val[7] == '-':
                        dates_set.add(val)
        except Exception as e_fast:
            try:
                ts_col = pd.read_csv(file_path, usecols=['timestamp'])
                if 'timestamp' in ts_col.columns and not ts_col.empty:
                    ts_clean = ts_col['timestamp'].astype(str).str.slice(0, 10)
                    dates_set.update(ts_clean.dropna().unique().tolist())
            except Exception as e_slow:
                logger.warning(
                    f"[DataManager] 날짜 범위 추출 실패 [{file_path}]: "
                    f"빠른파싱={e_fast!r}, pandas={e_slow!r} — 빈 날짜셋으로 처리"
                )
        _dates_cache[file_path] = dates_set
        return dates_set

    # -------------------------------------------------------------
    # 마켓 캘린더 엔진 연동 (KRX 및 US/CME 공식 공휴일/휴장일 실시간 로드)
    # -------------------------------------------------------------
    from shared.core.market_calendar import get_market_holidays
    KRX_HOLIDAYS = get_market_holidays("KRX")
    US_CME_HOLIDAYS = get_market_holidays("US")

    if is_stock_market:
        baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        def _find_all_existing_files(candidates):
            found = []
            for c in candidates:
                if os.path.exists(c) and os.path.getsize(c) > 0:
                    found.append(c)
            return found

        def _find_existing_file(candidates):
            for c in candidates:
                if c and candle_store.path_has_data(c):
                    return c
            logging.warning(
                "[DataManager] 파일을 찾을 수 없습니다. 탐색 경로:\n"
                + "\n".join(f"  - {c}" for c in candidates)
            )
            return None

        clean_mkt = str(mkt).replace("KRW-", "")
        target_name = sym_info.get("name", clean_mkt)

        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        relations = SymbolRelationsManager.get_relations_for_target(clean_mkt)

        if is_us_asset:
            # 미국 개별주식 / 해외 ETF 전용 검증 타겟
            stock_targets = {
                "us_candle_1m": {
                    "name": f"미국 주식/ETF 1분봉 ({clean_mkt})",
                    "timeframe": "1m",
                    "holiday_calendar": "us",
                    "file_path": _find_existing_file([
                        os.path.join(baker_root, "data", "historical", "kis", f"candle_{interval}", f"{clean_mkt}_{interval}.csv"),
                        os.path.join(baker_root, "data", "historical", "kis", "candle_1m", f"{clean_mkt}_1m.csv")
                    ])
                },
                "us_candle_5m": {
                    "name": f"미국 주식/ETF 5분봉 ({clean_mkt})",
                    "timeframe": "5m",
                    "holiday_calendar": "us",
                    "file_path": os.path.join(baker_root, "data", "historical", "kis", "candle_5m", f"{clean_mkt}_5m.csv")
                },
                "us_candle_1d": {
                    "name": f"미국 주식/ETF 일봉 ({clean_mkt})",
                    "timeframe": "1d",
                    "holiday_calendar": "us",
                    "file_path": os.path.join(baker_root, "data", "historical", "kis", "candle_1d", f"{clean_mkt}_1d.csv")
                }
            }

            # 미국 자산 관련 매크로 선물 동적 등록
            macro_futures = [r for r in relations if r.get("role") == "macro_futures"]
            if not macro_futures:
                logger.warning(
                    f"[DataManager] {clean_mkt}: symbol_relations에 macro_futures 참조 없음"
                    " — CME 선물 검증 항목이 등록되지 않습니다. symbol_relations.json을 확인하세요."
                )
            for mf in macro_futures:
                mf_sym = str(mf.get("symbol", "")).strip().upper()
                raw_name = mf.get("name", f"미국 선물 ({mf_sym})")
                clean_name = raw_name.replace(f"({mf_sym})", "").strip()
                for tf in ["1m", "5m", "60m", "1d"]:
                    stock_targets[f"cme_{mf_sym.lower()}_{tf}"] = {
                        "name": f"{clean_name} ({mf_sym}) — {tf}",
                        "timeframe": tf,
                        "holiday_calendar": "us",
                        "file_path": os.path.join(baker_root, "data", "historical", "overseas_futures", f"{mf_sym}_{tf}.csv")
                    }
        else:
            fut_candidates = [
                dpaths.macro_raw_path("10100", interval),
                dpaths.macro_raw_path("10100", "1m"),
                dpaths.raw_path("creon", f"candle_{interval}", f"10100_{interval}.csv"),
                dpaths.raw_path("creon", f"candle_{interval}", f"101V000_{interval}.csv"),
                dpaths.raw_path("creon", "candle_1m", "10100_1m.csv"),
                dpaths.raw_path("creon", "candle_1m", "101V000_1m.csv"),
            ]
            fut_file = _find_existing_file(fut_candidates)
            fut_tf = interval if (fut_file and f"candle_{interval}" in fut_file) else "1m"
            fut_label = fut_tf if fut_tf == interval else f"{fut_tf} 연동"

            spot_candidates = [
                dpaths.macro_raw_path("U180", interval),
                dpaths.macro_raw_path("U180", "1m"),
                dpaths.raw_path("creon", f"candle_{interval}", f"U180_{interval}.csv"),
                dpaths.raw_path("creon", "candle_1m", "U180_1m.csv"),
            ]
            spot_file = _find_existing_file(spot_candidates)
            spot_tf = interval if (spot_file and f"candle_{interval}" in spot_file) else "1m"
            spot_label = spot_tf if spot_tf == interval else f"{spot_tf} 연동"

            stock_targets = {
                "creon_candle": {
                    "name": f"{target_name} {interval} 캔들 ({clean_mkt})",
                    "file_path": _find_existing_file([
                        dpaths.raw_path("creon", f"candle_{interval}", f"A{clean_mkt}_{interval}.csv"),
                        dpaths.raw_path("creon", f"candle_{interval}", f"{clean_mkt}_{interval}.csv"),
                        dpaths.raw_path("creon", "candle_1m", f"A{clean_mkt}_1m.csv"),
                        dpaths.raw_path("creon", "candle_1m", f"{clean_mkt}_1m.csv"),
                    ])
                },
                "creon_futures": {
                    "name": f"KOSPI 200 선물 캔들 ({fut_label})",
                    "timeframe": fut_tf,
                    "holiday_calendar": "krx",
                    "file_path": fut_file
                },
                "creon_spot": {
                    "name": f"KOSPI 200 지수 캔들 ({spot_label})",
                    "timeframe": spot_tf,
                    "holiday_calendar": "krx",
                    "file_path": spot_file
                }
            }

            # 1. 국내 주식/ETF 참조 종목 검증 카드 자동 등록.
            # 판정은 원장(market_type)으로 한다 — role 이름("leading_stock")이나 "6자리 영숫자이고 US로 시작 안 함"
            # 같은 문자열 모양 추측은 참조 모델이 바뀌면 조용히 어긋난다(선물 10100/지수 U180 은 아래에서 별도 카드로 등록).
            from shared.config.config_manager import get_symbol_info as _ref_sym_info
            _KRX_EQUITY_TYPES = ("KRX_STOCK", "KRX_ETF")
            ref_equities = [
                r for r in relations
                if str((_ref_sym_info(str(r.get("symbol", "")).strip()) or {}).get("market_type", "")).upper()
                in _KRX_EQUITY_TYPES
            ]
            for r in ref_equities:
                r_sym = str(r.get("symbol", "")).strip()
                r_name = r.get("name", r_sym)
                r_file = _find_existing_file([
                    dpaths.raw_path("creon", "candle_1m", f"A{r_sym}_1m.csv"),
                    dpaths.raw_path("creon", "candle_1m", f"{r_sym}_1m.csv"),
                    dpaths.raw_path("creon", f"candle_{interval}", f"A{r_sym}_{interval}.csv"),
                    dpaths.raw_path("creon", f"candle_{interval}", f"{r_sym}_{interval}.csv"),
                ])
                if r_file is None:
                    # 파일이 없어도 카드는 등록한다: 등록을 건너뛰면 참조 종목이 검증 UI에서 통째로 사라져
                    # "데이터 없음"이 조용히 숨겨진다(file_exists=False 로 노출하는 다른 타겟과 동일 처리).
                    logging.warning(f"[DataManager] 참조 종목 {r_sym} 데이터 파일 없음 — file_exists=False 로 표기")
                stock_targets[f"ref_stock_{r_sym}"] = {
                    "name": f"{r_name} 1m 캔들 ({r_sym})",
                    "timeframe": "1m",
                    "holiday_calendar": "krx",
                    "file_path": r_file
                }

            # 2. 동적 글로벌 매크로 선물(macro_futures) 자동 등록
            macro_futures = [r for r in relations if r.get("role") == "macro_futures"]
            if not macro_futures:
                logging.warning(
                    f"[DataManager] {clean_mkt}: symbol_relations에 macro_futures 참조 없음"
                    " — CME 선물 검증 항목이 등록되지 않습니다. symbol_relations.json을 확인하세요."
                )
            for mf in macro_futures:
                mf_sym = str(mf.get("symbol", "")).strip().upper()
                raw_name = mf.get("name", f"미국 선물 ({mf_sym})")
                clean_name = raw_name.replace(f"({mf_sym})", "").strip()
                for tf in ["1m", "5m", "60m", "1d"]:
                    stock_targets[f"cme_{mf_sym.lower()}_{tf}"] = {
                        "name": f"{clean_name} ({mf_sym}) — {tf}",
                        "timeframe": tf,
                        "holiday_calendar": "us",
                        "file_path": os.path.join(baker_root, "data", "historical", "overseas_futures", f"{mf_sym}_{tf}.csv")
                    }

            # 3. 타겟 틱 체결 등록
            stock_targets["creon_tick"] = {
                "name": f"{target_name} 틱 체결 ({clean_mkt})",
                "tick_symbol": clean_mkt,
            }

            # 4. 참조 종목 틱 체결 (디스크에 존재하는 경우 자동 등록)
            for r in ref_equities:
                r_sym = str(r.get("symbol", "")).strip()
                r_name = r.get("name", r_sym)
                if tick_store.has_data(r_sym):
                    stock_targets[f"ref_stock_{r_sym}_tick"] = {
                        "name": f"{r_name} 틱 체결 ({r_sym})",
                        "holiday_calendar": "krx",
                        "tick_symbol": r_sym,
                    }
        
        # 틱 데이터 전체 일자 집합 사전 추출 (타겟 및 참조 종목 틱 포괄)
        tick_symbols = [clean_mkt]
        if not is_us_asset:
            tick_symbols += [str(r.get("symbol", "")).strip() for r in ref_equities]
        all_tick_dates_set = set()
        for ts_sym in tick_symbols:
            if ts_sym and tick_store.has_data(ts_sym):
                all_tick_dates_set.update(tick_store.dates(ts_sym))

        is_sub_minute_interval = any(unit in str(interval).lower() for unit in ['s', 'sec']) or interval in ['1s', '5s', '10s', '15s', '30s']

        for key, info in stock_targets.items():
            # 다중 파일 또는 단일 파일 경로 처리
            found_dates_set = set()
            if info.get("tick_symbol"):
                found_dates_set = set(tick_store.dates(info["tick_symbol"]))
            elif "file_paths" in info:
                for fp in info["file_paths"]:
                    found_dates_set.update(_fast_extract_dates_from_csv(fp))
            elif info.get("file_path"):
                found_dates_set = _fast_extract_dates_from_csv(info["file_path"])
            
            # 초 단위 인터벌(10s 등)이고 전용 캔들 파일이 없지만 틱 데이터가 있으면 틱 리샘플링 커버리지 적용
            if key == "creon_candle" and is_sub_minute_interval and not found_dates_set and all_tick_dates_set:
                found_dates_set = all_tick_dates_set
                info["name"] = f"KODEX 200 {interval} 캔들 ({mkt}) [틱 리샘플링]"

            # 이 카드에 적용할 공휴일 캘린더 결정 (us: 미국, krx: 한국 기본)
            _holiday_cal = info.get("holiday_calendar", "krx")
            _active_holidays = US_CME_HOLIDAYS if _holiday_cal == "us" else KRX_HOLIDAYS

            # 수집된 데이터의 실제 첫 일자 및 마지막 일자
            sorted_found = sorted(list(found_dates_set))
            min_found_date = sorted_found[0] if sorted_found else None
            max_found_date = sorted_found[-1] if sorted_found else None

            found = 0
            holidays = []
            out_of_range = []
            true_missing = []
            found_dates = []
            status_slots = []

            # 실제 거래 가능한 유효 영업일 카운트
            actual_trading_days = 0

            for dt in date_range:
                d_str = dt.strftime("%Y-%m-%d")
                is_holiday = d_str in _active_holidays
                exists = d_str in found_dates_set
                
                if exists:
                    found += 1
                    actual_trading_days += 1
                    found_dates.append(d_str)
                    status_slots.append("found")
                elif is_holiday:
                    holidays.append(d_str)
                    status_slots.append("holiday")
                elif min_found_date and d_str < min_found_date:
                    out_of_range.append(d_str)
                    status_slots.append("out_of_range")
                else:
                    actual_trading_days += 1
                    true_missing.append(d_str)
                    status_slots.append("missing")
            
            # 수집 구간 내 실제 유효 거래일 대비 수집률 계산
            in_range_trading_days = found + len(true_missing)
            rate = round((found / in_range_trading_days * 100), 1) if in_range_trading_days > 0 else 0
            
            # 진짜 결측일이 있을 때만 all_complete = False (타겟 캔들 및 대장주 캔들 결측 감지)
            if len(true_missing) > 0 and (key == "creon_candle" or (key.startswith("ref_stock_") and not key.endswith("_tick"))):
                all_complete = False
                
            timeline_slots = []
            if total_days > 0:
                chunk_size = total_days / NUM_SLOTS
                for s in range(NUM_SLOTS):
                    idx_start = int(s * chunk_size)
                    idx_end = int((s + 1) * chunk_size) if s < NUM_SLOTS - 1 else total_days
                    sub = status_slots[idx_start:idx_end]
                    
                    # 슬롯 상태 판정: 진짜 결측 포함 여부 > 정상 수집 > 수집 범위 외 > 휴장일
                    if "missing" in sub:
                        s_color_type = "missing"
                        s_rate = 0
                    elif "found" in sub:
                        s_color_type = "found"
                        s_rate = 100
                    elif "out_of_range" in sub:
                        s_color_type = "out_of_range"
                        s_rate = 50
                    else:
                        s_color_type = "holiday"
                        s_rate = 100
                        
                    slot_start_dt = date_range[idx_start].strftime("%Y-%m-%d")
                    slot_end_dt = date_range[min(idx_end - 1, total_days - 1)].strftime("%Y-%m-%d")
                    timeline_slots.append({
                        "rate": s_rate,
                        "type": s_color_type,
                        "start": slot_start_dt,
                        "end": slot_end_dt
                    })
                    
            # 파일 실제 존재 여부 판정
            if info.get("tick_symbol"):
                has_file = tick_store.has_data(info["tick_symbol"])
            elif "file_paths" in info:
                has_file = any(candle_store.path_has_data(fp) for fp in info["file_paths"])
            elif info.get("file_path"):
                fp = info["file_path"]
                has_file = bool(candle_store.path_has_data(fp))
            else:
                has_file = False

            items[key] = {
                "name": info["name"],
                "timeframe": info.get("timeframe", ""),
                "holiday_calendar": info.get("holiday_calendar", "krx"),
                "count": found,
                "total": in_range_trading_days,
                "rate": rate,
                "min_date": min_found_date,
                "max_date": max_found_date,
                "holiday_count": len(holidays),
                "out_of_range_count": len(out_of_range),
                "out_of_range_ranges": _group_ranges(out_of_range),
                "true_missing_count": len(true_missing),
                "true_missing_ranges": _group_ranges(true_missing),
                "found_ranges": _group_ranges(found_dates),
                "timeline_slots": timeline_slots,
                "file_exists": has_file
            }


    else:
        # 암호화폐 전용 검증 타겟
        coin_code = mkt.split('-')[-1]
        symbol_binance = f"{coin_code}USDT"
        symbol_usdc = f"{coin_code}USDC"
        binance_interval = _binance_supported_interval(interval)
        upbit_interval = _upbit_supported_interval(interval)
        
        targets = {
            "upbit_candle": {
                "name": f"업비트 {upbit_interval} 캔들" if upbit_interval != interval else f"업비트 {interval} 캔들",
                "path_fn": lambda dt: os.path.join(f"{_DATA_ROOT}/historical/upbit/candle_{interval}_extracted", f"{mkt}_candle-{upbit_interval}_{dt.strftime('%Y%m%d')}.csv")
            },
            "upbit_trade": {
                "name": "업비트 틱 체결",
                "path_fn": lambda dt: os.path.join(f"{_DATA_ROOT}/historical/upbit/trade_{interval}_extracted", f"{mkt}_trade_{dt.strftime('%Y%m%d')}.csv")
            },
            "upbit_usdt": {
                "name": "업비트 KRW-USDT(김프)",
                "path_fn": lambda dt: os.path.join(f"{_DATA_ROOT}/historical/upbit/candle_{interval}_extracted", f"KRW-USDT_candle-{upbit_interval}_{dt.strftime('%Y%m%d')}.csv")
            },
            "binance_spot": {
                "name": f"바이낸스 현물 ({binance_interval})",
                "path_fn": lambda dt: os.path.join(f"{_DATA_ROOT}/historical/binance_extracted/{symbol_binance}", f"{symbol_binance}-{binance_interval}-{dt.strftime('%Y-%m-%d')}.csv")
            },
            "binance_oi": {
                "name": "바이낸스 선물 OI",
                "path_fn": lambda dt: os.path.join(f"{_DATA_ROOT}/historical/binance_extracted/{symbol_binance}", f"{symbol_binance}-openInterestHist-{dt.strftime('%Y-%m-%d')}.csv")
            },
            "binance_usdc": {
                "name": f"바이낸스 USDC ({binance_interval})",
                "path_fn": lambda dt: os.path.join(f"{_DATA_ROOT}/historical/binance_extracted/{symbol_usdc}", f"{symbol_usdc}-{binance_interval}-{dt.strftime('%Y-%m-%d')}.csv")
            },
            "binance_eth": {
                "name": f"바이낸스 ETH ({binance_interval})",
                "path_fn": lambda dt: os.path.join(f"{_DATA_ROOT}/historical/binance_extracted/ETHUSDT", f"ETHUSDT-{binance_interval}-{dt.strftime('%Y-%m-%d')}.csv")
            },
            "coinbase_btc": {
                "name": "코인베이스 BTC-USD",
                "path_fn": lambda dt: os.path.join(f"{_DATA_ROOT}/historical/coinbase_extracted/BTC-USD", f"BTC-USD-{interval}-{dt.strftime('%Y-%m-%d')}.csv")
            }
        }
        
        from shared.data_library.data_catalog_manager import DataCatalogManager

        for key, info in targets.items():
            found = 0
            missing = []
            pre_listing_dates = []
            found_dates = []
            status_bools = []
            
            # 원천 데이터 거버넌스 카탈로그 메타데이터 추출
            prov = "UPBIT" if "upbit" in key else ("BINANCE" if "binance" in key else ("COINBASE" if "coinbase" in key else "KRX"))
            source_id = DataCatalogManager.resolve_source_id(prov, key)
            source_meta = DataCatalogManager.get_source(source_id) if source_id else None
            avail_start = source_meta.get("availability", {}).get("available_start_date") if source_meta else None
            
            for dt in date_range:
                p = info["path_fn"](dt)
                exists = os.path.exists(p)
                status_bools.append(exists)
                d_str = dt.strftime("%Y-%m-%d")
                if exists:
                    found += 1
                    found_dates.append(d_str)
                else:
                    # 상장일 이전인지 판정
                    if avail_start and d_str < avail_start:
                        pre_listing_dates.append(d_str)
                    else:
                        missing.append(d_str)
            
            # 상장일 이후의 실제 유효 일자 수를 기준으로 수집률 계산 (상장 전 기간 왜곡 방지)
            effective_total = total_days - len(pre_listing_dates)
            rate = round((found / effective_total * 100), 1) if effective_total > 0 else 100.0
            if found < effective_total and (source_meta and source_meta.get("is_critical", False)):
                all_complete = False
                
            timeline_slots = []
            if total_days > 0:
                chunk_size = total_days / NUM_SLOTS
                for s in range(NUM_SLOTS):
                    idx_start = int(s * chunk_size)
                    idx_end = int((s + 1) * chunk_size) if s < NUM_SLOTS - 1 else total_days
                    sub = status_bools[idx_start:idx_end]
                    sub_rate = round(sum(sub) / len(sub) * 100) if sub else 0
                    slot_start_dt = date_range[idx_start].strftime("%Y-%m-%d")
                    slot_end_dt = date_range[min(idx_end - 1, total_days - 1)].strftime("%Y-%m-%d")
                    timeline_slots.append({
                        "rate": sub_rate,
                        "start": slot_start_dt,
                        "end": slot_end_dt
                    })

            items[key] = {
                "name": info["name"],
                "count": found,
                "total": total_days,
                "effective_total": effective_total,
                "pre_listing_count": len(pre_listing_dates),
                "rate": rate,
                "missing_count": len(missing),
                "missing_ranges": _group_ranges(missing),
                "pre_listing_ranges": _group_ranges(pre_listing_dates),
                "found_ranges": _group_ranges(found_dates),
                "timeline_slots": timeline_slots,
                "source_meta": {
                    "source_id": source_id,
                    "available_start_date": avail_start,
                    "skip_reason": source_meta.get("availability", {}).get("skip_reason") if source_meta else None,
                    "fallback_policy": source_meta.get("fallback_policy", {}).get("policy") if source_meta else "NONE",
                    "is_critical": source_meta.get("is_critical", False) if source_meta else False
                } if source_meta else None
            }
        
    # 통합 데이터셋 검증
    snapshot_path = get_snapshot_path(config)
    dataset_info = {"exists": False}
    if os.path.exists(snapshot_path):
        try:
            df = pd.read_csv(snapshot_path)
            null_counts = df.isnull().sum().to_dict()
            total_nulls = int(sum(null_counts.values()))
            dataset_info = {
                "exists": True,
                "path": snapshot_path,
                "rows": int(len(df)),
                "size_mb": round(os.path.getsize(snapshot_path) / (1024 * 1024), 2),
                "start": str(df['timestamp'].iloc[0]) if not df.empty and 'timestamp' in df.columns else "",
                "end": str(df['timestamp'].iloc[-1]) if not df.empty and 'timestamp' in df.columns else "",
                "total_nulls": total_nulls,
                "columns": list(df.columns)
            }
        except Exception as e:
            dataset_info = {"exists": True, "error": str(e)}
            
    return {
        "mkt": mkt,
        "interval": interval,
        "start_ym": start_ym,
        "end_ym": end_ym,
        "total_days": total_days,
        "all_complete": all_complete,
        "items": items,
        "dataset": dataset_info
    }



