"""무역 연결 원장 — 한국 상대국별 수출입(ECOS, 월), OECD 부가가치 무역(TiVA, 연), 상대국 경기선행지수(FRED·OECD), 대만 지수.

  derived/trade/kr_exp.parquet       한국 상대국별 수출(천 달러, 월, 기간 끝 날짜) — 열 = ECOS 국가 코드 + EUR(대륙 유럽)
  derived/trade/kr_imp.parquet       같은 형식, 수입
  derived/trade/tiva.parquet         TiVA 장형: measure(EXGR 총수출 / FFD_DVA 해외 최종수요에 담긴 국내 부가가치)·ref(KOR/TWN)·partner·year·value
  derived/trade/partner_cli.parquet  상대국 OECD 선행지수(월, 기간 끝) — 열 = TiVA 국가 코드
  derived/trade/twii.parquet         대만 가권지수 월말 종가(Yahoo ^TWII)
대응표 PARTNERS: ECOS 코드 ↔ TiVA 코드 ↔ 선행지수(없으면 None — 가중 합성에서 빠지고 남은 가중치를 다시 100으로).
(2026-10-22 4단계 무역 사전등록 전에 고정)
"""
import csv
import io
import json
import os
from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import ledger, sources, store
from .store import log

TIVA_URL = "https://sdmx.oecd.org/sti-public/rest/data/OECD.STI.PIE,DSD_TIVA_MAINLV@DF_MAINLV,1.1/"
CLI_LAG_DAYS = 20
TRADE_LAG_DAYS = 15

# 대응표는 관계 원장(reference/relations_ledger.json, ledger.py) 에서 읽는다 — 2026-10-22 코드 리터럴에서 이동(값 동일, 테스트 fixture 로 고정)
PARTNERS: Dict[str, Tuple[str, str, Optional[str]]] = ledger.trade_partners()      # ECOS 코드: (이름, TiVA 코드, 선행지수 FRED id 또는 None)
EXTRA_TIVA: List[str] = ledger.tiva_aggregates()                                   # 집계·추가(스페인 선행지수 있음)
EXTRA_CLI: Dict[str, str] = ledger.aggregate_cli()


def _ecos_partner(kind: str, code: str) -> pd.Series:
    k = store.key_env("ECOS_KEY")
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{k}/json/kr/1/100000/901Y121/M/196001/{date.today():%Y%m}/{kind}/{code}"
    raw = store.get(url)
    rows = sources.month_start_to_end(sources.parse_ecos(raw))
    store.save_raw(f"trade/ecos_{kind}_{code}/{date.today():%Y%m%d}.json", raw)
    s = pd.Series(dict(rows), dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def _ecos_europe(kind: str) -> pd.Series:
    k = store.key_env("ECOS_KEY")
    raw = store.get(f"https://ecos.bok.or.kr/api/StatisticSearch/{k}/json/kr/1/100000/901Y119/M/200001/{date.today():%Y%m}/{kind}/30")
    rows = sources.month_start_to_end(sources.parse_ecos(raw))
    store.save_raw(f"trade/ecos_{kind}_EUR/{date.today():%Y%m%d}.json", raw)
    s = pd.Series(dict(rows), dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def fetch_ecos() -> dict:
    out = {}
    for kind, fn in (("T002", "kr_exp"), ("T004", "kr_imp")):
        cols, miss = {}, []
        for code in PARTNERS:
            try:
                cols[code] = _ecos_partner(kind, code)
            except Exception as e:   # noqa: BLE001
                miss.append(f"{code} {str(e)[:50]}")
        try:
            cols["EUR"] = _ecos_europe(kind)
        except Exception as e:   # noqa: BLE001
            miss.append(f"EUR {str(e)[:50]}")
        df = pd.DataFrame(cols).sort_index()
        df.to_parquet(store.path("derived", "trade", f"{fn}.parquet"))
        out[fn] = {"cols": len(cols), "start": str(df.index[0].date()), "end": str(df.index[-1].date()), "missing": miss}
    return out


def parse_tiva_csv(text: str) -> pd.DataFrame:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        if not r.get("OBS_VALUE"):
            continue
        rows.append({"measure": r["MEASURE"], "ref": r["REF_AREA"], "partner": r["COUNTERPART_AREA"], "year": int(r["TIME_PERIOD"]),
                     "value": float(r["OBS_VALUE"]) * 10 ** int(r.get("UNIT_MULT") or 0)})
    if not rows:
        raise ValueError("TiVA 응답에 값 없음")
    return pd.DataFrame(rows)


def fetch_tiva() -> dict:
    partners = "+".join(sorted({v[1] for v in PARTNERS.values()} | set(EXTRA_TIVA)))
    raw = store.get(TIVA_URL + f"FFD_DVA+EXGR.KOR+TWN._T.{partners}..?format=csvfile", timeout=300)
    store.save_raw(f"trade/tiva/{date.today():%Y%m%d}.csv", raw)
    df = parse_tiva_csv(raw.decode("utf-8"))
    df.to_parquet(store.path("derived", "trade", "tiva.parquet"))
    return {"rows": len(df), "years": [int(df.year.min()), int(df.year.max())], "partners": int(df.partner.nunique())}


def fetch_cli() -> dict:
    ids = {v[1]: v[2] for v in PARTNERS.values() if v[2]}
    ids.update(EXTRA_CLI)
    ids.pop("HKG", None)
    cols, miss = {}, []
    for tcode, fid in ids.items():
        try:
            rows = sources.fetch_fred({"key": f"cli_{tcode}", "sid": fid, "freq": "M"})
            s = pd.Series(dict(rows), dtype=float)
            s.index = pd.to_datetime(s.index)
            cols[tcode] = s.sort_index()
        except Exception as e:   # noqa: BLE001
            miss.append(f"{tcode} {str(e)[:50]}")
    df = pd.DataFrame(cols)
    df.to_parquet(store.path("derived", "trade", "partner_cli.parquet"))
    return {"cli": list(df.columns), "missing": miss, "end": str(df.index[-1].date())}


def fetch_twii() -> dict:
    rows = sources.fetch_yahoo_sym("^TWII", "tw_twii")
    s = pd.Series(dict(rows), dtype=float)
    s.index = pd.to_datetime(s.index)
    m = s.groupby(s.index.to_period("M")).last()
    m.index = m.index.to_timestamp(how="end").normalize()
    last = s.index[-1]
    if last < (last + pd.offsets.BMonthEnd(0)).normalize():
        m = m.iloc[:-1]                     # 다 차지 않은 마지막 달 제외
    m.to_frame("twii").to_parquet(store.path("derived", "trade", "twii.parquet"))
    return {"start": str(m.index[0].date()), "end": str(m.index[-1].date())}


def run() -> bool:
    os.makedirs(store.path("derived", "trade"), exist_ok=True)
    info = {}
    from . import tiva_industry
    for nm, fn in (("ecos", fetch_ecos), ("tiva", fetch_tiva), ("tiva_industry", tiva_industry.fetch), ("cli", fetch_cli), ("twii", fetch_twii)):
        try:
            info[nm] = fn()
            log(f"[trade] {nm} {info[nm]}")
        except Exception as e:   # noqa: BLE001
            info[nm] = {"error": str(e)[:300]}
            log(f"[trade] {nm} 실패: {e}")
    with open(store.path("quality", "trade_status.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return all("error" not in v for v in info.values())
