"""2009-12 이후 나스닥100 구성 종목(Yahoo 가격 있는 것)의 SEC companyfacts 원본 수집 (2026-10-21)
저장: DataVault/staging/us_fundamentals_sec/raw/companyfacts/{현재티커}.json (무수정, 있으면 건너뜀). 초당 6회 이하.
"""
import os, sys, json, time, requests
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from ndx_membership import members_at
from ndx_common import RENAME, ROOT
OUT = f"{ROOT}/staging/us_fundamentals_sec"
UA = {"User-Agent": os.environ.get("SEC_USER_AGENT", "CTradeSystem research script")}  # SEC 공정 접근: 이름+연락처 필요
need = set()
for q in pd.date_range('2009-12-31', '2026-09-30', freq='QE'):
    for t in members_at(q):
        y = RENAME.get(t, t).replace('.', '-')
        if os.path.exists(f"{ROOT}/raw/yahoo/candle_1d/{y}_1d.csv"):
            need.add(y)
tick = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=30).json()
cik = {v["ticker"].replace('.', '-'): int(v["cik_str"]) for v in tick.values()}
json.dump({t: cik.get(t) for t in sorted(need)}, open(f"{OUT}/reference/ndx_cik.json", "w"), indent=1)
print("대상", len(need), "EDGAR 티커 없음", sorted(t for t in need if t not in cik))
for t in sorted(need):
    if t not in cik: continue
    fp = f"{OUT}/raw/companyfacts/{t}.json"
    if os.path.exists(fp): continue
    r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[t]:010d}.json", headers=UA, timeout=60)
    time.sleep(0.17)
    if r.status_code != 200: print("실패", t, r.status_code); continue
    open(fp, "wb").write(r.content); print("ok", t, len(r.content) // 1000, "KB", flush=True)
