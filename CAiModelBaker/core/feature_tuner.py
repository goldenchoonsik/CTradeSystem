import os
import json
import numpy as np
import pandas as pd
import optuna
try:
    from .data_manager import get_snapshot_path
    from .feature_registry import load_feature_config
    from .physics_constraint import resolve_round_trip_cost
except (ImportError, ValueError):
    from core.data_manager import get_snapshot_path
    from core.feature_registry import load_feature_config
    from core.physics_constraint import resolve_round_trip_cost


def get_interval_context(interval_str: str) -> dict:
    """
    봉길이(interval)에서 모든 시간 단위 파생값을 계산하는 단일 진입점.
    이 함수를 통해서만 interval_minutes, candles_per_day 등을 얻어야 한다.

    반환 dict 키:
      interval_minutes : 봉 하나의 분 수 (예: 15m → 15)
      candles_per_day  : 하루 캔들 수
      candles_per_week : 1주일 캔들 수
      candles_per_year : 1년 캔들 수
      mins_to_candles  : 분(minutes) → 캔들 수 변환 lambda
      candles_to_mins  : 캔들 수 → 분(minutes) 변환 lambda
    """
    if interval_str.endswith('s') and not interval_str.endswith('ms'):
        secs = float(interval_str.replace('s', ''))
        mins = secs / 60.0
    elif interval_str.endswith('m') and not interval_str.endswith('month'):
        mins = float(interval_str.replace('m', ''))
    elif interval_str.endswith('h'):
        mins = float(interval_str.replace('h', '')) * 60.0
    elif interval_str.endswith('d') or interval_str.endswith('D'):
        mins = float(interval_str.lower().replace('d', '')) * 1440.0
    else:
        mins = 15.0  # fallback

    return {
        'interval_minutes': mins,
        'candles_per_day':  max(1, int(24 * 60 / mins)),
        'candles_per_week': max(1, int(7 * 24 * 60 / mins)),
        'candles_per_year': max(1, int(365 * 24 * 60 / mins)),
        'mins_to_candles':  lambda m, _m=mins: max(1, int(m / _m)),
        'candles_to_mins':  lambda c, _m=mins: int(c * _m),
    }

def calculate_ic(a, b=None, c=None):
    """
    Calculate Spearman rank correlation (Information Coefficient).
    Supports two signatures:
      1) calculate_ic(df, feature_col, forward_return_col)
      2) calculate_ic(feature_series, forward_return_series)
    """
    if c is not None:
        df, feat_col, ret_col = a, b, c
        if feat_col not in df.columns or ret_col not in df.columns:
            return 0.0
        f_s = df[feat_col]
        r_s = df[ret_col]
    elif b is not None:
        f_s, r_s = a, b
    else:
        return 0.0

    valid_mask = ~(f_s.isna() | r_s.isna() | np.isinf(f_s) | np.isinf(r_s))
    f_valid = f_s[valid_mask]
    r_valid = r_s[valid_mask]
    if len(f_valid) < 100:
        return 0.0
    try:
        if f_valid.std() == 0 or r_valid.std() == 0:
            return 0.0
        with np.errstate(divide='ignore', invalid='ignore'):
            corr = f_valid.corr(r_valid, method='spearman')
        return 0.0 if np.isnan(corr) else float(corr)
    except:
        return 0.0


def _parse_valid_ts(value):
    """빈 문자열/None/NaT/파싱 불가 값은 None, 아니면 Timestamp 반환 (pd.to_datetime('')는 예외 없이 NaT)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        ts = pd.to_datetime(value)
    except Exception:
        return None
    return None if pd.isna(ts) else ts


def run_pre_tuning_features(config, log_fn=None):
    """
    Tune feature parameters to maximize statistical Spearman IC.
    """
    if log_fn is None:
        log_fn = print
        
    interval = config.get('INTERVAL', '15m')
    ctx = get_interval_context(interval)
    candles_per_day = ctx['candles_per_day']  # 하위 코드(RSI daily 계산 등)에서 사용

    snapshot_path = get_snapshot_path(config)
    if not os.path.exists(snapshot_path):
        raise FileNotFoundError("Dataset not found. Build dataset first.")

    log_fn("🔍 [Feature Tuner] Starting feature parameter optimization...")

    # 튜닝에 필요한 핵심 컬럼만 선택 로드 (I/O 90% 단축)
    sample_head = pd.read_csv(snapshot_path, nrows=5)
    candidate_cols = [
        'timestamp', 'target_close', 'upbit_close', 'target_high', 'upbit_high',
        'target_low', 'upbit_low', 'target_volume', 'upbit_volume',
        'target_trade_bid_ratio', 'upbit_trade_bid_ratio',
        'binance_spot_close', 'binance_spot_taker_ratio'
    ]
    use_cols = [c for c in candidate_cols if c in sample_head.columns]
    df = pd.read_csv(snapshot_path, usecols=use_cols)
    df = df.sort_values('timestamp').reset_index(drop=True)
    df['timestamp'] = pd.to_datetime(df['timestamp'])


    # 🛡️ [Quant Standard] 데이터 누수(Leakage) 및 단기 과적합 방지:
    # 30일 단기 슬라이싱 대신 Config의 TRAIN 구간을 우선 사용하여 통계적 강건성 확보
    train_start = config.get("TRAIN_START")
    train_end = config.get("TRAIN_END")
    t_start = _parse_valid_ts(train_start)
    t_end = _parse_valid_ts(train_end)
    if t_start is not None and t_end is not None:
        mask = (df['timestamp'] >= t_start) & (df['timestamp'] <= t_end)
        if mask.sum() >= 5000:
            analysis_df = df[mask].reset_index(drop=True)
            log_fn(f"   → 분석 데이터: Train 구간({train_start} ~ {train_end}, {len(analysis_df):,}캔들) 기반 정규 튜닝")
        else:
            tuning_days = 30
            desired_candles = ctx['mins_to_candles'](tuning_days * 24 * 60)
            tail_candles = min(len(df), max(5000, desired_candles))
            # 최근(tail)은 홀드아웃을 포함하므로 데이터 선두(head)를 사용한다 (홀드아웃 누수 방지)
            analysis_df = df.head(tail_candles).reset_index(drop=True)
            log_fn(f"   → 분석 데이터: 선두 {tail_candles:,}캔들 (TRAIN 구간 부족, 홀드아웃 배제 head 폴백 튜닝)")
    else:
        tuning_days = 30
        desired_candles = ctx['mins_to_candles'](tuning_days * 24 * 60)
        tail_candles = min(len(df), max(5000, desired_candles))
        analysis_df = df.head(tail_candles).reset_index(drop=True)
        log_fn(f"   → 분석 데이터: 선두 {tail_candles:,}캔들 (TRAIN 설정 없음, 홀드아웃 배제 head 튜닝)")

    # Fix 1: forward_candles — TARGET_HORIZON_CONFIG 잠금 모드 우선 반영
    target_horizon_cfg = config.get('TARGET_HORIZON_CONFIG', {})
    is_horizon_locked = (target_horizon_cfg.get('mode', 'locked') == 'locked')
    
    if is_horizon_locked and target_horizon_cfg.get('locked_predict_ahead'):
        forward_candles = int(target_horizon_cfg.get('locked_predict_ahead', 6))
        forward_minutes = ctx['candles_to_mins'](forward_candles)
        log_fn(f"🔒 [Target Horizon] IC 타겟: {forward_candles}캔들 = {forward_minutes}분 선행 (사정거리 잠금 모드 기준)")
    else:
        pa_range_cfg  = config.get('OPTUNA_CONFIG', {}).get('predict_ahead_candles_range', {})
        pa_min_cfg    = pa_range_cfg.get('user_min', pa_range_cfg.get('min', 8))
        pa_max_cfg    = pa_range_cfg.get('user_max', pa_range_cfg.get('max', 96))
        forward_candles = int((pa_min_cfg + pa_max_cfg) / 2)
        forward_minutes = ctx['candles_to_mins'](forward_candles)
        log_fn(f"   → IC 타겟: {forward_candles}캔들 = {forward_minutes}분 선행 (PA {pa_min_cfg}~{pa_max_cfg} mid)")
    
    # 기본 Series 사전 추출 (매 trial마다 DataFrame 전체 복사하는 오버헤드 100% 제거)
    t_close = analysis_df['target_close'] if 'target_close' in analysis_df.columns else analysis_df['upbit_close']
    t_high = analysis_df['target_high'] if 'target_high' in analysis_df.columns else analysis_df.get('upbit_high', t_close)
    t_low = analysis_df['target_low'] if 'target_low' in analysis_df.columns else analysis_df.get('upbit_low', t_close)
    t_vol = analysis_df['target_volume'] if 'target_volume' in analysis_df.columns else analysis_df.get('upbit_volume', pd.Series(0.0, index=analysis_df.index))
    t_bid_ratio = analysis_df['target_trade_bid_ratio'] if 'target_trade_bid_ratio' in analysis_df.columns else analysis_df.get('upbit_trade_bid_ratio', pd.Series(0.5, index=analysis_df.index))
    binance_close = analysis_df.get('binance_spot_close')
    binance_taker = analysis_df.get('binance_spot_taker_ratio')
    
    from shared.core.market_utils import detect_market_type as _dmt, session_forward_return
    _intraday_krx = _dmt(config) == 'KRX' and bool(config.get('INTRADAY_SQUARE_OFF', True))
    forward_return = pd.Series(session_forward_return(t_close.to_numpy(), analysis_df['timestamp'], forward_candles, _intraday_krx),
                               index=t_close.index)
    
    feature_config = load_feature_config()
    raw_enabled = feature_config.get('enabled_features', [])
    from core.feature_registry import FEATURE_ALIASES
    enabled_features = [FEATURE_ALIASES.get(f, f) for f in raw_enabled]
    
    tune_targets = {}
    if "binance_ret_5m" in enabled_features:
        tune_targets["binance_ret_5m"] = {
            "param": "candles",
            "type": "int",
            "min": 1,
            "max": 60
        }
    if "kimchi_premium" in enabled_features:
        tune_targets["kimchi_premium"] = {
            "param": "window",
            "type": "int_step",
            "min": 60,
            "max": 4320,
            "step": 60
        }
    if "target_ma_ratio" in enabled_features or "upbit_ma_ratio" in enabled_features:
        tune_targets["target_ma_ratio"] = {
            "param": "period",
            "type": "int",
            "min": 5,
            "max": 50
        }
    if "target_rsi_daily" in enabled_features or "upbit_rsi_daily" in enabled_features:
        tune_targets["target_rsi_daily"] = {
            "param": "period_days",
            "type": "int",
            "min": 1,
            "max": 30
        }
    if "target_trade_bid_ratio" in enabled_features or "upbit_trade_bid_ratio" in enabled_features:
        tune_targets["target_trade_bid_ratio"] = {
            "param": "window",
            "type": "int",
            "min": 1,
            "max": 20
        }
    if "binance_spot_taker_ratio" in enabled_features:
        tune_targets["binance_spot_taker_ratio"] = {
            "param": "window",
            "type": "int",
            "min": 1,
            "max": 20
        }
    if "target_rsi_curr" in enabled_features or "upbit_rsi_curr" in enabled_features:
        tune_targets["target_rsi_curr"] = {
            "param": "period_candles",
            "type": "int",
            "min": 5,
            "max": 30
        }
    if "target_atr_ratio" in enabled_features or "upbit_atr_ratio" in enabled_features:
        tune_targets["target_atr_ratio"] = {
            "param": "period_candles",
            "type": "int",
            "min": 5,
            "max": 30
        }
    if "target_adx" in enabled_features or "upbit_adx" in enabled_features:
        tune_targets["target_adx"] = {
            "param": "period_candles",
            "type": "int",
            "min": 5,
            "max": 30
        }
    if "target_bb_pctb" in enabled_features or "upbit_bb_pctb" in enabled_features:
        tune_targets["target_bb_pctb"] = {
            "param": "period",
            "type": "int",
            "min": 5,
            "max": 50
        }
    if "target_macd_hist" in enabled_features or "upbit_macd_hist" in enabled_features:
        tune_targets["target_macd_hist"] = {
            "param": "fast_period",
            "type": "int",
            "min": 5,
            "max": 20
        }
    if "target_mfi" in enabled_features or "upbit_mfi" in enabled_features:
        tune_targets["target_mfi"] = {
            "param": "period",
            "type": "int",
            "min": 5,
            "max": 30
        }
        
    if not tune_targets:
        log_fn("ℹ️ No active features with tunable parameters. Keeping current.")
        return {}
        
    log_fn(f"🎯 Target tuning features: {list(tune_targets.keys())}")
    
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    
    def objective(trial):
        score = 0.0
        
        if "binance_ret_5m" in tune_targets and binance_close is not None:
            candles = trial.suggest_int("binance_ret_5m_candles", tune_targets["binance_ret_5m"]["min"], tune_targets["binance_ret_5m"]["max"])
            feat_binance_ret = binance_close.pct_change(candles).fillna(0.0)
            score += abs(calculate_ic(feat_binance_ret, forward_return))
            
        if "kimchi_premium" in tune_targets and binance_close is not None:
            window = trial.suggest_int("kimchi_premium_window", tune_targets["kimchi_premium"]["min"], tune_targets["kimchi_premium"]["max"], step=tune_targets["kimchi_premium"]["step"])
            approx_usdkrw = (t_close / (binance_close + 1e-8)).rolling(window, min_periods=1).mean()
            feat_kimchi = (t_close / (binance_close * approx_usdkrw + 1e-8)) - 1.0
            score += abs(calculate_ic(feat_kimchi, forward_return))
            
        if "target_ma_ratio" in tune_targets:
            period = trial.suggest_int("target_ma_ratio_period", tune_targets["target_ma_ratio"]["min"], tune_targets["target_ma_ratio"]["max"])
            feat_ma_ratio = t_close / (t_close.rolling(period, min_periods=1).mean() + 1e-8) - 1.0
            score += abs(calculate_ic(feat_ma_ratio, forward_return))
            
        if "target_rsi_daily" in tune_targets:
            period_days = trial.suggest_int("target_rsi_daily_period_days", tune_targets["target_rsi_daily"]["min"], tune_targets["target_rsi_daily"]["max"])
            from shared.features.feature_registry import _compute_target_rsi_daily
            feat_rsi = _compute_target_rsi_daily(analysis_df, {'period_days': period_days}, interval)
            score += abs(calculate_ic(feat_rsi, forward_return))
 
        if "target_trade_bid_ratio" in tune_targets:
            window = trial.suggest_int("target_trade_bid_ratio_window", tune_targets["target_trade_bid_ratio"]["min"], tune_targets["target_trade_bid_ratio"]["max"])
            feat_bid_ratio = t_bid_ratio.rolling(window, min_periods=1).mean().fillna(0.5)
            score += abs(calculate_ic(feat_bid_ratio, forward_return))
 
        if "binance_spot_taker_ratio" in tune_targets:
            window = trial.suggest_int("binance_spot_taker_ratio_window", tune_targets["binance_spot_taker_ratio"]["min"], tune_targets["binance_spot_taker_ratio"]["max"])
            taker_s = binance_taker if binance_taker is not None else pd.Series(0.5, index=t_close.index)
            feat_taker_ratio = taker_s.rolling(window, min_periods=1).mean().fillna(0.5)
            score += abs(calculate_ic(feat_taker_ratio, forward_return))
 
        if "target_rsi_curr" in tune_targets:
            rsi_win = trial.suggest_int("target_rsi_curr_period_candles", tune_targets["target_rsi_curr"]["min"], tune_targets["target_rsi_curr"]["max"])
            diff_curr = t_close.diff()
            u_curr = diff_curr.clip(lower=0)
            d_curr = -diff_curr.clip(upper=0)
            rs_curr = u_curr.rolling(rsi_win, min_periods=1).mean() / (d_curr.rolling(rsi_win, min_periods=1).mean() + 1e-8)
            feat_rsi_curr = 100.0 - (100.0 / (1.0 + rs_curr))
            score += abs(calculate_ic(feat_rsi_curr, forward_return))
 
        if "target_atr_ratio" in tune_targets:
            atr_win = trial.suggest_int("target_atr_ratio_period_candles", tune_targets["target_atr_ratio"]["min"], tune_targets["target_atr_ratio"]["max"])
            if not t_high.isna().all() and not t_low.isna().all():
                close_prev = t_close.shift(1)
                tr = pd.concat([t_high - t_low, (t_high - close_prev).abs(), (t_low - close_prev).abs()], axis=1).max(axis=1)
                atr = tr.rolling(atr_win, min_periods=1).mean()
                feat_atr_ratio = (atr / (t_close + 1e-8)).fillna(0.0)
            else:
                std_val = t_close.rolling(atr_win, min_periods=1).std()
                feat_atr_ratio = (std_val / (t_close + 1e-8)).fillna(0.0)
            score += abs(calculate_ic(feat_atr_ratio, forward_return))
 
        if "target_adx" in tune_targets:
            adx_win = trial.suggest_int("target_adx_period_candles", tune_targets["target_adx"]["min"], tune_targets["target_adx"]["max"])
            if not t_high.isna().all() and not t_low.isna().all():
                close_prev = t_close.shift(1)
                up_move = t_high - t_high.shift(1)
                down_move = t_low.shift(1) - t_low
                plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
                minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
                tr = pd.concat([t_high - t_low, (t_high - close_prev).abs(), (t_low - close_prev).abs()], axis=1).max(axis=1)
                tr_smooth = tr.rolling(adx_win, min_periods=1).mean()
                plus_dm_smooth = pd.Series(plus_dm, index=t_close.index).rolling(adx_win, min_periods=1).mean()
                minus_dm_smooth = pd.Series(minus_dm, index=t_close.index).rolling(adx_win, min_periods=1).mean()
                plus_di = 100.0 * (plus_dm_smooth / (tr_smooth + 1e-8))
                minus_di = 100.0 * (minus_dm_smooth / (tr_smooth + 1e-8))
                dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-8))
                feat_adx = dx.rolling(adx_win, min_periods=1).mean().fillna(20.0)
            else:
                feat_adx = pd.Series(20.0, index=t_close.index)
            score += abs(calculate_ic(feat_adx, forward_return))
 
        if "target_bb_pctb" in tune_targets:
            bb_period = trial.suggest_int("target_bb_pctb_period", tune_targets["target_bb_pctb"]["min"], tune_targets["target_bb_pctb"]["max"])
            bb_std = 2.0
            bb_ma = t_close.rolling(bb_period, min_periods=1).mean()
            bb_std_val = t_close.rolling(bb_period, min_periods=1).std()
            bb_upper = bb_ma + bb_std * bb_std_val
            bb_lower = bb_ma - bb_std * bb_std_val
            feat_bb_pctb = ((t_close - bb_lower) / (bb_upper - bb_lower + 1e-8)).fillna(0.5)
            score += abs(calculate_ic(feat_bb_pctb, forward_return))
 
        if "target_macd_hist" in tune_targets:
            macd_fast = trial.suggest_int("target_macd_hist_fast_period", tune_targets["target_macd_hist"]["min"], tune_targets["target_macd_hist"]["max"])
            macd_slow = macd_fast + 14
            macd_signal = 9
            ema_f = t_close.ewm(span=macd_fast, adjust=False).mean()
            ema_s = t_close.ewm(span=macd_slow, adjust=False).mean()
            macd_line = ema_f - ema_s
            macd_sig = macd_line.ewm(span=macd_signal, adjust=False).mean()
            feat_macd_hist = (macd_line - macd_sig).fillna(0.0)
            score += abs(calculate_ic(feat_macd_hist, forward_return))
 
        if "target_mfi" in tune_targets:
            mfi_period = trial.suggest_int("target_mfi_period", tune_targets["target_mfi"]["min"], tune_targets["target_mfi"]["max"])
            mfi_close = t_close
            mfi_vol = t_vol
            mfi_high = t_high
            mfi_low = t_low
            mfi_tp = (mfi_high + mfi_low + mfi_close) / 3.0
            mfi_rmf = mfi_tp * mfi_vol
            mfi_tp_diff = mfi_tp.diff()
            pmf = np.where(mfi_tp_diff > 0, mfi_rmf, 0.0)
            nmf = np.where(mfi_tp_diff < 0, mfi_rmf, 0.0)
            pmf_sum = pd.Series(pmf, index=t_close.index).rolling(mfi_period, min_periods=1).sum()
            nmf_sum = pd.Series(nmf, index=t_close.index).rolling(mfi_period, min_periods=1).sum()
            mr = pmf_sum / (nmf_sum + 1e-8)
            feat_mfi = (100.0 - (100.0 / (1.0 + mr))).fillna(50.0)
            score += abs(calculate_ic(feat_mfi, forward_return))
            
        return score

    study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=30)

    
    best_params = study.best_params
    result = {}
    
    for fid, target in tune_targets.items():
        param_name = target["param"]
        optuna_key = f"{fid}_{param_name}"
        if optuna_key in best_params:
            if fid not in result:
                result[fid] = {}
            result[fid][param_name] = best_params[optuna_key]
            log_fn(f"   ↳ 🏆 [{fid}] Best {param_name}: {best_params[optuna_key]}")
            
    log_fn("✅ [Feature Tuner] Pre-tuning complete!")
    return result


def generate_block_shadow_features(df, features, block_size, seed=None):
    """
    Generate shadow features by block bootstrapping to preserve short-term autocorrelation.
    seed 를 주면 같은 입력 → 같은 그림자 (2026-10-20: 시드가 없어 같은 데이터로 돌려도 합격선이 423.97/444.98 로 달랐다).
    """
    rng = np.random.default_rng(seed)
    n = len(df)
    if n <= block_size or block_size <= 1:
        return pd.DataFrame({f"shadow_{f}": rng.permutation(df[f].values) for f in features}, index=df.index)

    num_blocks = int(np.ceil(n / block_size))
    block_indices = []
    for i in range(num_blocks):
        start = i * block_size
        end = min((i + 1) * block_size, n)
        block_indices.append(np.arange(start, end))
    
    shuffled_block_idx = rng.permutation(len(block_indices))
    shuffled_global_idx = np.concatenate([block_indices[idx] for idx in shuffled_block_idx])

    # 열을 하나씩 끼우면 수백 번 insert → PerformanceWarning·느림. 한 번에 만든다.
    return pd.DataFrame({f"shadow_{f}": df[f].values[shuffled_global_idx] for f in features}, index=df.index)


# 자동 선정 검증 조각의 최소 거래일 — 조각 4개(학습 확장 3회) × 2거래일.
MIN_SELECTION_TRADING_DAYS = 8


def split_dates_into_expanding_folds(timestamps: pd.Series, n_splits: int = 3):
    """학습 구간을 '실제 거래일' 기준 n_splits+1 조각으로 나눠, k번째 검증용 확장 창의 끝 행 번호를 돌려준다.

    (2026-10-20) 예전엔 조각 최소 크기를 '30일×24시간'을 봉 수로 바꿔 박아 두었다. 10초봉이면 259,200봉이라
    약 6.5만 봉 학습 구간보다 커서 세 조각이 모두 학습 구간 전체 = 같은 데이터로 세 번 학습했다(검증 0).
    24시간 가정은 장 시간이 6.5시간인 KRX 에서도 틀렸다. 이제 데이터에 실제로 있는 날짜로 자른다.
    """
    days = pd.to_datetime(timestamps).dt.normalize()
    uniq = days.drop_duplicates().sort_values().reset_index(drop=True)
    n_days = len(uniq)
    if n_days < MIN_SELECTION_TRADING_DAYS:
        raise ValueError(
            f"학습 구간에 거래일이 {n_days}일뿐이라 시점을 나눈 검증을 할 수 없습니다 "
            f"(최소 {MIN_SELECTION_TRADING_DAYS}거래일). 연구 기간을 늘려 주세요.")
    chunk_days = np.array_split(uniq.values, n_splits + 1)
    ends = []
    for k in range(1, n_splits + 1):
        last_day = chunk_days[k][-1]
        ends.append(int((days.values <= last_day).sum()))
    return ends, n_days


def find_dead_feature_columns(df: pd.DataFrame, cols, min_valid_ratio: float = 0.5):
    """학습 구간에서 값이 없거나(유효 < 50%) 한 값뿐인 열 — 이 종목·인터벌엔 데이터가 없는 지표.

    이런 열의 그림자도 gain 0 이라, 그대로 두면 '그림자 80번째 백분위' 합격선이 끌려 내려가
    소음 수준 지표까지 통과한다(122630 10s: 후보 115개 중 60여 개가 이 경우였다).
    """
    dead = {}
    n = len(df)
    for c in cols:
        s = df[c]
        valid = s.notna()
        if s.dtype.kind in 'fc':
            valid &= np.isfinite(s.to_numpy(dtype=float, na_value=np.nan))
        ratio = float(valid.sum()) / n if n else 0.0
        if ratio < min_valid_ratio:
            dead[c] = f"유효값 {ratio:.0%}"
        elif s[valid].nunique() <= 1:
            dead[c] = "값이 하나뿐"
    return dead


def find_duplicate_feature_columns(df: pd.DataFrame, cols):
    """값이 앞선 열과 완전히 같은 열 → {복제 열: 원본 열}.

    KRX 종목 Gold 에선 upbit_* 원천 열이 target_* 와 같은 값이라 upbit_ma_ratio 등이 target_ma_ratio 의
    복제다. 트리는 동점이면 앞 열만 써서 복제는 gain 0 — 그 그림자도 0 근처라 합격선을 끌어내린다.
    """
    seen, dup = {}, {}
    for c in cols:
        key = pd.util.hash_pandas_object(df[c], index=False).sum()
        if key in seen and df[c].equals(df[seen[key]]):
            dup[c] = seen[key]
        else:
            seen.setdefault(key, c)
    return dup


def run_auto_feature_selection(config, log_fn=None, check_abort_fn=None, lookback_period: str = "3y"):
    """
    LightGBM Feature Importance Auto Feature Selection (Quant Engine v3 - Advanced)
    """
    if log_fn is None:
        log_fn = print

    import lightgbm as lgb
    from core.model_engine import triple_barrier_labeling, label_decision_points
    from shared.features.feature_engine import run_feature_engineering
    from core.feature_registry import DEFAULT_FEATURE_REGISTRY

    log_fn("\n🧬 [Auto Feature Selection] Advanced Multi-Period Cross-Validation Engine")
    
    from shared.config.config_manager import load_project_config
    optuna_cfg = {}
    feature_penalties = {}
    try:
        cfg_data = load_project_config("CAiModelBaker")
        optuna_cfg = cfg_data.get('OPTUNA_CONFIG', {})
        feature_penalties = cfg_data.get('FEATURE_CONFIG', {}).get('feature_penalties', {})
    except Exception as e:
        log_fn(f"⚠️ Error loading config: {e}")

    target_horizon_cfg = config.get('TARGET_HORIZON_CONFIG') or cfg_data.get('TARGET_HORIZON_CONFIG', {})
    is_horizon_locked = (target_horizon_cfg.get('mode', 'locked') == 'locked')
    
    if is_horizon_locked and target_horizon_cfg.get('locked_predict_ahead'):
        locked_pa = int(target_horizon_cfg.get('locked_predict_ahead', 6))
        predict_ahead_list = [locked_pa]
        log_fn(f"🔒 [Target Horizon] 고정 사정거리 모드: {locked_pa}캔들 단일 타깃으로 정밀 지표 선발 (Moving Target 원천 차단)")
    else:
        pa_range = optuna_cfg.get('predict_ahead_candles_range', {})
        pa_min = pa_range.get('user_min', pa_range.get('min', 12))
        pa_max = pa_range.get('user_max', pa_range.get('max', 48))
        pa_mid = int((pa_min + pa_max) / 2)
        predict_ahead_list = sorted(list(set([int(pa_min), int(pa_mid), int(pa_max)])))
        log_fn(f"📊 [Config] Optuna horizon range: {pa_min} ~ {pa_max} candles")
        log_fn(f"   → Ensemble targets: {predict_ahead_list}")

    snapshot_path = get_snapshot_path(config)
    if not os.path.exists(snapshot_path):
        raise FileNotFoundError("Dataset not found. Build dataset first.")

    df = pd.read_csv(snapshot_path)
    df = df.sort_values('timestamp').reset_index(drop=True)
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    interval = config.get('INTERVAL', '15m')

    # 분석 구간 = 지표 스크리닝과 같은 규칙: 끝은 홀드아웃 직전(WFA 창 설계와 무관), 시작은 lookback.
    # (예전: '데이터 앞 60%' → 10-20 오전: WFA 구간(TRAIN_START~END) — 창 설계를 바꿀 때마다 선정 구간이 움직였다)
    from CAiModelBaker.core.feature_screener import slice_screening_window
    _win_df, sel_start, sel_end, sel_desc, _boundary = slice_screening_window(df[['timestamp']], config, lookback_period)
    train_start = pd.to_datetime(sel_start)
    train_end = pd.to_datetime(sel_end) + pd.Timedelta(days=1)   # 끝날 포함
    log_fn(f"   → 분석 구간: {sel_desc}")

    df = run_feature_engineering(df, interval, {'enabled_features': [], 'feature_params': {}})

    # 참조 종목 지표는 지연 등록이라, 여기서 직접 등록하지 않으면 '이 프로세스에서 스크리닝을 먼저 돌렸나'에 따라
    # ref_* 가 후보에 들기도 빠지기도 했다 (T-7 탐색 풀 드리프트가 자동 선정에 남아 있던 것, 2026-10-20).
    from shared.features.feature_registry import register_configured_reference_features
    register_configured_reference_features(config, DEFAULT_FEATURE_REGISTRY)

    all_feature_ids = list(DEFAULT_FEATURE_REGISTRY.keys())
    eval_feature_cols = []
    feature_to_cols = {}

    for fid in all_feature_ids:
        feature_def = DEFAULT_FEATURE_REGISTRY.get(fid)
        if feature_def is None or 'compute' not in feature_def:
            continue
            
        compute_fn = feature_def['compute']
        params_def = feature_def.get('params', {})
        
        if not params_def:
            col_name = fid
            try:
                df[col_name] = compute_fn(df, {}, interval)
                eval_feature_cols.append(col_name)
                feature_to_cols[fid] = [col_name]
            except Exception as e:
                log_fn(f"⚠️ {fid} baseline calc error: {e}")
            continue

        target_param_name = None
        target_param_cfg = None
        for pname, pcfg in params_def.items():
            if 'min' in pcfg and 'max' in pcfg:
                target_param_name = pname
                target_param_cfg = pcfg
                break
        
        if target_param_name is None:
            col_name = fid
            default_params = {pname: pcfg.get('default') for pname, pcfg in params_def.items()}
            try:
                df[col_name] = compute_fn(df, default_params, interval)
                eval_feature_cols.append(col_name)
                feature_to_cols[fid] = [col_name]
            except Exception as e:
                log_fn(f"⚠️ {fid} default calc error: {e}")
            continue

        p_min = target_param_cfg['min']
        p_max = target_param_cfg['max']
        is_int = isinstance(p_min, int) and isinstance(p_max, int)

        # Fix 3: 3점 평가 (min / mid / max) — 중간 구간 놓침 방지
        p_mid = int((p_min + p_max) / 2) if is_int else round((p_min + p_max) / 2, 2)
        periods = sorted(list(set([p_min, p_mid, p_max])))
        feature_to_cols[fid] = []

        for p_val in periods:
            run_params = {}
            for pname, pcfg in params_def.items():
                if pname == target_param_name:
                    run_params[pname] = int(p_val) if is_int else float(p_val)
                else:
                    run_params[pname] = pcfg.get('default')

            if p_val == p_min:
                suffix = "min"
            elif p_val == p_max:
                suffix = "max"
            else:
                suffix = "mid"
            col_name = f"{fid}_{suffix}_{int(p_val) if is_int else round(p_val, 2)}"

            try:
                df[col_name] = compute_fn(df, run_params, interval)
                eval_feature_cols.append(col_name)
                feature_to_cols[fid].append(col_name)
            except Exception as e:
                log_fn(f"⚠️ {fid} ({suffix}={p_val}) calc error: {e}")

    train_df = df[(df['timestamp'] >= train_start) & (df['timestamp'] < train_end)].copy().reset_index(drop=True)

    if len(train_df) < 500:
        raise ValueError(f"분석 구간 데이터가 {len(train_df)}행뿐입니다 (최소 500). 기간 선택 또는 데이터셋을 확인하세요.")

    # 이 종목·인터벌에 데이터가 없는 지표는 후보에서도, 그림자 합격선 계산에서도 뺀다.
    dead_cols = find_dead_feature_columns(train_df, eval_feature_cols)
    dup_cols = find_duplicate_feature_columns(train_df, [c for c in eval_feature_cols if c not in dead_cols])
    if dup_cols:
        log_fn(f"   → 다른 지표와 값이 같아 제외 ({len(dup_cols)}개 열): "
               + ", ".join(f"{d}={o}" for d, o in list(dup_cols.items())[:8]) + (" …" if len(dup_cols) > 8 else ""))
        dead_cols = {**dead_cols, **dup_cols}
    if dead_cols:
        eval_feature_cols = [c for c in eval_feature_cols if c not in dead_cols]
        feature_to_cols = {fid: [c for c in cols if c not in dead_cols] for fid, cols in feature_to_cols.items()}
        dead_fids = sorted(fid for fid, cols in feature_to_cols.items() if not cols)
        feature_to_cols = {fid: cols for fid, cols in feature_to_cols.items() if cols}
        log_fn(f"   → 데이터 없음·복제로 후보 제외 ({len(dead_fids)}개 지표): {', '.join(dead_fids)}")

    if len(eval_feature_cols) < 2:
        log_fn("⚠️ [Auto Feature Selection] Insufficient feature columns. Keeping current.")
        return None

    accumulated_fi = {f: 0.0 for f in eval_feature_cols}
    accumulated_shadow_fi = {f"shadow_{f}": 0.0 for f in eval_feature_cols}

    optuna_cfg = config.get('OPTUNA_CONFIG', {})
    cost      = resolve_round_trip_cost(config)

    # Fix 4: TP/SL — OPTUNA_CONFIG 범위 mid 값 동적 읽기 (봉길이·사용자 설정 자동 반영)
    tp_range_cfg = optuna_cfg.get('take_profit_range', {})
    sl_range_cfg = optuna_cfg.get('stop_loss_range', {})
    take_profit = (
        (tp_range_cfg.get('user_min', tp_range_cfg.get('min', 1.5)) +
         tp_range_cfg.get('user_max', tp_range_cfg.get('max', 3.5))) / 2
    )
    stop_loss = (
        (sl_range_cfg.get('user_min', sl_range_cfg.get('min', 0.8)) +
         sl_range_cfg.get('user_max', sl_range_cfg.get('max', 2.0))) / 2
    )
    log_fn(f"   → 레이블 기준: TP={take_profit:.2f} ATR, SL={stop_loss:.2f} ATR")

    # 검증 조각 — 실제 거래일 기준 확장 창 (split_dates_into_expanding_folds 주석 참조)
    n_splits = 3
    fold_end_rows, n_train_days = split_dates_into_expanding_folds(train_df['timestamp'], n_splits)
    # 하루 봉 수도 24시간 가정이 아니라 데이터에서 잰다 (KRX 10초봉: 8,640 이 아니라 약 2,340)
    candles_per_day = max(1, int(round(len(train_df) / n_train_days)))
    log_fn(f"   → Candidate sub-features: {len(eval_feature_cols)} | Train samples: {len(train_df):,} ({n_train_days}거래일)")
    log_fn(f"   → 확장 창 끝: " + ", ".join(f"{e:,}봉" for e in fold_end_rows))
    from shared.core.market_utils import detect_market_type
    _tb_market_type = 'KRX' if (detect_market_type(config) == 'KRX' and config.get('INTRADAY_SQUARE_OFF', True)) else 'CRYPTO'
    log_fn("   → Running 3-Fold time-series cross-validation & block shadow analysis...")

    total_evals = 0
    for train_end_idx in fold_end_rows:
        fold_train_df = train_df.iloc[:train_end_idx].copy().reset_index(drop=True)
        
        if len(fold_train_df) < 300:
            continue

        for pa in predict_ahead_list:
            # Fix 4 (continued): 루프 내에서 TP/SL 분기 제거 — 위에서 동적 계산한 값 사용

            fold_train_df_clean = label_decision_points(fold_train_df, pa, take_profit, stop_loss, cost, market_type=_tb_market_type).dropna(subset=['target'])

            if len(fold_train_df_clean) < 150:
                continue

            # Fix 5: block_size — 최소 1일치 캔들 보장 (봉길이 연동)
            block_size = max(pa * 3, candles_per_day)
            shadow_df = generate_block_shadow_features(fold_train_df_clean, eval_feature_cols, block_size, seed=42 + total_evals)
            shadow_features = [f"shadow_{f}" for f in eval_feature_cols]

            lgb_train_df = pd.concat([fold_train_df_clean[eval_feature_cols], shadow_df], axis=1)
            all_eval_features = eval_feature_cols + shadow_features

            categorical_feats = [f for f in eval_feature_cols if f in ['upbit_hour', 'upbit_is_weekend']]
            categorical_all = categorical_feats + [f"shadow_{f}" for f in categorical_feats]

            train_data = lgb.Dataset(
                lgb_train_df[all_eval_features],
                label=fold_train_df_clean['target'],
                categorical_feature=categorical_all
            )
            lgb_params = {
                'objective':     'binary',
                'metric':        'binary_logloss',
                'learning_rate': 0.05,
                'num_leaves':    31,
                'is_unbalance':  True,
                'verbose':       -1,
                'seed':          42,
                'n_jobs':        -1,
                'feature_pre_filter': False
            }
            # Fix 7: num_boost_round — 샘플 수 비례 동적 조정 (최소 50, 최대 100)
            n_boost = max(50, min(100, len(fold_train_df_clean) // 100))
            model = lgb.train(lgb_params, train_data, num_boost_round=n_boost)

            importance_vals = model.feature_importance(importance_type='gain')
            fi = dict(zip(all_eval_features, importance_vals))

            for f in eval_feature_cols:
                accumulated_fi[f] += fi[f]
                accumulated_shadow_fi[f"shadow_{f}"] += fi[f"shadow_{f}"]
            
            total_evals += 1

    if total_evals == 0:
        raise ValueError("유효한 평가가 0회입니다 (라벨 가능한 표본 부족). 연구 기간 또는 사정거리를 확인하세요.")

    mean_fi = {f: accumulated_fi[f] / total_evals for f in eval_feature_cols}
    mean_shadow_fi = {sf: accumulated_shadow_fi[sf] / total_evals for sf in accumulated_shadow_fi}
    
    shadow_max_fi = np.percentile(list(mean_shadow_fi.values()), 80) if mean_shadow_fi else 0.0

    log_fn(f"\n📊 [Auto Feature Selection] CV Mean Gain Analysis:")
    log_fn(f"   🛡️ Shadow baseline threshold (80th Pct): {shadow_max_fi:.2f}")

    max_fi_val = max(mean_fi.values()) if max(mean_fi.values()) > 0 else 1.0

    init_selected = []
    best_col_map = {}
    
    for fid, cols in feature_to_cols.items():
        is_fid_passed = False
        best_col_info = None
        
        for col in cols:
            fval = mean_fi.get(col, 0.0)
            penalty = feature_penalties.get(fid, 0.0)
            adjusted_val = fval * (1.0 - penalty)
            is_passed = adjusted_val > shadow_max_fi
            
            if is_passed:
                is_fid_passed = True
                
            if best_col_info is None or adjusted_val > best_col_info['adjusted_val']:
                best_col_info = {
                    'col': col,
                    'fval': fval,
                    'adjusted_val': adjusted_val,
                    'is_passed': is_passed
                }
                
        if best_col_info:
            col = best_col_info['col']
            fval = best_col_info['fval']
            adjusted_val = best_col_info['adjusted_val']
            is_passed = best_col_info['is_passed']
            
            penalty = feature_penalties.get(fid, 0.0)
            flag = "✅" if is_passed else "❌"
            bar_len = int(fval / max_fi_val * 20) if max_fi_val > 0 else 0
            bar  = "█" * bar_len + "░" * (20 - bar_len)
            penalty_str = f" [Penalty: {penalty:.1f}]" if penalty > 0 else ""
            log_fn(f"   {flag} {col:<35}{penalty_str:<15} {bar} {fval:.2f} (Adj: {adjusted_val:.2f}, Diff: {adjusted_val - shadow_max_fi:+.2f})")
            
            if is_fid_passed:
                init_selected.append(fid)
                best_col_map[fid] = col

    selected = []
    correlation_dropped = []
    
    if len(init_selected) > 0:
        log_fn("\n🔄 [Auto Feature Selection] Spearman Rank Multicollinearity Filter (Threshold: 0.80)...")
        sorted_selected_by_gain = sorted(init_selected, key=lambda x: mean_fi[best_col_map[x]] * (1.0 - feature_penalties.get(x, 0.0)), reverse=True)
        
        for target_feat in sorted_selected_by_gain:
            has_high_corr = False
            for confirmed_feat in selected:
                t_col = best_col_map[target_feat]
                c_col = best_col_map[confirmed_feat]

                # Fix 6: 상관관계 누수 방지 — 첫 번째 fold 구간(가장 엄격한 과거)만 사용
                corr_base_df = train_df.iloc[:fold_end_rows[0]]
                corr_val = abs(corr_base_df[t_col].corr(corr_base_df[c_col], method='spearman'))
                if corr_val >= 0.80:
                    has_high_corr = True
                    correlation_dropped.append((target_feat, confirmed_feat, corr_val))
                    break

            if not has_high_corr:
                selected.append(target_feat)

        for dropped, parent, cval in correlation_dropped:
            log_fn(f"   ❌ [Drop Duplicated] {dropped:<28} ➡️ similar to {parent:<28} (Corr: {cval:.2f})")

    # Excluded features logging
    excluded_list = [fid for fid in feature_to_cols if fid not in init_selected]
    if excluded_list:
        log_fn(f"\n   ❌ [Gain Threshold Excluded ({len(excluded_list)})]: {', '.join(excluded_list)}")

    if len(selected) < 2:
        if len(init_selected) >= 2:
            log_fn("⚠️ [Auto Feature Selection] Under 2 selected after drop. Reverting to initial selection.")
            selected = init_selected
        else:
            # 예전엔 '등록된 지표 전부'로 되돌려 저장했다 — 데이터 없는 지표까지 켜졌다. 이제 저장하지 않는다.
            raise ValueError(f"소음 기준을 넘은 지표가 {len(init_selected)}개뿐입니다 (최소 2). 지표 목록을 바꾸지 않았습니다.")

    log_fn(f"\n   🏆 Selected Features ({len(selected)}): {', '.join(selected)}")

    try:
        from shared.config.config_manager import load_project_config, save_project_config
        full_config = load_project_config("CAiModelBaker")
        fc = full_config.get('FEATURE_CONFIG', {})
        fc['enabled_features'] = selected
        # 어느 구간으로 뽑았나 — 홀드아웃 경계가 바뀌면 화면(AutoLab)이 '재선정 필요'를 띄운다
        fc['selection_period'] = {'start': sel_start, 'end': sel_end, 'lookback': lookback_period,
                                  'holdout_start': _boundary['holdout_start'],
                                  'method': 'auto_select', 'at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}
        full_config['FEATURE_CONFIG'] = fc
        save_project_config("CAiModelBaker", full_config)
        log_fn("💾 enabled_features auto-saved successfully.")
    except Exception as e:
        log_fn(f"⚠️ Error saving configuration: {e}")

    log_fn("✅ [Auto Feature Selection] Completed!\n")
    return selected
