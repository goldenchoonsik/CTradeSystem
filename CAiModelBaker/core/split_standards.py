# -*- coding: utf-8 -*-
"""
봉수(Interval)별 WFA · 홀드아웃 **표준 분할 정책** — 단일 소스 (P-2)
===================================================================
종전에는 표준값이 웹 대시보드 JS(`index.html:3984 getIntervalStandardConfig`)에만 존재했고,
백엔드 게이트(`research_loop` 의 `HOLDOUT_MIN_DAYS`)와 **독립적으로 진화**했다. 그 결과:

  · JS 표준: 초봉(10s) 홀드아웃 = 0.1개월 = **달력 3일**
  · 백엔드 게이트: `HOLDOUT_MIN_DAYS = 20` (달력일)
  → 초봉은 **영구히 통과 불가**하거나, 반대로 2거래일짜리 홀드아웃으로 **거래 1건이 배포**되었다.

본 모듈은 분할 정책의 **유일한 진실 원천(SSOT)** 이다.
  · 모든 기간 단위를 **달력일이 아니라 거래일(trading days)** 로 정의한다.
    근거: 통계적 표본 크기는 관측치 수로 결정된다 (López de Prado, *AFML* — bar 기반 샘플링).
  · 표준 적용이 불가한(데이터 부족) 경우 **최소값 + 비율 축소**로 강등하되,
    **비표준임을 mode/scale/deviations 로 명시**하여 결과물에 추적 가능하게 한다.

설계 (3-Tier)
-------------
| Tier | mode | 조건 | 표시 |
| :--- | :--- | :--- | :--- |
| 1 | `standard` | 데이터 ≥ 표준 lookback | 정상 |
| 2 | `proportional` | 부족 → 비율 축소 (최소값 이상 유지) | 🔶 비표준 |
| 3 | `minimum` | 비율 축소가 최소값에 클램프됨 | 🔶 비표준(최소) |
| — | `unavailable` | 최소 조합도 불가 | ⛔ WFA 불가 |

[설계: 버킷별 최솟값 (MINIMUMS_BY_BUCKET)]
------------------------------------------
타임프레임마다 하루 봉수(bars/day)가 극단적으로 다르다.
  - 10s 초봉: 하루 2,340봉  → Train 8거래일 = 18,720봉 (LightGBM에 충분)
  - 1m 분봉:  하루 381봉    → Train 10거래일 = 3,810봉
  - 5m 단기:  하루 76봉     → Train 15거래일 = 1,140봉
  - 1d 일봉:  하루 1봉      → Train 60거래일 = 60봉 (최소 통계)

동일한 train_td=20 최솟값을 전 타임프레임에 적용하면:
  - 초봉 31거래일 데이터 → 창 1개만 생성 (WFA 의미 없음) ← 핵심 문제
  - 초봉에서 train_td를 8로 낮추면 → 7개 창 생성 (정통 WFA 전진학습)
"""

from typing import Any, Dict

# ── 분할 모드 (결과물에 그대로 기록되어 감사·재현성에 사용된다) ──
SPLIT_MODE_STANDARD = "standard"          # Tier 1 — 표준
SPLIT_MODE_PROPORTIONAL = "proportional"  # Tier 2 — 비율 축소 (비표준)
SPLIT_MODE_MINIMUM = "minimum"            # Tier 3 — 최소값 (비표준)
SPLIT_MODE_UNAVAILABLE = "unavailable"    # 최소 조합도 불가 — WFA 불가
SPLIT_MODE_FULL = "full"                  # 전체 수동 분할

# ── 홀드아웃 검증 모드 (프로 퀀트 표준) ──
HOLDOUT_MODE_FULL = "full"                # 단일 홀드아웃 (트라이얼 선택·최종 판정 겸용 — 독립 OOS 아님)
HOLDOUT_MODE_SPLIT = "split"              # 고정 거래일 2단계 분할 (Validation + Final Test)
DEFAULT_FINAL_TEST_TD = 5                 # 분할 모드 시 기본 최종 Test 거래일수 (HOLDOUT_MIN_TRADING_DAYS=5 동기화)

# ── 봉수 버킷별 표준 (단위: 거래일) ──
# 정합성: 창수 = 1 + (lookback − holdout − 1윈도우) / test  → 전 구간 4~6개 (Pardo 표준)
STANDARD_SPLITS: Dict[str, Dict[str, Any]] = {
    "sub_minute": {   # 1s ~ 10s (초봉/HFT)
        "label": "1s~10s",
        "lookback_td": 63, "train_td": 21, "opt_td": 5, "test_td": 5, "holdout_td": 10,
        "note": "달력 3개월 상당. 종전 0.1개월(3달력일→2거래일)은 WFA 성립 불가",
    },
    "1m": {           # 1m ~ 3m (초단기 분봉)
        "label": "1m~3m",
        "lookback_td": 126, "train_td": 41, "opt_td": 10, "test_td": 10, "holdout_td": 21,
        "note": "달력 6개월 상당",
    },
    "5m": {           # 5m ~ 15m (단기/스윙)
        "label": "5m~15m",
        "lookback_td": 252, "train_td": 82, "opt_td": 21, "test_td": 21, "holdout_td": 41,
        "note": "달력 12개월 상당",
    },
    "30m": {          # 30m (중기/30분봉)
        "label": "30m",
        "lookback_td": 378, "train_td": 126, "opt_td": 31, "test_td": 31, "holdout_td": 63,
        "note": "달력 18개월 상당",
    },
    "1h": {           # 1h ~ 4h (장기/시간봉/60분봉)
        "label": "1h~4h",
        "lookback_td": 504, "train_td": 164, "opt_td": 42, "test_td": 42, "holdout_td": 82,
        "note": "달력 24개월 상당",
    },
    "1d": {           # 일봉
        "label": "1d",
        "lookback_td": 756, "train_td": 252, "opt_td": 63, "test_td": 63, "holdout_td": 126,
        "note": "달력 36개월 상당 (일봉은 1 bar = 1 거래일)",
    },
}

# ── 가상화폐(CRYPTO / 24시간 연중무휴) 표준 (단위: 거래일/일수) ──
# 초봉/단기분봉은 하루 캔들 수가 주식 대비 3.7배 많아 1/3~1/2로 축소하되,
# 60분봉(1h)은 하루 24봉에 불과하므로 충분한 머신러닝 표본(약 13,000봉) 및 다중 국면 학습을 위해
# 1.5년(540일) 룩백 / 180일 훈련창 표준을 적용. (일봉은 하루 1캔들이므로 756일 유지)
STANDARD_SPLITS_CRYPTO: Dict[str, Dict[str, Any]] = {
    "sub_minute": {   # 1s ~ 10s (초봉/HFT) — 하루 8,640봉
        "label": "1s~10s (Crypto)",
        "lookback_td": 21, "train_td": 8, "opt_td": 2, "test_td": 2, "holdout_td": 3,
        "note": "달력 3주 상당 (약 18만봉). 주식 3개월(14.7만봉)과 동등 이상의 표본 확보",
    },
    "1m": {           # 1m ~ 3m (초단기 분봉) — 하루 1,440봉
        "label": "1m~3m (Crypto)",
        "lookback_td": 42, "train_td": 14, "opt_td": 4, "test_td": 4, "holdout_td": 8,
        "note": "달력 6주 상당 (약 6만봉). 주식 6개월(4.9만봉)과 동등 표본 및 알파 신선도 유지",
    },
    "5m": {           # 5m ~ 15m (단기/스윙) — 하루 96~288봉
        "label": "5m~15m (Crypto)",
        "lookback_td": 84, "train_td": 28, "opt_td": 7, "test_td": 7, "holdout_td": 14,
        "note": "달력 12주(약 3개월) 상당 (약 8,000봉). 주식 1년(6,550봉)과 동등 표본 확보",
    },
    "30m": {          # 30m (중기/30분봉) — 하루 48봉
        "label": "30m (Crypto)",
        "lookback_td": 168, "train_td": 56, "opt_td": 14, "test_td": 14, "holdout_td": 28,
        "note": "달력 24주(약 6개월) 상당 (약 8,000봉)",
    },
    "1h": {           # 1h ~ 4h (시간봉/60분봉) — 하루 24봉 (연중무휴)
        "label": "1h~4h (Crypto)",
        "lookback_td": 540, "train_td": 180, "opt_td": 30, "test_td": 30, "holdout_td": 90,
        "note": "달력 약 18개월(약 1.5년) 상당 (약 13,000봉). 88개 고차원 피처 과적합 방지 및 다양한 시장 국면 학습",
    },
    "1d": {           # 일봉 (연중무휴)
        "label": "1d (Crypto)",
        "lookback_td": 756, "train_td": 252, "opt_td": 63, "test_td": 63, "holdout_td": 126,
        "note": "달력 약 25개월 상당 (일봉은 1 bar = 1일로 주식과 동일)",
    },
}

# ── 버킷별 최솟값 (Tier 3 하한 · 타임프레임 데이터 밀도 근거) ──
# 초봉은 하루 2,340봉이므로 train_td=8만으로도 18,720봉(LightGBM 충분).
# train_td를 20으로 유지하면 31거래일 데이터에서 창 1개밖에 생성되지 않아 WFA 의미 없음.
MINIMUMS_BY_BUCKET: Dict[str, Dict[str, int]] = {
    "sub_minute": {   # 1s ~ 10s (초봉/HFT)
        # 하루 2,340봉 → train 8일 = 18,720봉 (LightGBM에 충분)
        # 31거래일에서 창 7개 보장 (step=2거래일)
        "train_td": 8,
        "opt_td": 2,
        "test_td": 2,
        "holdout_td": 5,
        "wfa_windows": 3,
    },
    "1m": {           # 1m ~ 3m (초단기 분봉)
        # 하루 381봉 → train 10일 = 3,810봉
        "train_td": 10,
        "opt_td": 2,
        "test_td": 2,
        "holdout_td": 5,
        "wfa_windows": 3,
    },
    "5m": {           # 5m ~ 15m (단기/스윙)
        # 하루 76봉 → train 15일 = 1,140봉
        "train_td": 15,
        "opt_td": 3,
        "test_td": 3,
        "holdout_td": 5,
        "wfa_windows": 3,
    },
    "30m": {          # 30m (중기/30분봉)
        "train_td": 20,
        "opt_td": 3,
        "test_td": 3,
        "holdout_td": 5,
        "wfa_windows": 2,
    },
    "1h": {           # 1h ~ 4h (시간봉/60분봉)
        "train_td": 30,
        "opt_td": 5,
        "test_td": 5,
        "holdout_td": 10,
        "wfa_windows": 2,
    },
    "1d": {           # 일봉
        # 일봉은 표본 1봉=1일이므로 최솟값을 높게 유지
        "train_td": 60,
        "opt_td": 10,
        "test_td": 10,
        "holdout_td": 20,
        "wfa_windows": 2,
    },
}

MINIMUMS_BY_BUCKET_CRYPTO: Dict[str, Dict[str, int]] = {
    "sub_minute": {
        "train_td": 8, "opt_td": 2, "test_td": 2, "holdout_td": 5, "wfa_windows": 3,
    },
    "1m": {
        "train_td": 7, "opt_td": 2, "test_td": 2, "holdout_td": 3, "wfa_windows": 3,
    },
    "5m": {
        "train_td": 10, "opt_td": 2, "test_td": 2, "holdout_td": 4, "wfa_windows": 3,
    },
    "30m": {
        "train_td": 15, "opt_td": 3, "test_td": 3, "holdout_td": 5, "wfa_windows": 2,
    },
    "1h": {
        "train_td": 30, "opt_td": 5, "test_td": 5, "holdout_td": 10, "wfa_windows": 2,
    },
    "1d": {
        "train_td": 60, "opt_td": 10, "test_td": 10, "holdout_td": 20, "wfa_windows": 2,
    },
}

# ── 하위 호환성 별칭 (MINIMUMS) ──
# 기존 코드/테스트에서 `MINIMUMS["train_td"]` 등으로 참조하는 경우를 위해 유지한다.
# 값은 전체 버킷 중 가장 낮은(관대한) 최솟값(sub_minute 기준)으로 제공하여
# `r[k] >= MINIMUMS[k]` 형태의 검증이 깨지지 않도록 한다.
# ⚠️ 신규 코드에서는 반드시 `get_minimums_for_bucket(bucket)` 을 사용할 것.
MINIMUMS: Dict[str, int] = {
    "train_td": 8,         # sub_minute 최솟값 — 가장 낮은 하한 (전 버킷 공통 검증용)
    "opt_td": 2,
    "test_td": 2,
    "holdout_td": 5,
    "wfa_windows": 2,      # 비표준 허용 시 최소 창 수 (표준은 4~6)
}

# 최소 홀드아웃 **거래 횟수** — 기간과 OR 결합되는 실질 관문 (CLT 근사)
MIN_HOLDOUT_TRADES = 30

# ── 월 환산 계수 (UI 표시용) ──
# 거래일 ↔ 개월 변환은 **시장별 거래일 밀도**에 따라 다르다.
#   KRX/US: 연 252거래일 → 21.0 거래일/월
#   CRYPTO: 연중무휴      → 30.44 거래일/월
TRADING_DAYS_PER_MONTH = {"KRX": 21.0, "US": 21.0, "CRYPTO": 30.44}


def trading_days_per_month(market: str = "KRX") -> float:
    """시장별 월당 거래일 수 (거래일 ↔ 개월 환산 SSOT)."""
    m = str(market).strip().upper()
    if m.startswith("KRW-") or m.startswith("BTC") or m.startswith("ETH") or m in ("CRYPTO",):
        return TRADING_DAYS_PER_MONTH["CRYPTO"]
    if m in ("US", "USA", "CME", "NYSE", "NASDAQ"):
        return TRADING_DAYS_PER_MONTH["US"]
    return TRADING_DAYS_PER_MONTH["KRX"]

# 적용 대상 기간 키 (순서 고정)
_SPLIT_KEYS = ("lookback_td", "train_td", "opt_td", "test_td", "holdout_td")


def resolve_interval_bucket(interval: str) -> str:
    """인터벌 문자열을 표준 버킷 키로 분류한다.

    Examples:
        '10s' '5s' '30s'  → 'sub_minute'
        '1m'  '3m'        → '1m'
        '5m'  '10m' '15m' → '5m'
        '30m'             → '30m'
        '1h' '60m' '4h'   → '1h'
        '1d'  '1D'        → '1d'
    """
    s = str(interval or "1m").strip().lower()
    try:
        if s.endswith("s"):
            return "sub_minute"
        if s.endswith("m"):
            mins = float(s[:-1] or 1)
        elif s.endswith("h"):
            mins = float(s[:-1] or 1) * 60.0
        elif s.endswith("d"):
            return "1d"
        else:
            mins = float(s)          # 숫자만 온 경우 = 분
    except Exception:
        return "1m"                  # 파싱 실패 시 안전 기본값

    if mins < 1.0:
        return "sub_minute"
    if mins <= 3.0:
        return "1m"
    if mins <= 15.0:
        return "5m"
    if mins < 60.0:
        return "30m"
    if mins <= 240.0:
        return "1h"
    return "1d"


def is_crypto_market(market: str = "KRX") -> bool:
    """심볼 또는 시장 키가 가상화폐(Crypto / 24시간 연중무휴)인지 판별."""
    m = str(market or "KRX").strip().upper()
    return (
        m.startswith("KRW-")
        or m.startswith("BTC-")
        or m.startswith("USDT-")
        or m.startswith("BTC")
        or m.startswith("ETH")
        or m in ("CRYPTO", "UPBIT", "BINANCE", "COINBASE")
    )


def get_standard_split(interval: str, market: str = "KRX") -> Dict[str, Any]:
    """봉수 및 시장(주식 vs 가상화폐)에 해당하는 표준 분할(거래일 단위)을 반환한다 (복사본)."""
    bucket = resolve_interval_bucket(interval)
    table = STANDARD_SPLITS_CRYPTO if is_crypto_market(market) else STANDARD_SPLITS
    std = dict(table.get(bucket, table["1m"]))
    std["bucket"] = bucket
    return std



def get_minimums_for_bucket(bucket: str, market: str = "KRX") -> Dict[str, int]:
    """버킷별 및 시장별 최솟값 정책 복사본을 반환한다 (SSOT).

    버킷별로 타임프레임 데이터 밀도(bars/day)가 다르므로,
    최솟값은 반드시 버킷별로 독립적으로 적용해야 한다.

    Args:
        bucket: 'sub_minute' | '1m' | '5m' | '30m' | '1d'
        market: 시장 키 ('KRX' | 'CRYPTO' 등)

    Returns:
        {train_td, opt_td, test_td, holdout_td, wfa_windows}
    """
    table = MINIMUMS_BY_BUCKET_CRYPTO if is_crypto_market(market) else MINIMUMS_BY_BUCKET
    return dict(table.get(bucket, table["30m"]))


def get_minimums() -> Dict[str, int]:
    """최소값 정책 복사본 (하위 호환성 유지 — 신규 코드는 get_minimums_for_bucket 사용)."""
    return dict(MINIMUMS)


def expected_wfa_windows(lookback_td: int, holdout_td: int,
                         train_td: int, opt_td: int, test_td: int) -> int:
    """주어진 창 크기로 확보 가능한 WFA 창 수를 계산한다 (Pardo 표준 4~6 목표)."""
    try:
        wfa_td = int(lookback_td) - int(holdout_td)
        one_window = int(train_td) + int(opt_td) + int(test_td)
        if wfa_td <= 0 or one_window <= 0:
            return 0
        if one_window >= wfa_td:
            return 1
        step = max(1, int(test_td))
        return 1 + (wfa_td - one_window) // step
    except Exception:
        return 0


def compute_scaled_split(interval: str, available_trading_days: int, market: str = "KRX") -> Dict[str, Any]:
    """**3-Tier 정책**으로 실제 적용할 분할을 결정한다 (P-2 핵심).

    Args:
        interval: 인터벌 문자열 ('10s', '1m', '10m', '1d' …)
        available_trading_days: 데이터셋의 **실측 유효 거래일 수** (데이터 스캔 결과)
        market: 시장 키 ('KRX' | 'CRYPTO' 등)

    Returns:
        mode / standard_met / scale / bucket / label / note /
        lookback_td·train_td·opt_td·test_td·holdout_td (실제 적용값) /
        standard (표준값) / expected_wfa_windows / deviations / reason
    """
    std = get_standard_split(interval, market=market)
    bucket = std["bucket"]
    mins = get_minimums_for_bucket(bucket, market=market)  # ← 버킷별 및 자산군별 최솟값
    try:
        avail = max(0, int(available_trading_days))
    except Exception:
        avail = 0

    std_applied = {k: int(std[k]) for k in _SPLIT_KEYS}
    need_min = (mins["train_td"] + mins["opt_td"] + mins["test_td"] + mins["holdout_td"])

    result: Dict[str, Any] = {
        "mode": SPLIT_MODE_UNAVAILABLE,
        "standard_met": False,
        "scale": 0.0,
        "bucket": std["bucket"],
        "label": std["label"],
        "note": std.get("note", ""),
        "standard": std_applied,
        "available_trading_days": avail,
        "reason": None,
    }

    # ── Tier 0: 최소 조합조차 불가 ──
    if avail < need_min:
        result.update({
            "lookback_td": avail,
            "train_td": 0, "opt_td": 0, "test_td": 0, "holdout_td": 0,
            "expected_wfa_windows": 0,
            "deviations": {},
            "reason": (
                f"데이터 {avail}거래일 < 최소 조합 {need_min}거래일 "
                f"(Train {mins['train_td']}/Opt {mins['opt_td']}/Test {mins['test_td']}"
                f"/홀드아웃 {mins['holdout_td']}) — WFA 불가"
            ),
        })
        return result

    std_lookback = std_applied["lookback_td"]

    # ── Tier 1: 표준 충족 ──
    if avail >= std_lookback:
        applied = dict(std_applied)
        applied["lookback_td"] = min(avail, std_lookback)
        mode = SPLIT_MODE_STANDARD
        scale = 1.0
        reason = None
        deviations: Dict[str, Any] = {}
    else:
        # ── Tier 2/3: 비율 축소 ──
        scale = avail / float(std_lookback)
        applied = {}
        clamped = []
        for k in _SPLIT_KEYS:
            if k == "lookback_td":
                applied[k] = avail
                continue
            scaled = int(round(std_applied[k] * scale))
            floor_v = int(mins[k])
            if scaled < floor_v:
                clamped.append(k)
                scaled = floor_v
            applied[k] = scaled

        if applied["holdout_td"] > avail:
            applied["holdout_td"] = avail

        mode = SPLIT_MODE_MINIMUM if clamped else SPLIT_MODE_PROPORTIONAL
        deviations = {
            k: {"standard": std_applied[k], "applied": applied[k]}
            for k in _SPLIT_KEYS if applied[k] != std_applied[k]
        }
        reason = (
            f"데이터 {avail}거래일 < 표준 {std_lookback}거래일 → 비율 {scale:.3f} 축소"
            + (f" (최소값 클램프: {', '.join(clamped)})" if clamped else "")
        )

    windows = expected_wfa_windows(
        applied["lookback_td"], applied["holdout_td"],
        applied["train_td"], applied["opt_td"], applied["test_td"],
    )
    if windows < mins["wfa_windows"]:
        mode = SPLIT_MODE_MINIMUM
        reason = (reason + " / " if reason else "") + (
            f"확보 창수 {windows} < 최소 {mins['wfa_windows']} — WFA 신뢰도 낮음"
        )

    result.update({
        "mode": mode,
        "standard_met": (mode == SPLIT_MODE_STANDARD),
        "scale": round(float(scale), 4),
        "expected_wfa_windows": windows,
        "deviations": deviations,
        "reason": reason,
        **applied,
    })
    return result
