"""
슬리브 → 계좌 목표 비중, 리밸런싱 밴드
=====================================
계좌(운영 단위) 안에 슬리브가 여럿 있을 수 있다(예: K200 슬리브 60%, 금 슬리브 40%).
각 슬리브는 자기 신호 종목의 종가로 노출을 정하고(policy), 자기 상품으로 실현한다(realize).
계좌 목표 비중 = Σ 슬리브 배분 × 슬리브 안 상품 비중. 남는 부분은 현금.

리밸런싱 밴드: 목표와 현재 비중의 차이가 밴드보다 작으면 매매하지 않는다.
매일 조금씩 따라가면 회전율·비용만 늘고(2026-10-09 HAR 변동성 예측 실험: 정확도는 올랐지만 회전↑로 성과 동일),
수동 주문표 계좌(ISA·연금)는 주문 횟수가 곧 사람의 수고다.
"""

from dataclasses import dataclass, field
from typing import Mapping

import pandas as pd

from shared.allocation.policy import AllocationParams, target_exposure
from shared.allocation.realize import max_exposure_of, realize_series


@dataclass(frozen=True)
class Sleeve:
    name: str
    signal_symbol: str          # 노출을 정하는 종가 시계열의 종목
    instruments: tuple          # realize.Instrument 들
    allocation: float = 1.0     # 계좌 안 배분 비율
    params: AllocationParams = field(default_factory=AllocationParams)

    def __post_init__(self):
        if not 0.0 < self.allocation <= 1.0:
            raise ValueError(f"{self.name}: allocation 은 (0, 1] 이어야 합니다")
        if self.params.max_exposure > max_exposure_of(self.instruments) + 1e-9:
            raise ValueError(f"{self.name}: max_exposure {self.params.max_exposure} 를 상품 배수로 만들 수 없습니다 "
                             f"(최대 {max_exposure_of(self.instruments)}) — 2배 상품을 추가하거나 상한을 낮추세요")


def sleeve_weights(sleeve: Sleeve, signal_close: pd.Series) -> pd.DataFrame:
    """슬리브 하나의 일별 상품 비중 (슬리브 배분 적용 전, 합 ≤ 1)"""
    return realize_series(target_exposure(signal_close, sleeve.params), sleeve.instruments)


def account_target_weights(sleeves, closes: Mapping[str, pd.Series]) -> pd.DataFrame:
    """슬리브 목록 + {신호 종목: 종가} → 계좌 일별 목표 비중 (열 = 종목, 합 ≤ 1, 나머지 현금)."""
    sleeves = list(sleeves)
    if not sleeves:
        raise ValueError("슬리브가 없습니다")
    if len({s.name for s in sleeves}) != len(sleeves):
        raise ValueError("슬리브 이름 중복")
    total = sum(s.allocation for s in sleeves)
    if total > 1.0 + 1e-9:
        raise ValueError(f"슬리브 배분 합이 1 을 넘습니다 ({total:.4f}) — 현금 계좌")
    parts = []
    for s in sleeves:
        if s.signal_symbol not in closes:
            raise ValueError(f"{s.name}: 신호 종목 {s.signal_symbol} 의 종가가 없습니다")
        parts.append(sleeve_weights(s, closes[s.signal_symbol]) * s.allocation)
    out = pd.concat(parts, axis=1).T.groupby(level=0).sum(min_count=1).T  # 같은 상품을 여러 슬리브가 쓰면 합산
    return out.fillna(0.0).sort_index()


def needs_rebalance(current: Mapping[str, float], target: Mapping[str, float], band: float) -> bool:
    """어느 한 종목이라도 목표와의 차이가 밴드 이상이면 True (그때는 전 종목을 목표로 맞춘다).
    목표가 0 인데 보유 중이면(추세 이탈 → 현금) 밴드와 무관하게 True — 청산 신호를 밴드가 삼키면 안 된다."""
    if band < 0:
        raise ValueError("band 는 0 이상")
    for s in set(current) | set(target):
        c, t = float(current.get(s, 0.0)), float(target.get(s, 0.0))
        if t <= 1e-12 < c:
            return True
        if abs(t - c) >= band - 1e-12 and abs(t - c) > 1e-12:
            return True
    return False
