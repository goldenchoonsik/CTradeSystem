import os
import json
import pandas as pd
import lightgbm as lgb
import numpy as np
try:
    from .data_manager import get_snapshot_path, get_params_path, get_model_path
    from .model_engine import run_feature_engineering
    from .feature_registry import load_feature_config
    from .physics_constraint import resolve_friction_rates, resolve_round_trip_cost
except (ImportError, ValueError):
    from core.data_manager import get_snapshot_path, get_params_path, get_model_path
    from core.model_engine import run_feature_engineering
    from core.feature_registry import load_feature_config
    from core.physics_constraint import resolve_friction_rates, resolve_round_trip_cost
from shared.core.market_utils import detect_market_type, get_target_symbol, is_krx_entry_allowed, is_krx_force_exit, parse_interval_minutes
from shared.config import project_store
from shared.core.tbm_executor import resolve_exit
from shared.core.ai_policy import META_APPROVAL_THRESHOLD
from shared.core.cexecution import CExecution, ExecutionConfig

def _permutation_null(CExecution, exec_config, signals, eligible, sim_kwargs, actual, n_perm, seed):
    """무작위 진입 귀무분포 — 순환 이동(circular shift) 검정.

    진입 가능 시점들의 순서 위에서 모델의 신호 패턴을 통째로 무작위 칸만큼 밀어 같은 엔진으로 재시뮬레이션한다.
    신호가 몰리는 구조(연속 신호는 보유 중이라 한 번의 거래가 된다)와 신호 개수가 그대로라 거래 횟수·노출이
    모델과 같은 '타이밍만 무작위' 전략이 된다. (낱개로 흩뿌리면 2026-10-07 069500 15m 에서 신호 49개가
    13건이 아니라 평균 34건 거래가 되어, 노출이 다른 전략과 비교하게 됐다)
    """
    sig = np.asarray(signals, dtype=bool)
    elig_idx = np.flatnonzero(np.asarray(eligible, dtype=bool))
    E = len(elig_idx)
    pattern = sig[elig_idx]
    if E < 2 or not pattern.any():
        return {"n": 0, "signals": int(sig.sum()), "eligible": int(E), "p_value": None}
    rng = np.random.default_rng(seed)
    offsets = np.arange(1, E)
    if n_perm < len(offsets):
        offsets = rng.choice(offsets, size=n_perm, replace=False)
    rets, trades = np.empty(len(offsets)), np.empty(len(offsets))
    for i, off in enumerate(offsets):
        m = np.zeros(len(sig), dtype=bool)
        m[elig_idx] = np.roll(pattern, int(off))
        r = CExecution.simulate_batch(entry_signals=m, exec_config=exec_config, entry_features_dict={}, **sim_kwargs)
        rets[i], trades[i] = float(r["net_return"]), float(r["total_trades"])
    n = len(offsets)
    hist, edges = np.histogram(rets, bins=30)
    return {
        "method": "circular_shift",
        "n": int(n), "signals": int(pattern.sum()), "eligible": int(E), "actual": float(actual),
        "p_value": float((np.sum(rets >= actual) + 1) / (n + 1)),   # 보수적 (관측값 포함)
        "median": float(np.median(rets)), "q05": float(np.quantile(rets, 0.05)), "q95": float(np.quantile(rets, 0.95)),
        "mean_trades": float(trades.mean()),
        "histogram": hist.tolist(), "hist_edges": [float(x) for x in edges],
    }


def process_03_pure_backtest(config, test_start_str, test_end_str, log_fn, session_id=None, feature_subset=None, cached_df=None,
                             model_dir=None, null_permutations=0, null_seed=0):
    """지정 구간 백테스트.

    model_dir: 특정 후보 폴더(best_params_{interval}.json + model_{interval}.txt)를 직접 지정 — 신뢰도 진단용.
    null_permutations: >0 이면 무작위 진입 귀무분포를 함께 만든다(순열 검정). 모델의 진입 신호 개수를 그대로 두고
        진입 가능한 시점들 사이에서 위치만 무작위로 섞어 **같은 실행 엔진**(쿨다운·중복 보유·당일 청산·비용 동일)으로
        시뮬레이션한다. 결과는 반환 dict 의 "null_test".
    """
    from datetime import datetime
    snapshot_path = get_snapshot_path(config)
    interval = config.get('INTERVAL', '1m')
    symbol_code = get_target_symbol(config).split('-')[-1]
    
    log_file_handle = None
    if session_id and session_id != "active":
        # 자동연구 중이면 runs/<시각>/work 의 재학습 모델을, 아니면 프로젝트 model/(챔피언·직접 학습)을 읽는다
        deploy_dir = project_store.artifact_dir(config, session_id)
        os.makedirs(deploy_dir, exist_ok=True)
        log_dir = project_store.run_log_dir(config, session_id)
        os.makedirs(log_dir, exist_ok=True)
        log_file_path = os.path.join(log_dir, "backtest_run.log")
        try:
            log_file_handle = open(log_file_path, "a", encoding="utf-8")
            log_file_handle.write(f"\n--- Backtest Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        except Exception as le:
            print(f"Failed to open log file: {le}")
        params_path = os.path.join(deploy_dir, "model", f"best_params_{interval}.json")
        model_path = os.path.join(deploy_dir, "model", f"model_{interval}.txt")
    else:
        params_path = get_params_path(config)
        model_path = get_model_path(config)
    if model_dir:
        params_path = os.path.join(model_dir, f"best_params_{interval}.json")
        model_path = os.path.join(model_dir, f"model_{interval}.txt")
        
    original_log_fn = log_fn
    def wrapped_log(msg):
        original_log_fn(msg)
        if log_file_handle:
            try:
                log_file_handle.write(str(msg) + "\n")
                log_file_handle.flush()
            except:
                pass
    log_fn = wrapped_log

    try:
        if not os.path.exists(params_path) or not os.path.exists(model_path) or (cached_df is None and not os.path.exists(snapshot_path)):
            raise FileNotFoundError(f"필수 아카이브 파일이 누락되었습니다:\n- {snapshot_path}\n- {params_path}\n- {model_path}")
            
        log_fn("\n======================================================================")
        log_fn("👑 [PROCESS 03] 하드디스크 저장 파일 기반 지정 테스트 구간 백테스트 가동")
        log_fn("======================================================================")
        
        if cached_df is not None:
            df = cached_df.copy()
        else:
            df = pd.read_csv(snapshot_path)
            df = df.sort_values('timestamp').reset_index(drop=True)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = run_feature_engineering(df, interval, config=config)
        
        interval_minutes = parse_interval_minutes(interval)
        
        with open(params_path, 'r') as f:
            bp = json.load(f)
        
        primary_model_path = model_path.replace(f"model_{interval}.txt", f"model_{interval}_primary.txt")
        meta_model_path = model_path.replace(f"model_{interval}.txt", f"model_{interval}_meta.txt")
        
        if os.path.exists(primary_model_path) and os.path.exists(meta_model_path):
            primary_model = lgb.Booster(model_file=primary_model_path)
            meta_model = lgb.Booster(model_file=meta_model_path)
            has_double_model = True
        else:
            primary_model = lgb.Booster(model_file=model_path)
            meta_model = None
            has_double_model = False
            
        try:
            if test_start_str:
                ts_start = pd.to_datetime(test_start_str)
                mask_s = df['timestamp'] >= ts_start
            else:
                mask_s = df.index >= int(len(df) * 0.75)
                
            if test_end_str:
                ts_end = pd.to_datetime(test_end_str) + pd.Timedelta(days=1)
                mask_e = df['timestamp'] < ts_end
            else:
                mask_e = pd.Series([True] * len(df))
                
            test_df = df[mask_s & mask_e].copy().reset_index(drop=True)
            if len(test_df) < 100:
                raise ValueError("테스트 구간 데이터가 너무 적습니다. 날짜를 확인하세요.")
        except Exception as date_e:
            raise ValueError(f"날짜 형식 오류 또는 데이터 부족: {date_e}")
            
        raw_start_time = test_df['timestamp'].iloc[0]
        raw_end_time = test_df['timestamp'].iloc[-1]
        
        # 📌 영점 조절 로그 출력 시 정확한 실제 분 단위 환산값 표기
        actual_predict_mins = round(bp['predict_ahead_candles'] * interval_minutes, 1) if interval_minutes < 1.0 else int(round(bp['predict_ahead_candles'] * interval_minutes))
        log_fn(f"🧬 [SUCCESS] 무기 복원 성공 (예측 한계 시간: {actual_predict_mins}분 / {bp['predict_ahead_candles']}캔들, 진입 문턱: {bp['buy_threshold']:.3f})")
        log_fn(f"📅 [📍 실제 시계열 테스트 타임라인]")
        log_fn(f"   - 검증 시작일시: {raw_start_time}")
        log_fn(f"   - 검증 종료일시: {raw_end_time}")
        log_fn(f"📊 총 {len(test_df):,}행의 연속 시뮬레이션 데이터 주입 중...\n")
        
        # 📌 피처 목록 결정 (우선순위: 명시적 feature_subset -> 모델 내장 피처 -> 세션 feature_spec -> 글로벌 설정)
        features = []
        if feature_subset is not None and len(feature_subset) > 0:
            features = list(feature_subset)
            log_fn(f"📋 명시적 피처 규격 적용: {len(features)}개 지표")
        elif primary_model:
            try:
                model_fnames = primary_model.feature_name()
                if model_fnames and len(model_fnames) > 0:
                    features = model_fnames
                    log_fn(f"📋 로드된 모델에서 피처 규격 추출 완료: {len(features)}개 지표")
            except Exception as fe:
                log_fn(f"⚠️ 모델 피처 추출 예외: {fe}")
        
        if not features and session_id and session_id != "active":
            feature_spec_path = os.path.join(deploy_dir, "model", "feature_spec.json")
            if os.path.exists(feature_spec_path):
                try:
                    with open(feature_spec_path, 'r', encoding='utf-8') as fs:
                        feature_spec = json.load(fs)
                    spec_features = feature_spec.get('features_order', [])
                    if spec_features:
                        features = spec_features
                        log_fn(f"📋 세션 피처 규격서 로드 완료: {len(features)}개 지표 ({session_id})")
                except Exception as fse:
                    log_fn(f"⚠️ feature_spec.json 로드 실패: {fse}")
        
        if not features:
            feature_config = load_feature_config()
            features = feature_config.get('enabled_features', ['binance_ret_5m', 'kimchi_premium', 'upbit_ma_ratio', 'upbit_trade_bid_ratio', 'binance_spot_taker_ratio', 'upbit_rsi_daily', 'upbit_hour', 'upbit_is_weekend'])
        # 📌 학습-추론 피처 동기화 검증 (Train-Serve Skew 방지)
        # feature_spec/모델에서 추출한 피처가 실제 DataFrame에 없으면 자동 재엔지니어링 수행
        missing_features = [f for f in features if f not in test_df.columns]
        if missing_features:
            log_fn(f"ℹ️ 세션 전용 피처 누락 감지 ({len(missing_features)}개) — 피처 재엔지니어링 가동: {missing_features}")
            try:
                temp_fc = load_feature_config()
                temp_fc['enabled_features'] = list(set(temp_fc.get('enabled_features', []) + features))
                df = run_feature_engineering(df, interval, config=config, feature_config=temp_fc)
                test_df = df[mask_s & mask_e].copy().reset_index(drop=True)
                missing_features = [f for f in features if f not in test_df.columns]
            except Exception as re_err:
                log_fn(f"⚠️ 피처 재엔지니어링 예외: {re_err}")
                
        if missing_features:
            raise ValueError(
                f"❗ 피처 누락 감지 (Train-Serve Skew): {missing_features}\n"
                f"   모델/feature_spec이 필요로 하는 피처가 feature_engineering 결과에 없습니다.\n"
                f"   feature_config의 enabled_features와 모델 학습 시 피처 목록이 일치하는지 확인하세요.\n"
                f"   현재 feature_config enabled_features: {load_feature_config().get('enabled_features', [])}"
            )

        try:
            if has_double_model:
                preds_primary = primary_model.predict(test_df[features])
                preds_meta = meta_model.predict(test_df[features])
                preds = preds_primary[:, 1] # 1차 모델의 상승(클래스 1) 예측 확률
            else:
                _raw = primary_model.predict(test_df[features])
                preds = _raw[:, 1] if getattr(_raw, "ndim", 1) > 1 else _raw  # multiclass 대응(H-2)
                preds_meta = np.ones(len(test_df)) # fallback
        except ValueError as ve:
            if "categorical_feature do not match" in str(ve):
                df_temp = test_df[features].copy()
                for col in df_temp.columns:
                    if df_temp[col].dtype.name == 'category':
                        df_temp[col] = df_temp[col].astype(int)
                if has_double_model:
                    preds_primary = primary_model.predict(df_temp)
                    preds_meta = meta_model.predict(df_temp)
                    preds = preds_primary[:, 1]
                else:
                    _raw = primary_model.predict(df_temp)
                    preds = _raw[:, 1] if getattr(_raw, "ndim", 1) > 1 else _raw  # multiclass 대응(H-2)
                    preds_meta = np.ones(len(test_df))
            else:
                raise ve
        
        close_col = 'target_close' if 'target_close' in test_df.columns else 'upbit_close'
        atr_col = 'target_atr' if 'target_atr' in test_df.columns else ('upbit_atr' if 'upbit_atr' in test_df.columns else None)
        
        bh_return = (test_df[close_col].iloc[-1] - test_df[close_col].iloc[0]) / test_df[close_col].iloc[0] * 100
        # 📌 비용 계산 단일 진실 공급원(FEE_RATE / FEE_UPBIT / OPTUNA_CONFIG) 동기화
        fee_rate, slip_rate = resolve_friction_rates(config)  # [FIX-14] 모듈 공통 헬퍼
        cost = resolve_round_trip_cost(config)  # 수수료·슬리피지 왕복 + 매도세
        cooldown_minutes = bp.get('cooldown_minutes', config.get('COOLDOWN_MINUTES', 3))
        cooldown_candles = max(1, int(cooldown_minutes / interval_minutes))
        
        # 📌 KRX 장중 청산 모드 (model_engine과 동기화)
        _market_type = detect_market_type(config)
        _intraday_mode = (_market_type == 'KRX' and config.get('INTRADAY_SQUARE_OFF', True))
        
        # 📌 [Fast-Index] 백테스트 시뮬레이션용 NumPy 1D C-배열 및 마스크 사전 추출
        test_prices = test_df[close_col].to_numpy()
        test_timestamps = test_df['timestamp'].to_numpy()
        test_atrs = test_df[atr_col].to_numpy() if (atr_col and atr_col in test_df.columns) else (test_prices * 0.015)
        test_highs = test_df['high'].to_numpy() if 'high' in test_df.columns else test_prices
        test_lows = test_df['low'].to_numpy() if 'low' in test_df.columns else test_prices
        test_n = len(test_prices)
        
        if _intraday_mode:
            test_krx_force_exit_mask = np.array([is_krx_force_exit(t) for t in test_df['timestamp']])
            test_krx_entry_allowed_mask = np.array([is_krx_entry_allowed(t) for t in test_df['timestamp']])
        else:
            test_krx_force_exit_mask = np.zeros(test_n, dtype=bool)
            test_krx_entry_allowed_mask = np.ones(test_n, dtype=bool)

        use_macro_filter = config.get('USE_MACRO_FILTER', True)
        macro_rsi_threshold = config.get('MACRO_RSI_FILTER_THRESHOLD', 50.0)
        rsi_col = 'target_rsi_daily' if 'target_rsi_daily' in test_df.columns else ('upbit_rsi_daily' if 'upbit_rsi_daily' in test_df.columns else None)
        if use_macro_filter and rsi_col and rsi_col in test_df.columns:
            macro_rsi_arr = test_df[rsi_col].to_numpy()
            macro_allowed_mask = (macro_rsi_arr >= macro_rsi_threshold)
        else:
            macro_allowed_mask = np.ones(test_n, dtype=bool)

        feat_arrays = {feat: test_df[feat].to_numpy() for feat in (features or []) if feat in test_df.columns}

        # 2차 메타 필터 마스크 벡터화
        if has_double_model:
            primary_classes = np.argmax(preds_primary, axis=1) if preds_primary.ndim > 1 else np.ones(test_n, dtype=int)
            meta_passed = (preds_meta >= META_APPROVAL_THRESHOLD)
            is_meta_allowed_mask = np.where(primary_classes == 1, meta_passed, True)
        else:
            is_meta_allowed_mask = np.ones(test_n, dtype=bool)

        entry_signals_mask = (preds >= bp['buy_threshold']) & macro_allowed_mask & is_meta_allowed_mask

        # 📌 [CExecution Engine] Numba C-레벨 초고속 배치 시뮬레이션 가동
        exec_config = CExecution.create_config_from_params(bp, config)
        exec_config.fee_rate = fee_rate
        exec_config.slip_rate = slip_rate
        exec_config.cooldown_candles = cooldown_candles
        exec_config.intraday_square_off = _intraday_mode
        exec_config.seed_money = float(config.get('SEED_MONEY', config.get('SIMULATION_INITIAL_BALANCE', config.get('INVEST_AMOUNT', 10_000_000.0))))

        sim_res = CExecution.simulate_batch(
            prices=test_prices,
            highs=test_highs,
            lows=test_lows,
            atrs=test_atrs,
            entry_signals=entry_signals_mask,
            krx_force_exit_mask=test_krx_force_exit_mask,
            krx_entry_allowed_mask=test_krx_entry_allowed_mask,
            exec_config=exec_config,
            timestamps=test_timestamps,
            preds=preds,
            entry_features_dict=feat_arrays
        )

        null_test = None
        if null_permutations and int(null_permutations) > 0:
            null_test = _permutation_null(
                CExecution, exec_config, entry_signals_mask,
                test_krx_entry_allowed_mask & macro_allowed_mask,
                dict(prices=test_prices, highs=test_highs, lows=test_lows, atrs=test_atrs,
                     krx_force_exit_mask=test_krx_force_exit_mask, krx_entry_allowed_mask=test_krx_entry_allowed_mask,
                     timestamps=test_timestamps, preds=preds),
                float(sim_res["net_return"]), int(null_permutations), int(null_seed))

        total_trades = sim_res["total_trades"]
        win_trades = sim_res["win_trades"]
        win_rate = sim_res["win_rate"]
        net_profit = sim_res["net_return"]
        mdd_v = sim_res["mdd"] / 100.0
        trade_logs = sim_res["trade_logs"]
        
        # 📌 손익비 (Profit Factor) 및 샤프 지수 (Sharpe Ratio), 평균 보유시간 계산
        returns = [t['net_return'] for t in trade_logs]
        gains = [r for r in returns if r > 0]
        losses = [abs(r) for r in returns if r < 0]
        sum_gains = sum(gains) if gains else 0.0
        sum_losses = sum(losses) if losses else 0.0
        # [D-14] 손실 0건이면 PF 는 수학적으로 정의되지 않는다(0 나눗셈). 99.0 은 "무손실" 표식일 뿐
        #        성과 지표가 아니므로 표본 부족 사실을 명시적으로 전달한다.
        #        실측 피해: 거래 1건 후보가 win_rate=100.0 / PF=99.0 으로 리더보드에 "완벽"으로 표시됨.
        pf_lossless = bool(sum_losses <= 0.0 and sum_gains > 0.0)
        profit_factor = (sum_gains / sum_losses) if sum_losses > 0 else (99.0 if sum_gains > 0 else 0.0)
        
        avg_return = np.mean(returns) if returns else 0.0
        std_return = np.std(returns) if len(returns) > 1 else 1e-6
        # [FIX-16] 'sharpe_ratio' 는 시간 연율화 샤프가 아니라 거래 단위 지표(mean/std * sqrt(n_trades), t-통계량 유사)이다.
        #   하위 호환(research_loop 게이트 sharpe_ratio>=1.0)을 위해 값/키는 유지하고, 명시적 필드를 추가로 제공한다.
        sharpe_per_trade = float(avg_return / (std_return + 1e-6)) if len(returns) > 1 else 0.0
        sharpe_ratio = float(sharpe_per_trade * np.sqrt(len(returns))) if len(returns) > 1 else 0.0
        _span_days = 0.0
        try:
            _span_days = float((test_timestamps[-1] - test_timestamps[0]) / np.timedelta64(1, 'D'))
        except Exception:
            _span_days = 0.0
        if len(returns) > 1 and _span_days >= 1.0:
            _trades_per_year = len(returns) / (_span_days / 365.25)
            sharpe_annualized = float(sharpe_per_trade * np.sqrt(_trades_per_year))
        else:
            sharpe_annualized = 0.0
        avg_hold_mins = float(np.mean([t['hold_mins'] for t in trade_logs])) if trade_logs else 0.0
        best_trade = float(max(returns)) if returns else 0.0
        worst_trade = float(min(returns)) if returns else 0.0

        log_fn("===========================================================================")
        log_fn(f" 👑  {config['SYMBOL_UPBIT']} QUANT ENGINE 지정 구간 시뮬레이션 리포트  👑 ")
        log_fn("===========================================================================")
        log_fn(f"  📅 실제 테스트 기간 : {raw_start_time} ~ {raw_end_time}")
        log_fn(f"  📈 해당 기간 내 자산 실제 변동률 (Buy & Hold) : {bh_return:.2f} %")
        log_fn(f"  🛡️ 세력 수급 필터 적용 후 총 매수 진입 횟수       : {total_trades} 회")
        log_fn(f"  🎯 AI 예측 신호 진입 적중률 (전체 승률)            : {win_rate:.2f} %")
        # [D-14] 표본 부족 사실을 리포트에 명시 (PF 99.0 / 샤프 0.0 은 성과가 아니라 계산불가 표식)
        _sample_notes = []
        if pf_lossless:
            _sample_notes.append("손실 0건 → PF 정의불가(99.0 표식)")
        if len(returns) <= 1:
            _sample_notes.append(f"표본 {len(returns)}건 → 샤프 계산불가")
        _sample_note = ("  ⚠️ [D-14] " + " / ".join(_sample_notes)) if _sample_notes else ""
        log_fn(f"  ⚖️ 손익비 (Profit Factor) / 샤프지수               : {profit_factor:.2f} / {sharpe_ratio:.2f}{_sample_note}")
        log_fn(f"  📉 시뮬레이션 최대 낙폭 (MDD)                      : {mdd_v * 100:.2f} %")
        log_fn("---------------------------------------------------------------------------")
        
        if total_trades > 0:
            log_fn("  [상세 매매 기록]")
            log_fn("  No | 진입시각         | 청산시각         | 진입가       | 청산가       | 보유(분) | 보유(시간) | 예측값 | 구분     | 순수익률")
            log_fn("  ---------------------------------------------------------------------------------------------------------------------------------------------------------")
            for t_idx, t in enumerate(trade_logs):
                entry_p_str = f"{t['entry_price']:,.0f}" if t['entry_price'] >= 1000 else f"{t['entry_price']:.4f}"
                exit_p_str = f"{t['exit_price']:,.0f}" if t['exit_price'] >= 1000 else f"{t['exit_price']:.4f}"
                hold_m_str = f"{t['hold_mins']:.1f}" if isinstance(t['hold_mins'], float) else f"{t['hold_mins']}"
                log_fn(f"  {t_idx+1:<2} | {pd.Timestamp(t['entry_time']).strftime('%Y-%m-%d %H:%M')} | {pd.Timestamp(t['exit_time']).strftime('%Y-%m-%d %H:%M')} | {entry_p_str:>12} | {exit_p_str:>12} | {hold_m_str:>8} | {t['hold_mins']/60:>10.1f} | {t['prob']*100:>5.1f}% | {t['exit_type']:<8} | {t['net_return']:+.2f}%")
            log_fn("  ---------------------------------------------------------------------------------------------------------------------------------------------------------")
            
        if total_trades == 0:
            log_fn(f"  💰 AI 엔진 최종 운용 결과 : 위험 장세 신호 미포착으로 자산 방어 (0.00%)")
        else:
            log_fn(f"  💰 AI 엔진 최종 운용 결과 : 누적 순수익률 {net_profit:+.2f}% 달성")
        log_fn("===========================================================================\n")
        
        serializable_trades = []
        for t in trade_logs:
            entry_t = t['entry_time'].strftime('%Y-%m-%d %H:%M') if hasattr(t['entry_time'], 'strftime') else str(t['entry_time'])
            exit_t = t['exit_time'].strftime('%Y-%m-%d %H:%M') if hasattr(t['exit_time'], 'strftime') else str(t['exit_time'])
            serializable_trades.append({
                "entry_time": entry_t,
                "exit_time": exit_t,
                "entry_price": float(t['entry_price']),
                "exit_price": float(t['exit_price']),
                "hold_mins": round(float(t.get('hold_mins', 0)), 1) if (isinstance(t.get('hold_mins', 0), float) or float(t.get('hold_mins', 0)) % 1 != 0) else int(t.get('hold_mins', 0)),
                "prob": float(t.get('prob', 0.0)),
                "exit_type": str(t.get('exit_type', 'NORMAL')),
                "net_return": float(t.get('net_return', 0.0)),
                "mfe": float(t.get('mfe', 0.0)),
                "mae": float(t.get('mae', 0.0)),
                "entry_features": t.get('entry_features', {})
            })

        return {
            "net_profit": float(net_profit),
            "bh_return": float(bh_return),
            "total_trades": int(total_trades),
            "win_rate": float(win_rate),
            "profit_factor": float(profit_factor),
            "pf_lossless": pf_lossless,
            "sample_count": int(len(returns)),
            "sharpe_ratio": float(sharpe_ratio),
            "sharpe_basis": "per_trade_sqrt_n",
            "sharpe_per_trade": float(sharpe_per_trade),
            "sharpe_annualized": float(sharpe_annualized),
            "avg_hold_mins": float(avg_hold_mins),
            "best_trade": float(best_trade),
            "worst_trade": float(worst_trade),
            "mdd": float(mdd_v * 100),
            "start_time": str(raw_start_time),
            "end_time": str(raw_end_time),
            "trade_logs": serializable_trades,
            "null_test": null_test,
        }
    finally:
        if log_file_handle:
            try:
                log_file_handle.close()
            except:
                pass