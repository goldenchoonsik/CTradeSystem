"""EDGAR 분기 색인(master.gz: CIK|회사명|양식|제출일|파일) 2006Q1~ 수집 — 13D/13G 사건 목록용.

원본 무수정·0o444, 이미 받은 분기는 건너뜀(진행 중인 분기는 매번 다시 받음).
산출: DataVault/staging/us_sec_bulk/raw/edgar_index/{연도}_QTR{분기}_master.gz
"""
import os, sys, time
from datetime import date
from pathlib import Path
import requests

OUT = Path(__file__).resolve().parents[3] / "DataVault/staging/us_sec_bulk/raw/edgar_index"
UA = os.environ.get("SEC_USER_AGENT") or sys.exit("SEC_USER_AGENT 가 없습니다")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cur = (today.year, (today.month - 1) // 3 + 1)
    for y in range(2006, today.year + 1):
        for q in range(1, 5):
            if (y, q) > cur:
                break
            dest = OUT / f"{y}_QTR{q}_master.gz"
            if dest.exists() and (y, q) != cur:
                continue
            r = requests.get(f"https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/master.gz", headers={"User-Agent": UA}, timeout=120)
            r.raise_for_status()
            if dest.exists():
                os.chmod(dest, 0o644)
            dest.write_bytes(r.content)
            os.chmod(dest, 0o444)
            print(f"{dest.name} {len(r.content) / 1e6:.1f}MB", flush=True)
            time.sleep(0.3)


if __name__ == "__main__":
    main()
