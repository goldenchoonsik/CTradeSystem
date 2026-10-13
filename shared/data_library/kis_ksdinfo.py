"""KIS OpenAPI '예탁원정보'(원 출처: 한국예탁결제원) 원본 축적 — 기업행위 원장 2층의 원천.

왜 쌓는가: 공시는 정정·철회·연기되고 API 는 최신 값만 준다 → '그날 무엇을 알았는가'는 그날 받아 둔 원본으로만 재현된다.
예정 일정(병합·증자 공시)은 지나가면 '예정이었다'는 사실이 사라진다.

저장: DataVault/raw/kis/ksdinfo/<kind>/<YYYYMMDDTHHMMSS>.json — 응답 원본(페이지별 본문·연속 헤더) + 조회 조건 + 시각.
  파일은 한 번 쓰면 잠근다(0o444). 같은 날 여러 번 받아도 덮어쓰지 않고 새 파일.
인증: CTrade 가 캐시한 토큰만 쓴다(새 토큰 발급 금지 — 같은 앱 키 재발급 시 Vultr 라이브 세션 토큰이 무효화됨).
  토큰이 20시간 넘게 오래됐으면 받지 않는다(scripts/fetch_kis_token.sh 로 동기화 후 재시도).
속도: 호출 간 0.5초 — 같은 앱 키를 쓰는 CTrade 의 호출 한도를 남겨 둔다.
연속 조회(실측 2026-10-11): tr_cont=N 만으로는 같은 페이지가 반복된다. CTS = 마지막 행 record_date + sht_cd 를 넣어야 다음 페이지.
"""
import json
import os
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from shared.data_library.paths import data_vault_path

APIS: Dict[str, dict] = {
    "dividend": {"path": "/uapi/domestic-stock/v1/ksdinfo/dividend", "tr_id": "HHKDB669102C0",
                 "params": {"GB1": "0", "HIGH_GB": ""}},
    "rev_split": {"path": "/uapi/domestic-stock/v1/ksdinfo/rev-split", "tr_id": "HHKDB669105C0",
                  "params": {"MARKET_GB": "0"}},
    "bonus_issue": {"path": "/uapi/domestic-stock/v1/ksdinfo/bonus-issue", "tr_id": "HHKDB669101C0", "params": {}},
    "paidin_capin": {"path": "/uapi/domestic-stock/v1/ksdinfo/paidin-capin", "tr_id": "HHKDB669100C0",
                     "params": {"GB1": "1"}},
    "cap_decrease": {"path": "/uapi/domestic-stock/v1/ksdinfo/cap-dcrs", "tr_id": "HHKDB669106C0", "params": {}},
    "merger_split": {"path": "/uapi/domestic-stock/v1/ksdinfo/merger-split", "tr_id": "HHKDB669104C0", "params": {}},
}
TOKEN_MAX_AGE = timedelta(hours=20)
CALL_INTERVAL = 0.5
MAX_PAGES = 400


class KisUnavailable(RuntimeError):
    pass


def store_root() -> str:
    return data_vault_path("raw", "kis", "ksdinfo")


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_credentials(token_file: Optional[str] = None, env: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    """캐시 토큰 + 앱 키. 토큰이 없거나 오래됐으면 KisUnavailable (발급하지 않는다)."""
    env = env if env is not None else os.environ
    token_file = token_file or os.path.join(_repo_root(), "CTrade", "config", "token_info.json")
    now = now or datetime.now()
    if not os.path.exists(token_file):
        raise KisUnavailable("KIS 토큰 캐시 없음 — scripts/fetch_kis_token.sh 로 동기화 필요(새 토큰은 발급하지 않음)")
    age = now - datetime.fromtimestamp(os.path.getmtime(token_file))
    if age > TOKEN_MAX_AGE:
        raise KisUnavailable(f"KIS 토큰 캐시가 {age} 지남 — scripts/fetch_kis_token.sh 로 동기화 필요")
    with open(token_file, encoding="utf-8") as f:
        token = json.load(f).get("access_token")
    key, secret = (env.get("KIS_APP_KEY") or "").strip("'\" "), (env.get("KIS_APP_SECRET") or "").strip("'\" ")
    url = (env.get("KIS_URL_BASE") or env.get("URL_BASE") or "https://openapi.koreainvestment.com:9443").strip("'\" ")
    if not token or not key or not secret:
        raise KisUnavailable("KIS 앱 키/시크릿(환경변수 KIS_APP_KEY/KIS_APP_SECRET) 또는 토큰 없음")
    return {"token": token, "app_key": key, "app_secret": secret, "url": url}


def fetch(kind: str, f_dt: str, t_dt: str, cred: dict, http_get: Optional[Callable] = None,
          sleep: Callable[[float], None] = time.sleep, symbol: str = "") -> dict:
    """한 조회 조건의 모든 페이지. 반환: {kind, query, pages:[{tr_cont, body}], rows, complete, error}."""
    if http_get is None:
        import requests
        http_get = requests.get
    api = APIS[kind]
    pages, rows, seen = [], [], set()
    cts, cont, complete, error = "", "", False, None
    for _ in range(MAX_PAGES):
        headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {cred['token']}",
                   "appkey": cred["app_key"], "appsecret": cred["app_secret"], "tr_id": api["tr_id"], "custtype": "P",
                   "tr_cont": cont}
        params = {"SHT_CD": symbol, "F_DT": f_dt, "T_DT": t_dt, "CTS": cts, **api["params"]}
        r = http_get(cred["url"] + api["path"], headers=headers, params=params, timeout=20)
        sleep(CALL_INTERVAL)
        body = r.json()
        tr_cont = r.headers.get("tr_cont", "")
        pages.append({"tr_cont": tr_cont, "cts": cts, "body": body})
        if body.get("rt_cd") != "0":
            error = f"{body.get('msg_cd')} {body.get('msg1')}"
            break
        out = body.get("output1") if isinstance(body.get("output1"), list) else (body.get("output") or [])
        fresh = [x for x in out if json.dumps(x, sort_keys=True, ensure_ascii=False) not in seen]
        for x in fresh:
            seen.add(json.dumps(x, sort_keys=True, ensure_ascii=False))
        rows.extend(fresh)
        if tr_cont not in ("F", "M") or not out:
            complete = True
            break
        if not fresh:
            error = "연속 조회가 같은 행을 반복 — CTS 형식 확인 필요"
            break
        last = out[-1]
        cts, cont = f"{last.get('record_date', '')}{last.get('sht_cd', '')}", "N"
    else:
        error = f"페이지 상한 {MAX_PAGES} 도달"
    return {"kind": kind, "query": {"SHT_CD": symbol, "F_DT": f_dt, "T_DT": t_dt, **api["params"]},
            "pages": pages, "rows": rows, "complete": complete and error is None, "error": error}


def save_snapshot(result: dict, root: Optional[str] = None, fetched_at: Optional[datetime] = None) -> str:
    """응답 원본을 새 파일로 저장하고 잠근다(덮어쓰기 없음)."""
    root = root or store_root()
    fetched_at = fetched_at or datetime.now()
    d = os.path.join(root, result["kind"])
    os.makedirs(d, exist_ok=True)
    stem = fetched_at.strftime("%Y%m%dT%H%M%S")
    path = os.path.join(d, f"{stem}_{result['query']['F_DT']}_{result['query']['T_DT']}.json")
    n = 1
    while os.path.exists(path):
        path = os.path.join(d, f"{stem}_{result['query']['F_DT']}_{result['query']['T_DT']}_{n}.json")
        n += 1
    doc = {"source": "KIS OpenAPI 예탁원정보 (한국예탁결제원)", "fetched_at": fetched_at.isoformat(timespec="seconds"),
           **{k: result[k] for k in ("kind", "query", "complete", "error")}, "row_count": len(result["rows"]),
           "pages": result["pages"]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, path)
    if os.name != "nt":
        os.chmod(path, 0o444)
    return path


def _windows(start: datetime, end: datetime, months: int) -> List[tuple]:
    out, cur = [], start
    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32 * months)).replace(day=1)
        out.append((cur.strftime("%Y%m%d"), min(nxt - timedelta(days=1), end).strftime("%Y%m%d")))
        cur = nxt
    return out


def sync(kinds=None, past_days: int = 90, future_days: int = 180, root: Optional[str] = None, log=print,
         cred: Optional[dict] = None, http_get=None, sleep=time.sleep, now: Optional[datetime] = None) -> List[dict]:
    """일일 스냅샷: 전 종목, 과거 past_days ~ 미래 future_days (정정·예정 일정 포착)."""
    now = now or datetime.now()
    return backfill(now - timedelta(days=past_days), now + timedelta(days=future_days), kinds=kinds, months=3,
                    root=root, log=log, cred=cred, http_get=http_get, sleep=sleep)


def completed_windows(kind: str, root: Optional[str] = None) -> set:
    """이미 완전하게 받은 (F_DT, T_DT) — 과거 이력 이어받기용."""
    d = os.path.join(root or store_root(), kind)
    done = set()
    if not os.path.isdir(d):
        return done
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                doc = json.load(f)
            if doc.get("complete"):
                done.add((doc["query"]["F_DT"], doc["query"]["T_DT"]))
        except Exception:
            continue
    return done


def backfill(start: datetime, end: datetime, kinds=None, months: int = 1, root: Optional[str] = None, log=print,
             cred: Optional[dict] = None, http_get=None, sleep=time.sleep, skip_done: bool = False) -> List[dict]:
    """기간을 months 개월 창으로 나눠 종류별 전 종목 조회 → 창마다 원본 스냅샷 1개.
    skip_done: 같은 창을 이미 완전하게 받았으면 건너뛴다(과거 이력 이어받기; 일일 스냅샷은 매번 새로 받는다)."""
    cred = cred or load_credentials()
    out = []
    for kind in (kinds or list(APIS)):
        done = completed_windows(kind, root) if skip_done else set()
        for f_dt, t_dt in _windows(start, end, months):
            if (f_dt, t_dt) in done:
                continue
            res = fetch(kind, f_dt, t_dt, cred, http_get=http_get, sleep=sleep)
            path = save_snapshot(res, root)
            out.append({"kind": kind, "f_dt": f_dt, "t_dt": t_dt, "rows": len(res["rows"]), "pages": len(res["pages"]),
                        "complete": res["complete"], "error": res["error"], "path": path})
            if res["error"]:
                log(f"[KSD] ⚠️ {kind} {f_dt}~{t_dt}: {res['error']}")
        log(f"[KSD] {kind}: 창 {sum(1 for o in out if o['kind'] == kind)}, 행 {sum(o['rows'] for o in out if o['kind'] == kind)}")
    return out
