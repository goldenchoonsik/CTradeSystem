# -*- coding: utf-8 -*-
"""신뢰도 진단 판정 래칫 — 2026-10-07 069500 15m 챔피언(+4.9%/13건)이 '신뢰 높음'으로 보이지 않아야 한다."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from CAiModelBaker.core.reliability_check import summarize  # noqa: E402

_NULL = {"n": 574, "p_value": 0.075, "median": -1.14, "q95": 6.22, "actual": 4.98}
_TRADES = [1.77, 1.24, -0.48, 1.06, 0.73, 0.4, 0.09, 0.94, 0.28, 0.43, -1.26, 0.35, -0.64]
_WFA = [0, 1.46, 15.62, 5.88, -0.83, -4.33, 5.43, -0.42, 2.34]


def test_selection_bias_is_corrected_by_trial_count():
    s = summarize(_NULL, _TRADES, _WFA, n_selection_trials=13, oos_independent=False)
    sel = next(c for c in s["checks"] if c["key"] == "selection")
    assert sel["value"] > 0.5 and sel["status"] == "fail"
    assert s["verdict"] == "low"


def test_independent_holdout_needs_no_selection_correction():
    s = summarize(_NULL, _TRADES, _WFA, n_selection_trials=13, oos_independent=True)
    sel = next(c for c in s["checks"] if c["key"] == "selection")
    assert abs(sel["value"] - 0.075) < 1e-9


def test_strong_evidence_is_rated_high():
    trades = [0.4, 0.5, 0.3, 0.6, 0.2, 0.45, 0.35, 0.5, 0.1, 0.55, 0.3, 0.4, 0.25, 0.6, 0.3, 0.2, 0.5, 0.4, 0.35, 0.45, -0.1]
    s = summarize({"n": 2000, "p_value": 0.002, "median": 0, "q95": 2, "actual": 8}, trades,
                  [2.0, 1.5, 2.5, 1.8, 2.2, 1.9], n_selection_trials=1, oos_independent=True)
    assert s["verdict"] == "high", s


def test_concentrated_wfa_is_flagged():
    s = summarize(_NULL, _TRADES, [20.0, -1.0, -2.0, 0.5], 1, True)
    conc = next(c for c in s["checks"] if c["key"] == "wfa_conc")
    assert conc["status"] == "fail"
