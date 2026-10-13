# -*- coding: utf-8 -*-
"""
CTradeSystem - Reference Alignment Audit (참조 계열 타임스탬프 정합 감사)
==========================================================================
[T-6] 배경
----------
참조/매크로 계열을 타겟 시계열에 결합할 때, 원천 캔들의 타임스탬프가 **봉 시작 시각**
관례인지 **봉 확정 시각** 관례인지는 수집 API마다 다르다. 시작 시각 관례를 그대로
`merge_asof(direction='backward')` 로 붙이면, 시각 t 의 행이 **[t, t+1봉] 구간의 종가**를
갖게 되어 미래 정보가 학습 데이터에 유입된다.

`direction='backward'` 는 이 사고를 막지 못한다. backward 결합은 "원천에 이미 존재하는
행 중 과거 것"만 고르는 규칙이라, **그 행 자체가 미래를 담고 있으면 그대로 통과**시킨다.

실측 (2026-09-28, 069500/10s Gold 스냅샷)
------------------------------------------
  ref_us_tech_100_close  과거정합 +0.08 / 미래정합 +0.51  → 1분 미래참조
  ref_10100_close        과거정합 +0.81 / 미래정합 -0.01  → 정상
  ref_000660_close       과거정합 +0.46 / 미래정합 +0.13  → 정상

설계 원칙
---------
1. **심볼·수집처·인터벌을 모른 채 판정한다.** 종목 이름이나 시장 구분에 대한 지식을
   코드에 두면, 신규 종목/신규 수집처가 추가될 때마다 같은 사고가 재발한다.
   본 감사는 결합 **산출물의 수치**만 본다.
2. **갱신 주기(Δ)도 데이터에서 추론한다.** 상위 인터벌 참조가 하위 인터벌 타겟에
   계단식으로 붙으면 Δ>1 이 된다. Δ 를 상수로 두지 않는다.
3. **판정 임계값은 표본 수에서 유도한다.** 귀무가설 하 Spearman 상관의 표준편차는
   대략 1/sqrt(n-1) 이므로, 잡음 바닥(noise floor)을 표본 수로부터 계산한다.
4. **구간을 나누어 본다.** 정합은 기간에 따라 달라질 수 있다 (수집 경로 교체, 원천 교체,
   과거 구간 백필 등). 전구간 평균만 보면 서로 반대 방향의 결함이 상쇄되어 둘 다 놓친다.
   실제로 2026-09-28 감사에서, 타겟 10초봉의 분내 역순 결함(2026-07-21~08-18)이
   참조의 미래참조를 상쇄해 전구간 판정을 '정상'으로 만든 사례가 있었다.
   따라서 **한 구간이라도 새면 전체를 불합격** 처리한다.

판정 논리
---------
참조 계열이 값을 갱신한 시점 t 만 표본으로 삼는다. 그 시점에 도착한 새 값은
  · 정상 정합이면 구간 **[t-Δ, t]** (이미 실현된 과거)를 요약한 값이고,
  · 미래참조이면 구간 **[t, t+Δ]** (아직 오지 않은 미래)를 요약한 값이다.
따라서 참조 수익률이 타겟의 **미래** 구간 수익률과 **과거** 구간 수익률 중
어느 쪽에 더 강하게 붙는지를 비교하면, 관례를 몰라도 어긋남을 잡아낼 수 있다.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

# 귀무가설 하 상관 표준편차의 몇 배를 잡음 바닥으로 볼 것인가.
# (도메인 상수가 아니라 통계적 신뢰수준이다. 3σ ≈ 99.7%)
DEFAULT_CONFIDENCE_SIGMA = 3.0

# 감사에 필요한 최소 갱신 표본 수. 이보다 적으면 판정하지 않고 '표본 부족'으로 보고한다.
MIN_UPDATE_SAMPLES = 100

# 구간 분할 상한. 구간이 많을수록 국소 결함을 잘 잡지만 구간당 표본이 줄어 잡음이 커진다.
# 실제 분할 수는 표본 수에서 정해지며(구간당 최소 2*MIN_UPDATE_SAMPLES), 이 값은 계산량 상한일 뿐이다.
MAX_AUDIT_WINDOWS = 12


class ReferenceLookaheadError(RuntimeError):
    """참조 계열이 타겟의 미래 구간을 담고 있을 때 발생. 결합 산출물 저장을 막는다."""

    def __init__(self, findings: Sequence["AlignmentFinding"]):
        self.findings = list(findings)
        lines = [f.describe() for f in self.findings]
        super().__init__(
            "참조 계열 타임스탬프 정합 감사 실패 — 미래참조가 감지되어 산출물을 폐기합니다:\n"
            + "\n".join(f"  · {ln}" for ln in lines)
        )


@dataclass(frozen=True)
class AlignmentFinding:
    """참조 컬럼 1개에 대한 정합 감사 결과."""

    column: str
    update_period_bars: int       # 데이터에서 추론한 갱신 주기 Δ (타겟 봉 수)
    n_updates: int                # 갱신 시점 표본 수 (판정 근거 구간)
    past_fit: float               # 참조 수익률 vs 타겟 [t-Δ, t] 수익률 상관
    future_fit: float             # 참조 수익률 vs 타겟 [t, t+Δ] 수익률 상관
    noise_floor: float            # 표본 수에서 유도한 잡음 바닥
    leaked: bool
    window: str = "전구간"        # 판정 근거가 된 구간 설명
    n_windows: int = 1            # 검사한 구간 수
    leaked_windows: int = 0       # 그중 미래참조로 판정된 구간 수
    skipped_reason: Optional[str] = None

    @property
    def margin(self) -> float:
        """미래정합이 과거정합을 얼마나 앞질렀는지 (양수면 미래참조 방향)."""
        return abs(self.future_fit) - abs(self.past_fit)

    def describe(self) -> str:
        if self.skipped_reason:
            head = "감사 불가" if self.leaked else "판정 보류"
            return f"{self.column}: {head} ({self.skipped_reason})"
        verdict = "미래참조" if self.leaked else "정상"
        scope = f"{self.window}"
        if self.n_windows > 1:
            scope += f", 구간 {self.leaked_windows}/{self.n_windows} 오염"
        return (
            f"{self.column}: {verdict} "
            f"(갱신주기 Δ={self.update_period_bars}봉, {scope}, 표본 {self.n_updates:,}개, "
            f"과거정합 {self.past_fit:+.4f} / 미래정합 {self.future_fit:+.4f}, "
            f"잡음바닥 {self.noise_floor:.4f})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "column": self.column,
            "update_period_bars": self.update_period_bars,
            "n_updates": self.n_updates,
            "past_fit": round(self.past_fit, 6),
            "future_fit": round(self.future_fit, 6),
            "noise_floor": round(self.noise_floor, 6),
            "margin": round(self.margin, 6),
            "leaked": self.leaked,
            "window": self.window,
            "n_windows": self.n_windows,
            "leaked_windows": self.leaked_windows,
            "skipped_reason": self.skipped_reason,
        }


def _spearman(a: pd.Series, b: pd.Series, mask: pd.Series) -> float:
    """표본 마스크 위에서의 Spearman 상관. 표본이 모자라거나 분산이 0이면 0.0."""
    m = mask & a.notna() & b.notna() & np.isfinite(a) & np.isfinite(b)
    if int(m.sum()) < MIN_UPDATE_SAMPLES:
        return 0.0
    a_v, b_v = a[m], b[m]
    if a_v.std() == 0 or b_v.std() == 0:
        return 0.0
    v = a_v.corr(b_v, method="spearman")
    return 0.0 if pd.isna(v) else float(v)


def _window_bounds(n_rows: int, n_updates: int) -> List[slice]:
    """구간 분할. 구간당 갱신 표본이 최소 2*MIN_UPDATE_SAMPLES 가 되도록 수를 정한다."""
    if n_updates <= 0:
        return [slice(0, n_rows)]
    n_win = int(min(MAX_AUDIT_WINDOWS, max(1, n_updates // (2 * MIN_UPDATE_SAMPLES))))
    if n_win <= 1:
        return [slice(0, n_rows)]
    edges = np.linspace(0, n_rows, n_win + 1).astype(int)
    return [slice(int(edges[i]), int(edges[i + 1])) for i in range(n_win)]


def _fit_segment(ref_ret: pd.Series, tgt_past: pd.Series, tgt_future: pd.Series,
                 sample: pd.Series, sl: slice, confidence_sigma: float):
    """구간 하나의 (과거정합, 미래정합, 표본수, 잡음바닥, 누출여부)."""
    seg = slice(sl.start, sl.stop)
    r, p, f, s = ref_ret[seg], tgt_past[seg], tgt_future[seg], sample[seg]
    n = int((s & r.notna() & f.notna()).sum())
    if n < MIN_UPDATE_SAMPLES:
        return None
    past_fit = _spearman(r, p, s)
    future_fit = _spearman(r, f, s)
    noise_floor = float(confidence_sigma / np.sqrt(max(n - 1, 1)))
    leaked = abs(future_fit) > abs(past_fit) and abs(future_fit) > noise_floor
    return past_fit, future_fit, n, noise_floor, leaked


def audit_column(ref_series: pd.Series, target_series: pd.Series, column: str,
                 confidence_sigma: float = DEFAULT_CONFIDENCE_SIGMA,
                 labels: Optional[pd.Series] = None) -> AlignmentFinding:
    """참조 컬럼 1개의 타임스탬프 정합을 감사한다 (심볼/수집처 지식 없음).

    전구간과 구간별을 모두 보고, **한 구간이라도 미래참조면 불합격**으로 판정한다.
    서로 반대 방향의 결함이 전구간 평균에서 상쇄되는 경우를 놓치지 않기 위함이다.
    """
    ref = pd.to_numeric(ref_series, errors="coerce").astype(float).reset_index(drop=True)
    tgt = pd.to_numeric(target_series, errors="coerce").astype(float).reset_index(drop=True)
    lab = labels.reset_index(drop=True) if labels is not None else None

    changed = ref.diff().ne(0) & ref.notna()
    update_idx = np.flatnonzero(changed.to_numpy())

    if len(update_idx) < MIN_UPDATE_SAMPLES:
        return AlignmentFinding(column, 0, int(len(update_idx)), 0.0, 0.0, 0.0, False,
                                skipped_reason=f"갱신 표본 {len(update_idx)}개 (최소 {MIN_UPDATE_SAMPLES}개 필요)")

    # 갱신 주기 Δ 를 데이터에서 추론 (상수 없음)
    delta = max(int(np.median(np.diff(update_idx))), 1)

    ref_ret = ref.pct_change(delta)
    tgt_past = tgt.pct_change(delta)                  # [t-Δ, t] — 이미 실현된 구간
    tgt_future = tgt.pct_change(delta).shift(-delta)  # [t, t+Δ] — 아직 오지 않은 구간
    sample = changed & ref_ret.notna()

    # 장 경계를 건너뛰는 비교는 표본에서 뺀다.
    # 그 자리의 '직전 값'은 전날 종가이거나, 참조 데이터가 아직 없어 채워 넣은 값이다.
    # (실측: ref_005930_close 는 2026-08-10 이전이 데이터 미연결이라 하루에 값이 하나뿐이었고,
    #  그 구간의 유일한 '갱신'인 일 경계 점프가 정합 판정을 오염시켜 오탐을 냈다)
    if lab is not None:
        try:
            session = pd.to_datetime(lab).dt.date
            sample = sample & session.eq(session.shift(1))
        except (AttributeError, TypeError, ValueError):
            pass

    whole = _fit_segment(ref_ret, tgt_past, tgt_future, sample, slice(0, len(ref)), confidence_sigma)
    if whole is None:
        n = int((sample & tgt_future.notna()).sum())
        return AlignmentFinding(column, delta, n, 0.0, 0.0, 0.0, False,
                                skipped_reason=f"유효 표본 {n}개 (최소 {MIN_UPDATE_SAMPLES}개 필요)")

    def _label(sl: slice) -> str:
        if lab is None:
            return f"행 {sl.start:,}~{sl.stop:,}"
        try:
            return f"{pd.Timestamp(lab.iloc[sl.start]).date()}~{pd.Timestamp(lab.iloc[sl.stop - 1]).date()}"
        except Exception:
            return f"행 {sl.start:,}~{sl.stop:,}"

    windows = _window_bounds(len(ref), len(update_idx))
    results = []
    for sl in windows:
        r = _fit_segment(ref_ret, tgt_past, tgt_future, sample, sl, confidence_sigma)
        if r is not None:
            results.append((sl, r))

    leaked_windows = [(sl, r) for sl, r in results if r[4]]
    n_windows = len(results)

    if leaked_windows:
        # 가장 심한 구간을 판정 근거로 보고한다
        sl, (past_fit, future_fit, n, noise_floor, _) = max(
            leaked_windows, key=lambda x: abs(x[1][1]) - abs(x[1][0])
        )
        return AlignmentFinding(column, delta, n, past_fit, future_fit, noise_floor, True,
                                window=_label(sl), n_windows=n_windows,
                                leaked_windows=len(leaked_windows))

    past_fit, future_fit, n, noise_floor, leaked = whole
    return AlignmentFinding(column, delta, n, past_fit, future_fit, noise_floor, leaked,
                            window="전구간", n_windows=max(n_windows, 1), leaked_windows=0)


def _resolve_ref_columns(df: pd.DataFrame, ref_columns: Optional[Iterable[str]]) -> List[str]:
    """감사 대상 컬럼 결정. 미지정 시 종목 원장이 알려주는 참조 종가 컬럼을 사용한다
    (컬럼 이름 규칙을 이 모듈이 따로 알지 않기 위함)."""
    if ref_columns is not None:
        return [c for c in ref_columns if c in df.columns]
    from shared.config.config_manager import find_ref_columns
    return [c for c in find_ref_columns(df.columns, field="close") if c in df.columns]


def audit_reference_alignment(
    df: pd.DataFrame,
    target_col: str = "target_close",
    ref_columns: Optional[Iterable[str]] = None,
    confidence_sigma: float = DEFAULT_CONFIDENCE_SIGMA,
    label_col: str = "timestamp",
) -> List[AlignmentFinding]:
    """결합 산출물의 모든 참조 종가 컬럼에 대해 타임스탬프 정합을 감사한다."""
    if df is None or df.empty or target_col not in df.columns:
        return []
    cols = _resolve_ref_columns(df, ref_columns)
    target = df[target_col]
    labels = df[label_col] if label_col in df.columns else None
    return [audit_column(df[c], target, c, confidence_sigma=confidence_sigma, labels=labels)
            for c in cols]


def expected_ref_close_columns(symbols: Iterable[str]) -> List[str]:
    """결합한 종목 목록 → 감사해야 할 참조 종가 컬럼 이름.

    컬럼 이름 규칙은 ``config_manager.ref_prefix`` 한 곳에서만 만들어진다 —
    이 모듈이 이름 규칙을 따로 알지 않기 위함이다.
    """
    from shared.config.config_manager import ref_prefix
    seen, cols = set(), []
    for sym in symbols or []:
        col = f"{ref_prefix(sym)}close"
        if col not in seen:
            seen.add(col)
            cols.append(col)
    return cols


def assert_reference_alignment(
    df: pd.DataFrame,
    target_col: str = "target_close",
    ref_columns: Optional[Iterable[str]] = None,
    confidence_sigma: float = DEFAULT_CONFIDENCE_SIGMA,
    log_fn=None,
    require_all: bool = False,
) -> List[AlignmentFinding]:
    """감사 후 미래참조가 하나라도 있으면 ``ReferenceLookaheadError`` 를 던진다 (fail-closed).

    결합 산출물을 디스크에 쓰기 **전에** 호출해, 오염된 데이터셋이 학습에 도달하지 못하게 한다.

    ``require_all`` 이 참이면 ``ref_columns`` 에 적힌 컬럼이 산출물에 없을 때도 실패시킨다.
    감사 대상이 조용히 0개가 되어 무조건 통과하는 fail-open 을 막기 위한 것으로,
    결합한 종목을 아는 호출자(빌더)는 반드시 켜야 한다.
    """
    if require_all:
        if ref_columns is None:
            raise ValueError("require_all=True 이면 감사 대상 컬럼을 명시해야 합니다")
        missing = [c for c in ref_columns if c not in (df.columns if df is not None else [])]
        if missing:
            raise ReferenceLookaheadError([
                AlignmentFinding(c, 0, 0, 0.0, 0.0, 0.0, True,
                                 skipped_reason="결합 산출물에 참조 종가 컬럼이 없습니다 "
                                                "(결합이 누락됐거나 컬럼 이름 규칙이 어긋났습니다)")
                for c in missing
            ])
    findings = audit_reference_alignment(df, target_col, ref_columns, confidence_sigma)
    if log_fn:
        for f in findings:
            icon = "🚨" if f.leaked else ("⏭️" if f.skipped_reason else "✅")
            log_fn(f"  {icon} [정합 감사] {f.describe()}")
    leaked = [f for f in findings if f.leaked]
    if leaked:
        raise ReferenceLookaheadError(leaked)
    return findings
