"""기업행위 원장 (1층: Creon 수정주가 ÷ 무수정주가 계수 변화) — 원천은 무수정 그대로, 수정은 이 원장으로 계산한다.

원칙
- 원천(raw)은 받은 그대로의 무수정 가격. 수정주가는 원장의 계수로 '읽을 때' 만든다(재현 가능: 기준일 지정).
- 원장은 추가만 한다. 같은 이벤트를 다시 보면 확인 시각만 갱신, 계수가 달라지면 개정 기록을 남기고 '확인 필요'.
- 수정 계산은 이 모듈의 adjust_* 만 한다(다른 곳에서 adj/raw 비율을 직접 쓰지 않는다).

계수 정의
- 이벤트 r (ex_date E): E 이전 날의 무수정 가격에 곱하면 E 이후와 같은 기준이 되는 값.
  5:1 병합 → r=5, 1:2 분할 → r=0.5, ETF 분배금 0.4% → r≈0.996
- 누적 계수 F_D(basis) = ∏ r_e  (D < ex_date_e ≤ basis). 수정가 = 무수정가 × F_D, 수정 거래량 = 무수정 거래량 / F_D.

감지 (실측 근거, 2026-10-11 staging 2,739 종목)
- Creon 수정 일봉은 원 단위 반올림 → 비율 log(adj/raw) 가 매일 ±(0.5/adj) 흔들린다(25,000여 건, 대부분 <0.05%).
- 진짜 이벤트는 |Δlog| > 3 × (0.5/adj_t + 0.5/adj_{t-1}) 이면서 0.1% 이상 (757 건, 모두 ≥0.2%, 되돌림형 0).
- Creon 수정주가는 분할·병합·무상/유상증자·주식배당·ETF 분배금을 반영하고, 일반 주식 현금배당은 반영하지 않는다
  (삼성전자·신한지주·KB금융 2년간 계수 불변) → 현금배당은 2층(KIS 예탁원 정보)에서.
"""
import json
import os
from datetime import datetime, timezone
from fractions import Fraction
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

SOURCE_CREON = "creon_adj_vs_unadj"
NOISE_MULT = 3.0
MIN_ABS_LOG = 0.001
FACTOR_TOL = 0.002          # 같은 이벤트로 볼 계수 차이(상대)
LIVE_STATUSES = ("detected", "confirmed")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kind_hint(r: float) -> str:
    """계수만으로 추정한 종류(확정은 2층). r>1 = 과거 가격을 올림(병합·감자), r<1 = 내림(분할·증자·배당·분배)."""
    if r <= 0:
        return "invalid"
    frac = Fraction(r).limit_denominator(100)
    simple = abs(float(frac) / r - 1) < 0.01 and frac != 1
    if simple and r > 1.2:
        return "reverse_split_or_capital_reduction"     # 병합·감자
    if simple and r < 0.85:
        return "split_or_bonus_issue"                   # 분할·무상증자
    if 0.97 <= r < 1:
        return "small_dilution_or_distribution"         # 주식배당·ETF 분배금·소규모 증자
    return "rights_issue_or_other"


def _close_series(df: pd.DataFrame) -> pd.Series:
    """일봉 → 날짜 색인 종가. 옛 수집 열(upbit_close)과 표준 열(close) 둘 다 받는다."""
    d = df.drop_duplicates("timestamp")
    return d.set_index(pd.to_datetime(d["timestamp"]))["upbit_close" if "upbit_close" in d else "close"]


def detect(adj: pd.DataFrame, raw: pd.DataFrame, observed_at: Optional[str] = None) -> List[dict]:
    """수정·무수정 일봉(timestamp + upbit_close 또는 close) → 이벤트 목록. 같은 출처·같은 정의의 종가끼리만 비교한다."""
    a, r = _close_series(adj), _close_series(raw)
    df = pd.DataFrame({"a": a, "r": r}).dropna().sort_index()
    df = df[(df["a"] > 0) & (df["r"] > 0)]
    if len(df) < 2:
        return []
    lf = np.log(df["a"] / df["r"])
    d = lf.diff()
    bound = 0.5 / df["a"] + 0.5 / df["a"].shift(1)
    hit = (d.abs() > NOISE_MULT * bound) & (d.abs() > MIN_ABS_LOG)
    idx = list(np.flatnonzero(hit.to_numpy()))
    # 계수 = 이벤트 사이 구간 중앙값의 차 (하루 쌍보다 반올림 잡음이 작다).
    # 단순 분수로 맞추지 않는다: 병합·감자 기준가는 호가 단위로 반올림돼 4:1 병합의 실제 계수가 4.0027 (742원×4=2,968→2,970)
    cuts = [0] + idx + [len(df)]
    seg = [float(np.median(lf.iloc[cuts[k]:cuts[k + 1]])) for k in range(len(cuts) - 1)]
    out = []
    for k, i in enumerate(idx):
        prev, cur = df.iloc[i - 1], df.iloc[i]
        ratio = float(np.exp(seg[k] - seg[k + 1]))   # F_{E-1} / F_E
        frac = Fraction(ratio).limit_denominator(20)
        nominal = f"{frac.numerator}:{frac.denominator}" if frac != 1 and abs(float(frac) / ratio - 1) < 0.01 else None
        out.append({
            "ex_date": df.index[i].strftime("%Y-%m-%d"),
            "price_factor": round(ratio, 8),
            "nominal_ratio": nominal,             # 참고: 가까운 단순 비율(병합 4:1 등) — 계산에는 price_factor 만 쓴다
            "kind_hint": kind_hint(ratio),
            "status": "detected",
            "source": SOURCE_CREON,
            "evidence": {"prev_date": df.index[i - 1].strftime("%Y-%m-%d"),
                         "prev_adj_close": float(prev["a"]), "prev_raw_close": float(prev["r"]),
                         "adj_close": float(cur["a"]), "raw_close": float(cur["r"]),
                         "noise_bound": round(float(bound.iloc[i]), 8)},
            "observed_at": observed_at or _now(),
        })
    return out


# ---------------------------------------------------------------- 원장 (종목별 JSON, 추가만)
def ledger_path(root: str, symbol: str) -> str:
    return os.path.join(root, f"{symbol}.json")


def load(root: str, symbol: str) -> dict:
    p = ledger_path(root, symbol)
    if not os.path.exists(p):
        return {"symbol": symbol, "events": [], "observations": []}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(root: str, led: dict):
    os.makedirs(root, exist_ok=True)
    p = ledger_path(root, led["symbol"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def update(root: str, symbol: str, detected: Iterable[dict], window: tuple, observed_at: Optional[str] = None) -> dict:
    """감지 결과를 원장에 합친다 — 추가만. window=(첫날, 마지막날) 은 이번 관측이 덮은 기간.

    - 새 이벤트: 추가(status=detected)
    - 같은 날·같은 계수: last_seen_at 갱신
    - 같은 날·다른 계수: revisions 에 남기고 status=conflict (사용자 확인)
    - 관측 기간 안인데 이번에 안 보인 기존 이벤트: missing_since 표시(삭제하지 않음)
    """
    observed_at = observed_at or _now()
    led = load(root, symbol)
    by_date = {e["ex_date"]: e for e in led["events"]}
    rep = {"added": [], "seen": [], "conflict": [], "missing": []}
    seen_dates = set()
    for ev in detected:
        dte = ev["ex_date"]
        seen_dates.add(dte)
        cur = by_date.get(dte)
        if cur is None:
            ev = dict(ev, first_seen_at=observed_at, last_seen_at=observed_at, revisions=[])
            led["events"].append(ev)
            by_date[dte] = ev
            rep["added"].append(dte)
        elif abs(ev["price_factor"] / cur["price_factor"] - 1) <= FACTOR_TOL:
            cur["last_seen_at"] = observed_at
            cur.pop("missing_since", None)
            rep["seen"].append(dte)
        else:
            cur["revisions"].append({"at": observed_at, "price_factor": ev["price_factor"], "evidence": ev["evidence"]})
            cur["status"] = "conflict"
            rep["conflict"].append(dte)
    lo, hi = window
    for e in led["events"]:
        if e["source"] == SOURCE_CREON and lo < e["ex_date"] <= hi and e["ex_date"] not in seen_dates \
                and e["status"] in LIVE_STATUSES and "missing_since" not in e:
            e["missing_since"] = observed_at
            rep["missing"].append(e["ex_date"])
    led["events"].sort(key=lambda e: e["ex_date"])
    led["observations"].append({"at": observed_at, "window": [lo, hi], "source": SOURCE_CREON,
                                **{k: len(v) for k, v in rep.items()}})
    _write(root, led)
    return rep


def events(root: str, symbol: str, statuses=LIVE_STATUSES) -> List[dict]:
    """수정 계산에 쓰는 이벤트 — missing_since 가 붙은 것(관측 기간 안인데 최근 수정·무수정 비교에서 더는 안 보임)은 뺀다.
    장중 수집분처럼 일시적 어긋남이 이벤트로 잡혔다가 사라진 경우, 남겨 두면 과거 전체 수정가가 ±1~3% 틀어진다(2026-10-19 장중 수집 627종목)."""
    return [e for e in load(root, symbol)["events"] if e["status"] in statuses and "missing_since" not in e]


# ---------------------------------------------------------------- 수정 계산 (유일한 지점)
def cumulative_factor(evs: List[dict], dates: Iterable, basis_date=None) -> pd.Series:
    """날짜별 누적 계수 F_D = ∏ r (D < ex_date ≤ basis_date). basis_date 생략 = 원장의 모든 이벤트(최신 기준)."""
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    f = pd.Series(1.0, index=idx)
    basis = pd.Timestamp(basis_date) if basis_date is not None else None
    for e in evs:
        ex = pd.Timestamp(e["ex_date"])
        if basis is not None and ex > basis:
            continue
        f[idx < ex] *= e["price_factor"]
    return f


def adjust_prices(raw: pd.DataFrame, evs: List[dict], basis_date=None, date_col="timestamp",
                  price_cols=("upbit_open", "upbit_high", "upbit_low", "upbit_close"), volume_col="upbit_volume") -> pd.DataFrame:
    """무수정 봉(일봉·분봉) → 수정 봉. 거래대금은 그대로(분할 불변)."""
    out = raw.copy()
    day = pd.to_datetime(out[date_col]).dt.normalize()
    f = cumulative_factor(evs, day.unique(), basis_date)
    fac = day.map(f).to_numpy()
    for c in price_cols:
        if c in out:
            out[c] = out[c].to_numpy(dtype=float) * fac
    if volume_col and volume_col in out:
        out[volume_col] = out[volume_col].to_numpy(dtype=float) / fac
    return out


def reconstruction_error(adj: pd.DataFrame, raw: pd.DataFrame, evs: List[dict]) -> pd.Series:
    """원장으로 재구성한 수정 종가 vs Creon 수정 종가의 상대 오차를 반올림 한계로 나눈 값(≤1 이면 반올림 범위 안)."""
    a = adj.drop_duplicates("timestamp").set_index(pd.to_datetime(adj.drop_duplicates("timestamp")["timestamp"]))["upbit_close"]
    rec = adjust_prices(raw.drop_duplicates("timestamp"), evs)
    rc = rec.set_index(pd.to_datetime(rec["timestamp"]))["upbit_close"]
    both = pd.DataFrame({"a": a, "rc": rc}).dropna()
    both = both[both["a"] > 0]
    return ((both["rc"] - both["a"]).abs() / 0.5).rename("err_over_rounding")
