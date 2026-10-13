# -*- coding: utf-8 -*-
"""1분봉 원천의 일자별 저장소 — 틱(tick_day_store)과 같은 엔진·같은 날짜 메타로 `A{sym}/YYYY-MM-DD.csv` 에 직접 병합한다(통합 CSV 없음).

저장 엔진(병합 규칙·백업·불변식·완결된 날만 저장·봉인일 불변)은 tick_day_store 를 그대로 쓰고, 여기는 1분봉에 고유한 것만 둔다:
  - 봉 격자 검사: 초가 0 이 아닌 시각(진행 중인 봉을 찍어 둔 스냅샷)은 저장하지 않고 quarantine/misaligned 로 격리한다.
  - 봉인 기준: 증권사 데이터를 신뢰하고, 혹시 모를 훼손만 다시 확인한다.
      1. 구조 — 모든 행이 그 날짜 · 시각이 엄격히 증가(중복 없음) · 분 격자 · 파일이 메타(해시·행 수)와 일치
      2. 일봉 상한 — 1분봉 거래량 합이 일봉 거래량을 넘으면 오염. (일봉에는 시간외 단일가 등이 더 들어가 합이 같지 않다 — 일치 검사는 쓰지 않는다)
      3. 틱 대조 — 그 날 틱이 봉인돼 있으면 틱이 대조한 1분봉 거래량(minute_ref_volume)과 지금 값이 같아야 한다(봉인 뒤 바뀌었는지)
    합격이면 VALID, 하나라도 어긋나면 QUARANTINED.
    봉 수는 기준이 아니다: Creon 은 체결이 없는 분에는 봉을 만들지 않아, 거래가 얇은 ETF 는 정상 데이터도 380봉 미만이다
    (2026-10-02 실측: 하한을 두면 12종목 7,668일 중 1,010일이 오탐 — 불합격 사유가 전부 봉 수였다). 봉 수는 메타에 기록만 한다.
  - 1분봉은 다른 데이터를 기다리지 않는다: 일봉이 아직 없으면 2번만 건너뛰고, 틱이 없으면 3번만 건너뛴다. (틱 봉인이 1분봉 봉인을 기다린다)

메타(`YYYY-MM-DD.meta.json`): status · rows · volume · sha256 · first · last · updated_at (+ 봉인 시 bars · daily_ref_volume · daily_ratio ·
tick_ref_volume · sealed_at · failures(불합격 사유)).

표준 라이브러리만 사용 — 32-bit Windows 수집기가 그대로 import 한다.
"""
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

try:
    from . import raw_day_store as rds
    from . import tick_day_store as tds
except ImportError:                                     # `py core\creon_collector.py` 스크립트 실행
    import raw_day_store as rds
    import tick_day_store as tds

KIND = tds.Kind("1m", "1m_", "volume")
SEALED = ("VALID",)
VOLUME_TOL = tds.VOLUME_TOL
_KST = tds._KST

DayStoreError = tds.DayStoreError

# 경로·메타·검증·잠금은 틱과 같다(저장 루트와 종목만 받는다)
core_symbol = tds.core_symbol
day_dir = tds.day_dir
day_csv = tds.day_csv
meta_path = tds.meta_path
read_meta = tds.read_meta
write_meta = tds.write_meta
all_metas = tds.all_metas
stored_days = tds.stored_days
scan_day = tds.scan_day
complete_through = tds.complete_through
resume_timestamp = tds.resume_timestamp
verify = tds.verify
lock_sealed = tds.lock_sealed


# ─────────────────────────────────────────────────────────────────────────────
# 수집 진입점
# ─────────────────────────────────────────────────────────────────────────────
def _drop_misaligned(new_tmp_csv: str, quarantine_dir: str, symbol: str, log: Callable[[str], None]) -> None:
    """분 격자에 맞지 않는 행(초 ≠ 0, 시각 형식 오류)을 수신 파일에서 빼 격리한다."""
    lines = rds._read_lines(new_tmp_csv)
    header = next(lines, "")
    kept, bad = [], []
    for l in lines:
        ts = rds._ts(l)
        (kept if rds._valid_ts(ts) and rds.is_aligned(ts, 60) else bad).append(l)
    if not bad:
        return
    stamp = time.strftime("%Y%m%d_%H%M%S")
    mdir = os.path.join(quarantine_dir, "misaligned")
    os.makedirs(mdir, exist_ok=True)
    bp = os.path.join(mdir, f"A{core_symbol(symbol)}_1m.{stamp}.csv")
    with open(bp, "w", encoding="utf-8", newline="") as w:
        w.write(header + "\n" + "".join(l + "\n" for l in bad))
    log(f"   ⚠️ [DayStore] A{core_symbol(symbol)} 1m: 봉 간격에 맞지 않거나 시각이 잘못된 {len(bad)}행은 저장하지 않았습니다(미완성 봉 등) → {bp}")
    with open(new_tmp_csv, "w", encoding="utf-8", newline="") as w:
        w.write(header + "\n" + "".join(l + "\n" for l in kept))


def ingest(new_tmp_csv: str, symbol: str, *, candle_root: str, quarantine_dir: str,
           requested_after: Optional[str] = None, source_exhausted: bool = False,
           lost_days_fn: Optional[Callable[[str, str], List[str]]] = None, collected_at: Optional[datetime] = None,
           log: Callable[[str], None] = print) -> Optional[Dict[str, Any]]:
    """수신분을 1분봉 일자 저장소에 병합한다(tick_day_store.ingest 와 같은 규칙). 반환: 보고서(실패 시 None)."""
    if os.path.exists(new_tmp_csv) and os.path.getsize(new_tmp_csv) > 0:
        _drop_misaligned(new_tmp_csv, quarantine_dir, symbol, log)
    return tds.ingest(new_tmp_csv, symbol, tick_root=candle_root, quarantine_dir=quarantine_dir, newest_first=None,
                      requested_after=requested_after, source_exhausted=source_exhausted, lost_days_fn=lost_days_fn,
                      collected_at=collected_at, kind=KIND, log=log)


# ─────────────────────────────────────────────────────────────────────────────
# 봉인
# ─────────────────────────────────────────────────────────────────────────────
def _daily_volumes(daily_csv: Optional[str], days: set) -> Dict[str, float]:
    """일봉 CSV(단일 파일) 에서 필요한 날짜의 거래량만 스트리밍으로 읽는다. 없으면 빈 dict(그 검사는 건너뛴다)."""
    out: Dict[str, float] = {}
    if not daily_csv or not os.path.exists(daily_csv):
        return out
    lines = rds._read_lines(daily_csv)
    header = next(lines, "").split(",")
    vol_idx = rds._find_volume_index(header, None)
    for line in lines:
        d = rds._day(rds._ts(line))
        if d in days:
            out[d] = rds._volume_of(line, vol_idx)
    return out


def _bar_checks(path: str, day: str) -> Dict[str, Any]:
    """일자 파일의 1분봉 고유 구조 검사 — 시각이 엄격히 증가(중복 없음)하고 분 격자에 맞는가. 반환: {bars, problems}"""
    lines = rds._read_lines(path)
    next(lines, None)
    prev, bars, problems = "", 0, []
    for l in lines:
        ts = rds._ts(l)
        bars += 1
        if ts <= prev:
            problems.append(f"시각 중복/역행 {prev} → {ts}")
            break
        if not rds.is_aligned(ts, 60):
            problems.append(f"분 격자 아님 {ts}")
            break
        prev = ts
    return {"bars": bars, "problems": problems}


def seal_closed_days(candle_root: str, symbol: str, *, daily_csv: Optional[str] = None, tick_root: Optional[str] = None,
                     now: Optional[datetime] = None, log: Callable[[str], None] = print) -> Dict[str, List[str]]:
    """마감된 OPEN/QUARANTINED 날을 위 기준으로 검사해 VALID / QUARANTINED 로 정한다."""
    rep: Dict[str, List[str]] = {"sealed": [], "quarantined": [], "skipped": []}
    now = now or datetime.now(_KST)
    metas = all_metas(candle_root, symbol)
    todo = [d for d, m in metas.items() if m.get("status") in ("OPEN", "QUARANTINED") and tds._is_closed(d, m, now)]
    if not todo:
        return rep
    dailies = _daily_volumes(daily_csv, set(todo))
    for d in todo:
        m = metas[d]
        path = day_csv(candle_root, symbol, d)
        try:
            s = scan_day(path, d)
            bc = _bar_checks(path, d)
        except (DayStoreError, OSError) as e:
            rep["skipped"].append(d)
            log(f"[Seal-1m] ❌ {d}: {e}")
            continue
        if s["sha256"] != m.get("sha256"):
            rep["skipped"].append(d)
            log(f"[Seal-1m] ❌ {d}: 일자 파일이 메타와 다름 — 봉인하지 않음")
            continue
        failures: List[str] = list(bc["problems"])
        daily = dailies.get(d)
        if daily is not None and s["volume"] > daily + VOLUME_TOL:
            failures.append(f"1분봉 거래량 {s['volume']:,.0f} 가 일봉 {daily:,.0f} 보다 큼")
        tick_ref = None
        if tick_root:
            tm = tds.read_meta(tick_root, symbol, d)
            if tm and tm.get("status") in SEALED and tm.get("minute_ref_volume") is not None:
                tick_ref = float(tm["minute_ref_volume"])
                if abs(s["volume"] - tick_ref) >= VOLUME_TOL:
                    failures.append(f"틱 봉인 때 대조한 1분봉 거래량 {tick_ref:,.0f} 와 다름")
        status = "QUARANTINED" if failures else "VALID"
        m.update({"status": status, "bars": bc["bars"],
                  "daily_ref_volume": None if daily is None else round(daily, 4),
                  "daily_ratio": None if not daily else round(s["volume"] / daily, 6),
                  "tick_ref_volume": tick_ref, "failures": failures})
        if status in SEALED:
            m["sealed_at"] = tds._now()
            os.chmod(path, 0o444)
            rep["sealed"].append(d)
        else:
            rep["quarantined"].append(d)
        write_meta(candle_root, symbol, d, m)
        log(f"[Seal-1m] {'🔒' if status in SEALED else '⚠️'} {d}: {status} (bars={bc['bars']}, rows={s['rows']:,})"
            + (f" — {'; '.join(failures)}" if failures else ""))
    return rep


def sealed_volumes(candle_root: str, symbol: str, days: Optional[set] = None) -> Dict[str, float]:
    """{날짜: 1분봉 거래량 합} — **봉인된 날만**(틱 봉인의 기준값). 봉인 전 값은 기준으로 삼지 않는다."""
    return {d: float(m["volume"]) for d, m in all_metas(candle_root, symbol).items()
            if m.get("status") in SEALED and m.get("volume") is not None and (days is None or d in days)}
