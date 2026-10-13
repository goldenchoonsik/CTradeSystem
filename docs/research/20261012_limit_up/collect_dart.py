"""최대주주 지분 원본 수집 — DART OpenAPI hyslrSttus (원장 밖 staging).

응답 원본 그대로(0o444, 있으면 건너뜀). 키 = 환경변수 OPEN_DART_KEY (bash -i). DART 하루 한도 2만 건 →
반기(11012)·사업(11011) 보고서만: 2023 사업 ~ 2026 반기, 종목당 6건.
우선주는 같은 회사 보통주 코드(앞 5자리+0)로 조회한다.

  bash -ic ".venv/bin/python docs/research/20261012_limit_up/collect_dart.py"
"""
import json
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

import pandas as pd
import requests

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
ROOT = os.path.join(STAGE, "raw", "dart")
LOG = os.path.join(ROOT, "collect_log.txt")
REPORTS = [("2023", "11011"), ("2024", "11012"), ("2024", "11011"), ("2025", "11012"), ("2025", "11011"), ("2026", "11012")]
MIN_GAP = 1 / 4


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def corp_map():
    z = zipfile.ZipFile(os.path.join(ROOT, "corpCode.zip"))
    root = ET.fromstring(z.read(z.namelist()[0]))
    return {e.findtext("stock_code").strip(): e.findtext("corp_code") for e in root.iter("list") if (e.findtext("stock_code") or "").strip()}


def main():
    key = os.environ["OPEN_DART_KEY"]
    m = corp_map()
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv"))
    codes = sorted({c[1:] if c[1:] in m else c[1:6] + "0" for c in uni.code})
    codes = [c for c in codes if c in m]
    log(f"시작: {len(codes)} 회사 × {len(REPORTS)} 보고서")
    last, calls = 0.0, 0
    for i, code in enumerate(codes):
        for yr, rc in REPORTS:
            fp = os.path.join(ROOT, "hyslr", code, f"{yr}_{rc}.json")
            if os.path.exists(fp):
                continue
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            for attempt in range(5):
                wait = MIN_GAP - (time.time() - last)
                if wait > 0:
                    time.sleep(wait)
                last = time.time()
                try:
                    j = requests.get("https://opendart.fss.or.kr/api/hyslrSttus.json",
                                     params={"crtfc_key": key, "corp_code": m[code], "bsns_year": yr, "reprt_code": rc}, timeout=20).json()
                    break
                except Exception as e:
                    log(f"  재시도 {code} {yr} {rc}: {e}")
                    time.sleep(3 * (attempt + 1))
            else:
                log(f"실패 {code} {yr} {rc}")
                continue
            calls += 1
            if j.get("status") == "020":                    # 요청 한도 초과
                log(f"한도 초과 — 중단 ({calls}건): {j.get('message')}")
                return
            if j.get("status") not in ("000", "013"):        # 013 = 데이터 없음
                log(f"  오류 {code} {yr} {rc}: {j.get('status')} {j.get('message')}")
                continue
            rec = {"source": "DART OpenAPI hyslrSttus", "stock_code": code, "corp_code": m[code], "bsns_year": yr, "reprt_code": rc,
                   "fetched_at": datetime.now().isoformat(timespec="seconds"), "response": j}
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            os.chmod(tmp, 0o444)
            os.replace(tmp, fp)
        if i % 200 == 0:
            log(f"{i+1}/{len(codes)} (호출 {calls})")
    log(f"완료 (호출 {calls})")


if __name__ == "__main__":
    main()
