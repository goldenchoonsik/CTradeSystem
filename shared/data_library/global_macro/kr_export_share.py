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
from typing import Dict, List, Optional

import pandas as pd

from . import reading as R
from . import store
from .store import log

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
        raise RuntimeError(f"DART document {rcept_no}: zip 아님 ({raw[:120]!r})")
    return raw


def index_path(year: int) -> str:
    return store.path("raw", "dart_reports", str(year), "index.csv")


def read_index(year: int) -> pd.DataFrame:
    fp = index_path(year)
    cols = ["stock_code", "corp_code", "rcept_no", "report_nm", "rcept_dt", "bytes", "status"]
    return pd.read_csv(fp, dtype=str) if os.path.exists(fp) else pd.DataFrame(columns=cols)


def fetch(year: int = 2025, limit: Optional[int] = None, sleep: float = 0.25) -> dict:
    """대상 종목의 사업보고서 원문을 받는다(받은 건 건너뜀, 목록 조회 결과도 index 에 남겨 재조회 안 함)."""
    t = targets()
    idx = read_index(year)
    done = set(idx.stock_code)
    os.makedirs(os.path.dirname(index_path(year)), exist_ok=True)
    new, fail = 0, []
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
                fail.append(f"{r.stock_code} {str(e)[:80]}")
                w.writerow([r.stock_code, r.corp_code, "", "", "", 0, f"error: {str(e)[:60]}"]); f.flush()
                time.sleep(sleep * 4)
                if len(fail) >= 30 and len(fail) > new:            # 키·한도 문제면 멈춘다
                    log(f"[export_share] 실패 누적 {len(fail)} — 중단: {fail[-1]}")
                    break
    info = {"year": year, "targets": len(t), "fetched_now": new, "failed": len(fail), "index_rows": len(read_index(year))}
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
    recs = []
    for r in idx.itertuples(index=False):
        rec = {"stock_code": r.stock_code, "corp_code": r.corp_code, "rcept_no": r.rcept_no, "status": r.status,
               "name": t.name.get(r.stock_code), "sector": t.sector.get(r.stock_code)}
        if r.status == "ok":
            fp = store.path("raw", "dart_reports", str(year), f"{r.corp_code}_{r.rcept_no}.zip")
            try:
                with open(fp, "rb") as f:
                    p = parse_xml(main_xml(f.read()))
                if p:
                    rec.update(export_share=p["share"], export=p["export"], domestic=p["domestic"], method=p["method"], verified=p["verified"],
                               title_good=p["title_good"], table_title=p["title"][-80:], parse="ok")
                else:
                    rec["parse"] = "no_table"
            except Exception as e:   # noqa: BLE001
                rec["parse"] = f"error: {str(e)[:60]}"
        else:
            rec["parse"] = r.status
        recs.append(rec)
    df = pd.DataFrame(recs)
    os.makedirs(store.path("derived", "kr_industry"), exist_ok=True)
    df.to_parquet(store.path("derived", "kr_industry", f"export_share_{year}.parquet"))
    ok = df[df.parse == "ok"]
    log(f"[export_share] {year} 파싱 {len(ok)}/{len(df)} (검증 {int(ok.verified.sum()) if len(ok) else 0}), 업종별 " +
        ", ".join(f"{s} {int((ok.sector == s).sum())}/{int((df.sector == s).sum())}" for s in sorted(df.sector.dropna().unique())))
    return df
