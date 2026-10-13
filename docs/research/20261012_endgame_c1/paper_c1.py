"""C1 모의 체결 기록기 — 서버 cron 용, CTrade 와 독립(표준 라이브러리 + requests).

주문은 내지 않는다. KIS REST 로 최근월 KOSPI200 선물 현재가·호가를 조회해 원본을 남기고, 15:47 에 정산한다.
정의는 PREREGISTRATION.md 그대로.

  python paper_c1.py snap t1530|t1532|t1547
  python paper_c1.py settle
  python paper_c1.py report

환경: C1_HOME(기록 폴더, 기본 이 파일 폴더), C1_REPO(키·토큰이 있는 저장소, 기본 /root/projects/CTradeSystem).
키는 C1_REPO/.env, 토큰은 C1_REPO/CTrade/config/token_info.json (CTrade 가 발급한 것을 읽기만 한다).
"""
import csv
import io
import json
import math
import os
import re
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HOME = os.environ.get("C1_HOME", os.path.dirname(os.path.abspath(__file__)))
REPO = os.environ.get("C1_REPO", "/root/projects/CTradeSystem")
SNAP_DIR = os.path.join(HOME, "snapshots")
LEDGER = os.path.join(HOME, "ledger.csv")
MASTER_URL = "https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip"
KOSPI200_BASE = "2001"
THRESH, FEE, BT_TICKS_PT = 0.01, 0.00003, 0.1
# 특수 거래일(장 시간이 다른 날) — 매년 갱신. 수능일은 1시간 늦게 열고 닫는다.
SPECIAL_DAYS = {"20261119"}
FIELDS = ["date", "code", "front_last_tr_date", "prev_close", "p1530", "r", "signal", "excluded",
          "p1532", "ask1_1532", "bid1_1532", "close", "entry_fill", "shortfall_pt", "paper_ret", "bt_ret", "note"]


def now():
    return datetime.now(KST)


def load_env():
    env = {}
    with open(os.path.join(REPO, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    with open(os.path.join(REPO, "CTrade", "config", "token_info.json"), encoding="utf-8") as f:
        token = json.load(f)["access_token"]
    url = env.get("KIS_URL_BASE") or "https://openapi.koreainvestment.com:9443"
    return {"token": token, "key": env["KIS_APP_KEY"], "secret": env["KIS_APP_SECRET"], "url": url}


def kis_get(cred, path, tr_id, params):
    headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {cred['token']}",
               "appkey": cred["key"], "appsecret": cred["secret"], "tr_id": tr_id, "custtype": "P"}
    r = requests.get(cred["url"] + path, headers=headers, params=params, timeout=10)
    return r.json()


def second_thursday(y, m):
    first = date(y, m, 1)
    return first + timedelta(days=(3 - first.weekday()) % 7 + 7)


def front_code(today: str):
    """최근월 지수선물 단축코드 (CTrade/utils/kis_futures_master.parse_front_months 와 같은 규칙, 휴장 보정 없음).
    하루 한 번 내려받아 캐시한다."""
    cache = os.path.join(HOME, "front_cache.json")
    if os.path.exists(cache):
        c = json.load(open(cache))
        if c.get("day") == today:
            return c["code"]
    resp = requests.get(MASTER_URL, timeout=15)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        text = zf.read(next(n for n in zf.namelist() if n.endswith(".mst"))).decode("cp949", errors="replace")
    t = datetime.strptime(today, "%Y%m%d").date()
    rolled = t >= second_thursday(t.year, t.month)
    best = None
    for line in text.splitlines():
        f = line.split("|")
        if len(f) < 8 or f[0].strip() != "1" or f[7].strip() != KOSPI200_BASE:
            continue
        m = re.search(r"\bF\s+(\d{6})\b", f[3])
        if not m or m.group(1) < today[:6] or (m.group(1) == today[:6] and rolled):
            continue
        seq = int(f[6]) if f[6].strip().isdigit() else 99
        if best is None or seq < best[0]:
            best = (seq, f[1].strip())
    code = best[1] if best else None
    json.dump({"day": today, "code": code}, open(cache, "w"))
    return code


def snap(label):
    t = now()
    today = t.strftime("%Y%m%d")
    cred = load_env()
    code = front_code(today)
    rec = {"label": label, "ts": t.isoformat(timespec="milliseconds"), "code": code}
    p = {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code}
    rec["price"] = kis_get(cred, "/uapi/domestic-futureoption/v1/quotations/inquire-price", "FHMIF10000000", p)
    rec["asking"] = kis_get(cred, "/uapi/domestic-futureoption/v1/quotations/inquire-asking-price", "FHMIF10010000", p)
    rec["ts_done"] = now().isoformat(timespec="milliseconds")
    os.makedirs(SNAP_DIR, exist_ok=True)
    with open(os.path.join(SNAP_DIR, f"{today}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"{rec['ts']} {label} {code} prpr={rec['price'].get('output1', {}).get('futs_prpr')} rt={rec['price'].get('rt_cd')}")


def read_ledger():
    if not os.path.exists(LEDGER):
        return []
    with open(LEDGER, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def settle():
    today = now().strftime("%Y%m%d")
    fp = os.path.join(SNAP_DIR, f"{today}.jsonl")
    snaps = {}
    if os.path.exists(fp):
        for line in open(fp, encoding="utf-8"):
            r = json.loads(line)
            snaps[r["label"]] = r
    rows = read_ledger()
    if any(r["date"] == today for r in rows):
        print("이미 정산됨")
        return
    row = {k: "" for k in FIELDS}
    row["date"] = today
    if not all(k in snaps and snaps[k]["price"].get("rt_cd") == "0" for k in ("t1530", "t1532", "t1547")):
        row["excluded"], row["note"] = "missing", "스냅샷 누락/오류: " + ",".join(sorted(snaps))
        _append(row)
        return
    o = {k: snaps[k]["price"]["output1"] for k in ("t1530", "t1532", "t1547")}
    if float(o["t1547"].get("acml_vol") or 0) <= 0:
        print("휴장일 — 기록 없음")
        return
    codes = {snaps[k]["code"] for k in ("t1530", "t1532", "t1547")}
    row["code"] = snaps["t1547"]["code"]
    row["front_last_tr_date"] = o["t1547"].get("futs_last_tr_date", "")
    prev = rows[-1] if rows else None
    t = datetime.strptime(today, "%Y%m%d").date()
    if prev and prev.get("front_last_tr_date") == today or (not prev and t == second_thursday(t.year, t.month)):
        row["excluded"] = "expiry"
    elif prev and prev.get("excluded") == "expiry":
        row["excluded"] = "after_expiry"
    elif today in SPECIAL_DAYS:
        row["excluded"] = "special_session"
    elif len(codes) != 1:
        row["excluded"] = "code_changed"
    prev_close, p1530 = float(o["t1530"]["futs_prdy_clpr"]), float(o["t1530"]["futs_prpr"])
    r = p1530 / prev_close - 1
    sig = int(math.copysign(1, r)) if abs(r) > THRESH else 0
    a = snaps["t1532"]["asking"].get("output2") or {}
    p1532, close = float(o["t1532"]["futs_prpr"]), float(o["t1547"]["futs_prpr"])
    ask1, bid1 = float(a.get("futs_askp1") or 0), float(a.get("futs_bidp1") or 0)
    row.update(prev_close=prev_close, p1530=p1530, r=round(r, 6), signal=sig, p1532=p1532, ask1_1532=ask1, bid1_1532=bid1, close=close)
    if sig and not row["excluded"]:
        fill = ask1 if sig > 0 else bid1
        if fill <= 0:
            row["note"] = "호가 없음 — 현재가로 대체"
            fill = p1532
        row.update(entry_fill=fill, shortfall_pt=round(sig * (fill - p1532), 2),
                   paper_ret=round(sig * (close / fill - 1) - 2 * FEE, 6),
                   bt_ret=round(sig * (close / p1532 - 1) - (BT_TICKS_PT / p1532 + 2 * FEE), 6))
    _append(row)


def _append(row):
    new = not os.path.exists(LEDGER)
    with open(LEDGER, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    print("정산:", {k: row[k] for k in ("date", "r", "signal", "excluded", "shortfall_pt", "paper_ret", "bt_ret")})


def report():
    rows = [r for r in read_ledger() if r["paper_ret"]]
    days = read_ledger()
    print(f"기록일 {len(days)}일, 신호(제외일 빼고) {len(rows)}건 — 판정은 40건부터 (PREREGISTRATION.md)")
    if not rows:
        return
    sf = [float(r["shortfall_pt"]) for r in rows]
    pr = [float(r["paper_ret"]) for r in rows]
    bt = [float(r["bt_ret"]) for r in rows]
    mean = lambda x: sum(x) / len(x)
    print(f"진입 체결 손실 평균 {mean(sf):+.3f}pt (가정 0.05pt) | 모의 거래당 {mean(pr)*100:+.3f}% 합 {sum(pr)*100:+.2f}% | 백테스트식 {mean(bt)*100:+.3f}%")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "snap":
        snap(sys.argv[2])
    elif cmd == "settle":
        settle()
    else:
        report()
