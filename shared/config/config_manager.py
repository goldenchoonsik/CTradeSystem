"""
공통 설정 관리 모듈 (Config Manager)
====================================
프로젝트별 독립 설정 저장/로드 + 공통 기본값 병합 + 시계열 키 검증 + 백업

구조:
    shared/config/common.json        → 공통 설정 (수집 기간, 수수료/슬리피지, 새 프로젝트의 기본 종목/인터벌)
    <project>/config/defaults.json   → 프로젝트 기본값 템플릿 (새 프로젝트의 출발점)
    CAiModelBaker/config/active_project.json → 열린 프로젝트 포인터 ("<종목>_<인터벌>/<이름>")
    CAiModelBaker/deploy/<종목>_<인터벌>/<이름>/config_snapshot.json → 설정의 **유일한** 원본

CAiModelBaker 에는 '자유 모드' 가 없다 (2026-10-01 은퇴). 열린 프로젝트가 없으면 읽기는 깨끗한 기본값
(common → defaults)만, 저장은 NoOpenProjectError. 포인터가 깨졌으면 BrokenProjectPointerError.
전역 CAiModelBaker/config/active.json·active_config.json 은 읽지도 쓰지도 않는다 (보관본: config/legacy/).
<project>/config/active.json 은 프로젝트 폴더가 없는 앱에서만 쓴다.
    shared/deploy/*/config_snapshot.json → 불변 배포 스냅샷

프로젝트가 열려 있으면 load/save_project_config("CAiModelBaker") 는 그 프로젝트 폴더를 읽고 쓴다
(shared/config/project_store.py). 종목·인터벌·이름은 폴더 경로로 고정된다.
"""

import os
import json
import shutil
from datetime import datetime

from shared.core.file_lock import with_file_lock

# =============================================================================
# 경로 상수
# =============================================================================
_SHARED_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMON_CONFIG_PATH = os.path.join(_SHARED_DIR, "config", "common.json")
SYMBOLS_REGISTRY_PATH = os.path.join(_SHARED_DIR, "config", "symbols_registry.json")
_REGISTRY_LOCK = with_file_lock(SYMBOLS_REGISTRY_PATH)  # read-modify-write 직렬화 (다른 프로세스간 fcntl 포함)

# 프로젝트 이름별 설정 디렉토리 (프로젝트 루트 기준 — shared의 형제)
_PROJECT_DIRS = {
    "CAiModelBaker": os.path.abspath(os.path.join(_SHARED_DIR, "..", "CAiModelBaker")),
    "CTrade": os.path.abspath(os.path.join(_SHARED_DIR, "..", "CTrade")),
}

# 시계열 검증 통제 장벽 키 (Train/Opt/Test 3구간)
PERIOD_KEYS = [
    "TRAIN_START", "TRAIN_END",
    "OPT_START", "OPT_END",
    "TEST_START", "TEST_END",
]

# =============================================================================
# 기본 유틸
# =============================================================================

def _deep_merge(base: dict, override: dict) -> dict:
    """딥 머지 — override가 base를 덮어씀"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def normalize_period_value(value) -> str:
    """
    시계열 키 값을 YYYY-MM-DD 포맷으로 정규화.
    파싱 불가하면 빈 문자열 반환.
    """
    if value is None:
        return ""
    from datetime import datetime
    str_val = str(value).strip()
    if not str_val:
        return ""
    # YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS 형태 파싱
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m"):
        try:
            dt = datetime.strptime(str_val, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def normalize_period_keys(config: dict) -> dict:
    """config의 시계열 키들을 YYYY-MM-DD 포맷으로 정규화"""
    config = dict(config)
    for key in PERIOD_KEYS:
        if key in config:
            config[key] = normalize_period_value(config[key])
    return config


def normalize_archive_ym(value) -> str:
    """
    수집 기간 년월(ARCHIVE_*_YM) 값을 YYYY-MM 포맷으로 정규화.
    파싱 불가하면 원본 문자열을 최대한 정제하여 반환.
    """
    if value is None:
        return ""
    from datetime import datetime
    str_val = str(value).strip().replace(".", "-").replace("/", "")
    if not str_val:
        return ""
    if len(str_val) == 6 and str_val.isdigit():
        return f"{str_val[:4]}-{str_val[4:]}"
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y%m"):
        try:
            dt = datetime.strptime(str_val[:10], fmt)
            return dt.strftime("%Y-%m")
        except ValueError:
            continue
    import re
    match = re.match(r"^(\d{4})[-\.]?(\d{2})", str_val)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return str_val[:7]


def normalize_fee_keys(config: dict) -> dict:
    """
    수수료/슬리피지 단일 진실 공급원(Single SOT) 동기화
    FEE_RATE, FEE_UPBIT, OPTUNA_CONFIG.fee_upbit 및 SLIPPAGE, OPTUNA_CONFIG.slippage 일치
    """
    config = dict(config)
    optuna_cfg = config.get("OPTUNA_CONFIG", {})
    if not isinstance(optuna_cfg, dict):
        optuna_cfg = {}
        config["OPTUNA_CONFIG"] = optuna_cfg
        
    # 수수료 단일값 결정 (우선순위: FEE_RATE -> optuna_cfg.fee_upbit -> FEE_UPBIT -> 0.0005)
    fee = config.get("FEE_RATE")
    if fee is None:
        fee = optuna_cfg.get("fee_upbit")
    if fee is None:
        fee = config.get("FEE_UPBIT")
    if fee is None:
        fee = 0.0005
    try:
        fee = float(fee)
    except (ValueError, TypeError):
        fee = 0.0005
        
    # 슬리피지 단일값 결정 (우선순위: SLIPPAGE -> optuna_cfg.slippage -> 0.0006)
    slippage = config.get("SLIPPAGE")
    if slippage is None:
        slippage = optuna_cfg.get("slippage")
    if slippage is None:
        slippage = 0.0006
    try:
        slippage = float(slippage)
    except (ValueError, TypeError):
        slippage = 0.0006

    config["FEE_RATE"] = fee
    config["FEE_UPBIT"] = fee
    optuna_cfg["fee_upbit"] = fee
    
    config["SLIPPAGE"] = slippage
    optuna_cfg["slippage"] = slippage

    # 수집 기간 정규화
    if "ARCHIVE_START_YM" in config and config["ARCHIVE_START_YM"]:
        config["ARCHIVE_START_YM"] = normalize_archive_ym(config["ARCHIVE_START_YM"])
    if "ARCHIVE_END_YM" in config and config["ARCHIVE_END_YM"]:
        config["ARCHIVE_END_YM"] = normalize_archive_ym(config["ARCHIVE_END_YM"])
    
    return config


# =============================================================================
# 공통 설정
# =============================================================================

_DEFAULT_COMMON = {
    "SYMBOL_UPBIT": "KRW-BTC",
    "SYMBOL_BINANCE": "BTCUSDT",
    "INTERVAL": "30m",
    "FEE_UPBIT": 0.0005,
    "SLIPPAGE": 0.0006,
    "ARCHIVE_START_YM": "2024-01",
    "ARCHIVE_END_YM": "2026-09",
}


def load_common_config() -> dict:
    """공통 설정 로드 — common.json 없으면 기본값 반환"""
    if not os.path.exists(COMMON_CONFIG_PATH):
        return dict(_DEFAULT_COMMON)
    try:
        with open(COMMON_CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
            return _deep_merge(_DEFAULT_COMMON, saved)
    except Exception:
        return dict(_DEFAULT_COMMON)


def save_common_config(config: dict) -> None:
    """공통 설정 저장"""
    os.makedirs(os.path.dirname(COMMON_CONFIG_PATH), exist_ok=True)
    _backup_if_exists(COMMON_CONFIG_PATH)
    merged = _deep_merge(_DEFAULT_COMMON, config)
    with open(COMMON_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=4, ensure_ascii=False)


# =============================================================================
# 프로젝트 설정 경로
# =============================================================================

def get_project_root(project: str) -> str:
    """프로젝트 루트 경로 반환"""
    if project not in _PROJECT_DIRS:
        raise ValueError(f"Unknown project: {project}")
    return _PROJECT_DIRS[project]


def get_config_dir(project: str) -> str:
    """프로젝트 config 디렉토리 경로"""
    return os.path.join(get_project_root(project), "config")


def get_defaults_path(project: str) -> str:
    """프로젝트 defaults.json 경로"""
    return os.path.join(get_config_dir(project), "defaults.json")


def get_active_path(project: str) -> str:
    """앱 active.json 경로 — 프로젝트 폴더가 없는 앱 전용 (CAiModelBaker 는 쓰지 않는다)"""
    return os.path.join(get_config_dir(project), "active.json")


# 프로젝트 폴더(deploy/<종목>_<인터벌>/<이름>)를 가진 앱. 열린 프로젝트가 있으면 설정 원본은 그 폴더다.
_PROJECT_FOLDER_APPS = ("CAiModelBaker",)


def get_active_project_ref(project: str = "CAiModelBaker"):
    """열린 프로젝트 ref ("<종목>_<인터벌>/<이름>") — 없으면 None, 깨졌으면 BrokenProjectPointerError"""
    if project not in _PROJECT_FOLDER_APPS:
        return None
    from shared.config import project_store
    return project_store.get_active_ref(get_config_dir(project))


def set_active_project_ref(ref, project: str = "CAiModelBaker") -> None:
    """프로젝트 열기/닫기 (ref=None 이면 닫기)"""
    from shared.config import project_store
    project_store.set_active_ref(get_config_dir(project), ref)


# =============================================================================
# 프로젝트 설정 로드 / 저장
# =============================================================================

def load_project_config(project: str) -> dict:
    """
    프로젝트 설정 로드 (올바른 병합 순서):
        전역 공통 베이스(common.json) → 프로젝트 기본값(defaults.json) → 열린 프로젝트의 config_snapshot.json
        (CAiModelBaker 에서 열린 프로젝트가 없으면 앞의 두 단계까지만. 프로젝트 폴더가 없는 앱만 active.json 을 쓴다)
    """
    # 1. 전역 공통 베이스 로드
    common = load_common_config()
    config = dict(common)

    # 2. 프로젝트 기본값 템플릿 로드
    defaults_path = get_defaults_path(project)
    if os.path.exists(defaults_path):
        try:
            with open(defaults_path, "r", encoding="utf-8") as f:
                defaults = json.load(f)
                config = _deep_merge(config, defaults)
        except Exception:
            pass

    # 전역 마스터 원장 수집 기간은 common.json을 단일 진실원(SOT)으로 유지
    for ym_k in ("ARCHIVE_START_YM", "ARCHIVE_END_YM"):
        if ym_k in common and common[ym_k]:
            config[ym_k] = common[ym_k]

    # 3-A. 열린 프로젝트가 있으면 그 폴더의 설정이 원본이다 (전역 active.json 은 읽지 않는다 —
    #      읽으면 직전 프로젝트에만 있던 키가 새어 들어온다). 신원 키는 폴더 경로로 고정.
    active_ref = get_active_project_ref(project)
    if active_ref:
        from shared.config import project_store
        stored = project_store.read_project_config(active_ref)
        config = _deep_merge(config, stored)
        config.update(project_store.identity_of(active_ref, stored))
        for ym_k in ("ARCHIVE_START_YM", "ARCHIVE_END_YM"):
            if ym_k in common and common[ym_k]:
                config[ym_k] = common[ym_k]
        config = normalize_period_keys(config)
        config = normalize_fee_keys(config)
        return config

    # 3-B. 프로젝트 폴더 앱(CAiModelBaker)에서 열린 프로젝트가 없으면 깨끗한 기본값만 돌려준다.
    #      전역 active.json 은 읽지 않는다 — 여러 프로젝트의 설정이 뒤섞인 낡은 파일이었고(2026-10-01 실측:
    #      삭제된 TEST2 의 세션ID·S10 의 지표 26개가 섞여 있었다), AI·사용자 모두 무엇이 적용되는지 알 수 없었다.
    if project in _PROJECT_FOLDER_APPS:
        return load_base_config(project)

    # 3-C. 프로젝트 폴더가 없는 앱: 앱 고유 활성 설정(active.json) 최종 덮어쓰기
    active_path = get_active_path(project)
    if os.path.exists(active_path):
        try:
            with open(active_path, "r", encoding="utf-8") as f:
                active = json.load(f)
                config = _deep_merge(config, active)
        except Exception:
            pass

    # 시계열 키 및 수수료/슬리피지 정규화
    config = normalize_period_keys(config)
    config = normalize_fee_keys(config)
    return config


def load_base_config(project: str) -> dict:
    """새 프로젝트의 출발점: 공통(common.json) → 기본값(defaults.json). 열린 프로젝트·active.json 은 섞지 않는다.

    종전에는 새 프로젝트가 load_config() — 즉 직전에 열려 있던 프로젝트 설정 전체 — 를 물려받았다.
    """
    common = load_common_config()
    config = dict(common)
    defaults_path = get_defaults_path(project)
    if os.path.exists(defaults_path):
        try:
            with open(defaults_path, "r", encoding="utf-8") as f:
                config = _deep_merge(config, json.load(f))
        except Exception:
            pass
    for ym_k in ("ARCHIVE_START_YM", "ARCHIVE_END_YM"):
        if ym_k in common and common[ym_k]:
            config[ym_k] = common[ym_k]
    config = normalize_period_keys(config)
    config = normalize_fee_keys(config)
    return config


def save_project_config(project: str, config: dict) -> None:
    """
    프로젝트 활성 설정 저장.
    - CAiModelBaker: 열린 프로젝트의 config_snapshot.json 에만 저장. 열린 프로젝트가 없으면 NoOpenProjectError.
    - 프로젝트 폴더가 없는 앱: 공통 키(common.json에 있는 값)는 active.json에서 제외하고 저장
    - 전체 교체 방식 (부분 갱신 없음)
    """
    config = dict(config)
    config = normalize_period_keys(config)
    config = normalize_fee_keys(config)

    # 열린 프로젝트가 있으면 그 폴더의 설정 파일에 저장 (신원 키 고정, 실행용 임시 키 제거)
    active_ref = get_active_project_ref(project)
    if active_ref:
        from shared.config import project_store
        project_store.write_project_config(active_ref, config)
        return
    if project in _PROJECT_FOLDER_APPS:
        from shared.config.project_store import NoOpenProjectError
        raise NoOpenProjectError("열린 프로젝트가 없어 설정을 저장할 수 없습니다 — 프로젝트를 먼저 열거나 만드세요. "
                                 "(전역 설정 파일에 대신 저장하지 않습니다)")

    config = {k: v for k, v in config.items() if not str(k).startswith("_")}

    # 공통 키 제외 (공통은 common.json에서 관리)
    common = load_common_config()
    for key in common:
        if key in config:
            del config[key]

    active_path = get_active_path(project)
    os.makedirs(os.path.dirname(active_path), exist_ok=True)
    _backup_if_exists(active_path)
    with open(active_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


# =============================================================================
# 배포 스냅샷
# =============================================================================

# 세션(배포 폴더)의 신원을 정하는 키. 폴더명(<종목>_<봉>/<세션ID>)과 1:1 이라 일반 설정 저장으로 바뀌면 안 된다.
SESSION_IDENTITY_KEYS = ("SYMBOL_UPBIT", "SYMBOL_BINANCE", "INTERVAL", "ACTIVE_SESSION_ID")


def merge_into_session_snapshot(snapshot: dict, update: dict) -> dict:
    """세션 스냅샷에 설정 변경을 병합하되 세션의 신원 키(종목/봉/세션ID)는 절대 덮어쓰지 않는다.

    화면이 들고 있던 다른 프로젝트의 설정(예: SYMBOL_UPBIT=069500)이 일반 설정 저장을 통해 다른 세션의
    스냅샷으로 새어 들어가, TIGER/SOLAI 스냅샷이 KODEX 200 으로 기록되던 사고를 막는다."""
    merged = dict(snapshot)
    merged.update({k: v for k, v in update.items() if k not in SESSION_IDENTITY_KEYS})
    return merged


def create_deploy_snapshot(project: str, deploy_dir: str, config: dict) -> None:
    """
    배포용 불변 스냅샷 생성.
    공통 + 프로젝트 전체 설정을 결합한 스냅샷을 deploy_dir/config_snapshot.json에 저장.
    """
    config = dict(config)
    config = normalize_period_keys(config)
    config = normalize_fee_keys(config)
    os.makedirs(deploy_dir, exist_ok=True)
    snapshot_path = os.path.join(deploy_dir, "config_snapshot.json")
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def load_deploy_snapshot(deploy_dir: str) -> dict:
    """배포 스냅샷 로드 (불변 값)"""
    snapshot_path = os.path.join(deploy_dir, "config_snapshot.json")
    if not os.path.exists(snapshot_path):
        return {}
    try:
        with open(snapshot_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# =============================================================================
# 백업
# =============================================================================

def _backup_if_exists(path: str) -> None:
    """저장 전 기존 파일 백업 (timestamp 포함 .bak)"""
    if not os.path.exists(path):
        return
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{path}.{stamp}.bak"
        shutil.copy2(path, backup_path)
        # 오래된 백업 정리 (최근 5개만 유지)
        backup_dir = os.path.dirname(path)
        if backup_dir:
            backups = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith(os.path.basename(path) + ".") and f.endswith(".bak")],
                reverse=True
            )
            for old in backups[5:]:
                try:
                    os.remove(os.path.join(backup_dir, old))
                except Exception:
                    pass
    except Exception:
        pass


# =============================================================================
# 종목 마스터 원장 (Symbol Ledger) — symbols_registry.json
# =============================================================================
#
# 원장 설계 원칙 (이 저장소에서 원천 데이터/종목 신원(identity)의 유일한 정답 = 이 파일)
#   1. 키(PK)      : 거래소 표기 그대로의 심볼 문자열(리스팅 키). 예) 069500, KRW-BTC, BTCUSDT
#   2. asset_id    : 같은 경제적 실체(예: 비트코인)를 가리키는 리스팅들을 묶는 그룹 키.
#                    생략하면 심볼 자신. (KRW-BTC / BTCUSDT / BTC-USD / BTCUSDT-PERP -> "BTC")
#   3. 비용 파라미터: tick_size/default_fee/default_slippage/sell_tax 는 종목이 아니라 시장 정책
#                    (market_policies.DEFAULT_MARKET_POLICIES)에 속한다. 파일에는 정책과 다른
#                    값만 `overrides` 로 저장하고, 읽을 때 정책 + overrides 로 해석한다.
#                    (정책을 복사해 박제하면 정책이 바뀌어도 기존 종목이 따라가지 못한다)
#   4. tradable    : False 면 다른 타겟의 참조/매크로 프록시 전용(학습·매매 타겟 후보 아님).
#   5. catalog     : True 면 "검색 후보"(아직 시스템에 등록되지 않은 알려진 종목). 같은 파일에 두어
#                    원장을 하나로 유지하되, 기본 조회(load_symbols_registry)에서는 숨기고 검색과
#                    이름 조회에만 노출한다. register_symbol/ensure_symbol_registered 가 등록하면
#                    catalog 플래그가 해제된다.
#   6. role/default_price : 이 종목을 참조종목으로 추가할 때 미리 채워 주는 기본값(선택). 별칭(alias)은 없다 —
#                    참조 컬럼/피처 이름은 심볼에서 기계적으로 유도한다(ref_key/ref_prefix).
#   7. name_source : 이름의 출처. 새 종목의 이름은 코드에 박아 넣지 않고 데이터 원천/실시간 접속처에서
#                    찾아 온다(shared.symbol_name_resolver). "UPBIT"/"KIS"/"CREON"=원천 조회,
#                    "manual"=사람이 입력, "unresolved"=아무 원천도 몰라 임시로 코드를 이름으로 씀(점검 대상).
#   8. 이 파일은 반드시 이 모듈의 함수(load/save/get/register/ensure/search/...)로만 접근한다.
#      직접 json.load 금지 (shared/tests/test_symbol_ledger.py 가 강제).
#   9. tax_class/leverage : 일 단위 배분 엔진용(2026-10-10). tax_class 는 매매차익 과세 분류
#                    (shared/core/tax_rules.TAX_CLASSES), leverage 는 기초지수 대비 배수(인버스는 음수).
#                    기본값을 두지 않는다 — 비어 있으면 배분 엔진이 그 종목을 거부한다(조용한 비과세 가정 금지).

import logging as _logging

_ledger_log = _logging.getLogger("shared.symbol_ledger")

LEDGER_COST_FIELDS = ("tick_size", "default_fee", "default_slippage", "sell_tax")
_LEDGER_LISTING_DEFAULT_FIELDS = ("exchange", "base_currency")

_registry_cache = {"sig": None, "raw": None}


class SymbolInUseError(ValueError):
    """다른 대장(source_mappings/symbol_relations)이 참조 중인 종목을 삭제하려 할 때"""


def _policy_for(market_type) -> dict:
    from shared.config.market_policies import DEFAULT_MARKET_POLICIES
    return DEFAULT_MARKET_POLICIES.get(market_type or "", DEFAULT_MARKET_POLICIES["OTHER"])


def _num_eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-12
    except (TypeError, ValueError):
        return a == b


def _expand_entry(key: str, raw: dict) -> dict:
    """파일 저장형(compact) -> 소비자에게 보이는 flat 형태(정책/기본값 해석 완료)"""
    e = dict(raw)
    overrides = e.pop("overrides", None) or {}
    policy = _policy_for(e.get("market_type"))
    for f in LEDGER_COST_FIELDS:
        if f in overrides:
            e[f] = overrides[f]
        elif f not in e:
            e[f] = policy[f]
    for f in _LEDGER_LISTING_DEFAULT_FIELDS:
        e.setdefault(f, policy[f])
    e.setdefault("symbol", key)
    e.setdefault("asset_id", key)
    e.setdefault("tradable", True)
    e.setdefault("is_active", True)
    e.setdefault("catalog", False)
    e.setdefault("description", "")
    return e


def _compact_entry(entry: dict) -> dict:
    """flat 형태 -> 파일 저장형. 정책과 같은 비용값/기본값은 저장하지 않는다."""
    e = dict(entry)
    over = dict(e.pop("overrides", None) or {})
    for f in LEDGER_COST_FIELDS:
        if f in e:
            over[f] = e.pop(f)
    policy = _policy_for(e.get("market_type"))
    for f in list(over):
        if f not in LEDGER_COST_FIELDS or _num_eq(over[f], policy.get(f)):
            del over[f]
    e.pop("data_source", None)  # 폐기된 필드: 읽는 코드가 없었다 (실제 수집 출처는 source_mappings.primary_source)
    if e.get("asset_id") in (None, "", e.get("symbol")):
        e.pop("asset_id", None)
    if e.get("tradable") is True:
        e.pop("tradable", None)
    if not e.get("catalog"):
        e.pop("catalog", None)
    if over:
        e["overrides"] = {f: over[f] for f in LEDGER_COST_FIELDS if f in over}
    return e


def validate_symbols_registry(registry) -> list:
    """원장 구조 검증. 문제 목록(문자열)을 반환하며 비어 있으면 정상."""
    problems = []
    if not isinstance(registry, dict):
        return [f"원장 최상위는 dict 여야 합니다 (현재 {type(registry).__name__})"]
    for key, e in registry.items():
        if not isinstance(e, dict):
            problems.append(f"{key}: 엔트리가 dict 가 아닙니다")
            continue
        if key != str(key).strip().upper():
            problems.append(f"{key}: 키는 공백 없는 대문자여야 합니다")
        if str(e.get("symbol", key)).strip().upper() != key:
            problems.append(f"{key}: symbol 필드({e.get('symbol')})가 키와 다릅니다")
        if not str(e.get("name", "")).strip():
            problems.append(f"{key}: name 이 비어 있습니다")
        if not str(e.get("market_type", "")).strip():
            problems.append(f"{key}: market_type 이 비어 있습니다")
        ov = e.get("overrides")
        if ov is not None and (not isinstance(ov, dict) or set(ov) - set(LEDGER_COST_FIELDS)):
            problems.append(f"{key}: overrides 는 {LEDGER_COST_FIELDS} 만 가질 수 있습니다")
        for f in LEDGER_COST_FIELDS:
            v = e.get(f, (ov or {}).get(f) if isinstance(ov, dict) else None)
            if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0):
                problems.append(f"{key}: {f} 는 0 이상의 숫자여야 합니다 (현재 {v!r})")
        for f in ("is_active", "tradable", "catalog"):
            if f in e and not isinstance(e[f], bool):
                problems.append(f"{key}: {f} 는 bool 이어야 합니다")
        if "alias" in e:
            problems.append(f"{key}: alias 는 폐지되었습니다 — 참조 컬럼 이름은 심볼에서 유도합니다(ref_key)")
        for f in ("role", "name_source"):
            if f in e and not isinstance(e[f], str):
                problems.append(f"{key}: {f} 는 문자열이어야 합니다")
        dp = e.get("default_price")
        if dp is not None and (not isinstance(dp, (int, float)) or isinstance(dp, bool) or dp < 0):
            problems.append(f"{key}: default_price 는 0 이상의 숫자여야 합니다 (현재 {dp!r})")
        aid = e.get("asset_id")
        if aid is not None and (not isinstance(aid, str) or aid != aid.strip().upper() or not aid):
            problems.append(f"{key}: asset_id 는 공백 없는 대문자 문자열이어야 합니다 (현재 {aid!r})")
        und = e.get("underlying")
        if und is not None:
            # 개별주식선물: 기초자산은 원장의 주식이어야 한다. KIS 월물 코드는 매달 만기로 바뀌므로 박제하지 않는다
            # (실시간 구독 시 KIS 종목 마스터에서 최근월을 해석 — CTrade/utils/kis_futures_master.py)
            u_entry = registry.get(str(und).strip().upper()) if isinstance(und, str) else None
            if not isinstance(u_entry, dict) or u_entry.get("market_type") != "KRX_STOCK":
                problems.append(f"{key}: underlying({und!r}) 은 원장에 등록된 KRX_STOCK 이어야 합니다")
        tc = e.get("tax_class")
        if tc is not None:
            from shared.core.tax_rules import TAX_CLASSES
            if tc not in TAX_CLASSES:
                problems.append(f"{key}: tax_class({tc!r}) 는 {TAX_CLASSES} 중 하나여야 합니다")
        lev = e.get("leverage")
        if lev is not None and (not isinstance(lev, (int, float)) or isinstance(lev, bool) or lev == 0):
            problems.append(f"{key}: leverage 는 0 이 아닌 숫자여야 합니다 (기초지수 대비 배수, 인버스는 음수) (현재 {lev!r})")
        if e.get("market_type") == "KRX_FUTURES":
            # 국내 선물은 만기마다 KIS 월물 코드가 바뀐다(예: A01612 → A01703). 박제하면 다음 만기에 실시간이 조용히 끊긴다.
            if e.get("kis_symbol"):
                problems.append(f"{key}: 국내 선물은 kis_symbol(월물 코드)을 고정하지 않습니다 — "
                                f"underlying(주식선물) 또는 kis_futures_base(지수선물)로 KIS 마스터에서 최근월을 해석합니다")
            kb = e.get("kis_futures_base")
            if kb is not None and (not isinstance(kb, str) or not kb.strip()):
                problems.append(f"{key}: kis_futures_base 는 KIS 지수선물 마스터의 기초자산 코드 문자열이어야 합니다 (현재 {kb!r})")
    return problems


def _read_registry_raw() -> dict:
    """파일 저장형(compact) 원본을 mtime 캐시와 함께 읽는다. 호출자는 반환값을 수정하면 안 된다."""
    try:
        st = os.stat(SYMBOLS_REGISTRY_PATH)
    except OSError:
        return {}
    sig = (st.st_mtime_ns, st.st_size)
    if _registry_cache["sig"] == sig and _registry_cache["raw"] is not None:
        return _registry_cache["raw"]
    try:
        with open(SYMBOLS_REGISTRY_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        _ledger_log.error(f"symbols_registry.json 로드 실패: {e}")
        return {}
    if not isinstance(raw, dict):
        _ledger_log.error("symbols_registry.json 최상위가 dict 가 아닙니다")
        return {}
    _registry_cache["sig"], _registry_cache["raw"] = sig, raw
    return raw


def load_symbols_registry(include_catalog: bool = False) -> dict:
    """원장을 flat 형태(비용 파라미터/기본값 해석 완료)로 반환. 반환값은 호출자 소유 사본.

    기본은 시스템에 등록된 종목만 (catalog 검색 후보 제외). include_catalog=True 면 전부."""
    raw = _read_registry_raw()
    return {
        k: _expand_entry(k, v) for k, v in raw.items()
        if isinstance(v, dict) and (include_catalog or not v.get("catalog"))
    }


@_REGISTRY_LOCK
def _write_ledger(registry: dict) -> None:
    """원장 전체를 교체 저장 (검증 실패 시 저장하지 않음). flat/compact 어느 형태든 compact 로 정규화."""
    normalized = {}
    for k, v in (registry or {}).items():
        if not isinstance(v, dict):
            raise ValueError(f"원장 엔트리 형식 오류: {k}")
        normalized[str(k).strip().upper()] = {**v, "symbol": str(v.get("symbol", k)).strip().upper()}
    problems = validate_symbols_registry(normalized)
    if problems:
        raise ValueError("종목 원장 검증 실패: " + "; ".join(problems[:5]))
    compact = {k: _compact_entry(v) for k, v in normalized.items()}
    os.makedirs(os.path.dirname(SYMBOLS_REGISTRY_PATH), exist_ok=True)
    _backup_if_exists(SYMBOLS_REGISTRY_PATH)
    with open(SYMBOLS_REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(compact, f, indent=4, ensure_ascii=False)
    _registry_cache["sig"] = None


@_REGISTRY_LOCK
def save_symbols_registry(registry: dict) -> None:
    """등록된 종목 집합을 저장. load_symbols_registry() 결과를 수정해 되돌려 저장해도 되도록,
    넘기지 않은 catalog(검색 후보) 항목은 보존한다."""
    merged = dict(registry or {})
    for k, v in _read_registry_raw().items():
        if isinstance(v, dict) and v.get("catalog") and str(k).strip().upper() not in {str(x).strip().upper() for x in merged}:
            merged[k] = v
    _write_ledger(merged)


def get_symbol_name(symbol: str):
    """원장(등록 종목 + 검색 후보)에 실제로 있는 종목명만 반환. 없으면 None (이름을 지어내지 않음)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    info = _read_registry_raw().get(sym)
    if isinstance(info, dict) and info.get("name"):
        return info["name"]
    return None


# 다른 대장(source_mappings/symbol_relations)이 복사 저장하면 안 되는, 원장이 소유한 종목 속성.
# 대장은 저장하지 않고 로드할 때 원장에서 채운다 (사본이 어긋나 드리프트하는 것을 구조적으로 막는다).
LEDGER_OWNED_FIELDS = ("name", "market_type", "description")


def get_ledger_fields(symbol: str, fields=LEDGER_OWNED_FIELDS) -> dict:
    """원장(검색 후보 포함)이 아는 종목 속성 중 값이 있는 것만 {필드: 값} 으로 반환. 미등록이면 {}."""
    info = _read_registry_raw().get(str(symbol or "").strip().upper())
    if not isinstance(info, dict):
        return {}
    return {f: info[f] for f in fields if info.get(f) not in (None, "")}


def resolve_macro_symbol(key: str):
    """macro 시계열 키 -> 등록된 원장 심볼 키(대문자). 원장이 모르면 None.
    macro 시계열 파일은 이 키(10100, U180, NQ, ES)로 이름 짓는다. 별칭은 없다."""
    k = str(key or "").strip().upper()
    info = _read_registry_raw().get(k)
    return k if isinstance(info, dict) and not info.get("catalog") else None


def strip_creon_prefix(symbol: str) -> str:
    """심볼 앞의 크레온 A 접두사만 정밀 제거 (AAPL, JAPAN_225 등 일반 영문 티커는 훼손하지 않는다). 대문자로 정규화."""
    s = str(symbol or "").strip().upper()
    if s.startswith("A") and len(s) == 7 and s[1:].isalnum():
        return s[1:]
    return s


# ── 참조 종목의 컬럼 이름: 원장 심볼에서 기계적으로 유도한다 (별칭 없음) ───────────────────────────
# 학습(Gold 스냅샷/피처 엔진)과 서빙(CTrade)이 같은 컬럼 계약을 쓰려면 이름을 만드는 곳이 하나여야 한다.
# `ref_{key}_*` 컬럼과 `ref_{key}_{kind}` 피처 id 는 반드시 이 함수들로만 조립한다
# (shared/tests/test_ref_naming_single_source.py 가 다른 곳의 조립을 막는다).
def ref_key(symbol: str) -> str:
    """원장 심볼 -> 컬럼 키: 크레온 A 접두 제거 + 소문자 + 하이픈은 밑줄 (미국 CFD 코드는 소문자 밑줄 형태가 된다)."""
    return strip_creon_prefix(symbol).lower().replace("-", "_")


def ref_prefix(symbol: str) -> str:
    """참조 종목 컬럼 접두어 'ref_{key}_' (예: ref_000660_ -> ref_000660_close)"""
    return f"ref_{ref_key(symbol)}_"


def symbol_from_ref_key(key: str):
    """컬럼 키 -> 원장 심볼 (등록된 종목만). 원장이 모르는 키면 None. 피처 표시명/시장 유형 조회용."""
    k = str(key or "").strip().lower()
    if not k:
        return None
    for sym, v in _read_registry_raw().items():
        if isinstance(v, dict) and not v.get("catalog") and ref_key(sym) == k:
            return sym
    return None


def find_ref_columns(columns, field: str = "close", market_types=None, require_field=None, exclude_field=None) -> list:
    """DataFrame 컬럼 중 참조 종목의 `ref_{key}_{field}` 컬럼을 키 정렬순으로 돌려준다.
    market_types(원장 market_type 접두어 튜플, 예: ("KRX_FUTURES",))를 주면 그 유형의 종목 컬럼만 남긴다.
    require_field/exclude_field: 원장 항목에 그 필드가 있는(없는) 종목만 남긴다 (예: 개별주식선물은 `underlying` 보유).
    피처가 컬럼 '이름'(별칭)이 아니라 원장이 말해주는 종목 '유형'으로 입력을 찾게 하기 위한 함수."""
    raw = _read_registry_raw()
    by_key = {ref_key(sym): sym for sym, v in raw.items() if isinstance(v, dict) and not v.get("catalog")}
    suffix = f"_{field}"
    found = []
    for col in columns:
        col = str(col)
        if not (col.startswith("ref_") and col.endswith(suffix)):
            continue
        sym = by_key.get(col[4:-len(suffix)])
        if sym is None:
            # 원장이 모르는 키는 참조 종목 컬럼이 아니다. 역할 별칭 투영(ref_{key}_target_close 등)이
            # 여기서 걸러지지 않으면 같은 종목이 여러 번 잡혀 하위 계산이 중복된다.
            continue
        if market_types is not None:
            mt = str((raw.get(sym) or {}).get("market_type", "")) if sym else ""
            if not any(mt.startswith(m) for m in market_types):
                continue
        entry = raw.get(sym) or {}
        if require_field and not entry.get(require_field):
            continue
        if exclude_field and entry.get(exclude_field):
            continue
        found.append(col)
    return sorted(found)


def get_proxy_of(symbol: str):
    """CFD 대용 종목이 대신하는 실물 종목의 원장 키 (예: US_TECH_100 -> NQ). 대용이 아니면 None."""
    info = _read_registry_raw().get(str(symbol or "").strip().upper())
    val = info.get("proxy_of") if isinstance(info, dict) else None
    return str(val).strip().upper() if val else None


def get_proxy_for(symbol: str):
    """실물 종목을 대신할 대용 종목의 원장 키 (예: NQ -> US_TECH_100). 대용이 없으면 None.

    `get_proxy_of` 의 역방향이다. 대용 관계의 정의는 원장의 `proxy_of` 한 곳뿐이며,
    어떤 코드도 {실물: 대용} 매핑을 따로 들고 있어서는 안 된다.
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    raw = _read_registry_raw()
    for key in sorted(raw):
        info = raw.get(key)
        if not isinstance(info, dict) or info.get("catalog"):
            continue
        val = info.get("proxy_of")
        if val and str(val).strip().upper() == sym:
            return str(key).strip().upper()
    return None


def is_symbol_registered(symbol: str) -> bool:
    """시스템에 실제로 등록되어 있는지 (catalog 검색 후보는 등록으로 치지 않음)"""
    info = _read_registry_raw().get(str(symbol or "").strip().upper())
    return isinstance(info, dict) and not info.get("catalog")


def get_symbol_display_name(symbol: str) -> str:
    """'종목명 (코드)' 표기. 미등록이면 코드 그대로."""
    sym = str(symbol or "").strip()
    if not sym:
        return "N/A"
    name = get_symbol_name(sym)
    return f"{name} ({sym})" if name else sym


def get_asset_listings(asset_id: str) -> list:
    """같은 asset_id 를 공유하는 모든 리스팅(거래소별 심볼)을 반환."""
    aid = str(asset_id or "").strip().upper()
    return [e for e in load_symbols_registry().values() if e.get("asset_id") == aid]


def get_asset_id(symbol: str) -> str:
    """리스팅 키 -> 자산 그룹 키 (KRW-BTC / BTCUSDT / BTC-USD -> 'BTC').
    등록되지 않았거나 asset_id 가 없으면 심볼 자신. 이미 asset_id 자체(BTC)를 넘겨도 그대로 돌려준다."""
    sym = str(symbol or "").strip().upper()
    info = _read_registry_raw().get(sym)
    if isinstance(info, dict) and info.get("asset_id"):
        return str(info["asset_id"]).strip().upper()
    return sym


_SCOPE_BY_MARKET_TYPE = (("CRYPTO", "crypto"), ("US_", "us"), ("KRX", "krx"))


def get_symbol_scope(symbol: str):
    """원장 기준 자산군: 'crypto' | 'us' | 'krx' | 'other'(등록된 매크로/해외선물/CFD 등) | None(미등록).

    리스팅 키로 먼저 찾고, 없으면 asset_id(예: 'BTC', 'XRP')로 등록된 리스팅을 찾는다.
    검색 후보(catalog)는 등록으로 치지 않는다."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    raw = _read_registry_raw()
    info = raw.get(sym)
    if not isinstance(info, dict) or info.get("catalog"):
        info = next((v for k, v in raw.items()
                     if isinstance(v, dict) and not v.get("catalog") and str(v.get("asset_id") or k).upper() == sym), None)
    if not isinstance(info, dict):
        return None
    mtype = str(info.get("market_type", "")).upper()
    for token, scope in _SCOPE_BY_MARKET_TYPE:
        if token in mtype:
            return scope
    return "other"


def get_symbol_info(symbol: str) -> dict:
    """특정 종목의 메타데이터 조회. 원장 -> (최후) 시장 표준 정책 기반 임시값(저장되지 않음)"""
    from shared.config.market_policies import DEFAULT_MARKET_POLICIES

    sym_str = str(symbol).strip().upper()
    raw = _read_registry_raw()
    for key in (sym_str, str(symbol).strip()):
        if isinstance(raw.get(key), dict):
            return _expand_entry(key, raw[key])

    _ledger_log.warning(f"[SymbolLedger] 원장에 없는 심볼 '{sym_str}' — 임시 기본값 사용")
    # 미등록 시 심볼 패턴 기반 시장 유형 판별 (임시값, 저장되지 않음)
    is_krx = sym_str.isdigit() and len(sym_str) == 6
    is_crypto = sym_str.startswith("KRW-") or sym_str.startswith("BTC-") or sym_str.startswith("USDT-")

    if is_krx:
        market_type = "KRX_STOCK"
        binance_symbol = ""
    elif is_crypto:
        market_type = "CRYPTO"
        coin = sym_str.split("-")[-1]
        binance_symbol = f"{coin}USDT"
    else:
        market_type = "US_ETF"
        binance_symbol = ""

    policy = DEFAULT_MARKET_POLICIES.get(market_type, DEFAULT_MARKET_POLICIES["OTHER"])

    return {
        "symbol": sym_str,
        "name": sym_str,
        "market_type": market_type,
        "exchange": policy["exchange"],
        "binance_symbol": binance_symbol,
        "base_currency": policy["base_currency"],
        "tick_size": policy["tick_size"],
        "default_fee": policy["default_fee"],
        "default_slippage": policy["default_slippage"],
        "sell_tax": policy["sell_tax"],
        "is_active": True,
        "description": ""
    }


def _resolve_name_for(symbol: str, market_type: str) -> dict:
    """원천 조회로 이름 결정. 못 찾으면 {} (호출자가 처리)."""
    from shared.data_library.symbol_name_resolver import resolve_symbol_name
    return resolve_symbol_name(symbol, market_type) or {}


def register_symbol(symbol_data: dict) -> dict:
    """신규 종목 등록 또는 기존 종목 정보 갱신 (시장 표준 정책 상속 및 사용자 커스텀 보존).

    이름 결정 순서: 명시 입력(manual) > 이미 있는 이름 유지 > 데이터 원천/실시간 접속처 조회 >
    (모두 실패) 심볼 코드를 임시 이름으로 쓰고 name_source="unresolved" 로 표시.
    catalog(검색 후보)였던 종목은 등록되면서 catalog 플래그가 해제된다."""
    data = dict(symbol_data)
    symbol = str(data.get("symbol", "")).strip().upper()
    if not symbol:
        raise ValueError("심볼 코드는 필수입니다.")

    existing = _read_registry_raw().get(symbol) or {}
    explicit = str(data.get("name") or "").strip()
    if explicit:
        if "name_source" not in data and explicit != existing.get("name"):
            data["name_source"] = "manual"
    elif not str(existing.get("name") or "").strip():
        # 네트워크 조회는 원장 파일 락 밖에서 수행한다
        found = _resolve_name_for(symbol, str(data.get("market_type") or existing.get("market_type") or ""))
        if found:
            data["name"], data["name_source"] = found["name"], found["source"]
        else:
            _ledger_log.warning(f"[SymbolLedger] '{symbol}' 이름을 어떤 원천에서도 찾지 못해 코드를 임시 이름으로 씁니다")
            data["name"], data["name_source"] = symbol, "unresolved"
    return _register_locked(data)


@_REGISTRY_LOCK
def _register_locked(symbol_data: dict) -> dict:
    symbol = str(symbol_data.get("symbol", "")).strip().upper()
    registry = load_symbols_registry(include_catalog=True)
    # 기존 값은 compact 형태(정책과 다른 값만)로 접어서 병합해야 market_type 이 바뀔 때
    # 옛 시장 정책값이 새 종목의 override 로 눌어붙지 않는다.
    seed = registry.get(symbol) or {}
    merged = {**_compact_entry(seed), **symbol_data, "symbol": symbol}
    if "catalog" not in symbol_data:
        merged.pop("catalog", None)
    merged.setdefault("market_type", "KRX_STOCK")
    merged.setdefault("name", symbol)
    registry[symbol] = merged
    _write_ledger(registry)
    return _expand_entry(symbol, _compact_entry(merged))


def ensure_symbol_registered(symbol: str, name: str = "", market_type: str = "", **fields) -> dict:
    """등록되어 있지 않으면 등록하고, 이미 등록되어 있으면 절대 덮어쓰지 않는다.
    catalog(검색 후보)에 있던 종목은 기존 정보 그대로 등록(승격)한다.

    새 종목의 이름은 데이터 원천/실시간 접속처에서 먼저 찾고, 못 찾을 때만 호출자가 넘긴 name 을
    힌트로 쓴다 (호출자 이름을 그대로 믿고 복사하면 하드코딩 이름이 원장으로 번진다).

    다른 대장(source_mappings/symbol_relations)에 새 심볼이 들어갈 때 호출해서
    '대장에는 있는데 원장에는 없는' 상태가 생기지 않게 한다.
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise ValueError("심볼 코드는 필수입니다.")
    existing = _read_registry_raw().get(sym)
    if isinstance(existing, dict):
        if existing.get("catalog"):
            return register_symbol({"symbol": sym})
        return _expand_entry(sym, existing)

    seed = {k: v for k, v in fields.items() if v is not None}
    seed.update({"symbol": sym, "market_type": market_type or fields.get("market_type") or "OTHER"})
    found = _resolve_name_for(sym, seed["market_type"])
    hint = str(name or "").strip()
    if found:
        seed["name"], seed["name_source"] = found["name"], found["source"]
    elif hint:
        seed["name"], seed["name_source"] = hint, "manual"
    else:
        _ledger_log.warning(f"[SymbolLedger] '{sym}' 이름을 어떤 원천에서도 찾지 못해 코드를 임시 이름으로 씁니다")
        seed["name"], seed["name_source"] = sym, "unresolved"
    return register_symbol(seed)


class _UsageProbe:
    """delete_symbol 이 다른 대장의 참조 여부를 확인하기 위한 지연 import 래퍼(순환 import 방지)"""

    @staticmethod
    def find(symbol: str) -> list:
        usages = []
        try:
            from shared.data_library.symbol_relations_manager import SymbolRelationsManager
            for hit in SymbolRelationsManager.find_referencing_targets(symbol):
                usages.append(f"symbol_relations({hit['target_symbol']}:{hit['relation']})")
        except Exception as e:
            _ledger_log.warning(f"symbol_relations 참조 확인 실패: {e}")
        try:
            from shared.data_library.source_mapping_manager import SourceMappingManager
            clean = SourceMappingManager.clean_symbol(symbol)
            if clean in SourceMappingManager.load_all_mappings():
                usages.append("source_mappings")
        except Exception as e:
            _ledger_log.warning(f"source_mappings 참조 확인 실패: {e}")
        return usages


@_REGISTRY_LOCK
def _delete_symbol_locked(sym_str: str) -> bool:
    registry = load_symbols_registry(include_catalog=True)
    if sym_str not in registry:
        return False
    del registry[sym_str]
    _write_ledger(registry)
    return True


def delete_symbol(symbol: str, force: bool = False) -> bool:
    """종목 삭제. 다른 대장이 쓰고 있으면 SymbolInUseError (force=True 면 강행).

    사용처 확인은 원장 락을 잡기 전에 한다 (원장 락 -> 대장 락 순서 역전으로 인한 교착 방지).
    """
    sym_str = str(symbol).strip().upper()
    if not force:
        usages = _UsageProbe.find(sym_str)
        if usages:
            raise SymbolInUseError(f"{sym_str} 는 다른 대장에서 사용 중이라 원장에서 삭제할 수 없습니다: {', '.join(usages)}")
    return _delete_symbol_locked(sym_str)


def search_symbols(query: str, limit: int = 15) -> list:
    """원장(등록 종목 + 검색 후보) 통합 종목 검색: 한글명, 심볼 코드, 영문 티커, 설명.

    공백 무시 / 다중 토큰 AND / 부분 일치. 등록된 종목이 같은 순위 안에서 먼저 나온다."""
    entries = load_symbols_registry(include_catalog=True)
    items = sorted(entries.values(), key=lambda e: bool(e.get("catalog")))  # 안정 정렬: 등록 종목 우선

    if not query or not query.strip():
        return items[:limit]

    raw_q = query.strip().upper()
    clean_q = raw_q.replace(" ", "")
    tokens = [t for t in raw_q.split() if t]

    results = []
    seen = set()

    def add_item(item):
        if item["symbol"] not in seen:
            seen.add(item["symbol"])
            results.append(item)

    def text_of(item):
        return f"{item['symbol']} {item['name']} {item.get('description', '')}".upper()

    # 1. 완전 일치
    for item in items:
        sym, name = item["symbol"].upper(), item["name"].upper()
        if sym == raw_q or name == raw_q or sym.replace("KRW-", "") == raw_q:
            add_item(item)
    # 2. 공백 제거 후 완전 일치 또는 전방 일치
    for item in items:
        sym, name_clean = item["symbol"].upper(), item["name"].upper().replace(" ", "")
        if name_clean == clean_q or name_clean.startswith(clean_q) or sym.startswith(clean_q):
            add_item(item)
    # 3. 공백 제거 후 부분 포함 (예: 'KODEX미국나스닥100' in 'KODEX미국나스닥100TR')
    for item in items:
        name_clean = item["name"].upper().replace(" ", "")
        desc_clean = item.get("description", "").upper().replace(" ", "")
        if clean_q in name_clean or clean_q in desc_clean or clean_q in item["symbol"].upper():
            add_item(item)
    # 4. 다중 토큰 AND 검색 (예: ['KODEX', '나스닥'])
    if len(tokens) > 1:
        for item in items:
            full_text = text_of(item)
            if all(t in full_text for t in tokens):
                add_item(item)
    # 5. 개별 토큰 부분 검색
    for item in items:
        full_text = text_of(item)
        if any(t in full_text for t in tokens):
            add_item(item)

    return results[:limit]
