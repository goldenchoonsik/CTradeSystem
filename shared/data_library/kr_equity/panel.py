"""kr_equity 분석용 패널 — raw 원본 → derived/*.parquet(지워도 재생성되는 캐시) + quality/panel_quality.json

  prices_1d.parquet       code·date·open/high/low/close/volume/value(수정) · close_unadj · factor · src · reg_fix
                          reg_fix 0 정규장 그대로 / 1 2026-09-14~ 1분봉으로 정규장 복원 / 2 복원 불가(NaN) / 3 네이버(시간외 포함 여부 미확인)
  daily_ext.parquet       code·date·shares_listed·market_cap·foreign_hold_qty·foreign_hold_pct·inst_net_qty  (상장=Creon, 상폐=DART 주식수×네이버 종가 근사, mcap_approx=1)
                          흐름 = foreign_hold_qty 일변화(외국인 순매수 근사, 기업행위 점프 주의)·inst_net_qty(기관 일 순매수)
  fundamentals_q.parquet  stock_code·period_end·rcept_dt·분기 손익(3개월치)·재무상태·fs_div  — 결합은 rcept_dt 이후에만(미래참조 방지)
  index_1d.parquet        지수 OHLCV
1분봉 정규장 복원은 vol_spike_universe 의 1분봉 일자 저장소를 읽기 전용으로 빌려 쓴다(2026-10-08 까지 있음).
"""
import glob
import json
import os
import re
import time
from datetime import datetime

import numpy as np
import pandas as pd

from shared.data_library.paths import data_vault_path

from . import store
from .store import log, path

M1_STORE = data_vault_path("staging", "vol_spike_universe", "raw", "creon", "candle_1m")
START = pd.Timestamp("2013-01-01")          # 2014 시작 + 1년 룩백
NXT_FROM = pd.Timestamp("2026-09-14")       # 이날부터 Creon 일봉 종가 = 20:00 시간외
COLS = {"timestamp": "date", "upbit_open": "open", "upbit_high": "high", "upbit_low": "low", "upbit_close": "close",
        "upbit_volume": "volume", "upbit_trade_price": "value"}
OHLCV = ["open", "high", "low", "close", "volume", "value"]
IS_ACCTS = {"매출액": "sales", "영업이익": "op_income", "영업이익(손실)": "op_income", "당기순이익(손실)": "net_income",
            "법인세차감전 순이익": "pretax_income"}
BS_ACCTS = {"자산총계": "assets", "부채총계": "liabilities", "자본총계": "equity", "자본금": "capital_stock",
            "유동자산": "current_assets", "유동부채": "current_liabilities", "이익잉여금": "retained_earnings"}
REPORT_Q = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}


def derived(name: str) -> str:
    return path("derived", name)


def universe() -> pd.DataFrame:
    return pd.read_csv(path("universe.csv"), dtype=str)


# ---------------------------------------------------------------- 가격
def _reg_session(code, day):
    """1분봉 09:00~15:30 → 정규장 OHLCV(무수정). 파일 없으면 None, 거래 없으면 0 거래량."""
    fp = os.path.join(M1_STORE, code, f"{day:%Y-%m-%d}.csv")
    if not os.path.exists(fp):
        return None
    m = pd.read_csv(fp, usecols=list(COLS), parse_dates=["timestamp"])
    m = m[(m.timestamp.dt.hour * 100 + m.timestamp.dt.minute).between(900, 1530)]
    if m.empty or m.upbit_volume.sum() <= 0:
        return [np.nan, np.nan, np.nan, np.nan, 0.0, 0.0]
    return [m.upbit_open.iloc[0], m.upbit_high.max(), m.upbit_low.min(), m.upbit_close.iloc[-1],
            m.upbit_volume.sum(), m.upbit_trade_price.sum()]


def read_creon(code: str) -> pd.DataFrame:
    base = path("raw", "creon")
    a = pd.read_csv(os.path.join(base, "candle_1d", f"{code}_1d.csv"), parse_dates=["timestamp"]).rename(columns=COLS)
    a = a[a.date >= START].drop_duplicates("date").sort_values("date")
    u = pd.read_csv(os.path.join(base, "candle_1d_unadj", f"{code}_1d.csv"), parse_dates=["timestamp"]).rename(columns=COLS)
    u = u[u.date >= START].drop_duplicates("date")
    d = a.merge(u[["date"] + OHLCV].add_suffix("_u").rename(columns={"date_u": "date"}), on="date", how="left")
    for c in OHLCV + ["close_u"]:                     # 정수로 읽힌 열에 소수(복원값×계수)를 넣을 수 있게
        d[c] = d[c].astype("float64")
    d["factor"] = d.close / d.close_u
    d["reg_fix"] = np.int8(0)
    for i in d.index[d.date >= NXT_FROM]:
        reg = _reg_session(code, d.at[i, "date"])
        f = d.at[i, "factor"]
        if reg is None or not np.isfinite(f):
            d.loc[i, OHLCV + ["close_u"]] = np.nan
            d.at[i, "reg_fix"] = 2                      # 복원 불가 → 시간외 값 쓰지 않음
            continue
        o, h, l, c, v, val = reg
        d.loc[i, ["open", "high", "low", "close"]] = np.array([o, h, l, c]) * f
        d.loc[i, ["volume", "value"]] = [v, val]
        d.at[i, "close_u"] = c
        d.at[i, "reg_fix"] = 1
    out = d[["date"] + OHLCV + ["close_u", "factor", "reg_fix"]].rename(columns={"close_u": "close_unadj"})
    out.insert(0, "code", code)
    out["src"] = "creon"
    return out


def parse_naver(raw: bytes) -> pd.DataFrame:
    rows = [x.decode().split("|") for x in re.findall(rb'data="([^"]+)"', raw)]
    d = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    if d.empty:
        return d
    d = d.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    d["date"] = pd.to_datetime(d.date, format="%Y%m%d")
    return d


def read_naver(code: str):
    with open(path("raw", "naver", "candle_1d", f"{code[1:]}.xml"), "rb") as f:
        d = parse_naver(f.read())
    if d.empty:
        return None
    d = d[d.date >= START].drop_duplicates("date").sort_values("date")
    d["value"] = np.nan
    d["close_unadj"] = np.nan
    d["factor"] = np.nan
    d["reg_fix"] = np.where(d.date >= NXT_FROM, np.int8(3), np.int8(0))
    d.insert(0, "code", code)
    d["src"] = "naver"
    return d[["code", "date"] + OHLCV + ["close_unadj", "factor", "reg_fix", "src"]]


def build_prices() -> pd.DataFrame:
    uni = universe()
    parts, t0 = [], time.time()
    for i, r in enumerate(uni.itertuples(), 1):
        try:
            d = read_creon(r.code) if r.price_source == "creon" else read_naver(r.code)
        except FileNotFoundError:
            d = None
        if d is not None and len(d):
            for c in OHLCV + ["close_unadj", "factor"]:
                d[c] = d[c].astype("float32")
            parts.append(d)
        if i % 500 == 0:
            log(f"[panel] prices {i}/{len(uni)} ({time.time() - t0:.0f}s)", "build_panel.txt")
    p = pd.concat(parts, ignore_index=True)
    p["code"] = p.code.astype("category")
    p["src"] = p.src.astype("category")
    p.to_parquet(derived("prices_1d.parquet"), index=False)
    log(f"[panel] prices {len(p):,} rows, {p.code.nunique()} codes", "build_panel.txt")
    return p


# ---------------------------------------------------------------- 주식수·시총 (상장=Creon, 상폐=DART×네이버 근사)
def parse_shares_file(j: dict):
    """stockTotqySttus 응답 → (rcept_dt, 보통주 발행주식수) | None. 보통주 행이 없으면 합계."""
    lst = (j.get("response") or {}).get("list") or []
    rows = {r.get("se", "").strip(): r for r in lst}
    r = rows.get("보통주") or rows.get("합계")
    if not r:
        return None
    try:
        n = float(str(r.get("distb_stock_co", "")).replace(",", ""))
    except ValueError:
        return None
    if not np.isfinite(n) or n <= 0:
        return None
    return r["rcept_no"][:8], n


def delisted_shares() -> pd.DataFrame:
    corp2stock = {c: s for c, s, _ in store.corp_rows()}
    recs = []
    for fp in glob.glob(path("raw", "dart", "shares", "*", "*.json")):
        j = store.read_json(fp)
        got = parse_shares_file(j or {})
        if not got:
            continue
        corp = os.path.basename(os.path.dirname(fp))
        y, rc = os.path.basename(fp)[:-5].split("_")
        recs.append(("A" + corp2stock.get(corp, ""), int(y), rc, got[0], got[1]))
    s = pd.DataFrame(recs, columns=["code", "bsns_year", "reprt_code", "rcept_dt", "shares"])
    s["rcept_dt"] = pd.to_datetime(s.rcept_dt, format="%Y%m%d")
    return s.sort_values(["code", "rcept_dt"]).drop_duplicates(["code", "rcept_dt"], keep="last")


def build_ext(prices: pd.DataFrame = None) -> pd.DataFrame:
    uni = universe()
    parts = []
    for r in uni[uni.price_source == "creon"].itertuples():
        fp = path("raw", "creon", "daily_ext", f"{r.code}_1d_ext.csv")
        if not os.path.exists(fp):
            continue
        d = pd.read_csv(fp, usecols=["date", "shares_listed", "market_cap", "foreign_hold_qty", "foreign_hold_pct", "inst_net_qty"], dtype={"date": str})
        d["date"] = pd.to_datetime(d.date, format="%Y%m%d")
        d = d[d.date >= START].drop_duplicates("date")
        d.insert(0, "code", r.code)
        d["mcap_approx"] = np.int8(0)
        parts.append(d)
    # 상폐 종목: 보고서별 발행주식수(접수일 이후 적용) × 네이버 수정 종가 — 분할·병합이 보고서 뒤에 있으면 어긋난다(근사)
    sh = delisted_shares() if os.path.isdir(path("raw", "dart", "shares")) else pd.DataFrame()
    if len(sh):
        if prices is None:
            prices = pd.read_parquet(derived("prices_1d.parquet"), columns=["code", "date", "close", "src"])
        nv = prices[prices.src == "naver"][["code", "date", "close"]].copy()
        nv["code"] = nv.code.astype(str)
        nv = nv.sort_values("date")
        m = pd.merge_asof(nv, sh[["code", "rcept_dt", "shares"]].rename(columns={"rcept_dt": "date"}).sort_values("date"),
                          on="date", by="code", direction="backward")
        m = m.dropna(subset=["shares"])
        d = pd.DataFrame({"code": m.code, "date": m.date, "shares_listed": m.shares, "market_cap": m.shares * m.close,
                          "foreign_hold_qty": np.nan, "foreign_hold_pct": np.nan, "inst_net_qty": np.nan, "mcap_approx": np.int8(1)})
        parts.append(d)
    e = pd.concat(parts, ignore_index=True)
    e["code"] = e.code.astype("category")
    e["foreign_hold_pct"] = e.foreign_hold_pct.astype("float32")
    e.to_parquet(derived("daily_ext.parquet"), index=False)
    log(f"[panel] ext {len(e):,} rows, {e.code.nunique()} codes (상폐 근사 {int((e.mcap_approx == 1).sum()):,}행)", "build_panel.txt")
    return e


# ---------------------------------------------------------------- 재무 (DART 주요계정)
def _amt(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "", "-") else np.nan
    except ValueError:
        return np.nan


def period_end_of(thstrm_dt: str):
    """손익 '2024.01.01 ~ 2024.06.30', 재무상태 '2024.06.30 현재' — 마지막 날짜가 기간 말."""
    dts = re.findall(r"(\d{4})\.(\d{2})\.(\d{2})", thstrm_dt or "")
    return "-".join(dts[-1]) if dts else None


def build_fin() -> pd.DataFrame:
    recs = []
    files = sorted(glob.glob(path("raw", "dart", "fnltt_multi", "*", "*.json")))
    for fp in files:
        j = store.read_json(fp) or {}
        fetched = j.get("fetched_at") or ""
        for r in (j.get("response") or {}).get("list") or []:
            key = IS_ACCTS.get(r.get("account_nm")) or BS_ACCTS.get(r.get("account_nm"))
            if not key:
                continue
            recs.append((r["stock_code"], r["corp_code"], int(r["bsns_year"]), r["reprt_code"], r["fs_div"], r["rcept_no"],
                         period_end_of(r.get("thstrm_dt")), key, int(r.get("ord") or 0),
                         _amt(r.get("thstrm_amount")), _amt(r.get("thstrm_add_amount")), fetched))
    f = pd.DataFrame(recs, columns=["stock_code", "corp_code", "bsns_year", "reprt_code", "fs_div", "rcept_no",
                                    "period_end", "acct", "ord", "amt", "amt_cum", "fetched"])
    del recs
    log(f"[panel] fin {len(f):,} account rows from {len(files)} files", "build_panel.txt")
    # 같은 (회사, 연도, 보고서)가 여러 파일(묶음 변경)에 있으면 가장 최근 수집분
    latest = f.groupby(["stock_code", "bsns_year", "reprt_code"]).fetched.transform("max")
    f = f[f.fetched == latest]
    has_cfs = f[f.fs_div == "CFS"][["stock_code", "bsns_year", "reprt_code"]].drop_duplicates()
    has_cfs["_cfs"] = True
    f = f.merge(has_cfs, on=["stock_code", "bsns_year", "reprt_code"], how="left")
    f = f[(f.fs_div == "CFS") | f._cfs.isna()].drop(columns="_cfs")     # 연결 우선, 없으면 별도
    f = f.sort_values("ord").drop_duplicates(["stock_code", "bsns_year", "reprt_code", "acct"])
    keys = ["stock_code", "corp_code", "bsns_year", "reprt_code", "fs_div", "rcept_no", "period_end"]
    wide = f.pivot_table(index=keys, columns="acct", values="amt", aggfunc="first").reset_index()
    cum = f[f.acct.isin(set(IS_ACCTS.values()))].pivot_table(index=keys, columns="acct", values="amt_cum", aggfunc="first")
    cum.columns = [c + "_cum" for c in cum.columns]
    wide = wide.merge(cum.reset_index(), on=keys, how="left")
    wide["q"] = wide.reprt_code.map(REPORT_Q)
    wide["rcept_dt"] = pd.to_datetime(wide.rcept_no.str[:8], format="%Y%m%d")
    wide["period_end"] = pd.to_datetime(wide.period_end)
    wide = derive_q4(wide)
    wide = wide.sort_values(["stock_code", "period_end", "rcept_dt"]).reset_index(drop=True)
    wide.to_parquet(derived("fundamentals_q.parquet"), index=False)
    log(f"[panel] fin {len(wide):,} company-quarters, {wide.stock_code.nunique()} companies", "build_panel.txt")
    return wide


def derive_q4(wide: pd.DataFrame) -> pd.DataFrame:
    """사업보고서(11011)의 손익은 연간치 → 4분기 = 연간 − 3분기 누적. 분기·반기·3분기 보고서의 amt 는 당기 3개월치."""
    for k in set(IS_ACCTS.values()):
        if k not in wide:
            continue
        ann = wide.q == 4
        wide.loc[ann, k + "_annual"] = wide.loc[ann, k]
        if k + "_cum" not in wide:
            wide[k + "_cum"] = np.nan
        q3 = wide[wide.q == 3][["stock_code", "bsns_year", k + "_cum"]].rename(columns={k + "_cum": "_q3cum"})
        wide = wide.merge(q3, on=["stock_code", "bsns_year"], how="left")
        wide.loc[ann, k] = wide.loc[ann, k + "_annual"] - wide.loc[ann, "_q3cum"]
        wide.loc[ann, k + "_cum"] = wide.loc[ann, k + "_annual"]
        wide = wide.drop(columns="_q3cum")
    return wide


# ---------------------------------------------------------------- 지수
def build_index() -> pd.DataFrame:
    parts = []
    for fp in sorted(glob.glob(path("raw", "creon", "index_1d", "*_1d.csv"))):
        d = pd.read_csv(fp, dtype={"date": str})
        d["date"] = pd.to_datetime(d.date, format="%Y%m%d")
        d.insert(0, "code", os.path.basename(fp).split("_")[0])
        parts.append(d[d.date >= START])
    x = pd.concat(parts, ignore_index=True)
    x.to_parquet(derived("index_1d.parquet"), index=False)
    log(f"[panel] index {len(x):,} rows, {x.code.nunique()} indices", "build_panel.txt")
    return x


# ---------------------------------------------------------------- 품질 점검
def quality() -> dict:
    uni = universe()
    p = pd.read_parquet(derived("prices_1d.parquet"))
    e = pd.read_parquet(derived("daily_ext.parquet"))
    fin = pd.read_parquet(derived("fundamentals_q.parquet"))
    idx = pd.read_parquet(derived("index_1d.parquet"))
    kospi = idx[idx.code == "U001"].date.sort_values()
    tdays = kospi[kospi >= "2014-01-01"]
    Q = {"built_at": datetime.now().isoformat(timespec="seconds")}
    q = {"rows": int(len(p)), "codes": int(p.code.nunique()), "universe": int(len(uni)),
         "missing_codes": sorted(set(uni.code) - set(p.code.astype(str))),
         "date_min": str(p.date.min().date()), "date_max": str(p.date.max().date()),
         "dup_code_date": int(p.duplicated(["code", "date"]).sum()),
         "by_src": {str(k): int(v) for k, v in p.groupby("src", observed=True).code.nunique().items()}}
    g = p.groupby("code", observed=True)
    first, last = g.date.min(), g.date.max()
    dl = uni[uni.status == "delisted"].set_index("code")
    dl_last = last.reindex(dl.index)
    gap = (pd.to_datetime(dl.delist_date) - dl_last).dt.days
    q["delisted"] = {"n": int(len(dl)), "no_price": int(dl_last.isna().sum()),
                     "last_price_gap_days": {"median": float(gap.median()), "p90": float(gap.quantile(0.9)), "over_30d": int((gap > 30).sum())},
                     "gap_over_30d_examples": {str(k): int(v) for k, v in gap[gap > 30].sort_values(ascending=False).head(8).items()}}
    listed = uni[uni.status == "listed"].code
    exp = pd.Series({c: int(((tdays >= max(first[c], pd.Timestamp("2014-01-01"))) & (tdays <= last[c])).sum())
                     for c in listed if c in first.index})
    act = p[p.date >= "2014-01-01"].groupby("code", observed=True).size().reindex(exp.index).fillna(0)
    miss = exp - act
    q["listed_missing_days"] = {"codes_with_missing": int((miss > 0).sum()), "total_missing": int(miss.clip(lower=0).sum()),
                                "worst": {str(k): int(v) for k, v in miss.sort_values(ascending=False).head(8).items()},
                                "note": "짧은 정지(VI·당일)는 거래량 0 봉이 있고, 장기 거래정지(회생·감사의견)는 봉 자체가 없다 — 재개일 수익률은 체결 불가"}
    q["zero_volume_days"] = int((p.volume == 0).sum())
    pc = p[p.src == "creon"].sort_values(["code", "date"])
    lf = np.log(pc.factor.astype("float64"))
    jump = lf.groupby(pc.code, observed=True).diff().abs()
    q["factor_jumps"] = {"events_over_0.5pct": int((jump > 0.005).sum()), "codes": int(pc.code[jump > 0.005].nunique()),
                         "max_abs_log_jump": float(jump.max()), "note": "액면분할·병합·무상증자 등 기업행위 수와 비교용"}
    q["factor_nan_creon"] = int(pc.factor.isna().sum())
    late = p[p.date >= NXT_FROM]
    q["regular_session_fix"] = {"rows": int(len(late)), "fixed_from_1m": int((late.reg_fix == 1).sum()),
                                "unavailable_nan": int((late.reg_fix == 2).sum()), "naver_unverified": int((late.reg_fix == 3).sum()),
                                "codes_all_unavailable": int((late[late.reg_fix == 2].groupby("code", observed=True).size() >= 15).sum())}
    ret = pc.groupby("code", observed=True).close.pct_change()
    q["abs_ret_over_35pct"] = {"n": int((ret.abs() > 0.35).sum()), "codes": int(pc.code[ret.abs() > 0.35].nunique()),
                               "note": "2015-06-15 이전 상하한 15% — 장기 정지 재개일(기준가 재평가) 포함"}
    Q["prices"] = q
    e2 = e[e.date >= "2014-01-01"]
    Q["daily_ext"] = {"rows": int(len(e2)), "codes": int(e2.code.nunique()),
                      "market_cap_zero_or_nan": int(((e2.market_cap <= 0) | e2.market_cap.isna()).sum()),
                      "shares_zero": int((e2.shares_listed <= 0).sum()),
                      "delisted_with_approx_mcap": int(e2[e2.mcap_approx == 1].code.nunique()),
                      "delisted_total": int((uni.status == "delisted").sum()),
                      "note": "상폐 종목 시총 = DART 보고서 발행주식수 × 네이버 수정 종가(근사, mcap_approx=1)"}
    lag = (fin.rcept_dt - fin.period_end).dt.days
    Q["fundamentals"] = {"company_quarters": int(len(fin)), "companies": int(fin.stock_code.nunique()),
                         "by_year": {int(k): int(v) for k, v in fin.groupby("bsns_year").stock_code.nunique().items()},
                         "fs_div": {str(k): int(v) for k, v in fin.fs_div.value_counts().items()},
                         "filing_lag_days": {"median": float(lag.median()), "p90": float(lag.quantile(0.9)), "p99": float(lag.quantile(0.99)),
                                             "negative": int((lag < 0).sum()), "over_180": int((lag > 180).sum())},
                         "sales_missing_pct": float(fin.sales.isna().mean() * 100) if "sales" in fin else None,
                         "q4_derived_missing_pct": float(fin[fin.q == 4].net_income.isna().mean() * 100),
                         "equity_nonpositive": int((fin.equity <= 0).sum()),
                         "note": "매출 결측 = 2015 Q4(분기보고서 없음) + 금융업(매출액 계정 없음); 접수일은 정정본이면 늦다(보수적)"}
    fin_codes = set("A" + fin.stock_code.astype(str))
    Q["coverage"] = {"price_codes_with_fin": int(len(set(p.code.astype(str)) & fin_codes)),
                     "n_without_fin": int(len(set(p.code.astype(str)) - fin_codes))}
    Q["index"] = {"rows": int(len(idx)), "indices": int(idx.code.nunique()), "kospi_trading_days_2014_": int(len(tdays))}
    os.makedirs(path("quality"), exist_ok=True)
    with open(path("quality", "panel_quality.json"), "w", encoding="utf-8") as f:
        json.dump(Q, f, ensure_ascii=False, indent=1, default=str)
    log("[panel] quality written", "build_panel.txt")
    return Q


def build_all():
    os.makedirs(path("derived"), exist_ok=True)
    p = build_prices()
    build_ext(p)
    del p
    build_fin()
    build_index()
    return quality()
