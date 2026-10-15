"""
CTradeSystem Shared - Market Utilities

SYMBOL_UPBIT 값 기반으로 자산 유형(KRX 주식/ETF vs 가상화폐)을 자동 판별하고
KRX 세션 기준 시각 상수를 제공한다. 또한 인터벌 문자열 파싱 유틸을 제공한다.

사용 예:
    from shared.core.market_utils import detect_market_type, KRX_WARMUP_END, KRX_FORCE_EXIT_AT, parse_interval_minutes
    market_type = detect_market_type(config)  # "KRX" or "CRYPTO"
"""
import numpy as np
import pandas as pd
from typing import Any, Dict
from datetime import time, datetime

DEFAULT_INTERVAL_MINUTES = 15.0

# 프로젝트 설정에서 "현재 타겟 심볼"이 들어 있을 수 있는 키 (우선순위 순).
# SYMBOL_UPBIT 는 옛 이름이지만 KRX 종목 코드도 여기에 담긴다 (common.json 이 항상 채움).
TARGET_SYMBOL_KEYS = ("SYMBOL_UPBIT", "TARGET_SYMBOL", "TARGET_STOCK", "COIN", "KIS_STOCK_CODE", "SYMBOL")


def get_target_symbol(config: dict, default: str = None) -> str:
    """프로젝트 설정의 현재 타겟 심볼을 반환하는 유일한 접근 함수.

    각 호출부가 `config.get("SYMBOL_UPBIT", "KRW-BTC")` 처럼 제각각의 기본 심볼(KRW-BTC/069500/…)을
    코드에 박아 두면, 설정이 비었을 때 KRX ETF 프로젝트가 조용히 다른 종목으로 폴백한다.
    그래서 값이 없으면 지어내지 않고 ValueError 를 낸다 (호출부가 빈 값을 허용하면 default="" 를 명시).
    """
    for key in TARGET_SYMBOL_KEYS:
        val = (config or {}).get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    if default is not None:
        return default
    raise ValueError(f"프로젝트 설정에 타겟 심볼이 없습니다 (확인한 키: {', '.join(TARGET_SYMBOL_KEYS)})")


# ==============================================================================
# KRX 세션 기준 시각 상수 (한국거래소 정규장 기준)
# ==============================================================================
KRX_SESSION_OPEN  = time(9,  0)   # 장 시작
KRX_WARMUP_END    = time(9, 10)   # 웜업 종료 — 이 이후부터 신규 진입 허용
KRX_NO_ENTRY_FROM = time(15,  0)  # 신규 진입 차단 시각
KRX_FORCE_EXIT_AT = time(15, 18)  # 미청산 포지션 강제 청산 시각
KRX_SESSION_CLOSE = time(15, 30)  # 장 종료


# ==============================================================================
# KRX 일자별 세션 (특수 거래일: 수능·새해 첫 거래일 — DataVault/config/krx_session_calendar.json 원장)
# ==============================================================================
# 위 상수는 평일 기본 세션이다. 날짜를 아는 곳은 반드시 krx_session(날짜) 로 읽는다.
# 파생 시각(웜업 종료/진입 차단/강제 청산/종가 단일가 시작)은 기본 세션과 같은 간격으로 개장·마감에서 유도한다.
from dataclasses import dataclass as _dataclass
from datetime import date as _date, timedelta as _timedelta


@_dataclass(frozen=True)
class KrxSession:
    open: time
    close: time

    @staticmethod
    def _shift(t: time, minutes: int) -> time:
        base = datetime(2000, 1, 1, t.hour, t.minute) + _timedelta(minutes=minutes)
        return base.time()

    @property
    def warmup_end(self) -> time:
        return self._shift(self.open, 10)

    @property
    def no_entry_from(self) -> time:
        return self._shift(self.close, -30)

    @property
    def force_exit_at(self) -> time:
        return self._shift(self.close, -12)

    @property
    def auction_start(self) -> time:
        """종가 단일가(동시호가) 시작 — 연속 체결 종료 시각."""
        return self._shift(self.close, -10)


_REGULAR_SESSION = KrxSession(KRX_SESSION_OPEN, KRX_SESSION_CLOSE)
_SESSION_CAL_CACHE: Dict[str, Any] = {}


def _parse_hhmm(v: str) -> time:
    h, m = str(v).strip().split(":")[:2]
    return time(int(h), int(m))


def _load_krx_session_calendar() -> Dict[_date, KrxSession]:
    """특수 거래일 원장 로드 (파일 변경 감지 캐싱). 원장이 없으면 특수일 없음 — 평일 기본 세션만 쓴다."""
    import json
    import os
    from shared.data_library.paths import data_vault_config_path
    path = data_vault_config_path("krx_session_calendar.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _SESSION_CAL_CACHE.get("mtime") == mtime:
        return _SESSION_CAL_CACHE["data"]
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    data = {
        _date.fromisoformat(d): KrxSession(_parse_hhmm(v["open"]), _parse_hhmm(v["close"]))
        for d, v in (raw.get("special_sessions") or {}).items()
    }
    _SESSION_CAL_CACHE.update(mtime=mtime, data=data)
    return data


def krx_session(day: Any) -> KrxSession:
    """그 날짜의 KRX 정규장 세션 (개장·마감과 파생 시각)."""
    if day is None:
        return _REGULAR_SESSION
    if not isinstance(day, _date) or isinstance(day, datetime):
        day = pd.Timestamp(day).date()
    return _load_krx_session_calendar().get(day, _REGULAR_SESSION)


def krx_regular_bar_mask(timestamps: Any, label: str = "BAR_CLOSE") -> pd.Series:
    """KRX 정규장 봉만 True. **분봉이 정규장에 속하는지의 유일한 판정** (장 일정 원장 krx_session 기준).

    label = 봉 라벨 규약(timestamp_convention): BAR_CLOSE 면 (개장, 마감], BAR_OPEN 이면 [개장, 마감).
    2026-09-14 NXT(대체거래소) 개장 이후 Creon 은 개별 주식 1분봉에 08:00~20:00 통합 체결을 준다 — 정규장 시계열은
    이 판정으로 거른다(ETF·지수는 원래 정규장 봉만 있어 결과가 같다)."""
    ts = pd.to_datetime(pd.Series(timestamps))
    days = ts.dt.normalize()
    sess = {d: krx_session(d) for d in days.unique()}
    o = days.map(lambda d: pd.Timestamp.combine(d.date(), sess[d].open))
    c = days.map(lambda d: pd.Timestamp.combine(d.date(), sess[d].close))
    if str(label).upper() == "BAR_OPEN":
        return (ts >= o) & (ts < c)
    return (ts > o) & (ts <= c)


def _ts_and_session(ts: Any):
    """(time-of-day, 그날 세션). 날짜 없는 time 이면 평일 기본 세션."""
    if isinstance(ts, time):
        return ts, _REGULAR_SESSION
    t = pd.Timestamp(ts)
    return t.time(), krx_session(t.date())


# ==============================================================================
# 자산 유형 자동 판별
# ==============================================================================

def detect_market_type(config: dict) -> str:
    """
    SYMBOL_UPBIT 값으로 자산 유형을 자동 판별한다.

    판별 규칙:
      - 순수 숫자 6자리 (예: '069500', '005930') → KRX (국내 주식/ETF)
      - 'KRW-XXX' 패턴 (예: 'KRW-BTC', 'KRW-ETH') → CRYPTO (가상화폐)
      - 그 외 / 불명 → KRX (안전한 기본값 — 본 시스템은 KRX 종목이 대다수이며,
        symbols_registry 조회 실패 시 0167A0 같은 영문 포함 KRX 코드를 크립토로
        오판해 24시간 연속장 캘린더가 적용되는 사고를 방지한다)

    Returns:
        "KRX"    — 국내 주식/ETF, KRX 세션(09:00~15:30) 기반 처리 적용
        "CRYPTO" — 가상화폐, 24시간 연속 처리 유지
    """
    symbol = config.get('SYMBOL_UPBIT', '') or config.get('SYMBOL', '')
    symbol = str(symbol).strip().upper()

    try:
        from shared.config.config_manager import get_symbol_info
        info = get_symbol_info(symbol)
        mtype = info.get("market_type", "")
        if "KRX" in mtype:
            return "KRX"
        if mtype == "CRYPTO":
            return "CRYPTO"
    except Exception:
        pass

    if (symbol.isdigit() and len(symbol) == 6) or (len(symbol) == 6 and symbol.isalnum() and any(c.isdigit() for c in symbol) and not symbol.startswith("US")):
        return "KRX"

    if symbol.startswith('KRW-') or symbol.startswith('BTC-') or symbol.startswith('USDT-'):
        return "CRYPTO"

    return "KRX"


_CRYPTO_LISTING_SUFFIXES = ("USDT", "USDC", "-USD", "-PERP", "_PERP", "_BPERP")


def is_crypto_symbol(symbol: str) -> bool:
    """심볼(또는 asset_id)이 가상자산인지: 종목 원장(market_type) 우선, 미등록일 때만 거래소 표기 규칙으로 추정.

    `"BTC" in sym` 같은 부분 문자열 판정은 상품명에 우연히 들어간 글자로 오판하므로 쓰지 않는다.
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    try:
        from shared.config.config_manager import get_symbol_scope, get_symbol_info
        scope = get_symbol_scope(sym)
        if scope is not None:
            # 🛡️ [Scope Discrepancy Guide] 원장에 등록되었으나 scope가 'other'인 경우 불일치 감지 및 가이드 경고
            if scope == "other":
                info = get_symbol_info(sym)
                mtype = (info or {}).get("market_type", "UNKNOWN")
                looks_like_crypto = sym.startswith(("KRW-", "BTC-", "USDT-")) or sym.endswith(_CRYPTO_LISTING_SUFFIXES)
                if looks_like_crypto:
                    import logging
                    logging.getLogger("shared.market_utils").warning(
                        f"⚠️ [Asset Scope Discrepancy] 자산 '{sym}'은 가상자산 명명 규칙을 띠고 있으나, "
                        f"원장(symbols_registry.json)의 market_type='{mtype}'으로 인해 scope='other'로 분류되었습니다. "
                        f"가상자산/무기한 선물인 경우 symbols_registry.json의 market_type을 'CRYPTO_FUTURES' 또는 'CRYPTO_SPOT'으로 지정하십시오."
                    )
            return scope == "crypto"
    except Exception:
        pass
    return sym.startswith(("KRW-", "BTC-", "USDT-")) or sym.endswith(_CRYPTO_LISTING_SUFFIXES)


def is_krx(config: dict) -> bool:
    """detect_market_type의 편의 래퍼 — KRX 여부를 bool로 반환"""
    return detect_market_type(config) == "KRX"


# ==============================================================================
# 시스템 공인 검증 시간봉 (Validated Timeframes) 규격 명세
# ==============================================================================
# 1. 거래소/증권사 서버 직접 제공 정형 캔들 (Direct Ingestion)
VALIDATED_DIRECT_CANDLES = ['1m', '3m', '5m', '10m', '15m', '30m', '60m', '240m', '1d', 'day', 'week']

# 2. 틱 체결 데이터 기반 6단계 정밀 리샘플링 검증 초봉 (Tick-Resampled Ultra-Fast)
VALIDATED_TICK_RESAMPLED_INTERVALS = ['1s', '3s', '5s', '10s', '15s', '30s']

# 전체 공인 인터벌 세트
ALL_VALIDATED_INTERVALS = set(VALIDATED_DIRECT_CANDLES + VALIDATED_TICK_RESAMPLED_INTERVALS)


def parse_interval_seconds(interval) -> int:
    """인터벌 문자열(예: '10s', '1m', '15m', '1h', '1d') 또는 숫자를 초 단위 int로 변환합니다."""
    if isinstance(interval, (int, float)):
        return max(1, int(round(interval * 60)))

    s = str(interval).strip().lower()
    if s.endswith('sec'):
        num = s.replace('sec', '')
        return int(num) if num.isdigit() else 10
    if s.endswith('s'):
        num = s.replace('s', '')
        return int(num) if num.isdigit() else 10
    if s.endswith('m'):
        num = s.replace('m', '')
        return int(float(num) * 60) if num.replace('.', '', 1).isdigit() else 900
    if s.endswith('h'):
        num = s.replace('h', '')
        return int(float(num) * 3600) if num.replace('.', '', 1).isdigit() else 3600
    if s.endswith('d') or s in ('day', '1d'):
        num = s.replace('d', '').replace('day', '') or '1'
        return int(float(num) * 86400) if num.replace('.', '', 1).isdigit() else 86400
    if s.isdigit():
        return int(s) * 60
    return int(DEFAULT_INTERVAL_MINUTES * 60)


def parse_interval_minutes(interval) -> float:
    """인터벌 문자열(예: '10s', '15m', '1h', '1d') 또는 숫자를 분 단위 float로 변환합니다."""
    return parse_interval_seconds(interval) / 60.0


# ==============================================================================
# KRX 세션 관련 헬퍼
# ==============================================================================

def get_krx_day_close_ts(date, tz=None) -> pd.Timestamp:
    """
    주어진 날짜의 KRX 세션 강제 청산 시각(15:18)을 pd.Timestamp로 반환.

    Args:
        date: datetime.date 또는 pd.Timestamp
        tz: 타임존 (기본 None = tz-naive)
    """
    ts = pd.Timestamp(date)
    result = ts.normalize() + pd.Timedelta(hours=15, minutes=18)
    if tz:
        result = result.tz_localize(tz)
    return result


def is_krx_entry_allowed(ts: Any) -> bool:
    """
    해당 시각에 KRX 신규 진입이 허용되는지 반환.
    허용 구간: 웜업 종료 <= t < 진입 차단 (평일 09:10~15:00, 특수 거래일은 원장 세션 기준)
    """
    t, sess = _ts_and_session(ts)
    return sess.warmup_end <= t < sess.no_entry_from


def decision_point_mask(timestamps, intraday_krx: bool) -> np.ndarray:
    """학습·스크리닝·임계값 보정에 쓸 수 있는 행 = **실제로 진입 결정을 내릴 수 있었던 시점**.

    백테스터·실전과 같은 정의(`is_krx_entry_allowed`)를 쓴다. 진입할 수 없는 시점의 행은 모델이 배울
    대상이 아니다 — 그 행들의 라벨(장 마감 강제 0 등)과 피처(동시호가의 거래량 0 등)가 시장 예측이 아니라
    '지금 몇 시인가'를 가르친다.
    (2026-10-01: 069500 10s 챔피언 후보 gain 의 81% 가 target_mfi 였는데, 대부분 15:19~15:30 동시호가의
     'MFI=0 → 라벨 0' 을 알아맞히는 데 쓰였다)

    intraday_krx 가 아니면(크립토 등 연속 시장) 모든 행이 의사결정 시점이다.
    """
    if not intraday_krx:
        return np.ones(len(timestamps), dtype=bool)
    ts = pd.to_datetime(pd.Series(timestamps).reset_index(drop=True))
    tod = ts.dt.hour * 3600 + ts.dt.minute * 60 + ts.dt.second
    _sec = lambda t: t.hour * 3600 + t.minute * 60
    days = ts.dt.date
    sessions = {d: krx_session(d) for d in days.unique()}
    lo = days.map(lambda d: _sec(sessions[d].warmup_end))
    hi = days.map(lambda d: _sec(sessions[d].no_entry_from))
    return ((tod >= lo) & (tod < hi)).to_numpy()


def session_forward_return(close, timestamps, horizon: int, intraday_krx: bool) -> np.ndarray:
    """h봉 뒤 전방 수익률 — 의사결정 시점에서만, 같은 세션 안에서만 정의한다(나머지는 NaN).

    `close.pct_change(h).shift(-h)` 를 그대로 쓰면 KRX 의 장 막판 봉에서 **밤사이 갭**이 전방 수익률이
    되고, 진입할 수 없는 시점까지 IC 계산에 들어간다.
    """
    c = pd.Series(np.asarray(close, dtype=float))
    h = int(horizon)
    fwd = (c.shift(-h) / c - 1.0).to_numpy()
    if not intraday_krx:
        return fwd
    ts = pd.to_datetime(pd.Series(timestamps).reset_index(drop=True))
    day = ts.dt.normalize()
    same_session = (day.shift(-h) == day).to_numpy()
    return np.where(same_session & decision_point_mask(ts, True), fwd, np.nan)


def is_krx_force_exit(ts: Any) -> bool:
    """
    해당 시각이 KRX 강제 청산 시각(평일 15:18, 특수 거래일은 마감-12분) 이상인지 반환.
    """
    t, sess = _ts_and_session(ts)
    return t >= sess.force_exit_at


def build_krx_eod_index(timestamps: pd.Series) -> pd.Series:
    """
    각 타임스탬프에 대해 해당 영업일의 세션 마감 인덱스를 반환한다.
    트리플 배리어 라벨링 시 날짜 경계 Clamp에 사용.

    Returns:
        pd.Series(dtype=int): 각 원점 i에서 바라본 당일 마지막 허용 인덱스
    """
    ts = pd.to_datetime(timestamps)
    dates = ts.dt.date
    # 날짜별 마지막 인덱스 (15:18 이전 구간의 마지막 캔들)
    day_last_idx = {}
    sessions = {d: krx_session(d) for d in dates.unique()}
    for idx, (t, d) in enumerate(zip(ts, dates)):
        t_time = t.time()
        if t_time < sessions[d].force_exit_at:
            day_last_idx[d] = idx

    # 각 행의 당일 마지막 허용 인덱스 매핑
    return pd.Series(
        [day_last_idx.get(d, idx) for idx, d in enumerate(dates)],
        index=timestamps.index,
        dtype=int
    )


def is_krx_etf(symbol: str) -> bool:
    """
    주어진 종목코드가 국내 ETF/ETN인지 판별합니다.
    (기본적으로 6자리 숫자 종목 중 ETF 코드 대역 및 대표 ETF 식별)
    """
    clean = str(symbol).strip()
    if not (clean.isdigit() and len(clean) == 6):
        return False
    # 국내 대표 ETF 접두/코드 대역 또는 일반 ETF 판별 (기본적으로 CTrade KRX 종목은 ETF 중심)
    return True


def get_krx_tick_size(price: float, is_etf: bool = True) -> int:
    """
    KRX 주식/ETF 호가단위(Tick Size)를 반환. (2023년 거래소 규정 기준)
    
    ETF/ETN/ELW 규정 (is_etf=True):
      - 2,000원 미만: 1원
      - 2,000원 이상: 5원 (전 가격대 단일 적용)
      
    일반 주식 규정 (is_etf=False):
      - 2,000원 미만: 1원
      - 2,000원 이상 ~ 5,000원 미만: 5원
      - 5,000원 이상 ~ 20,000원 미만: 10원
      - 20,000원 이상 ~ 50,000원 미만: 50원
      - 50,000원 이상 ~ 200,000원 미만: 100원
      - 200,000원 이상 ~ 500,000원 미만: 500원
      - 500,000원 이상: 1,000원
    """
    if price < 2000:
        return 1

    if is_etf:
        return 5

    if price < 5000:
        return 5
    elif price < 20000:
        return 10
    elif price < 50000:
        return 50
    elif price < 200000:
        return 100
    elif price < 500000:
        return 500
    else:
        return 1000


def round_to_krx_tick(price: float, is_etf: bool = True) -> int:
    """
    주어진 가격을 KRX 정규 호가단위에 맞게 반올림한 정수 가격으로 변환.
    """
    if price <= 0:
        return int(price)
    tick = get_krx_tick_size(price, is_etf=is_etf)
    return int(round(price / tick) * tick)

