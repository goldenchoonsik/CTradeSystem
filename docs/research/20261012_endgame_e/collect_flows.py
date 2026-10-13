"""E 항목 수급 원본 수집 — KIS 종목별 투자자·프로그램매매 일별 (원장 밖 staging).

응답 원본을 그대로 저장(0o444, 이미 있으면 건너뜀 → 이어받기). 30행/호출, 마지막 날짜 하루 전으로 계속.
캐시 토큰만 쓴다(새 토큰 발급 금지). 셸 환경변수 KIS_APP_KEY 가 필요하므로 bash -i 로 실행.

  bash -ic ".venv/bin/python docs/research/20261012_endgame_e/collect_flows.py"
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from shared.data_library.kis_ksdinfo import KisUnavailable, load_credentials  # noqa: E402

STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
ROOT = os.path.join(STAGE, "raw", "kis", "flows")
LOG = os.path.join(ROOT, "collect_log.txt")
START, END = "20240801", "20261008"
MIN_GAP = 1 / 10                                   # 초당 10회 이하 (같은 앱 키 실전 호출 여유)
KINDS = {
    "investor": ("/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily", "FHPTJ04160001",
                 lambda code, dt: {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": dt,
                                   "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": ""}, "output2"),
    "program": ("/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily", "FHPPG04650201",
                lambda code, dt: {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": dt}, "output"),
}
_last = [0.0]


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def call(cred, kind, code, dt):
    path, tr, params, _ = KINDS[kind]
    headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {cred['token']}",
               "appkey": cred["app_key"], "appsecret": cred["app_secret"], "tr_id": tr, "custtype": "P"}
    for attempt in range(6):
        wait = MIN_GAP - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = requests.get(cred["url"] + path, headers=headers, params=params(code, dt), timeout=20)
            j = r.json()
        except Exception as e:                      # 네트워크 일시 오류
            log(f"  재시도 {kind} {code} {dt}: {e}")
            time.sleep(2 * (attempt + 1))
            continue
        if j.get("rt_cd") == "0":
            return j
        if j.get("msg_cd") == "EGW00201":           # 초당 거래건수 초과
            time.sleep(1)
            continue
        if "토큰" in str(j.get("msg1", "")) or j.get("msg_cd") in ("EGW00123", "EGW00121"):
            raise KisUnavailable(f"토큰 문제: {j.get('msg1')}")
        log(f"  오류 {kind} {code} {dt}: {j.get('msg_cd')} {j.get('msg1')}")
        time.sleep(1)
    return None


def collect_one(cred, kind, code):
    out_dir = os.path.join(ROOT, kind, code)
    os.makedirs(out_dir, exist_ok=True)
    dt = END
    while dt >= START:
        fp = os.path.join(out_dir, f"{dt}.json")
        if os.path.exists(fp):
            j = json.load(open(fp, encoding="utf-8"))
        else:
            j = call(cred, kind, code, dt)
            if j is None:
                return False
            rec = {"source": "KIS OpenAPI", "tr_id": KINDS[kind][1], "kind": kind, "code": code, "query_date": dt,
                   "fetched_at": datetime.now().isoformat(timespec="seconds"), "response": j}
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            os.chmod(tmp, 0o444)
            os.replace(tmp, fp)
            j = rec
        rows = j["response"].get(KINDS[kind][3]) or []
        dates = sorted(x["stck_bsop_date"] for x in rows if x.get("stck_bsop_date"))
        if not dates:
            break                                    # 상장 전
        nxt = (datetime.strptime(dates[0], "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        if nxt >= dt:
            break
        dt = nxt
    return True


def main():
    os.makedirs(ROOT, exist_ok=True)
    cred = load_credentials()
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv"))
    uni = uni[(uni.is_preferred == 0) & (uni.is_spac == 0)]
    codes = [c[1:] for c in uni.code]
    done_file = os.path.join(ROOT, "done.txt")
    done = set(open(done_file).read().split()) if os.path.exists(done_file) else set()
    log(f"시작: {len(codes)} 종목, 완료 {len(done)}")
    t0 = time.time()
    for i, code in enumerate(codes):
        for kind in KINDS:
            key = f"{kind}:{code}"
            if key in done:
                continue
            ok = collect_one(cred, kind, code)
            if ok:
                with open(done_file, "a") as f:
                    f.write(key + "\n")
            else:
                log(f"실패 {key}")
        if i % 50 == 0:
            el = time.time() - t0
            log(f"{i+1}/{len(codes)} 종목, 경과 {el/60:.1f}분")
    log("완료")


if __name__ == "__main__":
    main()
