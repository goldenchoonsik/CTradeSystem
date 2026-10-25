"""KIS 시장별 투자자 매매동향(일별) 수집 — TR FHPTJ04040000. 주체별 '순매수'(수량·거래대금 백만원)만 제공(총 매수·매도액 없음).
앱 키는 stdin 으로 받는다(파일에 쓰지 않음): ssh root@서버 "grep ... .env" | python fetch_kis_investor_daily.py KSP 0001
응답 원본은 DataVault/staging/kr_investor_daily/raw/ 에 저장(0o444)."""
import sys, json, time, os, requests
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT = os.path.join(ROOT, "DataVault", "staging", "kr_investor_daily", "raw")
mkt, iscd = sys.argv[1], sys.argv[2]
kv = {}
for l in sys.stdin:
    k, _, v = l.strip().partition("="); kv[k] = v.strip("'\" ")
tok = json.load(open(os.path.join(ROOT, "CTrade", "config", "token_info.json")))["access_token"]
h = {"authorization": "Bearer " + tok, "appkey": kv["KIS_APP_KEY"], "appsecret": kv["KIS_APP_SECRET"], "tr_id": "FHPTJ04040000", "custtype": "P"}
URL = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market"
end, start = sys.argv[3] if len(sys.argv) > 3 else "20261023", sys.argv[4] if len(sys.argv) > 4 else "20130101"
seen, rows, n = set(), [], 0
while end >= start and n < 60:
    p = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": iscd, "FID_INPUT_DATE_1": end, "FID_INPUT_ISCD_1": mkt, "FID_INPUT_DATE_2": start, "FID_INPUT_ISCD_2": iscd}
    r = requests.get(URL, headers=h, params=p, timeout=30); j = r.json(); n += 1
    if j.get("rt_cd") != "0": print("오류", j.get("msg_cd"), j.get("msg1")); break
    o = j.get("output") or []
    if not o: break
    path = os.path.join(OUT, f"{mkt}_{iscd}_{end}_{start}_{n:02d}.json")
    json.dump({"params": p, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S"), "output": o}, open(path, "w"), ensure_ascii=False)
    os.chmod(path, 0o444)
    new = [x for x in o if x["stck_bsop_date"] not in seen]
    for x in new: seen.add(x["stck_bsop_date"]); rows.append(x)
    oldest = min(x["stck_bsop_date"] for x in o)
    print(f"호출{n}: {len(o)}행 {max(x['stck_bsop_date'] for x in o)}~{oldest} 신규 {len(new)}")
    if not new or oldest <= start: break
    end = (__import__("datetime").datetime.strptime(oldest, "%Y%m%d") - __import__("datetime").timedelta(days=1)).strftime("%Y%m%d")
    time.sleep(0.6)
import csv
rows.sort(key=lambda x: x["stck_bsop_date"])
if rows:
    with open(os.path.join(OUT, "..", f"{mkt}_{iscd}_daily.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("총", len(rows), "일", rows[0]["stck_bsop_date"] if rows else "", "~", rows[-1]["stck_bsop_date"] if rows else "")
