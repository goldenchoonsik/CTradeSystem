# -*- coding: utf-8 -*-
"""
CTradeSystem - Interval Consistency Audit (상·하위 봉 집계 정합 감사)
=======================================================================
[T-6] 배경
----------
같은 종목의 하위 봉(예: 10초)과 상위 봉(예: 1분)은 **같은 체결 흐름의 두 표현**이므로,
하위 봉을 상위 봉 길이로 집계하면 상위 봉과 일치해야 한다. 이 불변식이 깨지면
어느 쪽을 믿어야 할지 알 수 없는 데이터가 학습에 들어간다.

실측 (2026-09-28, 069500)
--------------------------
  2026-07-21 ~ 08-18 (20거래일): 10초봉을 분 단위로 집계하면
    고가/저가/거래량은 1분봉과 99.7% 일치하는데 **시가와 종가가 서로 뒤바뀌어** 있었다.
    → 분봉 하나를 10초봉 여섯 개로 펼치는 과정에서 **시간 순서가 뒤집힌** 것.
  이 역순 구간은 참조 계열의 미래참조를 상쇄해, 전구간 정합 감사까지 통과시켰다.

설계 원칙
---------
1. **라벨 관례를 가정하지 않는다.** 하위 봉과 상위 봉의 타임스탬프 관례(봉 시작/봉 확정)가
   다를 수 있으므로, 두 계열을 맞추는 오프셋을 **데이터에서 고른다**.
   고가/저가는 봉 내부 순서와 무관하므로, 오프셋 탐색의 기준으로 쓴다.
2. **판정은 과반 규칙으로 한다.** 임의의 허용오차 상수를 두지 않는다.
   역순 일치가 정방향 일치보다 많으면 역순으로 판정한다.
3. **시가=종가인 봉은 판정에서 제외한다.** 정방향과 역순을 구별할 수 없기 때문이다.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

# 오프셋 탐색 범위 (상위 봉 수). 라벨 관례 차이는 최대 1봉이므로 ±1 이면 충분하다.
_OFFSET_CANDIDATES = (0, 1, -1)
# 하위 봉 라벨 → 구간 시작 이동 탐색 범위 (하위 봉 수). 하위 봉이 bar_close 라벨이면 -1 이다.
# 상위 봉 오프셋만 보면 '상위 봉 경계가 하위 봉 1개만큼 밀린' 집계(15분봉에 09:14~09:15 체결이 빠지고
# 다음 봉으로 넘어간 경우)를 잡지 못한다 — 같은 파이프라인의 두 산출물은 그래도 서로 맞아 보이기 때문이다.
_FINE_SHIFT_CANDIDATES = (0, -1, 1)

# 판정에 필요한 최소 봉 수
MIN_BARS = 30


class IntervalConsistencyError(RuntimeError):
    """하위 봉을 집계한 결과가 상위 봉과 어긋날 때 발생."""

    def __init__(self, findings: Sequence["IntervalConsistencyFinding"]):
        self.findings = list(findings)
        super().__init__(
            "상·하위 봉 집계 정합 감사 실패:\n"
            + "\n".join(f"  · {f.describe()}" for f in self.findings)
        )


@dataclass(frozen=True)
class IntervalConsistencyFinding:
    symbol: str
    fine_interval: str
    coarse_interval: str
    offset_bars: int          # 하위 봉 집계 라벨을 상위 봉 라벨에 맞추는 오프셋
    n_bars: int               # 판정에 쓰인 봉 수 (시가≠종가인 봉만)
    extrema_rate: float       # 고가/저가 일치율 (봉 내부 순서와 무관)
    forward_rate: float       # 시가=시가, 종가=종가 일치율
    reversed_rate: float      # 시가=종가, 종가=시가 일치율 (시간 역순)
    ok: bool
    detail: str = ""
    bad_dates: tuple = ()
    fine_shift_bars: int = 0  # 하위 봉 라벨을 구간 시작으로 옮기는 이동 (하위 봉 수, bar_close 면 -1)

    def describe(self) -> str:
        verdict = "정상" if self.ok else "불일치"
        msg = (
            f"{self.symbol} {self.fine_interval}→{self.coarse_interval}: {verdict} "
            f"(오프셋 {self.offset_bars:+d}봉, 하위이동 {self.fine_shift_bars:+d}봉, 판정봉 {self.n_bars:,}개, "
            f"고저 {self.extrema_rate:.1%} / 정방향 {self.forward_rate:.1%} / 역순 {self.reversed_rate:.1%})"
        )
        if self.detail:
            msg += f" — {self.detail}"
        if self.bad_dates:
            shown = ", ".join(str(d) for d in self.bad_dates[:5])
            more = f" 외 {len(self.bad_dates) - 5}일" if len(self.bad_dates) > 5 else ""
            msg += f" [역순 일자: {shown}{more}]"
        return msg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "fine_interval": self.fine_interval,
            "coarse_interval": self.coarse_interval,
            "offset_bars": self.offset_bars,
            "fine_shift_bars": self.fine_shift_bars,
            "n_bars": self.n_bars,
            "extrema_rate": round(self.extrema_rate, 6),
            "forward_rate": round(self.forward_rate, 6),
            "reversed_rate": round(self.reversed_rate, 6),
            "ok": self.ok,
            "detail": self.detail,
            "bad_dates": [str(d) for d in self.bad_dates],
        }


def _pick(df: pd.DataFrame, *names: str) -> Optional[str]:
    """여러 스키마(원천별 컬럼명 변형)에서 같은 역할의 컬럼을 고른다."""
    for n in names:
        if n in df.columns:
            return n
    return None


def _ohlc_columns(df: pd.DataFrame) -> Optional[Dict[str, str]]:
    o = _pick(df, "open", "target_open", "upbit_open")
    h = _pick(df, "high", "target_high", "upbit_high")
    l = _pick(df, "low", "target_low", "upbit_low")
    c = _pick(df, "close", "target_close", "upbit_close")
    if not all([o, h, l, c]):
        return None
    return {"open": o, "high": h, "low": l, "close": c}


def audit_interval_consistency(
    fine_df: pd.DataFrame,
    coarse_df: pd.DataFrame,
    symbol: str,
    fine_interval: str,
    coarse_interval: str,
    ts_col: str = "timestamp",
) -> Optional[IntervalConsistencyFinding]:
    """하위 봉을 상위 봉 길이로 집계해 상위 봉과 일치하는지 감사한다.

    판정 불가(컬럼 부재/표본 부족)면 ``None`` 을 돌려준다 — 감사 자체를 건너뛴다는 뜻이며,
    '통과'와 구분된다.
    """
    if fine_df is None or coarse_df is None or fine_df.empty or coarse_df.empty:
        return None
    fcols, ccols = _ohlc_columns(fine_df), _ohlc_columns(coarse_df)
    if not fcols or not ccols:
        return None

    f = fine_df[[ts_col] + list(fcols.values())].copy()
    c = coarse_df[[ts_col] + list(ccols.values())].copy()
    f[ts_col] = pd.to_datetime(f[ts_col])
    c[ts_col] = pd.to_datetime(c[ts_col])
    f = f.sort_values(ts_col)
    c = c.sort_values(ts_col).rename(columns={v: f"c_{k}" for k, v in ccols.items()})

    step = pd.Timedelta(_interval_to_timedelta(coarse_interval))
    fine_step = pd.Timedelta(_interval_to_timedelta(fine_interval))
    anchor_candidates = (
        (pd.Timedelta(0), pd.Timedelta(hours=9))
        if step >= pd.Timedelta(days=1)
        else (pd.Timedelta(0),)
    )

    best = None
    for anchor in anchor_candidates:
        for fshift in _FINE_SHIFT_CANDIDATES:
            start = f[ts_col] + fshift * fine_step - anchor
            f["_bucket"] = start.dt.floor(step)
            agg = f.groupby("_bucket").agg(
                f_open=(fcols["open"], "first"),
                f_high=(fcols["high"], "max"),
                f_low=(fcols["low"], "min"),
                f_close=(fcols["close"], "last"),
            ).reset_index()

            for off in _OFFSET_CANDIDATES:
                cc = c.copy()
                cc["_bucket"] = cc[ts_col] - off * step
                j = agg.merge(cc[["_bucket", "c_open", "c_high", "c_low", "c_close"]], on="_bucket", how="inner")
                if len(j) < MIN_BARS:
                    continue
                extrema = float((np.isclose(j.f_high, j.c_high) & np.isclose(j.f_low, j.c_low)).mean())
                if best is None or extrema > best[1]:
                    best = (off, extrema, j, fshift)

    if best is None:
        return None
    offset, extrema_rate, j, fine_shift = best

    # 시가=종가인 봉은 정방향/역순을 구별할 수 없다
    determinate = j[~np.isclose(j.c_open, j.c_close)]
    if len(determinate) < MIN_BARS:
        return None

    forward = np.asarray(np.isclose(determinate.f_open, determinate.c_open)
                         & np.isclose(determinate.f_close, determinate.c_close))
    backward = np.asarray(np.isclose(determinate.f_open, determinate.c_close)
                          & np.isclose(determinate.f_close, determinate.c_open))
    forward_rate = float(forward.mean())
    reversed_rate = float(backward.mean())

    bad_dates: tuple = ()
    detail = ""
    ok = True
    if reversed_rate > forward_rate:
        ok = False
        detail = "하위 봉의 봉 내부 시간 순서가 뒤집혀 있습니다"
        d = determinate.assign(_rev=backward, _d=determinate["_bucket"].dt.date)
        per_day = d.groupby("_d")["_rev"].mean()
        bad_dates = tuple(per_day[per_day > 0.5].index)
    elif extrema_rate <= 0.5:
        ok = False
        detail = "고가/저가가 상위 봉과 과반 불일치 — 두 계열의 원천이 어긋났습니다"

    return IntervalConsistencyFinding(
        symbol=symbol, fine_interval=fine_interval, coarse_interval=coarse_interval,
        offset_bars=offset, n_bars=len(determinate), extrema_rate=extrema_rate,
        forward_rate=forward_rate, reversed_rate=reversed_rate, ok=ok,
        detail=detail, bad_dates=bad_dates, fine_shift_bars=fine_shift,
    )


def _interval_to_timedelta(interval: str) -> pd.Timedelta:
    """'10s'/'1m'/'15m'/'1h'/'1d' 형태의 인터벌 문자열을 Timedelta 로 변환."""
    s = str(interval or "").strip().lower()
    num = "".join(ch for ch in s if ch.isdigit()) or "1"
    unit = "".join(ch for ch in s if ch.isalpha()) or "m"
    mapping = {"s": "s", "sec": "s", "m": "min", "min": "min", "h": "h", "hour": "h", "d": "D", "day": "D"}
    return pd.Timedelta(f"{int(num)}{mapping.get(unit, 'min')}")


def assert_interval_consistency(
    fine_df: pd.DataFrame,
    coarse_df: pd.DataFrame,
    symbol: str,
    fine_interval: str,
    coarse_interval: str,
    ts_col: str = "timestamp",
    log_fn=None,
) -> Optional[IntervalConsistencyFinding]:
    """감사 후 불일치면 ``IntervalConsistencyError`` 를 던진다 (fail-closed)."""
    finding = audit_interval_consistency(fine_df, coarse_df, symbol, fine_interval, coarse_interval, ts_col)
    if finding is None:
        if log_fn:
            log_fn(f"  ⏭️ [집계 정합] {symbol} {fine_interval}→{coarse_interval}: 판정 보류 (대조 가능한 봉 부족)")
        return None
    if log_fn:
        log_fn(f"  {'✅' if finding.ok else '🚨'} [집계 정합] {finding.describe()}")
    if not finding.ok:
        raise IntervalConsistencyError([finding])
    return finding
