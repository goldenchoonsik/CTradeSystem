"""
M-2 회귀 테스트: 테스트 레벨 Lv.1 쿨다운 '캔들 수' 인터벌 환산

Lv.1(MILD)은 문서상 cooldown=1분이어야 하며, 캔들 수 단위인 cooldown_candles 는
인터벌에 맞게 환산되어야 한다(기존엔 1로 고정되어 10s 인터벌에서 10초에 불과했음).
"""
import os

import pytest

from core.ai_strategy import AIStrategyModule

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.parametrize("rel, expected", [
    ("shared/deploy/069500_10s/KODEX200_S10_BEST1", 6),   # 10s → 1분 = 6캔들
    ("shared/deploy/069500_1m/KODEX200_M1_BEST3", 1),     # 1m  → 1캔들
    ("shared/deploy/BTC_10m/BTC_M10_BEST1", 1),           # 10m → 1캔들(최소 1)
])
def test_lv1_cooldown_candles_interval_scaled(rel, expected):
    dep = os.path.join(REPO_ROOT, rel)
    if not os.path.isdir(dep):
        pytest.skip(f"배포 패키지 없음: {dep}")
    mod = AIStrategyModule(dep, symbol="KRW-BTC", enable_parity_logging=False)
    mod.set_test_level(1)
    assert mod.position_manager.cooldown_candles == expected


def test_lv0_restores_original_cooldown_scaled():
    """Lv.0 복원 경로도 인터벌 환산 일관성을 유지해야 한다."""
    rel = "shared/deploy/069500_10s/KODEX200_S10_BEST1"
    dep = os.path.join(REPO_ROOT, rel)
    if not os.path.isdir(dep):
        pytest.skip(f"배포 패키지 없음: {dep}")
    mod = AIStrategyModule(dep, symbol="KRW-BTC", enable_parity_logging=False)
    mod.set_test_level(1)
    mod.set_test_level(0)
    assert mod.position_manager.cooldown_candles == max(1, int(mod.original_cooldown_minutes / mod.interval_minutes))
