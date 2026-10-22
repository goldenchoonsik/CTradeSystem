"""판독 엔진 확장 — 사전등록 원안(reading.py, 해시 고정)을 건드리지 않고 덧붙이는 전진 확인 트랙.

  R5-CE  EU 시장 경쟁 성분만(z(C^EU)) — PREREGISTRATION_stage4_eu.md E2 '작게 실험' → 조치 규칙대로 2026-10-31~ 전진 기록
시장 공통 점유 계산(competition_from)은 reading.competition 과 같은 식이고 시장·국가 코드만 매개변수다.
"""
from typing import Dict, Optional

import numpy as np
import pandas as pd

from . import reading as R
from . import store

EU_KR, EU_CN = "KR", "CN"


def load12(name: str) -> Optional[pd.DataFrame]:
    """derived/trade/<name>.parquet(장형) → (품목, 상대국) 열의 12개월 합. 없으면 None."""
    fp = store.path("derived", "trade", f"{name}.parquet")
    try:
        df = pd.read_parquet(fp)
    except Exception:
        return None
    piv = df.pivot_table(index="month", columns=["cmd", "partner"], values="value", aggfunc="sum").sort_index()
    return piv.rolling(12, min_periods=12).sum()


def competition_from(m12: Optional[pd.DataFrame], t, kr: str, cn: str) -> Dict[str, dict]:
    """시점 t(t−2 자료)의 품목별 점유 변화 — reading.competition 과 같은 규칙. 'us_imports_12m' 키는 R.sectors 가중치 호환용(시장 무관 '수입액')."""
    if m12 is None:
        return {}
    L2 = R._me(pd.Timestamp(t) - pd.offsets.MonthEnd(2))
    base = R._me(L2 - pd.DateOffset(months=12))
    out = {}
    for cmd in sorted({c for c, _ in m12.columns}):
        if (cmd, "-") not in m12:
            continue
        tot = m12[(cmd, "-")]
        rec = {}
        for code, nm in ((kr, "kr"), (cn, "cn")):
            if (cmd, code) not in m12:
                continue
            sh = m12[(cmd, code)] / tot * 100
            a, b = sh.get(L2), sh.get(base)
            if a is None or b is None or pd.isna(a) or pd.isna(b):
                continue
            dlt = float(a - b)
            rec[nm] = {"share": float(a), "delta": dlt, "state": "확대" if dlt >= R.SHARE_BAND else "잠식" if dlt <= -R.SHARE_BAND else "유지"}
        if rec:
            rec["us_imports_12m"] = float(tot.get(L2, np.nan))
            rec["month"] = str(L2.date())
            out[cmd] = rec
    return out


def sector_ce(sectors: dict, comp_eu: Dict[str, dict]) -> Dict[str, dict]:
    """업종별 EU 경쟁 성분 C^EU(업종 품목의 한국 점유 변화, EU 수입액 가중) → 업종 간 z = score_CE. 3개 미만이면 None."""
    c = {}
    for s in sectors:
        cv, w = 0.0, 0.0
        for cmd in R.SECTOR_US.get(s, []):
            r = comp_eu.get(cmd, {})
            if "kr" in r and not pd.isna(r.get("us_imports_12m", np.nan)):
                cv += r["kr"]["delta"] * r["us_imports_12m"]
                w += r["us_imports_12m"]
        c[s] = (cv / w) if w > 0 else None
    ser = pd.Series({k: v for k, v in c.items() if v is not None}, dtype=float)
    z = (ser - ser.mean()) / ser.std() if len(ser) >= 3 and ser.std() > 0 else None
    out = {}
    for s in sectors:
        zs = float(z[s]) if z is not None and s in z.index else None
        out[s] = {"C_EU": c[s], "score_CE": zs, "reading_CE": None if zs is None else "우호" if zs > 0 else "비우호" if zs < 0 else "중립"}
    return out


def attach_ce(r: dict, eu12: Optional[pd.DataFrame]) -> dict:
    """read_at 결과 r 에 R5-CE 필드를 덧붙인다(원안 필드는 그대로)."""
    if eu12 is None or not r.get("sectors"):
        return r
    comp = competition_from(eu12, r["t"], EU_KR, EU_CN)
    for s, v in sector_ce(r["sectors"], comp).items():
        r["sectors"][s].update(v)
    r["competition_eu"] = comp
    return r
