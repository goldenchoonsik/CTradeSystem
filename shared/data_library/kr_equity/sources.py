"""kr_equity 수집 단계(Linux) — 전부 증분·멱등: 받은 건 건너뛰고, 모자란 것만 채운다.

  kind          KIND 상장폐지 목록(2014~오늘) → reference/delisted.csv → universe.csv 재생성
  naver         유니버스 상폐 종목 중 일봉이 없는 것(네이버 차트, 수정주가)
  dart_corpcode DART 회사코드 zip 새로 받기(신규 상장사 반영)
  dart_fnltt    DART 주요계정 2015~올해 × 분기·반기·연간 (최근 2개년의 '자료 없음'은 다시 조회)
  dart_events   DART 주요사항 7종 — 마지막 조회일 다음 날부터 오늘까지 창 하나 추가(회사당 1회)
  dart_list     DART 공시 목록 2014-01~지난달 (없는 달·페이지만)
  dart_shares   DART 주식총수현황 — 상폐 종목(상장 종목 주식수는 Creon daily_ext)
"""
import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from . import store
from .store import Dart, DartQuota, Job, get, log, path, write_once

FROM_DATE = "2014-01-01"
EVENT_APIS = [("tsstkAqDecsn", "자기주식 취득 결정"), ("tsstkDpDecsn", "자기주식 처분 결정"), ("piicDecsn", "유상증자 결정"),
              ("cvbdIsDecsn", "전환사채 발행 결정"), ("bdwtIsDecsn", "신주인수권부사채 발행 결정"),
              ("fricDecsn", "무상증자 결정"), ("crDecsn", "감자 결정")]
EVENTS_MIN_GAP_DAYS = 7          # 마지막 창 이후 이만큼 지나야 새 창을 연다

# 단계 목록(UI·CLI 공용). runner: linux = 이 모듈, creon = Windows universe_driver.py
STEPS: List[dict] = [
    {"key": "kind", "title": "상폐 목록·유니버스", "runner": "linux", "desc": "KIND 상장폐지 목록을 오늘 기준으로 새로 받고 universe.csv 를 다시 만든다"},
    {"key": "naver", "title": "상폐 종목 일봉", "runner": "linux", "desc": "유니버스의 상폐 종목 중 아직 없는 일봉(네이버, 수정주가)"},
    {"key": "dart_corpcode", "title": "DART 회사코드", "runner": "linux", "desc": "회사코드 zip 갱신(신규 상장사 반영) — DART 키 필요"},
    {"key": "dart_fnltt", "title": "DART 주요계정", "runner": "linux", "desc": "2015~올해 분기·반기·연간, 100개사 묶음; 최근 2개년 '자료 없음'은 재조회"},
    {"key": "dart_events", "title": "DART 주요사항 7종", "runner": "linux", "desc": "자사주·증자·CB·BW·무상·감자 — 마지막 조회일 이후 창 추가"},
    {"key": "dart_list", "title": "DART 공시 목록", "runner": "linux", "desc": "2014-01~지난달, 유가·코스닥, 없는 달만"},
    {"key": "dart_shares", "title": "DART 주식총수(상폐)", "runner": "linux", "desc": "상폐 종목 발행주식수(보고서별) — 시총 근사용"},
    {"key": "creon_universe", "title": "Creon 상장 스냅샷", "runner": "creon", "desc": "현재 상장 보통주 목록 → reference/listed_snapshot.csv (다음에 kind 실행)"},
    {"key": "creon_daily", "title": "Creon 수정 일봉", "runner": "creon", "desc": "상장 종목 수정 일봉 증분(--full: 기업행위 반영 위해 전체 재수집) → 끝나면 원천 적재(기업행위 원장)까지 자동"},
    {"key": "creon_daily_unadj", "title": "Creon 무수정 일봉", "runner": "creon", "desc": "상장 종목 무수정 일봉 증분 → 끝나면 원천 적재(raw/creon/candle_1d_unadj)까지 자동"},
    {"key": "creon_daily_ext", "title": "Creon 주식수·시총·외국인·기관", "runner": "creon", "desc": "일별 보조 지표 증분(--full: 전체 재수집)"},
    {"key": "creon_index", "title": "Creon 지수 일봉", "runner": "creon", "desc": "KOSPI·KOSDAQ·업종·K200 계열 128개 증분"},
    {"key": "creon_bronze", "title": "Creon 일봉 원천 적재", "runner": "linux", "desc": "착륙 구역의 무수정 일봉 → raw/creon/candle_1d_unadj, 기업행위 원장 갱신 (Creon 일봉 단계가 자동으로 부른다 — 수동 재실행용)"},
    {"key": "gold", "title": "골드 지표 표", "runner": "linux", "desc": "패널(실버) 위 종목×일자 지표 + 미래수익 라벨 → derived/gold_1d·gold_labels_1d.parquet (분석 패널 빌드 뒤 실행)"},
    {"key": "panel", "title": "분석 패널 빌드", "runner": "linux", "desc": "derived/*.parquet 재생성 + quality/panel_quality.json (수집 후 실행)"},
]
LINUX_STEPS = {s["key"] for s in STEPS if s["runner"] == "linux"}


# ---------------------------------------------------------------- KIND
_ROW = re.compile(r"<tr[^>]*>\s*<td class=\"first txc\"[^>]*>(\d+)</td>\s*<td>(.*?)</td>\s*<td class=\"txc\">([\d-]+)</td>\s*<td>(.*?)</td>", re.S)


def parse_kind(html: str) -> List[dict]:
    out = []
    for no, comp, dt, why in _ROW.findall(html):
        code = re.search(r"companysummary_open\('(\w+)'\)", comp)
        name = re.search(r"title='([^']+)'", comp)
        mk = re.search(r"alt='(유가증권|코스닥|코넥스)'", comp)
        out.append({"no": int(no), "kind_code": code.group(1) if code else "", "name": (name.group(1) if name else "").strip(),
                    "market": store.MARKETS.get(mk.group(1), "") if mk else "", "delist_date": dt,
                    "reason": re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", why)).strip()})
    return out


def _kind_summary_code(kind_code: str) -> Optional[str]:
    """KIND 회사개요의 '종목코드' — 해외 상장사(회사코드 CHN01 류)는 이걸로만 6자리를 안다. 원본 raw/kind/company/<회사코드>.html"""
    fp = path("raw", "kind", "company", f"{kind_code}.html")
    if not os.path.exists(fp):
        r = get("https://kind.krx.co.kr/common/companysummary.do", method="post", data={
            "method": "searchCompanySummaryOvrvwDetail", "menuIndex": 0, "strIsurCd": kind_code, "lstCd": "undefined",
            "taskDiv": 1, "spotIsuTrdMktTpCd": "", "methodType": 0})
        if r is None:
            return None
        write_once(fp, r.content)
        time.sleep(0.5)
    with open(fp, "rb") as f:
        text = re.sub(r"<[^>]+>", " ", f.read().decode("utf-8", "ignore"))
    m = re.search(r"종목코드\s+(\d{6})", text)
    return m.group(1) if m else None


def run_kind() -> bool:
    stamp = datetime.now().strftime("%Y%m%d")
    to_date = datetime.now().strftime("%Y-%m-%d")
    job = Job("kind_delisted", "KIND 상장폐지 목록", None, "페이지")
    rows, page = [], 1
    while True:
        fp = path("raw", "kind", "delisted", f"{stamp}_{FROM_DATE}_{to_date}_p{page:03d}.html")
        if os.path.exists(fp):
            with open(fp, "rb") as f:
                html = f.read().decode("utf-8", "ignore")
            job.tick(skipped=True)
        else:
            r = get("https://kind.krx.co.kr/investwarn/delcompany.do", method="post", data={
                "method": "searchDelCompanySub", "currentPageSize": 100, "pageIndex": page, "orderMode": 2, "orderStat": "D",
                "forward": "delcompany_sub", "marketType": "", "fromDate": FROM_DATE, "toDate": to_date})
            if r is None:
                job.tick(failed=f"p{page}")
                job.stop("KIND 요청 실패")
                return False
            html = r.content.decode("utf-8", "ignore")
            write_once(fp, r.content)
            job.tick(fetched=True)
            time.sleep(0.5)
        got = parse_kind(html)
        if not got:
            break
        rows += got
        if min(r["no"] for r in got) <= 1:          # KIND 는 마지막 뒤 페이지를 요청해도 마지막 페이지를 다시 준다
            break
        page += 1
    by_name: Dict[str, List[str]] = {}
    for _, stock, name in store.corp_rows():
        by_name.setdefault(name, []).append(stock)
    for r in rows:
        prefix = r["kind_code"] if r["kind_code"].isdigit() else ""
        cand = [s for s in by_name.get(r["name"], []) if s.startswith(prefix)]
        if cand:
            r["code"], r["code_match"] = cand[0], "name"
        elif not prefix:
            code = _kind_summary_code(r["kind_code"])
            r["code"], r["code_match"] = (code, "kind_summary") if code else (r["kind_code"] + "0", "unresolved")
        else:
            r["code"], r["code_match"] = r["kind_code"] + "0", "guess"
    out = path("reference", "delisted.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "market", "delist_date", "reason", "kind_code", "code_match", "no"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["delist_date"]))
    os.replace(out + ".tmp", out)
    job.save(target=page)
    job.finish(f"{len(rows)}건")
    if not os.path.exists(path("reference", "listed_snapshot.csv")):
        log("[kind] reference/listed_snapshot.csv 없음 — Creon 상장 스냅샷(creon_universe) 먼저")
        return False
    n = store.build_universe()
    job.save(note=f"상폐 {len(rows)}건 · 유니버스 상장 {n['listed']} + 상폐 {n['delisted']}")
    return True


# ---------------------------------------------------------------- 네이버 (상폐 종목 일봉)
def _naver_items(fp: str) -> int:
    with open(fp, "rb") as f:
        return f.read().count(b"<item ")


def run_naver() -> bool:
    codes = [r["code"][1:] for r in store.read_universe() if r["price_source"] == "naver"]
    job = Job("naver_delisted_1d", "상폐 종목 일봉 (네이버, 수정주가)", len(codes), "종목")
    for code in codes:
        fp = path("raw", "naver", "candle_1d", f"{code}.xml")
        if os.path.exists(fp) and _naver_items(fp) > 0:
            job.tick(skipped=True)
            continue
        r = get(f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=10000&requestType=0")
        if r is None:
            job.tick(failed=code)
            continue
        write_once(fp, r.content, replace=True)          # 빈 응답도 남기되, 다음 실행에서 다시 시도한다
        job.tick(fetched=True, failed=None if r.content.count(b"<item ") else f"{code}:빈응답")
        time.sleep(0.3)
    job.finish()
    return job.s["failed_n"] == 0


# ---------------------------------------------------------------- DART
def run_dart_corpcode() -> bool:
    job = Job("dart_corpcode", "DART 회사코드", 1, "파일")
    fp = path("raw", "dart", f"corpCode_{datetime.now():%Y%m%d}.zip")
    if os.path.exists(fp):
        job.tick(skipped=True)
        job.finish()
        return True
    r = get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": store.dart_key()}, timeout=120)
    if r is None or not r.content.startswith(b"PK"):
        job.tick(failed="corpCode")
        job.stop("회사코드 zip 수신 실패")
        return False
    write_once(fp, r.content)
    job.tick(fetched=True)
    job.finish()
    return True


def _fnltt_chunks(corps: List[str]) -> List[List[str]]:
    """100개사 묶음을 reference/dart_fnltt_chunks.json 에 고정 — 새 회사는 마지막 묶음에 붙여 기존 묶음(=파일명)이 안 바뀌게."""
    fp = path("reference", "dart_fnltt_chunks.json")
    chunks: List[List[str]] = (store.read_json(fp) or {}).get("chunks") or []
    have = {c for ch in chunks for c in ch}
    for c in sorted(set(corps) - have):
        if chunks and len(chunks[-1]) < 100:
            chunks[-1].append(c)
        else:
            chunks.append([c])
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"chunks": chunks, "updated_at": datetime.now().isoformat(timespec="seconds")}, f)
    return chunks


def run_dart_fnltt() -> bool:
    dart = Dart()
    chunks = _fnltt_chunks(sorted(store.universe_corps()))
    now_y = datetime.now().year
    tasks = [(y, rc, ch) for y in range(store.DART_FIRST_YEAR, now_y + 1) for rc, _ in store.DART_REPORTS for ch in chunks]
    job = Job("dart_fnltt_multi", f"DART 주요계정 ({store.DART_FIRST_YEAR}~{now_y}, 분기·반기·연간)", len(tasks), "호출")
    job.save(note=f"{sum(map(len, chunks))}개사 / {len(chunks)}묶음")
    try:
        for y, rc, ch in tasks:
            h = hashlib.sha1(",".join(ch).encode()).hexdigest()[:10]
            fp = path("raw", "dart", "fnltt_multi", f"{y}_{rc}", f"{h}.json")
            replace = False
            if os.path.exists(fp):
                if y < now_y - 1 or not store.is_empty_response((store.read_json(fp) or {}).get("response")):
                    job.tick(skipped=True)
                    continue
                replace = True                               # 최근 2개년의 '자료 없음'은 공시가 났을 수 있다
            q = {"corp_code": ",".join(ch), "bsns_year": y, "reprt_code": rc}
            st, j = dart.call("fnlttMultiAcnt", q)
            if st not in ("000", "013"):
                job.tick(failed=f"{y}_{rc}_{h}:{st}")
                continue
            dart.save(fp, "fnlttMultiAcnt", {**q, "corp_codes": ch, "corp_code": None}, j, replace=replace)
            job.tick(fetched=True)
    except DartQuota as e:
        job.stop(f"DART 하루 한도 초과 — 내일 같은 단계를 다시 실행하면 이어받는다 ({e})")
        return False
    job.finish()
    return True


def _event_windows() -> List[dict]:
    fp = path("reference", "dart_events_windows.json")
    w = (store.read_json(fp) or {}).get("windows")
    if w is None:                                       # 첫 수집(collect/dart_events.asof, 폴더 <asof>) 이어받기
        w = []
        asof = path("collect", "dart_events.asof")
        if os.path.exists(asof):
            a = open(asof).read().strip()
            w.append({"from": "20150101", "to": a, "dir": a})
    return w


def _save_event_windows(w: List[dict]):
    fp = path("reference", "dart_events_windows.json")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"windows": w}, f, ensure_ascii=False, indent=1)


def run_dart_events() -> bool:
    dart, corps = Dart(), sorted(store.universe_corps())
    windows = _event_windows()
    today = datetime.now().strftime("%Y%m%d")
    if windows:
        last_to = datetime.strptime(windows[-1]["to"], "%Y%m%d")
        if (datetime.now() - last_to).days >= EVENTS_MIN_GAP_DAYS:
            nf = (last_to + timedelta(days=1)).strftime("%Y%m%d")
            windows.append({"from": nf, "to": today, "dir": f"{nf}_{today}"})
    else:
        windows.append({"from": "20150101", "to": today, "dir": today})
    _save_event_windows(windows)
    # 어떤 창에도 없는 회사(신규)는 전체 구간을 한 번에
    seen = set()
    for api, _ in EVENT_APIS[:1]:
        for w in windows:
            d = path("raw", "dart", "events", api, w["dir"])
            if os.path.isdir(d):
                seen |= {f[:-5] for f in os.listdir(d) if f.endswith(".json")}
    cur = windows[-1]
    tasks = []
    for corp in corps:
        if corp in seen:
            tasks.append((corp, cur["from"], cur["to"], cur["dir"]))
        else:
            tasks.append((corp, "20150101", today, f"full_{today}"))
    tasks = [(api, *t) for api, _ in EVENT_APIS for t in tasks]
    job = Job("dart_events", f"DART 주요사항 7종 (창 {cur['from']}~{cur['to']})", len(tasks), "호출")
    job.save(note=" · ".join(n for _, n in EVENT_APIS))
    try:
        for api, corp, a, b, d in tasks:
            fp = path("raw", "dart", "events", api, d, f"{corp}.json")
            if os.path.exists(fp):
                job.tick(skipped=True)
                continue
            q = {"corp_code": corp, "bgn_de": a, "end_de": b}
            st, j = dart.call(api, q)
            if st not in ("000", "013"):
                job.tick(failed=f"{api}_{corp}:{st}")
                continue
            dart.save(fp, api, q, j)
            job.tick(fetched=True)
    except DartQuota as e:
        job.stop(f"DART 하루 한도 초과 — 내일 같은 단계를 다시 실행하면 이어받는다 ({e})")
        return False
    job.finish()
    return True


def run_dart_list() -> bool:
    dart = Dart()
    end = datetime.now().replace(day=1)
    months, m = [], datetime(2014, 1, 1)
    while m < end:
        nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        months.append((m, nxt - timedelta(days=1)))
        m = nxt
    job = Job("dart_list", f"DART 공시 목록 (2014-01~{months[-1][0]:%Y-%m}, 유가·코스닥)", len(months) * 2, "월×시장")
    try:
        for m0, m1 in months:
            for cls in ("Y", "K"):
                page, total, new = 1, None, False
                while total is None or page <= total:
                    fp = path("raw", "dart", "list", f"{m0:%Y%m}_{cls}_{page:03d}.json")
                    if os.path.exists(fp):
                        total = (store.read_json(fp) or {}).get("response", {}).get("total_page", 0)
                        page += 1
                        continue
                    q = {"bgn_de": f"{m0:%Y%m%d}", "end_de": f"{m1:%Y%m%d}", "corp_cls": cls, "page_no": page, "page_count": 100}
                    st, j = dart.call("list", q)
                    if st not in ("000", "013"):
                        job.s["failed"].append(f"{m0:%Y%m}_{cls}_{page}:{st}")
                        job.s["failed_n"] += 1
                        break
                    dart.save(fp, "list", q, j)
                    total = j.get("total_page", 0)
                    page += 1
                    new = True
                job.tick(fetched=new, skipped=not new)
    except DartQuota as e:
        job.stop(f"DART 하루 한도 초과 — 내일 같은 단계를 다시 실행하면 이어받는다 ({e})")
        return False
    job.finish()
    return True


def run_dart_shares() -> bool:
    """주식의 총수 현황 — 상폐 종목만. 발행주식수 = 보통주 행 distb_stock_co (Ⅳ. 발행주식의 총수)."""
    dart = Dart()
    corps = store.universe_corps()
    dl = {r["code"][1:6] + "0": int(r["delist_date"][:4]) for r in store.read_universe() if r["status"] == "delisted" and r["delist_date"]}
    tasks = [(c, y, rc) for c, stock in sorted(corps.items()) if stock in dl
             for y in range(store.DART_FIRST_YEAR, dl[stock] + 1) for rc, _ in store.DART_REPORTS]
    job = Job("dart_shares", "DART 주식총수현황 (상폐 종목, 2015~상폐연도)", len(tasks), "호출")
    job.save(note=f"{sum(1 for s in corps.values() if s in dl)}개사")
    try:
        for corp, y, rc in tasks:
            fp = path("raw", "dart", "shares", corp, f"{y}_{rc}.json")
            if os.path.exists(fp):
                job.tick(skipped=True)
                continue
            q = {"corp_code": corp, "bsns_year": str(y), "reprt_code": rc}
            st, j = dart.call("stockTotqySttus", q)
            if st not in ("000", "013"):
                job.tick(failed=f"{corp}_{y}_{rc}:{st}")
                continue
            dart.save(fp, "stockTotqySttus", q, j)
            job.tick(fetched=True)
    except DartQuota as e:
        job.stop(f"DART 하루 한도 초과 — 내일 같은 단계를 다시 실행하면 이어받는다 ({e})")
        return False
    job.finish()
    return True


def run_creon_bronze() -> bool:
    from . import bronze
    return bronze.run(log=log)["failed"] == 0


def run_gold() -> bool:
    from . import gold
    gold.build_all()
    return True


RUNNERS = {"kind": run_kind, "naver": run_naver, "dart_corpcode": run_dart_corpcode, "dart_fnltt": run_dart_fnltt,
           "dart_events": run_dart_events, "dart_list": run_dart_list, "dart_shares": run_dart_shares,
           "creon_bronze": run_creon_bronze, "gold": run_gold}


def run(step: str) -> bool:
    if step == "panel":
        from . import panel
        panel.build_all()
        return True
    return RUNNERS[step]()
