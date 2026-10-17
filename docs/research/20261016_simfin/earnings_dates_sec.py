"""SEC EDGAR 에서 실적 발표(8-K Item 2.02) 접수 시각 수집. 산출: derived/earnings_8k.csv (ticker,cik,filed_dt(ET)).
SimFin 회사 중 현재 EDGAR 티커와 매칭되는 곳만(상폐 티커는 매칭 불가 → 한계로 보고)."""
import json, time
from pathlib import Path
import requests, pandas as pd

R = Path(__file__).resolve().parents[3] / "DataVault/staging/us_fundamentals_simfin/raw"
OUT = R.parent / "derived"; OUT.mkdir(exist_ok=True)
UA = {"User-Agent": "CTradeSystem research script"}

def get(url):
    for k in range(5):
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200: return r.json()
        time.sleep(2 * (k + 1))
    return None

comps = {c["id"]: c for c in json.loads((R / "companies_list.json").read_text())}
tick = get("https://www.sec.gov/files/company_tickers.json")
cik_of = {v["ticker"].upper(): int(v["cik_str"]) for v in tick.values()}
ids = [int(f.stem) for f in (R / "statements").glob("*.json") if f.stat().st_size > 500 and (R / "prices" / f.name).exists()]
rows, miss = [], 0
for n, cid in enumerate(ids):
    t = comps[cid]["ticker"].upper()
    cik = cik_of.get(t)
    if cik is None: miss += 1; continue
    sub = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"); time.sleep(0.12)
    if not sub: continue
    blocks = [sub["filings"]["recent"]]
    oldest = min(blocks[0]["filingDate"]) if blocks[0]["filingDate"] else "9999"
    if oldest > "2019-09-01":
        for fl in sub["filings"].get("files", []):
            if fl["filingTo"] >= "2019-09-01":
                b = get("https://data.sec.gov/submissions/" + fl["name"]); time.sleep(0.12)
                if b: blocks.append(b)
    for b in blocks:
        for form, items, acc in zip(b["form"], b["items"], b["acceptanceDateTime"]):
            if form == "8-K" and "2.02" in (items or "").split(","):
                rows.append(dict(id=cid, ticker=t, cik=cik, accepted_utc=acc))
    if n % 50 == 0: print(n, len(ids), len(rows), flush=True)
df = pd.DataFrame(rows)
df["accepted_utc"] = pd.to_datetime(df["accepted_utc"], utc=True)
df["accepted_et"] = df["accepted_utc"].dt.tz_convert("America/New_York")
df.to_csv(OUT / "earnings_8k.csv", index=False)
print(f"회사 {len(ids)}개 중 EDGAR 현재 티커 미매칭 {miss}, 실적 8-K {len(df)}건, 회사 {df['id'].nunique()}개")
