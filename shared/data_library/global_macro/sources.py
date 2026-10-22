"""원천별 수집 → raw/ 원본 + series/<key>.csv 정리본. 전부 멱등(다시 돌리면 최신으로 덮음, 월·주 거시는 받은 날짜별 판을 남김).

  fred         fredgraph.csv (키 불필요)
  yahoo        chart API 일봉 수정 종가
  ecos         한국은행 ECOS StatisticSearch (ECOS_KEY)
  creon_index  kr_equity 원천의 Creon 지수 일봉(수집은 kr_equity 'creon_index' 단계) + 그 뒤 날짜는 Yahoo 로 이어 붙임
"""
import csv
import io
import json
import os
import re
import urllib.parse
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from shared.data_library.paths import data_vault_path

from . import catalog, store
from .store import log

Rows = List[Tuple[str, float]]
MIN_OVERLAP = 250          # Creon 지수에 Yahoo 를 이어 붙이려면 겹치는 날이 이만큼
MAX_SPLICE_DIFF = 0.02     # 그리고 겹친 구간 최대 상대 차이가 이 이하


# ---------------------------------------------------------------- 파서 (네트워크 없이 시험 가능)
def parse_fred_csv(text: str) -> Rows:
    out = []
    rd = csv.reader(io.StringIO(text))
    head = next(rd, None)
    if not head or "date" not in head[0].lower():
        raise ValueError(f"FRED CSV 아님: {text[:80]!r}")
    for r in rd:
        if len(r) < 2 or r[1] in ("", "."):
            continue
        out.append((r[0], float(r[1])))
    return out


def parse_yahoo_chart(raw: bytes) -> Rows:
    j = json.loads(raw)
    res = (j.get("chart") or {}).get("result")
    if not res:
        raise ValueError(f"Yahoo 응답 이상: {str(j)[:200]}")
    r = res[0]
    ts = r.get("timestamp") or []
    q = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
    off = r["meta"].get("gmtoffset", 0) or 0
    out = []
    for i, t in enumerate(ts):
        v = adj[i] if adj[i] is not None else q["close"][i]
        if v is None:
            continue
        out.append((datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d"), float(v)))
    return out


def parse_ecos(raw: bytes) -> Rows:
    j = json.loads(raw)
    rows = (j.get("StatisticSearch") or {}).get("row")
    if not rows:
        raise ValueError(f"ECOS 응답 이상: {str(j)[:200]}")
    out = []
    for r in rows:
        v = r.get("DATA_VALUE")
        if v in ("", None):
            continue
        t = r["TIME"]
        dt = f"{t[:4]}-{t[4:6]}-{t[6:8]}" if len(t) == 8 else f"{t[:4]}-{t[4:6]}-01"
        out.append((dt, float(v)))
    return out


def parse_multpl(html: str) -> Rows:
    """multpl.com 월별 표(<td>Oct 1, 2026</td><td>&#x2002; 40.81</td>) → (YYYY-MM-DD, 값)."""
    out = []
    for d, v in re.findall(r"<td>\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s*</td>\s*<td>\s*(?:&#x2002;)?\s*([-\d.]+)\s*</td>", html):
        out.append((datetime.strptime(d, "%b %d, %Y").strftime("%Y-%m-%d"), float(v)))
    if not out:
        raise ValueError("multpl 표 없음")
    return sorted(out)


def month_start_to_end(rows: Rows) -> Rows:
    """월 자료의 날짜를 그 달 말일로(FRED·ECOS 는 월초 표기) — 발표 시차 계산의 기준점."""
    import calendar
    out = []
    for dt, v in rows:
        y, m = int(dt[:4]), int(dt[5:7])
        out.append((f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}", v))
    return out


# ---------------------------------------------------------------- 수집
def _vintage(s: dict) -> bool:
    return s["freq"] in ("M", "W")


def fetch_fred(s: dict) -> Rows:
    raw = store.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={s['sid']}")
    rows = parse_fred_csv(raw.decode("utf-8", errors="replace"))
    rel = f"fred/{s['key']}/{date.today():%Y%m%d}.csv" if _vintage(s) else f"fred/{s['key']}.csv"
    store.save_raw(rel, raw)
    return month_start_to_end(rows) if s["freq"] == "M" else rows


def fetch_yahoo_sym(sym: str, key: str) -> Rows:
    p2 = int(datetime.now(timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
           f"?period1=-2208988800&period2={p2}&interval=1d&events=div%2Csplit")
    raw = store.get(url)
    rows = parse_yahoo_chart(raw)
    store.save_raw(f"yahoo/{key}.json", raw)
    return rows


def fetch_ecos(s: dict) -> Rows:
    stat, cyc, *items = s["sid"].split("/")
    k = store.key_env("ECOS_KEY")
    start, end = ("19600101", f"{date.today():%Y%m%d}") if cyc == "D" else ("196001", f"{date.today():%Y%m}")
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{k}/json/kr/1/100000/{stat}/{cyc}/{start}/{end}/" + "/".join(items)
    raw = store.get(url)
    rows = parse_ecos(raw)
    rel = f"ecos/{s['key']}/{date.today():%Y%m%d}.json" if _vintage(s) else f"ecos/{s['key']}.json"
    store.save_raw(rel, raw)
    return month_start_to_end(rows) if cyc == "M" else rows


def fetch_multpl(s: dict) -> Rows:
    """CAPE 등 multpl 월 표. 월의 첫날 값(그 달 평균) + 오늘자 최신 값이 섞여 있음 → 월 기준으로 바꿔 같은 달은 월초 값만 남긴다."""
    raw = store.get(f"https://www.multpl.com/{s['sid']}/table/by-month")
    rows = parse_multpl(raw.decode("utf-8", errors="replace"))
    store.save_raw(f"multpl/{s['key']}/{date.today():%Y%m%d}.html", raw)
    first = {}
    for d, v in rows:
        ym = d[:7]
        if ym not in first or d < first[ym][0]:
            first[ym] = (d, v)
    return month_start_to_end([(f"{ym}-01", v) for ym, (d, v) in sorted(first.items())])


def read_creon_index(code: str) -> Rows:
    fp = data_vault_path("staging", "kr_equity", "raw", "creon", "index_1d", f"{code}_1d.csv")
    out = []
    with open(fp, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = r["date"]
            out.append((f"{d[:4]}-{d[4:6]}-{d[6:8]}", float(r["close"])))
    return out


def fetch_creon_index(s: dict) -> Tuple[Rows, Optional[dict]]:
    """Creon 지수 + (있으면) Yahoo 로 Creon 마지막 날 이후를 이어 붙임. 겹치는 구간 차이를 품질 정보로 남긴다."""
    rows = read_creon_index(s["sid"])
    ext = s.get("extend_with")
    if not ext:
        return rows, None
    y = fetch_yahoo_sym(ext, s["key"] + "_ext")
    last = rows[-1][0]
    cre = dict(rows)
    overlap = [(d, v, cre[d]) for d, v in y if d in cre and d >= "2015-01-01"]
    diff = max((abs(v / c - 1) for _, v, c in overlap), default=None)
    info = {"creon_last": last, "overlap_days": len(overlap), "max_rel_diff": diff}
    # 겹치는 날이 충분하고 값이 같은 지수일 때만 잇는다(검증 안 되는 이어 붙이기 금지)
    if len(overlap) >= MIN_OVERLAP and diff is not None and diff <= MAX_SPLICE_DIFF:
        rows = rows + [(d, v) for d, v in y if d > last]
        info["extended_to"] = rows[-1][0]
    else:
        info["extended_to"] = None
        info["skipped"] = f"겹침 {len(overlap)}일·최대 차이 {diff} — 잇지 않음"
    return rows, info


def fetch_one(s: dict) -> dict:
    info = None
    if s["source"] == "fred":
        rows = fetch_fred(s)
    elif s["source"] == "yahoo":
        rows = fetch_yahoo_sym(s["sid"], s["key"])
    elif s["source"] == "ecos":
        rows = fetch_ecos(s)
    elif s["source"] == "multpl":
        rows = fetch_multpl(s)
    elif s["source"] == "creon_index":
        rows, info = fetch_creon_index(s)
    else:
        raise ValueError(f"알 수 없는 원천 {s['source']}")
    if not rows:
        raise RuntimeError("행 없음")
    store.write_series(s["key"], rows)
    return {"key": s["key"], "rows": len(rows), "first": rows[0][0], "last": rows[-1][0], "extend": info}


def fetch_all(keys: Optional[List[str]] = None) -> bool:
    """전부(또는 keys) 받기. 하나가 실패해도 나머지는 계속, 결과는 quality/fetch_status.json."""
    errs = catalog.validate()
    if errs:
        raise RuntimeError("원장 규약 오류: " + "; ".join(errs))
    status: Dict[str, dict] = store.read_json(store.path("quality", "fetch_status.json")) or {}
    ok = True
    for s in catalog.SERIES:
        if keys and s["key"] not in keys:
            continue
        try:
            r = fetch_one(s)
            r.update(ok=True, fetched_at=datetime.now().isoformat(timespec="seconds"))
            log(f"[fetch] {s['key']:14s} {r['first']} ~ {r['last']} ({r['rows']}행)")
        except Exception as e:   # noqa: BLE001
            ok = False
            r = dict(status.get(s["key"], {}), ok=False, error=str(e)[:300], failed_at=datetime.now().isoformat(timespec="seconds"))
            log(f"[fetch] {s['key']:14s} 실패: {e}")
        status[s["key"]] = r
    os.makedirs(store.path("quality"), exist_ok=True)
    with open(store.path("quality", "fetch_status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
    return ok
