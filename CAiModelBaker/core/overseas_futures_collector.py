# -*- coding: utf-8 -*-
"""
CAiModelBaker - Overseas Futures Collector
미국 CME 나스닥 100(NQ) 및 S&P 500(ES) 선물 데이터 수집기
- 틱/분/시/일/주/월 다중 타임프레임 지원
- KST(+09:00) 타임스탬프 변환 및 DataVault/raw/overseas/futures_{주기}/ 공통 절차 적재 (raw_day_store.ingest_file)
"""

import os
import re
import sys
import time
import json
import argparse
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import numpy as np

KST = timezone(timedelta(hours=9))

# shared.source_mapping_manager (UI 설정 저장소) 임포트를 위한 저장소 루트 등록
_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_CORE_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

def _load_fallback_futures_symbols() -> dict:
    """원장(registry)에서 yahoo_ticker가 등록된 GLOBAL_FUTURES 심볼 매핑을 구성.
    원장 파일은 반드시 config_manager 를 경유한다(직접 open 금지 — shared/tests/test_symbol_ledger.py 가드)."""
    try:
        from shared.config.config_manager import load_symbols_registry
        reg = load_symbols_registry()
        return {
            sym: info["yahoo_ticker"]
            for sym, info in reg.items()
            if isinstance(info, dict) and info.get("market_type") == "GLOBAL_FUTURES" and info.get("yahoo_ticker")
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"[Futures] 원장 로드 실패 — 해외선물 내장 심볼 매핑 없음: {e}"
        )
        return {}


_FALLBACK_FUTURES_SYMBOLS = _load_fallback_futures_symbols()
# 하위 호환용 별칭 (기존 코드/테스트가 FUTURES_SYMBOLS를 직접 참조하는 경우 대비)
FUTURES_SYMBOLS = _FALLBACK_FUTURES_SYMBOLS

# 인터벌 매핑 (Yahoo Chart v8 규격)
# 주의: CTradeSystem 표준에서 1M은 1분봉(1-minute)이며, 월봉은 1mo이다.
INTERVAL_MAP = {
    "1m": "1m",
    "1M": "1m",
    "2m": "2m",
    "5m": "5m",
    "5M": "5m",
    "15m": "15m",
    "15M": "15m",
    "30m": "30m",
    "30M": "30m",
    "60m": "60m",
    "60M": "60m",
    "1h": "1h",
    "1H": "1h",
    "1d": "1d",
    "1D": "1d",
    "1w": "1wk",
    "1W": "1wk",
    "1mo": "1mo",
    "1MO": "1mo"
}

# 인터벌별 Yahoo 조회 range 기본값 (interval 키는 INTERVAL_MAP과 동일 체계)
_RANGE_BY_INTERVAL = {
    "1m": "7d", "1M": "7d",
    "2m": "60d", "5m": "60d", "5M": "60d", "15m": "60d", "15M": "60d", "30m": "60d", "30M": "60d",
    "60m": "730d", "60M": "730d", "1h": "730d", "1H": "730d",
    "1d": "10y", "1D": "10y",
    "1w": "10y", "1W": "10y",
    "1mo": "10y", "1MO": "10y",
}
# 자산의 ingestion_sources/target_scales가 UI에 전혀 설정되지 않았을 때만 쓰는 최후 폴백
_DEFAULT_FUTURES_INTERVALS = ["1m", "1d"]


def _load_source_mappings() -> dict:
    """UI(웹 대시보드)가 편집하는 source_mappings.json을 로드. 실패 시 빈 dict."""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        return SourceMappingManager.load_all_mappings()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"[Futures] source_mappings 로드 실패 — UI 설정 없이 레지스트리 기본값으로 동작: {e}"
        )
        return {}


def resolve_yahoo_symbol(symbol: str, mappings: dict = None) -> str:
    """
    UI에 등록된 live_connection.symbol을 최우선으로 사용한다.
    (대시보드에서 실시간 접속 정보를 갱신하면 실제 수집 티커도 즉시 반영되어야 함)
    등록이 없을 때만 내장 폴백 매핑을 사용한다.
    """
    sym_up = symbol.upper()
    mappings = mappings if mappings is not None else _load_source_mappings()
    info = mappings.get(sym_up)
    if info:
        cfg_symbol = (info.get("live_connection") or {}).get("symbol")
        if cfg_symbol:
            return cfg_symbol
    return _FALLBACK_FUTURES_SYMBOLS.get(sym_up, symbol)


def discover_active_futures_symbols(mappings: dict = None) -> list:
    """
    UI에서 활성화된 OVERSEAS_FUTURES 자산 목록을 동적으로 조회 (하드코딩 배제).
    설정을 정상적으로 읽었다면 그 결과를 그대로 신뢰한다 — 활성 자산이 0개라도
    (예: NQ/ES가 UI에서 비활성/아카이브 처리된 경우) 임의로 되살리지 않는다.
    설정 자체를 읽지 못했을 때(예: shared 패키지 접근 불가한 독립 실행 환경)만
    최후 폴백으로 내장 기본값을 사용한다.
    """
    mappings = mappings if mappings is not None else _load_source_mappings()
    if not mappings:
        return list(_FALLBACK_FUTURES_SYMBOLS.keys())[:2]
    return [
        sym for sym, info in mappings.items()
        if info.get("category") == "OVERSEAS_FUTURES" and info.get("is_active", True)
    ]


def _target_intervals_for(symbol: str, mappings: dict = None) -> list:
    """UI에 설정된 ingestion_sources 또는 target_scales/canonical_intervals을 수집 스케줄로 사용"""
    mappings = mappings if mappings is not None else _load_source_mappings()
    info = mappings.get(symbol.upper(), {})
    # 1순위: 원천 수집 설정인 ingestion_sources 확인
    configured = info.get("ingestion_sources") or info.get("target_scales") or info.get("canonical_intervals") or []
    intervals = []
    for iv in configured:
        iv_s = str(iv).strip()
        if iv_s in INTERVAL_MAP:
            intervals.append(INTERVAL_MAP[iv_s])
        elif iv_s.lower() in INTERVAL_MAP:
            intervals.append(INTERVAL_MAP[iv_s.lower()])
    # 중복 제거 및 기본값 폴백
    seen = set()
    dedup = []
    for iv in intervals:
        if iv not in seen:
            seen.add(iv)
            dedup.append(iv)
    return dedup or list(_DEFAULT_FUTURES_INTERVALS)


def get_save_directory(baker_root: str = None) -> str:
    """레거시 사본 폴더(data/historical/overseas_futures). 원천의 원본은 DataVault 이고, 동기화 뒤 여기로 사본을 둔다(옛 코드 호환)."""
    if baker_root is None:
        baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_dir = os.path.join(baker_root, "data", "historical", "overseas_futures")
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def interval_label(interval: str) -> str:
    """파일/폴더 이름용 주기 라벨 — 월봉 '1mo'/'1MO'는 '1mo', 분봉 '1m'/'1M'은 '1m'으로 정규화."""
    iv = str(interval).strip()
    if iv in ("1mo", "1MO"):
        return "1mo"
    return iv.lower()


def raw_target(kind: str, symbol: str, interval: str) -> str:
    """원천 파일 경로 — kind='overseas' → DataVault/raw/overseas/futures_{주기}/, 'us_stock' → DataVault/raw/yahoo/candle_{주기}/
    (미국 주식·ETF 의 출처는 Yahoo 다. 2026-10-15 이전에는 raw/kis 에 두었으나 출처와 폴더가 달라 옮겼다.)"""
    from shared.data_library.paths import data_vault_path
    lab = interval_label(interval)
    if kind == "overseas":
        return data_vault_path("raw", "overseas", f"futures_{lab}", f"{symbol}_{lab}.csv")
    return data_vault_path("raw", "yahoo", f"candle_{lab}", f"{symbol}_{lab}.csv")


def _ingest(df: pd.DataFrame, target: str, interval: str, label: str, requested_after, source_exhausted: bool, local_log=print):
    """모든 원천 공통 저장 절차(shared.data_library.raw_ingest → raw_day_store.ingest_file)로 저장한다."""
    from shared.data_library.raw_ingest import ingest_dataframe
    from shared.data_library import raw_ingest
    need_head = any(k in ("full", "head") for _, _, k in raw_ingest.fetch_ranges(target))
    rep = ingest_dataframe(df, target, provider="yahoo", market="US", interval=interval,
                           requested_after=requested_after, source_exhausted=source_exhausted, log=local_log)
    if rep is None:
        local_log(f"  ❌ [{label}] 저장 실패 — 기존 원천은 그대로입니다")
        return ""
    if need_head and source_exhausted and not df.empty:
        # Yahoo 는 주기별 최대 조회 창(예: 1분봉 7일)만 준다 — 그보다 앞(수집 시작~)은 원천에 없다
        raw_ingest.note_head_floor(target, raw_ingest._kst(str(df["timestamp"].iloc[0])), log=local_log)
    return target


def _interval_delta(interval: str):
    """Yahoo interval 문자열 -> Timedelta (알 수 없으면 None)"""
    iv = str(interval).strip().lower()
    m = re.fullmatch(r"(\d+)(m|h|d|wk)", iv)
    if not m:
        return None
    n = int(m.group(1))
    return {"m": pd.Timedelta(minutes=n), "h": pd.Timedelta(hours=n),
            "d": pd.Timedelta(days=n), "wk": pd.Timedelta(weeks=n)}[m.group(2)]


def _drop_incomplete_last_bar(df: pd.DataFrame, interval: str, now_kst=None) -> pd.DataFrame:
    """진행 중(미완성) 마지막 봉 제거: 봉 시작 + interval > 현재(KST) 인 행"""
    if df is None or df.empty:
        return df
    delta = _interval_delta(interval)
    ts = pd.to_datetime(df["timestamp"], errors="coerce")
    if now_kst is None:
        now_kst = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None)
    if delta is None:
        return df.iloc[:-1].reset_index(drop=True)
    keep = (ts + delta) <= now_kst
    return df[keep].reset_index(drop=True)


def fetch_yahoo_chart_data(symbol: str, interval: str = "1m", range_str: str = "7d", local_log=print) -> pd.DataFrame:
    """
    Yahoo Finance v8 Chart API를 통해 해외 선물 시세 데이터 수집
    """
    clean_sym = resolve_yahoo_symbol(symbol)
    yahoo_interval = INTERVAL_MAP.get(interval, "1m")
    
    # 1m는 최대 7d, 2m/5m/15m/30m은 최대 60d, 60m/1h는 최대 730d, 1d/1w/1mo는 최대 10y까지 가능
    if interval in ["1m"] and range_str not in ["1d", "5d", "7d"]:
        range_str = "7d"
    elif interval in ["2m", "5m", "15m", "30m"] and range_str in ["1y", "2y", "5y", "10y", "max"]:
        range_str = "60d"
    elif interval in ["60m", "1h"] and range_str in ["5y", "10y", "max"]:
        range_str = "730d"
    elif interval in ["1d", "1D", "1w", "1W", "1M", "1mo"] and range_str in ["max"]:
        range_str = "10y"
        
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_sym}?interval={yahoo_interval}&range={range_str}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    local_log(f"  🌐 [Futures] {clean_sym} ({interval}, {range_str}) 다운로드 요청: {url}")
    
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("chart", {}).get("result")
                if not result:
                    local_log(f"  ⚠️ [Futures] 데이터 결과 없음: {data}")
                    return pd.DataFrame()
                
                res = result[0]
                timestamps = res.get("timestamp", [])
                indicators = res.get("indicators", {})
                quote = indicators.get("quote", [{}])[0]
                
                opens = quote.get("open", [])
                highs = quote.get("high", [])
                lows = quote.get("low", [])
                closes = quote.get("close", [])
                volumes = quote.get("volume", [])
                
                if not timestamps or not closes:
                    local_log("  ⚠️ [Futures] 타임스탬프 또는 종가 데이터 부재")
                    return pd.DataFrame()
                
                # UTC timestamp -> KST 문자열 변환
                records = []
                skipped_incomplete = 0
                for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes):
                    if c is None or (isinstance(c, float) and np.isnan(c)):
                        continue
                    if o is None or h is None or l is None:
                        skipped_incomplete += 1
                        continue
                    dt_kst = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(KST)
                    records.append({
                        "timestamp": dt_kst.strftime("%Y-%m-%d %H:%M:%S"),
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": float(v) if v is not None else 0.0
                    })
                if skipped_incomplete:
                    local_log(f"  ⚠️ [Futures] {clean_sym} ({interval}): O/H/L 누락 캔들 {skipped_incomplete}개 제외됨 (Yahoo Finance 불완전 응답)")
                    
                df = pd.DataFrame(records)
                if not df.empty:
                    df = df.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)
                    # 진행 중(미완성) 마지막 봉이 아카이브에 영구 고정되지 않도록 제외
                    df = _drop_incomplete_last_bar(df, interval)
                    if df.empty:
                        return df
                    local_log(f"  ✅ [Futures] {clean_sym} ({interval}) 총 {len(df):,}개 캔들 수집 완료 ({df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]})")
                return df
            else:
                local_log(f"  ⚠️ [Futures] HTTP 오류 {resp.status_code} (재시도 {attempt+1}/3)")
                time.sleep(1.0)
        except requests.exceptions.ConnectionError as ce:
            local_log(f"  ⚠️ [Futures] 네트워크/DNS 연결 제한 ({type(ce).__name__}). 기존 아카이브를 안전하게 유지합니다.")
            break
        except Exception as e:
            local_log(f"  ⚠️ [Futures] 요청 실패: {e} (재시도 {attempt+1}/3)")
            time.sleep(1.0)
            
    return pd.DataFrame()


# Yahoo Finance chart API가 인터벌별로 실제 제공하는 최대 과거 조회 범위(일).
# fetch_yahoo_chart_data()의 range_str 상한 강제 로직(1m→7d, 5m/15m/30m→60d, 60m/1h→730d, 1d 이상→10y)과 동일한 값이다.
_YAHOO_MAX_LOOKBACK_DAYS = {
    "1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60,
    "60m": 730, "1h": 730,
    "1d": 3650, "1D": 3650, "1w": 3650, "1W": 3650, "1M": 3650, "1mo": 3650,
}


def download_and_save_futures(symbol: str, interval: str = "1m", range_str: str = None, baker_root: str = None, local_log=print) -> str:
    """해외 선물 데이터를 받아 원천(DataVault/raw/overseas/futures_{주기})에 공통 절차로 저장한다.
    Yahoo 는 요청한 조회 창 전체를 주므로(source_exhausted), 기존 마지막 기록이 창 밖이면 그 사이는 영구 결손으로 기록된다."""
    sym_clean = symbol.upper().replace("=F", "")
    target = raw_target("overseas", sym_clean, interval)
    before = _last_ts(target)
    if range_str is None:
        range_str = _RANGE_BY_INTERVAL.get(interval, _RANGE_BY_INTERVAL.get(interval.lower(), "7d"))
    df = fetch_yahoo_chart_data(symbol, interval, range_str, local_log=local_log)
    if df.empty:
        local_log(f"  ❌ [Futures] {symbol} ({interval}) 데이터 다운로드 실패")
        return ""
    out = _ingest(df, target, interval, f"Futures/{sym_clean}", before, True, local_log)
    if out:
        local_log(f"  💾 [Futures] {sym_clean} ({interval}) 원천 저장: {out} (수신 {len(df):,}행, {df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]})")
    return out


def _last_ts(path: str):
    from shared.data_library.raw_ingest import last_timestamp
    return last_timestamp(path)


def download_and_save_us_stock(symbol: str, interval: str = "1m", range_str: str = None, baker_root: str = None, local_log=print) -> str:
    """
    미국 개별 주식/ETF (GOOGL, NVDA, TSLA, AAPL, QQQ 등) 시세를 받아 원천(DataVault/raw/yahoo/candle_{주기})에 공통 절차로 저장.
    일봉은 전 종목 수집(shared.data_library.us_equity)과 같은 경로·같은 파일이다 — 무수정가(분할 되돌림)·기업행위 분리·응답 원본 보존.
    분봉은 Yahoo 조회 창(1m 7일·5m 60일)만 주며 분할 수정값 그대로다(창 안에 분할이 있으면 '확인 필요'로 드러난다).
    """
    sym_clean = symbol.upper().strip()
    if interval_label(interval) == "1d":
        from shared.data_library import raw_ingest
        from shared.data_library.us_equity import bronze
        from shared.data_library.us_equity.yahoo import PERIOD1_ALL, fetch_chart
        target = bronze.candle_path(sym_clean)
        last = raw_ingest._kst(raw_ingest.last_timestamp(target))
        period1 = PERIOD1_ALL if last is None else int((last - timedelta(days=14)).timestamp())
        raw, why = fetch_chart(sym_clean, period1=period1, log=local_log)
        got = time.time()
        if raw is None:
            local_log(f"  ❌ [US Market] {sym_clean} (1d) 데이터 다운로드 실패: {why}")
            return ""
        bronze.save_response(sym_clean, raw, datetime.now().strftime("%Y%m%d"))
        rep = bronze.ingest_response(sym_clean, raw, full_history=last is None, fetched_at=got, log=local_log)
        if rep is None:
            local_log(f"  ❌ [US Market] {sym_clean} (1d) 저장 실패 — 기존 원천은 그대로입니다")
            return ""
        local_log(f"  💾 [US Market] {sym_clean} (1d) 원천 저장: {target} (수신 {rep['rows']:,}행, 추가 {rep['added_rows']:,})")
        return target

    if range_str is None:
        range_str = _RANGE_BY_INTERVAL.get(interval, _RANGE_BY_INTERVAL.get(interval.lower(), "7d"))
    target = raw_target("us_stock", sym_clean, interval)
    before = _last_ts(target)
    df = fetch_yahoo_chart_data(symbol, interval, range_str, local_log=local_log)
    if df.empty:
        local_log(f"  ❌ [US Market] {symbol} ({interval}) 데이터 다운로드 실패")
        return ""
    std_df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    out = _ingest(std_df, target, interval, f"US/{sym_clean}", before, True, local_log)
    if out:
        local_log(f"  💾 [US Market] {sym_clean} ({interval}) 원천 저장: {out} (수신 {len(std_df):,}행)")
    return out


def download_us_market_data(symbol: str, interval: str = "1m", range_str: str = None, baker_root: str = None, local_log=print) -> pd.DataFrame:
    """
    미국 개별 주식/ETF 데이터 다운로드 및 원천 캔들 저장 메인 진입점
    """
    sym_clean = symbol.upper().strip()
    local_log(f"🚀 [US Market] {sym_clean} 시세 데이터 수집 파이프라인 가동 (단위: {interval})")
    
    out_file = download_and_save_us_stock(sym_clean, interval=interval, range_str=range_str, baker_root=baker_root, local_log=local_log)
    if out_file and os.path.exists(out_file):
        return pd.read_csv(out_file)
    return pd.DataFrame()


def import_external_futures_csv(source_csv_path: str, symbol: str, interval: str = "1m", baker_root: str = None, local_log=print) -> str:
    """
    외부 과거 아카이브 CSV 파일을 표준 포맷으로 변환하여 원천(DataVault/raw/overseas)에 공통 절차로 적재
    """
    if not os.path.exists(source_csv_path):
        local_log(f"  ❌ [Futures Import] 원본 파일 없음: {source_csv_path}")
        return ""
        
    local_log(f"  📥 [Futures Import] 외부 CSV 임포트 시작: {source_csv_path} -> {symbol}_{interval}")
    try:
        raw_df = pd.read_csv(source_csv_path)
        # 컬럼 소문자 정규화
        col_map = {c: c.strip().lower() for c in raw_df.columns}
        raw_df = raw_df.rename(columns=col_map)
        
        # 타임스탬프 컬럼 탐색
        ts_col = next((c for c in ['timestamp', 'datetime', 'date', 'time', 'dt'] if c in raw_df.columns), None)
        if not ts_col:
            local_log(f"  ❌ [Futures Import] 타임스탬프 컬럼을 찾을 수 없습니다: {list(raw_df.columns)}")
            return ""
            
        try:
            _ts = pd.to_datetime(raw_df[ts_col])
        except (ValueError, TypeError):
            _ts = pd.to_datetime(raw_df[ts_col], utc=True)
        if _ts.dtype == object:  # 혼합 오프셋
            _ts = pd.to_datetime(raw_df[ts_col], utc=True)
        if getattr(_ts.dt, 'tz', None) is not None:
            # 오프셋 포함 입력은 KST 로 변환 후 tz 제거 (naive 는 기존대로 KST 로 간주)
            _ts = _ts.dt.tz_convert('Asia/Seoul').dt.tz_localize(None)
        raw_df['timestamp'] = _ts.dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # 필수 컬럼 구성
        for c in ['open', 'high', 'low', 'close']:
            if c not in raw_df.columns and 'close' in raw_df.columns:
                raw_df[c] = raw_df['close']
        if 'volume' not in raw_df.columns:
            raw_df['volume'] = 0.0
            
        std_df = raw_df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].dropna(subset=['close'])
        std_df = std_df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
        
        sym_clean = symbol.upper().replace("=F", "")
        out_file = _ingest(std_df, raw_target("overseas", sym_clean, interval), interval, f"Futures Import/{sym_clean}", None, False, local_log)
        if out_file:
            local_log(f"  🎉 [Futures Import] 외부 데이터 임포트 완료: {out_file} (수신 {len(std_df):,}행)")
        return out_file
    except Exception as e:
        local_log(f"  ❌ [Futures Import] 외부 CSV 처리 실패: {e}")
        return ""


def download_all_major_futures(symbols: list = None, baker_root: str = None, local_log=print):
    """
    해외 선물 다층 타임프레임 일괄 다운로드.
    - symbols 를 지정하지 않으면 UI(source_mappings.json)에서 category=OVERSEAS_FUTURES 이고
      is_active 인 자산을 동적으로 조회한다 (호출부가 활성 목록을 이미 필터링해 넘기는 것을 권장).
    - 각 자산의 수집 인터벌도 UI의 target_scales/canonical_intervals을 그대로 따른다.
      (설정이 없는 자산만 1m/5m/60m/1d 4계층 기본값으로 폴백)
    """
    mappings = _load_source_mappings()
    if symbols is None:
        symbols = discover_active_futures_symbols(mappings)

    local_log(f"\n🚀 [Futures] 해외 선물({', '.join(symbols)}) 다층 타임프레임 일괄 수집 시작...")
    results = {}
    for sym in symbols:
        intervals = _target_intervals_for(sym, mappings)
        local_log(f"\n📈 [Futures] === {sym} 선물 다계층 데이터 수집 (intervals={intervals}) ===")
        for iv in intervals:
            range_str = _RANGE_BY_INTERVAL.get(iv, "7d")
            out_file = download_and_save_futures(sym, interval=iv, range_str=range_str, baker_root=baker_root, local_log=local_log)
            results[f"{sym}_{iv}"] = out_file

    local_log("\n🏁 [Futures] 해외 선물 다층 타임프레임 일괄 수집 완료.")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Overseas Futures Data Collector (NQ, ES)")
    parser.add_argument("--symbol", type=str, required=True, help="Futures Symbol (예: NQ, ES, YM)")
    parser.add_argument("--interval", type=str, default="1m", help="Candle interval (1m, 5m, 60m, 1d, 1w, 1M)")
    parser.add_argument("--range", type=str, default="7d", help="Data range (1d, 5d, 7d, 60d, 730d, 1y, 5y, 10y)")
    parser.add_argument("--all", action="store_true", help="Download all major futures (NQ, ES 1m + 5m + 60m + 1d)")
    parser.add_argument("--import_csv", type=str, default="", help="Path to external CSV to import")
    
    args = parser.parse_args()
    if args.import_csv:
        import_external_futures_csv(args.import_csv, symbol=args.symbol, interval=args.interval)
    elif args.all:
        download_all_major_futures()
    else:
        download_and_save_futures(args.symbol, args.interval, args.range)
