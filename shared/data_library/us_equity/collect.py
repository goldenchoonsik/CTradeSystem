"""미국 전 종목 일봉 수집 — 종목 목록(nasdaqtrader) → Yahoo 응답 원본 보존 → 원천(raw/yahoo) 반영.

  python -m shared.data_library.us_equity.collect lists            종목 목록만
  python -m shared.data_library.us_equity.collect chart [--limit N] 목록 + 일봉(파일 있으면 마지막 14일 전부터 증분, 없으면 전체)
  python -m shared.data_library.us_equity.collect import --day D    이미 받은 응답 폴더(D)를 원천에 반영(재적재·복구용)

증분은 미국 정규장 마감 뒤(KST 06~22시)에 돌린다 — 장중 응답의 진행 중 봉은 parse_chart 가 빼지만 그날 값은 다음 수집에서야 채워진다.
진행 상태: DataVault/staging/us_equity/collect/yahoo_chart_1d.json (대시보드 '연구 데이터셋' 패널).
"""
import argparse
import csv
import gzip
import json
import os
import time
from datetime import datetime, timedelta
from typing import List, Optional

import requests

from shared.data_library import raw_ingest
from shared.data_library.paths import data_vault_path
from shared.data_library.us_equity import bronze
from shared.data_library.us_equity.yahoo import PERIOD1_ALL, UA, fetch_chart, yahoo_symbol

LISTS = {"nasdaqlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
         "otherlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"}
EXCHANGES = {"A": "NYSE American", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}
PAUSE = 0.5                           # 요청 간격(초)
TAIL_OVERLAP_DAYS = 14                # 증분 수집 시 마지막 봉보다 이만큼 앞에서부터 다시 받아 겹침 검사


def stage(*parts) -> str:
    return data_vault_path("staging", "us_equity", *parts)


def log(msg: str):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    os.makedirs(stage("logs"), exist_ok=True)
    with open(stage("logs", "collect_log.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_lists(day: str) -> List[dict]:
    """nasdaqtrader 목록 원본 보존 → 종목표(reference/universe.csv). 시험 종목 제외, Yahoo 표기 중복 제거."""
    rows = []
    for name, url in LISTS.items():
        fp = data_vault_path("raw", "nasdaqtrader", "symdir", day, f"{name}.txt")
        if not os.path.exists(fp):
            r = requests.get(url, headers=UA, timeout=60)
            r.raise_for_status()
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp + ".tmp", "wb") as f:
                f.write(r.content)
            os.replace(fp + ".tmp", fp)
            os.chmod(fp, 0o444)
        with open(fp, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="|"):
                sym = row.get("Symbol") if name == "nasdaqlisted" else row.get("ACT Symbol")
                if not sym or sym.startswith("File Creation Time") or row.get("Test Issue") != "N":
                    continue
                rows.append({"symbol": sym, "yahoo_symbol": yahoo_symbol(sym), "name": row["Security Name"],
                             "exchange": "NASDAQ" if name == "nasdaqlisted" else EXCHANGES.get(row["Exchange"], row["Exchange"]),
                             "etf": row["ETF"], "list": name, "list_day": day})
    seen, out = set(), []
    for r in rows:
        if r["yahoo_symbol"] not in seen:
            seen.add(r["yahoo_symbol"])
            out.append(r)
    record_listing_changes(day, out)
    os.makedirs(stage("reference"), exist_ok=True)
    with open(stage("reference", "universe.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    return out


LISTING_CHANGES = ("raw", "nasdaqtrader", "listing_changes.csv")
CHANGE_FIELDS = ["day", "prev_day", "change", "yahoo_symbol", "name", "exchange", "etf"]


def _read_list(day: str) -> dict:
    """그날 보존된 목록 원본 → {Yahoo 심볼: 행}."""
    out = {}
    for name in LISTS:
        fp = data_vault_path("raw", "nasdaqtrader", "symdir", day, f"{name}.txt")
        if not os.path.exists(fp):
            continue
        with open(fp, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="|"):
                sym = row.get("Symbol") if name == "nasdaqlisted" else row.get("ACT Symbol")
                if sym and not sym.startswith("File Creation Time") and row.get("Test Issue") == "N":
                    out.setdefault(yahoo_symbol(sym), {"name": row.get("Security Name", ""), "etf": row.get("ETF", ""),
                                                       "exchange": "NASDAQ" if name == "nasdaqlisted" else EXCHANGES.get(row.get("Exchange"), row.get("Exchange", ""))})
    return out


def record_listing_changes(day: str, rows: List[dict]) -> dict:
    """직전 목록과 비교해 신규 상장(added)·목록 제외(removed)를 원장(raw/nasdaqtrader/listing_changes.csv)에 더한다.
    Yahoo 는 상폐 종목을 주지 않으므로, 이 원장이 미국 상폐(·티커 변경) 기록을 우리 손으로 쌓는 유일한 경로다.
    같은 (day, 변화, 심볼)은 한 번만 — 몇 번 돌려도 같다."""
    root = data_vault_path("raw", "nasdaqtrader", "symdir")
    prev_days = sorted(d for d in os.listdir(root) if d < day) if os.path.isdir(root) else []
    if not prev_days:
        return {"added": 0, "removed": 0}
    prev = _read_list(prev_days[-1])
    cur = {r["yahoo_symbol"]: r for r in rows}
    fp = data_vault_path(*LISTING_CHANGES)
    have = set()
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            have = {(r["day"], r["change"], r["yahoo_symbol"]) for r in csv.DictReader(f)}
    new = [{"day": day, "prev_day": prev_days[-1], "change": "added", "yahoo_symbol": s, "name": r["name"],
            "exchange": r["exchange"], "etf": r["etf"]} for s, r in cur.items() if s not in prev]
    new += [{"day": day, "prev_day": prev_days[-1], "change": "removed", "yahoo_symbol": s, "name": r["name"],
             "exchange": r["exchange"], "etf": r["etf"]} for s, r in prev.items() if s not in cur]
    new = [r for r in new if (r["day"], r["change"], r["yahoo_symbol"]) not in have]
    if new:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        first = not os.path.exists(fp)
        with open(fp, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CHANGE_FIELDS)
            if first:
                w.writeheader()
            w.writerows(sorted(new, key=lambda r: (r["change"], r["yahoo_symbol"])))
    n = {"added": sum(r["change"] == "added" for r in new), "removed": sum(r["change"] == "removed" for r in new)}
    if new:
        log(f"상장 변동 {day} (직전 {prev_days[-1]}): 신규 {n['added']} · 제외 {n['removed']}")
    return n


class Job:
    """collect/<source>.json — 대시보드가 읽는 진행 상태. 실패는 전부 all_failed 에, 상태 파일에는 50개까지."""

    def __init__(self, source: str, title: str, target: int):
        self.p = stage("collect", f"{source}.json")
        now = datetime.now().isoformat(timespec="seconds")
        self.s = {"source": source, "title": title, "unit": "종목", "target": target, "done": 0, "fetched": 0, "skipped": 0,
                  "failed": [], "failed_n": 0, "started_at": now, "first_started_at": now, "updated_at": None,
                  "finished": False, "stopped_reason": None, "note": "현재 상장만(상폐 없음)", "pid": os.getpid()}
        self.all_failed: List[str] = []
        self._t = 0.0
        self.save()

    def tick(self, fetched=False, skipped=False, failed: Optional[str] = None):
        self.s["done"] += 1
        self.s["fetched"] += int(fetched)
        self.s["skipped"] += int(skipped)
        if failed:
            self.s["failed_n"] += 1
            self.all_failed.append(failed)
            if len(self.s["failed"]) < 50:
                self.s["failed"].append(failed)
        if time.time() - self._t > 5:
            self.save()

    def finish(self, day: str):
        self.s["finished"] = True
        self.save()
        with open(stage("collect", f"{self.s['source']}_failed_{day}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(self.all_failed))

    def save(self):
        self._t = time.time()
        self.s["updated_at"] = datetime.now().isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(self.p), exist_ok=True)
        with open(self.p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.s, f, ensure_ascii=False, indent=1)
        os.replace(self.p + ".tmp", self.p)


def _ingest(sym: str, raw: bytes, full: bool, job: Job, quiet) -> None:
    try:
        rep = bronze.ingest_response(sym, raw, full_history=full, log=quiet)
    except Exception as e:                       # 응답 원본은 이미 보존됨 — import 로 다시 반영할 수 있다
        job.tick(failed=f"{sym}:ingest:{type(e).__name__}:{e}")
        return
    if rep is None:
        job.tick(failed=f"{sym}:ingest:rejected")
    else:
        if rep.get("conflicts") or rep.get("action_conflicts"):
            log(f"{sym} 확인 필요: 일봉 충돌 {rep.get('conflicts')} · 기업행위 충돌 {rep.get('action_conflicts')}")
        job.tick(fetched=True)


def run_chart(day: str, limit: int = 0) -> None:
    uni = fetch_lists(day)[: limit or None]
    log(f"수집 시작 {day}: 대상 {len(uni)} (ETF {sum(r['etf'] == 'Y' for r in uni)})")
    job = Job("yahoo_chart_1d", f"Yahoo 일봉 ({day})", len(uni))
    sess = requests.Session()
    quiet = lambda m: None
    for i, r in enumerate(uni, 1):
        sym = r["yahoo_symbol"]
        resp = os.path.join(bronze.responses_dir(day), f"{sym}.json.gz")
        if os.path.exists(resp):                 # 이어받기: 응답은 있으니 원천 반영만(멱등)
            _ingest(sym, gzip.open(resp).read(), not os.path.exists(bronze.candle_path(sym)), job, quiet)
            continue
        last = raw_ingest._kst(raw_ingest.last_timestamp(bronze.candle_path(sym)))
        period1 = PERIOD1_ALL if last is None else int((last - timedelta(days=TAIL_OVERLAP_DAYS)).timestamp())
        raw, why = fetch_chart(sym, period1=period1, session=sess, log=log)
        if raw is None:
            job.tick(failed=f"{sym}:{why}")
        else:
            bronze.save_response(sym, raw, day)
            _ingest(sym, raw, last is None, job, quiet)
        if i % 500 == 0:
            log(f"{i}/{len(uni)} 반영 {job.s['fetched']} 실패 {job.s['failed_n']}")
        time.sleep(PAUSE)
    job.finish(day)
    log(f"수집 끝 {day}: 반영 {job.s['fetched']} 실패 {job.s['failed_n']}")


def run_import(day: str, limit: int = 0) -> None:
    """보존된 응답 폴더를 원천에 반영. 응답이 상장 이후 전체인지(period1=1900)는 파일만으로 모르므로, 원천 파일이 없을 때만 전체로 본다."""
    d = bronze.responses_dir(day)
    files = sorted(f for f in os.listdir(d) if f.endswith(".json.gz"))[: limit or None]
    log(f"반영 시작 {day}: 응답 {len(files)}")
    job = Job("yahoo_import_1d", f"Yahoo 응답 → 원천 반영 ({day})", len(files))
    quiet = lambda m: None
    for i, fn in enumerate(files, 1):
        sym = fn[: -len(".json.gz")]
        _ingest(sym, gzip.open(os.path.join(d, fn)).read(), not os.path.exists(bronze.candle_path(sym)), job, quiet)
        if i % 1000 == 0:
            log(f"{i}/{len(files)} 반영 {job.s['fetched']} 실패 {job.s['failed_n']}")
    job.finish(day)
    log(f"반영 끝 {day}: 반영 {job.s['fetched']} 실패 {job.s['failed_n']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["lists", "chart", "import"])
    ap.add_argument("--day", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.step == "lists":
        log(f"목록 {a.day}: {len(fetch_lists(a.day))}")
    elif a.step == "chart":
        run_chart(a.day, a.limit)
    else:
        run_import(a.day, a.limit)


if __name__ == "__main__":
    main()


# ── 대시보드 실행기: 별도 프로세스로 띄운다(서버가 재시작돼도 수집은 계속, 한 번에 하나) ──
RUN_LOCK = ("collect", ".running.json")


def running() -> Optional[dict]:
    """실행 중인 수집 작업(pid 가 살아 있으면) 또는 None. 죽은 잠금은 지운다."""
    fp = stage(*RUN_LOCK)
    try:
        with open(fp, encoding="utf-8") as f:
            info = json.load(f)
        pid = int(info["pid"])
        if _alive(pid):
            return info
    except (OSError, ValueError, KeyError):
        pass
    if os.path.exists(fp):
        os.remove(fp)
    return None


def _alive(pid: int) -> bool:
    """살아 있는 프로세스인가. 대시보드가 띄운 자식은 끝나도 회수 전까지 좀비로 남아 kill(0) 이 성공한다(2026-10-15 실측:
    수집이 끝났는데 '갱신 중'에 버튼이 잠김) — 우리 자식이면 회수하고, /proc 상태가 Z 면 끝난 것으로 본다."""
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass                                   # 우리 자식이 아님(다른 프로세스가 띄움) — 아래로
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def start_background(step: str = "chart", day: Optional[str] = None) -> dict:
    """python -m shared.data_library.us_equity.collect <step> 을 새 세션으로 띄운다. 이미 돌고 있으면 그 정보를 돌려준다."""
    import subprocess
    import sys
    cur = running()
    if cur:
        return {"started": False, "reason": "already_running", **cur}
    day = day or datetime.now().strftime("%Y%m%d")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    os.makedirs(stage("logs"), exist_ok=True)
    out = open(stage("logs", f"run_{step}_{day}.txt"), "a", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "shared.data_library.us_equity.collect", step, "--day", day],
                         cwd=root, stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "PYTHONPATH": root})
    info = {"pid": p.pid, "step": step, "day": day, "started_at": datetime.now().isoformat(timespec="seconds")}
    with open(stage(*RUN_LOCK), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    log(f"대시보드에서 시작: {step} {day} (pid {p.pid})")
    return {"started": True, **info}


def job_status() -> dict:
    """대시보드용 — 실행 여부 + 마지막 작업 상태(collect/yahoo_chart_1d.json)."""
    st = {}
    try:
        with open(stage("collect", "yahoo_chart_1d.json"), encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        pass
    return {"running": running(), "last": {k: st.get(k) for k in ("title", "target", "done", "fetched", "failed_n",
                                                                   "started_at", "updated_at", "finished")} if st else None}
