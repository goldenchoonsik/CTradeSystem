"""판독 엔진 — PREREGISTRATION_reading_engine.md 정의 그대로. 모든 판독은 시점 t(한국 월말)에 공개된 자료만 쓴다.

  R1 연결 상태   상대국 경기 → 그 나라로의 한국 수출, 최근 60개월 창의 최대 상관(선행 0~9개월) → 강함/약함/끊김
  R2 수요 판독   활성 연결로 6개월 뒤 대상국 수출 전년비가 지금보다 높아질지(가속)/낮아질지(감속)
  R3 총수출 판독  R2 를 전년 수출 비중으로 가중 합
  R4 경쟁 판독   미국 수입 중 한국산 점유의 최근 12개월 변화 → 확대/유지/잠식(중국산도 표시)
  R5 업종 판독   업종 품목의 상대국 노출 × R2 수요 충격 + 미국 시장 점유 변화 → 우호/비우호
  R5-D          수요 충격 성분만(z(D))으로 읽는 변형 — 과거 재현 결과를 보고 고른 것이라(사후 선택) 판정은
                FORWARD_FROM 이후 전진 기록으로만 한다 (PREREGISTRATION_reading_engine_D.md, 2026-10-22 고정)
"""
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import panel as gp
from . import store

WINDOW, LAGS, H = 60, range(0, 10), 6
STRONG, WEAK = 0.5, 0.25
SHARE_BAND = 0.3                       # %p
# R5-D 전진 확인: 이 달(월말) 이후 판독만 전진 기록(reading/forward_log.jsonl, append-only)으로 보관·채점한다.
FORWARD_FROM = pd.Timestamp("2026-10-31")
FORWARD_MIN_MONTHS, FORWARD_MAX_MONTHS = 24, 36         # 채점 가능한 달 수 — 24 에 1차 판정, 보류면 36 에 최종
# 상대국 그룹: ECOS 국가 코드 → 선행지수(TiVA 코드)
PARTNERS: Dict[str, tuple] = {
    "중국+홍콩": (["CN", "HK"], "CHN"), "미국": (["US"], "USA"), "유럽": (["DE", "FR", "IT", "GB", "NL", "PL"], "DEU"),
    "일본": (["JP"], "JPN"), "인도": (["IN"], "IND"), "멕시코": (["MX"], "MEX"), "인도네시아": (["ID"], "IDN"),
    "호주": (["AU"], "AUS"), "캐나다": (["CA"], "CAN"), "튀르키예": (["TR"], "TUR"), "브라질": (["BR"], "BRA"),
}
# 코스피 업종 ↔ 한국 수출 품목(HS 4자리 접두, 관세청) — 제외 목록은 다른 업종으로 감
SECTOR_HS: Dict[str, dict] = {
    "U013": {"name": "전기·전자", "include": ["85", "8471", "8473", "9013"], "exclude": []},
    "U015": {"name": "운송장비·부품", "include": ["87", "89"], "exclude": []},
    "U008": {"name": "화학", "include": ["27", "29", "33", "39", "40"], "exclude": []},
    "U011": {"name": "금속", "include": ["72", "73"], "exclude": []},
    "U012": {"name": "기계·장비", "include": ["84"], "exclude": ["8471", "8473"]},
    "U009": {"name": "제약", "include": ["30"], "exclude": []},
    "U014": {"name": "의료·정밀기기", "include": ["90"], "exclude": ["9013"]},
}
# 업종 ↔ 미국 수입 품목(Census, us_imports.PRODUCTS 키) — 미국 시장 경쟁 성분
SECTOR_US: Dict[str, List[str]] = {"U013": ["8542", "8471", "8517", "8528", "9013", "8507"], "U015": ["8703", "8708", "89"],
                                   "U008": ["39", "2710"], "U011": ["72"], "U012": ["84"], "U009": ["30"]}


def _hs_in(hs4: str, spec: dict) -> bool:
    return any(hs4.startswith(p) for p in spec["include"]) and not any(hs4.startswith(p) for p in spec["exclude"])


def _me(t) -> pd.Timestamp:
    return (pd.Timestamp(t) + pd.offsets.MonthEnd(0)).normalize()


class Data:
    """엔진 입력(한 번 읽어 두고 여러 시점에 재사용)."""

    def __init__(self):
        tr = lambda n: pd.read_parquet(store.path("derived", "trade", f"{n}.parquet"))
        ex = tr("kr_exp")
        self.cli = tr("partner_cli")
        self.exp = pd.DataFrame({g: ex[[c for c in cs if c in ex]].sum(axis=1, min_count=1) for g, (cs, _) in PARTNERS.items()})
        tot = gp.load("kr_exp_total")
        tot.index = tot.index.to_period("M").to_timestamp(how="end").normalize()
        self.exp_total = tot
        self.y = self._yoy3(self.exp)
        self.y_total = self._yoy3(self.exp_total.to_frame("total"))["total"]
        self.x = pd.DataFrame({g: self.cli[c] - self.cli[c].shift(6) for g, (_, c) in PARTNERS.items() if c in self.cli})
        try:
            self.items = tr("kr_exp_item")
            self.items_world = tr("kr_exp_item_world")
        except Exception:
            self.items = self.items_world = None
        try:
            ui = tr("us_imports")
            piv = ui.pivot_table(index="month", columns=["cmd", "partner"], values="value", aggfunc="sum").sort_index()
            self.us12 = piv.rolling(12, min_periods=12).sum()
        except Exception:
            self.us12 = None
        sc = pd.read_parquet(store.path("derived", "kr_industry", "sector_close.parquet"))
        self.close = sc

    @staticmethod
    def _yoy3(df: pd.DataFrame) -> pd.DataFrame:
        s = df.rolling(3, min_periods=3).sum()
        return np.log(s / s.shift(12))


# ---------------------------------------------------------------- R1·R2
def link(D: Data, g: str, t) -> Optional[dict]:
    """상대국 g 의 연결 상태 — 데이터월 ≤ t−1 의 최근 60개월."""
    L = _me(pd.Timestamp(t) - pd.offsets.MonthEnd(1))
    if g not in D.x:
        return None
    y = D.y[g].loc[:L].dropna().iloc[-WINDOW:]
    if len(y) < 36:
        return None
    best = None
    for k in LAGS:
        xk = D.x[g].shift(k).reindex(y.index)
        d = pd.DataFrame({"y": y, "x": xk}).dropna()
        if len(d) < 36 or d.x.std() == 0:
            continue
        r = d.y.corr(d.x)
        if best is None or r > best[1]:
            b, a = np.polyfit(d.x, d.y, 1)
            best = (k, float(r), float(a), float(b))
    if best is None:
        return None
    k, r, a, b = best
    state = "강함" if r >= STRONG else "약함" if r >= WEAK else "끊김"
    return {"k": k, "rho": r, "alpha": a, "beta": b, "state": state, "last_month": str(L.date())}


def demand(D: Data, g: str, t, lk: Optional[dict] = None) -> Optional[dict]:
    lk = lk or link(D, g, t)
    if not lk or lk["state"] == "끊김":
        return None
    L = _me(pd.Timestamp(t) - pd.offsets.MonthEnd(1))
    target = _me(pd.Timestamp(t) + pd.offsets.MonthEnd(H))
    src = _me(target - pd.DateOffset(months=lk["k"]))
    xs = D.x[g].loc[:L].dropna()
    if xs.empty:
        return None
    xv = float(D.x[g].get(src)) if src <= L and not pd.isna(D.x[g].get(src, np.nan)) else float(xs.iloc[-1])
    ylast = D.y[g].get(L)
    if ylast is None or pd.isna(ylast):
        return None
    yhat = lk["alpha"] + lk["beta"] * xv
    return {"yhat": yhat, "ylast": float(ylast), "delta": yhat - float(ylast), "call": "가속" if yhat > ylast else "감속",
            "target": str(target.date()), "x_used": xv, "x_month": str((src if src <= L else xs.index[-1]).date())}


def total_reading(D: Data, t, dem: Dict[str, dict]) -> Optional[dict]:
    L = _me(pd.Timestamp(t) - pd.offsets.MonthEnd(1))
    prev = D.exp.loc[str(L.year - 1)].sum()
    w = {g: float(prev.get(g, 0)) for g in dem}
    tot = sum(w.values())
    if not dem or tot <= 0:
        return None
    delta = sum(w[g] / tot * dem[g]["delta"] for g in dem)
    return {"delta": delta, "call": "가속" if delta > 0 else "감속", "ylast": float(D.y_total.get(L, np.nan))}


# ---------------------------------------------------------------- R4
def competition(D: Data, t) -> Dict[str, dict]:
    if D.us12 is None:
        return {}
    L2 = _me(pd.Timestamp(t) - pd.offsets.MonthEnd(2))
    base = _me(L2 - pd.DateOffset(months=12))
    out = {}
    for cmd in sorted({c for c, _ in D.us12.columns}):
        try:
            tot = D.us12[(cmd, "-")]
        except KeyError:
            continue
        rec = {}
        for code, nm in (("5800", "kr"), ("5700", "cn")):
            if (cmd, code) not in D.us12:
                continue
            sh = D.us12[(cmd, code)] / tot * 100
            a, b = sh.get(L2), sh.get(base)
            if a is None or b is None or pd.isna(a) or pd.isna(b):
                continue
            dlt = float(a - b)
            rec[nm] = {"share": float(a), "delta": dlt, "state": "확대" if dlt >= SHARE_BAND else "잠식" if dlt <= -SHARE_BAND else "유지"}
        if rec:
            rec["us_imports_12m"] = float(tot.get(L2, np.nan))
            rec["month"] = str(L2.date())
            out[cmd] = rec
    return out


# ---------------------------------------------------------------- R5
def sector_exposure(D: Data, year: int) -> Dict[str, Dict[str, float]]:
    """업종 s 의 품목 수출 중 상대국 g 비중(그 해, 세계 합계 대비)."""
    if D.items is None:
        return {}
    a = D.items[D.items.month.dt.year == year]
    w = D.items_world[D.items_world.month.dt.year == year]
    out = {}
    for s, spec in SECTOR_HS.items():
        wa = w[w.hs4.map(lambda h: _hs_in(h, spec))]["exp"].sum()
        if wa <= 0:
            continue
        aa = a[a.hs4.map(lambda h: _hs_in(h, spec))]
        byc = aa.groupby("country")["exp"].sum()
        out[s] = {g: float(byc.reindex(cs).fillna(0).sum() / wa) for g, (cs, _) in PARTNERS.items()}
    return out


def sectors(D: Data, t, dem: Dict[str, dict], comp: Dict[str, dict], expo_cache: dict) -> Dict[str, dict]:
    L = _me(pd.Timestamp(t) - pd.offsets.MonthEnd(1))
    yr = L.year - 1
    if yr not in expo_cache:
        expo_cache[yr] = sector_exposure(D, yr)
    expo = expo_cache[yr]
    raw = {}
    for s, spec in SECTOR_HS.items():
        if s not in expo:
            continue
        Dv = sum(expo[s].get(g, 0) * dem[g]["delta"] for g in dem)
        Cv, wsum = 0.0, 0.0
        for cmd in SECTOR_US.get(s, []):
            c = comp.get(cmd, {})
            if "kr" in c and not pd.isna(c.get("us_imports_12m", np.nan)):
                Cv += c["kr"]["delta"] * c["us_imports_12m"]
                wsum += c["us_imports_12m"]
        raw[s] = {"name": spec["name"], "D": Dv, "C": (Cv / wsum) if wsum > 0 else None}
    if not raw:
        return {}
    df = pd.DataFrame(raw).T
    z = lambda c: (df[c].astype(float) - df[c].astype(float).mean()) / df[c].astype(float).std() if df[c].notna().sum() >= 3 and df[c].astype(float).std() > 0 else pd.Series(0.0, index=df.index)
    zD = z("D")
    zC = z("C") if df["C"].notna().sum() >= 3 else pd.Series(0.0, index=df.index)
    lab = lambda v: "우호" if v > 0 else "비우호" if v < 0 else "중립"
    for s in raw:
        sc = float(zD[s] + (zC[s] if pd.notna(df.loc[s, "C"]) else 0.0))
        sd = float(zD[s])                                   # R5-D: 수요 충격 성분만
        raw[s].update(score=sc, reading=lab(sc), score_D=sd, reading_D=lab(sd))
    return raw


def read_at(D: Data, t, expo_cache: Optional[dict] = None) -> dict:
    """시점 t 의 전체 판독."""
    expo_cache = {} if expo_cache is None else expo_cache
    links, dem = {}, {}
    for g in PARTNERS:
        lk = link(D, g, t)
        if lk:
            links[g] = lk
            d = demand(D, g, t, lk)
            if d:
                dem[g] = d
    comp = competition(D, t)
    return {"t": str(_me(t).date()), "links": links, "demand": dem, "total": total_reading(D, t, dem), "competition": comp,
            "sectors": sectors(D, t, dem, comp, expo_cache)}
