"""실패 트라이얼·WFA 사전 점검 래칫 (2026-10-20).

0167A0 10s: 탐색 13거래일 < WFA 창 1개(11+3+3) → 트라이얼마다 같은 '데이터 부족' 예외가 났는데
루프는 이를 '거래 0회' 로 기록해 SAMPLE_SHORTAGE(문턱을 낮추라) 로 오진했고,
실패 트라이얼이 Top-3 후보로 올라 최종 Test 재학습까지 같은 예외를 반복했다.
"""
import pandas as pd

from CAiModelBaker.core.research_loop import check_wfa_feasibility, diagnose_failure


def _bars(n_days: int) -> pd.DataFrame:
    days = pd.bdate_range("2026-08-03", periods=n_days)
    ts = [d + pd.Timedelta(hours=9, minutes=m) for d in days for m in range(0, 390, 30)]
    return pd.DataFrame({"timestamp": ts})


def _cfg(train=0.52, opt=0.14, test=0.14, enabled=True):
    return {"SYMBOL": "0167A0", "OPTUNA_CONFIG": {
        "wfa_enabled": enabled, "wfa_train_months": train, "wfa_opt_months": opt, "wfa_test_months": test}}


def test_wfa_feasibility_rejects_short_search_window():
    r = check_wfa_feasibility(_bars(13), _cfg())
    assert not r["ok"]
    assert r["need_td"] == 17 and r["trading_days"] == 13


def test_wfa_feasibility_accepts_enough_days():
    r = check_wfa_feasibility(_bars(23), _cfg())
    assert r["ok"] and r["windows"] >= 1


def test_wfa_feasibility_skipped_when_wfa_disabled():
    assert check_wfa_feasibility(_bars(3), _cfg(enabled=False))["ok"]


def test_all_failed_trials_diagnosed_as_data_shortage_not_sample_shortage():
    hist = [{"trial": i, "trades": 0, "net_profit": 0.0, "mdd": 99.0, "ic_pass_count": 5,
             "failed": True, "error": "데이터 부족: 시작일(2026-08-26) 기준 Train 11 + Opt 3 + Test 3"}
            for i in range(3)]
    assert diagnose_failure(hist, min_trades=8, min_features=2)["code"] == "DATA_SHORTAGE"


def test_failed_trials_do_not_dilute_real_results():
    real = [{"trial": i, "trades": 40, "net_profit": -5.0, "mdd": 30.0, "ic_pass_count": 5,
             "features": [f"f{i}"]} for i in range(3)]
    failed = [{"trial": 9, "trades": 0, "net_profit": 0.0, "mdd": 99.0, "ic_pass_count": 5,
               "failed": True, "error": "x"}] * 10
    # 실패 10개가 섞여도 평균 거래 40회 → SAMPLE_SHORTAGE 가 아니어야 한다
    assert diagnose_failure(real + failed, min_trades=8, min_features=2)["code"] != "SAMPLE_SHORTAGE"
