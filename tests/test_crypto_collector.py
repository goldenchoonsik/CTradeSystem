"""
tests/test_crypto_collector.py
==============================
가상자산 통합 증분 수집기 (CAiModelBaker/core/crypto_collector.py) 단위 테스트
- 거래소별 인터벌 규격 정규화 및 Lookahead 방지 매핑 검증
- 타임스탬프 추출 및 원자적 병합(Upsert) 무결성 검증
- 5m -> 10m 리샘플링 합성 무결성 검증
"""
import os
import tempfile
import pandas as pd
import pytest
from datetime import datetime

from CAiModelBaker.core.crypto_collector import (
    BinanceClient, UpbitClient, get_file_last_timestamp,
    _merge_and_save_crypto, sync_crypto_binance,
)
from shared.data_library.candle_resampler import resample_candles


def test_binance_normalize_interval():
    """바이낸스 인터벌 정규화 및 10m -> 5m 룩어헤드 방지 매핑 검증"""
    assert BinanceClient.normalize_interval('1m') == '1m'
    assert BinanceClient.normalize_interval('5m') == '5m'
    assert BinanceClient.normalize_interval('15m') == '15m'
    assert BinanceClient.normalize_interval('1h') == '1h'
    assert BinanceClient.normalize_interval('10m') == '5m'
    assert BinanceClient.normalize_interval('99m') is None
    assert BinanceClient.normalize_interval('unknown') is None


def test_upbit_parse_unit():
    """업비트 분봉 인터벌 파싱 검증"""
    assert UpbitClient.parse_unit('1m') == 1
    assert UpbitClient.parse_unit('3m') == 3
    assert UpbitClient.parse_unit('5m') == 5
    assert UpbitClient.parse_unit('10m') == 10
    assert UpbitClient.parse_unit('15m') == 15
    assert UpbitClient.parse_unit('60m') == 60
    assert UpbitClient.parse_unit('240m') == 240
    assert UpbitClient.parse_unit('invalid') == 10


def test_get_file_last_timestamp():
    """CSV 파일로부터 마지막 타임스탬프 역방향 청크 파싱 검증"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write('timestamp,open,high,low,close,volume\n')
        f.write('2026-09-20 10:00:00,100,105,95,102,10\n')
        f.write('2026-09-20 10:10:00,102,108,100,107,15\n')
        f.write('2026-09-20 10:20:00,107,110,105,109,20\n')
        temp_csv = f.name
    try:
        last_dt = get_file_last_timestamp(temp_csv)
        assert last_dt is not None
        assert last_dt == datetime(2026, 9, 20, 10, 20, 0)
    finally:
        if os.path.exists(temp_csv):
            os.remove(temp_csv)


def test_merge_and_save_crypto_upsert():
    """원자적 Upsert 병합 검증: 기존 데이터와 신규 데이터의 중복 제거 및 시간순 정렬"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write('timestamp,open,high,low,close,volume\n')
        f.write('2026-09-20 10:00:00,100,105,95,102,10\n')
        f.write('2026-09-20 10:10:00,102,108,100,107,15\n')
        temp_csv = f.name

    try:
        df_new = pd.DataFrame([
            {'timestamp': '2026-09-20 10:10:00', 'open': 102, 'high': 109, 'low': 100, 'close': 108, 'volume': 16},
            {'timestamp': '2026-09-20 10:20:00', 'open': 108, 'high': 112, 'low': 107, 'close': 111, 'volume': 25},
        ])
        total_rows = _merge_and_save_crypto(df_new, temp_csv, local_log=lambda s: None)
        assert total_rows == 3
        df_merged = pd.read_csv(temp_csv)
        assert len(df_merged) == 3
        row_1010 = df_merged[df_merged['timestamp'] == '2026-09-20 10:10:00'].iloc[0]
        assert row_1010['close'] == 108
        assert df_merged.iloc[-1]['timestamp'] == '2026-09-20 10:20:00'
    finally:
        if os.path.exists(temp_csv):
            os.remove(temp_csv)


def test_crypto_resampling_integration():
    """5분봉 2개를 묶어 10분봉으로 리샘플링할 때 OHLCV 집계 규칙 검증"""
    df_5m = pd.DataFrame([
        {'timestamp': '2026-09-20 10:00:00', 'open': 100.0, 'high': 105.0, 'low': 98.0, 'close': 103.0, 'volume': 50.0, 'quote_vol': 5000.0, 'trades': 100.0},
        {'timestamp': '2026-09-20 10:05:00', 'open': 103.0, 'high': 108.0, 'low': 102.0, 'close': 106.0, 'volume': 70.0, 'quote_vol': 7200.0, 'trades': 150.0},
    ])
    df_10m = resample_candles(df_5m, target_interval='10m', source_interval='5m')
    assert len(df_10m) == 1
    row = df_10m.iloc[0]
    assert row['open'] == 100.0
    assert row['high'] == 108.0
    assert row['low'] == 98.0
    assert row['close'] == 106.0
    assert row['volume'] == 120.0
    assert row['quote_vol'] == 12200.0
    assert row['trades'] == 250.0


def test_get_file_last_timestamp_microsecond_precision():
    """마이크로초(16자리) open_time이 연도 폭주 없이 올바르게 해석되는지 검증
    (ms/us 자릿수 오판별로 발생했던 회귀 버그에 대한 추가 커버리지 - 원본 테스트 스위트에는
    없었으나, 재구성 과정에서 함께 고친 get_file_last_timestamp의 단위 판별 버그를 검증하기 위해 추가)
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write('timestamp,open,high,low,close,volume\n')
        # 1735689600000000 = 2025-01-01 00:00:00 UTC를 마이크로초(16자리)로 표현한 값
        f.write('1735689600000000,100,105,95,102,10\n')
        temp_csv = f.name
    try:
        last_dt = get_file_last_timestamp(temp_csv)
        assert last_dt is not None
        assert last_dt.year == 2025
    finally:
        if os.path.exists(temp_csv):
            os.remove(temp_csv)


# ── 수집 구간 규칙 (2026-09-25): 빈 파일은 수집 시작부터, 앞쪽이 비면 채움, 원천에 없으면 기록 후 재시도 안 함 ──
def _fake_upbit(listing_kst, now_kst, unit=10):
    """listing ~ now 사이의 10분봉을 업비트처럼 최신→과거 200개씩 돌려주는 가짜 API. 호출 기록을 남긴다."""
    from datetime import datetime as _dt, timedelta as _td
    calls = []

    def fetch(market, u, to=None, count=200):
        calls.append(to)
        end = now_kst if not to else (_dt.strptime(to[:19], "%Y-%m-%dT%H:%M:%S") + (_td(0) if "+09:00" in to else _td(hours=9)))
        out, t = [], end - _td(minutes=unit)
        while t >= listing_kst and len(out) < count:
            out.append({"candle_date_time_kst": t.strftime("%Y-%m-%dT%H:%M:%S"),
                        "candle_date_time_utc": (t - _td(hours=9)).strftime("%Y-%m-%dT%H:%M:%S"),
                        "opening_price": 1.0, "high_price": 1.0, "low_price": 1.0, "trade_price": 1.0,
                        "candle_acc_trade_volume": 1.0, "candle_acc_trade_price": 1.0})
            t -= _td(minutes=unit)
        return out
    return fetch, calls


def test_new_symbol_is_filled_from_the_archive_start_and_head_floor_is_remembered(tmp_path, monkeypatch):
    from datetime import datetime as _dt
    from CAiModelBaker.core import crypto_collector as cc
    from shared.data_library import raw_ingest
    start, listing, now = _dt(2026, 9, 1), _dt(2026, 9, 3, 12, 0), _dt(2026, 9, 5, 0, 0)
    fetch, calls = _fake_upbit(listing, now)
    monkeypatch.setattr(cc.UpbitClient, "fetch_minute_candles", classmethod(lambda cls, *a, **k: fetch(*a, **k)))
    monkeypatch.setattr(raw_ingest, "archive_start", lambda: start)
    monkeypatch.setattr(cc.time, "sleep", lambda *_: None)
    target = str(tmp_path / "KRW-NEW_10m.csv")
    cc.sync_crypto_upbit("KRW-NEW", "10m", target, local_log=lambda *_: None)
    df = pd.read_csv(target)
    assert df["timestamp"].iloc[0] == "2026-09-03 12:00:00" and df["timestamp"].iloc[-1] == "2026-09-04 23:50:00"
    assert len(df) == (36 * 60) // 10                                          # 상장~지금 전부 (페이지 제한 없음)
    # 상장 전(수집 시작~상장)은 원천에 없음 → 기록되어 다음 동기화가 앞쪽을 다시 받지 않는다
    assert [k for _, _, k in raw_ingest.fetch_ranges(target)] == ["tail"]


def test_existing_file_gets_its_missing_head_filled(tmp_path, monkeypatch):
    from datetime import datetime as _dt
    from CAiModelBaker.core import crypto_collector as cc
    from shared.data_library import raw_ingest
    start, now = _dt(2026, 9, 1), _dt(2026, 9, 3, 0, 0)
    fetch, _ = _fake_upbit(start, now)
    monkeypatch.setattr(cc.UpbitClient, "fetch_minute_candles", classmethod(lambda cls, *a, **k: fetch(*a, **k)))
    monkeypatch.setattr(raw_ingest, "archive_start", lambda: start)
    monkeypatch.setattr(cc.time, "sleep", lambda *_: None)
    target = tmp_path / "KRW-OLD_10m.csv"
    target.write_text("timestamp,upbit_close\n2026-09-02 00:00:00,1.0\n2026-09-02 00:10:00,1.0\n")      # 앞쪽이 빈 옛 파일
    cc.sync_crypto_upbit("KRW-OLD", "10m", str(target), local_log=lambda *_: None)
    df = pd.read_csv(target)
    assert df["timestamp"].iloc[0] == "2026-09-01 00:00:00" and len(df) == 2 * 144
    assert list(df["timestamp"]) == sorted(df["timestamp"]) and df["timestamp"].is_unique
