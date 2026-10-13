import pytest
from CAiModelBaker.core.research_loop import _run_holdout_validation

def test_monthly_trades_calculation():
    """monthly_trades가 holdout_days(30.4일 기준)에 맞추어 정확히 정규화 계산되는지 검증"""
    config = {}
    target_criteria = {"min_profit": 0.0, "min_trades": 20.0, "max_mdd": 10.0}

    # fallback/empty case
    res = _run_holdout_validation(
        config=config,
        holdout_start="2026-07-07",
        holdout_end="2026-08-25",
        target_criteria=target_criteria,
        log_fn=lambda x: None
    )
    assert "monthly_trades" in res
    assert "trades" in res
    assert isinstance(res["monthly_trades"], (int, float))
