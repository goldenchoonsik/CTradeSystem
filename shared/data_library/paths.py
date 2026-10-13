# -*- coding: utf-8 -*-
"""
CTradeSystem - Data Paths (데이터·배포 경로 단일 진실 공급원)
==============================================================================
[T-5] 배경
----------
종전에는 `CAiModelBaker/core/data_manager.py` 등이 `f"data/..."` **CWD 상대경로**를 사용했다.
운영 스크립트(`scripts/run_baker.sh`)가 `cd CAiModelBaker` 후 실행하므로 정상 동작했지만,
저장소 루트에서 실행하면 쓰기가 `<repo>/data/...` 로 흘러나가 **데이터 루트가 2개로 분리**되었다.
(실측: 루트 `data/` 에 고아 32파일 3.5MB — Bronze 원천이 두 곳에 쌓임)

규칙
----
1. 모든 데이터 경로는 본 모듈의 함수로만 해석한다 (**CWD 상대경로 금지**).
2. 루트는 환경변수로 재정의 가능하다 (배포·서버 유연성):
   `CTRADE_DATA_ROOT`, `CTRADE_DEPLOY_ROOT`
3. 반환값은 **절대경로** — 어느 CWD 에서 실행해도 동일한 파일을 가리킨다.
4. 계층 대응 (ARCHITECTURE.md Gold 계약 참조):
   · Bronze  = `DataVault/raw/**`          (append-only 원천 — 레거시 data/historical 은 은퇴)
   · Gold    = `data/{SYMBOL}/data_snapshot_*.csv` (+ .meta.json)
   · Artifact= `deploy/{SYMBOL}_{INTERVAL}/{SESSION}/` — config_snapshot.json(설정 원본)·model/(합격 모델)·
               runs/<시각>/(자동연구 실행 기록). 규칙은 shared/config/project_store.py
   · 캐시    = `shared/data_library/cache/**` (Silver 캔들 / Gold 미리보기)
"""

import os

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))                        # <repo>/shared/data_library
PROJECT_ROOT = os.path.dirname(os.path.dirname(_MODULE_DIR))                    # <repo>  (CTradeSystem)
BAKER_ROOT = os.path.join(PROJECT_ROOT, "CAiModelBaker")

# 데이터 루트 (기본: CAiModelBaker/data)
DATA_ROOT = os.path.abspath(
    os.environ.get("CTRADE_DATA_ROOT") or os.path.join(BAKER_ROOT, "data")
)
# 메달리온 아키텍처 볼트 루트 (기본: <repo>/DataVault, 폴백: <repo>/data_vault)
_default_vault = os.path.join(PROJECT_ROOT, "DataVault")
if not os.path.exists(_default_vault):
    _legacy_vault = os.path.join(PROJECT_ROOT, "data_vault")
    if os.path.exists(_legacy_vault):
        _default_vault = _legacy_vault

DATA_VAULT_ROOT = os.path.abspath(
    os.environ.get("CTRADE_DATA_VAULT_ROOT") or _default_vault
)
# 모델 아티팩트(Deploy) 루트 — Medallion 3계층 **밖**의 4번째 계층
DEPLOY_ROOT = os.path.abspath(
    os.environ.get("CTRADE_DEPLOY_ROOT") or os.path.join(BAKER_ROOT, "deploy")
)


def _join(root: str, parts) -> str:
    clean = [str(p) for p in parts if p is not None and str(p) != ""]
    return os.path.join(root, *clean)


def data_vault_path(*parts: str) -> str:
    """메달리온 볼트(data_vault) 루트 하위 절대경로."""
    return _join(DATA_VAULT_ROOT, parts)


def data_vault_config_path(*parts: str) -> str:
    """DataVault 거버넌스 설정 디렉토리(<DATA_VAULT_ROOT>/config) 하위 절대경로."""
    return data_vault_path("config", *parts)


# ==============================================================================
# Raw / Normalized / Features 표준 3단계 경로 헬퍼 (구 Bronze/Silver/Gold)
# ==============================================================================

def raw_root() -> str:
    """Raw (원천 불변 원시 데이터) 루트: ``<DATA_VAULT_ROOT>/raw``"""
    return data_vault_path("raw")


def raw_path(provider: str, asset_type: str, filename: str = None, fallback: bool = True) -> str:
    """Raw 원천 데이터 경로.
    예: ``raw_path("creon", "tick", "A069500_tick.csv")``
    """
    parts = [provider, asset_type]
    if filename:
        parts.append(filename)
    vault_target = data_vault_path("raw", *parts)
    
    # 폴백: raw에 없으면 bronze 탐색 (레거시 data/historical 은 은퇴 — 2026-10-02)
    if fallback and filename and not os.path.exists(vault_target):
        bronze_target = data_vault_path("bronze", *parts)
        if os.path.exists(bronze_target):
            return bronze_target
    return vault_target


def normalized_root() -> str:
    """Normalized (단일 자산 표준 정규화 데이터) 루트: ``<DATA_VAULT_ROOT>/normalized``"""
    return data_vault_path("normalized")


def normalized_path(category: str, filename: str = None, fallback: bool = True) -> str:
    """Normalized 데이터 경로.
    예: ``normalized_path("candles_10s", "069500_10s.parquet")``
    """
    parts = [category]
    if filename:
        parts.append(filename)
    vault_target = data_vault_path("normalized", *parts)
    if fallback and filename and not os.path.exists(vault_target):
        silver_target = data_vault_path("silver", *parts)
        if os.path.exists(silver_target):
            return silver_target
    return vault_target


def _macro_key(key: str) -> str:
    try:
        from shared.config.config_manager import resolve_macro_symbol
        return resolve_macro_symbol(key) or str(key).strip()
    except Exception:
        return str(key).strip()


def macro_normalized_path(key: str, interval: str = "1m") -> str:
    """macro 정규화 시계열(국내선물·지수·해외선물) 경로: ``normalized/macro/<원장 심볼>_<봉>.parquet``.

    데이터 파일은 종목 원장 키로 이름 짓는다 (예: 10100_1m.parquet). key 는 원장 심볼이다 (별칭은 없다).
    """
    return normalized_path("macro", f"{_macro_key(key)}_{interval}.parquet", fallback=False)


def macro_raw_path(key: str, interval: str = "1m") -> str:
    """국내 선물/지수(CREON) 원천 CSV 경로: ``raw/creon/candle_<봉>/<원장 심볼>_<봉>.csv`` (10100_1m.csv, U180_1m.csv)."""
    return raw_path("creon", f"candle_{interval}", f"{_macro_key(key)}_{interval}.csv", fallback=False)


def features_root() -> str:
    """Features (모델 학습/연구용 피처셋) 루트: ``<DATA_VAULT_ROOT>/features``"""
    return data_vault_path("features")


def features_path(*parts: str) -> str:
    """Features 데이터 경로: ``data_vault/features/{*parts}``"""
    return data_vault_path("features", *parts)


def features_dataset_path(symbol: str, name: str) -> str:
    """Features 데이터셋 경로: ``data_vault/features/{SYMBOL}/datasets/{NAME}``"""
    return data_vault_path("features", symbol, "datasets", name)


def features_session_dir(symbol: str, session_id: str) -> str:
    """Features 세션 디렉토리: ``data_vault/features/{SYMBOL}/sessions/{SESSION_ID}``"""
    return data_vault_path("features", symbol, "sessions", session_id)


# --- 하위 호환성 (Bronze / Silver / Gold -> Raw / Normalized / Features) ---
def bronze_root() -> str:
    """Bronze (하위 호환) -> Raw 루트 반환"""
    return raw_root()


def bronze_path(provider: str, asset_type: str, filename: str = None, fallback: bool = True) -> str:
    """Bronze (하위 호환) -> Raw 데이터 경로 반환"""
    return raw_path(provider, asset_type, filename, fallback=fallback)


def silver_root() -> str:
    """Silver (하위 호환) -> Normalized 루트 반환"""
    return normalized_root()


def silver_path(category: str, filename: str = None) -> str:
    """Silver (하위 호환) -> Normalized 데이터 경로 반환"""
    return normalized_path(category, filename, fallback=True)


def gold_root() -> str:
    """Gold (하위 호환) -> Features 루트 반환"""
    return features_root()


def gold_snapshot_path(symbol: str, interval: str) -> str:
    """Gold (하위 호환) -> Features 스냅샷 경로 반환"""
    return features_path(symbol, f"data_snapshot_{interval}.csv")


def gold_snapshot_meta_path(symbol: str, interval: str) -> str:
    """Gold (하위 호환) -> Features 메타 경로 반환"""
    return features_path(symbol, f"data_snapshot_{interval}.meta.json")


def data_path(*parts: str) -> str:
    """데이터 루트 하위 절대경로.

    예: ``data_path("historical", "kis", "candle_1m")``
        → ``<DATA_ROOT>/historical/kis/candle_1m``
    """
    return _join(DATA_ROOT, parts)


def deploy_path(*parts: str) -> str:
    """배포(모델 아티팩트) 루트 하위 절대경로.

    예: ``deploy_path("069500_10s", "KODEX200_S10_BEST1", "model")``
    """
    return _join(DEPLOY_ROOT, parts)


def display(path) -> str:
    """로그 가독성용 표시 경로 — 프로젝트 루트 기준 상대(밖이면 원본 그대로)."""
    try:
        rel = os.path.relpath(str(path), PROJECT_ROOT)
        return rel.replace(os.sep, "/") if not rel.startswith("..") else str(path)
    except Exception:
        return str(path)


# ==============================================================================
# Gold (학습 데이터셋) — 단일 writer: run_dataset_build_pipeline
# ==============================================================================

def snapshot_path(symbol: str, interval: str) -> str:
    """Gold 학습 데이터셋 경로:
    우선 ``data_vault/gold/{SYMBOL}/data_snapshot_{INTERVAL}.csv`` 존재 시 반환,
    없을 경우 기존 ``data/{SYMBOL}/data_snapshot_{INTERVAL}.csv`` 폴백.
    """
    gold_target = gold_snapshot_path(symbol, interval)
    if os.path.exists(gold_target):
        return gold_target
    return data_path(symbol, f"data_snapshot_{interval}.csv")


def snapshot_meta_path(symbol: str, interval: str) -> str:
    """Gold 계보 메타:
    우선 ``data_vault/gold/{SYMBOL}/data_snapshot_{INTERVAL}.meta.json`` 존재 시 반환,
    없을 경우 기존 ``data/{SYMBOL}/data_snapshot_{INTERVAL}.meta.json`` 폴백.
    """
    gold_target = gold_snapshot_meta_path(symbol, interval)
    if os.path.exists(gold_target):
        return gold_target
    return data_path(symbol, f"data_snapshot_{interval}.meta.json")


def data_vault_preview_path(symbol: str, interval: str) -> str:
    """온디맨드 조인 미리보기 (Gold Feature Store 원장: DataVault/features/{SYMBOL}/data_vault_preview_{INTERVAL}.csv)"""
    gold_target = features_path(symbol, f"data_vault_preview_{interval}.csv")
    if os.path.exists(gold_target):
        return gold_target
    legacy_target = data_path(symbol, f"data_vault_preview_{interval}.csv")
    if os.path.exists(legacy_target):
        return legacy_target
    return gold_target


# ==============================================================================
# Artifact (모델 아티팩트) — deploy 계층
# ==============================================================================

def session_dir(symbol: str, interval: str, session_id: str) -> str:
    """``deploy/{SYMBOL}_{INTERVAL}/{SESSION}``"""
    return deploy_path(f"{symbol}_{interval}", session_id)


def model_dir(symbol: str, interval: str, session_id: str) -> str:
    """``deploy/{SYMBOL}_{INTERVAL}/{SESSION}/model``"""
    return os.path.join(session_dir(symbol, interval, session_id), "model")


def best_params_path(symbol: str, interval: str, session_id: str = None) -> str:
    """베스트 파라미터 경로.

    · 세션 지정 시: ``deploy/{SYM}_{INT}/{SESSION}/model/best_params_{INT}.json``
    · 미지정 시   : ``data/{SYM}/best_params_{INT}.json`` (레거시 배치 경로)
    """
    name = f"best_params_{interval}.json"
    if session_id:
        return os.path.join(model_dir(symbol, interval, session_id), name)
    return data_path(symbol, name)


def model_file_path(symbol: str, interval: str, session_id: str = None) -> str:
    """모델 가중치 경로 (best_params_path 와 동일 규칙)."""
    name = f"model_{interval}.txt"
    if session_id:
        return os.path.join(model_dir(symbol, interval, session_id), name)
    return data_path(symbol, name)

