"""
CTradeSystem Shared - Feature Configuration Manager

feature_registry.py에서 지표 설정(config 로드/저장/정규화) 로직을 분리합니다.

사용 예:
    from shared.features.feature_config import load_feature_config, save_feature_config, get_enabled_feature_names
    cfg = load_feature_config()
"""
import json
import logging
import os

_CONFIG_ROOT_INTERNAL = None


def _registry():
    """lazy import helper — 순환 import 방지 (feature_registry ↔ feature_config)"""
    from shared.features.feature_registry import (
        DEFAULT_FEATURE_REGISTRY,
        FEATURE_ALIASES,
        CATEGORY_INFO,
        DEFAULT_OPTUNA_CONFIG,
    )
    return DEFAULT_FEATURE_REGISTRY, FEATURE_ALIASES, CATEGORY_INFO, DEFAULT_OPTUNA_CONFIG


# shared/features/feature_config.py -> 레포 루트는 3단계 위
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def set_config_root(root_path: str):
    """설정 파일 탐색 기준 디렉토리를 외부에서 지정합니다."""
    global _CONFIG_ROOT_INTERNAL
    _CONFIG_ROOT_INTERNAL = root_path


def get_config_path():
    """지표 설정 원본 = 열린 프로젝트의 config_snapshot.json. 열린 프로젝트가 없으면 None.

    전역 active.json / active_config.json 으로 대신 가지 않는다 (2026-10-01 은퇴 — 낡은 설정이 섞여 있었다).
    포인터가 깨졌으면 BrokenProjectPointerError 가 그대로 올라간다.
    """
    base = _CONFIG_ROOT_INTERNAL or _REPO_ROOT
    from shared.config import project_store
    active_ref = project_store.get_active_ref(os.path.join(base, "CAiModelBaker", "config"))
    return project_store.config_path(active_ref) if active_ref else None


def _normalize_feature_list(features):
    """피처 리스트 내의 레거시 ID(upbit_*)를 표준 ID(target_*)로 정규화하고 중복 제거.

    참조 피처(`ref_{key}_{kind}`)는 레지스트리에 **지연 등록**된다. 그래서 여기서 단순히
    "레지스트리에 있나"만 보면, 그 프로세스에서 누가 먼저 등록해 줬는지에 따라 같은 설정이
    다르게 읽힌다 — 대시보드에서 지표 화면을 먼저 열면 살아남고, 새 프로세스(CLI/자동연구)
    에서는 **조용히 사라진다**. 실제로 저장된 참조 피처 2개가 그렇게 지워져 자동연구가
    참조 피처를 후보로 보지도 못했다.

    id 안에 종목 키가 들어 있으므로 등록에 다른 정보가 필요 없다. 여기서 id 로 등록을
    시도해 호출 순서 의존을 없앤다.
    """
    DEFAULT_FEATURE_REGISTRY, FEATURE_ALIASES, _, _ = _registry()
    from shared.features.feature_registry import ensure_reference_feature
    if not features:
        return []
    normalized = []
    seen = set()
    for f in features:
        canon = FEATURE_ALIASES.get(f, f)
        if canon in seen:
            continue
        if canon not in DEFAULT_FEATURE_REGISTRY:
            ensure_reference_feature(canon)  # 참조 피처면 id 만으로 등록된다
        if canon in DEFAULT_FEATURE_REGISTRY:
            normalized.append(canon)
            seen.add(canon)
    return normalized


def _find_session_spec(session_id: str, symbol: str = "", interval: str = "") -> dict:
    """세션의 model/feature_spec.json 로드 — <종목>_<인터벌>/<세션> 정확한 경로만 본다.

    종전에는 deploy 루트 3곳(CTrade 의 shared/deploy 포함)의 모든 인터벌 폴더를 이름으로 뒤져 첫 매치를
    썼다. 같은 이름이 다른 인터벌에 있으면 다른 모델의 지표 목록을 가져왔다.
    """
    if not session_id or session_id in ("N/A", "default", "") or not symbol or not interval:
        return {}
    base_dir = _CONFIG_ROOT_INTERNAL or _REPO_ROOT
    market_dir = f"{str(symbol).split('-')[-1]}_{interval}"
    spec_path = os.path.join(base_dir, "CAiModelBaker", "deploy", market_dir, session_id, "model", "feature_spec.json")
    if not os.path.exists(spec_path):
        return {}
    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_feature_config():
    """설정 파일에서 지표 설정 로드 (활성 세션의 feature_spec.json 우선 연동 및 정규화)"""
    DEFAULT_FEATURE_REGISTRY, FEATURE_ALIASES, CATEGORY_INFO, DEFAULT_OPTUNA_CONFIG = _registry()
    config_path = get_config_path()

    canonical_all_keys = [k for k, v in DEFAULT_FEATURE_REGISTRY.items() if not v.get("is_alias", False)]
    # 기본 템플릿 지표 (20개)
    default_template_keys = [
        "binance_ret_5m", "kimchi_premium", "target_ma_ratio", "target_trade_bid_ratio",
        "binance_spot_taker_ratio", "target_rsi_daily", "target_hour", "target_is_weekend",
        "target_rsi_curr", "target_atr_ratio", "target_adx", "target_bb_pctb",
        "target_macd_hist", "target_mfi", "binance_oi_change_ratio", "binance_oi_ma_ratio",
        "binance_cvd_momentum", "target_cvd_momentum", "binance_cvd_divergence", "binance_usdc_volume_share"
    ]

    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            feature_config = config.get("FEATURE_CONFIG", {})
            optuna_config = config.get("OPTUNA_CONFIG", DEFAULT_OPTUNA_CONFIG)

            raw_enabled = None
            spec_params = {}

            # 📌 1순위: active.json 내 FEATURE_CONFIG.enabled_features (사용자가 설정/저장한 설정)
            if "enabled_features" in feature_config and feature_config["enabled_features"] is not None:
                raw_enabled = feature_config["enabled_features"]

            # 📌 2순위: active.json 내 FEATURE_CONFIG.active_features
            elif "active_features" in feature_config and feature_config["active_features"] is not None:
                raw_enabled = feature_config["active_features"]

            # 📌 3순위: 활성 세션(ACTIVE_SESSION_ID)의 model/feature_spec.json 스마트 검색
            else:
                active_sess_id = config.get("ACTIVE_SESSION_ID", "").strip()
                if active_sess_id and active_sess_id != "N/A":
                    spec_json = _find_session_spec(active_sess_id, config.get("SYMBOL_UPBIT", ""), config.get("INTERVAL", ""))
                    if spec_json:
                        s_feats = spec_json.get("features_order") or spec_json.get("feature_names", [])
                        if s_feats:
                            raw_enabled = s_feats
                            spec_params = spec_json.get("feature_params", {})

            # 📌 4순위: 기본 템플릿 지표 (20개) - 설정이 전혀 없는 초기 상태
            if raw_enabled is None:
                raw_enabled = default_template_keys

            raw_active = raw_enabled

            enabled_features = _normalize_feature_list(raw_enabled)
            if not enabled_features and raw_enabled == default_template_keys:
                enabled_features = default_template_keys
            active_features = _normalize_feature_list(raw_active)
            if not active_features and raw_active == default_template_keys:
                active_features = enabled_features

            # 파라미터 및 패널티 키 정규화
            raw_params = feature_config.get("feature_params", {})
            if spec_params:
                merged_params = dict(raw_params)
                merged_params.update(spec_params)
                raw_params = merged_params

            feature_params = {}
            for k, v in raw_params.items():
                canon_k = FEATURE_ALIASES.get(k, k)
                if canon_k not in feature_params:
                    feature_params[canon_k] = v

            raw_penalties = feature_config.get("feature_penalties", {})
            feature_penalties = {}
            for k, v in raw_penalties.items():
                canon_k = FEATURE_ALIASES.get(k, k)
                if canon_k not in feature_penalties:
                    feature_penalties[canon_k] = v

            raw_regime = feature_config.get("regime_features", {})
            regime_features = {}
            for k, v in (raw_regime or {}).items():
                canon_k = FEATURE_ALIASES.get(k, k)
                if canon_k not in regime_features:
                    regime_features[canon_k] = bool(v)

            return {
                "registry": DEFAULT_FEATURE_REGISTRY,
                "enabled_features": enabled_features,
                "active_features": active_features,
                "feature_params": feature_params,
                "feature_penalties": feature_penalties,
                "regime_features": regime_features,
                "optuna_config": optuna_config,
                "categories": CATEGORY_INFO,
                # 지표 파라미터 정책 (shared/features/param_policy.py) — 키가 없으면 legacy
                "param_policy": feature_config.get("param_policy"),
            }
        except Exception as e:
            logging.error(f"[FeatureRegistry] Config load error: {e}")

    return {
        "registry": DEFAULT_FEATURE_REGISTRY,
        "enabled_features": canonical_all_keys,
        "active_features": canonical_all_keys,
        "feature_params": {},
        "feature_penalties": {},
        "regime_features": {},
        "optuna_config": DEFAULT_OPTUNA_CONFIG,
        "categories": CATEGORY_INFO
    }


def save_feature_config(enabled_features, feature_params, optuna_config=None, active_features=None, feature_penalties=None, regime_features=None):
    """지표 설정을 config 파일들에 저장 (표준 ID 기준 정규화 및 중복 제거)"""
    _, FEATURE_ALIASES, _, _ = _registry()
    config_path = get_config_path()
    if not config_path:
        from shared.config.project_store import NoOpenProjectError
        raise NoOpenProjectError("열린 프로젝트가 없어 지표 설정을 저장할 수 없습니다 — 프로젝트를 먼저 열거나 만드세요.")

    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            pass

    norm_enabled = _normalize_feature_list(enabled_features)
    norm_active = _normalize_feature_list(active_features if active_features is not None else enabled_features)

    norm_params = {}
    for k, v in (feature_params or {}).items():
        canon_k = FEATURE_ALIASES.get(k, k)
        if canon_k not in norm_params:
            norm_params[canon_k] = v

    norm_penalties = {}
    raw_p = feature_penalties if feature_penalties is not None else config.get("FEATURE_CONFIG", {}).get("feature_penalties", {})
    for k, v in (raw_p or {}).items():
        canon_k = FEATURE_ALIASES.get(k, k)
        if canon_k not in norm_penalties:
            norm_penalties[canon_k] = v

    norm_regime = {}
    raw_r = regime_features if regime_features is not None else config.get("FEATURE_CONFIG", {}).get("regime_features", {})
    for k, v in (raw_r or {}).items():
        canon_k = FEATURE_ALIASES.get(k, k)
        if canon_k not in norm_regime:
            norm_regime[canon_k] = bool(v)

    feature_config_dict = {
        "enabled_features": norm_enabled,
        "active_features": norm_active,
        "feature_params": norm_params,
        "feature_penalties": norm_penalties,
        "regime_features": norm_regime
    }
    # 지표 파라미터 정책은 화면 저장으로 바뀌지 않는다 (프로젝트 생성 시 정해지는 계산 규약)
    _prev_policy = (config.get("FEATURE_CONFIG") or {}).get("param_policy")
    if _prev_policy:
        feature_config_dict["param_policy"] = _prev_policy

    config["FEATURE_CONFIG"] = feature_config_dict

    if optuna_config:
        if isinstance(optuna_config, dict):
            existing_optuna = config.get("OPTUNA_CONFIG", {})
            if isinstance(existing_optuna, dict):
                merged_optuna = dict(existing_optuna)
                merged_optuna.update(optuna_config)
                config["OPTUNA_CONFIG"] = merged_optuna
            else:
                config["OPTUNA_CONFIG"] = optuna_config
        else:
            config["OPTUNA_CONFIG"] = optuna_config

    # 주 설정 파일 저장
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

    return True


def get_feature_list_with_params(config=None):
    """활성화된 지표 목록과 파라미터 반환 (UI 렌더링용: 별칭 제외)"""
    _, FEATURE_ALIASES, _, _ = _registry()
    feature_config = load_feature_config()
    registry = feature_config["registry"]
    enabled = feature_config["enabled_features"]
    params = feature_config["feature_params"]

    features = []
    for fid in enabled:
        canon_id = FEATURE_ALIASES.get(fid, fid)
        if canon_id in registry:
            feature = registry[canon_id].copy()
            feature.pop('compute', None)
            if canon_id in params:
                feature["current_params"] = params[canon_id]
            else:
                feature["current_params"] = {}
                for pname, pconfig in feature.get("params", {}).items():
                    feature["current_params"][pname] = pconfig.get("default")
            features.append(feature)

    return features


def get_enabled_feature_names(config=None):
    """활성화된 지표 ID 목록 반환 (model_engine용)"""
    feature_config = load_feature_config()
    return feature_config["enabled_features"]