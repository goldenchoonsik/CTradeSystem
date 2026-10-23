import os
import sys
import json
import logging
import numpy as np
import pandas as pd
import lightgbm as lgb
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)
try:
    from .data_manager import get_snapshot_path, get_params_path, get_model_path
    from .feature_registry import load_feature_config, get_enabled_feature_names
    from .physics_constraint import resolve_round_trip_cost
except (ImportError, ValueError):
    from core.data_manager import get_snapshot_path, get_params_path, get_model_path
    from core.feature_registry import load_feature_config, get_enabled_feature_names
    from core.physics_constraint import resolve_round_trip_cost

_logger = logging.getLogger(__name__)

# [FIX-3/4] 부스팅 라운드 단일 상수 — 튜닝(inner trial) / WFA OOS 평가 / 최종 저장 모델 / 배포 재학습 공통
NUM_BOOST_ROUND = 30


OOF_FOLDS = 4   # 문턱 보정용 표본 밖 예측의 시간순 조각 수


def oof_predictions(lgb_params, X, y, purge=0, n_folds=OOF_FOLDS, categorical_feature='auto',
                    num_boost_round=None, proba_col=None):
    """시간순 조각 교차검증의 표본 밖 예측 (purged k-fold, López de Prado).

    행은 시간순이어야 한다. 조각 i 는 조각 i 와 그 앞뒤 purge 행을 뺀 나머지로 학습한 모델이 예측한다 —
    라벨이 미래 purge 봉을 보므로 경계 양쪽을 비워야 정보가 새지 않는다(의사결정 시점만 남긴 행에서
    purge 행은 purge 봉 이상을 덮으므로 보수적이다). 예측할 수 없는 조각은 NaN.
    """
    n = len(X)
    out = np.full(n, np.nan)
    if n < n_folds * 20:
        return out
    y_arr = np.asarray(y)
    rounds = num_boost_round or NUM_BOOST_ROUND
    for idx in np.array_split(np.arange(n), n_folds):
        lo, hi = int(idx[0]), int(idx[-1])
        keep = np.ones(n, dtype=bool)
        keep[max(0, lo - purge):min(n, hi + 1 + purge)] = False
        if keep.sum() < 100 or len(np.unique(y_arr[keep])) < 2:
            continue
        m = lgb.train(lgb_params, lgb.Dataset(X.iloc[keep] if hasattr(X, 'iloc') else X[keep], label=y_arr[keep],
                                              categorical_feature=categorical_feature),
                      num_boost_round=rounds)
        p = m.predict(X.iloc[idx] if hasattr(X, 'iloc') else X[idx])
        out[idx] = p[:, proba_col] if (proba_col is not None and np.ndim(p) == 2) else p
    return out


def threshold_for_model(model, X_train, params, lgb_params=None, y_train=None, purge=0,
                        categorical_feature='auto', proba_col=None, log_fn=None) -> float:
    """진입 임계값(절대 확률)을 정한다 — 분위수(buy_quantile)는 **모델이 학습하지 않은 행의 확률**로 환산한다.

    `buy_quantile`(상위 몇 % 에서 진입)이 있으면 같은 설정(lgb_params)으로 학습 구간을 시간순 조각 교차검증해
    얻은 표본 밖 예측(OOF)의 분위수를 문턱으로 쓴다. 없으면(구 설정) 저장된 절대값 `buy_threshold`.

    종전 결함 (2026-10-07, 069500 15m): 학습한 행 자신의 예측 분위수를 썼다. 모델은 학습한 행에 확신을 크게
    내므로 '상위 14.6%' 문턱 0.613 이 처음 보는 홀드아웃에서는 2.0% 만 넘었다(41일 3건 진입).
    현업 표준: 문턱·확률 보정은 표본 밖 예측으로 한다. 실전에서 모델이 받는 것도 처음 보는 데이터다.
    그 이전 결함 (2026-10-01, 069500 10s): 절대 확률 탐색 범위가 모델 확률 분포와 어긋나 진입 고갈 → 분위수 도입.

    lgb_params/y_train 이 없거나 OOF 표본이 부족하면 학습 행 예측으로 대체한다(경고).
    """
    q = params.get('buy_quantile')
    if q is None:
        return float(params['buy_threshold'])
    if lgb_params is not None and y_train is not None:
        oof = oof_predictions(lgb_params, X_train, y_train, purge=purge,
                              categorical_feature=categorical_feature, proba_col=proba_col)
        oof = oof[~np.isnan(oof)]
        if len(oof) >= 100:
            return float(np.quantile(oof, float(q)))
    if log_fn:
        log_fn("⚠️ [진입 문턱] 표본 밖 예측을 만들 수 없어 학습 행 예측 분위수로 대체합니다 (실전 진입 빈도가 의도보다 낮을 수 있음)")
    p = model.predict(X_train)
    p = p[:, proba_col] if (proba_col is not None and np.ndim(p) == 2) else p
    return float(np.quantile(p, float(q)))

# shared/ 모듈에서 공통 피처 엔지니어링 함수와 설정 관리 모듈 import
_CTRADE_SYSTEM_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _CTRADE_SYSTEM_ROOT not in sys.path:
    sys.path.insert(0, _CTRADE_SYSTEM_ROOT)
from shared.features.feature_engine import run_feature_engineering  # noqa: F401 — 재수출
from shared.features.feature_registry import _get_series
from shared.config.config_manager import save_project_config, load_project_config
from shared.config import project_store
from shared.core.market_utils import detect_market_type, get_target_symbol, is_krx_entry_allowed, is_krx_force_exit, parse_interval_minutes
from shared.core.market_calendar import prev_trading_day_strict
from CAiModelBaker.core.split_planner import build_wfa_windows
from CAiModelBaker.core.split_standards import trading_days_per_month
from shared.core.tbm_executor import resolve_exit
from shared.core.ai_policy import META_APPROVAL_THRESHOLD


# ══════════════════════════════════════════════════════════════════════════════
# [D-11] WFA / Optuna 공용 점수 가드 — 거래 1건·무손실 후보의 PF 폭발 차단
# ══════════════════════════════════════════════════════════════════════════════
WFA_PF_SCORE_CAP = 20.0    # PF 상한 (초과분은 점수에 반영하지 않음 — 손실 0건 시 PF→∞ 방지)
WFA_MIN_TRADES_ABS = 10.0  # 절대 최소 거래 수 (표본 부족 후보 원천 차단)

# ── [D-12/D-17/P-4] 최종 홀드아웃(지정 테스트) 판정 정책 ──
# 단위는 **거래일(trading days)** 이다. 근거: 표본 크기는 관측치 수로 결정된다 (AFML bar 기반).
# 종전 `HOLDOUT_MIN_DAYS = 20` 은 **달력일**이라 KRX 기준 실제 표본이 13~14거래일로 40% 부족했다.
HOLDOUT_MIN_TRADING_DAYS = 5         # Tier 3 절대 하한 (거래일) — 미달 시 판정 불가
HOLDOUT_STANDARD_TRADES = 30         # 표준 최소 거래 횟수 (CLT 근사) — 기간과 OR 결합
HOLDOUT_MIN_TRADES_ABS = WFA_MIN_TRADES_ABS  # 표본 부족 후보 하드 차단 (실질 관문)
HOLDOUT_MIN_DAYS = HOLDOUT_MIN_TRADING_DAYS  # ⚠️ 하위 호환 별칭 — 의미가 '달력일'→'거래일'로 변경됨


def compute_wfa_score(total_gain, total_loss, trades_v, mdd_v, val_months, optuna_cfg=None):
    """WFA 창 선택과 Optuna 목적함수가 **공용으로 사용하는 단일 점수 함수** (D-11).

    📌 종전 결함 (실측: 069500/10s KODEX200_S10_BEST1____ / reports/train_run.log)
      · `pf = total_gain / (total_loss + 1e-8)` 에 상한이 없어, **손실 0건(거래 1건·승률 100%)**
        후보의 PF 가 7만점대로 폭발했다. (validation PF 58,747 ~ 84,236)
      · `required_min_trades = max(1.0, min_trades_per_month * val_months)` 에서 WFA 창이
        2~4일이면 우변(20회/월 × 0.07개월 ≈ 1.4)이 **1.0 에 수렴** → 거래 1건도 "충분"으로 통과.
        → `New Best Score: 14.99 (Trades: 1 [요구: 1.0회], WinRate: 100.0%)` 가 최고점을 독식.
      → 옵티마이저가 **"거래를 거의 하지 않는 해"를 최적해로 선택** → 최종 Test 구간 매매 0건.

    📌 가드 2종
      1. **PF 상한** — PF 는 점수 기여도가 상한을 넘지 못한다 (`pf_score_cap`).
      2. **절대 최소 거래 수** — 창 길이와 무관하게 `min_trades_abs` 건 미만은 원천 탈락
         (`score = -90 + trades`, 어떤 실질 후보보다도 낮음).

    Args:
        total_gain: 검증 구간 총이익 (이익 거래 순수익 합)
        total_loss: 검증 구간 총손실 (손실 거래 순수익 절대값 합)
        trades_v: 검증 구간 거래 수
        mdd_v: 최대낙폭 (0~1 비율)
        val_months: 검증 구간 길이(개월)
        optuna_cfg: OPTUNA_CONFIG — `min_trades` / `min_trades_abs` / `pf_score_cap` 로 재정의 가능

    Returns:
        (score, pf_capped, pf_raw, required_min_trades)
    """
    optuna_cfg = optuna_cfg or {}
    pf_raw = float(total_gain) / (float(total_loss) + 1e-8)
    pf_cap = float(optuna_cfg.get('pf_score_cap', WFA_PF_SCORE_CAP))
    pf = min(pf_raw, pf_cap)

    min_trades_per_month = float(optuna_cfg.get('min_trades', 5))
    min_trades_abs = float(optuna_cfg.get('min_trades_abs', WFA_MIN_TRADES_ABS))
    required_min_trades = max(min_trades_abs, min_trades_per_month * float(val_months))

    if trades_v < required_min_trades:
        return -90.0 + float(trades_v), pf, pf_raw, required_min_trades

    # 월간 거래 빈도 보너스 (월 20회 이상이면 최대 +50%) — 저빈도 해에 대한 이중 억제
    monthly_trade_rate = float(trades_v) / max(float(val_months), 0.1)
    trade_bonus = min(monthly_trade_rate / 20.0, 0.5)
    score = (pf * (1.0 - mdd_v)) * (1.0 + trade_bonus)
    return score, pf, pf_raw, required_min_trades


def resolve_holdout_trade_requirement(raw_min_trades, holdout_trading_days, optuna_cfg=None):
    """최종 홀드아웃(지정 테스트)의 **목표 거래 수**와 **표본 충분성**을 산정한다 (D-12/D-17/P-4).

    ⚠️ [P-4] 단위는 **거래일(trading days)** 이다. 종전에는 **달력일**을 받아
    `max(1, round(raw × 달력일/30.4))` 로 계산했고, 그 결과:
      · 지정 테스트가 2달력일(09-14~09-15)이면 `round(20 × 2/30.4) = 1` → **`max(1, 1) = 1` 로 붕괴**
      · `거래수: 1회 (목표: >= 1회)` 로 **거래 1건이 목표를 충족**해 passed=True
      · 배포 후보 3개(rank_1~3)가 전부 "거래 1건"(사실상 동일 거래) 으로 챔피언 승격
    같은 `max(1, …)` 붕괴 클래스가 `compute_wfa_score`(D-11) 에도 있었고 최종 판정에서 반복되었다.

    📌 정책
      ① **절대 하한** — 창 길이와 무관하게 `HOLDOUT_MIN_TRADES_ABS` 미만은 목표로 인정하지 않는다.
      ② **최소 거래일** — 홀드아웃이 `HOLDOUT_MIN_TRADING_DAYS`(거래일) 미만이면
         `sample_sufficient=False` 를 반환한다 (호출자는 판정을 `passed=False` 로 강제).

    Args:
        raw_min_trades: 월간 목표 거래 빈도 (target_criteria['min_trades'])
        holdout_trading_days: 홀드아웃 **거래일 수** (`market_calendar.count_trading_days` 결과)
        optuna_cfg: OPTUNA_CONFIG — `min_trades_abs` / `min_holdout_trading_days` 로 재정의 가능

    Returns:
        (target_trades: int, sample_sufficient: bool, holdout_trading_days: int)
    """
    optuna_cfg = optuna_cfg or {}
    try:
        days = max(1, int(holdout_trading_days))
    except Exception:
        days = 30

    try:
        raw = float(raw_min_trades)
    except Exception:
        raw = 4.0

    floor = float(optuna_cfg.get('min_trades_abs', HOLDOUT_MIN_TRADES_ABS))
    min_days = float(optuna_cfg.get('min_holdout_trading_days', HOLDOUT_MIN_TRADING_DAYS))

    holdout_months = days / 30.4
    target_trades = int(max(floor, round(raw * holdout_months)))
    sample_sufficient = days >= min_days
    return target_trades, sample_sufficient, days


def evaluate_holdout_standard_met(holdout_trading_days, standard_trading_days,
                                  actual_trades, optuna_cfg=None) -> bool:
    """홀드아웃이 **업계 표준**을 충족했는지 판정한다 (P-4).

    표준은 관문이 **기간이 아니라 표본 크기**라는 원칙에 따라 OR 로 결합한다.
        `(거래일 ≥ 표준 거래일)  OR  (실제 거래 횟수 ≥ 표준 거래 횟수)`

    근거: 통계적 유의성은 관측치 수로 결정된다 (CLT 근사, ≥30). 따라서 bar 밀도가 높은
    초봉(HFT)은 짧은 기간으로도 표본이 충분하고, 반대로 일봉은 긴 기간이 필요하다.
    미충족이어도 **차단하지 않고 '비표준' 표시**만 남긴다 (차단은 `HOLDOUT_MIN_*` 하한이 담당).

    Args:
        holdout_trading_days: 홀드아웃 거래일 수
        standard_trading_days: 봉수별 표준 홀드아웃 거래일 (`split_standards.get_standard_split`)
        actual_trades: 실제 거래 횟수
        optuna_cfg: OPTUNA_CONFIG — `standard_holdout_trades` 로 재정의 가능

    Returns:
        bool — True면 표준 충족(정상), False면 비표준(🔶 표시 대상)
    """
    optuna_cfg = optuna_cfg or {}
    std_trd = float(optuna_cfg.get('standard_holdout_trades', HOLDOUT_STANDARD_TRADES))
    try:
        td = float(holdout_trading_days)
        std_td = float(standard_trading_days)
        tr = float(actual_trades)
    except Exception:
        return False
    return (td >= std_td) or (tr >= std_trd)


def _fallback_train_end(master_df, ratio: float = 0.6):
    """[R-2] 비WFA(일반 2구간) 경로의 Train 종료 폴백 — **거래일 기준** 비율 분할 (SSOT).

    종전에는 `master_df['timestamp'].iloc[int(len(master_df) * 0.6)]` 의 **bar 인덱스** 였다.
    결측일/비균일 데이터에서는 bar 인덱스와 거래일 인덱스가 어긋나 경계가 왜곡된다.
    """
    try:
        from CAiModelBaker.core.split_planner import split_point_by_ratio
        sp = split_point_by_ratio(master_df['timestamp'], ratio)
        if sp is not None:
            return sp
    except Exception:
        pass
    n = len(master_df)
    if n == 0:
        return None
    return master_df['timestamp'].iloc[min(int(n * ratio), n - 1)]


def label_decision_points(df, predict_ahead_candles, take_profit, stop_loss, cost, market_type='CRYPTO'):
    """학습용 라벨의 **단일 진입점**: 연속 시계열 전체로 라벨을 만든 뒤 의사결정 시점의 행만 남긴다.

    순서가 중요하다. 행을 먼저 걸러 내고 라벨을 만들면 14:59 진입의 라벨이 볼 15:00 이후 봉이 사라진다.
    의사결정 시점의 정의는 백테스터·실전과 같은 `decision_point_mask` 하나다(KRX 장중 모드: 09:10~15:00).
    종전에는 경로마다 웜업(09:00~09:09)만 빼거나(WFA) 아무것도 안 빼서(비WFA), 진입할 수 없는
    15:00 이후 행 — 라벨이 강제 0 인 동시호가 — 이 학습에 들어갔다.

    market_type='KRX' 는 호출부가 이미 'KRX 장중 청산 모드' 일 때만 넘긴다(기존 규약 유지).
    """
    from shared.core.market_utils import decision_point_mask
    out = df.copy()
    target = triple_barrier_labeling(out, predict_ahead_candles, take_profit, stop_loss, cost, market_type=market_type)
    keep = decision_point_mask(out['timestamp'], market_type == 'KRX') if 'timestamp' in out.columns \
        else np.ones(len(out), dtype=bool)
    out['target'] = target
    return out[keep].copy().reset_index(drop=True)


def build_final_train_df(df, t_start, t_end, predict_ahead_candles, take_profit, stop_loss, cost,
                         market_type='CRYPTO', exclude_warmup=False):
    """[FIX-9] 최종 학습셋 생성: 학습 구간 슬라이스 -> 라벨링(연속 시계열) -> 학습 경계 직전 predict_ahead 봉
    purge (라벨이 경계 뒤 가격을 참조하지 않도록) -> 의사결정 시점 행만 유지.

    `exclude_warmup` 은 하위 호환용 인자다 — 의사결정 시점 마스크가 웜업을 포함해 더 넓게 처리한다.
    """
    ts = df['timestamp']
    out = df[(ts >= t_start) & (ts < t_end)].copy().reset_index(drop=True)
    pa = int(predict_ahead_candles)
    labeled = label_decision_points(out.assign(_row=np.arange(len(out))), predict_ahead_candles,
                                    take_profit, stop_loss, cost, market_type=market_type)
    if pa > 0:
        labeled = labeled[labeled['_row'] < len(out) - pa]
    return labeled.drop(columns=['_row']).dropna(subset=['target']).copy().reset_index(drop=True)


def pick_best_outer_trial(study):
    """[FIX-16] inner_best_params 가 설정된 완료 트라이얼 중 최고 점수를 반환 (없으면 None).

    모든 트라이얼이 -99(데이터 부족/예외) 로 조기 반환되면 user_attrs 에 inner_best_params 가 없어
    study.best_trial.user_attrs['inner_best_params'] 가 KeyError 를 일으키던 문제를 방어한다.
    """
    cands = [t for t in study.trials
             if t.value is not None and 'inner_best_params' in t.user_attrs]
    if not cands:
        return None
    return max(cands, key=lambda t: t.value)


def triple_barrier_labeling(df, predict_ahead_candles, take_profit, stop_loss, cost, market_type='CRYPTO', emergency_sl_mult=None):
    """하이브리드 트리플 배리어 라벨링 (Hybrid Triple Barrier Labeling) — NumPy 벡터화 구현
    
    📌 하이브리드 배리어 규칙 (Train-Serve Skew 100% 해소 & Whipsaw 방지):
      - Hard Emergency SL: 인트라바 저가(Low)가 비상 손절선(emergency_barrier = Close - ATR * emergency_sl_mult) 터치 시 즉시 하락(손절) 확정
      - Soft SL: 캔들 완성(종가 Close) 시점에 하단 장벽(lower_barrier = Close - ATR * stop_loss) 이하이면 하락(손절) 확정
      - Soft TP: 캔들 완성(종가 Close) 시점에 상단 장벽(upper_barrier = Close + ATR * take_profit) 이상이면 상승(익절) 확정
      - 로직 우선순위: 비상손절(Hard SL) -> Soft SL -> Soft TP (동시 도달 시 보수적 손절 우선)
    
    📌 KRX 전용 — 날짜 경계 Clamp:
      market_type='KRX'인 경우, 미래 캔들 탐색 시 당일 장 마감(15:18)을 초과하면 강제 중단.
    """
    if emergency_sl_mult is None:
        emergency_sl_mult = stop_loss * 2.0

    n = len(df)
    close_prices = (df['target_close'] if 'target_close' in df.columns else df['upbit_close']).values
    low_col = 'target_low' if 'target_low' in df.columns else ('low' if 'low' in df.columns else None)
    low_prices = df[low_col].values if (low_col and low_col in df.columns) else close_prices
    atr_col = 'target_atr' if 'target_atr' in df.columns else ('upbit_atr' if 'upbit_atr' in df.columns else None)
    atr_values = df[atr_col].values if (atr_col and atr_col in df.columns) else (close_prices * 0.015)
    labels = np.zeros(n, dtype=int)

    upper_barrier = close_prices + (atr_values * take_profit)
    lower_barrier = close_prices - (atr_values * stop_loss)
    emergency_barrier = close_prices - (atr_values * emergency_sl_mult)

    # KRX: 각 원점 i에서 탐색 가능한 최대 인덱스(당일 15:18 이전 마지막 캔들) 사전 계산
    if market_type == 'KRX' and 'timestamp' in df.columns:
        from shared.core.market_utils import build_krx_eod_index
        krx_eod_idx = build_krx_eod_index(df['timestamp']).values
    else:
        krx_eod_idx = None

    hit_up   = np.zeros(n, dtype=bool)  # 상단 장벽 최초 도달
    hit_down = np.zeros(n, dtype=bool)  # 하단 장벽 최초 도달

    for ahead in range(1, predict_ahead_candles + 1):
        if ahead >= n:
            break
        valid = np.arange(n - ahead)
        future_close = close_prices[ahead:]
        future_low = low_prices[ahead:]

        # KRX: 날짜 경계를 초과하는 원점은 탐색 대상에서 제외
        if krx_eod_idx is not None:
            within_day = (valid + ahead) <= krx_eod_idx[valid]
            valid = valid[within_day]
            future_close = close_prices[valid + ahead]
            future_low = low_prices[valid + ahead]

        undecided = ~hit_up[valid] & ~hit_down[valid]

        # 1. Hard Emergency SL (인트라바 Low 기준 급락 방어)
        new_emerg = undecided & (future_low <= emergency_barrier[valid])
        if new_emerg.any():
            hit_down[valid[new_emerg]] = True

        # 2. Soft SL (캔들 종가 기준)
        undecided2 = ~hit_up[valid] & ~hit_down[valid]
        new_down = undecided2 & (future_close <= lower_barrier[valid])
        if new_down.any():
            hit_down[valid[new_down]] = True

        # 3. Soft TP (캔들 종가 기준)
        undecided3 = ~hit_up[valid] & ~hit_down[valid]
        new_up = undecided3 & (future_close >= upper_barrier[valid])
        if new_up.any():
            hit_up[valid[new_up]] = True

    labels[hit_up] = 1
    neither = ~hit_up & ~hit_down
    if neither.any():
        final_idx = np.minimum(np.arange(n) + predict_ahead_candles, n - 1)
        if krx_eod_idx is not None:
            # [FIX-10] KRX 타임아웃도 predict_ahead 를 초과하지 않도록 (장마감이 더 가까우면 장마감)
            # [FIX-11] 원점 i가 이미 15:18(강제청산) 이후면 krx_eod_idx[i] < i가 되어
            # 과거 인덱스로 역전될 수 있으므로, 최소 i 자신으로 하한을 둔다(역방향 라벨 오염 방지).
            final_idx = np.maximum(np.minimum(final_idx, krx_eod_idx), np.arange(n))
        final_ret = (close_prices[final_idx] - close_prices) / (close_prices + 1e-8)
        labels[neither & (final_ret > cost)] = 1

    return labels


def triple_barrier_labeling_afml(df, predict_ahead_candles, take_profit, stop_loss, cost, max_hold_candles=None, market_type='CRYPTO', emergency_sl_mult=None):
    """
    정석 AFML 하이브리드 트리플 배리어 & 메타 라벨링 구조
    
    1. Primary Label (상승/하락/횡보 삼진 분류):
       - 비상 손절(Hard SL) 또는 소프트 손절(Soft SL) 먼저 도달: 2 (하락)
       - 상단 장벽(Soft TP) 먼저 도달: 1 (상승)
       - 장벽 미도달 (시간만료): 0 (횡보)
    
    2. Meta Label (진입 신뢰성 이진 분류):
       - 1차 라벨이 1(상승)이고, 실제로 진입하여 청산 시 비용 차감 후 실익이 양수(+)인 경우: 1 (진입 적절)
       - 1차 라벨이 0, 2이거나 실익이 마이너스(-)인 경우: 0 (진입 불가)
    
    max_hold_candles: 최대 보유 캔들 수. None이면 predict_ahead_candles를 사용.
    """
    if max_hold_candles is None:
        max_hold_candles = predict_ahead_candles
    if emergency_sl_mult is None:
        emergency_sl_mult = stop_loss * 2.0

    n = len(df)
    close_prices = (df['target_close'] if 'target_close' in df.columns else df['upbit_close']).values
    low_col = 'target_low' if 'target_low' in df.columns else ('low' if 'low' in df.columns else None)
    low_prices = df[low_col].values if (low_col and low_col in df.columns) else close_prices
    atr_col = 'target_atr' if 'target_atr' in df.columns else ('upbit_atr' if 'upbit_atr' in df.columns else None)
    atr_values = df[atr_col].values if (atr_col and atr_col in df.columns) else (close_prices * 0.015)
    
    upper_barrier = close_prices + (atr_values * take_profit)
    lower_barrier = close_prices - (atr_values * stop_loss)
    emergency_barrier = close_prices - (atr_values * emergency_sl_mult)

    # KRX: 각 원점 i에서 탐색 가능한 최대 인덱스 사전 계산
    if market_type == 'KRX' and 'timestamp' in df.columns:
        from shared.core.market_utils import build_krx_eod_index
        krx_eod_idx = build_krx_eod_index(df['timestamp']).values
    else:
        krx_eod_idx = None

    hit_up   = np.zeros(n, dtype=bool)
    hit_down = np.zeros(n, dtype=bool)
    exit_idx = np.full(n, n - 1, dtype=int)

    effective_max = min(max_hold_candles, predict_ahead_candles)

    for ahead in range(1, effective_max + 1):
        if ahead >= n:
            break
        valid = np.arange(n - ahead)
        future_close = close_prices[ahead:]
        future_low = low_prices[ahead:]

        # KRX: 날짜 경계를 초과하는 원점은 탐색 대상에서 제외
        if krx_eod_idx is not None:
            within_day = (valid + ahead) <= krx_eod_idx[valid]
            valid = valid[within_day]
            future_close = close_prices[valid + ahead]
            future_low = low_prices[valid + ahead]

        undecided = ~hit_up[valid] & ~hit_down[valid]

        # 1. Hard Emergency SL
        new_emerg = undecided & (future_low <= emergency_barrier[valid])
        if new_emerg.any():
            hit_down[valid[new_emerg]] = True
            exit_idx[valid[new_emerg]] = valid[new_emerg] + ahead

        # 2. Soft SL
        undecided2 = ~hit_up[valid] & ~hit_down[valid]
        new_down = undecided2 & (future_close <= lower_barrier[valid])
        if new_down.any():
            hit_down[valid[new_down]] = True
            exit_idx[valid[new_down]] = valid[new_down] + ahead

        # 3. Soft TP
        undecided3 = ~hit_up[valid] & ~hit_down[valid]
        new_up = undecided3 & (future_close >= upper_barrier[valid])
        if new_up.any():
            hit_up[valid[new_up]] = True
            exit_idx[valid[new_up]] = valid[new_up] + ahead

    # 시간만료 처리: effective_max까지 아무 장벽에도 도달하지 않은 경우
    no_barrier = ~hit_up & ~hit_down
    if no_barrier.any():
        final_exit_idx = np.minimum(np.arange(n) + effective_max, n - 1)
        if krx_eod_idx is not None:
            # [FIX-10] KRX 타임아웃도 effective_max(min(mhc, pa)) 를 초과하지 않도록
            # [FIX-11] 원점 i가 이미 15:18(강제청산) 이후면 krx_eod_idx[i] < i가 되어
            # 과거 인덱스로 역전될 수 있으므로, 최소 i 자신으로 하한을 둔다(역방향 라벨 오염 방지).
            final_exit_idx = np.maximum(np.minimum(final_exit_idx, krx_eod_idx), np.arange(n))
        exit_idx[no_barrier] = final_exit_idx[no_barrier]

    primary_labels = np.zeros(n, dtype=int)
    primary_labels[hit_up] = 1
    primary_labels[hit_down] = 2

    meta_labels = np.zeros(n, dtype=int)
    long_signals = (primary_labels == 1)
    if long_signals.any():
        exit_prices = close_prices[exit_idx]
        returns = (exit_prices - close_prices) / (close_prices + 1e-8) - cost
        meta_labels[long_signals & (returns > 0)] = 1

    return primary_labels, meta_labels



def run_standalone_feature_optimization(config, log_fn, check_abort_fn=None, progress_fn=None):
    """
    학습 이전에 별도로 지표 선정(Step 1)과 지표 파라미터 최적화(Step 2)만 수행하여
    결과를 active_config.json에 저장하고 갱신 정보를 리턴합니다.
    """
    from core.feature_tuner import run_auto_feature_selection, run_pre_tuning_features

    selected_features = []
    tuned_params = {}

    if progress_fn:
        progress_fn(2.0, "Step 1/2: 최적 지표 조합 자동 선정 중...")
    try:
        selected_features = run_auto_feature_selection(config, log_fn=log_fn, check_abort_fn=check_abort_fn)
        if selected_features:
            config.setdefault('FEATURE_CONFIG', {})['enabled_features'] = selected_features
            log_fn(f"   ✅ [지표 선정] 완료 → {len(selected_features)}개 지표 확정")
        else:
            log_fn("   ℹ️ [지표 선정] 기존 enabled_features 유지")
    except Exception as e:
        log_fn(f"   ⚠️ [지표 선정] 실패 → 기존 설정 유지: {e}")

    if progress_fn:
        progress_fn(5.0, "Step 2/2: 지표 파라미터 사전 최적화 중...")
    try:
        tuned_params = run_pre_tuning_features(config, log_fn=log_fn)
        if tuned_params:
            try:
                _cfg_disk = load_project_config("CAiModelBaker")
                _fc = _cfg_disk.get('FEATURE_CONFIG', {})
                _fp = _fc.get('feature_params', {})
                _fp.update(tuned_params)
                _fc['feature_params'] = _fp
                _cfg_disk['FEATURE_CONFIG'] = _fc
                save_project_config("CAiModelBaker", _cfg_disk)
                config.setdefault('FEATURE_CONFIG', {}).setdefault('feature_params', {}).update(tuned_params)
            except Exception:
                pass
            log_fn(f"   ✅ [파라미터 최적화] 완료")
        else:
            log_fn("   ℹ️ [파라미터 최적화] 기존 파라미터 유지")
    except Exception as e:
        log_fn(f"   ⚠️ [파라미터 최적화] 실패 → 기존 설정 유지: {e}")

    return {
        "selected_features": selected_features or config.get('FEATURE_CONFIG', {}).get('enabled_features', []),
        "best_params": tuned_params
    }


def detect_boundary_collisions(best_params: dict, ranges: dict, log_fn=print) -> list:
    """
    최적 파라미터가 설정된 탐색 범위의 경계(min/max)에 충돌하는지 감지.

    프로 퀀트 방식: 최적값이 경계에 정확히 위치하면 실제 최적값이 범위 밖에 있을
    가능성이 높다는 신호입니다. 검증 데이터를 보고 범위를 수정하는 look-ahead가 아니라,
    단순 진단 로그만 남기는 데이터 무결성을 해치지 않는 방법입니다.

    Args:
        best_params: Optuna 최적화 결과 파라미터 딕셔너리
        ranges: {파라미터명: (min, max, step)} 범위 딕셔너리
        log_fn: 로그 출력 함수

    Returns:
        경계 충돌 감지 목록 [{"param": ..., "value": ..., "boundary": ..., ...}]
    """
    collisions = []
    float_params = {'take_profit', 'stop_loss', 'learning_rate', 'buy_threshold', 'buy_quantile'}
    for param_name, (pmin, pmax, _step) in ranges.items():
        if param_name not in best_params:
            continue
        val = best_params[param_name]
        # float 파라미터는 부동소수점 오차 허용
        if param_name in float_params:
            is_min = abs(val - pmin) < 1e-9
            is_max = abs(val - pmax) < 1e-9
        else:
            is_min = (val == pmin)
            is_max = (val == pmax)
        if is_min:
            collisions.append({
                "param": param_name,
                "value": float(val),
                "boundary": "min",
                "boundary_value": float(pmin),
                "range": [float(pmin), float(pmax)]
            })
        elif is_max:
            collisions.append({
                "param": param_name,
                "value": float(val),
                "boundary": "max",
                "boundary_value": float(pmax),
                "range": [float(pmin), float(pmax)]
            })
    if collisions:
        log_fn("\n" + "="*60)
        log_fn("⚠️  [경계 충돌 감지] 최적 파라미터가 설정 범위의 경계에 위치합니다")
        log_fn("="*60)
        for c in collisions:
            direction = "최소값" if c["boundary"] == "min" else "최대값"
            log_fn(
                f"   ⚠️  {c['param']:<22} = {c['value']:<10} "
                f"→ 설정 {direction}({c['boundary_value']})와 일치"
            )
        log_fn(
            "   💡 실제 최적값이 현재 범위 밖에 있을 가능성이 있습니다.\n"
            "      Settings → Optuna 범위에서 해당 파라미터의 범위를 확장하여 재시도해 보세요."
        )
        log_fn("="*60)
    return collisions


def process_02_relay_optimization(config, train_start_str, train_end_str, optuna_start_str, optuna_end_str, log_fn, status_update_fn=None, check_abort_fn=None, memo="", progress_fn=None, holdout_start="", purge_candles=None, feature_subset=None, cached_df=None):
    """
    홀드아웃 경계 강제 클램프 적용 (실전 데이터 무결성):
      - holdout_start: 홀드아웃 시작일. 주어지면 WFA/비WFA 모든 학습·검증이 이 경계 이전으로 제한됨.
      - purge_candles: Train/Val 경계에서 라벨 참조(미래 봉) 누출 방지를 위해 제거할 캔들 수.
      - feature_subset: 명시적 피처 목록 (AutoLab Trial별 피처 격리 및 Train-Serve 일치 보장)
      - cached_df: 사전 피처 엔지니어링 완료 데이터프레임 (AutoLab 고속 캐시)
    """
    snapshot_path = get_snapshot_path(config)
    
    if cached_df is None and not os.path.exists(snapshot_path):
        raise FileNotFoundError(f"박제된 스냅샷 파일({snapshot_path})이 없습니다. 1번을 먼저 누르세요.")

    # 사용자가 직접 학습(자동연구 밖)할 때는 로그를 프로젝트 reports/train_run.log 에 서버가 직접 기록한다.
    # (자동연구 중에는 연구 루프가 runs/<시각>/train_run.log 에 이미 기록 중)
    _run_mode = bool(config.get(project_store.RUN_DIR_KEY))
    _sid = config.get("ACTIVE_SESSION_ID")
    if not _run_mode and _sid and _sid not in ("active", "N/A"):
        try:
            log_fn = project_store.tee_log(
                log_fn, os.path.join(project_store.session_root(config, _sid), "reports", project_store.RUN_LOG_FILE),
                truncate=True)
        except Exception:
            pass

    log_fn("\n======================================================================")
    log_fn("👑 [PROCESS 02] 최외곽 매매장벽(정답지) 최적화 결합형 2-STAGE 퀀트 엔진 가동")
    log_fn("======================================================================")

    if progress_fn:
        progress_fn(8.0, "Step 3/3: 2-Stage Optuna 최적화 가동 중...")

    log_fn("\n======================================================================")
    log_fn("🚀 [Step 3/3] 2-Stage Optuna 최적화 시작")
    log_fn("======================================================================")
    
    interval = config.get('INTERVAL', '1m')
    _market_type = detect_market_type(config)
    _intraday_mode = (_market_type == 'KRX' and config.get('INTRADAY_SQUARE_OFF', True))
    
    if cached_df is not None:
        log_fn("⚡ [FastCache] 사전 연산된 메모리 피처 데이터셋 즉시 주입 (0.00s)")
        master_df = cached_df.copy()
    else:
        log_fn(f"📖 로컬 데이터 로딩 중: {snapshot_path}")
        import io as _io
        from shared.data_library.manifest_manager import ManifestManager
        with open(snapshot_path, "rb") as _sf:
            _snap_bytes = _sf.read()
        config["_DATASET"] =ManifestManager.dataset_fingerprint(snapshot_path, content=_snap_bytes)
        master_df = pd.read_csv(_io.BytesIO(_snap_bytes))
        del _snap_bytes
        master_df = master_df.sort_values('timestamp').reset_index(drop=True)
        master_df['timestamp'] = pd.to_datetime(master_df['timestamp'])
        master_df = run_feature_engineering(master_df, interval, config=config)

    # ⚖️ [선험 제약 엔진] 마찰력과 변동성 기반으로 비물리적 탐색 파라미터 사전 클램핑
    try:
        from .physics_constraint import calc_mean_atr_pct, apply_physics_constraints_to_config, slice_pre_holdout
    except (ImportError, ValueError):
        from core.physics_constraint import calc_mean_atr_pct, apply_physics_constraints_to_config, slice_pre_holdout

    # [FIX-2] 물리 제약은 홀드아웃 이전 구간만으로 산출
    _phys_df = slice_pre_holdout(master_df, holdout_start)
    mean_atr = calc_mean_atr_pct(_phys_df)
    config = apply_physics_constraints_to_config(config, mean_atr_pct=mean_atr, df=_phys_df)
    phys = config.get("PHYSICS_CONSTRAINTS", {})
    log_fn(f"⚖️ [물리 선험 제약] ATR: {phys.get('mean_atr_pct', 0.0):.3f}% | 마찰비용: {phys.get('roundtrip_friction_pct', 0.0):.3f}% | 상태: {phys.get('friction_status')} (권장 TP: {phys.get('tp_atr_min')}~{phys.get('tp_atr_max')} ATR, 권장 SL: {phys.get('sl_atr_min')}~{phys.get('sl_atr_max')} ATR, 권장 지평: {phys.get('min_horizon_candles')}~{phys.get('max_hold_candles')}캔들)")

    interval_minutes = parse_interval_minutes(interval)
    
    optuna_cfg = config.get('OPTUNA_CONFIG', {})
    seed = optuna_cfg.get('seed', 42)
    cost = resolve_round_trip_cost(config)
    
    def get_range(cfg_key, default_min, default_max, default_step=None):
        range_cfg = optuna_cfg.get(cfg_key, {})
        vmin = range_cfg.get('user_min', default_min)
        if vmin is None: vmin = range_cfg.get('min', 0.0)
        vmax = range_cfg.get('user_max', default_max)
        if vmax is None: vmax = range_cfg.get('max', 1.0)
        vstep = range_cfg.get('step', default_step)
        return (vmin, vmax, vstep)
    
    target_horizon_cfg = config.get('TARGET_HORIZON_CONFIG', {})
    horizon_mode = target_horizon_cfg.get('mode', 'locked')
    is_horizon_locked = (horizon_mode == 'locked')
    candidate_horizons = target_horizon_cfg.get('candidate_horizons', [3, 6, 12, 24]) if not is_horizon_locked else None
    if is_horizon_locked:
        locked_pa = int(target_horizon_cfg.get('locked_predict_ahead', 6))
        locked_mhc = int(target_horizon_cfg.get('locked_max_hold', locked_pa))
        log_fn(f"🔒 [Target Horizon] 고정 사정거리 모드: 예측 {locked_pa}캔들, 최대보유 {locked_mhc}캔들 (Moving Target 원천 차단)")
    else:
        locked_pa = None
        locked_mhc = None
        log_fn(f"🔍 [Target Horizon] 가변 사정거리 탐색 모드 (후보군: {candidate_horizons})")
    
    pa_range = get_range('predict_ahead_candles_range', 8, 32, 1)
    mhc_range = get_range('max_hold_candles_range', 24, 64, 1)
    tp_range = get_range('take_profit_range', 0.01, 0.05, 0.001)
    sl_range = get_range('stop_loss_range', 0.01, 0.03, 0.001)
    lr_range = get_range('learning_rate_range', 0.01, 0.10, 0.005)
    md_range = get_range('max_depth_range', 3, 10, 1)
    nl_range = get_range('num_leaves_range', 15, 127, 1)
    mcs_range = get_range('min_child_samples_range', 10, 100, 1)
    bt_range = get_range('buy_threshold_range', 0.50, 0.65, 0.005)
    # 진입 문턱 탐색 방식: 'quantile'(기본, 모델 확률의 상위 분위수) | 'absolute'(구 방식, 절대 확률)
    from shared.features.feature_registry import DEFAULT_OPTUNA_CONFIG as _OPT_DEFAULTS
    threshold_mode = str(optuna_cfg.get('threshold_mode', _OPT_DEFAULTS['threshold_mode'])).lower()
    _bq_def = _OPT_DEFAULTS['buy_quantile_range']
    bq_range = get_range('buy_quantile_range', _bq_def['user_min'], _bq_def['user_max'], None)
    cd_range = get_range('cooldown_minutes_range', 1, 30, 1)

    # 전체 Optuna 탐색 범위 (경계 충돌 감지용)
    optuna_ranges = {
        'take_profit': tp_range,
        'stop_loss': sl_range,
        'learning_rate': lr_range,
        'max_depth': md_range,
        'num_leaves': nl_range,
        'min_child_samples': mcs_range,
        'cooldown_minutes': cd_range,
    }
    if threshold_mode == 'quantile':
        optuna_ranges['buy_quantile'] = bq_range
        log_fn(f"🎚️ [진입 문턱] 분위수 모드: 학습 확률 상위 {(1 - bq_range[1]) * 100:.1f}%~{(1 - bq_range[0]) * 100:.1f}% 에서 진입 (모델마다 자기 확률 분포로 환산)")
    else:
        optuna_ranges['buy_threshold'] = bt_range
    if not is_horizon_locked:
        optuna_ranges['predict_ahead_candles'] = pa_range
        optuna_ranges['max_hold_candles'] = mhc_range

    feature_config = load_feature_config()
    if feature_subset is not None and len(feature_subset) > 0:
        features = list(feature_subset)
    else:
        features = feature_config.get('enabled_features')
        if not features:
            features = ['binance_ret_5m', 'kimchi_premium', 'target_ma_ratio', 'target_trade_bid_ratio', 'binance_spot_taker_ratio', 'target_rsi_daily', 'target_hour', 'target_is_weekend']
    
    # master_df에 피처 컬럼이 존재하는지 검증 및 필요시 엔지니어링 수행
    valid_features = [f for f in features if f in master_df.columns]
    if len(valid_features) < len(features):
        master_df = run_feature_engineering(master_df, interval, config=config)
        valid_features = [f for f in features if f in master_df.columns]
    
    if valid_features:
        features = valid_features
    else:
        log_fn(f"⚠️ 경고: 지정된 피처가 데이터셋에 없어 기본 피처로 복구합니다.")
        features = [col for col in ['target_rsi_daily', 'target_ma_ratio', 'target_trade_bid_ratio', 'target_hour', 'target_is_weekend'] if col in master_df.columns]

    # 커버리지 게이트 — 학습 입구에서 한 번 더 막는다. 사용자가 고른 지표·단일 실행 모드는 IC 스크리닝을
    # 거치지 않으므로, 데이터가 학습 기간을 덮지 못하거나 학습/홀드아웃 유효율이 다른 지표가 그대로 들어온다.
    from shared.features.feature_health import find_insufficient_coverage
    from shared.core.market_utils import decision_point_mask
    _cov_train = slice_pre_holdout(master_df, holdout_start)
    if train_start_str:
        _cov_train = _cov_train[_cov_train['timestamp'] >= pd.to_datetime(train_start_str)]
    _cov_recent = master_df[master_df['timestamp'] >= pd.to_datetime(holdout_start)] if holdout_start else None
    if _cov_recent is not None and len(_cov_recent) == 0:
        _cov_recent = None
    _low_cov = find_insufficient_coverage(
        _cov_train, features, row_mask=decision_point_mask(_cov_train['timestamp'], _intraday_mode),
        recent_df=_cov_recent,
        recent_mask=decision_point_mask(_cov_recent['timestamp'], _intraday_mode) if _cov_recent is not None else None)
    if _low_cov:
        log_fn(f"⛔ [커버리지 게이트] 학습에서 제외하는 지표 {len(_low_cov)}개:")
        for _fid, _why in _low_cov.items():
            log_fn(f"   - {_fid}: {_why}")
        features = [f for f in features if f not in _low_cov]
        if not features:
            raise ValueError("커버리지 게이트를 통과한 지표가 없습니다 — 데이터가 학습 기간을 덮는 지표를 선택하세요")

    categorical_features =[f for f in features if f in ['target_hour', 'target_is_weekend', 'upbit_hour', 'upbit_is_weekend', 'krx_session_time']]
    
    # 지표별 패널티 파라미터 구성 (현업 퀀트 기법)
    penalties_dict = feature_config.get("feature_penalties", {})
    feature_penalties = [float(penalties_dict.get(feat, 1.0)) for feat in features]
    
    log_fn(f"📊 사용 지표 ({len(features)}개): {', '.join(features)}")
    log_fn(f"🎯 지표 가중치(패널티) 설정 완료")

    wfa_enabled = optuna_cfg.get('wfa_enabled', False)
    outer_study_best_value = -999.0
    
    if wfa_enabled:
        train_months = optuna_cfg.get('wfa_train_months', 12)
        opt_months = optuna_cfg.get('wfa_opt_months', 3)
        test_months = optuna_cfg.get('wfa_test_months', 3)
        wfa_start_date_str = optuna_cfg.get('wfa_start_date', '')
        wfa_end_date_str = optuna_cfg.get('wfa_end_date', '')
        
        first_date, last_date = master_df['timestamp'].iloc[0], master_df['timestamp'].iloc[-1]
        
        if wfa_start_date_str:
            try:
                user_start_date = pd.to_datetime(wfa_start_date_str)
                # 날짜 단위로 비교한다 — 첫 bar 가 09:00 이라 같은 날짜도 '0일치 부족' 으로 경고되던 오탐 제거
                if user_start_date.normalize() < first_date.normalize():
                    # [Pre-flight] meta.json으로 기간 충돌 정밀 진단
                    shortfall_days = int((first_date - user_start_date).days)
                    log_fn(
                        f"⚠️ [Pre-flight] WFA 시작일({wfa_start_date_str}) < 마스터 데이터셋 시작일({first_date.strftime('%Y-%m-%d')})\n"
                        f"   → {shortfall_days}일치 데이터 부족. 마스터 데이터셋 첫 일자({first_date.strftime('%Y-%m-%d')})로 자동 클램핑.\n"
                        f"   → 학습 기간이 의도보다 짧을 수 있습니다. Data Vault에서 '{user_start_date.strftime('%Y-%m')}' 이전부터 재빌드하면 해소됩니다."
                    )
                elif user_start_date > last_date:
                    raise ValueError(f"지정된 WFA 시작일({wfa_start_date_str})이 전체 데이터의 끝 일자({last_date.strftime('%Y-%m-%d')})보다 미래입니다.")
                else:
                    first_date = user_start_date
            except Exception as e:
                log_fn(f"⚠️ WFA 시작일 파싱 실패: {e}. 기본 첫 일자로 구동합니다.")
                
        if wfa_end_date_str:
            try:
                user_end_date = pd.to_datetime(wfa_end_date_str)
                if user_end_date > last_date:
                    log_fn(f"⚠️ 경고: 지정된 WFA 종료일({wfa_end_date_str})이 전체 데이터의 마지막 일자({last_date.strftime('%Y-%m-%d')})보다 미래입니다. 마지막 일자로 설정합니다.")
                elif user_end_date < first_date:
                    raise ValueError(f"지정된 WFA 종료일({wfa_end_date_str})이 시작일({first_date.strftime('%Y-%m-%d')})보다 이전입니다.")
                else:
                    last_date = user_end_date
            except Exception as e:
                log_fn(f"⚠️ WFA 종료일 파싱 실패: {e}. 기본 마지막 일자로 구동합니다.")

        # ── 홀드아웃 경계 강제 클램프 (실전 데이터 무결성) ──
        # 홀드아웃 데이터는 최종 Test에서만 사용 — WFA 최적화/학습이 홀드아웃을 침범하지 않도록 경계 강제
        _mkt_key = str(config.get('SYMBOL_UPBIT') or config.get('SYMBOL') or 'KRX')
        holdout_msg = ""
        if holdout_start:
            try:
                _holdout_dt = pd.to_datetime(holdout_start)
                if _holdout_dt <= first_date:
                    log_fn(f"ℹ️ 홀드아웃 시작일({holdout_start})이 WFA 시작 이전 — WFA가 전부 홀드아웃 경계 앞에 위치하므로 정상")
                elif _holdout_dt >= last_date:
                    log_fn(f"ℹ️ 홀드아웃 시작일({holdout_start})이 WFA 종료일 이후 — WFA가 홀드아웃을 침범하지 않음 (정상)")
                else:
                    # [R-1] 홀드아웃 시작 **직전 거래일** 의 마지막 bar 까지만 WFA 사용
                    #      종전 `holdout_start - 1 bar`(달력 산술)는 휴장일을 고려하지 않았다.
                    _prev_td = prev_trading_day_strict(_holdout_dt, _mkt_key)
                    if _prev_td:
                        _prev_date = pd.Timestamp(_prev_td).date()
                        _ts_series = pd.to_datetime(master_df['timestamp'], errors='coerce')
                        _mask = _ts_series.dt.date <= _prev_date
                        if bool(_mask.any()):
                            last_date = _ts_series[_mask].max()
                            holdout_msg = (
                                f" → 홀드아웃({holdout_start}) 직전 거래일({_prev_td}) "
                                f"{pd.Timestamp(last_date).strftime('%Y-%m-%d %H:%M')} 까지 강제 클램프"
                            )
                        else:
                            last_date = _holdout_dt - pd.Timedelta(minutes=interval_minutes)
                            holdout_msg = f" → 홀드아웃({holdout_start}) 직전으로 강제 클램프 (직전 거래일 bar 부재)"
                    else:
                        last_date = _holdout_dt - pd.Timedelta(minutes=interval_minutes)
                        holdout_msg = f" → 홀드아웃({holdout_start}) 직전으로 강제 클램프"
            except Exception as e:
                log_fn(f"⚠️ 홀드아웃 시작일 파싱 실패 — 클램프 생략: {e}")

        # purge_candles 기본값: 라벨 참조 최대치(pa_max) 기준 자동 설정 (미래 봉 누출 방지)
        if purge_candles is None:
            purge_candles = int(pa_range[1])

        log_fn(f"🔄 [WFA 모드 가동] 범위: {first_date.strftime('%Y-%m-%d')} ~ {last_date.strftime('%Y-%m-%d')}{holdout_msg} | Train: {train_months}개월, Opt: {opt_months}개월, Test: {test_months}개월")
        log_fn(f"🧹 [Purge] 경계 라벨 참조 방지 캔들: {purge_candles}개")
        
        # purge/embargo: 경계에서 미래 라벨 참조(누출)를 차단하기 위한 분 단위 시간
        purge_period = pd.Timedelta(minutes=purge_candles * interval_minutes)

        # ── [R-1] 거래일 기준 WFA 창 생성 (SSOT: shared.split_planner.build_wfa_windows) ──
        # 종전: train_days = train_months × 30.44 (**달력일**) → 창 경계가 휴장일에 떨어졌다.
        #       실측: Opt 창 종료가 2026-08-23(일) → 경계가 다음 거래일로 밀려 창이 왜곡됨.
        # 개월 → 거래일 환산은 시장별 거래일 밀도 SSOT 를 사용한다 (KRX 21.0 / CRYPTO 30.44).
        _tdpm = trading_days_per_month(_mkt_key)
        train_td = max(1, int(round(float(train_months) * _tdpm)))
        opt_td = max(1, int(round(float(opt_months) * _tdpm)))
        test_td = max(1, int(round(float(test_months) * _tdpm)))
        log_fn(
            f"📅 [WFA/R-1] 창 크기(거래일 환산): Train {train_td} / Opt {opt_td} / Test {test_td} 거래일 "
            f"(월당 {_tdpm}거래일 · 입력 {train_months}/{opt_months}/{test_months}개월)"
        )

        # [R-1] 창 생성 — 거래일 기준 SSOT (경계는 실재 거래일의 첫 bar 시각으로 스냅)
        _wfa_ts = master_df.loc[
            (master_df['timestamp'] >= first_date) & (master_df['timestamp'] <= last_date), 'timestamp'
        ]
        windows = build_wfa_windows(
            _wfa_ts, train_td, opt_td, test_td, interval_minutes=interval_minutes
        )

        if not windows:
            raise ValueError(
                f"데이터 부족: 시작일({first_date.strftime('%Y-%m-%d')}) 기준 "
                f"Train {train_td} + Opt {opt_td} + Test {test_td} 거래일 이상 여유 데이터가 있어야 "
                f"WFA 윈도우 구성이 가능합니다."
            )
        all_test_trades, best_params_per_window = [], []
        wfa_final_metrics = {}
        window_best_scores = []
        window_test_scores = {}  # {w_idx: OOS 거래당 평균수익} — 진단 전용 (파라미터 가중에는 사용하지 않음, OOS 오염 방지)
        window_test_profits = {}  # {w_idx: OOS 순수익률 합} — edge decay 진단용 (창 인덱스로 정렬)
        window_best_idx = []      # best_params_per_window / window_best_scores 와 1:1 대응하는 창 인덱스

        wfa_outer_trials = optuna_cfg.get('wfa_outer_trials', 15)
        wfa_inner_trials = optuna_cfg.get('wfa_inner_trials', 15)

        for w_idx, w in enumerate(windows):
            if check_abort_fn and check_abort_fn():
                raise RuntimeError("Aborted by user")
            # purge 적용: Train 경계에서 purge_period만큼 제거 — Train 라벨이 Opt 구간 가격을 참조하지 않도록 차단
            w_train_df = master_df[(master_df['timestamp'] >= w['train_start']) & (master_df['timestamp'] < (w['train_end'] - purge_period))].copy().reset_index(drop=True)
            w_val_df = master_df[(master_df['timestamp'] >= w['opt_start']) & (master_df['timestamp'] < (w['opt_end'] - purge_period))].copy().reset_index(drop=True)
            
            # 📌 [Fast-Index] NumPy 1D C-배열 및 KRX 마스크 1회 사전 추출 (.iloc 오버헤드 100% 제거)
            val_close_col = 'target_close' if 'target_close' in w_val_df.columns else 'upbit_close'
            val_low_col = 'target_low' if 'target_low' in w_val_df.columns else ('low' if 'low' in w_val_df.columns else None)
            val_atr_col = 'target_atr' if 'target_atr' in w_val_df.columns else ('upbit_atr' if 'upbit_atr' in w_val_df.columns else None)
            val_prices = w_val_df[val_close_col].to_numpy()
            val_lows = w_val_df[val_low_col].to_numpy() if (val_low_col and val_low_col in w_val_df.columns) else val_prices
            val_timestamps = w_val_df['timestamp'].to_numpy()
            val_atrs = w_val_df[val_atr_col].to_numpy() if val_atr_col else (val_prices * 0.015)
            val_n = len(val_prices)
            
            if _intraday_mode:
                val_krx_force_exit_mask = np.array([is_krx_force_exit(t) for t in w_val_df['timestamp']])
                val_krx_entry_allowed_mask = np.array([is_krx_entry_allowed(t) for t in w_val_df['timestamp']])
            else:
                val_krx_force_exit_mask = np.zeros(val_n, dtype=bool)
                val_krx_entry_allowed_mask = np.ones(val_n, dtype=bool)

            w_best_val = -999.0
            _w_label_cache = {}

            def w_outer_objective(outer_trial):
                if check_abort_fn and check_abort_fn():
                    raise RuntimeError("Aborted by user")
                if is_horizon_locked:
                    predict_ahead_candles = locked_pa
                    max_hold_candles = locked_mhc
                elif candidate_horizons and len(candidate_horizons) > 0:
                    predict_ahead_candles = outer_trial.suggest_categorical('predict_ahead_candles', candidate_horizons)
                    max_hold_candles = predict_ahead_candles
                else:
                    predict_ahead_candles = outer_trial.suggest_int('predict_ahead_candles', pa_range[0], pa_range[1])
                    max_hold_candles = outer_trial.suggest_int('max_hold_candles', min(mhc_range[0], predict_ahead_candles), min(mhc_range[1], predict_ahead_candles))
                take_profit = outer_trial.suggest_float('take_profit', tp_range[0], tp_range[1], step=tp_range[2])
                stop_loss = outer_trial.suggest_float('stop_loss', sl_range[0], sl_range[1], step=sl_range[2])

                _cache_key = (predict_ahead_candles, round(take_profit, 5), round(stop_loss, 5))
                if _cache_key not in _w_label_cache:
                    _tmp = label_decision_points(w_train_df, predict_ahead_candles, take_profit, stop_loss, cost, market_type='KRX' if _intraday_mode else 'CRYPTO')
                    _tmp.dropna(subset=['target'], inplace=True)
                    _w_label_cache[_cache_key] = _tmp[features + ['target']].copy() if len(_tmp) >= 200 else None
                _cached_df = _w_label_cache[_cache_key]
                if _cached_df is None:
                    outer_trial.set_user_attr('skipped_insufficient_train_rows', True)  # [FIX-16] inner_best_params 미설정 경로 표식
                    return -99.0
                train_data = lgb.Dataset(_cached_df[features], label=_cached_df['target'], categorical_feature=categorical_features)
                
                def w_inner_objective(inner_trial):
                    if check_abort_fn and check_abort_fn():
                        raise RuntimeError("Aborted by user")
                    
                    W = len(windows)
                    O = wfa_outer_trials
                    I = wfa_inner_trials
                    total_steps = W * O * I
                    current_step = (w_idx * O * I) + (outer_trial.number * I) + inner_trial.number
                    pct = 10.0 + (current_step / total_steps) * 85.0
                    if progress_fn:
                        progress_fn(pct, f"WFA 최적화 진행 중 (윈도우 {w_idx+1}/{W}): Trial {outer_trial.number * I + inner_trial.number + 1}/{O * I}")

                    max_depth_val = inner_trial.suggest_int('max_depth', md_range[0], md_range[1])
                    num_leaves_val = inner_trial.suggest_int('num_leaves', nl_range[0], nl_range[1])

                    lgb_params = {
                        'objective': 'binary', 'metric': 'binary_logloss',
                        'learning_rate': inner_trial.suggest_float('learning_rate', lr_range[0], lr_range[1]),
                        'max_depth': max_depth_val,
                        'num_leaves': num_leaves_val,
                        'min_child_samples': inner_trial.suggest_int('min_child_samples', mcs_range[0], mcs_range[1]),
                        'is_unbalance': False, 'verbose': -1, 'seed': seed, 'n_jobs': -1,
                        'feature_pre_filter': False,
                        'feature_penalty': feature_penalties
                    }
                    if threshold_mode == 'quantile':
                        _entry = {'buy_quantile': inner_trial.suggest_float('buy_quantile', bq_range[0], bq_range[1])}
                    else:
                        _entry = {'buy_threshold': inner_trial.suggest_float('buy_threshold', bt_range[0], bt_range[1])}
                    cooldown_minutes = inner_trial.suggest_int('cooldown_minutes', cd_range[0], cd_range[1])
                    try:
                        t_model = lgb.train(lgb_params, train_data, num_boost_round=NUM_BOOST_ROUND)
                        preds_val = t_model.predict(w_val_df[features])
                        buy_threshold = threshold_for_model(t_model, _cached_df[features], _entry, lgb_params=lgb_params, y_train=_cached_df['target'], purge=max(predict_ahead_candles, max_hold_candles), categorical_feature=categorical_features)
                    except BaseException as _trial_err:  # [FIX-16] 타입은 유지하되 원인을 기록 (버그 은폐 방지)
                        _logger.exception("WFA inner trial 예외 (window=%s, outer=%s, inner=%s): %s", w_idx, outer_trial.number, inner_trial.number, _trial_err)
                        return -99.0
                    
                    total_gain, total_loss, trades_v, win_v, p_cum, peak, mdd_v = 0.0, 0.0, 0, 0, 1.0, 1.0, 0.0001
                    holding, entry_price, entry_time_idx, cooldown_until = False, 0.0, 0, 0
                    cooldown_candles = max(1, int(cooldown_minutes / interval_minutes))
                    trade_logs = []
                    for idx in range(val_n):
                        if idx % 100 == 0 and check_abort_fn and check_abort_fn():
                            raise RuntimeError("Aborted by user")
                        current_price = val_prices[idx]
                        current_low = val_lows[idx]
                        current_time = val_timestamps[idx]
                        if holding:
                            entry_atr = val_atrs[entry_time_idx]
                            krx_force_exit = (_intraday_mode and val_krx_force_exit_mask[idx])
                            _exit = resolve_exit(
                                entry_price=entry_price, entry_atr=entry_atr,
                                current_price=current_price, current_low=current_low,
                                take_profit=take_profit, stop_loss=stop_loss,
                                emergency_sl_mult=stop_loss * 2.0,
                                max_hold_candles=max_hold_candles,
                                elapsed_candles=idx - entry_time_idx, cost=cost,
                                force_exit=krx_force_exit, is_candle_close=True,
                                interval_minutes=interval_minutes,
                            )
                            if _exit:
                                net_p = _exit['net_return'] / 100.0
                                p_cum *= (1 + net_p); trades_v += 1
                                if net_p > 0:
                                    total_gain += net_p; win_v += 1
                                else:
                                    total_loss += abs(net_p)
                                holding = False; cooldown_until = idx + cooldown_candles
                                trade_logs.append({'entry_time': val_timestamps[entry_time_idx], 'exit_time': current_time, 'entry_price': entry_price, 'exit_price': _exit['exit_price'], 'hold_mins': int((idx - entry_time_idx) * interval_minutes), 'prob': preds_val[entry_time_idx], 'exit_type': _exit['exit_type'], 'net_return': _exit['net_return']})
                        elif idx >= cooldown_until and preds_val[idx] >= buy_threshold:
                            if _intraday_mode and not val_krx_entry_allowed_mask[idx]:
                                pass
                            else:
                                holding = True; entry_price = current_price; entry_time_idx = idx


                        if holding:
                            p_curr = p_cum * (1.0 + (current_price - entry_price) / entry_price - (cost / 2.0))
                        else:
                            p_curr = p_cum
                        if p_curr > peak: peak = p_curr
                        dd = (peak - p_curr) / peak
                        if dd > mdd_v: mdd_v = dd
                    val_duration_days = max(1.0, float((val_timestamps[-1] - val_timestamps[0]) / np.timedelta64(1, 'D')))
                    val_months = val_duration_days / 30.44
                    # [D-11] 단일 점수 함수 (PF 상한 + 절대 최소 거래 수 가드)
                    score, pf, _pf_raw, required_min_trades = compute_wfa_score(
                        total_gain, total_loss, trades_v, mdd_v, val_months, optuna_cfg
                    )
                    
                    if trades_v < required_min_trades:
                        return score

                    nonlocal w_best_val
                    if score > w_best_val:
                        w_best_val = score
                        log_fn(f"\n🔥 [WFA Window {w_idx+1}] New Best Score: {score:.4f} (Trades: {trades_v} [요구: {required_min_trades:.1f}회], WinRate: {win_v/trades_v*100:.1f}%, MDD: {mdd_v*100:.2f}%)")
                        log_fn(f"⚙️ Params: TP({take_profit:.2f} ATR), SL({stop_loss:.2f} ATR), Hold({max_hold_candles}), BuyTh({buy_threshold:.3f}{', 상위 %.2f%%' % ((1 - _entry['buy_quantile']) * 100) if 'buy_quantile' in _entry else ''})")
                        for t in trade_logs[:30]:
                            log_fn(f"  ↳ {t['entry_time']} ~ {t['exit_time']} | Entry: {float(t['entry_price']):,.0f} -> Exit: {float(t['exit_price']):,.0f} | Hold: {t['hold_mins']}m | {t['exit_type']} | {t['net_return']:+.2f}%")
                        if len(trade_logs) > 30:
                            log_fn(f"  ↳ ... and {len(trade_logs) - 30} more trades.")

                    return score

                # [FIX-12] 창/트라이얼별 seed 오프셋 (동일 seed=42 반복으로 인한 탐색 상관 제거)
                inner_study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=seed + (w_idx + 1) * 10007 + outer_trial.number))
                inner_study.optimize(w_inner_objective, n_trials=wfa_inner_trials)
                outer_trial.set_user_attr('inner_best_params', inner_study.best_params)
                return inner_study.best_value

            w_outer_study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=seed + w_idx))
            w_outer_study.optimize(w_outer_objective, n_trials=wfa_outer_trials)
            _w_best_trial = pick_best_outer_trial(w_outer_study)  # [FIX-16] 전 트라이얼 -99 등으로 inner_best_params 부재 시 None
            if _w_best_trial is None:
                log_fn(f"⚠️ [WFA Window {w_idx+1}] 유효한 outer 트라이얼 없음(학습 행 부족/예외) — 해당 윈도우 건너뜀")
            if _w_best_trial is not None:
                bp_w = {**_w_best_trial.params, **_w_best_trial.user_attrs['inner_best_params']}
                if is_horizon_locked:
                    bp_w['predict_ahead_candles'] = locked_pa
                    bp_w['max_hold_candles'] = locked_mhc
                else:
                    # 지평 후보 모드는 max_hold 를 탐색하지 않고 지평과 같게 둔다(w_outer_objective) — 창 파라미터에도 같은 값을 채워야
                    # 아래 진단·OOS 백테스트가 KeyError 없이 학습 때와 같은 보유 한도로 돈다 (2026-10-20 'max_hold_candles' 진단 실패)
                    bp_w.setdefault('max_hold_candles', bp_w['predict_ahead_candles'])
                best_params_per_window.append(bp_w)
                window_best_scores.append(_w_best_trial.value)
                window_best_idx.append(w_idx)
                
                # ── WFA 마지막 윈도우인 경우 최우수 조합의 최적화(Validation) 성적 산출 ──
                if w_idx == len(windows) - 1:
                    try:
                        labeled_train_w = label_decision_points(w_train_df, bp_w['predict_ahead_candles'], bp_w['take_profit'], bp_w['stop_loss'], cost, market_type='KRX' if _intraday_mode else 'CRYPTO')
                        labeled_train_w.dropna(subset=['target'], inplace=True)
                        w_params_eval = {
                            'objective': 'binary',
                            'learning_rate': bp_w['learning_rate'],
                            'max_depth': bp_w['max_depth'],
                            'num_leaves': bp_w['num_leaves'],
                            'min_child_samples': bp_w['min_child_samples'],
                            'is_unbalance': False,
                            'verbose': -1,
                            'seed': seed,
                            'n_jobs': -1,
                            'feature_penalty': feature_penalties
                        }
                        t_model_w = lgb.train(w_params_eval, lgb.Dataset(labeled_train_w[features], label=labeled_train_w['target'], categorical_feature=categorical_features), num_boost_round=NUM_BOOST_ROUND)
                        preds_val_w = t_model_w.predict(w_val_df[features])
                        _thr_eval_w = threshold_for_model(t_model_w, labeled_train_w[features], bp_w, lgb_params=w_params_eval, y_train=labeled_train_w['target'], purge=max(bp_w['predict_ahead_candles'], bp_w.get('max_hold_candles', 0)), categorical_feature=categorical_features)
                        
                        total_gain, total_loss, trades_v, win_v, p_cum, peak, mdd_v = 0.0, 0.0, 0, 0, 1.0, 1.0, 0.0001
                        holding, entry_price, entry_time_idx, cooldown_until = False, 0.0, 0, 0
                        cooldown_candles = max(1, int(bp_w['cooldown_minutes'] / interval_minutes))
                        for idx in range(val_n):
                            current_price = val_prices[idx]
                            current_low = val_lows[idx]
                            current_time = val_timestamps[idx]
                            if holding:
                                entry_atr = val_atrs[entry_time_idx]
                                krx_force_exit = (_intraday_mode and val_krx_force_exit_mask[idx])
                                _exit = resolve_exit(
                                    entry_price=entry_price, entry_atr=entry_atr,
                                    current_price=current_price, current_low=current_low,
                                    take_profit=bp_w['take_profit'], stop_loss=bp_w['stop_loss'],
                                    emergency_sl_mult=bp_w.get('emergency_sl_mult', bp_w['stop_loss'] * 2.0),
                                    max_hold_candles=bp_w['max_hold_candles'],
                                    elapsed_candles=idx - entry_time_idx, cost=cost,
                                    force_exit=krx_force_exit, is_candle_close=True,
                                    interval_minutes=interval_minutes,
                                )
                                if _exit:
                                    net_p = _exit['net_return'] / 100.0
                                    p_cum *= (1 + net_p); trades_v += 1
                                    if net_p > 0:
                                        total_gain += net_p; win_v += 1
                                    else:
                                        total_loss += abs(net_p)
                                    holding = False; cooldown_until = idx + cooldown_candles
                            elif idx >= cooldown_until and preds_val_w[idx] >= _thr_eval_w:
                                if _intraday_mode and not val_krx_entry_allowed_mask[idx]:
                                    pass
                                else:
                                    holding = True; entry_price = current_price; entry_time_idx = idx


                            if holding:
                                p_curr = p_cum * (1.0 + (current_price - entry_price) / entry_price - (cost / 2.0))
                            else:
                                p_curr = p_cum
                            if p_curr > peak: peak = p_curr
                            dd = (peak - p_curr) / peak
                            if dd > mdd_v: mdd_v = dd
                            
                        val_duration_days = max(1.0, float((val_timestamps[-1] - val_timestamps[0]) / np.timedelta64(1, 'D')))
                        val_months = val_duration_days / 30.44
                        # [D-11] 단일 점수 함수 (PF 상한 + 절대 최소 거래 수 가드)
                        _wfa_score, pf, pf_raw, required_min_trades = compute_wfa_score(
                            total_gain, total_loss, trades_v, mdd_v, val_months, optuna_cfg
                        )
                        
                        wfa_final_metrics = {
                            'score': float(_wfa_score),
                            'trades': trades_v,
                            'required_trades': float(required_min_trades),
                            'win_rate': float(win_v / trades_v * 100) if trades_v > 0 else 0.0,
                            'mdd': float(mdd_v * 100),
                            'profit_factor': float(pf),
                            'profit_factor_raw': float(pf_raw),
                            'total_return': float((p_cum - 1.0) * 100)
                        }
                    except Exception as wfa_err:
                        log_fn(f"⚠️ WFA 마지막 윈도우 지표 진단 연산 실패: {wfa_err}")
                
                # 의사결정 시점(KRX: 09:10~15:00)만 학습 — 백테스터 진입 규칙과 같은 정의
                labeled_train = label_decision_points(w_train_df, bp_w['predict_ahead_candles'], bp_w['take_profit'], bp_w['stop_loss'], cost, market_type='KRX' if _intraday_mode else 'CRYPTO')
                labeled_train.dropna(subset=['target'], inplace=True)
                
                if len(labeled_train) >= 200:
                    train_ds = lgb.Dataset(labeled_train[features], label=labeled_train['target'], categorical_feature=categorical_features)
                    w_params = {
                        'objective': 'binary',
                        'learning_rate': bp_w['learning_rate'],
                        'max_depth': bp_w['max_depth'],
                        'num_leaves': bp_w['num_leaves'],
                        'min_child_samples': bp_w['min_child_samples'],
                        'is_unbalance': False,
                        'verbose': -1,
                        'seed': seed,
                        'n_jobs': -1,
                        'feature_pre_filter': False,
                        'feature_penalty': feature_penalties
                    }
                    try:
                        w_model = lgb.train(w_params, train_ds, num_boost_round=NUM_BOOST_ROUND)  # [FIX-3/4] 튜닝/최종 저장과 동일 라운드
                        w_test_df = master_df[(master_df['timestamp'] >= w['test_start']) & (master_df['timestamp'] < w['test_end'])].copy().reset_index(drop=True)
                        if len(w_test_df) > 0:
                            preds_test = w_model.predict(w_test_df[features])
                            _thr_oos_w = threshold_for_model(w_model, labeled_train[features], bp_w, lgb_params=w_params, y_train=labeled_train['target'], purge=max(bp_w['predict_ahead_candles'], bp_w.get('max_hold_candles', 0)), categorical_feature=categorical_features)
                            
                            # 📌 [Fast-Index] OOS 테스트용 NumPy 배열 및 마스크 사전 추출
                            test_close_col = 'target_close' if 'target_close' in w_test_df.columns else 'upbit_close'
                            test_low_col = 'target_low' if 'target_low' in w_test_df.columns else ('low' if 'low' in w_test_df.columns else None)
                            test_atr_col = 'target_atr' if 'target_atr' in w_test_df.columns else ('upbit_atr' if 'upbit_atr' in w_test_df.columns else None)
                            test_prices = w_test_df[test_close_col].to_numpy()
                            test_lows = w_test_df[test_low_col].to_numpy() if (test_low_col and test_low_col in w_test_df.columns) else test_prices
                            test_timestamps = w_test_df['timestamp'].to_numpy()
                            test_atrs = w_test_df[test_atr_col].to_numpy() if test_atr_col else (test_prices * 0.015)
                            test_n = len(test_prices)
                            
                            if _intraday_mode:
                                test_krx_force_exit_mask = np.array([is_krx_force_exit(t) for t in w_test_df['timestamp']])
                                test_krx_entry_allowed_mask = np.array([is_krx_entry_allowed(t) for t in w_test_df['timestamp']])
                            else:
                                test_krx_force_exit_mask = np.zeros(test_n, dtype=bool)
                                test_krx_entry_allowed_mask = np.ones(test_n, dtype=bool)
                            
                            cooldown_candles_oos = max(1, int(bp_w['cooldown_minutes'] / interval_minutes))
                            holding, entry_price, entry_time_idx, cooldown_until = False, 0.0, 0, 0
                            for idx in range(test_n):
                                current_price = test_prices[idx]
                                current_low = test_lows[idx]
                                current_time = test_timestamps[idx]

                                if holding:
                                    entry_atr = test_atrs[entry_time_idx]
                                    krx_force_exit = (_intraday_mode and test_krx_force_exit_mask[idx])
                                    _exit = resolve_exit(
                                        entry_price=entry_price, entry_atr=entry_atr,
                                        current_price=current_price, current_low=current_low,
                                        take_profit=bp_w['take_profit'], stop_loss=bp_w['stop_loss'],
                                        emergency_sl_mult=bp_w.get('emergency_sl_mult', bp_w['stop_loss'] * 2.0),
                                        max_hold_candles=bp_w.get('max_hold_candles', bp_w['predict_ahead_candles']),
                                        elapsed_candles=idx - entry_time_idx, cost=cost,
                                        force_exit=krx_force_exit, is_candle_close=True,
                                        interval_minutes=interval_minutes,
                                    )
                                    if _exit:
                                        all_test_trades.append({
                                            'window_index': w_idx,
                                            'entry_time': test_timestamps[entry_time_idx],
                                            'exit_time': current_time,
                                            'entry_price': entry_price,
                                            'exit_price': _exit['exit_price'],
                                            'hold_mins': int((idx - entry_time_idx) * interval_minutes),
                                            'prob': preds_test[entry_time_idx],
                                            'exit_type': _exit['exit_type'],
                                            'net_return': _exit['net_return']
                                        })
                                        holding = False
                                        cooldown_until = idx + cooldown_candles_oos
                                elif idx >= cooldown_until and preds_test[idx] >= _thr_oos_w:
                                    # [KRX 가드레일 ②] 09:10 이전 또는 15:00 이후 신규 진입 차단
                                    if _intraday_mode and not is_krx_entry_allowed(current_time):
                                        pass  # 진입 차단
                                    else:
                                        holding = True
                                        entry_price = current_price
                                        entry_time_idx = idx

                            
                            # OOS(Test) 성과 집계 — 진정한 미래 데이터 기반 가중치
                            _window_trades = [t for t in all_test_trades if t.get('window_index') == w_idx]
                            if _window_trades:
                                _total_ret = sum(float(t['net_return']) for t in _window_trades)
                                window_test_profits[w_idx] = _total_ret
                                window_test_scores[w_idx] = _total_ret / max(len(_window_trades), 1)
                                log_fn(f"   → [Window {w_idx+1}] OOS(Test) 성과: {_total_ret:+.2f}% / {len(_window_trades)}건 (거래당 평균 {_total_ret/max(len(_window_trades),1):+.3f}%)")
                            else:
                                window_test_profits[w_idx] = 0.0
                                window_test_scores[w_idx] = 0.0
                                log_fn(f"   ⚠️ [Window {w_idx+1}] OOS(Test) 거래 없음")
                    except Exception as we:
                        log_fn(f"⚠️ WFA 윈도우 {w_idx} 테스트 백테스트 중 오류: {we}")
        
        if best_params_per_window:
            bp = {}
            # [FIX-11] WFA 파라미터 집계 — 창 내부 검증(Opt) 점수 가중 평균.
            # 종전에는 OOS(Test) 성과로 가중해 OOS 가 파라미터 선택에 유입(오염)되었고, best_params_per_window(항상 append)와
            # window_test_scores(성공 창만 append)를 zip 하여 창 매칭이 어긋났다.
            # window_best_scores 는 best_params_per_window 와 1:1 (append 동시) 이므로 인덱스 정렬이 보장된다. OOS 는 진단 전용.
            raw_weights = window_best_scores
            clipped_scores = [max(0.0, s) for s in raw_weights]
            total_weight = sum(clipped_scores)
            for k in best_params_per_window[0].keys():
                vals = [w_bp[k] for w_bp in best_params_per_window]
                if total_weight > 0:
                    weighted = sum(v * w for v, w in zip(vals, clipped_scores)) / total_weight
                else:
                    weighted = vals[-1]  # fallback: 가장 최근 윈도우
                bp[k] = weighted if isinstance(vals[0], float) else int(round(weighted))
            log_fn(f"   → WFA 파라미터: 창 내부 검증(Opt) 점수 가중 평균 (유효 윈도우={sum(1 for s in clipped_scores if s > 0)}/{len(windows)}개, OOS 미사용)")
            # ── WFA 견고성 판정 — 창 밖 테스트(OOS) 성과로 한다 ──
            # 파라미터 선택에는 OOS 를 쓰지 않는다(FIX-11). 여기서는 '이 조합을 후보로 올릴 것인가' 만 판정한다 —
            # 그것이 WFA 의 본래 용도다. 창 내부 검증 점수는 225 조합 중 최고값이라 거의 항상 양수여서 판정력이 없다
            # (2026-10-01 실측: 내부 점수 6/6 양수, OOS 6창 중 5창 음수).
            wfa_valid_windows = sum(1 for k in range(len(windows)) if window_test_profits.get(k, 0.0) > 0)
            wfa_oos_total = float(sum(window_test_profits.values()))
            wfa_robust = (wfa_valid_windows * 2 >= len(windows)) and wfa_oos_total > 0
            wfa_window_details = []
            for w_idx in range(len(windows)):
                _w_trades = [t for t in all_test_trades if t.get('window_index') == w_idx]
                _w_ret = float(window_test_profits.get(w_idx, 0.0))
                _w_info = windows[w_idx] if w_idx < len(windows) else {}
                wfa_window_details.append({
                    "window": w_idx + 1,
                    "train_start": str(_w_info.get("train_start", ""))[:10],
                    "train_end": str(_w_info.get("train_end", ""))[:10],
                    "opt_start": str(_w_info.get("opt_start", ""))[:10],
                    "opt_end": str(_w_info.get("opt_end", ""))[:10],
                    "test_start": str(_w_info.get("test_start", ""))[:10],
                    "test_end": str(_w_info.get("test_end", ""))[:10],
                    "profit": round(_w_ret, 2),
                    "trades": len(_w_trades),
                    "passed": bool(_w_ret > 0),
                })
            log_fn(f"   {'✅' if wfa_robust else '⛔'} [WFA 견고성] OOS 양수 창 {wfa_valid_windows}/{len(windows)} · OOS 합계 {wfa_oos_total:+.2f}% "
                   f"→ {'후보 승격 가능' if wfa_robust else '절반 이상 창에서 양수 & 합계 양수 미충족 — 후보 승격 대상 아님(진단용 모델만 생성)'}")

            # ── WFA 파라미터 안정성 진단 — 윈도우별 최적 파라미터 분포 분석 ──
            log_fn("\n" + "="*60)
            log_fn("📊 [WFA 파라미터 안정성 진단]")
            log_fn("="*60)
            for k in ['predict_ahead_candles', 'max_hold_candles', 'take_profit', 'stop_loss',
                      'buy_threshold', 'buy_quantile', 'cooldown_minutes']:
                vals = [bp_w[k] for bp_w in best_params_per_window if k in bp_w]
                if not vals:
                    continue
                unique_vals = set(round(v, 4) if isinstance(v, float) else v for v in vals)
                log_fn(f"   {k:<24} {vals}")
                if len(unique_vals) == 1:
                    log_fn(f"      → 모든 윈도우에서 동일 ({vals[0]}) — 구간 독립적 안정 파라미터 ✅")
                else:
                    log_fn(f"      → 윈도우별 분산 ({len(unique_vals)}종) — 시장 체제 변화 반영")
            log_fn("="*60)
            detect_boundary_collisions(bp, optuna_ranges, log_fn)

            # ── Edge Decay 진단 — 최근 윈도우 성과가 하락하는지 확인 ──
            _profits_sorted = [window_test_profits[k] for k in sorted(window_test_profits)]  # 창 인덱스 순 정렬
            if len(_profits_sorted) >= 2:
                half = len(_profits_sorted) // 2
                first_half = _profits_sorted[:half]
                second_half = _profits_sorted[half:]
                decay_ratio = (sum(second_half) / max(len(second_half), 1)) / (abs(sum(first_half) / max(len(first_half), 1)) + 1e-8)
                log_fn(f"\n📉 [Edge Decay 진단] 윈도우별 OOS 수익률: {['{:.2f}%'.format(p) for p in _profits_sorted]}")
                if sum(second_half) < sum(first_half) * 0.5 and sum(first_half) > 0:
                    log_fn(f"   ⚠️ 주의: 최근 윈도우 성과 감쇠 감지 (전반 대비 후반 {decay_ratio:.1%}) — 전략 수명이 다했을 가능성. 배포 신중 권고.")
                else:
                    log_fn(f"   → 최근 윈도우 성과가 안정적입니다.")
            elif len(_profits_sorted) == 1:
                log_fn(f"\n📉 [Edge Decay 진단] 윈도우 수 부족({len(_profits_sorted)}개) — 진단 생략")
            master_train_end = windows[-1]['opt_end']
            # [FIX-9] 최종 학습셋: 탐색과 동일하게 market_type/웜업 제외 적용 + 학습 경계 직전 predict_ahead 봉 purge
            train_df = build_final_train_df(
                master_df, windows[0]['train_start'], master_train_end,
                bp['predict_ahead_candles'], bp['take_profit'], bp['stop_loss'], cost,
                market_type='KRX' if _intraday_mode else 'CRYPTO', exclude_warmup=_intraday_mode)
            # Fix 1 (WFA): Early Stopping 으로 최적 부스팅 라운드 자동 탐상
            # Trial 평가(30라운드)와 Final 학습의 라운드 불일치 해결
            final_lgb_params = {
                'objective': 'binary', 'learning_rate': bp['learning_rate'],
                'max_depth': bp['max_depth'], 'num_leaves': bp['num_leaves'],
                'min_child_samples': bp['min_child_samples'],
                'is_unbalance': False, 'verbose': -1, 'seed': seed, 'n_jobs': -1,
                'feature_pre_filter': False,
                'feature_penalty': feature_penalties
            }
            train_full_ds = lgb.Dataset(train_df[features], label=train_df['target'], categorical_feature=categorical_features)
            final_model = lgb.train(final_lgb_params, train_full_ds, num_boost_round=NUM_BOOST_ROUND)
            bp['buy_threshold'] = threshold_for_model(final_model, train_df[features], bp, lgb_params=final_lgb_params, y_train=train_df['target'], purge=max(bp['predict_ahead_candles'], bp.get('max_hold_candles', 0)), categorical_feature=categorical_features, log_fn=log_fn)
            log_fn(f"   → [WFA Final Model] 전체 학습 구간 대상 {NUM_BOOST_ROUND}라운드 최종 모델 빌드 완료 (창별 OOS 모델과 라운드 동일, 파라미터는 창 평균)")
            t_start, t_end, opt_start, opt_end = windows[0]['train_start'], master_train_end, windows[0]['opt_start'], master_train_end
            outer_study_best_value = sum(window_best_scores) / len(window_best_scores) if window_best_scores else 0.0
        else: raise RuntimeError("WFA 학습 실패")
    else:
        def parse_end_date(date_str):
            if not date_str:
                return None
            dt = pd.to_datetime(date_str)
            if len(date_str.strip()) <= 10 or (' ' not in date_str and 'T' not in date_str):
                return dt + pd.Timedelta(days=1)
            return dt
            
        try:
            t_start = pd.to_datetime(train_start_str) if train_start_str else master_df['timestamp'].iloc[0]
            t_end = parse_end_date(train_end_str) if train_end_str else _fallback_train_end(master_df)
            train_df_raw = master_df[(master_df['timestamp'] >= t_start) & (master_df['timestamp'] < t_end)].copy()
            opt_start = pd.to_datetime(optuna_start_str) if optuna_start_str else t_end
            opt_end = parse_end_date(optuna_end_str) if optuna_end_str else (opt_start + pd.Timedelta(days=10))
            val_df_raw = master_df[(master_df['timestamp'] >= opt_start) & (master_df['timestamp'] < opt_end)].copy()
            if len(train_df_raw) < 500 or len(val_df_raw) < 200: raise ValueError("데이터 부족")
        except Exception as e: raise ValueError(f"날짜/데이터 오류: {e}")

        # 📌 [Fast-Index] 비WFA 검증 데이터셋 NumPy 1D 배열 사전 추출
        val_raw_close_col = 'target_close' if 'target_close' in val_df_raw.columns else 'upbit_close'
        val_raw_low_col = 'target_low' if 'target_low' in val_df_raw.columns else ('low' if 'low' in val_df_raw.columns else None)
        val_raw_atr_col = 'target_atr' if 'target_atr' in val_df_raw.columns else ('upbit_atr' if 'upbit_atr' in val_df_raw.columns else None)
        val_raw_prices = val_df_raw[val_raw_close_col].to_numpy()
        val_raw_lows = val_df_raw[val_raw_low_col].to_numpy() if (val_raw_low_col and val_raw_low_col in val_df_raw.columns) else val_raw_prices
        val_raw_timestamps = val_df_raw['timestamp'].to_numpy()
        val_raw_atrs = val_df_raw[val_raw_atr_col].to_numpy() if val_raw_atr_col else (val_raw_prices * 0.015)
        val_raw_n = len(val_raw_prices)

        # ── 평가 백테스트 헬퍼 함수 (Fix 1: ATR 기반 장벽) ──
        def run_eval_backtest(pa, mhc, tp, sl, lr, md, nl, mcs, entry, cd):
            _tmp = label_decision_points(train_df_raw, pa, tp, sl, cost, market_type='KRX' if _intraday_mode else 'CRYPTO')  # [FIX-9] market_type 전달
            _tmp.dropna(subset=['target'], inplace=True)
            if len(_tmp) < 200:
                return None
            train_ds = lgb.Dataset(_tmp[features], label=_tmp['target'], categorical_feature=categorical_features)
            lgb_params = {
                'objective': 'binary', 'metric': 'binary_logloss', 'learning_rate': lr, 
                'max_depth': md, 'num_leaves': nl, 'min_child_samples': mcs, 
                'is_unbalance': False, 'verbose': -1, 'seed': seed, 'n_jobs': -1, 'feature_pre_filter': False,
                'feature_penalty': feature_penalties
            }
            try:
                model = lgb.train(lgb_params, train_ds, num_boost_round=NUM_BOOST_ROUND)
                preds_val = model.predict(val_df_raw[features])
                bt = threshold_for_model(model, _tmp[features], entry, lgb_params=lgb_params, y_train=_tmp['target'], purge=max(pa, mhc), categorical_feature=categorical_features)
            except BaseException as _eval_err:  # [FIX-16] 기록만 (타입 유지)
                _logger.exception("run_eval_backtest 예외: %s", _eval_err)
                return None
                
            total_gain, total_loss, trades_v, win_v, p_cum, peak, mdd_v = 0.0, 0.0, 0, 0, 1.0, 1.0, 0.0001
            holding, entry_price, entry_time_idx, cooldown_until = False, 0.0, 0, 0
            # [FIX-13] cd 는 '분' 단위 — inner objective 와 동일하게 캔들 수로 환산
            cd_candles = max(1, int(cd / interval_minutes))
            for idx in range(val_raw_n):
                current_price = val_raw_prices[idx]
                current_low = val_raw_lows[idx]
                if holding:
                    entry_atr = val_raw_atrs[entry_time_idx]
                    krx_force_exit = (_intraday_mode and is_krx_force_exit(val_raw_timestamps[idx]))
                    _exit = resolve_exit(
                        entry_price=entry_price, entry_atr=entry_atr,
                        current_price=current_price, current_low=current_low,
                        take_profit=tp, stop_loss=sl,
                        emergency_sl_mult=sl * 2.0,
                        max_hold_candles=mhc,
                        elapsed_candles=idx - entry_time_idx, cost=cost,
                        force_exit=krx_force_exit, is_candle_close=True,
                        interval_minutes=interval_minutes,
                    )
                    if _exit:
                        net_p = _exit['net_return'] / 100.0
                        p_cum *= (1 + net_p); trades_v += 1
                        if net_p > 0:
                            total_gain += net_p; win_v += 1
                        else:
                            total_loss += abs(net_p)
                        holding = False; cooldown_until = idx + cd_candles
                elif idx >= cooldown_until and preds_val[idx] >= bt:
                    # [FIX-13] KRX 진입 허용 시간대 마스크 (WFA 경로와 동일)
                    if not (_intraday_mode and not is_krx_entry_allowed(val_raw_timestamps[idx])):
                        holding = True; entry_price = current_price; entry_time_idx = idx

                if holding:
                    p_curr = p_cum * (1.0 + (current_price - entry_price) / entry_price - (cost / 2.0))
                else:
                    p_curr = p_cum
                if p_curr > peak: peak = p_curr
                dd = (peak - p_curr) / peak
                if dd > mdd_v: mdd_v = dd
                
            val_duration_days = max(1.0, float((val_raw_timestamps[-1] - val_raw_timestamps[0]) / np.timedelta64(1, 'D')))
            val_months = val_duration_days / 30.44
            # [D-11] 단일 점수 함수 (PF 상한 + 절대 최소 거래 수 가드)
            score, pf, _pf_raw, required_min_trades = compute_wfa_score(
                total_gain, total_loss, trades_v, mdd_v, val_months, optuna_cfg
            )
            
            # [D-11] 최소 거래 수 미달 시 score = -90 + trades (가드 내부에서 처리됨)
                
            return {
                'score': score,
                'trades': trades_v,
                'required_trades': float(required_min_trades),
                'win_rate': float(win_v / trades_v * 100) if trades_v > 0 else 0.0,
                'mdd': float(mdd_v * 100),
                'profit_factor': float(pf),
                'total_return': float((p_cum - 1.0) * 100)
            }

        # Fix 2: outer/inner_trials — OPTUNA_CONFIG 설정에서 동적으로 읽기
        outer_trials = optuna_cfg.get('outer_trials', 15)
        inner_trials = optuna_cfg.get('inner_trials', 15)
        best_val = -999.0
        _label_cache = {}

        def outer_objective(outer_trial):
            if check_abort_fn and check_abort_fn():
                raise RuntimeError("Aborted by user")
            if is_horizon_locked:
                predict_ahead_candles = locked_pa
                max_hold_candles = locked_mhc
            elif candidate_horizons and len(candidate_horizons) > 0:
                predict_ahead_candles = outer_trial.suggest_categorical('predict_ahead_candles', candidate_horizons)
                max_hold_candles = predict_ahead_candles
            else:
                predict_ahead_candles = outer_trial.suggest_int('predict_ahead_candles', pa_range[0], pa_range[1])
                max_hold_candles = outer_trial.suggest_int('max_hold_candles', min(mhc_range[0], predict_ahead_candles), min(mhc_range[1], predict_ahead_candles))
            take_profit = outer_trial.suggest_float('take_profit', tp_range[0], tp_range[1], step=tp_range[2])
            stop_loss = outer_trial.suggest_float('stop_loss', sl_range[0], sl_range[1], step=sl_range[2])
            _cache_key = (predict_ahead_candles, round(take_profit, 5), round(stop_loss, 5))
            if _cache_key not in _label_cache:
                _tmp = label_decision_points(train_df_raw, predict_ahead_candles, take_profit, stop_loss, cost, market_type='KRX' if _intraday_mode else 'CRYPTO')  # [FIX-9]
                _tmp.dropna(subset=['target'], inplace=True)
                _label_cache[_cache_key] = _tmp[features + ['target']].copy() if len(_tmp) >= 200 else None
            _cached_df = _label_cache[_cache_key]
            if _cached_df is None:
                outer_trial.set_user_attr('skipped_insufficient_train_rows', True)  # [FIX-16]
                return -99.0
            train_data = lgb.Dataset(_cached_df[features], label=_cached_df['target'], categorical_feature=categorical_features)
            
            def inner_objective(inner_trial):
                if check_abort_fn and check_abort_fn():
                    raise RuntimeError("Aborted by user")
                
                total_steps = outer_trials * inner_trials
                current_step = outer_trial.number * inner_trials + inner_trial.number
                pct = 10.0 + (current_step / total_steps) * 85.0
                if progress_fn:
                    progress_fn(pct, f"최적화 진행 중: Trial {current_step + 1}/{total_steps}")

                max_depth_val = inner_trial.suggest_int('max_depth', md_range[0], md_range[1])
                num_leaves_val = inner_trial.suggest_int('num_leaves', nl_range[0], nl_range[1])

                lgb_params = {
                    'objective': 'binary',
                    'metric': 'binary_logloss',
                    'learning_rate': inner_trial.suggest_float('learning_rate', lr_range[0], lr_range[1]),
                    'max_depth': max_depth_val,
                    'num_leaves': num_leaves_val,
                    'min_child_samples': inner_trial.suggest_int('min_child_samples', mcs_range[0], mcs_range[1]),
                    'is_unbalance': False,
                    'verbose': -1,
                    'seed': seed,
                    'n_jobs': -1,
                    'feature_pre_filter': False,
                    'feature_penalty': feature_penalties
                }
                if threshold_mode == 'quantile':
                    _entry = {'buy_quantile': inner_trial.suggest_float('buy_quantile', bq_range[0], bq_range[1])}
                else:
                    _entry = {'buy_threshold': inner_trial.suggest_float('buy_threshold', bt_range[0], bt_range[1])}
                cooldown_minutes = inner_trial.suggest_int('cooldown_minutes', cd_range[0], cd_range[1])
                try:
                    _inner_model = lgb.train(lgb_params, train_data, num_boost_round=NUM_BOOST_ROUND)
                    preds_val = _inner_model.predict(val_df_raw[features])
                    buy_threshold = threshold_for_model(_inner_model, _cached_df[features], _entry, lgb_params=lgb_params, y_train=_cached_df['target'], purge=max(predict_ahead_candles, max_hold_candles), categorical_feature=categorical_features)
                except BaseException as _trial_err:  # [FIX-16] 기록만 (타입 유지)
                    _logger.exception("inner trial 예외 (outer=%s, inner=%s): %s", outer_trial.number, inner_trial.number, _trial_err)
                    return -99.0
                total_gain, total_loss, trades_v, win_v, p_cum, peak, mdd_v = 0.0, 0.0, 0, 0, 1.0, 1.0, 0.0001
                holding, entry_price, entry_time_idx, cooldown_until = False, 0.0, 0, 0
                cooldown_candles = max(1, int(cooldown_minutes / interval_minutes))
                trade_logs = []
                for idx in range(val_raw_n):
                    if idx % 100 == 0 and check_abort_fn and check_abort_fn():
                        raise RuntimeError("Aborted by user")
                    current_price = val_raw_prices[idx]
                    current_low = val_raw_lows[idx]
                    if holding:
                        entry_atr = val_raw_atrs[entry_time_idx]
                        krx_force_exit = (_intraday_mode and is_krx_force_exit(val_raw_timestamps[idx]))
                        _exit = resolve_exit(
                            entry_price=entry_price, entry_atr=entry_atr,
                            current_price=current_price, current_low=current_low,
                            take_profit=take_profit, stop_loss=stop_loss,
                            emergency_sl_mult=stop_loss * 2.0,
                            max_hold_candles=max_hold_candles,
                            elapsed_candles=idx - entry_time_idx, cost=cost,
                            force_exit=krx_force_exit, is_candle_close=True,
                            interval_minutes=interval_minutes,
                        )
                        if _exit:
                            net_p = _exit['net_return'] / 100.0
                            p_cum *= (1 + net_p); trades_v += 1
                            if net_p > 0:
                                total_gain += net_p; win_v += 1
                            else:
                                total_loss += abs(net_p)
                            holding = False; cooldown_until = idx + cooldown_candles
                            trade_logs.append({'entry_time': val_raw_timestamps[entry_time_idx], 'exit_time': val_raw_timestamps[idx], 'entry_price': entry_price, 'exit_price': _exit['exit_price'], 'hold_mins': int((idx - entry_time_idx) * interval_minutes), 'prob': preds_val[entry_time_idx], 'exit_type': _exit['exit_type'], 'net_return': _exit['net_return']})
                    elif idx >= cooldown_until and preds_val[idx] >= buy_threshold:
                        # [FIX-13] KRX 진입 허용 시간대 마스크 (run_eval_backtest 와 동일 적용)
                        if not (_intraday_mode and not is_krx_entry_allowed(val_raw_timestamps[idx])):
                            holding = True; entry_price = current_price; entry_time_idx = idx


                    if holding:
                        p_curr = p_cum * (1.0 + (current_price - entry_price) / entry_price - (cost / 2.0))
                    else:
                        p_curr = p_cum
                    if p_curr > peak: peak = p_curr
                    dd = (peak - p_curr) / peak
                    if dd > mdd_v: mdd_v = dd
                
                val_duration_days = max(1.0, float((val_raw_timestamps[-1] - val_raw_timestamps[0]) / np.timedelta64(1, 'D')))
                val_months = val_duration_days / 30.44
                # [D-11] 단일 점수 함수 (PF 상한 + 절대 최소 거래 수 가드)
                score, pf, _pf_raw, required_min_trades = compute_wfa_score(
                    total_gain, total_loss, trades_v, mdd_v, val_months, optuna_cfg
                )
                
                if trades_v < required_min_trades:
                    return score

                nonlocal best_val
                if score > best_val:
                    best_val = score
                    log_fn(f"\n🔥 [3-Zone Opt] New Best Score: {score:.4f} (Trades: {trades_v} [요구: {required_min_trades:.1f}회], WinRate: {win_v/trades_v*100:.1f}%, MDD: {mdd_v*100:.2f}%)")
                    log_fn(f"⚙️ Params: TP({take_profit:.2f} ATR), SL({stop_loss:.2f} ATR), Hold({max_hold_candles}), BuyTh({buy_threshold:.3f}{', 상위 %.2f%%' % ((1 - _entry['buy_quantile']) * 100) if 'buy_quantile' in _entry else ''})")
                    for t in trade_logs[:30]:
                        log_fn(f"  ↳ {t['entry_time']} ~ {t['exit_time']} | Entry: {float(t['entry_price']):,.0f} -> Exit: {float(t['exit_price']):,.0f} | Hold: {t['hold_mins']}m | {t['exit_type']} | {t['net_return']:+.2f}%")
                    if len(trade_logs) > 30:
                        log_fn(f"  ↳ ... and {len(trade_logs) - 30} more trades.")

                return score

            # [FIX-12] 트라이얼별 seed 오프셋
            inner_study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=seed + outer_trial.number))
            inner_study.optimize(inner_objective, n_trials=inner_trials)
            
            bp_inner = inner_study.best_params
            metrics = run_eval_backtest(
                predict_ahead_candles, max_hold_candles, take_profit, stop_loss,
                bp_inner['learning_rate'], bp_inner['max_depth'], bp_inner['num_leaves'], bp_inner['min_child_samples'],
                bp_inner, bp_inner['cooldown_minutes']
            )
            if metrics:
                outer_trial.set_user_attr('metrics', metrics)
                
            outer_trial.set_user_attr('inner_best_params', bp_inner)
            return inner_study.best_value
        outer_study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=seed))
        outer_study.optimize(outer_objective, n_trials=outer_trials)
        _best_outer = pick_best_outer_trial(outer_study)  # [FIX-16]
        if _best_outer is None:
            raise RuntimeError("유효한 outer 트라이얼이 없습니다 (학습 행 부족 또는 모든 트라이얼 예외/-99). 로그를 확인하세요.")
        bp = {**_best_outer.params, **_best_outer.user_attrs['inner_best_params']}
        if is_horizon_locked:
            bp['predict_ahead_candles'] = locked_pa
            bp['max_hold_candles'] = locked_mhc

        # 경계 충돌 감지 — 최적 파라미터가 설정 범위의 경계에 위치하는지 진단
        detect_boundary_collisions(bp, optuna_ranges, log_fn)

        # [FIX-9] 최종 학습셋: market_type 전달 + 학습 경계 직전 predict_ahead 봉 purge
        train_df = build_final_train_df(
            master_df, t_start, t_end,
            bp['predict_ahead_candles'], bp['take_profit'], bp['stop_loss'], cost,
            market_type='KRX' if _intraday_mode else 'CRYPTO')
        # Fix 1: Optuna 탐색 시와 동일한 라운드(30) 및 전체 train_df로 최종 모델 학습 (불필요한 Early Stopping 분할 및 라운드 불일치 제거)
        final_lgb_params = {
            'objective': 'binary', 'learning_rate': bp['learning_rate'],
            'max_depth': bp['max_depth'], 'num_leaves': bp['num_leaves'],
            'min_child_samples': bp['min_child_samples'],
            'is_unbalance': False, 'verbose': -1, 'seed': seed, 'n_jobs': -1,
            'feature_pre_filter': False,
            'feature_penalty': feature_penalties
        }
        train_full_ds = lgb.Dataset(train_df[features], label=train_df['target'], categorical_feature=categorical_features)
        final_model = lgb.train(final_lgb_params, train_full_ds, num_boost_round=NUM_BOOST_ROUND)
        bp['buy_threshold'] = threshold_for_model(final_model, train_df[features], bp, lgb_params=final_lgb_params, y_train=train_df['target'], purge=max(bp['predict_ahead_candles'], bp.get('max_hold_candles', 0)), categorical_feature=categorical_features, log_fn=log_fn)
        log_fn(f"✅ [Final Model] 전체 학습 구간 대상 {NUM_BOOST_ROUND}라운드 최종 모델 빌드 완료!")

        # 📌 [신규] 생성된 최종 모델로 학습/검증 데이터셋에 대한 상세 백테스트 실행 및 검증 로그 출력
        try:
            preds_train_eval = final_model.predict(train_df[features])
            t_gain, t_loss, t_cnt, t_win, p_c, p_k, mdd_c = 0.0, 0.0, 0, 0, 1.0, 1.0, 0.0001
            hld, e_p, e_i, cd_u = False, 0.0, 0, 0
            cd_c = max(1, int(bp['cooldown_minutes'] / interval_minutes))
            eval_logs = []
            
            # 📌 [Fast-Index] 최종 학습 검증용 NumPy 1D 배열 사전 추출
            train_eval_close_col = 'target_close' if 'target_close' in train_df.columns else 'upbit_close'
            train_eval_atr_col = 'target_atr' if 'target_atr' in train_df.columns else ('upbit_atr' if 'upbit_atr' in train_df.columns else None)
            train_eval_prices = train_df[train_eval_close_col].to_numpy()
            train_eval_atrs = train_df[train_eval_atr_col].to_numpy() if train_eval_atr_col else (train_eval_prices * 0.015)
            train_eval_n = len(train_eval_prices)
            
            for idx in range(train_eval_n):
                c_price = train_eval_prices[idx]
                if hld:
                    e_atr = train_eval_atrs[e_i]
                    tp_p = e_p + (e_atr * bp['take_profit'])
                    sl_p = e_p - (e_atr * bp['stop_loss'])
                    if c_price <= sl_p:
                        np_val = (c_price - e_p) / e_p - cost; t_loss += abs(np_val); p_c *= (1 + np_val); t_cnt += 1; hld = False; cd_u = idx + cd_c
                        eval_logs.append({'type': '손절', 'ret': np_val * 100})
                    elif c_price >= tp_p:
                        np_val = (c_price - e_p) / e_p - cost; t_gain += np_val; p_c *= (1 + np_val); t_cnt += 1; t_win += 1; hld = False; cd_u = idx + cd_c
                        eval_logs.append({'type': '익절', 'ret': np_val * 100})
                    elif idx - e_i >= bp['max_hold_candles']:
                        p_v = (c_price - e_p) / e_p - cost; p_c *= (1 + p_v); t_cnt += 1
                        if p_v > 0: t_gain += p_v; t_win += 1
                        else: t_loss += abs(p_v)
                        hld = False; cd_u = idx + cd_c
                        eval_logs.append({'type': '시간만료', 'ret': p_v * 100})
                elif idx >= cd_u and preds_train_eval[idx] >= bp['buy_threshold']:
                    hld = True; e_p = c_price; e_i = idx
                if hld:
                    p_curr = p_c * (1.0 + (c_price - e_p) / e_p - (cost / 2.0))
                else: p_curr = p_c
                if p_curr > p_k: p_k = p_curr
                dd_val = (p_k - p_curr) / p_k
                if dd_val > mdd_c: mdd_c = dd_val

            tr_win_rate = (t_win / t_cnt * 100) if t_cnt > 0 else 0.0
            log_fn(f"📊 [최종 모델 학습구간 검증] 총 거래: {t_cnt}회 | 승률: {tr_win_rate:.1f}% | 누적수익률: {(p_c - 1)*100:+.2f}% | MDD: {mdd_c*100:.2f}%")
            log_fn(f"🎯 최적화 파라미터 확정: TP({bp['take_profit']:.2f} ATR), SL({bp['stop_loss']:.2f} ATR), Hold({bp['max_hold_candles']}봉), BuyThreshold({bp['buy_threshold']:.3f}), Cooldown({bp['cooldown_minutes']}분)")
        except Exception as eval_e:
            log_fn(f"⚠️ 최종 모델 검증 로그 생성 중 참고 오류: {eval_e}")

        outer_study_best_value = _best_outer.value

        valid_trials = []
        for t in outer_study.trials:
            if t.state == optuna.trial.TrialState.COMPLETE:
                inner_params = t.user_attrs.get('inner_best_params', {})
                metrics = t.user_attrs.get('metrics', {})
                if not inner_params or not metrics:
                    continue
                trial_params = {**t.params, **inner_params}
                valid_trials.append({
                    'trial_number': t.number,
                    'value': float(t.value),
                    'params': trial_params,
                    'metrics': metrics
                })
        valid_trials.sort(key=lambda x: x['value'], reverse=True)
        top_trials = valid_trials[:20]

    from datetime import datetime
    opt_session_id = config.get("ACTIVE_SESSION_ID")
    if not opt_session_id or opt_session_id == 'active':
        opt_session_id = f"BTC_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
    created_at_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    opt_metrics = {}
    if 'outer_study' in locals():
        opt_metrics = _best_outer.user_attrs.get('metrics', {})
    elif 'wfa_final_metrics' in locals() and wfa_final_metrics:
        opt_metrics = wfa_final_metrics
    bp.update({
        'opt_session_id': opt_session_id,
        'memo': memo,
        'seed': seed,
        'train_start': str(t_start),
        'train_end': str(t_end),
        'opt_start': str(opt_start),
        'opt_end': str(opt_end),
        'created_at': created_at_str,
        'optuna_score': float(outer_study_best_value),
        'validation_metrics': opt_metrics,
        # [FIX-3/4] 학습 목적함수/확률 정의 메타데이터 (튜닝 = 저장 모델 = binary, 동일 라운드)
        'training_meta': {
            'num_boost_round': NUM_BOOST_ROUND,
            'tuning_objective': 'binary',
            'saved_model_objective': 'binary',
            'buy_threshold_prob_basis': 'binary P(label=1)',
            'optuna_score_note': ('창별 best 점수의 평균 — 선택 편향 포함, 성과 지표로 해석 금지' if wfa_enabled else 'outer best value (in-sample 튜닝 점수)'),
            # 이 모델이 학습한 데이터의 지문 (sha256 등). 자동연구는 연구 루프가, 직접 학습은 위 로딩부가 채운다.
            'dataset': config.get('_DATASET'),
            # WFA 견고성: 창 내부 검증 점수가 양수인 창 수 / 전체 창 수 (절반 미만이면 연구 루프가 승격하지 않는다)
            'wfa_windows': (len(windows) if (wfa_enabled and 'windows' in locals()) else None),
            'wfa_valid_windows': (wfa_valid_windows if 'wfa_valid_windows' in locals() else None),  # OOS 양수 창 수
            'wfa_oos_total_pct': (wfa_oos_total if 'wfa_oos_total' in locals() else None),
            'wfa_robust': (wfa_robust if 'wfa_robust' in locals() else None),
            'wfa_window_details': (wfa_window_details if 'wfa_window_details' in locals() else []),
        },
    })
    
    # 시계열 검증 통제 장벽 설정을 실제 사용값으로 갱신 후 저장
    config['ACTIVE_SESSION_ID'] = opt_session_id
    config['TRAIN_START'] = str(t_start)[:10]
    config['TRAIN_END'] = str(t_end)[:10]
    config['OPT_START'] = str(opt_start)[:10]
    config['OPT_END'] = str(opt_end)[:10]
    # 자동연구 중(trial·후보 재학습)에는 프로젝트 설정을 건드리지 않는다 — 이번 실행 설정은 runs/<시각>/config_used.json
    if not _run_mode:
        try:
            save_project_config("CAiModelBaker", config)
        except Exception as ce:
            print(f"Failed to update CAiModelBaker config: {ce}")
    
    # 산출물 위치: 자동연구 중이면 runs/<시각>/work, 사용자가 직접 학습하면 프로젝트 폴더 (paths 단일 규칙)
    deploy_dir = project_store.artifact_dir(config, opt_session_id)
    model_dir = os.path.join(deploy_dir, "model")
    reports_dir = os.path.join(deploy_dir, "reports")
    
    os.makedirs(model_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)
    
    final_model.save_model(os.path.join(model_dir, f"model_{interval}.txt"))
    with open(os.path.join(model_dir, f"best_params_{interval}.json"), 'w', encoding='utf-8') as f:
        json.dump(bp, f, indent=4, ensure_ascii=False)
    if not _run_mode:
        # 사용자가 직접 학습해 model/ 을 교체한 경우 출처를 남긴다 (배포 API 가 자동연구 탈락 후보와 구분)
        with open(os.path.join(model_dir, "model_source.json"), 'w', encoding='utf-8') as f:
            json.dump({"kind": "manual", "status": "MANUAL", "trained_at": created_at_str}, f, indent=2, ensure_ascii=False)
        _stale = os.path.join(model_dir, "candidate_detail.json")   # 이전 챔피언 상세가 새 모델 옆에 남지 않게
        if os.path.exists(_stale):
            os.remove(_stale)

    if not wfa_enabled:
        with open(os.path.join(deploy_dir, f"pending_trials_{interval}.json"), 'w', encoding='utf-8') as f:
            json.dump(top_trials, f, indent=4, ensure_ascii=False)
        
    feature_config = load_feature_config()
    feature_spec = {
        "session_id": opt_session_id,
        "symbol": get_target_symbol(config),
        "interval": interval,
        "seed": seed,
        "features_order": features,
        "feature_params": {fid: feature_config.get("feature_params", {}).get(fid, {}) for fid in features}
    }
    _spec_v2 = _resolved_spec_fields(feature_config, features, interval, config)
    if _spec_v2:
        feature_spec.update(_spec_v2)
    with open(os.path.join(model_dir, "feature_spec.json"), 'w', encoding='utf-8') as f:
        json.dump(feature_spec, f, indent=4, ensure_ascii=False)
        
    if _run_mode:
        # 실행 폴더의 작업 사본 (프로젝트 설정 원본이 아님)
        with open(os.path.join(deploy_dir, "config_snapshot.json"), 'w', encoding='utf-8') as f:
            json.dump(project_store.strip_runtime_keys(config), f, indent=4, ensure_ascii=False)
    else:
        # 프로젝트 설정 원본 — 신원 키 고정·임시 키 제거 규칙으로 저장
        try:
            project_store.write_project_config(project_store.ref_of_dir(deploy_dir) or "", config)
        except Exception:
            with open(os.path.join(deploy_dir, "config_snapshot.json"), 'w', encoding='utf-8') as f:
                json.dump(project_store.strip_runtime_keys(config), f, indent=4, ensure_ascii=False)
        
    readme_content = f"""# AI Optimization Session: {opt_session_id}

- **생성 일시**: {created_at_str}
- **대상 자산**: {get_target_symbol(config)} ({interval})
- **모델 메모/설명**: {memo if memo else "기재된 메모가 없습니다."}
- **Optuna Score**: {float(outer_study_best_value):.6f}
- **Validation Metrics**: {json.dumps(opt_metrics, ensure_ascii=False) if opt_metrics else "N/A"}
- **Assessment**: VALIDATION_ONLY (최종 Test 결과는 research_loop Phase 3에서 확정)

## 📂 Directory Structure

```
├── model/ (실전 배포용 핵심 파일)
│   ├── model_{interval}.txt           (LightGBM 가중치 모델)
│   ├── best_params_{interval}.json    (최적 하이퍼파라미터 설정)
│   └── feature_spec.json       (기술 지표 전처리 명세서)
│
└── reports/ (검증 및 기록 보조 파일)
    ├── train_run.log           (학습 실행 콘솔 로그)
    ├── test_trades.csv         (테스트 구간 상세 거래 기록)
    └── analysis_report_*.md    (성과 분석 보고서 - Analyze 저장 시 생성)
```

---
*본 폴더는 CAiModelBaker 엔진에 의해 자동 아카이빙된 최적화 세션 패키지입니다.*
"""
    with open(os.path.join(deploy_dir, "README.md"), 'w', encoding='utf-8') as f:
        f.write(readme_content)

    # train_run.log 는 서버가 실행 중에 직접 기록한다 (자동연구: runs/<시각>/, 직접 학습: reports/).
    # 종전의 '공용 로그 끝 2MB 복사' 는 브라우저가 아직 저장하지 않은 로그 대신 다른 세션 로그를 담았다.

    def run_insample_backtest(df_val):
        if len(df_val) == 0:
            return []
        preds = final_model.predict(df_val[features])
        profit, trades, win_trades, holding, entry_price, entry_time_idx, cooldown_until = 0.0, 0, 0, False, 0.0, 0, 0
        cooldown_candles = max(1, int(bp['cooldown_minutes'] / interval_minutes))
        
        # 📌 [Fast-Index] NumPy 1D 배열 사전 추출
        in_close_col = 'target_close' if 'target_close' in df_val.columns else 'upbit_close'
        in_low_col = 'target_low' if 'target_low' in df_val.columns else ('low' if 'low' in df_val.columns else None)
        in_atr_col = 'target_atr' if 'target_atr' in df_val.columns else ('upbit_atr' if 'upbit_atr' in df_val.columns else None)
        in_prices = df_val[in_close_col].to_numpy()
        in_lows = df_val[in_low_col].to_numpy() if (in_low_col and in_low_col in df_val.columns) else in_prices
        in_timestamps = df_val['timestamp'].to_numpy()
        in_atrs = df_val[in_atr_col].to_numpy() if in_atr_col else (in_prices * 0.015)
        in_n = len(in_prices)
        
        trade_logs = []
        for idx in range(in_n):
            current_price = in_prices[idx]
            current_low = in_lows[idx]
            current_time = in_timestamps[idx]
            if holding:
                entry_atr = in_atrs[entry_time_idx]
                krx_force_exit = (_intraday_mode and is_krx_force_exit(current_time))
                _exit = resolve_exit(
                    entry_price=entry_price, entry_atr=entry_atr,
                    current_price=current_price, current_low=current_low,
                    take_profit=bp['take_profit'], stop_loss=bp['stop_loss'],
                    emergency_sl_mult=bp.get('emergency_sl_mult', bp['stop_loss'] * 2.0),
                    max_hold_candles=bp.get('max_hold_candles', bp['predict_ahead_candles']),
                    elapsed_candles=idx - entry_time_idx, cost=cost,
                    force_exit=krx_force_exit, is_candle_close=True,
                    interval_minutes=interval_minutes,
                )
                if _exit:
                    net_p = _exit['net_return'] / 100.0
                    profit += net_p; trades += 1
                    if net_p > 0: win_trades += 1
                    holding = False; cooldown_until = idx + cooldown_candles
                    trade_logs.append({'entry_time': in_timestamps[entry_time_idx], 'exit_time': current_time, 'entry_price': entry_price, 'exit_price': _exit['exit_price'], 'hold_mins': int((idx - entry_time_idx) * interval_minutes), 'prob': preds[entry_time_idx], 'exit_type': _exit['exit_type'], 'net_return': _exit['net_return']})
            elif idx >= cooldown_until and preds[idx] >= bp['buy_threshold']:
                if not (_intraday_mode and not is_krx_entry_allowed(current_time)):  # [FIX-13] KRX 진입 허용 마스크
                    holding = True; entry_price = current_price; entry_time_idx = idx

        return trade_logs

    try:
        import csv
        trades_csv_path = os.path.join(reports_dir, "test_trades.csv")
        if wfa_enabled:
            trades_to_save = all_test_trades
        else:
            trades_to_save = run_insample_backtest(val_df_raw)
            
        # [FIX-12] 비WFA test_trades 는 튜닝에 사용된 opt(val) 구간의 in-sample 결과이므로 OOS 로 표기하지 않는다
        _trade_log_title = "OOS BACKTEST TRADE LOGS" if wfa_enabled else "IN-SAMPLE(OPT/VALIDATION) BACKTEST TRADE LOGS - NOT OOS"
        log_fn(f"\n========== 📋 [{_trade_log_title}] ==========")
        exit_counts = {"익절": 0, "손절": 0, "시간만료": 0}
        for t in trades_to_save:
            et = t.get('exit_type', '기타')
            exit_counts[et] = exit_counts.get(et, 0) + 1
            w_prefix = f"[Window {t['window_index'] + 1}] " if wfa_enabled else ""  # 창 번호는 화면 전체에서 1부터
            log_fn(f"{w_prefix}{t['entry_time']} ~ {t['exit_time']} | Entry: {float(t['entry_price']):,.0f} -> Exit: {float(t['exit_price']):,.0f} | Hold: {t['hold_mins']} min | Type: {et} | Return: {float(t['net_return']):+.2f}% | Prob: {float(t['prob']):.4f}")
        
        log_fn("\n📊 [TRADE SUMMARY]")
        total_trades = len(trades_to_save)
        if total_trades > 0:
            for k, v in exit_counts.items():
                log_fn(f" - {k}: {v}회 ({v/total_trades*100:.1f}%)")
        log_fn("==================================================\n")
            
        with open(trades_csv_path, 'w', encoding='utf-8', newline='') as f:
            fieldnames = ['entry_time', 'exit_time', 'entry_price', 'exit_price', 'hold_mins', 'prob', 'exit_type', 'net_return']
            if wfa_enabled:
                fieldnames = ['window_index'] + fieldnames
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for t in trades_to_save:
                row = {
                    'entry_time': str(pd.Timestamp(t['entry_time'])),
                    'exit_time': str(pd.Timestamp(t['exit_time'])),
                    'entry_price': float(t['entry_price']),
                    'exit_price': float(t['exit_price']),
                    'hold_mins': int(t['hold_mins']),
                    'prob': float(t['prob']),
                    'exit_type': str(t['exit_type']),
                    'net_return': float(t['net_return'])
                }
                if wfa_enabled:
                    row['window_index'] = int(t['window_index'])
                writer.writerow(row)
        log_fn(f"📈 test_trades.csv 저장 완료 ({len(trades_to_save)}건의 거래)")
    except Exception as te:
        log_fn(f"⚠️ test_trades.csv 저장 실패: {te}")

    log_fn("✅ [SUCCESS] 메타 이중 최적화 기반 마스터 모델 완공!")
    if status_update_fn: status_update_fn()


def confirm_and_deploy_strategy(market_dir, session_id, selected_params, config, log_fn=None):
    """
    사용자가 선택한 파라미터 셋으로 모델을 다시 학습하고 배포 파일을 업데이트합니다.
    """
    from datetime import datetime
    if log_fn is None:
        log_fn = print
        
    import lightgbm as lgb
    
    log_fn(f"⚡ [Confirm Strategy] 세션 {session_id}에 대해 파라미터 재구성 및 재학습 개시...")
    
    snapshot_path = get_snapshot_path(config)
    if not os.path.exists(snapshot_path):
        raise FileNotFoundError(f"스냅샷 파일이 없습니다: {snapshot_path}")
        
    master_df = pd.read_csv(snapshot_path)
    master_df = master_df.sort_values('timestamp').reset_index(drop=True)
    master_df['timestamp'] = pd.to_datetime(master_df['timestamp'])
    
    interval = config.get('INTERVAL', '1m')
    _market_type = detect_market_type(config)
    _intraday_mode = (_market_type == 'KRX' and config.get('INTRADAY_SQUARE_OFF', True))
    master_df = run_feature_engineering(master_df, interval, config=config)
    
    feature_config = load_feature_config()
    features = feature_config.get('enabled_features', ['binance_ret_5m', 'kimchi_premium', 'target_ma_ratio', 'target_trade_bid_ratio', 'binance_spot_taker_ratio', 'target_rsi_daily', 'target_hour', 'target_is_weekend'])
    categorical_features = [f for f in features if f in ['target_hour', 'target_is_weekend', 'upbit_hour', 'upbit_is_weekend', 'krx_session_time']]
    
    # 지표별 패널티 파라미터 구성 (현업 퀀트 기법)
    penalties_dict = feature_config.get("feature_penalties", {})
    feature_penalties = [float(penalties_dict.get(feat, 1.0)) for feat in features]
    
    try:
        t_start = pd.to_datetime(config.get('TRAIN_START', '')) if config.get('TRAIN_START') else master_df['timestamp'].iloc[0]
        t_end = (pd.to_datetime(config.get('TRAIN_END', '')) + pd.Timedelta(days=1)) if config.get('TRAIN_END') else _fallback_train_end(master_df)
        
        opt_start = pd.to_datetime(config.get('OPT_START', '')) if config.get('OPT_START') else t_end
        opt_end = (pd.to_datetime(config.get('OPT_END', '')) + pd.Timedelta(days=1)) if config.get('OPT_END') else (opt_start + pd.Timedelta(days=10))
        
        val_df_raw = master_df[(master_df['timestamp'] >= opt_start) & (master_df['timestamp'] < opt_end)].copy()
    except Exception as e:
        raise ValueError(f"날짜 처리 중 오류: {e}")
        
    optuna_cfg = config.get('OPTUNA_CONFIG', {})
    cost = resolve_round_trip_cost(config)
    
    bp = selected_params
    # Fix 6: max_hold_candles 전달
    mhc = bp.get('max_hold_candles', bp.get('predict_ahead_candles'))
    primary_labels, meta_labels = triple_barrier_labeling_afml(master_df, bp['predict_ahead_candles'], bp['take_profit'], bp['stop_loss'], cost, max_hold_candles=mhc, market_type='KRX' if _intraday_mode else 'CRYPTO')
    master_df['target_primary'] = primary_labels
    master_df['target_meta'] = meta_labels
    master_df.dropna(subset=['target_primary', 'target_meta'], inplace=True)
    train_df = master_df[(master_df['timestamp'] >= t_start) & (master_df['timestamp'] < t_end)].copy().reset_index(drop=True)
    # [FIX-9] 학습 경계 직전 라벨 horizon(min(mhc, pa)) 봉 purge — 라벨이 t_end 이후 가격을 참조하지 않도록
    _label_h = int(min(int(mhc), int(bp['predict_ahead_candles'])))
    if _label_h > 0:
        train_df = (train_df.iloc[:-_label_h] if len(train_df) > _label_h else train_df.iloc[0:0]).copy().reset_index(drop=True)
    # 의사결정 시점(KRX: 09:10~15:00)만 학습 — 라벨은 위에서 연속 시계열로 이미 만들었다
    from shared.core.market_utils import decision_point_mask
    train_df = train_df[decision_point_mask(train_df['timestamp'], _intraday_mode)].copy().reset_index(drop=True)
    
    primary_params = {
        'objective': 'multiclass',
        'num_class': 3,
        'learning_rate': bp['learning_rate'],
        'max_depth': bp['max_depth'],
        'num_leaves': bp['num_leaves'],
        'min_child_samples': bp['min_child_samples'],
        'is_unbalance': False,
        'verbose': -1,
        'seed': 42,
        'n_jobs': -1,
        'feature_pre_filter': False,
        'feature_penalty': feature_penalties
    }
    primary_model = lgb.train(primary_params, lgb.Dataset(train_df[features], label=train_df['target_primary'], categorical_feature=categorical_features), num_boost_round=NUM_BOOST_ROUND)  # [FIX-3/4] 튜닝과 동일 라운드
    
    meta_model = None
    if train_df['target_meta'].nunique() > 1:
        meta_params = {
            'objective': 'binary',
            'learning_rate': bp['learning_rate'],
            'max_depth': bp['max_depth'],
            'num_leaves': bp['num_leaves'],
            'min_child_samples': bp['min_child_samples'],
            'verbose': -1,
            'seed': 42,
            'n_jobs': -1,
            'feature_penalty': feature_penalties
        }
        meta_model = lgb.train(meta_params, lgb.Dataset(train_df[train_df['target_primary'] == 1][features], label=train_df[train_df['target_primary'] == 1]['target_meta'], categorical_feature=categorical_features), num_boost_round=NUM_BOOST_ROUND)
    
    deploy_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", market_dir, session_id)
    model_dir = os.path.join(deploy_dir, "model")
    reports_dir = os.path.join(deploy_dir, "reports")
    
    os.makedirs(model_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)
    
    primary_model.save_model(os.path.join(model_dir, f"model_{interval}_primary.txt"))
    if meta_model: meta_model.save_model(os.path.join(model_dir, f"model_{interval}_meta.txt"))
    primary_model.save_model(os.path.join(model_dir, f"model_{interval}.txt"))
    # 사용자가 전략을 직접 확정해 model/ 을 교체 — 출처 기록 (자동연구 탈락 후보와 구분)
    with open(os.path.join(model_dir, "model_source.json"), 'w', encoding='utf-8') as _msf:
        json.dump({"kind": "manual", "status": "MANUAL", "via": "confirm_and_deploy_strategy",
                   "trained_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, _msf, indent=2, ensure_ascii=False)
    _stale = os.path.join(model_dir, "candidate_detail.json")
    if os.path.exists(_stale):
        os.remove(_stale)
    
    bp_new = bp.copy()
    # 분위수 문턱이면 배포 모델(3-class) 자신의 class-1 확률 분포로 다시 환산한다 — 튜닝(binary) 모델의 절대 확률을
    # 다른 목적함수의 확률에 그대로 쓰면 척도가 다르다. '상위 몇 %' 라는 뜻은 모델이 바뀌어도 유지된다.
    if bp.get('buy_quantile') is not None:
        bp_new['buy_threshold'] = threshold_for_model(
            primary_model, train_df[features], bp, lgb_params=primary_params, y_train=train_df['target_primary'],
            purge=max(int(bp['predict_ahead_candles']), int(bp.get('max_hold_candles', 0) or 0)),
            categorical_feature=categorical_features, proba_col=1, log_fn=log_fn)
        log_fn(f"🎚️ [진입 문턱] 배포 모델 class-1 표본 밖 확률 상위 {(1 - float(bp['buy_quantile'])) * 100:.2f}% → {bp_new['buy_threshold']:.4f}")
    bp_new.update({
        'opt_session_id': session_id,
        'memo': config.get('MEMO', ''),
        'train_start': str(t_start),
        'train_end': str(t_end),
        'opt_start': str(opt_start),
        'opt_end': str(opt_end),
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'optuna_score': float(bp.get('score', bp.get('optuna_score', 0.0))),
        # [FIX-3/4] 튜닝(binary)과 배포 모델(3-class multiclass + meta)의 확률 정의 불일치를 명시 기록
        'training_meta': {
            'num_boost_round': NUM_BOOST_ROUND,
            'tuning_objective': 'binary',
            'deployed_primary_objective': 'multiclass(3)',
            'deployed_meta_model': bool(meta_model is not None),
            'buy_threshold_prob_basis': ('multiclass class-1(up) probability, 분위수(buy_quantile)로 이 모델에서 재환산'
                                         if bp.get('buy_quantile') is not None else
                                         'multiclass class-1(up) probability [tuned on binary P(label=1)] — 정의 불일치'),
            'objective_mismatch': True,
        },
    })
    log_fn("⚠️ [확률 정의 불일치] buy_threshold 는 튜닝 시 binary P(label=1) 기준이나, 배포 모델은 3-class multiclass 의 class-1 확률에 적용됩니다 "
           f"(라운드는 {NUM_BOOST_ROUND}로 통일). 배포 백테스트 성과가 튜닝 점수와 다를 수 있습니다. training_meta 에 기록됨.")
    
    with open(os.path.join(model_dir, f"best_params_{interval}.json"), 'w', encoding='utf-8') as f:
        json.dump(bp_new, f, indent=4, ensure_ascii=False)
        
    config['ACTIVE_SESSION_ID'] = session_id
    config['TRAIN_START'] = str(t_start)[:10]
    config['TRAIN_END'] = str(t_end)[:10]
    config['OPT_START'] = str(opt_start)[:10]
    config['OPT_END'] = str(opt_end)[:10]
    try:
        save_project_config("CAiModelBaker", config)
    except Exception as ce:
        log_fn(f"⚠️ CAiModelBaker config 업데이트 실패: {ce}")
        
    has_double_model = (meta_model is not None)
    def run_insample_backtest(df_val):
        if len(df_val) == 0:
            return []
        
        preds_primary = primary_model.predict(df_val[features])
        preds = preds_primary[:, 1]
        
        primary_is_buy = np.argmax(preds_primary, axis=1) == 1
        preds_meta = np.zeros(len(df_val))
        if primary_is_buy.any() and meta_model is not None:
            preds_meta[primary_is_buy] = meta_model.predict(df_val[features].iloc[primary_is_buy])
        
        profit, trades, win_trades, holding, entry_price, entry_time_idx, cooldown_until = 0.0, 0, 0, False, 0, 0, 0
        trade_logs = []
        
        interval_minutes = parse_interval_minutes(interval)
        cooldown_candles = max(1, int(bp_new['cooldown_minutes'] / interval_minutes))
        
        close_col = 'target_close' if 'target_close' in df_val.columns else 'upbit_close'
        low_col = 'target_low' if 'target_low' in df_val.columns else ('low' if 'low' in df_val.columns else None)
        atr_col = 'target_atr' if 'target_atr' in df_val.columns else ('upbit_atr' if 'upbit_atr' in df_val.columns else None)

        for idx in range(len(df_val)):
            current_price = df_val[close_col].iloc[idx]
            current_low = df_val[low_col].iloc[idx] if (low_col and low_col in df_val.columns) else current_price
            current_time = df_val['timestamp'].iloc[idx]
            if holding:
                entry_atr = df_val[atr_col].iloc[entry_time_idx] if atr_col else entry_price * 0.015
                krx_force_exit = (_intraday_mode and is_krx_force_exit(current_time))
                _exit = resolve_exit(
                    entry_price=entry_price, entry_atr=entry_atr,
                    current_price=current_price, current_low=current_low,
                    take_profit=bp_new['take_profit'], stop_loss=bp_new['stop_loss'],
                    emergency_sl_mult=bp_new.get('emergency_sl_mult', bp_new['stop_loss'] * 2.0),
                    max_hold_candles=bp_new.get('max_hold_candles', bp_new['predict_ahead_candles']),
                    elapsed_candles=idx - entry_time_idx, cost=cost,
                    force_exit=krx_force_exit, is_candle_close=True,
                    interval_minutes=interval_minutes,
                )
                if _exit:
                    net_p = _exit['net_return'] / 100.0
                    profit += net_p; trades += 1
                    if net_p > 0: win_trades += 1
                    holding = False; cooldown_until = idx + cooldown_candles
                    h_mins = round((idx - entry_time_idx) * interval_minutes, 1) if interval_minutes < 1.0 else int(round((idx - entry_time_idx) * interval_minutes))
                    trade_logs.append({'entry_time': df_val['timestamp'].iloc[entry_time_idx], 'exit_time': current_time, 'entry_price': entry_price, 'exit_price': _exit['exit_price'], 'hold_mins': h_mins, 'prob': preds[entry_time_idx], 'exit_type': _exit['exit_type'], 'net_return': _exit['net_return']})
            else:
                if _intraday_mode and not is_krx_entry_allowed(current_time):
                    continue
                is_macro_allowed = True
                if config.get('USE_MACRO_FILTER', True):
                    rsi_threshold = config.get('MACRO_RSI_FILTER_THRESHOLD', 50.0)
                    rsi_col = 'target_rsi_daily' if 'target_rsi_daily' in df_val.columns else ('upbit_rsi_daily' if 'upbit_rsi_daily' in df_val.columns else None)
                    if rsi_col and rsi_col in df_val.columns:
                        current_rsi = df_val[rsi_col].iloc[idx]
                        if current_rsi < rsi_threshold:
                            is_macro_allowed = False
                
                is_meta_allowed = True
                if has_double_model:
                    is_meta_allowed = preds_meta[idx] >= META_APPROVAL_THRESHOLD if primary_is_buy[idx] else True
                
                if idx >= cooldown_until and preds[idx] >= bp_new['buy_threshold'] and is_macro_allowed and is_meta_allowed:
                    holding = True
                    entry_price = current_price
                    entry_time_idx = idx

        return trade_logs

    val_trades = run_insample_backtest(val_df_raw)
    train_trades = run_insample_backtest(train_df)
    
    trades_to_save = val_trades + train_trades
    try:
        import csv
        with open(os.path.join(reports_dir, "test_trades.csv"), 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['window_index', 'entry_time', 'exit_time', 'entry_price', 'exit_price', 'hold_mins', 'prob', 'exit_type', 'net_return'])
            writer.writeheader()
            for t in trades_to_save:
                row = {
                    'window_index': 0,
                    'entry_time': str(pd.Timestamp(t['entry_time'])),
                    'exit_time': str(pd.Timestamp(t['exit_time'])),
                    'entry_price': float(t['entry_price']),
                    'exit_price': float(t['exit_price']),
                    'hold_mins': int(t['hold_mins']),
                    'prob': float(t['prob']),
                    'exit_type': str(t['exit_type']),
                    'net_return': float(t['net_return'])
                }
                writer.writerow(row)
        log_fn(f"📈 test_trades.csv 업데이트 완료 ({len(trades_to_save)}건의 거래)")
    except Exception as te:
        log_fn(f"⚠️ test_trades.csv 업데이트 실패: {te}")
        
    log_fn("✅ [SUCCESS] 선택한 전략으로 리배포 완료!")
    return True


def _resolved_spec_fields(feature_config, features, interval, config):
    """scale_v2 프로젝트: 학습에 쓴 확정 파라미터·정책·필요 이력을 스펙에 기록한다.

    실전은 이 값만 읽는다 — 레지스트리 기본값이나 예측 지평 설정이 나중에 바뀌어도 배포 모델의 지표가 바뀌지 않는다.
    legacy 프로젝트는 None (기존 스펙 형식 그대로).
    """
    from shared.features.param_policy import policy_of, POLICY_V2, resolve_feature_config, project_horizon_bars
    from shared.features.feature_registry import DEFAULT_FEATURE_REGISTRY, FEATURE_ALIASES
    from shared.core.market_utils import detect_market_type
    if policy_of(feature_config) != POLICY_V2:
        return None
    fc = {"enabled_features": list(features), "feature_params": feature_config.get("feature_params", {}),
          "param_policy": POLICY_V2}
    resolved, invalid, required = resolve_feature_config(
        fc, DEFAULT_FEATURE_REGISTRY, interval, project_horizon_bars(config), detect_market_type(config),
        FEATURE_ALIASES)
    if invalid:
        raise ValueError(f"[feature_spec] {interval} 에서 쓸 수 없는 지표가 모델에 포함됐습니다: {invalid}")
    return {
        "param_policy": POLICY_V2,
        "params_resolved": True,
        "feature_params": {fid: resolved["feature_params"].get(fid, {}) for fid in features},
        "required_history_bars": required,
        "horizon_bars": resolved.get("horizon_bars"),
    }
