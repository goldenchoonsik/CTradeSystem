"""KIS 프로그램매매 종합현황(일별) 수집 — TR FHPPG04600001 (코스피 K). 차익/비차익/전체 × 위탁/자기 × 매도/매수 거래대금(백만원)·수량. 투자자 구분 없음.
앱 키는 stdin: ssh root@서버 "grep ... .env" | python fetch_kis_program_daily.py [시작YYYYMMDD]"""
import sys, json, time, os, csv, datetime as dt, requests
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT = os.path.join(ROOT, "DataVault", "staging", "kr_investor_daily")
kv = {}
for l in sys.stdin:
    k, _, v = l.strip().partition("="); kv[k] = v.strip("'\" ")
tok = json.load(open(os.path.join(ROOT, "CTrade", "config", "token_info.json")))["access_token"]
h = {"authorization": "Bearer " + tok, "appkey": kv["KIS_APP_KEY"], "appsecret": kv["KIS_APP_SECRET"], "tr_id": "FHPPG04600001", "custtype": "P"}
URL = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/comp-program-trade-daily"
start = dt.datetime.strptime(sys.argv[1] if len(sys.argv) > 1 else "19990101", "%Y%m%d"); end = dt.datetime(2026, 10, 23); rows = {}; empty = 0
cur = end
while cur >= start:
    a = max(start, cur - dt.timedelta(days=89))
    p = {"FID_COND_MRKT_DIV_CODE": "J", "FID_MRKT_CLS_CODE": "K", "FID_SCTN_CLS_CODE": "", "FID_INPUT_ISCD": "", "FID_COND_MRKT_DIV_CODE1": "", "FID_INPUT_DATE_1": a.strftime("%Y%m%d"), "FID_INPUT_DATE_2": cur.strftime("%Y%m%d")}
    j = requests.get(URL, headers=h, params=p, timeout=30).json()
    if j.get("rt_cd") != "0": print("오류", a.date(), j.get("msg_cd"), j.get("msg1")); break
    o = j.get("output") or []
    n0 = len(rows)
    for x in o: rows[x["stck_bsop_date"]] = x
    print(a.date(), "~", cur.date(), len(o), "행, 누적", len(rows), flush=True)
    empty = empty + 1 if not o else 0
    if empty >= 8: break
    cur = a - dt.timedelta(days=1); time.sleep(0.5)
ks = sorted(rows)
if ks:
    with open(os.path.join(OUT, "KSP_program_daily.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[ks[0]].keys())); w.writeheader(); [w.writerow(rows[k]) for k in ks]
print("총", len(ks), "일", ks[0] if ks else "", "~", ks[-1] if ks else "")
