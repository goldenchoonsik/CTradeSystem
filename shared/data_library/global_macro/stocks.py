"""종목 단계(L3) — 업종 판독을 종목으로 내리는 계측. PREREGISTRATION_stage5_stocks.md.

  derived/kr_industry/stock_close_m.parquet    상장 보통주 월말 수정종가(kr_equity prices_1d 에서, 열 = A코드)
  업종 베타: 최근 36개월, 종목 월수익(코스피 초과) ~ 업종 지수 월수익(코스피 초과) 기울기 — 시점 t 까지 자료만.
수출 노출(회사별 수출 비중)은 구조화 원천이 없어 미확보 → 업종 베타를 '업종 수요 충격에 대한 민감도'의 대리로 쓴다(한계로 명시).
"""
import os
from typing import Dict, Optional

import numpy as np
import pandas as pd

from shared.data_library.paths import data_vault_path

from . import store

BETA_WINDOW, BETA_MIN = 36, 24
MIN_ADV_KRW, MIN_MCAP_KRW = 5e8, 5e10        # 유동성·시총 하한(그 시점): 일평균 거래대금 5억, 시총 500억


def monthly_close(rebuild: bool = False) -> pd.DataFrame:
    fp = store.path("derived", "kr_industry", "stock_close_m.parquet")
    src = data_vault_path("staging", "kr_equity", "derived", "prices_1d.parquet")
    if not rebuild and os.path.exists(fp) and os.path.getmtime(fp) >= os.path.getmtime(src):
        return pd.read_parquet(fp)
    p = pd.read_parquet(src, columns=["code", "date", "close"])
    p = p[p.close > 0]
    p["m"] = pd.to_datetime(p.date).dt.to_period("M")
    last = p.sort_values("date").groupby(["code", "m"]).close.last().unstack("code")
    last.index = last.index.to_timestamp(how="end").normalize()
    last.columns = [str(c) for c in last.columns]           # code 가 범주형이면 parquet 재읽기가 깨진다
    last.columns.name = None
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    last.to_parquet(fp)
    return last


def sector_beta(stock_m: pd.DataFrame, sector_m: pd.Series, market_m: pd.Series, t, window: int = BETA_WINDOW) -> pd.Series:
    """시점 t(월말)까지 최근 window 개월로 종목별 업종 베타(둘 다 코스피 초과 월 로그수익). 관측 BETA_MIN 미만은 NaN."""
    idx = stock_m.index[stock_m.index <= t][-(window + 1):]
    r = np.log(stock_m.loc[idx]).diff().iloc[1:]
    mk = np.log(market_m.reindex(idx)).diff().iloc[1:]
    sx = (np.log(sector_m.reindex(idx)).diff().iloc[1:] - mk)
    ex = r.sub(mk, axis=0)
    vx = sx.var()
    if not np.isfinite(vx) or vx == 0:
        return pd.Series(np.nan, index=stock_m.columns)
    n = ex.notna().sum()
    cov = ex.apply(lambda c: c.cov(sx))
    return (cov / vx).where(n >= BETA_MIN)


def latest_fundamentals(codes) -> pd.DataFrame:
    """kr_equity gold_1d 의 최신 행(시총·거래대금·수익률)."""
    g = pd.read_parquet(data_vault_path("staging", "kr_equity", "derived", "gold_1d.parquet"),
                        columns=["code", "date", "market_cap", "adv_20", "ret_120", "ret_20", "foreign_hold_pct"],
                        filters=[("code", "in", list(codes))])
    return g.sort_values("date").groupby("code").last()


def candidates(sector_readings: Dict[str, dict], top: int = 8) -> dict:
    """화면용 — 업종별(우호/비우호) 종목 후보: 업종 대응 종목 중 유동성·시총 하한 통과, 업종 베타 상위.
    반환 {sector: {name, reading, score, stocks:[{code,name,beta,mcap_bn,adv_bn,ret_6m,ret_1m}]}} — 현재 파악용(검정은 stage5)."""
    from . import kr_industry
    fp = store.path("derived", "kr_industry", "company_ksic.parquet")
    if not os.path.exists(fp):
        return {}
    ks = pd.read_parquet(fp)
    ks = ks[ks.sector.notna()]
    sm = monthly_close()
    sc = pd.read_parquet(store.path("derived", "kr_industry", "sector_close.parquet"))
    t = sm.index.max()
    fund = latest_fundamentals(ks.stock_code)
    out = {}
    for s, v in sector_readings.items():
        codes = [c for c in ks[ks.sector == s].stock_code if c in sm.columns and c in fund.index]
        if not codes or s not in sc:
            continue
        beta = sector_beta(sm[codes], sc[s], sc[kr_industry.MARKET], t)
        f = fund.loc[codes]
        ok = f[(f.adv_20 >= MIN_ADV_KRW) & (f.market_cap >= MIN_MCAP_KRW)].index
        b = beta.reindex(ok).dropna().sort_values(ascending=False)
        rows = []
        for c in b.index[:top]:
            nm = ks.loc[ks.stock_code == c, "name"].iloc[0]
            rows.append({"code": c, "name": nm, "beta": round(float(b[c]), 2), "mcap_bn": round(float(f.loc[c, "market_cap"]) / 1e9, 0),
                         "adv_bn": round(float(f.loc[c, "adv_20"]) / 1e9, 1), "ret_6m": None if pd.isna(f.loc[c, "ret_120"]) else round(float(f.loc[c, "ret_120"]) * 100, 1),
                         "ret_1m": None if pd.isna(f.loc[c, "ret_20"]) else round(float(f.loc[c, "ret_20"]) * 100, 1), "foreign_pct": None if pd.isna(f.loc[c, "foreign_hold_pct"]) else round(float(f.loc[c, "foreign_hold_pct"]), 1)})
        out[s] = {"name": v["name"], "reading": v["reading"], "score": v["score"], "reading_D": v.get("reading_D"), "n_universe": int(len(codes)), "n_pass": int(len(ok)),
                  "beta_median": None if b.empty else round(float(b.median()), 2), "stocks": rows, "as_of": str(t.date()), "fund_date": str(f.date.max().date())}
    return out
