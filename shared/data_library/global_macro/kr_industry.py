"""한국 업종 원장 — 코스피 업종 지수(Creon, kr_equity 원천) 월말 종가 + 통계청 업종별 생산지수(KOSIS, 1990~).

  derived/kr_industry/sector_close.parquet   코스피 업종 지수 월말 종가(열 = U코드, + U001 코스피)
  derived/kr_industry/prod.parquet           KOSIS 광업제조업동향조사 생산지수 원지수(열 = KSIC 코드), 월(기간 끝 날짜)
대응표 SECTORS: 한국 업종 ↔ 미국 Ken French 49업종 ↔ 미국 FRED 업종 생산(industry.FUND_MAP 키) ↔ KOSIS 산업 코드.
(2026-10-22 3단계 사전등록 전에 고정, 같은 날 관계 원장 reference/relations_ledger.json 으로 이동 — 바꾸면 원장에서 valid_to/reason, PLAN.md 에 날짜·이유)
"""
import json
import os
import urllib.parse
from datetime import date
from typing import Dict, Optional

import pandas as pd

from shared.data_library.paths import data_vault_path

from . import ledger, store
from .store import log

KOSIS_LAG_DAYS = 35     # 산업활동동향은 다음 달 말 공표

# 코드: (이름, 미국 KF49, 미국 업종 생산(FUND_MAP 키), KOSIS KSIC) — 관계 원장(reference/relations_ledger.json)에서 읽음(2026-10-22 이동, 값 동일)
SECTORS: Dict[str, tuple] = ledger.kr_sectors()
MARKET = "U001"


def _creon_monthly(code: str) -> pd.Series:
    fp = data_vault_path("staging", "kr_equity", "raw", "creon", "index_1d", f"{code}_1d.csv")
    d = pd.read_csv(fp, dtype={"date": str})
    s = pd.Series(d["close"].astype(float).values, index=pd.to_datetime(d["date"], format="%Y%m%d")).sort_index()
    s = s[s > 0]
    m = s.groupby(s.index.to_period("M")).last()
    m.index = m.index.to_timestamp(how="end").normalize()
    # 마지막 달이 다 차지 않았으면(월말 전 마지막 거래일이 아님) 버린다 — 월말 종가가 아님
    last_day = s.index[-1]
    if last_day < (last_day + pd.offsets.BMonthEnd(0)).normalize():
        m = m.iloc[:-1]
    return m.rename(code)


def build_sectors() -> dict:
    cols = {c: _creon_monthly(c) for c in list(SECTORS) + [MARKET]}
    df = pd.DataFrame(cols)
    d = store.path("derived", "kr_industry")
    os.makedirs(d, exist_ok=True)
    df.to_parquet(os.path.join(d, "sector_close.parquet"))
    # 같은 값이 길게 이어지는 구간(지수 미산출·정지) 점검
    flat = {c: int((df[c].pct_change().abs() < 1e-12).sum()) for c in df.columns}
    info = {"sectors": len(SECTORS), "start": str(df.index[0].date()), "end": str(df.index[-1].date()), "flat_months": {k: v for k, v in flat.items() if v > 3}}
    log(f"[kr_industry] 업종 지수 {info}")
    return info


def fetch_kosis_production() -> dict:
    codes = sorted({v[3] for v in SECTORS.values() if v[3]})
    k = store.key_env("KOSIS_KEY")
    q = {"method": "getList", "apiKey": k, "itmId": "T10+", "objL1": "00+", "objL2": "+".join(codes) + "+", "format": "json",
         "jsonVD": "Y", "prdSe": "M", "startPrdDe": "198001", "endPrdDe": f"{date.today():%Y%m}", "orgId": "101", "tblId": "DT_1F02001"}
    raw = store.get("https://kosis.kr/openapi/Param/statisticsParameterData.do?" + urllib.parse.urlencode(q), timeout=180)
    store.save_raw(f"kosis/prod_T10/{date.today():%Y%m%d}.json", raw)
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise RuntimeError(f"KOSIS 응답 이상: {str(rows)[:200]}")
    recs = {}
    for r in rows:
        t = pd.Timestamp(f"{r['PRD_DE'][:4]}-{r['PRD_DE'][4:6]}-01") + pd.offsets.MonthEnd(0)
        recs.setdefault(r["C2"], {})[t] = float(r["DT"])
    df = pd.DataFrame(recs).sort_index()
    d = store.path("derived", "kr_industry")
    os.makedirs(d, exist_ok=True)
    df.to_parquet(os.path.join(d, "prod.parquet"))
    info = {"codes": list(df.columns), "start": str(df.index[0].date()), "end": str(df.index[-1].date()), "missing": sorted(set(codes) - set(df.columns))}
    log(f"[kr_industry] KOSIS 업종 생산지수 {info}")
    return info


def run() -> bool:
    info = {"sectors": build_sectors(), "kosis": fetch_kosis_production()}
    os.makedirs(store.path("quality"), exist_ok=True)
    with open(store.path("quality", "kr_industry_status.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return True
