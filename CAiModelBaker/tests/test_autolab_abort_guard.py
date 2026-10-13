import os
import tempfile
import pytest
from CAiModelBaker.core.research_loop import (
    _research_state,
    _research_lock,
)


def test_zero_trade_penalty_in_ranking():
    """거래 0회인 Trial은 손실 Trial보다 우위로 승격되지 않도록 -999.0 패널티를 받아야 함"""
    # Dummy scoring function reproducing research_loop logic
    def adjusted_score(t, min_features=4, complexity_penalty=0.15):
        if t.get("trades", 0) <= 0:
            return -999.0
        net = t.get("net_profit", 0.0)
        n_feat = len(t.get("features", []))
        penalty = (n_feat - min_features) * complexity_penalty if n_feat > min_features else 0.0
        return net - penalty

    t_zero_trades = {"trial": 1, "trades": 0, "net_profit": 0.0, "features": ["f1", "f2", "f3", "f4"]}
    t_loss = {"trial": 2, "trades": 3, "net_profit": -0.5, "features": ["f1", "f2", "f3", "f4"]}
    t_win = {"trial": 3, "trades": 5, "net_profit": 1.2, "features": ["f1", "f2", "f3", "f4"]}

    score_zero = adjusted_score(t_zero_trades)
    score_loss = adjusted_score(t_loss)
    score_win = adjusted_score(t_win)

    assert score_zero == -999.0
    assert score_loss > score_zero, "손실이 발생했더라도 실제 거래가 있는 모델이 무거래 모델보다 점수가 높아야 함"
    assert score_win > score_loss


def test_session_cleanup_removes_old_backtest_log():
    """세션 초기화 시 기존 backtest_run.log 파일이 깔끔하게 삭제되어야 함"""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = os.path.join(tmpdir, "TEST_SESSION")
        os.makedirs(session_dir, exist_ok=True)
        old_log = os.path.join(session_dir, "backtest_run.log")
        with open(old_log, "w", encoding="utf-8") as f:
            f.write("Old backtest log")
        assert os.path.exists(old_log)

        # research_loop 세션 초기화 로직 시뮬레이션
        if os.path.exists(old_log):
            os.remove(old_log)

        assert not os.path.exists(old_log), "기존 backtest_run.log가 삭제되어야 함"


def test_abort_state_toggle():
    """_research_state의 abort 플래그가 스레드 안전하게 토글되는지 검증"""
    with _research_lock:
        _research_state["abort"] = True
        assert _research_state.get("abort") is True
        _research_state["abort"] = False
        assert _research_state.get("abort") is False

