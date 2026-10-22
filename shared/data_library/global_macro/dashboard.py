"""화면용 JSON — derived/dashboard/stage1.json (세계 지도·국가 영향력·약세장 기록(참고)·현재 상태).

화면(/topdown)은 이 파일만 읽는다. 모든 항목은 '검증 전 기술 통계'이며 매매 신호가 아니다 — 신호는 사전등록 통과 후에만 signals 에 들어간다.
"""
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

from . import analytics, catalog, issues, ledger, store

MARKETS = ["US", "EU", "JP", "CN", "KR"]
MARKET_KO = {"US": "미국 S&P500", "EU": "독일 DAX", "JP": "일본 닛케이", "CN": "중국 상하이", "KR": "한국 코스피"}
CRASH_INDEX = [("us_spx", "1950-01-01"), ("us_ndx", "1985-10-01"), ("kr_kospi", "1980-01-01"), ("jp_n225", "1965-01-01"),
               ("eu_dax", "1988-01-01"), ("cn_sse", "1997-07-01")]
# 현재 상태판: (키, 변환, 설명) — 변환 lvl=수준, yoy=전년비(%), chg6=6개월 변화, dd=52주 고점 대비, r12=12개월 수익
STATE = [
    ("w_cli_g7", "lvl", "G7 선행지수(100 = 장기 추세)"), ("w_cli_g7", "chg6", "G7 선행지수 6개월 변화"),
    ("us_cli", "chg6", "미국 선행지수 6개월 변화"), ("cn_cli", "chg6", "중국 선행지수 6개월 변화"),
    ("eu_cli", "chg6", "독일 선행지수 6개월 변화"), ("jp_cli", "chg6", "일본 선행지수 6개월 변화"),
    ("kr_cli", "chg6", "한국 선행지수 순환변동치 6개월 변화"),
    ("us_curve", "lvl", "미 장단기 금리차(10년−3개월, %p)"), ("us_baa", "lvl", "신용 스프레드(BAA−10년, %p)"),
    ("us_hy", "lvl", "하이일드 스프레드(%p)"), ("us_sahm", "lvl", "샴 규칙(0.5 이상 = 침체 신호로 알려짐)"),
    ("us_claims", "yoy", "실업수당 청구 전년비(%)"), ("us_10y", "chg6", "미 10년물 6개월 변화(%p)"),
    ("us_ffr", "lvl", "연방기금금리(%)"), ("us_cpi", "yoy", "미 소비자물가 전년비(%)"),
    ("w_vix", "lvl", "VIX"), ("w_dxy", "yoy", "달러 인덱스 전년비(%)"), ("w_copper_f", "yoy", "구리 전년비(%)"),
    ("w_oil", "yoy", "유가 전년비(%)"), ("kr_exp_total", "yoy3", "한국 수출 전년비(3개월 합, %)"),
    ("cn_exports", "yoy3", "중국 수출 전년비(3개월 합, %)"), ("kr_usdkrw", "yoy", "원/달러 전년비(%)"),
] + [(catalog.EQUITY[m], "dd", f"{MARKET_KO[m]} 52주 고점 대비(%)") for m in MARKETS] + \
    [(catalog.EQUITY[m], "r12", f"{MARKET_KO[m]} 12개월 수익(%)") for m in MARKETS]


def _transform(s: pd.Series, how: str, freq: str) -> pd.Series:
    s = s.dropna()
    if freq == "D":
        if how == "dd":
            return (s / s.rolling(252, min_periods=120).max() - 1) * 100
        if how == "r12":
            return (s / s.shift(252) - 1) * 100
        m = s.resample("ME").last()
    elif freq == "W":
        m = s.resample("ME").mean()
    else:
        m = s
    if how == "lvl":
        return s if freq == "D" else m
    if how == "yoy":
        return (m / m.shift(12) - 1) * 100
    if how == "yoy3":
        r3 = m.rolling(3).sum()
        return (r3 / r3.shift(12) - 1) * 100
    if how == "chg6":
        return m - m.shift(6)
    raise ValueError(how)


def _pct_rank(hist: pd.Series, v: float) -> float:
    h = hist.dropna()
    return float((h <= v).mean() * 100) if len(h) else float("nan")


def current_state(data) -> list:
    out = []
    for key, how, label in STATE:
        if key not in data:
            continue
        s = catalog.BY_KEY[key]
        x = _transform(data[key], how, s["freq"]).dropna()
        if x.empty:
            continue
        v, d = float(x.iloc[-1]), x.index[-1]
        hist = x[x.index >= d - pd.DateOffset(years=30)]
        prev = x[x.index <= d - pd.DateOffset(months=3)]
        obs = data[key].dropna().index[-1]          # 월로 묶은 값의 날짜 표기는 실제 마지막 관측일(미래 월말 아님)
        shown = min(d, obs) if s["freq"] != "M" else d
        out.append({"key": key, "how": how, "label": label, "block": s["block"], "value": round(v, 3), "date": str(shown.date()),
                    "pct30y": round(_pct_rank(hist, v), 1), "pct_years": round((d - hist.index[0]).days / 365.25, 1),
                    "chg3m": round(v - float(prev.iloc[-1]), 3) if len(prev) else None,
                    "series": [[str(i.date()), round(float(z), 4)] for i, z in x[x.index >= d - pd.DateOffset(years=12)].resample("ME").last().dropna().items()]})
    return out


def crashes(data) -> list:
    out = []
    for key, start in CRASH_INDEX:
        if key not in data:
            continue
        s = data[key].dropna()
        s = s[s.index >= start]
        bears = analytics.bear_markets(s)
        dd = (s / s.cummax() - 1) * 100
        out.append({"key": key, "name": catalog.BY_KEY[key]["name"], "block": catalog.BY_KEY[key]["block"], "start": str(s.index[0].date()),
                    "end": str(s.index[-1].date()), "bears": bears,
                    "drawdown": [[str(i.date()), round(float(v), 2)] for i, v in dd.resample("W-FRI").min().dropna().items()]})
    return out


ASIA = {"JP", "CN", "KR"}


def weekly_synced(daily: pd.DataFrame) -> pd.DataFrame:
    """시간대 맞춘 주간 종가: 아시아는 금요일 종가, 미국·유럽은 목요일 종가(아시아 금요일 마감 전에 이미 나온 값).
    금요일 종가끼리 맞추면 미국 금요일 움직임이 아시아에는 다음 주에 반영돼 미국 영향이 과소 측정된다."""
    out = {}
    for m in MARKETS:
        s = daily[catalog.EQUITY[m]].dropna()
        if m in ASIA:
            w = s.groupby(s.index.to_period("W-FRI")).last()
        else:
            w = s.groupby(s.index.to_period("W-THU")).last()
            w.index = (w.index.to_timestamp(how="end").normalize() + pd.Timedelta(days=1)).to_period("W-FRI")
        out[m] = w
    df = pd.DataFrame(out)
    df.index = df.index.to_timestamp(how="end").normalize()
    return df


def influence(daily: pd.DataFrame) -> dict:
    R = np.log(weekly_synced(daily)).diff()
    R = R[R.index >= "1997-07-04"].dropna()
    full = analytics.spillover(R)
    halves = {f"{a[:4]}~{b[:4]}": analytics.spillover(R.loc[a:b]) for a, b in [("1997-07-01", "2011-12-31"), ("2012-01-01", "2026-12-31")]}
    roll = analytics.rolling_spillover(R, window=104, step=4)
    corr = R.corr().round(3)
    return {"markets": MARKETS, "labels": [MARKET_KO[m] for m in MARKETS], "full": full, "halves": halves,
            "rolling": [{"date": r["date"], "total": round(r["total"], 2),
                         "to_kr": dict(zip(MARKETS, [round(x, 2) for x in r["table"][MARKETS.index("KR")]])),
                         "net": dict(zip(MARKETS, r["net"]))} for r in roll],
            "corr": {"names": MARKETS, "table": corr.values.tolist()}}


def kr_exports(data) -> dict:
    keys = {"중국+홍콩": ["kr_exp_cn", "kr_exp_hk"], "미국": ["kr_exp_us"], "베트남": ["kr_exp_vn"], "일본": ["kr_exp_jp"], "대만": ["kr_exp_tw"]}
    if "kr_exp_total" not in data:
        return {}
    tot = data["kr_exp_total"].resample("YE").sum() / 1000.0          # FRED: 달러 → 천 달러 단위 맞춤(ECOS 천불)
    shares = {}
    for nm, ks in keys.items():
        if not all(k in data for k in ks):
            continue
        s = sum(data[k].resample("YE").sum() for k in ks)
        shares[nm] = (s / tot * 100).round(1)
    df = pd.DataFrame(shares).dropna(how="all")
    df = df[(df.index.year >= 1990) & (df.index.year <= pd.Timestamp.now().year)]
    # 선행지표(6개월 변화)와 한국 수출 전년비의 선후 — 지역별 최대 상관 시차
    exp = data["kr_exp_total"].resample("ME").last()
    r3 = exp.rolling(3).sum()
    y = np.log(r3 / r3.shift(12))
    leads = {}
    for nm, k in [("G7", "w_cli_g7"), ("미국", "us_cli"), ("중국", "cn_cli"), ("독일", "eu_cli"), ("일본", "jp_cli")]:
        if k not in data:
            continue
        x = data[k].resample("ME").last()
        x = x - x.shift(6)
        leads[nm] = {p: analytics.best_lead(y.loc[a:b], x.loc[a:b]) for p, (a, b) in
                     {"전체": ("1995", "2026"), "1995~2010": ("1995", "2010"), "2011~2026": ("2011", "2026")}.items()}
    return {"years": [int(i.year) for i in df.index], "shares": {c: [None if np.isnan(v) else float(v) for v in df[c]] for c in df.columns},
            "partial_year": int(df.index[-1].year) if len(df) else None, "cli_leads": leads}


def trade_map(influence_full=None) -> dict:
    """실물 경로 — 노출(총수출 vs 부가가치), 민감도(상대국 경기 → 한국 수출), 영향력 지수, 수출 가중 선행지수 현재값."""
    from . import trade_signal as ts
    if not os.path.exists(store.path("derived", "trade", "tiva.parquet")):
        return {}
    out = {"exposure": {}}
    for ref in ("KOR", "TWN"):
        ex = ts.exposure_shares(ref)
        out["exposure"][ref] = {m: {"years": [int(y) for y in df.index], "groups": {c: [float(v) for v in df[c]] for c in df.columns}} for m, df in ex.items()}
    sens = ts.sensitivity()
    out["sensitivity"] = sens
    # 영향력 지수 = 부가가치 비중(최신 연도) × 1σ당 수출 탄력성(음수는 0) — 합 100
    t = ts._load("tiva")
    t = t[(t.ref == "KOR") & (t.measure == "FFD_DVA")]
    y = int(t.year.max())
    p = t[t.year == y].set_index("partner")["value"]
    share = {"중국+홍콩": p.get("CHN", 0) + p.get("HKG", 0), "미국": p.get("USA", 0), "유럽": p.get("EU27_2020", 0), "일본": p.get("JPN", 0),
             "인도": p.get("IND", 0), "인도네시아": p.get("IDN", 0), "멕시코": p.get("MEX", 0), "호주": p.get("AUS", 0), "캐나다": p.get("CAN", 0),
             "튀르키예": p.get("TUR", 0), "브라질": p.get("BRA", 0)}
    W = p.get("W", 1)
    inf = {}
    for per in ("full", "late"):
        raw = {r["name"]: share.get(r["name"], 0) / W * 100 * max(r.get(per, {}).get("beta_pct") or 0, 0) for r in sens}
        tot = sum(raw.values()) or 1
        inf[per] = {k: round(v / tot * 100, 1) for k, v in sorted(raw.items(), key=lambda x: -x[1])}
    out["influence_index"] = {"year": y, "va_share": {k: round(v / W * 100, 1) for k, v in share.items()}, **inf}
    if influence_full:
        kr = influence_full["names"].index("KR")
        out["equity_to_kr"] = dict(zip(influence_full["names"], influence_full["table"][kr]))
    # 수출 가중 선행지수(총수출·부가가치) 현재 — 누가 지금 값을 움직이나
    at = pd.date_range("2000-01-31", pd.Timestamp.now().normalize(), freq="ME")
    for nm, w in (("gross", ts.weights_kr_gross()), ("va", ts.weights_tiva("KOR", "FFD_DVA"))):
        c = ts.weighted_cli(w, at).dropna(subset=["total"])
        last = c.iloc[-1].drop("total").dropna().sort_values()
        out[f"wcli_{nm}"] = {"date": str(c.index[-1].date()), "total": float(c["total"].iloc[-1]),
                             "contrib": {k: round(float(v), 3) for k, v in last.items()},
                             "series": [[str(i.date()), round(float(v), 3)] for i, v in c["total"].loc["2010":].items()]}
    return out


def _relations() -> dict:
    from . import relations
    try:
        out = relations.build()
        issues.step_ok("dashboard.relations")
        return out
    except Exception as e:   # noqa: BLE001 — 원천 일부가 없어도 화면 나머지는 그린다
        issues.step_failed("dashboard.relations", e, "relations.build — 무역·수입 경쟁 산출물 확인")
        return {"error": str(e)[:200]}


def _edges_sync(reading: dict, relations: dict, influence: dict) -> dict:
    """측정 엣지 저장소(append-only)에 이번 산출물을 덧붙이고 요약을 돌려준다 — 실패해도 화면 나머지는 그린다."""
    from . import edge_store
    try:
        fp = store.path("reading", "history.parquet")
        hist = pd.read_parquet(fp) if os.path.exists(fp) else None
        out = edge_store.sync(hist, reading, relations, influence)
        issues.step_ok("dashboard.edges")
        return out
    except Exception as e:   # noqa: BLE001
        issues.step_failed("dashboard.edges", e, "edge_store.collect — 산출물 모양 변화 확인")
        return {"error": str(e)[:200]}


def _clean(o):
    """JSON 에 NaN·inf 를 넣지 않는다(브라우저 JSON.parse 실패) → null."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    return o


def merged_signals() -> dict:
    """signals/registry_stage*.json (연구 단계별 사전등록 검정 결과)를 하나로 — 각 행에 구간 이름을 붙인다."""
    import glob
    out = {"sources": [], "signals": [], "combos": []}
    for fp in sorted(glob.glob(store.path("signals", "registry_stage*.json"))):
        r = store.read_json(fp) or {}
        per = r.get("periods") or ["개발", "확인"]
        out["sources"].append({"stage": r.get("stage"), "source": r.get("source"), "prereg": (r.get("prereg_sha256") or "")[:12],
                               "generated_at": r.get("generated_at"), "periods": per})
        out["signals"] += [dict(x, periods=x.get("periods") or per, stage=r.get("stage")) for x in r.get("signals", [])]
        out["combos"] += [dict(x, stage=r.get("stage")) for x in r.get("combos", [])]
    return out


def build() -> dict:
    data = {}
    for s in catalog.SERIES:
        fp = store.path("series", f"{s['key']}.csv")
        if os.path.exists(fp):
            d = pd.read_csv(fp, parse_dates=["date"])
            data[s["key"]] = d.set_index("date")["value"].astype(float).sort_index()
    daily = pd.read_parquet(store.path("derived", "daily.parquet"))
    q = store.read_json(store.path("quality", "panel_quality.json")) or {}
    out = {
        "stage": 1, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "검증 전 기술 통계 — 매매 신호 아님. 신호는 사전등록 통과 후 signals 에만.",
        "catalog": [{k: s[k] for k in ("key", "block", "name", "source", "sid", "freq", "lag_days", "kind")} | (q.get("series", {}).get(s["key"]) or {})
                    for s in catalog.SERIES],
        "state": current_state(data),
        "crashes": crashes(data),
        "influence": (inf := influence(daily)),
        "trade": trade_map(inf.get("full")),
        "relations": (rel := _relations()),
        "reading": (rd := store.read_json(store.path("derived", "dashboard", "reading.json")) or {}),
        "kr_exports": kr_exports(data),
        "signals": merged_signals(),
        "ledger": ledger.summary() | ledger.graph_view(),
        "edges": _edges_sync(rd, rel, inf),
        "issues": issues.summary(),
    }
    os.makedirs(store.path("derived", "dashboard"), exist_ok=True)
    fp = store.path("derived", "dashboard", "stage1.json")
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, allow_nan=False, default=str)
    os.replace(tmp, fp)
    store.log(f"[dashboard] stage1.json — 상태 {len(out['state'])}개, 사건표 {len(out['crashes'])}지수, 연결도 {len(out['influence']['rolling'])}창")
    return out
