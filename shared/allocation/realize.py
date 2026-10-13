"""
목표 노출 → 상품별 비중 (현금 계좌)
==================================
현금 계좌는 비중 합이 1 을 넘을 수 없다. 노출 1 을 넘는 부분은 레버리지 상품으로만 만들 수 있다.

같은 노출을 만드는 조합은 여러 가지다(예: 노출 1.5 = 1배 50% + 2배 50%, 또는 2배 75% + 현금 25%).
여기서는 **노출을 감싸는 인접한 두 상품만 섞는다** — 레버리지 상품 비중이 가장 작아진다.
레버리지 상품은 일반계좌에서 매매차익 15.4% 과세 대상이고(국내주식형 1배는 비과세) 보수·괴리도 크기 때문이다.
  노출 0.6 → 1배 60% + 현금 40%
  노출 1.5 → 1배 50% + 2배 50%
  노출 2.0 → 2배 100%

상품의 배수·세금 분류는 종목 원장(leverage, tax_class)에서 읽는다. 원장에 없으면 거부한다.
인버스(음수 배수)는 아직 지원하지 않는다 — 이 정책은 롱/현금만 오간다.
"""

from dataclasses import dataclass
from typing import Sequence

import pandas as pd


@dataclass(frozen=True)
class Instrument:
    symbol: str
    leverage: float
    tax_class: str


def instruments_from_ledger(symbols: Sequence[str]) -> tuple:
    """원장에서 상품 정보를 읽는다. 미등록이거나 leverage/tax_class 가 비어 있으면 ValueError."""
    from shared.config.config_manager import get_symbol_info, is_symbol_registered
    out = []
    for s in symbols:
        if not is_symbol_registered(s):
            raise ValueError(f"원장에 등록되지 않은 종목은 배분 대상이 될 수 없습니다: {s}")
        info = get_symbol_info(s)
        lev, tc = info.get("leverage"), info.get("tax_class")
        if lev is None or tc is None:
            raise ValueError(f"{s}: 원장에 leverage/tax_class 가 없습니다 — 배분 대상이 되려면 먼저 원장에 기입하세요")
        out.append(Instrument(symbol=info["symbol"], leverage=float(lev), tax_class=tc))
    return _validated(out)


def _validated(instruments) -> tuple:
    inst = tuple(sorted(instruments, key=lambda i: i.leverage))
    if not inst:
        raise ValueError("상품이 하나도 없습니다")
    if any(i.leverage <= 0 for i in inst):
        raise ValueError("인버스(음수 배수) 상품은 아직 지원하지 않습니다")
    if len({i.leverage for i in inst}) != len(inst):
        raise ValueError("같은 배수의 상품이 둘 이상입니다 — 어느 것을 쓸지 정할 수 없습니다")
    if len({i.symbol for i in inst}) != len(inst):
        raise ValueError("중복 종목")
    return inst


def max_exposure_of(instruments) -> float:
    return max(i.leverage for i in instruments)


def realize_exposure(exposure: float, instruments) -> dict:
    """노출 하나 → {종목: 비중}. 비중 합 ≤ 1, Σ 비중×배수 = 노출."""
    inst = _validated(instruments)
    L = float(exposure)
    if L < -1e-12:
        raise ValueError(f"음수 노출은 지원하지 않습니다: {L}")
    top = inst[-1].leverage
    if L > top + 1e-9:
        raise ValueError(f"노출 {L:.4f} 는 가장 높은 배수 상품({top})으로도 현금 계좌에서 만들 수 없습니다")
    w = {i.symbol: 0.0 for i in inst}
    if L <= 0:
        return w
    lo = inst[0]
    if L <= lo.leverage:
        w[lo.symbol] = L / lo.leverage
        return w
    for a, b in zip(inst, inst[1:]):
        if L <= b.leverage + 1e-12:
            # w_a + w_b = 1, a·w_a + b·w_b = L
            wb = (L - a.leverage) / (b.leverage - a.leverage)
            w[b.symbol] = min(max(wb, 0.0), 1.0)
            w[a.symbol] = 1.0 - w[b.symbol]
            return w
    w[inst[-1].symbol] = 1.0
    return w


def realize_series(exposure: pd.Series, instruments) -> pd.DataFrame:
    """일별 노출 → 일별 상품 비중 (열 = 종목)"""
    inst = _validated(instruments)
    rows = [realize_exposure(v, inst) for v in exposure.fillna(0.0).to_numpy()]
    return pd.DataFrame(rows, index=exposure.index, columns=[i.symbol for i in inst])
