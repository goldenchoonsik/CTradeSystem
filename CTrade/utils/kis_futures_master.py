"""KIS 국내 선물 최근월 코드 해석 (지수선물·개별주식선물).

국내 선물은 만기(최종거래일)마다 KIS 실시간 구독 코드가 바뀐다 (예: A01612 = KOSPI200 F 202612,
A11610 = 삼성전자 F 202610). 원장에 박제하면 다음 만기에 조용히 끊기므로, 원장에는 연결 코드(Creon 10100/11100 등)와
마스터 조회 키(지수선물 `kis_futures_base`, 개별주식선물 `underlying`)만 두고, 구독 시점마다 KIS 공개 종목 마스터에서
최근월 코드를 찾는다.

월물 교체 시점 = 최종거래일 '당일' 장 시작부터 다음 월물.
  학습 데이터인 Creon 연결 시계열이 그렇게 넘어간다 (2026-09-10 만기 실측: 10100·11100·15000 모두 09-10 08:46 봉부터
  다음 월물 가격과 일치, 09-09 까지는 만기물). 실시간 구독도 같은 날 넘어가야 학습·실전 입력이 같은 월물을 본다.
최종거래일 = 만기월 둘째 목요일, 휴장이면 직전 거래일.

마스터 행 형식 (cp949, '|' 구분, 2026-10-07 실측):
  주식: 1|A11610|KR4A116A0005|삼성전자   F 202610 (  10)| |00000.00|1|005930|삼성전자
  지수: 1|A01612|KR4A016C0004|F 202612| |00000.00|1|2001|KOSPI200
  [0]=상품유형(1=선물, 그 외=스프레드/옵션/미니 등) [1]=단축코드 [3]=한글명(만기 연월) [6]=월물 순번 [7]=기초자산 코드
"""
import io
import logging
import re
import zipfile
from datetime import date, datetime, timedelta, timezone

_MASTER_BASE_URL = "https://new.real.download.dws.co.kr/common/master/"
MASTER_URLS = {
    "stock": _MASTER_BASE_URL + "fo_stk_code_mts.mst.zip",   # 개별주식선물 (원장 underlying = 기초 주식 코드)
    "index": _MASTER_BASE_URL + "fo_idx_code_mts.mst.zip",   # 지수선물 (원장 kis_futures_base, KOSPI200 = 2001)
}
_KST = timezone(timedelta(hours=9))
_FUTURES_ROW = "1"
_MONTH_RE = re.compile(r"\bF\s+(\d{6})\b")

_cache = {}   # kind -> {"day": 'YYYY-MM-DD', "front": {기초: 코드}}


def master_base_of(info: dict):
    """원장 항목 -> (마스터 종류, 마스터 기초자산 키). 최근월 해석 대상이 아니면 None."""
    if not isinstance(info, dict):
        return None
    if info.get("underlying"):
        return "stock", str(info["underlying"]).strip()
    if info.get("kis_futures_base"):
        return "index", str(info["kis_futures_base"]).strip()
    return None


def _krx_trading_day(d: date) -> bool:
    try:
        from shared.core.market_calendar import is_trading_day
        return is_trading_day(d, "KRX")
    except Exception:
        return d.weekday() < 5


def last_trading_day(year: int, month: int, is_trading_day=_krx_trading_day) -> date:
    """선물 최종거래일: 둘째 목요일, 휴장이면 직전 거래일."""
    first = date(year, month, 1)
    d = first + timedelta(days=(3 - first.weekday()) % 7 + 7)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def parse_front_months(text: str, today: str, is_trading_day=_krx_trading_day) -> dict:
    """마스터 본문 -> {기초자산 코드: 최근월 단축코드}. today = 'YYYYMMDD' (KST).
    월물 순번이 가장 작은 선물 중 아직 교체되지 않은 것만 고른다:
      - 만기 연월이 지난 월물 제외 (상장폐지 종목의 만기물이 순번 1로 남는 경우 — 예: 금양 F 202504)
      - 이번 달 월물은 최종거래일 당일부터 제외 (Creon 연결 시계열과 같은 날 교체; 마스터 갱신이 늦어도 안전)"""
    t = datetime.strptime(today, "%Y%m%d").date()
    ym = today[:6]
    rolled_this_month = t >= last_trading_day(t.year, t.month, is_trading_day)
    best = {}
    for line in text.splitlines():
        f = line.split("|")
        if len(f) < 8 or f[0].strip() != _FUTURES_ROW:
            continue
        m = _MONTH_RE.search(f[3])
        if not m or m.group(1) < ym or (m.group(1) == ym and rolled_this_month):
            continue
        try:
            seq = int(f[6])
        except ValueError:
            continue
        und, code = f[7].strip(), f[1].strip()
        if und and code and (und not in best or seq < best[und][0]):
            best[und] = (seq, code)
    return {und: code for und, (_, code) in best.items()}


def _download_master_text(kind: str, timeout: float = 15.0) -> str:
    import requests
    resp = requests.get(MASTER_URLS[kind], timeout=timeout)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".mst"))
        return zf.read(name).decode("cp949", errors="replace")


def resolve_front_month(base: str, kind: str = "stock", now: datetime = None, fetch=_download_master_text):
    """마스터 기초자산 키의 KIS 최근월 단축코드. 해석 실패 시 None (추측 코드로 대체하지 않는다).
    마스터는 종류별로 KST 일자당 한 번만 내려받는다."""
    if kind not in MASTER_URLS:
        logging.error(f"[KISFuturesMaster] 알 수 없는 마스터 종류: {kind!r}")
        return None
    now = now or datetime.now(_KST)
    day = now.strftime("%Y-%m-%d")
    c = _cache.get(kind)
    if not c or c["day"] != day:
        try:
            c = {"day": day, "front": parse_front_months(fetch(kind), now.strftime("%Y%m%d"))}
            _cache[kind] = c
        except Exception as e:
            logging.error(f"[KISFuturesMaster] {kind} 선물 마스터 조회 실패: {e}")
            return None
    return c["front"].get(str(base).strip())
