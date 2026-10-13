# -*- coding: utf-8 -*-
"""피처 데이터 건강 검사 — "켜 두었지만 실제로는 데이터가 연결되지 않은 피처"를 이름을 보지 않고 찾아낸다.

배경: 피처가 입력 컬럼을 못 찾으면 조용히 상수(0/50)를 돌려준다. 그 결과 후보 피처 절반이 죽어 있어도
학습 로그의 `IC=+0.0000` 으로만 드러났다. 죽은 피처는 어떤 종목/별칭 체계에서도 똑같이 "값이 하나뿐"이라는
같은 증상을 보이므로, 심볼이나 컬럼 이름을 하드코딩하지 않는 범용 검사로 잡는다.
"""
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

# ── 가용성 안정성 게이트 ──
AVAILABILITY_BLOCKS = 8          # 학습 구간을 시간순 등분한 블록 수 (체제 게이트와 같은 해상도)
AVAILABILITY_MAX_SPREAD = 0.20   # 블록별 유효율(non-NaN 비율)의 최대-최소 허용 폭

# ── 커버리지 게이트 (2026-10-02) ──
# 같은 허용폭(20%p)을 쓴다: 학습 구간에서 20% 넘게 비어 있거나, 학습과 최근(홀드아웃/실전) 구간의
# 유효율이 20%p 넘게 다르면 제외한다. 앞의 것은 '그 피처의 데이터가 학습 기간 전체에 존재하는가',
# 뒤의 것은 '학습 때와 실전 때 같은 조건인가'(train/serve parity) 를 묻는다.
COVERAGE_MIN = 1.0 - AVAILABILITY_MAX_SPREAD
COVERAGE_MAX_SHIFT = AVAILABILITY_MAX_SPREAD


def find_dead_features(df: pd.DataFrame, feature_ids: Iterable[str]) -> Dict[str, str]:
    """데이터가 연결되지 않은(사실상 상수이거나 전부 결측인) 피처를 {feature_id: 사유} 로 돌려준다.

    - 컬럼이 없으면 '컬럼 없음', 전부 NaN 이면 '전부 결측', 서로 다른 값이 하나뿐이면 '상수'.
    - 참조 종목 피처(ref_{key}_{kind})는 입력 컬럼(ref_{key}_close)이 스냅샷에 없는지도 함께 알려 준다
      (어떤 데이터를 붙여야 살아나는지가 사유에 나온다). 입력 원장(FEATURE_INPUT_REQUIREMENTS)에 선언된
      지표도 같은 방식으로 빠진 입력을 사유에 붙인다.
    """
    from shared.features.feature_registry import parse_reference_feature_id, find_missing_inputs

    dead: Dict[str, str] = {}
    for fid in feature_ids:
        parsed = parse_reference_feature_id(fid)
        missing_input = (
            f" — 입력 컬럼 ref_{parsed[0]}_close 가 스냅샷에 없음 (참조 종목이 결합되지 않음)"
            if parsed and f"ref_{parsed[0]}_close" not in df.columns else ""
        )
        if not missing_input:
            declared_missing = find_missing_inputs(fid, df)
            if declared_missing:
                missing_input = f" — 필수 입력 없음: {', '.join(declared_missing)}"
        if fid not in df.columns:
            dead[fid] = "컬럼 없음" + missing_input
            continue
        s = df[fid]
        if s.isna().all():
            dead[fid] = "전부 결측" + missing_input
        elif s.nunique(dropna=True) <= 1:
            dead[fid] = "상수(값이 하나뿐)" + missing_input
    return dead


def find_unstable_availability(df: pd.DataFrame, feature_ids: Iterable[str], order_col: str = "timestamp",
                               row_mask: Optional[np.ndarray] = None, n_blocks: int = AVAILABILITY_BLOCKS,
                               max_spread: float = AVAILABILITY_MAX_SPREAD) -> Dict[str, str]:
    """결측이 **시기에 몰려 있는** 피처를 {feature_id: 사유} 로 돌려준다 (원인을 묻지 않는 범용 검사).

    결측 자체는 괜찮다 — LightGBM 은 NaN 을 결측으로 다룬다. 문제는 결측 여부가 시기와 엮일 때다.
    그러면 '값이 있느냐 없느냐' 가 곧 '어느 기간이냐' 를 알려 주는 기간 표식이 되고, 모델은 시장이 아니라
    수집 이력을 배운다. 매일 장 시작 몇 봉이 비는 식의 **구조적 결측은 모든 블록에 똑같이** 나타나므로 통과한다.

    판정: 학습 구간을 시간순으로 n_blocks 등분해(경계는 데이터가 정한다 — 날짜 목록을 코드에 두지 않는다)
    블록별 유효율을 재고, 최대-최소 폭이 max_spread 를 넘으면 제외한다. 블록당 표본이 수천 행이라 표본 잡음은
    1%p 미만이므로, 이 폭은 잡음이 아니라 데이터 가용성 자체의 변화를 뜻한다.
    (2026-10-01: ref_000660_ret_5c 는 학습 구간 앞 57% 에서 전부 NaN(틱 순서 섞임으로 제외된 날)·뒤에서 100%
     유효였다 — 홀드아웃에선 100% 유효라 학습과 실전의 조건이 달랐다)

    row_mask: 판정에 쓸 행(의사결정 시점). 주지 않으면 전체 행.
    """
    work = df if row_mask is None else df.loc[np.asarray(row_mask, dtype=bool)]
    if order_col in work.columns:
        work = work.sort_values(order_col, kind="stable")
    n = len(work)
    out: Dict[str, str] = {}
    if n < n_blocks * 30:
        return out
    block_ids = np.minimum((np.arange(n) * n_blocks) // n, n_blocks - 1)
    for fid in feature_ids:
        if fid not in work.columns:
            continue
        valid = pd.to_numeric(work[fid], errors="coerce").replace([np.inf, -np.inf], np.nan).notna().to_numpy()
        cov = pd.Series(valid).groupby(block_ids).mean().to_numpy()
        spread = float(cov.max() - cov.min())
        if spread > max_spread:
            out[fid] = (f"결측이 시기에 몰림 — 블록별 유효율 {' '.join(f'{c:.0%}' for c in cov)} "
                        f"(폭 {spread:.0%} > {max_spread:.0%}): 값의 유무가 기간 표식이 된다")
    return out



def _coverage(df: pd.DataFrame, fid: str, row_mask=None) -> Optional[float]:
    work = df if row_mask is None else df.loc[np.asarray(row_mask, dtype=bool)]
    if fid not in work.columns or len(work) == 0:
        return None
    v = pd.to_numeric(work[fid], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return float(v.notna().mean())


def find_insufficient_coverage(df: pd.DataFrame, feature_ids: Iterable[str], row_mask: Optional[np.ndarray] = None,
                               recent_df: Optional[pd.DataFrame] = None, recent_mask: Optional[np.ndarray] = None,
                               min_coverage: float = COVERAGE_MIN,
                               max_shift: float = COVERAGE_MAX_SHIFT) -> Dict[str, str]:
    """데이터가 학습 기간을 덮지 못하는 피처를 {feature_id: 사유} 로 돌려준다.

    find_unstable_availability 는 블록 간 유효율 '차이' 만 본다. 그래서 거의 전부 비어 있는 피처는
    블록마다 똑같이 비어 있어 통과한다 (2026-10-02 실측: KRW-BTC 15m binance_oi_change_ratio 는 유효 0.7% —
    수집이 09-25 에 시작돼 2년 학습 구간 대부분이 결측인데, 마지막 블록조차 6% 라 폭이 작았다).
    퀀트 정석: 피처를 쓰려면 그 데이터가 학습 구간 전체에 있어야 한다. 이력이 짧으면 피처를 빼거나
    학습 구간을 그 피처의 시작 이후로 줄인다 — 섞으면 학습은 결측으로, 실전은 실값으로 돌아간다.

    - 학습 구간(row_mask 로 고른 df 행) 유효율 < min_coverage → 제외
    - recent_df(홀드아웃 등 학습 이후 구간)가 주어지면 학습·최근 유효율 차이 > max_shift → 제외
    유효율은 라벨을 보지 않으므로 홀드아웃의 유효율을 보는 것은 누수가 아니다.
    """
    out: Dict[str, str] = {}
    for fid in feature_ids:
        cov = _coverage(df, fid, row_mask)
        if cov is None:
            continue
        if cov < min_coverage:
            out[fid] = (f"학습 구간 유효율 {cov:.1%} < {min_coverage:.0%} — 데이터가 학습 기간을 덮지 못한다 "
                        f"(피처를 빼거나 학습 시작을 데이터 시작 이후로)")
            continue
        if recent_df is not None:
            rcov = _coverage(recent_df, fid, recent_mask)
            if rcov is not None and abs(rcov - cov) > max_shift:
                out[fid] = (f"학습 유효율 {cov:.1%} vs 최근 {rcov:.1%} (차이 {abs(rcov - cov):.0%}p > "
                            f"{max_shift:.0%}p) — 학습과 실전의 입력 조건이 다르다")
    return out
