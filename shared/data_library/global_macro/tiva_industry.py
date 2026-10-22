"""TiVA 산업 × 상대국 — 한국 산업(ISIC)의 부가가치가 어느 나라 최종수요에 담기는가(FFD_DVA). 업종 노출을 '총수출 품목 비중'이 아니라
'부가가치 기준(중간재 경유·제3국 우회 포함)'으로 재는 원천 = ICIO 레온티에프 승수를 OECD 가 미리 계산해 둔 값. 판독 엔진 전진 트랙 V 가 쓴다.

  raw/trade/tiva_industry/<받은날>.csv            원본(SDMX-CSV)
  derived/trade/tiva_industry.parquet              장형: measure(FFD_DVA·EXGR_DVA·EXGR)·activity(ISIC)·partner(TiVA 코드)·year·value
공표 시차: 2025 판(2025-11)에 2022 까지 → 판독 시점 t 에 쓸 수 있는 해 = t.year − 3 이하(보수적).
"""
from datetime import date
from typing import Dict, Optional

import pandas as pd

from . import ledger, store, trade

MEASURES = ["FFD_DVA", "EXGR_DVA", "EXGR"]
PUBLICATION_LAG_YEARS = 3


def activities() -> Dict[str, str]:
    """코스피 업종 → ISIC(TiVA) — 관계 원장 sector_isic."""
    return ledger._one("sector_isic")


def fetch() -> dict:
    acts = "+".join(sorted(set(activities().values())))
    partners = "+".join(sorted({v[1] for v in trade.PARTNERS.values()} | set(trade.EXTRA_TIVA)))
    url = trade.TIVA_URL + f"{'+'.join(MEASURES)}.KOR.{acts}.{partners}..?format=csvfile"
    raw = store.get(url, timeout=300)
    store.save_raw(f"trade/tiva_industry/{date.today():%Y%m%d}.csv", raw)
    import io
    d = pd.read_csv(io.BytesIO(raw))
    df = pd.DataFrame({"measure": d["MEASURE"], "activity": d["ACTIVITY"], "partner": d["COUNTERPART_AREA"], "year": d["TIME_PERIOD"].astype(int),
                       "value": d["OBS_VALUE"].astype(float) * (10.0 ** d["UNIT_MULT"].fillna(0).astype(int))})
    df.to_parquet(store.path("derived", "trade", "tiva_industry.parquet"), index=False)
    info = {"rows": len(df), "years": [int(df.year.min()), int(df.year.max())], "activities": sorted(df.activity.unique()), "partners": int(df.partner.nunique())}
    store.log(f"[tiva_industry] {info}")
    return info


def load() -> Optional[pd.DataFrame]:
    try:
        return pd.read_parquet(store.path("derived", "trade", "tiva_industry.parquet"))
    except Exception:
        return None


def va_exposure_by_year(df: Optional[pd.DataFrame] = None, measure: str = "FFD_DVA") -> Dict[int, Dict[str, Dict[str, float]]]:
    """{연도: {업종: {상대국 그룹: 비중}}} — 업종의 산업(ISIC) FFD_DVA 중 그룹(구성국 TiVA 코드 합)의 몫 / 세계(W). reading.sector_exposure 와 같은 모양."""
    df = load() if df is None else df
    if df is None or not len(df):
        return {}
    acts = activities()
    tiva_of = {e: v[1] for e, v in trade.PARTNERS.items()}                  # ECOS 코드 → TiVA 코드(HK→HKG)
    groups = {g: [tiva_of[c] for c in cs if c in tiva_of] for g, (cs, _) in ledger.groups("reading").items()}
    m = df[df.measure == measure].pivot_table(index=["year", "activity"], columns="partner", values="value", aggfunc="sum")
    out: Dict[int, dict] = {}
    for (yr, act), row in m.iterrows():
        w = row.get("W")
        if w is None or pd.isna(w) or w <= 0:
            continue
        for s, a in acts.items():
            if a != act:
                continue
            out.setdefault(int(yr), {})[s] = {g: float(row.reindex(cs).fillna(0).sum() / w) for g, cs in groups.items()}
    return out


def year_available(t, years) -> Optional[int]:
    """시점 t 에 쓸 수 있는 가장 최근 TiVA 연도(공표 시차 3년)."""
    ok = [y for y in years if y <= pd.Timestamp(t).year - PUBLICATION_LAG_YEARS]
    return max(ok) if ok else None
