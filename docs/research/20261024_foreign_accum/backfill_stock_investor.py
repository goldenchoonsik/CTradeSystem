"""종목별 일별 투자자(외국인·개인·기관 세부) 순매수 이력 수집 — KIS FHPTJ04160001, 30거래일/호출, 날짜를 거슬러 올라가며 수집. 수량·종가·거래대금 포함.
앱 키는 stdin: ssh ... | python backfill_stock_investor.py A005930,A000660 20250101   (저장: DataVault/staging/kr_investor_daily/stock_daily/<code>.csv, 증분 병합)"""
import sys, os, json, time, datetime as dt, requests, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")); ST = f"{ROOT}/DataVault/staging/kr_investor_daily/stock_daily"
kv = {}
for l in sys.stdin:
    k, _, v = l.strip().partition("="); kv[k] = v.strip("'\" ")
tok = json.load(open(f"{ROOT}/CTrade/config/token_info.json"))["access_token"]; codes = sys.argv[1].split(","); start = sys.argv[2]
def call(code, d):
    h = {"authorization": "Bearer " + tok, "appkey": kv["KIS_APP_KEY"], "appsecret": kv["KIS_APP_SECRET"], "tr_id": "FHPTJ04160001", "custtype": "P"}
    time.sleep(0.13); return requests.get("https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily", headers=h, params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code[1:], "FID_INPUT_DATE_1": d, "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": ""}, timeout=30).json()
for code in codes:
    rows, d, calls = {}, (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d"), 0
    while d >= start and calls < 200:
        for tr in range(4):
            j = call(code, d); calls += 1
            if j.get("rt_cd") == "0": break
            time.sleep(1.5 * (tr + 1))
        if j.get("rt_cd") != "0": print(code, "오류", j.get("msg1")); break
        o = j.get("output2") or []
        if not o: break
        for x in o: rows[x["stck_bsop_date"]] = x
        old = min(x["stck_bsop_date"] for x in o)
        if old <= start: break
        d = (dt.datetime.strptime(old, "%Y%m%d") - dt.timedelta(days=1)).strftime("%Y%m%d")
    if not rows: print(code, "자료 없음"); continue
    df = pd.DataFrame(rows.values()).sort_values("stck_bsop_date"); df = df[df.stck_bsop_date >= start]; p = f"{ST}/{code}.csv"
    if os.path.exists(p): df = pd.concat([pd.read_csv(p, dtype=str), df.astype(str)]).drop_duplicates("stck_bsop_date", keep="last").sort_values("stck_bsop_date")
    df.to_csv(p, index=False); print(code, len(df), "일", df.stck_bsop_date.min(), "~", df.stck_bsop_date.max(), f"({calls}호출)")
