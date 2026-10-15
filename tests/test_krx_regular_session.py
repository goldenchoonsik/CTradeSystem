"""KRX 정규장 판정 — 2026-09-14 NXT(대체거래소) 개장 이후 Creon 개별 주식 1분봉에 08:00~20:00 통합 체결이 들어온다.

원천(raw)은 받은 그대로 보존하고, 정규화(normalized)는 정규장 시계열만 둔다. 그렇지 않으면 참조 결합(backward)이
아침 첫 봉에 전날 20:00 애프터마켓 값을 끌어오고, 09-14 이전 학습 이력과 지표의 뜻이 달라진다(2026-10-16 발견:
000660·005930 정규화 1분봉에 정규장 밖 봉 각 ~4,550개, 이를 참조로 쓰는 프로젝트 3개).
"""
import os
import re

import pandas as pd
import pytest

from shared.core.market_utils import krx_regular_bar_mask

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_mask_bar_close_and_bar_open():
    ts = ["2026-10-14 08:30:00", "2026-10-14 09:00:00", "2026-10-14 09:01:00", "2026-10-14 15:30:00",
          "2026-10-14 15:31:00", "2026-10-14 20:00:00"]
    assert krx_regular_bar_mask(ts, "BAR_CLOSE").tolist() == [False, False, True, True, False, False]
    assert krx_regular_bar_mask(ts, "BAR_OPEN").tolist() == [False, True, True, False, False, False]


def test_stock_minute_builder_drops_nxt_bars(tmp_path):
    from CAiModelBaker.core.normalized_builder import build_normalized_candle_stock
    raw = tmp_path / "A999999_1m.csv"
    raw.write_text("timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume,upbit_trade_price\n"
                   "2026-09-11 09:01:00,100,101,99,100,10,1000\n"
                   "2026-09-11 15:30:00,100,101,99,101,10,1000\n"
                   "2026-09-14 08:01:00,90,91,89,90,5,450\n"        # NXT 프리마켓
                   "2026-09-14 09:01:00,100,101,99,100,10,1000\n"
                   "2026-09-14 15:30:00,100,101,99,102,10,1000\n"
                   "2026-09-14 20:00:00,110,111,109,110,5,550\n")   # NXT 애프터마켓
    out = build_normalized_candle_stock("999999", interval="1m", raw_candle_path=str(raw),
                                        output_parquet_path=str(tmp_path / "out.parquet"), local_log=lambda *_: None)
    got = pd.read_parquet(out)["timestamp"].dt.strftime("%Y-%m-%d %H:%M").tolist()
    assert got == ["2026-09-11 09:01", "2026-09-11 15:30", "2026-09-14 09:01", "2026-09-14 15:30"]


def test_real_vault_krx_minute_normalized_is_regular_session_only():
    """래칫(실물, 읽기만 함): KRX 주식·ETF 1분봉 정규화에는 정규장 밖 봉이 없다(선물·지수는 normalized/macro 라 대상 아님)."""
    d = os.path.join(ROOT, "DataVault", "normalized", "candles_1m")
    files = [f for f in os.listdir(d) if re.fullmatch(r"\d[0-9A-Z]{5}_1m\.parquet", f)] if os.path.isdir(d) else []
    if not files:
        pytest.skip("실물 부재")
    bad = {}
    for f in files:
        ts = pd.read_parquet(os.path.join(d, f), columns=["timestamp"])["timestamp"]
        n = int((~krx_regular_bar_mask(ts, "BAR_CLOSE")).sum())
        if n:
            bad[f] = n
    assert not bad, f"정규장 밖 봉이 남은 KRX 1분봉 정규화: {bad} — normalized_builder.build_normalized_candle_stock 로 다시 빌드"
