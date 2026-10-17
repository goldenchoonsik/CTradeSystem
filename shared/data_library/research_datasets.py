"""원장 밖 연구 데이터셋(DataVault/staging/<이름>/) 현황 — 읽기 전용.

연구용 대량 수집(예: 전 종목 1분봉)은 종목 원장(symbols_registry)·동기화·Raw Store 에 넣지 않는다.
이 모듈은 그 폴더들을 훑어 대시보드 '연구 데이터셋' 패널에 진행률·품질·실패를 보여줄 뿐, 아무것도 쓰지 않는다.

폴더 규약(있는 것만 읽는다):
  dataset.json                      이름·목적·생성일·스크립트 위치
  universe.csv                      종목 목록(code, market, ...)
  targets_all.txt                   1분봉 수집 대상
  raw/creon/candle_1m/<code>/       1분봉 일자 저장소
  raw/creon/candle_1d*/<code>_1d.csv 일봉
  driver_log.txt / ingest_log.txt   수집·병합 로그
  inbox_1m/, inbox_1m_failed/       병합 대기·실패
  quality/quality_summary.json      품질 점검 요약
  events/                           연구 산출물
  collect/<source>.json             출처별 수집 작업 진행(수집 스크립트가 덮어씀): target·done·fetched·failed·finished ...
  raw/<provider>/<kind>/            Creon 외 원본(naver/candle_1d/<code>.xml, kind/, dart/ ...)

universe.csv 에 status(listed/delisted)·price_source 열이 있으면 일봉 데이터셋으로 보고 종목별 일봉 기간을 보여준다.
"""
import csv
import json
import os
import re
import subprocess
import time
from datetime import datetime
from typing import Dict, List, Optional

from shared.data_library.paths import data_vault_path

_PROGRESS = re.compile(r"^(\S+ \S+) \[(\w+)\] (\d+)/(\d+) 성공 (\d+) 실패 (\d+) 경과 ([\d.]+)분 \(종목당 ([\d.]+)s\)")
_SIZE_CACHE: Dict[str, tuple] = {}
_SIZE_TTL = 600
_STALE_SEC = 900          # 끝나지 않았는데 이만큼 갱신이 없으면 '멈춤'
_RANGE_CACHE: Dict[str, tuple] = {}
_NAVER_ITEM = re.compile(rb'<item data="(\d{8})\|')


def staging_root() -> str:
    return data_vault_path("staging")


def _read_json(path) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _tail(path, n=1) -> List[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        return [ln for ln in lines if ln.strip()][-n:]
    except OSError:
        return []


def _dir_size(path) -> Optional[int]:
    """du -sb (10분 캐시) — 수십만 파일 폴더를 매 요청마다 훑지 않게."""
    hit = _SIZE_CACHE.get(path)
    if hit and time.time() - hit[0] < _SIZE_TTL:
        return hit[1]
    try:
        out = subprocess.run(["du", "-sb", path], capture_output=True, text=True, timeout=60).stdout
        size = int(out.split()[0])
    except Exception:
        size = None
    _SIZE_CACHE[path] = (time.time(), size)
    return size


def _latest_progress(log_path) -> Dict[str, dict]:
    """드라이버 로그에서 단계별 마지막 진행 줄."""
    out: Dict[str, dict] = {}
    for line in _tail(log_path, 400):
        m = _PROGRESS.match(line)
        if m:
            ts, step, i, n, ok, fail, el, per = m.groups()
            out[step] = {"at": ts, "done": int(i), "total": int(n), "ok": int(ok), "fail": int(fail),
                         "elapsed_min": float(el), "sec_per_item": float(per)}
    return out


def _count_lines(path) -> Optional[int]:
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return None


def _stores(path) -> Dict[str, int]:
    """raw/<provider>/<kind>/ 별 항목 수(종목 폴더 또는 원본 파일). Creon 은 기존대로 kind 이름만, 나머지는 provider/kind."""
    out: Dict[str, int] = {}
    raw = os.path.join(path, "raw")
    if not os.path.isdir(raw):
        return out
    for prov in sorted(os.listdir(raw)):
        pp = os.path.join(raw, prov)
        if not os.path.isdir(pp):
            continue
        for kind in sorted(os.listdir(pp)):
            p = os.path.join(pp, kind)
            if not os.path.isdir(p):
                continue
            entries = os.listdir(p)
            dirs = sum(1 for e in entries if os.path.isdir(os.path.join(p, e)))
            if prov == "creon":
                n = dirs or sum(1 for e in entries if e.endswith(".csv"))
            else:
                n = dirs or sum(1 for e in entries if not e.endswith(".tmp") and not e.startswith("."))
            out[kind if prov == "creon" else f"{prov}/{kind}"] = n
    return out


def _parse_ts(v) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def collect_jobs(path: str, now: Optional[datetime] = None) -> List[dict]:
    """collect/<source>.json → 진행률·남은 시간·상태(done/running/stopped)."""
    d = os.path.join(path, "collect")
    if not os.path.isdir(d):
        return []
    now = now or datetime.now()
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        j = _read_json(os.path.join(d, fn))
        if not j:
            continue
        target, done = j.get("target"), int(j.get("done") or 0)
        upd, start = _parse_ts(j.get("updated_at")), _parse_ts(j.get("started_at"))
        if j.get("finished"):
            state = "done"
        elif j.get("stopped_reason") or (upd and (now - upd).total_seconds() > _STALE_SEC):
            state = "stopped"
        else:
            state = "running"
        eta = None
        if state == "running" and target and start and done:
            el = (now - start).total_seconds()
            eta = round(max(0, target - done) * el / done / 60, 1)
        failed = j.get("failed") or []
        out.append({"source": j.get("source") or fn[:-5], "title": j.get("title") or fn[:-5], "unit": j.get("unit") or "",
                    "target": target, "done": done, "fetched": int(j.get("fetched") or 0), "skipped": int(j.get("skipped") or 0),
                    "pct": round(100 * done / target, 1) if target else None, "state": state, "eta_min": eta,
                    "failed_n": len(failed), "failed": failed[:50], "updated_at": j.get("updated_at"),
                    "first_started_at": j.get("first_started_at"), "stopped_reason": j.get("stopped_reason"), "note": j.get("note") or ""})
    return out


def dataset_status(path: str, with_size: bool = True) -> dict:
    name = os.path.basename(path.rstrip("/"))
    meta = _read_json(os.path.join(path, "dataset.json")) or {}
    st = {"name": name, "path": path, "title": meta.get("title", name), "purpose": meta.get("purpose", ""),
          "created": meta.get("created"), "scripts": meta.get("scripts"), "registered_in_ledger": False,
          "result": meta.get("result", ""), "details": meta.get("details", ""), "status_note": meta.get("status", "")}
    st["has_symbols"] = os.path.exists(os.path.join(path, "universe.csv"))
    uni = os.path.join(path, "universe.csv")
    if os.path.exists(uni):
        by_market: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        excluded = 0
        with open(uni, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("status"):
                    by_status[r["status"]] = by_status.get(r["status"], 0) + 1
                by_market[r.get("market", "?")] = by_market.get(r.get("market", "?"), 0) + 1
                excluded += int(r.get("is_preferred", 0) or 0) or int(r.get("is_spac", 0) or 0)
        st["universe"] = {"total": sum(by_market.values()), "by_market": by_market, "preferred_or_spac": excluded}
        if by_status:
            st["universe"]["by_status"] = by_status
    st["stores"] = _stores(path)
    stores = st["stores"]
    target_n = _count_lines(os.path.join(path, "targets_all.txt"))
    inbox, failed = os.path.join(path, "inbox_1m"), os.path.join(path, "inbox_1m_failed")
    pending = len([f for f in os.listdir(inbox) if f.endswith(".csv.json")]) if os.path.isdir(inbox) else 0
    failed_codes = sorted({f.split(".")[0] for f in os.listdir(failed)}) if os.path.isdir(failed) else []
    progress = _latest_progress(os.path.join(path, "driver_log.txt"))
    m1 = progress.get("minute")
    collect = None
    if target_n:
        done = stores.get("candle_1m", 0)
        finished = os.path.exists(os.path.join(inbox, "DONE"))
        eta_min = None
        if m1 and not finished:
            eta_min = round(max(0, target_n - done) * m1["sec_per_item"] / 60, 1)
        collect = {"target": target_n, "done": done, "pct": round(100 * done / target_n, 1), "finished": finished,
                   "eta_min": eta_min, "inbox_pending": pending, "failed": failed_codes,
                   "download_fail": m1["fail"] if m1 else 0, "last_download": m1["at"] if m1 else None,
                   "last_ingest": (_tail(os.path.join(path, "ingest_log.txt")) or [None])[0]}
    st["collect_1m"] = collect
    st["progress"] = progress
    st["jobs"] = collect_jobs(path)
    st["quality"] = _read_json(os.path.join(path, "quality", "quality_summary.json"))
    ev_dir = os.path.join(path, "events")
    st["outputs"] = sorted(os.listdir(ev_dir)) if os.path.isdir(ev_dir) else []
    dv = os.path.join(path, "derived")
    st["derived"] = sorted(f for f in os.listdir(dv) if not f.startswith(".")) if os.path.isdir(dv) else []
    pq = _read_json(os.path.join(path, "quality", "panel_quality.json"))
    st["panel_quality"] = {"built_at": pq.get("built_at"), "prices": pq.get("prices", {}).get("rows"),
                           "codes": pq.get("prices", {}).get("codes"), "fin_companies": pq.get("fundamentals", {}).get("companies"),
                           "date_max": pq.get("prices", {}).get("date_max")} if pq else None
    st["collector"] = "kr_equity" if name == "kr_equity" else None
    if with_size:
        st["size_bytes"] = _dir_size(path)
    return st


def list_datasets(root: Optional[str] = None, with_size: bool = True) -> List[dict]:
    root = root or staging_root()
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isdir(p) and not name.startswith("."):
            out.append(dataset_status(p, with_size=with_size))
    return out


def _safe_dataset_path(name: str, root: Optional[str] = None) -> Optional[str]:
    """이름 → staging 하위 폴더 (경로 탈출 금지)."""
    root = root or staging_root()
    if not name or name.startswith(".") or os.sep in name or "/" in name or ".." in name:
        return None
    p = os.path.join(root, name)
    return p if os.path.isdir(p) else None


_XML_TAG = re.compile(r"<(korePrlstNm|hsSgn)>([^<]*)</")


def _tree_rows(path: str) -> List[dict]:
    """종목 개념이 없는 데이터셋(raw/<묶음>/<파일...>)의 묶음별 파일 수·크기·연도 범위. 관세청 XML 이면 품목명도 읽는다."""
    raw = os.path.join(path, "raw")
    out = []
    if not os.path.isdir(raw):
        return out
    for grp in sorted(os.listdir(raw)):
        gp = os.path.join(raw, grp)
        if not os.path.isdir(gp):
            continue
        files = [f for f in os.listdir(gp) if os.path.isfile(os.path.join(gp, f)) and not f.startswith(".")]
        years = sorted({m.group(1) for f in files for m in [re.search(r"(20\d\d)", f)] if m})
        size = sum(os.path.getsize(os.path.join(gp, f)) for f in files)
        mt = max((os.path.getmtime(os.path.join(gp, f)) for f in files), default=None)
        label = ""
        if files and sorted(files)[0].endswith(".xml"):
            try:
                with open(os.path.join(gp, sorted(files)[0]), "rb") as fh:
                    tags = dict(_XML_TAG.findall(fh.read(4096).decode("utf-8", errors="replace")))
                label = tags.get("korePrlstNm", "")
            except OSError:
                pass
        out.append({"group": grp, "label": label, "files": len(files), "size": size,
                    "year_from": years[0] if years else "", "year_to": years[-1] if years else "",
                    "updated": datetime.fromtimestamp(mt).strftime("%Y-%m-%d") if mt else ""})
    return out


def files(name: str, root: Optional[str] = None) -> Optional[List[dict]]:
    path = _safe_dataset_path(name, root)
    return None if path is None else _tree_rows(path)


def symbols(name: str, root: Optional[str] = None) -> Optional[List[dict]]:
    """데이터셋의 종목별 상태 — 유니버스 + 수집 상태 + 품질 점검 결과(있으면). 없는 데이터셋이면 None."""
    path = _safe_dataset_path(name, root)
    if path is None:
        return None
    if _is_daily_dataset(path):
        return _daily_symbols(path)
    m1 = os.path.join(path, "raw", "creon", "candle_1m")
    have = set(os.listdir(m1)) if os.path.isdir(m1) else set()
    inbox, failed_dir = os.path.join(path, "inbox_1m"), os.path.join(path, "inbox_1m_failed")
    pending = {f.split(".")[0] for f in os.listdir(inbox) if f.endswith(".csv.json")} if os.path.isdir(inbox) else set()
    failed = {f.split(".")[0] for f in os.listdir(failed_dir)} if os.path.isdir(failed_dir) else set()
    targets = set()
    tp = os.path.join(path, "targets_all.txt")
    if os.path.exists(tp):
        with open(tp, encoding="utf-8") as f:
            targets = {ln.strip() for ln in f if ln.strip()}
    quality = {}
    qp = os.path.join(path, "quality", "quality_by_code.csv")
    if os.path.exists(qp):
        with open(qp, encoding="utf-8") as f:
            quality = {r["code"]: r for r in csv.DictReader(f)}
    out = []
    up = os.path.join(path, "universe.csv")
    if not os.path.exists(up):
        return out
    with open(up, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = r["code"]
            if code in failed:
                state = "failed"
            elif code in pending:
                state = "pending_merge"
            elif code in have:
                state = "done"
            elif targets and code not in targets:
                state = "excluded"
            else:
                state = "waiting"
            q = quality.get(code)

            def num(k, cast=int):
                try:
                    return cast(float(q[k])) if q and q.get(k) not in (None, "") else None
                except ValueError:
                    return None
            out.append({"code": code, "name": r.get("name", ""), "market": r.get("market", ""), "state": state,
                        "preferred": r.get("is_preferred") == "1", "spac": r.get("is_spac") == "1",
                        "supervision": r.get("supervision_kind") == "1", "listed": r.get("listed_date", ""),
                        "checked": q is not None, "m1_days": num("m1_days"), "halted_days": num("halted_days"),
                        "missing_days": num("missing_traded_days"), "quarantined_days": num("st_QUARANTINED"),
                        "adjust_events": num("factor_changes"), "factor_max": num("factor_max", float),
                        "factor_min": num("factor_min", float), "issue": (q or {}).get("issue", "") or ""})
    return out


# ---------------------------------------------------------------- 일봉 데이터셋 (상장=Creon, 상폐=네이버)
def _is_daily_dataset(path) -> bool:
    try:
        with open(os.path.join(path, "universe.csv"), encoding="utf-8") as f:
            return "price_source" in (f.readline().strip().split(","))
    except OSError:
        return False


def _cached_range(fp, reader) -> Optional[dict]:
    """(첫 날, 마지막 날, 봉 수) — 파일 mtime 이 같으면 다시 읽지 않는다."""
    try:
        mt = os.stat(fp).st_mtime
    except OSError:
        return None
    hit = _RANGE_CACHE.get(fp)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        val = reader(fp)
    except Exception:
        val = None
    _RANGE_CACHE[fp] = (mt, val)
    return val


def _creon_range(fp) -> Optional[dict]:
    m = _read_json(fp + ".manifest.json") or {}
    i = m.get("integrity") or {}
    if not i.get("row_count"):
        return None
    return {"first": (i.get("first_timestamp") or "")[:10], "last": (i.get("last_timestamp") or "")[:10], "bars": int(i["row_count"])}


def _naver_range(fp) -> Optional[dict]:
    with open(fp, "rb") as f:
        days = _NAVER_ITEM.findall(f.read())
    if not days:
        return None
    a, b = days[0].decode(), days[-1].decode()
    return {"first": f"{a[:4]}-{a[4:6]}-{a[6:]}", "last": f"{b[:4]}-{b[4:6]}-{b[6:]}", "bars": len(days)}


def _failed_codes(path) -> Dict[str, str]:
    """수집 실패 종목 → 출처. 작업 파일(failed 'code' 또는 'code:사유')과 Creon 드라이버 로그('[daily] 실패 A000001: ...')."""
    out: Dict[str, str] = {}
    for j in collect_jobs(path):
        for item in (_read_json(os.path.join(path, "collect", j["source"] + ".json")) or {}).get("failed") or []:
            code = str(item).split(":")[0]
            if code.isdigit() and len(code) == 6:
                out["A" + code] = j["source"]
    for line in _tail(os.path.join(path, "driver_log.txt"), 2000):
        m = re.search(r"\[(daily\w*)\] 실패 (A\w{6})", line)
        if m:
            out[m.group(2)] = "creon_" + m.group(1)
    return out


def _daily_symbols(path) -> List[dict]:
    failed = _failed_codes(path)
    adj_dir = os.path.join(path, "raw", "creon", "candle_1d")
    unadj_dir = os.path.join(path, "raw", "creon", "candle_1d_unadj")
    naver_dir = os.path.join(path, "raw", "naver", "candle_1d")
    out = []
    with open(os.path.join(path, "universe.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code, src = r["code"], r.get("price_source", "")
            if src == "naver":
                rng = _cached_range(os.path.join(naver_dir, f"{code[1:]}.xml"), _naver_range)
                has_unadj = None
            else:
                fp = os.path.join(adj_dir, f"{code}_1d.csv")
                rng = _cached_range(fp, _creon_range)
                has_unadj = os.path.exists(os.path.join(unadj_dir, f"{code}_1d.csv"))
            state = "failed" if code in failed else "done" if rng else "waiting"
            out.append({"code": code, "name": r.get("name", ""), "market": r.get("market", ""), "state": state,
                        "status": r.get("status", ""), "price_source": src, "listed": r.get("listed_date", ""),
                        "delist_date": r.get("delist_date", ""), "delist_reason": r.get("delist_reason", ""),
                        "preferred": r.get("is_preferred") == "1", "spac": r.get("is_spac") == "1",
                        "first": (rng or {}).get("first"), "last": (rng or {}).get("last"), "bars": (rng or {}).get("bars"),
                        "has_unadj": has_unadj, "failed_by": failed.get(code)})
    return out


# ---------------------------------------------------------------- 수집 단계 실행 (kr_equity 만 — 다른 데이터셋은 읽기 전용)
def steps(name: str, root: Optional[str] = None) -> Optional[dict]:
    """데이터셋의 실행 가능한 단계 목록 + 실행 중 작업 + 최근 로그. 수집기가 없는 데이터셋이면 steps=[]."""
    if _safe_dataset_path(name, root) is None:
        return None
    if name != "kr_equity":
        return {"steps": [], "running": None, "log": []}
    from shared.data_library.kr_equity import runner, sources
    return {"steps": sources.STEPS, "running": runner.running(), "log": runner.log_tail()}


def start_step(name: str, step: str, full: bool = False) -> dict:
    """단계 실행(백그라운드 프로세스). 실패 사유는 RuntimeError 메시지."""
    if name != "kr_equity":
        raise RuntimeError(f"수집기가 없는 데이터셋: {name}")
    from shared.data_library.kr_equity import runner, sources
    if step not in {s["key"] for s in sources.STEPS}:
        raise RuntimeError(f"알 수 없는 단계: {step}")
    return runner.start(step, full=full)
