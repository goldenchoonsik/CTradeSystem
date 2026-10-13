"""재무·업종 원본 수집 — KIS 재무제표(분기·연간)·종목 기본정보 (원장 밖 staging).

응답 원본 그대로(0o444, 있으면 건너뜀 → 이어받기). 캐시 토큰만 쓴다(새 토큰 발급 금지). bash -i 로 실행.
조회 1회에 그 종목의 전체 이력(분기 약 30개, 연간 약 20년)이 온다.

  bash -ic ".venv/bin/python docs/research/20261012_limit_up/collect_fin.py"
"""
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from shared.data_library.kis_ksdinfo import KisUnavailable, load_credentials  # noqa: E402

STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
ROOT = os.path.join(STAGE, "raw", "kis", "finance")
LOG = os.path.join(ROOT, "collect_log.txt")
MIN_GAP = 1 / 10
FIN = "/uapi/domestic-stock/v1/finance/"
APIS = {   # 이름: (경로, tr_id, 파라미터(code))
    "balance_q": (FIN + "balance-sheet", "FHKST66430100", lambda c: {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": c}),
    "income_q": (FIN + "income-statement", "FHKST66430200", lambda c: {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": c}),
    "income_y": (FIN + "income-statement", "FHKST66430200", lambda c: {"FID_DIV_CLS_CODE": "0", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": c}),
    "ratio_q": (FIN + "financial-ratio", "FHKST66430300", lambda c: {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": c}),
    "info": ("/uapi/domestic-stock/v1/quotations/search-stock-info", "CTPF1002R", lambda c: {"PRDT_TYPE_CD": "300", "PDNO": c}),
}
_last = [0.0]


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def call(cred, name, code):
    path, tr, params = APIS[name]
    headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {cred['token']}",
               "appkey": cred["app_key"], "appsecret": cred["app_secret"], "tr_id": tr, "custtype": "P"}
    for attempt in range(6):
        wait = MIN_GAP - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            j = requests.get(cred["url"] + path, headers=headers, params=params(code), timeout=20).json()
        except Exception as e:
            log(f"  재시도 {name} {code}: {e}")
            time.sleep(2 * (attempt + 1))
            continue
        if j.get("rt_cd") == "0":
            return j
        if j.get("msg_cd") == "EGW00201":
            time.sleep(1)
            continue
        if j.get("msg_cd") in ("EGW00123", "EGW00121"):
            raise KisUnavailable(f"토큰 문제: {j.get('msg1')}")
        log(f"  오류 {name} {code}: {j.get('msg_cd')} {j.get('msg1')}")
        return {"_error": j}
    return None


def main():
    os.makedirs(ROOT, exist_ok=True)
    cred = load_credentials()
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv"))
    codes = [c[1:] for c in uni.code]
    log(f"시작: {len(codes)} 종목 × {len(APIS)} 조회")
    for i, code in enumerate(codes):
        for name in APIS:
            fp = os.path.join(ROOT, name, f"{code}.json")
            if os.path.exists(fp):
                continue
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            j = call(cred, name, code)
            if j is None:
                log(f"실패 {name} {code}")
                continue
            rec = {"source": "KIS OpenAPI", "tr_id": APIS[name][1], "api": name, "code": code,
                   "fetched_at": datetime.now().isoformat(timespec="seconds"), "response": j}
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            os.chmod(tmp, 0o444)
            os.replace(tmp, fp)
        if i % 200 == 0:
            log(f"{i+1}/{len(codes)}")
    log("완료")


if __name__ == "__main__":
    main()
