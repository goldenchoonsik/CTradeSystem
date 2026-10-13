# -*- coding: utf-8 -*-
"""
================================================================================
[STRICT SYSTEM PRINCIPLE] 대신증권 (CREON / CYBOS Plus) 사용 범위 절대 제한 원칙
1. 대신증권(CREON)은 오직 'AI 모델 학습용 과거 틱(Tick) 및 분봉 대량 데이터 다운로드'에만 사용한다.
2. 실시간 시세 수신, 호가 분석, 실전 매매 주문, 잔고 관리 등 모든 실시간 트레이딩(CTrade)에는 일절 사용하지 않는다.
3. 실시간 주식/ETF 트레이딩은 전적으로 '한국투자증권(KIS OpenAPI)'이 전담한다.
================================================================================
대신증권 (CREON / CYBOS Plus) 초경량 스트리밍 데이터 수집기
- 32-bit Windows Python 환경의 메모리 한계(2GB)를 원천 극복하기 위해 메모리 연산을 100% 배제
- 수신되는 즉시 디스크 파일에 1줄씩 순차 기록 (메모리 점유율 < 1MB)
- 최종 정렬, 중복 제거, 캔들 리샘플링은 64-bit Linux 엔진(data_manager.py)이 전담
"""

import sys
import os
import time
import csv
import json
import argparse
from datetime import datetime

# Windows 콘솔 유니코드/이모지 출력 인코딩 오류 방지
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# [T-5] CWD 상대경로 금지 — 본 파일은 32-bit Windows 에서 실행되므로
#       shared 패키지(pandas/numpy 유입)를 import 하지 않고 자체적으로 루트를 해석한다.
#       (메모리 점유 < 1MB 설계 유지)
_BAKER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # core/ → CAiModelBaker
_REPO_ROOT = os.path.dirname(_BAKER_ROOT)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_DATA_ROOT = os.path.join(_BAKER_ROOT, "data")
_default_vault = os.path.join(_REPO_ROOT, "DataVault")
if not os.path.exists(_default_vault):
    _legacy_vault = os.path.join(_REPO_ROOT, "data_vault")
    if os.path.exists(_legacy_vault):
        _default_vault = _legacy_vault
_DATA_VAULT_ROOT = os.environ.get("CTRADE_DATA_VAULT_ROOT") or _default_vault
KOSPI200_FUTURES_KEY = "10100"   # 종목 원장 키 (연속 선물의 대체 코드 101V000 도 같은 파일에 쌓는다)


def _macro_raw_csv(ledger_key, interval):
    """국내 선물/지수 원천 CSV 경로 — 종목 원장 키로 이름 짓는다 (10100_1m.csv, U180_1m.csv).
    (shared 패키지를 import 하지 않는 32-bit 설계라 shared.data_library.paths.macro_raw_path 와 같은 규칙을 여기에 둔다)"""
    return os.path.join(_DATA_VAULT_ROOT, "raw", "creon", f"candle_{interval}m", f"{ledger_key}_{interval}m.csv")

# 플랫폼 확인
IS_WINDOWS = sys.platform.startswith("win")


def check_creon_connection():
    """크레온/CYBOS Plus 연결 및 로그인 상태 확인"""
    if not IS_WINDOWS:
        return False, "Windows OS 환경이 아닙니다."
    
    try:
        import win32com.client
        cp_cybos = win32com.client.Dispatch("CpUtil.CpCybos")
        if cp_cybos.IsConnect != 1:
            return False, "대신증권(CREON/CYBOS Plus)이 실행 및 로그인되어 있지 않습니다. 관리자 권한으로 로그인해 주세요."
        return True, "연결 정상"
    except Exception as e:
        return False, f"COM 객체 초기화 실패: {e}"


def check_symbol_exists(symbol: str) -> dict:
    """CREON 종목코드 마스터(CpUtil.CpCodeMgr)에서 코드 실재 여부와 정식 명칭을 조회한다.
    실시간 구독이 아닌 마스터 조회 1회성 호출이므로 '규칙 6'(CREON 실시간 금지)을 위반하지 않는다."""
    if not IS_WINDOWS:
        return {"exists": False, "name": "", "error": "Windows OS 환경이 아닙니다."}
    is_ok, msg = check_creon_connection()
    if not is_ok:
        return {"exists": False, "name": "", "error": msg}
    try:
        import win32com.client
        clean = _clean_sym(symbol)
        query_code = clean if clean.upper().startswith("A") else f"A{clean}"
        code_mgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")
        name = code_mgr.CodeToName(query_code)
        if name:
            return {"exists": True, "name": name, "queried_code": query_code}
        return {"exists": False, "name": "", "queried_code": query_code, "error": "CREON 종목 마스터에서 해당 코드를 찾을 수 없습니다."}
    except Exception as e:
        return {"exists": False, "name": "", "error": f"COM 조회 실패: {e}"}


def _clean_sym(symbol) -> str:
    """크레온 'A'+7자리 접두사만 제거 (shared SourceMappingManager.clean_symbol 재사용).
    shared 를 import 할 수 없는 환경(32-bit 경량 실행)에서는 동일 규칙으로 폴백한다."""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        return SourceMappingManager.clean_symbol(symbol)
    except Exception:
        s = str(symbol).strip().upper()
        if s.startswith("A") and len(s) == 7 and s[1:].isalnum():
            return s[1:]
        return s


def _uniq_tmp(path: str, tag: str) -> str:
    """동시 실행 충돌 방지용 pid/uuid 포함 tmp 파일명"""
    import uuid
    return f"{path}.{tag}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"


def get_existing_last_timestamp(csv_path: str, local_log=print) -> str:
    """CSV 파일의 수집 중단 기준점(checkpoint)을 추출한다.
    [Self-Healing Gap Recovery - Pure Python Zero-Dependency Scanner]
    - 파일 끝에서부터 버퍼(64KB) 단위로 역순 스캔하여 최근 60개 고유 거래일과 타임스탬프를 추출한다.
    - 거래일 판정 캘린더(shared.market_calendar.is_trading_day)와 대조하여,
      주말/공휴일이 아닌 실제 KRX 개장일 누락 갭이 감지되면
      갭 직전 거래일의 마지막 타임스탬프를 반환하여 누락 구간을 자동 백필(Backfill)한다.
    - 결측 갭이 없으면 파일의 맨 마지막(최신) 타임스탬프를 반환하여 초고속 증분 수집을 진행한다.
    - 외부 라이브러리(pandas)에 전혀 의존하지 않으므로 32-bit/64-bit 모든 환경에서 0.05초 만에 메모리 걱정 없이 작동한다.
    """
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return None

    try:
        from datetime import datetime, timedelta
        file_size = os.path.getsize(csv_path)

        # 1. 파일 헤더에서 timestamp 컬럼 인덱스 파악
        ts_col_idx = 0
        with open(csv_path, 'r', encoding='utf-8', errors='ignore') as f:
            header_line = f.readline()
            if header_line:
                headers = [h.strip().strip('\"') for h in header_line.split(',')]
                for idx, h in enumerate(headers):
                    if h.lower() in ('timestamp', 'datetime', 'date'):
                        ts_col_idx = idx
                        break

        # 2. 파일 끝에서부터 역방향 블록 버퍼링으로 최근 60거래일 날짜 및 마지막 타임스탬프 추출
        buffer_size = 64 * 1024
        pos = file_size
        remainder = ""
        date_last_ts = {}  # { 'YYYY-MM-DD': 'YYYY-MM-DD HH:MM:SS' (해당 날짜의 가장 늦은 타임스탬프) }
        latest_ts = None
        max_scan_dates = 60

        with open(csv_path, 'rb') as f:
            while pos > 0 and len(date_last_ts) < max_scan_dates:
                read_len = min(pos, buffer_size)
                pos -= read_len
                f.seek(pos)
                chunk = f.read(read_len).decode('utf-8', errors='ignore') + remainder
                lines = chunk.splitlines()

                # 파일의 맨 처음이 아니면 첫 라인은 앞 청크와 연결되어 잘렸을 수 있으므로 remainder로 보관
                if pos > 0:
                    remainder = lines[0]
                    lines = lines[1:]
                else:
                    remainder = ""
                    # 파일 맨 앞이면 첫 줄(헤더) 제외
                    if lines and any(h in lines[0].lower() for h in ('timestamp', 'datetime', 'date')):
                        lines = lines[1:]

                # 역순으로 라인 파싱
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(',')
                    if len(parts) > ts_col_idx:
                        ts_val = parts[ts_col_idx].strip().strip('\"')
                        if len(ts_val) >= 10 and ts_val[:4].isdigit():
                            if latest_ts is None:
                                latest_ts = ts_val
                            d_str = ts_val[:10]
                            # 역순으로 읽으므로 처음 만나는 ts_val이 그 날짜의 가장 늦은(마지막) 타임스탬프임
                            if d_str not in date_last_ts:
                                date_last_ts[d_str] = ts_val
                                if len(date_last_ts) >= max_scan_dates:
                                    break

        if not date_last_ts:
            return latest_ts

        sorted_dates = sorted(date_last_ts.keys())
        if len(sorted_dates) <= 1:
            return latest_ts

        # 3. 거래일 판정 함수 준비 (shared.market_calendar 연동)
        try:
            from shared.core.market_calendar import is_trading_day
        except Exception:
            try:
                repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                if repo_root not in sys.path:
                    sys.path.insert(0, repo_root)
                from shared.core.market_calendar import is_trading_day
            except Exception:
                is_trading_day = lambda dt, mkt="KRX": dt.weekday() < 5

        # 4. 역순으로 인접 거래일 사이의 결측 갭(KRX 개장일 누락) 탐색 — 원장에 '영구 결손'(서버에 더 이상 없음)으로 기록된 날은 제외
        try:
            _store = _load_day_store()
            _ranges = _store.unrecoverable_ranges(csv_path)
            _is_lost = lambda d: _store.is_unrecoverable(d, _ranges)
        except Exception:
            _is_lost = lambda d: False
        for i in range(len(sorted_dates) - 1, 0, -1):
            d_curr = datetime.strptime(sorted_dates[i], '%Y-%m-%d')
            d_prev = datetime.strptime(sorted_dates[i - 1], '%Y-%m-%d')
            cur = d_prev + timedelta(days=1)
            missing_biz_days = []
            while cur < d_curr:
                if is_trading_day(cur, "KRX") and not _is_lost(cur.strftime("%Y-%m-%d")):
                    missing_biz_days.append(cur.strftime("%Y-%m-%d"))
                cur += timedelta(days=1)

            if missing_biz_days:
                gap_date = sorted_dates[i - 1]
                gap_ts = date_last_ts[gap_date]
                local_log(f"   🔍 [Auto-Healing] 파일 내부 결측 갭 감지: {missing_biz_days} 누락 ➔ 갭 이전 시점({gap_ts})까지 백필 수신")
                return gap_ts

        return latest_ts
    except Exception as e:
        local_log(f"   ⚠️ [Auto-Healing] 갭 스캔 예외: {e}")
        return None


def _load_tick_day_store():
    """일자별 틱 저장소(tick_day_store) — 패키지/스크립트 실행 모두 지원 (32-bit Windows, 표준 라이브러리만)."""
    try:
        from . import tick_day_store as tds
    except ImportError:
        import tick_day_store as tds
    return tds


def _load_candle_day_store():
    """일자별 1분봉 저장소(candle_day_store) — 패키지/스크립트 실행 모두 지원 (32-bit Windows, 표준 라이브러리만)."""
    try:
        from . import candle_day_store as cds
    except ImportError:
        import candle_day_store as cds
    return cds


def _candle_day_key(out_csv, interval):
    """1분봉 저장 경로가 Creon 원천 폴더의 논리 경로(`candle_1m/<키>_1m.csv`)이면 일자 저장소의 종목 키, 아니면 None.
    일자 저장소는 1분봉만 쓴다 — 다른 봉 간격이나 다른 폴더(--out 직접 지정)는 종전의 단일 CSV 로 저장한다."""
    if int(interval) != 1 or not out_csv:
        return None
    root = os.path.abspath(os.path.join(_DATA_VAULT_ROOT, "raw", "creon", "candle_1m"))
    base = os.path.basename(out_csv)
    if os.path.abspath(os.path.dirname(os.path.abspath(out_csv))) == root and base.endswith("_1m.csv"):
        return base[: -len("_1m.csv")]
    return None


def _candle_day_root():
    return os.path.join(_DATA_VAULT_ROOT, "raw", "creon", "candle_1m")


def _candle_day_resume(key, local_log=print):
    return _load_candle_day_store().resume_timestamp(_candle_day_root(), key, _krx_trading_day_fn(), log=local_log)


def _candle_day_seal(key, local_log=print):
    """마감된 1분봉 날을 봉인한다 — 구조 · 일봉 상한(일봉 파일이 있으면) · 틱 대조(틱이 봉인돼 있으면). 다른 데이터를 기다리지 않는다."""
    daily = os.path.join(_DATA_VAULT_ROOT, "raw", "creon", "candle_1d", f"{key}_1d.csv")
    cds = _load_candle_day_store()
    cds.seal_closed_days(_candle_day_root(), key, daily_csv=daily if os.path.exists(daily) else None,
                         tick_root=os.path.join(_DATA_VAULT_ROOT, "raw", "creon", "tick"), log=local_log)
    return cds.day_dir(_candle_day_root(), key)


def _candle_day_save(tmp_out, key, last_ts, reached_existing_point, server_exhausted, collected_at, local_log=print):
    """수신분을 1분봉 일자 저장소에 병합하고(봉인일 불변·구조 불변식·완결된 날만) 마감일을 봉인한다. 실패 시 None."""
    cds = _load_candle_day_store()
    rep = cds.ingest(tmp_out, key, candle_root=_candle_day_root(), quarantine_dir=os.path.join(_DATA_VAULT_ROOT, "quarantine"),
                     requested_after=None if reached_existing_point else last_ts, source_exhausted=server_exhausted,
                     lost_days_fn=_trading_days_between, collected_at=collected_at, log=local_log)
    if rep is None:
        return None
    return _candle_day_seal(key, local_log)


def _tick_seal(tds, vault_tick_dir, symbol, local_log=print):
    """마감된 틱 날을 봉인한다 — 기준은 **봉인된** 1분봉 거래량(1분봉 봉인이 먼저, 아직 봉인 전인 날은 대기)."""
    cds = _load_candle_day_store()
    root = _candle_day_root()
    return tds.seal_closed_days(vault_tick_dir, symbol, ref_volumes=lambda days: cds.sealed_volumes(root, symbol, days), log=local_log)


def _krx_trading_day_fn():
    try:
        from shared.core.market_calendar import is_trading_day
        return is_trading_day
    except Exception:
        return lambda dt, mkt="KRX": dt.weekday() < 5


def _load_day_store():
    """날짜 단위 무결성 엔진(raw_day_store) — 패키지로 import 될 때와 `py core\\creon_collector.py` 스크립트로 실행될 때(32-bit Windows) 모두 지원."""
    try:
        from . import raw_day_store as store
    except ImportError:
        import raw_day_store as store
    return store


def merge_and_save_data(new_tmp_csv: str, target_csv: str, subset_keys=None, local_log=print,
                        reference_csv: str = None, volume_col: str = None, interval_seconds: int = None,
                        requested_after: str = None, source_exhausted: bool = False) -> str:
    """신규 수신분을 원천에 저장한다 — 모든 원천이 거치는 공통 절차 `raw_day_store.ingest_file` 을 그대로 쓴다.

    - 수신 순서와 무관한 정렬, 날짜 단위 불변식·백업·원장, 봉인된 과거와 다른 데이터는 '확인 필요', 실패 시 원본 유지 + 신규분 격리.
    - reference_csv: 날짜별 거래량을 대조할 상위 원천(틱→1분봉).
    - requested_after/source_exhausted: 기존 기록 지점과 '서버가 더 줄 데이터 없음' 여부 — 닿지 못했으면 영구 결손으로 기록.
    - subset_keys 는 하위 호환용 인자다(병합 단위는 '같은 시각 묶음'으로 고정).
    """
    store = _load_day_store()
    # 격리/백업 폴더: 대상이 DataVault 안이면 DataVault/quarantine, 밖(레거시 경로·임시 경로)이면 그 파일 옆에 둔다
    vault_abs = os.path.abspath(_DATA_VAULT_ROOT)
    in_vault = os.path.abspath(target_csv).startswith(vault_abs + os.sep)
    quarantine = os.path.join(vault_abs, "quarantine") if in_vault else os.path.join(os.path.dirname(os.path.abspath(target_csv)), ".merge_quarantine")
    rep = store.ingest_file(new_tmp_csv, target_csv, quarantine_dir=quarantine, interval_seconds=interval_seconds,
                            reference_csv=reference_csv, volume_col=volume_col, newest_first=True,
                            requested_after=requested_after, source_exhausted=source_exhausted,
                            lost_days_fn=_trading_days_between, log=local_log)
    if rep is None:
        return None
    if not os.path.exists(target_csv):
        return None
    led = store.load_ledger(target_csv) or {}
    odd = [(d, led["days"][d].get("ref_ratio")) for d in rep.get("touched_days", [])
           if d in led.get("days", {}) and led["days"][d].get("ref_ratio") is not None and abs(led["days"][d]["ref_ratio"] - 1.0) > 0.02]
    if odd:
        local_log(f"   ⚠️ [Raw Merge] 상위 원천 대비 거래량이 맞지 않는 날짜 {len(odd)}개(부실 수신 가능): "
                  + ", ".join(f"{d}={r}" for d, r in odd[:6]))
    return target_csv


def _trading_days_between(after_day: str, before_day: str) -> list:
    """after_day 와 before_day 사이(양 끝 제외)의 KRX 거래일 (shared.market_calendar, 없으면 평일)"""
    from datetime import datetime, timedelta
    try:
        from shared.core.market_calendar import is_trading_day
    except Exception:
        is_trading_day = lambda dt, mkt="KRX": dt.weekday() < 5
    cur = datetime.strptime(after_day, "%Y-%m-%d") + timedelta(days=1)
    end = datetime.strptime(before_day, "%Y-%m-%d")
    out = []
    while cur < end:
        if is_trading_day(cur, "KRX"):
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def download_tick_data(symbol, count=99999999, force_full=False, progress_callback=None, local_log=print):
    """
    대신증권 StockChart COM 객체를 통한 틱(Tick) 데이터 대량 수집 (증분 수집 및 영구 아카이브 보존)
    """
    if not IS_WINDOWS:
        local_log("[Creon] Windows 환경이 아닙니다. Windows 32-bit Python 환경에서 실행해야 합니다.")
        return None

    import win32com.client
    
    clean_sym = symbol.strip()
    if clean_sym.startswith("A"):
        core_code = clean_sym[1:]
        clean_sym = f"A{core_code.zfill(6)}" if core_code.isdigit() and len(core_code) < 6 else clean_sym
    elif clean_sym.isdigit():
        clean_sym = f"A{clean_sym.zfill(6)}"
    elif len(clean_sym) == 6 and clean_sym.isalnum():
        clean_sym = f"A{clean_sym}"
        
    # 틱은 일자별 저장소에만 쓴다: raw/creon/tick/A{sym}/YYYY-MM-DD.csv (+ .meta.json)
    vault_tick_dir = os.path.join(_DATA_VAULT_ROOT, "raw", "creon", "tick")
    os.makedirs(vault_tick_dir, exist_ok=True)
    tds = _load_tick_day_store()

    # 기존 데이터 최신 시점 확인 (증분 수집 모드)
    last_ts = None
    if not force_full:
        last_ts = tds.resume_timestamp(vault_tick_dir, clean_sym, _krx_trading_day_fn(), log=local_log)
        if last_ts:
            local_log(f"[Creon] 🔍 일자 저장소 감지 ➔ 최신 시점({last_ts}) 이후 신규 틱만 증분 수집")
    
    if not last_ts:
        local_log(f"[Creon] {clean_sym} 틱(Tick) 전체 수집 시작 (목표: 최대 {count:,}틱 또는 서버 바닥)...")
    
    cp_cybos = win32com.client.Dispatch("CpUtil.CpCybos")
    stock_chart = win32com.client.Dispatch("CpSysDib.StockChart")
    
    # 0:날짜, 1:시간(HHMM), 2:시가, 3:고가, 4:저가, 5:종가(체결가), 8:거래량, 9:거래대금, 10:누적매도, 11:누적매수, 16:매수호가, 17:매도호가
    fields = [0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 16, 17]
    headers = [
        "timestamp", "upbit_open", "upbit_high", "upbit_low", "upbit_close", 
        "upbit_volume", "upbit_trade_price", "cvd", "bid", "ask", "spread", "mid_price"
    ]
    
    stock_chart.SetInputValue(0, clean_sym)
    stock_chart.SetInputValue(1, ord('2'))       # 개수로 요청
    stock_chart.SetInputValue(4, count)
    stock_chart.SetInputValue(5, fields)
    stock_chart.SetInputValue(6, ord('T'))       # 'T': 틱 차트
    stock_chart.SetInputValue(9, ord('0'))       # 0: 무수정주가 (실전 체결가 100% 일치)
    
    total_received = 0
    reached_existing_point = False
    server_exhausted = False
    
    # 임시 파일에 수신 즉시 스트리밍 작성 (메모리 1MB 미만 유지)
    tmp_out = _uniq_tmp(os.path.join(vault_tick_dir, f"{clean_sym}_tick.incoming"), "raw")
    collected_at = datetime.now()        # 서버 조회 시작 시각(로컬=KST) — 이 시각 기준 완결된 날만 저장한다
    with open(tmp_out, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.writer(f_out)
        writer.writerow(headers)
        
        while True:
            remain_time = cp_cybos.GetLimitRemainTime(1)
            remain_count = cp_cybos.GetLimitRemainCount(1)
            if remain_count <= 2:
                sleep_sec = (remain_time / 1000.0) + 0.1
                time.sleep(max(0.5, sleep_sec))
                
            stock_chart.BlockRequest()
            
            status = stock_chart.GetDibStatus()
            msg = stock_chart.GetDibMsg1()
            if status != 0:
                local_log(f"   [Creon] 응답 오류 ({status}): {msg}")
                break
                
            num_data = stock_chart.GetHeaderValue(3)
            if num_data <= 0:
                server_exhausted = True
                break
                
            for i in range(num_data):
                d_val = str(int(stock_chart.GetDataValue(0, i)))
                raw_t = str(int(stock_chart.GetDataValue(1, i)))
                
                # 4자리(HHMM) 또는 6자리(HHMMSS) 시간 안전 파싱
                if len(raw_t) <= 4:
                    t_val = raw_t.zfill(4)
                    ts_str = f"{d_val[:4]}-{d_val[4:6]}-{d_val[6:8]} {t_val[:2]}:{t_val[2:4]}:00"
                else:
                    t_val = raw_t.zfill(6)
                    ts_str = f"{d_val[:4]}-{d_val[4:6]}-{d_val[6:8]} {t_val[:2]}:{t_val[2:4]}:{t_val[4:6]}"
                    
                # 증분 모드일 때: 기존 저장 시점에 도달하면 수신 조기 종료 (과거 데이터 유실 원천 방지)
                if last_ts and ts_str < last_ts:
                    reached_existing_point = True
                    break
                    
                op = float(stock_chart.GetDataValue(2, i))
                hp = float(stock_chart.GetDataValue(3, i))
                lp = float(stock_chart.GetDataValue(4, i))
                cp = float(stock_chart.GetDataValue(5, i))
                vol = float(stock_chart.GetDataValue(6, i))
                tp = float(stock_chart.GetDataValue(7, i))
                cum_sell = float(stock_chart.GetDataValue(8, i))
                cum_buy = float(stock_chart.GetDataValue(9, i))
                bid = float(stock_chart.GetDataValue(10, i))
                ask = float(stock_chart.GetDataValue(11, i))
                cvd = cum_buy - cum_sell
                
                if bid <= 0:
                    bid = cp
                if ask <= 0:
                    ask = cp
                spread = max(0.0, ask - bid)
                mid = (ask + bid) / 2.0
                
                writer.writerow([ts_str, op, hp, lp, cp, vol, tp, cvd, bid, ask, spread, mid])
                total_received += 1
                
            if progress_callback:
                progress_callback(min(100, int((total_received / min(count, 3500000)) * 100)))
                
            local_log(f"   [Creon] 📥 수신 중: 누적 {total_received:,} 틱 수신 완료")
            
            if reached_existing_point:
                local_log(f"   [Creon] ⏹️ 기존 보관 시점({last_ts}) 도달 완료 ➔ 안전하게 조기 종료.")
                break
                
            if not stock_chart.Continue:
                server_exhausted = True
            if not stock_chart.Continue or total_received >= count:
                break
                
            time.sleep(0.05)
            
    if total_received == 0 and last_ts:
        local_log(f"[Creon] ℹ️ 이미 최신 데이터까지 모두 보관되어 있습니다. (신규 틱 없음: {last_ts})")
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        _tick_seal(tds, vault_tick_dir, clean_sym, local_log)
        return tds.day_dir(vault_tick_dir, clean_sym)
    elif total_received == 0:
        local_log("[Creon] 수신된 틱 데이터가 없습니다.")
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        return None

    # 날짜별 병합(봉인일 불변·구조 불변식) → 마감일 봉인(1분봉 대조)
    rep = tds.ingest(tmp_out, clean_sym, tick_root=vault_tick_dir, quarantine_dir=os.path.join(_DATA_VAULT_ROOT, "quarantine"),
                     newest_first=True, requested_after=None if reached_existing_point else last_ts,
                     source_exhausted=server_exhausted, lost_days_fn=_trading_days_between, collected_at=collected_at, log=local_log)
    if rep is None:
        return None
    _tick_seal(tds, vault_tick_dir, clean_sym, local_log)
    return tds.day_dir(vault_tick_dir, clean_sym)


def download_minute_data(symbol, interval=1, count=99999999, save_path=None, force_full=False, progress_callback=None, local_log=print):
    """
    대신증권 StockChart COM 객체를 통한 분봉 데이터 수집 (증분 수집 및 영구 아카이브 보존)
    """
    if not IS_WINDOWS:
        local_log("[Creon] Windows 환경이 아닙니다. Windows 32-bit Python 환경에서 실행해야 합니다.")
        return None

    import win32com.client
    
    clean_sym = symbol.strip()
    if clean_sym.startswith("A"):
        core_code = clean_sym[1:]
        clean_sym = f"A{core_code.zfill(6)}" if core_code.isdigit() and len(core_code) < 6 else clean_sym
    elif clean_sym.isdigit():
        clean_sym = f"A{clean_sym.zfill(6)}"
    elif len(clean_sym) == 6 and clean_sym.isalnum():
        clean_sym = f"A{clean_sym}"
        
    vault_candle_dir = os.path.join(_DATA_VAULT_ROOT, "raw", "creon", f"candle_{interval}m")
    out_csv = save_path or os.path.join(vault_candle_dir, f"{clean_sym}_{interval}m.csv")
    os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
    day_key = _candle_day_key(out_csv, interval)      # 1분봉은 일자별 저장소(raw/creon/candle_1m/A{sym}/YYYY-MM-DD.csv)에만 쓴다
    
    # 기존 데이터 최신 시점 확인 (증분 수집 모드)
    last_ts = None
    if day_key:
        if not force_full:
            last_ts = _candle_day_resume(day_key, local_log)
    elif not force_full and os.path.exists(out_csv) and os.path.getsize(out_csv) > 0:
        last_ts = get_existing_last_timestamp(out_csv, local_log=local_log)
    if last_ts:
        local_log(f"[Creon] 🔍 기존 아카이브 감지 ➔ 최신 시점({last_ts}) 이후 신규 분봉만 증분 수집(Incremental Upsert) 가동")
            
    if not last_ts:
        local_log(f"[Creon] {clean_sym} {interval}분봉 전체 수집 시작 (목표: 최대 {count:,}봉 또는 서버 바닥)...")
    
    collected_at = datetime.now()        # 서버 조회 시작 시각(로컬=KST) — 이 시각 기준 완결된 날만 저장한다
    cp_cybos = win32com.client.Dispatch("CpUtil.CpCybos")
    stock_chart = win32com.client.Dispatch("CpSysDib.StockChart")
    
    fields = [0, 1, 2, 3, 4, 5, 8, 9, 10, 11]
    headers = [
        "timestamp", "upbit_open", "upbit_high", "upbit_low", "upbit_close", 
        "upbit_volume", "upbit_trade_price", "cvd"
    ]
    
    stock_chart.SetInputValue(0, clean_sym)
    stock_chart.SetInputValue(1, ord('2'))
    stock_chart.SetInputValue(4, count)
    stock_chart.SetInputValue(5, fields)
    stock_chart.SetInputValue(6, ord('m'))
    stock_chart.SetInputValue(7, int(interval))
    stock_chart.SetInputValue(9, ord('0'))       # 0: 무수정
    
    total_received = 0
    reached_existing_point = False
    server_exhausted = False
    tmp_out = _uniq_tmp(out_csv, "raw")
    
    with open(tmp_out, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.writer(f_out)
        writer.writerow(headers)
        
        while True:
            remain_time = cp_cybos.GetLimitRemainTime(1)
            remain_count = cp_cybos.GetLimitRemainCount(1)
            if remain_count <= 2:
                sleep_sec = (remain_time / 1000.0) + 0.1
                time.sleep(max(0.5, sleep_sec))
                
            stock_chart.BlockRequest()
            
            status = stock_chart.GetDibStatus()
            msg = stock_chart.GetDibMsg1()
            if status != 0:
                local_log(f"   [Creon] 응답 오류 ({status}): {msg}")
                break
                
            num_data = stock_chart.GetHeaderValue(3)
            if num_data <= 0:
                server_exhausted = True
                break
                
            for i in range(num_data):
                d_val = str(int(stock_chart.GetDataValue(0, i)))
                t_val = str(int(stock_chart.GetDataValue(1, i))).zfill(4)
                try:
                    dt_obj = datetime.strptime(f"{d_val}{t_val}", "%Y%m%d%H%M")
                    ts_str = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    ts_str = f"{d_val[:4]}-{d_val[4:6]}-{d_val[6:8]} {t_val[:2]}:{t_val[2:4]}:00"
                    
                # 증분 모드일 때: 기존 저장 시점에 도달하면 수신 조기 종료
                if last_ts and ts_str < last_ts:
                    reached_existing_point = True
                    break
                    
                op = float(stock_chart.GetDataValue(2, i))
                hp = float(stock_chart.GetDataValue(3, i))
                lp = float(stock_chart.GetDataValue(4, i))
                cp = float(stock_chart.GetDataValue(5, i))
                vol = float(stock_chart.GetDataValue(6, i))
                tp = float(stock_chart.GetDataValue(7, i))
                cum_sell = float(stock_chart.GetDataValue(8, i))
                cum_buy = float(stock_chart.GetDataValue(9, i))
                cvd = cum_buy - cum_sell
                
                writer.writerow([ts_str, op, hp, lp, cp, vol, tp, cvd])
                total_received += 1
                
            if progress_callback:
                progress_callback(min(100, int((total_received / min(count, 500000)) * 100)))
                
            local_log(f"   [Creon] 📥 수신 중: 누적 {total_received:,} 봉 수신 완료")
            
            if reached_existing_point:
                local_log(f"   [Creon] ⏹️ 기존 보관 시점({last_ts}) 도달 완료 ➔ 안전하게 조기 종료.")
                break
                
            if not stock_chart.Continue:
                server_exhausted = True
            if not stock_chart.Continue or total_received >= count:
                break
                
            time.sleep(0.05)
            
    if total_received == 0 and last_ts:
        local_log(f"[Creon] ℹ️ 이미 최신 데이터까지 모두 보관되어 있습니다. (신규 분봉 없음: {last_ts})")
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        return _candle_day_seal(day_key, local_log) if day_key else out_csv
    elif total_received == 0:
        local_log("[Creon] 수신된 분봉 데이터가 없습니다.")
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        return None

    if day_key:
        saved = _candle_day_save(tmp_out, day_key, last_ts, reached_existing_point, server_exhausted, collected_at, local_log)
        if saved:
            local_log(f"[Creon] 💾 원시 분봉 데이터 일자별 저장 완료: {saved}")
        return saved

    final_path = merge_and_save_data(tmp_out, out_csv, subset_keys=['timestamp'], local_log=local_log,
                                     interval_seconds=60 * int(interval),
                                     requested_after=None if reached_existing_point else last_ts, source_exhausted=server_exhausted)
    if final_path and os.path.exists(final_path):
        local_log(f"[Creon] 💾 원시 분봉 데이터 수집 및 안전 보존 완료: {final_path}")
        try:
            from shared.data_library.manifest_manager import ManifestManager
            ManifestManager.create_manifest(
                data_path=final_path,
                provider="creon",
                data_tier="bronze",
                asset_type=f"candle_{interval}m",
                symbol=_clean_sym(clean_sym),
                market="KRX",
                notes=f"대신증권 {interval}분봉 데이터 수집 아카이브"
            )
            local_log(f"   📜 [Manifest] 사이드카 매니페스트 발행 완료: {final_path}.manifest.json")
        except Exception as me:
            local_log(f"   ⚠️ [Manifest] 매니페스트 발행 건너뜀: {me}")
    return final_path


def download_daily_data(symbol, count=99999999, save_path=None, force_full=False, progress_callback=None, local_log=print):
    """
    대신증권 StockChart COM 객체를 통한 일봉(Day) 데이터 장기 대량 수집 (수십 년 치 전 기간 일봉 아카이브)
    - 수정주가(SetInputValue 9, ord('1')) 적용으로 증자/배당락 무결성 100% 보장
    """
    if not IS_WINDOWS:
        local_log("[Creon] Windows 환경이 아닙니다. Windows 32-bit Python 환경에서 실행해야 합니다.")
        return None

    import win32com.client
    
    clean_sym = symbol.strip()
    if clean_sym.startswith("A"):
        core_code = clean_sym[1:]
        clean_sym = f"A{core_code.zfill(6)}" if core_code.isdigit() and len(core_code) < 6 else clean_sym
    elif clean_sym.isdigit():
        clean_sym = f"A{clean_sym.zfill(6)}"
    elif len(clean_sym) == 6 and clean_sym.isalnum():
        clean_sym = f"A{clean_sym}"
        
    vault_candle_dir = os.path.join(_DATA_VAULT_ROOT, "raw", "creon", "candle_1d")
    out_csv = save_path or os.path.join(vault_candle_dir, f"{clean_sym}_1d.csv")
    os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
    
    # 기존 데이터 최신 시점 확인 (증분 수집 모드)
    last_ts = None
    if not force_full and os.path.exists(out_csv) and os.path.getsize(out_csv) > 0:
        last_ts = get_existing_last_timestamp(out_csv, local_log=local_log)
        if last_ts:
            local_log(f"[Creon] 🔍 기존 일봉 아카이브 감지 ➔ 최신 시점({last_ts}) 이후 신규 일봉만 증분 수집(Incremental Upsert) 가동")
            
    if not last_ts:
        local_log(f"[Creon] {clean_sym} 일봉(Day) 장기 전체 수집 시작 (목표: 최대 {count:,}봉 또는 상장 이래 전 기간)...")
    
    cp_cybos = win32com.client.Dispatch("CpUtil.CpCybos")
    stock_chart = win32com.client.Dispatch("CpSysDib.StockChart")
    
    fields = [0, 2, 3, 4, 5, 8, 9]
    headers = [
        "timestamp", "upbit_open", "upbit_high", "upbit_low", "upbit_close", 
        "upbit_volume", "upbit_trade_price"
    ]
    
    stock_chart.SetInputValue(0, clean_sym)
    stock_chart.SetInputValue(1, ord('2'))       # 개수로 요청
    stock_chart.SetInputValue(4, count)          # 요청 개수
    stock_chart.SetInputValue(5, fields)         # 필드 목록
    stock_chart.SetInputValue(6, ord('D'))       # 'D': 일봉
    stock_chart.SetInputValue(9, ord('1'))       # 1: 수정주가 적용 (장기 시계열 필수)
    
    total_received = 0
    tmp_out = _uniq_tmp(out_csv, "stream")
    if os.path.exists(tmp_out):
        try:
            os.remove(tmp_out)
        except Exception:
            pass
            
    stop_early = False
    server_exhausted = False
    with open(tmp_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        
        while True:
            remain_req = cp_cybos.GetLimitRemainCount(1)
            if remain_req <= 2:
                time.sleep(1.0)
                
            ret = stock_chart.BlockRequest()
            if ret != 0:
                local_log(f"[Creon] 일봉 BlockRequest 실패 (코드: {ret})")
                break
                
            num_data = stock_chart.GetHeaderValue(3)
            if num_data <= 0:
                server_exhausted = True
                break
                
            for i in range(num_data):
                d_val = stock_chart.GetDataValue(0, i)
                o_val = stock_chart.GetDataValue(1, i)
                h_val = stock_chart.GetDataValue(2, i)
                l_val = stock_chart.GetDataValue(3, i)
                c_val = stock_chart.GetDataValue(4, i)
                v_val = stock_chart.GetDataValue(5, i)
                p_val = stock_chart.GetDataValue(6, i)
                
                d_str = str(d_val)
                # YYYYMMDD -> YYYY-MM-DD 00:00:00
                if len(d_str) == 8:
                    ts_formatted = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]} 00:00:00"
                else:
                    ts_formatted = d_str
                    
                if last_ts and ts_formatted < last_ts:
                    stop_early = True
                    break
                    
                writer.writerow([ts_formatted, o_val, h_val, l_val, c_val, v_val, p_val])
                total_received += 1
                
            if not stock_chart.Continue:
                server_exhausted = True
            if stop_early or not stock_chart.Continue:
                break
                
            time.sleep(0.2)
            
    if last_ts and total_received == 0:
        local_log(f"[Creon] ✅ 최신 일봉 데이터가 이미 완벽하게 동기화되어 있습니다 (추가 수신: 0건)")
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        return out_csv
    elif total_received == 0:
        local_log("[Creon] 수신된 일봉 데이터가 없습니다.")
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        return None
        
    final_path = merge_and_save_data(tmp_out, out_csv, subset_keys=['timestamp'], local_log=local_log,
                                     requested_after=None if stop_early else last_ts, source_exhausted=server_exhausted)
    if final_path and os.path.exists(final_path):
        local_log(f"[Creon] 💾 장기 일봉 원천 데이터 수집 및 안전 보존 완료: {final_path} (수신: {total_received:,}봉)")
        try:
            from shared.data_library.manifest_manager import ManifestManager
            ManifestManager.create_manifest(
                data_path=final_path,
                provider="creon",
                data_tier="raw",
                asset_type="candle_1d",
                symbol=_clean_sym(clean_sym),
                market="KRX",
                notes="대신증권 일봉(Day) 장기 아카이브"
            )
            local_log(f"   📜 [Manifest] 사이드카 매니페스트 발행 완료: {final_path}.manifest.json")
        except Exception as me:
            local_log(f"   ⚠️ [Manifest] 매니페스트 발행 건너뜀: {me}")
    return final_path


def _futures_ledger_and_candidates(clean_sym):
    """선물 코드 → (원장 키, Creon 조회 후보 코드 목록). 지수선물은 대체 코드(101V000)와 같은 원장 키를 쓴다."""
    alt = f"{KOSPI200_FUTURES_KEY[:3]}V000"
    if clean_sym in (KOSPI200_FUTURES_KEY, alt):
        order = [KOSPI200_FUTURES_KEY, alt] if clean_sym == KOSPI200_FUTURES_KEY else [alt, KOSPI200_FUTURES_KEY]
        return KOSPI200_FUTURES_KEY, order
    cands = [clean_sym]   # 개별주식선물은 원장의 Creon 연결 코드(예: 15000=SK하이닉스 연결)로 직접 조회한다
    return clean_sym, cands


def download_futures_minute_data(symbol, interval=1, count=99999999, save_path=None, force_full=False, progress_callback=None, local_log=print):
    """
    대신증권 CpFutureChart COM 객체를 통한 KOSPI 200 선물 분봉 데이터 수집 (증분 수집 및 영구 아카이브 보존)
    """
    if not IS_WINDOWS:
        local_log("[Creon] Windows 환경이 아닙니다. Windows 32-bit Python 환경에서 실행해야 합니다.")
        return None

    import win32com.client
    
    clean_sym = symbol.strip()
    ledger_key, candidate_symbols = _futures_ledger_and_candidates(clean_sym)
        
    out_csv = save_path or _macro_raw_csv(ledger_key, interval)   # 개별주식선물도 고유 원장 키 파일에 적재
    os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
    day_key = _candle_day_key(out_csv, interval)      # 1분봉은 일자별 저장소에만 쓴다(원장 키 폴더 A10100/)
    
    # 기존 데이터 최신 시점 확인 (증분 수집 모드)
    last_ts = None
    if day_key:
        if not force_full:
            last_ts = _candle_day_resume(day_key, local_log)
    elif not force_full and os.path.exists(out_csv) and os.path.getsize(out_csv) > 0:
        last_ts = get_existing_last_timestamp(out_csv, local_log=local_log)
    if last_ts:
        local_log(f"[Creon] 🔍 기존 아카이브 감지 ➔ 최신 시점({last_ts}) 이후 신규 선물 분봉만 증분 수집 가동")
    
    collected_at = datetime.now()        # 서버 조회 시작 시각(로컬=KST)
    cp_cybos = win32com.client.Dispatch("CpUtil.CpCybos")
    
    # 선물/옵션 전용 차트 객체 생성 (CpSysDib.CpFutOptChart)
    chart_obj = None
    for obj_name in ["CpSysDib.CpFutOptChart", "CpSysDib.FutOptChart", "CpSysDib.CpFutureChart"]:
        try:
            chart_obj = win32com.client.Dispatch(obj_name)
            break
        except Exception:
            continue
            
    if not chart_obj:
        local_log("❌ [Creon] 선물 차트 COM 객체 초기화 실패")
        return None

    fields = [0, 1, 2, 3, 4, 5, 8, 9, 10, 11]
    headers = [
        "timestamp", "upbit_open", "upbit_high", "upbit_low", "upbit_close", 
        "upbit_volume", "upbit_trade_price", "cvd"
    ]
    
    for sym in candidate_symbols:
        local_log(f"[Creon] 선물 심볼 '{sym}' {interval}분봉 데이터 수집 시도...")
        try:
            chart_obj.SetInputValue(0, sym)
            chart_obj.SetInputValue(1, ord('2'))
            chart_obj.SetInputValue(4, count)
            chart_obj.SetInputValue(5, fields)
            chart_obj.SetInputValue(6, ord('m'))
            chart_obj.SetInputValue(7, int(interval))
            chart_obj.SetInputValue(8, ord('0'))
        except Exception as e:
            local_log(f"   ⚠️ 선물 심볼 '{sym}' 입력 설정 실패: {e}")
            continue

        total_received = 0
        reached_existing_point = False
        server_exhausted = False
        tmp_out = _uniq_tmp(out_csv, "raw")
        
        with open(tmp_out, "w", newline="", encoding="utf-8") as f_out:
            writer = csv.writer(f_out)
            writer.writerow(headers)
            
            while True:
                remain_time = cp_cybos.GetLimitRemainTime(1)
                remain_count = cp_cybos.GetLimitRemainCount(1)
                if remain_count <= 2:
                    sleep_sec = (remain_time / 1000.0) + 0.1
                    time.sleep(max(0.5, sleep_sec))
                    
                chart_obj.BlockRequest()
                
                status = chart_obj.GetDibStatus()
                msg = chart_obj.GetDibMsg1()
                if status != 0:
                    local_log(f"   [Creon] 응답 오류 ({status}): {msg}")
                    break
                    
                num_data = chart_obj.GetHeaderValue(3)
                if num_data <= 0:
                    server_exhausted = True
                    break
                    
                for i in range(num_data):
                    d_val = str(int(chart_obj.GetDataValue(0, i)))
                    t_val = str(int(chart_obj.GetDataValue(1, i))).zfill(4)
                    try:
                        dt_obj = datetime.strptime(f"{d_val}{t_val}", "%Y%m%d%H%M")
                        ts_str = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        ts_str = f"{d_val[:4]}-{d_val[4:6]}-{d_val[6:8]} {t_val[:2]}:{t_val[2:4]}:00"
                        
                    # 증분 모드일 때: 기존 저장 시점에 도달하면 수신 조기 종료
                    if last_ts and ts_str < last_ts:
                        reached_existing_point = True
                        break
                        
                    op = float(chart_obj.GetDataValue(2, i))
                    hp = float(chart_obj.GetDataValue(3, i))
                    lp = float(chart_obj.GetDataValue(4, i))
                    cp = float(chart_obj.GetDataValue(5, i))
                    vol = float(chart_obj.GetDataValue(6, i))
                    tp = float(chart_obj.GetDataValue(7, i))
                    cum_sell = float(chart_obj.GetDataValue(8, i))
                    cum_buy = float(chart_obj.GetDataValue(9, i))
                    cvd = cum_buy - cum_sell
                    
                    writer.writerow([ts_str, op, hp, lp, cp, vol, tp, cvd])
                    total_received += 1
                    
                if progress_callback:
                    progress_callback(min(100, int((total_received / min(count, 500000)) * 100)))
                    
                local_log(f"   [Creon] 📥 수신 중: 누적 {total_received:,} 선물 봉 수신 완료")
                
                if reached_existing_point:
                    local_log(f"   [Creon] ⏹️ 기존 보관 시점({last_ts}) 도달 완료 ➔ 안전하게 조기 종료.")
                    break
                    
                if not chart_obj.Continue:
                    server_exhausted = True
                if not chart_obj.Continue or total_received >= count:
                    break
                    
                time.sleep(0.05)
                
        if total_received == 0 and last_ts:
            local_log(f"[Creon] ℹ️ 이미 최신 데이터까지 모두 보관되어 있습니다. (신규 선물 분봉 없음: {last_ts})")
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
            return _candle_day_seal(day_key, local_log) if day_key else out_csv
        elif total_received > 0 and day_key:
            saved = _candle_day_save(tmp_out, day_key, last_ts, reached_existing_point, server_exhausted, collected_at, local_log)
            if saved:
                local_log(f"[Creon] 💾 선물 분봉 데이터 일자별 저장 완료: {saved}")
            return saved
        elif total_received > 0:
            final_path = merge_and_save_data(tmp_out, out_csv, subset_keys=['timestamp'], local_log=local_log,
                                             interval_seconds=60 * int(interval),
                                             requested_after=None if reached_existing_point else last_ts, source_exhausted=server_exhausted)
            local_log(f"[Creon] 💾 선물 분봉 데이터 수집 및 안전 보존 완료: {final_path}")
            return final_path
        else:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
                
    local_log("⚠️ [Creon] 모든 선물 심볼에 대해 수신 가능한 분봉 데이터가 없습니다.")
    return None


def download_futures_tick_data(symbol="10100", count=99999999, force_full=False, progress_callback=None, local_log=print):
    """
    대신증권 CpFutOptChart COM 객체를 통한 KOSPI 200 선물 틱(Tick) 데이터 대량 수집 (증분 수집 및 영구 아카이브 보존)
    """
    if not IS_WINDOWS:
        local_log("[Creon] Windows 환경이 아닙니다. Windows 32-bit Python 환경에서 실행해야 합니다.")
        return None

    import win32com.client

    clean_sym = symbol.strip() if symbol else "10100"
    ledger_key, candidate_symbols = _futures_ledger_and_candidates(clean_sym)

    # 틱은 일자별 저장소에만 쓴다: raw/creon/tick/A{원장키}/YYYY-MM-DD.csv
    vault_tick_dir = os.path.join(_DATA_VAULT_ROOT, "raw", "creon", "tick")
    os.makedirs(vault_tick_dir, exist_ok=True)
    tds = _load_tick_day_store()

    # 기존 데이터 최신 시점 확인 (증분 수집 모드)
    last_ts = None
    if not force_full:
        last_ts = tds.resume_timestamp(vault_tick_dir, ledger_key, _krx_trading_day_fn(), log=local_log)
        if last_ts:
            local_log(f"[Creon] 🔍 일자 저장소 감지 ➔ 최신 시점({last_ts}) 이후 신규 선물 틱만 증분 수집")

    cp_cybos = win32com.client.Dispatch("CpUtil.CpCybos")

    chart_obj = None
    for obj_name in ["CpSysDib.CpFutOptChart", "CpSysDib.FutOptChart", "CpSysDib.CpFutureChart"]:
        try:
            chart_obj = win32com.client.Dispatch(obj_name)
            break
        except Exception:
            continue

    if not chart_obj:
        local_log("❌ [Creon] 선물 차트 COM 객체 초기화 실패")
        return None

    # 0:날짜, 1:시간(HHMMSS), 2:시가, 3:고가, 4:저가, 5:종가(체결가), 8:거래량, 9:거래대금, 10:누적매도, 11:누적매수, 17:매도호가, 18:매수호가
    fields = [0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 17, 18]
    headers = [
        "timestamp", "upbit_open", "upbit_high", "upbit_low", "upbit_close",
        "upbit_volume", "upbit_trade_price", "cvd", "bid", "ask", "spread", "mid_price"
    ]

    for sym in candidate_symbols:
        local_log(f"[Creon] 선물 심볼 '{sym}' 틱(Tick) 데이터 수집 시도...")
        try:
            chart_obj.SetInputValue(0, sym)
            chart_obj.SetInputValue(1, ord('2'))       # 개수로 요청
            chart_obj.SetInputValue(4, count)          # 요청 개수
            chart_obj.SetInputValue(5, fields)
            chart_obj.SetInputValue(6, ord('T'))       # 'T': 틱 차트
            chart_obj.SetInputValue(7, 1)              # 1틱
            chart_obj.SetInputValue(8, ord('0'))       # 무수정
        except Exception as e:
            local_log(f"   ⚠️ 선물 심볼 '{sym}' 틱 입력 설정 실패: {e}")
            continue

        total_received = 0
        reached_existing_point = False
        server_exhausted = False
        tmp_out = _uniq_tmp(os.path.join(vault_tick_dir, f"{ledger_key}_tick.incoming"), "raw")
        collected_at = datetime.now()    # 서버 조회 시작 시각(로컬=KST)

        with open(tmp_out, "w", newline="", encoding="utf-8") as f_out:
            writer = csv.writer(f_out)
            writer.writerow(headers)

            while True:
                remain_time = cp_cybos.GetLimitRemainTime(1)
                remain_count = cp_cybos.GetLimitRemainCount(1)
                if remain_count <= 2:
                    sleep_sec = (remain_time / 1000.0) + 0.1
                    time.sleep(max(0.5, sleep_sec))

                chart_obj.BlockRequest()

                status = chart_obj.GetDibStatus()
                msg = chart_obj.GetDibMsg1()
                if status != 0:
                    local_log(f"   [Creon] 응답 오류 ({status}): {msg}")
                    break

                num_data = chart_obj.GetHeaderValue(3)
                if num_data <= 0:
                    server_exhausted = True
                    break

                for i in range(num_data):
                    d_val = str(int(chart_obj.GetDataValue(0, i)))
                    raw_t = str(int(chart_obj.GetDataValue(1, i)))

                    if len(raw_t) <= 4:
                        raw_t = raw_t.zfill(4)
                        hh = raw_t[:2]
                        mm = raw_t[2:4]
                        ss = "00"
                    elif len(raw_t) == 5:
                        raw_t = raw_t.zfill(6)
                        hh = raw_t[:2]
                        mm = raw_t[2:4]
                        ss = raw_t[4:6]
                    else:
                        raw_t = raw_t.zfill(6)
                        hh = raw_t[:2]
                        mm = raw_t[2:4]
                        ss = raw_t[4:6]

                    ts_str = f"{d_val[:4]}-{d_val[4:6]}-{d_val[6:8]} {hh}:{mm}:{ss}"

                    if last_ts and ts_str < last_ts:
                        reached_existing_point = True
                        break

                    op = float(chart_obj.GetDataValue(2, i))
                    hp = float(chart_obj.GetDataValue(3, i))
                    lp = float(chart_obj.GetDataValue(4, i))
                    cp = float(chart_obj.GetDataValue(5, i))
                    vol = float(chart_obj.GetDataValue(6, i))
                    tp = float(chart_obj.GetDataValue(7, i))
                    cum_sell = float(chart_obj.GetDataValue(8, i))
                    cum_buy = float(chart_obj.GetDataValue(9, i))
                    cvd = cum_buy - cum_sell

                    try:    # 선물 틱 차트는 호가 필드(17/18)를 지원하지 않아 읽기가 COM 예외를 낸다 → 0(결측)
                        ask = float(chart_obj.GetDataValue(10, i))
                        bid = float(chart_obj.GetDataValue(11, i))
                    except Exception:
                        ask = bid = 0.0
                    spread = ask - bid if ask > 0 and bid > 0 else 0.0
                    mid_price = (ask + bid) / 2.0 if ask > 0 and bid > 0 else cp

                    writer.writerow([ts_str, op, hp, lp, cp, vol, tp, cvd, bid, ask, spread, mid_price])
                    total_received += 1

                if progress_callback:
                    try:
                        progress_callback(total_received, count)
                    except Exception:
                        pass

                if reached_existing_point:
                    local_log(f"   [Creon] 🎯 기존 저장된 최신 틱 시점({last_ts}) 도달 완료. 수신을 종료합니다.")
                    break

                if not chart_obj.Continue or total_received >= count:
                    if not chart_obj.Continue:
                        server_exhausted = True
                    break

                time.sleep(0.05)

        if total_received == 0 and last_ts:
            local_log(f"[Creon] ℹ️ 이미 최신 데이터까지 모두 보관되어 있습니다. (신규 선물 틱 없음: {last_ts})")
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
            _tick_seal(tds, vault_tick_dir, ledger_key, local_log)
            return tds.day_dir(vault_tick_dir, ledger_key)
        elif total_received > 0:
            rep = tds.ingest(tmp_out, ledger_key, tick_root=vault_tick_dir, quarantine_dir=os.path.join(_DATA_VAULT_ROOT, "quarantine"),
                             newest_first=True, requested_after=None if reached_existing_point else last_ts,
                             source_exhausted=server_exhausted, lost_days_fn=_trading_days_between, collected_at=collected_at, log=local_log)
            if rep is None:
                return None
            _tick_seal(tds, vault_tick_dir, ledger_key, local_log)
            return tds.day_dir(vault_tick_dir, ledger_key)
        else:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)

    local_log("⚠️ [Creon] 모든 선물 심볼에 대해 수신 가능한 틱 데이터가 없습니다.")
    return None


def download_index_minute_data(symbol, interval=1, count=99999999, save_path=None, force_full=False, progress_callback=None, local_log=print):
    """
    대신증권 StockChart를 통한 KOSPI 200 업종 지수 분봉 데이터 수집 (증분 수집 및 영구 아카이브 보존)
    """
    if not IS_WINDOWS:
        local_log("[Creon] Windows 환경이 아닙니다. Windows 32-bit Python 환경에서 실행해야 합니다.")
        return None

    clean_sym = symbol.strip()
    out_csv = save_path or _macro_raw_csv(clean_sym, interval)
    os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
    
    local_log(f"[Creon] 지수 {clean_sym} {interval}분봉 데이터 수집 시작...")
    return download_minute_data(symbol=clean_sym, interval=interval, count=count, save_path=out_csv, force_full=force_full, progress_callback=progress_callback, local_log=local_log)


def _get_configured_sources(symbol):
    """자산의 UI 원천 수집 대상(TICK, 1M, 1D 등) 목록을 조회. 미지정/실패 시 ['TICK', '1M', '1D'] 반환."""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        mappings = SourceMappingManager.load_all_mappings()
        clean_s = symbol.lstrip("A") if symbol.startswith("A") and len(symbol) == 7 else symbol
        info = mappings.get(clean_s) or mappings.get(symbol) or {}
        srcs = info.get("ingestion_sources")
        if srcs:
            return [str(s).upper() for s in srcs]
    except Exception:
        pass
    return ["TICK", "1M", "1D"]


def download_all_krx(target_symbol, futures_symbol, index_symbol, interval=1, count=99999999, force_full=False, ref_symbols=None, sources=None, local_log=print):
    """
    KODEX 200 틱/분봉/일봉, KOSPI 200 선물 분봉, KOSPI 200 지수 분봉 및
    Symbol Relations에 등록된 참조 대장주(Reference Symbols) 틱/분봉/일봉 일괄 수집 (증분 모드 기본 지원)
    - UI 원천 수집 대상(ingestion_sources)에 체크된 주기(TICK, 1M, 1D)만 조건부 수집
    """
    mode_text = "전체 재수신" if force_full else "안전 증분 누적(Incremental Upsert)"
    local_log(f"🚀 [Creon] KOSPI 200 & KODEX 200 전체 데이터셋 일괄 수집 가동 [모드: {mode_text}]...")
    
    tgt_sources = [s.strip().upper() for s in sources.split(",")] if isinstance(sources, str) else (list(sources) if sources else _get_configured_sources(target_symbol))
    local_log(f"   📋 타겟 {target_symbol} 수집 대상 주기: {', '.join(tgt_sources)}")

    # 0. 타겟 ETF 일봉 — 1분봉 봉인이 일봉 거래량 상한을 쓰므로 1분봉보다 먼저 받는다
    if "1D" in tgt_sources:
        try:
            download_daily_data(symbol=target_symbol, count=count, force_full=force_full, local_log=local_log)
        except Exception as e:
            local_log(f"   ⚠️ [1/6] 타겟 ETF 일봉 수집 예외: {e}")
    else:
        local_log(f"   ℹ️ [1/6] 타겟 ETF 일봉(1D) 수집 건너뜀 (UI 설정 미선택)")

    # 1. 타겟 ETF 1분봉
    if "1M" in tgt_sources:
        try:
            download_minute_data(symbol=target_symbol, interval=interval, count=count, force_full=force_full, local_log=local_log)
        except Exception as e:
            local_log(f"   ⚠️ [1/5] 타겟 ETF 1분봉 수집 예외: {e}")
    else:
        local_log(f"   ℹ️ [1/5] 타겟 ETF 1분봉(1M) 수집 건너뜀 (UI 설정 미선택)")
    
    # 2. 선물 1분봉
    configured_fut = _get_configured_sources(futures_symbol)
    fut_sources = [s for s in configured_fut if s in [x.upper() for x in sources]] if sources is not None else configured_fut
    if "1M" in fut_sources or ("TICK" in fut_sources and sources is None):
        try:
            download_futures_minute_data(symbol=futures_symbol, interval=interval, count=count, force_full=force_full, local_log=local_log)
        except Exception as e:
            local_log(f"   ⚠️ [2/5] 선물 1분봉 수집 예외: {e}")
    else:
        local_log(f"   ℹ️ [2/5] 선물 1분봉 수집 건너뜀 (UI 설정 미선택)")
    
    # 3. 현물 지수 1분봉
    configured_idx = _get_configured_sources(index_symbol)
    idx_sources = [s for s in configured_idx if s in [x.upper() for x in sources]] if sources is not None else configured_idx
    if "1M" in idx_sources or ("TICK" in idx_sources and sources is None):
        try:
            download_index_minute_data(symbol=index_symbol, interval=interval, count=count, force_full=force_full, local_log=local_log)
        except Exception as e:
            local_log(f"   ⚠️ [3/5] 현물 지수 1분봉 수집 예외: {e}")
    else:
        local_log(f"   ℹ️ [3/5] 현물 지수 1분봉 수집 건너뜀 (UI 설정 미선택)")
    
    # 4. 타겟 ETF 틱 데이터
    if "TICK" in tgt_sources:
        try:
            download_tick_data(symbol=target_symbol, count=count, force_full=force_full, local_log=local_log)
        except Exception as e:
            local_log(f"   ⚠️ [4/6] 타겟 ETF 틱 수집 예외: {e}")
    else:
        local_log(f"   ℹ️ [4/6] 타겟 ETF 틱(TICK) 수집 건너뜀 (UI 설정 미선택)")
    
    # 6. 참조 대장주(Reference Symbols) 1분봉, 틱 및 일봉 데이터 동적 일괄 수집
    resolved_refs = list(ref_symbols) if ref_symbols else []
    clean_target = _clean_sym(target_symbol)
    if not resolved_refs:
        # symbol_relations.json에서 자동 탐색 fallback
        try:
            from shared.data_library.symbol_relations_manager import SymbolRelationsManager
            from shared.config.config_manager import get_symbol_info
            for ref_item in SymbolRelationsManager.get_relations_for_target(clean_target):
                sym = str(ref_item.get("symbol", "")).strip().upper()
                clean_s = sym[1:] if sym.startswith("A") and len(sym) == 7 and sym[1:].isalnum() else sym
                if not clean_s or clean_s == clean_target or clean_s in resolved_refs:
                    continue
                market_type = str((get_symbol_info(clean_s) or {}).get("market_type", "")).upper()
                if market_type in ("KRX_STOCK", "KRX_ETF", "KRX_FUTURES"):
                    resolved_refs.append(clean_s)
        except Exception as e:
            local_log(f"   ⚠️ [Creon] symbol_relations.json 자동 탐색 예외: {e}")

    if resolved_refs:
        local_log(f"   ⚡ [6/6] 참조 대장주 및 주식선물({len(resolved_refs)}개: {', '.join(resolved_refs)}) 1분봉/일봉/틱 일괄 증분 수집 시작...")
        for idx, ref_sym in enumerate(resolved_refs, 1):
            s = str(ref_sym).strip().upper()
            raw_s = s.lstrip("A") if s.startswith("A") and len(s) == 7 and s[1:].isalnum() else s
            try:
                from shared.config.config_manager import get_symbol_info
                sym_info = get_symbol_info(raw_s) or get_symbol_info(s) or {}
                m_type = str(sym_info.get("market_type", "")).upper()
            except Exception:
                m_type = ""

            is_futures = (m_type == "KRX_FUTURES")
            if is_futures:
                clean_ref = raw_s
            elif s.startswith("A"):
                core_code = s[1:]
                clean_ref = f"A{core_code.zfill(6)}" if core_code.isdigit() and len(core_code) < 6 else s
            elif s.isdigit():
                clean_ref = f"A{s.zfill(6)}"
            elif len(s) == 6 and s.isalnum():
                clean_ref = f"A{s}"
            else:
                clean_ref = s

            configured_ref = _get_configured_sources(clean_ref)
            ref_sources = [s for s in configured_ref if s in [x.upper() for x in sources]] if sources is not None else configured_ref
            role_label = "주식선물" if is_futures else "대장주"
            local_log(f"   📦 [{idx}/{len(resolved_refs)}] {role_label} {clean_ref} 수집 시작... (대상: {', '.join(ref_sources)})")
            
            if not is_futures:
                if "1D" in ref_sources:
                    try:
                        download_daily_data(symbol=clean_ref, count=count, force_full=force_full, local_log=local_log)
                    except Exception as e:
                        local_log(f"      ⚠️ 대장주 {clean_ref} 일봉 수집 예외: {e}")
                else:
                    local_log(f"      ℹ️ 대장주 {clean_ref} 일봉(1D) 수집 건너뜀 (UI 설정 미선택)")

            if "1M" in ref_sources:
                try:
                    if is_futures:
                        download_futures_minute_data(symbol=clean_ref, interval=interval, count=count, force_full=force_full, local_log=local_log)
                    else:
                        download_minute_data(symbol=clean_ref, interval=interval, count=count, force_full=force_full, local_log=local_log)
                except Exception as e:
                    local_log(f"      ⚠️ {role_label} {clean_ref} 1분봉 수집 예외: {e}")
            else:
                local_log(f"      ℹ️ {role_label} {clean_ref} 1분봉(1M) 수집 건너뜀 (UI 설정 미선택)")

            if "TICK" in ref_sources:
                try:
                    if is_futures:
                        download_futures_tick_data(symbol=clean_ref, count=count, force_full=force_full, local_log=local_log)
                    else:
                        download_tick_data(symbol=clean_ref, count=count, force_full=force_full, local_log=local_log)
                except Exception as e:
                    local_log(f"      ⚠️ {role_label} {clean_ref} 틱 수집 예외: {e}")
            else:
                local_log(f"      ℹ️ {role_label} {clean_ref} 틱(TICK) 수집 건너뜀 (UI 설정 미선택)")
    else:
        local_log("   ℹ️ [6/6] 부착된 참조 대장주 없음 (단독 수집 완료)")
    
    local_log("🎉 [Creon] 전체 데이터 및 참조 대장주 수집 파이프라인 완료!")
    try:
        from shared.data_library.data_quality import DataQualityAuditor
        local_log("🔍 [Creon] 수집 완료 자산 품질 증분 검사(Incremental Audit) 자동 실행...")
        DataQualityAuditor.audit_symbol(_clean_sym(target_symbol), full=False)
        for r_sym in resolved_refs:
            DataQualityAuditor.audit_symbol(_clean_sym(r_sym), full=False)
        local_log("✅ [Creon] 수집 완료 자산 품질 증분 검사 완료!")
    except Exception as qe:
        local_log(f"   ℹ️ 품질 감사 자동 실행 스킵 (또는 환경 미지원): {qe}")



# 수집 타입별 필수 인자 — 집합으로 한 번만 선언한다(비교식에 리터럴을 흘리지 않기 위함)
_TYPES_REQUIRING_FUTURES_SYMBOL = {"futures", "all_krx"}
_TYPES_REQUIRING_INDEX_SYMBOL = {"index", "all_krx"}
_TYPES_SYMBOL_AS_FUTURES = {"futures", "futures_tick"}   # --symbol 만 넘어오면 선물 코드로 간주

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="대신증권(크레온) 초경량 틱/분봉/선물/지수 데이터 수집기")
    parser.add_argument("--symbol", type=str, required=True, help="종목 코드 (예: A069500)")
    parser.add_argument("--type", type=str, choices=["tick", "minute", "stock", "daily", "futures", "futures_tick", "index", "all_krx", "check_symbol"], default="minute", help="수집 타입")
    parser.add_argument("--futures_symbol", type=str, default=None, help="선물 코드 (예: 10100 / 101V000) — futures, futures_tick, all_krx 타입에 필수")
    parser.add_argument("--index_symbol", type=str, default=None, help="지수 코드 (예: U180/KOSPI200) — index, all_krx 타입에 필수")
    parser.add_argument("--interval", type=int, default=1, help="분봉 주기 (기본: 1분)")
    parser.add_argument("--count", type=int, default=99999999, help="수집할 데이터 개수 (기본: 최대치 99,999,999)")
    parser.add_argument("--force", action="store_true", default=False, help="기존 데이터 무시하고 전체 강제 재다운로드")
    parser.add_argument("--out", type=str, default=None, help="저장할 CSV 파일 경로")
    parser.add_argument("--ref_symbols", type=str, default="", help="쉼표(,)로 구분된 참조 대장주 심볼 목록 (예: 000660,005930,379810,449190)")
    parser.add_argument("--sources", type=str, default=None, help="수집할 원천 스케일 쉼표 구분 (예: TICK,1M,1D — 생략 시 UI 설정에 따름)")
    
    args = parser.parse_args()

    # 단일 선물/지수 수집 시 필수 인자가 생략되고 --symbol만 넘어온 경우 fallback 지원
    if args.type in _TYPES_SYMBOL_AS_FUTURES and not args.futures_symbol and args.symbol:
        args.futures_symbol = args.symbol
    if args.type == "index" and not args.index_symbol and args.symbol:
        args.index_symbol = args.symbol

    # 선물/지수 코드는 원장에서 결정되어 호출자가 넘겨야 한다(하드코딩 기본값 금지).
    if args.type in _TYPES_REQUIRING_FUTURES_SYMBOL and not args.futures_symbol:
        parser.error(f"--type {args.type} 에는 --futures_symbol 이 필요합니다 (원장에 등록된 KRX_FUTURES 코드를 넘기세요).")
    if args.type in _TYPES_REQUIRING_INDEX_SYMBOL and not args.index_symbol:
        parser.error(f"--type {args.type} 에는 --index_symbol 이 필요합니다 (원장에 등록된 KRX_INDEX 코드를 넘기세요).")

    is_ok, msg = check_creon_connection()
    if not is_ok:
        print(f"[Creon Error] {msg}")
        sys.exit(1)
        
    if args.type == "tick":
        download_tick_data(symbol=args.symbol, count=args.count, force_full=args.force)     # 틱은 일자 저장소에만 쓴다(--out 미사용)
    elif args.type in ("minute", "stock"):
        out_file = args.out
        download_minute_data(symbol=args.symbol, interval=args.interval, count=args.count, save_path=out_file, force_full=args.force)
    elif args.type == "daily":
        out_file = args.out
        download_daily_data(symbol=args.symbol, count=args.count, save_path=out_file, force_full=args.force)
    elif args.type == "futures":
        f_sym = (args.futures_symbol or args.symbol or KOSPI200_FUTURES_KEY).strip()
        out_file = args.out or _macro_raw_csv(f_sym, args.interval)
        download_futures_minute_data(symbol=f_sym, interval=args.interval, count=args.count, save_path=out_file, force_full=args.force)
    elif args.type == "futures_tick":
        f_sym = args.futures_symbol or args.symbol or KOSPI200_FUTURES_KEY
        download_futures_tick_data(symbol=f_sym, count=args.count, force_full=args.force)  # 틱은 일자 저장소에만 쓴다(--out 미사용)
    elif args.type == "index":
        out_file = args.out or _macro_raw_csv(args.index_symbol, args.interval)
        download_index_minute_data(symbol=args.index_symbol, interval=args.interval, count=args.count, save_path=out_file, force_full=args.force)
    elif args.type == "all_krx":
        ref_syms = [s.strip() for s in args.ref_symbols.split(",") if s.strip()] if args.ref_symbols else None
        download_all_krx(
            target_symbol=args.symbol,
            futures_symbol=args.futures_symbol,
            index_symbol=args.index_symbol,
            interval=args.interval,
            count=args.count,
            force_full=args.force,
            ref_symbols=ref_syms,
            sources=args.sources
        )
    elif args.type == "check_symbol":
        result = check_symbol_exists(args.symbol)
        print(f"CREON_CHECK_RESULT_JSON:{json.dumps(result, ensure_ascii=False)}")


