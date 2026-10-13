"""KIS 예탁원 정보(기업행위 2층 후보) 조회 가능성 확인 — 읽기 전용, 캐시 토큰만 사용(새 토큰 발급 금지: CTrade 라이브 세션 보호).

  .venv/bin/python docs/research/20261011_vol_spike/kis_ksd_probe.py
"""
import json
import os
import sys
import time

import requests
from dotenv import dotenv_values

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TOKEN_FILE = os.path.join(REPO, "CTrade", "config", "token_info.json")
ENV = {**dotenv_values(os.path.join(REPO, "CTrade", ".env")),
       **{k: v.strip("'\"").strip() for k, v in os.environ.items() if k.startswith(("KIS_", "URL_BASE"))}}   # CTrade 와 같은 환경변수
URL = ENV.get("KIS_URL_BASE") or ENV.get("URL_BASE") or "https://openapi.koreainvestment.com:9443"

# KIS 공개 예제 기준 엔드포인트·TR (응답으로 확인)
APIS = {
    "dividend": ("/uapi/domestic-stock/v1/ksdinfo/dividend", "HHKDB669102C0", {"CTS": "", "GB1": "0", "HIGH_GB": ""}),
    "rev_split": ("/uapi/domestic-stock/v1/ksdinfo/rev-split", "HHKDB669105C0", {"CTS": "", "MARKET_GB": "0"}),
    "cap_decrease": ("/uapi/domestic-stock/v1/ksdinfo/cap-dcrs", "HHKDB669106C0", {"CTS": ""}),
    "bonus_issue": ("/uapi/domestic-stock/v1/ksdinfo/bonus-issue", "HHKDB669101C0", {"CTS": ""}),
    "paidin_capin": ("/uapi/domestic-stock/v1/ksdinfo/paidin-capin", "HHKDB669100C0", {"CTS": "", "GB1": "1"}),
    "merger_split": ("/uapi/domestic-stock/v1/ksdinfo/merger-split", "HHKDB669104C0", {"CTS": ""}),
}


def call(api, code, f_dt, t_dt):
    path, tr, extra = APIS[api]
    tok = json.load(open(TOKEN_FILE))["access_token"]
    h = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {tok}", "appkey": ENV["KIS_APP_KEY"],
         "appsecret": ENV["KIS_APP_SECRET"], "tr_id": tr, "custtype": "P"}
    params = {"SHT_CD": code, "F_DT": f_dt, "T_DT": t_dt, **extra}
    r = requests.get(URL + path, headers=h, params=params, timeout=15)
    try:
        j = r.json()
    except Exception:
        return {"http": r.status_code, "text": r.text[:200]}
    return {"http": r.status_code, "rt_cd": j.get("rt_cd"), "msg": j.get("msg1"),
            "rows": j.get("output1") if isinstance(j.get("output1"), list) else j.get("output")}


if __name__ == "__main__":
    cases = [("rev_split", "000040"), ("cap_decrease", "900340"), ("dividend", "005930"), ("dividend", "036810"),
             ("bonus_issue", "036810"), ("paidin_capin", "000040"), ("merger_split", "000040")]
    if len(sys.argv) > 2:
        cases = [(sys.argv[1], sys.argv[2])]
    for api, code in cases:
        res = call(api, code, "20240801", "20261011")
        rows = res.get("rows") or []
        print(f"== {api} {code}: HTTP {res.get('http')} rt_cd={res.get('rt_cd')} msg={res.get('msg')} rows={len(rows) if isinstance(rows, list) else type(rows).__name__}")
        for r in (rows if isinstance(rows, list) else [rows])[:4]:
            print("   ", json.dumps(r, ensure_ascii=False)[:400])
        time.sleep(0.6)
