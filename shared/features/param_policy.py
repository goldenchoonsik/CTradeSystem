# -*- coding: utf-8 -*-
"""
지표 파라미터 정책 (Feature Parameter Policy) — 봉 주기에 맞는 지표 창 길이의 단일 결정 지점
=================================================================================

배경 (2026-10-07)
-----------------
069500 을 15분봉으로 옮기자 1분봉 기준으로 정한 기본값이 무너졌다.
  · 10~30분 창 22개 지표가 15분봉에서 1~2봉 — 1봉 RSI/평균은 '직전 봉 하나'일 뿐이다.
  · KRX 지표를 날짜별로 따로 계산하는데 15분봉은 하루 26봉이라 MACD(26+9)·z-score 30/60 은 하루 안에 차지 않고,
    min_periods=1 이 그 공백을 '개장부터의 누적값'으로 조용히 채웠다.
  · 같은 구조 탓에 target_rsi_daily 는 일봉이 아니라 장중 RSI, overnight_gap_pct 는 개장 시점에 항상 0 이었다.

현업 규칙 — 주기별 기본값 표를 두지 않고, 파라미터 '종류'마다 규칙 하나로 봉 수를 유도한다
------------------------------------------------------------------------------------
  stat     : 통계 표본 수(RSI/ATR/BB/MACD/MFI/z-score 창). 어느 주기에서나 같은 봉 수 (RSI 14 = 14봉).
  flow     : 수익률·수급 흐름의 측정 구간. 예측 시간(H봉)의 배수 — 무엇을 잴지가 예측 지평에서 정해진다.
             짝 파라미터(short/long)는 레지스트리 기본값의 비율을 지킨다.
  physical : 달력상 물리 주기(펀딩 8시간, 하루). 분 단위 그대로.
  none     : 창 길이가 아닌 값(표준편차 배수, 가중치, 모드).
그리고 결과 봉 수에 하한(2봉)·상한(실전 이력 예산)을 둔다. 벗어나면 그 주기에서 그 지표는 '쓸 수 없음'이다 —
엉뚱한 값을 조용히 만들지 않는다.

버전
----
  legacy   : 정책 키가 없는 기존 프로젝트·배포 모델. 지금까지의 계산을 비트 단위로 유지한다 (KRX 날짜별 계산).
  scale_v2 : 위 규칙 + 세션을 넘는 연속 계산(차트와 같다; 밤사이 갭은 실제 가격 변동). 학습 때 정해진 봉 수를
             feature_spec.json 에 전부 기록하고(params_resolved), 실전은 기록된 값만 쓴다.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

POLICY_LEGACY = "legacy"
POLICY_V2 = "scale_v2"

MIN_WINDOW_BARS = 2
# 실전 이력 예산 — 실전 집계기 버퍼와 백필이 감당하는 최대 봉 수. 이보다 긴 이력이 필요한 지표는 그 주기에서 쓸 수 없다.
MAX_HISTORY_BARS = 20000

# 파라미터 이름 → 종류 (기본 규칙)
_KIND_BY_NAME = {
    "period_bars": "stat", "period": "stat", "period_candles": "stat",
    "fast_period": "stat", "slow_period": "stat", "signal_period": "stat",
    "zscore_bars": "stat", "smooth_bars": "stat", "window": "stat",
    "period_days": "days",
    "window_mins": "flow", "candles": "flow", "window_bars": "flow",
    "short_bars": "flow", "long_bars": "flow",
    "std_dev": "none", "w_sam": "none", "w_hyn": "none", "mode": "none",
}
# 이름 규칙의 예외 (지표 의미로 정한다)
_KIND_OVERRIDES = {
    ("semi_duo_lead_delta", "window_bars"): "stat",     # 1봉 선행 수익률 — 리드-래그는 봉 단위 정의
    ("binance_funding_rate", "window"): "physical",     # 펀딩 주기(분)
    ("binance_funding_rate", "candles"): "stat",
    ("kimchi_premium", "window_mins"): "physical",      # 하루(1440분) 평균 환율
}
# 분 단위로 해석하는 파라미터 (나머지 창 파라미터는 봉 단위)
_MINUTE_PARAMS = {"window_mins"}
# 짝 파라미터: long 은 short 대비 레지스트리 비율을 지킨다
_PAIRS = (("short_bars", "long_bars"),)


def interval_minutes(interval: str) -> float:
    s = str(interval or "1m").strip().lower()
    num = "".join(ch for ch in s if ch.isdigit() or ch == ".") or "1"
    unit = "".join(ch for ch in s if ch.isalpha()) or "m"
    v = float(num)
    return {"s": v / 60.0, "sec": v / 60.0, "m": v, "min": v, "h": v * 60.0, "d": v * 1440.0}.get(unit, v)


def session_bars_per_day(interval: str, market_type: str) -> int:
    """하루(세션) 봉 수 — KRX 평일 정규장 390분, 그 밖은 24시간."""
    minutes = 390.0 if str(market_type).upper() == "KRX" else 1440.0
    return max(1, int(math.ceil(minutes / interval_minutes(interval))))


def param_kind(feature_id: str, pname: str) -> str:
    return _KIND_OVERRIDES.get((feature_id, pname)) or _KIND_BY_NAME.get(pname, "none")


def policy_of(feature_config: Optional[Dict[str, Any]]) -> str:
    p = str((feature_config or {}).get("param_policy") or POLICY_LEGACY).strip().lower()
    return POLICY_V2 if p == POLICY_V2 else POLICY_LEGACY


def project_horizon_bars(config: Optional[Dict[str, Any]]) -> Optional[int]:
    """프로젝트 예측 지평(봉). 잠금 모드면 잠금값, 아니면 탐색 범위 중앙 — 자동학습 IC 스크리닝과 같은 정의."""
    if not config:
        return None
    hz = config.get("TARGET_HORIZON_CONFIG") or {}
    if str(hz.get("mode", "locked")).lower() == "locked" and int(hz.get("locked_predict_ahead") or 0) > 0:
        return int(hz["locked_predict_ahead"])
    rng = (config.get("OPTUNA_CONFIG") or {}).get("predict_ahead_candles_range") or {}
    lo, hi = rng.get("user_min", rng.get("min")), rng.get("user_max", rng.get("max"))
    if lo is None or hi is None:
        return None
    return max(1, int((int(lo) + int(hi)) / 2))


def _bars_of(pname: str, value: float, interval: str) -> float:
    return float(value) / interval_minutes(interval) if pname in _MINUTE_PARAMS else float(value)


def _to_param_value(pname: str, bars: int, interval: str):
    if pname in _MINUTE_PARAMS:
        return round(bars * interval_minutes(interval), 6)
    return int(bars)


def resolve_feature_params(
    feature_id: str,
    registry_params: Dict[str, Dict[str, Any]],
    user_params: Optional[Dict[str, Any]],
    interval: str,
    horizon_bars: Optional[int],
    market_type: str,
) -> Tuple[Dict[str, Any], Optional[str], int]:
    """한 지표의 파라미터를 scale_v2 규칙으로 확정한다.

    Returns: (모든 파라미터가 명시된 dict, 쓸 수 없는 이유 또는 None, 필요한 이력 봉 수)

    사용자 값은 존중하되 레지스트리 기본값과 같은 값은 '설정하지 않음'으로 본다 — 1분봉 프로젝트에서 복사돼 온
    기본값 묶음이 15분봉에서 1봉짜리 지표로 굳는 것을 막는다.
    """
    user = dict(user_params or {})
    defaults = {k: (v or {}).get("default") for k, v in (registry_params or {}).items()}
    out: Dict[str, Any] = {}
    explicit = {k for k, v in user.items() if k in defaults and v is not None and v != defaults.get(k)}
    for k, v in user.items():          # 레지스트리에 없는 키(레거시 별칭 등)도 그대로 전달
        if k not in defaults:
            out[k] = v
    H = max(MIN_WINDOW_BARS, int(horizon_bars)) if horizon_bars else None
    if H is not None and str(market_type).upper() == "KRX":
        # 장중 청산(KRX) 모델의 지평은 하루를 넘지 못한다 — 라벨도 그날 강제 청산 시점에서 잘린다
        H = min(H, session_bars_per_day(interval, market_type))

    problem = None
    lookback = 1          # 봉 창 최대 (지수평활 수렴 여유 ×2)
    daily_lookback = 0    # 일봉 지표가 요구하는 세션 이력 (여유 불필요)
    resolved_bars: Dict[str, int] = {}
    for pname, dflt in defaults.items():
        kind = param_kind(feature_id, pname)
        value = user[pname] if pname in explicit else dflt
        if kind in ("none",) or value is None or isinstance(value, str):
            out[pname] = value
            continue
        if kind == "days":
            days = max(1, int(value))
            out[pname] = days
            daily_lookback = max(daily_lookback, (days + 1) * session_bars_per_day(interval, market_type))
            continue
        if kind == "physical":
            out[pname] = value
            lookback = max(lookback, int(math.ceil(_bars_of(pname, value, interval))))
            continue
        if kind == "flow" and pname not in explicit and H is not None:
            bars = H
        else:
            bars = int(round(_bars_of(pname, value, interval)))
        resolved_bars[pname] = bars

    # 짝 파라미터: 둘 다 기본값이면 long = short × (레지스트리 비율)
    for s_name, l_name in _PAIRS:
        if s_name in resolved_bars and l_name in resolved_bars and l_name not in explicit and s_name not in explicit:
            ratio = float(defaults[l_name]) / max(1e-9, float(defaults[s_name]))
            resolved_bars[l_name] = int(round(resolved_bars[s_name] * ratio))

    for pname, bars in resolved_bars.items():
        dflt = defaults.get(pname)
        if bars < MIN_WINDOW_BARS and not (param_kind(feature_id, pname) == "stat" and dflt is not None
                                           and int(round(_bars_of(pname, dflt, "1m"))) < MIN_WINDOW_BARS):
            # (기본값 자체가 1봉인 stat 파라미터 — smooth_bars=1(평활 없음), 1봉 리드 — 는 정의상 1봉이다)
            problem = problem or f"{pname}={bars}봉 < 최소 {MIN_WINDOW_BARS}봉 ({interval})"
        out[pname] = _to_param_value(pname, max(1, bars), interval)
        if pname == "window" and param_kind(feature_id, pname) == "stat":
            # 'window' 는 계산 함수(_get_window_bars)에서 60 이상이면 분으로 읽히고, 'candles' 보다 뒤에 읽힌다
            # (top2_lead_momentum 의 z-score 창이 candles=5 로 계산되던 legacy 결함). 봉 수를 명시 키로 고정한다.
            out["window_bars"] = int(max(1, bars))
        lookback = max(lookback, bars)
    if "slow_period" in resolved_bars and "signal_period" in resolved_bars:   # MACD: slow EMA 위의 signal EMA
        lookback = max(lookback, resolved_bars["slow_period"] + resolved_bars["signal_period"])

    required = max(int(lookback * 2 + 10), daily_lookback)   # 지수평활 수렴·z-score 창 위 창 여유
    if required > MAX_HISTORY_BARS and problem is None:
        problem = f"필요 이력 {required:,}봉 > 실전 이력 예산 {MAX_HISTORY_BARS:,}봉 ({interval})"
    return out, problem, required


def resolve_feature_config(
    feature_config: Dict[str, Any],
    registry: Dict[str, Dict[str, Any]],
    interval: str,
    horizon_bars: Optional[int],
    market_type: str,
    aliases: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, Any], Dict[str, str], int]:
    """enabled_features 전부의 파라미터를 확정한 feature_config 사본, 쓸 수 없는 지표 {id: 이유}, 전체 필요 이력 봉 수."""
    aliases = aliases or {}
    fc = dict(feature_config or {})
    user_all = fc.get("feature_params") or {}
    resolved: Dict[str, Any] = {}
    invalid: Dict[str, str] = {}
    required = 1
    for fid in fc.get("enabled_features") or []:
        fdef = registry.get(fid) or registry.get(aliases.get(fid, ""))
        user = user_all.get(fid, user_all.get(aliases.get(fid, ""), {}))
        if fdef is None:
            resolved[fid] = dict(user or {})
            continue
        params, problem, req = resolve_feature_params(
            fid, fdef.get("params") or {}, user, interval, horizon_bars, market_type)
        resolved[fid] = params
        if problem:
            invalid[fid] = problem
        else:
            required = max(required, req)
    fc["feature_params"] = resolved
    fc["param_policy"] = POLICY_V2
    fc["params_resolved"] = True
    fc["required_history_bars"] = required
    fc["param_invalid"] = invalid
    fc["horizon_bars"] = horizon_bars
    return fc, invalid, required
