"""kr_equity 분석용 패널 — raw 원본 → derived/*.parquet(지워도 재생성되는 캐시) + quality/panel_quality.json

  prices_1d.parquet       code·date·open/high/low/close/volume/value(수정) · close_unadj · factor · src · reg_fix
                          reg_fix 0 정규장 그대로 / 1 2026-09-14~ 1분봉으로 정규장 복원 / 2 복원 불가(NaN) / 3 네이버(시간외 포함 여부 미확인)
  daily_ext.parquet       code·date·shares_listed·market_cap·foreign_hold_qty·foreign_hold_pct·inst_net_qty·mcap_src·mcap_approx
                          market_cap = 그날 실제 시총 (상장=Creon, 상폐=KRX 공식 일별 시총 raw/marcap) — mcap_src creon/krx
                          shares_listed = 수정 기준 주식수(시총 ÷ 수정 종가, Creon 필드 12 기준) — 그날의 실제 주식수가 아님
                          mcap_approx 는 예전 호환 열(항상 0). 쓰기 전 check_ext 검사(실패 시 파일 안 씀, quality/daily_ext_check.json)
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
    """실버 가격: 원천(raw/creon/candle_1d_unadj, 무수정) × 기업행위 원장 계수(raw/creon/corporate_actions). 착륙 구역의 수정 일봉은 쓰지 않는다."""
    from shared.data_library import corporate_actions
    from . import bronze
    u = pd.read_csv(bronze.unadj_path(code), parse_dates=["timestamp"]).rename(columns={"timestamp": "date"})
    u = u[u.date >= START].drop_duplicates("date").sort_values("date").reset_index(drop=True)
    d = u[["date"] + OHLCV].copy()
    d[OHLCV] = d[OHLCV].astype("float64")
    d["close_u"] = d.close
    f = corporate_actions.cumulative_factor(corporate_actions.events(bronze.actions_root(), code), d.date).to_numpy()
    for c in ("open", "high", "low", "close"):
        d[c] = d[c] * f
    d["volume"] = d.volume / f
    d["factor"] = f
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
    parts, missing, t0 = [], [], time.time()
    for i, r in enumerate(uni.itertuples(), 1):
        try:
            d = read_creon(r.code) if r.price_source == "creon" else read_naver(r.code)
        except FileNotFoundError:
            d = None
            if r.price_source == "creon":
                missing.append(r.code)
        if d is not None and len(d):
            for c in OHLCV + ["close_unadj", "factor"]:
                d[c] = d[c].astype("float32")
            parts.append(d)
        if i % 500 == 0:
            log(f"[panel] prices {i}/{len(uni)} ({time.time() - t0:.0f}s)", "build_panel.txt")
    if missing:
        raise RuntimeError(f"원천(raw/creon/candle_1d_unadj)에 없는 상장 종목 {len(missing)}개 {missing[:5]} — creon_bronze 단계를 먼저 실행하세요")
    p = pd.concat(parts, ignore_index=True)
    p["code"] = p.code.astype("category")
    p["src"] = p.src.astype("category")
    p.to_parquet(derived("prices_1d.parquet"), index=False)
    log(f"[panel] prices {len(p):,} rows, {p.code.nunique()} codes", "build_panel.txt")
    return p


# ---------------------------------------------------------------- 주식수·시총 (상장=Creon, 상폐=KRX 공식 marcap)
# 예전 상폐 종목 시총 = 'DART 보고서 유통주식수(접수일부터) × 네이버 수정 종가' 근사는 두 군데서 틀렸다(2026-10-21 실측, KRX 공식 대비
# 상폐 행의 29%가 2배 이상, 7%가 10배 이상): ① 수정 종가(뒤 기업행위 기준)에 그날 주식수를 곱해 감자·병합 전 구간이 병합 비율만큼
# 부풀고(분할·무상증자 전은 쪼그라듦) ② DART 보고서 단위 오기(카프로 2023 반기 40,000,000,000주 등 ×1000)를 그대로 받았다.
# → 상폐 종목 시총은 KRX 공식 일별 시총(그날 상장주식수 × 그날 실제 종가)으로 바꾸고, 쓰기 전에 check_ext 를 통과시킨다.
CHECK_IDENTITY = (0.5, 2.0)     # 시총 ÷ (수정 주식수 × 수정 종가) 허용 범위
CHECK_OFFICIAL = 3.0            # KRX 공식 시총 대비 배수
CHECK_OFFICIAL_RUN = 5          # ... 가 이만큼 연속(거래일)이면 실패 — 하루 이틀은 신주 상장일 반영 차이(흥아해운 2021-07 ×4)
CHECK_SPIKE = 10.0              # 수정 주식수가 이 배수 넘게 뛰었다가
CHECK_SPIKE_BACK = 2.0          # ... 이 배수 안으로 되돌아오면 단위 오기 모양
CHECK_SPIKE_WINDOW = 130        # ... 되돌아오는 기간(거래일, 보고서 두 번 사이 넘게)
CHECK_DOMINANCE = 0.5           # 한 종목 시총 ÷ 그날 전 종목 합계 상한


class ExtCheckError(RuntimeError):
    """daily_ext 검사 실패 — 파일을 쓰지 않는다(사례는 quality/daily_ext_check.json)."""


def read_marcap(codes=None) -> pd.DataFrame:
    """KRX 공식 일별 시세·시총(raw/marcap 연도별 최신 스냅샷) → code·date·krx_close·krx_shares·krx_mcap·krx_market (codes 는 A 붙은 코드)."""
    files = {y: f for y, f in store.marcap_files().items() if y >= START.year}
    if not files:
        raise RuntimeError("원천(raw/marcap)이 없다 — krx_marcap 단계를 먼저 실행하세요 (상폐 종목 시총·시총 대조의 원천)")
    gap = sorted(set(range(START.year, max(files) + 1)) - set(files))
    if gap:
        raise RuntimeError(f"raw/marcap 에 빠진 해 {gap} — krx_marcap 단계를 다시 실행하세요")
    flt = None if codes is None else [("Code", "in", sorted({c[1:] for c in codes}))]
    m = pd.concat([pd.read_parquet(f, columns=["Code", "Date", "Close", "Stocks", "Marcap", "Market"], filters=flt)
                   for _, f in sorted(files.items())], ignore_index=True)
    out = pd.DataFrame({"code": "A" + m.Code.astype(str), "date": pd.to_datetime(m.Date), "krx_close": m.Close.astype("float64"),
                        "krx_shares": m.Stocks.astype("float64"), "krx_mcap": m.Marcap.astype("float64"), "krx_market": m.Market.astype(str)})
    return out[out.date >= START].drop_duplicates(["code", "date"], keep="last").reset_index(drop=True)


def delisted_ext(nv: pd.DataFrame, uni: pd.DataFrame, krx: pd.DataFrame) -> pd.DataFrame:
    """상폐 종목(네이버 일봉) 행: market_cap = KRX 공식 시총, shares_listed = 시총 ÷ 네이버 수정 종가.
    shares_listed 는 Creon 필드 12 와 같은 '수정 기준' 주식수다(상장 종목도 시총 ÷ (주식수 × 수정 종가) 중앙값 0.9996) —
    그날의 실제 주식수가 아니라 수정 거래량과 짝이 맞는 값(회전율 = 수정 거래량 ÷ shares_listed)."""
    dl = pd.to_datetime(uni.set_index("code").delist_date, errors="coerce")
    k = krx[krx.code.isin(set(nv.code))]
    k = k[~(k.date >= k.code.map(dl))]                     # 상폐일 이후 같은 코드 = 코드 재사용(다른 회사)
    m = nv.merge(k[["code", "date", "krx_mcap"]], on=["code", "date"], how="inner")
    m = m[(m.close > 0) & (m.krx_mcap > 0)]
    return pd.DataFrame({"code": m.code.to_numpy(), "date": m.date.to_numpy(), "shares_listed": (m.krx_mcap / m.close).to_numpy(),
                         "market_cap": m.krx_mcap.to_numpy(), "foreign_hold_qty": np.nan, "foreign_hold_pct": np.nan,
                         "inst_net_qty": np.nan, "mcap_src": "krx"})


def _examples(d: pd.DataFrame, n: int = 20) -> list:
    d = d.head(n).copy()
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m-%d")
    return json.loads(d.to_json(orient="records", force_ascii=False))


def check_ext(e: pd.DataFrame, px: pd.DataFrame, krx: pd.DataFrame) -> dict:
    """daily_ext 를 쓰기 전 검사 — 하나라도 걸리면 ExtCheckError, 결과는 언제나 quality/daily_ext_check.json.
      identity   시총 ÷ (shares_listed × 수정 종가) 가 1/2~2 — 주식수·시총 단위 오기, 수정 기준 어긋남
      official   KRX 공식이 아닌 시총(Creon)이 KRX 공식과 3배 넘게 다른 날이 5거래일 이상 이어지면 실패
      spike      수정 주식수(시총 ÷ 수정 종가)가 10배 넘게 뛰었다가 130거래일 안에 2배 이내로 되돌아오고, 그 두 날 모두 그날 주식수
                 (시총 ÷ 무수정 종가)도 10배 넘게 움직였으면 실패 — 가격은 그대로인데 시총만 오가는 단위 오기의 모양.
                 정리매매 폭락·출자전환 신주처럼 한 방향으로 끝나는 변화, 무상증자 권리락~신주상장 사이(KRX 공식 시총도 한 달간
                 1/15 — 아리바이오홀딩스 2023-12-26~2024-01-26), 감자 뒤 출자전환처럼 기업행위로 설명되는 왕복은 진짜라 걸지 않는다
      dominance  한 종목 시총이 그날 전 종목 합계의 절반을 넘으면 실패
    px: code·date·close(수정)·close_unadj(무수정, 없으면 KRX 종가), krx: read_marcap() 결과."""
    x = e[["code", "date", "shares_listed", "market_cap", "mcap_src"]].copy()
    x["code"] = x.code.astype(str)
    x["mcap_src"] = x.mcap_src.astype(str)
    x = x.merge(px[["code", "date", "close", "close_unadj"]], on=["code", "date"], how="left")
    x = x.merge(krx[["code", "date", "krx_mcap", "krx_close"]], on=["code", "date"], how="left")
    x = x.sort_values(["code", "date"]).reset_index(drop=True)
    rep = {"checked_at": datetime.now().isoformat(timespec="seconds"), "rows": int(len(x)),
           "thresholds": {"identity": CHECK_IDENTITY, "official": CHECK_OFFICIAL, "official_run": CHECK_OFFICIAL_RUN,
                          "spike": CHECK_SPIKE, "spike_back": CHECK_SPIKE_BACK, "spike_window": CHECK_SPIKE_WINDOW,
                          "dominance": CHECK_DOMINANCE}}
    ok = (x.market_cap > 0) & (x.shares_listed > 0) & (x.close > 0)
    y = x[ok].reset_index(drop=True)

    r = y.market_cap / (y.shares_listed * y.close)
    bad = y.assign(ratio=r)[(r < CHECK_IDENTITY[0]) | (r > CHECK_IDENTITY[1])]     # 거른 뒤 assign 하면 빈 표가 r 의 색인을 통째로 받는다
    rep["identity"] = {"checked": int(len(y)), "violations": int(len(bad)),
                       "examples": _examples(bad[["code", "date", "mcap_src", "market_cap", "shares_listed", "close", "ratio"]])}

    o = x[(x.mcap_src != "krx") & (x.market_cap > 0) & (x.krx_mcap > 0)].reset_index(drop=True)
    q = o.market_cap / o.krx_mcap
    flag = (q > CHECK_OFFICIAL) | (q < 1 / CHECK_OFFICIAL)
    run = flag.groupby((~flag | (o.code != o.code.shift())).cumsum()).transform("sum")
    bad = o.assign(ratio=q, run=run)[flag & (run >= CHECK_OFFICIAL_RUN)]
    rep["official"] = {"checked": int(len(o)), "off_days": int(flag.sum()), "violations": int(len(bad)),
                       "codes": sorted(bad.code.unique().tolist())[:50],
                       "examples": _examples(bad[["code", "date", "mcap_src", "market_cap", "krx_mcap", "ratio", "run"]])}

    code = y.code.to_numpy()
    same = code[1:] == code[:-1]

    def step(v):
        s = np.log(v.to_numpy(dtype="float64"))
        d = np.full(len(s), np.nan)
        d[1:][same] = np.diff(s)[same]
        return s, d
    s, ds = step(y.market_cap / y.close)                                          # 수정 주식수
    _, ds_raw = step(y.market_cap / y.close_unadj.where(y.close_unadj > 0, y.krx_close))   # 그날 주식수
    big = np.log(CHECK_SPIKE)
    pos = np.flatnonzero(np.abs(ds) >= big)
    spikes = []
    for a, i in enumerate(pos):
        for j in pos[a + 1:]:
            if j - i > CHECK_SPIKE_WINDOW or code[j] != code[i]:
                break
            if np.sign(ds[j]) != np.sign(ds[i]) and abs(s[j] - s[i - 1]) <= np.log(CHECK_SPIKE_BACK):
                if np.isfinite(ds_raw[i]) and np.isfinite(ds_raw[j]) and min(abs(ds_raw[i]), abs(ds_raw[j])) < big:
                    break                                                         # 기업행위로 설명되는 왕복(가격도 같이 움직임)
                spikes.append({"code": code[i], "mcap_src": y.mcap_src.iat[i], "from": f"{y.date.iat[i]:%Y-%m-%d}",
                               "to": f"{y.date.iat[j]:%Y-%m-%d}", "jump": float(np.exp(ds[i])), "market_cap_peak": float(y.market_cap.iat[i])})
                break
    rep["spike"] = {"big_jumps": int(len(pos)), "violations": len(spikes), "examples": spikes[:20]}

    t = x[x.market_cap > 0]
    g = t.groupby("date").market_cap
    share = (g.max() / g.sum())
    top = t.loc[t.groupby("date").market_cap.idxmax(), ["date", "code", "mcap_src", "market_cap"]].set_index("date")
    bad = top.assign(share=share)[share > CHECK_DOMINANCE].reset_index()
    rep["dominance"] = {"dates": int(len(share)), "max_share": float(share.max()), "violations": int(len(bad)),
                        "examples": _examples(bad)}

    rep["passed"] = all(rep[k]["violations"] == 0 for k in ("identity", "official", "spike", "dominance"))
    os.makedirs(path("quality"), exist_ok=True)
    with open(path("quality", "daily_ext_check.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    if not rep["passed"]:
        raise ExtCheckError("daily_ext 검사 실패 — " + ", ".join(f"{k} {rep[k]['violations']}건" for k in ("identity", "official", "spike", "dominance")
                                                            if rep[k]["violations"]) + " (사례: quality/daily_ext_check.json, 파일은 쓰지 않음)")
    return rep


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
        d["mcap_src"] = "creon"
        parts.append(d)
    if prices is None:
        prices = pd.read_parquet(derived("prices_1d.parquet"), columns=["code", "date", "close", "close_unadj", "src"])
    px = prices[["code", "date", "close", "close_unadj", "src"]].copy()
    px["code"] = px.code.astype(str)
    px["close"] = px.close.astype("float64")
    px["close_unadj"] = px.close_unadj.astype("float64")
    krx = read_marcap()
    parts.append(delisted_ext(px[px.src == "naver"][["code", "date", "close"]], uni, krx))
    e = pd.concat(parts, ignore_index=True)
    e["mcap_approx"] = np.int8(0)                          # 예전 호환 열 — 근사 시총은 더 없다(상폐도 KRX 공식)
    rep = check_ext(e, px, krx)
    del krx
    e["code"] = e.code.astype("category")
    e["mcap_src"] = e.mcap_src.astype("category")
    e["foreign_hold_pct"] = e.foreign_hold_pct.astype("float32")
    e.to_parquet(derived("daily_ext.parquet"), index=False)
    log(f"[panel] ext {len(e):,} rows, {e.code.nunique()} codes (상폐 KRX 공식 {int((e.mcap_src == 'krx').sum()):,}행) — 검사 통과 "
        f"(Creon↔KRX 3배 초과 {rep['official']['off_days']}일·연속 없음, 큰 주식수 점프 {rep['spike']['big_jumps']}건·되돌림 없음, "
        f"최대 단일 비중 {rep['dominance']['max_share']:.1%})", "build_panel.txt")
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
    chk = store.read_json(path("quality", "daily_ext_check.json")) or {}
    Q["daily_ext"] = {"rows": int(len(e2)), "codes": int(e2.code.nunique()),
                      "market_cap_zero_or_nan": int(((e2.market_cap <= 0) | e2.market_cap.isna()).sum()),
                      "shares_zero": int((e2.shares_listed <= 0).sum()),
                      "rows_by_src": {str(k): int(v) for k, v in e2.mcap_src.value_counts().items()},
                      "delisted_with_mcap": int(e2[e2.mcap_src == "krx"].code.nunique()),
                      "delisted_total": int((uni.status == "delisted").sum()),
                      "check": {k: chk[k]["violations"] for k in ("identity", "official", "spike", "dominance") if k in chk},
                      "check_passed": chk.get("passed"),
                      "note": "시총: 상장=Creon, 상폐=KRX 공식 일별 시총(raw/marcap). shares_listed 는 수정 기준(시총 ÷ 수정 종가)"}
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
