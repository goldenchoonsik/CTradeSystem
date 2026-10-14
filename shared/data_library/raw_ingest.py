# -*- coding: utf-8 -*-
"""원천(Raw) 저장 공통 어댑터 — pandas 로 데이터를 만드는 수집기(해외선물·미국주식·가상자산·cTrader)가 쓰는 입구.

모든 원천은 크레온과 **같은 절차** `CAiModelBaker.core.raw_day_store.ingest_file` 로 저장한다:
수신 순서와 무관한 정렬 → 봉 간격 검사(미완성 봉 격리) → 날짜 단위 병합(백업·원장) → 봉인된 과거와 다른 데이터는
'확인 필요' → 원천 보존 기간이 지나 받지 못한 구간은 영구 결손 기록. 이 모듈은 DataFrame 을 임시 CSV 로 써서
그 절차에 넘기고, 끝나면 매니페스트를 발행할 뿐이다 (병합 규칙을 따로 두지 않는다).
"""
import os
import re
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import pandas as pd

from shared.data_library.paths import data_vault_path

try:
    from CAiModelBaker.core import raw_day_store as rds
except ImportError:                                   # CAiModelBaker 를 루트로 실행하는 경우
    from core import raw_day_store as rds


def quarantine_for(target_csv: str) -> str:
    """격리/백업 폴더 — 대상이 DataVault 안이면 DataVault/quarantine, 밖(임시 경로 등)이면 그 파일 옆 .merge_quarantine."""
    vault = os.path.realpath(data_vault_path())
    if os.path.realpath(target_csv).startswith(vault + os.sep):
        return data_vault_path("quarantine")
    return os.path.join(os.path.dirname(os.path.abspath(target_csv)), ".merge_quarantine")


def interval_seconds(interval: Optional[str]) -> Optional[int]:
    """'1m'/'15m'/'60m'/'1h' → 초. 일봉 이상('1d','1w','1mo')이나 알 수 없는 값은 None (격자 검사 안 함)."""
    m = re.fullmatch(r"(\d+)\s*(s|m|min|h)", str(interval or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    return n * {"s": 1, "m": 60, "min": 60, "h": 3600}[m.group(2)]


def lost_days_fn(market: str) -> Callable[[str, str], List[str]]:
    """(after_day, before_day) 사이(양 끝 제외) 그 시장의 거래일 — 영구 결손 판정용."""
    def _fn(after_day: str, before_day: str) -> List[str]:
        try:
            from shared.core.market_calendar import is_trading_day
        except Exception:
            is_trading_day = lambda d, m=market: datetime.strptime(str(d)[:10], "%Y-%m-%d").weekday() < 5
        cur = datetime.strptime(after_day, "%Y-%m-%d") + timedelta(days=1)
        end = datetime.strptime(before_day, "%Y-%m-%d")
        out = []
        while cur < end:
            if is_trading_day(cur, market):
                out.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        return out
    return _fn


def last_timestamp(csv_path: str) -> Optional[str]:
    """원천 파일의 마지막 시각(원문 그대로). 파일이 없으면 None."""
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return None
    with open(csv_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 65536))
        tail = f.read().decode("utf-8", "ignore").splitlines()
    for line in reversed(tail):
        raw = line.split(",", 1)[0].strip().strip('"')
        if rds._valid_ts(rds._norm_ts(raw)):
            return raw
    return None


def ingest_dataframe(df: pd.DataFrame, target_csv: str, *, provider: str, market: str,
                     interval: Optional[str] = None, requested_after: Optional[str] = None,
                     source_exhausted: bool = False, volume_col: Optional[str] = None,
                     publish_manifest: bool = True, timezone_name: str = "Asia/Seoul",
                     log: Callable[[str], None] = print) -> Optional[Dict]:
    """DataFrame(첫 열 = 시각)을 원천 파일에 공통 절차로 저장한다. 반환: 병합 보고서, 실패 시 None(원본 유지, 신규분 격리).

    requested_after : 이번 수집을 시작할 때 파일의 마지막 시각(없으면 None).
    source_exhausted: 원천이 줄 수 있는 가장 오래된 데이터까지 받았는가(예: Yahoo 는 조회 창 전체를 주므로 True).
    timezone_name   : 타임스탬프 문자열의 시간대(매니페스트 기록용). 원천 저장소 기본 규약은 KST 문자열이다
                      — 예외: 미국 일봉은 거래소 현지 세션 날짜(America/New_York).
                      둘 다 주어지면, 받은 데이터가 requested_after 에 닿지 못한 구간을 영구 결손으로 기록한다.
    """
    if df is None or df.empty:
        return {"added_rows": 0, "touched_days": [], "conflicts": 0}
    os.makedirs(os.path.dirname(os.path.abspath(target_csv)), exist_ok=True)
    tmp = f"{target_csv}.ingest.{os.getpid()}.{int(time.time() * 1000)}.tmp"
    df.to_csv(tmp, index=False)
    rep = rds.ingest_file(tmp, target_csv, quarantine_dir=quarantine_for(target_csv),
                          interval_seconds=interval_seconds(interval), volume_col=volume_col,
                          requested_after=requested_after, source_exhausted=source_exhausted,
                          lost_days_fn=lost_days_fn(market), log=log)
    if rep is not None and publish_manifest and os.path.exists(target_csv):
        try:
            from shared.data_library.manifest_manager import ManifestManager
            ManifestManager.generate_and_save_manifest(data_file_path=target_csv, data_tier="raw", provider=provider.lower(),
                                                       market=market, timezone_name=timezone_name,
                                                       notes=f"raw_ingest {interval or ''}".strip())
        except Exception as e:
            log(f"   ⚠️ [Raw Ingest] 매니페스트 발행 실패({os.path.basename(target_csv)}): {e}")
    return rep


# ─────────────────────────────────────────────────────────────────────────────
# 무엇을 받아야 하는가 — 모든 수집기가 같은 규칙으로 수집 구간을 정한다
#   · 파일이 없으면: 설정한 수집 시작(ARCHIVE_START_YM)부터 지금까지
#   · 파일 시작이 수집 시작보다 늦으면: 앞쪽(수집 시작 ~ 파일 시작)을 채운다
#     (원천에 그보다 옛 데이터가 없으면 한 번 확인한 뒤 원장에 기록해 다시 시도하지 않는다)
#   · 그리고 마지막 기록(또는 내부 결측 갭)부터 지금까지
# ─────────────────────────────────────────────────────────────────────────────

def archive_start() -> datetime:
    """UI '원천 데이터 수집 기간 설정'의 시작 년월(ARCHIVE_START_YM)의 1일 00:00 (KST, naive)."""
    ym = None
    try:
        from shared.config.config_manager import load_common_config
        ym = (load_common_config() or {}).get("ARCHIVE_START_YM")
    except Exception:
        pass
    try:
        return datetime.strptime(str(ym)[:7] + "-01", "%Y-%m-%d")
    except (TypeError, ValueError):
        return datetime(datetime.now().year - 2, 1, 1)


def _kst(raw: Optional[str]) -> Optional[datetime]:
    """원천 파일의 시각 문자열 → KST naive datetime (epoch 는 UTC 로 보고 +9h, 문자열은 KST 그대로)."""
    if not raw:
        return None
    raw = raw.strip().strip('"')
    try:
        if raw.replace(".", "", 1).isdigit() and "-" not in raw:
            return datetime.strptime(rds._norm_ts(raw), "%Y-%m-%d %H:%M:%S") + timedelta(hours=9)
        return datetime.strptime(raw.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None


def first_timestamp(csv_path: str) -> Optional[str]:
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return None
    lines = rds._read_lines(csv_path)
    next(lines, None)
    for line in lines:
        raw = line.split(",", 1)[0].strip().strip('"')
        if rds._valid_ts(rds._norm_ts(raw)):
            return raw
    return None


def _head_checked(csv_path: str, start: datetime, first: datetime) -> bool:
    """수집 시작 ~ 파일 시작 구간을 이미 확인해 '원천에 없음'으로 기록했는가."""
    s, f = start.strftime("%Y-%m-%d"), first.strftime("%Y-%m-%d")
    return any(a < s and b >= f for a, b in rds.unrecoverable_ranges(csv_path))


def fetch_ranges(csv_path: str, tail_from: Optional[datetime] = None) -> List[tuple]:
    """이번 수집에서 받을 구간 [(시작 KST, 끝 KST 또는 None=지금, 종류)] — 종류: 'full' | 'head' | 'tail'.
    tail_from: 수집기가 계산한 이어받기 기점(내부 결측 갭 고려). 없으면 파일의 마지막 시각."""
    start = archive_start()
    first = _kst(first_timestamp(csv_path))
    if first is None:
        return [(start, None, "full")]
    out = []
    if first > start and not _head_checked(csv_path, start, first):
        out.append((start, first, "head"))
    out.append((tail_from or _kst(last_timestamp(csv_path)) or start, None, "tail"))
    return out


def note_head_floor(csv_path: str, oldest_received: Optional[datetime], log: Callable[[str], None] = print) -> None:
    """앞쪽 채우기('full'/'head')를 끝까지 받았는데 가장 오래된 데이터가 수집 시작보다 늦으면, 그 앞은 원천에 없다(상장 전·보존 기간 밖).
    원장에 기록해 다음 동기화가 같은 구간을 다시 시도하지 않게 한다."""
    start = archive_start()
    oldest = oldest_received or _kst(first_timestamp(csv_path))
    if oldest is None or not os.path.exists(csv_path) or oldest.date() <= start.date():
        return
    after = (start - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    rds.record_unrecoverable(csv_path, after, oldest.strftime("%Y-%m-%d %H:%M:%S"),
                             reason=f"수집 시작({start:%Y-%m}) 이후 원천이 주는 가장 오래된 데이터 이전 — 원천에 없음")
    log(f"   ℹ️ [Raw Ingest] {os.path.basename(csv_path)}: 원천의 가장 오래된 데이터가 {oldest:%Y-%m-%d %H:%M} — 그 이전(수집 시작 {start:%Y-%m}~)은 원천에 없어 기록해 둡니다")
