"""
매도세(증권거래세) 반영 래칫 (2026-10-07)

사고: 비용 모델이 2×(수수료+슬리피지)만 계산해 국내 주식(예: 000660)의 왕복 비용이 0.07%로 잡혔다.
      실제로는 매도 시 증권거래세 0.20%가 더해져 0.27%다 (약 4배).
규칙:
  1. 매도세율은 시장 정책 sell_tax(종목별 overrides 가능)가 정답이다.
  2. 왕복 비용은 shared.core.trade_costs.round_trip_cost 한 곳에서만 계산한다.
  3. 학습(라벨/물리제약)·백테스트·실매매가 모두 같은 매도세를 쓴다.
"""
import os
import re

import numpy as np
import pytest

from shared.config.market_policies import DEFAULT_MARKET_POLICIES
from shared.config.config_manager import get_symbol_info, LEDGER_COST_FIELDS
from shared.core.trade_costs import round_trip_cost, resolve_sell_tax, sell_tax_for_symbol
from shared.core.cexecution import CExecution, ExecutionConfig

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_every_policy_declares_sell_tax():
    for mtype, policy in DEFAULT_MARKET_POLICIES.items():
        assert "sell_tax" in policy, f"{mtype} 정책에 sell_tax 가 없습니다"
        assert policy["sell_tax"] >= 0


def test_policy_values():
    assert DEFAULT_MARKET_POLICIES["KRX_STOCK"]["sell_tax"] == pytest.approx(0.0020)  # 2026-01-01~ 코스피·코스닥 0.20%
    assert DEFAULT_MARKET_POLICIES["KRX_ETF"]["sell_tax"] == 0.0                      # 국내 ETF 매도 거래세 면제
    for mtype in ("CRYPTO", "US_ETF", "US_STOCK", "GLOBAL_FUTURES", "OTHER"):
        assert DEFAULT_MARKET_POLICIES[mtype]["sell_tax"] == 0.0


def test_sell_tax_is_ledger_cost_field():
    assert "sell_tax" in LEDGER_COST_FIELDS


def test_ledger_resolves_sell_tax_per_symbol():
    assert get_symbol_info("000660")["sell_tax"] == pytest.approx(0.0020)  # SK하이닉스 (주식)
    assert get_symbol_info("069500")["sell_tax"] == 0.0                    # KODEX 200 (ETF)
    assert get_symbol_info("KRW-BTC")["sell_tax"] == 0.0


def test_round_trip_cost_formula():
    # 국내 주식 기본값: 2×(0.015%+0.02%) + 0.20% = 0.27%
    p = DEFAULT_MARKET_POLICIES["KRX_STOCK"]
    assert round_trip_cost(p["default_fee"], p["default_slippage"], p["sell_tax"]) == pytest.approx(0.0027)
    assert round_trip_cost(0.0005, 0.0006) == pytest.approx(0.0022)


def test_resolve_sell_tax_from_project_config():
    assert resolve_sell_tax({"SYMBOL_UPBIT": "000660"}) == pytest.approx(0.0020)
    assert resolve_sell_tax({"SYMBOL_UPBIT": "069500"}) == 0.0
    assert resolve_sell_tax({}) == 0.0
    assert resolve_sell_tax({"SYMBOL_UPBIT": "000660", "SELL_TAX": 0.0}) == 0.0  # 명시값 우선
    assert sell_tax_for_symbol("") == 0.0


def test_execution_config_includes_sell_tax():
    cfg = CExecution.create_config_from_params(
        {"take_profit": 1.5, "stop_loss": 1.0, "predict_ahead_candles": 10},
        {"SYMBOL_UPBIT": "000660", "INTERVAL": "1m", "FEE_RATE": 0.00015, "SLIPPAGE": 0.0002},
    )
    assert cfg.sell_tax == pytest.approx(0.0020)
    assert cfg.cost == pytest.approx(0.0027)


def test_batch_kernel_deducts_sell_tax():
    """같은 시세·신호에서 매도세만큼 거래당 순수익률이 정확히 줄어든다"""
    n = 40
    prices = np.full(n, 10000.0)
    prices[5:] = 10300.0  # 진입 후 +3% → 익절
    highs, lows = prices * 1.001, prices * 0.999
    atrs = np.full(n, 150.0)
    signals = np.zeros(n, dtype=bool)
    signals[2] = True
    no_force = np.zeros(n, dtype=bool)
    allow = np.ones(n, dtype=bool)

    def run(tax):
        cfg = ExecutionConfig(fee_rate=0.00015, slip_rate=0.0002, sell_tax=tax, take_profit=1.5,
                              stop_loss=1.0, max_hold_candles=10, cooldown_candles=1)
        return CExecution.simulate_batch(prices, highs, lows, atrs, signals, no_force, allow, cfg)

    base, taxed = run(0.0), run(0.0020)
    assert base["total_trades"] == taxed["total_trades"] == 1
    diff = base["trade_logs"][0]["net_return"] - taxed["trade_logs"][0]["net_return"]
    assert diff == pytest.approx(0.20)  # %p
    assert taxed["trade_logs"][0]["tax_out"] > 0


# ── 래칫: 왕복 비용을 직접 계산하면 매도세가 빠진다 ──
_SCAN_DIRS = ("shared", "CAiModelBaker/core", "CAiModelBaker/web_dashboard", "CTrade/core", "CTrade/utils")
_DIRECT_ROUND_TRIP = re.compile(
    r"\(\s*[\w.\[\]'\"]*(fee|FEE)[\w.\[\]'\"(),\s]*\+\s*[\w.\[\]'\"]*(slip|SLIP)[^)]*\)+\s*\*\s*2"
    r"|(fee|FEE)\w*\s*\*\s*2(\.0)?\b"
)
_ALLOWED = {
    os.path.join("shared", "core", "trade_costs.py"),
    os.path.join("shared", "core", "cexecution.py"),  # njit 커널 안 인라인 1곳 (trade_costs 와 같은 식)
}


def test_no_direct_round_trip_cost_outside_trade_costs():
    offenders = []
    for d in _SCAN_DIRS:
        for root, _dirs, files in os.walk(os.path.join(PROJECT_ROOT, d)):
            if "tests" in root.split(os.sep) or "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                rel = os.path.relpath(path, PROJECT_ROOT)
                if rel in _ALLOWED:
                    continue
                with open(path, encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        code = line.split("#", 1)[0]
                        if _DIRECT_ROUND_TRIP.search(code):
                            offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, "왕복 비용은 trade_costs.round_trip_cost 로만 계산하세요 (매도세 누락):\n" + "\n".join(offenders)
