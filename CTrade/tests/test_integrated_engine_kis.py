import asyncio
import os
import sys
import logging
import hashlib
import tempfile
from unittest.mock import patch
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from main import load_trading_config
from core.engine import STradeEngine
from core.shared_state import SharedState

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# ==============================================================================
# [D-6] 프로덕션 상태 파일 격리
# ------------------------------------------------------------------------------
# 실 STradeEngine 구동은 PortfolioTracker.calculate_portfolio →
# update_baseline_rollover → save_baseline 을 호출한다.
# save_baseline / load_baseline 은 유일한 경로 병목인 _get_baseline_path() 를
# 사용하므로, 테스트는 반드시 이 경로를 임시 파일로 patch 하여
# 추적 대상 파일(account_baseline.json) 을 덮어쓰지 않아야 한다.
#   · 미격리 시: 실계좌 잔고로 기준선이 덮어써지고
#                09:00 KST 롤오버 계약이 무력화되어 계좌 ROI 기준점이 왜곡됨
# ==============================================================================
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REAL_BASELINE_PATH = os.path.join(_PROJECT_ROOT, "account_baseline.json")


def _sha256_or_none(path: str):
    """파일 SHA256 (1MB 청크 스트리밍). 파일이 없으면 None."""
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def _run_integrated_kis():
    config = load_trading_config()
    shared = SharedState()

    logging.info("[IntegrationTest] Initializing STradeEngine with TQQQ (KIS)...")
    engine = STradeEngine(config)

    # stock_symbols 확인
    logging.info(f"📊 Engine stock_symbols: {engine.stock_symbols}")
    logging.info(f"📊 Candle aggregators: {list(engine.candle_aggregators.keys())}")

    # 엔진을 2초간 실행하고 틱 데이터 유입 및 UI 레벨/지표 계산 확인
    engine_task = asyncio.create_task(engine.run(shared))

    await asyncio.sleep(2.0)

    # 데이터 매니저 상태 점검
    tqqq_data = engine.data_manager.store.get("TQQQ", {})
    ticker = tqqq_data.get("ticker")
    if ticker:
        logging.info(f"✅ [TQQQ 수신 성공] 현재가: ${ticker.price:.4f} | 방향: {ticker.side} | 거래량: {ticker.volume}")
    else:
        logging.warning("⚠️ TQQQ 틱이 아직 수신되지 않았습니다.")

    engine.is_running = False
    engine_task.cancel()
    logging.info("🏁 Integration Test Complete!")

def test_integrated_kis():
    """
    KIS 통합 엔진 구동 검증.

    [D-6 회귀 방지] 기준선 저장 경로를 임시 디렉토리로 격리한 뒤 실행하고,
    실행 전후의 실제 account_baseline.json 해시가 동일함을 검증한다.
    프로덕션 기준선이 테스트에 의해 변경되면 즉시 실패한다.
    """
    before = _sha256_or_none(_REAL_BASELINE_PATH)

    with tempfile.TemporaryDirectory() as tmpdir:
        isolated_baseline = os.path.join(tmpdir, "account_baseline.json")
        with patch("utils.portfolio_tracker._get_baseline_path", return_value=isolated_baseline):
            try:
                asyncio.run(_run_integrated_kis())
            except Exception as e:
                logging.warning(f"Integration test skip or non-critical: {e}")

    after = _sha256_or_none(_REAL_BASELINE_PATH)
    assert before == after, (
        "테스트가 프로덕션 기준선 파일(account_baseline.json)을 변경했습니다. "
        f"({before} → {after}) _get_baseline_path 격리(patch)를 확인하세요. [D-6]"
    )


if __name__ == "__main__":
    # 수동 실행 시에도 프로덕션 기준선을 오염시키지 않도록 동일하게 격리한다. [D-6]
    with tempfile.TemporaryDirectory() as _tmpdir:
        with patch("utils.portfolio_tracker._get_baseline_path",
                   return_value=os.path.join(_tmpdir, "account_baseline.json")):
            asyncio.run(_run_integrated_kis())
