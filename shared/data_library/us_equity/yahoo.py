"""Yahoo chart API 응답 → 무수정 일봉 + 기업행위. 네트워크·저장 없이 순수 변환만 한다(테스트 대상)."""
import json
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
UA = {"User-Agent": "Mozilla/5.0"}
PERIOD1_ALL = -2208988800              # 1900-01-01 — 상장 이후 전체
BAR_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
ACTION_COLUMNS = ["date", "type", "numerator", "denominator", "amount", "yahoo_amount"]
PRICE_DECIMALS = 4


def yahoo_symbol(sym: str) -> str:
    """nasdaqtrader(CQS) 표기 → Yahoo: BRK.B→BRK-B, ABR$D→ABR-PD."""
    return sym.replace("$", "-P").replace(".", "-")


def fetch_chart(sym: str, period1: int = PERIOD1_ALL, session: Optional[requests.Session] = None,
                log=print) -> Tuple[Optional[bytes], Optional[str]]:
    """일봉·배당·분할 응답 원본. (bytes | None, 실패 사유 | None) — 429·5xx·네트워크 오류는 물러났다 재시도."""
    sess = session or requests.Session()
    params = {"period1": period1, "period2": int(time.time()), "interval": "1d",
              "events": "div,split", "includeAdjustedClose": "true"}
    reason = "unknown"
    for attempt in range(6):
        try:
            r = sess.get(CHART_URL.format(sym=sym), params=params, headers=UA, timeout=30)
        except requests.RequestException as e:
            reason = f"net:{type(e).__name__}"
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            reason = f"http{r.status_code}"
            log(f"{sym} HTTP {r.status_code} — {60 * (attempt + 1)}s 대기")
            time.sleep(60 * (attempt + 1))
            continue
        try:
            j = r.json()
        except ValueError:
            return None, f"http{r.status_code}:not-json"
        res = (j.get("chart") or {}).get("result")
        if not res or not res[0].get("timestamp"):
            err = (j.get("chart") or {}).get("error") or {}
            return None, f"empty:{err.get('code', r.status_code)}"
        return r.content, None
    return None, reason


def snap_price(p: np.ndarray) -> np.ndarray:
    """Yahoo 가격은 float32 정밀도(상대 6e-8)라 되돌리면 2447.0 이 2446.99984 처럼 된다. 그 오차 안에서 센트와 같으면 센트로,
    아니면(1달러 미만 호가·숨은 조정 구간) 소수 4자리로 둔다."""
    p = np.asarray(p, dtype="float64")
    cents = np.round(p, 2)
    near = np.abs(p - cents) <= 2e-7 * np.abs(p) + 1e-9
    return np.where(near, cents, np.round(p, PRICE_DECIMALS))


def _local_dates(epochs, tz: str) -> pd.Series:
    """epoch → 거래소 현지 날짜 문자열. 뉴욕은 빠른 길: 일봉·이벤트 시각은 정규장 시작(09:30 ET) 근처라
    EST(UTC−5) 고정 오프셋으로 빼도 서머타임과 무관하게 같은 날짜다(05:00~24:00 ET 이면 성립)."""
    e = np.asarray(epochs, dtype="int64")
    if tz == "America/New_York":
        return pd.Series(np.datetime_as_string((e - 5 * 3600).astype("datetime64[s]"), unit="D"))
    return pd.to_datetime(pd.Series(e), unit="s", utc=True).dt.tz_convert(tz).dt.strftime("%Y-%m-%d")


def parse_chart(raw: bytes) -> Tuple[pd.DataFrame, pd.DataFrame, Dict]:
    """응답 → (무수정 일봉, 기업행위, 요약).

    일봉: timestamp='거래소 현지 세션 날짜 00:00:00'. 빼는 행 — 전부 빈 행(무거래일·장 전 자리표시),
    진행 중 세션 봉(regularMarketTime < 정규장 마감). 분할 이전 봉은 가격×누적분할, 거래량÷누적분할로 되돌린다.
    """
    r = json.loads(raw)["chart"]["result"][0]
    meta = r.get("meta") or {}
    tz = meta.get("exchangeTimezoneName") or "America/New_York"
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    bars = pd.DataFrame({"epoch": ts, **{k: q.get(k) or [None] * len(ts) for k in ("open", "high", "low", "close", "volume")}})
    info = {"symbol": meta.get("symbol"), "currency": meta.get("currency"), "instrument_type": meta.get("instrumentType"),
            "rows_received": len(bars), "dropped_null": 0, "dropped_partial": 0, "dropped_incomplete": 0}

    # 진행 중 세션: 정규장 시작 이후 봉인데 마지막 체결 시각이 정규장 마감 전이면 아직 확정되지 않은 값이다
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    if reg.get("start") and reg.get("end") and (meta.get("regularMarketTime") or 0) < reg["end"]:
        live = bars["epoch"] >= reg["start"]
        info["dropped_incomplete"] = int((live & bars["close"].notna()).sum())
        bars = bars[~live]
    null = bars[["open", "high", "low", "close"]].isna().all(axis=1)
    # 가격이 일부만 빈 봉 = 아직 확정되지 않은 봉(2026-10-15 실측: 마감 직후 응답 3,247종목의 그날 봉이 종가만 비어 있었다) — 저장하지 않고 다음 수집에서 받는다
    partial = bars[["open", "high", "low", "close"]].isna().any(axis=1) & ~null
    info["dropped_null"] = int(null.sum())
    info["dropped_partial"] = int(partial.sum())
    bars = bars[~null & ~partial].copy()

    ev = r.get("events") or {}
    sp_raw = list((ev.get("splits") or {}).values())
    dv_raw = list((ev.get("dividends") or {}).values())
    ev_dates = _local_dates([int(e["date"]) for e in sp_raw + dv_raw], tz).tolist() if sp_raw or dv_raw else []
    splits = sorted(((ev_dates[i], float(s["numerator"]), float(s["denominator"])) for i, s in enumerate(sp_raw)),
                    key=lambda x: x[0])
    bars["date"] = _local_dates(bars["epoch"].astype("int64"), tz).values if len(bars) else []

    valid = [(sd, num / den) for sd, num, den in splits if num > 0 and den > 0]
    sdates = np.array([sd for sd, _ in valid], dtype=object)
    # after[i] = i번째 분할부터 끝까지의 누적 비율 → 날짜 d 의 계수 = d 보다 뒤(ex-date > d)인 첫 분할부터의 누적
    after = np.append(np.cumprod([r for _, r in valid][::-1])[::-1], 1.0) if valid else np.array([1.0])

    def factor(d) -> np.ndarray:
        return after[np.searchsorted(sdates, np.asarray(d, dtype=object), side="right")] if valid else np.ones(np.size(d))

    if len(bars):
        f = factor(bars["date"].values)
        for c in ("open", "high", "low", "close"):
            bars[c] = snap_price(pd.to_numeric(bars[c], errors="coerce").values * f)
        bars["volume"] = (pd.to_numeric(bars["volume"], errors="coerce") / f).round(0)
    if len(bars):
        out = pd.DataFrame({"timestamp": bars["date"] + " 00:00:00", **{c: bars[c].values for c in BAR_COLUMNS[1:]}})
    else:                                  # 빈 응답(장 전 자리표시만 있던 신규 종목 등) — 열 형식은 유지
        out = pd.DataFrame({c: pd.Series(dtype=object if c == "timestamp" else float) for c in BAR_COLUMNS})
    out = out.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)

    acts: List[dict] = [{"date": d, "type": "split", "numerator": n, "denominator": m, "amount": None, "yahoo_amount": None}
                        for d, n, m in splits]
    dv_dates = ev_dates[len(sp_raw):]
    dv_factor = factor(dv_dates) if dv_raw else []
    for dv, d, f in zip(dv_raw, dv_dates, dv_factor):
        amt = float(dv["amount"])
        acts.append({"date": d, "type": "dividend", "numerator": None, "denominator": None,
                     "amount": round(amt * float(f), 6), "yahoo_amount": amt})
    actions = pd.DataFrame(acts, columns=ACTION_COLUMNS).sort_values(["date", "type"]).reset_index(drop=True)
    info["rows"] = len(out)
    return out, actions, info
