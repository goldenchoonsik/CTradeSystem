# -*- coding: utf-8 -*-
"""
CTradeSystem Shared - Gap Healing (원천 CSV 내부 결측 갭 탐지)

`CAiModelBaker/core/creon_collector.py`의 `get_existing_last_timestamp`
(Self-Healing Gap Recovery)와 동일한 원리를 CREON 외 출처(KIS/Yahoo 해외,
업비트/바이낸스/코인베이스 크립토)에도 적용하기 위한 공용 유틸리티다.

파일 끝에서부터 역순으로 최근 N개 고유 날짜와 그 날짜의 마지막 타임스탬프를
스캔하고, `shared.market_calendar.is_trading_day`로 인접 날짜 사이에
휴장일/주말이 아닌 진짜 거래일 결측이 있는지 확인한다. 결측이 발견되면
"파일의 진짜 마지막 줄"이 아니라 "결측 직전 시점"을 반환하여, 호출자가
그 시점부터 다시 수집하도록 유도한다(=자동 백필).

외부 라이브러리 의존 없이 순수 파일 I/O만 사용하므로 pandas가 없는 환경에서도
안전하게 동작한다.
"""
import os
from datetime import datetime, timedelta
from typing import Callable, Optional


def _to_kst_text(ts_val: str) -> str:
    """epoch(초/ms/us/ns, UTC) 값이면 KST 'YYYY-MM-DD HH:MM:SS' 로 바꾼다 (바이낸스 옛 원천 형식). 그 밖의 값은 그대로.
    옛 코드는 epoch 의 앞 10자리를 날짜로 읽으려다 예외가 나 결측 되감기 기준점을 잃었다."""
    if ts_val and ts_val.replace(".", "", 1).isdigit() and "-" not in ts_val:
        try:
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            n = float(ts_val)
            digits = len(str(int(n)))
            div = 1.0 if digits <= 10 else 1e3 if digits <= 13 else 1e6 if digits <= 16 else 1e9
            return _dt.fromtimestamp(n / div, tz=_tz.utc).astimezone(_tz(_td(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OverflowError, OSError):
            return ts_val
    return ts_val


def find_gap_checkpoint(
    csv_path: str,
    market: str = "KRX",
    max_scan_dates: int = 60,
    local_log: Callable[[str], None] = print,
) -> Optional[str]:
    """CSV 파일의 실제 수집 재개 기준점(checkpoint)을 반환한다.

    Returns:
        - 파일이 없거나 비어있으면 None
        - 결측 갭이 없으면 파일의 진짜 마지막(최신) 타임스탬프 문자열
        - 인접 날짜 사이에 거래일 결측 갭이 발견되면, 그 갭 직전 날짜의
          마지막 타임스탬프 문자열 (호출자는 이 시점부터 재수집해야 갭이 메워진다)
    """
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return None

    try:
        file_size = os.path.getsize(csv_path)

        # 1. 헤더에서 timestamp 컬럼 인덱스 파악
        ts_col_idx = 0
        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            header_line = f.readline()
            if header_line:
                headers = [h.strip().strip('"') for h in header_line.split(',')]
                for idx, h in enumerate(headers):
                    if h.lower() in ('timestamp', 'datetime', 'date'):
                        ts_col_idx = idx
                        break

        # 2. 파일 끝에서부터 역방향 블록 버퍼링으로 최근 max_scan_dates일 날짜/마지막 타임스탬프 추출
        buffer_size = 64 * 1024
        pos = file_size
        remainder = ""
        date_last_ts = {}
        latest_ts = None

        with open(csv_path, 'rb') as f:
            while pos > 0 and len(date_last_ts) < max_scan_dates:
                read_len = min(pos, buffer_size)
                pos -= read_len
                f.seek(pos)
                chunk = f.read(read_len).decode('utf-8', errors='ignore') + remainder
                lines = chunk.splitlines()

                if pos > 0:
                    remainder = lines[0]
                    lines = lines[1:]
                else:
                    remainder = ""
                    if lines and any(h in lines[0].lower() for h in ('timestamp', 'datetime', 'date')):
                        lines = lines[1:]

                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(',')
                    if len(parts) > ts_col_idx:
                        ts_val = _to_kst_text(parts[ts_col_idx].strip().strip('"'))
                        if len(ts_val) >= 10 and ts_val[:4].isdigit():
                            if latest_ts is None:
                                latest_ts = ts_val
                            d_str = ts_val[:10]
                            if d_str not in date_last_ts:
                                date_last_ts[d_str] = ts_val
                                if len(date_last_ts) >= max_scan_dates:
                                    break

        if not date_last_ts:
            return latest_ts

        sorted_dates = sorted(date_last_ts.keys())
        if len(sorted_dates) <= 1:
            return latest_ts

        try:
            from shared.core.market_calendar import is_trading_day
        except Exception:
            is_trading_day = lambda dt, mkt="KRX": dt.weekday() < 5

        for i in range(len(sorted_dates) - 1, 0, -1):
            d_curr = datetime.strptime(sorted_dates[i], '%Y-%m-%d')
            d_prev = datetime.strptime(sorted_dates[i - 1], '%Y-%m-%d')
            cur = d_prev + timedelta(days=1)
            missing_days = []
            while cur < d_curr:
                if is_trading_day(cur, market):
                    missing_days.append(cur.strftime("%Y-%m-%d"))
                cur += timedelta(days=1)

            if missing_days:
                gap_date = sorted_dates[i - 1]
                gap_ts = date_last_ts[gap_date]
                local_log(f"   🔍 [Auto-Healing/{market}] 파일 내부 결측 갭 감지: {missing_days} 누락 ➔ 갭 이전 시점({gap_ts})까지 백필 수신")
                return gap_ts

        return latest_ts
    except Exception as e:
        local_log(f"   ⚠️ [Auto-Healing/{market}] 갭 스캔 예외: {e}")
        return None
