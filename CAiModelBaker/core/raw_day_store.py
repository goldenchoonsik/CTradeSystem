# -*- coding: utf-8 -*-
"""원천 CSV 의 '날짜 단위 무결성' 엔진 — 병합(트랜잭션) · 전체 검증 · 복구.

왜 필요한가 (2026-09-24 사고):
  구 `merge_and_save_data` 는 행 단위로 합쳤고 "기존 파일은 오름차순"이라고 가정했다. 파일이 처음 만들어질 때 수신 순서(최신→과거)
  그대로 저장돼 내림차순이 되면, 다음 동기화가 `[오름차순][내림차순]` 이중 기록을 만들고 틱은 행이 사라졌다
  (일봉 12종목 전체, 1분봉·틱 일부 종목). 서버가 희소 데이터를 돌려준 날이 온전한 날을 덮어쓸 수도 있었다.
  병합 전 백업도, 병합 후 검증도 없었다.

설계 원칙:
  1. **날짜가 검증·기록 단위**다. 파일은 날짜 블록이 연속·오름차순이어야 하고, 날짜마다 (행 수, 거래량, 내용 해시)를 원장(`<파일>.days.json`)에 남긴다.
  2. **병합 단위는 타임스탬프 묶음**(같은 시각의 행 전부)이다. 같은 시각의 묶음이 신·구 양쪽에 있으면 *더 온전한 쪽*(행 수 → 거래량)을 통째로
     채택한다. 부실한 재수신이 온전한 기존 데이터를 덮어쓸 수 없고, 반쯤 끊긴 마지막 분은 온전한 재수신으로 채워진다.
  3. **트랜잭션**: 임시 파일에 다 쓰고 불변식을 검사한 뒤에만 원자적으로 교체한다. 불변식 위반이면 교체하지 않고 원본을 그대로 둔다.
       - 파일 전체가 시각 오름차순, 각 날짜 블록 연속
       - 어떤 날짜의 행 수·거래량도 병합 전보다 줄지 않는다 (삭제는 병합이 아니라 복구 도구의 명시적 작업)
       - 이번 병합이 건드리지 않은 날짜는 내용 해시가 그대로다 (과거 날짜 불변)
  4. 교체 직전에 이전 파일을 백업한다(최근 N개 유지). 병합 기록은 원장에 남는다.
  5. 표준 라이브러리만 쓴다 — 32-bit Windows 파이썬(메모리 2GB, pandas 없음)에서 도는 수집기가 그대로 import 한다.
     기존 파일은 스트리밍으로 읽고, 신규 수신분(증분)만 메모리에 올린다.

검증(`verify_file`)은 날짜를 하나씩 보며 파일 전체가 유효한지 판정한다: 정렬/연속성, 날짜별 완전 중복 비율, 기준 원천(예: 1분봉)과의
날짜별 거래량 비율, 원장 해시 일치(봉인 이후 변경 여부). 하나라도 무효인 날이 있으면 파일은 무효다.
"""
import csv
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

LEDGER_SUFFIX = ".days.json"
LEDGER_SCHEMA = 1
MAX_HISTORY = 200


class MergeAborted(Exception):
    """불변식 위반/스키마 불일치 등으로 병합을 취소했다 — 원본 파일은 변경되지 않았다."""


class OrderViolation(MergeAborted):
    """기존 파일이 시각 오름차순이 아니다 — 병합 대신 복구(repair_file)가 필요하다."""


# ─────────────────────────────────────────────────────────────────────────────
# 기본 파싱
# ─────────────────────────────────────────────────────────────────────────────
def _norm_ts(raw: str) -> str:
    """첫 열의 시각을 'YYYY-MM-DD HH:MM:SS' 비교용 문자열로. 원천마다 표기가 달라도(ISO 'T', epoch 초/ms/us/ns) 같은 기준으로 정렬·날짜 구분한다.
    epoch 는 UTC 로 해석한다(하위 계층 규약과 동일). 파일에 쓰는 행 자체는 바꾸지 않는다."""
    if raw and (raw[0].isdigit()) and raw.replace(".", "", 1).isdigit() and "-" not in raw:
        try:
            n = float(raw)
            digits = len(str(int(n)))
            div = 1.0 if digits <= 10 else 1e3 if digits <= 13 else 1e6 if digits <= 16 else 1e9
            return datetime.fromtimestamp(n / div, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OverflowError, OSError):
            return raw
    if len(raw) > 10 and raw[10] == "T":
        return raw[:10] + " " + raw[11:19]
    return raw


def _ts(line: str) -> str:
    return _norm_ts(line.split(",", 1)[0].strip().strip('"'))


def _day(ts: str) -> str:
    return ts[:10]


def _valid_ts(ts: str) -> bool:
    return len(ts) >= 10 and ts[:4].isdigit() and ts[4] == "-" and ts[7] == "-"


def is_aligned(ts: str, interval_seconds: int) -> bool:
    """봉 시각이 봉 간격의 격자에 놓였는가. 하루 미만 간격만 검사한다(일봉 이상은 시장마다 기준 시각이 다르다).

    격자 = gcd(간격, 30분): 봉은 초가 0 이어야 하고, 분은 그 격자 위에 있어야 한다. 30분을 넣는 이유 — 시간봉은 거래소 장 시작에
    맞춰 매시 30분에 시작하기도 한다(실측: Yahoo 해외선물 60분봉 22:30/23:30 KST). 반대로 초가 0이 아닌 시각(15:33:10)이나
    5분봉의 18:28 같은 시각은 진행 중이던 봉을 찍어 둔 스냅샷(미완성 봉)이다."""
    if not interval_seconds or interval_seconds >= 86400 or len(ts) < 19:
        return True
    try:
        sec = int(ts[11:13]) * 3600 + int(ts[14:16]) * 60 + int(ts[17:19])
    except ValueError:
        return False
    import math
    return sec % math.gcd(int(interval_seconds), 1800) == 0


def _find_volume_index(header: List[str], volume_col: Optional[str]) -> Optional[int]:
    names = [h.strip().strip('"').lower() for h in header]
    for cand in ([volume_col.lower()] if volume_col else []) + ["upbit_volume", "volume"]:
        if cand in names:
            return names.index(cand)
    return None


def _volume_of(line: str, vol_idx: Optional[int]) -> float:
    if vol_idx is None:
        return 0.0
    try:
        if '"' not in line:                                 # 따옴표가 없으면 csv 파서와 결과가 같다 — 줄마다 파서를 만들지 않는다
            return float(line.split(",")[vol_idx])
        return float(next(csv.reader([line]))[vol_idx])
    except Exception:
        return 0.0


def _read_lines(path: str) -> Iterator[str]:
    """줄 끝(\\r\\n/\\n)을 제거한 행을 스트리밍한다. 빈 줄은 건너뛴다."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if line:
                yield line


def _detect_eol(path: str) -> str:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        return "\r\n" if b"\r\n" in head else "\n"
    except OSError:
        return "\n"


class _DayAcc:
    """날짜 하나의 (행 수, 거래량, 내용 해시, 첫/끝 시각) 누적기"""
    __slots__ = ("rows", "volume", "h", "first", "last")

    def __init__(self):
        self.rows, self.volume, self.first, self.last = 0, 0.0, None, None
        self.h = hashlib.sha256()

    def add(self, line: str, ts: str, vol: float):
        self.rows += 1
        self.volume += vol
        self.h.update(line.encode("utf-8") + b"\n")
        if self.first is None:
            self.first = ts
        self.last = ts

    def as_dict(self) -> Dict[str, Any]:
        return {"rows": self.rows, "volume": round(self.volume, 6), "sha256": self.h.hexdigest(),
                "first": self.first, "last": self.last}


# ─────────────────────────────────────────────────────────────────────────────
# 원장(sidecar)
# ─────────────────────────────────────────────────────────────────────────────
def ledger_path(csv_path: str) -> str:
    return csv_path + LEDGER_SUFFIX


def load_ledger(csv_path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(ledger_path(csv_path), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("schema") == LEDGER_SCHEMA else None
    except Exception:
        return None


def _write_json_atomic(path: str, obj: Any):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _save_ledger(csv_path: str, days: Dict[str, Dict[str, Any]], event: Dict[str, Any], prev: Optional[Dict[str, Any]]):
    history = list((prev or {}).get("history", []))[-(MAX_HISTORY - 1):] + [event]
    _write_json_atomic(ledger_path(csv_path), {
        "schema": LEDGER_SCHEMA, "updated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": sum(d["rows"] for d in days.values()), "days": days, "history": history,
        "unrecoverable": list((prev or {}).get("unrecoverable", []))})


def record_unrecoverable(csv_path: str, after_ts: str, before_ts: str, reason: str) -> Optional[Dict[str, Any]]:
    """after_ts 와 before_ts 사이(양 끝 제외)를 '영구 결손'으로 원장에 기록한다 — 수집기가 서버 데이터를 끝까지 받았는데도
    기존 기록 지점(after_ts)에 닿지 못했을 때 부른다. 다음 동기화의 결측 되감기가 이 구간을 다시 받으려 하지 않게 한다.
    보존 기간을 코드에 적지 않고 서버의 실제 응답으로 판단한다."""
    if not after_ts or not before_ts or _day(before_ts) <= _day(after_ts):
        return None
    led = load_ledger(csv_path) or {"schema": LEDGER_SCHEMA, "days": {}, "history": []}
    rng = {"after": after_ts, "before": before_ts, "reason": reason, "at": datetime.now().isoformat(timespec="seconds")}
    items = [r for r in led.get("unrecoverable", []) if not (r["after"] == after_ts and r["before"] == before_ts)] + [rng]
    led["unrecoverable"] = items
    led.setdefault("rows", sum(d.get("rows", 0) for d in led.get("days", {}).values()))
    _write_json_atomic(ledger_path(csv_path), led)
    return rng


def unrecoverable_ranges(csv_path: str) -> List[Tuple[str, str]]:
    """[(after_day, before_day)] — 이 사이(양 끝 제외)의 날짜는 영구 결손으로 기록돼 있다."""
    led = load_ledger(csv_path) or {}
    return [(_day(r["after"]), _day(r["before"])) for r in led.get("unrecoverable", [])]


def is_unrecoverable(day: str, ranges: List[Tuple[str, str]]) -> bool:
    return any(a < day < b for a, b in ranges)


# ─────────────────────────────────────────────────────────────────────────────
# 스트리밍 스캔
# ─────────────────────────────────────────────────────────────────────────────
def iter_groups(path: str) -> Iterator[Tuple[str, List[str]]]:
    """(타임스탬프, [그 시각의 행들]) 을 파일 순서대로 낸다. 시각이 줄어들면 OrderViolation."""
    cur_ts, bucket, prev_ts = None, [], ""
    lines = _read_lines(path)
    next(lines, None)                                   # 헤더
    for line in lines:
        ts = _ts(line)
        if not _valid_ts(ts):
            raise MergeAborted(f"시각 형식이 아닌 행: {line[:60]!r}")
        if ts < prev_ts:
            raise OrderViolation(f"{os.path.basename(path)}: 시각이 거꾸로 감({prev_ts} → {ts}) — 정렬되지 않은 파일은 병합하지 않고 복구해야 한다")
        if ts != cur_ts:
            if bucket:
                yield cur_ts, bucket
            cur_ts, bucket = ts, []
        bucket.append(line)
        prev_ts = ts
    if bucket:
        yield cur_ts, bucket


def read_header(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return f.readline().rstrip("\r\n") or None
    except OSError:
        return None


def _load_new_groups(tmp_csv: str, newest_first: Optional[bool] = None) -> Tuple[str, Dict[str, List[str]]]:
    """신규 수신 파일 → (헤더, {ts: [행…]}). 시각 사이의 순서는 정렬로 해결된다(수신 순서와 무관). **같은 시각 안의 행 순서**(틱의 체결 순서)는
    그 묶음이 놓인 흐름의 방향으로 복원한다: 앞뒤 시각이 줄어드는 흐름(최신→과거) 안에 있으면 뒤집고, 늘어나는 흐름이면 그대로 둔다.
    오름·내림이 섞인 파일이어도 묶음마다 판단한다. 흐름이 바뀌는 꼭짓점에 있거나 시각이 하나뿐이라 방향을 알 수 없는 묶음만
    newest_first(수집기 힌트, 기본 최신→과거)를 쓴다. 꼭짓점에서는 같은 날짜 안의 이웃 방향을 우선한다."""
    lines = _read_lines(tmp_csv)
    header = next(lines, "")
    runs: List[Tuple[str, List[str]]] = []            # 연속된 같은 시각 묶음 (수신 순서)
    for line in lines:
        ts = _ts(line)
        if not _valid_ts(ts):
            continue
        if runs and runs[-1][0] == ts:
            runs[-1][1].append(line)
        else:
            runs.append((ts, [line]))
    hint_desc = True if newest_first is None else bool(newest_first)
    groups: Dict[str, List[str]] = {}
    for i, (ts, ls) in enumerate(runs):
        enter = None if i == 0 else ("down" if runs[i - 1][0] > ts else "up")
        leave = None if i == len(runs) - 1 else ("down" if ts > runs[i + 1][0] else "up")
        if enter and leave and enter != leave:
            # 흐름이 바뀌는 꼭짓점: 같은 날짜 안의 이웃 쪽 방향을 따른다 (날짜가 바뀌는 쪽은 블록이 이어 붙은 자리일 가능성이 크다)
            same_prev = _day(runs[i - 1][0]) == _day(ts)
            same_next = _day(runs[i + 1][0]) == _day(ts)
            desc = (enter == "down") if same_prev and not same_next else (leave == "down") if same_next and not same_prev else hint_desc
        else:
            d = enter or leave
            desc = hint_desc if d is None else d == "down"
        groups.setdefault(ts, []).extend(reversed(ls) if desc else ls)
    return header, groups


# ─────────────────────────────────────────────────────────────────────────────
# 백업
# ─────────────────────────────────────────────────────────────────────────────
def _backup(target: str, backup_dir: Optional[str], keep: int, log: Callable[[str], None]) -> Optional[str]:
    if not backup_dir or not os.path.exists(target):
        return None
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(backup_dir, f"{os.path.basename(target)}.{stamp}.bak")
    try:
        os.link(target, dst)            # 같은 볼륨이면 즉시·무용량. 교체는 항상 os.replace(새 inode)라 백업 내용은 변하지 않는다
    except OSError:
        shutil.copy2(target, dst)       # UNC/다른 파일시스템 등 하드링크가 안 되면 복사
    olds = sorted(p for p in os.listdir(backup_dir) if p.startswith(os.path.basename(target) + ".") and p.endswith(".bak"))
    for p in olds[:-keep]:
        try:
            os.remove(os.path.join(backup_dir, p))
        except OSError:
            pass
    log(f"   🛟 [Raw Merge] 병합 전 백업: {dst}")
    return dst


# ─────────────────────────────────────────────────────────────────────────────
# 병합 (트랜잭션)
# ─────────────────────────────────────────────────────────────────────────────
def _norm_line(line: str) -> Tuple:
    """값 비교용 정규화 — 부동소수 표기 차이(432.79998779296875 vs 432.7999877929688)를 같은 값으로 본다."""
    out = []
    for x in line.split(","):
        try:
            out.append(round(float(x), 6))
        except ValueError:
            out.append(x.strip())
    return tuple(out)


def _contains(big: List[str], small: List[str]) -> bool:
    """big 이 small 의 모든 행을 (중복 개수까지) 포함하는가 — '보강'인지 '다른 데이터'인지 가르는 기준."""
    cnt: Dict[Tuple, int] = {}
    for l in big:
        k = _norm_line(l); cnt[k] = cnt.get(k, 0) + 1
    for l in small:
        k = _norm_line(l)
        if cnt.get(k, 0) <= 0:
            return False
        cnt[k] -= 1
    return True


def conflicts_path(csv_path: str) -> str:
    return csv_path + ".conflicts.json"


def load_conflicts(csv_path: str) -> List[Dict[str, Any]]:
    try:
        with open(conflicts_path(csv_path), "r", encoding="utf-8") as f:
            return json.load(f).get("items", [])
    except Exception:
        return []


def pending_conflicts(csv_path: str) -> List[Dict[str, Any]]:
    return [c for c in load_conflicts(csv_path) if c.get("status") == "pending"]


def _save_conflicts(csv_path: str, items: List[Dict[str, Any]]):
    _write_json_atomic(conflicts_path(csv_path), {"schema": 1, "file": os.path.basename(csv_path), "items": items})


def _add_conflicts(csv_path: str, found: List[Dict[str, Any]]):
    """같은 시각에 기존과 다른 데이터가 들어온 곳을 '확인 필요' 목록에 쌓는다 (같은 시각·같은 신규 내용은 한 번만)."""
    if not found:
        return
    items = load_conflicts(csv_path)
    key = lambda c: (c["ts"], tuple(sorted(_norm_line(l) for l in c["new"])))      # 받은 순서가 달라도 같은 내용이면 같은 항목
    seen = {key(c) for c in items if c.get("status") == "pending"}
    for c in found:
        if key(c) not in seen:
            items.append(c); seen.add(key(c))
    _save_conflicts(csv_path, items)


def _better(new: List[str], old: List[str], vol_idx: Optional[int], tie_goes_to_new: bool = False) -> bool:
    """같은 시각의 묶음 중 신규가 더 온전한가: 행이 많으면, 같으면 거래량이 크면.
    행 수·거래량이 같은데 내용만 다를 때는 기존 파일의 **최신 날짜(아직 형성 중일 수 있는 마지막 봉)** 에 한해 신규를 채택한다
    (tie_goes_to_new). 봉인된 과거 날짜의 충돌은 기존 값을 유지하고 conflicts 로 센다."""
    if len(new) != len(old):
        return len(new) > len(old)
    nv, ov = sum(_volume_of(l, vol_idx) for l in new), sum(_volume_of(l, vol_idx) for l in old)
    if nv > ov + 1e-9:
        return True
    return tie_goes_to_new and abs(nv - ov) <= 1e-9 and new != old


def _tail_day(path: str) -> Optional[str]:
    """파일 마지막 행의 날짜 (꼬리만 읽는다)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode("utf-8", "ignore").splitlines()
        for line in reversed(tail):
            ts = _ts(line)
            if _valid_ts(ts):
                return _day(ts)
    except OSError:
        pass
    return None


_AUTO = object()


def merge_file(new_tmp_csv: str, target_csv: str, *, volume_col: Optional[str] = None,
               backup_dir: Optional[str] = None, keep_backups: int = 3,
               reference_day_volume: Optional[Callable[[str], Optional[float]]] = None,
               newest_first: Optional[bool] = None,
               forming_day: Any = _AUTO, write_ledger: bool = True, conflicts_target: Optional[str] = None,
               log: Callable[[str], None] = print) -> Dict[str, Any]:
    """신규 수신분을 기존 원천에 날짜 단위 불변식을 지키며 병합한다. 반환: 병합 보고서. 실패는 MergeAborted.

    같은 시각의 묶음이 양쪽에 있을 때:
      - 기존 파일의 마지막 날(아직 형성 중일 수 있음): 더 온전한 쪽(행 수 → 거래량, 동률이면 신규).
      - 봉인된 과거 날짜: 신규가 기존 행을 모두 포함하고 더 많으면 '보강'으로 채택. 기존이 신규를 모두 포함하면 기존 유지(부분 재수신).
        그 밖에 **내용이 다르면 기존 값을 유지하고 '확인 필요'(<파일>.conflicts.json)에 양쪽을 기록**한다 — 자동으로 고르지 않는다.

    일자별 저장소(tick_day_store)용 선택 인자 — 기본값이면 지금 동작 그대로다:
      forming_day      '아직 형성 중인 날'을 직접 지정한다(기본: 대상 파일의 마지막 날). 일자 파일은 마지막 날이 늘 자기 자신이라
                       기본값을 쓰면 모든 과거 날이 느슨한 규칙을 받게 되므로, 저장소 전체의 최신 날을 넘긴다(None = 형성 중인 날 없음).
      write_ledger     False 면 <파일>.days.json 원장을 쓰지 않는다(날짜 메타가 대신한다).
      conflicts_target '확인 필요' 목록을 <conflicts_target>.conflicts.json 에 쓴다(기본: 대상 파일).
    """
    report: Dict[str, Any] = {"target": target_csv, "added_rows": 0, "replaced_groups": 0, "kept_groups": 0,
                              "conflicts": 0, "touched_days": [], "created": False}
    if not os.path.exists(new_tmp_csv) or os.path.getsize(new_tmp_csv) == 0:
        return report
    new_header, new_groups = _load_new_groups(new_tmp_csv, newest_first)
    found_conflicts: List[Dict[str, Any]] = []
    if not new_groups:
        os.remove(new_tmp_csv)
        return report
    eol = _detect_eol(target_csv) if os.path.exists(target_csv) else "\n"
    tmp_out = f"{target_csv}.merge.{os.getpid()}.{int(time.time())}.tmp"
    prev_ledger = load_ledger(target_csv)
    exists = os.path.exists(target_csv) and os.path.getsize(target_csv) > 0
    old_header = read_header(target_csv) if exists else None
    if exists and old_header and old_header.strip() != new_header.strip():
        raise MergeAborted(f"헤더가 다르다: 기존 {old_header[:60]!r} / 신규 {new_header[:60]!r}")
    header = old_header or new_header
    vol_idx = _find_volume_index(next(csv.reader([header])), volume_col)

    latest_day = (_tail_day(target_csv) if exists else None) if forming_day is _AUTO else forming_day
    old_acc: Dict[str, _DayAcc] = {}
    out_acc: Dict[str, _DayAcc] = {}
    touched: set = set()
    new_ts_sorted = sorted(new_groups)
    ni = 0
    prev_out_ts = ""

    def emit(w, ts, lines):
        nonlocal prev_out_ts
        if ts < prev_out_ts:                               # 구조상 일어나선 안 된다 — 방어
            raise MergeAborted(f"출력 순서 위반 {prev_out_ts} → {ts}")
        prev_out_ts = ts
        acc = out_acc.setdefault(_day(ts), _DayAcc())
        for l in lines:
            w.write(l + eol)
            acc.add(l, ts, _volume_of(l, vol_idx))

    try:
        with open(tmp_out, "w", encoding="utf-8", newline="") as w:
            w.write(header + eol)
            if exists:
                for ts, old_lines in iter_groups(target_csv):
                    oacc = old_acc.setdefault(_day(ts), _DayAcc())
                    for l in old_lines:
                        oacc.add(l, ts, _volume_of(l, vol_idx))
                    while ni < len(new_ts_sorted) and new_ts_sorted[ni] < ts:      # 기존에 없는 더 이른 시각
                        nts = new_ts_sorted[ni]
                        emit(w, nts, new_groups[nts]); touched.add(_day(nts)); report["added_rows"] += len(new_groups[nts]); ni += 1
                    if ni < len(new_ts_sorted) and new_ts_sorted[ni] == ts:          # 같은 시각 — 더 온전한 쪽
                        nl = new_groups[ts]
                        if nl == old_lines or (len(nl) == len(old_lines) and [_norm_line(x) for x in nl] == [_norm_line(x) for x in old_lines]):
                            emit(w, ts, old_lines)
                        elif _day(ts) == latest_day:
                            if _better(nl, old_lines, vol_idx, tie_goes_to_new=True):
                                emit(w, ts, nl); touched.add(_day(ts))
                                report["replaced_groups"] += 1; report["added_rows"] += len(nl) - len(old_lines)
                            else:
                                emit(w, ts, old_lines); report["kept_groups"] += 1
                        elif len(nl) > len(old_lines) and _contains(nl, old_lines):            # 보강
                            emit(w, ts, nl); touched.add(_day(ts))
                            report["replaced_groups"] += 1; report["added_rows"] += len(nl) - len(old_lines)
                        elif len(nl) <= len(old_lines) and _contains(old_lines, nl):           # 부분 재수신 — 기존이 더 온전
                            emit(w, ts, old_lines); report["kept_groups"] += 1
                        else:                                                                  # 다른 데이터 — 확인 필요
                            emit(w, ts, old_lines); report["kept_groups"] += 1; report["conflicts"] += 1
                            found_conflicts.append({"day": _day(ts), "ts": ts, "old": old_lines, "new": nl, "status": "pending",
                                                    "detected_at": datetime.now().isoformat(timespec="seconds")})
                        ni += 1
                    else:
                        emit(w, ts, old_lines)
            while ni < len(new_ts_sorted):
                nts = new_ts_sorted[ni]
                emit(w, nts, new_groups[nts]); touched.add(_day(nts)); report["added_rows"] += len(new_groups[nts]); ni += 1
        report["created"] = not exists

        # ── 불변식 (날짜 단위) ──
        if exists:
            for d, oa in old_acc.items():
                na = out_acc.get(d)
                if na is None:
                    raise MergeAborted(f"{d}: 기존 날짜가 결과에서 사라졌다")
                if na.rows < oa.rows or na.volume < oa.volume - 1e-6:
                    raise MergeAborted(f"{d}: 병합 후 행/거래량이 줄었다 (행 {oa.rows}→{na.rows}, 거래량 {oa.volume:g}→{na.volume:g})")
                if d not in touched and na.h.hexdigest() != oa.h.hexdigest():
                    raise MergeAborted(f"{d}: 건드리지 않은 과거 날짜의 내용이 바뀌었다")
        # 외부 변경 감지: 원장에 기록된 마지막 상태와 지금 기존 파일이 다르면 (엔진을 거치지 않고 누군가 고쳤다는 뜻) 기록한다
        external = []
        if prev_ledger:
            for d, oa in old_acc.items():
                rec = prev_ledger["days"].get(d)
                if rec and rec["sha256"] != oa.h.hexdigest():
                    external.append(d)
        if external:
            log(f"   ⚠️ [Raw Merge] 원장 이후 외부에서 바뀐 날짜 {len(external)}개 감지: {external[:5]}")

        report["touched_days"] = sorted(touched)
        if found_conflicts:
            _add_conflicts(conflicts_target or target_csv, found_conflicts)
            log(f"   ⚠️ [Raw Merge] {os.path.basename(target_csv)}: 같은 시각에 기존과 다른 데이터 {len(found_conflicts)}곳 — 기존 값을 유지하고 "
                f"확인 목록에 올렸습니다 (날짜: {sorted({c['day'] for c in found_conflicts})[:6]})")
        if exists and not touched:
            # 바뀐 것이 없다 — 파일을 다시 쓰지도, 백업하지도 않는다 (수백 MB 재기록과 백업 누적 방지). 원장만 없으면 만든다.
            os.remove(tmp_out)
            if os.path.exists(new_tmp_csv):
                os.remove(new_tmp_csv)
            if prev_ledger is None and write_ledger:
                _save_ledger(target_csv, {d: a.as_dict() for d, a in out_acc.items()},
                             {"at": datetime.now().isoformat(timespec="seconds"), "note": "baseline"}, None)
            return report
        backup = _backup(target_csv, backup_dir, keep_backups, log) if exists else None
        os.replace(tmp_out, target_csv)
    except BaseException:
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        raise

    days = {d: a.as_dict() for d, a in out_acc.items()}
    report["days"] = days
    if not write_ledger:
        if os.path.exists(new_tmp_csv):
            os.remove(new_tmp_csv)
        return report
    if reference_day_volume:
        for d in report["touched_days"]:
            ref = reference_day_volume(d)
            if ref:
                days[d]["ref_ratio"] = round(days[d]["volume"] / ref, 4)
    _save_ledger(target_csv, days, {"at": datetime.now().isoformat(timespec="seconds"), "added_rows": report["added_rows"],
                                   "replaced_groups": report["replaced_groups"], "kept_groups": report["kept_groups"],
                                   "conflicts": report["conflicts"], "touched_days": report["touched_days"][:30],
                                   "external_changes": external[:30], "created": report["created"]}, prev_ledger)
    if os.path.exists(new_tmp_csv):
        os.remove(new_tmp_csv)
    return report


# ─────────────────────────────────────────────────────────────────────────────
# 기준 원천(예: 1분봉)의 날짜별 거래량
# ─────────────────────────────────────────────────────────────────────────────
def reference_volumes_from_csv(path: str, volume_col: Optional[str] = None) -> Dict[str, float]:
    """기준 CSV 의 {날짜: 거래량 합}. 같은 시각의 중복 행은 하나로 본다(기준 원천 자체가 이중 기록이어도 왜곡되지 않게)."""
    by_ts: Dict[str, float] = {}
    if not path or not os.path.exists(path):
        return {}
    lines = _read_lines(path)
    header = next(lines, "")
    vol_idx = _find_volume_index(next(csv.reader([header])), volume_col)
    for line in lines:
        ts = _ts(line)
        if _valid_ts(ts):
            v = _volume_of(line, vol_idx)
            if v > by_ts.get(ts, -1.0):
                by_ts[ts] = v
    out: Dict[str, float] = {}
    for ts, v in by_ts.items():
        out[_day(ts)] = out.get(_day(ts), 0.0) + v
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 전체 검증 — 날짜를 하나씩 보며 파일 전체가 유효한지
# ─────────────────────────────────────────────────────────────────────────────
def verify_file(path: str, *, volume_col: Optional[str] = None, reference: Optional[Dict[str, float]] = None,
                ref_tolerance: float = 0.02, dup_limit: float = 0.20, check_ledger: bool = True,
                trading_days: Optional[Iterable[str]] = None, interval_seconds: Optional[int] = None) -> Dict[str, Any]:
    """파일을 처음부터 끝까지 읽어 날짜별로 판정한다. 파일은 **모든 날짜가 유효할 때만** valid 이다.

    날짜별 검사: 블록 연속성(같은 날짜가 떨어져 두 번 나오면 무효), 시각 오름차순, 완전 중복 비율(dup_limit 초과 = 이중 기록),
    기준 원천 대비 거래량 비율(ref_tolerance 밖 = 누락/부풀림), 원장 해시 일치(봉인 뒤 변경).
    """
    res: Dict[str, Any] = {"path": path, "valid": True, "file_issues": [], "days": {}}
    if not os.path.exists(path):
        res.update(valid=False, file_issues=["파일 없음"]); return res
    lines = _read_lines(path)
    header = next(lines, "")
    vol_idx = _find_volume_index(next(csv.reader([header])), volume_col)
    accs: Dict[str, _DayAcc] = {}
    seen: Dict[str, set] = {}
    dups: Dict[str, int] = {}
    blocks: Dict[str, int] = {}
    back: Dict[str, int] = {}
    misaligned: Dict[str, int] = {}
    cur_day, prev_ts = None, ""
    for line in lines:
        ts = _ts(line)
        if not _valid_ts(ts):
            res["file_issues"].append(f"시각 형식 아님: {line[:50]!r}"); continue
        d = _day(ts)
        if d != cur_day:
            blocks[d] = blocks.get(d, 0) + 1
            cur_day = d
        if ts < prev_ts:
            back[d] = back.get(d, 0) + 1
        prev_ts = ts
        if interval_seconds and not is_aligned(ts, interval_seconds):
            misaligned[d] = misaligned.get(d, 0) + 1
        acc = accs.setdefault(d, _DayAcc())
        acc.add(line, ts, _volume_of(line, vol_idx))
        h = hash(line)
        s = seen.setdefault(d, set())
        if h in s:
            dups[d] = dups.get(d, 0) + 1
        else:
            s.add(h)
    ledger = load_ledger(path) if check_ledger else None
    for d in sorted(accs):
        a = accs[d]
        info = a.as_dict()
        issues: List[str] = []
        if blocks.get(d, 1) > 1:
            issues.append(f"날짜 블록이 {blocks[d]}조각으로 흩어져 있음(정렬 위반/이중 기록)")
        if back.get(d):
            issues.append(f"시각 역행 {back[d]}회")
        if misaligned.get(d):
            issues.append(f"봉 간격에 맞지 않는 시각 {misaligned[d]}행(미완성 봉/주기 불일치)")
        dup_share = dups.get(d, 0) / a.rows if a.rows else 0.0
        info["dup_share"] = round(dup_share, 4)
        if dup_share > dup_limit:
            issues.append(f"완전 중복 행 {dup_share:.0%} (이중 기록)")
        if reference and reference.get(d):
            ratio = a.volume / reference[d]
            info["ref_ratio"] = round(ratio, 4)
            if abs(ratio - 1.0) > ref_tolerance:
                issues.append(f"기준 원천 대비 거래량 {ratio:.3f}배")
        elif reference is not None:
            info["ref_ratio"] = None
        if ledger:
            rec = ledger["days"].get(d)
            if rec is None:
                info["ledger"] = "미기록"
            elif rec["sha256"] != info["sha256"]:
                info["ledger"] = "변경됨"
                issues.append("원장에 기록된 이후 내용이 바뀜(엔진을 거치지 않은 수정)")
            else:
                info["ledger"] = "일치"
        info["issues"] = issues
        info["valid"] = not issues
        res["days"][d] = info
    if ledger:
        for d in ledger["days"]:
            if d not in accs:
                res["days"][d] = {"rows": 0, "issues": ["원장에는 있는데 파일에서 사라진 날짜"], "valid": False, "ledger": "사라짐"}
    # 확인 필요: 같은 시각에 기존과 다른 데이터가 들어와 보류된 곳
    pend = pending_conflicts(path) if check_ledger else []
    for c in pend:
        v = res["days"].setdefault(c["day"], {"rows": 0, "issues": [], "valid": False})
        msg = "같은 시각에 다른 데이터가 들어옴 — 사용자 확인 필요"
        if msg not in v["issues"]:
            v["issues"].append(msg)
        v["valid"] = False
    # 거래일 누락: 파일의 첫날~마지막 날 사이에 거래일인데 한 줄도 없는 날 (영구 결손으로 기록된 날은 그렇게 표시)
    missing: Dict[str, str] = {}
    if trading_days is not None and accs:
        first, last = min(accs), max(accs)
        ranges = unrecoverable_ranges(path)
        for d in trading_days:
            if first < d < last and d not in accs:
                missing[d] = "영구 결손(서버에 더 이상 없음)" if is_unrecoverable(d, ranges) else "누락"
    res["missing_days"] = missing
    res["pending_conflicts"] = len(pend)
    res["valid"] = not res["file_issues"] and all(v["valid"] for v in res["days"].values())
    bad = sorted(d for d, v in res["days"].items() if not v["valid"])
    res["complete"] = not missing
    res["summary"] = {"days": len(res["days"]), "valid_days": len(res["days"]) - len(bad), "invalid_days": bad,
                      "missing_days": sorted(missing), "pending_conflicts": len(pend)}
    return res


def ledger_mismatches(path: str) -> Dict[str, str]:
    """원장이 있으면 파일을 한 번 스트리밍해 날짜별 해시를 원장과 대조한다. {날짜: 'changed'|'vanished'} — 원장이 없거나 모두 일치하면 {}.
    (엔진을 거치지 않은 수정, 예: 편집기 저장/외부 스크립트/손상)"""
    led = load_ledger(path)
    if not led or not os.path.exists(path):
        return {}
    accs: Dict[str, Any] = {}
    lines = _read_lines(path)
    next(lines, None)
    for line in lines:
        ts = _ts(line)
        if _valid_ts(ts):
            h = accs.get(_day(ts))
            if h is None:
                h = accs[_day(ts)] = hashlib.sha256()
            h.update(line.encode("utf-8") + b"\n")
    out = {}
    for d, rec in led["days"].items():
        if d not in accs:
            out[d] = "vanished"
        elif accs[d].hexdigest() != rec["sha256"]:
            out[d] = "changed"
    return out


def rebuild_ledger(path: str, volume_col: Optional[str] = None, note: str = "rebuild") -> Dict[str, Any]:
    """현재 파일 내용을 기준선으로 원장을 (재)작성한다. 검증을 통과한 파일에만 쓴다."""
    v = verify_file(path, volume_col=volume_col, check_ledger=False)
    days = {d: {k: i[k] for k in ("rows", "volume", "sha256", "first", "last") if k in i} for d, i in v["days"].items()}
    _save_ledger(path, days, {"at": datetime.now().isoformat(timespec="seconds"), "note": note}, load_ledger(path))
    return v


# ─────────────────────────────────────────────────────────────────────────────
# 복구 — 이미 손상된 파일을 날짜 단위로 고친다
# ─────────────────────────────────────────────────────────────────────────────
def _index_day_blocks(path: str) -> Tuple[str, str, Dict[str, List[Tuple[int, int]]]]:
    """(헤더, 줄끝, {날짜: [(시작 바이트, 끝 바이트)…]}) — 정렬 여부와 무관하게 날짜별 블록 위치만 색인한다 (본문은 메모리에 올리지 않음)."""
    blocks: Dict[str, List[Tuple[int, int]]] = {}
    with open(path, "rb") as f:
        header_raw = f.readline()
        eol = "\r\n" if header_raw.endswith(b"\r\n") else "\n"
        header = header_raw.decode("utf-8").rstrip("\r\n")
        cur, start = None, None
        pos = f.tell()
        for raw in iter(f.readline, b""):
            end = pos + len(raw)
            if raw.strip():
                d = _norm_ts(raw.split(b",", 1)[0].decode("utf-8", "ignore").strip().strip('"'))[:10]
                if d != cur:
                    if cur is not None:
                        blocks.setdefault(cur, []).append((start, pos))
                    cur, start = d, pos
            pos = end
        if cur is not None:
            blocks.setdefault(cur, []).append((start, pos))
    return header, eol, {d: b for d, b in blocks.items() if _valid_ts(d)}


def _read_blocks(path: str, spans: List[Tuple[int, int]]) -> List[str]:
    out: List[str] = []
    with open(path, "rb") as f:
        for s, e in spans:
            f.seek(s)
            out.extend(l for l in f.read(e - s).decode("utf-8", "ignore").splitlines() if l.strip())
    return out


def _sorted_stable(lines: List[str]) -> List[str]:
    return sorted(lines, key=_ts)          # 파이썬 sorted 는 안정 정렬 — 같은 시각 안의 파일 내 순서를 보존


def _halved(lines: List[str]) -> List[str]:
    counts: Dict[str, int] = {}
    for l in lines:
        counts[l] = counts.get(l, 0) + 1
    keep = {l: (c + 1) // 2 for l, c in counts.items()}
    out, used = [], {}
    for l in lines:
        if used.get(l, 0) < keep[l]:
            out.append(l); used[l] = used.get(l, 0) + 1
    return out


def _unique(lines: List[str]) -> List[str]:
    seen, out = set(), []
    for l in lines:
        if l not in seen:
            seen.add(l); out.append(l)
    return out


def _dedupe_by_ts(lines: List[str], vol_idx: Optional[int]) -> List[str]:
    best: Dict[str, str] = {}
    for l in lines:
        ts = _ts(l)
        if ts not in best or _volume_of(l, vol_idx) > _volume_of(best[ts], vol_idx):
            best[ts] = l
    return [best[ts] for ts in sorted(best)]


def repair_file(path: str, *, kind: str = "tick", volume_col: Optional[str] = None,
                reference: Optional[Dict[str, float]] = None, sources: Iterable[str] = (),
                backup_dir: Optional[str] = None, apply: bool = False, ref_tolerance: float = 0.02,
                log: Callable[[str], None] = print) -> Dict[str, Any]:
    """손상된 원천 파일을 날짜 단위로 복구한다.

    kind='tick'   : 같은 시각에 여러 행이 정당하게 존재한다. 날짜마다 후보(그대로 정렬 / 이중 기록 절반으로 / 완전 중복 제거)를
                    만들고 **기준 원천(reference) 거래량에 가장 가까운 후보**를 고른다. 기준이 없는 날은 손대지 않고 표시만 한다.
    kind='candle' : 시각이 유일 키다. 정렬하고 같은 시각의 중복은 거래량이 큰 행 하나만 남긴다.
    sources       : 같은 스키마의 다른 파일(예: 격리된 백업). 날짜별로 기준 거래량에 더 가까우면(1%p 이상 개선) 그 날짜를 가져온다.
    apply=False 면 계획만 세우고 파일은 쓰지 않는다. apply=True 면 새 파일을 만들어 전체 검증을 통과했을 때만 백업 후 교체한다.
    """
    header, eol, idx = _index_day_blocks(path)
    vol_idx = _find_volume_index(next(csv.reader([header])), volume_col)
    src_idx = []
    for sp in sources:
        if os.path.exists(sp):
            h, _, i2 = _index_day_blocks(sp)
            if h.strip() == header.strip():
                src_idx.append((sp, i2))
            else:
                log(f"   ⚠️ 소스 {os.path.basename(sp)} 헤더 불일치 — 건너뜀")

    def vol(lines):
        return sum(_volume_of(l, vol_idx) for l in lines)

    def score(lines, day):
        ref = (reference or {}).get(day)
        return abs(vol(lines) / ref - 1.0) if ref else None

    plan: Dict[str, Dict[str, Any]] = {}
    tmp_out = f"{path}.repair.{os.getpid()}.tmp"
    all_days = sorted(set(idx) | {d for _, i2 in src_idx for d in i2 if (reference or {}).get(d)})
    with open(tmp_out, "w", encoding="utf-8", newline="") as w:
        w.write(header + eol)
        for day in all_days:
            lines = _read_blocks(path, idx[day]) if day in idx else []
            cands: List[Tuple[str, List[str]]] = []
            if lines:
                if kind == "candle":
                    cands.append(("ts별 중복 제거", _dedupe_by_ts(lines, vol_idx)))
                else:
                    base = _sorted_stable(lines)
                    cands += [("그대로(정렬)", base), ("이중 기록→절반", _sorted_stable(_halved(lines))),
                              ("완전 중복 제거", _sorted_stable(_unique(lines)))]
            info: Dict[str, Any] = {"rows_before": len(lines), "blocks": len(idx.get(day, []))}
            cur_name, cur_lines = (cands[0] if cands else ("없음", []))
            if kind == "tick" and reference and reference.get(day) and cands:
                scored = [(score(c, day), i, n, c) for i, (n, c) in enumerate(cands)]
                best = min(scored, key=lambda t: t[0])
                # 그대로(정렬) 후보가 이미 기준 거래량에 0.5%p 이내로 맞으면 손대지 않는다 — 노이즈 수준의 차이로 실제 체결을 지우지 않기 위해
                base = scored[0]
                chosen = best if best[0] + 0.005 < base[0] else base
                cur_name, cur_lines = chosen[2], chosen[3]
                info["ref_before"] = round(vol(lines) / reference[day], 4)
            elif kind == "tick" and not (reference or {}).get(day):
                info["note"] = "기준 원천 없음 — 손대지 않음(정렬만)"
            # 다른 원천에서 더 온전한 날 가져오기
            cur_score = score(cur_lines, day)
            for sp, i2 in src_idx:
                if day not in i2 or cur_score is None and lines:
                    continue
                sl = _read_blocks(sp, i2[day])
                sc = [(score(c, day), n, c) for n, c in ([("그대로(정렬)", _sorted_stable(sl)), ("이중 기록→절반", _sorted_stable(_halved(sl))),
                                                         ("완전 중복 제거", _sorted_stable(_unique(sl)))] if kind == "tick"
                                                        else [("ts별 중복 제거", _dedupe_by_ts(sl, vol_idx))])
                      if score(c, day) is not None]
                if not sc:
                    continue
                sbest = min(sc, key=lambda t: t[0])
                if cur_score is None or sbest[0] + 0.01 < cur_score:
                    cur_name, cur_lines, cur_score = f"소스 {os.path.basename(sp)} · {sbest[1]}", sbest[2], sbest[0]
            info.update(action=cur_name, rows_after=len(cur_lines))
            if reference and reference.get(day):
                info["ref_after"] = round(vol(cur_lines) / reference[day], 4)
            plan[day] = info
            for l in cur_lines:
                w.write(l + eol)
    ok_after = verify_file(tmp_out, volume_col=volume_col, reference=reference, ref_tolerance=ref_tolerance, check_ledger=False)
    report = {"path": path, "plan": plan, "valid_after": ok_after["valid"], "verify_after": ok_after["summary"],
              "changed_days": [d for d, i in plan.items() if i["action"] not in ("그대로(정렬)", "ts별 중복 제거") or i["rows_after"] != i["rows_before"] or i["blocks"] > 1]}
    if not apply:
        os.remove(tmp_out)
        return report
    if not ok_after["file_issues"] and all(("정렬" not in " ".join(v["issues"]) and "흩어져" not in " ".join(v["issues"])) for v in ok_after["days"].values()):
        _backup(path, backup_dir, keep=5, log=log)
        os.replace(tmp_out, path)
        rebuild_ledger(path, volume_col=volume_col, note="repair")
        report["applied"] = True
    else:
        os.remove(tmp_out)
        report["applied"] = False
        log("   ❌ 복구 결과가 구조 검증(정렬/연속성)을 통과하지 못해 교체하지 않았습니다")
    return report


# ─────────────────────────────────────────────────────────────────────────────
# 재구성 — 새로 받은 원천을 1순위로, 옛 사본으로 부족한 날짜만 채운다 (2026-09-25 전체 재수집)
# ─────────────────────────────────────────────────────────────────────────────

def _day_variants(lines: List[str], kind: str, vol_idx: Optional[int], clean: bool) -> List[Tuple[str, List[str]]]:
    """한 날짜의 후보 변형들. clean=True(새로 받은 파일)면 정렬만, 옛 사본이면 이중 기록을 되돌리는 변형까지 만든다."""
    if kind != "tick":
        return [("ts별 중복 제거", _dedupe_by_ts(lines, vol_idx))]
    base = _sorted_stable(lines)
    if clean:
        return [("그대로(정렬)", base)]
    return [("그대로(정렬)", base), ("이중 기록→절반", _sorted_stable(_halved(lines))), ("완전 중복 제거", _sorted_stable(_unique(lines)))]


def _same_values(a: str, b: str, rel: float = 1e-6) -> bool:
    """두 CSV 행이 값으로 같은가 — 저장 도구마다 다른 부동소수 표기(432.79998779296875 vs 432.7999877929688)는 같은 값으로 본다."""
    if a == b:
        return True
    xa, xb = a.split(","), b.split(",")
    if len(xa) != len(xb):
        return False
    for u, v in zip(xa, xb):
        if u == v:
            continue
        try:
            fu, fv = float(u), float(v)
        except ValueError:
            return False
        if abs(fu - fv) > rel * max(1.0, abs(fu), abs(fv)):
            return False
    return True


def assemble_file(fresh: Optional[str], sources: Iterable[str], out_path: str, *, kind: str,
                  reference: Optional[Dict[str, float]] = None, volume_col: Optional[str] = None,
                  ref_tolerance: float = 0.02, note: str = "assemble", log: Callable[[str], None] = print) -> Dict[str, Any]:
    """새로 받은 파일(fresh)을 1순위로 삼고, 옛 사본(sources, 앞쪽이 우선)으로 부족한 날짜만 채워 out_path 에 새 파일을 만든다.

    옛 사본은 순서가 뒤섞였거나 이중 기록됐을 수 있으므로 파일 안 위치와 무관하게 날짜별로 모아 정렬한다. 하루는 한 출처에서만 가져온다
    (1분봉의 경계일 보충만 예외 — 시각이 유일 키라 빠진 시각만 채운다).

    kind='tick'  : 기준(reference, 날짜별 1분봉 거래량)에 새 파일이 ref_tolerance 안이면 새 파일. 아니면 모든 후보 중 기준에 가장 가까운 것
                   (동률이면 새 파일). 기준이 없는 날은 새 파일, 없으면 우선순위가 가장 높은 사본의 완전 중복 제거본.
    kind='candle': 시각이 유일 키. 새 파일의 시각이 우선이고, 사본에만 있는 시각을 채운다. 같은 시각 값이 다르면 conflicts 로 센다.
    kind='daily' : 수정주가 — 받은 시점마다 과거 가격이 달라지므로 섞지 않는다. 새 파일이 덮는 기간은 새 파일만, 그보다 오래된 날짜만 사본에서.
    결과 파일은 전체 검증 후 원장을 새로 쓴다. 반환: 날짜별 출처(plan)와 요약.
    """
    if kind not in ("tick", "candle", "daily"):
        raise ValueError(kind)
    inputs: List[Tuple[str, str, Dict[str, List[Tuple[int, int]]], bool]] = []   # (라벨, 경로, 색인, 새 파일 여부)
    header = None
    if fresh and os.path.exists(fresh) and os.path.getsize(fresh) > 0:
        h, _, i = _index_day_blocks(fresh)
        header = h
        inputs.append(("fresh", fresh, i, True))
    for sp in sources:
        if not (sp and os.path.exists(sp) and os.path.getsize(sp) > 0):
            continue
        h, _, i = _index_day_blocks(sp)
        if header is None:
            header = h
        if h.strip() != header.strip():
            log(f"   ⚠️ 사본 {sp} 헤더 불일치 — 건너뜀")
            continue
        inputs.append((f"src:{sp}", sp, i, False))
    if header is None:
        raise MergeAborted("입력 파일이 하나도 없습니다")
    vol_idx = _find_volume_index(next(csv.reader([header])), volume_col)
    fresh_idx = inputs[0][2] if inputs and inputs[0][3] else {}
    fresh_first = min(fresh_idx) if fresh_idx else None

    def vol(lines):
        return sum(_volume_of(l, vol_idx) for l in lines)

    plan: Dict[str, Dict[str, Any]] = {}
    conflicts = 0
    all_days = sorted({d for _, _, i, _ in inputs for d in i})
    tmp_out = f"{out_path}.assemble.{os.getpid()}.tmp"
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(tmp_out, "w", encoding="utf-8", newline="") as w:
        w.write(header + "\n")
        for day in all_days:
            ref = (reference or {}).get(day)
            cands: List[Tuple[int, str, str, List[str]]] = []          # (입력 순위, 출처, 변형, 행)
            for rank, (label, path, idx, is_fresh) in enumerate(inputs):
                if day in idx:
                    for vname, vlines in _day_variants(_read_blocks(path, idx[day]), kind, vol_idx, is_fresh):
                        cands.append((rank, label, vname, vlines))
            fresh_c = next((c for c in cands if c[0] == 0 and inputs[0][3]), None)
            info: Dict[str, Any] = {}
            chosen: Optional[Tuple[int, str, str, List[str]]] = None
            out_lines: List[str] = []
            if kind == "tick":
                if ref:
                    score = lambda c: abs(vol(c[3]) / ref - 1.0)
                    if fresh_c and score(fresh_c) <= ref_tolerance:
                        chosen = fresh_c
                    else:
                        chosen = min(cands, key=lambda c: (round(score(c), 6), c[0]))
                    info["ref_ratio"] = round(vol(chosen[3]) / ref, 4)
                    if fresh_c:
                        info["fresh_ratio"] = round(vol(fresh_c[3]) / ref, 4)
                else:
                    chosen = fresh_c or next((c for c in cands if c[2] == "완전 중복 제거"), cands[0])
                    info["note"] = "기준 없음"
                out_lines = chosen[3]
            elif kind == "daily":
                if fresh_c:
                    chosen = fresh_c
                elif fresh_first is None or day < fresh_first:
                    chosen = cands[0]
                else:
                    info["note"] = "새 수신 기간 안의 누락일 — 수정주가 기준이 달라 옛 값을 쓰지 않음"
                out_lines = chosen[3] if chosen else []
            else:
                by_ts: Dict[str, str] = {}
                origin: Dict[str, int] = {}
                for c in sorted(cands, key=lambda c: c[0]):
                    for l in c[3]:
                        ts = _ts(l)
                        if ts not in by_ts:
                            by_ts[ts] = l
                            origin[ts] = c[0]
                        elif not _same_values(l, by_ts[ts]):
                            conflicts += 1
                            info["conflicts"] = info.get("conflicts", 0) + 1
                            info.setdefault("conflict_example", [by_ts[ts], l])
                out_lines = [by_ts[t] for t in sorted(by_ts)]
                ranks = sorted(set(origin.values()))
                chosen = next(c for c in cands if c[0] == ranks[0])
                if len(ranks) > 1:
                    info["filled_ts"] = sum(1 for r in origin.values() if r != ranks[0])
            if chosen:
                info.update(origin=chosen[1], variant=chosen[2], rows=len(out_lines))
                if chosen[0] != 0 or not inputs[0][3]:
                    info["from_backup"] = True
            plan[day] = info
            for l in out_lines:
                w.write(l + "\n")
    v = verify_file(tmp_out, volume_col=volume_col, reference=reference if kind == "tick" else None,
                    ref_tolerance=ref_tolerance, check_ledger=False)
    structural = [d for d, i in v["days"].items() if any(("흩어져" in s or "역행" in s) for s in i["issues"])]
    if v["file_issues"] or structural:
        os.remove(tmp_out)
        raise MergeAborted(f"재구성 결과 구조 검증 실패: {v['file_issues'][:3]} {structural[:5]}")
    os.replace(tmp_out, out_path)
    rebuild_ledger(out_path, volume_col=volume_col, note=note)
    counts: Dict[str, int] = {}
    for i in plan.values():
        k = "fresh" if i.get("origin") == "fresh" else ("backup" if i.get("origin") else "skipped")
        counts[k] = counts.get(k, 0) + 1
    return {"path": out_path, "plan": plan, "counts": counts, "conflicts": conflicts, "valid": v["valid"],
            "invalid_days": v["summary"]["invalid_days"], "fresh_first": fresh_first,
            "inputs": [p for _, p, _, _ in inputs]}



def resolve_conflicts(csv_path: str, day: Optional[str] = None, *, use: str, backup_dir: Optional[str] = None,
                      volume_col: Optional[str] = None, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """'확인 필요' 항목을 사용자가 결정한 대로 처리한다. use='old' 는 기존 값 유지(기록만 닫음), use='new' 는 그 시각의 행을 신규로 교체.
    day 를 주면 그 날짜만, 없으면 보류 중인 전부."""
    if use not in ("old", "new"):
        raise ValueError(use)
    items = load_conflicts(csv_path)
    targets = [c for c in items if c.get("status") == "pending" and (day is None or c["day"] == day)]
    if not targets:
        return {"resolved": 0}
    if use == "new":
        repl = {c["ts"]: c["new"] for c in targets}
        tmp = f"{csv_path}.resolve.{os.getpid()}.tmp"
        eol = _detect_eol(csv_path)
        with open(tmp, "w", encoding="utf-8", newline="") as w:
            w.write(read_header(csv_path) + eol)
            for ts, lines in iter_groups(csv_path):
                for l in repl.get(ts, lines):
                    w.write(l + eol)
        v = verify_file(tmp, volume_col=volume_col, check_ledger=False)
        if v["file_issues"] or any(("흩어져" in i or "역행" in i) for d in v["days"].values() for i in d["issues"]):
            os.remove(tmp)
            raise MergeAborted("교체 결과가 구조 검증을 통과하지 못해 적용하지 않았습니다")
        _backup(csv_path, backup_dir, keep=5, log=log)
        os.replace(tmp, csv_path)
        rebuild_ledger(csv_path, volume_col=volume_col, note=f"resolve_conflicts:new:{day or 'all'}")
    now = datetime.now().isoformat(timespec="seconds")
    for c in targets:
        c["status"] = f"resolved_{use}"; c["resolved_at"] = now
    _save_conflicts(csv_path, items)
    return {"resolved": len(targets), "use": use, "days": sorted({c["day"] for c in targets})}



# ─────────────────────────────────────────────────────────────────────────────
# 공통 수집 진입점 — 모든 원천(크레온·해외선물·가상자산·cTrader …)이 이 한 절차로 저장한다
# ─────────────────────────────────────────────────────────────────────────────

def widen_header(csv_path: str, new_header: str, *, backup_dir: Optional[str] = None, log: Callable[[str], None] = print) -> bool:
    """기존 파일 헤더에 없는 열이 새로 들어올 때, 기존 열 순서를 그대로 두고 새 열을 뒤에 붙인다(기존 행은 빈 값).
    기존 열을 지우거나 순서를 바꾸는 변경은 하지 않는다(그런 변경은 MergeAborted)."""
    old = read_header(csv_path)
    if old is None or old.strip() == new_header.strip():
        return False
    oc = next(csv.reader([old])); nc = next(csv.reader([new_header]))
    extra = [c for c in nc if c not in oc]
    if not extra:
        return False
    eol = _detect_eol(csv_path)
    tmp = f"{csv_path}.widen.{os.getpid()}.tmp"
    pad = "," * len(extra)
    with open(tmp, "w", encoding="utf-8", newline="") as w:
        w.write(",".join(oc + extra) + eol)
        lines = _read_lines(csv_path); next(lines, None)
        for l in lines:
            w.write(l + pad + eol)
    _backup(csv_path, backup_dir, keep=3, log=log)
    os.replace(tmp, csv_path)
    rebuild_ledger(csv_path, note=f"widen_header:+{','.join(extra)}")
    log(f"   🧩 [Raw Ingest] {os.path.basename(csv_path)}: 새 열 {extra} 추가 (기존 행은 빈 값)")
    return True


def _reorder_to_header(tmp_csv: str, target_header: str) -> None:
    """신규 수신 파일의 열을 대상 파일 헤더 순서로 맞춘다(없는 열은 빈 값). 대상에 없는 열이 있으면 호출 전에 widen_header 로 넓힌다."""
    src_header = read_header(tmp_csv)
    if src_header is None or src_header.strip() == target_header.strip():
        return
    sc = next(csv.reader([src_header])); tc = next(csv.reader([target_header]))
    missing = [c for c in sc if c not in tc]
    if missing:
        raise MergeAborted(f"대상 파일에 없는 열 {missing} — widen_header 로 먼저 넓혀야 한다")
    pos = [sc.index(c) if c in sc else None for c in tc]
    out = f"{tmp_csv}.reorder"
    with open(tmp_csv, "r", encoding="utf-8", newline="") as r, open(out, "w", encoding="utf-8", newline="") as w:
        rd = csv.reader(r); next(rd, None)
        wr = csv.writer(w, lineterminator="\n")
        wr.writerow(tc)
        for row in rd:
            if row:
                wr.writerow([row[i] if i is not None and i < len(row) else "" for i in pos])
    os.replace(out, tmp_csv)


def ingest_file(new_tmp_csv: str, target_csv: str, *, quarantine_dir: str, interval_seconds: Optional[int] = None,
                reference_csv: Optional[str] = None, volume_col: Optional[str] = None, newest_first: Optional[bool] = None,
                requested_after: Optional[str] = None, source_exhausted: bool = False,
                lost_days_fn: Optional[Callable[[str, str], List[str]]] = None,
                log: Callable[[str], None] = print) -> Optional[Dict[str, Any]]:
    """모든 원천 수집기가 거치는 **단일 저장 절차**. 반환: 병합 보고서(실패 시 None — 원본은 그대로, 신규 수신분은 격리).

      1. 열 맞추기: 대상에 없는 새 열은 뒤에 붙이고(widen_header), 신규 수신분의 열 순서를 대상에 맞춘다.
      2. 봉 간격 검사: interval_seconds 가 있으면 격자에 맞지 않는 시각(미완성 봉·주기 불일치)은 저장하지 않고 격리한다.
      3. 병합: merge_file — 수신 순서와 무관, 날짜 단위 불변식·백업·원장, 봉인된 과거와 다른 데이터는 '확인 필요'.
      4. 영구 결손: 원천이 줄 수 있는 데이터를 다 받았는데(source_exhausted) 요청 기점(requested_after)에 닿지 못했으면,
         그 사이 거래일(lost_days_fn)을 원장에 영구 결손으로 기록하고 경고한다.
    """
    if not os.path.exists(new_tmp_csv) or os.path.getsize(new_tmp_csv) == 0:
        if os.path.exists(new_tmp_csv):
            os.remove(new_tmp_csv)
        return {"added_rows": 0, "touched_days": [], "conflicts": 0}
    os.makedirs(quarantine_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = os.path.basename(target_csv)

    def _reject(reason: str) -> None:
        os.makedirs(os.path.join(quarantine_dir, "rejected_merges"), exist_ok=True)
        keep = os.path.join(quarantine_dir, "rejected_merges", f"{name}.{stamp}.csv")
        try:
            os.replace(new_tmp_csv, keep)
        except OSError:
            keep = new_tmp_csv
        log(f"   ❌ [Raw Ingest] {name}: 저장 취소 — 원본은 그대로입니다: {reason}")
        log(f"      신규 수신분은 보관했습니다: {keep}")

    try:
        # 1. 열
        new_header = read_header(new_tmp_csv) or ""
        if os.path.exists(target_csv) and os.path.getsize(target_csv) > 0:
            widen_header(target_csv, new_header, backup_dir=os.path.join(quarantine_dir, "merge_backups"), log=log)
            _reorder_to_header(new_tmp_csv, read_header(target_csv))
        # 2. 봉 간격
        oldest = None
        if interval_seconds or requested_after:
            kept, bad = [], []
            lines = _read_lines(new_tmp_csv); header = next(lines, "")
            for l in lines:
                ts = _ts(l)
                if not _valid_ts(ts):
                    bad.append(l); continue
                if interval_seconds and not is_aligned(ts, interval_seconds):
                    bad.append(l); continue
                kept.append(l)
                oldest = ts if oldest is None or ts < oldest else oldest
            if bad:
                os.makedirs(os.path.join(quarantine_dir, "misaligned"), exist_ok=True)
                bp = os.path.join(quarantine_dir, "misaligned", f"{name}.{stamp}.csv")
                with open(bp, "w", encoding="utf-8", newline="") as w:
                    w.write(header + "\n" + "".join(l + "\n" for l in bad))
                log(f"   ⚠️ [Raw Ingest] {name}: 봉 간격에 맞지 않거나 시각이 잘못된 {len(bad)}행은 저장하지 않았습니다(미완성 봉 등) → {bp}")
                with open(new_tmp_csv, "w", encoding="utf-8", newline="") as w:
                    w.write(header + "\n" + "".join(l + "\n" for l in kept))
                if not kept:
                    os.remove(new_tmp_csv)
                    return {"added_rows": 0, "touched_days": [], "conflicts": 0}
        # 3. 병합
        ref_fn = None
        if reference_csv and os.path.exists(reference_csv):
            ref_fn = reference_volumes_from_csv(reference_csv).get
        rep = merge_file(new_tmp_csv, target_csv, volume_col=volume_col, newest_first=newest_first,
                         backup_dir=os.path.join(quarantine_dir, "merge_backups"), reference_day_volume=ref_fn, log=log)
    except MergeAborted as e:
        _reject(str(e)); return None
    except Exception as e:
        _reject(f"예외 {e!r}"); return None

    # 4. 영구 결손
    if requested_after and source_exhausted and oldest and _day(oldest) > _day(_norm_ts(requested_after)):
        after = _norm_ts(requested_after)
        lost = lost_days_fn(_day(after), _day(oldest)) if lost_days_fn else []
        if lost:
            record_unrecoverable(target_csv, after, oldest, reason="원천 보존 기간 경과(원천이 더 이전 데이터를 주지 않음)")
            log(f"   🚨 [Raw Ingest] 영구 결손 {len(lost)}일: {name} — 기존 기록({after}) 이후 ~ 원천의 가장 오래된 데이터({oldest}) 이전은 "
                f"원천에 더 이상 없어 받을 수 없습니다: {lost[:8]}{' …' if len(lost) > 8 else ''}")
    if rep.get("created"):
        log(f"   💾 [Raw Ingest] 신규 원천 생성(시각 오름차순): {target_csv}")
    else:
        log(f"   💾 [Raw Ingest] {name}: 신규 {rep['added_rows']:+,}행 · 보강 {rep['replaced_groups']}묶음 · "
            f"기존 유지 {rep['kept_groups']}묶음 · 갱신 날짜 {len(rep['touched_days'])}일")
    return rep
