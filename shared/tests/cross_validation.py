"""
CTradeSystem - 오프라인 교차 검증 (Cross-Validation)

Baker의 backtester.py와 CTrade의 AIStrategyModule에 동일한 과거 데이터를 주입하여
진입/청산 시점, 예측 확률, 순수익률이 일치하는지 자동 검증합니다.

사용법:
    python shared/tests/cross_validation.py
"""
import os
import sys
import json
import pandas as pd
import numpy as np
import lightgbm as lgb
from datetime import datetime, timezone, timedelta

# 경로 설정
CTRADE_SYSTEM_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CTRADE_SYSTEM_ROOT)
sys.path.insert(0, os.path.join(CTRADE_SYSTEM_ROOT, 'CAiModelBaker'))

from shared.core.tbm_executor import resolve_exit  # noqa: E402

KST = timezone(timedelta(hours=9))

# ── 설정 ──
# 현재 실전 배포 세션 (Vultr 서버와 동일) 기준 교차검증
DEPLOY_PATH = os.path.join(CTRADE_SYSTEM_ROOT, 'shared', 'deploy', 'BTC_1m', 'BTC_M1_BEST1')
SNAPSHOT_CSV = os.path.join(CTRADE_SYSTEM_ROOT, 'CAiModelBaker', 'data', 'BTC', 'data_snapshot_1m.csv')
INTERVAL = '1m'
INTERVAL_MINUTES = 1.0
FEE = 0.00005
SLIPPAGE = 0.0006
COST = (FEE + SLIPPAGE) * 2

# config_snapshot.json에서 실전 학습/테스트 구간을 자동 로드 (없으면 기본값)
_snapshot_cfg_path = os.path.join(DEPLOY_PATH, 'config_snapshot.json')
_snapshot_cfg = {}
if os.path.exists(_snapshot_cfg_path):
    with open(_snapshot_cfg_path, 'r', encoding='utf-8') as f:
        _snapshot_cfg = json.load(f)
TEST_START = str(_snapshot_cfg.get('TEST_START', '2026-05-01'))
TEST_END = str(_snapshot_cfg.get('TEST_END', '2026-07-25'))


def run_baker_backtest(df_full, params, model, features, cost) -> list:
    """Baker backtester.py L175-L257의 로직을 그대로 재현"""
    from shared.features.feature_engine import run_feature_engineering
    from shared.features.feature_registry import load_feature_config

    # 피처 설정 로드
    feature_spec_path = os.path.join(DEPLOY_PATH, 'model', 'feature_spec.json')
    with open(feature_spec_path, 'r') as f:
        feature_spec = json.load(f)

    feature_config = {
        'enabled_features': feature_spec['features_order'],
        'feature_params': feature_spec.get('feature_params', {})
    }

    # config_snapshot 로드
    config_snapshot_path = os.path.join(DEPLOY_PATH, 'config_snapshot.json')
    deploy_config = {}
    if os.path.exists(config_snapshot_path):
        with open(config_snapshot_path, 'r', encoding='utf-8') as f:
            deploy_config = json.load(f)

    df = run_feature_engineering(df_full.copy(), interval=INTERVAL, feature_config=feature_config, config=deploy_config)

    # 테스트 구간 필터
    ts_start = pd.to_datetime(TEST_START)
    ts_end = pd.to_datetime(TEST_END) + pd.Timedelta(days=1)
    test_df = df[(df['timestamp'] >= ts_start) & (df['timestamp'] < ts_end)].copy().reset_index(drop=True)

    if len(test_df) < 100:
        print(f"❌ 테스트 데이터 부족: {len(test_df)}행")
        return []

    # LightGBM 추론
    preds = model.predict(test_df[features])

    # Baker 백테스트 루프 (backtester.py와 동일)
    interval_minutes = INTERVAL_MINUTES
    cooldown_candles = max(1, int(params.get('cooldown_minutes', 3) / interval_minutes))
    holding = False
    entry_price = 0
    entry_time_idx = 0
    cooldown_until = 0
    trade_logs = []

    for idx in range(len(test_df)):
        current_price = test_df['upbit_close'].iloc[idx]
        current_low = test_df['upbit_low'].iloc[idx] if 'upbit_low' in test_df.columns else current_price
        current_time = test_df['timestamp'].iloc[idx]

        if holding:
            elapsed = idx - entry_time_idx
            entry_atr = test_df['upbit_atr'].iloc[entry_time_idx]
            # [단일 청산 판정] shared.tbm_executor.resolve_exit
            _exit = resolve_exit(
                entry_price=entry_price, entry_atr=entry_atr,
                current_price=current_price, current_low=current_low,
                take_profit=params['take_profit'], stop_loss=params['stop_loss'],
                emergency_sl_mult=params.get('emergency_sl_mult', params['stop_loss'] * 2.0),
                max_hold_candles=params.get('max_hold_candles', params['predict_ahead_candles']),
                elapsed_candles=elapsed, cost=cost,
                force_exit=False, is_candle_close=True,
                interval_minutes=INTERVAL_MINUTES,
            )
            if _exit:
                trade_logs.append({
                    'entry_idx': entry_time_idx,
                    'exit_idx': idx,
                    'entry_time': str(test_df['timestamp'].iloc[entry_time_idx]),
                    'exit_time': str(current_time),
                    'entry_price': entry_price,
                    'exit_price': _exit['exit_price'],
                    'prob': preds[entry_time_idx],
                    'exit_type': _exit['exit_type'],
                    'net_return': _exit['net_return'],
                    'source': 'BAKER'
                })
                holding = False
                cooldown_until = idx + cooldown_candles
        else:
            # L-7: 매크로 필터(일봉 RSI) 반영 — 실전/백테스터와 정합
            _macro_ok = True
            if deploy_config.get('USE_MACRO_FILTER', True):
                _rsi_col = 'target_rsi_daily' if 'target_rsi_daily' in test_df.columns else ('upbit_rsi_daily' if 'upbit_rsi_daily' in test_df.columns else None)
                if _rsi_col:
                    try:
                        if float(test_df[_rsi_col].iloc[idx]) < float(deploy_config.get('MACRO_RSI_FILTER_THRESHOLD', 50.0)):
                            _macro_ok = False
                    except Exception:
                        pass
            if idx >= cooldown_until and preds[idx] >= params['buy_threshold'] and _macro_ok:
                holding = True
                entry_price = current_price
                entry_time_idx = idx

    return trade_logs


def run_ctrade_simulation(df_full, deploy_path, cost) -> list:
    """CTrade AIStrategyModule + CandleAggregator를 사용한 시뮬레이션"""
    sys.path.insert(0, os.path.join(CTRADE_SYSTEM_ROOT, 'CTrade'))

    from core.candle_aggregator import CandleAggregator
    from shared.features.feature_engine import run_feature_engineering
    from shared.core.tbm_executor import TBMPositionManager

    # deploy 패키지 로드 (interval 자동 감지)
    model_dir = os.path.join(deploy_path, 'model')
    params_files = [f for f in os.listdir(model_dir) if f.startswith("best_params_") and f.endswith(".json")]
    if not params_files:
        raise FileNotFoundError(f"best_params_*.json not found in {model_dir}")
    params_path = os.path.join(model_dir, params_files[0])
    with open(params_path, 'r') as f:
        params = json.load(f)
    interval_str = params_files[0].replace("best_params_", "").replace(".json", "")
    with open(os.path.join(model_dir, 'feature_spec.json'), 'r') as f:
        feature_spec = json.load(f)
    model_files = [f for f in os.listdir(model_dir) if f.startswith("model_") and f.endswith(".txt")]
    if not model_files:
        raise FileNotFoundError(f"model_*.txt not found in {model_dir}")
    model = lgb.Booster(model_file=os.path.join(model_dir, model_files[0]))

    features = feature_spec['features_order']
    feature_config = {
        'enabled_features': features,
        'feature_params': feature_spec.get('feature_params', {})
    }

    # 테스트 구간 필터
    ts_start = pd.to_datetime(TEST_START)
    ts_end = pd.to_datetime(TEST_END) + pd.Timedelta(days=1)
    test_df = df_full[(df_full['timestamp'] >= ts_start) & (df_full['timestamp'] < ts_end)].copy().reset_index(drop=True)

    # TBM 포지션 관리자
    pm = TBMPositionManager(params, cost=cost, interval_minutes=INTERVAL_MINUTES)

    # config_snapshot 로드
    config_snapshot_path = os.path.join(DEPLOY_PATH, 'config_snapshot.json')
    deploy_config = {}
    if os.path.exists(config_snapshot_path):
        with open(config_snapshot_path, 'r', encoding='utf-8') as f:
            deploy_config = json.load(f)

    # 전체 구간에 피처 계산
    df = run_feature_engineering(df_full.copy(), interval=INTERVAL, feature_config=feature_config, config=deploy_config)
    df_test = df[(df['timestamp'] >= ts_start) & (df['timestamp'] < ts_end)].copy().reset_index(drop=True)

    preds = model.predict(df_test[features])

    interval_minutes = INTERVAL_MINUTES
    cooldown_candles = pm.cooldown_candles
    holding = False
    entry_price = 0
    entry_atr = 0
    entry_idx = 0
    cooldown_until = 0
    trade_logs = []

    for idx in range(len(df_test)):
        current_price = df_test['upbit_close'].iloc[idx]
        current_low = df_test['upbit_low'].iloc[idx] if 'upbit_low' in df_test.columns else current_price
        current_time = df_test['timestamp'].iloc[idx]

        if holding:
            elapsed = idx - entry_idx
            # 1. 틱 단위 비상손절(Hard SL) 먼저 검사
            exit_result = pm.check_exit(
                entry_price=entry_price,
                entry_atr=entry_atr,
                current_price=current_low,
                elapsed_candles=elapsed,
                is_candle_close=False
            )
            # 2. 비상손절 미발생 시 캔들 종가 기준 Soft TP/SL/시간만료 검사
            if not exit_result:
                exit_result = pm.check_exit(
                    entry_price=entry_price,
                    entry_atr=entry_atr,
                    current_price=current_price,
                    elapsed_candles=elapsed,
                    is_candle_close=True
                )

            if exit_result:
                trade_logs.append({
                    'entry_idx': entry_idx,
                    'exit_idx': idx,
                    'entry_time': str(df_test['timestamp'].iloc[entry_idx]),
                    'exit_time': str(current_time),
                    'entry_price': entry_price,
                    'exit_price': exit_result['exit_price'],
                    'prob': preds[entry_idx],
                    'exit_type': exit_result['exit_type'],
                    'net_return': exit_result['net_return'],
                    'source': 'CTRADE'
                })
                holding = False
                cooldown_until = idx + cooldown_candles
        else:
            if idx >= cooldown_until and preds[idx] >= params['buy_threshold']:
                holding = True
                entry_price = current_price
                entry_atr = df_test['upbit_atr'].iloc[idx]
                entry_idx = idx


    return trade_logs


def compare_logs(baker_logs, ctrade_logs):
    """두 매매 로그를 비교하여 정합성 검증"""
    print("\n" + "=" * 80)
    print("  📊 교차 검증 결과 (Baker Backtest vs CTrade AI Module)")
    print("=" * 80)

    print(f"\n  총 매매 횟수: Baker={len(baker_logs)} / CTrade={len(ctrade_logs)}")

    if len(baker_logs) != len(ctrade_logs):
        print(f"  ❌ 매매 횟수 불일치! (차이: {abs(len(baker_logs) - len(ctrade_logs))}건)")
    else:
        print(f"  ✅ 매매 횟수 일치")

    # 상세 비교
    min_len = min(len(baker_logs), len(ctrade_logs))
    mismatches = 0

    print(f"\n  {'No':<4} | {'구분':<8} | {'Baker 진입idx':<13} | {'CTrade 진입idx':<14} | {'Baker 수익률':<12} | {'CTrade 수익률':<13} | {'일치'}")
    print(f"  {'-'*95}")

    for i in range(min_len):
        b = baker_logs[i]
        c = ctrade_logs[i]

        idx_match = b['entry_idx'] == c['entry_idx'] and b['exit_idx'] == c['exit_idx']
        type_match = b['exit_type'] == c['exit_type']
        ret_match = abs(b['net_return'] - c['net_return']) < 0.01  # 0.01% 이내

        is_ok = idx_match and type_match and ret_match
        status = "✅" if is_ok else "❌"
        if not is_ok:
            mismatches += 1

        print(f"  {i+1:<4} | {b['exit_type']:<8} | {b['entry_idx']:<13} | {c['entry_idx']:<14} | {b['net_return']:>+10.4f}% | {c['net_return']:>+11.4f}% | {status}")

    print(f"  {'-'*95}")

    if mismatches == 0 and len(baker_logs) == len(ctrade_logs):
        print(f"\n  🎉 교차 검증 통과! {min_len}건 매매 기록 100% 일치")
    else:
        print(f"\n  ⚠️ 불일치 {mismatches}건 / 총 {min_len}건")

    # 요약 통계
    if baker_logs:
        b_total = sum(t['net_return'] for t in baker_logs)
        c_total = sum(t['net_return'] for t in ctrade_logs) if ctrade_logs else 0
        print(f"\n  누적 수익률: Baker={b_total:+.4f}% / CTrade={c_total:+.4f}%")
        print(f"  차이: {abs(b_total - c_total):.6f}%")

    print("=" * 80)
    return mismatches == 0 and len(baker_logs) == len(ctrade_logs)


def check_data_loss(df, start_date, end_date):
    """지정된 검증 구간 내에서 바이낸스 등 핵심 컬럼들의 결측치(NaN, 0) 데이터 소실 기간을 추적합니다."""
    loss_cols = [
        'binance_spot_close', 'binance_spot_volume', 
        'binance_spot_taker_ratio', 'binance_futures_oi',
        'binance_usdc_volume'
    ]
    
    # 해당 기간 필터링
    ts_start = pd.to_datetime(start_date)
    ts_end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
    df_filtered = df[(df['timestamp'] >= ts_start) & (df['timestamp'] < ts_end)].copy().reset_index(drop=True)
    
    if len(df_filtered) == 0:
        return []

    # 검사 대상 컬럼 중 존재하지 않는 것은 제외
    existing_cols = [col for col in loss_cols if col in df_filtered.columns]

    # 소실 행 조건 판별 (결측치이거나 0인 경우)
    is_missing = df_filtered[existing_cols].isna().any(axis=1) | (df_filtered[existing_cols] == 0).any(axis=1)
    
    if not is_missing.any():
        return []
        
    loss_indices = df_filtered[is_missing].index
    
    # 연속된 인덱스를 그룹화하여 범위 추출
    loss_ranges = []
    start_idx = loss_indices[0]
    prev_idx = loss_indices[0]
    
    for idx in loss_indices[1:]:
        if idx > prev_idx + 1:
            loss_ranges.append((start_idx, prev_idx))
            start_idx = idx
        prev_idx = idx
    loss_ranges.append((start_idx, prev_idx))
    
    loss_periods = []
    for start, end in loss_ranges:
        start_time = df_filtered['timestamp'].iloc[start]
        end_time = df_filtered['timestamp'].iloc[end]
        row_count = end - start + 1
        
        # 블록 내 결측이 일어난 실제 컬럼명 수집
        block_df = df_filtered.iloc[start:end+1]
        missing_cols_in_block = []
        for col in existing_cols:
            if block_df[col].isna().any() or (block_df[col] == 0).any():
                missing_cols_in_block.append(col)
                
        loss_periods.append({
            'start': start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'end': end_time.strftime('%Y-%m-%d %H:%M:%S'),
            'rows': row_count,
            'cols': ", ".join(missing_cols_in_block)
        })
    return loss_periods


if __name__ == '__main__':
    print("=" * 80)
    print("  🔬 CTradeSystem 오프라인 교차 검증 시작")
    print(f"  Deploy: {os.path.basename(DEPLOY_PATH)}")
    print(f"  테스트 구간: {TEST_START} ~ {TEST_END}")
    print("=" * 80)

    # 데이터 로드
    print("\n[1/4] 스냅샷 데이터 로드...")
    df_full = pd.read_csv(SNAPSHOT_CSV)
    df_full = df_full.sort_values('timestamp').reset_index(drop=True)
    df_full['timestamp'] = pd.to_datetime(df_full['timestamp'])
    print(f"  → {len(df_full):,}행 로드 완료")

    # 데이터 소실(결측치) 확인 및 출력
    loss_periods = check_data_loss(df_full, TEST_START, TEST_END)
    if loss_periods:
        # 1. 상세 기록 출력 (10행 이상)
        print("\n  ⚠️ [경고] 검증 구간 내 바이낸스 주요 소실 구간 감지 (10행 이상 연속 누락):")
        print("  " + "-" * 95)
        printed_count = 0
        for period in loss_periods:
            if period['rows'] >= 10:
                printed_count += 1
                print(f"    - {period['start']} ~ {period['end']} ({period['rows']:>5}행 누락) [항목: {period['cols']}]")
        if printed_count == 0:
            print("    * 10행 이상의 연속된 장기 누락 구간은 발견되지 않았습니다. (자잘한 단편 누락만 존재)")
        print("  " + "-" * 95)
        
        # 2. 항목별 요약 집계 리포트 출력
        # df_full에서 해당 구간만 필터링
        ts_start = pd.to_datetime(TEST_START)
        ts_end = pd.to_datetime(TEST_END) + pd.Timedelta(days=1)
        df_filtered = df_full[(df_full['timestamp'] >= ts_start) & (df_full['timestamp'] < ts_end)].copy().reset_index(drop=True)
        total_len = len(df_filtered)
        
        loss_cols = [
            'binance_spot_close', 'binance_spot_volume', 
            'binance_spot_taker_ratio', 'binance_futures_oi',
            'binance_usdc_volume'
        ]
        existing_cols = [col for col in loss_cols if col in df_filtered.columns]
        
        print("\n  📊 [바이낸스 데이터 결측 요약 집계 리포트]")
        print("  " + "=" * 95)
        for col in existing_cols:
            col_missing = df_filtered[col].isna() | (df_filtered[col] == 0)
            missing_count = col_missing.sum()
            ratio = (missing_count / total_len) * 100
            
            # 이 컬럼의 최대 연속 누락 구간 찾기
            max_loss_period_str = "없음"
            if missing_count > 0:
                col_missing_indices = df_filtered[col_missing].index
                c_start = col_missing_indices[0]
                c_prev = col_missing_indices[0]
                max_len = 0
                max_range = (c_start, c_prev)
                for idx in col_missing_indices[1:]:
                    if idx > c_prev + 1:
                        c_len = c_prev - c_start + 1
                        if c_len > max_len:
                            max_len = c_len
                            max_range = (c_start, c_prev)
                        c_start = idx
                    c_prev = idx
                c_len = c_prev - c_start + 1
                if c_len > max_len:
                    max_len = c_len
                    max_range = (c_start, c_prev)
                
                m_s = pd.to_datetime(df_filtered['timestamp'].iloc[max_range[0]]).strftime('%Y-%m-%d %H:%M:%S')
                m_e = pd.to_datetime(df_filtered['timestamp'].iloc[max_range[1]]).strftime('%Y-%m-%d %H:%M:%S')
                max_loss_period_str = f"{m_s} ~ {m_e} ({max_len}행)"
                
            print(f"    * {col:<26}: 누락 {missing_count:>7,}행 ({ratio:>6.2f}%) | 최대 결측대역: {max_loss_period_str}")
        print("  " + "=" * 95)
    else:
        print("\n  ✅ 검증 구간 내 바이낸스 데이터 소실 없음 (데이터 무결성 확인)")

    # deploy 패키지 로드 (interval 자동 감지)
    print("\n[2/4] Deploy 패키지 로드...")
    model_dir = os.path.join(DEPLOY_PATH, 'model')
    params_files = [f for f in os.listdir(model_dir) if f.startswith("best_params_") and f.endswith(".json")]
    if not params_files:
        raise FileNotFoundError(f"best_params_*.json not found in {model_dir}")
    with open(os.path.join(model_dir, params_files[0]), 'r') as f:
        params = json.load(f)
    with open(os.path.join(model_dir, 'feature_spec.json'), 'r') as f:
        feature_spec = json.load(f)
    model_files = [f for f in os.listdir(model_dir) if f.startswith("model_") and f.endswith(".txt")]
    if not model_files:
        raise FileNotFoundError(f"model_*.txt not found in {model_dir}")
    model = lgb.Booster(model_file=os.path.join(model_dir, model_files[0]))
    features = feature_spec['features_order']
    print(f"  → session={params.get('opt_session_id')} | features={len(features)} | threshold={params['buy_threshold']:.4f}")

    # Baker 백테스트
    print("\n[3/4] Baker 백테스트 실행...")
    baker_logs = run_baker_backtest(df_full, params, model, features, COST)
    print(f"  → {len(baker_logs)}건 매매 기록 생성")

    # CTrade 시뮬레이션
    print("\n[4/4] CTrade AI 모듈 시뮬레이션 실행...")
    ctrade_logs = run_ctrade_simulation(df_full, DEPLOY_PATH, COST)
    print(f"  → {len(ctrade_logs)}건 매매 기록 생성")

    # 비교
    is_pass = compare_logs(baker_logs, ctrade_logs)
    sys.exit(0 if is_pass else 1)
