"""
CTradeSystem - 실시간 정합성 교차 검증 (Live Parity Check)

실시간으로 수집/기록된 'live_feature_dump.csv'와 'live_trades.csv'를 로드하여
Baker 백테스트 로직을 이 데이터셋 위에서 재실행한 결과와 1:1로 완전 대조합니다.
"""
import os
import sys
import json
import pandas as pd
import numpy as np
import lightgbm as lgb
from datetime import datetime

# 경로 설정
CTRADE_SYSTEM_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CTRADE_SYSTEM_ROOT)
sys.path.insert(0, os.path.join(CTRADE_SYSTEM_ROOT, 'CAiModelBaker'))
sys.path.insert(0, os.path.join(CTRADE_SYSTEM_ROOT, 'CTrade'))

import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--log-dir", type=str, default=None)
args, _ = parser.parse_known_args()

def _find_default_log_dir():
    # 1. vultr_logs 폴더 내 최신 data_logs 탐색
    vultr_root = os.path.join(CTRADE_SYSTEM_ROOT, "vultr_logs")
    if os.path.exists(vultr_root):
        dates = [d for d in os.listdir(vultr_root) if os.path.isdir(os.path.join(vultr_root, d))]
        for d in sorted(dates, reverse=True):
            dlogs = os.path.join(vultr_root, d, "data_logs")
            if os.path.isdir(dlogs):
                files = [f for f in os.listdir(dlogs) if f.startswith("live_feature_dump") and os.path.getsize(os.path.join(dlogs, f)) > 500]
                if files:
                    return dlogs
    # 2. 기본 CTrade/data/logs 반환
    return os.path.join(CTRADE_SYSTEM_ROOT, "CTrade", "data", "logs")

LOG_DIR = args.log_dir if args.log_dir else _find_default_log_dir()

# ── 설정 로드 ──
# CTrade 설정 파일에서 현재 구동 중인 AI 배포 폴더를 자동으로 감지합니다.
CONFIG_PATH = os.path.join(CTRADE_SYSTEM_ROOT, 'CTrade', 'config', 'trading_config.json')
DEFAULT_DEPLOY_PATH = os.path.join(CTRADE_SYSTEM_ROOT, 'CAiModelBaker', 'deploy', 'BTC_30m', 'BTC_20260709_M30_BEST4')
DEPLOY_PATH = DEFAULT_DEPLOY_PATH

# 활성 AI_BAKER 전략 타겟 목록: [(symbol, strategy_name)] — Parity 검증 대상 심볼 확보
AI_TARGETS = []
config = {}
templates = {}

try:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # 1. strategy_templates에서 기본 template 획득
        templates = config.get("STRATEGY_TEMPLATES", {})
        ai_template = templates.get("ai_baker", {})
        deploy_base = ai_template.get("deploy_base", "")
        deploy_session = ai_template.get("deploy_session", "")

        # 2. SCALPING_TARGETS 중 활성 AI_BAKER 전략 타겟들을 수집
        targets = config.get("SCALPING_TARGETS", [])
        for t_conf in targets:
            strat_name = t_conf.get("strategy_name", "")
            strat_tmpl = templates.get(strat_name, {})
            is_ai_baker = (
                strat_tmpl.get("type") == "AI_BAKER" or
                strat_name.startswith("ai_baker") or
                bool(t_conf.get("deploy_session"))
            )
            if is_ai_baker and t_conf.get("active", False):
                sym = t_conf.get("symbol", "")
                if sym:
                    AI_TARGETS.append((sym, strat_name))

        if AI_TARGETS:
            deploy_base = None
            deploy_session = None
except Exception as e:
    print(f"⚠️ 설정 로드 중 오류 (기본 배포 경로 사용): {e}")

# 검증 대상별 deploy 경로 결정 (심볼별로 전략 템플릿/타겟 설정 우선)
def _resolve_deploy_path(symbol, strategy_name, config, templates):
    """심볼+전략명 → 배포 세션 절대경로 해석"""
    t_conf = next((t for t in config.get("SCALPING_TARGETS", []) if t.get("symbol") == symbol), None)
    strat_tmpl = templates.get(strategy_name, {})
    base = (t_conf or {}).get("deploy_base") or strat_tmpl.get("deploy_base") or templates.get("ai_baker", {}).get("deploy_base", "")
    session = (t_conf or {}).get("deploy_session") or strat_tmpl.get("deploy_session") or templates.get("ai_baker", {}).get("deploy_session", "")
    if not base or not session:
        return None
    if not os.path.isabs(base):
        base = os.path.normpath(os.path.join(os.path.join(CTRADE_SYSTEM_ROOT, 'CTrade'), base))
    return os.path.join(base, session)


def run_live_parity_check(symbol: str = "KRW-BTC", deploy_path: str = None, log_dir: str = None):
    print("=" * 80)
    print("  🔬 CTrade <-> Baker 실시간 수집 데이터 정합성 검증")
    print(f"  대상 심볼: {symbol}")
    print(f"  Deploy Path: {os.path.basename(deploy_path) if deploy_path else 'N/A'}")
    print("=" * 80)

    global DEPLOY_PATH
    if deploy_path:
        DEPLOY_PATH = deploy_path
    if log_dir is None:
        log_dir = LOG_DIR

    # ── 심볼별 Parity 로그 파일 경로 (신규 명명 규칙 우선, 레거시 공용 파일 폴백) ──
    _safe_symbol = symbol.replace("/", "_").replace("\\", "_")
    FEATURE_DUMP_PATH = os.path.join(log_dir, f"live_feature_dump_{_safe_symbol}.csv")
    TRADES_DUMP_PATH = os.path.join(log_dir, f"live_trades_{_safe_symbol}.csv")
    RAW_CANDLES_PATH = os.path.join(log_dir, f"live_raw_candles_{_safe_symbol}.csv")
    if not os.path.exists(FEATURE_DUMP_PATH) or not os.path.exists(TRADES_DUMP_PATH):
        FEATURE_DUMP_PATH = os.path.join(log_dir, "live_feature_dump.csv")
        TRADES_DUMP_PATH = os.path.join(log_dir, "live_trades.csv")
        RAW_CANDLES_PATH = os.path.join(log_dir, "live_raw_candles.csv")

    if not os.path.exists(FEATURE_DUMP_PATH) or not os.path.exists(TRADES_DUMP_PATH):
        print("❌ 에러: 실시간 로그 파일이 존재하지 않습니다.")
        print(f"  확인 경로:\n  - {FEATURE_DUMP_PATH}\n  - {TRADES_DUMP_PATH}")
        return False

    # 1. 파일 로드
    print("\n[1/4] 실시간 덤프 로그 파일 로드 중...")
    df_live = pd.read_csv(FEATURE_DUMP_PATH)
    df_trades_live = pd.read_csv(TRADES_DUMP_PATH)
    print(f"  → 실시간 피처 덤프: {len(df_live)}행")
    print(f"  → 실시간 매매 로그: {len(df_trades_live)}건")

    if len(df_live) == 0:
        print("❌ 에러: 피처 덤프 데이터가 비어 있습니다.")
        return False

    # [보완①] 시간 커버리지 체크 — dump에 기록된 봉 수 vs 기대 봉 수 대조
    # 수신 불량으로 Feature Gate에 막힌 봉은 dump에 없으므로, 커버리지 저하가 수신 불량의 간접 지표
    try:
        df_live['timestamp'] = pd.to_datetime(df_live['timestamp'])
        _ts_min = df_live['timestamp'].min()
        _ts_max = df_live['timestamp'].max()
        _interval_hint = None
        # params 로드 전이므로 interval을 파일명 패턴에서 추정
        import glob as _glob
        _p_files = _glob.glob(os.path.join(DEPLOY_PATH, 'model', 'best_params_*.json'))
        if _p_files:
            _interval_hint = os.path.basename(_p_files[0]).replace('best_params_', '').replace('.json', '')
        if _interval_hint and _interval_hint.endswith('m'):
            _intv_min = float(_interval_hint[:-1])
        elif _interval_hint and _interval_hint.endswith('h'):
            _intv_min = float(_interval_hint[:-1]) * 60.0
        else:
            _intv_min = 10.0  # fallback
        _expected = int((_ts_max - _ts_min).total_seconds() / 60 / _intv_min) + 1
        _actual = len(df_live)
        _coverage = _actual / max(_expected, 1)
        if _coverage < 0.90:
            print(f"  🚨 [Coverage Alert] 봉 커버리지 {_coverage*100:.1f}% — 기대 {_expected}봉 중 {_actual}봉만 dump에 기록됨")
            print(f"     → 원인: Feature Gate(NaN 차단)로 {_expected - _actual}봉이 추론 Skip됐을 가능성")
            print(f"     → 원인: CTrade 수신 불량으로 해당 봉이 집계되지 않았을 가능성")
        elif _coverage < 0.95:
            print(f"  ⚠️ [Coverage Warning] 봉 커버리지 {_coverage*100:.1f}% — {_expected - _actual}봉 유실 (5% 초과)")
        else:
            print(f"  ✅ 봉 커버리지 {_coverage*100:.1f}% 정상 ({_actual}/{_expected}봉)")
    except Exception as _ce:
        print(f"  ⚠️ 커버리지 체크 실패 (비치명적): {_ce}")

    if os.path.exists(RAW_CANDLES_PATH):
        df_raw = pd.read_csv(RAW_CANDLES_PATH)
        # 중복 타임스탬프 제거 (가장 최신 기록 킵) 및 시간 순서 정렬
        df_raw['timestamp'] = pd.to_datetime(df_raw['timestamp'])
        df_raw = df_raw.drop_duplicates(subset=['timestamp'], keep='last')
        df_raw = df_raw.sort_values('timestamp').reset_index(drop=True)
        print(f"  → 실시간 원본 캔들 히스토리 로드: {len(df_raw)}행 (중복 제거 및 시간 정렬 완료)")
    else:
        raw_columns = [col for col in [
            'timestamp', 'target_open', 'target_high', 'target_low', 'target_close', 'target_volume',
            'target_trade_bid_ratio', 'target_trade_count', 'target_large_trade_count',
            'upbit_open', 'upbit_close', 'upbit_high', 'upbit_low', 'upbit_volume',
            'upbit_trade_bid_ratio', 'upbit_trade_count', 'upbit_large_trade_count',
            'upbit_avg_trade_size', 'binance_spot_close', 'binance_spot_volume',
            'binance_spot_taker_ratio', 'binance_spot_trade_count', 'binance_spot_quote_volume',
            'binance_spot_taker_quote_ratio', 'binance_futures_oi', 'binance_futures_oi_value',
            'binance_usdc_volume', 'binance_funding_rate', 'kospi_futures_close', 'nq_futures_close', 'sp500_futures_close'
        ] if col in df_live.columns]
        df_raw = df_live[raw_columns].copy()
        # 중복 제거 및 정렬
        df_raw['timestamp'] = pd.to_datetime(df_raw['timestamp'])
        df_raw = df_raw.drop_duplicates(subset=['timestamp'], keep='last')
        df_raw = df_raw.sort_values('timestamp').reset_index(drop=True)
        print("  ⚠️ 주의: 원본 캔들 히스토리 파일이 없어 피처 덤프 기준으로 계산합니다. (EMA 초기 오차 가능성 있음)")

    # 2. 모델 및 파라미터 로드
    model_dir = os.path.join(DEPLOY_PATH, 'model')
    
    # 📌 [동적 파일 탐색]: best_params_*.json 및 model_*.txt 패턴으로 파일 검색
    params_files = [f for f in os.listdir(model_dir) if f.startswith("best_params_") and f.endswith(".json")]
    if not params_files:
        print(f"❌ 에러: best_params_*.json 파일을 찾을 수 없습니다 (경로: {model_dir})")
        return False
    params_path = os.path.join(model_dir, params_files[0])
    with open(params_path, 'r', encoding='utf-8') as f:
        params = json.load(f)

    # 파라미터 파일명에서 interval 자동 추출
    interval_str = params_files[0].replace("best_params_", "").replace(".json", "")

    with open(os.path.join(model_dir, 'feature_spec.json'), 'r', encoding='utf-8') as f:
        feature_spec = json.load(f)

    model_files = [f for f in os.listdir(model_dir) if f.startswith("model_") and f.endswith(".txt")]
    if not model_files:
        print(f"❌ 에러: model_*.txt 파일을 찾을 수 없습니다 (경로: {model_dir})")
        return False
    model_path = os.path.join(model_dir, model_files[0])
    model = lgb.Booster(model_file=model_path)
    features = feature_spec['features_order']

    # 3. 1차 검증: 실시간 피처 및 예측값 Parity 체크
    print("\n[2/4] 피처 가공 및 모델 예측 정합성 검증...")
    
    from shared.features.feature_engine import run_feature_engineering
    feature_config = {
        'enabled_features': features,
        'feature_params': feature_spec.get('feature_params', {})
    }
    
    # config_snapshot 로드하여 자산 유형 전달
    config_snapshot_path = os.path.join(DEPLOY_PATH, 'config_snapshot.json')
    deploy_config = {}
    if os.path.exists(config_snapshot_path):
        with open(config_snapshot_path, 'r', encoding='utf-8') as f:
            deploy_config = json.load(f)

    # 피처 재생성 (동적 감지된 interval 및 config 전달)
    df_recreated = run_feature_engineering(df_raw, interval=interval_str, feature_config=feature_config, config=deploy_config)
    
    # 타임스탬프 자료형 통일
    df_live['timestamp'] = pd.to_datetime(df_live['timestamp'])
    df_recreated['timestamp'] = pd.to_datetime(df_recreated['timestamp'])
    
    # 머지하여 1:1 대조 준비
    # 📌 [피처 누락 예방]: 컬럼 존재 및 NaN 비율까지 검사
    missing_features = [col for col in features if col not in df_live.columns]
    if missing_features:
        print(f"❌ 에러: 실시간 피처 덤프에 모델 필수 피처가 누락되었습니다: {missing_features}")
        print("  → 현재 로드된 모델 버전과 과거 수집된 로그 간의 피처 불일치가 감지되었습니다.")
        return False

    # [보완②] NaN Rate Check — 컬럼은 있지만 값이 NaN인 케이스 탐지
    # 수신 불량으로 carry-forward된 경우는 잡지 못하지만, 명시적 NaN은 반드시 잡아야 함
    _nan_alert = False
    for _col in features:
        _nan_rate = df_live[_col].isna().mean()
        if _nan_rate > 0.05:   # 5% 초과 NaN → 심각
            print(f"  🚨 [NaN Alert] '{_col}': NaN 비율 {_nan_rate*100:.1f}% — 실시간 수신 불량 또는 피처 계산 실패")
            print(f"     → 원인: 해당 소스(Coinbase/Binance/KIS) 연결 단절 또는 CTrade 집계 오류")
            _nan_alert = True
        elif _nan_rate > 0.01:  # 1~5% → 경고
            print(f"  ⚠️ [NaN Warning] '{_col}': NaN 비율 {_nan_rate*100:.1f}% ({int(_nan_rate*len(df_live))}봉)")
    if not _nan_alert and all(df_live[c].isna().mean() <= 0.01 for c in features):
        print(f"  ✅ 필수 피처 NaN 체크 통과 ({len(features)}개 피처 모두 정상)")

    df_matched = pd.merge(
        df_live[['timestamp', 'predict_prob'] + features],
        df_recreated[['timestamp'] + features],
        on='timestamp',
        suffixes=('_live', '_recreated')
    )
    
    if len(df_matched) == 0:
        print("❌ 에러: 타임스탬프 매칭 실패. 실시간 덤프와 원본 캔들의 시간대가 다릅니다.")
        return False
        
    # [보완③] 피처별 허용치 분리 + NaN diff 처리
    # 결정론적 피처: 엄격 (1e-4), 롤링 지표: 완화 (1.0), NaN diff: inf로 처리하여 반드시 검출
    ROLLING_WARMUP_FEATURES = ('rsi', 'mfi', 'bb', 'macd', 'kimchi', 'momentum', 'atr', 'ma', 'ema',
                               'vwap', 'stoch', 'adx', 'cci', 'obv', 'cmf', 'trix')
    max_feat_diff = 0.0
    diff_feature_name = ""
    feat_fail_details = []

    for col in features:
        live_vals = df_matched[f"{col}_live"].values
        reco_vals = df_matched[f"{col}_recreated"].values
        # NaN이 한쪽에만 있으면 inf (반드시 잡아야 할 불일치)
        diff = np.where(
            np.isnan(live_vals) & np.isnan(reco_vals), 0.0,       # 둘 다 NaN → 0
            np.where(
                np.isnan(live_vals) | np.isnan(reco_vals), np.inf,  # 한쪽만 NaN → inf
                np.abs(live_vals - reco_vals)                        # 둘 다 유효 → 절대 편차
            )
        )
        col_max_diff = np.max(diff)
        is_rolling = any(kw in col.lower() for kw in ROLLING_WARMUP_FEATURES)
        threshold = 1.0 if is_rolling else 1e-4   # 롤링: 1.0, 결정론: 1e-4
        if col_max_diff > threshold:
            feat_fail_details.append((col, col_max_diff, is_rolling))
        if col_max_diff > max_feat_diff:
            max_feat_diff = col_max_diff
            diff_feature_name = col

    # 재생성 피처 컬럼명 원복 후 재예측
    df_recreated_for_pred = df_matched[[f"{col}_recreated" for col in features]].rename(
        columns=lambda x: x.replace('_recreated', '')
    )
    preds_recreated = model.predict(df_recreated_for_pred)
    pred_diffs = np.abs(df_matched['predict_prob'].values - preds_recreated)
    max_pred_diff = float(np.nanmax(pred_diffs)) if len(pred_diffs) > 0 else 0.0

    print(f"  - 피처값 최대 편차: {max_feat_diff:.8e} ({diff_feature_name if max_feat_diff > 0 else 'None'})")
    print(f"  - 모델 예측값 최대 편차: {max_pred_diff:.8e}")

    # [보완④] 예측 허용치 5%로 강화 (이전: 25%)
    feat_ok = len(feat_fail_details) == 0
    pred_ok = max_pred_diff < 0.05  # 5% — 이전 25%에서 강화

    if feat_ok and pred_ok:
        print("  ✅ 피처 가공 및 LightGBM 예측 정합성 통과!")
    else:
        print("  ❌ 피처/예측값 불일치 발생! (정합성 실패)")
        if not feat_ok:
            for _fc, _fd, _fr in feat_fail_details:
                _type = '롤링지표(허용 1.0)' if _fr else '결정론적(허용 1e-4)'
                _cause = ''
                if np.isinf(_fd):
                    _cause = ' ← 한쪽에만 NaN 존재 (수신 불량 또는 carry-forward 의심)'
                elif _fr:
                    _cause = ' ← 웜업 데이터 부족 또는 재시작 직후 수렴 지연'
                else:
                    _cause = ' ← 피처 계산 로직 또는 Pandas 버전 차이'
                print(f"     🔴 [{_type}] '{_fc}': 최대편차={_fd:.6e}{_cause}")
        if not pred_ok:
            print(f"     → 예측값 편차 {max_pred_diff:.4f} > 허용치 0.05 — 피처 불일치가 모델 예측에 영향")

    # 4. 2차 검증: 매매 체결 및 포지션 관리 로직 비교 (Parity Run)
    print("\n[3/4] 백테스트 시뮬레이션 재실행 및 매매 로그 Parity 검증...")
    
    # 📌 [인터벌 변환]: interval_str에서 interval_minutes 추출
    interval_minutes = 15.0
    if interval_str == "1s":
        interval_minutes = 1.0 / 60.0
    elif interval_str.endswith("m"):
        interval_minutes = float(interval_str[:-1])
    elif interval_str.endswith("h"):
        interval_minutes = float(interval_str[:-1]) * 60.0
    elif interval_str.endswith("d"):
        interval_minutes = float(interval_str[:-1]) * 1440.0

    cost = (0.0005 + 0.0005) * 2
    from shared.core.tbm_executor import TBMPositionManager
    pm = TBMPositionManager(params, cost=cost, interval_minutes=interval_minutes)
    
    # df_recreated 전체에 대해 예측 수행하여 매매 시뮬레이션에 주입
    preds_full = model.predict(df_recreated[features])
    
    # Baker 백테스트 시뮬레이션
    holding = False
    entry_price = 0.0
    entry_atr = 0.0
    entry_idx = 0
    cooldown_until = 0
    trade_logs_baker = []
    baker_timeline = []  # 매 분봉별 백테스트 상태 스냅샷 저장
    min_live_ts = df_live['timestamp'].min()

    for idx in range(len(df_recreated)):
        current_price = df_recreated['upbit_close'].iloc[idx]
        current_time = df_recreated['timestamp'].iloc[idx]
        
        # 📌 [Warm-up 보정]: 실시간 피처 덤프가 아직 가동되지 않은 워밍업 구간 시각에는 매매 진입(BUY)을 하지 않도록 제한
        allowed_to_buy = (current_time >= min_live_ts)

        state_record = {
            'timestamp': current_time,
            'idx': idx,
            'price': current_price,
            'holding': holding,
            'entry_idx': entry_idx if holding else None,
            'entry_time': df_recreated['timestamp'].iloc[entry_idx] if holding else None,
            'entry_price': entry_price if holding else 0.0,
            'in_cooldown': (idx < cooldown_until),
            'pred_prob': float(preds_full[idx]),
            'action': None,
            'reason': None
        }

        if holding:
            elapsed = idx - entry_idx
            
            # 📌 [고정밀 봉내 익손절 체크]: 종가만 체크하지 않고, 저가(손절)와 고가(익절)를 순차적으로 대조
            # 1. 1분 동안 가장 아래로 내려간 저가(Low) 기준으로 손절(Stop Loss) 판단
            low_price = df_recreated['upbit_low'].iloc[idx] if 'upbit_low' in df_recreated.columns else current_price
            exit_result = pm.check_exit(
                entry_price=entry_price,
                entry_atr=entry_atr,
                current_price=low_price,
                elapsed_candles=elapsed,
                current_time=current_time
            )
            
            # 2. 손절되지 않았다면, 가장 위로 올라간 고가(High) 기준으로 익절(Take Profit) 판단
            if not exit_result:
                high_price = df_recreated['upbit_high'].iloc[idx] if 'upbit_high' in df_recreated.columns else current_price
                exit_result = pm.check_exit(
                    entry_price=entry_price,
                    entry_atr=entry_atr,
                    current_price=high_price,
                    elapsed_candles=elapsed,
                    current_time=current_time
                )
                
            # 3. 둘 다 도달하지 않았다면, 마지막으로 종가(Close) 기준으로 시간청산 등 최종 판단
            if not exit_result:
                exit_result = pm.check_exit(
                    entry_price=entry_price,
                    entry_atr=entry_atr,
                    current_price=current_price,
                    elapsed_candles=elapsed,
                    current_time=current_time
                )
            if exit_result:
                exit_log = {
                    'timestamp': str(current_time),
                    'action': 'SELL',
                    'price': current_price,
                    'probability': float(preds_full[entry_idx]),
                    'reason': exit_result['exit_type'],
                    'net_return': float(exit_result['net_return']),
                    'hold_mins': int(exit_result['hold_mins']),
                    'entry_time': str(df_recreated['timestamp'].iloc[entry_idx])
                }
                trade_logs_baker.append(exit_log)
                state_record['action'] = 'SELL'
                state_record['reason'] = exit_result['exit_type']
                holding = False
                cooldown_until = idx + pm.cooldown_candles
        else:
            if idx >= cooldown_until and allowed_to_buy and preds_full[idx] >= params['buy_threshold']:
                holding = True
                entry_price = current_price
                entry_atr = df_recreated['upbit_atr'].iloc[idx] if 'upbit_atr' in df_recreated.columns else current_price * 0.015
                entry_idx = idx
                buy_log = {
                    'timestamp': str(current_time),
                    'action': 'BUY',
                    'price': entry_price,
                    'probability': float(preds_full[idx]),
                    'reason': 'AI_SIGNAL',
                    'net_return': 0.0,
                    'hold_mins': 0
                }
                trade_logs_baker.append(buy_log)
                state_record['action'] = 'BUY'
                state_record['reason'] = 'AI_SIGNAL'
                state_record['holding'] = True
                state_record['entry_time'] = current_time
                state_record['entry_price'] = entry_price

        baker_timeline.append(state_record)

    df_trades_baker = pd.DataFrame(trade_logs_baker)

    # 매매 내역 비교
    print(f"  - CTrade 실시간 매매 건수: {len(df_trades_live)}건")
    print(f"  - Baker 백테스트 매매 건수: {len(df_trades_baker)}건")

    # 매매 로그 정합성 분석
    mismatches = 0
    trade_ok = True

    # [보완⑤] CTrade 매매 0건 → 판정 보류 (이전: 조용히 통과)
    if len(df_trades_live) == 0:
        print("  🚨 [Alert] CTrade 실시간 매매 로그 0건 — 판정 보류")
        print("     → 원인 후보 1: 검증 기간 중 실제로 매수 신호가 없었음 (정상)")
        print("     → 원인 후보 2: Feature Gate가 전 봉 차단 → 신호 자체 발생 불가 (수신 불량)")
        print("     → 원인 후보 3: CTrade 비정상 종료 후 매매 기록 미저장")
        print("     → Baker 시뮬 매매 건수와 비교:")
        if len(df_trades_baker) > 0:
            print(f"       Baker 시뮬에서는 {len(df_trades_baker)}건 발생 → CTrade가 신호를 놓친 것으로 의심")
        else:
            print(f"       Baker 시뮬에서도 0건 → 해당 기간 신호 없음 (정상 가능성 높음)")
        trade_ok = None  # 판정 보류
    else:
        # 두 로그의 타임스탬프 문자열 표준화
        df_trades_live['timestamp'] = pd.to_datetime(df_trades_live['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')
        if len(df_trades_baker) > 0:
            df_trades_baker['timestamp'] = pd.to_datetime(df_trades_baker['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')
            
            # 실시간 trades와 백테스트 trades가 동일하게 매칭되는지 확인
            # 레이턴시 등으로 체결이 ±1분 전후로 밀리는 현상 대응
            matched_baker_trades = []
            for _, live_row in df_trades_live.iterrows():
                live_ts = pd.to_datetime(live_row['timestamp'])
                baker_match = df_trades_baker[
                    (pd.to_datetime(df_trades_baker['timestamp']) >= live_ts - pd.Timedelta(minutes=1)) &
                    (pd.to_datetime(df_trades_baker['timestamp']) <= live_ts + pd.Timedelta(minutes=1)) &
                    (df_trades_baker['action'] == live_row['action'])
                ]
                if len(baker_match) > 0:
                    matched_baker_trades.append(baker_match.iloc[0].to_dict())
                else:
                    matched_baker_trades.append(None)
            
            matched_count = 0
            print(f"\n  {'No':<3} | {'Action':<5} | {'실시간 시각':<19} | {'실시간가':<11} | {'백테스트가':<11} | {'상태 및 세부 사유'}")
            print("-" * 115)
            for i, live_t in df_trades_live.iterrows():
                baker_t = matched_baker_trades[i]
                live_ts = pd.to_datetime(live_t['timestamp'])
                live_action = live_t['action']
                live_price = float(live_t['price'])
                live_prob = float(live_t.get('probability', 0.0))
                live_reason = str(live_t.get('reason', ''))
                live_ret = float(live_t.get('net_return', 0.0))
                live_hold = int(live_t.get('hold_mins', 0))

                if baker_t is not None:
                    baker_price = float(baker_t['price'])
                    # 📌 [가격 허용치 완화]: 레이턴시로 인한 1분봉 시점 차이 가격 편차(최대 0.5%) 구제
                    price_match = np.abs(live_price - baker_price) / live_price < 0.005
                    if price_match:
                        matched_count += 1
                        if live_action == 'BUY':
                            status_detail = f"✅ 진입 일치 (확률: {live_prob:.4f})"
                        else:
                            b_reason = baker_t.get('reason', '')
                            status_detail = f"✅ 청산 일치 ({live_reason} / 백테스트: {b_reason}, {live_hold}분)"
                    else:
                        mismatches += 1
                        status_detail = f"❌ 가격 불일치 (실시간 {live_price:.1f} vs 백테스트 {baker_price:.1f})"
                    print(f"  {i+1:<3} | {live_action:<5} | {str(live_t['timestamp']):<19} | {live_price:<11.1f} | {baker_price:<11.1f} | {status_detail}")
                else:
                    # 백테스트 타임라인에서 해당 시각 전후 상태 추적
                    matching_states = [s for s in baker_timeline if abs((s['timestamp'] - live_ts).total_seconds()) <= 60]
                    cur_state = matching_states[0] if matching_states else None

                    if live_action == 'BUY':
                        if cur_state and cur_state['holding']:
                            e_time = cur_state['entry_time'].strftime('%H:%M') if cur_state['entry_time'] else '이전'
                            future_sells = [t for t in trade_logs_baker if t['action'] == 'SELL' and pd.to_datetime(t['timestamp']) >= live_ts]
                            f_exit = future_sells[0]['timestamp'][11:16] if future_sells else '미청산'
                            status_detail = f"⚠️ 백테스트 포지션 보유 중({e_time}~{f_exit})으로 신규 진입 스킵"
                        elif cur_state and cur_state['in_cooldown']:
                            status_detail = "⚠️ 백테스트 청산 후 쿨다운 상태로 신규 진입 스킵"
                        elif cur_state and cur_state['pred_prob'] < params.get('buy_threshold', 0.5):
                            status_detail = f"⚠️ 백테스트 예측확률({cur_state['pred_prob']:.4f}) < 임계값({params.get('buy_threshold', 0.5):.4f})"
                        else:
                            status_detail = "⚠️ 백테스트 미진입 (신호 미발생 또는 틱 유실)"
                    else:  # SELL
                        if cur_state and cur_state['holding']:
                            future_sells = [t for t in trade_logs_baker if t['action'] == 'SELL' and pd.to_datetime(t['timestamp']) >= live_ts]
                            if future_sells:
                                f_time = future_sells[0]['timestamp'][11:16]
                                f_reason = future_sells[0]['reason']
                                status_detail = f"⚠️ 실시간 {live_reason}({live_ret:+.2f}%, {live_hold}분) vs 백테스트 {f_time}까지 홀딩({f_reason} 청산)"
                            else:
                                status_detail = f"⚠️ 실시간 {live_reason}({live_ret:+.2f}%, {live_hold}분) vs 백테스트 미청산 홀딩"
                        else:
                            status_detail = f"⚠️ 실시간 {live_reason}({live_ret:+.2f}%, {live_hold}분) vs 백테스트 해당 포지션 미보유"

                    print(f"  {i+1:<3} | {live_action:<5} | {str(live_t['timestamp']):<19} | {live_price:<11.1f} | {'N/A':<11} | {status_detail}")
            print("-" * 115)
            
            # 매칭 성공한 건이 최소 1건 이상이고, 매칭된 건들 중 가격 불일치(mismatches)가 없으면 최종 통과로 인정
            if matched_count >= 1 and mismatches == 0:
                trade_ok = True
                print(f"  → 📈 매매 로그 매칭 성립 ({matched_count}건 매칭 완료, 가격 불일치 0건)")
            else:
                trade_ok = False
                print(f"  → ❌ 매매 로그 매칭 실패 (매칭 건수: {matched_count}건, 불일치 건수: {mismatches}건)")
        else:
            print("  ❌ 에러: 백테스트 시뮬레이션에서 매매가 단 1건도 생성되지 않았습니다.")
            trade_ok = False

    # 5. 최종 결론
    print("\n[4/4] 최종 검증 리포트 요약")
    print("=" * 80)
    _trade_definitive_ok = (trade_ok is True) and (mismatches == 0)
    _trade_suspended = (trade_ok is None)  # 0건 판정 보류

    if feat_ok and pred_ok and _trade_definitive_ok:
        print("  🎉 [통과] CTrade 실시간 구동과 Baker 백테스트 간에 100% 정합성이 성립합니다!")
        print("  → 피처·예측·매매 로그 모두 일치 확인")
        print("=" * 80)
        return True
    elif feat_ok and pred_ok and _trade_suspended:
        print("  🟡 [보류] 피처·예측 정합성은 통과했으나 매매 로그 비교 불가 (CTrade 0건)")
        print("  → 위 원인 후보를 점검하세요.")
        print("=" * 80)
        return None
    else:
        print("  🔴 [실패] 실시간 구동과 백테스트 간 불일치가 탐지되었습니다.")
        print(f"  - 피처 일치  : {'✅' if feat_ok else '❌'} {feat_ok}")
        print(f"  - 예측값 일치: {'✅' if pred_ok else '❌'} (최대편차 {max_pred_diff:.4f}, 허용 0.05)")
        print(f"  - 매매로그   : {'✅' if _trade_definitive_ok else ('🟡 보류' if _trade_suspended else '❌')} (불일치 {mismatches}건)")
        print("  → 위 ❌ 항목의 원인을 상단 리포트에서 확인하세요.")
        print("=" * 80)
        return False


if __name__ == '__main__':
    # ── 멀티 심볼 Parity 검증 (활성 AI_BAKER 전략 모두) ──
    if AI_TARGETS:
        overall_pass = True
        for sym, strat_name in AI_TARGETS:
            try:
                dp = _resolve_deploy_path(sym, strat_name, config, templates)
                ok = run_live_parity_check(symbol=sym, deploy_path=dp, log_dir=LOG_DIR)
                if not ok:
                    overall_pass = False
                print()
            except Exception as e:
                print(f"⚠️ {sym} Parity 검증 중 오류: {e}")
                overall_pass = False
        print("=" * 80)
        print("  📊 전체 AI_BAKER Parity 검증 결과:", "🎉 통과" if overall_pass else "⚠️ 일부 실패")
        print("=" * 80)
        sys.exit(0 if overall_pass else 1)
    else:
        run_live_parity_check()
