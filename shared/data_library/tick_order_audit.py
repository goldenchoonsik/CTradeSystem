# -*- coding: utf-8 -*-
"""
CTradeSystem - Tick Minute-Order Audit (분 단위 틱의 분 내부 순서 감사)
=========================================================================
[T-6] 배경
----------
크레온 틱 원천은 **초(second) 정보가 없다** — 한 분의 모든 틱이 같은 분 타임스탬프를 갖는다.
그래서 10초봉 빌더는 분 안의 **행 순번**으로 6개 버킷에 나눠 담는다
(`normalized_builder.build_normalized_10s_stock`, 합성 시각). 결국 **행 순서가 곧 시간축**이다.

행 순서가 뒤집히면 그 분의 10초봉 6개가 통째로 시간 역주행한다. 값의 집합(고가·저가·거래량)은
그대로라서 행수·결측·합계 검사로는 **절대** 드러나지 않는다.

실측 (2026-09-28, 069500)
--------------------------
  2026-07-21~08-18 (20거래일): 분 첫 틱이 1분봉 **종가**와 일치 100.0% (정상은 시가와 일치해야 함)
  2026-08-19~09-23 (20거래일): 정상 100.0%
  → 옛 수집분이 최신→과거 순서로 저장돼 있었다. 현재 수집 경로(raw_day_store._load_new_groups)는
    수신 흐름 방향을 보고 뒤집으므로 새 데이터는 정상이다.

판정 방법
---------
같은 종목의 **1분봉**을 정답지로 쓴다 (다른 API·다른 보존기간의 독립 원천).
분 M 의 틱 묶음은 1분봉 한 개에 대응하며, 어느 라벨에 대응하는지는 수집처 원장이 선언한
타임스탬프 규약에서 유도한다 (`market_policies.bar_bucket_offset`) — 날짜도 오프셋도 코드에 박지 않는다.

  정방향이면  첫 틱 가격 = 1분봉 시가,  마지막 틱 가격 = 1분봉 종가
  역순이면    첫 틱 가격 = 1분봉 종가,  마지막 틱 가격 = 1분봉 시가

시가=종가인 분은 둘을 구별할 수 없으므로 판정에서 뺀다. 판정은 과반 규칙이라 허용오차 상수가 없다.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

# 하루를 판정하는 데 필요한 최소 '판정 가능한 분' 수
MIN_DETERMINATE_MINUTES = 30


class TickOrderError(RuntimeError):
    """틱의 분 내부 순서가 1분봉과 어긋날 때 발생."""

    def __init__(self, findings: Sequence["TickOrderFinding"]):
        self.findings = list(findings)
        super().__init__(
            "틱의 분 내부 순서가 1분봉과 어긋납니다 (10초봉 시간축이 뒤집힙니다):\n"
            + "\n".join(f"  · {f.describe()}" for f in self.findings)
        )


FORWARD = "forward"       # 분 내부 순서가 시간순 — 정상
REVERSED = "reversed"     # 분 내부 순서가 통째로 역전 — 뒤집으면 복구된다
UNORDERED = "unordered"   # 정방향도 역순도 아님(순서가 섞임) — 단순 역전으로 복구 불가

_VERDICT_LABEL = {FORWARD: "정상", REVERSED: "역순(복구 가능)", UNORDERED: "순서 섞임(복구 불가)"}


@dataclass(frozen=True)
class TickOrderFinding:
    symbol: str
    day: str
    n_minutes: int          # 판정에 쓴 분 수 (시가≠종가인 분)
    forward_rate: float
    reversed_rate: float
    verdict: str

    @property
    def reversed(self) -> bool:
        return self.verdict == REVERSED

    @property
    def usable(self) -> bool:
        return self.verdict == FORWARD

    def describe(self) -> str:
        return (f"{self.symbol} {self.day}: {_VERDICT_LABEL.get(self.verdict, self.verdict)} "
                f"(판정분 {self.n_minutes:,}개, 정방향 {self.forward_rate:.1%} / 역순 {self.reversed_rate:.1%})")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "day": self.day, "n_minutes": self.n_minutes,
            "forward_rate": round(self.forward_rate, 6),
            "reversed_rate": round(self.reversed_rate, 6),
            "verdict": self.verdict,
        }


def _pick(df: pd.DataFrame, *names: str) -> Optional[str]:
    for n in names:
        if n in df.columns:
            return n
    return None


def audit_tick_minute_order(
    tick_df: pd.DataFrame,
    minute_df: pd.DataFrame,
    symbol: str,
    minute_interval: str = "1m",
    ts_col: str = "timestamp",
) -> List[TickOrderFinding]:
    """틱의 분 내부 행 순서를 1분봉과 대조해 날짜별로 판정한다.

    판정 불가(컬럼 부재/표본 부족)인 날짜는 결과에 넣지 않는다 — '통과'와 구분된다.
    """
    from shared.config.market_policies import bar_bucket_offset

    t_price = _pick(tick_df, "upbit_close", "close", "trade_price")
    m_open = _pick(minute_df, "open", "target_open", "upbit_open")
    m_close = _pick(minute_df, "close", "target_close", "upbit_close")
    if not (t_price and m_open and m_close):
        return []

    t = tick_df[[ts_col, t_price]].copy()
    t[ts_col] = pd.to_datetime(t[ts_col])
    t["_bucket"] = t[ts_col].dt.floor("min")

    m = minute_df[[ts_col, m_open, m_close]].copy()
    m[ts_col] = pd.to_datetime(m[ts_col])
    # 1분봉 라벨 → 그 봉이 담는 구간의 **시작 분**(= 틱 버킷).
    #   bar_close 라벨: 봉 T 는 [T-1봉, T) 를 담는다  → 버킷 = T - 1봉
    #   bar_open  라벨: 봉 T 는 [T, T+1봉) 을 담는다  → 버킷 = T
    # 라벨 산술은 market_policies 한 곳에만 둔다.
    m["_bucket"] = m[ts_col] - bar_bucket_offset(symbol, minute_interval)
    m = m.rename(columns={m_open: "_m_open", m_close: "_m_close"})

    grp = t.groupby("_bucket")[t_price]
    edges = pd.DataFrame({"_first": grp.first(), "_last": grp.last()}).reset_index()

    j = edges.merge(m[["_bucket", "_m_open", "_m_close"]], on="_bucket", how="inner")
    j = j[~np.isclose(j["_m_open"], j["_m_close"])]      # 시가=종가인 분은 구별 불가
    if j.empty:
        return []

    j["_fwd"] = np.isclose(j["_first"], j["_m_open"]) & np.isclose(j["_last"], j["_m_close"])
    j["_rev"] = np.isclose(j["_first"], j["_m_close"]) & np.isclose(j["_last"], j["_m_open"])
    j["_day"] = j["_bucket"].dt.date

    out: List[TickOrderFinding] = []
    for day, g in j.groupby("_day"):
        if len(g) < MIN_DETERMINATE_MINUTES:
            continue
        fwd, rev = float(g["_fwd"].mean()), float(g["_rev"].mean())
        # 어느 쪽도 과반이 아니면 순서가 통째로 뒤집힌 게 아니라 **섞인** 것이다.
        # 뒤집어도 복구되지 않으므로 '역순'과 구분해 따로 보고한다 (조용히 넘기면 그게 fail-open 이다).
        if max(fwd, rev) <= 0.5:
            verdict = UNORDERED
        else:
            verdict = REVERSED if rev > fwd else FORWARD
        out.append(TickOrderFinding(symbol, str(day), len(g), fwd, rev, verdict))
    return out


def repair_minute_order(tick_df: pd.DataFrame, days: Sequence[str], ts_col: str = "timestamp") -> pd.DataFrame:
    """지정한 날짜의 **분 내부 행 순서만** 뒤집는다 (분 사이 순서와 다른 날짜는 그대로).

    하루 전체를 뒤집은 뒤 타임스탬프로 **안정 정렬**하면 분 순서는 제자리로 돌아오고
    분 내부만 역전된다. 잃어버린 정보가 '행 순서' 하나뿐이므로 이것이 정확한 역연산이다.
    """
    if not days:
        return tick_df
    df = tick_df.copy()
    ts = pd.to_datetime(df[ts_col])
    target = ts.dt.date.astype(str).isin(set(str(d) for d in days))
    if not target.any():
        return df

    fixed = df[target].iloc[::-1].sort_values(ts_col, kind="stable")
    rest = df[~target]
    return (pd.concat([rest, fixed])
              .sort_values(ts_col, kind="stable")
              .reset_index(drop=True))


def assert_tick_minute_order(
    tick_df: pd.DataFrame,
    minute_df: pd.DataFrame,
    symbol: str,
    minute_interval: str = "1m",
    ts_col: str = "timestamp",
    log_fn=None,
) -> List[TickOrderFinding]:
    """역순 날짜가 하나라도 있으면 ``TickOrderError`` 를 던진다 (fail-closed)."""
    findings = audit_tick_minute_order(tick_df, minute_df, symbol, minute_interval, ts_col)
    bad = [f for f in findings if not f.usable]
    if log_fn:
        if not findings:
            log_fn(f"  ⏭️ [틱 순서] {symbol}: 1분봉과 대조할 수 있는 분이 부족해 판정 보류")
        else:
            log_fn(f"  {'🚨' if bad else '✅'} [틱 순서] {symbol}: "
                   f"{len(findings)}일 검사, 역순 {len(bad)}일")
            for f in bad:
                log_fn(f"      · {f.describe()}")
    if bad:
        raise TickOrderError(bad)
    return findings
