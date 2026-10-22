"""global_macro 공통 — 경로·로그·원본 저장·HTTP·키."""
import csv
import json
import os
import subprocess
import time
from datetime import datetime
from typing import List, Optional, Tuple

import requests

from shared.data_library.paths import data_vault_path

DATASET = "global_macro"
UA = {"User-Agent": "Mozilla/5.0 (CTradeSystem global_macro)"}


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


def save_raw(rel: str, data: bytes) -> str:
    """원본 저장(tmp→rename). 월·주 거시 시계열은 받은 날짜별 판(vintage)을 남기고, 일봉 시세는 최신 하나만 덮어쓴다 — 호출자가 rel 로 정함."""
    fp = path("raw", rel)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    tmp = fp + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, fp)
    return fp


def write_series(key: str, rows: List[Tuple[str, float]]) -> str:
    """정리본 series/<key>.csv (date,value) — 날짜 오름차순, 중복 날짜는 뒤의 값."""
    d = {}
    for dt, v in rows:
        if v is None:
            continue
        d[dt] = float(v)
    fp = path("series", f"{key}.csv")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    tmp = fp + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "value"])
        for dt in sorted(d):
            w.writerow([dt, repr(d[dt])])
    os.replace(tmp, fp)
    return fp


def read_json(fp: str) -> Optional[dict]:
    try:
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def key_env(name: str) -> str:
    """API 키: 환경변수, 없으면 로그인 셸(bash -ic)에서 읽는다(대시보드 프로세스는 .bashrc 를 안 읽음)."""
    k = os.environ.get(name)
    if not k:
        try:
            k = subprocess.run(["bash", "-ic", f"echo -n ${name}"], capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            k = ""
    if not k:
        raise RuntimeError(f"{name} 없음 — 환경변수 또는 .bashrc 에 설정")
    return k


def get(url: str, timeout: int = 90, tries: int = 4, headers: Optional[dict] = None) -> bytes:
    """HTTP GET(재시도). requests 가 막히면 curl 로 한 번 더(FRED 점검·TLS 차이 대비)."""
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=headers or UA, timeout=timeout)
            if r.status_code == 200:
                return r.content
            last = f"HTTP {r.status_code}"
        except Exception as e:   # noqa: BLE001
            last = repr(e)
        time.sleep(2 * (i + 1))
    try:
        out = subprocess.run(["curl", "-s", "-L", "--max-time", str(timeout), "-A", (headers or UA)["User-Agent"], url],
                             capture_output=True, timeout=timeout + 10)
        if out.returncode == 0 and out.stdout:
            return out.stdout
    except Exception as e:   # noqa: BLE001
        last = repr(e)
    raise RuntimeError(f"받기 실패: {url.split('?')[0]} ({last})")
