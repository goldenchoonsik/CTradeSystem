"""나스닥 대형주 후보군의 SEC EDGAR companyfacts 원본 수집 (무수정 저장, 이미 있으면 건너뜀). 공개 API, 초당 8회 이하."""
import json, os, sys, time, requests
ROOT = "/home/kuzimo/projects/CTradeSystem/DataVault"
OUT = f"{ROOT}/staging/us_fundamentals_sec"
UA = {"User-Agent": os.environ.get("SEC_USER_AGENT", "CTradeSystem research script")}  # SEC 공정 접근: 이름+연락처 필요
POOL = """AAPL MSFT AMZN GOOGL META NVDA TSLA AVGO COST PEP ADBE CSCO INTC NFLX CMCSA AMGN QCOM TXN AMD GILD SBUX MDLZ INTU BKNG ISRG AMAT
REGN VRTX MU LRCX ADP PYPL TMUS HON MRNA ABNB PANW SNPS CDNS KLAC MAR CHTR MELI BIIB ORLY CSX CTAS ADI WBA EBAY ILMN LULU DXCM KDP MNST
CRWD WDAY ROST FTNT PCAR CSGP TTWO AZN CTSH ATVI XLNX CELG YHOO""".split()
have = {t for t in POOL if os.path.exists(f"{ROOT}/raw/yahoo/candle_1d/{t}_1d.csv")}
print("Yahoo 가격 있음", len(have), "없음", sorted(set(POOL) - have))
tick = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=30).json()
cik = {v["ticker"]: (int(v["cik_str"]), v["title"]) for v in tick.values()}
json.dump({t: cik.get(t) for t in sorted(have)}, open(f"{OUT}/reference/pool_cik.json", "w"), ensure_ascii=False, indent=1)
miss = [t for t in have if t not in cik]; print("EDGAR 현재 티커 없음", miss)
for t in sorted(have):
    if t not in cik: continue
    fp = f"{OUT}/raw/companyfacts/{t}.json"
    if os.path.exists(fp): continue
    r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[t][0]:010d}.json", headers=UA, timeout=60)
    time.sleep(0.15)
    if r.status_code != 200: print("실패", t, r.status_code); continue
    open(fp, "wb").write(r.content); print("ok", t, len(r.content) // 1000, "KB", flush=True)
