"""
거래 비용 단일 계산 지점 (Trade Costs)
=====================================
왕복 비용 = 2 × (편도 수수료 + 편도 슬리피지) + 매도세

- 수수료/슬리피지는 매수·매도 양쪽에 붙고, 매도세(증권거래세)는 매도할 때만 한 번 붙는다.
- 매도세율은 종목 마스터 원장(시장 정책 sell_tax, 종목별 overrides 가능)이 정답이다.
  프로젝트 설정(config_snapshot)에 복사해 두지 않는다 — 세율이 바뀌면 옛 스냅샷이 옛 세율을 들고 다니게 된다.
  설정에 SELL_TAX 키가 명시돼 있을 때만(시험/스트레스 테스트용) 그 값을 우선한다.

왕복 비용을 직접 `(fee + slip) * 2` 로 계산하지 말고 반드시 round_trip_cost 를 쓴다
(직접 계산하면 매도세가 빠진다 — 2026-10-07 국내 주식 비용이 0.07%로 잡혀 있던 사고).
"""

from typing import Optional


def round_trip_cost(fee_rate: float, slip_rate: float, sell_tax: float = 0.0) -> float:
    """진입가 대비 왕복 총 비용 비율"""
    return (float(fee_rate) + float(slip_rate)) * 2.0 + float(sell_tax)


def sell_tax_for_symbol(symbol: Optional[str]) -> float:
    """원장 기준 종목의 매도세율 (심볼이 비면 0)"""
    if not symbol or not str(symbol).strip():
        return 0.0
    from shared.config.config_manager import get_symbol_info
    return float(get_symbol_info(str(symbol).strip()).get("sell_tax") or 0.0)


def resolve_sell_tax(config: Optional[dict]) -> float:
    """프로젝트 설정의 타겟 종목 매도세율. 설정에 SELL_TAX 가 명시되면 그 값을 우선한다."""
    config = config or {}
    explicit = config.get("SELL_TAX")
    if explicit is not None:
        return float(explicit)
    from shared.core.market_utils import get_target_symbol
    return sell_tax_for_symbol(get_target_symbol(config, default=""))
