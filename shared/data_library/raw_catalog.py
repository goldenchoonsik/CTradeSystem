"""원천 저장소 목록 — 운용(수집 대상 원장 종목)과 유니버스(연구용 전 종목)를 한 목록·한 이상 집계로 본다.

원천 화면이 '전부 나열 + 종목마다 날짜 칸'이던 방식은 원장 ~30종목에선 됐지만 유니버스(미국 ~1.3만)가 들어오면 감당이 안 된다.
그렇다고 유니버스를 이름이 다른 별도 목록으로 빼면 아무도 안 보게 된다(사용자 지적 2026-10-15). 그래서:
  · 같은 목록·같은 이상 집계에 넣고, 구분(tier)만 '운용'/'유니버스'로 표시한다.
  · 화면은 요약 → 이상만 → 검색(서버 페이지) 순서로 보고, 날짜 칸 상세는 종목을 열 때만 계산한다.

가볍게 만든다: 유니버스 항목은 매니페스트(~1KB)·파일 stat·'확인 필요' 파일 유무만 읽는다(날짜 원장은 수백 KB 라 목록에서 안 읽는다).
항목은 매니페스트 mtime 이 같으면 캐시(DataVault/temp/cache/raw_catalog.json)를 재사용한다.
운용 항목은 기존 원천 건강 매트릭스(RawStoreHealthEngine)의 캐시 결과를 그대로 가져온다(계산을 새로 하지 않는다).

이상(issues):
  conflict       같은 날짜에 기존과 다른 값이 들어와 '확인 필요'로 보류된 곳이 있다
  stale          그 시장 최신 거래일보다 N 영업일 이상 뒤처졌다(상장 종료 종목 제외)
  listing_ended  최신 거래소 목록에서 사라졌다(상폐·티커 변경 추정 — 파일은 보존, 결측 경고 제외)
  no_manifest    매니페스트 없음
  size_mismatch  파일 크기가 매니페스트 기록과 다르다(공통 절차 밖에서 바뀜)
  missing_days   (운용) 결측일이 있다 — 건강 매트릭스 결과
"""
import csv
import glob
import json
import os
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional


from shared.data_library.paths import data_vault_path

STALE_TRADING_DAYS = 5                                  # 확정된 마지막 거래일보다 이만큼(거래일) 뒤처지면 '최신일 지연'
UNIVERSE_DIRS = {"yahoo": ("candle_1d", "US")}        # 원천 폴더 → (하위 폴더, 시장) — 유니버스 원천
_CACHE_PATH = data_vault_path("temp", "cache", "raw_catalog.json")
_LOCK = threading.Lock()
_MEM: Dict[str, Any] = {"at": 0.0, "data": None}
_MEM_TTL = 60


def _latest_listing() -> Dict[str, Dict[str, str]]:
    """가장 최근 nasdaqtrader 목록(원본) → {Yahoo 심볼: {name, exchange, etf, list_day}}."""
    from shared.data_library.us_equity.yahoo import yahoo_symbol
    days = sorted(glob.glob(data_vault_path("raw", "nasdaqtrader", "symdir", "*")))
    if not days:
        return {}
    day = os.path.basename(days[-1])
    out = {}
    for name in ("nasdaqlisted", "otherlisted"):
        fp = os.path.join(days[-1], f"{name}.txt")
        if not os.path.exists(fp):
            continue
        with open(fp, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="|"):
                sym = row.get("Symbol") if name == "nasdaqlisted" else row.get("ACT Symbol")
                if not sym or sym.startswith("File Creation Time") or row.get("Test Issue") != "N":
                    continue
                out.setdefault(yahoo_symbol(sym), {"name": row.get("Security Name", ""), "etf": row.get("ETF", ""),
                                                   "exchange": "NASDAQ" if name == "nasdaqlisted" else row.get("Exchange", ""),
                                                   "list_day": day})
    return out


def _load_cache() -> Dict[str, Any]:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    tmp = f"{_CACHE_PATH}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _CACHE_PATH)


def _universe_entry(csv_path: str, provider: str, market: str) -> Dict[str, Any]:
    mf = csv_path + ".manifest.json"
    sym = os.path.basename(csv_path)[: -len("_1d.csv")]
    e: Dict[str, Any] = {"key": os.path.relpath(csv_path, data_vault_path("raw")), "symbol": sym, "provider": provider.upper(),
                         "market": market, "interval": "1D", "size": os.path.getsize(csv_path)}
    try:
        with open(mf, encoding="utf-8") as f:
            m = json.load(f)
        it = m.get("integrity") or {}
        e.update({"rows": it.get("row_count"), "first": str(it.get("first_timestamp") or "")[:10],
                  "last": str(it.get("last_timestamp") or "")[:10], "manifest_size": it.get("byte_size"), "manifest": True})
    except Exception:
        e.update({"rows": None, "first": "", "last": "", "manifest_size": None, "manifest": False})
    e["blank_bars"] = _tail_blank_bars(csv_path)
    e["conflicts"] = 0
    if os.path.exists(csv_path + ".conflicts.json"):
        try:
            with open(csv_path + ".conflicts.json", encoding="utf-8") as f:
                e["conflicts"] = sum(1 for c in json.load(f).get("items", []) if c.get("status") == "pending")
        except Exception:
            e["conflicts"] = -1
    return e


TAIL_BYTES = 4096                                       # 최근 ~60봉 — 미완성 봉은 늘 끝부분에 생긴다


def _tail_blank_bars(csv_path: str) -> int:
    """끝부분에서 가격 칸이 빈 봉 수(마감 직후 받은 미확정 봉 등)."""
    try:
        with open(csv_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            lines = f.read().decode("utf-8", "ignore").splitlines()[1:]
        return sum(1 for ln in lines if ln[:2] in ("19", "20") and "" in ln.split(",")[1:5])
    except OSError:
        return 0


def _scan_universe(cache: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """유니버스 원천 파일 → 항목(매니페스트 mtime 이 같으면 캐시 재사용)."""
    old = cache.get("universe", {})
    out = {}
    for provider, (sub, market) in UNIVERSE_DIRS.items():
        for p in glob.glob(data_vault_path("raw", provider, sub, "*_1d.csv")):
            key = os.path.relpath(p, data_vault_path("raw"))
            try:
                sig = [os.stat(p + ".manifest.json").st_mtime_ns, os.path.getsize(p), 2,   # 2 = 항목 형식 판(미완성 봉 추가)
                       os.stat(p + ".conflicts.json").st_mtime_ns if os.path.exists(p + ".conflicts.json") else 0]
            except OSError:
                sig = [0, os.path.getsize(p), 2, 0]
            hit = old.get(key)
            out[key] = hit if hit and hit.get("_sig") == sig else {**_universe_entry(p, provider, market), "_sig": sig}
    return out


def _operating_entries() -> List[Dict[str, Any]]:
    """운용 = 수집 대상(source_mappings) 종목. 결측은 건강 매트릭스 디스크 캐시에서 키로 찾기만 한다.
    (RawStoreHealthEngine.get_matrix(cache_only=True) 는 크레온 일자 저장소 메타를 ~50만 번 열어 20초가 걸린다 — 목록에서는 안 부른다.)"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        regs = SourceMappingManager.get_feed_registry_status()
    except Exception:
        return []
    health: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    try:
        with open(data_vault_path("temp", "cache", "raw_store_health_cache.json"), encoding="utf-8") as f:
            for k, v in (json.load(f).get("matrix") or {}).items():
                sym, path, _days = k.rsplit("|", 2) if k.count("|") >= 2 else (k, "", "")
                health[sym].append({"path": path, **{x: v.get(x) for x in ("missing_count", "span_end", "computed_at")}})
    except Exception:
        pass
    out = []
    for x in regs:
        hs = health.get(x["symbol"], [])
        latest_by_path: Dict[str, Dict[str, Any]] = {}
        for h in hs:                                  # 같은 파일의 여러 기간 창 중 가장 최근 계산
            if h["path"] not in latest_by_path or (h.get("computed_at") or "") > (latest_by_path[h["path"]].get("computed_at") or ""):
                latest_by_path[h["path"]] = h
        missing = sum(int(h.get("missing_count") or 0) for h in latest_by_path.values())
        last = max([str(h.get("span_end") or "") for h in latest_by_path.values()] + [str(x.get("last_timestamp") or "")[:10].replace("-", "") and str(x.get("last_timestamp"))[:10]])
        out.append({"key": f"feed:{x['symbol']}", "symbol": x["symbol"], "name": x.get("name", ""), "tier": "운용",
                    "provider": str(x.get("primary_source") or "").upper(), "market": _feed_market(x.get("category", ""), x.get("primary_source", "")),
                    "category": x.get("category", ""), "interval": ",".join(x.get("ingestion_sources") or []),
                    "rows": x.get("rows"), "first": "", "last": last if last != "-" else "",
                    "archived": bool(x.get("archived")), "file_exists": bool(x.get("file_exists")),
                    "missing_days": missing, "health_checked": bool(latest_by_path), "conflicts": 0, "path": x.get("file_path") or ""})
    return out


def _feed_market(category: str, provider: str) -> str:
    c, p = str(category).upper(), str(provider).upper()
    if p in ("UPBIT", "BINANCE", "COINBASE") or "CRYPTO" in c:
        return "CRYPTO"
    if p in ("CREON", "KIS") and not c.startswith("US"):
        return "KRX"
    return "US" if c.startswith("US") or p in ("CME", "YAHOO", "OVERSEAS", "CTRADER") else (c or p)


# 시장별 정규장 마감(현지) — 이 시각이 지나야 그날 일봉이 확정된다
_SESSION_CLOSE = {"US": ("America/New_York", 16, 0)}


def expected_latest(market: str, now: Optional[datetime] = None) -> Optional[str]:
    """그 시장에서 일봉이 확정된 마지막 거래일(거래일 달력 기준). 오늘이 거래일이고 마감이 지났으면 오늘, 아니면 직전 거래일."""
    if market not in _SESSION_CLOSE:
        return None
    from zoneinfo import ZoneInfo
    from shared.core import market_calendar as mc
    tz, hh, mm = _SESSION_CLOSE[market]
    local = (now or datetime.now(ZoneInfo("Asia/Seoul"))).astimezone(ZoneInfo(tz))
    today = local.strftime("%Y-%m-%d")
    if mc.is_trading_day(today, market) and (local.hour, local.minute) >= (hh, mm):
        return today
    return mc.prev_trading_day_strict(today, market)


def trading_days_behind(last: str, latest: str, market: str) -> int:
    """last 다음 거래일부터 latest 까지의 거래일 수(0 = 최신)."""
    from shared.core import market_calendar as mc
    if not last or not latest or last >= latest:
        return 0
    return max(0, mc.count_trading_days(last, latest, market) - 1)


def _market_latest(entries: List[Dict[str, Any]]) -> Optional[str]:
    """유니버스가 실제로 가진 최신일 = 상장 중 종목들의 마지막 날짜 최빈값(갱신 여부 경고용)."""
    c = Counter(e["last"] for e in entries if e.get("last") and e.get("listing") != "ended")
    return c.most_common(1)[0][0] if c else None


def build(force: bool = False) -> Dict[str, Any]:
    """목록 전체(캐시) — 항목 + 시장별 최신일. 60초 메모리 캐시, 디스크 캐시는 매니페스트 mtime 단위 증분."""
    with _LOCK:
        if not force and _MEM["data"] is not None and time.time() - _MEM["at"] < _MEM_TTL:
            return _MEM["data"]
        cache = _load_cache()
        uni = _scan_universe(cache)
        listing = _latest_listing()
        feeds = _operating_entries()
        feed_paths = {os.path.abspath(e["path"]) for e in feeds if e.get("path")}
        us_feed_syms = {e["symbol"].upper() for e in feeds if str(e.get("category", "")).upper().startswith("US")}
        entries = []
        for key, e in uni.items():
            e = {k: v for k, v in e.items()}
            # 같은 파일이거나 미국 주식·ETF 수집 대상의 같은 티커일 때만 운용 — 심볼 문자열만 같으면 안 된다(유니버스 ES=Eversource ≠ 선물 ES)
            full = os.path.abspath(data_vault_path("raw", key))
            e["tier"] = "운용" if (full in feed_paths or e["symbol"].upper() in us_feed_syms) else "유니버스"
            meta = listing.get(e["symbol"])
            e["name"] = (meta or {}).get("name", "")
            e["etf"] = (meta or {}).get("etf", "") == "Y"
            e["listing"] = "listed" if meta else ("ended" if listing else "unknown")
            entries.append(e)
        latest, expected = {}, {}
        by_market = defaultdict(list)
        for e in entries:
            by_market[e["market"]].append(e)
        for mk, es in by_market.items():
            latest[mk] = _market_latest(es)
            expected[mk] = expected_latest(mk) or latest[mk]
        for e in entries:
            e["issues"] = _issues(e, expected.get(e["market"]))
            e.pop("_sig", None)
        for f in feeds:
            f["issues"] = (["missing_days"] if f["missing_days"] and not f["archived"] else []) + \
                          (["no_file"] if not f["file_exists"] and not f["archived"] else [])
        data = {"built_at": datetime.now().isoformat(timespec="seconds"), "market_latest": latest, "market_expected": expected,
                "listing_day": next(iter(listing.values()), {}).get("list_day") if listing else None,
                "items": feeds + entries}
        _save_cache({"universe": uni, "built_at": data["built_at"]})
        _MEM.update({"at": time.time(), "data": data})
        return data


def _issues(e: Dict[str, Any], market_latest: Optional[str]) -> List[str]:
    out = []
    if e.get("conflicts"):
        out.append("conflict")
    if e.get("blank_bars"):
        out.append("incomplete_bar")
    if not e.get("manifest"):
        out.append("no_manifest")
    elif e.get("manifest_size") is not None and e["manifest_size"] != e.get("size"):
        out.append("size_mismatch")
    if e.get("listing") == "ended":
        out.append("listing_ended")
    elif market_latest and e.get("last") and \
            trading_days_behind(e["last"], market_latest, e["market"]) >= STALE_TRADING_DAYS:
        out.append("stale")
    return out


ISSUE_LABELS = {"conflict": "확인 필요", "incomplete_bar": "미완성 봉", "stale": "최신일 지연", "listing_ended": "상장 종료", "no_manifest": "매니페스트 없음",
                "size_mismatch": "크기 불일치", "missing_days": "결측일", "quality_bad": "품질 불량", "no_file": "원천 없음"}
# 상장 종료는 '결함'이 아니라 상태다 — 이상 건수에서 빼고 따로 센다
NOT_ANOMALY = {"listing_ended"}


def summary(force: bool = False) -> Dict[str, Any]:
    d = build(force)
    groups: Dict[tuple, Dict[str, Any]] = {}
    issue_counts = Counter()
    for e in d["items"]:
        g = groups.setdefault((e["market"], e["provider"], e["tier"]),
                              {"market": e["market"], "provider": e["provider"], "tier": e["tier"], "count": 0,
                               "anomaly": 0, "ended": 0, "last_max": ""})
        g["count"] += 1
        real = [i for i in e["issues"] if i not in NOT_ANOMALY]
        g["anomaly"] += bool(real)
        g["ended"] += "listing_ended" in e["issues"]
        g["last_max"] = max(g["last_max"], e.get("last") or "")
        issue_counts.update(e["issues"])
    warnings = []
    for mk, last in (d["market_latest"] or {}).items():
        exp = (d.get("market_expected") or {}).get(mk)
        behind = trading_days_behind(last, exp, mk) if last and exp else 0
        if behind >= 1:
            warnings.append(f"{mk} 유니버스가 확정된 마지막 거래일 {exp}보다 {behind}거래일 뒤처져 있습니다(보유 최신일 {last}) — 갱신 필요")
    return {"built_at": d["built_at"], "market_latest": d["market_latest"], "market_expected": d.get("market_expected"),
            "listing_day": d["listing_day"],
            "total": len(d["items"]), "anomaly_total": sum(g["anomaly"] for g in groups.values()),
            "groups": sorted(groups.values(), key=lambda g: (g["tier"] != "운용", g["market"], g["provider"])),
            "issues": [{"code": k, "label": ISSUE_LABELS.get(k, k), "count": v} for k, v in issue_counts.most_common()],
            "warnings": warnings}


def search(q: str = "", market: str = "", provider: str = "", tier: str = "", issue: str = "",
           only_anomaly: bool = False, page: int = 1, size: int = 50) -> Dict[str, Any]:
    """서버 쪽 거르기·페이지. issue='' 이고 only_anomaly 면 상장 종료를 뺀 이상 항목만."""
    d = build()
    ql = q.strip().lower()
    rows = []
    for e in d["items"]:
        if market and e["market"] != market:
            continue
        if provider and e["provider"] != provider.upper():
            continue
        if tier and e["tier"] != tier:
            continue
        if issue and issue not in e["issues"]:
            continue
        if only_anomaly and not [i for i in e["issues"] if i not in NOT_ANOMALY]:
            continue
        if ql and ql not in e["symbol"].lower() and ql not in (e.get("name") or "").lower():
            continue
        rows.append(e)
    # 이상 있는 것 먼저, 그다음 운용, 심볼 순
    # 검색어가 있으면: 같은 심볼 → 심볼로 시작 → 심볼에 포함 → 이름에만 포함. 그다음 이상 있는 것 → 운용 → 심볼 순
    qu = q.strip().upper()

    def _match_rank(e):
        if not qu:
            return 0
        s = e["symbol"].upper()
        return 0 if s == qu else 1 if s.startswith(qu) else 2 if qu in s else 3
    rows.sort(key=lambda e: (_match_rank(e), not [i for i in e["issues"] if i not in NOT_ANOMALY], e["tier"] != "운용", e["symbol"]))
    size = max(1, min(int(size), 200))
    page = max(1, int(page))
    view = rows[(page - 1) * size: page * size]
    for e in view:
        e["issue_labels"] = [ISSUE_LABELS.get(i, i) for i in e["issues"]]
    return {"total": len(rows), "page": page, "size": size, "items": view}


def detail(key: str, recent: int = 120) -> Dict[str, Any]:
    """유니버스 항목 상세 — 확인 필요 목록, 영구 결손, 기업행위, 최근 N 거래일 중 빠진 날(같은 시장 기준 종목의 날짜와 대조)."""
    from CAiModelBaker.core import raw_day_store as rds
    raw = data_vault_path("raw")
    p = os.path.normpath(os.path.join(raw, key))
    if not p.startswith(raw + os.sep) or not os.path.exists(p):
        return {"error": "원천 파일 없음"}
    e = next((x for x in build()["items"] if x["key"] == key), None) or {}
    out = {"key": key, "entry": e, "conflicts": rds.pending_conflicts(p)[:20], "unrecoverable": rds.unrecoverable_ranges(p)}
    sym = os.path.basename(p)[: -len("_1d.csv")]
    act = data_vault_path("raw", "yahoo", "corporate_actions", f"{sym}.csv")
    if os.path.exists(act):
        with open(act, encoding="utf-8") as f:
            acts = list(csv.DictReader(f))
        out["actions"] = {"splits": [a for a in acts if a["type"] == "split"][-10:],
                          "dividends": len([a for a in acts if a["type"] == "dividend"]),
                          "last_dividend": next((a for a in reversed(acts) if a["type"] == "dividend"), None)}
    # 거래일 달력(대형주 다수결로 만든 미국 달력) 기준 최근 N 거래일 중 빠진 날
    if e.get("market") in _SESSION_CLOSE:
        from datetime import timedelta
        from shared.core import market_calendar as mc
        end = expected_latest(e["market"])
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=int(recent * 1.6))).strftime("%Y-%m-%d")
        cal = mc.get_trading_days(start, end, e["market"])[-recent:]
        mine = set(_dates(p))
        first, last = e.get("first") or "", e.get("last") or ""
        out["recent_missing"] = [d for d in cal if first <= d <= last and d not in mine]
        out["recent_after_last"] = len([d for d in cal if d > last])
        out["recent_window"] = [cal[0], cal[-1]] if cal else []
    return out


def _dates(csv_path: str) -> List[str]:
    with open(csv_path, encoding="utf-8") as f:
        next(f, None)
        return [line[:10] for line in f if line.strip()]
