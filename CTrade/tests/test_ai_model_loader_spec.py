"""
H-2 회귀 테스트: CTrade AI 모델 로더 규격 단일화 및 결정론적 선택

- `model_{interval}.txt` 를 정식 소비 규격으로 고정하고,
  `_primary`/`_meta` (Baker 메타 라벨링 산출물)는 주 모델로 선택하지 않는다.
- binary(1D) / multiclass(2D, 상승=class 1) 추론을 모두 안전 처리한다.
"""
import json
import os

import numpy as np
import pandas as pd
import lightgbm as lgb
import pytest

from core.ai_strategy import AIStrategyModule

FEATS = ['f1', 'f2', 'f3']


def _make_package(base: str, multiclass: bool) -> str:
    model_dir = os.path.join(base, 'model')
    os.makedirs(model_dir, exist_ok=True)

    rng = np.random.RandomState(0)
    X = rng.rand(200, len(FEATS))
    y = np.random.RandomState(1).randint(0, 3 if multiclass else 2, 200)
    if multiclass:
        params = {'objective': 'multiclass', 'num_class': 3, 'verbose': -1, 'min_data_in_leaf': 1}
    else:
        params = {'objective': 'binary', 'verbose': -1, 'min_data_in_leaf': 1}
    booster = lgb.train(params, lgb.Dataset(X, label=y, feature_name=FEATS), num_boost_round=5)

    # 정식 소비 파일 + Baker 메타 라벨링 산출물(_primary/_meta) 동시 존재 시나리오
    booster.save_model(os.path.join(model_dir, 'model_1m.txt'))
    booster.save_model(os.path.join(model_dir, 'model_1m_primary.txt'))
    booster.save_model(os.path.join(model_dir, 'model_1m_meta.txt'))

    with open(os.path.join(model_dir, 'best_params_1m.json'), 'w', encoding='utf-8') as f:
        json.dump({
            'buy_threshold': 0.5, 'take_profit': 2.0, 'stop_loss': 1.5,
            'cooldown_minutes': 5, 'predict_ahead_candles': 10,
            'max_hold_candles': 10, 'opt_session_id': 'TEST',
        }, f)
    with open(os.path.join(model_dir, 'feature_spec.json'), 'w', encoding='utf-8') as f:
        json.dump({'features_order': FEATS, 'feature_params': {}}, f)
    return model_dir


def test_loader_picks_canonical_model_file(tmp_path):
    """_primary/_meta 가 공존해도 반드시 model_1m.txt 를 선택한다."""
    _make_package(str(tmp_path), multiclass=False)
    mod = AIStrategyModule(str(tmp_path), symbol='KRW-BTC', enable_parity_logging=False)
    assert os.path.basename(mod.model_path) == 'model_1m.txt'
    assert mod.model_num_class == 1

    raw = np.asarray(mod.model.predict(pd.DataFrame(np.random.rand(1, 3), columns=FEATS)))
    assert raw.ndim == 1  # binary → 스칼라 확률


def test_loader_handles_multiclass_primary(tmp_path):
    """multiclass 주 모델도 로드/추론이 가능해야 한다(상승=class 1 추출)."""
    _make_package(str(tmp_path), multiclass=True)
    mod = AIStrategyModule(str(tmp_path), symbol='KRW-BTC', enable_parity_logging=False)
    assert os.path.basename(mod.model_path) == 'model_1m.txt'
    assert mod.model_num_class == 3

    raw = np.asarray(mod.model.predict(pd.DataFrame(np.random.rand(1, 3), columns=FEATS)))
    assert raw.ndim == 2 and raw.shape[1] == 3
    assert np.isfinite(raw[0, 1])


def test_loader_raises_when_only_meta_artifacts(tmp_path):
    """정식 소비 파일이 없고 _primary/_meta 만 있으면 명시적으로 실패한다."""
    model_dir = _make_package(str(tmp_path), multiclass=False)
    os.remove(os.path.join(model_dir, 'model_1m.txt'))
    with pytest.raises(FileNotFoundError):
        AIStrategyModule(str(tmp_path), symbol='KRW-BTC', enable_parity_logging=False)


def test_loader_loads_meta_model_when_present(tmp_path):
    """H-3: _meta.txt 가 있으면 메타 모델을 로드해 실전 메타 필터에 사용한다."""
    _make_package(str(tmp_path), multiclass=True)
    mod = AIStrategyModule(str(tmp_path), symbol='KRW-BTC', enable_parity_logging=False)
    assert mod.meta_model is not None
    assert os.path.basename(mod.meta_model_path) == 'model_1m_meta.txt'


def test_multiclass_blocks_entry_when_primary_not_buy(tmp_path, monkeypatch):
    """multiclass 에서 상승(class 1) 확률이 buy_threshold 이상이어도 argmax!=1 이면 진입 차단"""
    from datetime import datetime, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    _make_package(str(tmp_path), multiclass=True)
    mod = AIStrategyModule(str(tmp_path), symbol='KRW-BTC', enable_parity_logging=False)
    mod.params['buy_threshold'] = 0.35
    mod.required_warmup_candles = 1
    mod.aggregator.min_required_candles = 1
    mod.test_level = 3  # 리스크 매니저/비용 스킵

    # mock predict: class 0=0.45, class 1=0.40(>=0.35), class 2=0.15 → argmax=0 (하락)
    monkeypatch.setattr(mod.model, 'predict', lambda df: np.array([[0.45, 0.40, 0.15]]))
    # 캔들 데이터프레임 모의 구성
    now = datetime.now(kst)
    candle_df = pd.DataFrame([{
        'timestamp': now,
        'upbit_open': 100.0, 'upbit_high': 105.0, 'upbit_low': 95.0,
        'upbit_close': 100.0, 'upbit_volume': 10.0,
        'f1': 1.0, 'f2': 1.0, 'f3': 1.0,
        'target_atr': 1.0, 'target_rsi_daily': 60.0,
    }])
    mod.aggregator.candles = [candle_df.iloc[0].to_dict()]
    monkeypatch.setattr('core.ai_strategy.run_feature_engineering', lambda *a, **kw: candle_df)

    # 1) argmax=0 (하락) → 매수 차단 검증
    sig_blocked = mod._on_candle_complete(candle_df, current_price=100.0, current_timestamp=now)
    assert sig_blocked is None, "primary_is_buy가 False인데 매수 신호가 발생함 (차단되어야 함)"

    # 2) argmax=1 (상승, class 1=0.55 > others) + meta 허용 → 매수 허용 검증
    monkeypatch.setattr(mod.model, 'predict', lambda df: np.array([[0.25, 0.55, 0.20]]))
    if mod.meta_model is not None:
        monkeypatch.setattr(mod.meta_model, 'predict', lambda df: np.array([0.90]))
    
    sig_allowed = mod._on_candle_complete(candle_df, current_price=100.0, current_timestamp=now)
    assert sig_allowed is not None and sig_allowed.action.name == "BUY", "primary_is_buy가 True이면 매수 신호가 발생해야 함"
    assert sig_allowed.tp_price > 100.0, "tp_price가 정상 계산되어야 함"
    assert sig_allowed.sl_price < 100.0, "sl_price가 정상 계산되어야 함"
    assert "f1" in sig_allowed.features_snapshot, "features_snapshot에 피처가 정상 보존되어야 함"
