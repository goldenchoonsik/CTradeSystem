"""
CAiModelBaker - Automated Strategy Research Loop (ASRL v2)

3단계 깔때기 구조:
  STEP 1. IC 스크리닝 (5초)    → 예선전: 쓸모없는 지표 탈락
  STEP 2. Optuna 최적화 (10분) → 본선: 파라미터 탐색
  STEP 3. 홀드아웃 검증 (1분)  → 결승전: 과적합 없는 최종 검증

과적합 방지: 홀드아웃 세트는 목표 달성 후 딱 1번만 개봉
"""
import os
import sys
import json
import shutil
import threading
import numpy as np
import pandas as pd
import lightgbm as lgb
from datetime import datetime, timedelta
from typing import Callable, Optional, Any, Dict
import gc

# 경로 설정
_BAKER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CTRADE_ROOT = os.path.dirname(_BAKER_ROOT)
if _BAKER_ROOT not in sys.path:
    sys.path.insert(0, _BAKER_ROOT)
if _CTRADE_ROOT not in sys.path:
    sys.path.insert(0, _CTRADE_ROOT)

from shared.core.market_utils import get_target_symbol, detect_market_type, session_forward_return
from shared.config import project_store
from shared.features.feature_health import find_dead_features, find_unstable_availability, find_insufficient_coverage
from shared.features.regime_stability import (
    VERDICT_INDETERMINATE as REGIME_INDETERMINATE, audit_regime_feature,
)

# 의사결정 시점 중 '직전 지평 봉 무거래' 비율 경고선 — 넘으면 거래량 기반 지표가 합성 봉의 상수를 학습시킬 위험
NO_TRADE_EXPOSURE_WARN = 0.02

try:
    from .data_manager import get_snapshot_path
    from .feature_registry import load_feature_config, save_feature_config, DEFAULT_FEATURE_REGISTRY
    from .feature_tuner import calculate_ic, get_interval_context, run_pre_tuning_features
    from .model_engine import (
        process_02_relay_optimization, run_feature_engineering,
        resolve_holdout_trade_requirement, evaluate_holdout_standard_met,
        HOLDOUT_MIN_TRADES_ABS, HOLDOUT_MIN_TRADING_DAYS, HOLDOUT_STANDARD_TRADES,
    )
    from .backtester import process_03_pure_backtest
except (ImportError, ValueError):
    from core.data_manager import get_snapshot_path
    from core.feature_registry import load_feature_config, save_feature_config, DEFAULT_FEATURE_REGISTRY
    from core.feature_tuner import calculate_ic, get_interval_context, run_pre_tuning_features
    from core.model_engine import (
        process_02_relay_optimization, run_feature_engineering,
        resolve_holdout_trade_requirement, evaluate_holdout_standard_met,
        HOLDOUT_MIN_TRADES_ABS, HOLDOUT_MIN_TRADING_DAYS, HOLDOUT_STANDARD_TRADES,
    )
    from core.backtester import process_03_pure_backtest

# ---------------------------------------------------------------------------
# 글로벌 상태 (API에서 접근)
# ---------------------------------------------------------------------------
_research_state = {
    "running": False,
    "current_trial": 0,
    "max_trials": 50,
    "best_profit": 0.0,
    "best_trial": -1,
    "best_features": [],
    "start_time": None,
    "status": "idle",        # idle/screening/optimizing/validating/done/failed
    "trial_history": [],
    "leaderboard": [],
    "diagnosis": None,
    "data_quality": None,    # 데이터 품질 게이트 보고 (제외한 날짜와 사유)
    "abort": False,
}
_research_lock = threading.Lock()
_research_thread: Optional[threading.Thread] = None


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------
def _sync_log(log_fn: Callable, msg: str):
    """log_fn이 동기/비동기 무관하게 호출 가능한 래퍼 (어떤 경우에도 메인 스레드를 죽이지 않음)"""
    if log_fn is None:
        print(msg)
        return
    import asyncio
    import inspect
    try:
        if inspect.iscoroutinefunction(log_fn):
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(log_fn(msg), loop)
        else:
            log_fn(msg)
    except Exception:
        print(f"[AUTO_RESEARCH] {msg}")


def _write_run_result(run_dir: str, updates: dict) -> None:
    """runs/<시각>/result.json — 이번 실행의 상태 요약 (RUNNING → PASSED / REJECTED / ABORTED / FAILED)"""
    path = os.path.join(run_dir, "result.json")
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
    data.update(updates)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _broadcast_progress(broadcast_fn: Optional[Callable], data: dict):
    """WebSocket 브로드캐스트 (선택적, 논블로킹 & 예외 방어)"""
    if broadcast_fn is None:
        return
    import asyncio
    import inspect
    try:
        payload = json.dumps(data, ensure_ascii=False)
        if inspect.iscoroutinefunction(broadcast_fn):
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(broadcast_fn(payload), loop)
        else:
            broadcast_fn(payload)
    except Exception:
        pass


def _format_elapsed(start_dt: Any) -> str:
    """소요 시간을 'X시간 Y분 Z초' 형식으로 포맷팅 (다양한 시간 객체 타입 완벽 지원)"""
    try:
        if isinstance(start_dt, str):
            start_dt = pd.to_datetime(start_dt).to_pydatetime()
        elif isinstance(start_dt, (np.datetime64, pd.Timestamp)):
            start_dt = pd.to_datetime(start_dt).to_pydatetime()
        
        delta = datetime.now() - start_dt
        if hasattr(delta, 'total_seconds'):
            total_sec = int(delta.total_seconds())
        elif isinstance(delta, np.timedelta64):
            total_sec = int(delta / np.timedelta64(1, 's'))
        else:
            total_sec = int(pd.Timedelta(delta).total_seconds())

        hours, remainder = divmod(total_sec, 3600)
        mins, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}시간 {mins}분 {secs}초"
        elif mins > 0:
            return f"{mins}분 {secs}초"
        else:
            return f"{secs}초"
    except Exception:
        return "0초"


def extract_feature_importance_from_model_file(model_txt_path: str) -> Dict[str, Dict[str, float]]:
    """LightGBM 모델 텍스트 파일로부터 Gain 기준 피처 중요도 딕셔너리 추출"""
    importance_map = {}
    if not model_txt_path or not os.path.exists(model_txt_path):
        return importance_map
    try:
        bst = lgb.Booster(model_file=model_txt_path)
        f_names = bst.feature_name()
        f_gains = bst.feature_importance(importance_type='gain')
        total_gain = float(np.sum(f_gains)) if len(f_gains) > 0 else 0.0
        for fname, fgain in zip(f_names, f_gains):
            gain_pct = round((float(fgain) / total_gain * 100.0), 2) if total_gain > 0 else 0.0
            importance_map[fname] = {
                "gain": round(float(fgain), 2),
                "gain_pct": gain_pct
            }
    except Exception:
        pass
    return importance_map


def _send_telegram_notification(msg: str):
    """텔레그램 알림 발송 유틸리티 (메인 스레드 블로킹 방지를 위해 백그라운드 스레드에서 전송)"""
    import threading
    
    def _send():
        try:
            from CTrade.utils.telegram_bot import get_bot
            bot = get_bot()
            if bot and bot._enabled:
                bot.send_message(msg)
                return
        except Exception:
            pass
        
        # 직접 API 발송 시도
        try:
            token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
            if token and chat_id:
                import requests
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                    timeout=5
                )
        except Exception:
            pass

    # 백그라운드 스레드로 즉시 위임하여 메인 루프 멈춤 0% 실현
    threading.Thread(target=_send, daemon=True).start()



def _is_regime_feature(fid: str, user_regime_config: Optional[dict] = None) -> bool:
    """체제/컨텍스트 지표 여부 판별 (선형 IC 스크리닝 면제 및 모델 백본 대상).
    사용자 override 설정(user_regime_config)이 존재하면 최우선 적용.
    """
    if user_regime_config and fid in user_regime_config:
        return bool(user_regime_config[fid])
    defn = DEFAULT_FEATURE_REGISTRY.get(fid, {})
    return defn.get("is_regime", False) or defn.get("category") == "time"


def calculate_conditional_alpha(df: pd.DataFrame, fid: str, ret_col: str = '_forward_return') -> dict:
    """
    이벤트/트리거 지표(희귀 상태, 스파이크 등)의 조건부 알파 및 유의성 평가.
    - 이벤트 발생 시(Event) vs 미발생 시(Baseline)의 전방 수익률 차이(Spread)
    - 이벤트 발생 시 승률(Hit Ratio)
    - Welch's t-statistic 및 등가 Effective IC 산출
    """
    if fid not in df.columns or ret_col not in df.columns:
        return {"passed": False, "reason": "column_missing"}

    valid_mask = ~(df[fid].isna() | df[ret_col].isna() | np.isinf(df[fid]) | np.isinf(df[ret_col]))
    f_s = df.loc[valid_mask, fid].astype(float)
    r_s = df.loc[valid_mask, ret_col].astype(float)
    n_total = len(f_s)
    if n_total < 100:
        return {"passed": False, "reason": "insufficient_data"}

    uniques = f_s.unique()
    if len(uniques) == 2:
        # 이진 지표: 높은 쪽 수준을 이벤트로 (0/1 이면 == 1)
        event_mask = (f_s > f_s.min())
    elif len(uniques) <= 4:
        # 다단 범주형(예: 세션 구간 1/2/3): `> 0` 으로 잡으면 **전체 행이 이벤트**가 되어
        # 기준군이 사라진다(실제로 `krx_session_time` 이 rare_sample 로 판정 불가였다).
        # 그래서 '나머지와 가장 다른 한 수준' 을 이벤트로 본다 — 연속형에서 상/하위 꼬리 중
        # 편차가 큰 쪽을 고르는 것과 같은 원리다.
        best_level, best_dev = None, -1.0
        for lv in uniques:
            m = (f_s == lv)
            if int(m.sum()) < 15 or int((~m).sum()) < 15:
                continue
            dev = abs(float(r_s[m].mean()) - float(r_s[~m].mean()))
            if dev > best_dev:
                best_dev, best_level = dev, lv
        if best_level is None:
            return {"passed": False,
                    "reason": f"rare_sample(levels={len(uniques)})",
                    "n_event": 0}
        event_mask = (f_s == best_level)
    elif 'squeeze' in fid.lower():
        # 스퀴즈 지표: 수축 상태(0 이하 또는 하위 20%)
        q_thresh = f_s.quantile(0.20)
        event_mask = (f_s <= min(0.0, q_thresh))
    elif 'spike' in fid.lower():
        # 스파이크 지표: 배율 >= 1.5 또는 상위 10%
        q_thresh = f_s.quantile(0.90)
        event_mask = (f_s >= max(1.5, q_thresh))
    else:
        # 일반 트리거: 상위 10% 또는 하위 10% 중 분산이 더 큰 방향
        q90 = f_s.quantile(0.90)
        q10 = f_s.quantile(0.10)
        mask_high = (f_s >= q90)
        mask_low = (f_s <= q10)
        ret_high = r_s[mask_high].mean() if mask_high.sum() > 0 else 0
        ret_low = r_s[mask_low].mean() if mask_low.sum() > 0 else 0
        event_mask = mask_high if abs(ret_high) >= abs(ret_low) else mask_low

    n_event = int(event_mask.sum())
    n_base = n_total - n_event

    # 최소 표본 수: 이벤트 발생이 최소 15회 이상이어야 통계적 유의성 검정 가능
    if n_event < 15 or n_base < 15:
        # 어느 쪽이 부족한지 밝힌다 (종전에는 기준군이 0개여도 'n=전체' 로 찍혀 원인을 숨겼다)
        short = "이벤트" if n_event < 15 else "기준군"
        return {
            "passed": False,
            "reason": f"rare_sample({short} n={min(n_event, n_base)})",
            "n_event": n_event
        }

    r_event = r_s[event_mask]
    r_base = r_s[~event_mask]

    mean_event = float(r_event.mean())
    mean_base = float(r_base.mean())
    spread = mean_event - mean_base
    std_event = float(r_event.std(ddof=1)) if n_event > 1 else 1e-6
    std_base = float(r_base.std(ddof=1)) if n_base > 1 else 1e-6

    # 승률 (Hit Ratio)
    win_rate = float((r_event > 0).mean())

    # Welch's t-statistic
    se_diff = np.sqrt((std_event**2 / n_event) + (std_base**2 / n_base))
    t_stat = float(spread / (se_diff + 1e-9))

    # 등가 Effective IC 산출: 퀀트 표준 점이연 상관계수 및 t-stat 정규화
    effective_ic = float(np.clip(abs(t_stat) / np.sqrt(abs(t_stat)**2 + min(n_event, 500)), 0.025, 0.12))

    # 통과 기준 (Dual-Trigger Pass Standard):
    # 1) 통계적 유의성: |t_stat| >= 1.96 AND |spread| >= 0.0008 (0.08% 이상)
    # OR
    # 2) 강력한 승률 편향: (win_rate >= 0.54 or win_rate <= 0.46) AND n_event >= 20 AND |spread| >= 0.0005
    # OR
    # 3) 초과수익률 차이: |spread| >= 0.0015 (0.15% 이상) AND n_event >= 20
    passed = (
        (abs(t_stat) >= 1.96 and abs(spread) >= 0.0008) or
        ((win_rate >= 0.54 or win_rate <= 0.46) and n_event >= 20 and abs(spread) >= 0.0005) or
        (abs(spread) >= 0.0015 and n_event >= 20)
    )

    return {
        "passed": passed,
        "n_event": n_event,
        "n_total": n_total,
        "event_ratio": float(n_event / n_total),
        "mean_event": mean_event,
        "mean_base": mean_base,
        "spread": spread,
        "win_rate": win_rate,
        "t_stat": t_stat,
        "effective_ic": effective_ic,
        # 미달 사유를 로그에 남긴다 (종전엔 키가 없어 '트리거 알파 미달: )' 로 비어 찍혔다)
        "reason": "" if passed else (f"spread={spread * 100:+.3f}% t={t_stat:+.2f} 승률={win_rate * 100:.1f}% "
                                     f"n={n_event} — 유의성·승률편향·초과수익 기준 모두 미달"),
    }


# ---------------------------------------------------------------------------
# STEP 1: IC 스크리닝
# ---------------------------------------------------------------------------
def _run_ic_screening(df: pd.DataFrame, forward_candles: int,
                      all_feature_ids: list, log_fn: Callable,
                      ic_threshold: float = 0.02,
                      min_features: int = 4,
                      broadcast_fn: Optional[Callable] = None,
                      user_regime_config: Optional[dict] = None,
                      intraday_krx: bool = False,
                      recent_df: Optional[pd.DataFrame] = None) -> tuple[list, list]:
    """
    멀티 트랙(Multi-Track) 지표 스크리닝:
    1. 체제/컨텍스트 지표: 선형 IC 면제 + **블록 일치도 검정** (무조건 프리패스가 아니다)
    2. 일반 연속형 지표: Spearman |IC| >= ic_threshold 검증
    3. 희귀 이벤트/트리거 지표: 조건부 전방 수익률(Spread) 및 Welch's t-test 기반 조건부 알파 평가
    반환: (alpha_ranked, regime_features)
      - alpha_ranked: [(feature_id, ic_score), ...] 내림차순 정렬
      - regime_features: [feature_id, ...] (프리패스 통과된 체제 지표 목록)
    """
    _sync_log(log_fn, "\n" + "="*60)
    _sync_log(log_fn, f"🔬 [STEP 1] IC 스크리닝 시작 ({len(all_feature_ids)}개 지표 평가, 기준: |IC| >= {ic_threshold})")
    _sync_log(log_fn, "="*60)

    df = df.copy()
    close_series = df['target_close'] if 'target_close' in df.columns else df['upbit_close']
    # 의사결정 시점·같은 세션 안에서만 정의 — KRX 장 막판의 밤사이 갭과 진입 불가 시간대를 IC 에서 뺀다
    # 연속 시장은 시각이 필요 없다. KRX 장중 모드에서 timestamp 가 없으면 여기서 KeyError 로 멈춘다(fail-closed).
    _ts = df['timestamp'] if (intraday_krx or 'timestamp' in df.columns) else pd.Series(pd.NaT, index=df.index)
    df['_forward_return'] = session_forward_return(close_series.to_numpy(), _ts, forward_candles, intraday_krx)

    alpha_results = []
    regime_results = []
    regime_findings = []
    total_feats = len(all_feature_ids)

    # 데이터가 연결되지 않은 피처(상수/전부 결측)는 IC=0 으로 조용히 탈락시키지 않고 사유와 함께 제외한다.
    # (체제 컨텍스트 프리패스도 예외가 아니다 — 죽은 컨텍스트 피처가 프리패스로 조합에 끼어들면 안 된다)
    dead_features = find_dead_features(df, all_feature_ids)
    if dead_features:
        _sync_log(log_fn, f"   ⛔ 데이터 미연결 피처 {len(dead_features)}개 제외:")
        for _fid, _why in dead_features.items():
            _sync_log(log_fn, f"      - {_fid:<38} {_why}")

    # 결측이 시기에 몰린 피처는 값의 유무가 기간 표식이 된다 — 원인(수집 공백·틱 순서 제외 등)을 묻지 않고
    # 의사결정 시점 행의 블록별 유효율로만 판정한다. 체제 지표도 예외가 아니다.
    from shared.core.market_utils import decision_point_mask
    unstable_avail = find_unstable_availability(
        df, [f for f in all_feature_ids if f not in dead_features], order_col='timestamp',
        row_mask=decision_point_mask(_ts, intraday_krx))
    if unstable_avail:
        _sync_log(log_fn, f"   ⛔ 가용성 불안정 피처 {len(unstable_avail)}개 제외:")
        for _fid, _why in unstable_avail.items():
            _sync_log(log_fn, f"      - {_fid:<38} {_why}")
        dead_features = {**dead_features, **unstable_avail}

    # 데이터가 학습 기간을 덮지 못하거나 학습·홀드아웃 유효율이 다른 피처 — 블록 간 '차이' 만 보는 위 검사는
    # 거의 전부 비어 있는 피처를 못 잡는다 (KRW-BTC OI 유효 0.7% 가 통과했다, 2026-10-02)
    if recent_df is not None and len(recent_df) == 0:
        recent_df = None
    low_coverage = find_insufficient_coverage(
        df, [f for f in all_feature_ids if f not in dead_features],
        row_mask=decision_point_mask(_ts, intraday_krx),
        recent_df=recent_df,
        recent_mask=decision_point_mask(recent_df['timestamp'], intraday_krx) if recent_df is not None else None)
    if low_coverage:
        _sync_log(log_fn, f"   ⛔ 커버리지 미달 피처 {len(low_coverage)}개 제외:")
        for _fid, _why in low_coverage.items():
            _sync_log(log_fn, f"      - {_fid:<38} {_why}")
        dead_features = {**dead_features, **low_coverage}

    for i, fid in enumerate(all_feature_ids):
        if fid in dead_features:
            continue
        ic = calculate_ic(df, fid, '_forward_return')
        abs_ic = abs(ic)

        # 1. 체제/컨텍스트 지표: 선형 IC 는 면제하되 **면제가 무검정은 아니다**.
        #    순위 IC 는 단조성을 가정해 주기형(hour_sin/cos)·U자형(세션 시간대)을 못 본다.
        #    대신 '블록 일치도' 로 판정한다 — 블록 밖에서 배운 집단 효과가 그 블록에서도 맞는지.
        #    (2026-09-28: 무조건 프리패스였던 탓에 챔피언 gain 84.5% 를 시각 지표가 차지했다)
        if _is_regime_feature(fid, user_regime_config):
            finding = audit_regime_feature(df, fid, '_forward_return', order_col='timestamp')
            if finding.stable:
                _sync_log(log_fn, f"   🛡️ {fid:<38} IC={ic:+.4f} (체제 컨텍스트: 블록 일치도 통과 — "
                                  f"{finding.agreement:+.3f} > 잡음바닥 {finding.noise_floor:+.3f})")
                regime_results.append(fid)
            elif finding.verdict == REGIME_INDETERMINATE:
                # 판정 불가는 통과가 아니다 — 근거 없이 백본에 올리지 않는다
                _sync_log(log_fn, f"   ⛔ {fid:<38} IC={ic:+.4f} (체제 컨텍스트: 판정 불가 — {finding.reason})")
            else:
                _sync_log(log_fn, f"   ❌ {fid:<38} IC={ic:+.4f} (체제 컨텍스트: 구간마다 뒤바뀜 — "
                                  f"일치도 {finding.agreement:+.3f} ≤ 잡음바닥 {finding.noise_floor:+.3f}, "
                                  f"블록 {finding.blocks}개)")
            regime_findings.append(finding)
            continue

        # 2. 일반 선형 IC 통과
        if abs_ic >= ic_threshold:
            _sync_log(log_fn, f"   ✅ {fid:<38} IC={ic:+.4f}  |IC|={abs_ic:.4f}")
            alpha_results.append((fid, abs_ic))
            continue

        # 3. 이벤트/트리거 지표 조건부 알파 평가 (희귀 상태/스파이크 지표 구제)
        defn = DEFAULT_FEATURE_REGISTRY.get(fid, {})
        is_trig = defn.get("is_trigger", False) or any(kw in fid.lower() for kw in ('squeeze', 'spike', 'large_trade'))
        if is_trig:
            cond_res = calculate_conditional_alpha(df, fid, '_forward_return')
            if cond_res.get("passed"):
                eff_ic = cond_res["effective_ic"]
                spread_pct = cond_res["spread"] * 100.0
                win_pct = cond_res["win_rate"] * 100.0
                t_val = cond_res["t_stat"]
                n_ev = cond_res["n_event"]
                _sync_log(log_fn, f"   ⚡ {fid:<38} IC={ic:+.4f} -> 조건부 알파 통과! (Spread={spread_pct:+.2f}%, 승률={win_pct:.1f}%, t={t_val:+.2f}, n={n_ev}, Eff_IC={eff_ic:.4f})")
                alpha_results.append((fid, eff_ic))
                continue
            else:
                reason = cond_res.get('reason', '')
                _sync_log(log_fn, f"   ❌ {fid:<38} IC={ic:+.4f}  |IC|={abs_ic:.4f} (트리거 알파 미달: {reason})")
                continue

        # 4. 일반 지표 기준 미달 탈락
        _sync_log(log_fn, f"   ❌ {fid:<38} IC={ic:+.4f}  |IC|={abs_ic:.4f}")
        
        if broadcast_fn and (i % 5 == 0 or i == total_feats - 1):
            _broadcast_progress(broadcast_fn, {
                "type": "auto_research_progress",
                "step": "ic_screening_progress",
                "current": i + 1,
                "total": total_feats
            })

    alpha_results.sort(key=lambda x: x[1], reverse=True)
    _n_regime_rejected = len(regime_findings) - len(regime_results)
    _sync_log(log_fn, f"\n📊 IC 스크리닝 결과: 알파 지표 {len(alpha_results)}개 통과 / "
                      f"체제 컨텍스트 지표 {len(regime_results)}개 통과"
                      + (f" ({_n_regime_rejected}개는 구간 불일치로 제외)" if _n_regime_rejected else ""))

    # ⚡ [과제 3] 소수 지표 적응: 가용 알파 풀 크기에 맞춰 최소 스크리닝 통과 요구 기준 동적 적응
    #   '가용 알파 풀' 은 **알파 트랙에서 평가된 지표 수**다. 체제로 분류된 지표는 통과 여부와
    #   무관하게 애초에 알파 후보가 아니고, 데이터가 없어 제외된 지표도 후보가 아니다.
    #   (종전에는 통과한 체제 지표만 빼서, 체제 지표가 탈락하면 그것을 알파 후보로 셌다)
    n_avail_alpha = max(1, len(all_feature_ids) - len(regime_findings) - len(dead_features))
    effective_min_screening = max(1, min(min_features, n_avail_alpha))
    if len(alpha_results) < effective_min_screening:
        _sync_log(log_fn, f"⚠️  알파 통과 지표가 {len(alpha_results)}개 (기준: {effective_min_screening}개 이상) — 피처 예측력 고갈 가능성")

    return alpha_results, regime_results


# ---------------------------------------------------------------------------
# STEP 1-B: 피처 조합 생성 (Trial 번호별)
# ---------------------------------------------------------------------------
def _build_feature_combinations(ic_ranked: list, trial_idx: int,
                                min_features: int = 4, max_features: int = 10,
                                regime_features: Optional[list] = None) -> list:
    """
    IC 랭킹 결과를 기반으로 Trial별 다양한 피처 조합 생성 (프로 표준).
    - 알파 지표: 최소 지표 수부터 시작해 IC 순으로 점진 확장 및 교체/제거로 다양성 확보
    - 체제 지표: 활성화된 체제/컨텍스트 지표를 백본(Backbone)으로 부착 — 단, **짝수/홀수 Trial 로
      '백본 있음 / 없음' 대조군을 만든다.**

    [백본 대조군 (2026-09-28)]
    종전에는 체제 백본이 **모든** 조합에 강제 부착돼 자기 부재와 비교된 적이 없었다. 그 결과
    챔피언 모델 gain 의 84.5% 를 시각 지표(`target_hour_sin/cos`)가 차지했는데도 어느 단계에서도
    드러나지 않았다. 같은 알파 조합을 백본 있음/없음 짝으로 돌리면 백본의 기여가 리더보드에서
    바로 보인다. 체제 지표가 없으면 짝을 만들지 않는다(중복 Trial 낭비 방지).
    """
    paired = bool(regime_features)
    bare = False
    if paired:
        bare = (trial_idx % 2 == 1)   # 홀수 Trial = 같은 알파 조합의 백본 없는 대조군
        trial_idx = trial_idx // 2

    def _attach_regime(subset: list) -> list:
        res = list(subset)
        # 알파가 하나도 없으면 대조군을 만들 수 없다(빈 피처셋) — 그때는 백본을 붙인다
        if regime_features and (not bare or not res):
            for rf in regime_features:
                if rf not in res:
                    res.append(rf)
        return res

    all_fids = [fid for fid, _ in ic_ranked]
    n_total = len(all_fids)
    if n_total == 0:
        return _attach_regime([])

    # -------------------------------------------------------------------------
    # ⚡ [과제 3] 소수 정예 알파 지표 (1~3개) 선택 시: 비어있지 않은 모든 부분집합(Power-Set) 순환
    #    K=1: [[f0]] (1개)
    #    K=2: [[f0], [f1], [f0, f1]] (3개)
    #    K=3: [[f0], [f1], [f2], [f0, f1], [f0, f2], [f1, f2], [f0, f1, f2]] (7개)
    # -------------------------------------------------------------------------
    if n_total <= 3:
        import itertools
        power_set_combos = []
        for r in range(1, n_total + 1):
            for combo in itertools.combinations(all_fids, r):
                power_set_combos.append(list(combo))

        chosen = power_set_combos[trial_idx % len(power_set_combos)]
        return _attach_regime(chosen)

    # 최소/최대 지표 수를 통과 풀 내에서 제한 (가용 풀 초과 방지)
    min_n = min(max(min_features, 1), n_total)
    max_n = min(max_features, n_total)

    # 통과가 최소 미만이면 전체 사용 (상한은 n_total)
    if n_total <= min_n:
        return _attach_regime(all_fids[:])

    oi_features = {"binance_oi_change_ratio", "binance_oi_ma_ratio"}

    # Phase 1: 최소 지표 수부터 시작해 IC 순으로 점진 확장 (+1씩)
    #   Trial 1: min_n개 (IC 상위)
    #   Trial 2: min_n+1
    #   ...      : max_n개 도달까지
    growth_steps = max(1, max_n - min_n + 1)
    if trial_idx < growth_steps:
        size = min_n + trial_idx
        return _attach_regime(all_fids[:size])

    # Phase 2: max_n개 도달 후 — 다양한 교체/제거 조합 순환
    cycle_idx = trial_idx - growth_steps
    max_base = all_fids[:max_n]

    # 2-A: OI 계열 제외 (동일해지면 첫 1개 제외)
    if cycle_idx == 0:
        filtered = [f for f in max_base if f not in oi_features]
        if len(filtered) == len(max_base):
            sub = max_base[:max_n - 1] if max_n - 1 >= min_n else max_base[:]
            return _attach_regime(sub)
        return _attach_regime(filtered)

    # 2-B: 카테고리 우선순위 (모멘텀/다이버전스 우선)
    if cycle_idx == 1:
        if max_n <= min_n:
            return _attach_regime(max_base[:])
        priority_cats = {"momentum", "divergence"}
        priority = [f for f in max_base
                    if DEFAULT_FEATURE_REGISTRY.get(f, {}).get("category") in priority_cats]
        rest = [f for f in max_base if f not in priority]
        combo = (priority + rest)[:max_n]
        if combo == max_base:
            sub = combo[1:] if len(combo) - 1 >= min_n else combo
            return _attach_regime(sub)
        return _attach_regime(combo)

    # 2-C: Leave-one-out + Pairwise 제거 조합 순환
    #   (최소 min_n 개수는 유지 — n_total - 1 >= min_n 이면 Leave-one-out 가능)
    if n_total >= min_n + 1:
        # Pairwise 제거가 최소 지표 수를 위반하면 사용하지 않음 (max_n - 2 < min_n)
        use_pairs = max_n >= min_n + 2
        pairs = [(i, j) for i in range(max_n - 1) for j in range(i + 1, max_n)] if use_pairs else []
        cycle_len = max_n + len(pairs)
        pos = (cycle_idx - 2) % max(cycle_len, 1)

        if pos < max_n:
            drop_idx = pos
            subset = [f for i, f in enumerate(max_base) if i != drop_idx]
        elif use_pairs:
            p_idx = pos - max_n
            if p_idx < len(pairs):
                drop_i, drop_j = pairs[p_idx]
                subset = [f for i, f in enumerate(max_base) if i != drop_i and i != drop_j]
            else:
                subset = max_base[:]
        else:
            # leave-one-out 사이클이 끝나면 전체 반환 (중복은 seen_combos가 스킵)
            subset = max_base[:]

        # 최소 개수 보장
        if len(subset) < min_n:
            return _attach_regime(max_base[:min_n])
        # max_n 초과 방지
        return _attach_regime(subset[:max_n])

    # 2-D: n_total이 max_n 초과 → 하위 지표 순환 교체 (기존 유지)
    if n_total > max_n:
        swap_range = n_total - max_n + 1
        swap_idx = (cycle_idx - 2) % swap_range
        replacement = all_fids[max_n:max_n + 1 + swap_idx]
        combined = all_fids[:max_n - len(replacement)] + replacement
        return _attach_regime(combined[:max_n])

    # Fallback
    return _attach_regime(max_base[:max_n])


# ---------------------------------------------------------------------------
# STEP 2: 최적화 실행
# ---------------------------------------------------------------------------
def _run_optimization(config: dict, feature_subset: list, log_fn: Callable,
                      holdout_start: str = "", purge_candles: int = None,
                      cached_df: pd.DataFrame = None) -> bool:
    """선택된 피처 조합(feature_subset)을 인메모리로 격리 주입하여 process_02 실행."""
    try:
        t_start = config.get("TRAIN_START", "")
        t_end = config.get("TRAIN_END", "")
        o_start = config.get("OPT_START", "")
        o_end = config.get("OPT_END", "")

        process_02_relay_optimization(
            config, t_start, t_end, o_start, o_end,
            log_fn=log_fn,
            check_abort_fn=lambda: _research_state["abort"],
            holdout_start=holdout_start,
            purge_candles=purge_candles,
            feature_subset=feature_subset,
            cached_df=cached_df,
        )
        return True
    except Exception as e:
        import traceback
        _sync_log(log_fn, f"❌ 최적화 실패: {e}\n{traceback.format_exc()}")
        return False


# ---------------------------------------------------------------------------
# STEP 3: 홀드아웃 검증
# ---------------------------------------------------------------------------
def detect_dead_features(df_full, features, test_start, test_end,
                         min_valid_ratio: float = 0.5):
    """
    최종 Test 구간에서 **사실상 죽은 피처**(전량/대부분 NaN, 또는 상수)를 탐지한다 (D-15).

    📌 종전 결함 (실측 배포 후보 2026-09-15):
      Phase 2 는 Validation 구간 성과만으로 Top-3 를 선정하고, Phase 3 최종 Test 에서
      그 피처가 가용한지 **검증하지 않았다.** 그 결과 참조 원천이 중단(2026-09-02)되어
      최종 Test 구간에서 전량 NaN 인 `ref_samsung_spread_vs_target` 이 rank_2/rank_3 에 채택되었다.
      훈련 구간에서만 gain 이 높은 **죽은 피처**는 추론 시 LightGBM 기본 방향으로 흘러 판정을 왜곡한다.

    Args:
        df_full: 피처 엔지니어링이 완료된 전체 데이터프레임
        features: 후보 피처 목록
        test_start: 최종 Test 시작일 (YYYY-MM-DD)
        test_end: 최종 Test 종료일 (YYYY-MM-DD, 해당 일 포함)
        min_valid_ratio: 유효값 비율 하한 (기본 0.5 → 50% 이상 유효해야 함)

    Returns:
        (dead: List[str], details: Dict[str, dict])
    """
    dead, details = [], {}
    if df_full is None or not features or "timestamp" not in getattr(df_full, "columns", []):
        return dead, details
    try:
        ts = pd.to_datetime(df_full["timestamp"])
        s_ts = pd.Timestamp(str(test_start))
        e_ts = pd.Timestamp(str(test_end)) + pd.Timedelta(days=1)
        window = df_full.loc[(ts >= s_ts) & (ts < e_ts)]
    except Exception:
        return dead, details
    if window.empty:
        return dead, details

    for col in features:
        if col not in window.columns:
            dead.append(col)
            details[col] = {"reason": "MISSING_COLUMN", "valid_ratio": 0.0,
                            "nunique": 0, "rows": int(len(window))}
            continue
        s = pd.to_numeric(window[col], errors="coerce")
        valid_ratio = float(s.notna().mean())
        nunique = int(s.dropna().nunique())
        if valid_ratio < float(min_valid_ratio):
            dead.append(col)
            details[col] = {"reason": "INSUFFICIENT_COVERAGE", "valid_ratio": round(valid_ratio, 4),
                            "nunique": nunique, "rows": int(len(window))}
        elif nunique <= 1:
            dead.append(col)
            details[col] = {"reason": "CONSTANT", "valid_ratio": round(valid_ratio, 4),
                            "nunique": nunique, "rows": int(len(window))}
    return dead, details


def _run_holdout_validation(config: dict, holdout_start: str, holdout_end: str,
                             target_criteria: dict, log_fn: Callable,
                             feature_subset: list = None,
                             session_id: str = None,
                             cached_df: pd.DataFrame = None) -> dict:
    """
    홀드아웃 구간으로 백테스트 실행 후 목표값 달성 여부 반환.

    feature_subset: 검증 중 사용할 피처 목록.
    cached_df: 사전 연산된 메모리 피처 데이터프레임 (AutoLab 고속 캐시)
    """
    _sync_log(log_fn, f"\n🔒 [STEP 3] 홀드아웃 검증: {holdout_start} ~ {holdout_end}")

    try:
        active_sid = session_id or config.get("ACTIVE_SESSION_ID") or config.get("SESSION_ID")
        result = process_03_pure_backtest(
            config, holdout_start, holdout_end,
            log_fn=log_fn, session_id=active_sid,
            feature_subset=feature_subset,
            cached_df=cached_df
        )
        if not result:
            return {"passed": False, "net_profit": 0.0, "monthly_profit": 0.0, "monthly_trades": 0.0, "mdd": 99.0, "trades": 0}

        net_profit = result.get("net_profit", 0.0)
        total_trades = result.get("total_trades", 0)
        mdd = result.get("mdd", 99.0)

        target_profit = target_criteria.get("min_profit", 0.0)
        target_mdd = target_criteria.get("max_mdd", 10.0)

        # ── [D-12/D-17/P-4] 홀드아웃 길이는 **거래일** 기준 (달력일 → 시장 캘린더 환산) ──
        # 종전: (d_end - d_start).days + 1 (달력일) → KRX 는 달력일의 ~67% 만 거래일이므로
        #       표본을 40% 부풀렸고, 2달력일 창에서 target_trades 가 1로 붕괴했다.
        raw_min_trades = float(target_criteria.get("min_trades", 4.0))
        optuna_cfg = config.get("OPTUNA_CONFIG", {}) or {}
        _mkt_key = str(config.get("SYMBOL_UPBIT") or config.get("SYMBOL") or "KRX")
        holdout_days = 30
        try:
            from shared.core.market_calendar import count_trading_days
            _n_td = count_trading_days(holdout_start, holdout_end, _mkt_key)
            if _n_td > 0:
                holdout_days = int(_n_td)
        except Exception:
            try:
                from datetime import datetime
                d_start = datetime.strptime(str(holdout_start).split()[0], "%Y-%m-%d")
                d_end = datetime.strptime(str(holdout_end).split()[0], "%Y-%m-%d")
                holdout_days = max(1, (d_end - d_start).days + 1)
            except Exception:
                pass
        target_trades, sample_sufficient, holdout_days = resolve_holdout_trade_requirement(
            raw_min_trades, holdout_days, optuna_cfg
        )

        # [P-4] 업계 표준 충족 여부 = (거래일 ≥ 표준) OR (거래 횟수 ≥ 30) — 차단이 아니라 '비표준' 표시용
        standard_holdout_td = 0
        try:
            from CAiModelBaker.core.split_standards import get_standard_split
            standard_holdout_td = int(get_standard_split(config.get("INTERVAL", "1m"))["holdout_td"])
        except Exception:
            pass
        standard_met = evaluate_holdout_standard_met(
            holdout_days, standard_holdout_td or holdout_days, total_trades, optuna_cfg
        )


        monthly_profit = round(net_profit * (30.0 / holdout_days), 2)
        monthly_trades = round(total_trades * (30.4 / holdout_days), 1)

        win_rate = result.get("win_rate", 0.0)
        profit_factor = result.get("profit_factor", 0.0)
        sharpe_ratio = result.get("sharpe_ratio", 0.0)

        # ── [다차원 적합도 평가 (Multi-Objective Fitness)] ──
        # 1) 일반 거래수 기준 충족: total_trades >= target_trades
        # 2) 고승률 스나이퍼 특례: 승률 >= 60% 및 (PF >= 1.5 또는 Sharpe >= 1.0)
        #    [D-13] 종전 임계는 `>= 2회` 였다 → 거래 4~5건 전승이 목표 5회를 우회했다(실측:
        #           `거래수: 4회 (목표: >= 5회) … 충족`). 특례도 절대 하한을 지켜야 한다.
        is_sniper_pass = (
            total_trades >= HOLDOUT_MIN_TRADES_ABS
            and win_rate >= 60.0
            and (profit_factor >= 1.5 or sharpe_ratio >= 1.0)
        )
        trade_requirement_met = (total_trades >= target_trades) or is_sniper_pass

        # [D-17] 홀드아웃이 최소 기간 미만이면 거래 수/성과 목표 자체가 통계적으로 무의미하다
        if not sample_sufficient:
            is_sniper_pass = False
            trade_requirement_met = False

        # [D-14] 손실 0건이면 PF 는 수학적으로 정의되지 않는다 (99.0 은 표식일 뿐 성과가 아님)
        pf_lossless = bool(result.get("pf_lossless", False))

        passed = (
            sample_sufficient and
            net_profit >= target_profit and
            trade_requirement_met and
            mdd <= target_mdd
        )

        _sync_log(log_fn, f"\n{'✅ 목표 달성!' if passed else '❌ 목표 미달성'}")
        _sync_log(log_fn, f"   순수익: {net_profit:+.2f}% (월환산: {monthly_profit:+.2f}%) (목표: >= {target_profit}%)")
        _sync_log(log_fn, f"   거래수: {total_trades}회 (월환산: {monthly_trades}회) (목표: >= {target_trades}회 또는 스나이퍼 특례 달성 여부: {'충족' if is_sniper_pass else '미충족'})")
        if not sample_sufficient:
            _sync_log(log_fn, f"   ⛔ [D-17/P-4] 홀드아웃 {holdout_days}거래일 < 최소 {int(HOLDOUT_MIN_TRADING_DAYS)}거래일 — 표본 부족으로 통계적 판정 불가 (passed 강제 False)")
        if not standard_met:
            _sync_log(log_fn, f"   🔶 [P-4] 비표준 홀드아웃 — 거래일 {holdout_days}/{standard_holdout_td} 미달 & 거래 {total_trades}회 < 표준 {HOLDOUT_STANDARD_TRADES}회 (차단 아님·표시용)")
        if pf_lossless:
            _sync_log(log_fn, f"   ⚠️ [D-14] 손실 0건 → PF 정의불가(99.0 표식). 표본 {total_trades}건만으로 성과 판정 불가")
        _sync_log(log_fn, f"   승률:   {win_rate:.1f}% | PF: {profit_factor:.2f} | 샤프: {sharpe_ratio:.2f}")
        _sync_log(log_fn, f"   MDD:    {mdd:.2f}% (목표: <= {target_mdd}%)")

        return {
            "passed": passed,
            "net_profit": net_profit,
            "monthly_profit": monthly_profit,
            "period_days": holdout_days,
            "mdd": mdd,
            "trades": total_trades,
            "monthly_trades": monthly_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            # ── [D-12~D-17 / P-4] 판정 근거 투명화 ──
            "required_trades": float(target_trades),
            "sample_sufficient": bool(sample_sufficient),
            "holdout_trading_days": int(holdout_days),
            "standard_holdout_td": int(standard_holdout_td),
            "standard_met": bool(standard_met),
            "pf_lossless": pf_lossless,
            "dead_features": [],
            "sharpe_ratio": sharpe_ratio,
            "avg_hold_mins": result.get("avg_hold_mins", 0.0),
            "best_trade": result.get("best_trade", 0.0),
            "worst_trade": result.get("worst_trade", 0.0),
            "trade_logs": result.get("trade_logs", []),
        }
    except Exception as e:
        _sync_log(log_fn, f"❌ 홀드아웃 검증 실패: {e}")
        return {
            "passed": False,
            "net_profit": 0.0,
            "monthly_profit": 0.0,
            "period_days": 1,
            "mdd": 99.0,
            "trades": 0,
            "monthly_trades": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "required_trades": float(HOLDOUT_MIN_TRADES_ABS),
            "sample_sufficient": False,
            "holdout_trading_days": 0,
            "standard_holdout_td": 0,
            "standard_met": False,
            "pf_lossless": False,
            "dead_features": [],
            "sharpe_ratio": 0.0,
            "avg_hold_mins": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "trade_logs": []
        }


# ---------------------------------------------------------------------------
# 실패 진단
# ---------------------------------------------------------------------------
def diagnose_failure(trial_history: list, min_trades: int = 5,
                     min_features: int = 4) -> dict:
    """전체 Trial 기록을 분석하여 실패 원인 자동 분류."""
    if not trial_history:
        return {
            "code": "NO_DATA",
            "title": "시도 기록 없음",
            "message": "유효한 Trial 결과가 없습니다.",
            "prescription": "스냅샷 데이터 유효성을 확인하세요."
        }

    trades_list = [t.get("trades", 0) for t in trial_history]
    mdd_list = [t.get("mdd", 0.0) for t in trial_history]
    profit_list = [t.get("net_profit", 0.0) for t in trial_history]
    ic_pass_count = trial_history[0].get("ic_pass_count", 99)

    avg_trades = np.mean(trades_list) if trades_list else 0
    avg_mdd = np.mean(mdd_list) if mdd_list else 0
    median_profit = np.median(profit_list) if profit_list else 0

    # IC 통과 지표가 충분하지 않으면 먼저 피처 예측력 부족 처리 (스크리닝 중단 케이스 포함)
    # 기준은 사용자 설정 min_features 반영 (최소 2개 보장)
    min_screening = max(min_features, 2)
    if ic_pass_count < min_screening:
        return {
            "code": "FEATURE_EXHAUSTED",
            "title": "피처 예측력 고갈",
            "message": f"IC 스크리닝 통과 지표 수: {ic_pass_count}개 (기준: {min_screening}개 이상)",
            "prescription": (
                "현재 지표들의 예측력이 현재 시장에서 유효하지 않습니다.\n"
                "권장 조치:\n"
                "  1. 신규 데이터 소스 발굴 (펀딩비, 청산 데이터 등)\n"
                "  2. 학습 기간을 최근 3~6개월로 단축하여 재시도\n"
                "  3. 타임프레임 변경 검토 (30m → 15m 또는 1h)"
            )
        }

    # 거래 수 부족이 근본 원인인지 먼저 확인 (통계적 유의성)
    # min_trades를 높게 설정한 경우 이 체크가 LOW_FEATURE_DIVERSITY보다 우선해야 정확한 진단이 가능
    if avg_trades < min_trades:
        return {
            "code": "SAMPLE_SHORTAGE",
            "title": "통계적 샘플 부족",
            "message": f"평균 거래 수: {avg_trades:.1f}회 (기준: {min_trades}회 이상)",
            "prescription": (
                "테스트 구간 내 거래가 너무 적어 통계적 신뢰성이 없습니다.\n"
                "권장 조치:\n"
                "  1. PA(예측 선행 캔들) 범위 단축\n"
                "  2. buy_threshold 낮추기 (0.5 방향)\n"
                "  3. cooldown_minutes 줄이기"
            )
        }

    # 동일 결과 반복 감지 (피처 다양성 부족)
    unique_profits = set(round(p, 4) for p in profit_list)
    unique_feature_sets = set(
        tuple(sorted(t.get("features", []))) for t in trial_history
    )
    if len(unique_feature_sets) <= 1 and min_screening <= ic_pass_count < 10:
        return {
            "code": "LOW_FEATURE_DIVERSITY",
            "title": "피처 다양성 부족",
            "message": (
                f"모든 Trial이 동일한 피처 조합({len(unique_feature_sets)}종)으로 "
                f"반복되었고, IC 통과 지표는 {ic_pass_count}개에 불과합니다."
            ),
            "prescription": (
                "IC 스크리닝 통과 지표가 적어 탐색 다양성이 확보되지 않았습니다.\n"
                "권장 조치:\n"
                "  1. IC 통과 기준 완화 ( |IC| ≥ 0.02 → 0.015 )\n"
                "  2. 학습 기간 최신화로 지표 예측력 회복\n"
                "  3. 신규 데이터 소스 추가 (펀딩비, 청산 데이터 등)"
            )
        }

    if avg_mdd > 20.0:
        return {
            "code": "POOR_RISK_REWARD",
            "title": "손익비 구조적 불리",
            "message": f"평균 MDD: {avg_mdd:.1f}% (기준: 20% 이하)",
            "prescription": (
                "손절이 너무 크거나 빈번하여 구조적으로 수익을 낼 수 없습니다.\n"
                "권장 조치:\n"
                "  1. ATR 동적 손익비 적용 (TP=2×ATR, SL=1×ATR)\n"
                "  2. stop_loss 범위 조정\n"
                "  3. 진입 신호 강화"
            )
        }

    if -2.0 <= median_profit <= 2.0:
        return {
            "code": "REGIME_MISMATCH",
            "title": "시장 체제 불일치",
            "message": f"순수익 중간값: {median_profit:+.1f}% (횡보 구간)",
            "prescription": (
                "학습 구간과 테스트 구간의 시장 체제가 일치하지 않습니다.\n"
                "권장 조치:\n"
                "  1. 학습 기간을 최근 데이터로 최신화\n"
                "  2. 스냅샷 데이터를 최신 구간으로 다시 다운로드\n"
                "  3. 봉길이 변경 검토"
            )
        }

    return {
        "code": "UNKNOWN",
        "title": "원인 미분류",
        "message": f"평균 거래:{avg_trades:.0f}, MDD:{avg_mdd:.1f}%, 수익 중간값:{median_profit:+.1f}%",
        "prescription": (
            "복합적 원인이 의심됩니다.\n"
            "권장 조치:\n"
            "  1. max_trials를 늘려 추가 탐색\n"
            "  2. 목표 기준값 완화 (min_profit 0.0, max_mdd 15.0)\n"
            "  3. 학습 구간 최신화 후 재시도"
        )
    }


def _print_diagnosis(diagnosis: dict, log_fn: Callable):
    _sync_log(log_fn, "\n" + "="*60)
    _sync_log(log_fn, "📋 [자동 진단 리포트]")
    _sync_log(log_fn, "="*60)
    _sync_log(log_fn, f"진단 결과: [{diagnosis.get('code')}] {diagnosis.get('title')}")
    _sync_log(log_fn, f"판단 근거: {diagnosis.get('message')}")
    _sync_log(log_fn, f"\n처방:\n{diagnosis.get('prescription')}")
    _sync_log(log_fn, "="*60)


def _save_best_feature_config(feature_subset: list, val_result: dict, log_fn: Callable,
                              assessment: str = "BEST_EFFORT"):
    try:
        fc = load_feature_config()
        # 탐색 대상 피처 풀(enabled_features)은 원본 보존하고, 모델 실행용 지표만 active_features에 저장
        fc["active_features"] = feature_subset
        fc["_asrl_result"] = {
            "saved_at": datetime.now().isoformat(),
            "assessment": assessment,
            "net_profit": val_result.get("net_profit", 0),
            "mdd": val_result.get("mdd", 0),
            "trades": val_result.get("trades", 0),
        }
        save_feature_config(
            enabled_features=fc.get("enabled_features", []),
            feature_params=fc.get("feature_params", {}),
            optuna_config=fc.get("optuna_config"),
            active_features=feature_subset,
            feature_penalties=fc.get("feature_penalties"),
        )
        _sync_log(log_fn, f"💾 피처 설정 자동 저장 완료: Active={feature_subset} (Enabled 풀 보존) [{assessment}]")
    except Exception as e:
        _sync_log(log_fn, f"⚠️  피처 설정 저장 실패: {e}")


# ---------------------------------------------------------------------------
# 메인 오케스트레이터
# ---------------------------------------------------------------------------
def run_auto_research(
    config: dict,
    target_criteria: dict,
    max_trials: int = 50,
    holdout_start: str = "",
    holdout_end: str = "",
    log_fn: Callable = print,
    broadcast_fn: Optional[Callable] = None,
    ic_threshold: float = 0.02,
    min_features: int = 4,
    max_features: int = 10,
    complexity_penalty: float = 0.15,
    final_test_ratio: int = 20,
    use_all_features: bool = False,
    single_run: bool = False,
    holdout_mode: str = "full",
    final_test_td: Optional[int] = None,
):
    """
    ASRL v2 메인 실행 함수 — 프로 표준 3-Phase.

    Phase 1: 전체 후보 피처 조합 탐색 (WFA 최적화 + Validation 홀드아웃)
    Phase 2: Validation 결과 중 최고 성과 조합 선정 (복잡도 패널티 적용)
    Phase 3: 최종 Test (미사용 최근 구간 1회) → 통과 시 배포 저장 / 미달 시 Best-effort 보존

    Args:
        config: 현재 active_config
        target_criteria: {"min_profit": 0.0, "min_trades": 10, "max_mdd": 10.0}
        max_trials: 최대 시도 횟수
        holdout_start: 홀드아웃 시작일 (YYYY-MM-DD)
        holdout_end: 홀드아웃 종료일 (YYYY-MM-DD)
        log_fn: 로그 출력 함수
        broadcast_fn: WebSocket 브로드캐스트 함수 (선택)
        ic_threshold: IC 통과 기준 (기본 0.02)
        min_features: 최소 지표 수 (기본 4)
        max_features: 최대 지표 수 (기본 10)
        complexity_penalty: 복잡도 패널티 %/지표 (기본 0.15)
        final_test_ratio: 최종 Test 비율 % (기본 20)
        use_all_features: True면 Settings 선택과 무관하게 전체 레지스트리 탐색 (기본 False = 선택 지표만)
    """
    global _research_state

    start_time_dt = datetime.now()
    symbol = get_target_symbol(config)
    coin_code = symbol.split('-')[-1]
    interval = config.get('INTERVAL', '15m')
    session_id = config.get('ACTIVE_SESSION_ID') or config.get('SESSION_ID') or f"{symbol.replace('KRW-', '')}_{interval}_AUTO"
    # ── 프로젝트 격리: 실행 1회 = runs/<시각>/ 폴더 1개 ──
    # 종전에는 프로젝트 루트의 leaderboard/trials/candidates 를 지우고 다시 썼고, 학습 엔진은 루트 model/ 을
    # 매 trial 덮어썼다. 탈락한 실행 뒤에도 model/ 에 마지막 재학습 후보가 남아 '실전 배포용' 으로 보였다.
    # 이제 이번 실행의 모든 산출물은 run 폴더에만 쓰고, 루트 model/ 은 합격(챔피언)일 때만 교체한다.
    project_ref = project_store.make_ref(project_store.market_dir_for(symbol, interval), session_id)
    session_dir = project_store.project_dir(project_ref)          # 프로젝트 루트 (champion model/ 자리)
    os.makedirs(session_dir, exist_ok=True)
    run_dir = project_store.new_run_dir(project_ref)
    config = dict(config)
    config[project_store.RUN_DIR_KEY] = run_dir                    # 학습 엔진·백테스터가 run/work 에 쓰게 함
    work_dir = project_store.artifact_dir(config, session_id)     # runs/<시각>/work
    os.makedirs(os.path.join(run_dir, "trials"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "candidates"), exist_ok=True)

    # 로그는 서버가 run 폴더에 직접 기록한다 (화면 전송은 그대로)
    _orig_log_fn = log_fn
    log_fn = project_store.tee_log(lambda m: _sync_log(_orig_log_fn, m),
                                   os.path.join(run_dir, project_store.RUN_LOG_FILE), truncate=True)
    try:
        with open(os.path.join(run_dir, "config_used.json"), "w", encoding="utf-8") as _cf:
            json.dump(project_store.strip_runtime_keys(config), _cf, indent=4, ensure_ascii=False)
        _write_run_result(run_dir, {"status": "RUNNING", "project": project_ref,
                                    "started_at": start_time_dt.strftime("%Y-%m-%d %H:%M:%S")})
    except Exception:
        pass

    with _research_lock:
        _research_state.update({
            "running": True,
            "current_trial": 0,
            "max_trials": max_trials,
            "best_profit": 0.0,
            "best_trial": -1,
            "best_features": [],
            "start_time": start_time_dt.isoformat(),
            "status": "screening",
            "trial_history": [],
            "leaderboard": [],
            "diagnosis": None,
            "abort": False,
        })

    try:
        _sync_log(log_fn, "\n" + "="*60)
        _sync_log(log_fn, "🤖 [ASRL v2] 자동화 전략 연구 루프 시작")
        _sync_log(log_fn, "="*60)
        _sync_log(log_fn, f"   세션 ID         : {session_id}")
        _sync_log(log_fn, f"   대상 자산       : {symbol} ({interval})")
        _sync_log(log_fn, f"   최대 시도 횟수  : {max_trials}회")
        _sync_log(log_fn, f"   목표 순수익     : >= {target_criteria.get('min_profit', 0.0)}%")
        _sync_log(log_fn, f"   목표 거래 빈도  : >= 월 {target_criteria.get('min_trades', 4)}회")
        _sync_log(log_fn, f"   목표 MDD        : <= {target_criteria.get('max_mdd', 10.0)}%")
        _sync_log(log_fn, f"   홀드아웃 구간   : {holdout_start} ~ {holdout_end}")

        # 텔레그램 시작 알림 발송
        _send_telegram_notification(
            f"🤖 <b>[ASRL v2] 자동화 연구 시작</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"• 세션: <code>{session_id}</code>\n"
            f"• 자산: <b>{symbol}</b> ({interval})\n"
            f"• 최대 Trials: {max_trials}회\n"
            f"• 목표 수익률: >= {target_criteria.get('min_profit', 0.0)}%\n"
            f"• 목표 거래빈도: >= 월 {target_criteria.get('min_trades', 4)}회\n"
            f"• 목표 MDD: <= {target_criteria.get('max_mdd', 10.0)}%\n"
            f"• 홀드아웃: {holdout_start or '자동'} ~ {holdout_end or '자동'}"
        )

        # 스냅샷 로딩 (1회만)
        snapshot_path = get_snapshot_path(config)
        if not os.path.exists(snapshot_path):
            raise FileNotFoundError(f"스냅샷 파일 없음: {snapshot_path}")

        _sync_log(log_fn, f"\n📂 스냅샷 로딩: {snapshot_path}")
        # 해시와 학습 데이터가 같은 바이트임을 보장하려고 한 번 읽어 둘 다 여기서 만든다.
        import io as _io
        from shared.data_library.manifest_manager import ManifestManager
        with open(snapshot_path, "rb") as _sf:
            _snap_bytes = _sf.read()
        _dataset_fp = ManifestManager.dataset_fingerprint(snapshot_path, content=_snap_bytes)
        config["_DATASET"] = _dataset_fp
        _write_run_result(run_dir, {"dataset": _dataset_fp})
        _sync_log(log_fn, f"   🔏 데이터 지문 sha256={_dataset_fp['sha256'][:16]}… "
                          f"({_dataset_fp['byte_size']:,} bytes, 매니페스트 {_dataset_fp['manifest_created_at']})")
        if _dataset_fp["matches_manifest"] is False:
            _sync_log(log_fn, "   ⚠️ [데이터 지문] 스냅샷이 매니페스트 기록 이후 바뀌었습니다 "
                              f"(매니페스트 {str(_dataset_fp['manifest_sha256'])[:16]}…) — 빌드 경로 밖에서 파일이 수정됐을 수 있습니다.")
        df_full = pd.read_csv(_io.BytesIO(_snap_bytes))
        del _snap_bytes
        df_full = df_full.sort_values('timestamp').reset_index(drop=True)
        df_full['timestamp'] = pd.to_datetime(df_full['timestamp'])

        # 📌 [피처 연산 스마트 슬라이싱]: 불필요한 과거 수백만 행 전체 연산 대신 웜업(Warmup) 포함 구간만 연산
        optuna_cfg = config.get("OPTUNA_CONFIG", {})
        _train_start_cfg = optuna_cfg.get("wfa_start_date") or config.get("TRAIN_START", "")
        if _train_start_cfg:
            try:
                _ts_dt = pd.to_datetime(_train_start_cfg)
                # 최대 지표 윈도우(일봉 14일 RSI 등)를 100% 만족하는 20일치 웜업 캔들 확보
                _warmup_dt = _ts_dt - pd.Timedelta(days=20)
                if _warmup_dt > df_full['timestamp'].min():
                    _orig_len = len(df_full)
                    df_full = df_full[df_full['timestamp'] >= _warmup_dt].copy().reset_index(drop=True)
                    _sync_log(log_fn, f"⚡ [스마트 슬라이싱] 불필요한 과거 데이터 제외 & 20일 웜업 적용 ({_orig_len:,}행 → {len(df_full):,}행)")
            except Exception as _slice_e:
                pass

        _sync_log(log_fn, f"⚡ [피처 엔지니어링] 멀티코어 지표 병렬 연산 가동 중 ({len(df_full):,}행)...")
        _broadcast_progress(broadcast_fn, {"type": "auto_research_progress", "step": "feature_engineering", "total_rows": len(df_full)})

        # [탐색 풀 단일 결정] 컬럼을 만드는 쪽(피처 엔지니어링)과 후보를 고르는 쪽(IC 스크리닝)이
        # 각자 판단하면 어긋난다: '전체 지표 탐색'을 켜도 엔지니어링은 저장된 선택 지표만 계산해
        # 컬럼이 없는 지표는 후보에서 조용히 빠졌다(전체 88개 중 5개만 평가됨). 여기서 한 번 정하고
        # 양쪽이 같은 목록을 쓴다.
        from shared.features.feature_registry import register_configured_reference_features
        register_configured_reference_features(config)
        search_feature_config = dict(load_feature_config())
        if use_all_features or not search_feature_config.get("enabled_features"):
            _all_pool = [fid for fid, defn in DEFAULT_FEATURE_REGISTRY.items()
                         if not defn.get("is_alias", False)]
            search_feature_config["enabled_features"] = _all_pool
            search_feature_config["active_features"] = _all_pool

        # 자산군이 다른 지표는 탐색 풀에서 빼낸다 (레지스트리의 market_scope 선언이 유일한 근거).
        # 대시보드는 이 검사를 해 왔지만 연구 루프는 하지 않아, 암호화폐 전용
        # `binance_funding_settle_window` 가 KRX ETF 챔피언의 배포 규격에까지 들어갔다 (2026-09-28).
        from shared.features.feature_registry import get_incompatible_features
        _pool = list(search_feature_config.get("enabled_features") or [])
        _bad = {x["feature_id"]: x for x in get_incompatible_features(symbol, _pool, session_id=session_id)}
        if _bad:
            _kept = [f for f in _pool if f not in _bad]
            search_feature_config["enabled_features"] = _kept
            search_feature_config["active_features"] = [
                f for f in (search_feature_config.get("active_features") or _kept) if f not in _bad]
            _scope = next(iter(_bad.values()))["symbol_scope"]
            _sync_log(log_fn, f"🧭 [자산군 적합성] {symbol}({_scope}) 에 맞지 않는 지표 {len(_bad)}개를 "
                              f"탐색에서 제외: {', '.join(sorted(_bad)[:10])}"
                              + (" ..." if len(_bad) > 10 else ""))

        df_full = run_feature_engineering(df_full, interval,
                                          feature_config=search_feature_config, config=config)
        _sync_log(log_fn, f"✅ 피처 엔지니어링 완료! ({len(df_full.columns)}개 컬럼)")

        # 🔎 [데이터 품질 게이트] 세밀한 원천(틱)을 상위 원천(1분봉)과 대조해 신뢰할 수 없는 날을 학습·검증·홀드아웃에서 제외.
        #    피처는 위에서 연속 데이터로 이미 계산했고, WFA 창은 실제 존재하는 거래일 기준이라 빠진 날은 자연스럽게 건너뛴다.
        #    (2026-09-24: 틱이 실제의 ~10%만 기록된 23/46일이 학습 행의 절반을 차지해 거래수 계열 피처가 수집 상태에 따라 요동쳤다)
        from shared.data_library.data_quality import apply_quality_gate
        from shared.core.market_utils import parse_interval_minutes as _pim
        df_full, _dq_report = apply_quality_gate(
            df_full, get_target_symbol(config), _pim(interval) * 60.0, log_fn=lambda m: _sync_log(log_fn, m),
            enabled=bool(config.get("DATA_QUALITY_GATE", True)))
        _research_state["data_quality"] = {k: v for k, v in _dq_report.items() if k != "kept_rows"}

        # ⚖️ [물리 선험 제약 엔진] 마찰력과 변동성 기반으로 비물리적 탐색 범위 사전 클램핑
        try:
            from .physics_constraint import calc_mean_atr_pct, apply_physics_constraints_to_config, slice_pre_holdout
        except (ImportError, ValueError):
            from core.physics_constraint import calc_mean_atr_pct, apply_physics_constraints_to_config, slice_pre_holdout

        # [FIX-2] 물리 제약(ATR/TP·SL·horizon 범위)은 홀드아웃 이전 학습 구간만으로 산출 (홀드아웃 누출 차단)
        if holdout_start:
            _phys_df = slice_pre_holdout(df_full, holdout_start)
        else:
            _phys_df = df_full.iloc[:int(len(df_full) * 0.8)]  # 아래 자동 홀드아웃(80/20)과 동일 경계
        mean_atr = calc_mean_atr_pct(_phys_df)
        config = apply_physics_constraints_to_config(config, mean_atr_pct=mean_atr, df=_phys_df)
        phys = config.get("PHYSICS_CONSTRAINTS", {})
        _sync_log(log_fn, f"\n⚖️ [물리 선험 제약] ATR: {phys.get('mean_atr_pct', 0.0):.3f}% | 마찰비용: {phys.get('roundtrip_friction_pct', 0.0):.3f}% | 상태: {phys.get('friction_status')} (방법: {phys.get('method', 'analytical')})")
        _sync_log(log_fn, f"   → {phys.get('recommendation')}")
        _sync_log(log_fn, f"   → 물리 권장 범위: TP {phys.get('tp_atr_min')}~{phys.get('tp_atr_max')} ATR | SL {phys.get('sl_atr_min')}~{phys.get('sl_atr_max')} ATR | 지평 {phys.get('min_horizon_candles')}~{phys.get('max_hold_candles')}캔들")
        # 실제로 쓰이는 범위를 함께 보여 준다 — 사용자 설정·지평 잠금이 권장 범위와 다르면 권장 범위는 적용되지 않는다
        _oc = config.get("OPTUNA_CONFIG", {}) or {}
        _thc = config.get("TARGET_HORIZON_CONFIG", {}) or {}
        _tp = _oc.get("take_profit_range", {}) or {}
        _sl = _oc.get("stop_loss_range", {}) or {}
        _hz = (f"잠금 {_thc.get('locked_predict_ahead')}/{_thc.get('locked_max_hold')}캔들"
               if _thc.get("mode", "locked") == "locked" else f"후보 {_thc.get('candidate_horizons')}")
        _sync_log(log_fn, f"   → 실제 탐색 범위: TP {_tp.get('user_min')}~{_tp.get('user_max')} ATR | "
                          f"SL {_sl.get('user_min')}~{_sl.get('user_max')} ATR | 지평 {_hz}")
        if _thc.get("mode", "locked") == "locked" and int(_thc.get("locked_predict_ahead") or 0) < int(phys.get("min_horizon_candles") or 0):
            _sync_log(log_fn, f"   ⚠️ 잠긴 지평 {_thc.get('locked_predict_ahead')}캔들이 물리 최소 지평 {phys.get('min_horizon_candles')}캔들보다 짧습니다 "
                              f"— 이 지평에서는 평균 가격 이동이 마찰비용을 넘기 어렵습니다.")

        # 🔎 [무거래 노출] 거래가 없는 봉은 가격을 이어 붙인 합성 봉이고, 거래량 기반 지표(MFI·VWAP·체결빈도 등)는
        #    그 구간에서 0/1 같은 상수를 낸다. 진입 불가 시간대는 학습에서 빠지지만, 의사결정 시점에도 무거래가 잦으면
        #    (저유동성 종목) 모델이 '지어낸 값' 을 배운다. 지표를 고치기 전에 노출량부터 잰다.
        #    (2026-10-01 실측: 069500 10s 의사결정 시점의 0.375%, 1m 0% — 이 수준이면 지표 계약 변경은 불필요)
        _vol_col = 'target_volume' if 'target_volume' in df_full.columns else ('upbit_volume' if 'upbit_volume' in df_full.columns else None)
        if _vol_col:
            _intraday_krx_flag = detect_market_type(config) == 'KRX' and bool(config.get('INTRADAY_SQUARE_OFF', True))
            from shared.core.market_utils import decision_point_mask as _dpm
            _dm = _dpm(df_full['timestamp'], _intraday_krx_flag)
            # 창 = 라벨 지평(이 봉 수 안의 움직임을 예측한다). 잠금 지평이 없으면 물리 제약의 최소 지평.
            _thc = config.get("TARGET_HORIZON_CONFIG", {}) or {}
            _win = int(_thc.get("locked_predict_ahead") or (config.get("PHYSICS_CONSTRAINTS", {}) or {}).get("min_horizon_candles") or 1)
            _no_trade = (pd.to_numeric(df_full[_vol_col], errors='coerce').rolling(_win, min_periods=_win).sum() == 0).to_numpy()
            _share = float((_no_trade & _dm).sum() / max(int(_dm.sum()), 1))
            _research_state["no_trade_exposure"] = round(_share, 5)
            if _share > NO_TRADE_EXPOSURE_WARN:
                _sync_log(log_fn, f"⚠️ [무거래 노출] 의사결정 시점의 {_share:.1%} 가 직전 {_win}봉 무거래입니다 (경고선 {NO_TRADE_EXPOSURE_WARN:.0%}) — "
                                  f"거래량 기반 지표가 이 구간에서 상수(0/1)를 내므로 모델이 합성 값을 배울 수 있습니다. "
                                  f"더 긴 인터벌을 쓰거나 거래량 기반 지표를 빼는 것을 검토하세요.")

        # 탐색용 / 홀드아웃 분리
        if holdout_start:
            df_train = df_full[df_full['timestamp'] < pd.to_datetime(holdout_start)].copy()
        else:
            split_idx = int(len(df_full) * 0.8)
            df_train = df_full.iloc[:split_idx].copy()
            holdout_start = pd.Timestamp(df_full.iloc[split_idx]['timestamp']).strftime('%Y-%m-%d')
            holdout_end = pd.Timestamp(df_full.iloc[-1]['timestamp']).strftime('%Y-%m-%d')
            _sync_log(log_fn, f"ℹ️  홀드아웃 자동 설정: {holdout_start} ~ {holdout_end}")

        # 📌 [학습 기간 단축 및 WFA 시작점 적용]
        # WFA 시작일(wfa_start_date) 또는 TRAIN_START 이후 데이터만 탐색에 사용
        if _train_start_cfg and len(df_train) > 0:
            try:
                _ts_dt = pd.to_datetime(_train_start_cfg)
                _holdout_dt = pd.to_datetime(holdout_start) if holdout_start else df_full['timestamp'].max()
                
                # _ts_dt가 홀드아웃 시작일보다 이전이고 데이터가 유효한 경우에만 필터링
                if _ts_dt < _holdout_dt and _ts_dt > df_train['timestamp'].min():
                    _filtered_train = df_train[df_train['timestamp'] >= _ts_dt].copy()
                    if len(_filtered_train) >= 100:
                        _before_n = len(df_train)
                        df_train = _filtered_train
                        _sync_log(log_fn, f"   📌 학습 기간 단축: {_train_start_cfg} 이후만 사용 ({_before_n:,}행 → {len(df_train):,}행)")
                    else:
                        _sync_log(log_fn, f"   ℹ️ TRAIN_START({_train_start_cfg}) 필터 시 데이터 부족으로 전체 WFA 데이터({len(df_train):,}행) 사용")
                else:
                    _sync_log(log_fn, f"   ℹ️ WFA 탐색 데이터 정상 유지: {len(df_train):,}행")
            except Exception as _e:
                _sync_log(log_fn, f"   ⚠️ TRAIN_START 필터 예외 처리: {_e}")

        _sync_log(log_fn, f"   탐색 데이터: {len(df_train):,}행")
        _sync_log(log_fn, f"   홀드아웃:    {len(df_full) - len(df_train):,}행")

        # [Phase 3 대비] 최종 Test 구간을 먼저 예약 (Phase 1 Validation에서 미사용)
        try:
            df_holdout = df_full[df_full['timestamp'] >= pd.to_datetime(holdout_start)].copy()
            if len(df_holdout) > 0:
                h_mode = str(holdout_mode).lower().strip() if holdout_mode else ("split" if final_test_ratio < 100 else "full")
                if h_mode == "full":
                    # 단일 순수 홀드아웃 (100% OOS 검증 — 프로 퀀트 표준)
                    final_test_start = holdout_start
                    final_test_end = holdout_end
                    val_end_str = holdout_end
                    _sync_log(log_fn, f"🔒 [단일 순수 홀드아웃] 최종 Test 구간: {final_test_start} ~ {final_test_end} (100% OOS 검증)")
                else:
                    # 고정 거래일 2단계 분할 모드
                    holdout_dates = sorted(pd.to_datetime(df_holdout['timestamp']).dt.date.unique())
                    f_td_req = int(final_test_td) if (final_test_td and int(final_test_td) > 0) else int(HOLDOUT_MIN_TRADING_DAYS)
                    if len(holdout_dates) > int(HOLDOUT_MIN_TRADING_DAYS):
                        f_td = max(int(HOLDOUT_MIN_TRADING_DAYS), min(f_td_req, len(holdout_dates) - 1))
                        final_test_start = str(holdout_dates[-f_td])
                        final_test_end = holdout_end
                        val_end_str = str(holdout_dates[-f_td - 1])
                    elif len(holdout_dates) > 1:
                        f_td = max(1, min(f_td_req, len(holdout_dates) - 1))
                        final_test_start = str(holdout_dates[-f_td])
                        final_test_end = holdout_end
                        val_end_str = str(holdout_dates[-f_td - 1])
                    else:
                        test_n = max(1, int(len(df_holdout) * final_test_ratio / 100))
                        final_test_start = pd.Timestamp(df_holdout.iloc[-test_n]['timestamp']).strftime('%Y-%m-%d')
                        final_test_end = holdout_end
                        val_end_dt = pd.to_datetime(final_test_start) - pd.Timedelta(minutes=1)
                        val_end_str = pd.Timestamp(val_end_dt).strftime('%Y-%m-%d')
                    _sync_log(log_fn, f"🔒 [2단계 분할] 최종 Test 구간: {final_test_start} ~ {final_test_end}")
                    _sync_log(log_fn, f"   → Validation 구간: {holdout_start} ~ {val_end_str} (최종 Test 구간 미사용)")
            else:
                final_test_start = holdout_start
                final_test_end = holdout_end
                val_end_str = holdout_end
        except Exception as e:
            _sync_log(log_fn, f"⚠️  최종 Test 구간 계산 실패: {e} — 전체 홀드아웃 사용")
            final_test_start = holdout_start
            final_test_end = holdout_end
            val_end_str = holdout_end

        # 트라이얼 선택(Validation) 구간과 최종 Test 구간이 겹치면 최종 성과는 진짜 OOS가 아니다.
        # (다수 트라이얼 중 이 구간 성과가 가장 좋은 것을 고르므로 승자의 저주로 과대평가된다)
        oos_independent = str(val_end_str) != str(final_test_end)
        if not oos_independent:
            _sync_log(
                log_fn,
                "⚠️  [OOS 경고] Validation(트라이얼 선택)과 최종 Test가 동일 구간입니다. "
                "선택 편향으로 보고 성과가 과대평가될 수 있으며 독립 OOS가 아닙니다. "
                "독립 검증이 필요하면 holdout_mode='split'(고정 거래일 2단계 분할)을 사용하세요."
            )

        # STEP 1: IC 스크리닝 (1회 실행 — 전체 Trial에서 재사용)
        ctx = get_interval_context(interval)
        target_horizon_cfg = config.get('TARGET_HORIZON_CONFIG', {})
        is_horizon_locked = (target_horizon_cfg.get('mode', 'locked') == 'locked')
        pa_range_cfg = config.get('OPTUNA_CONFIG', {}).get('predict_ahead_candles_range', {})
        pa_min = pa_range_cfg.get('user_min', pa_range_cfg.get('min', 8))
        pa_max = pa_range_cfg.get('user_max', pa_range_cfg.get('max', 32))

        if is_horizon_locked and target_horizon_cfg.get('locked_predict_ahead'):
            forward_candles = int(target_horizon_cfg.get('locked_predict_ahead', 6))
            locked_mhc = int(target_horizon_cfg.get('locked_max_hold', forward_candles))
            pa_max = max(forward_candles, locked_mhc)
            _sync_log(log_fn, f"🔒 [Target Horizon] IC 스크리닝 타겟: {forward_candles}캔들 (사정거리 잠금 모드 기준, Purge: {pa_max}캔들)")
        else:
            forward_candles = int((pa_min + pa_max) / 2)

        # 탐색 대상은 위에서 이미 정해졌다 (엔지니어링이 계산한 목록과 동일해야 한다)
        feature_config = search_feature_config
        enabled = feature_config.get("enabled_features", [])
        user_regime_config = feature_config.get("regime_features", {})
        if single_run:
            _sync_log(log_fn, "\n🎯 [선택 지표 단일 실행 모드 (1-Trial)]")
            selected_features = [fid for fid in enabled if fid in df_train.columns]
            if not selected_features:
                selected_features = [fid for fid, defn in DEFAULT_FEATURE_REGISTRY.items()
                                     if not defn.get("is_alias", False) and fid in df_train.columns][:4]
            _sync_log(log_fn, f"   사용자 선택 지표 ({len(selected_features)}개): {', '.join(selected_features)}")
            ic_ranked = [(fid, 1.0) for fid in selected_features if not _is_regime_feature(fid, user_regime_config)]
            regime_features = [fid for fid in selected_features if _is_regime_feature(fid, user_regime_config)]
            ic_pass_count = len(selected_features)
            _broadcast_progress(broadcast_fn, {
                "type": "auto_research_progress",
                "step": "ic_screening_done",
                "ic_results": [{"feature": fid, "ic": 1.0, "is_regime": _is_regime_feature(fid, user_regime_config)} for fid in selected_features],
                "pass_count": ic_pass_count
            })
        else:
            all_feature_ids = [fid for fid in enabled if fid in df_train.columns]
            _missing = [fid for fid in enabled if fid not in df_train.columns]
            if use_all_features:
                _sync_log(log_fn, f"\n🌐 전체 지표 탐색 모드: {len(all_feature_ids)}개 지표 (레지스트리 {len(enabled)}개 중 컬럼 생성 성공)")
            else:
                _sync_log(log_fn, f"\n⚙️ Settings 선택 지표 탐색: {len(all_feature_ids)}개 / 선택 {len(enabled)}개")
            if _missing:
                # 조용히 빠지면 "왜 그 지표를 안 잡나"를 IC 표에서 영원히 찾을 수 없다
                _sync_log(log_fn, f"   ⚠️ 데이터가 없어 계산되지 않은 지표 {len(_missing)}개: "
                                  f"{', '.join(_missing[:12])}{' ...' if len(_missing) > 12 else ''}")

            ic_ranked, regime_features = _run_ic_screening(df_train, forward_candles, all_feature_ids, log_fn,
                                                           ic_threshold=ic_threshold,
                                                           min_features=min_features,
                                                           broadcast_fn=broadcast_fn,
                                                           user_regime_config=user_regime_config,
                                                           intraday_krx=(detect_market_type(config) == 'KRX'
                                                                         and bool(config.get('INTRADAY_SQUARE_OFF', True))),
                                                           recent_df=df_full[df_full['timestamp'] >= pd.to_datetime(holdout_start)])
            ic_pass_count = len(ic_ranked) + len(regime_features)

            all_screened_results = [{"feature": fid, "ic": round(ic, 5), "is_regime": False} for fid, ic in ic_ranked] + \
                                   [{"feature": fid, "ic": 1.0, "is_regime": True} for fid in regime_features]

            _broadcast_progress(broadcast_fn, {
                "type": "auto_research_progress",
                "step": "ic_screening_done",
                "ic_results": all_screened_results,
                "pass_count": ic_pass_count
            })

            # ⚡ [과제 3] 소수 지표 적응: 가용 알파 풀 크기 및 체제 지표 유무에 맞춰 최소 통과 요구 기준 동적 적응
            n_avail_alpha = max(1, len(all_feature_ids) - len(regime_features))
            effective_min_screening = max(1, min(min_features, n_avail_alpha))

            if len(ic_ranked) < effective_min_screening:
                _sync_log(log_fn, f"\n❌ 알파 통과 지표가 {effective_min_screening}개 미만 — 탐색 중단")
                diagnosis = diagnose_failure([{"ic_pass_count": len(ic_ranked)}],
                                             min_features=effective_min_screening)
                with _research_lock:
                    _research_state["status"] = "failed"
                    _research_state["diagnosis"] = diagnosis
                    _research_state["running"] = False
                _print_diagnosis(diagnosis, log_fn)
                _broadcast_progress(broadcast_fn, {"type": "auto_research_done", "status": "failed", "diagnosis": diagnosis})
                return

        # [지표 파라미터 사전 튜닝 비활성화]
        # 과거 데이터 노이즈 과적합(Data Snooping) 방지: 정규화된 표준 지표 파라미터 규격을 그대로 유지
        _sync_log(log_fn, "\nℹ️ [지표 파라미터] 표준 규격 파라미터(시간/봉수 고정 스케일)를 유지하며 연구를 진행합니다.")

        # STEP 2 & 3: 피처 조합별 최적화 + 홀드아웃 검증 반복
        # [프로 표준 Phase 1] — 목표 달성 즉시 중단하지 않고 전체 후보 탐색 (Validation)
        trial_history = []
        seen_combos = set()  # 이미 탐색한 피처 조합 추적 (중복 실행 방지)

        # 📌 IC 통과 피처 수가 적을 때 중복 조합으로 Trial이 낭비되지 않도록
        #    실제 생성 가능한 고유 조합 수를 사전 계산하여 max_trials를 상한 제한한다.
        n_ic = len(ic_ranked)
        if single_run:
            max_possible_trials = 1
        elif n_ic <= 3:
            # ⚡ [과제 3] 소수 정예 알파 지표 (1~3개): 비어있지 않은 모든 부분집합(2^n - 1)
            theoretical = (1 << n_ic) - 1
            max_possible_trials = min(max_trials, max(1, theoretical))
        elif n_ic <= max_features:
            # 통과 피처 수가 max_features 이하: growth(1~n) + leave-one-out + OI제거/카테고리 우선 등
            #   가능한 고유 조합 = n + 1 (growth) + 1 (OI제거) + 1 (카테고리) + n (LOO) + C(n,2) (pairwise)
            n_pairs = (n_ic * (n_ic - 1)) // 2 if n_ic >= min_features + 2 else 0
            theoretical = n_ic + 1 + 1 + 1 + n_ic + n_pairs
            max_possible_trials = min(max_trials, max(1, theoretical))
        else:
            max_possible_trials = max_trials

        if not single_run and max_possible_trials < max_trials and max_possible_trials > 0:
            _sync_log(log_fn, f"ℹ️  알파 통과 지표 {n_ic}개 기준 최대 가능한 고유 Trial 수: {max_possible_trials} (기존 {max_trials} → {max_possible_trials}로 제한)")

        for trial_idx in range(max_possible_trials):
            if _research_state["abort"]:
                _sync_log(log_fn, "\n🛑 사용자가 자동 연구를 중단했습니다.")
                break

            with _research_lock:
                _research_state["current_trial"] = trial_idx + 1
                _research_state["status"] = "optimizing"

            if single_run:
                feature_subset = selected_features
            else:
                feature_subset = _build_feature_combinations(
                    ic_ranked, trial_idx, min_features=min_features, max_features=max_features,
                    regime_features=regime_features
                )
            if not feature_subset:
                _sync_log(log_fn, f"⚠️  Trial {trial_idx+1}: 유효한 피처 조합 없음, 건너뜀")
                continue

            # 중복 피처 조합 건너뛰기 (동일 최적화 반복으로 시간 낭비 방지)
            combo_key = tuple(sorted(feature_subset))
            if combo_key in seen_combos:
                _sync_log(log_fn, f"⚠️  Trial {trial_idx+1}: 이미 탐색한 피처 조합입니다. 건너뜁니다.")
                continue
            seen_combos.add(combo_key)

            # 백본 유무는 조합에서 유도한다 (별도 상태를 들고 다니면 로그와 실제가 어긋난다)
            _bb_in = [f for f in (regime_features or []) if f in feature_subset]
            if _bb_in:
                _bb_tag = f" · 체제 백본 {len(_bb_in)}개"
            elif regime_features:
                _bb_tag = " · 체제 백본 제외(대조군)"      # 통과한 백본이 있는데 이 Trial 만 뺐다
            else:
                _bb_tag = " · 통과한 체제 백본 없음"        # 애초에 붙일 백본이 없다
            _sync_log(log_fn, f"\n{'='*60}")
            _sync_log(log_fn, f"🔬 [Trial {trial_idx+1}/{max_trials}] 피처 조합:{_bb_tag}")
            for f in feature_subset:
                _sync_log(log_fn, f"     - {f}")
            _sync_log(log_fn, f"{'='*60}")

            _broadcast_progress(broadcast_fn, {
                "type": "auto_research_progress",
                "step": "trial_start",
                "trial": trial_idx + 1,
                "max_trials": max_trials,
                "features": feature_subset,
            })

            # STEP 2: 최적화 (홀드아웃 경계 전달 — WFA가 홀드아웃을 침범하지 않도록, 메모리 캐시 주입)
            ok = _run_optimization(config, feature_subset, log_fn,
                                   holdout_start=holdout_start,
                                   purge_candles=int(pa_max),
                                   cached_df=df_full)
            if not ok:
                trial_history.append({
                    "trial": trial_idx + 1, "features": feature_subset,
                    "net_profit": 0.0, "mdd": 99.0, "trades": 0,
                    "ic_pass_count": ic_pass_count
                })
                continue

            # STEP 3: 홀드아웃 검증 (Validation — 최종 Test 구간 제외, 메모리 캐시 주입)
            with _research_lock:
                _research_state["status"] = "validating"

            val_result = _run_holdout_validation(
                config, holdout_start, val_end_str, target_criteria, log_fn,
                feature_subset=feature_subset,
                session_id=session_id,
                cached_df=df_full
            )
            val_result["trial"] = trial_idx + 1
            val_result["features"] = feature_subset
            val_result["ic_pass_count"] = ic_pass_count

            # 📌 최적 파라미터 및 Feature Importance 로드
            trial_best_params = {}
            try:
                bp_path = os.path.join(work_dir, "model", f"best_params_{interval}.json")
                if os.path.exists(bp_path):
                    with open(bp_path, "r", encoding="utf-8") as bpf:
                        trial_best_params = json.load(bpf)
            except Exception:
                pass

            model_txt_path = os.path.join(work_dir, "model", f"model_{interval}.txt")
            trial_feature_importance = extract_feature_importance_from_model_file(model_txt_path)

            # 📌 Trial 전용 상세 JSON 분리 저장 (온디맨드 빠른 조회를 위해)
            trials_dir = os.path.join(run_dir, "trials")
            os.makedirs(trials_dir, exist_ok=True)
            trial_detail_file = os.path.join(trials_dir, f"trial_{trial_idx+1:03d}.json")
            trial_detail_data = {
                "trial": trial_idx + 1,
                "features": feature_subset,
                "passed": val_result.get("passed", False),
                "net_profit": val_result.get("net_profit", 0.0),
                "monthly_profit": val_result.get("monthly_profit", 0.0),
                "period_days": val_result.get("period_days", 30),
                "mdd": val_result.get("mdd", 99.0),
                "trades": val_result.get("trades", 0),
                "monthly_trades": val_result.get("monthly_trades", 0.0),
                "win_rate": val_result.get("win_rate", 0.0),
                "profit_factor": val_result.get("profit_factor", 0.0),
                "sharpe_ratio": val_result.get("sharpe_ratio", 0.0),
                "avg_hold_mins": val_result.get("avg_hold_mins", 0.0),
                "best_trade": val_result.get("best_trade", 0.0),
                "worst_trade": val_result.get("worst_trade", 0.0),
                "best_params": trial_best_params,
                "feature_importance": trial_feature_importance,
                "trade_logs": val_result.get("trade_logs", [])
            }
            try:
                with open(trial_detail_file, "w", encoding="utf-8") as tf:
                    json.dump(trial_detail_data, tf, indent=2, ensure_ascii=False)
            except Exception as t_err:
                _sync_log(log_fn, f"⚠️ Trial #{trial_idx+1} 상세 파일 저장 실패: {t_err}")

            # 📌 메모리 및 leaderboard.json용 trial_history에는 상세 trade_logs를 제외한 초경량 요약만 보관
            summary_val = {
                "trial": trial_idx + 1,
                "features": feature_subset,
                "passed": val_result.get("passed", False),
                "net_profit": val_result.get("net_profit", 0.0),
                "monthly_profit": val_result.get("monthly_profit", 0.0),
                "period_days": val_result.get("period_days", 30),
                "mdd": val_result.get("mdd", 99.0),
                "trades": val_result.get("trades", 0),
                "monthly_trades": val_result.get("monthly_trades", 0.0),
                "win_rate": val_result.get("win_rate", 0.0),
                "profit_factor": val_result.get("profit_factor", 0.0),
                "sharpe_ratio": val_result.get("sharpe_ratio", 0.0),
                "ic_pass_count": ic_pass_count,
                # WFA 견고성(OOS 양수 창 ≥ 절반 & 합계 > 0). WFA 를 안 쓴 실행은 None.
                "wfa_robust": (trial_best_params.get("training_meta") or {}).get("wfa_robust"),
                "wfa_windows": (trial_best_params.get("training_meta") or {}).get("wfa_windows"),
                "wfa_valid_windows": (trial_best_params.get("training_meta") or {}).get("wfa_valid_windows"),
                "wfa_oos_total_pct": (trial_best_params.get("training_meta") or {}).get("wfa_oos_total_pct"),
                "wfa_window_details": (trial_best_params.get("training_meta") or {}).get("wfa_window_details", []),
            }
            trial_history.append(summary_val)

            curr_profit = val_result.get("net_profit", 0.0)
            curr_trades = val_result.get("trades", 0)
            curr_mdd = val_result.get("mdd", 99.0)
            curr_passed = val_result.get("passed", False)

            # 실시간 중간 리더보드 Top-3 계산 (실시간 스트리밍)
            def _live_score(t):
                if t.get("trades", 0) <= 0:
                    return -999.0
                net = t.get("net_profit", 0.0)
                n_f = len(t.get("features", []))
                pen = (n_f - min_features) * complexity_penalty if n_f > min_features else 0.0
                return net - pen

            live_sorted = sorted(trial_history, key=_live_score, reverse=True)
            live_lb = []
            seen_cand_combos_live = set()
            for lt in live_sorted:
                ck = tuple(sorted(lt.get("features", [])))
                if ck not in seen_cand_combos_live:
                    seen_cand_combos_live.add(ck)
                    live_lb.append({
                        "rank": len(live_lb) + 1,
                        "trial": lt.get("trial", -1),
                        "features": lt.get("features", []),
                        "val_profit": lt.get("net_profit", 0.0),
                        "val_monthly_profit": lt.get("monthly_profit", 0.0),
                        "val_mdd": lt.get("mdd", 99.0),
                        "val_trades": lt.get("trades", 0),
                        "val_monthly_trades": lt.get("monthly_trades", 0.0),
                        "val_passed": lt.get("passed", False),
                        "val_win_rate": lt.get("win_rate", 0.0),
                        "val_profit_factor": lt.get("profit_factor", 0.0),
                        "val_adj": round(_live_score(lt), 2),
                        "status": "진행중 (Live)"
                    })
                    if len(live_lb) >= 3:
                        break

            with _research_lock:
                if curr_profit > _research_state["best_profit"]:
                    _research_state["best_profit"] = curr_profit
                    _research_state["best_trial"] = trial_idx + 1
                    _research_state["best_features"] = feature_subset
                _research_state["trial_history"] = trial_history
                _research_state["leaderboard"] = live_lb

            _broadcast_progress(broadcast_fn, {
                "type": "auto_research_progress",
                "step": "trial_done",
                "trial": trial_idx + 1,
                "result": summary_val,
                "best_profit": _research_state["best_profit"],
                "best_trial": _research_state["best_trial"],
                "leaderboard": live_lb,
            })

            # 매 Trial이 종료될 때마다 명시적 가비지 컬렉션 수행 (메모리 누수 방지)
            gc.collect()

        # 사용자 중단(Abort) 요청 시 처리
        if _research_state.get("abort"):
            elapsed_str = _format_elapsed(start_time_dt)
            best_p = _research_state.get("best_profit", 0.0)
            best_t = _research_state.get("best_trial", -1)
            _send_telegram_notification(
                f"🛑 <b>[ASRL v2] 자동 연구 사용자 중단</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"• 세션: <code>{session_id}</code> ({symbol} {interval})\n"
                f"• 소요 시간: {elapsed_str} (총 {len(trial_history)}회 시도 후 중단)\n"
                f"• 탐색 최고수익: {best_p:+.2f}% (Trial {best_t})\n\n"
                f"💡 사용자에 의해 연구가 정상 중단되었습니다."
            )
            with _research_lock:
                _research_state["status"] = "aborted"
                _research_state["running"] = False
            _broadcast_progress(broadcast_fn, {"type": "auto_research_done", "status": "aborted", "trial_history": trial_history})
            return

        # ============================================================
        # [프로 표준 Phase 2] — Validation 성과 기준 Top-3 후보 선정 (복잡도 패널티 적용)
        # ============================================================
        if not trial_history:
            _sync_log(log_fn, "\n❌ 유효한 Trial 결과가 없습니다.")
            diagnosis = {"code": "NO_DATA", "title": "시도 기록 없음",
                         "message": "모든 Trial이 실패했습니다.", "prescription": "스냅샷 데이터를 확인하세요."}
            with _research_lock:
                _research_state["status"] = "failed"
                _research_state["diagnosis"] = diagnosis
                _research_state["running"] = False
            _broadcast_progress(broadcast_fn, {"type": "auto_research_done", "status": "failed", "diagnosis": diagnosis})
            return

        def _adjusted_score(t):
            # 거래수 0회인 모델은 알파/통계적 유효성이 없으므로 Top 후보군 선정에서 배제
            if t.get("trades", 0) <= 0:
                return -999.0
            net = t.get("net_profit", 0.0)
            n_feat = len(t.get("features", []))
            penalty = (n_feat - min_features) * complexity_penalty if n_feat > min_features else 0.0
            return net - penalty

        # 최종 Test 는 한 번뿐인 독립 검증이다 — WFA 견고성 미달(승격 불가) 트라이얼에 쓰지 않는다.
        # (2026-10-01 069500 1m: 견고성 통과는 Trial 9 하나였는데, Validation 상위 3개가 모두 미달 트라이얼이라
        #  승격될 수 없는 후보 3개가 최종 Test 를 소모했고 Trial 9 는 검증받지 못했다)
        _robust_pool = [t for t in trial_history if t.get("wfa_robust") is not False]
        _n_not_robust = len(trial_history) - len(_robust_pool)
        if _n_not_robust:
            _sync_log(log_fn, f"\n🧱 [Phase 2] WFA 견고성 미달 트라이얼 {_n_not_robust}개는 최종 Test 후보에서 제외 "
                              f"(남은 후보 {len(_robust_pool)}개)")
        # 점수 기준 내림차순 정렬 후 고유 피처 조합 기준 Top-3 후보 선별
        sorted_history = sorted(_robust_pool, key=_adjusted_score, reverse=True)
        top_candidates = []
        seen_cand_combos = set()
        for t in sorted_history:
            ckey = tuple(sorted(t.get("features", [])))
            if ckey not in seen_cand_combos:
                seen_cand_combos.add(ckey)
                rank_num = len(top_candidates) + 1
                top_candidates.append({
                    "rank": rank_num,
                    "trial": t.get("trial", -1),
                    "features": t.get("features", []),
                    "val_profit": t.get("net_profit", 0.0),
                    "val_monthly_profit": t.get("monthly_profit", round(t.get("net_profit", 0.0) * (30.0 / max(1, t.get("period_days", 30))), 2)),
                    "val_days": t.get("period_days", 30),
                    "val_mdd": t.get("mdd", 99.0),
                    "val_trades": t.get("trades", 0),
                    "val_monthly_trades": t.get("monthly_trades", round(t.get("trades", 0) * (30.4 / max(1, t.get("period_days", 30))), 1)),
                    "val_adj": _adjusted_score(t),
                    "val_passed": t.get("passed", False),
                })
                if len(top_candidates) >= 3:
                    break

        _sync_log(log_fn, "\n" + "="*60)
        _sync_log(log_fn, f"🏆 [Phase 2] Validation Top-{len(top_candidates)} 후보 선정 완료 (복잡도 패널티 적용)")
        if not top_candidates:
            _sync_log(log_fn, "   ⛔ WFA 견고성을 통과한 후보가 없어 최종 Test 를 실시하지 않습니다 — 이번 실행은 탈락입니다.")
        for cand in top_candidates:
            _sync_log(log_fn, f"   🥇 [Rank {cand['rank']}] Trial {cand['trial']} | 지표 {len(cand['features'])}개: {cand['features']}")
            _sync_log(log_fn, f"      → Val 순수익: {cand['val_profit']:+.2f}% (월환산: {cand['val_monthly_profit']:+.2f}%) | 조정점수: {cand['val_adj']:+.2f}% | MDD: {cand['val_mdd']:.2f}% | 거래: {cand['val_trades']}회 (월 {cand['val_monthly_trades']}회)")
        _sync_log(log_fn, "="*60)

        # ============================================================
        # [프로 표준 Phase 3] — Top-3 Cascade 순차 검증 & 아카이빙
        # ============================================================
        _sync_log(log_fn, f"\n🔒 [Phase 3] 최종 Test 구간: {final_test_start} ~ {final_test_end} (Validation: ~{val_end_str})")
        _sync_log(log_fn, f"🔄 상위 후보군(Top-{len(top_candidates)})에 대해 순차적 최종 검증 및 개별 저장을 시작합니다.")

        coin_code = symbol.split('-')[-1]
        candidates_dir = os.path.join(run_dir, "candidates")      # 이번 실행의 후보 보관
        work_model_dir = os.path.join(work_dir, "model")          # 재학습 산출물 (임시)
        model_dir = os.path.join(session_dir, "model")            # 프로젝트 챔피언 자리 — 합격 시에만 교체
        os.makedirs(candidates_dir, exist_ok=True)

        leaderboard = []
        champion_cand = None
        champion_rank = None

        # ── [백본 단독 기준선] 체제 백본만으로 만든 모델을 같은 최종 Test 구간에서 잰다 ──
        # 알파 지표가 그 기준선을 넘지 못하면 그 후보는 '시계 모델' 이다 — 승격시키지 않는다.
        # (2026-09-28: 챔피언 gain 의 84.5% 가 target_hour_sin/cos 였고 참조 피처는 0.08% 였는데,
        #  어느 단계에서도 걸리지 않았다. 체제 백본이 모든 조합에 붙어 비교 대상이 없었기 때문이다)
        backbone_baseline = None
        backbone_only = [f for f in (regime_features or [])]
        if backbone_only:
            _sync_log(log_fn, f"\n{'─'*60}")
            _sync_log(log_fn, f"📏 [백본 기준선] 체제 백본 {len(backbone_only)}개만으로 최종 Test 성과를 잰다: {backbone_only}")
            _sync_log(log_fn, f"   → 후보는 이 기준선을 넘어야 승격된다 (알파가 더한 것이 있어야 한다)")
            _sync_log(log_fn, f"{'─'*60}")
            try:
                if _run_optimization(config, backbone_only, log_fn,
                                     holdout_start=holdout_start,
                                     purge_candles=int(pa_max), cached_df=df_full):
                    _bb_res = _run_holdout_validation(
                        config, final_test_start, final_test_end, target_criteria, log_fn,
                        feature_subset=backbone_only, session_id=session_id, cached_df=df_full,
                    )
                    if _bb_res:
                        backbone_baseline = float(_bb_res.get("net_profit", 0.0))
                        _sync_log(log_fn, f"📏 [백본 기준선] 순수익 {backbone_baseline:+.2f}% / "
                                          f"거래 {_bb_res.get('trades', 0)}회 — 후보는 이보다 나아야 한다")
                else:
                    _sync_log(log_fn, "📏 [백본 기준선] 학습 실패 — 기준선 없이 진행합니다")
            except Exception as _bb_err:
                _sync_log(log_fn, f"📏 [백본 기준선] 산출 실패({_bb_err}) — 기준선 없이 진행합니다")
        else:
            _sync_log(log_fn, "\n📏 [백본 기준선] 통과한 체제 백본이 없어 기준선 비교를 건너뜁니다")

        with _research_lock:
            _research_state["backbone_baseline"] = backbone_baseline

        for cand in top_candidates:
            if _research_state.get("abort"):
                _sync_log(log_fn, "\n🛑 사용자가 자동 연구를 중단했습니다.")
                break

            rank_no = cand["rank"]
            cand_features = cand["features"]
            rank_dir = os.path.join(candidates_dir, f"rank_{rank_no}")
            os.makedirs(rank_dir, exist_ok=True)

            _sync_log(log_fn, f"\n{'━'*60}")
            _sync_log(log_fn, f"🔬 [Phase 3] Rank {rank_no} 후보 재학습 및 최종 Test 검증 시작")
            _sync_log(log_fn, f"   피처 ({len(cand_features)}개): {cand_features}")
            _sync_log(log_fn, f"{'━'*60}")

            # 모델 재학습 (Train-Serve Skew 원천 방지, cand_features 인메모리 주입)
            retrain_ok = _run_optimization(
                config, cand_features, log_fn,
                holdout_start=holdout_start,
                purge_candles=int(pa_max),
                cached_df=df_full,
            )

            if _research_state.get("abort"):
                _sync_log(log_fn, "\n🛑 사용자가 자동 연구를 중단했습니다.")
                break

            if not retrain_ok:
                _sync_log(log_fn, f"❌ [Phase 3] Rank {rank_no} 후보 재학습 실패")
                final_res = None
            else:
                # 재학습된 모델 파일을 candidates/rank_{N}/에 즉시 복사하여 영구 보존
                try:
                    for fname in [f"model_{interval}.txt", f"best_params_{interval}.json", "feature_spec.json"]:
                        src = os.path.join(work_model_dir, fname)
                        dst = os.path.join(rank_dir, fname)
                        if os.path.exists(src):
                            shutil.copy2(src, dst)
                    _sync_log(log_fn, f"💾 Rank {rank_no} 모델 및 지표 규격 저장 완료 ➔ candidates/rank_{rank_no}/")
                except Exception as ce:
                    _sync_log(log_fn, f"⚠️ Rank {rank_no} 모델 파일 복사 실패: {ce}")

                # 최종 Test 검증 실행
                final_res = _run_holdout_validation(
                    config, final_test_start, final_test_end, target_criteria, log_fn,
                    feature_subset=cand_features,
                    session_id=session_id,
                    cached_df=df_full,
                )

            # ── [D-15] 최종 Test 구간 피처 가용성 검증 (죽은 피처 배제) ──
            # Validation 구간 성과만 보고 선정된 후보가 최종 Test 구간에서 죽은 피처에 의존하면 무효다.
            dead_feats, dead_details = detect_dead_features(
                df_full, cand_features, final_test_start, final_test_end
            )
            if dead_feats:
                _sync_log(log_fn, f"⛔ [D-15] Rank {rank_no} 최종 Test 구간에서 사용 불가한 피처 {len(dead_feats)}개 → 후보 무효 처리")
                for _dc, _info in dead_details.items():
                    _sync_log(log_fn, f"      · {_dc}: {_info['reason']} (유효값 {_info['valid_ratio']*100:.1f}%, 값 종류 {_info['nunique']}개 / {_info['rows']}행)")
                if final_res is not None:
                    final_res["dead_features"] = dead_feats
                    final_res["passed"] = False

            final_passed = bool(final_res and final_res.get("passed", False))
            final_net = final_res.get("net_profit", 0.0) if final_res else 0.0
            final_monthly = final_res.get("monthly_profit", 0.0) if final_res else 0.0
            final_days = final_res.get("period_days", 1) if final_res else 1
            final_trades = final_res.get("trades", 0) if final_res else 0
            final_monthly_trades = final_res.get("monthly_trades", round(final_trades * (30.4 / max(1, final_days)), 1)) if final_res else 0.0
            final_mdd = final_res.get("mdd", 99.0) if final_res else 99.0
            final_win_rate = final_res.get("win_rate", 0.0) if final_res else 0.0
            final_pf = final_res.get("profit_factor", 0.0) if final_res else 0.0
            final_sharpe = final_res.get("sharpe_ratio", 0.0) if final_res else 0.0
            final_avg_hold = final_res.get("avg_hold_mins", 0.0) if final_res else 0.0
            final_best_trade = final_res.get("best_trade", 0.0) if final_res else 0.0
            final_worst_trade = final_res.get("worst_trade", 0.0) if final_res else 0.0

            # Rank 전용 파라미터 및 Feature Importance 로드
            cand_best_params = {}
            try:
                rank_bp_path = os.path.join(rank_dir, f"best_params_{interval}.json")
                if os.path.exists(rank_bp_path):
                    with open(rank_bp_path, "r", encoding="utf-8") as rbpf:
                        cand_best_params = json.load(rbpf)
            except Exception:
                pass

            # WFA 견고성 게이트 — 창 절반 이상에서 내부 검증이 양수여야 한다 (소수 창의 값은 잡음으로 고른 값)
            _tm = (cand_best_params.get("training_meta") or {})
            _wfa_w, _wfa_ok = _tm.get("wfa_windows"), _tm.get("wfa_valid_windows")
            wfa_robust = _tm.get("wfa_robust") is not False      # WFA 를 안 쓴 실행(None)은 이 게이트 대상이 아니다
            if not wfa_robust:
                final_passed = False
                if final_res is not None:
                    final_res["passed"] = False

            rank_model_path = os.path.join(rank_dir, f"model_{interval}.txt")
            cand_feature_importance = extract_feature_importance_from_model_file(rank_model_path)

            cand_summary = {
                "rank": rank_no,
                "trial": cand["trial"],
                "features": cand_features,
                "val_profit": cand["val_profit"],
                "val_monthly_profit": cand.get("val_monthly_profit", 0.0),
                "val_days": cand.get("val_days", 30),
                "val_mdd": cand["val_mdd"],
                "val_trades": cand["val_trades"],
                "val_monthly_trades": cand.get("val_monthly_trades", 0.0),
                "val_adj": cand["val_adj"],
                "final_profit": final_net,
                "final_monthly_profit": final_monthly,
                "final_days": final_days,
                "final_mdd": final_mdd,
                "final_trades": final_trades,
                "final_monthly_trades": final_monthly_trades,
                # ── [D-12~D-17 / P-4] 판정 근거 투명화 (거래 수 하한 / 표본 / 죽은 피처 / 표준 여부) ──
                "final_required_trades": final_res.get("required_trades", 0.0) if final_res else 0.0,
                "final_sample_sufficient": final_res.get("sample_sufficient", True) if final_res else False,
                "final_holdout_trading_days": final_res.get("holdout_trading_days", 0) if final_res else 0,
                "final_standard_holdout_td": final_res.get("standard_holdout_td", 0) if final_res else 0,
                "final_standard_met": final_res.get("standard_met", True) if final_res else False,
                "final_non_standard": bool(final_res and not final_res.get("standard_met", True)) if final_res else True,
                "final_pf_lossless": final_res.get("pf_lossless", False) if final_res else False,
                "dead_features": dead_feats,
                "dead_feature_details": dead_details,
                "wfa_windows": _wfa_w,
                "wfa_valid_windows": _wfa_ok,
                "wfa_oos_total_pct": _tm.get("wfa_oos_total_pct"),
                "wfa_robust": wfa_robust,
                "wfa_window_details": _tm.get("wfa_window_details", []),
                "win_rate": final_win_rate,
                "profit_factor": final_pf,
                "sharpe_ratio": final_sharpe,
                "avg_hold_mins": final_avg_hold,
                "best_trade": final_best_trade,
                "worst_trade": final_worst_trade,
                "passed": final_passed,
                "status": "PASSED" if final_passed else "REJECTED",
                "is_champion": False,
                "best_params": cand_best_params,
                "feature_importance": cand_feature_importance,
                "trades_detail": final_res.get("trade_logs", []) if final_res else [],
            }

            # 백본 단독 기준선 대비 — 알파가 아무것도 더하지 못했으면 승격시키지 않는다
            beats_backbone = (backbone_baseline is None) or (final_net > backbone_baseline)
            cand_summary["backbone_baseline"] = backbone_baseline
            cand_summary["beats_backbone"] = beats_backbone
            if final_passed and not beats_backbone:
                _sync_log(log_fn, f"⛔ [백본 기준선 미달] Rank {rank_no}: 순수익 {final_net:+.2f}% ≤ "
                                  f"백본 단독 {backbone_baseline:+.2f}% — 알파가 더한 것이 없어 승격 보류")

            # candidate_detail.json 영구 아카이빙
            try:
                cand_detail_file = os.path.join(rank_dir, "candidate_detail.json")
                with open(cand_detail_file, "w", encoding="utf-8") as cdf:
                    json.dump(cand_summary, cdf, indent=2, ensure_ascii=False)
            except Exception as cde:
                _sync_log(log_fn, f"⚠️ Rank {rank_no} candidate_detail.json 저장 실패: {cde}")

            # Cascade 챔피언 승격 로직
            if champion_cand is None and final_passed and beats_backbone:
                champion_cand = cand_summary
                champion_rank = rank_no
                cand_summary["is_champion"] = True
                _sync_log(log_fn, f"\n🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉")
                _sync_log(log_fn, f"🎉 [Cascade 승격] Rank {rank_no} 후보가 최종 Test를 통과하여 챔피언으로 선정되었습니다!")
                _sync_log(log_fn, f"   순수익: {final_net:+.2f}% (월환산: {final_monthly:+.2f}%) | 거래: {final_trades}회 | MDD: {final_mdd:.2f}%")
                _sync_log(log_fn, f"🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉")
                # 프로젝트 model/ 교체는 Phase 3 이 모두 끝난 뒤 한 번만 한다 (아래 '챔피언 확정')
            elif not final_passed:
                _reasons = []
                if dead_feats:
                    _reasons.append(f"죽은 피처 {len(dead_feats)}개({','.join(dead_feats[:3])})")
                if not wfa_robust:
                    _reasons.append(f"WFA 견고성 미달(OOS 양수 창 {_wfa_ok}/{_wfa_w}, 합계 {_tm.get('wfa_oos_total_pct', 0.0):+.2f}%)")
                if final_res and not final_res.get("sample_sufficient", True):
                    _reasons.append(f"표본 부족({final_res.get('holdout_trading_days', final_res.get('period_days', 0))}거래일 < 최소 {int(HOLDOUT_MIN_TRADING_DAYS)}거래일)")
                _req = final_res.get("required_trades", 0) if final_res else 0
                if _req and final_trades < _req:
                    _reasons.append(f"목표 거래 미달({final_trades}/{_req:.0f}회)")
                _why = ", ".join(_reasons) if _reasons else "성과 기준 미달"
                _sync_log(log_fn, f"❌ Rank {rank_no} 최종 Test 미달 — {_why} (순수익: {final_net:+.2f}%, 월환산: {final_monthly:+.2f}%, 거래: {final_trades}회, MDD: {final_mdd:.2f}%)")

            leaderboard.append(cand_summary)

        # 사용자 중단(Abort) 요청 시 처리 (Phase 3 도중 중단 대응)
        if _research_state.get("abort"):
            elapsed_str = _format_elapsed(start_time_dt)
            best_p = _research_state.get("best_profit", 0.0)
            best_t = _research_state.get("best_trial", -1)
            _send_telegram_notification(
                f"🛑 <b>[ASRL v2] 자동 연구 사용자 중단</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"• 세션: <code>{session_id}</code> ({symbol} {interval})\n"
                f"• 소요 시간: {elapsed_str} (Phase 3 검증 중 중단)\n"
                f"• 탐색 최고수익: {best_p:+.2f}% (Trial {best_t})\n\n"
                f"💡 사용자에 의해 연구가 정상 중단되었습니다."
            )
            with _research_lock:
                _research_state["status"] = "aborted"
                _research_state["running"] = False
            _broadcast_progress(broadcast_fn, {"type": "auto_research_done", "status": "aborted", "trial_history": trial_history})
            return

        # 📌 [챔피언 확정] 합격한 경우에만 프로젝트 model/ 을 교체한다.
        # model/ 을 비우고 채워 이전 챔피언의 잔재(candidate_detail.json 등)가 섞이지 않게 하고,
        # 출처(model_source.json)를 남겨 배포 API 가 합격 여부를 확인할 수 있게 한다.
        # 탈락(REJECTED)이면 model/ 은 이전 챔피언 그대로다.
        if champion_rank:
            champ_dir = os.path.join(candidates_dir, f"rank_{champion_rank}")
            try:
                staging = model_dir + ".staging"
                shutil.rmtree(staging, ignore_errors=True)
                os.makedirs(staging)
                for fname in [f"model_{interval}.txt", f"best_params_{interval}.json", "feature_spec.json", "candidate_detail.json"]:
                    src = os.path.join(champ_dir, fname)
                    if os.path.exists(src):
                        shutil.copy2(src, os.path.join(staging, fname))
                with open(os.path.join(staging, "model_source.json"), "w", encoding="utf-8") as msf:
                    json.dump({"kind": "autolab_champion", "status": "PASSED", "rank": champion_rank,
                               "trial": champion_cand.get("trial"),
                               "run": os.path.relpath(run_dir, session_dir),
                               "promoted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                              msf, indent=2, ensure_ascii=False)
                if os.path.isdir(model_dir):
                    shutil.rmtree(model_dir)
                os.replace(staging, model_dir)

                # 챔피언 거래 상세 내역 복원 (reports/test_trades.csv)
                detail_json_path = os.path.join(champ_dir, "candidate_detail.json")
                if os.path.exists(detail_json_path):
                    try:
                        with open(detail_json_path, "r", encoding="utf-8") as df:
                            cd_obj = json.load(df)
                        td_list = cd_obj.get("trades_detail", [])
                        if td_list:
                            df_trades = pd.DataFrame(td_list)
                            if "entry_features" in df_trades.columns:
                                df_trades = df_trades.drop(columns=["entry_features"])
                            trades_csv_path = os.path.join(session_dir, "reports", "test_trades.csv")
                            os.makedirs(os.path.dirname(trades_csv_path), exist_ok=True)
                            df_trades.to_csv(trades_csv_path, index=False, encoding="utf-8")
                    except Exception as tde:
                        _sync_log(log_fn, f"⚠️ 챔피언 test_trades.csv 동기화 경고: {tde}")

                # 세션 README.md를 챔피언(Rank {champion_rank}) 실측 결과로 갱신
                try:
                    bp_path = os.path.join(champ_dir, f"best_params_{interval}.json")
                    bp_data = {}
                    if os.path.exists(bp_path):
                        with open(bp_path, "r", encoding="utf-8") as bpf:
                            bp_data = json.load(bpf)
                    
                    readme_lines = [
                        f"# AI Optimization Session: {session_id}",
                        "",
                        f"- **생성 일시**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        f"- **대상 자산**: {symbol} ({interval})",
                        f"- **최종 챔피언**: Rank {champion_rank} (Trial {champion_cand.get('trial')})",
                        f"- **챔피언 피처 ({len(champion_cand['features'])}개)**: `{champion_cand['features']}`",
                        f"- **Optuna Score**: {bp_data.get('optuna_score', 'N/A')}",
                        f"- **Validation Metrics**: {json.dumps(bp_data.get('validation_metrics', {}), ensure_ascii=False)}",
                        f"- **Final Test Metrics**: {{\"profit\": {champion_cand['final_profit']:.2f}, \"trades\": {champion_cand['final_trades']}, \"win_rate\": {champion_cand.get('win_rate', 0.0):.1f}, \"mdd\": {champion_cand['final_mdd']:.2f}, \"profit_factor\": {champion_cand.get('profit_factor', 0.0):.2f}}}",
                        f"- **Assessment**: PASSED",
                        "",
                        "## 📂 Directory Structure",
                        "",
                        "```",
                        "├── model/ (실전 배포용 챔피언 핵심 파일)",
                        f"│   ├── model_{interval}.txt           (LightGBM 가중치 모델)",
                        f"│   ├── best_params_{interval}.json    (최적 하이퍼파라미터 설정)",
                        "│   └── feature_spec.json       (기술 지표 전처리 명세서)",
                        "│",
                        "│   └── model_source.json       (출처: 이 챔피언을 만든 실행)",
                        "│",
                        f"├── runs/ (자동연구 실행 기록 — 최근 {project_store.RUNS_KEEP}회 보관, 실행마다 폴더 1개)",
                        f"│   └── {os.path.basename(run_dir)}/  (챔피언 출처 실행)",
                        "│       ├── leaderboard.json · result.json · config_used.json",
                        "│       ├── train_run.log       (서버가 실행 중 직접 기록한 로그)",
                        "│       ├── trials/ · candidates/rank_N/",
                        "│       └── work/               (재학습 임시 산출물)",
                        "│",
                        "└── reports/",
                        "    └── test_trades.csv         (챔피언 테스트 구간 상세 거래 기록)",
                        "```",
                        "",
                        "---",
                        "*본 폴더는 CAiModelBaker 엔진에 의해 자동 아카이빙된 최적화 세션 패키지입니다.*",
                        ""
                    ]
                    with open(os.path.join(session_dir, "README.md"), "w", encoding="utf-8") as rf:
                        rf.write("\n".join(readme_lines))
                except Exception as rde:
                    _sync_log(log_fn, f"⚠️ README.md 챔피언 갱신 경고: {rde}")

                _sync_log(log_fn, f"✅ [Cascade 완료] 최종 챔피언(Rank {champion_rank}) 아티팩트가 model/ 에 완벽히 복원·확정되었습니다.")
            except Exception as fe:
                _sync_log(log_fn, f"⚠️ 최종 챔피언 복원 오류: {fe}")

        # 리더보드 JSON 파일 저장 (Trial 전체 히스토리 포함)
        leaderboard_data = {
            "session_id": session_id,
            "symbol": get_target_symbol(config),
            "interval": interval,
            "evaluated_at": datetime.now().isoformat(),
            "final_test_start": final_test_start,
            "final_test_end": final_test_end,
            "oos_independent": oos_independent,
            "champion_rank": champion_rank,
            "status": "PASSED" if champion_rank else "REJECTED",
            "leaderboard": leaderboard,
            "trial_history": trial_history,
        }
        try:
            leaderboard_file = os.path.join(run_dir, "leaderboard.json")
            with open(leaderboard_file, "w", encoding="utf-8") as lf:
                json.dump(leaderboard_data, lf, indent=4, ensure_ascii=False)
            _sync_log(log_fn, f"\n📊 리더보드 및 Trial 히스토리 아카이빙 완료: {leaderboard_file}")
        except Exception as le:
            _sync_log(log_fn, f"⚠️ 리더보드 파일 저장 실패: {le}")
        try:
            _write_run_result(run_dir, {
                "status": leaderboard_data["status"], "champion_rank": champion_rank,
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "final_test": f"{final_test_start} ~ {final_test_end}",
                "model_replaced": bool(champion_rank),
            })
            if not champion_rank:
                _sync_log(log_fn, "🔒 탈락(REJECTED) — 프로젝트 model/ 은 이전 챔피언 그대로 유지됩니다 (이번 후보는 runs/ 에만 보관).")
        except Exception:
            pass

        # 최종 판정 및 상태 처리
        if champion_cand is not None:
            _save_best_feature_config(champion_cand["features"], {
                "net_profit": champion_cand["final_profit"],
                "mdd": champion_cand["final_mdd"],
                "trades": champion_cand["final_trades"],
            }, log_fn, assessment="PASSED")

            # 📌 활성 세션 폴더 내 config_snapshot.json의 champion_features 기록 및 ACTIVE_SESSION_ID 동기화 (원본 지표 풀은 유지)
            try:
                session_snapshot_path = os.path.join(session_dir, "config_snapshot.json")
                snap_obj = {}
                if os.path.exists(session_snapshot_path):
                    with open(session_snapshot_path, "r", encoding="utf-8") as ssf:
                        snap_obj = json.load(ssf)
                else:
                    snap_obj = dict(config)

                snap_obj["champion_features"] = champion_cand["features"]
                snap_obj["ACTIVE_SESSION_ID"] = session_id
                with open(session_snapshot_path, "w", encoding="utf-8") as ssf:
                    json.dump(snap_obj, ssf, indent=4, ensure_ascii=False)
                _sync_log(log_fn, f"📁 세션 스냅샷 챔피언 지표 기록 완료 ({len(champion_cand['features'])}개) / 원본 지표 풀 보존")
            except Exception as se:
                _sync_log(log_fn, f"⚠️ 세션 스냅샷 지표 동기화 실패: {se}")

            with _research_lock:
                _research_state["status"] = "done"
                _research_state["diagnosis"] = None
                _research_state["trial_history"] = trial_history
                _research_state["best_features"] = champion_cand["features"]
                _research_state["best_profit"] = champion_cand["val_profit"]
                # best_profit/best_features 를 챔피언 값으로 바꾸면 best_trial 도 같이 바꾼다
                # (종전: 진행 중 최고 트라이얼 번호가 남아 '최고 달성 Trial' 이 챔피언과 어긋났다)
                _research_state["best_trial"] = champion_cand.get("trial", _research_state.get("best_trial", -1))
                _research_state["leaderboard"] = leaderboard
                _research_state["running"] = False

            # 텔레그램 완료 알림 발송 (통과)
            elapsed_str = _format_elapsed(start_time_dt)
            lb_text = ""
            for item in leaderboard:
                icon = "🏆" if item.get("is_champion") else ("✅" if item.get("passed") else "❌")
                val_m = item.get("val_monthly_profit", item.get("val_profit", 0.0))
                fin_m = item.get("final_monthly_profit", item.get("final_profit", 0.0))
                lb_text += f"\n• <b>Rank {item['rank']}</b> {icon}: Val {item['val_profit']:+.2f}%(월 {val_m:+.1f}%) ➔ Final <b>{item['final_profit']:+.2f}%(월 {fin_m:+.1f}%)</b> (거래 {item['final_trades']}회, MDD {item['final_mdd']:.2f}%)"

            champ_features_str = ", ".join(champion_cand.get("features", []))
            champ_val_m = champion_cand.get("val_monthly_profit", champion_cand.get("val_profit", 0.0))
            champ_fin_m = champion_cand.get("final_monthly_profit", champion_cand.get("final_profit", 0.0))

            _send_telegram_notification(
                f"🏆 <b>[ASRL v2] 자동 연구 성공 & 챔피언 배포 승인</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"• 세션: <code>{session_id}</code> ({symbol} {interval})\n"
                f"• 소요 시간: {elapsed_str} (총 {len(trial_history)}회 시도)\n"
                f"• 최종 챔피언: <b>Rank {champion_rank} 후보</b>\n"
                f"• 선택 지표 ({len(champion_cand['features'])}개): <code>{champ_features_str}</code>\n\n"
                f"📈 <b>[챔피언 검증 성적]</b>\n"
                f"• Validation: 수익 {champion_cand['val_profit']:+.2f}% (월 {champ_val_m:+.1f}%) | 거래 {champion_cand['val_trades']}회 | MDD {champion_cand['val_mdd']:.2f}%\n"
                f"• Final Test: <b>수익 {champion_cand['final_profit']:+.2f}% (월 {champ_fin_m:+.1f}%)</b> | 거래 {champion_cand['final_trades']}회 | <b>MDD {champion_cand['final_mdd']:.2f}%</b>\n\n"
                f"📊 <b>[Top-{len(leaderboard)} 리더보드 결과]</b>{lb_text}\n\n"
                f"🚀 <i>챔피언 모델이 실전 배포 폴더(model/)에 자동 배치되었습니다.</i>"
            )

            _broadcast_progress(broadcast_fn, {
                "type": "auto_research_done",
                "status": "success",
                "session_id": session_id,
                "champion_rank": champion_rank,
                "features": champion_cand["features"],
                "leaderboard": leaderboard,
                "trial_history": trial_history,
                "final_test": {
                    "start": final_test_start, "end": final_test_end,
                    "net_profit": champion_cand["final_profit"],
                    "monthly_profit": champ_fin_m,
                    "trades": champion_cand["final_trades"],
                    "mdd": champion_cand["final_mdd"]
                }
            })
            return
        else:
            # 모든 상위 후보 미달
            top1 = leaderboard[0] if leaderboard else {}
            top1_features = top1.get("features", [])
            if top1_features:   # 후보가 없으면 활성 지표를 빈 목록으로 덮어쓰지 않는다
                _save_best_feature_config(top1_features, {
                    "net_profit": top1.get("final_profit", 0.0),
                    "monthly_profit": top1.get("final_monthly_profit", 0.0),
                    "mdd": top1.get("final_mdd", 99.0),
                    "trades": top1.get("final_trades", 0),
                }, log_fn, assessment="REJECTED")

            diagnosis = diagnose_failure(
                trial_history,
                min_trades=target_criteria.get("min_trades", 10),
                min_features=min_features
            )
            _print_diagnosis(diagnosis, log_fn)

            with _research_lock:
                _research_state["status"] = "failed"
                _research_state["diagnosis"] = diagnosis
                _research_state["trial_history"] = trial_history
                _research_state["leaderboard"] = leaderboard
                _research_state["running"] = False

            elapsed_str = _format_elapsed(start_time_dt)
            lb_text = ""
            for item in leaderboard:
                val_m = item.get("val_monthly_profit", item.get("val_profit", 0.0))
                fin_m = item.get("final_monthly_profit", item.get("final_profit", 0.0))
                lb_text += f"\n• <b>Rank {item['rank']}</b> ❌: Val {item['val_profit']:+.2f}%(월 {val_m:+.1f}%) ➔ Final {item.get('final_profit', 0.0):+.2f}%(월 {fin_m:+.1f}%) (거래 {item.get('final_trades', 0)}회, MDD {item.get('final_mdd', 99.0):.2f}%)"

            diag_title = diagnosis.get("title", "기준 미달") if isinstance(diagnosis, dict) else "검증 기준 미달"
            diag_msg = diagnosis.get("message", "") if isinstance(diagnosis, dict) else ""

            _send_telegram_notification(
                f"⚠️ <b>[ASRL v2] 자동 연구 종료 (후보군 전체 미달)</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"• 세션: <code>{session_id}</code> ({symbol} {interval})\n"
                f"• 소요 시간: {elapsed_str} (총 {len(trial_history)}회 시도)\n"
                f"• 진단: <b>{diag_title}</b> ({diag_msg})\n"
                f"• 상위 {len(leaderboard)}개 후보 모두 최종 기준에 미달하여 자동 배포되지 않았습니다.\n\n"
                f"📊 <b>[Top-{len(leaderboard)} 리더보드 성적]</b>{lb_text if lb_text else '\n• 후보군 없음'}\n\n"
                f"💡 <i>대시보드 AutoLab 리더보드에서 원하는 후보를 수동으로 배포할 수 있습니다.</i>"
            )

            _broadcast_progress(broadcast_fn, {
                "type": "auto_research_done",
                "status": "failed",
                "session_id": session_id,
                "diagnosis": diagnosis,
                "trial_history": trial_history,
                "leaderboard": leaderboard,
                "final_test": {
                    "start": final_test_start, "end": final_test_end,
                }
            })
            return

    except Exception as e:
        import traceback
        _sync_log(log_fn, f"\n❌ [ASRL] 예외 발생: {e}")
        _sync_log(log_fn, traceback.format_exc())
        
        elapsed_str = _format_elapsed(start_time_dt) if 'start_time_dt' in locals() else "미상"
        curr_session_id = session_id if 'session_id' in locals() else config.get('ACTIVE_SESSION_ID', 'default')
        curr_symbol = symbol if 'symbol' in locals() else get_target_symbol(config)
        curr_interval = interval if 'interval' in locals() else config.get('INTERVAL', '15m')

        # 텔레그램 예외 알림 발송
        _send_telegram_notification(
            f"🚨 <b>[ASRL v2] 자동 연구 에러 발생 및 종료</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"• 세션: <code>{curr_session_id}</code> ({curr_symbol} {curr_interval})\n"
            f"• 진행 시간: {elapsed_str}\n"
            f"• 에러 내용: <code>{str(e)}</code>"
        )
        
        with _research_lock:
            _research_state["status"] = "failed"
            _research_state["running"] = False
        _broadcast_progress(broadcast_fn, {"type": "auto_research_done", "status": "error", "error": str(e)})
    finally:
        # 중단·실패로 끝난 실행도 result.json 에 최종 상태를 남긴다 (RUNNING 으로 방치하지 않음)
        try:
            _res_path = os.path.join(run_dir, "result.json")
            _cur = {}
            if os.path.exists(_res_path):
                with open(_res_path, "r", encoding="utf-8") as _rf:
                    _cur = json.load(_rf) or {}
            if _cur.get("status") in (None, "RUNNING"):
                _final = {"aborted": "ABORTED", "failed": "FAILED"}.get(_research_state.get("status"), "INCOMPLETE")
                _write_run_result(run_dir, {"status": _final, "model_replaced": False,
                                            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# API 상태 조회 / 중단 / 스레드 실행
# ---------------------------------------------------------------------------
def get_research_status() -> dict:
    with _research_lock:
        state = dict(_research_state)
    elapsed = ""
    if state.get("start_time"):
        try:
            start = pd.to_datetime(state["start_time"]).to_pydatetime()
            delta = datetime.now() - start
            if hasattr(delta, 'total_seconds'):
                total_sec = int(delta.total_seconds())
            elif isinstance(delta, np.timedelta64):
                total_sec = int(delta / np.timedelta64(1, 's'))
            else:
                total_sec = int(pd.Timedelta(delta).total_seconds())
            elapsed = str(timedelta(seconds=total_sec))
        except Exception:
            pass
    state["elapsed"] = elapsed
    return state


def abort_research():
    with _research_lock:
        _research_state["abort"] = True
    return {"status": "ok", "message": "Abort signal sent"}


def reset_research_state(new_session_id: Optional[str] = None) -> bool:
    """새 프로젝트 생성 또는 프로젝트 전환 시 메모리 상의 AutoLab 진행 상태 및 리더보드를 완전 리셋"""
    with _research_lock:
        if _research_state.get("running"):
            return False
        _research_state.update({
            "running": False,
            "current_trial": 0,
            "max_trials": 50,
            "best_profit": 0.0,
            "best_trial": -1,
            "best_features": [],
            "start_time": None,
            "status": "idle",
            "trial_history": [],
            "leaderboard": [],
            "diagnosis": None,
            "abort": False,
            "session_id": new_session_id,
            "elapsed": ""
        })
        return True


def start_research_in_thread(config: dict, target_criteria: dict, max_trials: int,
                              holdout_start: str, holdout_end: str,
                              log_fn: Callable, broadcast_fn: Optional[Callable] = None,
                              ic_threshold: float = 0.02,
                              min_features: int = 4,
                              max_features: int = 10,
                              complexity_penalty: float = 0.15,
                              final_test_ratio: int = 20,
                              use_all_features: bool = False,
                              single_run: bool = False,
                              holdout_mode: str = "full",
                              final_test_td: Optional[int] = None):
    """별도 스레드에서 run_auto_research 실행 (main.py에서 호출)"""
    with _research_lock:
        if _research_state.get("running"):
            return {"status": "already_running", "message": "이미 자동 연구가 실행 중입니다."}
        _research_state["running"] = True
        _research_state["status"] = "running"
        _research_state["abort"] = False

    _research_thread = threading.Thread(
        target=run_auto_research,
        args=(config, target_criteria, max_trials, holdout_start, holdout_end,
              log_fn, broadcast_fn, ic_threshold, min_features, max_features,
              complexity_penalty, final_test_ratio, use_all_features, single_run,
              holdout_mode, final_test_td),
        daemon=True
    )
    _research_thread.start()
    return {"status": "started"}
