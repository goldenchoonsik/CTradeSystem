# -*- coding: utf-8 -*-
"""
스마트 분할 (봉수 맞춤 + 데이터 스캔) 계약 테스트 (P-1 ~ P-4)
============================================================
고정하는 계약
-------------
1. `market_calendar` — 거래일 카운트/스냅, 크립토 연중무휴 분기
2. `split_standards` — 봉수 버킷 분류, 표준값 창수 4~6(Pardo), 3-Tier 판정
3. `split_planner` — 데이터 스캔 기반 경계 확정, 휴일 스냅, purge, 휴일 테이블 충돌 진단

실측 결함 (069500/10s, 2026-09-15)
----------------------------------
웹 대시보드가 `maxDate − holdoutDays×86400000` 의 **순수 달력일 산술**로 경계를 잡았고
휴일을 전혀 고려하지 않았다.
  · 10s 표준 홀드아웃 = 0.1개월 = 달력 3일 → `2026-09-12 (토)`
  · 실제 데이터는 09-12(토)·09-13(일) 부재 → 측정 구간이 **거래일 2일**로 축소
  · 그 2일에 거래 1건 → `target_trades = 1` 붕괴 → **거래 1건이 배포** (D-12)

휴일 테이블 자체의 오분류 (데이터 우선 원칙의 근거)
  · 원천 `A069500_1m.csv`: `2026-08-03` 381 bars 有 (정상 거래일) — 테이블은 휴일로 오분류
  · 원천 `A069500_1m.csv`: `2026-08-17` 0 bars (실제 휴장)      — 테이블과 일치
  → **실제 bar 존재 여부가 1차 진실**이며, 테이블은 진단에만 사용한다.
"""

import os

import pandas as pd
import pytest

from shared.core.market_calendar import (
    count_trading_days,
    get_market_holidays,
    is_continuous_market,
    is_trading_day,
    next_trading_day,
    next_trading_day_strict,
    prev_trading_day,
    prev_trading_day_strict,
    snap_to_trading_day,
)
from CAiModelBaker.core.split_planner import (
    diagnose_calendar_gaps,
    plan_split,
    scan_daily_bars,
    summarize_split,
)
from CAiModelBaker.core.split_standards import (
    MINIMUMS,
    STANDARD_SPLITS,
    SPLIT_MODE_FULL,
    SPLIT_MODE_MINIMUM,
    SPLIT_MODE_PROPORTIONAL,
    SPLIT_MODE_STANDARD,
    SPLIT_MODE_UNAVAILABLE,
    compute_scaled_split,
    expected_wfa_windows,
    get_standard_split,
    resolve_interval_bucket,
)

_REAL_SNAPSHOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "CAiModelBaker", "data", "069500", "data_snapshot_10s.csv",
)


def _trading_dates(end="2026-09-15", n=40):
    """실제 KRX 거래일(데이터 우선) — 주말과 **실제 휴장**(2026-08-17)만 제외한다.

    ⚠️ 휴일 테이블이 오분류한 `2026-08-03` 은 **포함**한다 (데이터에 bar 가 존재하는 날).
    """
    out, d = [], pd.Timestamp(end)
    while len(out) < n:
        if d.weekday() < 5 and str(d.date()) != "2026-08-17":
            out.append(str(d.date()))
        d -= pd.Timedelta(days=1)
    return sorted(out)


def _mk_df(dates, bars_per_day=100, start_time="09:00:00"):
    """날짜 목록 → 균일 bar 데이터프레임"""
    rows = []
    for d in dates:
        base = pd.Timestamp(f"{d} {start_time}")
        rows.extend(base + pd.Timedelta(minutes=i) for i in range(bars_per_day))
    return pd.DataFrame({"timestamp": rows})


# ==============================================================================
# 1. market_calendar — 거래일 카운트/스냅 + 크립토 연중무휴 (P-1)
# ==============================================================================

def test_continuous_market_is_never_skipped():
    """크립토는 연중무휴 — 주말/공휴일에도 거래일이어야 한다."""
    assert is_continuous_market("KRW-BTC") is True
    assert is_continuous_market("069500") is False

    assert is_trading_day("2026-09-12", "KRX") is False      # 토요일
    assert is_trading_day("2026-09-12", "CRYPTO") is True
    assert is_trading_day("2026-05-05", "CRYPTO") is True    # 어린이날
    assert get_market_holidays("CRYPTO") == set()


def test_count_trading_days_matches_market():
    """거래일 수는 시장별로 달라야 한다 (크립토 = 전 일수)."""
    krx = count_trading_days("2026-07-21", "2026-09-15", "KRX")
    crypto = count_trading_days("2026-07-21", "2026-09-15", "CRYPTO")
    assert crypto == 57, "7/21~9/15 은 달력 57일 → 크립토는 57거래일"
    assert krx < crypto, "KRX 는 주말/휴장일 제외로 더 적어야 한다"


def test_snap_to_trading_day_avoids_holidays():
    """휴장일 경계는 반드시 거래일로 스냅되어야 한다 (실측 09-12 토요일)."""
    assert snap_to_trading_day("2026-09-12", "KRX", "prev") == "2026-09-11"
    assert snap_to_trading_day("2026-09-12", "KRX", "next") == "2026-09-14"
    assert snap_to_trading_day("2026-09-11", "KRX", "prev") == "2026-09-11", "당일이 거래일이면 그대로"
    assert prev_trading_day("2026-08-17", "KRX") == "2026-08-14"   # 광복절 대체공휴일
    assert next_trading_day("2026-08-17", "KRX") == "2026-08-18"
    assert snap_to_trading_day("2026-09-12", "CRYPTO", "prev") == "2026-09-12", "크립토는 스냅 불필요"


def test_snap_robust_to_bad_input():
    """비정상 입력은 예외 없이 None."""
    assert snap_to_trading_day("bad-date", "KRX") is None
    assert snap_to_trading_day(None, "KRX") is None


def test_strict_trading_day_variants_exclude_the_day_itself():
    """
    `*_strict` 는 **당일을 제외**해야 한다.

    WFA 종료 경계처럼 "그 날짜를 반드시 제외해야 하는" 곳에 필요하다.
    2026-09-08 은 화요일(거래일) 이므로:
        prev_trading_day(09-08)        → 09-08 (당일 반환 → 홀드아웃 첫날 침범!)
        prev_trading_day_strict(09-08) → 09-07 ← WFA 종료에 사용해야 하는 값
    """
    assert prev_trading_day("2026-09-08", "KRX") == "2026-09-08"      # 당일 반환
    assert prev_trading_day_strict("2026-09-08", "KRX") == "2026-09-07"  # 당일 제외

    # 토요일 기준: prev 는 금요일, strict 도 금요일(토요일은 거래일이 아니므로)
    assert prev_trading_day("2026-09-12", "KRX") == "2026-09-11"
    assert prev_trading_day_strict("2026-09-12", "KRX") == "2026-09-11"

    # next strict
    assert next_trading_day("2026-09-08", "KRX") == "2026-09-08"
    assert next_trading_day_strict("2026-09-08", "KRX") == "2026-09-09"
    assert next_trading_day_strict("2026-09-04", "KRX") == "2026-09-07", "금요일 다음은 월요일"

    # 크립토(연중무휴)는 달력 ±1일
    assert prev_trading_day_strict("2026-09-08", "KRW-BTC") == "2026-09-07"
    assert next_trading_day_strict("2026-09-08", "KRW-BTC") == "2026-09-09"


def test_strict_variants_robust_to_bad_input():
    assert prev_trading_day_strict("bad", "KRX") is None
    assert next_trading_day_strict(None, "KRX") is None


# ==============================================================================
# 2. split_standards — 버킷 분류 · 표준 정합 · 3-Tier (P-2)
# ==============================================================================

@pytest.mark.parametrize("interval,bucket", [
    ("5s", "sub_minute"), ("10s", "sub_minute"), ("30s", "sub_minute"),
    ("1m", "1m"), ("3m", "1m"),
    ("5m", "5m"), ("10m", "5m"), ("15m", "5m"),
    ("30m", "30m"), ("1h", "1h"), ("60m", "1h"), ("4h", "1h"),
    ("1d", "1d"), ("1D", "1d"),
])
def test_interval_bucket_classification(interval, bucket):
    assert resolve_interval_bucket(interval) == bucket


def test_all_standard_splits_yield_4_to_6_windows():
    """모든 봉수 표준값이 Pardo 권장 창수(4~6)를 만족해야 한다."""
    for bucket, s in STANDARD_SPLITS.items():
        w = expected_wfa_windows(
            s["lookback_td"], s["holdout_td"], s["train_td"], s["opt_td"], s["test_td"]
        )
        assert 4 <= w <= 6, f"{s['label']} 창수 {w} — 표준 4~6 위반"


def test_standard_mode_when_data_sufficient():
    """데이터가 표준 lookback 을 충족하면 Tier 1(standard)."""
    r = compute_scaled_split("10s", 63)
    assert r["mode"] == SPLIT_MODE_STANDARD
    assert r["standard_met"] is True
    assert r["scale"] == 1.0
    assert r["deviations"] == {}
    assert r["reason"] is None


def test_proportional_or_minimum_mode_when_data_short():
    """데이터가 부족하면 비표준(비율/최소)으로 강등되고 사유가 기록되어야 한다."""
    r = compute_scaled_split("1m", 90)          # 표준 lookback 126
    assert r["mode"] in (SPLIT_MODE_PROPORTIONAL, SPLIT_MODE_MINIMUM)
    assert r["standard_met"] is False
    assert 0.0 < r["scale"] < 1.0
    assert r["reason"] and "축소" in r["reason"]
    assert r["deviations"], "표준과 다른 항목이 기록되어야 한다"


def test_minimum_mode_clamps_to_floors():
    """비율 축소가 최소값에 클램프되면 Tier 3(minimum) + 사유에 클램프 명시.

    sub_minute 표준 lookback=63. 데이터 20거래일 → scale=20/63≈0.32.
    비율 축소 후 train = round(21×0.32) = 7 < 최솟값(8) → 클램프 발생 → minimum.
    """
    r = compute_scaled_split("10s", 20)
    assert r["mode"] == SPLIT_MODE_MINIMUM
    assert r["train_td"] >= MINIMUMS["train_td"]
    assert r["holdout_td"] >= MINIMUMS["holdout_td"]
    assert "클램프" in (r["reason"] or "")


def test_unavailable_mode_when_data_too_small():
    """최소 조합조차 불가하면 WFA 불가 (Tier 0).

    sub_minute 최솟값 합 = train(8) + opt(2) + test(2) + holdout(5) = 17거래일.
    16거래일 데이터는 17 미만이므로 WFA 불가여야 한다.
    """
    r = compute_scaled_split("10s", 16)
    assert r["mode"] == SPLIT_MODE_UNAVAILABLE
    assert r["holdout_td"] == 0
    assert "WFA 불가" in (r["reason"] or "")


def test_scaled_split_never_below_minimums():
    """어떤 입력에서도 최소값 아래로 내려가지 않아야 한다."""
    for avail in range(31, 400, 7):
        for iv in ("10s", "1m", "10m", "1d"):
            r = compute_scaled_split(iv, avail)
            if r["mode"] in (SPLIT_MODE_STANDARD, SPLIT_MODE_PROPORTIONAL, SPLIT_MODE_MINIMUM):
                for k in ("train_td", "opt_td", "test_td", "holdout_td"):
                    assert r[k] >= MINIMUMS[k], f"{iv}/{avail} → {k}={r[k]} < 최소 {MINIMUMS[k]}"


def test_standard_split_has_bucket_and_note():
    std = get_standard_split("10s")
    assert std["bucket"] == "sub_minute"
    assert std["holdout_td"] == 10
    assert std["note"]


# ==============================================================================
# 3. split_planner — 데이터 스캔 기반 경계 확정 (P-3)
# ==============================================================================

def test_scan_daily_bars_counts_per_day():
    """날짜별 bar 수 집계가 정확해야 한다 (유효 거래일 판정의 1차 진실)."""
    df = _mk_df(["2026-09-14", "2026-09-15"], bars_per_day=7)
    scan = scan_daily_bars(df)
    assert len(scan) == 2
    assert list(scan.values) == [7, 7]
    assert str(scan.index[0]) == "2026-09-14"


def test_scan_daily_bars_robust():
    """빈/잘못된 입력은 예외 없이 빈 Series."""
    assert scan_daily_bars(None).empty
    assert scan_daily_bars(pd.DataFrame({"x": [1]})).empty
    assert scan_daily_bars(pd.DataFrame()).empty


def test_plan_split_boundaries_are_real_data_dates():
    """
    경계는 반드시 **실재 bar 날짜**여야 한다 (주말·휴장일 배제).
    실측: 종전 달력일 산술은 홀드아웃 시작을 토요일(09-12)로 잡았다.
    """
    dates = _trading_dates(n=40)
    df = _mk_df(dates, bars_per_day=100)
    r = plan_split(df, "10s", market="KRX")

    assert r["ok"] is True
    for key in ("wfa_start", "wfa_end", "holdout_start", "holdout_end"):
        assert r[key] in dates, f"{key}={r[key]} 가 실제 데이터 날짜가 아닙니다"

    # 경계일이 주말이면 안 된다
    for key in ("wfa_start", "wfa_end", "holdout_start", "holdout_end"):
        assert pd.Timestamp(r[key]).weekday() < 5, f"{key}={r[key]} 가 주말입니다 [P-3]"

    # 시간 순서 정합 + 누수 차단
    assert r["wfa_start"] <= r["wfa_end"] < r["holdout_start"] <= r["holdout_end"]


def test_plan_split_holdout_expands_beyond_calendar_arithmetic():
    """
    [핵심 회귀] 40거래일 데이터에서 홀드아웃은 표준 기준으로 확보되어야 한다.
    종전 달력일 산술(10s 표준 0.1개월=3일)은 거래일 2일로 축소되었고,
    그 결과 `target_trades=1` 붕괴 → 거래 1건이 배포되었다.
    """
    dates = _trading_dates(n=40)
    df = _mk_df(dates, bars_per_day=100)
    r = plan_split(df, "10s", market="KRX")

    assert r["holdout_td_actual"] >= 5, \
        f"홀드아웃 {r['holdout_td_actual']}거래일 — 최소 5거래일 미달 [P-3]"
    assert r["holdout_td_actual"] > 2, "종전 실측(2거래일)보다 반드시 커야 한다"
    assert r["holdout_bars"] == r["holdout_td_actual"] * 100, "홀드아웃 bar 수 정합"
    assert r["non_standard"] is True, "데이터 부족이므로 비표준 표시가 필요하다"
    assert r["mode"] in (SPLIT_MODE_PROPORTIONAL, SPLIT_MODE_MINIMUM)


def test_plan_split_purge_secures_bar_gap():
    """WFA 종료와 홀드아웃 시작 사이 bar 간격이 purge_candles 이상이어야 한다 (AFML Ch.7)."""
    dates = _trading_dates(n=40)
    df = _mk_df(dates, bars_per_day=100)

    no_purge = plan_split(df, "10s", market="KRX", purge_candles=None)
    with_purge = plan_split(df, "10s", market="KRX", purge_candles=250)

    assert no_purge["purge_days_dropped"] == 0
    assert with_purge["purge_bars_gap"] >= 250, "purge bar 간격 확보 실패"
    assert with_purge["purge_days_dropped"] >= 3, "100bars/일 → 250bars 는 3일 필요"
    assert with_purge["wfa_end"] < no_purge["wfa_end"], "purge 로 WFA 종료가 앞당겨져야 함"
    assert with_purge["wfa_end"] < with_purge["holdout_start"]


def test_plan_split_detects_holiday_table_misclassification(monkeypatch):
    """
    [데이터 우선 원칙] 휴일 테이블이 휴일이라 주장해도 **데이터에 bar 가 있으면 거래일**이다.
    실측: `2026-08-03` 은 원천에 381 bars 가 존재하지만 테이블은 휴일로 오분류했다.

    실제 테이블의 오분류는 2026-10-01 에 고쳤다(관측된 거래일은 휴일이 될 수 없다). 이 테스트는 오분류 **감지**
    기능을 검증하므로, 실제 테이블에 기대지 않고 오분류가 든 테이블을 주입한다.
    """
    import CAiModelBaker.core.split_planner as _sp
    _real = get_market_holidays("KRX")
    monkeypatch.setattr(_sp, "get_market_holidays", lambda market: set(_real) | {"2026-08-03"})
    dates = _trading_dates(n=40)
    assert "2026-08-03" in dates, "테스트 전제: 08-03 은 실제 데이터가 있는 날"

    df = _mk_df(dates, bars_per_day=100)
    r = plan_split(df, "10s", market="KRX")

    assert "2026-08-03" in r["diagnostics"]["mismatched_holidays"], \
        "휴일 테이블 오분류가 진단에 보고되어야 한다 [P-3]"
    assert any("오분류" in w for w in r["warnings"]), "경고 문구가 생성되어야 한다"

    # 데이터에 존재하는 날은 거래일로 집계되어야 한다
    assert r["data"]["trading_days"] == 40


def test_diagnose_calendar_gaps_reports_missing_collection():
    """테이블상 거래일인데 데이터가 없으면 수집 누락으로 보고해야 한다."""
    dates = [d for d in _trading_dates(end="2026-09-15", n=40) if d != "2026-09-04"]
    diag = diagnose_calendar_gaps(pd.to_datetime(dates).date.tolist(), "KRX")
    assert "2026-09-04" in diag["missing_trading_days"]
    assert diag["expected_holidays"], "정상 휴장일 목록도 함께 산출되어야 한다"


def test_diagnose_skipped_for_continuous_market():
    """크립토는 휴장일 개념이 없으므로 진단을 생략한다 (빈 결과)."""
    diag = diagnose_calendar_gaps(
        pd.to_datetime(["2026-09-12", "2026-09-13"]).date.tolist(), "CRYPTO"
    )
    assert diag == {"mismatched_holidays": [], "missing_trading_days": [], "expected_holidays": []}


def test_plan_split_full_mode_80_20():
    """수동 80:20 분할 — 홀드아웃 20% 를 거래일 기준으로 확보."""
    dates = _trading_dates(n=40)
    df = _mk_df(dates, bars_per_day=100)
    r = plan_split(df, "10s", market="KRX", mode="full")

    assert r["ok"] is True
    assert r["mode"] == SPLIT_MODE_FULL
    assert r["holdout_td_actual"] == 8, "40거래일 × 20% = 8거래일"
    assert r["wfa_td_actual"] == 32
    assert r["wfa_start"] == dates[0]
    assert r["holdout_end"] == dates[-1]


def test_plan_split_crypto_uses_all_calendar_days():
    """크립토는 주말도 거래일이므로 연속 날짜가 그대로 창에 포함된다."""
    dates = [str(d.date()) for d in pd.date_range("2026-08-07", "2026-09-15", freq="D")]
    df = _mk_df(dates, bars_per_day=10)
    r = plan_split(df, "10s", market="KRW-BTC")

    assert r["ok"] is True
    assert r["data"]["trading_days"] == 40, "크립토는 주말 포함 40일 전부 거래일"
    assert r["diagnostics"]["mismatched_holidays"] == [], "크립토는 휴일 진단 제외"
    assert r["holdout_end"] == "2026-09-15"
    # 주말이 창에 포함되어도 정상 (KRX 였다면 제외됨)
    assert any(pd.Timestamp(d).weekday() >= 5 for d in dates)


def test_plan_split_crypto_small_data_is_unavailable():
    """크립토라도 최소 조합(31거래일) 미만이면 WFA 불가 — 정책은 시장 공통이다."""
    dates = [str(d.date()) for d in pd.date_range("2026-09-01", "2026-09-15", freq="D")]
    df = _mk_df(dates, bars_per_day=10)
    r = plan_split(df, "10s", market="KRW-BTC")
    assert r["ok"] is False
    assert r["mode"] == SPLIT_MODE_UNAVAILABLE
    assert "WFA 불가" in (r["reason"] or "")


def test_plan_split_unavailable_when_data_too_small():
    """데이터가 최소 조합보다 작으면 WFA 불가 사유와 함께 ok=False."""
    df = _mk_df(_trading_dates(n=10), bars_per_day=50)
    r = plan_split(df, "10s", market="KRX")
    assert r["ok"] is False
    assert r["mode"] == SPLIT_MODE_UNAVAILABLE
    assert "WFA 불가" in (r["reason"] or "")


def test_plan_split_robust_to_bad_input():
    """빈 데이터/단일 날짜는 예외 없이 ok=False."""
    assert plan_split(pd.DataFrame(), "10s")["ok"] is False
    assert plan_split(_mk_df(["2026-09-15"], bars_per_day=5), "10s")["ok"] is False


def test_summarize_split_is_human_readable():
    """요약 문자열에 표준/비표준 뱃지와 핵심 수치가 포함되어야 한다."""
    df = _mk_df(_trading_dates(n=40), bars_per_day=100)
    text = summarize_split(plan_split(df, "10s", market="KRX"))
    assert "비표준" in text
    assert "거래일" in text and "홀드아웃" in text
    assert "⛔" in summarize_split({"ok": False, "reason": "데이터 없음"})


def test_real_069500_10s_regression():
    """
    [실측 회귀 고정] 실제 069500/10s 스냅샷으로 종전 결함이 해소되었는지 확인한다.

    종전: 달력일 산술로 홀드아웃이 09-12(토)~09-15 → **거래일 2일** (거래 1건 배포)
    신규: 데이터 스캔으로 09-08~09-15 → **거래일 6일** (14,046 bars)
    """
    if not os.path.exists(_REAL_SNAPSHOT):
        pytest.skip("실측 스냅샷 없음")

    df = pd.read_csv(_REAL_SNAPSHOT, usecols=["timestamp"])
    r = plan_split(df, "10s", market="KRX", purge_candles=32)

    assert r["ok"] is True
    assert r["data"]["trading_days"] == 38, "실측 정상 데이터셋은 38거래일 (장 마감 전 미완료 2일 가짜 수평선 제거 반영)"
    assert r["holdout_td_actual"] == 6, "종전 2거래일 → 6거래일로 확대되어야 한다"
    assert r["holdout_bars"] > 10000, f"홀드아웃 bar {r['holdout_bars']} — 표본 3배 확대 기대"
    assert r["non_standard"] is True
    assert r["expected_wfa_windows"] >= 2, "WFA 창이 최소 2개는 확보되어야 한다"
    assert "2026-08-03" in r["diagnostics"]["mismatched_holidays"]


def test_scan_daily_bars_filters_zero_volume_and_dust_days():
    """거래량 0 또는 비정상 먼지 거래일(Dust day)이 자동 배제되어야 한다."""
    dates = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    rows = []
    # 09-01: 정상 거래 (100봉, 봉당 100주)
    for i in range(100):
        rows.append({"timestamp": f"2026-09-01 09:{i//60:02d}:{i%60:02d}", "target_volume": 100.0})
    # 09-02: 거래량 0 (100봉, 봉당 0주)
    for i in range(100):
        rows.append({"timestamp": f"2026-09-02 09:{i//60:02d}:{i%60:02d}", "target_volume": 0.0})
    # 09-03: 먼지 거래일 (100봉 중 95봉이 0, 총 거래량 10주)
    for i in range(100):
        vol = 2.0 if i < 5 else 0.0
        rows.append({"timestamp": f"2026-09-03 09:{i//60:02d}:{i%60:02d}", "target_volume": vol})
    # 09-04: 정상 거래 (100봉, 봉당 50주)
    for i in range(100):
        rows.append({"timestamp": f"2026-09-04 09:{i//60:02d}:{i%60:02d}", "target_volume": 50.0})

    df = pd.DataFrame(rows)
    scan = scan_daily_bars(df)

    assert len(scan) == 2, f"예상 2거래일 대신 {len(scan)}거래일 반환"
    assert [str(d) for d in scan.index] == ["2026-09-01", "2026-09-04"]
    excluded = scan.attrs.get("excluded_no_trade_days", [])
    assert "2026-09-02" in excluded
    assert "2026-09-03" in excluded


def test_find_contiguous_trading_segments_detects_gap():
    """영업일 결측 Gap이 존재할 때 독립 세그먼트로 분할되어야 한다."""
    from CAiModelBaker.core.split_planner import find_contiguous_trading_segments
    # 07-21 ~ 07-24 (4일 연속) + 장기 결측 (8월 중순) + 08-20 ~ 08-21 (2일 연속)
    dates = ["2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24", "2026-08-20", "2026-08-21"]
    segs = find_contiguous_trading_segments(dates, market="KRX")

    assert len(segs) == 2
    assert segs[0] == ["2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]
    assert segs[1] == ["2026-08-20", "2026-08-21"]


def test_plan_split_holdout_modes_and_units():
    """단일 순수 홀드아웃(100%)과 2단계 고정거래일 분할 모드가 정확히 작동해야 한다."""
    from CAiModelBaker.core.split_standards import HOLDOUT_MODE_FULL, HOLDOUT_MODE_SPLIT
    dates = _trading_dates(n=35)
    df = _mk_df(dates, bars_per_day=100)

    # 1. 단일 순수 홀드아웃 (Full: 100% OOS)
    r_full = plan_split(df, "10s", market="KRX", holdout_mode=HOLDOUT_MODE_FULL)
    assert r_full["ok"] is True
    assert r_full["holdout_mode"] == HOLDOUT_MODE_FULL
    assert r_full["final_test_start"] == r_full["holdout_start"]
    assert r_full["final_test_end"] == r_full["holdout_end"]
    assert r_full["final_test_td"] == r_full["holdout_td_actual"]
    assert r_full["units"]["td"]["holdout"] == r_full["units"]["td"]["final_test"]

    # 2. 2단계 고정거래일 분할 (Split: final_test_td=2 고정)
    r_split = plan_split(df, "10s", market="KRX", holdout_mode=HOLDOUT_MODE_SPLIT, final_test_td=2)
    assert r_split["ok"] is True
    assert r_split["holdout_mode"] == HOLDOUT_MODE_SPLIT
    assert r_split["final_test_td"] == 2
    assert r_split["val_td"] == r_split["holdout_td_actual"] - 2
    assert pd.Timestamp(r_split["val_end"]) < pd.Timestamp(r_split["final_test_start"])
    assert r_split["units"]["td"]["final_test"] == 2
    assert r_split["units"]["td"]["val"] == r_split["holdout_td_actual"] - 2

