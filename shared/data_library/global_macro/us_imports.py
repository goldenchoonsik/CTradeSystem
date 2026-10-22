"""미국 수입 — 상대국 × 품목(HS) 월별, 미국 Census 국제무역 API(CENSUS_KEY). 한국산 vs 중국산 등 미국 시장 점유 경쟁 판독용.

  raw/census/us_imports/<품목>/<받은날>.json   품목별 원본(2010-01 ~ 최신, 한 요청에 전 기간·전 상대국)
  derived/trade/us_imports.parquet             장형: month(월말)·partner(Census 국가 코드, '-' = 전체)·cmd·value(달러, 일반수입)
2026-10-22: Census 가 키 필요로 바뀜 → 사용자가 CENSUS_KEY 등록. 처음엔 UN Comtrade 공개 미리보기(raw/comtrade/, 2010~2012 일부만 받다 중단)를 썼으나
Census 가 2010-01~ 전부를 품목당 한 번에 줘서(7초) 일원화.
"""
import json
import os
from datetime import date
from typing import Dict

import pandas as pd

from . import ledger, store
from .store import log

URL = "https://api.census.gov/data/timeseries/intltrade/imports/hs"
# 상대국·품목은 관계 원장(reference/relations_ledger.json, import_market "us"·product 노드)에서 읽음 — 2026-10-22 이동, 값 동일
PARTNERS: Dict[str, str] = ledger.market_partners("us")      # Census CTY_CODE → 이름('-' = 전체)
PRODUCTS: Dict[str, str] = ledger.products()                  # HS(2·4자리, TOTAL=전 품목) → 판독용 이름
START = "2010-01"


def _query(cmd: str) -> str:
    end = f"{date.today():%Y-%m}"
    q = [("get", "CTY_CODE,GEN_VAL_MO"), ("time", f"from {START} to {end}")]
    if cmd == "TOTAL":
        q.append(("I_COMMODITY", "-"))
    else:
        q += [("COMM_LVL", "HS2" if len(cmd) == 2 else "HS4"), ("I_COMMODITY", cmd)]
    q += [("CTY_CODE", c) for c in PARTNERS]
    q.append(("key", store.key_env("CENSUS_KEY")))
    from urllib.parse import urlencode
    return URL + "?" + urlencode(q)


def parse(raw: bytes, cmd: str) -> list:
    j = json.loads(raw)
    if not isinstance(j, list) or len(j) < 2:
        raise RuntimeError(f"Census 응답 이상: {str(raw[:200])}")
    head = j[0]
    ic, iv, it = head.index("CTY_CODE"), head.index("GEN_VAL_MO"), head.index("time")
    out = []
    for r in j[1:]:
        month = pd.Timestamp(r[it] + "-01") + pd.offsets.MonthEnd(0)
        out.append({"month": month, "partner": r[ic], "cmd": cmd, "value": float(r[iv] or 0)})
    return out


def run() -> bool:
    rows, fail = [], []
    for cmd in PRODUCTS:
        try:
            raw = store.get(_query(cmd), timeout=180)
            store.save_raw(f"census/us_imports/{cmd}/{date.today():%Y%m%d}.json", raw)
            rows += parse(raw, cmd)
        except Exception as e:   # noqa: BLE001
            fail.append(f"{cmd} {str(e)[:80]}")
    if not rows:
        log(f"[us_imports] 전부 실패: {fail}")
        return False
    df = pd.DataFrame(rows)
    os.makedirs(store.path("derived", "trade"), exist_ok=True)
    df.to_parquet(store.path("derived", "trade", "us_imports.parquet"))
    log(f"[us_imports] Census {len(df)}행, {df.month.min().date()}~{df.month.max().date()}, 품목 {df.cmd.nunique()}, 실패 {fail}")
    return not fail
