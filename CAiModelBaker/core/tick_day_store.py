# -*- coding: utf-8 -*-
"""틱 원천의 일자별 저장소 — 수신분을 날짜별로 나눠 `A{sym}/YYYY-MM-DD.csv` 에 직접 병합한다(통합 CSV 없음).

한 날짜 = 일자 파일 하나 + 메타(`YYYY-MM-DD.meta.json`). 메타가 원장을 대신한다:
  status   OPEN(병합 중) · VALID / ACCEPTED_DEVIATION(봉인 — 불변, 0o444) · QUARANTINED(마감했지만 1분봉과 불일치) · LOST(영구 결손, 파일 없음)
  rows · tick_volume · sha256 · first · last · updated_at (+ 봉인 시 minute_ref_volume · match_ratio · sealed_at,
  허용 편차는 deviation_ratio · deviation_reason · deviation_accepted_at)
충돌('같은 시각에 다른 데이터')은 그 날짜의 `YYYY-MM-DD.csv.conflicts.json` 에 남는다(엔진 기본 규칙 — 기존 스캐너·해결 도구가 그대로 찾는다).

병합 규칙은 raw_day_store.merge_file 을 그대로 쓴다(같은 시각 묶음 비교·보강/부분 재수신/충돌 판정·줄어듦 금지·원자적 교체).
그 위에 더하는 것:
  0. 완결된 날만 저장한다: 수집 시작 시각 기준 complete_through() 이후의 행(당일·당일 밤)은 버리고 다음 수집에서 다시 받는다.
  1. '형성 중인 날'(느슨한 규칙)은 저장소의 최신 날이 OPEN 일 때만 — 완결된 날만 저장하므로 보통은 없다.
  2. 봉인된 날은 절대 수정하지 않는다 — 임시 사본에 병합해 보고, 바뀌면 원본은 두고 충돌로 기록 + 수신분 격리.
  3. 쓰기 직후 구조 불변식을 검사한다: 모든 행이 그 날짜 · 시각 오름차순 · 행/거래량이 줄지 않음 · 메타 해시 일치.
     하나라도 어기면 그 날의 변경을 되돌리고 중단한다.
  4. 날짜는 오름차순으로 처리하고 첫 실패에서 멈춘다 — 더 최신 날이 먼저 기록돼 이어받기 지점이 갭을 건너뛰는 일이 없다.
표준 라이브러리만 사용 — 32-bit Windows 수집기가 그대로 import 한다.
"""
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, NamedTuple, Optional

try:
    from . import raw_day_store as rds
except ImportError:                                     # `py core\creon_collector.py` 스크립트 실행
    import raw_day_store as rds

SEALED = ("VALID", "ACCEPTED_DEVIATION")
COMPLETE_AFTER = "06:00:00"  # 날 D 는 'D+1 일 06:00(KST) 이후에 시작한 수집'에서만 저장한다(완결된 날만 저장 → 저장과 동시에 봉인 판정).
                             # 주식은 2026-09-14부터 애프터마켓이 ~19:59 까지 오고, 서버 반영 지연도 있을 수 있어 당일·당일 밤 수집분은 버린다.
VOLUME_TOL = 1e-4
_KST = timezone(timedelta(hours=9))


class DayStoreError(rds.MergeAborted):
    """일자 저장소 불변식 위반 — 해당 날의 변경은 적용되지 않았다."""


class Kind(NamedTuple):
    """같은 일자 저장소 엔진을 쓰는 원천의 차이 — 파일 이름 접두와 메타의 거래량 키만 다르다(candle_day_store 가 1분봉용으로 쓴다)."""
    name: str          # 격리/백업 파일 이름에 들어가는 원천 이름
    tag: str           # 백업·격리 파일의 날짜 앞 접두 ('' 이면 틱 — 기존 이름 그대로)
    vol_key: str       # 메타에 거래량을 적는 키


TICK = Kind("tick", "", "tick_volume")


# ─────────────────────────────────────────────────────────────────────────────
# 경로·메타
# ─────────────────────────────────────────────────────────────────────────────
def core_symbol(symbol: str) -> str:
    s = str(symbol).strip()
    return s[1:] if s.upper().startswith("A") and len(s) >= 7 else s


def day_dir(tick_root: str, symbol: str) -> str:
    return os.path.join(tick_root, f"A{core_symbol(symbol)}")


def day_csv(tick_root: str, symbol: str, day: str) -> str:
    return os.path.join(day_dir(tick_root, symbol), f"{day}.csv")


def meta_path(tick_root: str, symbol: str, day: str) -> str:
    return os.path.join(day_dir(tick_root, symbol), f"{day}.meta.json")


def conflicts_target(tick_root: str, symbol: str, day: str) -> str:
    """raw_day_store 가 `<이 경로>.conflicts.json` 에 쓴다 → `YYYY-MM-DD.csv.conflicts.json` (봉인일 사본 병합도 원본 쪽에 기록)"""
    return day_csv(tick_root, symbol, day)


def read_meta(tick_root: str, symbol: str, day: str) -> Optional[Dict[str, Any]]:
    try:
        with open(meta_path(tick_root, symbol, day), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_meta(tick_root: str, symbol: str, day: str, meta: Dict[str, Any]) -> None:
    p = meta_path(tick_root, symbol, day)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def all_metas(tick_root: str, symbol: str) -> Dict[str, Dict[str, Any]]:
    d = day_dir(tick_root, symbol)
    out: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        if fn.endswith(".meta.json"):
            try:
                with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue
            if m.get("date"):
                out[m["date"]] = m
    return dict(sorted(out.items()))


def stored_days(tick_root: str, symbol: str) -> List[str]:
    """데이터 파일이 있는 날짜(오름차순)"""
    d = day_dir(tick_root, symbol)
    if not os.path.isdir(d):
        return []
    return sorted(fn[:-4] for fn in os.listdir(d) if fn.endswith(".csv") and len(fn) == 14)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# 일자 파일 스캔·불변식
# ─────────────────────────────────────────────────────────────────────────────
def scan_day(path: str, day: str) -> Dict[str, Any]:
    """일자 파일 한 개의 (행, 거래량, 해시, 첫/끝 시각) — 구조 불변식 위반이면 DayStoreError.
    해시는 raw_day_store 원장과 같은 방식(행 + '\\n')이라 통합 CSV 원장의 같은 날 해시와 비교할 수 있다."""
    header = rds.read_header(path)
    if not header:
        raise DayStoreError(f"{path}: 헤더 없음")
    vol_idx = rds._find_volume_index(header.split(","), None)
    acc = rds._DayAcc()
    prev = ""
    lines = rds._read_lines(path)
    next(lines, None)
    for line in lines:
        ts = rds._ts(line)
        if not rds._valid_ts(ts):
            raise DayStoreError(f"{os.path.basename(path)}: 시각 형식이 아닌 행 {line[:40]!r}")
        if rds._day(ts) != day:
            raise DayStoreError(f"{os.path.basename(path)}: 다른 날짜의 행 {ts}")
        if ts < prev:
            raise DayStoreError(f"{os.path.basename(path)}: 시각이 거꾸로 감 {prev} → {ts}")
        prev = ts
        acc.add(line, ts, rds._volume_of(line, vol_idx))
    if acc.rows == 0:
        raise DayStoreError(f"{os.path.basename(path)}: 빈 일자 파일")
    out = acc.as_dict()
    out["header"] = header
    return out


def _backup_day(path: str, backup_dir: Optional[str], symbol: str, day: str, keep: int = 3, kind: Kind = TICK) -> None:
    if not backup_dir or not os.path.exists(path):
        return
    os.makedirs(backup_dir, exist_ok=True)
    prefix = f"A{core_symbol(symbol)}_{kind.tag}{day}.csv."
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(backup_dir, f"{prefix}{stamp}.bak")
    n = 1
    while os.path.exists(dst):                          # 같은 초의 연속 병합 — 이름을 겹치지 않게(정렬 순서 = 시간 순서 유지)
        dst = os.path.join(backup_dir, f"{prefix}{stamp}_{n:02d}.bak")
        n += 1
    try:
        os.link(path, dst)                              # 교체는 항상 os.replace(새 inode)라 하드링크 백업 내용은 변하지 않는다
    except OSError:
        shutil.copy2(path, dst)
    olds = sorted(p for p in os.listdir(backup_dir) if p.startswith(prefix) and p.endswith(".bak"))
    for p in olds[:-keep]:
        try:
            os.remove(os.path.join(backup_dir, p))
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 수신분 → 날짜별 분할
# ─────────────────────────────────────────────────────────────────────────────
def _split_incoming(new_tmp_csv: str, work_dir: str, newest_first: Optional[bool]) -> (str, Dict[str, str]):
    """수신 파일을 날짜별 임시 파일(시각 오름차순)로 나눈다. 같은 시각 안의 행 순서(체결 순서)는 raw_day_store 가
    수신 파일 **전체**의 흐름 방향으로 복원한 것을 그대로 쓴다 — 날짜 경계에서 잘라 판단하면 경계의 묶음 방향을 잃는다."""
    header, groups = rds._load_new_groups(new_tmp_csv, newest_first)
    by_day: Dict[str, List[str]] = {}
    for ts in sorted(groups):
        by_day.setdefault(rds._day(ts), []).append(ts)
    os.makedirs(work_dir, exist_ok=True)
    out: Dict[str, str] = {}
    for day, tss in by_day.items():
        p = os.path.join(work_dir, f"{day}.in.csv")
        with open(p, "w", encoding="utf-8", newline="") as w:
            w.write(header + "\n")
            for ts in tss:
                for l in groups[ts]:
                    w.write(l + "\n")
        out[day] = p
    return header, dict(sorted(out.items()))


# ─────────────────────────────────────────────────────────────────────────────
# 수집 진입점
# ─────────────────────────────────────────────────────────────────────────────
def complete_through(collected_at: Optional[datetime] = None) -> str:
    """이 시각에 시작한 수집이 '완결'로 저장할 수 있는 마지막 날 — D+1 06:00 이 지났으면 D 까지."""
    t = collected_at or datetime.now(_KST)
    if t.tzinfo is None:
        t = t.replace(tzinfo=_KST)
    t = t.astimezone(_KST)
    h, m, sec = (int(x) for x in COMPLETE_AFTER.split(":"))
    return (t - timedelta(hours=h, minutes=m, seconds=sec) - timedelta(days=1)).strftime("%Y-%m-%d")


def ingest(new_tmp_csv: str, symbol: str, *, tick_root: str, quarantine_dir: str,
           newest_first: Optional[bool] = True, requested_after: Optional[str] = None, source_exhausted: bool = False,
           lost_days_fn: Optional[Callable[[str, str], List[str]]] = None, collected_at: Optional[datetime] = None,
           kind: Kind = TICK, log: Callable[[str], None] = print) -> Optional[Dict[str, Any]]:
    """수신분을 일자 저장소에 병합한다. 반환: 보고서(실패 시 None — 적용된 날짜까지는 유효, 실패한 날 이후는 미적용, 수신분은 격리).
    collected_at: 서버 조회를 시작한 시각(기본 지금) — complete_through(collected_at) 이후 날짜의 행은 저장하지 않는다."""
    rep: Dict[str, Any] = {"added_rows": 0, "replaced_groups": 0, "kept_groups": 0, "conflicts": 0,
                           "touched_days": [], "sealed_conflicts": [], "lost_days": [], "skipped_incomplete_days": []}
    if not os.path.exists(new_tmp_csv) or os.path.getsize(new_tmp_csv) == 0:
        if os.path.exists(new_tmp_csv):
            os.remove(new_tmp_csv)
        return rep
    sym = core_symbol(symbol)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    work = os.path.join(quarantine_dir, "daystore_work", f"A{sym}{kind.tag and '.' + kind.name}.{os.getpid()}.{stamp}")
    backup_dir = os.path.join(quarantine_dir, "merge_backups")

    def _reject(reason: str) -> None:
        keep_dir = os.path.join(quarantine_dir, "rejected_merges")
        os.makedirs(keep_dir, exist_ok=True)
        keep = os.path.join(keep_dir, f"A{sym}_{kind.name}.{stamp}.csv")
        try:
            shutil.copy2(new_tmp_csv, keep)
        except OSError:
            keep = new_tmp_csv
        log(f"   ❌ [DayStore] A{sym}: 저장 중단 — {reason}")
        log(f"      신규 수신분은 보관했습니다: {keep}")

    try:
        header, parts = _split_incoming(new_tmp_csv, work, newest_first)
        last_complete = complete_through(collected_at)
        rep["skipped_incomplete_days"] = [d for d in parts if d > last_complete]
        parts = {d: p for d, p in parts.items() if d <= last_complete}
        if rep["skipped_incomplete_days"]:
            log(f"   ⏭️ [DayStore] A{sym}: 완결 전 날짜 {rep['skipped_incomplete_days']} 는 저장하지 않음(다음 수집에서 완결분으로 저장)")
        if not parts:
            if os.path.exists(new_tmp_csv):
                os.remove(new_tmp_csv)
            return rep
        existing = stored_days(tick_root, sym)
        newest = existing[-1] if existing else None
        forming = newest if newest and (read_meta(tick_root, sym, newest) or {}).get("status") == "OPEN" else None
        oldest_in = None
        for day, part in parts.items():
            oldest_in = oldest_in or day
            target = day_csv(tick_root, sym, day)
            meta = read_meta(tick_root, sym, day) or {}
            if os.path.exists(target):
                old_header = rds.read_header(target)
                if old_header and old_header.strip() != header.strip():
                    raise DayStoreError(f"{day}: 헤더가 다르다 — 기존 {old_header[:60]!r} / 신규 {header[:60]!r}")
            if meta.get("status") in SEALED and os.path.exists(target):
                # 봉인된 날 — 사본에 병합해 보고, 바뀌면 충돌로만 기록한다
                probe = os.path.join(work, f"{day}.probe.csv")
                shutil.copyfile(target, probe)
                r = rds.merge_file(part, probe, forming_day=None, write_ledger=False,
                                   conflicts_target=conflicts_target(tick_root, sym, day), newest_first=False, log=log)
                rep["conflicts"] += r["conflicts"]
                if r["touched_days"]:
                    rep["sealed_conflicts"].append(day)
                    keep_dir = os.path.join(quarantine_dir, "sealed_day_changes")
                    os.makedirs(keep_dir, exist_ok=True)
                    shutil.copyfile(probe, os.path.join(keep_dir, f"A{sym}_{kind.tag}{day}.{stamp}.proposed.csv"))
                    log(f"   ⚠️ [DayStore] A{sym} {day}: 봉인된 날에 다른/추가 데이터 수신 — 봉인본은 그대로 두고 확인 대상으로 보관했습니다")
                continue
            # 병합 전 기준값은 메타에서(쓸 때마다 검증해 기록한 값). 메타가 없으면 그때만 스캔한다.
            before = None
            if os.path.exists(target):
                before = ({"rows": meta["rows"], "volume": meta[kind.vol_key]} if meta.get("rows") and meta.get(kind.vol_key) is not None
                          else scan_day(target, day))
            _backup_day(target, backup_dir, sym, day, kind=kind)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            r = rds.merge_file(part, target, forming_day=forming, write_ledger=False,
                               conflicts_target=conflicts_target(tick_root, sym, day), newest_first=False, log=log)
            try:
                after = scan_day(target, day)
                if before and (after["rows"] < before["rows"] or after["volume"] < before["volume"] - 1e-6):
                    raise DayStoreError(f"{day}: 병합 후 행/거래량이 줄었다")
            except DayStoreError:
                _restore_latest_backup(target, backup_dir, sym, day, had_before=before is not None, kind=kind)
                raise
            for k in ("added_rows", "replaced_groups", "kept_groups", "conflicts"):
                rep[k] += r[k]
            if r["touched_days"] or not meta:
                rep["touched_days"].append(day)
                status = meta.get("status") if meta.get("status") in ("OPEN",) else "OPEN"
                new_meta = {"symbol": sym, "date": day, "status": status, "rows": after["rows"],
                            kind.vol_key: round(after["volume"], 4), "sha256": after["sha256"],
                            "first": after["first"], "last": after["last"], "updated_at": _now()}
                for k in ("deviation_ratio", "deviation_reason", "deviation_accepted_at"):
                    if k in meta:
                        new_meta[k] = meta[k]
                write_meta(tick_root, sym, day, new_meta)
    except rds.MergeAborted as e:
        _reject(str(e))
        return None
    except Exception as e:
        _reject(f"예외 {e!r}")
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # 영구 결손: 원천이 더 줄 게 없는데(source_exhausted) 요청 기점에 닿지 못했다
    if requested_after and source_exhausted and oldest_in and oldest_in > requested_after[:10]:
        lost = lost_days_fn(requested_after[:10], oldest_in) if lost_days_fn else []
        for d in lost:
            if not os.path.exists(day_csv(tick_root, sym, d)) and not read_meta(tick_root, sym, d):
                write_meta(tick_root, sym, d, {"symbol": sym, "date": d, "status": "LOST", "rows": 0,
                                               "reason": f"원천 보존 기간 경과 — {requested_after} 이후 ~ {oldest_in} 이전을 원천이 더 주지 않음",
                                               "recorded_at": _now()})
                rep["lost_days"].append(d)
        if rep["lost_days"]:
            log(f"   🚨 [DayStore] A{sym} 영구 결손 {len(rep['lost_days'])}일: {rep['lost_days'][:8]}")
    if os.path.exists(new_tmp_csv):
        os.remove(new_tmp_csv)
    log(f"   💾 [DayStore] A{sym}: 신규 {rep['added_rows']:+,}행 · 보강 {rep['replaced_groups']}묶음 · 기존 유지 {rep['kept_groups']}묶음 · "
        f"갱신 날짜 {len(rep['touched_days'])}일" + (f" · 봉인일 충돌 {len(rep['sealed_conflicts'])}일" if rep["sealed_conflicts"] else ""))
    return rep


def _restore_latest_backup(target: str, backup_dir: str, symbol: str, day: str, had_before: bool, kind: Kind = TICK) -> None:
    if not had_before:
        if os.path.exists(target):
            os.remove(target)
        return
    prefix = f"A{core_symbol(symbol)}_{kind.tag}{day}.csv."
    baks = sorted(p for p in os.listdir(backup_dir) if p.startswith(prefix) and p.endswith(".bak"))
    if baks:
        shutil.copy2(os.path.join(backup_dir, baks[-1]), target + ".restore.tmp")
        os.replace(target + ".restore.tmp", target)


# ─────────────────────────────────────────────────────────────────────────────
# 봉인 (일자 파일을 1분봉과 대조해 상태만 바꾼다 — 복사 없음)
# ─────────────────────────────────────────────────────────────────────────────
def minute_volumes(minute_csv: Optional[str], days: Optional[set] = None) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not minute_csv or not os.path.exists(minute_csv):
        return out
    lines = rds._read_lines(minute_csv)
    header = next(lines, "").split(",")
    vol_idx = rds._find_volume_index(header, None)
    for line in lines:
        d = rds._day(rds._ts(line))
        if days is not None and d not in days:
            continue
        out[d] = out.get(d, 0.0) + rds._volume_of(line, vol_idx)
    return out


def _is_closed(day: str, meta: Dict[str, Any], now: datetime) -> bool:
    """완결 = D+1 06:00(KST) 이 지났다. 마지막 체결 시각이나 정규장 마감으로 판단하지 않는다."""
    return day <= complete_through(now)


def seal_closed_days(tick_root: str, symbol: str, minute_csv: Optional[str] = None, *, now: Optional[datetime] = None,
                     ref_volumes: Optional[Callable[[set], Dict[str, float]]] = None,
                     log: Callable[[str], None] = print) -> Dict[str, List[str]]:
    """마감된 OPEN/QUARANTINED 날을 1분봉과 대조해 VALID / ACCEPTED_DEVIATION(등록된 경우만) / QUARANTINED 로 정한다.
    기준: ref_volumes(날짜 집합) → {날짜: 1분봉 거래량}(1분봉 일자 저장소의 **봉인된** 날) 이 있으면 그것을, 없으면 minute_csv(통합 CSV)를 쓴다.
    ref_volumes 를 쓰면 기준이 없는 날(1분봉이 아직 봉인되지 않음)은 격리하지 않고 건너뛴다 — 1분봉 봉인이 먼저고 틱 봉인이 뒤다."""
    rep: Dict[str, List[str]] = {"sealed": [], "quarantined": [], "skipped": []}
    now = now or datetime.now(_KST)
    metas = all_metas(tick_root, symbol)
    todo = [d for d, m in metas.items() if m.get("status") in ("OPEN", "QUARANTINED") and _is_closed(d, m, now)]
    if not todo:
        return rep
    refs = ref_volumes(set(todo)) if ref_volumes else minute_volumes(minute_csv, set(todo))
    for d in todo:
        if ref_volumes and d not in refs:
            rep["skipped"].append(d)
            log(f"[Seal] ⏳ {d}: 1분봉이 아직 봉인되지 않아 대기")
            continue
        m = metas[d]
        path = day_csv(tick_root, symbol, d)
        try:
            s = scan_day(path, d)
        except (DayStoreError, OSError) as e:
            rep["skipped"].append(d)
            log(f"[Seal] ❌ {d}: {e}")
            continue
        if s["sha256"] != m.get("sha256"):
            rep["skipped"].append(d)
            log(f"[Seal] ❌ {d}: 일자 파일이 메타와 다름 — 봉인하지 않음")
            continue
        ref = refs.get(d, 0.0)
        exact = ref > 0 and abs(s["volume"] - ref) < VOLUME_TOL
        dev = m.get("deviation_ratio")
        accepted = (not exact and dev is not None and ref > 0 and s["volume"] <= ref + VOLUME_TOL
                    and s["volume"] / ref >= dev - 1e-6)
        status = "VALID" if exact else ("ACCEPTED_DEVIATION" if accepted else "QUARANTINED")
        m.update({"status": status, "minute_ref_volume": round(ref, 4), "volume_diff": round(abs(s["volume"] - ref), 4),
                  "match_ratio": round(s["volume"] / ref, 6) if ref > 0 else 0.0})
        if status in SEALED:
            m["sealed_at"] = _now()
            os.chmod(path, 0o444)
            rep["sealed"].append(d)
        else:
            rep["quarantined"].append(d)
        write_meta(tick_root, symbol, d, m)
        log(f"[Seal] {'🔒' if status in SEALED else '⚠️'} {d}: {status} (rows={s['rows']:,}, 일치율 {m['match_ratio']:.6f})")
    return rep


def accept_deviation(tick_root: str, symbol: str, day: str, minute_csv: Optional[str], reason: str,
                     ref_volumes: Optional[Callable[[set], Dict[str, float]]] = None) -> Dict[str, Any]:
    """복구 불가가 확인된 부족 편차를 그 날짜에 명시 등록한다(사유 필수). 이후 이 일치율보다 나빠지면 허용되지 않는다."""
    if not reason or not reason.strip():
        raise ValueError("사유(reason)가 필요합니다")
    m = read_meta(tick_root, symbol, day)
    if not m or m.get("status") not in ("OPEN", "QUARANTINED"):
        raise ValueError(f"{day}: 등록할 수 있는 상태가 아닙니다 ({(m or {}).get('status')})")
    ref = (ref_volumes({day}) if ref_volumes else minute_volumes(minute_csv, {day})).get(day, 0.0)
    if ref <= 0 or m["tick_volume"] > ref + VOLUME_TOL:
        raise ValueError(f"{day}: 1분봉 기준이 없거나 틱이 더 많습니다 — 부족 편차가 아님")
    m.update({"deviation_ratio": round(m["tick_volume"] / ref, 6), "deviation_reason": reason.strip(),
              "deviation_accepted_at": _now()})
    write_meta(tick_root, symbol, day, m)
    return m


# ─────────────────────────────────────────────────────────────────────────────
# 이어받기 기준 · 검증
# ─────────────────────────────────────────────────────────────────────────────
def resume_timestamp(tick_root: str, symbol: str, is_trading_day: Optional[Callable] = None,
                     log: Callable[[str], None] = print) -> Optional[str]:
    """다음 수집을 시작할 시각. 저장된 날짜 사이에 (영구 결손이 아닌) 거래일 갭이 있으면 갭 직전 날의 마지막 시각, 없으면 최신 시각."""
    metas = all_metas(tick_root, symbol)
    have = [d for d, m in metas.items() if m.get("status") != "LOST" and m.get("last")]
    if not have:
        return None
    lost = {d for d, m in metas.items() if m.get("status") == "LOST"}
    if is_trading_day:
        recent = have[-60:]
        for i in range(len(recent) - 1, 0, -1):
            cur = datetime.strptime(recent[i - 1], "%Y-%m-%d") + timedelta(days=1)
            end = datetime.strptime(recent[i], "%Y-%m-%d")
            gap = []
            while cur < end:
                k = cur.strftime("%Y-%m-%d")
                if is_trading_day(cur, "KRX") and k not in lost:
                    gap.append(k)
                cur += timedelta(days=1)
            if gap:
                log(f"   🔍 [DayStore] 결측 갭 {gap} ➔ {metas[recent[i - 1]]['last']} 부터 백필")
                return metas[recent[i - 1]]["last"]
    return metas[have[-1]]["last"]


def verify(tick_root: str, symbol: str) -> Dict[str, List[str]]:
    """저장소 전체 검사: 일자 파일 구조 불변식 + 메타 해시/행 수 일치 + 봉인본 잠금. 반환 {날짜: [문제…]} (문제 없으면 빈 dict)."""
    issues: Dict[str, List[str]] = {}
    metas = all_metas(tick_root, symbol)
    for d in sorted(set(metas) | set(stored_days(tick_root, symbol))):
        m, path = metas.get(d), day_csv(tick_root, symbol, d)
        probs: List[str] = []
        if m is None:
            probs.append("메타 없음")
        elif m.get("status") == "LOST":
            if os.path.exists(path):
                probs.append("LOST 인데 데이터 파일이 있음")
        elif not os.path.exists(path):
            probs.append("데이터 파일 없음")
        else:
            try:
                s = scan_day(path, d)
                if s["sha256"] != m.get("sha256") or s["rows"] != m.get("rows"):
                    probs.append("메타와 내용 불일치(행/해시)")
            except DayStoreError as e:
                probs.append(str(e))
            if m.get("status") in SEALED and os.access(path, os.W_OK) and os.name != "nt":
                probs.append("봉인본이 잠겨 있지 않음")
        if probs:
            issues[d] = probs
    return issues


def lock_sealed(tick_root: str, symbol: str) -> List[str]:
    """봉인된 날(VALID/ACCEPTED_DEVIATION)의 일자 파일을 읽기 전용으로 잠근다. 반환: 새로 잠근 날짜.
    윈도우 수집기가 WSL 경로에 건 0o444 는 리눅스에서 적용되지 않으므로, 리눅스 쪽(대시보드)이 수집 직후 다시 잠근다.
    봉인 보호의 1차 수단은 메타 status 이고, 이것은 엔진 밖 도구의 실수를 막는 2차 수단이다."""
    locked = []
    if os.name == "nt":
        return locked
    for d, m in all_metas(tick_root, symbol).items():
        p = day_csv(tick_root, symbol, d)
        if m.get("status") in SEALED and os.path.exists(p) and os.stat(p).st_mode & 0o222:
            os.chmod(p, 0o444)
            locked.append(d)
    return locked
