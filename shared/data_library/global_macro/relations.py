"""관계 판독 — 시간에 따라 변하는 관계를 '읽기' 위한 이동창 계측(예측 규칙 아님, 판정 없음).

  export_links   상대국 경기(선행지수 6개월 변화) → 그 나라로의 한국 수출 전년비: 5년 이동창의 최대 상관·시차·탄력성
  export_shares  한국 수출의 상대국별 12개월 이동 비중
  health         관계 건강도: 중국 경기 → 코스피, G7 경기 → 코스피(12개월, 음수 = 역발상), 미국 업종 생산 → 한국 같은 업종(이동 IC)
  us_competition 미국 수입시장 품목별 점유율(한국·중국·일본·멕시코·베트남·대만, 12개월 이동)
  events         reference/events.json — 국면 사건(직접 추가·수정 가능)
"""
import json
import os
import shutil
from typing import Dict, List

import numpy as np
import pandas as pd

from . import catalog, kr_industry, store, trade
from . import panel as gp

WINDOW = 60                 # 5년 이동창(월)
LAGS = range(0, 10)
LINK_PAIRS = [("중국+홍콩", ["CN", "HK"], "CHN"), ("미국", ["US"], "USA"), ("유럽", ["EUR"], "DEU"), ("일본", ["JP"], "JPN"), ("인도", ["IN"], "IND")]
SHARE_GROUPS = {"중국+홍콩": ["CN", "HK"], "미국": ["US"], "아세안(베트남 포함)": ["VN", "SG", "MY", "ID", "TH", "PH"], "유럽": ["EUR"],
                "일본": ["JP"], "대만": ["TW"], "인도": ["IN"]}


def _trade(name: str) -> pd.DataFrame:
    return pd.read_parquet(store.path("derived", "trade", f"{name}.parquet"))


def _ser(rows) -> List[list]:
    return [[str(i.date()), None if pd.isna(v) else round(float(v), 4)] for i, v in rows.items()]


def export_links(window: int = WINDOW) -> Dict[str, list]:
    ex, cli = _trade("kr_exp"), _trade("partner_cli")
    out = {}
    for name, codes, c in LINK_PAIRS:
        if c not in cli or not all(e in ex for e in codes):
            continue
        s = ex[codes].sum(axis=1, min_count=len(codes)).rolling(3).sum()
        y = np.log(s / s.shift(12))
        x = cli[c] - cli[c].shift(6)
        df = pd.DataFrame({"y": y, **{k: x.shift(k) for k in LAGS}}).loc["1994":]
        res = []
        for i in range(window, len(df) + 1):
            w = df.iloc[i - window:i]
            if w.y.notna().sum() < 36:
                continue
            cs = {k: w.y.corr(w[k]) for k in LAGS}
            cs = {k: v for k, v in cs.items() if not np.isnan(v)}
            if not cs:
                continue
            k = max(cs, key=lambda z: cs[z])
            d = w[["y", k]].dropna()
            beta = np.polyfit((d[k] - d[k].mean()) / d[k].std(), d.y, 1)[0] * 100 if len(d) > 24 and d[k].std() > 0 else np.nan
            res.append([str(w.index[-1].date()), round(float(cs[k]), 3), int(k), None if np.isnan(beta) else round(float(beta), 2)])
        out[name] = res
    return out


def export_shares() -> Dict[str, list]:
    ex = _trade("kr_exp")
    tot = gp.load("kr_exp_total")
    tot.index = tot.index.to_period("M").to_timestamp(how="end").normalize()
    tot12 = (tot / 1000.0).rolling(12).sum()                    # FRED 달러 → ECOS 천 달러
    out = {}
    for g, codes in SHARE_GROUPS.items():
        cs = [c for c in codes if c in ex]
        if not cs:
            continue
        s12 = ex[cs].sum(axis=1, min_count=1).rolling(12).sum()
        sh = (s12 / tot12.reindex(s12.index) * 100).loc["1995":].dropna()
        out[g] = _ser(sh)
    return out


def _monthly_close(code: str) -> pd.Series:
    df = pd.read_parquet(store.path("derived", "kr_industry", "sector_close.parquet"))
    return df[code]


def health(window: int = WINDOW) -> Dict[str, dict]:
    kospi = _monthly_close(kr_industry.MARKET)
    at = kospi.index
    out = {}
    f6 = np.log(kospi.shift(-6) / kospi)
    f12 = np.log(kospi.shift(-12) / kospi)
    cli = _trade("partner_cli")
    grid = pd.date_range(at.min() - pd.DateOffset(months=8), at.max(), freq="ME")
    chn = gp.point_in_time(cli["CHN"].dropna(), trade.CLI_LAG_DAYS, grid)[0]
    chn6 = (chn - chn.shift(6)).reindex(at)
    g7s = gp.load("w_cli_g7")
    g7 = gp.point_in_time(g7s.dropna(), catalog.BY_KEY["w_cli_g7"]["lag_days"], grid)[0]
    g76 = (g7 - g7.shift(6)).reindex(at)
    def roll_corr(x, f):
        # 미래 수익이 다 찬 달까지만 → 날짜 = 창의 마지막 '결과가 확정된' 신호월
        d = pd.DataFrame({"x": x, "f": f}).dropna()
        return d.x.rolling(window, min_periods=36).corr(d.f).loc["2000":].dropna()
    out["중국 경기 → 코스피 6개월"] = {"desc": "이동 상관(+ = 중국 선행지수가 오를 때 이후 6개월 코스피 강함)", "series": _ser(roll_corr(chn6, f6))}
    out["G7 경기 → 코스피 12개월"] = {"desc": "이동 상관(− = 선진국 경기 개선 확인 뒤 1년이 약함, 역발상)", "series": _ser(roll_corr(g76, f12))}
    # 미국 같은 업종 생산 가속 → 한국 같은 업종 6개월(3단계 X2 정의와 같음): 월별 업종 간 순위상관의 5년 이동 평균
    ip = pd.read_parquet(store.path("derived", "us_industry", "fund_ip.parquet"))
    y = np.log(ip / ip.shift(12)).rolling(3).mean()
    acc = y - y.shift(3)
    accp = pd.DataFrame({c: gp.point_in_time(acc[c].dropna(), catalog.BY_KEY["us_indpro"]["lag_days"], at)[0] for c in acc.columns}, index=at)
    close = pd.read_parquet(store.path("derived", "kr_industry", "sector_close.parquet"))
    fs = np.log(close.shift(-6) / close)
    ics = {}
    for t in at:
        pairs = [(accp.loc[t, v[2]], fs.loc[t, c]) for c, v in kr_industry.SECTORS.items() if v[2] and v[2] in accp]
        d = pd.DataFrame(pairs, columns=["s", "f"]).dropna()
        if len(d) >= 8:
            ics[t] = d.s.rank().corr(d.f.rank())
    ic = pd.Series(ics)
    out["미국 업종 생산 → 한국 같은 업종"] = {"desc": "월별 업종 간 순위상관의 5년 이동 평균(+ = 미국에서 생산이 빨라지는 업종의 한국 주가가 이후 6개월 강함)",
                                     "series": _ser(ic.rolling(window, min_periods=36).mean().loc["2000":].dropna())}
    return out


TOTAL = "-"                 # Census '전 국가 합계' 코드


def us_competition() -> Dict[str, dict]:
    from . import us_imports
    fp = store.path("derived", "trade", "us_imports.parquet")
    if not os.path.exists(fp):
        return {}
    df = pd.read_parquet(fp)
    out = {}
    for cmd, name in us_imports.PRODUCTS.items():
        d = df[df.cmd == cmd].pivot_table(index="month", columns="partner", values="value", aggfunc="sum").sort_index()
        if TOTAL not in d:
            continue
        d12 = d.rolling(12, min_periods=12).sum()
        sh = d12.div(d12[TOTAL], axis=0) * 100
        series = {us_imports.PARTNERS[p]: _ser(sh[p].dropna()) for p in sh.columns if p != TOTAL}
        last = sh.dropna(how="all").iloc[-1] if len(sh.dropna(how="all")) else None
        snap = {}
        if last is not None:
            t = sh.dropna(how="all").index[-1]
            for p in sh.columns:
                if p == TOTAL:
                    continue
                ago3 = sh[p].loc[:t - pd.DateOffset(years=3)].dropna()
                y2017 = sh[p].loc["2017-12-31":"2017-12-31"].dropna()
                snap[us_imports.PARTNERS[p]] = {"now": round(float(last[p]), 2) if pd.notna(last[p]) else None,
                                                "chg3y": round(float(last[p] - ago3.iloc[-1]), 2) if len(ago3) and pd.notna(last[p]) else None,
                                                "vs2017": round(float(last[p] - y2017.iloc[-1]), 2) if len(y2017) and pd.notna(last[p]) else None}
        out[cmd] = {"name": name, "series": series, "snapshot": snap,
                    "us_total_12m_bn": round(float(d12[TOTAL].dropna().iloc[-1]) / 1e9, 1) if d12[TOTAL].notna().any() else None,
                    "last_month": str(d.index.max().date())}
    return out


SEED_EVENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference", "events.json")


def events() -> list:
    """국면 사건 목록. 운영본은 DataVault/staging/global_macro/reference/events.json(직접 편집),
    없으면 패키지 시드(reference/events.json)를 복사해 만든다 — 시드는 git 에 남는 원장 사본."""
    fp = store.path("reference", "events.json")
    if not os.path.exists(fp) and os.path.exists(SEED_EVENTS):
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        shutil.copyfile(SEED_EVENTS, fp)
    try:
        with open(fp, encoding="utf-8") as f:
            return sorted(json.load(f), key=lambda e: e["date"])
    except Exception:
        return []


def build() -> dict:
    comp = us_competition()
    # HS 코드 키는 숫자처럼 보여 브라우저가 숫자 순으로 재정렬 → 표시 순서를 따로 넘김
    return {"window_months": WINDOW, "export_links": export_links(), "export_shares": export_shares(), "health": health(),
            "us_competition": comp, "us_competition_order": list(comp), "events": events()}
