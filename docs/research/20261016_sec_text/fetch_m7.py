"""M7 의 10-K·10-Q 본문(primary document) 원본 수집 — 무수정 저장(0o444), 이미 있으면 건너뜀(이어받기). SEC 공개 API, 초당 약 6회."""
import json, os, re, sys, time, requests
OUT = "/home/kuzimo/projects/CTradeSystem/DataVault/staging/us_filings_sec"
UA = {"User-Agent": os.environ.get("SEC_USER_AGENT", "CTradeSystem research script")}  # SEC 공정 접근: 이름+연락처 필요
M7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"]
FORMS = {"10-K", "10-Q"}
SINCE = "2016-01-01"
pool = json.load(open("/home/kuzimo/projects/CTradeSystem/DataVault/staging/us_fundamentals_sec/reference/pool_cik.json"))
last = [0.0]
def get(url, **kw):
    for a in range(5):
        w = 0.16 - (time.time() - last[0])
        if w > 0: time.sleep(w)
        last[0] = time.time()
        try:
            r = requests.get(url, headers=UA, timeout=60, **kw)
            if r.status_code == 200: return r
            print("  status", r.status_code, url[-60:], flush=True)
        except Exception as e:
            print("  재시도", e, flush=True)
        time.sleep(2 * (a + 1))
    return None
def sealed_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f: f.write(data)
    os.chmod(path, 0o444)
tot = 0; rows = []
for t in M7:
    cik = pool[t][0]
    sub = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
    sp = f"{OUT}/reference/submissions_{t}.json"
    if not os.path.exists(sp): sealed_write(sp, json.dumps(sub).encode())
    blocks = [sub["filings"]["recent"]]
    for f in sub["filings"].get("files", []):
        if f["filingTo"] >= SINCE: blocks.append(get("https://data.sec.gov/submissions/" + f["name"]).json())
    n = 0
    for b in blocks:
        for i, form in enumerate(b["form"]):
            if form not in FORMS or b["filingDate"][i] < SINCE: continue
            acc, doc = b["accessionNumber"][i], b["primaryDocument"][i]
            fn = f"{b['filingDate'][i]}_{form}_{acc}_{os.path.basename(doc)}"
            fp = f"{OUT}/raw/{t}/{fn}"
            rows.append(dict(ticker=t, form=form, filed=b["filingDate"][i], report_date=b["reportDate"][i], accession=acc, file=fn))
            if os.path.exists(fp): continue
            r = get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}")
            if r is None: print("실패", t, fn); continue
            sealed_write(fp, r.content); tot += len(r.content); n += 1
    print(t, "새로 받음", n, "건, 누계 %.0fMB" % (tot / 1e6), flush=True)
json.dump(rows, open(f"{OUT}/reference/index.json", "w"), ensure_ascii=False, indent=0)
print("완료", len(rows), "건 색인")
