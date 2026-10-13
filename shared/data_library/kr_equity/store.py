"""kr_equity 공통 — 경로·원본 저장·작업 상태·HTTP/DART 클라이언트·유니버스."""
import csv
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from typing import Dict, Iterator, List, Optional, Tuple

import requests

from shared.data_library.paths import data_vault_path

DATASET = "kr_equity"
UA = {"User-Agent": "Mozilla/5.0"}
MARKETS = {"유가증권": "KOSPI", "코스닥": "KOSDAQ", "코넥스": "KONEX"}
DART_REPORTS = [("11013", "1분기"), ("11012", "반기"), ("11014", "3분기"), ("11011", "사업")]
DART_FIRST_YEAR = 2015                 # fnlttMultiAcnt 는 2015 사업연도부터


def stage() -> str:
    return data_vault_path("staging", DATASET)


def path(*parts) -> str:
    return os.path.join(stage(), *parts)


def log(msg: str, name: str = "collect_log.txt"):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    os.makedirs(path("logs"), exist_ok=True)
    with open(path("logs", name), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_once(fp: str, data: bytes, replace: bool = False) -> bool:
    """원본 저장: tmp→rename, 읽기 전용. 이미 있으면 건드리지 않는다(replace=True 는 '자료 없음' 응답 갱신에만)."""
    if os.path.exists(fp) and not replace:
        return False
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    tmp = fp + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    if os.path.exists(fp):
        os.chmod(fp, 0o644)
    os.replace(tmp, fp)
    os.chmod(fp, 0o444)
    return True


def read_json(fp: str) -> Optional[dict]:
    try:
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


class Job:
    """collect/<source>.json — 대시보드가 읽는 진행 상태(덮어쓰기). 실패 목록은 50개까지만 남긴다."""

    def __init__(self, source: str, title: str, target: Optional[int], unit: str):
        self.p = path("collect", f"{source}.json")
        prev = read_json(self.p) or {}
        now = datetime.now().isoformat(timespec="seconds")
        self.s = {"source": source, "title": title, "unit": unit, "target": target, "done": 0, "fetched": 0, "skipped": 0,
                  "failed": [], "failed_n": 0, "started_at": now, "first_started_at": prev.get("first_started_at") or now,
                  "updated_at": None, "finished": False, "stopped_reason": None, "note": "", "pid": os.getpid()}
        self._t = 0.0
        self.save()

    def tick(self, fetched=False, skipped=False, failed: Optional[str] = None):
        self.s["done"] += 1
        self.s["fetched"] += int(fetched)
        self.s["skipped"] += int(skipped)
        if failed:
            self.s["failed_n"] += 1
            if len(self.s["failed"]) < 50:
                self.s["failed"].append(failed)
        if time.time() - self._t > 5:
            self.save()

    def save(self, **kw):
        self.s.update(kw)
        self.s["updated_at"] = datetime.now().isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(self.p), exist_ok=True)
        with open(self.p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.s, f, ensure_ascii=False, indent=1)
        os.replace(self.p + ".tmp", self.p)
        self._t = time.time()

    def finish(self, msg: str = ""):
        self.save(finished=True)
        log(f"[{self.s['source']}] 완료 {self.s['done']} (신규 {self.s['fetched']}, 이미 있음 {self.s['skipped']}, 실패 {self.s['failed_n']}) {msg}")

    def stop(self, reason: str):
        self.save(stopped_reason=reason)
        log(f"[{self.s['source']}] 중단 — {reason}")


def get(url: str, method: str = "get", tries: int = 5, **kw) -> Optional[requests.Response]:
    for attempt in range(tries):
        try:
            r = requests.request(method, url, headers=UA, timeout=kw.pop("timeout", 30), **kw)
            if r.status_code == 200:
                return r
            log(f"  HTTP {r.status_code} {url[:80]}")
        except Exception as e:
            log(f"  재시도 {attempt + 1}: {e}")
        time.sleep(2 * (attempt + 1))
    return None


# ---------------------------------------------------------------- DART
class DartQuota(Exception):
    """하루 한도 초과(status 020) — 단계를 멈추고 다음 날 같은 명령으로 이어받는다."""


def dart_key() -> str:
    """OPEN_DART_KEY — 환경변수, 없으면 로그인 셸(.bashrc)에서 읽는다(앱이 bash -i 없이 떴을 때)."""
    k = os.environ.get("OPEN_DART_KEY")
    if k:
        return k
    try:
        out = subprocess.run(["bash", "-ic", "echo $OPEN_DART_KEY"], capture_output=True, text=True, timeout=15).stdout.strip().splitlines()
        k = out[-1].strip() if out else ""
    except Exception:
        k = ""
    if not k:
        sys.exit("OPEN_DART_KEY 없음 — 환경변수 또는 .bashrc 에 설정")
    os.environ["OPEN_DART_KEY"] = k
    return k


class Dart:
    MIN_GAP = 0.25

    def __init__(self):
        self.key = dart_key()
        self.last = 0.0

    def call(self, api: str, params: dict) -> Tuple[Optional[str], Optional[dict]]:
        """→ (status, json) | (None, None) 네트워크 실패. 020 은 DartQuota. 저장용 params 에는 키를 넣지 않는다."""
        wait = self.MIN_GAP - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        r = get(f"https://opendart.fss.or.kr/api/{api}.json", params={"crtfc_key": self.key, **params})
        if r is None:
            return None, None
        j = r.json()
        if j.get("status") == "020":
            raise DartQuota(j.get("message"))
        return j.get("status"), j

    def save(self, fp: str, api: str, query: dict, j: dict, replace: bool = False) -> bool:
        rec = {"source": f"DART OpenAPI {api}", "query": query, "fetched_at": datetime.now().isoformat(timespec="seconds"), "response": j}
        return write_once(fp, json.dumps(rec, ensure_ascii=False).encode("utf-8"), replace=replace)


def corpcode_zip() -> Optional[str]:
    """가장 최근 raw/dart/corpCode_<날짜>.zip (없으면 None)."""
    d = path("raw", "dart")
    if not os.path.isdir(d):
        return None
    zs = sorted(f for f in os.listdir(d) if f.startswith("corpCode") and f.endswith(".zip"))
    return os.path.join(d, zs[-1]) if zs else None


def corp_rows() -> Iterator[Tuple[str, str, str]]:
    """(corp_code, stock_code, corp_name) — 종목코드 있는 회사만."""
    z = corpcode_zip()
    if not z:
        return iter(())
    zf = zipfile.ZipFile(z)
    root = ET.fromstring(zf.read(zf.namelist()[0]))
    return ((e.findtext("corp_code"), (e.findtext("stock_code") or "").strip(), (e.findtext("corp_name") or "").strip())
            for e in root.iter("list") if (e.findtext("stock_code") or "").strip())


def is_empty_response(j: Optional[dict]) -> bool:
    return not j or j.get("status") == "013" or not j.get("list")


# ---------------------------------------------------------------- 유니버스
UNIVERSE_FIELDS = ["code", "name", "market", "status", "is_preferred", "is_spac", "listed_date", "delist_date", "delist_reason", "price_source"]


def read_universe() -> List[dict]:
    fp = path("universe.csv")
    if not os.path.exists(fp):
        return []
    with open(fp, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def universe_corps() -> Dict[str, str]:
    """유니버스 회사(우선주는 보통주 회사로) → {corp_code: stock_code}."""
    want = {r["code"][1:6] + "0" for r in read_universe()}
    return {corp: stock for corp, stock, _ in corp_rows() if stock in want}


def build_universe() -> dict:
    """reference/listed_snapshot.csv(Creon 상장 스냅샷) + reference/delisted.csv(KIND) → universe.csv, targets_creon.txt.
    같은 코드가 둘 다면 상장 우선 — 단 스냅샷 날짜 이후 폐지면 상폐(Creon 에서 사라졌다)."""
    rows, snap = {}, {}
    with open(path("reference", "listed_snapshot.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            snap[r["code"]] = r.get("snapshot_date", "")
            rows[r["code"]] = {"code": r["code"], "name": r["name"], "market": r["market"], "status": "listed",
                               "is_preferred": r["is_preferred"], "is_spac": r["is_spac"], "listed_date": r["listed_date"],
                               "delist_date": "", "delist_reason": "", "price_source": "creon"}
    dl = path("reference", "delisted.csv")
    if os.path.exists(dl):
        with open(dl, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["market"] == "KONEX":
                    continue
                code = "A" + r["code"]
                if code in rows and r["delist_date"] < snap.get(code, ""):
                    continue
                rows[code] = {"code": code, "name": r["name"], "market": r["market"], "status": "delisted",
                              "is_preferred": int(r["code"][-1] != "0"), "is_spac": int("스팩" in r["name"]), "listed_date": "",
                              "delist_date": r["delist_date"], "delist_reason": r["reason"], "price_source": "naver"}
    with open(path("universe.csv") + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=UNIVERSE_FIELDS)
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))
    os.replace(path("universe.csv") + ".tmp", path("universe.csv"))
    with open(path("targets_creon.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(k for k in sorted(rows) if rows[k]["status"] == "listed") + "\n")
    n = {"listed": sum(r["status"] == "listed" for r in rows.values()), "delisted": sum(r["status"] == "delisted" for r in rows.values())}
    log(f"[universe] 상장 {n['listed']} + 상폐 {n['delisted']}")
    return n
