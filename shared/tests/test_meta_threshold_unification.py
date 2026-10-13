"""
H-3 회귀 테스트: 메타(2차) 모델 승인 임계값 단일화

- 학습(Backtester/WFA/OOS/web_dashboard)과 실전(CTrade)이 `shared.ai_policy.META_APPROVAL_THRESHOLD`
  단일 상수(0.50)를 사용하는지 검증한다.
- 구 하드코딩 임계값(0.35 / 0.50)이 잔존하지 않는지 확인한다.
"""
import os

import pytest

from shared.core.ai_policy import META_APPROVAL_THRESHOLD

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SOURCES = [
    'CAiModelBaker/core/backtester.py',
    'CAiModelBaker/core/model_engine.py',
    'CAiModelBaker/web_dashboard/main.py',
    'CTrade/core/ai_strategy.py',
]


def test_meta_threshold_value():
    assert META_APPROVAL_THRESHOLD == 0.50


@pytest.mark.parametrize('relpath', SOURCES)
def test_sources_use_single_constant(relpath):
    src = open(os.path.join(REPO_ROOT, relpath), encoding='utf-8').read()
    assert 'META_APPROVAL_THRESHOLD' in src, f"{relpath} 이 단일 상수를 사용하지 않음"
    # 구 하드코딩 임계값 잔존 금지
    assert 'preds_meta[idx] >= 0.35' not in src, f"{relpath} 에 구 임계값 0.35 잔존"
    assert 'preds_meta[idx] >= 0.50' not in src, f"{relpath} 에 하드코딩 0.50 잔존"
