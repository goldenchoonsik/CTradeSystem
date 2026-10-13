"""
목표 노출 정책 — 변동성 목표 × 추세 필터
=======================================
노출(기초지수 대비 배수) = clip(vol_target / 연환산 실현변동성, 0, max_exposure) × [종가 > 이동평균 ? 1 : trend_off_scale]

- 방향을 예측하지 않는다. 2026-10-09 측정에서 얕은 GBM 방향 예측은 동전 수준(적중 50~52%)이었고 규칙을 악화시켰다.
- 기본 파라미터는 문헌 표준값이다(최적화하지 않음). 주변값 견고성은 검증 도구(allocation_lab)가 따로 확인한다.
- 시점: t 의 값은 t 종가까지의 정보만 쓴다. 언제 체결하는지(다음 날 종가 등)는 백테스트/실행 쪽이 정한다.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class AllocationParams:
    vol_target: float = 0.25        # 연환산 목표 변동성
    vol_window: int = 20            # 실현변동성 창 (거래일)
    trend_window: int = 120         # 추세 이동평균 창 (거래일)
    max_exposure: float = 2.0       # 노출 상한 (기초지수 대비 배수)
    trend_off_scale: float = 0.0    # 종가가 이동평균 아래일 때 곱하는 배수 (0 = 현금)
    annualization: int = 252

    def __post_init__(self):
        if self.vol_target <= 0 or self.max_exposure <= 0:
            raise ValueError("vol_target·max_exposure 는 양수여야 합니다")
        if self.vol_window < 2 or self.trend_window < 2:
            raise ValueError("vol_window·trend_window 는 2 이상이어야 합니다")
        if not 0.0 <= self.trend_off_scale <= 1.0:
            raise ValueError("trend_off_scale 은 0~1 이어야 합니다")


def target_exposure(close: pd.Series, p: AllocationParams = AllocationParams()) -> pd.Series:
    """일별 종가(분배금 반영 수정주가 권장) → 일별 목표 노출. 지표가 준비되지 않은 초기 구간은 0(현금)."""
    close = pd.Series(close, dtype=float).sort_index()
    if close.index.has_duplicates:
        raise ValueError("종가 인덱스에 중복 날짜가 있습니다")
    if (close.dropna() <= 0).any():
        raise ValueError("종가에 0 이하 값이 있습니다")
    ret = close.pct_change(fill_method=None)
    vol = ret.rolling(p.vol_window, min_periods=p.vol_window).std() * np.sqrt(p.annualization)
    ma = close.rolling(p.trend_window, min_periods=p.trend_window).mean()

    with np.errstate(divide="ignore"):
        raw = (p.vol_target / vol).clip(lower=0.0, upper=p.max_exposure)
    trend = np.where(close > ma, 1.0, p.trend_off_scale)
    exposure = raw * trend
    ready = vol.notna() & ma.notna() & close.notna()
    return exposure.where(ready, 0.0).rename("exposure")
