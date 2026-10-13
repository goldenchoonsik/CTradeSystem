"""덜 쓰이는 데이터 원본 수집 (원장 밖 staging, 응답 원본 그대로 0o444, 있으면 건너뜀).

  kis  : KIS 신용잔고·공매도·대차 일별 (캐시 토큰, 초당 10회)
  dart : DART 공시 목록 (유가 Y·코스닥 K, 월 단위 × 100건 페이지)

  bash -ic ".venv/bin/python docs/research/20261012_limit_up/collect_alt.py kis"
  bash -ic ".venv/bin/python docs/research/20261012_limit_up/collect_alt.py dart"
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
START, END = "20240801", "20261008"
Q = "/uapi/domestic-stock/v1/quotations/"
_last = [0.0]


def log(root, msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(os.path.join(root, "collect_log.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def save(fp, rec):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.chmod(tmp, 0o444)
    os.replace(tmp, fp)


def kis_get(cred, path, tr, params, root, gap=0.1):
    headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {cred['token']}",
               "appkey": cred["app_key"], "appsecret": cred["app_secret"], "tr_id": tr, "custtype": "P"}
    for attempt in range(6):
        w = gap - (time.time() - _last[0])
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()
        try:
            j = requests.get(cred["url"] + path, headers=headers, params=params, timeout=20).json()
        except Exception as e:
            log(root, f"  재시도 {tr} {params}: {e}")
            time.sleep(2 * (attempt + 1))
            continue
        if j.get("rt_cd") == "0":
            return j
        if j.get("msg_cd") == "EGW00201":
            time.sleep(1)
            continue
        if j.get("msg_cd") in ("EGW00123", "EGW00121"):
            raise KisUnavailable(f"토큰 문제: {j.get('msg1')}")
        log(root, f"  오류 {tr} {params}: {j.get('msg_cd')} {j.get('msg1')}")
        return None
    return None


def windows(days=100):
    s, e = datetime.strptime(START, "%Y%m%d"), datetime.strptime(END, "%Y%m%d")
    out = []
    while s <= e:
        t = min(s + timedelta(days=days - 1), e)
        out.append((s.strftime("%Y%m%d"), t.strftime("%Y%m%d")))
        s = t + timedelta(days=1)
    return out


def kis_main():
    root = os.path.join(STAGE, "raw", "kis", "alt")
    os.makedirs(root, exist_ok=True)
    cred = load_credentials()
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv"))
    codes = [c[1:] for c in uni.code]
    wins = windows()
    log(root, f"시작: {len(codes)} 종목, 공매도·대차 {len(wins)} 창, 신용 30행 역순")
    for i, code in enumerate(codes):
        for a, b in wins:
            fp = os.path.join(root, "short", code, f"{a}_{b}.json")
            if not os.path.exists(fp):
                p = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": a, "FID_INPUT_DATE_2": b}
                j = kis_get(cred, Q + "daily-short-sale", "FHPST04830000", p, root)
                if j is not None:
                    save(fp, {"source": "KIS FHPST04830000", "code": code, "query": p, "fetched_at": datetime.now().isoformat(timespec="seconds"), "response": j})
            fp = os.path.join(root, "loan", code, f"{a}_{b}.json")
            if not os.path.exists(fp):
                pages, cts = [], ""
                for _ in range(5):
                    # 시장 구분 3 = 종목별 (1·2 는 코스피·코스닥 전체 합계 — 종목 코드를 무시한다)
                    p = {"MRKT_DIV_CLS_CODE": "3", "MKSC_SHRN_ISCD": code, "START_DATE": a, "END_DATE": b, "CTS": cts}
                    j = kis_get(cred, Q + "daily-loan-trans", "HHPST074500C0", p, root)
                    if j is None:
                        break
                    pages.append(j)
                    cts = (j.get("ctx_area_nk100") or j.get("CTS") or "").strip() if isinstance(j, dict) else ""
                    if not cts or len(j.get("output1") or []) < 100:
                        break
                if pages:
                    save(fp, {"source": "KIS HHPST074500C0", "code": code, "window": [a, b], "fetched_at": datetime.now().isoformat(timespec="seconds"), "pages": pages})
        dt = END
        while dt >= START:
            fp = os.path.join(root, "credit", code, f"{dt}.json")
            if os.path.exists(fp):
                j = json.load(open(fp, encoding="utf-8"))["response"]
            else:
                p = {"fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20476", "fid_input_iscd": code, "fid_input_date_1": dt}
                j = kis_get(cred, Q + "daily-credit-balance", "FHPST04760000", p, root)
                if j is None:
                    break
                save(fp, {"source": "KIS FHPST04760000", "code": code, "query": p, "fetched_at": datetime.now().isoformat(timespec="seconds"), "response": j})
            ds = sorted(x["deal_date"] for x in (j.get("output") or []) if x.get("deal_date"))
            if not ds:
                break
            nxt = (datetime.strptime(ds[0], "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            if nxt >= dt:
                break
            dt = nxt
        if i % 100 == 0:
            log(root, f"{i+1}/{len(codes)}")
    log(root, "완료")


def dart_main():
    root = os.path.join(STAGE, "raw", "dart", "list")
    os.makedirs(root, exist_ok=True)
    key = os.environ["OPEN_DART_KEY"]
    months = pd.date_range("2024-07-01", "2026-10-08", freq="MS")
    log(root, f"시작: {len(months)} 개월 × 유가/코스닥")
    calls, last = 0, 0.0
    for m0 in months:
        m1 = min(m0 + pd.offsets.MonthEnd(0), pd.Timestamp(END))
        for cls in ("Y", "K"):
            page, total = 1, None
            while total is None or page <= total:
                fp = os.path.join(root, f"{m0:%Y%m}_{cls}_{page:03d}.json")
                if os.path.exists(fp):
                    total = json.load(open(fp, encoding="utf-8"))["response"].get("total_page", 0)
                    page += 1
                    continue
                w = 0.25 - (time.time() - last)
                if w > 0:
                    time.sleep(w)
                last = time.time()
                p = {"crtfc_key": key, "bgn_de": f"{m0:%Y%m%d}", "end_de": f"{m1:%Y%m%d}", "corp_cls": cls, "page_no": page, "page_count": 100}
                try:
                    j = requests.get("https://opendart.fss.or.kr/api/list.json", params=p, timeout=30).json()
                except Exception as e:
                    log(root, f"  재시도 {m0:%Y%m} {cls} {page}: {e}")
                    time.sleep(3)
                    continue
                calls += 1
                if j.get("status") == "020":
                    log(root, f"한도 초과 — 중단 (호출 {calls})")
                    return
                if j.get("status") not in ("000", "013"):
                    log(root, f"  오류 {m0:%Y%m} {cls} {page}: {j.get('status')} {j.get('message')}")
                    break
                p.pop("crtfc_key")
                save(fp, {"source": "DART OpenAPI list", "query": p, "fetched_at": datetime.now().isoformat(timespec="seconds"), "response": j})
                total = j.get("total_page", 0)
                page += 1
        log(root, f"{m0:%Y-%m} (호출 {calls})")
    log(root, f"완료 (호출 {calls})")


if __name__ == "__main__":
    {"kis": kis_main, "dart": dart_main}[sys.argv[1]]()
