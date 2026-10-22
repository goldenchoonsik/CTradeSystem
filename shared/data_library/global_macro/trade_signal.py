"""수출 가중 상대국 경기선행지수(wCLI) — 4단계 무역 사전등록(PREREGISTRATION_stage4_trade.md) 정의 그대로.

가중치는 t 가 속한 해의 '전년' 비중(실시간): 한국 총수출 = ECOS, 대만 총수출·부가가치 = TiVA(2023~ 는 2022, ~1995 는 1995).
선행지수 없는 상대국은 빠지고 남은 가중치를 합 1 로. 홍콩 = 중국 선행지수.
"""
import os
from typing import Dict

import numpy as np
import pandas as pd

from . import panel as gp
from . import store, trade

CLI_OF = {e: (v[1] if v[1] != "HKG" else "CHN") for e, v in trade.PARTNERS.items() if v[2]}   # ECOS 코드 → 선행지수 국가(TiVA 코드)


def _load(name: str) -> pd.DataFrame:
    return pd.read_parquet(store.path("derived", "trade", f"{name}.parquet"))


def cli_change(at: pd.DatetimeIndex, months: int = 6) -> pd.DataFrame:
    """각 상대국 선행지수의 6개월 변화 — 월말 at 에 공개된 값 기준(발표 시차 20일)."""
    cli = _load("partner_cli")
    # 달력 기준 6개월: 연속 월말 격자에서 계산한 뒤 at 으로 고른다(at 이 띄엄띄엄이어도 정확)
    grid = pd.date_range(at.min() - pd.DateOffset(months=months + 1), at.max(), freq="ME")
    out = {}
    for c in cli.columns:
        v, _ = gp.point_in_time(cli[c].dropna(), trade.CLI_LAG_DAYS, grid)
        out[c] = (v - v.shift(months)).reindex(at.normalize() + pd.offsets.MonthEnd(0)).values
    return pd.DataFrame(out, index=at)


def weights_kr_gross() -> pd.DataFrame:
    """연도 y 행 = y년 1~12월 한국 총수출 비중(선행지수 국가 기준, 합 1) → y+1년 신호에 씀."""
    ex = _load("kr_exp")
    yr = ex.groupby(ex.index.year).sum(min_count=12)
    agg: Dict[str, pd.Series] = {}
    for e, c in CLI_OF.items():
        if e in yr:
            agg[c] = agg.get(c, 0) + yr[e].fillna(0)
    w = pd.DataFrame(agg)
    return w.div(w.sum(axis=1), axis=0)


def weights_tiva(ref: str, measure: str) -> pd.DataFrame:
    t = _load("tiva")
    t = t[(t.ref == ref) & (t.measure == measure)]
    p = t.pivot_table(index="year", columns="partner", values="value", aggfunc="sum")
    cols = {}
    for c in set(CLI_OF.values()) | set(trade.EXTRA_CLI):
        s = p[c] if c in p else 0
        if c == "CHN" and "HKG" in p:
            s = s + p["HKG"]
        if isinstance(s, pd.Series):
            cols[c] = s
    w = pd.DataFrame(cols).fillna(0)
    return w.div(w.sum(axis=1), axis=0)


def weighted_cli(w_by_year: pd.DataFrame, at: pd.DatetimeIndex) -> pd.DataFrame:
    """wCLI(t) = Σ_p w_p(y−1) × 6개월 변화_p(t). 반환: total + 상대국별 기여."""
    ch = cli_change(at)
    first, last = int(w_by_year.index.min()), int(w_by_year.index.max())
    rows = []
    for t in at:
        y = min(max(t.year - 1, first), last)
        w = w_by_year.loc[y].dropna()
        x = ch.loc[t, [c for c in w.index if c in ch.columns]]
        ok = x.notna()
        if not ok.any():
            rows.append(pd.Series(dtype=float, name=t))
            continue
        ww = w[ok.index[ok]]
        ww = ww / ww.sum()
        contrib = ww * x[ok]
        rows.append(contrib.rename(t))
    c = pd.DataFrame(rows).reindex(at)
    c["total"] = c.sum(axis=1, min_count=1)
    return c


def exposure_shares(ref: str) -> Dict[str, pd.DataFrame]:
    """TiVA 상대 지역별 비중(%, 연): 총수출 vs 해외 최종수요 부가가치."""
    t = _load("tiva")
    t = t[t.ref == ref]
    groups = {"중국+홍콩": ["CHN", "HKG"], "미국": ["USA"], "유럽(EU27)": ["EU27_2020"], "일본": ["JPN"], "아세안": ["ASEAN"], "인도": ["IND"]}
    out = {}
    for m in ("EXGR", "FFD_DVA"):
        p = t[t.measure == m].pivot_table(index="year", columns="partner", values="value", aggfunc="sum")
        W = p["W"]
        df = pd.DataFrame({g: p[[c for c in cs if c in p]].sum(axis=1) / W * 100 for g, cs in groups.items()})
        df["기타"] = 100 - df.sum(axis=1)
        out[m] = df.round(2)
    return out


def sensitivity(lags=range(0, 13)) -> list:
    """상대국 선행지수 6개월 변화(데이터월) → 그 나라로의 한국 수출 전년비(3개월 합) — 최대 상관 시차·1σ당 탄력성(%)."""
    ex = _load("kr_exp")
    cli = _load("partner_cli")
    pairs = [("중국+홍콩", ["CN", "HK"], "CHN"), ("미국", ["US"], "USA"), ("유럽", ["EUR"], "DEU"), ("일본", ["JP"], "JPN"),
             ("인도", ["IN"], "IND"), ("인도네시아", ["ID"], "IDN"), ("멕시코", ["MX"], "MEX"), ("호주", ["AU"], "AUS"),
             ("캐나다", ["CA"], "CAN"), ("튀르키예", ["TR"], "TUR"), ("브라질", ["BR"], "BRA")]
    out = []
    for name, ecos, c in pairs:
        if c not in cli or not all(e in ex for e in ecos):
            continue
        s = ex[ecos].sum(axis=1, min_count=len(ecos)).rolling(3).sum()
        y = np.log(s / s.shift(12))
        x = cli[c] - cli[c].shift(6)
        rec = {"name": name, "cli": c}
        for per, (a, b) in {"full": ("1995", "2026"), "early": ("1995", "2009"), "late": ("2010", "2026")}.items():
            yy, xx = y.loc[a:b], x.loc[a:b]
            cs = {k: yy.corr(xx.shift(k)) for k in lags}
            cs = {k: v for k, v in cs.items() if not np.isnan(v)}
            if not cs:
                continue
            k = max(cs, key=lambda z: cs[z])
            d = pd.DataFrame({"y": yy, "x": xx.shift(k)}).dropna()
            beta = float(np.polyfit((d.x - d.x.mean()) / d.x.std(), d.y, 1)[0]) if len(d) > 24 else np.nan
            rec[per] = {"lag": int(k), "corr": round(float(cs[k]), 3), "beta_pct": round(beta * 100, 2)}
        out.append(rec)
    return out
