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


def test_interval_audit_handles_fx_rollover_daily_bars():
    """FX·CFD 일봉은 뉴욕 17:00 롤오버 — 하루 경계가 서머타임에 따라 KST 06:00/07:00 으로 바뀐다.
    고정 앵커(0h/9h)로는 맞출 수 없어 정상 데이터가 '불일치'로 판정됐다(2026-10-16) — 상위 봉 시작 시각을 경계로 쓴다."""
    import numpy as np
    from shared.data_library.interval_consistency_audit import audit_interval_consistency
    rng = np.random.default_rng(0)
    starts = [pd.Timestamp("2026-03-02 07:00") + pd.Timedelta(days=i) for i in range(20)] + \
             [pd.Timestamp("2026-03-23 06:00") + pd.Timedelta(days=i) for i in range(20)]      # 서머타임 전환
    fine, coarse = [], []
    for s in starts:
        t = pd.date_range(s, s + pd.Timedelta(hours=23, minutes=59), freq="1min")
        p = 100 + np.cumsum(rng.normal(0, 0.01, len(t)))
        fine.append(pd.DataFrame({"timestamp": t, "open": p, "high": p + 0.005, "low": p - 0.005, "close": p}))
        coarse.append({"timestamp": s, "open": p[0], "high": p.max() + 0.005, "low": p.min() - 0.005, "close": p[-1]})
    r = audit_interval_consistency(pd.concat(fine), pd.DataFrame(coarse), "FX", "1m", "1d")
    assert r is not None and r.ok and r.extrema_rate == 1.0


def test_real_vault_ctrader_daily_normalized_is_one_bar_per_day():
    """래칫(실물): cTrader 일봉 정규화가 1분봉을 일봉 이름으로 저장하던 결함(수백만 행) 재발 방지 — 하루 한 봉."""
    d = os.path.join(ROOT, "DataVault", "normalized", "candles_1d")
    names = ("USDJPY", "US_500", "XAUUSD", "XTIUSD", "JAPAN_225", "HONG_KONG_50", "US_TECH_100")
    files = [f"{n}_1d.parquet" for n in names if os.path.exists(os.path.join(d, f"{n}_1d.parquet"))]
    if not files:
        pytest.skip("실물 부재")
    bad = {}
    for f in files:
        ts = pd.to_datetime(pd.read_parquet(os.path.join(d, f), columns=["timestamp"])["timestamp"])
        if ts.dt.date.duplicated().any():
            bad[f] = int(ts.dt.date.duplicated().sum())
    assert not bad, f"하루 여러 봉인 일봉 정규화: {bad}"
