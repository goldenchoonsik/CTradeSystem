"""받은편지함(inbox_1m)의 1분봉 수신 원본을 일자 저장소에 병합·봉인한다 (Linux).

universe_driver.py minute 이 Windows 에서 떨군 <key>.csv + <key>.csv.json 을 수집기와 같은 함수
creon_collector._candle_day_save 로 넣는다 — 저장 규칙(봉인·격리·완결된 날만)은 실수집과 동일.

  .venv/bin/python docs/research/20261011_vol_spike/ingest_inbox.py [--watch]
--watch: 수집기 종료 표시(inbox_1m/DONE)가 생기고 받은편지함이 빌 때까지 반복.
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
os.environ["CTRADE_DATA_VAULT_ROOT"] = STAGE
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "CAiModelBaker", "core"))

import creon_collector as cc  # noqa: E402

INBOX = os.path.join(STAGE, "inbox_1m")
FAILED = os.path.join(STAGE, "inbox_1m_failed")
LOG = os.path.join(STAGE, "ingest_log.txt")


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ingest_one(meta_path):
    with open(meta_path, encoding="utf-8") as f:
        m = json.load(f)
    csv_path = meta_path[: -len(".json")]
    quiet = []
    saved = cc._candle_day_save(csv_path, m["key"], m["last_ts"], m["reached_existing_point"], m["server_exhausted"],
                                datetime.fromisoformat(m["collected_at"]), local_log=quiet.append)
    if saved:
        if os.path.exists(csv_path):
            os.remove(csv_path)
        os.remove(meta_path)
        return True
    os.makedirs(FAILED, exist_ok=True)
    for p in (csv_path, meta_path):
        if os.path.exists(p):
            os.replace(p, os.path.join(FAILED, os.path.basename(p)))
    log(f"실패 {m['key']}: {' | '.join(str(q) for q in quiet[-3:])}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    a = ap.parse_args()
    os.makedirs(INBOX, exist_ok=True)
    done = ok = fail = 0
    t0 = time.time()
    while True:
        metas = sorted(glob.glob(os.path.join(INBOX, "*.csv.json")))
        for mp in metas:
            if ingest_one(mp):
                ok += 1
            else:
                fail += 1
            done += 1
            if done % 50 == 0:
                log(f"병합 {done} (성공 {ok} 실패 {fail}) 종목당 {(time.time() - t0) / done:.1f}s")
        if not a.watch or (os.path.exists(os.path.join(INBOX, "DONE")) and not glob.glob(os.path.join(INBOX, "*.csv.json"))):
            break
        time.sleep(15)
    log(f"종료: 병합 {done} (성공 {ok} 실패 {fail})")


if __name__ == "__main__":
    main()
