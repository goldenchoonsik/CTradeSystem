"""SimFin v3 수집 (재개 가능, 단일 워커). 키는 환경변수 SIMFIN_KEY. 원본 무수정 저장.
회사 단위: 재무 → (재무 데이터가 있으면) 주가. 우선순위: ISIN 보유·ID 내림차순(최근 등록 우선).
한도(429)는 응답 헤더 x-rate-limit-retry-after-seconds 만큼 자고 재개.
"""
import json, os, time
from pathlib import Path
import requests

BASE = "https://backend.simfin.com/api/v3/"
ROOT = Path(__file__).resolve().parents[3] / "DataVault/staging/us_fundamentals_simfin/raw"
H = {"Authorization": f"api-key {os.environ['SIMFIN_KEY']}", "accept": "application/json"}
GAP = float(os.environ.get("SF_GAP", "1.0"))
STM = {"statements": "pl,bs,cf,derived", "period": "q1,q2,q3,q4,fy"}


def get(path, params=None):
    for attempt in range(10):
        r = requests.get(BASE + path, headers=H, params=params, timeout=90)
        if r.status_code == 200:
            return r.content
        if r.status_code == 429:
            wait = int(r.headers.get("x-rate-limit-retry-after-seconds", 60)) + 5
            print(f"429 한도 소진 → {wait}s 대기 ({time.strftime('%H:%M:%S')})", flush=True)
            time.sleep(wait); continue
        if r.status_code in (404, 400):
            return b"[]"
        print(f"HTTP {r.status_code} {path} {r.text[:120]}", flush=True)
        time.sleep(5 * (attempt + 1))
    return None


def main():
    for sub in ("statements", "prices"):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    lst = ROOT / "companies_list.json"
    if not lst.exists():
        lst.write_bytes(get("companies/list"))
    comps = json.loads(lst.read_text())
    comps.sort(key=lambda c: (not c["isin"], -c["id"]))
    n = 0
    for c in comps:
        sp, pp = ROOT / "statements" / f"{c['id']}.json", ROOT / "prices" / f"{c['id']}.json"
        if not sp.exists():
            body = get("companies/statements/compact", {"ticker": c["ticker"], **STM}); time.sleep(GAP)
            if body is None: continue
            sp.write_bytes(body)
        if sp.stat().st_size > 500 and not pp.exists():
            body = get("companies/prices/compact", {"ticker": c["ticker"]}); time.sleep(GAP)
            if body is None: continue
            pp.write_bytes(body)
        n += 1
        if n % 100 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] 처리 회사 {n}", flush=True)


if __name__ == "__main__":
    main()
