"""
CTradeSystem Shared - Market Calendar Module (마켓 캘린더 엔진)

한국거래소(KRX) 및 미국(CME/NYSE) 시장의 실제 증시 휴장일과 영업일(Trading Days)을
자동 감지·동기화하고 로컬 캐시(JSON)를 통해 중앙 집중 관리하는 엔진입니다.

주요 기능:
  - get_market_holidays(market, force_refresh=False) -> Set[str]
  - is_trading_day(dt_or_str, market) -> bool
  - get_trading_days(start_date, end_date, market) -> List[str]
  - count_trading_days(start_date, end_date, market) -> int
  - snap_to_trading_day(dt_or_str, market, direction) -> Optional[str]
  - prev_trading_day(dt_or_str, market) / next_trading_day(dt_or_str, market) -> Optional[str]
  - is_continuous_market(market) -> bool   (크립토 등 연중무휴 시장 판정)
  - sync_market_holidays(market) -> Set[str] (Yahoo Finance 실거래 일봉 기반 자동 스캔)

[P-1] 시장 구분 정책 (2026-09-15)
  · KRX / US  : 주말 + 공휴일 제외 (거래일 = 평일 − 휴장일)
  · CRYPTO    : **연중무휴** → 휴장일 개념이 없다 (주말 포함 365일 전부 거래일)
    종전에는 크립토 요청이 `_normalize_market` 에서 KRX 로 폴백되어
    주말/한국 공휴일이 크립토 창 산정에 잘못 적용될 수 있었다.
"""
import os
import json
import logging
import urllib.request
from datetime import datetime, date, timedelta
from typing import Set, List, Union, Optional

logger = logging.getLogger(__name__)

# 연중무휴 시장 (휴장일 없음) — 주말/공휴일 스킵 금지
_CONTINUOUS_MARKETS: Set[str] = {"CRYPTO"}

from shared.data_library.paths import data_vault_config_path

# 공통 설정 디렉토리 및 캐시 경로
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_LEGACY_CACHE_FILE = os.path.join(os.path.dirname(_CURRENT_DIR), "config", "market_holidays.json")
_VAULT_CACHE_FILE = data_vault_config_path("market_holidays.json")

# DataVault/config/ 우선, 없으면 레거시 shared/config/ 폴백 (기본값: DataVault/config/)
_CACHE_FILE = _VAULT_CACHE_FILE if (os.path.exists(_VAULT_CACHE_FILE) or not os.path.exists(_LEGACY_CACHE_FILE)) else _LEGACY_CACHE_FILE

# ==============================================================================
# 기본 오프라인 Fallback 휴장일 목록 (네트워크 장애 대비)
# ==============================================================================
_DEFAULT_FALLBACK_HOLIDAYS = {
    "KRX": [
        # 2024년
        "2024-01-01", "2024-02-09", "2024-02-12", "2024-03-01", "2024-04-10",
        "2024-05-01", "2024-05-06", "2024-05-15", "2024-06-06", "2024-08-15",
        "2024-09-16", "2024-09-17", "2024-09-18", "2024-10-01", "2024-10-03",
        "2024-10-09", "2024-12-25", "2024-12-31",
        # 2025년
        "2025-01-01", "2025-01-27", "2025-01-28", "2025-01-29", "2025-01-30",
        "2025-03-03", "2025-05-01", "2025-05-05", "2025-05-06", "2025-06-03",
        "2025-06-06", "2025-08-15", "2025-09-02", "2025-10-02", "2025-10-03",
        "2025-10-06", "2025-10-07", "2025-10-08", "2025-10-09", "2025-11-03",
        "2025-12-02", "2025-12-25", "2025-12-31",
        # 2026년
        "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02",
        "2026-05-01", "2026-05-05", "2026-05-25", "2026-06-02", "2026-06-03",
        # 2026-08-03·2026-09-28 은 휴장일이 아니다 (실거래 관측: 069500 1분봉 381분 정상 체결) — 2026-10-01 제거
        "2026-07-02", "2026-08-17", "2026-09-24", "2026-09-25",
        "2026-10-05", "2026-10-09", "2026-12-25", "2026-12-31"
    ],
    "US": [
        # 2022년
        "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30", "2022-06-19",
        "2022-07-04", "2022-09-05", "2022-11-24", "2022-12-26",
        # 2023년
        "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29",
        "2023-06-19", "2023-07-04", "2023-09-04", "2023-11-23", "2023-12-25",
        # 2024년
        "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
        "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
        # 2025년
        "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
        "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
        "2025-12-25",
        # 2026년
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"
    ]
}

# 메모리 캐시
_MEM_CACHE: dict = {}


def _normalize_market(market: str) -> str:
    m = str(market).strip().upper()
    if m in ["KRX", "KR", "KODEX", "KOSPI", "KOSDAQ"]:
        return "KRX"
    if m in ["US", "USA", "CME", "NYSE", "NASDAQ"]:
        return "US"
    # [P-1] 크립토는 연중무휴 — 휴장일 개념이 없다 (주말 포함)
    if (m in ["CRYPTO", "UPBIT", "BINANCE", "COINBASE", "CRYPTOCURRENCY"]
            or m.startswith("KRW-") or m.startswith("BTC") or m.startswith("ETH")):
        return "CRYPTO"
    return "KRX"


def is_continuous_market(market: str) -> bool:
    """연중무휴 시장(크립토 등) 여부. True면 주말/공휴일 스킵을 적용하지 않는다."""
    return _normalize_market(market) in _CONTINUOUS_MARKETS


def _to_date(dt_or_str: Union[str, datetime, date]) -> Optional[date]:
    """문자열/날짜를 date 로 정규화 (실패 시 None)"""
    try:
        if isinstance(dt_or_str, datetime):
            return dt_or_str.date()
        if isinstance(dt_or_str, date):
            return dt_or_str
        return datetime.strptime(str(dt_or_str).strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _load_cache_from_disk() -> dict:
    """로컬 디스크에서 휴장일 캐시 로드"""
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[MarketCalendar] 캐시 파일 로드 실패: {e}")
    return {}


def _save_cache_to_disk(data: dict):
    """로컬 디스크에 휴장일 캐시 저장"""
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[MarketCalendar] 캐시 파일 저장 실패: {e}")


def _extract_krx_vault_historical_holidays():
    """DataVault 내 KRX 장기 일봉(예: A005930_1d.csv) 아카이브를 역산 스캔하여
    1980년부터의 평일 비영업일(공휴일/휴장일) 집합과 관측 거래일 집합을 추출한다.
    Returns:
        (detected_holidays_set, trading_dates_set)
    """
    try:
        from shared.data_library.paths import data_vault_path
        candle_dir = data_vault_path("raw", "creon", "candle_1d")
        if not os.path.exists(candle_dir):
            return set(), set()

        # 가장 오래된 장기 일봉 파일(우선순위: A005930_1d.csv)
        target_path = os.path.join(candle_dir, "A005930_1d.csv")
        if not os.path.exists(target_path):
            candidates = [os.path.join(candle_dir, f) for f in os.listdir(candle_dir) if f.endswith("_1d.csv")]
            if not candidates:
                return set(), set()
            target_path = max(candidates, key=os.path.getsize)

        import csv
        dates = set()
        with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            ts_idx = 0
            if header:
                for idx, col in enumerate(header):
                    if col.strip().lower() in ("timestamp", "date", "datetime"):
                        ts_idx = idx
                        break
            for row in reader:
                if row and len(row) > ts_idx:
                    val = row[ts_idx].strip()[:10]
                    if len(val) >= 10 and val[4] == "-" and val[7] == "-":
                        dates.add(val)

        if not dates:
            return set(), set()

        sorted_dates = sorted(dates)
        min_date_str = sorted_dates[0]
        max_date_str = sorted_dates[-1]

        # 연초 휴장일(예: 1980-01-01~03 신정 연휴)도 누락 없이 포함하기 위해 해당 연도 1월 1일부터 스캔
        start_year = int(min_date_str[:4])
        cur_d = date(start_year, 1, 1)
        end_d = datetime.strptime(max_date_str, "%Y-%m-%d").date()

        detected = set()
        while cur_d <= end_d:
            if cur_d.weekday() < 5:  # 월~금 평일
                d_str = cur_d.strftime("%Y-%m-%d")
                if d_str not in dates:
                    detected.add(d_str)
            cur_d += timedelta(days=1)

        return detected, dates
    except Exception as e:
        logger.warning(f"[MarketCalendar] DataVault KRX 과거 휴장일 스캔 실패: {e}")
        return set(), set()


# 미국 거래일 기준 종목 — 1962년(PFE 1972)부터 거래일마다 체결된 대형주. 한 종목의 거래정지·결측에 흔들리지 않게 다수결로 본다.
_US_CALENDAR_REFS = ("IBM", "KO", "GE", "XOM", "PG", "JNJ", "MRK", "PFE", "CAT", "MMM", "DIS")


def _extract_us_vault_historical_holidays():
    """DataVault 미국 일봉(raw/yahoo/candle_1d, 거래소 현지 세션 날짜 라벨)에서 기준 대형주 다수결로 거래일을 뽑고
    그 기간 평일 중 거래가 없던 날을 휴장일로 역산한다 (2026-10-15 — 종전 ES 선물 5년치 역산을 대체; 1962~ 16,304 거래일,
    겹치는 2021~ 구간은 종전 캐시와 일치). 기준 종목이 거래 중이던 날만 표를 센다(상장 전·후는 기권).
    Returns:
        (detected_holidays_set, trading_dates_set)
    """
    try:
        from collections import Counter
        from shared.data_library.paths import data_vault_path
        votes, voters = Counter(), Counter()
        spans = []
        for sym in _US_CALENDAR_REFS:
            fp = data_vault_path("raw", "yahoo", "candle_1d", f"{sym}_1d.csv")
            if not os.path.exists(fp):
                continue
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                next(f, None)
                ds = [line[:10] for line in f if len(line) >= 10 and line[4] == "-" and line[7] == "-"]
            if ds:
                votes.update(ds)
                spans.append((ds[0], ds[-1]))
        if not spans:
            return set(), set()
        start = min(a for a, _ in spans)
        end = max(b for _, b in spans)
        trading, detected = set(), set()
        cur_d = datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        while cur_d <= end_d:
            if cur_d.weekday() < 5:
                d_str = cur_d.strftime("%Y-%m-%d")
                n = sum(1 for a, b in spans if a <= d_str <= b)
                if n and votes[d_str] * 2 >= n:
                    trading.add(d_str)
                else:
                    detected.add(d_str)
            cur_d += timedelta(days=1)
        return detected, trading
    except Exception as e:
        logger.warning(f"[MarketCalendar] DataVault 미국 과거 휴장일 스캔 실패: {e}")
        return set(), set()


def sync_market_holidays(market: str = "KRX", range_str: str = "5y") -> Set[str]:
    """
    Yahoo Finance 공식 실거래 일봉(KRX: 069500.KS, US: ES=F) 및
    DataVault 장기 일봉 아카이브(KRX 1980~현재, US 1962~현재 대형주 다수결)를 스캔하여
    평일 중 거래가 없는 날을 '실제 증시 휴장일'로 자동 역산 추출합니다.
    """
    norm_mkt = _normalize_market(market)
    symbol = "069500.KS" if norm_mkt == "KRX" else "ES=F"
    
    trading_dates = set()
    detected_holidays = set()

    # 1. 온라인 Yahoo Finance 실거래 일봉 스캔 (최신 거래일 확보)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={range_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        timestamps = data["chart"]["result"][0]["timestamp"]
        online_dates = set(datetime.fromtimestamp(ts).strftime("%Y-%m-%d") for ts in timestamps)
        if online_dates:
            trading_dates.update(online_dates)
            min_date = min(online_dates)
            max_date = max(online_dates)
            cur_d = datetime.strptime(min_date, "%Y-%m-%d").date()
            end_d = datetime.strptime(max_date, "%Y-%m-%d").date()
            while cur_d <= end_d:
                if cur_d.weekday() < 5:
                    ds = cur_d.strftime("%Y-%m-%d")
                    if ds not in online_dates:
                        detected_holidays.add(ds)
                cur_d += timedelta(days=1)
    except Exception as e:
        logger.warning(f"[MarketCalendar] {norm_mkt} 온라인 휴장일 스캔 실패 ({e})")

    # 2. KRX의 경우 DataVault 내 장기 일봉 아카이브(1980~현재) 스캔 병합
    if norm_mkt == "KRX":
        vault_hols, vault_trades = _extract_krx_vault_historical_holidays()
        if vault_trades:
            trading_dates.update(vault_trades)
            detected_holidays.update(vault_hols)
    elif norm_mkt == "US":
        vault_hols, vault_trades = _extract_us_vault_historical_holidays()
        if vault_trades:
            # 주식 일봉이 있는 기간은 주식 거래일이 정답이다. ES 선물은 주식 휴장일에도 단축 거래를 해서(2023 추수감사절·
            # 2025 카터 국장일·메모리얼데이·준틴스·독립기념일 실측), ES 관측일로 휴장일을 지우면 그 날이 거래일로 둔갑한다.
            # ES 는 주식 일봉 마지막 날 이후 구간에만 쓴다.
            vmax = max(vault_trades)
            trading_dates = set(vault_trades) | {d for d in trading_dates if d > vmax}
            detected_holidays = set(vault_hols) | {d for d in detected_holidays if d > vmax}

    if not trading_dates and not detected_holidays:
        # 둘 다 실패 시 로컬 캐시/Fallback 반환
        fallback_set = set(_DEFAULT_FALLBACK_HOLIDAYS.get(norm_mkt, []))
        return fallback_set

    # 3. 기본 Fallback 휴장일과 병합 — 단, 실제 거래가 관측된 날은 휴장일이 될 수 없다 (데이터 우선).
    fallback = set(_DEFAULT_FALLBACK_HOLIDAYS.get(norm_mkt, [])) - trading_dates
    merged_holidays = sorted(list((detected_holidays | fallback) - trading_dates))

    # 4. 캐시 갱신
    disk_data = _load_cache_from_disk()
    disk_data[norm_mkt] = {
        "last_synced": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "range": range_str,
        "holidays": merged_holidays
    }
    _save_cache_to_disk(disk_data)
    _MEM_CACHE[norm_mkt] = set(merged_holidays)

    return set(merged_holidays)


def get_market_holidays(market: str = "KRX", force_refresh: bool = False) -> Set[str]:
    """
    지정된 시장의 공휴일/휴장일 Set(YYYY-MM-DD)을 반환합니다.
    
    1. 메모리 캐시 확인
    2. 로컬 디스크 캐시 확인
    3. 캐시가 없거나 force_refresh=True인 경우 자동 온라인 동기화
    4. 네트워크 실패 시 Fallback 반환

    [P-1] 연중무휴 시장(크립토)은 휴장일이 없으므로 **빈 Set** 을 반환한다.
    """
    norm_mkt = _normalize_market(market)

    if norm_mkt in _CONTINUOUS_MARKETS:
        _MEM_CACHE[norm_mkt] = set()
        return set()

    if not force_refresh and norm_mkt in _MEM_CACHE:
        return _MEM_CACHE[norm_mkt]
        
    if not force_refresh:
        disk_data = _load_cache_from_disk()
        if norm_mkt in disk_data and "holidays" in disk_data[norm_mkt]:
            h_set = set(disk_data[norm_mkt]["holidays"])
            _MEM_CACHE[norm_mkt] = h_set
            return h_set
            
    # 온라인 동기화 시도
    try:
        h_set = sync_market_holidays(norm_mkt)
        if h_set:
            return h_set
    except Exception:
        pass
        
    # Fallback
    fallback_set = set(_DEFAULT_FALLBACK_HOLIDAYS.get(norm_mkt, []))
    _MEM_CACHE[norm_mkt] = fallback_set
    return fallback_set


def is_trading_day(dt_or_str: Union[str, datetime, date], market: str = "KRX") -> bool:
    """
    특정 날짜가 해당 시장의 유효한 영업일(Trading Day)인지 여부를 판정합니다.
    (주말(토/일) 및 휴장일 제외)

    [P-1] 연중무휴 시장(크립토)은 모든 날짜가 거래일이다 (주말 포함).
    """
    if is_continuous_market(market):
        return True

    if isinstance(dt_or_str, (datetime, date)):
        dt = dt_or_str
        d_str = dt.strftime("%Y-%m-%d")
        weekday = dt.weekday()
    else:
        d_str = str(dt_or_str).strip()[:10]
        try:
            dt = datetime.strptime(d_str, "%Y-%m-%d")
        except Exception as e:
            logger.warning(f"[MarketCalendar] 날짜 파싱 실패('{dt_or_str}'): {e}")
            return False
        weekday = dt.weekday()

    # 주말(토=5, 일=6) 판정
    if weekday >= 5:
        return False

    holidays = get_market_holidays(market)
    return d_str not in holidays


def get_trading_days(start_date: Union[str, datetime, date],
                     end_date: Union[str, datetime, date],
                     market: str = "KRX") -> List[str]:
    """
    start_date ~ end_date 사이의 실제 거래 가능한 영업일(YYYY-MM-DD) 리스트를 반환합니다.
    (양끝 포함)

    [P-1] 연중무휴 시장(크립토)은 주말/휴장일 없이 **모든 날짜**를 반환한다.
    """
    s_dt = _to_date(start_date)
    e_dt = _to_date(end_date)
    if s_dt is None or e_dt is None or s_dt > e_dt:
        return []

    cur_d = s_dt
    if is_continuous_market(market):
        res = []
        while cur_d <= e_dt:
            res.append(cur_d.strftime("%Y-%m-%d"))
            cur_d += timedelta(days=1)
        return res

    holidays = get_market_holidays(market)
    trading_days = []
    while cur_d <= e_dt:
        if cur_d.weekday() < 5:
            d_str = cur_d.strftime("%Y-%m-%d")
            if d_str not in holidays:
                trading_days.append(d_str)
        cur_d += timedelta(days=1)
    return trading_days


def count_trading_days(start_date: Union[str, datetime, date],
                       end_date: Union[str, datetime, date],
                       market: str = "KRX") -> int:
    """start_date ~ end_date(양끝 포함) 사이의 유효 거래일 수.

    창(window) 길이를 **달력일이 아니라 거래일**로 산정하기 위한 기본 계수기 (P-3).
    """
    return len(get_trading_days(start_date, end_date, market))


def snap_to_trading_day(dt_or_str: Union[str, datetime, date], market: str = "KRX",
                        direction: str = "prev", max_scan_days: int = 30) -> Optional[str]:
    """주어진 날짜를 가장 가까운 **유효 거래일**로 스냅하여 YYYY-MM-DD 로 반환한다.

    Args:
        direction: 'prev' (당일 포함 과거 방향) 또는 'next' (당일 포함 미래 방향)
        max_scan_days: 탐색 상한 (초과 시 None — 비정상 휴일 테이블 방어)

    Returns:
        YYYY-MM-DD 또는 None(스냅 실패/입력 오류)

    Note:
        연중무휴 시장(크립토)은 스냅이 필요 없으므로 입력 날짜를 그대로 반환한다.
    """
    d = _to_date(dt_or_str)
    if d is None:
        return None
    if is_continuous_market(market):
        return d.strftime("%Y-%m-%d")

    step = -1 if str(direction).lower() == "prev" else 1
    cur = d
    for _ in range(max(1, int(max_scan_days)) + 1):
        if is_trading_day(cur, market):
            return cur.strftime("%Y-%m-%d")
        cur = cur + timedelta(days=step)
    return None


def prev_trading_day(dt_or_str: Union[str, datetime, date], market: str = "KRX") -> Optional[str]:
    """직전 유효 거래일 — 당일이 거래일이면 그대로, 아니면 과거 방향으로 스냅."""
    return snap_to_trading_day(dt_or_str, market, direction="prev")


def next_trading_day(dt_or_str: Union[str, datetime, date], market: str = "KRX") -> Optional[str]:
    """다음 유효 거래일 — 당일이 거래일이면 그대로, 아니면 미래 방향으로 스냅."""
    return snap_to_trading_day(dt_or_str, market, direction="next")


def prev_trading_day_strict(dt_or_str: Union[str, datetime, date], market: str = "KRX") -> Optional[str]:
    """주어진 날짜의 **직전** 유효 거래일 (당일 제외).

    WFA 종료 경계처럼 "그 날짜를 반드시 제외해야 하는" 곳에 사용한다.
      예) 홀드아웃 시작일이 2026-09-08(화, 거래일) 이면
          `prev_trading_day`      → 2026-09-08 (당일 반환 → 홀드아웃 침범!)
          `prev_trading_day_strict` → 2026-09-07  ← WFA 종료에 사용해야 하는 값
    """
    d = _to_date(dt_or_str)
    if d is None:
        return None
    if is_continuous_market(market):
        return (d - timedelta(days=1)).strftime("%Y-%m-%d")
    return snap_to_trading_day(d - timedelta(days=1), market, direction="prev")


def next_trading_day_strict(dt_or_str: Union[str, datetime, date], market: str = "KRX") -> Optional[str]:
    """주어진 날짜의 **다음** 유효 거래일 (당일 제외)."""
    d = _to_date(dt_or_str)
    if d is None:
        return None
    if is_continuous_market(market):
        return (d + timedelta(days=1)).strftime("%Y-%m-%d")
    return snap_to_trading_day(d + timedelta(days=1), market, direction="next")
