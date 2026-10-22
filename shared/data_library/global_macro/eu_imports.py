"""EU 수입 — 상대국 × 품목(HS) 월별, Eurostat Comext DS-045409(키 불필요). 한국산 vs 중국산 등 유럽 시장 점유 경쟁 판독용(미국 Census 와 같은 모양).

  raw/eurostat/eu_imports/<품목>/<받은날>.csv   품목별 원본(2010-01 ~ 최신, 한 요청에 전 기간·전 상대국, SDMX-CSV)
  derived/trade/eu_imports.parquet              장형: month(월말)·partner(ISO2, '-' = 역외 전체 EXT_EU27_2020)·cmd·value(유로, 수입)
키 순서 freq.reporter.partner.product.flow.indicators — reporter EU27_2020, flow 1(수입), VALUE_IN_EUROS. 분모는 역외 수입 합계(역내 거래 제외).
공표 시차: 월 종료 후 약 7주(2026-10-22 기준 최신 2026-08) → 판독 시점 규칙은 미국과 같은 t−2.
"""
import io
import os
from datetime import date
from typing import Dict

import pandas as pd

from . import store
from .store import log
from .us_imports import PRODUCTS  # 품목표는 미국과 같은 16개(비교 가능하도록)

URL = "https://ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1/data/DS-045409"
EXT = "EXT_EU27_2020"
PARTNERS: Dict[str, str] = {"KR": "한국", "CN": "중국", "JP": "일본", "US": "미국", "TW": "대만", "VN": "베트남", "MX": "멕시코", "-": "역외 전체"}
START = "2010-01"


def _query(cmd: str) -> str:
    partners = "+".join(EXT if p == "-" else p for p in PARTNERS)
    return f"{URL}/M.EU27_2020.{partners}.{cmd}.1.VALUE_IN_EUROS?startPeriod={START}&format=SDMX-CSV"


def parse(raw: bytes, cmd: str) -> list:
    df = pd.read_csv(io.BytesIO(raw))
    need = {"partner", "product", "TIME_PERIOD", "OBS_VALUE"}
    if not need <= set(df.columns):
        raise RuntimeError(f"Comext 응답 이상: {str(raw[:200])}")
    out = []
    for r in df.itertuples(index=False):
        if str(r.product) != cmd:
            continue
        month = pd.Timestamp(str(r.TIME_PERIOD) + "-01") + pd.offsets.MonthEnd(0)
        partner = "-" if r.partner == EXT else str(r.partner)
        out.append({"month": month, "partner": partner, "cmd": cmd, "value": float(r.OBS_VALUE if pd.notna(r.OBS_VALUE) else 0)})
    return out


def run() -> bool:
    rows, fail = [], []
    for cmd in PRODUCTS:
        try:
            raw = store.get(_query(cmd), timeout=240)
            store.save_raw(f"eurostat/eu_imports/{cmd}/{date.today():%Y%m%d}.csv", raw)
            rows += parse(raw, cmd)
        except Exception as e:   # noqa: BLE001
            fail.append(f"{cmd} {str(e)[:80]}")
    if not rows:
        log(f"[eu_imports] 전부 실패: {fail}")
        return False
    df = pd.DataFrame(rows)
    os.makedirs(store.path("derived", "trade"), exist_ok=True)
    df.to_parquet(store.path("derived", "trade", "eu_imports.parquet"))
    log(f"[eu_imports] Comext {len(df)}행, {df.month.min().date()}~{df.month.max().date()}, 품목 {df.cmd.nunique()}, 실패 {fail}")
    return not fail
