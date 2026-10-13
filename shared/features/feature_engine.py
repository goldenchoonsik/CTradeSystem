"""
CTradeSystem Shared - Feature Engineering Engine

Baker의 model_engine.py에서 추출한 피처 엔지니어링 함수.
학습(Baker)과 실전(CTrade) 모두에서 동일 코드로 피처를 계산하여
Train-Serve Skew를 원천 차단합니다.

[KRX 세션 분리 연산 — legacy 정책]
SYMBOL_UPBIT가 국내 주식/ETF(숫자 6자리)인 경우,
날짜(Date)별로 독립적으로 피처를 계산하여 오버나이트 갭 오염을 차단합니다.

[scale_v2 정책 — shared/features/param_policy.py]
feature_config['param_policy'] == 'scale_v2' 이면 파라미터를 봉 주기·예측 지평 규칙으로 확정하고,
세션을 넘어 연속으로 계산한다(차트와 같다). 그 주기에서 쓸 수 없는 지표는 NaN 으로 두고
result.attrs['feature_param_invalid'] 에 이유를 남긴다. 정책 키가 없으면 legacy — 기존 계산 그대로.
"""
import logging
import re
import inspect
import numpy as np
import pandas as pd

from shared.core.market_utils import parse_interval_minutes


def run_feature_engineering(df, interval='15m', feature_config=None, config=None):
    """
    레지스트리 기반 피처 엔지니어링 (플러그인 아키텍처 v2)

    새 지표 추가 시 shared/feature_registry.py 한 곳만 수정 → 이 함수는 변경 불필요.

    항상 계산:
      - upbit_rsi_daily: 매크로 대세 장세 판별 필수 피처 (feature_params의 period_days 파라미터 반영)
      - upbit_atr: 동적 익손절(TBM)용 기준 데이터
    조건부 계산 (enabled_features 기준):
      - 나머지 모든 지표

    [KRX 전용]
    config가 제공되고 market_type이 "KRX"인 경우:
      - 날짜별로 피처를 독립 연산하여 전일 갭 오염 차단
      - is_warmup 컬럼 추가: 09:00~09:09 구간 True (라벨링/백테스트에서 진입 제외용)
    """
    from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY, load_feature_config as _load_fc
    from shared.core.market_utils import detect_market_type, KRX_WARMUP_END

    df = df.copy()

    if feature_config is None:
        feature_config = _load_fc()

    # 자산 유형 자동 판별
    market_type = detect_market_type(config) if config else "CRYPTO"

    input_gaps = {}
    from shared.features.param_policy import policy_of, POLICY_V2
    if policy_of(feature_config) == POLICY_V2:
        result = _run_feature_engineering_v2(df, interval, feature_config, config, market_type, input_gaps)
        _report_input_gaps(result, input_gaps)
        return result
    if market_type == "KRX":
        # KRX: 날짜별 세션 독립 연산
        result = _run_feature_engineering_krx(df, interval, feature_config, KRX_WARMUP_END, input_gaps)
    else:
        # CRYPTO / 기본: 기존 연속 연산 유지
        result = _run_feature_engineering_core(df, interval, feature_config, input_gaps)
    _report_input_gaps(result, input_gaps)
    return result


def _report_input_gaps(result, input_gaps):
    """입력 사전검사에서 계산을 생략한 지표를 실행 단위로 한 번만 알리고 df.attrs 에 남긴다."""
    if not input_gaps:
        return
    summary = {fid: {"missing": sorted(g["missing"]), "segments": g["segments"]} for fid, g in input_gaps.items()}
    result.attrs["feature_input_gaps"] = summary
    lines = [f"{fid}: {v['missing']} ({v['segments']}구간)" for fid, v in sorted(summary.items())]
    logging.warning(f"[FeatureEngine] 필수 입력이 없어 NaN 으로 둔 지표 {len(summary)}개: " + "; ".join(lines))


def _run_feature_engineering_krx(df, interval, feature_config, warmup_end, input_gaps=None):
    """
    KRX 전용: 날짜별로 분리하여 피처를 독립 연산하고 concat 후 반환.

    - 매일 09:00~09:09 구간은 지표 웜업을 위해 포함하되 is_warmup=True 마킹
    - 전일 데이터가 당일 지표에 혼입되지 않음
    """
    import pandas as pd

    ts_col = pd.to_datetime(df['timestamp'])
    df = df.copy()
    df['_date'] = ts_col.dt.date

    result_days = []
    for date, day_df in df.groupby('_date', sort=True):
        day_df = day_df.copy().reset_index(drop=True)

        # 지표 연산 (전일 데이터 완전 격리)
        day_computed = _run_feature_engineering_core(day_df, interval, feature_config, input_gaps)

        # 웜업 구간 마킹: 09:00~09:09 → is_warmup=True (특수 거래일은 원장 세션의 개장+10분)
        day_ts = pd.to_datetime(day_computed['timestamp'])
        from shared.core.market_utils import KRX_WARMUP_END, krx_session
        day_warmup_end = krx_session(date).warmup_end if warmup_end == KRX_WARMUP_END else warmup_end
        day_computed['is_warmup'] = day_ts.dt.time < day_warmup_end

        result_days.append(day_computed)

    if not result_days:
        return df

    result = pd.concat(result_days, ignore_index=True)
    # pd.concat 이후 timestamp가 numpy.datetime64로 변환되는 타입 오염 방지
    result['timestamp'] = pd.to_datetime(result['timestamp'])
    result.drop(columns=['_date'], errors='ignore', inplace=True)
    return result


def _run_feature_engineering_v2(df, interval, feature_config, config, market_type, input_gaps):
    """scale_v2: 파라미터 확정(학습) 또는 기록값 사용(실전) → 세션을 넘는 연속 계산 → 쓸 수 없는 지표 NaN."""
    import pandas as pd
    from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY, FEATURE_ALIASES
    from shared.features.param_policy import resolve_feature_config, project_horizon_bars

    fc = dict(feature_config)
    if not fc.get("params_resolved"):
        fc, _, _ = resolve_feature_config(fc, DEFAULT_FEATURE_REGISTRY, interval,
                                          project_horizon_bars(config), market_type, FEATURE_ALIASES)
    invalid = dict(fc.get("param_invalid") or {})
    if invalid:
        fc["enabled_features"] = [f for f in (fc.get("enabled_features") or []) if f not in invalid]
        fc["explicit_feature_list"] = True   # 전부 쓸 수 없으면 '지정 없음(=전체)'으로 해석되지 않게

    result = _run_feature_engineering_core(df.copy(), interval, fc, input_gaps, continuous=True)

    for fid in invalid:
        result[fid] = np.nan
        result[FEATURE_ALIASES.get(fid, fid)] = np.nan
    if invalid:
        logging.warning(f"[FeatureEngine] {interval} 에서 쓸 수 없는 지표 {len(invalid)}개를 NaN 으로 둡니다: "
                        + "; ".join(f"{k}: {v}" for k, v in sorted(invalid.items())))
    result.attrs["feature_param_invalid"] = invalid
    result.attrs["feature_params_resolved"] = fc.get("feature_params", {})
    result.attrs["required_history_bars"] = fc.get("required_history_bars")

    if market_type == "KRX" and "timestamp" in result.columns:
        from shared.core.market_utils import krx_session
        ts = pd.to_datetime(result["timestamp"])
        days = ts.dt.date
        warm = {d: krx_session(d).warmup_end for d in days.unique()}
        result["is_warmup"] = [t < warm[d] for t, d in zip(ts.dt.time, days)]
    return result


_FEATURE_DEP_CACHE = {}


def _raw_feature_refs(compute_fn):
    """compute 함수 소스에서 참조하는 컬럼명 집합을 추출(캐시)."""
    key = id(compute_fn)
    cached = _FEATURE_DEP_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        body = inspect.getsource(compute_fn)
        refs = set(re.findall(r"_get_series\(df,\s*['\"]([a-z0-9_]+)['\"]", body))
        refs |= set(re.findall(r"df\[['\"]([a-z0-9_]+)['\"]\]", body))
    except Exception:
        refs = set()
    _FEATURE_DEP_CACHE[key] = refs
    return refs


def _execute_feature_tasks(df, feature_tasks, interval, parallel=False):
    """피처 태스크를 의존성 위상(레벨) 순으로 실행하여 df에 컬럼을 채운다 (M-3).

    - 피처 간 의존성(예: target_cvd_momentum ← target_trade_bid_ratio)이 있는 경우,
      선행 피처가 df에 반영된 '이후 레벨'에서 계산되므로 순차/병렬 결과가 100% 동일하다.
    - 동일 레벨 내 피처들은 상호 독립이므로 병렬 실행해도 결정론적이다.
    """
    from shared.features.feature_registry import FEATURE_ALIASES

    task_map = {}
    for fid, fdef, prms in feature_tasks:
        task_map[fid] = (fdef, prms)
    id_set = set(task_map.keys())

    deps = {
        fid: {x for x in _raw_feature_refs(task_map[fid][0].get("compute")) if x in id_set and x != fid}
        for fid in task_map
    }

    # 위상 레벨 산출 (사이클/미해결 의존성은 사전순으로 안전 처리)
    remaining = dict(deps)
    resolved = set()
    levels = []
    while remaining:
        ready = sorted(f for f, d in remaining.items() if d <= resolved)
        if not ready:
            ready = sorted(remaining.keys())
        levels.append(ready)
        resolved |= set(ready)
        for f in ready:
            remaining.pop(f, None)

    def _calc(fid):
        fdef, prms = task_map[fid]
        try:
            return fid, fdef["compute"](df, prms, interval)
        except Exception as ex:
            logging.error(f"[FeatureEngineering] {fid} 계산 오류: {ex}")
            return fid, None

    for level in levels:
        if parallel and len(level) > 1:
            import os
            from concurrent.futures import ThreadPoolExecutor
            max_workers = min(12, os.cpu_count() or 4)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                computed_results = list(executor.map(_calc, level))
        else:
            computed_results = [_calc(fid) for fid in level]

        for fid, computed_val in computed_results:
            if computed_val is None:
                continue
            df[fid] = computed_val
            canonical_id = FEATURE_ALIASES.get(fid, fid)
            df[canonical_id] = computed_val


def _run_feature_engineering_core(df, interval, feature_config, input_gaps=None, continuous=False):
    """
    핵심 피처 엔지니어링 로직 (CRYPTO 및 KRX 일별 서브 df 모두 공통 사용).
    기존 run_feature_engineering 로직을 그대로 유지.

    input_gaps: 주면 입력 사전검사로 계산을 생략한 지표를 {fid: {"missing": set, "segments": n}} 로 누적한다.
    continuous: scale_v2 연속 계산 — 일봉 RSI 를 실제 일별 종가로 계산한다 (legacy 는 분 환산 창).
    """
    from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY, load_feature_config as _load_fc
    from shared.features.feature_registry import _get_series, FEATURE_ALIASES, ensure_reference_feature
    from shared.features.feature_registry import find_missing_inputs

    if feature_config is None:
        feature_config = _load_fc()

    enabled_features = feature_config.get('enabled_features')
    if not enabled_features and not feature_config.get('explicit_feature_list'):
        enabled_features = [k for k, v in DEFAULT_FEATURE_REGISTRY.items() if not v.get("is_alias", False)]
    enabled_features = enabled_features or []
    feature_params   = feature_config.get('feature_params', {})

    df = df.copy()

    intv_mins = parse_interval_minutes(interval)
    _rsi_daily_params = feature_params.get('target_rsi_daily', feature_params.get('upbit_rsi_daily', {}))
    _rsi_period_days  = _rsi_daily_params.get('period_days', 14)
    rsi_window  = max(30, int((_rsi_period_days * 1440) / intv_mins))
    min_periods = min(100, int(rsi_window * 0.2))

    t_close = _get_series(df, 'target_close', 'upbit_close')
    _diff = t_close.diff()
    _u    = _diff.clip(lower=0)
    _d    = -_diff.clip(upper=0)
    _rs   = _u.rolling(rsi_window, min_periods=min_periods).mean() / \
            (_d.rolling(rsi_window, min_periods=min_periods).mean() + 1e-8)
    rsi_daily_series = (100.0 - (100.0 / (1.0 + _rs))).fillna(50.0).shift(1).fillna(50.0)
    if continuous and 'timestamp' in df.columns:
        # 실제 일봉: 각 거래일 마지막 종가로 RSI → 전일 확정값을 그날 전체에 (미래참조 없음)
        from shared.features.feature_registry import _compute_target_rsi_daily
        rsi_daily_series = _compute_target_rsi_daily(df, _rsi_daily_params, interval)
    df['target_rsi_daily'] = rsi_daily_series
    df['upbit_rsi_daily'] = rsi_daily_series

    # ── 항상 계산: target_atr & upbit_atr ──
    atr_win = 14
    t_high = _get_series(df, 'target_high', 'upbit_high')
    t_low = _get_series(df, 'target_low', 'upbit_low')
    if not t_high.isna().all() and not t_low.isna().all():
        _c_prev = t_close.shift(1)
        _tr = pd.concat([t_high - t_low, (t_high - _c_prev).abs(), (t_low - _c_prev).abs()], axis=1).max(axis=1)
        atr_series = _tr.rolling(atr_win, min_periods=1).mean().fillna(t_close * 0.015)
    else:
        atr_series = t_close.rolling(atr_win, min_periods=1).std().fillna(t_close * 0.015)
    df['target_atr'] = atr_series
    df['upbit_atr'] = atr_series

    # ── 레지스트리 순회: enabled_features 기준 피처 병렬 계산 (멀티코어 가동) ──
    feature_tasks = []
    _unresolved_features = []
    for raw_fid in enabled_features:
        feature_id = raw_fid
        if feature_id in ('target_rsi_daily', 'upbit_rsi_daily'):
            continue  # 위에서 이미 계산

        feature_def = DEFAULT_FEATURE_REGISTRY.get(feature_id) or DEFAULT_FEATURE_REGISTRY.get(FEATURE_ALIASES.get(feature_id))
        if feature_def is None and ensure_reference_feature(feature_id):
            # 참조 종목 피처(ref_{key}_*)는 등록 순서에 의존하지 않고 id 만으로 즉시 등록된다
            feature_def = DEFAULT_FEATURE_REGISTRY.get(feature_id)
        if feature_def is None:
            _unresolved_features.append(feature_id)
            continue

        compute_fn = feature_def.get('compute')
        if compute_fn is None:
            continue

        # 입력 사전검사 — 외부/참조 입력이 없으면 대체값으로 계산을 강행하지 않고 NaN 으로 둔다
        # (출력만 보는 사후 검사는 대체값이 타겟과 섞인 경우를 못 잡는다: kimchi_premium = target*1e8-1, 2026-10-02)
        missing_inputs = find_missing_inputs(feature_id, df)
        if missing_inputs:
            df[feature_id] = np.nan
            df[FEATURE_ALIASES.get(feature_id, feature_id)] = np.nan
            if input_gaps is not None:
                gap = input_gaps.setdefault(feature_id, {"missing": set(), "segments": 0})
                gap["missing"].update(missing_inputs)
                gap["segments"] += 1
            continue

        params = dict(feature_params.get(feature_id, feature_params.get(FEATURE_ALIASES.get(feature_id, ''), {})))
        for pname, pcfg in feature_def.get('params', {}).items():
            if pname not in params:
                params[pname] = pcfg.get('default')

        feature_tasks.append((feature_id, feature_def, params))

    if _unresolved_features:
        # 조용히 건너뛰면 학습 후반에야 "피처 누락"으로 드러난다 — 등록되지 않은 피처는 즉시 알린다
        logging.warning(f"[FeatureEngine] 레지스트리에 없는 피처 {len(_unresolved_features)}개를 건너뜁니다: {_unresolved_features}")

    # 의존성 위상(레벨) 순 실행 — 병렬/순차 결과 100% 동일 보장 (M-3)
    use_parallel = (len(df) >= 3000 and len(feature_tasks) > 4)
    _execute_feature_tasks(df, feature_tasks, interval, parallel=use_parallel)

    # ── 하위 호환성 보장: target_* <-> upbit_* 피처 컬럼 미러링 ──
    for legacy_k, target_k in FEATURE_ALIASES.items():
        if target_k in df.columns and legacy_k not in df.columns:
            df[legacy_k] = df[target_k]
        elif legacy_k in df.columns and target_k not in df.columns:
            df[target_k] = df[legacy_k]

    return df
