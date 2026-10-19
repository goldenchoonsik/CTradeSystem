"""
CAiModelBaker - Full-Period Feature Alpha Screener
전구간 데이터 기반 단변량 선행 지표 스크리너 & 랭킹 엔진 (초고속 벡터화)
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from typing import Dict, List, Any, Optional
from scipy import stats

# 프로젝트 루트 경로 등록
_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
_BAKER_ROOT = os.path.dirname(_CUR_DIR)
_PROJECT_ROOT = os.path.dirname(_BAKER_ROOT)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _BAKER_ROOT not in sys.path:
    sys.path.insert(0, _BAKER_ROOT)

from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY
from shared.data_library import paths as dpaths   # [T-5] 경로 SSOT
from shared.data_library.source_mapping_manager import SourceMappingManager
from shared.core.market_utils import get_target_symbol, detect_market_type, session_forward_return
try:
    from .data_manager import get_snapshot_path
except (ImportError, ValueError):
    from core.data_manager import get_snapshot_path


# 스크리닝 상수 (통과율 급변 방지를 위해 보수적으로 고정)
SCREEN_MAX_EVAL_ROWS = 60000          # IC 평가 시점 최대 개수 (서브샘플링 대상)
SCREEN_OVERLAP_CORRECTION = True      # 겹치는 forward return 유효표본 보정
SCREEN_MULTITEST_LAGS = 3             # 다중 lag 선택 보정에 쓰는 유효 독립 lag 수 (Sidak)


def _get_clean_coin_code(config: dict) -> str:
    """종목 코드 추출 (가상자산 및 국내 주식 모두 지원)"""
    sym = get_target_symbol(config)
    return sym.split("-")[-1]



LOOKBACK_LABELS = {"3y": "최근 3년", "2y": "최근 2년", "1y": "최근 1년", "all": "전체", "project": "AutoLab 학습 구간"}


def slice_screening_window(df: pd.DataFrame, config: dict, lookback_period: str = "3y", time_col: str = "timestamp"):
    """지표 스크리닝·자동 지표 선정이 공유하는 분석 구간 (2026-10-20).

    끝은 언제나 **홀드아웃 직전 거래일**(split_planner.resolve_holdout_boundary — WFA 창 설계와 무관).
    시작은 lookback: '3y'/'2y'/'1y' = 그 끝에서 N년, 'all' = 데이터 시작, 'project' = AutoLab 학습 구간(TRAIN_START~)
    — 1차(긴 역사 재현성)와 2차(지금 학습 구간) 확인을 같은 규칙으로 고른다(T-9 설계).
    반환: (잘린 df, 시작일, 끝일, 설명, 경계 dict)
    """
    from CAiModelBaker.core.split_planner import resolve_holdout_boundary
    df = df.copy()
    df[time_col] = pd.to_datetime(df[time_col])
    b = resolve_holdout_boundary(df, config, ts_col=time_col)
    if not b.get("ok"):
        raise ValueError(f"홀드아웃 경계를 정할 수 없습니다: {b.get('reason')}")
    end_dt = pd.to_datetime(b["research_end"]) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)  # 끝날 포함
    df = df[df[time_col] <= end_dt]
    lb = str(lookback_period or "3y")
    if lb == "project":
        p_start = config.get("TRAIN_START") or config.get("WFA_START_DATE")
        if not p_start:
            raise ValueError("AutoLab 학습 구간(WFA 시작일)이 아직 없습니다 — '최근 N년'·'전체'로 스크리닝하세요.")
        start_dt = pd.to_datetime(p_start)
        p_end = config.get("TRAIN_END") or config.get("WFA_END_DATE")
        if p_end:
            df = df[df[time_col] <= min(end_dt, pd.to_datetime(p_end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1))]
    elif lb in ("1y", "2y", "3y"):
        start_dt = pd.to_datetime(b["research_end"]) + pd.Timedelta(days=1) - pd.DateOffset(years=int(lb[0]))
    else:
        start_dt = df[time_col].min()
    df = df[df[time_col] >= start_dt].copy()
    if len(df) < 50:
        raise ValueError(f"분석 구간 데이터가 {len(df)}행뿐입니다 (홀드아웃 {b['holdout_start']}~ 제외 후).")
    start_str = df[time_col].min().strftime("%Y-%m-%d")
    end_str = df[time_col].max().strftime("%Y-%m-%d")
    desc = f"{LOOKBACK_LABELS.get(lb, lb)} ({start_str} ~ {end_str}, 홀드아웃 {b['holdout_start']}~ 제외)"
    return df, start_str, end_str, desc, b

def run_full_period_feature_screening(
    config: dict,
    max_lag_candles: int = 10,
    ic_threshold: float = 0.015,
    min_periods_ratio: float = 0.3,
    lookback_period: str = "3y"
) -> Dict[str, Any]:
    """
    수집된 스냅샷 데이터를 대상으로 지정 기간(프로젝트 구간, 최근 1/2/3년, 전체)에 맞추어
    전체 지표의 선행성, IC, 유의성, 단조성을 전수 계산합니다.
    """
    coin_code = _get_clean_coin_code(config)
    interval = config.get("INTERVAL", "1m")
    
    # 1. Gold 결합 데이터 스마트 탐색 (실버 Parquet 단독 참조는 다중 AI 불변 헌법 제1법칙으로 엄격 금지)
    #    [우선순위]
    #    1순위: 프로젝트 확정 Gold 스냅샷 (data_snapshot_{interval}.csv)
    #    2순위: DataVault 온디맨드 결합 골드 (data_vault_preview_{interval}.csv)
    #    3순위: FeatureComposer 인메모리 즉시 결합 (Fallback)
    # 원장 심볼 그대로(KRW-BTC) — Gold 작성기·조립기와 같은 키. 'BTC' 로 자르면 프리뷰/조립 폴백이
    # 다른 종목 키로 돌아 옛 조립 캐시(features/BTC/gold/*.parquet)를 읽었다 (2026-10-02).
    clean_code = SourceMappingManager.clean_symbol(get_target_symbol(config, default=coin_code))
    snapshot_path = None

    candidate_paths = [
        get_snapshot_path(config),                          # [T-5] 절대경로 (paths SSOT)
        dpaths.data_vault_preview_path(clean_code, interval), # [메달리온 3단계] 대장주 시점결합 골드 원장
    ]
    if config.get("SNAPSHOT_PATH"):
        candidate_paths.insert(0, config["SNAPSHOT_PATH"])

    for p in candidate_paths:
        if p and os.path.exists(p) and os.path.isfile(p):
            snapshot_path = p
            break

    df = None
    if snapshot_path and os.path.exists(snapshot_path):
        if snapshot_path.endswith(".parquet"):
            df = pd.read_parquet(snapshot_path)
        else:
            df = pd.read_csv(snapshot_path)
    else:
        # 3순위: 디스크에 골드 파일이 없으면 FeatureComposer로 인메모리 즉시 시점 결합 (실버 단독 우회 금지)
        from CAiModelBaker.core.feature_composer import FeatureComposer
        composer = FeatureComposer()
        df = composer.compose(
            target_symbol=clean_code,
            target_interval=interval,
            auto_bind_ssot=True,
            align_to_common_dates=True,
            synthesize_cross_features=True,
            use_cache=True,
        )

    if df is None or df.empty or len(df) < 50:
        raise ValueError(f"분석에 필요한 데이터 수가 부족합니다.")

    # ── [스마트 기간 슬라이싱] Lookback Period 필터링 ──
    time_col = None
    for c in ['timestamp', 'datetime', 'date', 'candle_date_time_kst', 'candle_date_time_utc']:
        if c in df.columns:
            time_col = c
            break

    if not time_col:
        raise ValueError("스냅샷에 시간 컬럼이 없습니다.")
    df, start_str, end_str, filtered_desc, _boundary = slice_screening_window(df, config, lookback_period, time_col)

    n_samples = len(df)
    
    # 2. 고속 연산: 지표/선행수익률은 전체(원본 시간축) 데이터로 계산하고,
    #    IC 평가 시점만 step 간격으로 서브샘플링한다 (다운샘플 후 롤링/shift 시 시간축 왜곡 방지).
    calc_df = df.copy().reset_index(drop=True)
    if n_samples > SCREEN_MAX_EVAL_ROWS:
        eval_step = n_samples // SCREEN_MAX_EVAL_ROWS
    else:
        eval_step = 1
    eval_idx = np.arange(0, n_samples, eval_step)

    # 가격 컬럼 확보
    target_close = None
    for col in ['target_close', 'upbit_close', 'close', 'current_price']:
        if col in calc_df.columns:
            target_close = calc_df[col].astype(float)
            break
    if target_close is None:
        raise ValueError("가격 컬럼(target_close / upbit_close)을 찾을 수 없습니다.")

    n_calc_samples = len(eval_idx)
    
    # 3. 미래 시차별 수익률(Forward Returns) 및 순위 사전 계산 (고속 벡터화)
    forward_returns = {}
    forward_returns_ranked = {}
    # 의사결정 시점·같은 세션 안에서만 정의 (자동학습 2차 스크리닝과 같은 규칙)
    _intraday_krx = (detect_market_type(config) == 'KRX' and bool(config.get('INTRADAY_SQUARE_OFF', True))
                     and time_col is not None)
    _ts_for_fwd = calc_df[time_col] if time_col else pd.Series(pd.NaT, index=calc_df.index)
    # 사정거리 잠금이면 그 지평 하나만 평가한다 — 학습 라벨·AutoLab 2차 스크리닝과 같은 지평.
    # (종전: 잠금과 무관하게 1~10캔들 중 최고 |IC| 시차를 골라, 60캔들 잠금에서도 10분 이내 예측력으로 지표를 골랐다)
    _hz = config.get("TARGET_HORIZON_CONFIG") or {}
    if str(_hz.get("mode", "")).lower() == "locked" and int(_hz.get("locked_predict_ahead") or 0) > 0:
        eval_lags = [int(_hz["locked_predict_ahead"])]
    else:
        eval_lags = list(range(1, max_lag_candles + 1))
    for lag in eval_lags:
        ret = pd.Series(session_forward_return(target_close.to_numpy(), _ts_for_fwd, lag, _intraday_krx))
        ret = ret.iloc[eval_idx].reset_index(drop=True)
        forward_returns[lag] = ret
        forward_returns_ranked[lag] = ret.rank()

    results = []
    
    # 4. 각 지표별 1:1 단변량 계산 및 선행성 검증
    feature_params = config.get("FEATURE_CONFIG", {}).get("feature_params", {})
    
    # ⚡ 참조 종목 피처 동적 등록 (선언 원장은 공용 소유자 한 곳에 위임 + 스냅샷의 ref_{key}_close 자동 감지)
    from shared.features.feature_registry import (
        register_reference_features, register_configured_reference_features,
    )
    register_configured_reference_features(config, DEFAULT_FEATURE_REGISTRY)

    for col in calc_df.columns:
        if col.startswith("ref_") and col.endswith("_close"):
            ref_candidate = col[4:-6]
            # 🛡️ 유령 별칭 접미사(_target, _upbit)는 참조 종목 자동 감지에서 영구 제외
            if ref_candidate.endswith("_target") or ref_candidate.endswith("_upbit"):
                continue
            try:
                register_reference_features(ref_candidate, DEFAULT_FEATURE_REGISTRY)
            except Exception:
                pass

    # scale_v2 프로젝트: 학습과 같은 규칙(봉 주기·예측 지평)으로 확정한 기본 파라미터로 평가하고,
    # 그 주기에서 쓸 수 없는 지표는 평가하지 않는다 (1봉짜리 지표가 우연한 IC 로 합격하는 것을 막는다)
    from shared.features.param_policy import (policy_of, POLICY_V2, resolve_feature_params, project_horizon_bars)
    _v2 = policy_of(config.get("FEATURE_CONFIG", {})) == POLICY_V2
    _v2_horizon = project_horizon_bars(config) if _v2 else None
    _v2_market = detect_market_type(config) if _v2 else None

    for feat_id, feat_def in DEFAULT_FEATURE_REGISTRY.items():
        if feat_def.get("is_alias", False):
            continue
            
        compute_fn = feat_def.get("compute")
        if not compute_fn:
            continue
            
        try:
            # 🛡️ [Quant Standard] 지표 스크리너는 외부 파라미터 오염에 영향받지 않고 지표 본연의 정보력을
            # 공평하게 평가하기 위해 레지스트리에 정의된 표준 기본값(Canonical Default)을 기준으로 평가합니다.
            params = {}
            for pname, pconfig in feat_def.get("params", {}).items():
                if "default" in pconfig:
                    params[pname] = pconfig["default"]
            if _v2:
                params, _problem, _ = resolve_feature_params(
                    feat_id, feat_def.get("params", {}), {}, interval, _v2_horizon, _v2_market)
                if _problem:
                    continue
                    
            feat_series = compute_fn(calc_df, params, interval)
            if feat_series is None or not isinstance(feat_series, pd.Series):
                continue
        except Exception:
            continue
            
        feat_series = feat_series.astype(float).iloc[eval_idx].reset_index(drop=True)
        valid_mask = feat_series.notna() & ~np.isinf(feat_series)
        
        # 유효 데이터 비율 검증
        if valid_mask.sum() < n_calc_samples * min_periods_ratio:
            continue
            
        feat_ranked = feat_series.rank()
        
        # 1~max_lag 시차별 Rank IC 계산
        best_lag = eval_lags[0]
        best_ic = 0.0
        best_abs_ic = 0.0
        lag_ic_profile = {}
        
        for lag in eval_lags:
            f_rank = forward_returns_ranked[lag]
            joint_mask = valid_mask & f_rank.notna()
            if joint_mask.sum() < 30:
                continue
                
            x_r = feat_ranked[joint_mask]
            y_r = f_rank[joint_mask]
            
            # 표준편차가 0인 상수 컬럼인 경우 분모 0 나눗셈 방지
            if x_r.std() == 0 or y_r.std() == 0:
                ic_val = 0.0
            else:
                with np.errstate(divide='ignore', invalid='ignore'):
                    # 피어슨 상관계수 on ranks = 스피어만 상관계수 (초고속)
                    ic_val = x_r.corr(y_r)
                if np.isnan(ic_val):
                    ic_val = 0.0
                    
            lag_ic_profile[lag] = round(float(ic_val), 4)
            if abs(ic_val) > best_abs_ic:
                best_abs_ic = abs(ic_val)
                best_ic = float(ic_val)
                best_lag = lag
                
        # t-statistic 및 p-value 계산 (최적 lag 기준)
        joint_mask = valid_mask & forward_returns[best_lag].notna()
        n_valid = int(joint_mask.sum())
        # 겹치는 forward return 자기상관 보정: 평가 시점 간격(eval_step) 대비 lag 겹침 배수만큼 유효 표본 축소
        overlap = max(1, int(np.ceil(best_lag / eval_step)))
        n_eff = int(n_valid / overlap) if SCREEN_OVERLAP_CORRECTION else n_valid
        if n_eff > 2 and abs(best_ic) < 0.9999:
            t_stat = best_ic * np.sqrt((n_eff - 2) / (1.0 - best_ic ** 2 + 1e-12))
            p_raw = float(2 * (1 - stats.t.cdf(abs(t_stat), df=n_eff - 2)))
            # 최대 |IC| lag 선택(다중검정) 보정: Sidak, 독립 lag 수는 상수로 제한
            m_tests = max(1, min(len(eval_lags), SCREEN_MULTITEST_LAGS))
            p_val = float(1.0 - (1.0 - p_raw) ** m_tests)
        else:
            t_stat = 0.0
            p_val = 1.0

        # [프로 퀀트 듀얼 트랙] 단조성(Monotonicity) 점수 계산
        mono_score = 0.0
        bucket_returns = []
        eval_track = "continuous"
        track_desc = "5분위"

        if n_valid >= 50:
            try:
                x_sub = feat_series[joint_mask]
                y_sub = forward_returns[best_lag][joint_mask]
                
                # 0값 비율 및 희소성 검사
                zero_ratio = float((x_sub == 0).mean())
                tried_qcut = False

                # 1) 일반 연속형: 0값 비중이 낮을 때 표준 5분위(Quantile) 분할
                if zero_ratio < 0.40:
                    try:
                        quantiles = pd.qcut(x_sub, q=5, labels=False, duplicates='drop')
                        if quantiles.nunique() >= 4:  # 유효 버킷 4~5개 형성 시
                            b_means = y_sub.groupby(quantiles).mean() * 100.0  # % 단위
                            bucket_returns = [round(float(v), 3) for v in b_means.values]
                            if len(bucket_returns) >= 3:
                                b_series = pd.Series(bucket_returns)
                                if b_series.std() != 0 and not np.isnan(b_series.std()):
                                    with np.errstate(divide='ignore', invalid='ignore'):
                                        mono_ic = pd.Series(range(len(bucket_returns))).corr(b_series)
                                    mono_score = round(abs(float(mono_ic)) * 100.0, 1) if not np.isnan(mono_ic) else 0.0
                                    eval_track = "continuous"
                                    track_desc = "5분위"
                                    tried_qcut = True
                    except Exception:
                        tried_qcut = False

                # 2) 희소/이벤트형 지표 (또는 5분위 분할 축소 지표): 3-상태 조건부 단조성
                if not tried_qcut:
                    eval_track = "event"
                    track_desc = "3상태(이벤트)"
                    m_down = x_sub < 0
                    m_flat = x_sub == 0
                    m_up = x_sub > 0

                    if m_down.sum() == 0 and m_up.sum() > 0:
                        # 비대칭 단방향 양수 지표 (예: 거래량 급증, 스파이크 비율 등)
                        q_pos = pd.qcut(x_sub[m_up], q=2, labels=[1, 2], duplicates='drop')
                        states = pd.Series(0, index=x_sub.index)
                        states[m_up] = q_pos
                    elif m_up.sum() == 0 and m_down.sum() > 0:
                        q_neg = pd.qcut(x_sub[m_down], q=2, labels=[-2, -1], duplicates='drop')
                        states = pd.Series(0, index=x_sub.index)
                        states[m_down] = q_neg
                    else:
                        # 양방향 글로벌 선물/모멘텀/충격 지표 (나스닥/S&P 500 선물 등)
                        states = pd.Series(0, index=x_sub.index)
                        states[m_down] = -1
                        states[m_flat] = 0
                        states[m_up] = 1

                    s_counts = states.value_counts()
                    valid_states = sorted([s for s in s_counts.index if s_counts[s] >= 10])

                    if len(valid_states) >= 3:
                        b_means = y_sub.groupby(states).mean() * 100.0
                        b_vals = [round(float(b_means[s]), 3) for s in valid_states]
                        bucket_returns = b_vals
                        b_series = pd.Series(b_vals)
                        if b_series.std() != 0 and not np.isnan(b_series.std()):
                            with np.errstate(divide='ignore', invalid='ignore'):
                                mono_ic = pd.Series(range(len(b_vals))).corr(b_series)
                            mono_score = round(abs(float(mono_ic)) * 100.0, 1) if not np.isnan(mono_ic) else 0.0
                    elif len(valid_states) == 2:
                        b_means = y_sub.groupby(states).mean() * 100.0
                        b_vals = [round(float(b_means[s]), 3) for s in valid_states]
                        bucket_returns = b_vals
                        diff = b_vals[1] - b_vals[0]
                        if (diff > 0 and best_ic > 0) or (diff < 0 and best_ic < 0):
                            mono_score = 100.0
                        else:
                            mono_score = 0.0
            except Exception:
                mono_score = 0.0

        # 인터벌 기반 선행 시간 문자열 계산
        interval_min = 1
        if interval.endswith('m'):
            try:
                interval_min = int(interval[:-1])
            except ValueError:
                interval_min = 1
        elif interval.endswith('s'):
            try:
                sec_val = int(interval[:-1])
                interval_min = sec_val / 60
            except ValueError:
                interval_min = 1/60
        elif interval.endswith('h') and interval[:-1].isdigit():
            interval_min = int(interval[:-1]) * 60
        elif interval.endswith('d') and interval[:-1].isdigit():
            interval_min = int(interval[:-1]) * 1440
            
        lead_time_str = f"{best_lag * interval_min:.1f}분 선행" if interval_min >= 1 else f"{int(best_lag * (interval_min * 60))}초 선행"
        
        # 합격 여부 판정 (|IC| >= 0.015 and p < 0.05 and mono >= 40%)
        is_passed = (best_abs_ic >= ic_threshold) and (p_val < 0.05) and (mono_score >= 40.0)

        results.append({
            "id": feat_id,
            "name": feat_def.get("name", feat_id),
            "name_en": feat_def.get("name_en", feat_id),
            "category": feat_def.get("category", "common"),
            "category_name": feat_def.get("category_name", "공통"),
            "lead_ic": round(best_ic, 4),
            "abs_ic": round(best_abs_ic, 4),
            "optimal_lag": int(best_lag),
            "lead_time": lead_time_str,
            "t_stat": round(float(t_stat), 2),
            "p_value": round(float(p_val), 5),
            "monotonicity_score": mono_score,
            "eval_track": eval_track,
            "track_desc": track_desc,
            "bucket_returns": bucket_returns,
            "lag_profile": lag_ic_profile,
            "passed": bool(is_passed)
        })

    # 5. 절댓값 IC 내림차순 정렬
    results.sort(key=lambda x: x["abs_ic"], reverse=True)
    
    # 순위 부여
    for idx, item in enumerate(results, start=1):
        item["rank"] = idx

    return {
        "status": "success",
        "symbol": get_target_symbol(config, default=coin_code),
        "interval": interval,
        "period_mode": lookback_period,
        "period_desc": filtered_desc,
        "start_date": start_str,
        "end_date": end_str,
        "holdout_start": _boundary["holdout_start"],
        "holdout_end": _boundary["holdout_end"],
        "total_samples": n_samples,
        "features_count": len(results),
        "passed_count": sum(1 for r in results if r["passed"]),
        "ranked_features": results
    }


def screen_features_for_target_horizon(
    df: pd.DataFrame,
    features: List[str],
    target_horizon_candles: int,
    min_ic: float = 0.015,
    interval: str = "10m"
) -> Dict[str, Any]:
    """
    [지표 수명-지평 적합성 스크리너]
    수수료/변동성에 의해 결정된 목표 호라이즌(target_horizon_candles)에 대해
    각 피처가 통계적 예측 유효성(Rank IC >= min_ic)을 유지하는지 사전 검증합니다.
    """
    if df is None or df.empty or len(df) < target_horizon_candles + 30:
        return {
            "status": "error",
            "message": "데이터가 부족하여 지표 수명 스크리닝을 수행할 수 없습니다.",
            "passed_features": features,
            "rejected_features": [],
            "is_playable": True
        }

    # 가격 컬럼 확보
    target_close = None
    for col in ['target_close', 'upbit_close', 'close', 'current_price']:
        if col in df.columns:
            target_close = df[col].astype(float)
            break

    if target_close is None:
        return {
            "status": "warning",
            "message": "가격 컬럼 미발견으로 스크리닝 건너뜀",
            "passed_features": features,
            "rejected_features": [],
            "is_playable": True
        }

    # 목표 지평(target_horizon_candles) 시점의 Forward Return 계산
    fwd_ret = (target_close.shift(-target_horizon_candles) - target_close) / (target_close + 1e-8)
    fwd_rank = fwd_ret.rank()

    passed_features = []
    rejected_features = []

    for feat_name in features:
        if feat_name not in df.columns:
            # 아직 연산되지 않은 피처는 레지스트리 확인
            feat_def = DEFAULT_FEATURE_REGISTRY.get(feat_name)
            if not feat_def or not feat_def.get("compute"):
                rejected_features.append({"feature": feat_name, "reason": "지표 정의 없음", "ic": 0.0})
                continue
            try:
                params = {k: v.get("default") for k, v in feat_def.get("params", {}).items() if "default" in v}
                feat_series = feat_def["compute"](df, params, interval)
            except Exception:
                rejected_features.append({"feature": feat_name, "reason": "연산 실패", "ic": 0.0})
                continue
        else:
            feat_series = df[feat_name]

        if feat_series is None or len(feat_series) == 0:
            rejected_features.append({"feature": feat_name, "reason": "데이터 없음", "ic": 0.0})
            continue

        feat_series = feat_series.astype(float)
        valid_mask = feat_series.notna() & ~np.isinf(feat_series) & fwd_rank.notna()
        if valid_mask.sum() < 30:
            rejected_features.append({"feature": feat_name, "reason": "유효 샘플수 부족", "ic": 0.0})
            continue

        x_r = feat_series.rank()[valid_mask]
        y_r = fwd_rank[valid_mask]

        if x_r.std() == 0 or y_r.std() == 0:
            rejected_features.append({"feature": feat_name, "reason": "분산 0 (상수값)", "ic": 0.0})
            continue

        corr, p_val = stats.pearsonr(x_r, y_r)
        corr = float(corr) if not np.isnan(corr) else 0.0
        abs_ic = abs(corr)

        feat_info = {
            "feature": feat_name,
            "ic": round(corr, 4),
            "abs_ic": round(abs_ic, 4),
            "p_value": round(float(p_val), 5)
        }

        if abs_ic >= min_ic and (p_val < 0.10 or np.isnan(p_val)):
            passed_features.append(feat_info)
        else:
            feat_info["reason"] = f"목표 지평({target_horizon_candles}캔들) 예측력 미달 (|IC|={abs_ic:.4f} < {min_ic})"
            rejected_features.append(feat_info)

    # 절댓값 IC 내림차순 정렬
    passed_features.sort(key=lambda x: x["abs_ic"], reverse=True)
    passed_names = [f["feature"] for f in passed_features]

    is_playable = len(passed_names) >= 3

    return {
        "status": "success",
        "target_horizon_candles": target_horizon_candles,
        "min_ic": min_ic,
        "is_playable": is_playable,
        "passed_count": len(passed_names),
        "passed_features": passed_names,
        "passed_details": passed_features,
        "rejected_count": len(rejected_features),
        "rejected_features": rejected_features,
        "summary": (
            f"🎯 목표 지평 {target_horizon_candles}캔들 스크리닝 완료: "
            f"적합 지표 {len(passed_names)}개 / 탈락 {len(rejected_features)}개 "
            f"➔ {'✅ 공략 적합 (학습 진행)' if is_playable else '🛑 공략 불가 (유효 지표 부족, 타임프레임 변경 권장)'}"
        )
    }

