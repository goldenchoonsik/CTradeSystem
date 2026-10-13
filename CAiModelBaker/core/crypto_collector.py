"""
CAiModelBaker/core/crypto_collector.py
======================================
가상자산(업비트, 바이낸스, 코인베이스) 무의존성 통합 증분 수집 엔진 (CryptoCollector)
- 특정 심볼/코인 하드코딩 배제: source_mappings.json 메타데이터 기반 동적 디스패치
- 24/7 연중무휴 시장 특성에 최적화된 고속 REST API 역방향 페이징 증분 수집
- DataVault/raw/crypto/{provider}/ 원천 불변 원장 무손실 증분 병합 (Upsert)
- 1:1 사이드카 매니페스트 (market="CRYPTO") 발행 및 2단계 정규화 Parquet 연계 최신화
"""
import os
import sys
import time
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Callable

import pandas as pd
import numpy as np

_BAKER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BAKER_ROOT)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.data_library.paths import data_vault_path, data_path
from shared.data_library.source_mapping_manager import SourceMappingManager
from shared.core.market_utils import is_crypto_symbol
from shared.data_library.manifest_manager import ManifestManager

logger = logging.getLogger(__name__)


def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 15, max_retries: int = 6) -> Optional[Any]:
    """GET → JSON. 거래소의 요청 한도 초과(429/418)나 일시 오류(5xx·네트워크)는 기다렸다가 다시 시도한다
    (Retry-After 헤더가 있으면 그만큼, 없으면 2·4·8…초, 최대 60초). 옛 코드는 약 2초 안에 3번만 시도하고 포기해
    긴 구간을 받는 도중 끊겼다."""
    import urllib.error
    req_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) CTradeSystem/2.0',
        'Accept': 'application/json',
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    for attempt in range(max_retries):
        wait = min(60.0, 2.0 ** (attempt + 1))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code in (429, 418) or e.code >= 500:
                try:
                    wait = max(wait, float(e.headers.get('Retry-After') or 0))
                except (TypeError, ValueError):
                    pass
            elif e.code < 500:
                logger.warning(f"HTTP GET 실패 ({url}): {e}")
                return None
            err = e
        except Exception as e:
            err = e
        if attempt == max_retries - 1:
            logger.warning(f"HTTP GET 실패 ({url}): {err}")
            return None
        logger.warning(f"HTTP GET 재시도 {attempt + 1}/{max_retries - 1} — {wait:.0f}초 대기 ({err})")
        time.sleep(wait)
    return None


class UpbitClient:
    """업비트 공개 REST API 클라이언트"""
    BASE_URL = 'https://api.upbit.com/v1'

    @classmethod
    def parse_unit(cls, interval: str) -> int:
        s = str(interval).lower().replace('m', '')
        try:
            return int(s)
        except ValueError:
            return 10

    @classmethod
    def fetch_minute_candles(cls, market: str, unit: int, to: Optional[str] = None, count: int = 200) -> List[Dict[str, Any]]:
        valid_units = [1, 3, 5, 10, 15, 30, 60, 240]
        actual_unit = unit if unit in valid_units else 10
        url = f"{cls.BASE_URL}/candles/minutes/{actual_unit}?market={market}&count={min(count, 200)}"
        if to:
            to_enc = urllib.parse.quote(to)
            url += f"&to={to_enc}"
        data = _http_get_json(url)
        if isinstance(data, list):
            return data
        return []

    @classmethod
    def fetch_daily_candles(cls, market: str, to: Optional[str] = None, count: int = 200) -> List[Dict[str, Any]]:
        url = f"{cls.BASE_URL}/candles/days?market={market}&count={min(count, 200)}"
        if to:
            to_enc = urllib.parse.quote(to)
            url += f"&to={to_enc}"
        data = _http_get_json(url)
        if isinstance(data, list):
            return data
        return []


class BinanceClient:
    """바이낸스 공개 REST API 클라이언트"""
    SPOT_URL = 'https://api.binance.com/api/v3'
    FUTURES_URL = 'https://fapi.binance.com/fapi/v1'
    FUTURES_DATA_URL = 'https://fapi.binance.com/futures/data'
    VALID_INTERVALS = {'1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M'}

    @classmethod
    def normalize_interval(cls, interval: str) -> Optional[str]:
        s = str(interval).lower()
        if s in cls.VALID_INTERVALS:
            return s
        if s == '10m':
            return '5m'
        return None

    @classmethod
    def fetch_spot_klines(cls, symbol: str, interval: str = '15m', start_time_ms: Optional[int] = None, limit: int = 1000) -> List[List[Any]]:
        sym = symbol.replace('-', '').replace('_', '').upper()
        intv = cls.normalize_interval(interval)
        if not intv:
            return []
        url = f"{cls.SPOT_URL}/klines?symbol={sym}&interval={intv}&limit={min(limit, 1000)}"
        if start_time_ms:
            url += f"&startTime={int(start_time_ms)}"
        data = _http_get_json(url)
        if isinstance(data, list):
            return data
        return []

    @classmethod
    def to_futures_symbol(cls, symbol: str) -> str:
        """내부 심볼(예: 'NVDA-PERP', 'BTCUSDT-PERP')을 실제 바이낸스 선물 상장 심볼로 정규화한다.
        바이낸스 선물 심볼은 항상 BASE+QUOTE(예: BTCUSDT) 형태다. "-PERP"만 떼어내면
        BTCUSDT-PERP처럼 base 심볼에 이미 QUOTE가 포함된 경우엔 우연히 맞아떨어지지만,
        NVDA-PERP처럼 base 심볼만 남는 경우엔 실제 상장 심볼(NVDAUSDT)과 어긋나 API가
        빈 응답만 반환한다. QUOTE가 없으면 USDT를 명시적으로 붙여 항상 유효한 심볼을 만든다.
        """
        base = symbol.replace('-PERP', '').replace('-', '').replace('_', '').upper()
        if not base.endswith('USDT'):
            base += 'USDT'
        return base

    @classmethod
    def fetch_futures_klines(cls, symbol: str, interval: str = '15m', start_time_ms: Optional[int] = None, limit: int = 1500) -> List[List[Any]]:
        sym = cls.to_futures_symbol(symbol)
        intv = cls.normalize_interval(interval)
        if not intv:
            return []
        url = f"{cls.FUTURES_URL}/klines?symbol={sym}&interval={intv}&limit={min(limit, 1500)}"
        if start_time_ms:
            url += f"&startTime={int(start_time_ms)}"
        data = _http_get_json(url)
        if isinstance(data, list):
            return data
        return []

    @classmethod
    def fetch_futures_oi(cls, symbol: str, period: str = '15m', start_time_ms: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
        sym = cls.to_futures_symbol(symbol)
        p = cls.normalize_interval(period)
        url = f"{cls.FUTURES_DATA_URL}/openInterestHist?symbol={sym}&period={p}&limit={min(limit, 500)}"
        if start_time_ms:
            url += f"&startTime={int(start_time_ms)}"
        data = _http_get_json(url)
        if isinstance(data, list):
            return data
        return []

    @classmethod
    def fetch_funding_rate(cls, symbol: str, start_time_ms: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        sym = cls.to_futures_symbol(symbol)
        url = f"{cls.FUTURES_URL}/fundingRate?symbol={sym}&limit={min(limit, 1000)}"
        if start_time_ms:
            url += f"&startTime={int(start_time_ms)}"
        data = _http_get_json(url)
        if isinstance(data, list):
            return data
        return []


class CoinbaseClient:
    """코인베이스 공개 REST API 클라이언트"""
    BASE_URL = 'https://api.exchange.coinbase.com'
    GRANULARITY_MAP = {
        '1m': 60,
        '5m': 300,
        '15m': 900,
        '1h': 3600,
        '60m': 3600,
        '6h': 21600,
        '1d': 86400,
    }

    @classmethod
    def resolve_granularity(cls, interval: str) -> tuple[int, str]:
        """주어진 interval에 대해 Coinbase API가 지원하는 최적의 granularity(초)와 원천 인터벌 반환.
        직접 지원: 1m(60), 5m(300), 15m(900), 1h(3600), 6h(21600), 1d(86400).
        비지원 주기(예: 10m): 상위로 안전하게 정방향 합성(합법)하기 위해 하위 주기 5m(300)을 반환.
        """
        intv = str(interval).lower()
        if intv in cls.GRANULARITY_MAP:
            return cls.GRANULARITY_MAP[intv], ('1h' if intv == '60m' else intv)
        if intv == '10m':
            return 300, '5m'
        return (60, '1m') if 'm' in intv and intv.replace('m', '').isdigit() and int(intv.replace('m', '')) < 5 else (300, '5m')

    @classmethod
    def fetch_candles(cls, product_id: str, granularity: int = 60, start_iso: Optional[str] = None, end_iso: Optional[str] = None) -> List[List[Any]]:
        prod = product_id.upper()
        url = f"{cls.BASE_URL}/products/{prod}/candles?granularity={granularity}"
        if start_iso:
            url += f"&start={urllib.parse.quote(start_iso)}"
        if end_iso:
            url += f"&end={urllib.parse.quote(end_iso)}"
        data = _http_get_json(url)
        if isinstance(data, list):
            return data
        return []


def get_file_last_timestamp(csv_path: str) -> Optional[datetime]:
    """원천 CSV 원장의 끝부분만 역방향 청크로 읽어 마지막 유효 타임스탬프를 반환한다."""
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return None
    try:
        with open(csv_path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, 65536)
            f.seek(size - read_size)
            chunk = f.read().decode('utf-8', errors='ignore')
            lines = [l.strip() for l in chunk.splitlines() if l.strip()]
            for line in reversed(lines):
                parts = line.split(',')
                if not parts:
                    continue
                if not parts[0]:
                    continue
                if any(h in parts[0].lower() for h in ('timestamp', 'date', 'time')):
                    # 헤더 행은 건너뛴다
                    continue
                ts_str = parts[0].strip().strip('"')
                if ts_str.replace('.', '').isdigit():
                    num = float(ts_str)
                    # 자릿수 기반 시간 단위 판별 (ms/us/ns).
                    # data_manager.py download_binance_spot_day()의 검증된 패턴과 동일하게,
                    # 단순 임계값(> 1e11 -> ms) 대신 정수 자릿수로 판별해야 마이크로초(16자리)/
                    # 나노초(19자리 이상) 타임스탬프에서도 연도 폭주(예: 58658-05-13) 없이 안전하다.
                    digit_len = len(str(int(num)))
                    if digit_len == 13:
                        divisor = 1000.0        # milliseconds
                    elif digit_len == 16:
                        divisor = 1000000.0     # microseconds
                    elif digit_len >= 19:
                        divisor = 1000000000.0  # nanoseconds
                    else:
                        divisor = 1.0           # seconds
                    return datetime.fromtimestamp(num / divisor, tz=timezone.utc).astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
                    try:
                        return datetime.strptime(ts_str[:19], fmt)
                    except ValueError:
                        continue
            return None
    except Exception:
        return None


def get_backfill_checkpoint(csv_path: str, local_log: Callable[[str], None] = print) -> Optional[datetime]:
    """`get_file_last_timestamp`의 결측 갭 인식 버전.

    파일의 진짜 마지막 줄이 아니라, 내부에 통째로 빠진 날짜(크립토는 연중무휴이므로
    주말 포함 모든 날짜가 거래일 대상)가 있으면 그 갭 직전 시점을 반환하여
    다음 증분 수집이 갭까지 자연스럽게 백필하도록 한다. (shared.gap_healing 재사용)
    """
    try:
        from shared.data_library.gap_healing import find_gap_checkpoint
    except Exception:
        return get_file_last_timestamp(csv_path)

    checkpoint_str = find_gap_checkpoint(csv_path, market="CRYPTO", local_log=local_log)
    if not checkpoint_str:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(checkpoint_str[:19], fmt)
        except ValueError:
            continue
    return get_file_last_timestamp(csv_path)


_KST = timezone(timedelta(hours=9))
_MAX_PAGES = 100000          # 안전장치(무한 루프 방지) — 구간 끝까지 받는 것이 기본이다


def _kst_ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=_KST).timestamp() * 1000)


def _save_ranges(symbol: str, interval: str, target_csv: str, provider: str, fetch_rows, local_log) -> int:
    """공통 수집 구간(raw_ingest.fetch_ranges)마다 fetch_rows(시작, 끝) 로 받아 공통 절차로 저장한다.
    fetch_rows 는 (KST 시각 문자열 'timestamp' 를 가진 dict 목록, 원천 소진 여부) 를 돌려준다.
    'full'/'head' 구간에서 원천이 더 옛 데이터를 주지 않으면 그 사실을 기록해 다음부터 다시 시도하지 않는다."""
    from shared.data_library import raw_ingest
    tail_from = get_backfill_checkpoint(target_csv, local_log=local_log)
    total = 0
    for start, end, kind in raw_ingest.fetch_ranges(target_csv, tail_from=tail_from):
        label = {"full": "처음부터", "head": "앞쪽 채우기", "tail": "이어받기"}[kind]
        local_log(f"   [{provider}] 📡 {symbol} ({interval}) {label}: {start:%Y-%m-%d %H:%M} ~ {(end or datetime.now()):%Y-%m-%d %H:%M}")
        rows, exhausted = fetch_rows(start, end)
        rows = [r for r in rows if r['timestamp'] >= start.strftime('%Y-%m-%d %H:%M:%S')
                and (end is None or r['timestamp'] < end.strftime('%Y-%m-%d %H:%M:%S'))]
        if rows:
            df_new = pd.DataFrame(rows).drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
            total = _merge_and_save_crypto(df_new, target_csv, local_log, interval=interval, provider=provider.lower())
        if kind in ("full", "head"):
            oldest = datetime.strptime(min(r['timestamp'] for r in rows), '%Y-%m-%d %H:%M:%S') if rows else None
            if exhausted or oldest is None or oldest > start:
                raw_ingest.note_head_floor(target_csv, oldest, log=local_log)
    if not total:
        local_log(f"   [{provider}] ℹ️ {symbol} ({interval}) 신규 수신 데이터 없음.")
    return total


def _upbit_row(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ts = c.get('candle_date_time_kst', '').replace('T', ' ')
    if not ts:
        return None
    return {
        'timestamp': ts[:19],
        'upbit_open': float(c.get('opening_price', 0.0)),
        'upbit_high': float(c.get('high_price', 0.0)),
        'upbit_low': float(c.get('low_price', 0.0)),
        'upbit_close': float(c.get('trade_price', 0.0)),
        'upbit_volume': float(c.get('candle_acc_trade_volume', 0.0)),
        'upbit_trade_price': float(c.get('candle_acc_trade_price', 0.0)),
        'target_open': float(c.get('opening_price', 0.0)),
        'target_high': float(c.get('high_price', 0.0)),
        'target_low': float(c.get('low_price', 0.0)),
        'target_close': float(c.get('trade_price', 0.0)),
        'target_volume': float(c.get('candle_acc_trade_volume', 0.0)),
        'target_trade_price': float(c.get('candle_acc_trade_price', 0.0)),
    }


def sync_crypto_upbit(symbol: str, interval: str, target_csv: str, local_log: Callable[[str], None] = print) -> int:
    """업비트 분봉/일봉 — 최신→과거로 페이지(200개)를 넘기며 구간 시작에 닿을 때까지 받는다."""
    is_daily = str(interval).lower() in ('1d', 'day', 'days')
    unit = UpbitClient.parse_unit(interval) if not is_daily else None

    def fetch_rows(start: datetime, end: Optional[datetime]):
        rows, curr_to, exhausted = [], (end.strftime('%Y-%m-%dT%H:%M:%S') + '+09:00') if end else None, False
        for page in range(_MAX_PAGES):
            raw = UpbitClient.fetch_daily_candles(symbol, curr_to, count=200) if is_daily else UpbitClient.fetch_minute_candles(symbol, unit, curr_to, count=200)
            if not raw:
                exhausted = True
                break
            rows.extend(r for r in (_upbit_row(c) for c in raw) if r)
            oldest = raw[-1].get('candle_date_time_kst', '').replace('T', ' ')[:19]
            if oldest and oldest <= start.strftime('%Y-%m-%d %H:%M:%S'):
                break
            if len(raw) < 200:
                exhausted = True
                break
            curr_to = raw[-1].get('candle_date_time_utc', '')
            if page and page % 50 == 0:
                local_log(f"      … {symbol} {oldest} 까지 {len(rows):,}봉")
            time.sleep(0.11)
        return rows, exhausted

    return _save_ranges(symbol, interval, target_csv, "Upbit", fetch_rows, local_log)


# openInterestHist 가 지원하는 주기 (1m 은 없다 — 더 잦은 주기를 요청하면 빈 응답이 온다)
_OI_PERIODS = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d")


def sync_binance_funding_rate(symbol: str, target_csv: str, local_log: Callable[[str], None] = print) -> int:
    """바이낸스 선물 8시간 펀딩비(fundingRate) 독립 1단계 원천 수집.
    - 2024년 초 또는 기존 원장의 마지막 수집 시점 이후부터 현재까지 증분 수집.
    - 원천 불변 원장: timestamp(KST), funding_rate, mark_price
    """
    os.makedirs(os.path.dirname(os.path.abspath(target_csv)), exist_ok=True)
    existing_df = None
    start_ms = 1704034800000  # 2024-01-01 00:00:00 KST
    if os.path.exists(target_csv) and os.path.getsize(target_csv) > 0:
        try:
            existing_df = pd.read_csv(target_csv)
            if not existing_df.empty and "timestamp" in existing_df.columns:
                last_ts_str = str(existing_df["timestamp"].dropna().iloc[-1])
                dt = datetime.strptime(last_ts_str[:19], "%Y-%m-%d %H:%M:%S")
                start_ms = _kst_ms(dt) + 1000
        except Exception as e:
            local_log(f"   [Binance] ⚠️ 기존 펀딩비 파일 읽기 알림: {e}")

    now_ms = int(time.time() * 1000)
    if start_ms >= now_ms:
        local_log(f"   [Binance] ℹ️ {symbol} 펀딩비 최신 상태 유지 중.")
        return len(existing_df) if existing_df is not None else 0

    local_log(f"   [Binance] 📡 {symbol} 8시간 펀딩비 독립 수집 시작...")
    curr = start_ms
    new_rows = []
    for _ in range(_MAX_PAGES):
        batch = BinanceClient.fetch_funding_rate(symbol, start_time_ms=curr, limit=1000)
        if not batch:
            break
        for item in batch:
            ts_ms = int(item.get("fundingTime", 0))
            if ts_ms == 0:
                continue
            dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(_KST).replace(tzinfo=None)
            new_rows.append({
                "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "funding_rate": float(item.get("fundingRate", 0.0)),
                "mark_price": float(item.get("markPrice", 0.0)) if item.get("markPrice") else np.nan,
            })
        last_ms = int(batch[-1].get("fundingTime", 0))
        if len(batch) < 1000 or last_ms >= now_ms:
            break
        curr = last_ms + 1
        time.sleep(0.15)

    if not new_rows:
        return len(existing_df) if existing_df is not None else 0

    df_new = pd.DataFrame(new_rows)
    if existing_df is not None and not existing_df.empty:
        df_combined = pd.concat([existing_df, df_new], ignore_index=True)
    else:
        df_combined = df_new

    df_combined = df_combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    df_combined.to_csv(target_csv, index=False)
    local_log(f"   [Binance] 💾 {symbol} 펀딩비 원천 저장 완료: {target_csv} (총 {len(df_combined):,}행, 신규 {len(new_rows):,}행)")
    return len(df_combined)


def sync_binance_oi(symbol: str, target_csv: str, local_log: Callable[[str], None] = print) -> int:
    """바이낸스 선물 미결제약정(openInterestHist) 독립 1단계 원천 수집.
    - 바이낸스 REST API 제한: 최근 29일 이내 구간만 조회 가능 (그 이전은 HTTP 400).
    - 원천 불변 원장: timestamp(KST), oi, oi_value
    """
    os.makedirs(os.path.dirname(os.path.abspath(target_csv)), exist_ok=True)
    now_ms = int(time.time() * 1000)
    earliest_ms = now_ms - int(29 * 86400 * 1000)

    existing_df = None
    start_ms = earliest_ms
    if os.path.exists(target_csv) and os.path.getsize(target_csv) > 0:
        try:
            existing_df = pd.read_csv(target_csv)
            if not existing_df.empty and "timestamp" in existing_df.columns:
                last_ts_str = str(existing_df["timestamp"].dropna().iloc[-1])
                dt = datetime.strptime(last_ts_str[:19], "%Y-%m-%d %H:%M:%S")
                last_ms = _kst_ms(dt)
                if last_ms > earliest_ms:
                    start_ms = last_ms + 1000
        except Exception as e:
            local_log(f"   [Binance] ⚠️ 기존 미결제약정 파일 읽기 알림: {e}")

    local_log(f"   [Binance] 📡 {symbol} 미결제약정(OI 5m) 독립 수집 시작...")
    curr = start_ms
    new_rows = []
    for _ in range(_MAX_PAGES):
        batch = BinanceClient.fetch_futures_oi(symbol, period="5m", start_time_ms=curr, limit=500)
        if not batch:
            break
        for item in batch:
            ts_ms = int(item.get("timestamp", 0))
            if ts_ms == 0:
                continue
            dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(_KST).replace(tzinfo=None)
            new_rows.append({
                "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "oi": float(item.get("sumOpenInterest", 0.0)),
                "oi_value": float(item.get("sumOpenInterestValue", 0.0)),
            })
        last_ms = int(batch[-1].get("timestamp", 0))
        if len(batch) < 500 or last_ms >= now_ms:
            break
        curr = last_ms + 1
        time.sleep(0.15)

    if not new_rows:
        local_log(f"   [Binance] ℹ️ {symbol} 미결제약정 신규 데이터 없음.")
        return len(existing_df) if existing_df is not None else 0

    df_new = pd.DataFrame(new_rows)
    if existing_df is not None and not existing_df.empty:
        df_combined = pd.concat([existing_df, df_new], ignore_index=True)
    else:
        df_combined = df_new

    df_combined = df_combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    df_combined.to_csv(target_csv, index=False)
    local_log(f"   [Binance] 💾 {symbol} 미결제약정 원천 저장 완료: {target_csv} (총 {len(df_combined):,}행, 신규 {len(new_rows):,}행)")
    return len(df_combined)


def sync_crypto_binance(symbol: str, interval: str, target_csv: str, is_futures: bool = False, local_log: Callable[[str], None] = print) -> int:
    """바이낸스 캔들 — 구간 시작부터 과거→최신으로 페이지를 넘기며 구간 끝(또는 지금)까지 받는다.
    순수 OHLCV 캔들 원장만 무손실 적재하며, 펀딩비/미결제약정은 독립 원천 파일로 분리 수집한다."""
    api_intv = BinanceClient.normalize_interval(interval)
    if not api_intv:
        local_log(f"   [Binance] ⚠️ 지원하지 않는 인터벌: {interval}")
        return 0
    limit = 1500 if is_futures else 1000

    def fetch_rows(start: datetime, end: Optional[datetime]):
        rows, curr, exhausted = [], _kst_ms(start), False
        end_ms = _kst_ms(end) if end else None
        for page in range(_MAX_PAGES):
            klines = (BinanceClient.fetch_futures_klines(symbol, interval=api_intv, start_time_ms=curr, limit=limit) if is_futures
                      else BinanceClient.fetch_spot_klines(symbol, interval=api_intv, start_time_ms=curr, limit=limit))
            if not klines:
                break
            for k in klines:
                ts_raw = int(k[0])
                digit_len = len(str(ts_raw))
                divisor = 1000000.0 if digit_len == 16 else 1000000000.0 if digit_len >= 19 else 1000.0
                if end_ms and ts_raw / divisor * 1000 >= end_ms:
                    break
                dt = datetime.fromtimestamp(ts_raw / divisor, tz=timezone.utc).astimezone(_KST).replace(tzinfo=None)
                rows.append({
                    'timestamp': dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'open': float(k[1]), 'high': float(k[2]), 'low': float(k[3]), 'close': float(k[4]), 'volume': float(k[5]),
                    'quote_vol': float(k[7]) if len(k) > 7 else 0.0,
                    'trades': float(k[8]) if len(k) > 8 else 0.0,
                    'taker_base': float(k[9]) if len(k) > 9 else 0.0,
                    'taker_quote': float(k[10]) if len(k) > 10 else 0.0,
                })
            last_open = int(klines[-1][0])
            if len(klines) < limit or (end_ms and last_open >= end_ms):
                break
            curr = last_open + 1
            if page and page % 50 == 0:
                local_log(f"      … {symbol} {rows[-1]['timestamp'] if rows else ''} 까지 {len(rows):,}봉")
            time.sleep(0.35 if is_futures else 0.1)      # 선물 klines(limit 1500)는 요청 가중치가 커 분당 한도에 맞춘다
        # 과거→최신으로 받으므로 '원천 소진' = 구간 시작 시각에 받은 첫 봉이 시작보다 늦음(상장 전) — _save_ranges 가 판정
        if rows and api_intv != interval:
            from shared.data_library.candle_resampler import resample_candles
            df = resample_candles(pd.DataFrame(rows), target_interval=interval, source_interval=api_intv)
            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')
            local_log(f"   [Binance] 🔄 {symbol} ({api_intv} ➔ {interval}) 리샘플링 합성 ({len(df):,}행)")
            rows = df.to_dict('records')
        return rows, exhausted

    return _save_ranges(symbol, interval, target_csv, "Binance", fetch_rows, local_log)


def sync_crypto_coinbase(symbol: str, interval: str, target_csv: str, local_log: Callable[[str], None] = print) -> int:
    """코인베이스 캔들 — 공개 API에서 interval에 맞는 최적 granularity로 구간을 청크 단위로 수신한다.
    거래소 직접 미지원 주기(예: 10m)는 하위 주기(5m)로 수신 후 정방향 리샘플링 합성한다."""
    granularity, src_intv = CoinbaseClient.resolve_granularity(interval)
    max_span = timedelta(seconds=granularity * 300)

    def fetch_rows(start: datetime, end: Optional[datetime]):
        now_dt = datetime.now()
        stop = min(end or now_dt, now_dt)
        rows, seen, curr = [], set(), start
        for page in range(_MAX_PAGES):
            if curr >= stop:
                break
            curr_end = min(curr + max_span, stop)
            raw = CoinbaseClient.fetch_candles(symbol, granularity=granularity,
                                               start_iso=curr.replace(tzinfo=_KST).isoformat(), end_iso=curr_end.replace(tzinfo=_KST).isoformat())
            for r in raw or []:
                ts_sec = int(r[0])
                if ts_sec in seen:
                    continue
                seen.add(ts_sec)
                dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc).astimezone(_KST).replace(tzinfo=None)
                rows.append({'timestamp': dt.strftime('%Y-%m-%d %H:%M:%S'), 'close': float(r[4]), 'coinbase_spot_close': float(r[4]),
                             'open': float(r[3]), 'high': float(r[2]), 'low': float(r[1]), 'volume': float(r[5])})
            curr = curr_end
            if page and page % 50 == 0:
                local_log(f"      … {symbol} {curr:%Y-%m-%d} 까지 {len(rows):,}봉")
            time.sleep(0.15)
        if rows and str(interval).lower() != src_intv:
            from shared.data_library.candle_resampler import resample_candles
            df = resample_candles(pd.DataFrame(rows).sort_values('timestamp'), target_interval=interval, source_interval=src_intv)
            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')
            local_log(f"   [Coinbase] 🔄 {symbol} ({src_intv} ➔ {interval}) 리샘플링 합성 ({len(df):,}행)")
            rows = df.to_dict('records')
        return rows, False

    return _save_ranges(symbol, interval, target_csv, "Coinbase", fetch_rows, local_log)


def _merge_and_save_crypto(df_new: pd.DataFrame, target_csv: str, local_log: Callable[[str], None] = print,
                           interval: Optional[str] = None, provider: str = "crypto") -> int:
    """모든 원천 공통 저장 절차(shared.data_library.raw_ingest → raw_day_store.ingest_file)로 저장한다. 반환: 저장 후 원천의 총 행 수(실패 시 0).

    df_new 의 timestamp 는 KST 시각 문자열이다. 기존 원천이 epoch(바이낸스 옛 형식)이면 그 형식에 맞춰 **UTC epoch(ms)** 와
    UTC 'datetime' 으로 바꿔 쓴다 — 하위 계층(normalized_builder)은 epoch 를 UTC 로 읽고 +9h 해 KST 로 쓴다.
    (옛 코드는 KST 시각을 그대로 epoch 로 바꿔 9시간 밀린 값을 저장했다.)
    """
    from shared.data_library.raw_ingest import ingest_dataframe
    if df_new is None or df_new.empty:
        return 0
    df = df_new.copy()
    if os.path.exists(target_csv) and os.path.getsize(target_csv) > 0:
        try:
            head = pd.read_csv(target_csv, nrows=50)
            is_old_numeric = pd.to_numeric(head["timestamp"], errors="coerce").notna().mean() > 0.5
        except Exception:
            is_old_numeric = False
        if is_old_numeric:
            kst = pd.to_datetime(df["timestamp"], errors="coerce").dt.tz_localize("Asia/Seoul")
            utc = kst.dt.tz_convert("UTC")
            df["timestamp"] = (utc.dt.tz_localize(None).astype("datetime64[ms]").astype("int64")).astype(float)
            df["datetime"] = utc.dt.strftime("%Y-%m-%d %H:%M:%S")
    rep = ingest_dataframe(df, target_csv, provider=provider, market="CRYPTO", interval=interval, log=local_log)
    if rep is None:
        local_log(f"   ❌ [Crypto] 저장 실패 — 기존 원천은 그대로입니다: {target_csv}")
        return 0
    led = rds_ledger(target_csv)
    return int(led.get("rows", 0)) if led else 0


def rds_ledger(path: str) -> dict:
    try:
        from CAiModelBaker.core import raw_day_store as rds
    except ImportError:
        from core import raw_day_store as rds
    return rds.load_ledger(path) or {}


def sync_single_crypto_asset(symbol: str, info: Dict[str, Any], target_scale: Optional[str] = None, local_log: Callable[[str], None] = print) -> bool:
    prov = str(info.get('primary_source', '')).upper()
    cat = str(info.get('category', '')).upper()
    is_futures = (cat == 'CRYPTO_FUTURES') or ('PERP' in symbol)
    raw_dir = data_vault_path('raw', 'crypto', prov.lower())
    os.makedirs(raw_dir, exist_ok=True)

    if target_scale:
        raw_sources = [str(target_scale).strip().upper()]
    else:
        configured = info.get('ingestion_sources')
        raw_sources = [str(s).strip().upper() for s in configured] if configured else ['1M', '1D']

    # 1단계 원천 분리: 선물 자산인 경우 펀딩비와 미결제약정을 전용 1단계 원장 파일로 독립 수집
    if is_futures and prov == 'BINANCE' and (not target_scale or target_scale.upper() in ('FR', 'OI', 'FUNDING_RATE', 'OPEN_INTEREST', 'ALL')):
        fr_csv = os.path.join(raw_dir, f"{symbol}_funding_rate.csv")
        oi_csv = os.path.join(raw_dir, f"{symbol}_oi.csv")
        if not target_scale or target_scale.upper() in ('FR', 'FUNDING_RATE', 'ALL'):
            try:
                sync_binance_funding_rate(symbol, fr_csv, local_log=local_log)
                ManifestManager.generate_and_save_manifest(
                    data_file_path=fr_csv,
                    data_tier='raw',
                    provider='binance',
                    notes=f"{symbol} 8h Funding Rate Ledger",
                    market='CRYPTO',
                )
            except Exception as fe:
                local_log(f"   [Binance] ⚠️ 펀딩비 독립 수집 알림: {fe}")

        if not target_scale or target_scale.upper() in ('OI', 'OPEN_INTEREST', 'ALL'):
            try:
                sync_binance_oi(symbol, oi_csv, local_log=local_log)
                ManifestManager.generate_and_save_manifest(
                    data_file_path=oi_csv,
                    data_tier='raw',
                    provider='binance',
                    notes=f"{symbol} Open Interest History",
                    market='CRYPTO',
                )
            except Exception as oe:
                local_log(f"   [Binance] ⚠️ 미결제약정 독립 수집 알림: {oe}")

    # 스케일 변환: '1M' -> '1m', '1D' -> '1d', '10M' -> '10m', '15M' -> '15m'
    intervals_to_fetch = []
    for src in raw_sources:
        s_lower = src.lower()
        if s_lower in ('1m', '1d', '5m', '10m', '15m', '1h'):
            intervals_to_fetch.append(s_lower)
        elif s_lower in ('fr', 'oi', 'funding_rate', 'open_interest'):
            pass  # 이미 상단에서 독립 수집 처리됨
        else:
            intervals_to_fetch.append(s_lower)

    success_any = False
    for intv in intervals_to_fetch:
        target_csv = os.path.join(raw_dir, f"{symbol}_{intv}.csv")

        rows = 0
        if prov == 'UPBIT':
            rows = sync_crypto_upbit(symbol, intv, target_csv, local_log=local_log)
        elif prov == 'BINANCE':
            rows = sync_crypto_binance(symbol, intv, target_csv, is_futures=is_futures, local_log=local_log)
        elif prov == 'COINBASE':
            rows = sync_crypto_coinbase(symbol, intv, target_csv, local_log=local_log)
        else:
            local_log(f"   ⚠️ 미지원 가상자산 프로바이더: {prov} ({symbol})")
            continue

        if not os.path.exists(target_csv):
            continue
        if not os.path.getsize(target_csv) > 0:
            continue

        success_any = True

        try:
            ManifestManager.generate_and_save_manifest(
                data_file_path=target_csv,
                data_tier='raw',
                provider=prov.lower(),
                notes=info.get('name', symbol),
                market='CRYPTO',
            )
        except Exception as me:
            local_log(f"   ⚠️ 매니페스트 생성 알림: {me}")

        try:
            from CAiModelBaker.core.normalized_builder import build_normalized_crypto
            build_normalized_crypto(symbol=symbol, interval=intv, raw_candle_path=target_csv, local_log=local_log)
        except Exception as ne:
            local_log(f"   ⚠️ 2단계 정규화 Parquet 빌드 알림: {ne}")

    return success_any


def sync_all_active_crypto(symbols: Optional[List[str]] = None, local_log: Callable[[str], None] = print, progress_callback: Optional[Callable[[int, str], None]] = None) -> Dict[str, Any]:
    mappings = SourceMappingManager.load_all_mappings()

    if symbols:
        crypto_assets = {s: mappings[s] for s in symbols if s in mappings and mappings[s].get('is_active', True)}
    else:
        crypto_assets = {
            s: info for s, info in mappings.items()
            if info.get('is_active', True) and (
                info.get('category') in ('CRYPTO_SPOT', 'CRYPTO_FUTURES')
                or info.get('primary_source') in ('UPBIT', 'BINANCE', 'COINBASE')
                or is_crypto_symbol(s)
            )
        }

    if not crypto_assets:
        local_log('ℹ️ [Crypto Sync] 활성화된 가상자산이 없습니다.')
        return {'success': [], 'failed': [], 'total': 0}

    local_log(f"🚀 [Crypto Sync] 총 {len(crypto_assets)}개 가상자산 일괄 증분 수집 가동: {', '.join(crypto_assets.keys())}")
    total = len(crypto_assets)
    success_list = []
    failed_list = []

    # 거래소(출처)끼리만 병렬 — 같은 거래소 안은 순차(호출 제한 준수). 동시 적재는 거래소 수(≤3)로 상한.
    import threading
    from collections import OrderedDict
    from concurrent.futures import ThreadPoolExecutor
    groups: "OrderedDict[str, list]" = OrderedDict()
    for sym, info in crypto_assets.items():
        groups.setdefault(str(info.get('primary_source', '')).upper(), []).append((sym, info))
    lock = threading.Lock()
    done = [0]

    def _run_group(items):
        for sym, info in items:
            t0 = time.perf_counter()
            local_log(f"\n🪙 {info.get('name', sym)} ({sym}, 출처: {info.get('primary_source')}) 수집 중...")
            try:
                ok = sync_single_crypto_asset(sym, info, local_log=local_log)
            except Exception as e:
                local_log(f"   ❌ {sym} 수집 중 예외 발생: {e}")
                ok = False
            local_log(f"   ⏱️ [{sym}] 가상자산 수집 {time.perf_counter() - t0:.1f}초")
            with lock:
                (success_list if ok else failed_list).append(sym)
                done[0] += 1
                n = done[0]
            if progress_callback:
                msg = f"[{n}/{total}] {info.get('name', sym)} ({sym}) 동기화 완료"
                try:
                    progress_callback(int(n / total * 100), msg)
                except TypeError:
                    progress_callback(int(n / total * 100))

    with ThreadPoolExecutor(max_workers=min(3, len(groups))) as ex:
        list(ex.map(_run_group, groups.values()))

    local_log(f"\n🎉 [Crypto Sync] 가상자산 전수 동기화 완료! (성공 {len(success_list)}/{total}, 실패 {len(failed_list)})")
    return {'success': success_list, 'failed': failed_list, 'total': total}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='가상자산 통합 증분 수집기')
    parser.add_argument('--symbol', type=str, default='', help='특정 심볼만 수집 (예: KRW-BTC, BTCUSDT)')
    parser.add_argument('--scale', type=str, default='', help='수집할 특정 스케일 (예: 1M, 1D)')
    parser.add_argument('--all', action='store_true', help='모든 활성 가상자산 일괄 수집')
    args = parser.parse_args()

    if args.all or not args.symbol:
        sync_all_active_crypto(local_log=print)
    else:
        mappings = SourceMappingManager.load_all_mappings()
        info = mappings.get(args.symbol) or {
            'symbol': args.symbol,
            'primary_source': 'UPBIT' if args.symbol.startswith('KRW-') else 'BINANCE',
            'category': 'CRYPTO_SPOT',
            'ingestion_sources': ['1M', '1D'],
        }
        sync_single_crypto_asset(args.symbol, info, target_scale=args.scale or None, local_log=print)
