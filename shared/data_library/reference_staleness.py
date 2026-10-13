# -*- coding: utf-8 -*-
"""
CTradeSystem - Reference Staleness Policy (참조/매크로 stale 전파 상한 정책)
============================================================================
[T-3 / D-1] 배경
----------------
`merge_asof(direction='backward')` 는 시간 정합성(미래참조 차단)을 보장하지만,
직후의 `ffill()` 이 tolerance 초과로 생긴 NaN 을 다시 앞값으로 채워 **tolerance 를 무력화**한다.
그 결과 참조 원천 데이터가 비어 있는 구간에서 **수십 일 전 값이 현재값처럼 학습**된다.

실측 (scripts/audit_reference_staleness.py, 2026-09-15)
-------------------------------------------------------
  069500/1m   ref_449190_close  최장 624.0시간(26.0일) 연속 동일값, >7일 구간 3.28%
              ref_hynix_close   최장 312.0시간(13.0일),             >7일 구간 3.24%
  069500/10s  ref_hynix_close   최장 221.2시간( 9.2일),             >7일 구간 20.51%

  ※ 원인은 "정상 휴장"이 아니라 **구간 공백**(참조 원천이 타겟보다 늦게 시작/일찍 종료)이며,
    ffill 이 그 공백을 조용히 메워 **결측을 은폐**했다.

정책
----
참조값을 ffill 로 전파하되, **참조 원천 타임스탬프 기준 경과시간(age)** 이 cap 을 넘으면 무효화한다.
무효값은 가격류 `NaN`, 거래량류 `0.0` 으로 남겨 모델이 '미지'로 인식하도록 한다.

cap 은 자산군별로 다르다 — **정상 휴장(주말/명절)은 허용**해야 하기 때문이다.
  · CRYPTO(24/7)            : 1일   (실측 최장 정상 갭 6.3시간)
  · GLOBAL_FUTURES(분/시봉) : 4일   (주말 실측 2.7일 + 공휴일 여유)
  · KRX(주식/ETF)           : 7일   (최장 명절 연휴 실측 5.0일 + 여유)
  · 일봉 계열(1d/1w)        : 7일   (1봉=1일 + 주말/연휴)

⚠️ 본 정책은 **동작 변경**이다. 다음 데이터셋 재빌드 시 참조/매크로 컬럼 값이 달라지므로
   모델 재학습 및 백테스트 재검증이 필요하다. `scripts/freeze_golden_baseline.py` 가
   사전/사후 비교 수단을 제공한다.
"""

from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

_SRC_TS_COL = "__ref_src_ts"

# Normalized 표준 필드의 시간 의미 — 결합 시 빈 행을 어떻게 다룰지가 여기서 갈린다 (2026-10-02).
#   상태값(가격·미결제약정·펀딩비·누적 CVD 등, 아래 둘에 없는 전부): 직전 관측을 stale 상한까지 이어 쓴다.
#   흐름값: 그 봉 동안 쌓인 양 — 처음 알려진 행에 한 번만, 원천이 살아 있는 나머지 행은 0, 원천 밖은 NaN.
#   봉 내부 비율값: 그 봉의 체결로만 정의 — 처음 알려진 행에만, 나머지는 NaN.
NORMALIZED_FLOW_FIELDS = frozenset({
    "volume", "trade_amount", "trade_count", "bid_volume", "ask_volume", "bid_vol", "ask_vol", "is_large_trade",
})
NORMALIZED_BAR_FIELDS = frozenset({"ofi", "rv"})

# 자산군별 stale 상한 (일). 정상 휴장은 허용하되 결측 은폐는 차단하는 값.
DEFAULT_STALE_CAP_DAYS: Dict[str, float] = {
    "CRYPTO": 1.0,
    "FX": 3.0,
    "GLOBAL_FUTURES": 4.0,
    "GLOBAL_FUTURES_DAILY": 7.0,
    "KRX": 7.0,
    "KRX_STOCK": 7.0,
    "KRX_ETF": 7.0,
    "US_ETF": 7.0,
    "US_STOCK": 7.0,
}
_FALLBACK_CAP_DAYS = 7.0


def _is_daily_like(interval: Optional[str]) -> bool:
    """일봉/주봉 등 1봉 길이가 하루 이상인 인터벌 판정"""
    clean = str(interval or "").strip().lower()
    if not clean:
        return False
    if clean.endswith("d") and not clean.endswith("ms"):
        return True
    return clean.endswith("w") or clean.endswith("mo") or clean.endswith("month")


def resolve_stale_cap(market_type: Optional[str] = None,
                      interval: Optional[str] = None,
                      override: Optional[Union[str, pd.Timedelta]] = None) -> pd.Timedelta:
    """자산군/인터벌로부터 stale 상한을 결정한다.

    Args:
        market_type: 종목 레지스트리의 market_type (예: "KRX_ETF", "GLOBAL_FUTURES", "CRYPTO")
        interval: 참조 캔들 인터벌 (일봉 계열이면 상한을 늘린다)
        override: 명시적 상한 (문자열은 pd.Timedelta 로 해석) — 최우선

    Returns:
        stale 상한 Timedelta
    """
    if override is not None:
        return override if isinstance(override, pd.Timedelta) else pd.Timedelta(override)

    if _is_daily_like(interval):
        return pd.Timedelta(days=DEFAULT_STALE_CAP_DAYS["GLOBAL_FUTURES_DAILY"])

    key = str(market_type or "").strip().upper()
    days = DEFAULT_STALE_CAP_DAYS.get(key)
    if days is None:
        for cand_key in DEFAULT_STALE_CAP_DAYS:
            if key.startswith(cand_key):
                days = DEFAULT_STALE_CAP_DAYS[cand_key]
                break
    if days is None:
        days = _FALLBACK_CAP_DAYS
    return pd.Timedelta(days=days)


def apply_stale_cap(target_ts: pd.Series,
                    matched_ts: pd.Series,
                    values: pd.Series,
                    cap: pd.Timedelta,
                    fill_value: Any = np.nan) -> pd.Series:
    """ffill 전파된 참조값 중 stale 상한을 초과한 구간을 무효화한다.

    Args:
        target_ts: 타겟(학습) 시계열 타임스탬프
        matched_ts: tolerance 없이 결합한 **참조 원천 타임스탬프** (봉 시작→확정 시프트 반영 후)
        values: 이미 ffill 이 적용된 참조값 시리즈
        cap: stale 상한
        fill_value: 무효 구간 대체값 (가격류 NaN, 거래량류 0.0)

    Returns:
        stale 구간이 무효화된 시리즈 (인덱스는 0..n-1 로 정규화)
    """
    ts = pd.to_datetime(pd.Series(target_ts).reset_index(drop=True))
    src = pd.to_datetime(pd.Series(matched_ts).reset_index(drop=True))
    out = pd.Series(values).reset_index(drop=True).copy()

    age = ts - src
    # 참조 원천 자체가 없는 구간(NaT)도 무효로 간주한다.
    stale = age.isna() | (age > cap)

    if out.dtype.kind in "fiub":
        out = out.astype(float)
    out[stale.values] = fill_value
    return out


def merge_asof_with_stale_cap(
    target_df: pd.DataFrame,
    ref_df: pd.DataFrame,
    value_cols: List[str],
    cap: pd.Timedelta,
    fill_values: Optional[Dict[str, Any]] = None,
    ts_col: str = "timestamp",
    flow_cols: Optional[List[str]] = None,
    bar_cols: Optional[List[str]] = None,
) -> pd.DataFrame:
    """참조 캔들을 타겟에 backward 결합하고, ffill 전파에 stale 상한을 적용한다.

    단일 구현 원칙(T-3 / T-4b): 배치 빌더와 온디맨드 조립기가 **동일 함수**를 사용한다.

    설계 노트
    ---------
    · `merge_asof(tolerance=N)` + `ffill()` 조합은 tolerance 를 무력화하므로(D-1),
      본 함수는 **tolerance 를 쓰지 않고** 대신 `cap` 을 유일한 유효 상한으로 사용한다.
      (tolerance 를 걸어 생긴 NaN 을 ffill 이 되메우기 때문에 결과는 동일하다)
    · 참조 원천 타임스탬프를 값 컬럼으로 **복제해 함께 결합**하여 경과시간(age)을 정확히 산출한다.
      (merge_asof 는 `on` 키를 공유하므로 같은 이름의 컬럼을 따로 만들지 않는다)
    · 결합은 항상 `direction="backward"` → 미래참조 원천 차단.

    Args:
        target_df: 타겟 데이터프레임 (ts_col 필수)
        ref_df: 참조 데이터프레임 (ts_col + value_cols). 봉 시작 시각 시프트는 호출자가 적용한다.
        value_cols: 결합할 참조 컬럼 목록
        cap: stale 상한 (이 시간을 넘겨 전파된 값은 무효화)
        fill_values: 무효 구간 대체값 (컬럼명 → 값). 기본 NaN
        ts_col: 타임스탬프 컬럼명
        flow_cols: 흐름값(거래량·거래대금 등 '그 봉 동안 쌓인 양') 컬럼. 상태값(가격·미결제약정)과 달리
                   이어 쓰면 같은 봉의 양이 여러 행에 복제된다. 원천 봉 하나의 값은 그 봉이 처음 알려진
                   행에 한 번만 싣고, 원천이 살아 있는(stale 상한 이내) 나머지 행은 0(새 체결 없음),
                   원천이 없거나 상한을 넘은 행은 fill_values(기본 NaN — '모름')로 둔다.
        bar_cols: 봉 내부 비율값(ofi 등 — 그 봉의 체결로만 정의되는 값). 처음 알려진 행에만 싣고
                  나머지 행은 NaN — 새 체결이 없으면 0 이 아니라 '정의되지 않음' 이다.

    Returns:
        target_df + value_cols (stale 상한 적용 완료), ts_col 기준 정렬
    """
    fill_values = fill_values or {}
    if target_df is None or target_df.empty or ref_df is None or ref_df.empty:
        return target_df

    missing = [c for c in value_cols if c not in ref_df.columns]
    if missing:
        raise ValueError(f"ref_df 에 결합 대상 컬럼이 없습니다: {missing}")

    t = target_df.sort_values(ts_col).copy()
    src = (
        ref_df[[ts_col] + value_cols]
        .drop_duplicates(subset=[ts_col])
        .sort_values(ts_col)
        .copy()
    )
    # 참조 원천 타임스탬프를 값 컬럼으로 복제 (age 산출용)
    src[_SRC_TS_COL] = src[ts_col]

    # 값 + 참조 타임스탬프를 한 번에 backward 결합 (미래참조 차단)
    merged = pd.merge_asof(
        t, src[[ts_col, _SRC_TS_COL] + value_cols], on=ts_col, direction="backward"
    )

    # 같은 원천 봉이 처음 알려진 행 — 흐름값은 여기에만 싣는다
    _flow = set(flow_cols or [])
    _bar = set(bar_cols or [])
    first_seen = merged[_SRC_TS_COL].notna() & merged[_SRC_TS_COL].ne(merged[_SRC_TS_COL].shift())

    # ffill 전파 후 stale 상한 적용
    for col in value_cols:
        if col in _flow:
            vals = pd.to_numeric(merged[col], errors="coerce")
            merged[col] = apply_stale_cap(
                merged[ts_col], merged[_SRC_TS_COL], vals.where(first_seen, 0.0), cap,
                fill_value=fill_values.get(col, np.nan),
            )
            continue
        if col in _bar:
            vals = pd.to_numeric(merged[col], errors="coerce").where(first_seen)
            merged[col] = apply_stale_cap(merged[ts_col], merged[_SRC_TS_COL], vals, cap, fill_value=np.nan)
            continue
        filled = merged[col].ffill()
        merged[col] = apply_stale_cap(
            merged[ts_col], merged[_SRC_TS_COL], filled, cap,
            fill_value=fill_values.get(col, np.nan),
        )

    return merged.drop(columns=[_SRC_TS_COL])


# ==============================================================================
# [T-6] 참조 결합 단일 진입점 — 정렬(타임스탬프 규약 보정)을 이 모듈이 소유한다
# ==============================================================================
# 2026-09-17 사고의 구조적 원인: 이 모듈이 stale 상한만 책임지고 "봉 시작 시각 시프트는
# 호출자가 적용한다"고 계약했다. 그 결과 조립기 포크가 생겼을 때 새 포크가 정렬을 빠뜨렸고,
# 방어 코드도 테스트도 옛 포크만 보고 있어 11일간 드러나지 않았다.
#
# 이제 참조 결합은 반드시 `attach_reference` 를 통과한다. 호출자는 규약을 알 필요도, 알 수도 없다.

def _as_ns(series: "pd.Series") -> "pd.Series":
    """타임스탬프 해상도를 ns 로 통일한다 (tz 정보는 보존).

    원천마다 datetime64[us]/[ns] 가 섞여 있어 그대로 merge_asof 에 넣으면
    "incompatible merge keys" 로 결합 자체가 실패한다.
    """
    s = pd.to_datetime(series)
    try:
        return s.dt.as_unit("ns")
    except (AttributeError, ValueError):
        return s


def attach_reference(
    base_df: "pd.DataFrame",
    ref_df: "pd.DataFrame",
    ref_symbol: str,
    ref_interval: str,
    target_symbol: str,
    target_interval: str,
    value_cols: Optional[List[str]] = None,
    cap: Optional[pd.Timedelta] = None,
    fill_values: Optional[Dict[str, Any]] = None,
    ts_col: str = "timestamp",
    flow_cols: Optional[List[str]] = None,
    bar_cols: Optional[List[str]] = None,
) -> "pd.DataFrame":
    """참조 캔들을 타겟 시계열에 결합한다. **모든 참조 결합의 유일한 진입점.**

    1. 타임스탬프 규약 보정: 원장이 선언한 규약으로 '의사결정 시점' 기준 결합 키를 만든다.
       (판정은 `shared.config.market_policies.reference_merge_shift` 한 곳에서만 한다)
    2. backward 결합: 그 시점까지 확정된 값만 고른다.
    3. stale 상한(`cap` 이 주어지면): 원천 공백을 ffill 이 조용히 메우지 못하게 한다.

    Args:
        value_cols: 결합할 참조 컬럼. None 이면 ``ts_col`` 을 뺀 전 컬럼.
        cap: stale 상한. None 이면 상한 없이 backward 결합만 한다(기존 조립기 동작 유지).
        flow_cols / bar_cols: 흐름값·봉 내부 비율값 컬럼 (merge_asof_with_stale_cap 참조). cap 이 있을 때만 적용된다.

    Returns:
        base_df + 참조 컬럼 (컬럼 이름 변경은 하지 않는다 — 이름 조립은 호출자 책임)
    """
    from shared.config.market_policies import reference_merge_shift

    if not str(target_symbol or "").strip() or not str(target_interval or "").strip():
        raise ValueError(
            "참조 결합에는 타겟의 신원(target_symbol, target_interval)이 필요합니다 — "
            "정렬 보정은 참조와 타겟의 봉 라벨 규약을 모두 알아야 계산됩니다."
        )
    if base_df is None or base_df.empty or ref_df is None or ref_df.empty:
        return base_df

    cols = list(value_cols) if value_cols is not None else [c for c in ref_df.columns if c != ts_col]

    # 이름 충돌은 fail-closed. merge 는 충돌 컬럼을 조용히 `_x`/`_y` 로 바꾸므로, 다음 참조가 같은 이름을
    # 들고 오면 그것이 **타겟 컬럼 자리를 차지한다**.
    # (2026-10-01 사고: 참조의 target_* 컬럼이 결합에 섞여 들어와 069500 10초봉 스냅샷의 target_close 가
    #  삼성전자 가격으로 바뀌었고, 원래 타겟은 target_close_x 로 밀려났다)
    clash = [c for c in cols if c in base_df.columns]
    if clash:
        raise ValueError(
            f"참조 {ref_symbol}({ref_interval}) 결합 컬럼이 기존 컬럼과 겹칩니다 — "
            f"결합하면 기존 값이 _x/_y 로 밀려납니다: {clash[:10]}"
        )

    src = ref_df[[ts_col] + cols].copy()
    src[ts_col] = _as_ns(src[ts_col])

    shift = reference_merge_shift(ref_symbol, ref_interval, target_symbol, target_interval)
    if shift != pd.Timedelta(0):
        src[ts_col] = src[ts_col] + shift

    src = src.drop_duplicates(subset=[ts_col]).sort_values(ts_col).reset_index(drop=True)

    out = base_df.copy()
    out[ts_col] = _as_ns(out[ts_col])
    out = out.sort_values(ts_col).reset_index(drop=True)

    if cap is None:
        return pd.merge_asof(out, src, on=ts_col, direction="backward")
    return merge_asof_with_stale_cap(out, src, cols, cap=cap, fill_values=fill_values, ts_col=ts_col,
                                     flow_cols=flow_cols, bar_cols=bar_cols)
