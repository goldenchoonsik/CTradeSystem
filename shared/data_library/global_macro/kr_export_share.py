"""회사별 수출 비중 — DART 사업보고서 원문(document.xml)의 '매출실적' / '지역별 매출' 표에서 수출(해외)·내수(국내) 분리를 읽는다. 종목 단계(L3)의 수출 노출 원천.

  raw/dart_reports/<연도>/<corp_code>_<rcept_no>.zip     사업보고서 원문(무수정, 한 번 받으면 재사용)
  raw/dart_reports/<연도>/index.csv                       받은 목록(stock_code·corp_code·rcept_no·report_nm·rcept_dt·bytes)
  derived/kr_industry/export_share_<연도>.parquet          회사별 수출 비중(파싱 결과·방법·근거 표 제목)
2026-10-22 파일럿: 2025 사업연도 1개, 판독 업종 7개(reading.SECTOR_HS)에 속한 상장 제조업만. 성공률을 본 뒤 이력(2013~)·재사전등록(stage5 S2).
DART 일 한도 10,000 — 종목당 목록 1 + 원문 1 요청.
"""
import csv
import io
import json
import os
import re
import time
import zipfile
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from . import issues as IS
from . import reading as R
from . import store
from .store import log

FIRST_YEAR = 2013                    # 이력 시작(종목 월말 종가 2013-01~)


def grid_years(latest: int) -> List[int]:
    """2년 격자(2026-10-22 결정: 1년 순위 상관 0.90·3년 0.81~0.84 → 2년): 최근 사업연도부터 2년씩 거슬러 FIRST_YEAR 까지."""
    return [y for y in range(latest, FIRST_YEAR - 1, -2)]


LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DOC_URL = "https://opendart.fss.or.kl/api/document.xml".replace(".kl", ".kr")


def _key() -> str:
    from shared.data_library.kr_equity.store import dart_key
    return dart_key()


def targets() -> pd.DataFrame:
    """파일럿 대상: company_ksic 에서 판독 업종 7개에 대응된 종목."""
    d = pd.read_parquet(store.path("derived", "kr_industry", "company_ksic.parquet"))
    return d[d.sector.isin(list(R.SECTOR_HS))].reset_index(drop=True)


def find_report(corp_code: str, year: int) -> Optional[dict]:
    """그 사업연도의 사업보고서(다음 해 접수) 중 가장 늦게 접수된 것(정정 포함) — 없으면 None."""
    url = f"{LIST_URL}?crtfc_key={_key()}&corp_code={corp_code}&bgn_de={year + 1}0101&end_de={year + 1}1231&pblntf_detail_ty=A001&page_count=100"
    j = json.loads(store.get(url, timeout=60))
    if j.get("status") == "013":                                   # 조회 결과 없음
        return None
    if j.get("status") != "000":
        raise RuntimeError(f"DART list {corp_code}: {j.get('status')} {j.get('message')}")
    cands = [x for x in j.get("list", []) if "사업보고서" in x.get("report_nm", "")]
    pref = [x for x in cands if f"({year}.12)" in x["report_nm"]] or cands
    if not pref:
        return None
    return max(pref, key=lambda x: x["rcept_dt"] + x["rcept_no"])


def fetch_doc(rcept_no: str) -> bytes:
    raw = store.get(f"{DOC_URL}?crtfc_key={_key()}&rcept_no={rcept_no}", timeout=180)
    if not raw.startswith(b"PK"):
        m = re.search(rb"<status>(\d+)</status>", raw)
        raise RuntimeError(f"DART document {rcept_no}: status {m.group(1).decode() if m else '?'} zip 아님 ({raw[:120]!r})")
    return raw


def index_path(year: int) -> str:
    return store.path("raw", "dart_reports", str(year), "index.csv")


def read_index(year: int) -> pd.DataFrame:
    fp = index_path(year)
    cols = ["stock_code", "corp_code", "rcept_no", "report_nm", "rcept_dt", "bytes", "status"]
    if not os.path.exists(fp):
        return pd.DataFrame(columns=cols)
    return pd.read_csv(fp, dtype=str).drop_duplicates("stock_code", keep="last").reset_index(drop=True)   # 재시도 줄은 뒤에 붙음 → 마지막 줄이 현재


def fetch(year: int = 2025, limit: Optional[int] = None, sleep: float = 0.25, retry_errors: bool = False) -> dict:
    """대상 종목의 사업보고서 원문을 받는다(받은 건 건너뜀, 목록 조회 결과도 index 에 남겨 재조회 안 함).
    DART 일 한도 초과(status 020)는 기록하지 않고 즉시 멈춘다(다음 날 다시 실행하면 이어서). retry_errors=True 면 전에 error 로 적힌 종목을 다시 시도(index 는 append-only 라 새 줄이 뒤에 붙고 read_index 는 마지막 줄을 쓴다)."""
    t = targets()
    idx = read_index(year)
    done = set(idx[~idx.status.str.startswith("error")].stock_code) if retry_errors else set(idx.stock_code)
    os.makedirs(os.path.dirname(index_path(year)), exist_ok=True)
    new, fail, quota_stop = 0, [], False
    with open(index_path(year), "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not len(idx):
            w.writerow(["stock_code", "corp_code", "rcept_no", "report_nm", "rcept_dt", "bytes", "status"])
        for i, r in enumerate(t.itertuples(index=False)):
            if r.stock_code in done or (limit and new >= limit):
                continue
            try:
                rep = find_report(r.corp_code, year)
                if not rep:
                    w.writerow([r.stock_code, r.corp_code, "", "", "", 0, "no_report"]); f.flush()
                    time.sleep(sleep)
                    continue
                fp = store.path("raw", "dart_reports", str(year), f"{r.corp_code}_{rep['rcept_no']}.zip")
                if not os.path.exists(fp):
                    data = fetch_doc(rep["rcept_no"])
                    with open(fp + ".tmp", "wb") as g:
                        g.write(data)
                    os.replace(fp + ".tmp", fp)
                    time.sleep(sleep)
                w.writerow([r.stock_code, r.corp_code, rep["rcept_no"], rep["report_nm"], rep["rcept_dt"], os.path.getsize(fp), "ok"]); f.flush()
                new += 1
                if new % 50 == 0:
                    log(f"[export_share] {year} 수신 {new}건 (대상 {len(t)}, 진행 {i + 1})")
            except Exception as e:   # noqa: BLE001
                if "020" in str(e) or "한도" in str(e) or "초과" in str(e):           # 일 한도 — 기록 없이 중단
                    log(f"[export_share] DART 한도 초과로 중단({new}건 받음): {str(e)[:80]}")
                    quota_stop = True
                    break
                fail.append(f"{r.stock_code} {str(e)[:80]}")
                w.writerow([r.stock_code, r.corp_code, "", "", "", 0, f"error: {str(e)[:60]}"]); f.flush()
                time.sleep(sleep * 4)
                if len(fail) >= 30 and len(fail) > new:            # 키·한도 문제면 멈춘다
                    log(f"[export_share] 실패 누적 {len(fail)} — 중단: {fail[-1]}")
                    break
    info = {"year": year, "targets": len(t), "fetched_now": new, "failed": len(fail), "index_rows": len(read_index(year)), "quota_stop": quota_stop,
            "complete": (not quota_stop) and len(read_index(year)) >= len(t)}
    log(f"[export_share] {year} 수신 끝 — 신규 {new}, 실패 {len(fail)} {fail[:3]}, index {info['index_rows']}행")
    return info


def main_xml(zip_bytes: bytes) -> str:
    """본문(<접수번호>.xml) 먼저, 그 뒤 첨부(감사보고서 — 지역별 매출 주석이 여기 있는 회사가 많다) 순으로 이어 붙인다."""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = sorted(zf.namelist(), key=lambda n: (0 if re.fullmatch(r"\d+\.xml", os.path.basename(n)) else 1, n))
    return "\n".join(_decode(zf.read(n)) for n in names)


def _decode(b: bytes) -> str:
    """옛 공시(~2019 접수)는 헤더에 utf-8 이라 적혀 있어도 실제는 cp949 — utf-8 엄격 디코딩이 실패하면 cp949."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp949", errors="replace")


# ---------------------------------------------------------------- 파싱: 수출(해외)·내수(국내) 분리 표 찾기
# 라벨 칸: 키워드로 시작하고 짧아야 한다("건축공사(국내)" 같은 품목명은 라벨이 아님)
EXP = re.compile(r"^(수\s*출|해\s*외|국\s*외|역\s*외)(\s*(매출|합계|소계|계|부문|지역|판매))?$")
DOM = re.compile(r"^(내\s*수|국\s*내)(\s*(매출|합계|소계|계|부문|지역|판매))?$")
ANY_EXP = re.compile(r"수\s*출|해\s*외|국\s*외")
ANY_DOM = re.compile(r"내\s*수|국\s*내")
TOTAL = re.compile(r"합\s*계|총\s*계|^계$|소\s*계|total", re.I)
KOREA = re.compile(r"^(한\s*국|대\s*한\s*민\s*국|국\s*내|내\s*수)(\s*(매출|합계|소계|계|부문|지역|판매))?$")
REGION_TITLE = re.compile(r"지역|국가|region|geograph", re.I)
NOT_REGION = re.compile(r"조정|내부|거래|제거|연결|중단|순매출|기타\s*조정|할인|차감")
NUM = re.compile(r"^\(?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?$|^\(?-?\d+(?:\.\d+)?\)?$")
TITLE_GOOD = re.compile(r"매출\s*실적|지역\s*별\s*매출|수출\s*및\s*내수|수출\s*·\s*내수|판매\s*실적|매출\s*현황|매출\s*의?\s*세부|매출\s*구성|매출\s*내역")
# 매출이 아닌 표(가격·수량·건수·채권·생산능력·인원 …)는 제외 — 제목(표 앞 글)과 머리글 둘 다 본다
TITLE_BAD = re.compile(r"가격|단가|생산\s*능력|가동률|생산\s*실적|채권|신용|위험|지적\s*재산|특허|임직원|종업원|인원|수주\s*잔고|수주\s*현황|원재료|매입|투자\s*현황|차입|담보|종속|관계기업|판매\s*수량|수량")


def _cell(x: str) -> str:
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", x))).strip()


def _num(x: str) -> Optional[float]:
    x = re.sub(r"\(\s*(US)?\$[^)]*\)|\$[\d,.]+", "", x).replace(" ", "")          # "409,178($285,161)" 달러 병기 제거
    if not NUM.match(x) or x in ("-", ""):
        return None
    neg = x.startswith("(") and x.endswith(")")
    v = float(x.strip("()").replace(",", ""))
    return -v if neg else v


def tables(xml: str) -> List[dict]:
    out = []
    for m in re.finditer(r"<TABLE.*?</TABLE>", xml, re.S):
        rows = [[_cell(c) for c in re.findall(r"<T[DH][^>]*>(.*?)</T[DH]>", r, re.S)] for r in re.findall(r"<TR.*?</TR>", m.group(0), re.S)]
        rows = [r for r in rows if any(r)]
        if len(rows) < 2:
            continue
        title = _cell(xml[max(0, m.start() - 400):m.start()])[-160:]
        out.append({"pos": m.start(), "rows": rows, "title": title})
    return out


def _label_hit(cells: List[str], rx) -> bool:
    """라벨 칸은 앞쪽 5칸 안(ROWSPAN 으로 부문·유형·품목 뒤에 구분 칸이 올 수 있음), 짧은 글, 숫자 아님."""
    return any(rx.search(c.strip()) and len(c) <= 12 and not _num(c) for c in cells[:5])


def _first_num(cells: List[str]) -> Optional[float]:
    for c in cells:
        v = _num(c)
        if v is not None:
            return v
    return None


def parse_table(tb: dict) -> Optional[dict]:
    rows = tb["rows"]
    # (A) 행 방향: 수출/해외 행과 내수/국내 행, 숫자는 첫 열(당기)
    # ROWSPAN: 라벨(수출/내수) 행 아래 라벨 없이 칸 수가 적은 행들은 같은 그룹(POSCO: 국내 열연·냉연·…). 합계/새 그룹 행에서 끊는다.
    exp_rows, dom_rows, cur, hdr = [], [], None, max(len(r) for r in rows[:2])
    for r in rows:
        is_e, is_d = _label_hit(r, EXP), _label_hit(r, DOM)
        if is_e and not is_d:
            cur = "e"; exp_rows.append(r)
        elif is_d and not is_e:
            cur = "d"; dom_rows.append(r)
        elif any(TOTAL.search(c) for c in r[:3] if c and not _num(c)) or len(r) >= hdr:
            cur = None
        elif cur and _first_num(r[1:]) is not None and r and not _num(r[0]):
            (exp_rows if cur == "e" else dom_rows).append(r)
    res = None
    if exp_rows and dom_rows:
        def pick(rs):
            """합계 라벨이 붙은 행이 있으면 그 행. 없고 값이 3개 이상이며 한 행이 나머지 합과 1% 안이면 그 행(라벨 없는 합계 블록). 아니면 합."""
            lab = [r for r in rs if any(TOTAL.search(c) for c in r[:3] if c and not _num(c))]
            vals = [v for v in (_first_num(r[1:]) for r in (lab or rs)) if v is not None]
            if not vals:
                return None
            if lab:
                return max(vals)
            if len(vals) >= 3:
                m, rest = max(vals), sum(vals) - max(vals)
                if rest > 0 and abs(m - rest) <= 0.01 * m:
                    return m
            return sum(vals)
        e, d = pick(exp_rows), pick(dom_rows)
        if e is not None and d is not None:
            res = {"export": e, "domestic": d, "method": "rows"}
    # (B) 열 방향: 머리글에 수출·내수 열, 값은 합계 행(없으면 숫자 행 합)
    if res is None:
        for hi, h in enumerate(rows[:4]):
            ei = [i for i, c in enumerate(h) if EXP.search(c.strip())]
            di = [i for i, c in enumerate(h) if DOM.search(c.strip())]
            if ei and di and len(h) >= 3:
                body = rows[hi + 1:]
                tot = [r for r in body if any(TOTAL.search(c) for c in r[:2] if not _num(c))]
                def col(r, i):
                    return _num(r[i]) if i < len(r) else None
                if tot:
                    e, d = col(tot[-1], ei[0]), col(tot[-1], di[0])
                else:
                    es = [col(r, ei[0]) for r in body]; ds = [col(r, di[0]) for r in body]
                    e = sum(v for v in es if v is not None) if any(v is not None for v in es) else None
                    d = sum(v for v in ds if v is not None) if any(v is not None for v in ds) else None
                if e is not None and d is not None:
                    res = {"export": e, "domestic": d, "method": "cols"}
                break
    # (C) 국가별: '한국' 행 = 내수, 다른 짧은 라벨 행(나라·지역) = 수출 — 제목이나 머리글에 지역/국가가 있을 때만
    if res is None and (REGION_TITLE.search(tb["title"][-90:]) or REGION_TITLE.search(" ".join(rows[0]))):
        kor = [r for r in rows if r and KOREA.search(r[0].strip()) and len(r[0]) <= 12]          # 나라·지역은 첫 칸 라벨이어야 한다(가운데 '국가' 열은 제외)
        if len(kor) == 1:
            d = _first_num(kor[0][1:])
            others = []
            for r in rows:
                if r is kor[0] or not r:
                    continue
                lab = r[0].strip()
                if not lab or _num(lab) or len(lab) > 12 or TOTAL.search(lab) or NOT_REGION.search(lab) or KOREA.search(lab):
                    continue
                v = _first_num(r[1:])
                if v is not None:
                    others.append(v)
            money = re.search(r"원", tb["title"][-90:] + " " + " ".join(rows[0]) + " ".join(rows[1] if len(rows) > 1 else []))
            if d is not None and others and (money or d + sum(others) > 10000):          # 비중(%) 표는 제외
                res = {"export": sum(others), "domestic": d, "method": "region"}
    if res is None:
        return None
    e, d = res["export"], res["domestic"]
    if e < 0 or d < 0 or e + d <= 0:
        return None
    res.update(share=e / (e + d), pos=tb["pos"], title=tb["title"])
    # 합계 검증: 표 안에 합계 행이 있고 그 첫 숫자가 e+d 와 2% 안이면 확인됨
    tot_rows = [r for r in rows if r and any(TOTAL.search(c) for c in r[:2] if not _num(c)) and not _label_hit(r, EXP) and not _label_hit(r, DOM)]
    nums = [v for r in tot_rows for v in (_num(c) for c in r[1:]) if v is not None]                 # 합계 행의 어느 숫자(당기 열·합계 열)든 e+d 와 2% 안
    res["verified"] = any(abs(v - (e + d)) <= 0.02 * (e + d) for v in nums)
    head = " ".join(" ".join(r) for r in rows[:2])
    tail = tb["title"][-90:]                                     # 표 바로 앞 제목만(앞 표의 꼬리는 제외)
    if TITLE_BAD.search(tail) and not TITLE_GOOD.search(tail):
        return None
    if re.search(r"원/|단가|수량|건수|톤|명$", head) and not re.search(r"백만원|천원|억원|원\)", tail + " " + head):
        return None
    res["title_good"] = bool(TITLE_GOOD.search(tail))
    return res


def parse_xml(xml: str) -> Optional[dict]:
    """문서 전체에서 가장 믿을 만한 수출/내수 표 하나 — 점수: 제목 매출실적·지역별 +3, 합계 검증 +2, 앞쪽(사업의 내용) 우선."""
    best = None
    for tb in tables(xml):
        flat = " ".join(" ".join(r) for r in tb["rows"])
        if not ((ANY_EXP.search(flat) and ANY_DOM.search(flat)) or any(r and KOREA.search(r[0].strip()) for r in tb["rows"])):
            continue
        r = parse_table(tb)
        if not r:
            continue
        r["score"] = 3 * r["title_good"] + 2 * r["verified"] - tb["pos"] / 1e7
        if best is None or r["score"] > best["score"]:
            best = r
    return best


def build(year: int = 2025) -> pd.DataFrame:
    """index 의 받은 원문을 전부 파싱해 회사별 수출 비중 표를 쓴다."""
    idx = read_index(year)
    t = targets().set_index("stock_code")
    recs, details = [], {}
    for r in idx.itertuples(index=False):
        rec = {"stock_code": r.stock_code, "corp_code": r.corp_code, "rcept_no": r.rcept_no, "status": r.status,
               "name": t.name.get(r.stock_code), "sector": t.sector.get(r.stock_code)}
        if r.status == "ok":
            fp = store.path("raw", "dart_reports", str(year), f"{r.corp_code}_{r.rcept_no}.zip")
            try:
                with open(fp, "rb") as f:
                    xml = main_xml(f.read())
                p = parse_xml(xml)
                if p:
                    rec.update(export_share=p["share"], export=p["export"], domestic=p["domestic"], method=p["method"], verified=p["verified"],
                               title_good=p["title_good"], table_title=p["title"][-80:], parse="ok")
                else:
                    rec["parse"] = "no_table"
                    details[r.stock_code] = {"hints": _sales_table_hints(xml)}
            except Exception as e:   # noqa: BLE001
                import traceback
                rec["parse"] = f"error: {str(e)[:60]}"
                details[r.stock_code] = {"trace": traceback.format_exc()[-800:]}
        else:
            rec["parse"] = r.status
        recs.append(rec)
    df = pd.DataFrame(recs)
    os.makedirs(store.path("derived", "kr_industry"), exist_ok=True)
    df.to_parquet(store.path("derived", "kr_industry", f"export_share_{year}.parquet"))
    ok = df[df.parse == "ok"]
    counts = write_issues(year, df, details)
    log(f"[export_share] {year} 파싱 {len(ok)}/{len(df)} (검증 {int(ok.verified.sum()) if len(ok) else 0}), 업종별 " +
        ", ".join(f"{s} {int((ok.sector == s).sum())}/{int((df.sector == s).sum())}" for s in sorted(df.sector.dropna().unique())) +
        f" · 이슈 {sum(counts.values())}건 {counts} → export_share/issues_digest.md")
    return df


def run_grid(latest: Optional[int] = None) -> dict:
    """UI 단계: 격자 연도를 최근 것부터 이어받는다 — 받은 종목은 건너뛰고, 한도에 걸리면 멈춘다(다음 날 다시 누르면 이어서).
    새로 받은 게 있거나 결과 파일이 없을 때만 그 해를 다시 파싱한다."""
    from datetime import date as _d
    latest = latest or (_d.today().year - 1)
    out = {"years": {}, "stopped": None}
    for y in grid_years(latest):
        info = fetch(y)
        have = os.path.exists(store.path("derived", "kr_industry", f"export_share_{y}.parquet"))
        if info["fetched_now"] or not have:
            build(y)
        out["years"][y] = {"fetched_now": info["fetched_now"], "complete": info["complete"], "index_rows": info["index_rows"], "targets": info["targets"]}
        if info["quota_stop"]:
            out["stopped"] = f"{y}: DART 일 한도 — 내일 같은 단계를 다시 실행하면 이어서 받음"
            break
    done = [y for y, v in out["years"].items() if v["complete"]]
    log(f"[export_share] 격자 {grid_years(latest)} 중 완료 {done}" + (f" · 중단 {out['stopped']}" if out["stopped"] else " · 전부 완료"))
    return out


# ---------------------------------------------------------------- 파싱 이슈 축적 — 사람이 아니라 다음에 파서를 고칠 때 한 번에 보도록
ISSUE_DIR = ("export_share",)                      # DataVault/staging/global_macro/export_share/issues_<연도>.jsonl, issues_digest.md, issues_history.jsonl
JUMP_PP = 30.0                                      # 인접 격자 해 사이 비중 변화가 이보다 크면 '급변' 이슈(사업 변화 또는 표 선택 변화)


def _sales_table_hints(xml: str, n: int = 3) -> List[dict]:
    """표 못 찾은 문서에서 '매출' 제목 표의 머리글 — 파서가 놓친 배치를 알아보기 위한 단서."""
    out = []
    for tb in tables(xml):
        if TITLE_GOOD.search(tb["title"][-90:]):
            out.append({"pos": tb["pos"], "title": tb["title"][-60:], "head": [r[:7] for r in tb["rows"][:3]]})
            if len(out) >= n:
                break
    return out


def _record_issue(issues: list, year: int, rec: dict, kind: str, detail: dict):
    issues.append({"year": year, "kind": kind, "stock_code": rec.get("stock_code"), "corp_code": rec.get("corp_code"), "name": rec.get("name"),
                   "sector": rec.get("sector"), "rcept_no": rec.get("rcept_no"), "detail": detail})


def _jump_issues(year: int, df: pd.DataFrame) -> list:
    """같은 종목의 이전 격자 해(있는 것 중 가장 가까운)와 비교해 JUMP_PP 넘게 바뀐 것 — 두 해의 표 제목·방법을 같이 적는다."""
    import glob
    prev = sorted(int(os.path.basename(f).split("_")[-1].split(".")[0]) for f in glob.glob(store.path("derived", "kr_industry", "export_share_*.parquet")))
    prev = [y for y in prev if y < year]
    if not prev:
        return []
    p = pd.read_parquet(store.path("derived", "kr_industry", f"export_share_{prev[-1]}.parquet"))
    p = p[p.parse == "ok"].set_index("stock_code")
    out = []
    for r in df[df.parse == "ok"].itertuples():
        if r.stock_code in p.index:
            q = p.loc[r.stock_code]
            d = (r.export_share - q.export_share) * 100
            if abs(d) >= JUMP_PP:
                _record_issue(out, year, r._asdict(), "jump", {"vs_year": prev[-1], "delta_pp": round(float(d), 1), "now": {"share": round(float(r.export_share), 3), "method": r.method, "verified": bool(r.verified), "title": r.table_title},
                                                            "prev": {"share": round(float(q.export_share), 3), "method": q.method, "verified": bool(q.verified), "title": q.table_title}})
    return out


def write_issues(year: int, df: pd.DataFrame, parse_details: Dict[str, dict]) -> dict:
    """빌드 결과에서 이슈를 뽑아 issues_<연도>.jsonl 로 다시 쓴다(그 해의 현재 상태 — 고쳐지면 다음 빌드에서 사라짐). 반환 = 종류별 건수."""
    issues = []
    for r in df.itertuples():
        rec = r._asdict()
        d = parse_details.get(r.stock_code, {})
        if r.parse == "no_table":
            _record_issue(issues, year, rec, "no_table", {"sales_tables": d.get("hints", []), "note": "매출 표 자체 없음" if not d.get("hints") else "매출 표는 있으나 수출/내수(또는 국가별) 분리를 못 읽음"})
        elif str(r.parse).startswith("error"):
            _record_issue(issues, year, rec, "parse_error", {"message": r.parse, "trace": d.get("trace")})
        elif r.parse == "ok" and not r.verified:
            _record_issue(issues, year, rec, "unverified", {"share": round(float(r.export_share), 3), "export": r.export, "domestic": r.domestic, "method": r.method, "title": r.table_title})
        elif str(r.status).startswith("error"):
            _record_issue(issues, year, rec, "doc_missing", {"message": r.status})
    issues += _jump_issues(year, df)
    d = store.path(*ISSUE_DIR)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"issues_{year}.jsonl"), "w", encoding="utf-8") as f:
        for it in issues:
            f.write(json.dumps(it, ensure_ascii=False, default=str) + "\n")
    counts = pd.Series([i["kind"] for i in issues]).value_counts().to_dict() if issues else {}
    IS.replace(f"export_share:{year}", [{"kind": ("doc_missing" if i["kind"] == "doc_missing" else i["kind"]), "key": f"{i['stock_code']}:{i['kind']}",
                                          "title": f"{i['name']}({i['stock_code']}, {i['sector']}) {i['kind']}" + (f" {i['detail'].get('delta_pp'):+.0f}%p" if i["kind"] == "jump" else ""),
                                          "detail": i["detail"], "hint": "kr_export_share.py 파서 규칙 — export_share/issues_digest.md 에 원인 자료"} for i in issues])
    with open(os.path.join(d, "issues_history.jsonl"), "a", encoding="utf-8") as f:       # 빌드마다 건수만 append — 고쳐지는 추이
        f.write(json.dumps({"logged_at": datetime.now().isoformat(timespec="seconds"), "year": year, "counts": counts, "n_ok": int((df.parse == "ok").sum()), "n": len(df)}, ensure_ascii=False) + "\n")
    write_digest()
    return counts


def read_issues() -> List[dict]:
    import glob
    out = []
    for fp in sorted(glob.glob(store.path(*ISSUE_DIR, "issues_*.jsonl"))):
        with open(fp, encoding="utf-8") as f:
            out += [json.loads(l) for l in f if l.strip()]
    return out


def write_digest(per_kind: int = 12) -> str:
    """issues_digest.md — 한 번에 읽고 파서를 고치기 위한 요약: 연도×종류 건수, 종류별 예(원인 자료 포함), 자주 나오는 머리글 패턴."""
    its = read_issues()
    L = ["# 회사별 수출 비중 — 파싱 이슈 다이제스트 (빌드마다 다시 씀)", "",
         f"생성 {datetime.now().isoformat(timespec='seconds')} · 전체 {len(its)}건. 종류: no_table(분리 표 못 찾음) · parse_error(예외) · unverified(합계 검증 안 됨) · doc_missing(DART 원문 없음, 고칠 수 없음) · jump(인접 격자 해 대비 {JUMP_PP:.0f}%p 이상 변화 — 사업 변화 또는 표 선택 변화).",
         "고치는 법: 아래 예를 보고 `kr_export_share.py` 파서 규칙을 바꾼 뒤 그 해를 다시 `build(year)` 하면 해결된 이슈는 사라진다. 결과는 `issues_history.jsonl` 에 건수 추이로 남는다.", ""]
    if not its:
        L.append("이슈 없음.")
    else:
        df = pd.DataFrame(its)
        L += ["## 연도 × 종류", "", "| 연도 | " + " | ".join(sorted(df.kind.unique())) + " |", "|---|" + "---|" * df.kind.nunique()]
        for y, g in df.groupby("year"):
            c = g.kind.value_counts()
            L.append(f"| {y} | " + " | ".join(str(int(c.get(k, 0))) for k in sorted(df.kind.unique())) + " |")
        # 패턴: no_table 에서 매출 표 머리글 첫 행의 빈도
        heads = {}
        for it in its:
            if it["kind"] == "no_table":
                for t in it["detail"].get("sales_tables", [])[:1]:
                    key = " | ".join(t["head"][0][:6]) if t["head"] else "(빈 표)"
                    heads[key] = heads.get(key, 0) + 1
        if heads:
            L += ["", "## no_table 에서 자주 보이는 매출 표 머리글(첫 행) — 파서가 못 읽는 배치", ""]
            for k, v in sorted(heads.items(), key=lambda kv: -kv[1])[:15]:
                L.append(f"- {v}건: `{k}`")
        for kind in ["parse_error", "no_table", "jump", "unverified", "doc_missing"]:
            g = df[df.kind == kind]
            if not len(g):
                continue
            L += ["", f"## {kind} ({len(g)}건) — 예 {min(per_kind, len(g))}건", ""]
            for it in g.head(per_kind).to_dict("records"):
                d = it["detail"]
                if kind == "no_table":
                    tt = "; ".join(f"[{t['title'][-40:]}] " + " / ".join(" ".join(h[:6]) for h in t["head"][:2]) for t in d.get("sales_tables", [])) or d.get("note")
                    L.append(f"- {it['year']} {it['name']}({it['stock_code']}, {it['sector']}) {it['rcept_no']}: {tt}")
                elif kind == "jump":
                    L.append(f"- {it['year']} {it['name']}({it['stock_code']}): {d['prev']['share']:.2f}({d['vs_year']}, {d['prev']['method']}{'○' if d['prev']['verified'] else ''}) → {d['now']['share']:.2f}({d['now']['method']}{'○' if d['now']['verified'] else ''}) {d['delta_pp']:+.0f}%p · 표: {str(d['prev']['title'])[-35:]} → {str(d['now']['title'])[-35:]}")
                elif kind == "unverified":
                    L.append(f"- {it['year']} {it['name']}({it['stock_code']}): 비중 {d['share']:.2f} 수출 {d['export']:,.0f} 내수 {d['domestic']:,.0f} {d['method']} · 표: {str(d['title'])[-50:]}")
                else:
                    L.append(f"- {it['year']} {it['name']}({it['stock_code']}): {d.get('message')}" + (f"\\n  {d['trace'][-300:]}" if d.get("trace") else ""))
    fp = store.path(*ISSUE_DIR, "issues_digest.md")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return fp
