# -*- coding: utf-8 -*-
"""
Dataset Builder Characterization Tests (데이터셋 빌더 동작 보존 계약 테스트)
============================================================================
목적
----
`CAiModelBaker/core/data_manager.py` 의 데이터셋 조립 함수는
Medallion Gold 계층의 실질적 생산자이지만, 리팩터링 이전 시점에
이를 검증하는 테스트가 전무했다 (grep 결과 0건).

본 테스트는 리팩터링 *이전* 동작을 그대로 고정(characterize)하여,
이후 Gold 단일화/경로 SSOT 작업 시 "동작 보존"을 기계적으로 증명한다.

고정하는 계약 (Contract)
------------------------
1. `get_snapshot_path` 경로 반환 규약 — 8개 모듈이 의존하는 문자열 계약.
2. `merge_reference_symbols_into_df` 의 참조 대장주 결합 규약:
   - ref_symbols 미설정/빈 DF → 원본 불변(no-op)
   - 대장주 캔들 부재 → 컬럼 미생성 (예외 없음)
   - 해외선물(overseas_futures) 캔들 → 캔들 시작→확정 시각 시프트 (Lookahead 차단)
   - 크립토(binance) 캔들 → 시프트 없음
   - bfill 금지 / ffill만 허용
   - tolerance = 7일 (⚠️ FeatureComposer 는 3일 — Gold 이중구현 불일치 지점)
3. `validate_snapshot_integrity` 무결성 게이트의 KRW 벤치마크 분기.

주의
----
본 테스트는 동작을 *변경*하지 않는다. 리팩터링으로 동작이 의도적으로 바뀌면
본 테스트를 먼저 갱신하고, 골든 기준선(`scripts/freeze_golden_baseline.py --check`)과
함께 회귀 여부를 판정한다.
"""

import os
import pandas as pd
import pytest

from CAiModelBaker.core.data_manager import (
    get_snapshot_path,
    merge_reference_symbols_into_df,
    validate_snapshot_integrity,
)


def _collect_log():
    """local_log 콜백 대체 — 로그 메시지를 리스트로 수집"""
    messages = []
    return messages, (lambda msg: messages.append(str(msg)))


# ==============================================================================
# 1. get_snapshot_path — 8개 모듈이 의존하는 경로 계약
#    ⚠️ [T-5] 절대경로화: 종전에는 `data/...` CWD 상대경로였다 (실행 위치에 따라 데이터 루트 분기)
# ==============================================================================

def _tail(path: str, n: int = 3) -> str:
    """절대경로에서 뒤 n개 구성요소만 추출 (플랫폼 무관 비교용)"""
    import os
    return "/".join(path.replace(os.sep, "/").rstrip("/").split("/")[-n:])


def test_snapshot_path_is_absolute_and_cwd_independent():
    """[T-5] 어느 CWD 에서 실행해도 동일한 절대경로를 반환해야 한다."""
    import os
    path = get_snapshot_path({"SYMBOL_UPBIT": "KRW-BTC", "INTERVAL": "10m"})
    assert os.path.isabs(path), f"절대경로가 아닙니다: {path}"
    assert _tail(path, 2) == "BTC/data_snapshot_10m.csv"
    # 데이터 루트가 CAiModelBaker/data 로 고정되어야 한다 (CWD 의존 제거 계약)
    assert _tail(path, 4) == "CAiModelBaker/data/BTC/data_snapshot_10m.csv"


def test_snapshot_path_contract_upbit_symbol():
    """업비트 심볼은 접두어(KRW-)가 제거된 coin_code 로 경로가 생성되어야 한다."""
    path = get_snapshot_path({"SYMBOL_UPBIT": "KRW-BTC", "INTERVAL": "10m"})
    assert path.endswith("BTC/data_snapshot_10m.csv")


def test_snapshot_path_contract_krx_symbol():
    """국내 종목코드는 coin_code 가 그대로 유지되어야 한다."""
    path = get_snapshot_path({"SYMBOL_UPBIT": "069500", "INTERVAL": "1m"})
    assert path.endswith("069500/data_snapshot_1m.csv")


def test_snapshot_path_contract_defaults():
    """타겟 심볼이 없으면 KRW-BTC 로 조용히 폴백하지 않고 오류를 낸다 (interval 만 1m 기본값)."""
    with pytest.raises(ValueError):
        get_snapshot_path({})
    assert get_snapshot_path({"SYMBOL_UPBIT": "069500"}).endswith("069500/data_snapshot_1m.csv")


def test_snapshot_path_key_priority():
    """SYMBOL_UPBIT > TARGET_STOCK > SYMBOL 우선순위가 유지되어야 한다."""
    path = get_snapshot_path({
        "SYMBOL_UPBIT": "KRW-ETH",
        "TARGET_STOCK": "005930",
        "SYMBOL": "QQQ",
        "INTERVAL": "15m",
    })
    assert path.endswith("ETH/data_snapshot_15m.csv")


# ==============================================================================
# 2. merge_reference_symbols_into_df — no-op 계약
# ==============================================================================

def test_merge_noop_when_reference_symbols_absent(tmp_path):
    """ref_symbols 미설정 시 원본 DF 가 변경 없이 그대로 반환되어야 한다."""
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01 09:00", periods=3, freq="1min"),
        "target_close": [1.0, 2.0, 3.0],
    })
    _, log = _collect_log()

    result = merge_reference_symbols_into_df(df, {}, "1m", str(tmp_path), log)

    assert result is df, "ref_symbols 부재 시 원본 객체가 그대로 반환되어야 함"
    assert list(result.columns) == ["timestamp", "target_close"]


def test_merge_noop_when_df_empty(tmp_path):
    """빈 DF 는 ref_symbols 가 있어도 조용히 그대로 반환되어야 한다."""
    empty = pd.DataFrame(columns=["timestamp", "target_close"])
    _, log = _collect_log()
    config = {"reference_symbols": [{"symbol": "NQ"}]}

    result = merge_reference_symbols_into_df(empty, config, "1m", str(tmp_path), log)

    assert result.empty
    assert list(result.columns) == ["timestamp", "target_close"]


def test_merge_skips_when_candle_files_missing(tmp_path):
    """대장주 캔들 파일이 전혀 없으면 예외 없이 컬럼 미생성 상태로 반환되어야 한다."""
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01 09:00", periods=3, freq="1min"),
        "target_close": [1.0, 2.0, 3.0],
    })
    logs, log = _collect_log()
    config = {"reference_symbols": [{"symbol": "NQ"}]}

    result = merge_reference_symbols_into_df(df, config, "1m", str(tmp_path), log)

    assert not any(c.startswith("ref_") for c in result.columns), "캔들 부재 시 ref_ 컬럼이 생기면 안 됨"
    assert any("전용 과거 캔들 부재" in m for m in logs), "부재 사유가 로그로 남아야 함"


# ==============================================================================
# 3. merge_reference_symbols_into_df — 해외선물 Lookahead 시프트 계약
# ==============================================================================

def _write_overseas_candles(baker_root, symbol, closes, volumes, start="2026-01-01 09:00", freq="1min"):
    """overseas_futures 경로에 대장주 캔들 CSV 생성"""
    target_dir = os.path.join(str(baker_root), "data", "historical", "overseas_futures")
    os.makedirs(target_dir, exist_ok=True)
    df = pd.DataFrame({
        "timestamp": pd.date_range(start, periods=len(closes), freq=freq),
        "close": closes,
        "volume": volumes,
    })
    path = os.path.join(target_dir, f"{symbol}_1m.csv")
    df.to_csv(path, index=False)
    return path


def _target_df(start="2026-01-01 09:00", periods=5):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=periods, freq="1min"),
        "target_close": [1000.0 + i for i in range(periods)],
    })


def test_overseas_reference_candle_start_to_close_shift(tmp_path):
    """
    [핵심 계약] 해외선물 캔들은 '봉 시작 시각'이므로 확정 시각으로 시프트되어야 한다.
    09:00 봉의 종가는 09:01 이후에만 매칭되어야 하며, 09:00 시점에는 미래참조가 없어야 한다.
    """
    _write_overseas_candles(tmp_path, "NQ", closes=[100.0, 101.0, 102.0], volumes=[10.0, 20.0, 30.0])
    _, log = _collect_log()
    config = {"reference_symbols": [{"symbol": "NQ"}]}

    result = merge_reference_symbols_into_df(_target_df(), config, "1m", str(tmp_path), log)

    assert "ref_nq_close" in result.columns
    # ffill 은 결측을 앞으로만 전파하며 bfill 역류는 금지 → 최초 시점은 반드시 NaN
    assert pd.isna(result.loc[0, "ref_nq_close"]), "09:00 시점에 미래 봉 종가가 누출되면 안 됨 (Lookahead)"
    # 봉 시작 시각 → 확정 시각 +1min 시프트 결과
    assert result.loc[1, "ref_nq_close"] == 100.0   # NQ 09:00 봉 종가
    assert result.loc[2, "ref_nq_close"] == 101.0   # NQ 09:01 봉 종가
    assert result.loc[3, "ref_nq_close"] == 102.0   # NQ 09:02 봉 종가
    assert result.loc[4, "ref_nq_close"] == 102.0   # backward hold (ffill)


def test_overseas_reference_volume_and_bid_vol_contract(tmp_path):
    """거래량은 결측 시 0.0, 매수체결량(bid_vol)은 bid ratio 부재 시 volume × 0.5 로 산출되어야 한다."""
    _write_overseas_candles(tmp_path, "NQ", closes=[100.0, 101.0, 102.0], volumes=[10.0, 20.0, 30.0])
    _, log = _collect_log()
    config = {"reference_symbols": [{"symbol": "NQ"}]}

    result = merge_reference_symbols_into_df(_target_df(), config, "1m", str(tmp_path), log)

    assert list(result["ref_nq_volume"]) == [0.0, 10.0, 20.0, 30.0, 30.0]
    assert list(result["ref_nq_bid_vol"]) == [0.0, 5.0, 10.0, 15.0, 15.0], "bid ratio 부재 시 volume × 0.5"


# ==============================================================================
# 4. merge_reference_symbols_into_df — 크립토(비해외) 캔들 시프트 없음 계약
# ==============================================================================

def test_crypto_reference_no_shift(tmp_path):
    """가상자산(binance_extracted) 캔들은 24/7 연속 시계열이므로 시프트되면 안 된다."""
    target_dir = os.path.join(str(tmp_path), "data", "historical", "binance_extracted", "BTCUSDT")
    os.makedirs(target_dir, exist_ok=True)
    pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01 09:00", periods=3, freq="1min"),
        "close": [200.0, 201.0, 202.0],
        "volume": [1.0, 2.0, 3.0],
    }).to_csv(os.path.join(target_dir, "BTCUSDT_1m.csv"), index=False)

    _, log = _collect_log()
    config = {"reference_symbols": [{"symbol": "BTCUSDT"}]}

    result = merge_reference_symbols_into_df(_target_df(), config, "1m", str(tmp_path), log)

    assert "ref_btcusdt_close" in result.columns
    # 시프트 없음 → 최초 시점부터 즉시 매칭
    assert result.loc[0, "ref_btcusdt_close"] == 200.0
    assert result.loc[1, "ref_btcusdt_close"] == 201.0
    assert result.loc[2, "ref_btcusdt_close"] == 202.0


# ==============================================================================
# 5. merge_reference_symbols_into_df — stale 상한 정책 (T-3 에서 동작 변경)
# ==============================================================================

def test_reference_stale_cap_replaces_tolerance(tmp_path):
    """
    ⚠️ [T-3 동작 변경 고정 — D-1 해소]
    종전: `merge_asof(tolerance=7일)` + `ffill()` → ffill 이 tolerance 초과 NaN 을 되메워
          tolerance 가 무력화되었고, 8일 경과분도 이전 값이 무한 전파되었다 (결함, P-2 특성화로 발견).
    현재: 공용 stale 상한 정책(`resolve_stale_cap`)이 **유일한 유효 상한**이다.
          참조 자산군의 cap 을 넘긴 값은 무효화된다.

    NQ 는 시장 분류상 GLOBAL_FUTURES → cap = 4일
      · 2일 경과 → cap 내 → 유지
      · 5일 경과 → cap 초과 → 무효 (NaN)   ← 종전에는 500.0 이 전파되던 구간
      · 8일 경과 → cap 초과 → 무효 (NaN)
    거래량/매수체결량 계열은 fill_value=0.0 으로 무효화된다.
    """
    _write_overseas_candles(
        tmp_path, "NQ", closes=[500.0], volumes=[1.0], start="2026-01-01 09:00"
    )
    _, log = _collect_log()
    config = {"reference_symbols": [{"symbol": "NQ"}]}

    df = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-01-01 09:00",   # 동시점 → 봉 시프트(+1분)로 미매칭
            "2026-01-03 09:01",   # 2일 경과 → cap(4일) 내 → 유지
            "2026-01-06 09:01",   # 5일 경과 → cap 초과 → 무효
            "2026-01-09 09:01",   # 8일 경과 → cap 초과 → 무효
        ]),
        "target_close": [1000.0, 1001.0, 1002.0, 1003.0],
    })

    result = merge_reference_symbols_into_df(df, config, "1m", str(tmp_path), log)

    assert pd.isna(result.loc[0, "ref_nq_close"]), "동시점은 봉 시작→확정 시프트로 미매칭"
    assert result.loc[1, "ref_nq_close"] == 500.0, "2일 경과는 cap 내 → 유지"
    assert pd.isna(result.loc[2, "ref_nq_close"]), "5일 경과는 cap(4일) 초과 → 무효 (D-1 해소)"
    assert pd.isna(result.loc[3, "ref_nq_close"]), "8일 경과 → 무효"

    assert result.loc[1, "ref_nq_volume"] == 1.0
    assert result.loc[2, "ref_nq_volume"] == 0.0, "거래량류는 0.0 으로 무효화"
    assert result.loc[0, "ref_nq_volume"] == 0.0, "최초 시점은 대응 봉이 없어 0.0"


def test_reference_stale_cap_keeps_holiday_gap(tmp_path):
    """정상 휴장(4일 이내)은 cap 내이므로 값이 유지되어야 한다 (과잉 무효화 방지)."""
    _write_overseas_candles(
        tmp_path, "NQ", closes=[500.0], volumes=[1.0], start="2026-01-01 09:00"
    )
    _, log = _collect_log()
    config = {"reference_symbols": [{"symbol": "NQ"}]}

    df = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-01-01 09:01",   # 원천 봉 시점
            "2026-01-05 09:01",   # 4일 경과 (= cap 경계) → 유지
        ]),
        "target_close": [1000.0, 1001.0],
    })

    result = merge_reference_symbols_into_df(df, config, "1m", str(tmp_path), log)

    assert result.loc[0, "ref_nq_close"] == 500.0
    assert result.loc[1, "ref_nq_close"] == 500.0, "cap 경계(4일) 이내는 유지되어야 함"


def test_reference_stale_cap_reports_capped_cols(tmp_path):
    """
    [D-10] 무효화한 컬럼명을 호출자에게 보고해야 한다.

    빌더(`run_kis_dataset_build_pipeline` / `run_dataset_build_pipeline`)는 이 목록을
    `DataCatalogManager.apply_fallback_to_dataframe(..., exclude_cols=...)` 로 전달하여
    하류 ffill 이 무효화를 되살리지 못하게 한다.
    (종전: 무효화 → 하류 ffill 되채움 → 상한 정책 무력화. 실측 ref_hynix_close 11,218행 재보간)
    """
    _write_overseas_candles(
        tmp_path, "NQ", closes=[500.0], volumes=[1.0], start="2026-01-01 09:00"
    )
    _, log = _collect_log()
    config = {"reference_symbols": [{"symbol": "NQ"}]}

    df = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-01-01 09:01",   # 1일 경과 → 유지
            "2026-01-20 09:01",   # 19일 경과 → cap(4일) 초과 → 무효
        ]),
        "target_close": [1000.0, 1001.0],
    })

    capped_cols: list = []
    result = merge_reference_symbols_into_df(
        df, config, "1m", str(tmp_path), log, capped_cols_out=capped_cols
    )

    assert capped_cols == ["ref_nq_close", "ref_nq_volume", "ref_nq_bid_vol"], \
        "무효화 컬럼 3종이 그대로 보고되어야 함 (exclude_cols 전달용)"
    assert pd.isna(result.loc[1, "ref_nq_close"]), \
        "보고된 컬럼이 실제로 무효화된 컬럼이어야 함 (cap 초과 구간)"


def test_reference_merge_capped_cols_out_is_optional(tmp_path):
    """`capped_cols_out` 미전달 시에도 종전 동작이 유지되어야 한다 (후방 호환)."""
    _write_overseas_candles(
        tmp_path, "NQ", closes=[500.0], volumes=[1.0], start="2026-01-01 09:00"
    )
    _, log = _collect_log()
    config = {"reference_symbols": [{"symbol": "NQ"}]}

    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-01 09:01", "2026-01-03 09:01"]),
        "target_close": [1000.0, 1001.0],
    })

    result = merge_reference_symbols_into_df(df, config, "1m", str(tmp_path), log)

    assert result.loc[0, "ref_nq_close"] == 500.0
    assert result.loc[1, "ref_nq_close"] == 500.0, "cap 내 구간은 유지되어야 함"


# ==============================================================================

# ==============================================================================
# 6. validate_snapshot_integrity — KRW 벤치마크 분기 (기존 테스트 미커버 분기)
# ==============================================================================

def test_integrity_gate_passes_clean_krw_dataset():
    _, log = _collect_log()
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=5, freq="15min"),
        "upbit_close": [100_000_000.0] * 5,
        "benchmark_btc_close": [100_000_000.0] * 5,
    })
    assert validate_snapshot_integrity(df, "KRW-BTC", log) is True


def test_integrity_gate_blocks_missing_benchmark_column():
    """가상자산 데이터셋에 benchmark_btc_close 가 누락되면 게이트가 즉시 차단해야 한다."""
    _, log = _collect_log()
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=5, freq="15min"),
        "upbit_close": [100_000_000.0] * 5,
    })
    with pytest.raises(ValueError, match="benchmark_btc_close"):
        validate_snapshot_integrity(df, "KRW-BTC", log)


def test_integrity_gate_blocks_all_nan_benchmark():
    """benchmark_btc_close 전량 결측도 차단 대상이다."""
    _, log = _collect_log()
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=5, freq="15min"),
        "upbit_close": [100_000_000.0] * 5,
        "benchmark_btc_close": [float("nan")] * 5,
    })
    with pytest.raises(ValueError, match="benchmark_btc_close"):
        validate_snapshot_integrity(df, "KRW-BTC", log)

