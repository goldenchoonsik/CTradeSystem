"""한국 상장사 KSIC 업종코드 — DART 기업개황(company.json, induty_code) → 종목 ↔ 코스피 업종(kr_industry.SECTORS 의 KSIC 열) 대응. 종목 단계(L3)용.

  raw/dart_company/<corp_code>.json                  기업개황 원본(받은 건 건너뜀 — 증분)
  derived/kr_industry/company_ksic.parquet           stock_code(A코드)·corp_code·name·market·induty_code(KSIC 3자리)·ksic2(C26 꼴)·sector(U코드 또는 None)
입력: kr_equity 저장소의 universe.csv(상장·보통주·스팩 제외)와 raw/dart/corpCode.zip(종목↔corp_code). 키 OPEN_DART_KEY(kr_equity.store.dart_key).
한계: 현재 상장사만(상폐사 제외 → 과거 검정은 생존 편향), 분류는 오늘 기준(이력 없음). 한 종목 1요청, 일 한도 10,000.
"""
import io
import json
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from typing import Dict, Optional

import pandas as pd

from shared.data_library.paths import data_vault_path

from . import kr_industry, store
from .store import log

URL = "https://opendart.fss.or.kr/api/company.json"
# KSIC 대분류 C(제조업) 2자리 → 코스피 업종(SECTORS 의 4번째 열에서 역산). 제조업 밖은 None.
KSIC2_TO_SECTOR: Dict[str, str] = {v[3]: k for k, v in kr_industry.SECTORS.items() if v[3]}


def ksic2(code: Optional[str]) -> Optional[str]:
    """DART induty_code(KSIC 3~5자리 숫자) → 'C26' 꼴 2자리(제조업 10~33 만 C, 나머지는 None)."""
    if not code or not str(code).strip().isdigit():
        return None
    d2 = int(str(code).strip()[:2])
    return f"C{d2:02d}" if 10 <= d2 <= 33 else None


def _universe() -> pd.DataFrame:
    u = pd.read_csv(data_vault_path("staging", "kr_equity", "universe.csv"), dtype=str)
    u = u[(u.status == "listed") & (u.is_preferred == "0") & (u.is_spac == "0")]
    return u[["code", "name", "market"]].reset_index(drop=True)


def _corp_codes() -> Dict[str, str]:
    z = zipfile.ZipFile(data_vault_path("staging", "kr_equity", "raw", "dart", "corpCode.zip"))
    root = ET.fromstring(z.read(z.namelist()[0]))
    out = {}
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            out["A" + sc] = el.findtext("corp_code")
    return out


def fetch_one(corp_code: str) -> dict:
    fp = store.path("raw", "dart_company", f"{corp_code}.json")
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    from shared.data_library.kr_equity.store import dart_key
    raw = store.get(f"{URL}?crtfc_key={dart_key()}&corp_code={corp_code}", timeout=60)
    j = json.loads(raw)
    if j.get("status") != "000":
        raise RuntimeError(f"DART {corp_code}: {j.get('status')} {j.get('message')}")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False)
    return j


def build_table(records: list, uni: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df["ksic2"] = df["induty_code"].map(ksic2)
    df["sector"] = df["ksic2"].map(KSIC2_TO_SECTOR)
    return uni.rename(columns={"code": "stock_code"}).merge(df[["stock_code", "corp_code", "induty_code", "ksic2", "sector"]], on="stock_code", how="left")


def run(limit: Optional[int] = None) -> bool:
    uni, cc = _universe(), _corp_codes()
    recs, fail, n_new = [], [], 0
    for i, r in enumerate(uni.itertuples(index=False)):
        if limit and i >= limit:
            break
        corp = cc.get(r.code)
        if not corp:
            fail.append(f"{r.code} corp_code 없음")
            continue
        try:
            cached = os.path.exists(store.path("raw", "dart_company", f"{corp}.json"))
            j = fetch_one(corp)
            if not cached:
                n_new += 1
                time.sleep(0.15)
            recs.append({"stock_code": r.code, "corp_code": corp, "induty_code": j.get("induty_code"), "est_dt": j.get("est_dt")})
        except Exception as e:   # noqa: BLE001
            fail.append(f"{r.code} {str(e)[:60]}")
    if not recs:
        log(f"[kr_ksic] 전부 실패: {fail[:5]}")
        return False
    df = build_table(recs, uni)
    os.makedirs(store.path("derived", "kr_industry"), exist_ok=True)
    df.to_parquet(store.path("derived", "kr_industry", "company_ksic.parquet"))
    log(f"[kr_ksic] {len(df)}종목(신규 요청 {n_new}), 업종 대응 {df.sector.notna().sum()}, 실패 {len(fail)} {fail[:3]}")
    return len(fail) < 20
