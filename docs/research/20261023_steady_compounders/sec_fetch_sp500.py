"""2009-12 이후 당시 S&P500 구성 종목(Yahoo 가격 있는 것)의 SEC companyfacts·submissions 원본 수집 (2026-10-23).

저장: DataVault/staging/us_fundamentals_sec/raw/companyfacts/{현재티커}.json (무수정, 있으면 건너뜀 — 10-16·10-21 수집분 재사용)
      DataVault/staging/us_fundamentals_sec/raw/submissions/{현재티커}.json.gz (업종 SIC·이름 이력)
      reference/sp500_cik.json (티커→CIK)
초당 6회 이하(SEC 공정 접근, SEC_USER_AGENT 필요).  실행: .venv/bin/python -I sec_fetch_sp500.py
"""
import gzip
import json
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sc_common import ROOT, SEC_DIR, members_at, membership, sec_env  # noqa: E402


def main():
    ua = sec_env()
    if not ua:
        sys.exit("SEC_USER_AGENT 없음")
    H = {"User-Agent": ua}
    memb = membership()
    need = set()
    for q in pd.date_range("2009-12-31", "2026-08-18", freq="QE"):
        for _, y in members_at(memb, q):
            if y and os.path.exists(f"{ROOT}/raw/yahoo/candle_1d/{y}_1d.csv"):
                need.add(y)
    tick = requests.get("https://www.sec.gov/files/company_tickers.json", headers=H, timeout=30).json()
    cik = {v["ticker"].replace('.', '-'): int(v["cik_str"]) for v in tick.values()}
    os.makedirs(f"{SEC_DIR}/reference", exist_ok=True)
    os.makedirs(f"{SEC_DIR}/raw/companyfacts", exist_ok=True)
    os.makedirs(f"{SEC_DIR}/raw/submissions", exist_ok=True)
    json.dump({t: cik.get(t) for t in sorted(need)}, open(f"{SEC_DIR}/reference/sp500_cik.json", "w"), indent=1)
    print("대상", len(need), "EDGAR 티커 없음", sorted(t for t in need if t not in cik), flush=True)
    ok = fail = skip = 0
    for t in sorted(need):
        if t not in cik:
            continue
        c = cik[t]
        for kind, url, fp, gz in (
            ("facts", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c:010d}.json", f"{SEC_DIR}/raw/companyfacts/{t}.json", False),
            ("subm", f"https://data.sec.gov/submissions/CIK{c:010d}.json", f"{SEC_DIR}/raw/submissions/{t}.json.gz", True),
        ):
            if os.path.exists(fp):
                skip += 1
                continue
            for k in range(3):
                try:
                    r = requests.get(url, headers=H, timeout=60)
                    break
                except Exception as e:  # noqa: BLE001
                    r = None
                    time.sleep(2 * (k + 1))
            time.sleep(0.17)
            if r is None or r.status_code != 200:
                fail += 1
                print("실패", kind, t, getattr(r, "status_code", "err"), flush=True)
                continue
            data = gzip.compress(r.content) if gz else r.content
            with open(fp + ".tmp", "wb") as f:
                f.write(data)
            os.replace(fp + ".tmp", fp)
            ok += 1
        if (ok + fail) % 100 == 0:
            print("진행", ok, fail, skip, flush=True)
    print("끝 받음", ok, "실패", fail, "건너뜀", skip, flush=True)


if __name__ == "__main__":
    main()
