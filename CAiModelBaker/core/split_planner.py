# -*- coding: utf-8 -*-
"""
데이터 스캔 기반 분할 플래너 (P-3)
==================================
"**달력일 계산은 seed, 최종 확정은 실제 데이터 스캔**" 원칙으로 WFA·홀드아웃 경계를 결정한다.

📌 종전 결함 (실측 2026-09-15, 069500/10s)
  웹 대시보드가 `maxDate − holdoutDays×86400000` 의 **순수 달력일 산술**로 경계를 잡았고
  휴일을 전혀 고려하지 않았다.
    · 10s 표준 홀드아웃 = 0.1개월 = 달력 3일 → `2026-09-12 (토)`
    · 실제 데이터는 09-12(토)·09-13(일) 부재 → 측정 구간이 **거래일 2일**로 축소
    · 그 2일에 거래 1건이 발생 → `target_trades = 1`(붕괴) → **passed=True 배포** (D-12)

📌 데이터 우선(Data-first) 판정 — 휴일 테이블은 보조
  실측(원천 Bronze `A069500_1m.csv`):
    · `2026-08-03` → **381 bars 존재** (정상 거래일)  ❌ 그런데 휴일 테이블은 휴일로 분류
    · `2026-07-02` → **381 bars 존재** (정상 거래일)  ❌ 그런데 휴일 테이블은 휴일로 분류
    · `2026-08-17` → 0 bars (실제 휴장)              ✓ 테이블과 일치
  → 휴일 테이블(Yahoo 일봉 결측 기반 역산)은 **오분류가 존재**하므로 1차 진실이 될 수 없다.
     **실제 bar 존재 여부가 1차 진실**이고, 테이블은 아래 진단에만 사용한다.
       · `mismatched_holidays`   : 테이블=휴일 & 데이터 有 → **테이블 오분류 경고**
       · `missing_trading_days`  : 테이블=거래일 & 데이터 無 → **수집 누락 경고**

📌 경계 확정 절차
  ① 날짜별 bar 수 스캔 → 유효 거래일 = 실제 bar 가 존재하는 날
  ② 3-Tier 표준(`split_standards.compute_scaled_split`)으로 창 크기 결정
  ③ 마지막 데이터부터 역순으로 홀드아웃 → WFA 순서로 배치
  ④ 경계를 **실재 bar 날짜**로 스냅 (주말·휴장일 배제)
  ⑤ Purge — WFA 종료와 홀드아웃 시작 사이 bar 간격 ≥ `purge_candles` 확보 (AFML Ch.7)
"""

from typing import Any, Dict, List, Optional

import pandas as pd

from shared.core.market_calendar import (
    get_market_holidays,
    get_trading_days,
    is_continuous_market,
)
from CAiModelBaker.core.split_standards import (
    DEFAULT_FINAL_TEST_TD,
    HOLDOUT_MODE_FULL,
    HOLDOUT_MODE_SPLIT,
    MIN_HOLDOUT_TRADES,
    SPLIT_MODE_FULL,
    SPLIT_MODE_STANDARD,
    SPLIT_MODE_UNAVAILABLE,
    compute_scaled_split,
    expected_wfa_windows,
    get_minimums_for_bucket,
    get_standard_split,
    trading_days_per_month,
)


def scan_daily_bars(
    df: pd.DataFrame,
    ts_col: str = "timestamp",
    vol_col: Optional[str] = None,
    filter_no_trade: bool = True,
) -> pd.Series:
    """날짜별 bar 수 집계 (유효 거래일 판정의 1차 진실).

    거래량(Volume) 컬럼이 존재하는 경우, 거래량이 0이거나 체결이 전무한
    비정상 무거래일(Dust / No-trade Days)을 자동 탐지하여 제외한다.

    Args:
        df: timestamp 컬럼을 가진 데이터프레임 (스냅샷 또는 피처 프레임)
        ts_col: 타임스탬프 컬럼명
        vol_col: 볼륨 컬럼명 (미지정 시 target_volume, upbit_volume, volume 자동 탐색)
        filter_no_trade: 무거래일 필터링 여부 (기본 True)

    Returns:
        index=date, values=bar 수 (날짜 오름차순). 실패 시 빈 Series.
        (Series.attrs['excluded_no_trade_days'] 에 제외된 날짜 리스트 기록)
    """
    if df is None or ts_col not in getattr(df, "columns", []):
        return pd.Series(dtype="int64")

    cols = [ts_col]
    v_col = None
    if filter_no_trade:
        if vol_col and vol_col in df.columns:
            v_col = vol_col
        else:
            candidates = ["target_volume", "upbit_volume", "volume", "vol"]
            for c in candidates:
                if c in df.columns:
                    v_col = c
                    break
        if v_col:
            cols.append(v_col)

    sub = df[cols].copy()
    sub[ts_col] = pd.to_datetime(sub[ts_col], errors="coerce")
    sub = sub.dropna(subset=[ts_col])
    if sub.empty:
        return pd.Series(dtype="int64")

    sub["date"] = sub[ts_col].dt.date
    excluded_dates = []

    if v_col:
        # 일별 집계: bars, vol_sum, zero_vol_ratio
        g = sub.groupby("date")[v_col].agg(
            bars="count",
            vol_sum="sum",
            zero_ratio=lambda x: (x == 0).mean()
        )
        # 판정:
        # 1) 하루 총 거래량 0 -> 완전 무거래일
        # 2) 캔들수 50봉 이상인데 90% 이상이 거래량 0 이고 하루 총 거래량이 10,000 미만 -> 허수/먼지 거래일
        mask = (g["vol_sum"] > 0) & ~((g["zero_ratio"] > 0.90) & (g["bars"] >= 50) & (g["vol_sum"] < 10000))
        valid_g = g[mask]
        excluded_dates = [str(d) for d in g[~mask].index]
        res = valid_g["bars"].sort_index().astype("int64")
    else:
        res = sub["date"].value_counts().sort_index().astype("int64")

    res.attrs["excluded_no_trade_days"] = excluded_dates
    return res


def find_contiguous_trading_segments(
    dates_sorted: List[Any],
    market: str = "KRX",
    max_gap_trading_days: int = 2,
) -> List[List[Any]]:
    """거래일 리스트에서 비정상 결측(Gap >= 3영업일)을 감지하여 연속 유효 매매 구간들로 분할한다.

    Args:
        dates_sorted: 정렬된 date 객체 또는 YYYY-MM-DD 문자열 리스트
        market: 시장 ('KRX', 'US', 'CRYPTO' 등)
        max_gap_trading_days: 허용되는 최대 결측 영업일 수 (기본 2일)

    Returns:
        연속된 날짜 구간 리스트: [[date1, date2, ...], [dateK, ...]]
    """
    if not dates_sorted:
        return []
    if len(dates_sorted) == 1:
        return [list(dates_sorted)]

    d_objs = [pd.to_datetime(d).date() for d in dates_sorted]
    segments: List[List[Any]] = []
    curr_seg: List[Any] = [dates_sorted[0]]
    continuous = is_continuous_market(market)

    for i in range(1, len(dates_sorted)):
        prev_d, curr_d = d_objs[i - 1], d_objs[i]
        is_gap = False
        if continuous:
            if (curr_d - prev_d).days > (max_gap_trading_days + 1):
                is_gap = True
        else:
            cal_td = get_trading_days(str(prev_d), str(curr_d), market)
            missing_td = max(0, len(cal_td) - 2)
            if missing_td > max_gap_trading_days:
                is_gap = True

        if is_gap:
            segments.append(curr_seg)
            curr_seg = [dates_sorted[i]]
        else:
            curr_seg.append(dates_sorted[i])

    segments.append(curr_seg)
    return segments


def diagnose_calendar_gaps(dates_sorted: List[Any], market: str = "KRX") -> Dict[str, List[str]]:
    """데이터에 존재하는 날짜와 시장 캘린더(휴일 테이블)를 대조해 충돌을 진단한다.

    Returns:
        {
          "mismatched_holidays": [테이블=휴일 & 데이터 有]  → 테이블 오분류 경고
          "missing_trading_days": [테이블=거래일 & 데이터 無] → 수집 누락 경고
          "expected_holidays":    [테이블=휴일 & 데이터 無]  → 정상 휴장
        }
    """
    out: Dict[str, List[str]] = {
        "mismatched_holidays": [],
        "missing_trading_days": [],
        "expected_holidays": [],
    }
    if not dates_sorted:
        return out

    data_dates = {str(d) for d in dates_sorted}
    first_d, last_d = min(data_dates), max(data_dates)

    # 연중무휴 시장(크립토)은 휴장일 개념이 없으므로 진단 생략
    if is_continuous_market(market):
        return out

    holidays = get_market_holidays(market)
    calendar_td = set(get_trading_days(first_d, last_d, market))

    out["mismatched_holidays"] = sorted(data_dates & holidays)
    out["missing_trading_days"] = sorted(calendar_td - data_dates)
    out["expected_holidays"] = sorted(holidays - data_dates)
    return out


def plan_split(df: pd.DataFrame, interval: str, market: str = "KRX",
               mode: str = "smart", purge_candles: Optional[int] = None,
               ts_col: str = "timestamp", vol_col: Optional[str] = None,
               full_holdout_ratio: float = 0.2,
               holdout_mode: str = HOLDOUT_MODE_FULL,
               final_test_td: Optional[int] = None,
               require_contiguous: bool = False) -> Dict[str, Any]:
    """데이터를 스캔하여 WFA·홀드아웃 경계를 **실재 거래일 기준**으로 확정한다 (P-3).

    무거래일(Volume 0 / 허수 거래일)을 자동 제외하며, require_contiguous=True 인 경우
    대규모 결측(Gap >= 3영업일)이 발견될 때 최신 연속 유효 구간에 경계를 고정한다.
    require_contiguous=False (기본값) 인 경우 비정상 결측 Gap을 건너뛰고 전체 유효 거래일을 통합한다.

    Args:
        df: timestamp 및 volume 컬럼을 가진 데이터프레임 (스냅샷/피처 프레임)
        interval: 인터벌 문자열 ('10s', '1m', '10m', '1d')
        market: 시장 ('KRX' / 'US' / 'CRYPTO' / 심볼명). 크립토는 연중무휴 자동 처리.
        mode: 'smart' (봉수 맞춤 3-Tier) | 'full' (전체 수동 분할)
        purge_candles: AFML Purge bar 수 (WFA 종료와 홀드아웃 시작 사이 최소 bar 간격)
        ts_col: 타임스탬프 컬럼명
        vol_col: 볼륨 컬럼명 (미지정 시 자동 탐색)
        full_holdout_ratio: mode='full' 일 때 홀드아웃 비율
        holdout_mode: 'full' (100% 단일 순수 OOS 검증) | 'split' (고정 거래일 2단계 분할)
        final_test_td: holdout_mode='split' 일 때 Final Test 고정 거래일수 (기본 3일)
        require_contiguous: 비정상 결측 Gap 배제 및 연속 유효 구간 기준 분할 여부 (False: 결측일 건너뛰고 전체 통합)

    Returns:
        경계·창 크기·표본 통계·진단·경고 dict. `ok=False` 면 사유(`reason`) 포함.
    """
    scan = scan_daily_bars(df, ts_col=ts_col, vol_col=vol_col, filter_no_trade=True)
    if scan.empty:
        return {"ok": False, "mode": SPLIT_MODE_UNAVAILABLE, "reason": "데이터가 비어 있거나 유효한 매매일이 없습니다."}

    excluded_no_trade_days = scan.attrs.get("excluded_no_trade_days", [])
    raw_date_objs = list(scan.index)

    # ── 비정상 결측 Gap 감지 및 연속 유효 구간 선택 ──
    segments = find_contiguous_trading_segments(raw_date_objs, market=market)
    selected_segment = raw_date_objs
    segment_warning = None

    if require_contiguous and len(segments) > 1:
        best_seg = segments[-1]
        for seg in reversed(segments):
            if len(seg) >= 20:  # 최소 유의미한 WFA/홀드아웃 구간
                best_seg = seg
                break
        if len(best_seg) != len(raw_date_objs):
            selected_segment = best_seg
            dropped_count = len(raw_date_objs) - len(best_seg)
            segment_warning = (
                f"⚠️ 데이터 내 결측 Gap 감지 — 최신 연속 유효 구간({best_seg[0]} ~ {best_seg[-1]}, "
                f"{len(best_seg)}거래일)에 자동 고정 분할 적용 (고립 파편 {dropped_count}일 제외)"
            )
    elif not require_contiguous and len(segments) > 1:
        segment_warning = (
            f"⚡ 비연속 유효일 통합 모드: 결측 Gap({len(segments)-1}개 구간)을 건너뛰고 "
            f"총 {len(raw_date_objs)}거래일 전체를 WFA 학습에 활용합니다."
        )

    date_objs = selected_segment
    dates = [str(d) for d in date_objs]
    bars = [int(scan.loc[d]) for d in date_objs]
    n_td = len(dates)
    bars_per_day = float(sum(bars)) / max(1, n_td)

    if n_td < 2:
        return {"ok": False, "mode": SPLIT_MODE_UNAVAILABLE,
                "reason": f"연속 유효 거래일이 {n_td}일뿐입니다 (최소 2일 필요)."}

    # ── ① 창 크기 결정 (3-Tier) ──
    if str(mode).lower() == "full":
        holdout_td = max(1, min(n_td - 1, int(round(n_td * float(full_holdout_ratio)))))
        wfa_td = max(1, n_td - holdout_td)

        std = get_standard_split(interval, market=market)
        bucket = std.get("bucket", "1m")
        mins = get_minimums_for_bucket(bucket, market=market)

        std_win = int(std["train_td"]) + int(std["opt_td"]) + int(std["test_td"])
        min_win = int(mins["train_td"]) + int(mins["opt_td"]) + int(mins["test_td"])

        if wfa_td >= std_win:
            train_td = int(std["train_td"])
            opt_td = int(std["opt_td"])
            test_td = int(std["test_td"])
            std_met = True
        elif wfa_td >= min_win:
            train_td = int(mins["train_td"])
            opt_td = int(mins["opt_td"])
            test_td = int(mins["test_td"])
            std_met = False
        else:
            test_td = 1
            opt_td = 1 if wfa_td > 2 else 0
            train_td = max(1, wfa_td - opt_td - test_td)
            std_met = False

        exp_windows = expected_wfa_windows(n_td, holdout_td, train_td, opt_td, test_td)

        split_meta: Dict[str, Any] = {
            "mode": SPLIT_MODE_FULL,
            "standard_met": std_met,
            "scale": 1.0,
            "bucket": bucket,
            "label": f"전체 80:20 분할 [{std.get('label', bucket)}]",
            "note": "전체 데이터셋 기준 80% WFA + 20% 홀드아웃 (분봉 표준 윈도우)",
            "lookback_td": n_td,
            "train_td": train_td,
            "opt_td": opt_td,
            "test_td": test_td,
            "holdout_td": holdout_td,
            "expected_wfa_windows": exp_windows,
            "deviations": {},
            "reason": f"전체 80:20 분할 — WFA {wfa_td}거래일 (창크기 {train_td}/{opt_td}/{test_td}TD) / 홀드아웃 {holdout_td}거래일",
        }
    else:
        split_meta = compute_scaled_split(interval, n_td, market=market)
        if split_meta.get("mode") == SPLIT_MODE_UNAVAILABLE:
            return {"ok": False, **split_meta,
                    "data": {"first": dates[0], "last": dates[-1], "trading_days": n_td}}
        holdout_td = min(int(split_meta["holdout_td"]), n_td - 1)

    # ── ② 홀드아웃 배치 (최근 데이터부터 역순) ──
    holdout_end_idx = n_td - 1
    holdout_start_idx = n_td - holdout_td
    wfa_end_idx = holdout_start_idx - 1
    if wfa_end_idx < 0:
        return {"ok": False, "mode": SPLIT_MODE_UNAVAILABLE,
                "reason": "WFA 구간이 확보되지 않습니다 (홀드아웃이 전체를 소비)."}

    # ── ③ Purge: WFA 종료 ↔ 홀드아웃 시작 사이 bar 간격 ≥ purge_candles (AFML Ch.7) ──
    purge_days_dropped = 0
    purge_bars_gap = 0
    try:
        purge_n = int(purge_candles) if purge_candles else 0
    except Exception:
        purge_n = 0
    if purge_n > 0:
        idx = wfa_end_idx
        while purge_bars_gap < purge_n and idx >= 0:
            purge_bars_gap += bars[idx]
            idx -= 1
            purge_days_dropped += 1
        wfa_end_idx = max(0, wfa_end_idx - purge_days_dropped)

    # ── ④ WFA 시작 (lookback − 홀드아웃 만큼 확보) ──
    wfa_td_target = max(1, int(split_meta.get("lookback_td", n_td)) - holdout_td)
    wfa_start_idx = max(0, wfa_end_idx - wfa_td_target + 1)

    wfa_td_actual = wfa_end_idx - wfa_start_idx + 1
    holdout_td_actual = holdout_end_idx - holdout_start_idx + 1

    # ── ⑤ 표본 통계 ──
    holdout_bars = sum(bars[holdout_start_idx:holdout_end_idx + 1])
    wfa_bars = sum(bars[wfa_start_idx:wfa_end_idx + 1])

    # ── ⑥ 홀드아웃 검증 모드 (단일 100% OOS vs 2단계 고정거래일 분할) ──
    h_mode = str(holdout_mode).lower().strip()
    if h_mode == HOLDOUT_MODE_SPLIT:
        if final_test_td is not None:
            f_td = int(final_test_td)
        else:
            f_td = int(DEFAULT_FINAL_TEST_TD)
        f_td = max(1, min(f_td, holdout_td_actual - 1)) if holdout_td_actual > 1 else 1
        final_test_start_idx = holdout_end_idx - f_td + 1
        final_test_start = dates[final_test_start_idx]
        final_test_end = dates[holdout_end_idx]
        final_test_td_actual = f_td
        final_test_bars = sum(bars[final_test_start_idx:holdout_end_idx + 1])

        val_start = dates[holdout_start_idx]
        val_end = dates[final_test_start_idx - 1]
        val_td_actual = final_test_start_idx - holdout_start_idx
        val_bars = sum(bars[holdout_start_idx:final_test_start_idx])
    else:
        h_mode = HOLDOUT_MODE_FULL
        final_test_start = dates[holdout_start_idx]
        final_test_end = dates[holdout_end_idx]
        final_test_td_actual = holdout_td_actual
        final_test_bars = holdout_bars
        val_start = dates[holdout_start_idx]
        val_end = dates[holdout_end_idx]
        val_td_actual = holdout_td_actual
        val_bars = holdout_bars

    diagnostics = diagnose_calendar_gaps(date_objs, market)

    warnings: List[str] = []
    if segment_warning:
        warnings.append(segment_warning)
    if excluded_no_trade_days:
        warnings.append(
            f"ℹ️ 매매 없는 날 {len(excluded_no_trade_days)}일 제외 완료: {', '.join(excluded_no_trade_days[:3])}"
        )
    non_standard = split_meta.get("mode") in ("proportional", "minimum")
    if non_standard:
        warnings.append(f"🔶 비표준 분할 — {split_meta.get('reason')}")
    if holdout_td_actual < 5:
        warnings.append(f"⛔ 홀드아웃 {holdout_td_actual}거래일 — 통계적 판정 불가 수준")
    if diagnostics["mismatched_holidays"]:
        warnings.append(
            f"⚠️ 휴일 테이블 오분류 {len(diagnostics['mismatched_holidays'])}건 — "
            f"데이터에 bar 가 존재하므로 거래일로 처리: {diagnostics['mismatched_holidays'][:5]}"
        )
    if diagnostics["missing_trading_days"]:
        warnings.append(
            f"⚠️ 수집 누락 의심 {len(diagnostics['missing_trading_days'])}건: "
            f"{diagnostics['missing_trading_days'][:5]}"
        )

    calendar_days = (date_objs[-1] - date_objs[0]).days + 1

    # 월 환산 (UI 표시용) — 시장별 거래일 밀도로 변환 (SSOT: split_standards)
    tdpm = trading_days_per_month(market)
    _td_keys = {
        "train_months": int(split_meta.get("train_td", 0)),
        "opt_months": int(split_meta.get("opt_td", 0)),
        "test_months": int(split_meta.get("test_td", 0)),
        "holdout_months": int(holdout_td),
        "final_test_months": int(final_test_td_actual),
    }
    month_map = {k: round(v / tdpm, 2) for k, v in _td_keys.items()}

    # 단위 체계 (UI 동적 바인딩 SSOT: 거래일 TD, 캔들 Bars, 개월 Months)
    units = {
        "td": {
            "train": int(split_meta.get("train_td", 0)),
            "opt": int(split_meta.get("opt_td", 0)),
            "test": int(split_meta.get("test_td", 0)),
            "wfa": int(wfa_td_actual),
            "holdout": int(holdout_td_actual),
            "final_test": int(final_test_td_actual),
            "val": int(val_td_actual),
        },
        "bars": {
            "train": int(round(int(split_meta.get("train_td", 0)) * bars_per_day)),
            "opt": int(round(int(split_meta.get("opt_td", 0)) * bars_per_day)),
            "test": int(round(int(split_meta.get("test_td", 0)) * bars_per_day)),
            "wfa": int(wfa_bars),
            "holdout": int(holdout_bars),
            "final_test": int(final_test_bars),
            "val": int(val_bars),
        },
        "months": {
            "train": round(int(split_meta.get("train_td", 0)) / tdpm, 2),
            "opt": round(int(split_meta.get("opt_td", 0)) / tdpm, 2),
            "test": round(int(split_meta.get("test_td", 0)) / tdpm, 2),
            "wfa": round(int(wfa_td_actual) / tdpm, 2),
            "holdout": round(int(holdout_td_actual) / tdpm, 2),
            "final_test": round(int(final_test_td_actual) / tdpm, 2),
            "val": round(int(val_td_actual) / tdpm, 2),
        },
    }

    return {
        "ok": True,
        "mode": split_meta.get("mode"),
        "standard_met": bool(split_meta.get("standard_met", False)),
        "non_standard": bool(non_standard),
        "scale": split_meta.get("scale", 1.0),
        "bucket": split_meta.get("bucket"),
        "label": split_meta.get("label"),
        "note": split_meta.get("note"),
        "reason": split_meta.get("reason"),
        # 경계 — 실재 bar 날짜 (휴장일 및 무거래일 배제 완료)
        "wfa_start": dates[wfa_start_idx],
        "wfa_end": dates[wfa_end_idx],
        "holdout_start": dates[holdout_start_idx],
        "holdout_end": dates[holdout_end_idx],
        # 홀드아웃 검증 모드 & 분할 경계
        "holdout_mode": h_mode,
        "final_test_start": final_test_start,
        "final_test_end": final_test_end,
        "final_test_td": int(final_test_td_actual),
        "final_test_bars": int(final_test_bars),
        "val_start": val_start,
        "val_end": val_end,
        "val_td": int(val_td_actual),
        "val_bars": int(val_bars),
        # 창 크기 (거래일)
        "lookback_td": int(split_meta.get("lookback_td", n_td)),
        "train_td": int(split_meta.get("train_td", 0)),
        "opt_td": int(split_meta.get("opt_td", 0)),
        "test_td": int(split_meta.get("test_td", 0)),
        "holdout_td": int(holdout_td),
        "wfa_td_actual": int(wfa_td_actual),
        "holdout_td_actual": int(holdout_td_actual),
        "expected_wfa_windows": int(split_meta.get("expected_wfa_windows", 0)),
        "standard": split_meta.get("standard"),
        "deviations": split_meta.get("deviations", {}),
        # 월 환산 (UI 표시용 — 시장별 거래일 밀도 기준)
        "trading_days_per_month": tdpm,
        **month_map,
        # 표본 통계 및 단위 SSOT
        "units": units,
        "holdout_bars": int(holdout_bars),
        "wfa_bars": int(wfa_bars),
        "bars_per_day": round(bars_per_day, 1),
        "min_holdout_trades": MIN_HOLDOUT_TRADES,
        # Purge (AFML Ch.7)
        "purge_candles": purge_n,
        "purge_bars_gap": int(purge_bars_gap),
        "purge_days_dropped": int(purge_days_dropped),
        # 데이터 요약 / 캘린더 진단 / 결측 처리 내역
        "excluded_no_trade_days": excluded_no_trade_days,
        "contiguous_segments_count": len(segments),
        "data": {
            "first": dates[0], "last": dates[-1],
            "trading_days": n_td, "calendar_days": int(calendar_days),
            "bars_total": int(sum(bars)),
            "bars_per_day_min": int(min(bars)), "bars_per_day_max": int(max(bars)),
            "market": str(market),
        },
        "diagnostics": diagnostics,
        "warnings": warnings,
    }


def _to_sorted_ts(timestamps) -> pd.Series:
    """timestamp 입력을 정렬된 datetime Series 로 정규화 (공용)."""
    try:
        ts = pd.to_datetime(pd.Series(list(timestamps)), errors="coerce").dropna()
    except Exception:
        return pd.Series(dtype="datetime64[ns]")
    if ts.empty:
        return ts
    return ts.sort_values().reset_index(drop=True)


def split_point_by_ratio(timestamps, ratio: float = 0.8):
    """**거래일 기준** 분할 지점을 반환한다 (앞 구간이 `ratio`).

    종전에는 세 곳이 각자 비율 분할을 계산했고, 모두 **거래일이 아닌 기준**이었다.
      · 웹 대시보드 타임라인 : `int(len(df) * 0.8)`        ← **bar 인덱스**
      · UI 일반 2구간 프리셋  : `start + totalMs * 0.8`     ← **달력 밀리초**
      · 학습엔진 비WFA 폴백   : `iloc[int(len(master_df) * 0.6)]` ← **bar 인덱스 60%**
    결측일/비균일 데이터에서는 bar 인덱스와 거래일 인덱스가 어긋나 경계가 왜곡된다.

    Args:
        timestamps: timestamp (Series/리스트)
        ratio: 앞 구간 비율 (0 < ratio < 1)

    Returns:
        분할일의 **첫 bar 시각** (pd.Timestamp) 또는 None(데이터 부족)
    """
    ts = _to_sorted_ts(timestamps)
    if ts.empty:
        return None
    day_first = [pd.Timestamp(v) for v in ts.groupby(ts.dt.normalize()).first().values]
    n = len(day_first)
    if n < 2:
        return None
    try:
        r = float(ratio)
    except Exception:
        r = 0.8
    idx = min(max(0, int(round(n * r))), n - 1)
    return day_first[idx]


def build_wfa_windows(timestamps, train_td: int, opt_td: int, test_td: int,
                      interval_minutes: Optional[float] = None,
                      max_windows: int = 500) -> List[Dict[str, Any]]:
    """**거래일 기준** Walk-Forward Analysis 창을 생성한다 (R-1).

    📌 종전 결함
      `model_engine` 과 웹 대시보드가 각각 `train_days = train_months × 30.44` 의
      **달력일 산술**로 창 경계를 만들었다. 그 결과 경계가 휴장일에 떨어졌다.
        실측 (069500/10s, WFA 시작 2026-07-21, Train/Opt/Test = 0.95/0.14/0.14개월):
          Train 종료 2026-08-18 (화) ✓
          Opt   종료 2026-08-23 (**일**) ✗ ← 경계가 휴장일 → 다음 거래일로 밀려 창이 왜곡
          Test  종료 2026-08-27 (목) ✓
      또한 같은 로직이 3곳(`model_engine`, `_build_wfa_windows`, `_calc_wfa_shift_start`)에
      중복 구현되어 있어 서로 다른 창을 계산할 수 있었다 (D-1 류 이중구현).

    📌 설계
      · 거래일 목록은 **실제 bar 가 존재하는 날** (데이터 우선, 휴일 테이블 미사용).
      · 모든 경계는 해당 거래일의 **첫 bar 시각** 으로 스냅된다.
      · 경계 규약: `*_end` 는 exclusive (그 날은 다음 구간에 속함).
        마지막 창의 `test_end` 는 `last_ts + 1bar` 로 두어 **마지막 bar 를 포함**한다.
      · Purge 는 호출자(=학습 엔진)가 `purge_candles × interval` 로 별도 적용한다 (AFML Ch.7).

    Args:
        timestamps: 마스터 데이터프레임의 timestamp (Series/리스트)
        train_td / opt_td / test_td: 각 구간 길이 (거래일)
        interval_minutes: 봉 길이(분) — 마지막 bar 포함용 경계 산정. 미지정 시 1분
        max_windows: 안전 상한

    Returns:
        [{"train_start","train_end","opt_start","opt_end","test_start","test_end"} …] (pd.Timestamp)
    """
    ts = _to_sorted_ts(timestamps)
    if ts.empty:
        return []

    # 거래일별 '첫 bar 시각' (데이터 우선 — 실제 bar 가 있는 날만 거래일)
    day_first_map = ts.groupby(ts.dt.normalize()).first()
    day_first = [pd.Timestamp(v) for v in day_first_map.values]
    n = len(day_first)

    t_train = max(1, int(train_td))
    t_opt = max(0, int(opt_td))
    t_test = max(1, int(test_td))
    if n < (t_train + t_opt + t_test):
        return []

    # 마지막 bar 포함용 step (interval 미지정 시 1분)
    step = pd.Timedelta(minutes=float(interval_minutes)) if interval_minutes else pd.Timedelta(minutes=1)
    if step <= pd.Timedelta(0):
        step = pd.Timedelta(minutes=1)
    last_ts = ts.iloc[-1]

    def _boundary(i: int):
        """거래일 인덱스 i 의 시작 시각. i >= n 이면 '데이터 끝 + 1bar' (포함 경계)."""
        if i < n:
            return day_first[i]
        return last_ts + step

    windows: List[Dict[str, Any]] = []
    start = 0
    while True:
        e_train = start + t_train
        e_opt = e_train + t_opt
        e_test = e_opt + t_test

        # 기존 규약 유지: Train/Opt 구간이 데이터를 초과하면 창을 만들지 않는다
        if e_train >= n or e_opt >= n:
            break
        e_test = min(e_test, n)

        # [R-1 개선] Test 구간이 비어 있거나 시작/종료가 같은 날짜(당일 0일 OOS)인 왜곡 창은 생성하지 않는다
        if e_test <= e_opt or pd.Timestamp(_boundary(e_test)).date() <= pd.Timestamp(_boundary(e_opt)).date():
            break

        windows.append({
            "train_start": _boundary(start),
            "train_end": _boundary(e_train),
            "opt_start": _boundary(e_train),
            "opt_end": _boundary(e_opt),
            "test_start": _boundary(e_opt),
            "test_end": _boundary(e_test),
        })

        start += t_test
        if start + t_train + t_opt > n:
            break
        if len(windows) >= int(max_windows):
            break

    return windows


def summarize_split(res: Dict[str, Any]) -> str:
    """분할 결과를 사람이 읽는 2줄 요약으로 변환 (로그/토스트/UI 공용)."""
    if not res or not res.get("ok"):
        return f"⛔ 분할 불가: {(res or {}).get('reason', '알 수 없음')}"

    badge = {
        "standard": "✅ 표준",
        "proportional": "🔶 비표준(비율)",
        "minimum": "🔶 비표준(최소)",
        "full": "🌐 수동 80:20",
    }.get(str(res.get("mode")), str(res.get("mode")))

    line1 = (
        f"{badge} [{res.get('label')}] "
        f"WFA {res.get('wfa_start')}~{res.get('wfa_end')} "
        f"({res.get('wfa_td_actual')}거래일 / {res.get('wfa_bars'):,}bars) "
        f"| 홀드아웃 {res.get('holdout_start')}~{res.get('holdout_end')} "
        f"({res.get('holdout_td_actual')}거래일 / {res.get('holdout_bars'):,}bars)"
    )
    line2 = (
        f"     창크기 Train {res.get('train_td')}/Opt {res.get('opt_td')}/Test {res.get('test_td')} 거래일 "
        f"| 예상 창수 {res.get('expected_wfa_windows')}개 | 일평균 {res.get('bars_per_day')}bars"
    )
    if res.get("mode") not in ("standard", "full"):
        std_lookback = (res.get("standard") or {}).get("lookback_td")
        line2 += f"  | 축소비율 {res.get('scale')} (표준 {std_lookback}거래일)"
    return line1 + "\n" + line2
