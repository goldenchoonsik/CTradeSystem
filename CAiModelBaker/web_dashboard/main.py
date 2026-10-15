"""
CAiModelBaker - Web Dashboard
FastAPI + Jinja2 + Plotly + WebSocket 기반 퀀트 대시보드
[Features Updated: semi_duo_lead_delta]
"""
import os
import sys
import time
import json
import shutil
import asyncio
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import re
import logging

logger = logging.getLogger(__name__)

# 프로젝트 루트를 path에 추가하여 core 모듈 임포트 가능하게 함
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BAKER_ROOT = _PROJECT_ROOT
sys.path.insert(0, _PROJECT_ROOT)

# 공통 설정 관리 모듈 로드
_CTRADE_SYSTEM_ROOT = os.path.dirname(_PROJECT_ROOT)
if _CTRADE_SYSTEM_ROOT not in sys.path:
    sys.path.append(_CTRADE_SYSTEM_ROOT)
if _PROJECT_ROOT in sys.path:
    sys.path.remove(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)
from shared.config.config_manager import (
    load_project_config, save_project_config,
    load_symbols_registry, save_symbols_registry,
    get_symbol_info, register_symbol, delete_symbol, SymbolInUseError,
    search_symbols, normalize_fee_keys, merge_into_session_snapshot, ref_key,
    get_active_project_ref, set_active_project_ref, load_base_config,
)
from shared.core.market_utils import get_target_symbol
from shared.core.trade_costs import round_trip_cost, resolve_sell_tax
from shared.config import project_store
from shared.config.market_policies import crypto_spot_default_interval, DEFAULT_MARKET_POLICIES

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, BackgroundTasks, Body
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

# ── 경로 세그먼트 검증 (deploy/{market_dir}/{session_id} 등 사용자 입력이 경로에 들어가기 전 필수) ──
_SAFE_SEG_RE = re.compile(r'^\w[\w.\-]*$')

def _is_safe_seg(*values) -> bool:
    """None/빈 값은 통과, 값이 있으면 '/', '\\', '..', 선행 '.' 등 경로 이탈 문자가 없어야 True."""
    for v in values:
        if v is None or v == "":
            continue
        if not isinstance(v, str) or not _SAFE_SEG_RE.match(v) or ".." in v:
            return False
    return True

def _bad_path_response():
    return JSONResponse(status_code=400, content={"status": "error", "error": "허용되지 않는 경로 문자가 포함되어 있습니다."})

# Jinja2 직접 사용 (Starlette wrapper 대신 - 호환성 이슈 해결)
from jinja2 import Environment, FileSystemLoader

# Core 모듈 임포트 (모노레포 네임스페이스 충돌 방지)
try:
    from CAiModelBaker.core.data_manager import (
        get_snapshot_path, get_params_path, get_model_path,
        run_archive_download_pipeline, run_dataset_build_pipeline,
        validate_downloaded_data, run_stock_download_pipeline
    )
    from CAiModelBaker.core.model_engine import process_02_relay_optimization, run_feature_engineering, confirm_and_deploy_strategy
    from CAiModelBaker.core.backtester import process_03_pure_backtest
    from CAiModelBaker.core.feature_registry import (
        load_feature_config, save_feature_config,
        get_feature_list_with_params, DEFAULT_FEATURE_REGISTRY,
        DEFAULT_OPTUNA_CONFIG, CATEGORY_INFO
    )
    from CAiModelBaker.core.feature_tuner import run_pre_tuning_features, run_auto_feature_selection
    from CAiModelBaker.core.feature_screener import run_full_period_feature_screening
    from CAiModelBaker.core.research_loop import (
        start_research_in_thread, abort_research, get_research_status,
        reset_research_state, extract_feature_importance_from_model_file
    )
except (ImportError, AttributeError):
    from core.data_manager import (
        get_snapshot_path, get_params_path, get_model_path,
        run_archive_download_pipeline, run_dataset_build_pipeline,
        validate_downloaded_data, run_stock_download_pipeline
    )
    from core.model_engine import process_02_relay_optimization, run_feature_engineering, confirm_and_deploy_strategy
    from core.backtester import process_03_pure_backtest
    from core.feature_registry import (
        load_feature_config, save_feature_config,
        get_feature_list_with_params, DEFAULT_FEATURE_REGISTRY,
        DEFAULT_OPTUNA_CONFIG, CATEGORY_INFO
    )
    from core.feature_tuner import run_pre_tuning_features, run_auto_feature_selection
    from core.feature_screener import run_full_period_feature_screening
    from core.research_loop import (
        start_research_in_thread, abort_research, get_research_status,
        reset_research_state, extract_feature_importance_from_model_file
    )
from shared.features.feature_registry import (
    register_reference_features,
    parse_reference_feature_id,
    get_incompatible_features,
    filter_compatible_features,
    detect_symbol_market_scope,
    validate_feature_registry
)

# ---------------------------------------------------------------------------
# FastAPI 앱 생성
# ---------------------------------------------------------------------------
app = FastAPI(title="CAiModelBaker Web Dashboard", version="6.6")


# 열린 프로젝트가 없거나 포인터가 깨졌을 때 — 전역 설정으로 대신 처리하지 않고 화면에 이유를 돌려준다.
# 화면(apiGet/apiPost)은 code 를 보고 '프로젝트를 열거나 만드세요' 안내를 띄운다.
from shared.config.project_store import NoOpenProjectError, BrokenProjectPointerError  # noqa: E402


@app.exception_handler(NoOpenProjectError)
async def _no_open_project_handler(request: Request, exc: NoOpenProjectError):
    return JSONResponse(status_code=409, content={"status": "error", "code": "PROJECT_NOT_OPEN", "error": str(exc)})


@app.exception_handler(BrokenProjectPointerError)
async def _broken_pointer_handler(request: Request, exc: BrokenProjectPointerError):
    return JSONResponse(status_code=409, content={"status": "error", "code": "PROJECT_POINTER_BROKEN", "error": str(exc)})

# Static & Templates 설정
BASE_DIR = Path(__file__).parent
STATIC_DIR = str(BASE_DIR / "static")
TEMPLATE_DIR = str(BASE_DIR / "templates")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Jinja2 환경 직접 초기화
jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=True
)

def render_template(name, **context):
    """Jinja2 템플릿 렌더링 헬퍼"""
    template = jinja_env.get_template(name)
    return HTMLResponse(template.render(**context))

# ---------------------------------------------------------------------------
# 글로벌 설정 — config_manager 기반 (CAiModelBaker 전용)
# ---------------------------------------------------------------------------
PROJECT_NAME = "CAiModelBaker"

def load_config():
    """공통 기본값 + CAiModelBaker 기본값 + 활성 설정 병합 (수수료/슬리피지 단일화 보장)"""
    config = load_project_config(PROJECT_NAME)
    config = normalize_fee_keys(config)
    return config

def save_config(config):
    """CAiModelBaker 활성 설정 저장 + 공통 키 동기화 (수수료/슬리피지/마스터 수집 기간 단일화 보장)"""
    from shared.config.config_manager import save_common_config
    config = normalize_fee_keys(config)
    # 프로젝트 저장이 먼저다 — 열린 프로젝트가 없어 거부되면 공통 설정도 건드리지 않는다.
    save_project_config(PROJECT_NAME, config)
    # 종목·인터벌은 프로젝트 신원(폴더 경로)이다. 공통 설정(common.json)에 쓰면 다른 화면·프로세스가
    # '현재 종목' 으로 오해한다 (2026-10-01: common.json 의 INTERVAL 이 마지막 저장 프로젝트의 10s 로 바뀌어 있었다).
    common_keys = ['FEE_RATE', 'FEE_UPBIT', 'SLIPPAGE', 'ARCHIVE_START_YM', 'ARCHIVE_END_YM']
    common_updates = {k: config[k] for k in common_keys if k in config}
    if common_updates:
        save_common_config(common_updates)

# ---------------------------------------------------------------------------
# WebSocket 연결 관리
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)

manager = ConnectionManager()

# ---------------------------------------------------------------------------
# 웹소켓 엔드포인트 (실시간 로그)
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ---------------------------------------------------------------------------
# 로그 함수 (WebSocket + stdout)
# ---------------------------------------------------------------------------
def get_log_fn(prefix=""):
    async def log_fn(msg):
        print(f"[{prefix}] {msg}")
        await manager.broadcast(json.dumps({
            "type": "log",
            "message": msg,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }, ensure_ascii=False))
    return log_fn

# ---------------------------------------------------------------------------
# HTTP 라우트
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    config = load_config()
    return render_template("index.html", request=request, config=config)

# ---------------------------------------------------------------------------
# 종목 관리 (Symbol Registry) API
# ---------------------------------------------------------------------------
@app.get("/api/symbols")
async def api_get_symbols():
    """등록된 전체 종목 목록 및 카테고리별 분류 반환"""
    registry = load_symbols_registry()
    
    # 카테고리별 그룹화
    categories = {
        "KRX_ETF": {"label": "국내 ETF", "symbols": []},
        "KRX_STOCK": {"label": "국내 주식", "symbols": []},
        "US_ETF": {"label": "해외 ETF", "symbols": []},
        "CRYPTO": {"label": "가상자산 (업비트)", "symbols": []},
        "OTHER": {"label": "기타", "symbols": []}
    }
    
    for sym_code, sym_info in registry.items():
        mtype = sym_info.get("market_type", "OTHER")
        if mtype not in categories:
            categories[mtype] = {"label": mtype, "symbols": []}
        categories[mtype]["symbols"].append(sym_info)
        
    return {
        "status": "ok",
        "symbols": registry,
        "categories": categories,
        "count": len(registry),
        "market_policies": DEFAULT_MARKET_POLICIES,
    }

@app.post("/api/symbols")
async def api_register_symbol(data: dict):
    """신규 종목 등록 또는 갱신"""
    symbol = str(data.get("symbol", "")).strip().upper()
    if not symbol:
        return JSONResponse(status_code=400, content={"status": "error", "message": "심볼 코드는 필수입니다."})
    
    # KRW-로 시작하지 않는 암호화폐 처리 또는 주식 코드 정규화
    if symbol.startswith("KRW-"):
        pass
    elif symbol.isdigit() and len(symbol) == 6:
        pass # 한국 표준 6자리 코드
    
    try:
        updated = register_symbol(data)
        return {"status": "ok", "symbol": updated}
    except Exception as ex:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(ex)})

@app.put("/api/symbols/{symbol}")
async def api_update_symbol(symbol: str, data: dict):
    """종목 정보 수정"""
    data["symbol"] = symbol
    try:
        updated = register_symbol(data)
        return {"status": "ok", "symbol": updated}
    except Exception as ex:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(ex)})

@app.delete("/api/symbols/{symbol}")
async def api_delete_symbol(symbol: str):
    """종목 삭제 (다른 대장이 사용 중인 종목은 409로 차단)"""
    try:
        deleted = delete_symbol(symbol)
    except SymbolInUseError as ex:
        return JSONResponse(status_code=409, content={"status": "error", "message": str(ex)})
    if deleted:
        return {"status": "ok", "message": f"{symbol} 종목이 삭제되었습니다."}
    return JSONResponse(status_code=404, content={"status": "error", "message": f"{symbol} 종목을 찾을 수 없습니다."})

@app.get("/api/symbols/lookup")
async def api_lookup_symbol(symbol: str):
    """심볼 코드로 종목 기본 메타데이터 추론 및 조회"""
    sym = symbol.strip().upper()
    info = get_symbol_info(sym)
    return {"status": "ok", "info": info}

@app.get("/api/symbols/search")
async def api_search_symbols(q: str = "", limit: int = 15, target: str = ""):
    """실시간 종목 검색 (코드, 한글명, 티커) + DataVault 수집 메타데이터 주입"""
    import os
    from shared.data_library import paths as dpaths
    from shared.data_library.source_mapping_manager import SourceMappingManager
    from shared.data_library.symbol_relations_manager import SymbolRelationsManager

    results = search_symbols(q, limit=limit)

    attached_symbols = set()
    if target:
        clean_t = SourceMappingManager.clean_symbol(target)
        attached_symbols = {r["symbol"] for r in SymbolRelationsManager.get_relations_for_target(clean_t)}

    # 각 항목에 DataVault 수집 여부 및 보유 스케일 동적 주입
    candidate_scales = ("10s", "1m", "5m", "15m", "60m", "1d")
    for item in results:
        clean_s = SourceMappingManager.clean_symbol(item["symbol"])
        found_scales = []
        for sc in candidate_scales:
            p = dpaths.normalized_path(f"candles_{sc}", f"{clean_s}_{sc}.parquet")
            pm = dpaths.macro_normalized_path(clean_s, sc)
            if (p and os.path.exists(p)) or (pm and os.path.exists(pm)):
                found_scales.append(sc)
        item["has_vault_data"] = len(found_scales) > 0
        item["collected_scales"] = found_scales
        item["is_attached"] = clean_s in attached_symbols

    return {
        "status": "ok",
        "query": q,
        "results": results,
        "count": len(results)
    }

@app.get("/api/symbols/available_vault_references")
async def api_available_vault_references(target: str = ""):
    """DataVault에 실제 수집된 자산 중 현재 타겟에 미등록된 가용 참조 자산 및 전체 수집 자산 조회"""
    from shared.data_library.symbol_relations_manager import SymbolRelationsManager
    clean_target = target.strip().upper() if target else str(get_target_symbol(load_config()) or "").upper()
    data = SymbolRelationsManager.get_available_vault_references(clean_target)
    return {
        "status": "ok",
        **data
    }

@app.get("/api/symbols/resolve_name")
async def api_resolve_symbol_name(symbol: str, market_type: str = ""):
    """데이터 원천/실시간 접속처(KIS, CREON, 업비트)에서 종목명을 찾아 돌려준다 (등록 폼 자동 채움용)"""
    from shared.data_library.symbol_name_resolver import resolve_symbol_name
    found = await asyncio.get_event_loop().run_in_executor(None, resolve_symbol_name, symbol, market_type)
    if not found:
        return {"status": "not_found", "symbol": symbol.strip().upper(),
                "message": "어떤 원천에서도 종목명을 찾지 못했습니다. 직접 입력해 주세요."}
    return {"status": "ok", "symbol": symbol.strip().upper(), **found}


@app.get("/api/symbols/ledger_overview")
async def api_symbol_ledger_overview():
    """종목 마스터 원장 전체 보기 (원장 + 수집 대장/참조 관계/CTrade 설정 사용처, 읽기 전용)"""
    from shared.data_library.symbol_overview import build_ledger_overview
    return {"status": "ok", **build_ledger_overview()}


@app.get("/api/symbols/name_audit")
async def api_symbol_name_audit():
    """원장에 등록된 종목 이름을 원천 조회 결과와 대조 (보고 전용, 이름은 바꾸지 않음)"""
    from shared.data_library.symbol_name_resolver import audit_symbol_names
    report = await asyncio.get_event_loop().run_in_executor(None, audit_symbol_names, load_symbols_registry())
    return {"status": "ok", **{k: v for k, v in report.items()}, "counts": {k: len(v) for k, v in report.items()}}


@app.get("/api/physics-constraints")
async def api_get_physics_constraints():
    """현재 활성 설정과 스냅샷 데이터 기반 물리적 선험 제약 조건 산출 (실측 표본 MFE/MAE 분위수 연동)"""
    config = load_config()
    try:
        from CAiModelBaker.core.physics_constraint import calc_friction_floor, calc_empirical_barrier_bounds, calc_mean_atr_pct
        from CAiModelBaker.core.data_manager import get_snapshot_path
        from shared.core.market_utils import detect_market_type
    except (ImportError, ValueError):
        try:
            from core.physics_constraint import calc_friction_floor, calc_empirical_barrier_bounds, calc_mean_atr_pct
            from core.data_manager import get_snapshot_path
            from shared.core.market_utils import detect_market_type
        except (ImportError, ValueError):
            from ..core.physics_constraint import calc_friction_floor, calc_empirical_barrier_bounds, calc_mean_atr_pct
            from ..core.data_manager import get_snapshot_path
            from shared.core.market_utils import detect_market_type

    mean_atr = 0.0020
    df = None
    try:
        snapshot_path = get_snapshot_path(config)
        full_path = os.path.join(_BAKER_ROOT, snapshot_path) if not os.path.isabs(snapshot_path) else snapshot_path
        if os.path.exists(full_path):
            df = pd.read_csv(full_path)
            mean_atr = calc_mean_atr_pct(df)
    except Exception:
        pass

    fee = float(config.get("OPTUNA_CONFIG", {}).get("fee_upbit", config.get("fee_upbit", 0.0005)))
    slippage = float(config.get("OPTUNA_CONFIG", {}).get("slippage", config.get("slippage", 0.0006)))
    sell_tax = resolve_sell_tax(config)
    m_type = detect_market_type(config)
    intv = config.get("INTERVAL", "10m")

    if df is not None and len(df) >= 100:
        physics = calc_empirical_barrier_bounds(
            df=df,
            fee_rate=fee,
            slippage_rate=slippage,
            interval=intv,
            market_type=m_type,
            mean_atr_pct=mean_atr,
            sell_tax=sell_tax
        )
    else:
        physics = calc_friction_floor(
            fee_rate=fee,
            slippage_rate=slippage,
            mean_atr_pct=mean_atr,
            market_type=m_type,
            sell_tax=sell_tax
        )

    return {
        "status": "success",
        "symbol": get_target_symbol(config),
        "interval": intv,
        "market_type": m_type,
        "physics": physics
    }

@app.get("/api/config")
async def api_get_config():
    return load_config()

@app.post("/api/config")
async def api_save_config(data: dict):
    config = load_config()
    
    # 📌 OPTUNA_CONFIG가 포함된 경우 기존 설정과 병합 (덮어쓰기 파괴 방지)
    if "OPTUNA_CONFIG" in data and isinstance(data["OPTUNA_CONFIG"], dict):
        current_optuna = config.get("OPTUNA_CONFIG", {})
        if isinstance(current_optuna, dict):
            merged_optuna = dict(current_optuna)
            merged_optuna.update(data["OPTUNA_CONFIG"])
            data["OPTUNA_CONFIG"] = merged_optuna
            
    from shared.core.market_utils import detect_market_type
    # 📌 가상자산(CRYPTO) 등 비-KRX 자산인 경우 INTRADAY_SQUARE_OFF는 항상 False 강제
    target_symbol = data.get('SYMBOL_UPBIT', config.get('SYMBOL_UPBIT', ''))
    temp_cfg = {'SYMBOL_UPBIT': target_symbol}
    if detect_market_type(temp_cfg) != 'KRX':
        data['INTRADAY_SQUARE_OFF'] = False

    # 열린 프로젝트의 종목·인터벌·이름은 폴더 경로와 1:1 이라 바꿀 수 없다 (바꾸려면 새 프로젝트)
    active_ref = get_active_project_ref(PROJECT_NAME)
    if active_ref:
        identity_changes = [k for k in ("SYMBOL_UPBIT", "INTERVAL", "ACTIVE_SESSION_ID")
                            if k in data and str(data[k]) != str(config.get(k))]
        if identity_changes:
            return JSONResponse(status_code=409, content={
                "status": "error",
                "error": f"열린 프로젝트({active_ref})의 {', '.join(identity_changes)} 는 바꿀 수 없습니다. "
                         f"다른 종목/인터벌은 새 프로젝트를 만드세요.",
            })

    config.update(data)
    # 열린 프로젝트가 있으면 save_config 가 곧 그 프로젝트 폴더의 config_snapshot.json 에 기록한다
    save_config(config)

    return {"status": "ok"}

def _is_model_artifact_name(name: str) -> bool:
    """배포 시 교체 대상이 되는 모델 관련 파일명 (이전 세션 잔재 정리 대상)"""
    return (
        (name.startswith("model_") and name.endswith(".txt"))
        or (name.startswith("best_params_") and name.endswith(".json"))
        or name == "feature_spec.json"
    )


def _replace_model_dir_atomic(source_model_dir: str, target_model_dir: str) -> int:
    """source 의 파일을 target 으로 교체 배포한다.

    target 의 모델 관련 파일은 제거(소스에 없는 잔재 삭제)하고 그 외 파일은 보존한다.
    staging 디렉터리 구성 후 rename 으로 교체하며, 교체 실패 시 원본으로 롤백한다.
    """
    import shutil
    import uuid
    parent = os.path.dirname(os.path.abspath(target_model_dir))
    os.makedirs(parent, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    staging = os.path.join(parent, f".model_staging_{tag}")
    backup = os.path.join(parent, f".model_backup_{tag}")
    copied = 0
    try:
        os.makedirs(staging)
        if os.path.isdir(target_model_dir):
            for item in os.listdir(target_model_dir):
                t_path = os.path.join(target_model_dir, item)
                if os.path.isfile(t_path) and not _is_model_artifact_name(item):
                    shutil.copy2(t_path, os.path.join(staging, item))
        for item in os.listdir(source_model_dir):
            s_file = os.path.join(source_model_dir, item)
            if os.path.isfile(s_file):
                shutil.copy2(s_file, os.path.join(staging, item))
                copied += 1
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    had_old = os.path.isdir(target_model_dir)
    try:
        if had_old:
            os.rename(target_model_dir, backup)
        os.rename(staging, target_model_dir)
    except Exception:
        # 롤백: 원본 복구
        if had_old and os.path.isdir(backup) and not os.path.isdir(target_model_dir):
            os.rename(backup, target_model_dir)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    return copied


MODEL_SOURCE_FILE = "model_source.json"  # model/ 의 출처 기록 (자동연구 챔피언 / 사용자 직접 학습)


def _run_view_dir(project_dir: str, need: str = "leaderboard") -> str:
    """화면에 보여 줄 자동연구 결과 폴더.

    - need="leaderboard": 리더보드가 있는(=끝난) 가장 최근 실행 — 후보·리더보드 조회용
    - need="any": 가장 최근 실행 — 진행 중 trial 조회용
    실행 폴더가 없는 레거시 프로젝트는 프로젝트 루트.
    """
    ref = project_store.ref_of_dir(project_dir)
    runs = project_store.list_runs(ref) if ref else []
    if need == "leaderboard":
        for run in reversed(runs):
            if os.path.exists(os.path.join(run, "leaderboard.json")):
                return run
    elif runs:
        return runs[-1]
    return project_dir


def _candidates_root(project_dir: str) -> str:
    """후보(rank_N) 폴더 — 끝난 최신 실행의 candidates/, 없으면 레거시 루트 candidates/"""
    return os.path.join(_run_view_dir(project_dir), "candidates")


def _latest_leaderboard(project_dir: str) -> dict:
    path = os.path.join(_run_view_dir(project_dir), "leaderboard.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}
    return {}


def _deploy_gate(project_dir: str, target_model: str, interval: str):
    """실전 배포 전 합격 여부 확인 → (허용 여부, 사유).

    종전에는 확인 없이 복사했다. 자동연구가 탈락(REJECTED)해도 model/ 에는 마지막으로 재학습한 후보가
    남아 있어, 그 모델이 그대로 실전(shared/deploy)으로 나갈 수 있었다 (2026-09-28 KODEX200_M1_TEST2).
    """
    if target_model.startswith("rank_"):
        detail_path = os.path.join(_candidates_root(project_dir), target_model, "candidate_detail.json")
        if not os.path.exists(detail_path):
            return False, f"{target_model} 후보의 검증 기록(candidate_detail.json)이 없어 합격 여부를 확인할 수 없습니다."
        try:
            with open(detail_path, "r", encoding="utf-8") as f:
                detail = json.load(f)
        except Exception:
            return False, f"{target_model} 후보의 검증 기록을 읽을 수 없습니다."
        if detail.get("status") != "PASSED" or not detail.get("passed", False):
            return False, (f"{target_model} 후보는 최종 Test 미달({detail.get('status', 'UNKNOWN')})입니다 — "
                           f"순수익 {detail.get('final_profit', 0):+.2f}%, 거래 {detail.get('final_trades', 0)}회.")
        return True, ""

    model_dir = os.path.join(project_dir, "model")
    if not os.path.exists(os.path.join(model_dir, f"model_{interval}.txt")):
        return False, "model/ 에 학습된 모델 파일이 없습니다."
    source_path = os.path.join(model_dir, MODEL_SOURCE_FILE)
    if os.path.exists(source_path):
        try:
            with open(source_path, "r", encoding="utf-8") as f:
                src = json.load(f)
        except Exception:
            src = {}
        if src.get("kind") == "manual":
            return True, ""
        if src.get("status") == "PASSED":
            return True, ""
        return False, f"model/ 의 모델은 합격 기록이 없습니다 (출처: {src.get('kind', '?')}, 상태: {src.get('status', '?')})."
    lb = _latest_leaderboard(project_dir)
    if lb and lb.get("status") != "PASSED":
        return False, (f"최근 자동연구가 탈락({lb.get('status')}, {lb.get('evaluated_at', '')[:16]})했고 model/ 의 출처 기록이 없습니다 — "
                       f"탈락 실행이 남긴 후보일 수 있습니다.")
    return True, ""


@app.post("/api/deploy/shared")
async def api_deploy_model_to_shared(data: Optional[dict] = None):
    """로컬 deploy 세션(또는 특정 AutoLab 후보 모델)을 shared/deploy 하위 경로로 격리 배포 복사합니다."""
    import shutil
    config = load_config()
    
    if data is None:
        data = {}
        
    coin_code = config['SYMBOL_UPBIT'].split('-')[-1]
    interval = config.get('INTERVAL', '15m')
    
    target_session = data.get("session_id") or config.get('ACTIVE_SESSION_ID', '').strip()
    target_model = data.get("target_model", "model") # "model", "rank_1", "rank_2" 등
    
    if not target_session or target_session == "N/A" or target_session == "active":
        target_session = config.get('ACTIVE_SESSION_ID', '').strip()
        
    if not target_session or target_session == "N/A":
        return JSONResponse(status_code=400, content={"error": "배포 대상 세션(session_id) 정보가 없습니다."})
    if not _is_safe_seg(target_session, target_model, coin_code, interval):
        return _bad_path_response()

    # 원본 경로 탐색
    source_dir = _find_session_dir(target_session, coin_code, interval)
    if not source_dir or not os.path.exists(source_dir):
        baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_dir = os.path.join(baker_root, "deploy", f"{coin_code}_{interval}", target_session)

    if not os.path.exists(source_dir):
        return JSONResponse(status_code=400, content={"error": f"세션 디렉토리가 존재하지 않습니다: {source_dir}"})

    source_model_dir = (os.path.join(_candidates_root(source_dir), target_model)
                        if target_model.startswith("rank_") else os.path.join(source_dir, "model"))

    # ── 합격 여부 확인 (fail-closed, 사용자가 사유를 보고 명시적으로 force 한 경우만 통과) ──
    gate_ok, gate_reason = _deploy_gate(source_dir, target_model, interval)
    if not gate_ok and not data.get("force"):
        return {"status": "blocked", "error": gate_reason, "can_force": True,
                "hint": "검증을 통과하지 못한 모델입니다. 그래도 배포하려면 확인 후 강제 배포를 선택하세요."}
    
    if not os.path.exists(source_model_dir):
        return JSONResponse(status_code=400, content={"error": f"배포할 소스 모델 디렉토리가 존재하지 않습니다: {source_model_dir}"})

    # ── [T-6] 학습 피처 ↔ 실전 공급원 계약 검사 (배포 전 fail-closed) ──
    # 백테스트는 과거 파일에서 어떤 참조든 읽을 수 있지만, 실전은 그 참조를 실시간으로
    # 받을 수 있어야 같은 피처를 만든다. 어긋나면 그 피처가 0/직전값으로 굳은 채 매매한다.
    try:
        from shared.data_library.live_feed_contract import (
            LiveFeedContractError, assert_live_feature_support,
        )
        spec_path = os.path.join(source_model_dir, "feature_spec.json")
        if os.path.exists(spec_path):
            with open(spec_path, "r", encoding="utf-8") as f:
                feature_ids = json.load(f).get("features_order", [])
            assert_live_feature_support(feature_ids)
    except LiveFeedContractError as e:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "error": str(e),
            "hint": "해당 참조의 실시간 커넥터를 CTrade 에 구현하거나, 그 지표를 뺀 모델로 재학습하세요.",
        })


    # 타겟 경로 설정 (shared/deploy 하위구조 정렬)
    # CTradeSystem/shared/deploy/{coin_code}_{interval}/{target_session}/model/
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target_base_dir = os.path.join(project_root, "shared", "deploy", f"{coin_code}_{interval}", target_session)
    target_model_dir = os.path.join(target_base_dir, "model")
    
    try:
        os.makedirs(target_model_dir, exist_ok=True)
        
        # 1. config_snapshot.json 복사
        src_config = os.path.join(source_dir, "config_snapshot.json")
        if os.path.exists(src_config):
            shutil.copy2(src_config, os.path.join(target_base_dir, "config_snapshot.json"))
            
        # 2. model/* 복사 to shared/deploy
        # 대상의 이전 세션 잔재(model_*.txt / best_params_*.json / feature_spec.json)가 남지 않도록
        # staging 디렉터리에 (기존 비-모델 파일 + 신규 소스 파일)을 구성한 뒤 교체하고, 실패 시 롤백한다.
        copied_count = _replace_model_dir_atomic(source_model_dir, target_model_dir)

        # 3. Baker 로컬 model/ 폴더에도 동기화 복사 (만약 candidate에서 배포한 경우)
        local_model_dir = os.path.join(source_dir, "model")
        if target_model != "model":
            os.makedirs(local_model_dir, exist_ok=True)
            for item in os.listdir(source_model_dir):
                s_file = os.path.join(source_model_dir, item)
                t_file = os.path.join(local_model_dir, item)
                if os.path.isfile(s_file):
                    shutil.copy2(s_file, t_file)

        # 4. feature_spec.json 동기화
        spec_path = os.path.join(source_model_dir, "feature_spec.json")
        applied_features = []
        if os.path.exists(spec_path):
            try:
                with open(spec_path, "r", encoding="utf-8") as sf:
                    spec = json.load(sf)
                    applied_features = spec.get("features_order", [])
                
                fc = load_feature_config()
                save_feature_config(
                    enabled_features=applied_features,
                    feature_params=fc.get("feature_params", {}),
                    optuna_config=config.get("OPTUNA_CONFIG"),
                    active_features=applied_features,
                    feature_penalties=fc.get("feature_penalties", {})
                )
            except Exception as se:
                print(f"Error syncing feature spec: {se}")

        # 5. 모델 가중치 SHA-256 지문 및 학습일자 추출
        import hashlib
        model_fingerprint = "N/A"
        trained_at_str = "N/A"
        for item in os.listdir(source_model_dir):
            if item.startswith("model_") and item.endswith(".txt"):
                try:
                    m_path = os.path.join(source_model_dir, item)
                    sha256 = hashlib.sha256()
                    with open(m_path, "rb") as mf:
                        for chunk in iter(lambda: mf.read(65536), b""):
                            sha256.update(chunk)
                    model_fingerprint = sha256.hexdigest()[:8]
                except Exception:
                    pass
            elif item.startswith("best_params_") and item.endswith(".json"):
                try:
                    bp_path = os.path.join(source_model_dir, item)
                    with open(bp_path, "r", encoding="utf-8") as bpf:
                        bp_data = json.load(bpf)
                        trained_at_str = bp_data.get("created_at", "N/A")
                except Exception:
                    pass

        # 6. 텔레그램 알림 발송
        try:
            market_label = f"{coin_code}_{interval}"
            _send_telegram_notification_sync(
                f"⚡ <b>[실전 모델 배포 완료]</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"• 프로젝트: <code>{target_session}</code> ({market_label})\n"
                f"• 모델 지문: <code>#{model_fingerprint}</code> (학습: {trained_at_str})\n"
                f"• 배포 대상: <b>{target_model}</b> ({copied_count}개 파일)\n"
                f"• 적용 피처 ({len(applied_features)}개): {applied_features}\n"
                f"• 실전 경로: <code>shared/deploy/{market_label}/{target_session}</code>\n"
                f"🚀 CTrade 매매 엔진 실전 모델이 성공적으로 갱신되었습니다."
            )
        except Exception:
            pass
                
        return {
            "status": "ok",
            "message": f"성공적으로 {copied_count}개의 모델 파일이 shared/deploy 경로에 격리 배포되었습니다.",
            "session_id": target_session,
            "target_model": target_model,
            "asset_unit": f"{coin_code}_{interval}",
            "target_dir": target_base_dir,
            "applied_features": applied_features,
            "model_fingerprint": model_fingerprint,
            "created_at": trained_at_str
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "error": f"모델 배포 복사 중 오류 발생: {str(e)}"})


@app.get("/api/status")
async def api_get_status():
    """데이터셋, 모델 상태 정보 반환"""
    config = load_config()
    snapshot_path = get_snapshot_path(config)
    params_path = get_params_path(config)
    model_path = get_model_path(config)

    coin_code = config['SYMBOL_UPBIT'].split('-')[-1]
    interval = config.get('INTERVAL', '15m')
    market_dir = f"{coin_code}_{interval}"

    from shared.core.market_utils import detect_market_type
    market_type = detect_market_type(config)

    status = {
        "coin": config['SYMBOL_UPBIT'],
        "interval": interval,
        "market_dir": market_dir,
        "market_type": market_type,
        "dataset": {"exists": False},
        "model": {"exists": False},
        "params": {"exists": False},
        "config": config,
        # 열린 프로젝트 ("<종목>_<인터벌>/<이름>") — None 이면 열린 프로젝트 없음 (설정 저장·학습 불가, 화면이 안내)
        "project_ref": get_active_project_ref(PROJECT_NAME),
    }

    if os.path.exists(snapshot_path):
        try:
            import pandas as pd
            df = pd.read_csv(snapshot_path, usecols=['timestamp'])
            status["dataset"] = {
                "exists": True,
                "path": snapshot_path,
                "rows": len(df),
                "size_mb": round(os.path.getsize(snapshot_path) / (1024 * 1024), 2),
                "start": str(df['timestamp'].iloc[0])[:19] if not df.empty else "",
                "end": str(df['timestamp'].iloc[-1])[:19] if not df.empty else "",
            }
        except Exception as e:
            status["dataset"] = {"exists": True, "error": str(e)}

    if os.path.exists(model_path):
        import hashlib
        m_fp = "N/A"
        try:
            sha256 = hashlib.sha256()
            with open(model_path, "rb") as mf:
                for chunk in iter(lambda: mf.read(65536), b""):
                    sha256.update(chunk)
            m_fp = sha256.hexdigest()[:8]
        except Exception:
            pass
        status["model"] = {"exists": True, "path": model_path, "model_fingerprint": m_fp}
    if os.path.exists(params_path):
        with open(params_path, 'r') as f:
            try:
                bp = json.load(f)
                status["params"] = {"exists": True, "path": params_path, "data": bp}
            except:
                status["params"] = {"exists": True, "path": params_path}
                
    # 📌 최근 백테스트 결과가 있다면 status에 주입
    coin_code = config['SYMBOL_UPBIT'].split('-')[-1]
    interval = config.get('INTERVAL', '15m')
    active_session_id = config.get('ACTIVE_SESSION_ID')
    if active_session_id and active_session_id != "N/A" and active_session_id.strip() != "":
        last_backtest_path = os.path.join(_BAKER_ROOT, "deploy", f"{coin_code}_{interval}", active_session_id, "model", "last_backtest_result.json")
    else:
        last_backtest_path = os.path.join(_BAKER_ROOT, "data", coin_code, "last_backtest_result.json")
        
    status["last_backtest"] = None
    if os.path.exists(last_backtest_path):
        try:
            with open(last_backtest_path, 'r', encoding='utf-8') as f:
                status["last_backtest"] = json.load(f)
        except:
            pass

    return status

@app.get("/api/markets")
async def api_get_markets():
    """종목 레지스트리 기반 주식/ETF/가상자산 + 업비트 KRW 마켓 통합 목록 반환"""
    registry = load_symbols_registry()
    market_list = []
    seen_symbols = set()
    
    # 1. 등록된 레지스트리 종목 우선 추가
    for sym_code, sym_info in registry.items():
        if not sym_info.get("is_active", True):
            continue
        if not sym_info.get("tradable", True):
            continue  # 참조/매크로 프록시 전용 심볼은 학습·매매 타겟 선택 목록에서 제외
        mtype = sym_info.get("market_type", "OTHER")
        prefix = "[ETF] " if "ETF" in mtype else ("[주식] " if "STOCK" in mtype else "")
        market_list.append({
            'market': sym_code,
            'korean_name': f"{prefix}{sym_info.get('name', sym_code)}",
            'english_name': sym_info.get('description', sym_code),
            'category': 'CRYPTO' if mtype == 'CRYPTO' else 'STOCK_ETF',
            'exchange': sym_info.get('exchange', 'KIS')
        })
        seen_symbols.add(sym_code)
        
    # 2. 업비트 실시간 KRW 마켓 목록 보조 병합
    try:
        import requests
        res = requests.get("https://api.upbit.com/v1/market/all?isDetails=false", timeout=5).json()
        for m in res:
            sym = m.get('market', '')
            if sym.startswith('KRW-') and sym not in seen_symbols:
                market_list.append(dict(m, category='CRYPTO', exchange='UPBIT'))
                seen_symbols.add(sym)
    except Exception as e:
        # 업비트 조회 실패 시에도 원장에 등록된 종목은 위에서 이미 담겼다 (종목명 폴백을 코드에 두지 않는다)
        logger.warning(f"[markets] 업비트 KRW 마켓 목록 조회 실패 — 원장 등록 종목만 반환: {e}")

    return market_list

@app.get("/api/dataset/preview")
async def api_dataset_preview(rows: int = 100):
    """데이터셋 미리보기 (첫 N행)"""
    config = load_config()
    snapshot_path = get_snapshot_path(config)
    if not os.path.exists(snapshot_path):
        return {"error": "Dataset not found"}
    import pandas as pd
    df = pd.read_csv(snapshot_path, nrows=rows)
    return {
        "columns": list(df.columns),
        "data": df.to_dict(orient="records"),
        "total_rows": len(pd.read_csv(snapshot_path)),
    }

# ---------------------------------------------------------------------------
# Feature Registry API
# ---------------------------------------------------------------------------
@app.get("/api/features/registry")
async def api_get_feature_registry():
    """지표 레지스트리 전체 반환"""
    return {
        "registry": DEFAULT_FEATURE_REGISTRY,
        "categories": CATEGORY_INFO,
        "optuna_defaults": DEFAULT_OPTUNA_CONFIG
    }

@app.get("/api/features/config")
async def api_get_feature_config(target: Optional[str] = None, symbol: Optional[str] = None, interval: Optional[str] = None):
    """현재 설정된 지표 설정 반환 (target/symbol 지정 시 해당 종목 기준 조회, interval 지정 시 해당 인터벌 기준 조회)"""
    config = load_config()
    feature_config = load_feature_config()
    
    # 활성화된 지표 목록 (단일 enabled_features로 통합)
    enabled_features = feature_config.get("enabled_features", list(DEFAULT_FEATURE_REGISTRY.keys()))
    feature_params = feature_config.get("feature_params", {})
    optuna_config = config.get("OPTUNA_CONFIG", DEFAULT_OPTUNA_CONFIG)

    # INTERVAL을 분 단위로 변환 (지정된 interval 파라미터 우선)
    interval_str = interval or config.get('INTERVAL')
    if not interval_str:
        active_sess = str(config.get('ACTIVE_SESSION_ID', ''))
        if '_M1_' in active_sess or '_1m' in active_sess.lower() or 'M1' in active_sess:
            interval_str = '1m'
        elif '_M5_' in active_sess or '_5m' in active_sess.lower() or 'M5' in active_sess:
            interval_str = '5m'
        elif '_M15_' in active_sess or '_15m' in active_sess.lower() or 'M15' in active_sess:
            interval_str = '15m'
        elif '_M60_' in active_sess or '_60m' in active_sess.lower() or 'M60' in active_sess:
            interval_str = '60m'
        else:
            interval_str = '1m'
    
    interval_str = str(interval_str).lower()
    interval_seconds = 60.0
    if interval_str.endswith('m'):
        try:
            interval_minutes = float(interval_str.replace('m', ''))
        except ValueError:
            interval_minutes = 1.0
        interval_seconds = interval_minutes * 60.0
    elif interval_str.endswith('h'):
        try:
            interval_minutes = float(interval_str.replace('h', '')) * 60.0
        except ValueError:
            interval_minutes = 60.0
        interval_seconds = interval_minutes * 60.0
    elif interval_str.endswith('s'):
        try:
            secs = float(interval_str.replace('s', ''))
            interval_minutes = secs / 60.0
            interval_seconds = secs
        except ValueError:
            interval_minutes = 10.0 / 60.0
            interval_seconds = 10.0
    elif interval_str.endswith('d'):
        try:
            days = float(interval_str.replace('d', ''))
            interval_minutes = days * 1440.0
            interval_seconds = days * 86400.0
        except ValueError:
            interval_minutes = 1440.0
            interval_seconds = 86400.0
    else:
        try:
            interval_minutes = float(interval_str)
            interval_seconds = interval_minutes * 60.0
        except ValueError:
            interval_minutes = 1.0
            interval_seconds = 60.0

    # 마켓 타입 및 활성 타겟 판별 (타겟 파라미터 우선, 기본: 069500 KODEX 200)
    chosen_target = target or symbol
    if chosen_target:
        active_target = str(chosen_target).strip()
        active_sess = str(config.get("ACTIVE_SESSION_ID", "")).strip()
        clean_target = str(active_target).replace("KRW-", "")
        market_type = detect_symbol_market_scope(active_target, session_id=active_sess)
    else:
        active_target = get_target_symbol(config)
        active_sess = str(config.get("ACTIVE_SESSION_ID", "")).strip()
        clean_target = str(active_target).replace("KRW-", "")
        market_type = detect_symbol_market_scope(active_target, session_id=active_sess)

    # ⚡ Data Vault(shared/config/symbol_relations.json)를 단일 진실 공급원(SSOT)으로 사용
    # 타깃 종목(clean_target)에 지정된 관계만 엄격히 반영하여 이전 프로젝트 잔재 누수 원천 차단
    from shared.data_library.symbol_relations_manager import SymbolRelationsManager
    vault_refs = SymbolRelationsManager.get_relations_for_target(clean_target)
    
    reference_symbols = [
        {
            "symbol": r["symbol"],
            "key": ref_key(r["symbol"]),        # 컬럼/피처 이름 키: 원장 심볼에서 유도 (별칭 없음)
            "name": r.get("name", r["symbol"]),
            "role": r.get("role", "leading_stock")
        }
        for r in vault_refs if isinstance(r, dict) and r.get("symbol")
    ]

    # ⚡ 유효한 참조 종목 키 수집 및 고아(삭제된) 참조 피처 레지스트리 자동 청소
    valid_ref_keys = {ref["key"] for ref in reference_symbols}
    for reg_k in list(DEFAULT_FEATURE_REGISTRY.keys()):
        parsed_ref = parse_reference_feature_id(reg_k)
        if parsed_ref and parsed_ref[0] not in valid_ref_keys:
            DEFAULT_FEATURE_REGISTRY.pop(reg_k, None)

    # ⚡ 참조 대장주 동적 등록 — 등록 루프는 공용 소유자 한 곳에 위임한다.
    #    (이 루프를 화면마다 복제했던 것이 "지표 화면을 먼저 열어야 참조 피처가 보이는"
    #     프로세스 상태 의존의 원인이었다)
    from shared.features.feature_registry import register_configured_reference_features
    register_configured_reference_features({"reference_symbols": reference_symbols},
                                           DEFAULT_FEATURE_REGISTRY)

    # ⚡ 데이터 가용 상태(Data Vault 캔들) 검사
    ref_status = {}
    baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    for ref in reference_symbols:
        if isinstance(ref, dict):
            sym = ref.get("symbol", "")
            name = ref.get("name", sym)
            # 과거 데이터 캔들 파일 검색 (DataVault 우선 탐색, Parquet 및 CSV 동시 지원)
            found = False
            row_count = 0
            from shared.data_library import paths as dpaths
            candidate_dirs = [
                # 1. 🏛️ DataVault (현대화된 SSOT 저장소 우선 탐색)
                os.path.join(dpaths.normalized_root(), f"candles_{interval_str}"),
                os.path.join(dpaths.normalized_root(), "candles_1m"),
                os.path.join(dpaths.normalized_root(), "macro"),
                os.path.join(dpaths.raw_root(), "ctrader"),
                os.path.join(dpaths.raw_root(), "creon", f"candle_{interval_str}"),
                os.path.join(dpaths.raw_root(), "creon", "candle_1m"),
                # 2. 레거시 historical 경로 폴백
                os.path.join(baker_root, "data", "historical", "kis", f"candle_{interval_str}"),
                os.path.join(baker_root, "data", "historical", "kis", "candle_1m"),
                os.path.join(baker_root, "data", "historical", "krx", "candle"),
                os.path.join(baker_root, "data", "historical", "overseas_futures"),
                os.path.join(baker_root, "data", "historical", "upbit", f"candle_{interval_str}_extracted"),
                os.path.join(baker_root, "data", "historical", "binance", f"candle_{interval_str}")
            ]
            for cdir in candidate_dirs:
                if not os.path.exists(cdir):
                    continue
                sym_l = sym.lower()
                clean_sym = sym_l[1:] if sym_l.startswith("a") and len(sym_l) == 7 and sym_l[1:].isalnum() else sym_l

                # 0. 크레온 1분봉 일자 저장소(candle_1m/A{종목}/ — 통합 CSV 가 없다): 날짜 메타의 행 수 합으로 판정
                from shared.data_library import candle_store as _cs
                if os.path.abspath(cdir) == os.path.abspath(_cs.candle_root()) and _cs.has_data(sym):
                    _rows = sum(int(e["rows"] or 0) for e in _cs.days(sym).values())
                    if _rows > row_count:
                        row_count, found = _rows, True

                # 1. Parquet 파일 직접 매칭
                exact_parquet_candidates = [
                    os.path.join(cdir, f"{sym}_{interval_str}.parquet"),
                    os.path.join(cdir, f"{sym}_1m.parquet"),
                    os.path.join(cdir, f"{sym}.parquet")
                ]
                for ep in exact_parquet_candidates:
                    if os.path.exists(ep):
                        try:
                            import pyarrow.parquet as pq
                            meta = pq.read_metadata(ep)
                            if meta.num_rows > row_count:
                                row_count = meta.num_rows
                                found = True
                                break
                        except Exception:
                            pass

                # 2. CSV 파일 직접 존재 확인
                if not found:
                    exact_csv_candidates = [
                        os.path.join(cdir, f"{sym}.csv"),
                        os.path.join(cdir, f"{sym}_1m.csv")
                    ]
                    for ec in exact_csv_candidates:
                        if os.path.exists(ec):
                            try:
                                with open(ec, "r", encoding="utf-8", errors="ignore") as f_cnt:
                                    cnt = sum(1 for _ in f_cnt) - 1
                                    if cnt > row_count:
                                        row_count = cnt
                                        found = True
                                        break
                            except Exception:
                                pass
                
                # 3. 디렉토리 순회 매칭 (Parquet 및 CSV)
                if not found:
                    matched_files = []
                    try:
                        dir_files = os.listdir(cdir)
                    except Exception:
                        dir_files = []
                    for fn in dir_files:
                        fn_l = fn.lower()
                        if fn_l.endswith(".manifest.json") or fn_l.endswith(".bak"):
                            continue
                        if (sym_l in fn_l or clean_sym in fn_l):
                            if fn_l.endswith(".parquet") or fn_l.endswith(".csv"):
                                matched_files.append(os.path.join(cdir, fn))
                    
                    if matched_files:
                        for mf in matched_files:
                            try:
                                if mf.endswith(".parquet"):
                                    import pyarrow.parquet as pq
                                    meta = pq.read_metadata(mf)
                                    cnt = meta.num_rows
                                else:
                                    with open(mf, "r", encoding="utf-8", errors="ignore") as f_cnt:
                                        cnt = sum(1 for _ in f_cnt) - 1
                                if cnt > row_count:
                                    row_count = cnt
                                    found = True
                            except Exception:
                                pass
                if found and row_count > 10:
                    break
            
            ref_status[sym] = {
                "symbol": sym,
                "key": ref_key(sym),
                "name": name,
                "ready": found and row_count > 10,
                "rows": max(0, row_count)
            }
    
    # 활성화된 지표 상세 정보 (고아 지표 자동 배제 및 동기화)
    active_features = []
    filtered_enabled_features = []
    for fid in enabled_features:
        if fid in DEFAULT_FEATURE_REGISTRY:
            f = DEFAULT_FEATURE_REGISTRY[fid].copy()
            f["current_params"] = feature_params.get(fid, {})
            f["enabled"] = True
            # 기본값 채우기
            for pname, pconfig in f.get("params", {}).items():
                if pname not in f["current_params"]:
                    f["current_params"][pname] = pconfig.get("default")
            active_features.append(f)
            filtered_enabled_features.append(fid)
    enabled_features = filtered_enabled_features

    # 지표 적합성 검사 (현재 자산군과 맞지 않는 활성 지표 목록 추출)
    incompatible_features = get_incompatible_features(active_target, enabled_features, session_id=active_sess)

    from CAiModelBaker.core.horizon_registry import get_horizon_context
    target_horizon_cfg = config.get("TARGET_HORIZON_CONFIG")
    horizon_defaults = get_horizon_context(interval_str)
    if not target_horizon_cfg or not isinstance(target_horizon_cfg, dict):
        target_horizon_cfg = horizon_defaults

    return {
        "active_features": active_features,
        "available_features": list(DEFAULT_FEATURE_REGISTRY.keys()),
        "feature_params": feature_params,
        "feature_penalties": feature_config.get("feature_penalties", {}),
        "regime_features": feature_config.get("regime_features", {}),
        "optuna_config": optuna_config,
        "optuna_defaults": DEFAULT_OPTUNA_CONFIG,   # 저장된 적 없는 항목의 기본값 — 화면이 숫자를 따로 갖지 않게
        "target_horizon_config": target_horizon_cfg,
        "horizon_defaults": horizon_defaults,
        "registry": DEFAULT_FEATURE_REGISTRY,
        "categories": CATEGORY_INFO,
        "interval_minutes": interval_minutes,
        "interval_seconds": interval_seconds,
        "interval": interval_str,
        "reference_groups": [
            all_groups[gid] for gid in SymbolRelationsManager.get_groups_for_target(clean_target)
            if gid in all_groups
        ] if 'all_groups' in locals() else [
            SymbolRelationsManager.load_all_groups()[gid] for gid in SymbolRelationsManager.get_groups_for_target(clean_target)
            if gid in SymbolRelationsManager.load_all_groups()
        ],
        "reference_symbols": reference_symbols,
        "reference_symbols_status": ref_status,
        "market_type": market_type,
        "incompatible_features": incompatible_features
    }

@app.get("/api/features/validate_suitability")
async def api_validate_feature_suitability(symbol: Optional[str] = None):
    """현재 심볼 또는 지정 심볼에 대한 활성 지표 적합성 검증"""
    config = load_config()
    target_sym = symbol or get_target_symbol(config, default="")
    active_sess = str(config.get("ACTIVE_SESSION_ID", "")).strip()
    feature_config = load_feature_config()
    enabled_features = feature_config.get("enabled_features", list(DEFAULT_FEATURE_REGISTRY.keys()))
    
    incompatible = get_incompatible_features(target_sym, enabled_features, session_id=active_sess)
    sym_scope = detect_symbol_market_scope(target_sym, session_id=active_sess)
    
    return {
        "symbol": target_sym,
        "symbol_scope": sym_scope,
        "is_suitable": len(incompatible) == 0,
        "incompatible_features": incompatible,
        "total_enabled": len(enabled_features)
    }

@app.get("/api/sessions")
async def api_get_sessions():
    """프로젝트 목록 — 프로젝트 폴더 기준 (모델이 없어도 표시).

    종전에는 model/feature_spec.json + model/best_params 가 있어야 목록에 나왔고, 메모·생성일도 모델 파일에서
    읽었다. model/ 을 '합격 모델 자리' 로 정리하면서 프로젝트 정보는 config_snapshot.json 의 PROJECT_META 로 옮겼다.
    """
    sessions = []
    try:
        for ref in project_store.list_projects():
            market_dir, sess_dir = ref.split("/", 1)
            sess_path = project_store.project_dir(ref)
            model_path = os.path.join(sess_path, "model")
            try:
                snap = project_store.read_project_config(ref)
                meta = snap.get(project_store.META_KEY) if isinstance(snap.get(project_store.META_KEY), dict) else {}

                spec, params = {}, {}
                spec_path = os.path.join(model_path, "feature_spec.json")
                if os.path.exists(spec_path):
                    with open(spec_path, 'r', encoding='utf-8') as sf:
                        spec = json.load(sf) or {}
                params_name = sorted(f for f in os.listdir(model_path) if f.startswith("best_params_") and f.endswith(".json")) \
                    if os.path.isdir(model_path) else []
                if params_name:
                    with open(os.path.join(model_path, params_name[0]), 'r', encoding='utf-8') as pf:
                        params = json.load(pf) or {}

                # 설정도 모델 흔적도 없는 폴더(예: 빈 폴더)는 프로젝트가 아니다
                if not snap and not spec and not params:
                    continue

                fee_val = snap.get("FEE_UPBIT") or (snap.get("OPTUNA_CONFIG") or {}).get("fee_upbit")
                slip_val = snap.get("SLIPPAGE") or (snap.get("OPTUNA_CONFIG") or {}).get("slippage")

                # model_*.txt 가중치 파일 SHA-256 지문
                sess_model_fp = "N/A"
                model_files = sorted(f for f in os.listdir(model_path) if f.startswith("model_") and f.endswith(".txt")) \
                    if os.path.isdir(model_path) else []
                if model_files:
                    try:
                        sha256 = hashlib.sha256()
                        with open(os.path.join(model_path, model_files[0]), "rb") as mf:
                            for chunk in iter(lambda: mf.read(65536), b""):
                                sha256.update(chunk)
                        sess_model_fp = sha256.hexdigest()[:8]
                    except Exception:
                        pass
                model_source = {}
                try:
                    with open(os.path.join(model_path, MODEL_SOURCE_FILE), 'r', encoding='utf-8') as msf:
                        model_source = json.load(msf)
                except Exception:
                    model_source = {}

                last_run = project_store.latest_run_dir(ref)
                last_result = project_store.read_run_result(last_run) if last_run else {}
                feat_cfg = snap.get("FEATURE_CONFIG") or {}
                identity = project_store.identity_of(ref, snap)

                sessions.append({
                    "session_id": sess_dir,
                    "market_dir": market_dir,
                    "project_ref": ref,
                    "symbol": identity["SYMBOL_UPBIT"] or spec.get("symbol") or "UNKNOWN",
                    "interval": identity["INTERVAL"],
                    "optuna_score": params.get("optuna_score", params.get("optuna_value", 0.0)),
                    "created_at": meta.get("created_at") or params.get("created_at", "N/A"),
                    "memo": meta.get("memo", params.get("memo", "")),
                    "features_count": len(spec.get("features_order", []) or feat_cfg.get("enabled_features", [])),
                    "train_start": params.get("train_start", snap.get("TRAIN_START", "N/A")),
                    "train_end": params.get("train_end", snap.get("TRAIN_END", "N/A")),
                    "opt_start": params.get("opt_start", snap.get("OPT_START", "N/A")),
                    "opt_end": params.get("opt_end", snap.get("OPT_END", "N/A")),
                    "fee_upbit": fee_val,
                    "slippage": slip_val,
                    "model_fingerprint": sess_model_fp,
                    "has_model": bool(model_files),
                    "model_source": model_source,
                    "last_run_status": last_result.get("status"),
                    "last_run": os.path.basename(last_run) if last_run else None,
                    "best_params": params
                })
            except Exception as ex:
                print(f"Error parsing session {ref}: {ex}")
    except Exception as e:
        print(f"Error listing sessions: {e}")

    # 최신 생성일자 순 정렬
    sessions.sort(key=lambda s: s.get("session_id", ""), reverse=True)
    return sessions

@app.get("/api/tuning/candidates")
async def api_get_tuning_candidates(market_dir: str, session_id: str):
    """특정 세션의 대기중인 상위 20개 전략 후보군 조회"""
    if not _is_safe_seg(market_dir, session_id):
        return _bad_path_response()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    deploy_sess_path = os.path.join(project_root, "deploy", market_dir, session_id)
    if not os.path.exists(deploy_sess_path):
        return JSONResponse(status_code=404, content={"error": "세션을 찾을 수 없습니다."})
        
    pending_files = [f for f in os.listdir(deploy_sess_path) if f.startswith("pending_trials_") and f.endswith(".json")]
    if not pending_files:
        return []
        
    pending_path = os.path.join(deploy_sess_path, pending_files[0])
    try:
        with open(pending_path, 'r', encoding='utf-8') as f:
            candidates = json.load(f)
        return candidates
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"후보군 로드 실패: {e}"})

@app.post("/api/tuning/confirm")
async def api_confirm_tuning_strategy(data: dict):
    """사용자가 선택한 파라미터로 최종 배포 확정"""
    session_id = data.get("session_id")
    market_dir = data.get("market_dir")
    selected_params = data.get("params")
    
    if not session_id or not market_dir or not selected_params:
        return {"status": "error", "error": "필수 파라미터 누락"}
    if not _is_safe_seg(session_id, market_dir):
        return _bad_path_response()
        
    config = load_config()
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: confirm_and_deploy_strategy(
                market_dir, session_id, selected_params, config
            )
        )
        return {"status": "ok", "message": "전략 배포 및 최종 확정 완료!"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/api/project/rename")
async def api_rename_project(data: dict):
    """프로젝트 별칭(memo) 변경"""
    session_id = data.get("session_id")
    market_dir = data.get("market_dir")
    if not _is_safe_seg(session_id, market_dir):
        return _bad_path_response()
    new_name = data.get("new_name")
    
    if not session_id or not market_dir or new_name is None:
        return {"status": "error", "error": "필수 파라미터 누락"}
        
    ref = f"{market_dir}/{session_id}"
    deploy_sess_path = project_store.project_dir(ref)
    model_dir = os.path.join(deploy_sess_path, "model")

    if not os.path.isdir(deploy_sess_path):
        return {"status": "error", "error": "프로젝트 폴더가 존재하지 않습니다."}

    try:
        # 프로젝트 정보(메모)는 config_snapshot.json 의 PROJECT_META 가 원본
        project_store.update_meta(ref, memo=new_name)
        # 하위 호환: model/best_params 의 memo 도 함께 갱신 (있을 때만)
        param_files = [f for f in os.listdir(model_dir) if f.startswith("best_params_") and f.endswith(".json")] \
            if os.path.isdir(model_dir) else []
        for pf_name in param_files[:1]:
            param_path = os.path.join(model_dir, pf_name)
            with open(param_path, 'r', encoding='utf-8') as f:
                bp = json.load(f)
            bp['memo'] = new_name
            with open(param_path, 'w', encoding='utf-8') as f:
                json.dump(bp, f, indent=4, ensure_ascii=False)
            
        # README.md 가 있다면 제목의 메모도 함께 변경해 줍니다.
        readme_path = os.path.join(deploy_sess_path, "README.md")
        if os.path.exists(readme_path):
            try:
                with open(readme_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                for idx, line in enumerate(lines):
                    if line.startswith("- **모델 메모/설명**:") or line.startswith("- **설명/메모**:") or line.startswith("- **설명/메모**:"):
                        prefix = line.split(":")[0]
                        lines[idx] = f"{prefix}: {new_name if new_name else '기재된 메모가 없습니다.'}\n"
                        break
                with open(readme_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
            except Exception:
                pass
                
        return {"status": "ok", "message": "프로젝트 이름 변경 완료"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/api/project/delete")
async def api_delete_project(data: dict):
    """프로젝트 물리 폴더 영구 삭제 (활성 프로젝트는 삭제 불가)"""
    session_id = data.get("session_id")
    market_dir = data.get("market_dir")
    
    if not session_id or not market_dir:
        return {"status": "error", "error": "필수 파라미터 누락"}
    if not _is_safe_seg(session_id, market_dir):
        return _bad_path_response()
        
    ref = f"{market_dir}/{session_id}"
    deploy_sess_path = project_store.project_dir(ref)
    if not os.path.exists(deploy_sess_path):
        return {"status": "error", "error": "세션 폴더가 존재하지 않습니다."}

    # 열린 프로젝트를 지우면 포인터를 지운다 (사라진 폴더를 가리키지 않게) — 이후 화면이 프로젝트 선택을 요구한다
    if get_active_project_ref(PROJECT_NAME) == ref:
        set_active_project_ref(None, PROJECT_NAME)

    import shutil
    try:
        shutil.rmtree(deploy_sess_path)
        return {"status": "ok", "message": f"프로젝트 {session_id}가 영구 삭제되었습니다."}
    except Exception as e:
        return {"status": "error", "error": f"삭제 실패: {str(e)}"}

@app.post("/api/project/rename_folder")
async def api_rename_project_folder(data: dict):
    """프로젝트 물리 폴더명(ID) 및 내부 세션 ID 설정 변경"""
    session_id = data.get("session_id")
    market_dir = data.get("market_dir")
    new_name = data.get("new_name")
    
    if not session_id or not market_dir or not new_name:
        return {"status": "error", "error": "필수 파라미터 누락"}
    if not _is_safe_seg(session_id, market_dir):
        return _bad_path_response()
        
    # 영문, 숫자, 언더바 형태 검증 및 정제
    import re
    safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', new_name)
    if not safe_name:
        return {"status": "error", "error": "새 폴더명이 올바르지 않습니다."}
        
    new_session_id = safe_name
    if new_session_id == session_id:
        return {"status": "error", "error": "기존 이름과 동일합니다."}
        
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source_path = os.path.join(project_root, "deploy", market_dir, session_id)
    target_path = os.path.join(project_root, "deploy", market_dir, new_session_id)
    
    if not os.path.exists(source_path):
        return {"status": "error", "error": "세션 폴더가 존재하지 않습니다."}
    # 이름은 모든 인터벌을 통틀어 유일해야 한다 (이름만으로 찾는 옛 코드·사용자 혼동 방지)
    if os.path.exists(target_path) or project_store.name_taken(new_session_id):
        return {"status": "error", "error": f"이미 존재하는 프로젝트 명입니다: {project_store.find_by_name(new_session_id) or new_session_id}"}

    old_ref = f"{market_dir}/{session_id}"
    was_active = (get_active_project_ref(PROJECT_NAME) == old_ref)

    try:
        # 1. 폴더명 변경
        os.rename(source_path, target_path)
        
        # 2. 내부 파일 세션 ID 정보 치환
        model_dir = os.path.join(target_path, "model")
        
        # best_params_*.json
        if os.path.exists(model_dir):
            param_files = [f for f in os.listdir(model_dir) if f.startswith("best_params_") and f.endswith(".json")]
            if param_files:
                bp_path = os.path.join(model_dir, param_files[0])
                try:
                    with open(bp_path, 'r', encoding='utf-8') as f:
                        bp = json.load(f)
                    bp['opt_session_id'] = new_session_id
                    with open(bp_path, 'w', encoding='utf-8') as f:
                        json.dump(bp, f, indent=4, ensure_ascii=False)
                except Exception as ex:
                    print(f"Failed to update best_params during rename: {ex}")
                    
            # feature_spec.json
            spec_path = os.path.join(model_dir, "feature_spec.json")
            if os.path.exists(spec_path):
                try:
                    with open(spec_path, 'r', encoding='utf-8') as f:
                        spec = json.load(f)
                    spec['session_id'] = new_session_id
                    with open(spec_path, 'w', encoding='utf-8') as f:
                        json.dump(spec, f, indent=4, ensure_ascii=False)
                except Exception as ex:
                    print(f"Failed to update feature_spec during rename: {ex}")
                    
        # config_snapshot.json
        snapshot_path = os.path.join(target_path, "config_snapshot.json")
        if os.path.exists(snapshot_path):
            try:
                with open(snapshot_path, 'r', encoding='utf-8') as f:
                    snap = json.load(f)
                snap['ACTIVE_SESSION_ID'] = new_session_id
                with open(snapshot_path, 'w', encoding='utf-8') as f:
                    json.dump(snap, f, indent=4, ensure_ascii=False)
            except Exception as ex:
                print(f"Failed to update config_snapshot during rename: {ex}")
                
        # 3. 열린 프로젝트였다면 포인터를 새 경로로 옮긴다
        if was_active:
            set_active_project_ref(f"{market_dir}/{new_session_id}", PROJECT_NAME)
            
        return {"status": "ok", "message": "폴더명 및 설정 변경 완료", "new_session_id": new_session_id}
    except Exception as e:
        return {"status": "error", "error": f"이름 변경 중 오류 발생: {str(e)}"}

@app.post("/api/project/create")
async def api_create_project(data: dict):
    """신규 프로젝트 생성 및 활성화 (입력된 이름으로 물리적 폴더 및 스냅샷 생성, 선택 시 기존 프로젝트 설정 복사)"""
    name = data.get("name")
    memo = data.get("memo", "")
    req_symbol = data.get("symbol")
    req_interval = data.get("interval")
    copy_from_session_id = data.get("copy_from_session_id")
    copy_from_market_dir = data.get("copy_from_market_dir")
    
    if not name:
        return {"status": "error", "error": "프로젝트 이름은 필수입니다."}
    if not _is_safe_seg(copy_from_session_id, copy_from_market_dir, req_interval) or \
            (req_symbol and not _is_safe_seg(str(req_symbol).split('-')[-1])):
        return _bad_path_response()
        
    # 영문, 숫자, 언더바 형태 검증 및 정제
    import re
    safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    if not safe_name:
        return {"status": "error", "error": "프로젝트 이름이 올바르지 않습니다."}
        
    session_id = safe_name
    
    req_fee = data.get("fee_upbit") if data.get("fee_upbit") is not None else data.get("fee_rate")
    req_slippage = data.get("slippage")
    
    # 새 프로젝트의 출발점은 기본값(common + defaults)이다. 직전에 열려 있던 프로젝트 설정을 물려받지 않는다.
    # 종목·인터벌을 요청에서 받지 못하면 지금 열린 프로젝트(없으면 공통 설정)의 것을 쓴다.
    current = load_config()
    config = load_base_config(PROJECT_NAME)
    config['SYMBOL_UPBIT'] = req_symbol or current.get('SYMBOL_UPBIT')
    config['SYMBOL_BINANCE'] = current.get('SYMBOL_BINANCE', '') if not req_symbol else ''
    if req_symbol:
        sym_info = get_symbol_info(req_symbol)
        config['SYMBOL_BINANCE'] = sym_info.get('binance_symbol', '')
        default_fee = sym_info['default_fee']
        default_slippage = sym_info['default_slippage']
        if not copy_from_session_id:
            config['FEE_UPBIT'] = float(req_fee) if req_fee is not None else default_fee
            config['FEE_RATE'] = config['FEE_UPBIT']
            config['SLIPPAGE'] = float(req_slippage) if req_slippage is not None else default_slippage
    config['INTERVAL'] = req_interval or current.get('INTERVAL', '15m')
    symbol = get_target_symbol(config)
    interval = config.get('INTERVAL', '15m')
    symbol_code = symbol.split('-')[-1]

    if _research_is_running():
        return {"status": "error", "error": "자동연구 실행 중에는 새 프로젝트를 열 수 없습니다. 중단 후 다시 시도하세요."}

    # 이름은 모든 인터벌을 통틀어 유일해야 한다 (같은 이름 폴더에 이어 쓰던 종전 동작 차단)
    if project_store.name_taken(session_id):
        return {"status": "error",
                "error": f"이미 존재하는 프로젝트 이름입니다: {project_store.find_by_name(session_id)[0]} — 다른 이름을 쓰세요."}

    new_ref = project_store.make_ref(f"{symbol_code}_{interval}", session_id)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    deploy_dir = project_store.project_dir(new_ref)
    model_dir = os.path.join(deploy_dir, "model")
    reports_dir = os.path.join(deploy_dir, "reports")
    create_warnings = []

    try:
        # 복사 원본 폴더 경로 및 데이터 로드 설정
        source_config = {}
        source_bp = {}
        source_spec = {}
        
        if copy_from_session_id and copy_from_market_dir:
            source_dir = project_store.project_dir(project_store.make_ref(copy_from_market_dir, copy_from_session_id))
            if not os.path.exists(source_dir):
                return {"status": "error", "error": f"복사 대상 프로젝트 폴더가 존재하지 않습니다: {copy_from_session_id}"}
            
            # config_snapshot 로드
            source_config_path = os.path.join(source_dir, "config_snapshot.json")
            if os.path.exists(source_config_path):
                try:
                    with open(source_config_path, "r", encoding="utf-8") as f:
                        source_config = json.load(f)
                except Exception as e:
                    print(f"Failed to load source config snapshot: {e}")
            
            # best_params 로드
            source_model_dir = os.path.join(source_dir, "model")
            if os.path.exists(source_model_dir):
                param_files = [f for f in os.listdir(source_model_dir) if f.startswith("best_params_") and f.endswith(".json")]
                if param_files:
                    source_bp_path = os.path.join(source_model_dir, param_files[0])
                    try:
                        with open(source_bp_path, "r", encoding="utf-8") as f:
                            source_bp = json.load(f)
                    except Exception as e:
                        print(f"Failed to load source best_params: {e}")
                        
                # feature_spec 로드
                source_spec_path = os.path.join(source_model_dir, "feature_spec.json")
                if os.path.exists(source_spec_path):
                    try:
                        with open(source_spec_path, "r", encoding="utf-8") as f:
                            source_spec = json.load(f)
                    except Exception as e:
                        print(f"Failed to load source feature_spec: {e}")

        # 복사한 설정 반영
        if source_config:
            # SYMBOL, INTERVAL, SESSION 관련 키를 제외한 모든 설정을 원본에서 동적 복사
            for key, val in source_config.items():
                if key not in project_store.IDENTITY_KEYS and not str(key).startswith("_"):
                    config[key] = val

            # 인터벌이 다르면 '캔들 수' 로 저장된 설정은 의미가 달라진다 (10s 의 12캔들=2분, 1m 의 12캔들=12분)
            source_interval = project_store.interval_of_market_dir(copy_from_market_dir)
            if source_interval and source_interval != interval:
                base = load_base_config(PROJECT_NAME)
                config.pop("TARGET_HORIZON_CONFIG", None)
                if "TARGET_HORIZON_CONFIG" in base:
                    config["TARGET_HORIZON_CONFIG"] = base["TARGET_HORIZON_CONFIG"]
                src_opt = dict(config.get("OPTUNA_CONFIG") or {})
                base_opt = base.get("OPTUNA_CONFIG") or {}
                for k in [k for k in src_opt if k.endswith("_candles_range")]:
                    if k in base_opt:
                        src_opt[k] = base_opt[k]
                    else:
                        src_opt.pop(k, None)
                config["OPTUNA_CONFIG"] = src_opt
                create_warnings.append(
                    f"원본({copy_from_market_dir})과 인터벌이 달라 캔들 수 기반 설정(예측 지평·보유 캔들 범위)을 기본값으로 "
                    f"되돌렸습니다. 지표 파라미터(bars 단위)도 {interval} 기준으로 다시 확인하세요.")

            # 사용자가 모달에서 명시적으로 수수료/슬리피지를 입력한 경우 덮어쓰기
            if req_fee is not None:
                config['FEE_UPBIT'] = float(req_fee)
                config['FEE_RATE'] = float(req_fee)
            if req_slippage is not None:
                config['SLIPPAGE'] = float(req_slippage)

        # [덮어쓰기 방지 및 기본값 보장] 신규 프로젝트 생성 시 기존 환경에 상관없이 WFA 모드(wfa_enabled = True)를 기본값으로 강제 설정합니다.
        if "OPTUNA_CONFIG" not in config or not isinstance(config["OPTUNA_CONFIG"], dict):
            config["OPTUNA_CONFIG"] = {}
        config["OPTUNA_CONFIG"]["wfa_enabled"] = True
        if req_fee is not None:
            config["OPTUNA_CONFIG"]["fee_upbit"] = float(req_fee)
            config["FEE_UPBIT"] = float(req_fee)
            config["FEE_RATE"] = float(req_fee)
        elif "fee_upbit" not in config["OPTUNA_CONFIG"]:
            config["OPTUNA_CONFIG"]["fee_upbit"] = config.get("FEE_UPBIT", 0.0005)
            
        if req_slippage is not None:
            config["OPTUNA_CONFIG"]["slippage"] = float(req_slippage)
            config["SLIPPAGE"] = float(req_slippage)
        elif "slippage" not in config["OPTUNA_CONFIG"]:
            config["OPTUNA_CONFIG"]["slippage"] = config.get("SLIPPAGE", 0.0006)

        config['ACTIVE_SESSION_ID'] = session_id
        project_store.create_project_dir(new_ref)

        # 1. best_params 생성 (초기값 메타데이터 포함)
        bp = {
            'opt_session_id': session_id,
            'memo': memo,
            'train_start': config.get('TRAIN_START', 'N/A'),
            'train_end': config.get('TRAIN_END', 'N/A'),
            'opt_start': config.get('OPT_START', 'N/A'),
            'opt_end': config.get('OPT_END', 'N/A'),
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'optuna_score': 0.0,
            # 기본 파라미터 셋업
            'take_profit': 2.5,
            'stop_loss': 1.5,
            'predict_ahead_candles': 24,
            'max_hold_candles': 24,
            'buy_threshold': 0.55,
            'cooldown_minutes': 3,
            'learning_rate': 0.05,
            'max_depth': 6,
            'num_leaves': 31,
            'min_child_samples': 20
        }
        
        # 소스 best_params가 있으면 덮어쓰기
        if source_bp:
            for param_key in ['take_profit', 'stop_loss', 'predict_ahead_candles', 'max_hold_candles',
                              'buy_threshold', 'cooldown_minutes',
                              'learning_rate', 'max_depth', 'num_leaves', 'min_child_samples',
                              'train_start', 'train_end', 'opt_start', 'opt_end', 'optuna_score']:
                if param_key in source_bp:
                    bp[param_key] = source_bp[param_key]
        
        with open(os.path.join(model_dir, f"best_params_{interval}.json"), 'w', encoding='utf-8') as f:
            json.dump(bp, f, indent=4, ensure_ascii=False)
            
        # 2. feature_spec.json 초기화 (복사 원본 → 없으면 이 프로젝트 기본 설정의 지표 풀)
        base_fc = config.get("FEATURE_CONFIG", {}) or {}
        raw_inherited_features = source_spec.get("features_order") or source_spec.get("feature_names") or base_fc.get("enabled_features", [])
        inherited_features = filter_compatible_features(symbol, raw_inherited_features, session_id=session_id)
        
        # 호환되지 않는 지표의 파라미터 및 페널티도 사전 제외
        compatible_set = set(inherited_features)
        raw_params = source_spec.get("feature_params") or base_fc.get("feature_params", {})
        inherited_params = {k: v for k, v in raw_params.items() if k in compatible_set}
        # 신규 프로젝트는 기본적으로 지표 페널티 없음 (모든 지표 가중치 1.0 정상값 초기화)
        inherited_penalties = {}

        removed_count = len(raw_inherited_features) - len(inherited_features)
        if removed_count > 0:
            create_warnings.append(
                f"대상 자산군({symbol})과 호환되지 않는 타 시장 전용 지표 {removed_count}개가 신규 프로젝트에서 자동 제외되었습니다."
            )

        feature_spec = {
            "session_id": session_id,
            "symbol": symbol,
            "interval": interval,
            "features_order": inherited_features,
            "feature_params": inherited_params,
            "param_policy": "scale_v2",
        }
        with open(os.path.join(model_dir, "feature_spec.json"), 'w', encoding='utf-8') as f:
            json.dump(feature_spec, f, indent=4, ensure_ascii=False)
            
        # 2.5 학습된 모델 가중치는 복사하지 않는다 — model/ 에는 이 프로젝트가 직접 검증(또는 학습)한 모델만 둔다

        # 3. config_snapshot.json 저장 (FEATURE_CONFIG 보장 및 부적합 지표 필터링)
        if "FEATURE_CONFIG" not in config or not config["FEATURE_CONFIG"].get("enabled_features"):
            config["FEATURE_CONFIG"] = {
                "enabled_features": inherited_features,
                "active_features": inherited_features,
                "feature_params": inherited_params,
                "feature_penalties": inherited_penalties
            }
        else:
            cur_fc = config["FEATURE_CONFIG"]
            cur_enabled = cur_fc.get("enabled_features", [])
            filtered_enabled = filter_compatible_features(symbol, cur_enabled, session_id=session_id)
            cur_fc["enabled_features"] = filtered_enabled
            cur_fc["active_features"] = filter_compatible_features(symbol, cur_fc.get("active_features", filtered_enabled), session_id=session_id)
            cur_fc["feature_params"] = {k: v for k, v in cur_fc.get("feature_params", {}).items() if k in set(filtered_enabled)}
            cur_fc["feature_penalties"] = {}
        # 새 프로젝트는 봉 주기·예측 지평 규칙의 지표 파라미터 정책을 쓴다 (기존 프로젝트·배포 모델은 legacy 유지)
        from shared.features.param_policy import POLICY_V2
        config["FEATURE_CONFIG"]["param_policy"] = POLICY_V2
        # 예측 지평: 화면은 설정이 없으면 인터벌 표준값(잠금)을 보여 주지만 학습은 탐색 범위 중앙을 썼다 —
        # 화면에 보이는 값이 곧 학습 값이 되도록 생성 시점에 기록한다
        if not isinstance(config.get("TARGET_HORIZON_CONFIG"), dict) or not config["TARGET_HORIZON_CONFIG"].get("locked_predict_ahead"):
            from CAiModelBaker.core.horizon_registry import get_horizon_context
            _hz = get_horizon_context(interval)
            config["TARGET_HORIZON_CONFIG"] = {"mode": "locked", "locked_predict_ahead": _hz["locked_predict_ahead"],
                                               "locked_max_hold": _hz["locked_max_hold"]}
        config["FEE_UPBIT"] = config["OPTUNA_CONFIG"].get("fee_upbit", config.get("FEE_UPBIT", 0.0005))
        config["FEE_RATE"] = config["FEE_UPBIT"]
        config["SLIPPAGE"] = config["OPTUNA_CONFIG"].get("slippage", config.get("SLIPPAGE", 0.0006))
        config[project_store.META_KEY] = {
            "memo": memo,
            "created_at": bp['created_at'],
            "created_from": (f"{copy_from_market_dir}/{copy_from_session_id}" if copy_from_session_id else "defaults"),
        }
        project_store.write_project_config(new_ref, config)

        # 새 프로젝트를 연다 (전역에는 포인터만 남는다) → 지표 ID 정규화 저장은 이 프로젝트 파일에 기록된다
        set_active_project_ref(new_ref, PROJECT_NAME)
        feat_cfg = config.get("FEATURE_CONFIG", {})
        save_feature_config(
            enabled_features=feat_cfg.get("enabled_features", []),
            feature_params=feat_cfg.get("feature_params", {}),
            optuna_config=config.get("OPTUNA_CONFIG"),
            active_features=feat_cfg.get("active_features", feat_cfg.get("enabled_features", [])),
            feature_penalties=feat_cfg.get("feature_penalties", {}),
            regime_features=feat_cfg.get("regime_features", {}),
        )

        # 4. README.md 생성
        readme_content = f"""# AI Optimization Session: {session_id}
- **생성 일시**: {bp['created_at']}
- **대상 자산**: {symbol} ({interval})
- **설명/메모**: {memo if memo else '신규 생성된 프로젝트입니다.'}
- **Optuna Score**: 0.000000
"""
        if copy_from_session_id:
            readme_content += f"- **복사 원본 세션**: {copy_from_market_dir}/{copy_from_session_id}\n"
            
        with open(os.path.join(deploy_dir, "README.md"), 'w', encoding='utf-8') as f:
            f.write(readme_content)
            
        # 신규 프로젝트 생성 시 메모리 상의 이전 AutoLab 진행 상태 및 리더보드 완전 리셋 (세션 격리)
        reset_research_state(session_id)

        return {"status": "ok", "session_id": session_id, "project_ref": new_ref, "warnings": create_warnings,
                "message": "신규 프로젝트가 성공적으로 생성 및 활성화되었습니다."
                           + (" ⚠️ " + " ".join(create_warnings) if create_warnings else "")}
    except project_store.ProjectExistsError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def _research_is_running() -> bool:
    try:
        return bool(get_research_status().get("running"))
    except Exception:
        return False


@app.post("/api/session/load")
async def api_load_session(data: dict):
    """프로젝트 열기 — 전역에는 포인터만 두고, 설정 원본은 그 프로젝트 폴더의 config_snapshot.json 이다."""
    session_id = data.get("session_id")
    market_dir = data.get("market_dir")

    if not session_id:
        return {"status": "error", "error": "필수 파라미터(session_id) 누락"}
    if not _is_safe_seg(session_id, market_dir):
        return _bad_path_response()
    # 실행 중에 프로젝트를 바꾸면 실행 중인 연구의 설정 저장이 다른 프로젝트로 흘러간다
    if _research_is_running():
        return {"status": "error", "error": "자동연구 실행 중에는 프로젝트를 바꿀 수 없습니다. 중단 후 다시 시도하세요."}

    try:
        ref = project_store.resolve(session_id, market_dir=market_dir)
    except project_store.AmbiguousProjectError as e:
        return {"status": "error", "error": str(e)}
    if not ref:
        where = f"{market_dir}/{session_id}" if market_dir else session_id
        return {"status": "error", "error": f"해당 프로젝트 폴더를 찾을 수 없습니다: {where}"}
    deploy_sess_path = project_store.project_dir(ref)

    try:
        stored = project_store.read_project_config(ref)
        spec_path = os.path.join(deploy_sess_path, "model", "feature_spec.json")
        spec = {}
        if os.path.exists(spec_path):
            try:
                with open(spec_path, "r", encoding="utf-8") as sf:
                    spec = json.load(sf)
            except Exception:
                spec = {}

        if not stored:
            # 레거시 세션(스냅샷 없음): 기본값 + model/ 의 학습 기간·지표로 프로젝트 설정을 새로 만든다
            if not spec:
                return {"status": "error", "error": "config_snapshot.json 과 model/feature_spec.json 이 모두 없습니다."}
            stored = load_base_config(PROJECT_NAME)
            param_files = sorted(f for f in os.listdir(os.path.join(deploy_sess_path, "model"))
                                 if f.startswith("best_params_") and f.endswith(".json"))
            if param_files:
                with open(os.path.join(deploy_sess_path, "model", param_files[0]), "r", encoding="utf-8") as f:
                    bp = json.load(f)
                for k_cfg, k_bp in (("TRAIN_START", "train_start"), ("TRAIN_END", "train_end"),
                                    ("OPT_START", "opt_start"), ("OPT_END", "opt_end"),
                                    ("TEST_START", "test_start"), ("TEST_END", "test_end")):
                    if bp.get(k_bp):
                        stored[k_cfg] = bp[k_bp]
            if spec.get("symbol"):
                stored["SYMBOL_UPBIT"] = spec["symbol"]

        # 지표 설정: 스냅샷의 FEATURE_CONFIG(프로젝트 원본 지표 풀) 최우선, 없을 때만 model/feature_spec.json
        feat_cfg = dict(stored.get("FEATURE_CONFIG", {}) or {})
        feat_params = feat_cfg.get("feature_params", {}) or {}
        enabled_feats = feat_cfg.get("enabled_features") or feat_cfg.get("active_features")
        if not enabled_feats and spec:
            enabled_feats = spec.get("features_order") or spec.get("feature_names", [])
            if not feat_params:
                feat_params = spec.get("feature_params", {})
        if not enabled_feats:
            enabled_feats = list(DEFAULT_FEATURE_REGISTRY.keys())

        feat_cfg.update({
            "enabled_features": enabled_feats,
            "active_features": enabled_feats,
            "feature_params": feat_params,
            "feature_penalties": feat_cfg.get("feature_penalties", {}),
            "regime_features": feat_cfg.get("regime_features", {}),
        })
        stored["FEATURE_CONFIG"] = feat_cfg
        project_store.write_project_config(ref, stored)

        # 프로젝트 열기 (포인터 교체) → 이후 load_config()/save_config() 는 이 프로젝트 폴더를 읽고 쓴다
        set_active_project_ref(ref, PROJECT_NAME)
        save_feature_config(
            enabled_features=enabled_feats,
            feature_params=feat_params,
            optuna_config=stored.get("OPTUNA_CONFIG"),
            active_features=enabled_feats,
            feature_penalties=feat_cfg.get("feature_penalties", {}),
            regime_features=feat_cfg.get("regime_features", {}),
        )

        # 프로젝트 전환 시 메모리 상의 이전 AutoLab 진행 상태 리셋
        reset_research_state(session_id)
        return {"status": "ok", "project_ref": ref, "message": f"프로젝트 {ref} 열기 완료"}
    except Exception as e:
        return {"status": "error", "error": f"프로젝트 열기 실패: {e}"}

@app.post("/api/features/config")
async def api_save_feature_config(data: dict):
    """지표 설정 저장 (글로벌 및 활성 세션 스냅샷 완전 동기화)"""
    enabled_features = data.get("enabled_features", [])
    feature_params = data.get("feature_params", {})
    feature_penalties = data.get("feature_penalties", {})
    regime_features = data.get("regime_features", None)
    optuna_config = data.get("optuna_config", None)
    
    config = load_config()
    if optuna_config and isinstance(optuna_config, dict):
        current_optuna = config.get('OPTUNA_CONFIG', {})
        if isinstance(current_optuna, dict):
            merged_optuna = dict(current_optuna)
            merged_optuna.update(optuna_config)
            optuna_config = merged_optuna
        
        config['OPTUNA_CONFIG'] = optuna_config
        if 'fee_upbit' in optuna_config:
            config['FEE_UPBIT'] = optuna_config['fee_upbit']
        if 'slippage' in optuna_config:
            config['SLIPPAGE'] = optuna_config['slippage']
        save_config(config)

    if "reference_symbols" in data:
        config["reference_symbols"] = data["reference_symbols"]
        config["REFERENCE_SYMBOLS"] = data["reference_symbols"]
        save_config(config)

    target_horizon_config = data.get("target_horizon_config", None)
    if target_horizon_config and isinstance(target_horizon_config, dict):
        config["TARGET_HORIZON_CONFIG"] = target_horizon_config
        save_config(config)
        
    # 🛡️ [지표 목록 소실 방지] enabled_features 키가 오지 않은 저장(파라미터만 변경 등)은 기존 목록 유지
    if not ("enabled_features" in data and isinstance(data["enabled_features"], list)):
        enabled_features = config.get("FEATURE_CONFIG", {}).get("enabled_features", []) or []

    # 열린 프로젝트가 있으면 save_feature_config 가 곧 그 프로젝트의 config_snapshot.json 에 기록한다.
    # 학습된 모델의 model/feature_spec.json 은 모델 가중치와 1:1 이라 여기서 건드리지 않는다
    # (종전: 화면의 feature_params 로 배포 모델 명세를 고쳐 학습·실전 지표 계산이 어긋날 수 있었다).
    save_feature_config(
        enabled_features, 
        feature_params, 
        optuna_config, 
        active_features=enabled_features, 
        feature_penalties=feature_penalties,
        regime_features=regime_features
    )

    return {"status": "ok", "message": "Feature config saved"}

@app.post("/api/features/apply_locked_horizon")
async def api_apply_locked_horizon(data: dict):
    """지정된 캔들(predict_ahead_candles)을 사정거리 잠금 모드로 active.json 및 활성 세션 스냅샷에 즉시 동기화 반영"""
    try:
        candles = int(data.get("candles", 6))
        config = load_config()
        
        horizon_cfg = config.get("TARGET_HORIZON_CONFIG", {})
        if not isinstance(horizon_cfg, dict):
            horizon_cfg = {}
        
        horizon_cfg["mode"] = "locked"
        horizon_cfg["locked_predict_ahead"] = candles
        horizon_cfg["locked_max_hold"] = candles
        if "candidate_horizons" not in horizon_cfg:
            horizon_cfg["candidate_horizons"] = [3, 6, 12, 24]
        
        config["TARGET_HORIZON_CONFIG"] = horizon_cfg
        save_config(config)  # 열린 프로젝트면 그 폴더의 config_snapshot.json 에 기록

        return {
            "status": "ok",
            "mode": "locked",
            "candles": candles,
            "message": f"사정거리 잠금 모드가 {candles}캔들로 즉시 적용 및 저장되었습니다."
        }
    except Exception as e:
        logger.error(f"apply_locked_horizon 실패: {e}")
        return {"status": "error", "error": str(e)}
        
@app.post("/api/reference_symbols/download")
async def api_download_reference_symbol(payload: dict, background_tasks: BackgroundTasks):
    """참조 대장주 원클릭 백그라운드 다운로드 API (코인/주식 자동 분기)"""
    sym = payload.get("symbol")
    interval = payload.get("interval", "1m")
    if not sym:
        return {"status": "error", "message": "종목 코드가 지정되지 않았습니다."}
    
    config = load_config()
    start_ym = config.get("DATA_COLLECT_START", "2025-01")
    end_ym = config.get("DATA_COLLECT_END", "2026-08")
    
    scope = detect_symbol_market_scope(sym)
    if scope == "crypto":
        background_tasks.add_task(
            run_archive_download_pipeline,
            config, "UPBIT", sym, start_ym, end_ym, interval,
            local_log=print
        )
    else:
        background_tasks.add_task(
            run_stock_download_pipeline,
            config, sym, start_ym, end_ym, interval,
            local_log=print
        )
    return {"status": "ok", "message": f"{sym} ({interval}) 과거 데이터 수집이 백그라운드에서 시작되었습니다."}


# ─────────────────────────────────────────────────────────────────────────────
# 🏛️ Symbol Relations & Data Vault API (하드코딩 제거 & 동적 리소스 연동)
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/api/symbols/relations")
async def api_get_symbol_relations(target: Optional[str] = None):
    """지정된 타겟 종목(또는 현재 활성 타겟)의 대장주 및 매크로 상관관계 조회 (장착된 팩 및 평면화 참조 포함)"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        config = load_config()
        t_sym = target or get_target_symbol(config)
        clean_target = t_sym.replace("KRW-", "")
        
        target_info = SymbolRelationsManager.find_symbol_info(clean_target)
        refs = SymbolRelationsManager.get_relations_for_target(clean_target)
        
        # 타겟에 장착된 팩(그룹) 상세 목록 수화
        target_group_ids = SymbolRelationsManager.get_groups_for_target(clean_target)
        all_groups = SymbolRelationsManager.load_all_groups()
        attached_groups = [
            all_groups[gid] for gid in target_group_ids
            if gid in all_groups
        ]
        
        return {
            "status": "ok",
            "target": {
                "symbol": clean_target,
                "name": target_info.get("name", clean_target),
                "market_type": target_info.get("market_type", "KRX_ETF")
            },
            "groups": attached_groups,
            "references": refs
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/symbols/relations/add")
async def api_add_symbol_relation(payload: dict):
    """타겟 종목에 새로운 대장주/매크로 부착 및 symbol_relations.json 영속화"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        config = load_config()
        target_sym = payload.get("target") or get_target_symbol(config)
        ref_data = payload.get("reference")
        if not ref_data or not ref_data.get("symbol"):
            return {"status": "error", "message": "부착할 종목 데이터가 유효하지 않습니다."}

        success = SymbolRelationsManager.add_or_update_reference(target_sym, ref_data)
        if success:
            clean_target = str(target_sym).replace("KRW-", "")
            current_refs = SymbolRelationsManager.get_relations_for_target(clean_target)
            config["REFERENCE_SYMBOLS"] = [
                {"symbol": r["symbol"], "name": r.get("name", r["symbol"])}
                for r in current_refs
            ]
            save_config(config)
            return {"status": "ok", "message": f"{ref_data.get('name', ref_data['symbol'])} 부착 완료"}
        return {"status": "error", "message": "상관관계 저장 실패"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/symbols/relations/remove")
async def api_remove_symbol_relation(payload: dict):
    """타겟 종목에서 대장주/매크로 분리 및 symbol_relations.json 영속화"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        config = load_config()
        target_sym = payload.get("target") or get_target_symbol(config)
        ref_symbol = payload.get("ref_symbol")
        if not ref_symbol:
            return {"status": "error", "message": "분리할 종목 코드가 필요합니다."}

        success = SymbolRelationsManager.remove_reference(target_sym, ref_symbol)
        if success:
            clean_target = str(target_sym).replace("KRW-", "")
            current_refs = SymbolRelationsManager.get_relations_for_target(clean_target)
            config["REFERENCE_SYMBOLS"] = [
                {"symbol": r["symbol"], "name": r.get("name", r["symbol"])}
                for r in current_refs
            ]
            save_config(config)
            return {"status": "ok", "message": f"{ref_symbol} 분리 완료"}
        return {"status": "error", "message": "상관관계 삭제 실패"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 📦 피처 그룹(Factor Basket) 관리 REST API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/symbols/groups")
async def api_get_symbol_groups():
    """등록된 모든 피처 그룹(Factor Basket) 카탈로그 조회 및 타겟별 사용 현황 반환"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        groups = SymbolRelationsManager.load_all_groups()
        all_relations = SymbolRelationsManager.load_all_relations()
        
        # 각 팩을 사용 중인 타겟 심볼 목록 집계
        usage_map = {gid: [] for gid in groups}
        for t_sym, t_info in all_relations.items():
            for gid in t_info.get("groups", []) or []:
                if gid in usage_map:
                    usage_map[gid].append({
                        "symbol": t_sym,
                        "name": t_info.get("name", t_sym)
                    })
        
        result_groups = []
        for gid, ginfo in groups.items():
            item = dict(ginfo)
            item["used_by_targets"] = usage_map.get(gid, [])
            result_groups.append(item)
            
        return {"status": "ok", "groups": result_groups}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/symbols/groups/save")
async def api_save_symbol_group(payload: dict):
    """피처 그룹 생성 또는 메타데이터 갱신"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        group_id = payload.get("id") or payload.get("group_id")
        if not group_id:
            return {"status": "error", "message": "그룹 ID가 필요합니다."}
        
        success = SymbolRelationsManager.create_or_update_group(group_id, payload)
        if success:
            return {"status": "ok", "message": f"팩 '{payload.get('name', group_id)}' 저장 완료"}
        return {"status": "error", "message": "팩 저장 실패"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/symbols/groups/delete")
async def api_delete_symbol_group(payload: dict):
    """피처 그룹 삭제"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        group_id = payload.get("group_id") or payload.get("id")
        force = bool(payload.get("force", False))
        if not group_id:
            return {"status": "error", "message": "삭제할 그룹 ID가 필요합니다."}
        
        success, msg = SymbolRelationsManager.delete_group(group_id, force=force)
        if success:
            return {"status": "ok", "message": msg}
        return {"status": "error", "message": msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/symbols/groups/member/add")
async def api_add_group_member(payload: dict):
    """팩에 종목 추가"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        group_id = payload.get("group_id")
        symbol = payload.get("symbol")
        role = payload.get("role")
        default_price = payload.get("default_price")
        if not group_id or not symbol:
            return {"status": "error", "message": "그룹 ID와 추가할 종목 코드가 필요합니다."}
        
        m_data = {"symbol": symbol}
        if role:
            m_data["role"] = role
        if default_price is not None:
            m_data["default_price"] = float(default_price)
            
        success = SymbolRelationsManager.add_member_to_group(group_id, m_data)
        if success:
            return {"status": "ok", "message": f"{symbol} 종목이 팩에 추가되었습니다."}
        return {"status": "error", "message": "팩 종목 추가 실패"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/symbols/groups/member/remove")
async def api_remove_group_member(payload: dict):
    """팩에서 종목 제외"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        group_id = payload.get("group_id")
        symbol = payload.get("symbol")
        if not group_id or not symbol:
            return {"status": "error", "message": "그룹 ID와 제외할 종목 코드가 필요합니다."}
        
        success = SymbolRelationsManager.remove_member_from_group(group_id, symbol)
        if success:
            return {"status": "ok", "message": f"{symbol} 종목이 팩에서 제외되었습니다."}
        return {"status": "error", "message": "팩 종목 제외 실패"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/symbols/relations/attach_group")
async def api_attach_group_to_target(payload: dict):
    """타겟 종목에 피처 그룹 바스켓 장착"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        config = load_config()
        target_sym = payload.get("target") or get_target_symbol(config)
        group_id = payload.get("group_id")
        if not group_id:
            return {"status": "error", "message": "장착할 팩 ID가 필요합니다."}
        
        success = SymbolRelationsManager.attach_group_to_target(target_sym, group_id)
        if success:
            clean_target = str(target_sym).replace("KRW-", "")
            current_refs = SymbolRelationsManager.get_relations_for_target(clean_target)
            config["REFERENCE_SYMBOLS"] = [
                {"symbol": r["symbol"], "name": r.get("name", r["symbol"])}
                for r in current_refs
            ]
            save_config(config)
            return {"status": "ok", "message": f"팩 '{group_id}'이(가) 타겟에 장착되었습니다."}
        return {"status": "error", "message": "팩 장착 실패"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/symbols/relations/detach_group")
async def api_detach_group_from_target(payload: dict):
    """타겟 종목에서 피처 그룹 바스켓 분리"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        config = load_config()
        target_sym = payload.get("target") or get_target_symbol(config)
        group_id = payload.get("group_id")
        if not group_id:
            return {"status": "error", "message": "분리할 팩 ID가 필요합니다."}
        
        success = SymbolRelationsManager.detach_group_from_target(target_sym, group_id)
        if success:
            clean_target = str(target_sym).replace("KRW-", "")
            current_refs = SymbolRelationsManager.get_relations_for_target(clean_target)
            config["REFERENCE_SYMBOLS"] = [
                {"symbol": r["symbol"], "name": r.get("name", r["symbol"])}
                for r in current_refs
            ]
            save_config(config)
            return {"status": "ok", "message": f"팩 '{group_id}'이(가) 타겟에서 분리되었습니다."}
        return {"status": "error", "message": "팩 분리 실패"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _scan_project_target_symbols() -> List[str]:
    """deploy/ 아래 실존 프로젝트들의 타겟 심볼 목록 (고아 설정 판별용).

    프로젝트 폴더 기준으로 센다 — 종전에는 model/feature_spec.json 이 있어야 셌는데, model/ 은 이제 합격 모델
    자리라 탈락만 한 프로젝트는 비어 있을 수 있다 (그 종목 설정이 '고아' 로 오판되면 안 된다).
    """
    symbols: List[str] = []
    try:
        for ref in project_store.list_projects():
            snap = project_store.read_project_config(ref)
            sym = project_store.identity_of(ref, snap).get("SYMBOL_UPBIT")
            if not snap:
                spec_path = os.path.join(project_store.project_dir(ref), "model", "feature_spec.json")
                if not os.path.exists(spec_path):
                    continue
                try:
                    with open(spec_path, "r", encoding="utf-8") as sf:
                        sym = (json.load(sf) or {}).get("symbol") or sym
                except Exception:
                    continue
            if sym:
                symbols.append(sym)
    except Exception as e:
        print(f"[OrphanScan] deploy/ 스캔 실패: {e}")
    return symbols


@app.get("/api/symbols/relations/orphans")
async def api_get_orphan_symbol_relations():
    """symbol_relations.json에 타겟으로 선언되어 있으나 연결된 학습 프로젝트가 없는 미사용 타겟 선언 조회"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        active_symbols = _scan_project_target_symbols()
        orphans = SymbolRelationsManager.list_orphan_targets(active_symbols)
        return {"status": "ok", "count": len(orphans), "orphans": orphans}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/symbols/relations/remove_target")
async def api_remove_symbol_relation_target(payload: dict):
    """타겟 선언(및 그 종목의 참조종목 설정)을 symbol_relations.json에서 제거. 종목 원장/원천 데이터는 유지"""
    try:
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        target_sym = payload.get("target")
        confirm_symbol = payload.get("confirm_symbol")
        if not target_sym:
            return {"status": "error", "message": "타겟 심볼이 지정되지 않았습니다."}
        if not confirm_symbol or confirm_symbol.strip().upper() != target_sym.strip().upper():
            return {"status": "error", "message": "삭제 확인을 위해 타겟 심볼을 정확히 입력해야 합니다."}
        success = SymbolRelationsManager.remove_target(target_sym)
        return {
            "status": "ok" if success else "error",
            "message": f"{target_sym} 타겟 선언이 해제되었습니다." if success else f"등록되지 않은 타겟입니다: {target_sym}"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/data_catalog/sources")
async def api_get_data_catalog_sources():
    """원천 데이터 전 항목의 출처, 가용 범위, 스킵 규칙 및 폴백 정책 카탈로그 조회"""
    try:
        from shared.data_library.data_catalog_manager import DataCatalogManager
        sources = DataCatalogManager.get_all_sources()
        return {"status": "ok", "count": len(sources), "sources": sources}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/data_vault/quality")
async def api_get_data_vault_quality():
    """현재 마스터 데이터셋 스냅샷의 컬럼별 원천 데이터 결측 및 보간(Imputation) 현황 조회"""
    try:
        try:
            from core.data_manager import get_snapshot_path
        except ImportError:
            from CAiModelBaker.core.data_manager import get_snapshot_path
        from shared.data_library.data_catalog_manager import DataCatalogManager
        config = load_config()
        snap_path = get_snapshot_path(config)
        meta_path = snap_path.replace('.csv', '.meta.json')
        
        # 1. meta.json에 data_quality가 저장되어 있으면 즉시 반환
        if os.path.exists(meta_path):
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
                if "data_quality" in meta and meta["data_quality"]:
                    return {
                        "status": "ok",
                        "exists": True,
                        "snapshot_path": snap_path,
                        "built_at": meta.get("built_at"),
                        "total_rows": meta.get("rows", 0),
                        "quality_report": meta["data_quality"]
                    }
        
        # 2. meta.json이 없거나 과거 포맷이면, csv를 실시간 점검하여 리포트 생성
        if os.path.exists(snap_path):
            import pandas as pd
            df = pd.read_csv(snap_path)
            _, quality_report = DataCatalogManager.apply_fallback_to_dataframe(df)
            return {
                "status": "ok",
                "exists": True,
                "snapshot_path": snap_path,
                "total_rows": len(df),
                "quality_report": quality_report
            }

        return {
            "status": "ok",
            "exists": False,
            "message": "빌드된 스냅샷 데이터셋이 없습니다."
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/data_vault/status")
async def api_get_data_vault_status():
    """타겟 종목 및 동적 연결된 모든 참조 풀의 시간단위별(1m~1d) 캔들 보유 현황, 최근 종가, 최근 일시 실시간 조회"""
    try:
        from shared.data_library.candle_vault import CandleVault
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        vault = CandleVault()
        config = load_config()
        target_sym = get_target_symbol(config)
        clean_target = target_sym.replace("KRW-", "")
        
        target_info = SymbolRelationsManager.find_symbol_info(clean_target)
        symbols_to_check = [
            {"symbol": clean_target, "name": target_info.get("name", clean_target), "role": "target"}
        ]
        
        # symbol_relations.json에서 동적으로 연결된 대장주/매크로 추출 (하드코딩 배제)
        relations = SymbolRelationsManager.get_relations_for_target(clean_target)
        for r in relations:
            r_s = r.get("symbol", "")
            if r_s and not any(s["symbol"] == r_s for s in symbols_to_check):
                symbols_to_check.append({
                    "symbol": r_s,
                    "name": r.get("name", r_s),
                    "role": r.get("role", "reference")
                })
        
        intervals = ["1m", "3m", "5m", "15m", "30m", "1d"]
        result = []
        
        for item in symbols_to_check:
            sym = item["symbol"]
            status_by_int = {}
            last_price = None
            last_time = None
            
            for interval in intervals:
                df = vault.get_candles(sym, interval, use_cache=True)
                if not df.empty and "timestamp" in df.columns:
                    cnt = len(df)
                    st = str(df["timestamp"].iloc[0])[:16]
                    ed = str(df["timestamp"].iloc[-1])[:16]
                    
                    # 1분봉 시세에서 실측 최근 종가 추출
                    if interval == "1m":
                        c_col = [c for c in ("target_close", "close", "upbit_close") if c in df.columns]
                        if c_col:
                            last_price = float(df[c_col[0]].iloc[-1])
                        last_time = ed
                        
                    status_by_int[interval] = {
                        "count": cnt,
                        "start": st,
                        "end": ed,
                        "available": True
                    }
                else:
                    status_by_int[interval] = {
                        "count": 0,
                        "start": "-",
                        "end": "-",
                        "available": False
                    }
                    
            result.append({
                "symbol": sym,
                "name": item["name"],
                "role": item["role"],
                "last_price": last_price,
                "last_time": last_time,
                "intervals": status_by_int
            })

        # [META] 마스터 데이터셋 기간 정보 — meta.json 우선, 없으면 CSV fallback
        master_meta = {}
        try:
            import json as _json
            from core.data_manager import get_snapshot_path
            snap_path = get_snapshot_path(config)
            meta_path = snap_path.replace('.csv', '.meta.json')
            if os.path.exists(meta_path):
                with open(meta_path, encoding='utf-8') as _mf:
                    master_meta = _json.load(_mf)
            elif os.path.exists(snap_path):
                import pandas as _pd
                _df = _pd.read_csv(snap_path, usecols=['timestamp'])
                master_meta = {
                    "rows": len(_df),
                    "timestamp_min": str(_df['timestamp'].min()),
                    "timestamp_max": str(_df['timestamp'].max()),
                    "built_at": None,
                }
        except Exception:
            pass

        return {"status": "ok", "items": result, "master_dataset": master_meta}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/resample")
async def api_resample_data_vault(payload: dict):
    """특정 종목의 1분봉 원천 데이터로부터 지정된 시간단위 캔들 즉시 리샘플링 및 캐싱"""
    try:
        from shared.data_library.candle_vault import CandleVault
        vault = CandleVault()
        symbol = payload.get("symbol")
        interval = payload.get("interval", "5m")
        if not symbol:
            return {"status": "error", "message": "종목 코드가 필요합니다."}
            
        vault.clear_cache(symbol, interval)
        df = vault.get_candles(symbol, interval, use_cache=False)
        if df.empty:
            return {"status": "error", "message": f"{symbol}의 원천 1분봉 데이터가 없어 리샘플링할 수 없습니다."}
            
        return {
            "status": "ok",
            "message": f"{symbol} ({interval}) 리샘플링 완료: 총 {len(df):,}개 캔들 생성",
            "count": len(df),
            "start": str(df["timestamp"].iloc[0])[:16],
            "end": str(df["timestamp"].iloc[-1])[:16]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/probe_feed")
async def api_probe_feed(payload: dict):
    """지정된 종목 또는 전체 종목의 실시간 피드 준비 상태를 안전하게 검증 (CTrade 세션 보호 및 장외 가이드)"""
    try:
        from shared.data_library.live_feed_prober import LiveFeedProber
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        symbol = payload.get("symbol")
        market_type = payload.get("market_type")
        
        if symbol:
            if not market_type:
                info = SymbolRelationsManager.find_symbol_info(symbol)
                market_type = info.get("market_type", "KRX_ETF")
            res = LiveFeedProber.probe_symbol(symbol, market_type)
            return {"status": "ok", "probe": res}

        # symbol이 비어있으면 현재 타겟 및 등록된 대장주 전체 프로브
        config = load_config()
        target_sym = payload.get("target") or get_target_symbol(config)
        clean_target = str(target_sym).replace("KRW-", "")
        
        target_info = SymbolRelationsManager.find_symbol_info(clean_target)
        relations = SymbolRelationsManager.get_relations_for_target(clean_target)
        
        symbols_to_probe = [
            {"symbol": clean_target, "market_type": target_info.get("market_type", "KRX_ETF")}
        ]
        for r in relations:
            r_s = r.get("symbol")
            if r_s and not any(s["symbol"] == r_s for s in symbols_to_probe):
                r_info = SymbolRelationsManager.find_symbol_info(r_s)
                symbols_to_probe.append({
                    "symbol": r_s,
                    "market_type": r_info.get("market_type", "KRX_STOCK")
                })
        
        probe_results = {}
        for s in symbols_to_probe:
            probe_results[s["symbol"]] = LiveFeedProber.probe_symbol(s["symbol"], s["market_type"])
            
        return {"status": "ok", "probes": probe_results}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/compose")
async def api_compose_features(payload: dict):
    """타겟 종목 베이스 데이터에 설정된 대장주 및 매크로 지표를 0.5초 내 온디맨드 즉시 결합"""
    try:
        from shared.data_library.feature_composer import FeatureComposer
        from shared.data_library.candle_vault import CandleVault
        composer = FeatureComposer()
        config = load_config()
        
        target_sym = get_target_symbol(config)
        clean_target = target_sym.replace("KRW-", "")
        interval = config.get("DATA_INTERVAL") or config.get("INTERVAL", "1m")
        
        # 타겟 베이스 캔들 로드
        target_base = composer.candle_vault.get_candles(clean_target, interval)
        if target_base.empty:
            # data_snapshot이 있는지 확인
            snap_path = get_snapshot_path(config)
            if os.path.exists(snap_path):
                import pandas as pd
                target_base = pd.read_csv(snap_path)
        
        if target_base.empty:
            return {"status": "error", "message": f"타겟 종목({clean_target})의 베이스 데이터가 없습니다. 먼저 원천 데이터를 수집해주세요."}
            
        ref_specs = config.get("REFERENCE_SYMBOLS", [])
        composed = composer.compose(target_base, ref_specs, target_symbol=clean_target, target_interval=interval)

        # ──────────────────────────────────────────────────────────────────────
        # [T-2 Gold 계약] 학습 데이터셋(data_snapshot_*.csv) 을 덮어쓰지 않는다.
        #   · get_snapshot_path() 는 배치 빌더(run_dataset_build_pipeline)의 산출물 경로이며
        #     model_engine / backtester / feature_tuner / feature_screener 가 그대로
        #     학습 입력으로 사용한다.
        #   · compose() 결과는 틱 파생 지표(cvd, trade_bid_ratio), 매크로 선물, 펀딩비,
        #     결측 보간(DataCatalogManager), 무결성 게이트가 없는 '조인 미리보기' 이므로
        #     학습 데이터셋을 대체할 수 없다.
        #   → 별도 프리뷰 파일로만 기록한다 (이중 writer 제거).
        # ──────────────────────────────────────────────────────────────────────
        preview_path = os.path.join(
            os.path.dirname(get_snapshot_path(config)) or "data",
            f"data_vault_preview_{interval}.csv",
        )
        os.makedirs(os.path.dirname(preview_path), exist_ok=True)
        composed.to_csv(preview_path, index=False)

        added_cols = [c for c in composed.columns if c.startswith("ref_")]
        return {
            "status": "ok",
            "message": (
                f"온디맨드 조립 완료(미리보기): {len(composed):,}행, "
                f"대장주 컬럼 {len(added_cols)}개 결합 성공 (재빌드 0초). "
                f"※ 학습 데이터셋은 배치 빌드가 생성하며 본 기능은 이를 대체하지 않습니다."
            ),
            "rows": len(composed),
            "added_columns": added_cols,
            "preview_path": preview_path.replace(os.sep, "/"),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/data_vault/medallion_status")
async def api_get_medallion_status(target: Optional[str] = None, interval: Optional[str] = None):
    """Raw / Normalized / Features 3단계 저장소 무결성 및 파일 현황 실시간 조회 (SSOT 종목 관계 동적 연동)"""
    try:
        from shared.data_library import paths as dpaths
        from shared.data_library.manifest_manager import ManifestManager
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        import glob

        # 1. Raw 현황
        raw_files = []
        _day_dirs = {}      # 틱 일자별 봉인 파일(tick/A{sym}/YYYY-MM-DD.*)은 종목당 한 줄로 묶어 보여준다
        for p in glob.glob(os.path.join(dpaths.raw_root(), "**", "*.*"), recursive=True):
            if p.endswith(".manifest.json") or p.endswith(".bak"):
                continue
            sz = os.path.getsize(p)
            rel = os.path.relpath(p, dpaths.raw_root()).replace(os.sep, "/")
            _m = re.match(r"^(creon/tick/A[^/]+)/\d{4}-\d{2}-\d{2}\.(csv|meta\.json)$", rel)
            if _m:
                d = _day_dirs.setdefault(_m.group(1) + "/", {"path": _m.group(1) + "/", "size_bytes": 0, "has_manifest": True, "day_files": 0})
                d["size_bytes"] += sz
                d["day_files"] += 1 if rel.endswith(".csv") else 0
                continue
            has_man = os.path.exists(ManifestManager.get_manifest_path(p))
            raw_files.append({"path": rel, "size_bytes": sz, "has_manifest": has_man})
        raw_files.extend(_day_dirs.values())

        # 2. Normalized 현황
        norm_files = []
        for p in glob.glob(os.path.join(dpaths.normalized_root(), "**", "*.parquet"), recursive=True):
            sz = os.path.getsize(p)
            has_man = os.path.exists(ManifestManager.get_manifest_path(p))
            rel = os.path.relpath(p, dpaths.normalized_root()).replace(os.sep, "/")
            # 간단 행수
            import pyarrow.parquet as pq
            meta = pq.read_metadata(p)
            rows = meta.num_rows
            norm_files.append({"path": rel, "size_bytes": sz, "rows": rows, "has_manifest": has_man})

        # 3. Features 현황
        feat_files = []
        for p in glob.glob(os.path.join(dpaths.features_root(), "**", "*.parquet"), recursive=True):
            sz = os.path.getsize(p)
            has_man = os.path.exists(ManifestManager.get_manifest_path(p))
            rel = os.path.relpath(p, dpaths.features_root()).replace(os.sep, "/")
            feat_files.append({"path": rel, "size_bytes": sz, "has_manifest": has_man})

        # 4. Coverage Summary (활성 타깃 및 symbol_relations.json 동적 산출)
        config = load_config()
        active_target = str(target or get_target_symbol(config)).strip()
        clean_target = active_target.replace("KRW-", "")
        active_interval = str(interval or config.get("INTERVAL") or "10s").strip().lower()

        from CAiModelBaker.core.feature_composer import FeatureComposer
        composer = FeatureComposer()

        plan = SymbolRelationsManager.join_plan(clean_target)
        dyn_ref_symbols, dyn_macro_symbols = plan["refs"], plan["series"]

        coverage = composer.get_coverage_summary(
            clean_target, 
            active_interval, 
            ref_symbols=dyn_ref_symbols, 
            macro_symbols=dyn_macro_symbols
        )

        return {
            "status": "ok",
            "target": clean_target,
            "interval": active_interval,
            "raw_count": len(raw_files),
            "raw_files": raw_files[:20],
            "normalized_count": len(norm_files),
            "normalized_files": norm_files,
            "features_count": len(feat_files),
            "features_files": feat_files[:20],
            "coverage_summary": coverage,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/build_normalized")
async def api_build_normalized(payload: dict):
    """지정된 자산의 표준 정규화 Parquet 빌드"""
    try:
        from CAiModelBaker.core.normalized_builder import build_normalized_10s_stock, build_normalized_candle_stock
        symbol = payload.get("symbol") or get_target_symbol(load_config())
        interval = payload.get("interval", "10s")
        if interval == "10s":
            out_path = build_normalized_10s_stock(symbol)
        else:
            out_path = build_normalized_candle_stock(symbol, interval=interval)
        return {"status": "ok", "message": f"{symbol} ({interval}) 정규화 Parquet 완공", "path": out_path}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/build_normalized_all")
async def api_build_normalized_all():
    """SourceMappingManager에 등록된 활성 자산 전체를 1클릭 일괄 정규화"""
    try:
        from CAiModelBaker.core.normalized_builder import build_all_active_normalized
        results = build_all_active_normalized()
        return {
            "status": "ok",
            "message": f"활성 자산 일괄 정규화 완료 (성공: {results['success_count']}, 실패: {results['failed_count']})",
            "results": results
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/data_vault/normalized_inventory")
async def api_get_normalized_inventory():
    """16대 Normalized Store Parquet 아티팩트의 실시간 인벤토리 및 헬스 메트릭 조회"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        from shared.data_library import paths as dpaths
        from shared.data_library.manifest_manager import ManifestManager
        import pandas as pd

        active_assets = SourceMappingManager.load_all_mappings()
        items = []
        total_bytes = 0

        # 자산별 타임프레임 및 대상 경로 목록 생성 (Zero-Leakage Auto-Resolution)
        targets = []
        for sym, asset in active_assets.items():
            cat = asset.get("category", "")
            role = asset.get("role", "")
            name = asset.get("name", sym)

            # 1순위: 자산 대장에 명시된 target_scales / canonical_intervals 순회 등록 (없을 시 지능적 폴백)
            canon_intervals = asset.get("target_scales") or asset.get("canonical_intervals")
            if not canon_intervals:
                if cat in ["KRX_ETF", "KRX_STOCK"]:
                    p_type = asset.get("primary_type", "")
                    p10s_path = dpaths.normalized_path("candles_10s", f"{sym}_10s.parquet")
                    from shared.data_library import tick_store
                    has_tick = (p_type == "TICK_AND_1M") or os.path.exists(p10s_path) or tick_store.has_data(sym)
                    canon_intervals = ["10s", "1m"] if has_tick else ["1m"]
                elif cat == "CRYPTO_SPOT":
                    canon_intervals = [crypto_spot_default_interval(sym)]
                else:
                    canon_intervals = ["1m"]

            for intv in canon_intervals:
                if cat in ["KRX_FUTURES", "KRX_INDEX", "DOMESTIC_FUTURES", "DOMESTIC_INDEX", "OVERSEAS_FUTURES"]:
                    targets.append((sym, name, cat, role, intv, dpaths.macro_normalized_path(sym, intv)))
                else:
                    targets.append((sym, name, cat, role, intv, dpaths.normalized_path(f"candles_{intv}", f"{sym}_{intv}.parquet")))

        # 3순위 안전망 (디스크 ➔ 인벤토리 역방향 자동 스캔):
        # DataVault/normalized/ 내에 1:1 유효한 .manifest.json을 가진 정규화 Parquet이 실재하면 자동 편입
        norm_root = dpaths.normalized_root()
        if os.path.exists(norm_root):
            for root, _, files in os.walk(norm_root):
                for f in files:
                    if f.endswith(".parquet") and not any(ign in f.lower() for ign in [".tmp", "temp", "backup"]):
                        full_pq = os.path.join(root, f)
                        mf_path = f"{full_pq}.manifest.json"
                        if os.path.exists(mf_path):
                            if any(t[5] == full_pq for t in targets):
                                continue
                            base_name = f[:-8]
                            parts = base_name.rsplit("_", 1)
                            if len(parts) == 2 and re.match(r"^(\d+[smhdw]|tick|daily|weekly|monthly)$", parts[1].lower()):
                                auto_sym, auto_intv = parts[0], parts[1]
                            else:
                                auto_sym, auto_intv = base_name, "1m"

                            # 이미 targets에 동일 (심볼, 인터벌) 조합이 등록되어 있다면 실재 경로로 승격 및 중복 방지
                            existing_idx = next((i for i, t in enumerate(targets) if t[0] == auto_sym and t[4] == auto_intv), None)
                            if existing_idx is not None:
                                if not os.path.exists(targets[existing_idx][5]):
                                    orig = targets[existing_idx]
                                    targets[existing_idx] = (orig[0], orig[1], orig[2], orig[3], orig[4], full_pq)
                                continue

                            # 대장에 등록된 자산인 경우, 선언된 target_scales에 속한 것만 인벤토리에 노출 (체크 해제된 고아 파생 파일 노출 차단)
                            if auto_sym in active_assets:
                                declared_scales = active_assets[auto_sym].get("target_scales") or active_assets[auto_sym].get("canonical_intervals") or []
                                if auto_intv.lower() not in [s.lower() for s in declared_scales]:
                                    continue

                            asset_meta = active_assets.get(auto_sym, {})
                            auto_cat = asset_meta.get("category", "NORMALIZED_AUTO")
                            auto_role = asset_meta.get("role", "macro_proxy" if "CFD" in auto_cat else "data_feed")
                            auto_name = asset_meta.get("name", auto_sym)
                            targets.append((auto_sym, auto_name, auto_cat, auto_role, auto_intv, full_pq))

        for sym, name, cat, role, interval, pq_path in targets:
            exists = os.path.exists(pq_path)
            size_mb = 0.0
            row_count = 0
            updated_at = "-"
            manifest_ok = False
            health_summary = {}
            manifest = None

            if exists:
                b_size = os.path.getsize(pq_path)
                total_bytes += b_size
                size_mb = round(b_size / (1024 * 1024), 2)
                mtime = os.path.getmtime(pq_path)
                updated_at = pd.to_datetime(mtime, unit="s", utc=True).tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M:%S")

                manifest = ManifestManager.load_manifest(pq_path)
                if manifest:
                    manifest_ok = True
                    health_summary = manifest.get("health_summary", {})
                    row_count = health_summary.get("total_bars") or manifest.get("integrity", {}).get("row_count", 0)
                else:
                    try:
                        df_tmp = pd.read_parquet(pq_path, columns=["timestamp"])
                        row_count = len(df_tmp)
                    except Exception:
                        row_count = 0

            sampling_mode = "BAR_NATIVE"
            sampling_desc = f"🔵 NATIVE {interval}"
            if manifest:
                source_meta = manifest.get("source_meta", {})
                sampling_mode = source_meta.get("sampling_mode", "TICK_RESAMPLED" if interval == "10s" else "BAR_NATIVE")
                sampling_desc = source_meta.get("sampling_desc", "🟢 NATIVE 10s" if interval == "10s" else f"🔵 NATIVE {interval}")
            elif interval == "10s":
                sampling_mode = "TICK_RESAMPLED"
                sampling_desc = "🟢 NATIVE 10s"

            items.append({
                "symbol": sym,
                "name": name,
                "category": cat,
                "role": role,
                "interval": interval,
                "sampling_mode": sampling_mode,
                "sampling_desc": sampling_desc,
                "file_path": dpaths.display(pq_path),
                "exists": exists,
                "size_mb": size_mb,
                "row_count": row_count,
                "updated_at": updated_at,
                "manifest_ok": manifest_ok,
                "health_summary": health_summary,
            })

        return {
            "status": "ok",
            "total_count": len(items),
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "items": items,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/upsert_normalized_all")
async def api_upsert_normalized_all():
    """SourceMappingManager에 등록된 활성 자산 전체에 대해 원클릭 일자별 증분 업서트"""
    try:
        from CAiModelBaker.core.normalized_builder import upsert_all_active_normalized
        results = upsert_all_active_normalized()
        return {
            "status": "ok",
            "message": f"활성 자산 증분 업서트 완료 (성공: {results['upserted_count']}, 스킵: {results['skipped_count']}, 실패: {results['failed_count']})",
            "results": results,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/upsert_normalized_single")
async def api_upsert_normalized_single(payload: dict):
    """단일 자산 일자별 증분 업서트"""
    try:
        from CAiModelBaker.core.normalized_builder import upsert_normalized_daily
        symbol = payload.get("symbol") or get_target_symbol(load_config())
        interval = payload.get("interval", "10s")
        res = upsert_normalized_daily(symbol, interval=interval)
        return {"status": "ok", "result": res}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/data_vault/normalized_preview")
async def api_get_normalized_preview(symbol: str = "069500", interval: str = "10s", limit: int = 20):
    """Normalized Parquet의 최근 캔들 데이터 및 마이크로 지표 미리보기"""
    try:
        from shared.data_library import paths as dpaths
        from shared.data_library.source_mapping_manager import SourceMappingManager
        from shared.config.config_manager import get_proxy_for
        import pandas as pd

        clean_sym = SourceMappingManager.clean_symbol(symbol)
        served_as_proxy_of = None   # 대용 프록시로 서빙한 경우 그 원본 심볼
        served_interval = interval  # 폴백으로 다른 주기를 서빙한 경우 그 실제 주기
        if interval == "10s":
            pq_path = dpaths.normalized_path("candles_10s", f"{clean_sym}_10s.parquet")
        elif SourceMappingManager.macro_series_info(clean_sym):
            pq_path = dpaths.macro_normalized_path(clean_sym, interval)
        else:
            pq_path = dpaths.normalized_path(f"candles_{interval}", f"{clean_sym}_{interval}.parquet")

        if not os.path.exists(pq_path):
            # 해외 매크로 선물 -> CFD 대용 폴백. 대용 관계는 원장(proxy_of)이 유일한 정의다.
            proxy_sym = get_proxy_for(clean_sym)

            # 동적 폴백: normalized 하위 폴더에서 f"{clean_sym}_{interval}.parquet" 및 프록시 스캔
            # (경로, 실제 심볼, 실제 interval) — 무엇을 서빙했는지 응답에 정직하게 싣기 위함
            alt_candidates = [
                (dpaths.normalized_path("candles_1m", f"{clean_sym}_1m.parquet"), clean_sym, "1m"),
                (dpaths.normalized_path("candles_10s", f"{clean_sym}_10s.parquet"), clean_sym, "10s"),
                (dpaths.macro_normalized_path(clean_sym, interval), clean_sym, interval),
            ]
            if proxy_sym:
                alt_candidates.extend([
                    (dpaths.normalized_path("candles_1m", f"{proxy_sym}_1m.parquet"), proxy_sym, "1m"),
                    (dpaths.normalized_path(f"candles_{interval}", f"{proxy_sym}_{interval}.parquet"), proxy_sym, interval),
                    (dpaths.macro_normalized_path(proxy_sym, interval), proxy_sym, interval),
                ])
            for cand, cand_sym, cand_interval in alt_candidates:
                if cand and os.path.exists(cand):
                    pq_path = cand
                    if cand_sym != clean_sym:
                        served_as_proxy_of = cand_sym
                    served_interval = cand_interval
                    break

        if not os.path.exists(pq_path):
            return {"status": "error", "message": f"정규화 파일이 존재하지 않습니다: {dpaths.display(pq_path)}"}

        df = pd.read_parquet(pq_path)
        tail_df = df.tail(limit).copy()
        if "timestamp" in tail_df.columns:
            tail_df["timestamp"] = tail_df["timestamp"].astype(str)

        records = tail_df.to_dict(orient="records")
        return {
            "status": "ok",
            # 프록시로 서빙했다면 실제로 읽은 심볼을 보고한다 (요청 심볼로 위장하지 않는다)
            "symbol": served_as_proxy_of or clean_sym,
            "requested_symbol": clean_sym,
            "is_proxy": served_as_proxy_of is not None,
            "source_path": dpaths.display(pq_path),
            "interval": served_interval,
            "requested_interval": interval,
            "total_rows": len(df),
            "columns": list(df.columns),
            "records": records,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}



@app.post("/api/data_vault/compose_features")
async def api_compose_features_v2(payload: dict):
    """FeatureComposer를 통한 온디맨드 피처셋 결합 및 스냅샷 생성"""
    try:
        from CAiModelBaker.core.feature_composer import FeatureComposer
        symbol = payload.get("symbol") or get_target_symbol(load_config())
        interval = payload.get("interval", "10s")
        ref_symbols = payload.get("ref_symbols")
        save_snapshot = payload.get("save_snapshot", True)

        # ref_symbols 미제공 시 symbol_relations.json에서 등록된 참조 자산 자동 바인딩
        if ref_symbols is None:
            try:
                from shared.data_library.symbol_relations_manager import SymbolRelationsManager
                rels = SymbolRelationsManager.get_relations(symbol)
                if rels and rels.get("references"):
                    ref_symbols = [r["symbol"] for r in rels["references"]]
            except Exception as ex:
                print(f"[api_compose_features_v2] symbol_relations 로드 예외: {ex}")

        composer = FeatureComposer()
        df = composer.compose(
            target_symbol=symbol,
            target_interval=interval,
            ref_symbols=ref_symbols,
            save_snapshot=save_snapshot,
        )
        return {
            "status": "ok",
            "message": f"{symbol} ({interval}) 피처셋 결합 완료: {len(df):,}행, {len(df.columns)}컬럼",
            "rows": len(df),
            "columns": list(df.columns),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Raw Store: Feed Ingestion Registry & Source Mapping APIs
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/raw_store/registry")
async def api_raw_store_registry():
    """수집 대상 자산 대장 및 출처 매핑 현황 조회"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        items = SourceMappingManager.get_feed_registry_status()
        active_count = sum(1 for x in items if x.get("is_active", True))
        healthy_count = sum(1 for x in items if x.get("status_code") == "HEALTHY")
        return {
            "status": "ok",
            "items": items,
            "total_count": len(items),
            "active_count": active_count,
            "healthy_count": healthy_count
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/raw_store/catalog/summary")
async def api_raw_store_catalog_summary(force: bool = False):
    """원천 목록 요약 — 시장×원천×구분(운용/유니버스)별 종목 수·최신일·이상 건수, 이상 종류별 건수, 경고."""
    try:
        from shared.data_library import raw_catalog
        loop = asyncio.get_event_loop()
        return {"status": "ok", **(await loop.run_in_executor(None, lambda: raw_catalog.summary(force=force)))}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/raw_store/catalog/items")
async def api_raw_store_catalog_items(q: str = "", market: str = "", provider: str = "", tier: str = "", issue: str = "",
                                      only_anomaly: bool = False, page: int = 1, size: int = 50):
    """원천 목록 검색 — 서버에서 거르고 페이지 단위로 보낸다(유니버스 ~1.3만 종목)."""
    try:
        from shared.data_library import raw_catalog
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: raw_catalog.search(q=q, market=market, provider=provider, tier=tier,
                                                                         issue=issue, only_anomaly=only_anomaly, page=page, size=size))
        return {"status": "ok", **res}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/raw_store/catalog/detail")
async def api_raw_store_catalog_detail(key: str):
    """유니버스 항목 상세 — 확인 필요·영구 결손·기업행위·최근 거래일 중 빠진 날."""
    try:
        from shared.data_library import raw_catalog
        loop = asyncio.get_event_loop()
        return {"status": "ok", **(await loop.run_in_executor(None, lambda: raw_catalog.detail(key)))}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/raw_store/catalog/us_job")
async def api_raw_store_catalog_us_job():
    """미국 유니버스 수집 작업 상태(실행 중 여부 + 마지막 진행)."""
    try:
        from shared.data_library.us_equity import collect
        return {"status": "ok", **collect.job_status()}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/catalog/us_job")
async def api_raw_store_catalog_us_job_start():
    """미국 유니버스 증분 수집 시작 — 거래소 목록(상장 변동 기록) → 종목별 마지막 14일부터 → 원천 반영. 별도 프로세스, 한 번에 하나."""
    try:
        from shared.data_library.us_equity import collect
        return {"status": "ok", **collect.start_background("chart")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/raw_store/health_matrix")
async def api_raw_store_health_matrix(days: str = "120", force_refresh: bool = False, cache_only: bool = True):
    """Raw Store 등록 자산 전체의 결측일 매트릭스 (자산 x 최근 N거래일 또는 전체) 조회 (비동기 스레드 풀 실행)"""
    try:
        from shared.data_library.raw_store_health import RawStoreHealthEngine
        days_param = int(days) if str(days).isdigit() else str(days)
        loop = asyncio.get_event_loop()
        items, meta = await loop.run_in_executor(
            None,
            lambda: RawStoreHealthEngine.get_matrix(
                days=days_param,
                force_refresh=force_refresh,
                cache_only=cache_only,
                return_meta=True
            )
        )
        # 보관(archived) 자산은 수집이 의도적으로 일시중단된 상태이므로 결측 경고 집계에서 제외
        # 결측일이 있거나, 원천 대조 품질 감사에서 불량일이 나온 자산 (감사를 아직 안 한 자산은 '이상 없음'이 아니라 '미검증')
        def _views(x):   # 구간(틱/1분/일봉)별 결과 — 하나라도 이상이면 그 자산은 이상
            return x.get("intervals") or [x]
        anomaly_count = sum(1 for x in items if not x.get("archived")
                            and any((v.get("missing_count") or 0) > 0 or (v.get("quality_bad_count") or 0) > 0 for v in _views(x)))
        unverified_count = sum(1 for x in items if not x.get("archived") and x.get("file_exists")
                               and any(v.get("quality_checked") is False for v in _views(x)))
        archived_count = sum(1 for x in items if x.get("archived"))
        return {
            "status": "ok",
            "items": items,
            "days": days_param,
            "total_count": len(items),
            "anomaly_count": anomaly_count,
            "quality_unverified_count": unverified_count,
            "archived_count": archived_count,
            "has_updates": meta.get("has_updates", False),
            "updated_count": meta.get("updated_count", 0),
            "updated_symbols": meta.get("updated_symbols", []),
            "cached_at": meta.get("saved_at"),
            "cache_only": meta.get("cache_only", False),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/raw_store/health_matrix/intervals")
async def api_raw_store_health_matrix_intervals(symbol: str, days: str = "120", force_refresh: bool = False, cache_only: bool = True):
    """단일 자산에 등록된 모든 그래뉼러리티(틱/1분봉/일봉 등)의 결측 현황을 개별 조회 (비동기 스레드 풀 실행)"""
    try:
        from shared.data_library.raw_store_health import RawStoreHealthEngine
        days_param = int(days) if str(days).isdigit() else str(days)
        loop = asyncio.get_event_loop()
        items = await loop.run_in_executor(
            None,
            lambda: RawStoreHealthEngine.get_intervals_for_symbol(
                symbol, days=days_param, force_refresh=force_refresh, cache_only=cache_only
            )
        )
        return {"status": "ok", "symbol": symbol, "intervals": items}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/health_matrix/refresh_one")
async def api_raw_store_health_matrix_refresh_one(payload: dict):
    """단일 자산의 결측 현황을 강제 재계산 (비동기 스레드 풀 실행)"""
    try:
        from shared.data_library.raw_store_health import RawStoreHealthEngine
        symbol = payload.get("symbol")
        days = int(payload.get("days", 120))
        if not symbol:
            return {"status": "error", "message": "심볼이 지정되지 않았습니다."}
        loop = asyncio.get_event_loop()
        def _refresh():
            # 결측 재스캔 + 원천 대조 품질 감사 강제 재실행 (파일이 바뀌었으면 캐시는 자동 무효화되지만 사용자가 명시적으로 원할 때)
            try:
                from shared.data_library.data_quality import DataQualityAuditor
                DataQualityAuditor.audit_symbol(symbol, force=True)
            except Exception as qe:
                print(f"⚠️ [DataQuality] 재감사 실패({symbol}): {qe}")
            return RawStoreHealthEngine.get_matrix(days=days, force_refresh=True, symbols=[symbol])
        items = await loop.run_in_executor(None, _refresh)
        if not items:
            return {"status": "error", "message": "등록되지 않은 심볼입니다."}
        return {"status": "ok", "item": items[0]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── 전 자산 원천 데이터 품질 감사(Quality Audit) 상태 및 백그라운드 워커 ──
_quality_audit_task_state = {
    "running": False,
    "full": False,
    "current": 0,
    "total": 0,
    "current_symbol": "",
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_quality_audit_task_lock = threading.Lock()


def _run_quality_audit_worker(full: bool, symbols: Optional[list] = None, policy_options: Optional[dict] = None, event_loop=None):
    global _quality_audit_task_state
    try:
        from shared.data_library.data_quality import DataQualityAuditor, QualityPolicy
        from shared.data_library.raw_store_health import RawStoreHealthEngine
        from shared.data_library.source_mapping_manager import SourceMappingManager

        policy = QualityPolicy.from_dict(policy_options)

        # 파일이 존재하는 전체 등록 자산 수 파악
        registry = SourceMappingManager.get_feed_registry_status()
        all_syms = [r["symbol"] for r in registry if r.get("file_exists")]
        if symbols:
            wanted = {SourceMappingManager.clean_symbol(s) for s in symbols} | {s.upper() for s in symbols}
            all_syms = [s for s in all_syms if s in wanted or SourceMappingManager.clean_symbol(s) in wanted]
        with _quality_audit_task_lock:
            _quality_audit_task_state["total"] = len(all_syms)

        def _on_progress(cur: int, tot: int, sym: str, *args, **kwargs):
            with _quality_audit_task_lock:
                _quality_audit_task_state["current"] = cur
                _quality_audit_task_state["total"] = tot
                _quality_audit_task_state["current_symbol"] = sym
            if event_loop and event_loop.is_running():
                try:
                    pct = int(cur / tot * 100) if tot > 0 else 0
                    msg = json.dumps({
                        "type": "quality_audit_progress",
                        "running": True,
                        "full": full,
                        "current": cur,
                        "total": tot,
                        "current_symbol": sym,
                        "progress": pct,
                    })
                    asyncio.run_coroutine_threadsafe(manager.broadcast(msg), event_loop)
                except Exception:
                    pass

        DataQualityAuditor.audit_all_symbols(
            symbols=symbols,
            full=full,
            max_workers=1,
            policy=policy,
            progress_cb=_on_progress
        )

        RawStoreHealthEngine.clear_cache()
        with _quality_audit_task_lock:
            _quality_audit_task_state["running"] = False
            _quality_audit_task_state["finished_at"] = datetime.now().isoformat(timespec="seconds")

        if event_loop and event_loop.is_running():
            try:
                done_msg = json.dumps({
                    "type": "quality_audit_done",
                    "status": "success",
                    "full": full,
                    "finished_at": _quality_audit_task_state["finished_at"]
                })
                asyncio.run_coroutine_threadsafe(manager.broadcast(done_msg), event_loop)
            except Exception:
                pass
    except Exception as exc:
        with _quality_audit_task_lock:
            _quality_audit_task_state["running"] = False
            _quality_audit_task_state["error"] = str(exc)
            _quality_audit_task_state["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if event_loop and event_loop.is_running():
            try:
                err_msg = json.dumps({
                    "type": "quality_audit_done",
                    "status": "error",
                    "error": str(exc),
                    "full": full
                })
                asyncio.run_coroutine_threadsafe(manager.broadcast(err_msg), event_loop)
            except Exception:
                pass


@app.get("/api/raw_store/quality_audit")
async def api_raw_store_quality_audit(symbol: str, force: bool = False, full: bool = False, only_problems: bool = True):
    """원천 데이터 품질 감사 — 세밀한 원천(틱)을 상위 원천(1분봉)과, 1분봉을 일봉과 대조한 일별 판정.
    full=True 이면 과거 전체 날짜를 100% 전수 재감사하고, full=False 이면 신규/미검증 일자만 고속 증분 감사한다."""
    try:
        from shared.data_library.data_quality import DataQualityAuditor
        loop = asyncio.get_event_loop()
        audits = await loop.run_in_executor(None, lambda: DataQualityAuditor.audit_symbol(symbol, force=force, full=full))
        out = {}
        for label, a in audits.items():
            days = a["days"]
            out[label] = {"summary": a["summary"],
                          "days": {d: v for d, v in days.items() if not only_problems or v["status"] in ("bad", "warn")}}
        return {"status": "ok", "symbol": symbol, "full": full, "audits": out}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/quality_audit_all")
async def api_raw_store_quality_audit_all(payload: Optional[dict] = Body(default=None)):
    """전 자산 원천 데이터 품질 감사 실행 (증분: full=false, 전수: full=true)"""
    global _quality_audit_task_state
    p = payload or {}
    full = bool(p.get("full", False))
    symbols = p.get("symbols", None)
    policy_options = p.get("policy_options", None)

    with _quality_audit_task_lock:
        if _quality_audit_task_state["running"]:
            return {
                "status": "busy",
                "message": "품질 감사가 이미 진행 중입니다.",
                "state": dict(_quality_audit_task_state)
            }
        _quality_audit_task_state["running"] = True
        _quality_audit_task_state["full"] = full
        _quality_audit_task_state["current"] = 0
        _quality_audit_task_state["total"] = 0
        _quality_audit_task_state["current_symbol"] = ""
        _quality_audit_task_state["error"] = None
        _quality_audit_task_state["started_at"] = datetime.now().isoformat(timespec="seconds")
        _quality_audit_task_state["finished_at"] = None

    loop = asyncio.get_event_loop()
    threading.Thread(target=_run_quality_audit_worker, args=(full, symbols, policy_options, loop), daemon=True).start()
    mode_text = "전수(Full)" if full else "증분(Incremental)"
    return {
        "status": "ok",
        "message": f"전 자산 {mode_text} 품질 감사를 백그라운드에서 시작했습니다.",
        "full": full
    }


@app.get("/api/raw_store/quality_policy_defaults")
async def api_raw_store_quality_policy_defaults():
    """품질 감사 정책 기본값 및 검사 항목 설명 반환"""
    from shared.data_library.data_quality import DEFAULT_POLICY
    from dataclasses import asdict
    return {
        "status": "ok",
        "defaults": asdict(DEFAULT_POLICY),
        "descriptions": {
            "check_time_reversal": "시간 역행 감지 (Time Reversal: ts_i < ts_{i-1})",
            "check_duplicate_time": "중복 타임스탬프 감지 (동일 분/초 중복)",
            "check_broken_geometry": "캔들 기하학 파손 (High < Low, Open/Close 범위 이탈)",
            "check_bad_price": "0 이하 / 결측(NaN) / 음수 가격",
            "check_missing_bars": "세션 분봉 결손율 (24시간 가상자산 1440분 등 대비)",
            "check_flash_spike": "이상치 가격 플래시 스파이크 (순간 급변 후 복귀)",
            "check_cross_reconciliation": "상위 원천(일봉/분봉) 거래량 및 가격 교차 대조 (CREON 등)",
        }
    }


@app.get("/api/raw_store/quality_audit_status")
async def api_raw_store_quality_audit_status():
    """품질 감사 태스크 현재 진행 상태 조회"""
    with _quality_audit_task_lock:
        return {"status": "ok", "state": dict(_quality_audit_task_state)}


@app.post("/api/raw_store/collect_now")
async def api_raw_store_collect_now(payload: dict):
    """
    단일 자산의 원천 데이터를 즉시 재수집한다. is_active/보관(archived) 여부와 무관하게
    수동으로 실행할 수 있다 — 보관 처리된 자산도 최신 데이터로 갱신은 하되
    상시 실시간 구독/자동 동기화 대상에는 다시 넣지 않고 싶을 때 사용.
    REST 백필이 가능한 제공처(YAHOO/CME 해외선물·미국 직투 KIS, UPBIT/BINANCE/COINBASE)만 지원하며,
    실시간 피드로만 누적되는 CREON/CTRADER 등은 지원 대상이 아니다.
    """
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        from shared.data_library.raw_store_health import RawStoreHealthEngine

        symbol = payload.get("symbol")
        if not symbol:
            return {"status": "error", "message": "심볼이 지정되지 않았습니다."}

        mappings = SourceMappingManager.load_all_mappings()
        info = mappings.get(symbol)
        if not info:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {symbol}"}

        category = (info.get("category") or "").upper()
        primary_source = (info.get("primary_source") or "").upper()

        logs = []

        def local_log(msg):
            logs.append(str(msg))

        loop = asyncio.get_event_loop()

        if category == "OVERSEAS_FUTURES":
            from CAiModelBaker.core.overseas_futures_collector import download_all_major_futures
            await loop.run_in_executor(
                None, lambda: download_all_major_futures(symbols=[symbol], local_log=local_log)
            )
        elif category in ("US_STOCK", "US_ETF"):
            from CAiModelBaker.core.overseas_futures_collector import download_us_market_data
            await loop.run_in_executor(None, lambda: download_us_market_data(symbol, local_log=local_log))
        elif category in ("CRYPTO_SPOT", "CRYPTO_FUTURES") or primary_source in ("UPBIT", "BINANCE", "COINBASE"):
            from CAiModelBaker.core.crypto_collector import sync_single_crypto_asset
            ok = await loop.run_in_executor(None, lambda: sync_single_crypto_asset(symbol, info, local_log=local_log))
            if not ok:
                return {"status": "error", "message": "수집 실패 (원천 응답 없음 또는 오류)", "log": "\n".join(logs[-30:])}
        elif primary_source in ("CTRADER", "SPOTWARE"):
            baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            repo_root = os.path.dirname(baker_root)
            script_path = os.path.join(repo_root, "scripts", "fetch_ctrader_history.py")
            ctrader_env_path = os.path.join(repo_root, "CTrade", ".env")
            cfd_env = os.environ.copy()
            if os.path.exists(ctrader_env_path):
                with open(ctrader_env_path, "r", encoding="utf-8") as ef:
                    for line in ef:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        cfd_env[k.strip()] = v.strip().strip("'").strip('"')
            clean_sym = SourceMappingManager.clean_symbol(symbol)
            from shared.data_library.raw_ingest import archive_start
            start_dt = archive_start()
            total_days = max(1, (datetime.now() - start_dt).days + 1)
            target_scales = info.get("ingestion_sources") or ["1M", "1D"]
            for sc in target_scales:
                sc_str = str(sc).strip().lower()
                local_log(f"🚀 [cTrader] {clean_sym} ({sc_str}) 다운로드 시작...")
                cmd = [
                    sys.executable, script_path,
                    "--symbol", clean_sym,
                    "--period", sc_str,
                    "--days", str(total_days)
                ]
                proc = await loop.run_in_executor(
                    None, lambda c=cmd: subprocess.run(c, cwd=repo_root, env=cfd_env, capture_output=True, text=True, timeout=180)
                )
                if proc.returncode == 0:
                    local_log(f"✅ [cTrader] {clean_sym} ({sc_str}) 수집 완료")
                else:
                    err_msg = (proc.stderr or proc.stdout)[:200]
                    local_log(f"⚠️ [cTrader] {clean_sym} ({sc_str}) 수집 에러: {err_msg}")
        else:
            return {
                "status": "error",
                "message": f"'{primary_source}'는 실시간 피드로만 누적되는 자산이라 수동 갱신을 지원하지 않습니다 (CREON 등은 실시간 구독으로만 채워집니다).",
            }

        items = await loop.run_in_executor(
            None, lambda: RawStoreHealthEngine.get_matrix(days=60, force_refresh=True, symbols=[symbol])
        )
        return {
            "status": "ok",
            "symbol": symbol,
            "item": items[0] if items else None,
            "log": "\n".join(logs[-30:]),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/register_asset")
async def api_raw_store_register_asset(payload: dict):
    """신규 자산을 수집 대상 대장에 등록"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        symbol = payload.get("symbol")
        name = payload.get("name")
        if not symbol or not name:
            return {"status": "error", "message": "심볼과 자산명은 필수입니다."}

        res = SourceMappingManager.register_asset(
            symbol=symbol,
            name=name,
            category=payload.get("category", "KRX_STOCK"),
            role=payload.get("role", "leading_stock"),
            primary_source=payload.get("primary_source", "CREON"),
            primary_type=payload.get("primary_type", "TICK_AND_1M"),
            fallback_source=payload.get("fallback_source", "KIS"),
            fallback_type=payload.get("fallback_type", "1M"),
            description=payload.get("description", ""),
            ingestion_resolution=payload.get("ingestion_resolution", "1M"),
            canonical_intervals=payload.get("canonical_intervals")
        )
        return res
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _run_creon_check_symbol_sync(symbol: str) -> dict:
    """CREON 종목코드 실재 여부를 확인 (Windows 네이티브 in-process / WSL PowerShell 브릿지 자동 분기).
    실시간 구독이 아닌 1회성 마스터 조회이므로 '규칙 6'(CREON 실시간 금지)에 저촉되지 않는다."""
    baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    is_win = sys.platform.startswith("win")

    if is_win:
        try:
            from CAiModelBaker.core.creon_collector import check_symbol_exists
            return check_symbol_exists(symbol)
        except Exception as e:
            return {"exists": False, "name": "", "error": f"CREON 조회 예외: {e}"}

    uname_str = ""
    try:
        uname_str = os.uname().release.lower()
    except Exception:
        pass
    candidate_ps = [
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        shutil.which("powershell.exe"),
        "/mnt/c/Windows/sysnative/WindowsPowerShell/v1.0/powershell.exe",
        "/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    ]
    ps_exe = next((p for p in candidate_ps if p and os.path.exists(p)), None)
    is_wsl = ps_exe is not None or "microsoft" in uname_str or "wsl" in uname_str
    if not (is_wsl and ps_exe):
        return {"exists": False, "checked": False, "name": "", "error": "이 환경(Windows/WSL 아님)에서는 CREON 연결을 확인할 수 없습니다."}

    cmd_str = f'"{ps_exe}" -NonInteractive -NoProfile -ExecutionPolicy Bypass -Command "py -3.10-32 core\\creon_collector.py --type check_symbol --symbol {symbol}"'
    try:
        proc = subprocess.run(cmd_str, shell=True, cwd=baker_root, capture_output=True, timeout=30)
        out = proc.stdout.decode('utf-8', errors='replace')
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("CREON_CHECK_RESULT_JSON:"):
                return json.loads(line[len("CREON_CHECK_RESULT_JSON:"):])
        err = proc.stderr.decode('utf-8', errors='replace')
        return {"exists": False, "name": "", "error": f"CREON 조회 결과를 파싱하지 못했습니다. (returncode={proc.returncode}) {(out + err)[-300:]}"}
    except subprocess.TimeoutExpired:
        return {"exists": False, "name": "", "error": "CREON 조회 시간 초과(30s) — CYBOS Plus 로그인 상태를 확인해 주세요."}
    except Exception as e:
        return {"exists": False, "name": "", "error": f"WSL 브릿지 실행 예외: {e}"}


def _get_cached_kis_token() -> Optional[str]:
    """CTrade 실행 엔진이 이미 발급해 캐시해둔 KIS 액세스 토큰을 재사용한다.
    새로 토큰을 발급받지 않는 이유: 동일 app_key로 재발급 시 CTrade가 이미 사용 중인
    라이브 세션의 토큰을 무효화시킬 위험이 있어, 대시보드의 경량 검증 용도로는 절대 새로 발급하지 않는다."""
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    token_file = os.path.join(repo_root, "CTrade", "config", "token_info.json")
    if not os.path.exists(token_file):
        return None
    try:
        from datetime import timedelta
        mtime = datetime.fromtimestamp(os.path.getmtime(token_file))
        if datetime.now() - mtime > timedelta(hours=20):
            return None
        with open(token_file, 'r') as f:
            data = json.load(f)
        return data.get("access_token") or None
    except Exception:
        return None


def _kis_lookup_sync(symbol: str) -> dict:
    """[가벼운 검증] KIS 국내주식 현재가 조회 REST API로 종목코드 실재 여부만 확인 (실시간 구독 없음).
    CTrade 실거래 엔진은 Vultr 서버에서 실행되며 KIS 토큰도 그쪽에서 발급/캐시된다.
    로컬에서는 scripts/fetch_kis_token.sh로 내려받은 사본(CTrade/config/token_info.json)만 재사용하고,
    새 토큰은 절대 발급하지 않는다 (같은 app_key로 재발급 시 Vultr의 라이브 세션 토큰이 무효화될 위험)."""
    try:
        from shared.core.market_utils import is_krx
    except Exception:
        is_krx = None
    if is_krx is not None and not is_krx({'SYMBOL': symbol}):
        return {"checked": False, "exists": None, "reason": "해외 종목은 경량 검증(REST 시세조회)을 지원하지 않습니다."}

    token = _get_cached_kis_token()
    if not token:
        return {"checked": False, "exists": None, "reason": "KIS 토큰 캐시가 없거나 20시간 이상 오래됐습니다. 터미널에서 scripts/fetch_kis_token.sh 를 먼저 실행해 Vultr의 토큰을 동기화한 뒤 다시 시도해 주세요. (새 토큰은 라이브 세션 보호를 위해 여기서 직접 발급하지 않음)"}

    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env_path = os.path.join(repo_root, "CTrade", ".env")
        from dotenv import dotenv_values
        env_vals = dotenv_values(env_path) if os.path.exists(env_path) else {}
        app_key = env_vals.get("KIS_APP_KEY", "")
        app_secret = env_vals.get("KIS_APP_SECRET", "")
        url_base = env_vals.get("KIS_URL_BASE") or env_vals.get("URL_BASE") or "https://openapi.koreainvestment.com:9443"
        if not app_key or not app_secret:
            return {"checked": False, "exists": None, "reason": "KIS 인증정보(.env)를 찾을 수 없습니다."}

        import requests
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "FHKST01010100",
            "custtype": "P",
        }
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
        res = requests.get(f"{url_base}/uapi/domestic-stock/v1/quotations/inquire-price", headers=headers, params=params, timeout=10)
        if res.status_code != 200:
            return {"exists": False, "checked": True, "error": f"KIS API 오류 (HTTP {res.status_code})"}
        data = res.json()
        if data.get("rt_cd") == "0" and data.get("output", {}).get("stck_prpr"):
            return {"exists": True, "checked": True, "name": data.get("output", {}).get("hts_kor_isnm", ""), "price": data.get("output", {}).get("stck_prpr")}
        return {"exists": False, "checked": True, "error": data.get("msg1", "KIS에서 유효하지 않은 종목코드로 응답했습니다.")}
    except Exception as e:
        return {"checked": False, "exists": None, "reason": f"KIS 조회 예외: {e}"}


async def _validate_kis_symbol(symbol: str) -> dict:
    """[가벼운 검증] KIS 국내주식 현재가 조회로 종목코드 실재 여부 확인 (비동기 래퍼)."""
    return await asyncio.get_event_loop().run_in_executor(None, _kis_lookup_sync, symbol)


# ---------------------------------------------------------------------------
# 종목명 원천 조회 공급자 등록 — 종목 이름은 코드에 박지 않고 KIS/CREON 이 실제로 쓰는 이름을 가져온다
# ---------------------------------------------------------------------------
import re as _re_names


def _is_krx_code_for_names(symbol: str, market_type: str) -> bool:
    if market_type and not str(market_type).startswith("KRX") and market_type != "OTHER":
        return False
    return bool(_re_names.fullmatch(r"\d[0-9A-Z]{5}", symbol))  # 069500, 0167A0, 0048J0


def _kis_name_provider(symbol: str, market_type: str = ""):
    if not _is_krx_code_for_names(symbol, market_type):
        return None
    res = _kis_lookup_sync(symbol)
    return res.get("name") if res.get("exists") else None


def _creon_name_provider(symbol: str, market_type: str = ""):
    if not _is_krx_code_for_names(symbol, market_type):
        return None
    res = _run_creon_check_symbol_sync(symbol)
    return res.get("name") if res.get("exists") else None


from shared.data_library.symbol_name_resolver import register_provider as _register_name_provider
_register_name_provider("KIS", _kis_name_provider)      # REST 시세조회 (빠름, 토큰 캐시 있을 때)
_register_name_provider("CREON", _creon_name_provider)  # 종목 마스터 COM/WSL 브릿지 (느릴 수 있어 KIS 뒤)


@app.post("/api/raw_store/validate_source")
async def api_raw_store_validate_source(payload: dict):
    """[가벼운 검증] 등록 전 CREON/KIS 종목코드 실재 여부를 확인한다.
    실시간 구독을 열지 않는 마스터/시세 조회 수준의 경량 검증이다 (강한 검증은 별도 과제)."""
    symbol = (payload.get("symbol") or "").strip()
    if not symbol:
        return {"status": "error", "message": "심볼 코드가 필요합니다."}

    primary_source = payload.get("primary_source", "CREON")
    fallback_source = payload.get("fallback_source", "KIS")
    sources_to_check = [s for s in {primary_source, fallback_source} if s in ("CREON", "KIS")]

    results = {}
    loop = asyncio.get_event_loop()
    for src in sources_to_check:
        if src == "CREON":
            results["CREON"] = await loop.run_in_executor(None, _run_creon_check_symbol_sync, symbol)
        elif src == "KIS":
            results["KIS"] = await _validate_kis_symbol(symbol)

    checked_results = [r for r in results.values() if r.get("checked", True) and r.get("exists") is not None]
    all_pass = all(r.get("exists") for r in checked_results) if checked_results else None

    return {"status": "ok", "symbol": symbol, "results": results, "all_pass": all_pass}


@app.post("/api/raw_store/toggle_asset")
async def api_raw_store_toggle_asset(payload: dict):
    """자산 수집 구독 활성/일시정지 토글"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        symbol = payload.get("symbol")
        is_active = payload.get("is_active")
        if not symbol:
            return {"status": "error", "message": "심볼이 지정되지 않았습니다."}
        return SourceMappingManager.toggle_asset_active(symbol, is_active)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/raw_store/provider_capabilities")
async def api_raw_store_provider_capabilities():
    """1단계 제공처별 실제 원천 스케일 및 2단계 퀀트 표준 스펙 목록 조회"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        return {
            "status": "ok",
            **SourceMappingManager.get_provider_capabilities()
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/update_sources")
async def api_raw_store_update_sources(payload: dict):
    """1단계: 원천 데이터 수집 스케일(ingestion_sources) 복수 설정 갱신"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        symbol = payload.get("symbol")
        sources = payload.get("ingestion_sources")
        if not symbol or sources is None:
            return {"status": "error", "message": "심볼과 수집 스케일 목록(ingestion_sources)은 필수입니다."}
        return SourceMappingManager.update_ingestion_sources(symbol, sources)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/probe_live")
async def api_raw_store_probe_live(payload: dict = None):
    """Data Vault 1단계: 실시간 접속 정보(live_connection) 기반 비차단 피드 헬스체크 및 리포팅"""
    try:
        from shared.data_library.live_feed_prober import LiveFeedProber
        payload = payload or {}
        symbol = payload.get("symbol")
        if symbol:
            res = LiveFeedProber.probe_symbol(symbol)
            return {"status": "ok", "probe": res}
        else:
            report = LiveFeedProber.probe_all_and_save_diagnostic()
            return {"status": "ok", "diagnostic_report": report}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/update_live_connection")
async def api_raw_store_update_live_connection(payload: dict):
    """Data Vault 1단계: 자산별 실시간 접속 정보(live_connection) 갱신"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        symbol = payload.get("symbol")
        live_conn = payload.get("live_connection")
        if not symbol or not live_conn:
            return {"status": "error", "message": "symbol 및 live_connection은 필수입니다."}
        return SourceMappingManager.update_live_connection(symbol, live_conn)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/update_source_config")
async def api_raw_store_update_source_config(payload: dict):
    """자산의 과거데이터 출처(주/보조 제공처 및 타입) 갱신"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        symbol = payload.get("symbol")
        if not symbol:
            return {"status": "error", "message": "심볼이 지정되지 않았습니다."}
        return SourceMappingManager.update_source_config(
            symbol=symbol,
            primary_source=payload.get("primary_source"),
            primary_type=payload.get("primary_type"),
            fallback_source=payload.get("fallback_source"),
            fallback_type=payload.get("fallback_type"),
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/archive_asset")
async def api_raw_store_archive_asset(payload: dict):
    """자산 보관(Archive) 처리 또는 해제 - 대장에서 제거하지 않고 별도 상태로만 표시"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        symbol = payload.get("symbol")
        archived = payload.get("archived", True)
        if not symbol:
            return {"status": "error", "message": "심볼이 지정되지 않았습니다."}
        return SourceMappingManager.set_archived(symbol, bool(archived))
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/delete_asset")
async def api_raw_store_delete_asset(payload: dict):
    """자산을 대장 및 원시/정규화 데이터에서 완전 삭제 (되돌릴 수 없음).
    symbol_relations.json(SSOT)에서 아직 참조 중이면 cascade=True 없이는 차단되고
    참조 중인 타겟 목록(status: "blocked")을 반환한다."""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        symbol = payload.get("symbol")
        confirm_symbol = payload.get("confirm_symbol")
        cascade = bool(payload.get("cascade", False))
        if not symbol:
            return {"status": "error", "message": "심볼이 지정되지 않았습니다."}
        if not confirm_symbol or confirm_symbol.strip().upper() != symbol.strip().upper():
            return {"status": "error", "message": "삭제 확인을 위해 종목코드를 정확히 입력해야 합니다."}
        return SourceMappingManager.unregister_asset(symbol, cascade=cascade)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/reset_and_refetch")
async def api_raw_store_reset_and_refetch(payload: dict, background_tasks: BackgroundTasks):
    """자산의 기존 원천/정규화 데이터를 안전 격리 후 삭제하고, 처음부터(Full) 전체 재수집을 실행한다."""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        symbol = payload.get("symbol")
        confirm_symbol = payload.get("confirm_symbol")
        if not symbol:
            return {"status": "error", "message": "심볼이 지정되지 않았습니다."}
        if not confirm_symbol or confirm_symbol.strip().upper() != symbol.strip().upper():
            return {"status": "error", "message": "초기화 확인을 위해 종목코드를 정확히 입력해야 합니다."}

        mappings = SourceMappingManager.load_all_mappings(force_refresh=True)
        key = SourceMappingManager.clean_symbol(symbol) or symbol.strip()
        info = mappings.get(symbol) or mappings.get(key)
        if not info:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {symbol}"}

        # 1. 기존 데이터 안전 격리 및 삭제
        reset_res = SourceMappingManager.reset_asset_data(symbol)
        if reset_res.get("status") != "ok":
            return reset_res

        # 2. 백그라운드에서 처음부터 전체 재수집 기동
        background_tasks.add_task(run_single_asset_refetch_worker, symbol, info)

        return {
            "status": "ok",
            "symbol": symbol,
            "name": info.get("name", symbol),
            "freed_mb": reset_res.get("freed_mb", 0),
            "deleted_count": len(reset_res.get("deleted_raw_files", [])),
            "message": f"'{info.get('name', symbol)}'({symbol}) 기존 데이터를 초기화하고 처음부터 전체 재수집을 시작했습니다."
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}



@app.post("/api/data_vault/update_target_scales")
async def api_data_vault_update_target_scales(payload: dict):
    """2단계: 정규화 목표 캔들 스케일(target_scales) 설정 갱신"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        symbol = payload.get("symbol")
        scales = payload.get("target_scales")
        if not symbol or scales is None:
            return {"status": "error", "message": "심볼과 목표 스케일 목록(target_scales)은 필수입니다."}
        return SourceMappingManager.update_target_scales(symbol, scales)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/batch_update_sources")
async def api_raw_store_batch_update_sources(payload: dict):
    """1단계: 복수/제공처별/전체 자산의 원천 수집 스케일 일괄 갱신"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        sources = payload.get("ingestion_sources")
        symbols = payload.get("symbols")
        provider = payload.get("provider")
        if sources is None:
            return {"status": "error", "message": "수집 스케일 목록(ingestion_sources)은 필수입니다."}
        return SourceMappingManager.batch_update_ingestion_sources(sources=sources, symbols=symbols, provider=provider)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/batch_update_target_scales")
async def api_data_vault_batch_update_target_scales(payload: dict):
    """2단계: 복수/전체 자산의 정규화 목표 캔들 스케일 일괄 갱신"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        scales = payload.get("target_scales")
        symbols = payload.get("symbols")
        apply_to_all = payload.get("apply_to_all", False)
        if scales is None:
            return {"status": "error", "message": "목표 스케일 목록(target_scales)은 필수입니다."}
        return SourceMappingManager.batch_update_target_scales(scales=scales, symbols=symbols, apply_to_all=apply_to_all)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/prune_orphans")
async def api_data_vault_prune_orphans():
    """2단계: 대장(source_mappings.json)의 target_scales에 선언되지 않은 고아/미사용 정규화 파생 Parquet 및 매니페스트 일괄 정리"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        return SourceMappingManager.prune_all_orphan_normalized_files()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/sync_single_scale")
async def api_data_vault_sync_single_scale(payload: dict):
    """지정된 자산의 특정 시간 단위(스케일)만 핀포인트로 증분 갱신(수집 또는 정규화)"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        from shared.data_library.paths import data_vault_path
        symbol = payload.get("symbol")
        scale = payload.get("scale")
        tier = payload.get("tier", "raw").lower()

        if not symbol or not scale:
            return {"status": "error", "message": "심볼(symbol)과 스케일(scale)은 필수입니다."}

        mappings = SourceMappingManager.load_all_mappings()
        info = mappings.get(symbol) or mappings.get(SourceMappingManager.clean_symbol(symbol))
        if not info:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {symbol}"}

        provider = str(info.get("primary_source", "")).upper()
        scale_clean = scale.strip()

        if tier == "raw":
            if provider in ("UPBIT", "BINANCE", "COINBASE"):
                try:
                    from CAiModelBaker.core.crypto_collector import sync_single_crypto_asset
                except ImportError:
                    from core.crypto_collector import sync_single_crypto_asset
                ok = sync_single_crypto_asset(symbol, info, target_scale=scale_clean)
                return {
                    "status": "ok" if ok else "error",
                    "message": f"[{provider}] {symbol} 원천 ({scale_clean}) 증분 수집 {'성공' if ok else '실패'}",
                    "symbol": symbol,
                    "scale": scale_clean,
                    "tier": "raw"
                }
            elif provider == "CREON":
                try:
                    from CAiModelBaker.core.creon_collector import download_all_krx
                except ImportError:
                    from core.creon_collector import download_all_krx
                fut = info.get("futures_symbol") or "10100"
                idx = info.get("index_symbol") or "U180"
                download_all_krx(target_symbol=symbol, futures_symbol=fut, index_symbol=idx, sources=[scale_clean])
                return {
                    "status": "ok",
                    "message": f"[CREON] {symbol} 원천 ({scale_clean}) 증분 수집 완료",
                    "symbol": symbol,
                    "scale": scale_clean,
                    "tier": "raw"
                }
            elif provider in ("CME", "YAHOO"):
                try:
                    from CAiModelBaker.core.overseas_futures_collector import download_and_save_futures
                except ImportError:
                    from core.overseas_futures_collector import download_and_save_futures
                clean_sym = SourceMappingManager.clean_symbol(symbol)
                out = download_and_save_futures(clean_sym, interval=scale_clean.lower())
                ok = bool(out and os.path.exists(out))
                return {
                    "status": "ok" if ok else "error",
                    "message": f"[{provider}] {symbol} 원천 ({scale_clean}) 증분 수집 {'완료' if ok else '실패'}",
                    "symbol": symbol,
                    "scale": scale_clean,
                    "tier": "raw"
                }
            elif provider == "KIS":
                clean_sym = SourceMappingManager.clean_symbol(symbol)
                try:
                    from CAiModelBaker.core.overseas_futures_collector import download_and_save_us_stock
                except ImportError:
                    from core.overseas_futures_collector import download_and_save_us_stock
                out = download_and_save_us_stock(clean_sym, interval=scale_clean.lower())
                ok = bool(out and os.path.exists(out))
                return {
                    "status": "ok" if ok else "error",
                    "message": f"[{provider}] {symbol} 원천 ({scale_clean}) 증분 수집 {'완료' if ok else '실패'}",
                    "symbol": symbol,
                    "scale": scale_clean,
                    "tier": "raw"
                }
            elif provider in ("CTRADER", "SPOTWARE"):
                baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                repo_root = os.path.dirname(baker_root)
                script_path = os.path.join(repo_root, "scripts", "fetch_ctrader_history.py")
                ctrader_env_path = os.path.join(repo_root, "CTrade", ".env")
                cfd_env = os.environ.copy()
                if os.path.exists(ctrader_env_path):
                    with open(ctrader_env_path, "r", encoding="utf-8") as ef:
                        for line in ef:
                            line = line.strip()
                            if not line or line.startswith("#") or "=" not in line:
                                continue
                            k, v = line.split("=", 1)
                            cfd_env[k.strip()] = v.strip().strip("'").strip('"')
                clean_sym = SourceMappingManager.clean_symbol(symbol)
                from shared.data_library.raw_ingest import archive_start
                start_dt = archive_start()
                total_days = max(1, (datetime.now() - start_dt).days + 1)
                cmd = [
                    sys.executable, script_path,
                    "--symbol", clean_sym,
                    "--period", scale_clean.lower(),
                    "--days", str(total_days)
                ]
                proc = subprocess.run(cmd, cwd=repo_root, env=cfd_env, capture_output=True, text=True, timeout=180)
                ok = (proc.returncode == 0)
                return {
                    "status": "ok" if ok else "error",
                    "message": f"[{provider}] {symbol} 원천 ({scale_clean}) 증분 수집 {'완료' if ok else '실패: ' + (proc.stderr or proc.stdout)[:100]}",
                    "symbol": symbol,
                    "scale": scale_clean,
                    "tier": "raw"
                }
            else:
                return {"status": "error", "message": f"지원하지 않는 프로바이더입니다: {provider}"}

        elif tier == "normalized":
            if provider in ("UPBIT", "BINANCE", "COINBASE"):
                from CAiModelBaker.core.normalized_builder import build_normalized_crypto
                raw_stat = SourceMappingManager.find_raw_file_and_manifest(symbol, provider, info.get("category", ""))
                raw_path = raw_stat.get("file_path")
                if not raw_path or not os.path.exists(raw_path):
                    raw_dir = data_vault_path("raw", "crypto", provider.lower())
                    raw_path = os.path.join(raw_dir, f"{symbol}_1m.csv")
                out_path = build_normalized_crypto(symbol=symbol, interval=scale_clean.lower(), raw_candle_path=raw_path)
                return {
                    "status": "ok" if out_path else "error",
                    "message": f"[{provider}] {symbol} 정규화 ({scale_clean}) 합성 완료: {out_path}",
                    "symbol": symbol,
                    "scale": scale_clean,
                    "tier": "normalized"
                }
            else:
                from CAiModelBaker.core.normalized_builder import build_normalized_asset
                out_path = build_normalized_asset(symbol=symbol, interval=scale_clean.lower())
                return {
                    "status": "ok" if out_path else "error",
                    "message": f"{symbol} 정규화 ({scale_clean}) 합성 완료: {out_path}",
                    "symbol": symbol,
                    "scale": scale_clean,
                    "tier": "normalized"
                }
        else:
            return {"status": "error", "message": f"유효하지 않은 계층(tier)입니다: {tier}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/delete_single_scale")
async def api_data_vault_delete_single_scale(payload: dict):
    """지정된 자산의 특정 시간 단위(스케일) 물리 파일 안전 삭제 (원천 CSV 또는 정규화 Parquet)"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        from shared.data_library.paths import data_vault_path
        symbol = payload.get("symbol")
        scale = payload.get("scale")
        tier = payload.get("tier", "normalized").lower()

        if not symbol or not scale:
            return {"status": "error", "message": "심볼(symbol)과 스케일(scale)은 필수입니다."}

        mappings = SourceMappingManager.load_all_mappings()
        info = mappings.get(symbol) or mappings.get(SourceMappingManager.clean_symbol(symbol)) or {}
        provider = str(info.get("primary_source", "")).upper()
        scale_clean = scale.strip()
        deleted_files = []

        if tier == "raw":
            candidates = []
            if provider in ("UPBIT", "BINANCE", "COINBASE"):
                raw_dir = data_vault_path("raw", "crypto", provider.lower())
                candidates.extend([
                    os.path.join(raw_dir, f"{symbol}_{scale_clean.lower()}.csv"),
                    os.path.join(raw_dir, f"{symbol}_{scale_clean.upper()}.csv"),
                ])
            elif provider == "CREON":
                clean_sym = SourceMappingManager.clean_symbol(symbol)
                if scale_clean.upper() == "TICK":
                    tick_dir = data_vault_path("raw", "creon", "tick", f"A{clean_sym}")
                    if os.path.exists(tick_dir):
                        import shutil
                        shutil.rmtree(tick_dir, ignore_errors=True)
                        deleted_files.append(tick_dir)
                    legacy_csv = data_vault_path("raw", "creon", "tick", f"A{clean_sym}_tick.csv")
                    candidates.append(legacy_csv)
                elif scale_clean.upper() == "1M":
                    candle_dir = data_vault_path("raw", "creon", "candle_1m", f"A{clean_sym}")
                    if os.path.exists(candle_dir):
                        import shutil
                        shutil.rmtree(candle_dir, ignore_errors=True)
                        deleted_files.append(candle_dir)
                    legacy_csv = data_vault_path("raw", "creon", "candle_1m", f"A{clean_sym}_1m.csv")
                    candidates.append(legacy_csv)
                elif scale_clean.upper() == "1D":
                    candidates.extend([
                        data_vault_path("raw", "creon", "candle_1d", f"A{clean_sym}.csv"),
                        data_vault_path("raw", "creon", "candle_1d", f"A{clean_sym}_1d.csv"),
                    ])
            elif provider in ("CME", "YAHOO"):
                clean_sym = SourceMappingManager.clean_symbol(symbol)
                raw_base = data_vault_path("raw", "overseas")
                sc = scale_clean.lower()
                candidates.extend([
                    os.path.join(raw_base, f"futures_{sc}", f"{clean_sym}_{sc}.csv"),
                    os.path.join(raw_base, f"futures_{sc}", f"{clean_sym}_{scale_clean.upper()}.csv"),
                    data_vault_path("raw", "cme", "futures", f"{clean_sym}_{sc}.csv"),
                ])
            elif provider == "KIS":
                clean_sym = SourceMappingManager.clean_symbol(symbol)
                raw_base = data_vault_path("raw", "kis")
                sc = scale_clean.lower()
                candidates.extend([
                    data_vault_path("raw", "yahoo", f"candle_{sc}", f"{clean_sym}_{sc}.csv"),   # 미국 주식·ETF(출처 Yahoo)
                    os.path.join(raw_base, f"candle_{sc}", f"{clean_sym}_{sc}.csv"),
                    os.path.join(raw_base, f"candle_{sc}", f"A{clean_sym}_{sc}.csv"),
                    os.path.join(raw_base, f"{clean_sym}_{sc}.csv"),
                ])
            elif provider in ("CTRADER", "SPOTWARE"):
                clean_sym = SourceMappingManager.clean_symbol(symbol)
                raw_dir = data_vault_path("raw", "ctrader")
                sc = scale_clean.lower()
                candidates.extend([
                    os.path.join(raw_dir, f"{clean_sym}_{sc}.csv"),
                    os.path.join(raw_dir, f"{symbol}_{sc}.csv"),
                    os.path.join(raw_dir, f"{clean_sym}_{scale_clean.upper()}.csv"),
                    os.path.join(raw_dir, f"{symbol}_{scale_clean.upper()}.csv"),
                ])
            else:
                raw_dir = data_vault_path("raw", provider.lower())
                candidates.append(os.path.join(raw_dir, f"{symbol}_{scale_clean.lower()}.csv"))

            for c in candidates:
                if os.path.exists(c):
                    os.remove(c)
                    deleted_files.append(c)
                mf = c + ".manifest.json" if not c.endswith(".manifest.json") else c
                if os.path.exists(mf):
                    os.remove(mf)
                    deleted_files.append(mf)
                dj = c + ".days.json"
                if os.path.exists(dj):
                    os.remove(dj)
                    deleted_files.append(dj)

        elif tier == "normalized":
            norm_dir = data_vault_path("normalized", f"candles_{scale_clean.lower()}")
            pq_path = os.path.join(norm_dir, f"{symbol}_{scale_clean.lower()}.parquet")
            mf_path = pq_path + ".manifest.json"
            if os.path.exists(pq_path):
                os.remove(pq_path)
                deleted_files.append(pq_path)
            if os.path.exists(mf_path):
                os.remove(mf_path)
                deleted_files.append(mf_path)

        try:
            from shared.data_library.raw_store_health import RawStoreHealthEngine
            RawStoreHealthEngine.clear_cache()
        except Exception:
            pass

        return {
            "status": "ok",
            "message": f"[{tier.upper()}] {symbol} ({scale_clean}) 삭제 완료 ({len(deleted_files)}개 파일 제거)",
            "symbol": symbol,
            "scale": scale_clean,
            "tier": tier,
            "deleted_files": deleted_files
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/update_intervals")
async def api_raw_store_update_intervals(payload: dict):
    """자산의 목표 정규화 봉 스케일(canonical_intervals) 업데이트 (하위 호환)"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        symbol = payload.get("symbol")
        intervals = payload.get("canonical_intervals")
        if not symbol or intervals is None:
            return {"status": "error", "message": "심볼과 봉 스케일 목록(canonical_intervals)은 필수입니다."}
        return SourceMappingManager.update_canonical_intervals(symbol, intervals)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/data_vault/build_asset_normalized")
async def api_data_vault_build_asset_normalized(payload: dict):
    """특정 자산의 모든 선언된 봉 스케일을 실버(Silver) 계층으로 일괄 정규화"""
    try:
        from CAiModelBaker.core.normalized_builder import build_single_asset_normalized
        symbol = payload.get("symbol")
        if not symbol:
            return {"status": "error", "message": "심볼이 지정되지 않았습니다."}
        
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, build_single_asset_normalized, symbol)
        return res
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _mirror_raw_to_legacy(raw_root: str, legacy_dir: str, symbols: list) -> int:
    """DataVault 원천(raw_root/<주기 폴더>/<SYM>_<주기>.csv)을 레거시 폴더(legacy_dir/<SYM>_<주기>.csv)로 복사한다 (옛 코드 호환 사본).
    원본 → 사본 단방향이며, 레거시 쪽을 원본으로 되돌려 쓰는 일은 없다."""
    import glob as _g
    os.makedirs(legacy_dir, exist_ok=True)
    n = 0
    syms = {str(s).upper() for s in symbols}
    for f in _g.glob(os.path.join(raw_root, "*", "*.csv")):
        base = os.path.basename(f)
        if base.rsplit("_", 1)[0].upper() not in syms:
            continue
        dst = os.path.join(legacy_dir, base)
        try:
            if os.path.exists(dst) and os.path.samefile(f, dst):
                os.remove(dst)                       # 옛 하드링크는 끊고 사본으로 둔다
        except OSError:
            pass
        shutil.copy2(f, dst)
        n += 1
    return n


async def run_single_asset_refetch_worker(symbol: str, info: dict):
    """단일 자산 기존 원천 데이터 초기화 후 처음부터 전체 재수집 백그라운드 워커"""
    from shared.data_library.source_mapping_manager import SourceMappingManager
    log_fn = get_log_fn("RAW-RESET-REFETCH")
    loop = asyncio.get_event_loop()
    clean_sym = SourceMappingManager.clean_symbol(symbol)
    name = info.get("name", symbol)
    primary_source = (info.get("primary_source") or "").upper()
    category = (info.get("category") or "").upper()

    await log_fn(f"🚀 [Raw Store] '{name}'({symbol}) 기존 원천 데이터 초기화 완료 ➔ 원천에서 처음부터(Full) 재수집 시작...")

    try:
        # 1. 가상자산 (Upbit, Binance, Coinbase)
        if category in ("CRYPTO_SPOT", "CRYPTO_FUTURES") or primary_source in ("UPBIT", "BINANCE", "COINBASE"):
            from CAiModelBaker.core.crypto_collector import sync_single_crypto_asset
            await log_fn(f"🪙 [Crypto] {name}({symbol}) 전 구간 다운로드 시작...")
            ok = await loop.run_in_executor(
                None,
                lambda: sync_single_crypto_asset(
                    symbol,
                    info,
                    local_log=lambda m: asyncio.run_coroutine_threadsafe(log_fn(m), loop).result()
                )
            )
            if ok:
                await log_fn(f"✅ [Crypto] {name}({symbol}) 원천 재수집 및 정규화 완료")
            else:
                await log_fn(f"⚠️ [Crypto] {name}({symbol}) 재수집 중 알림 또는 실패 발생")

        # 2. cTrader CFD
        elif primary_source in ("CTRADER", "SPOTWARE"):
            await log_fn(f"📈 [CFD] {name}({symbol}) 전 구간 다운로드 시작...")
            baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            repo_root = os.path.dirname(baker_root)
            ctrader_env_path = os.path.join(repo_root, "CTrade", ".env")
            cfd_env = os.environ.copy()
            if os.path.exists(ctrader_env_path):
                with open(ctrader_env_path, "r", encoding="utf-8") as ef:
                    for line in ef:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        cfd_env[k.strip()] = v.strip().strip("'").strip('"')

            script_path = os.path.join(repo_root, "scripts", "fetch_ctrader_history.py")
            from shared.data_library.raw_ingest import archive_start
            start_dt = archive_start()
            total_days = max(1, (datetime.now() - start_dt).days + 1)

            def _run_ctrader():
                proc = subprocess.Popen(
                    [sys.executable, script_path, "--symbol", symbol, "--days", str(total_days)],
                    cwd=repo_root,
                    env=cfd_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1
                )
                for raw_line in iter(proc.stdout.readline, b''):
                    if raw_line:
                        l_str = raw_line.decode('utf-8', errors='replace').strip()
                        if l_str:
                            asyncio.run_coroutine_threadsafe(log_fn(f"   [CFD-{symbol}] {l_str}"), loop).result()
                proc.stdout.close()
                proc.wait()
                return proc.returncode

            rc = await loop.run_in_executor(None, _run_ctrader)
            if rc == 0:
                await log_fn(f"✅ [CFD] {name}({symbol}) 원천 수집 완결 ➔ 정규화 진행...")
                norm_script = os.path.join(repo_root, "scripts", "normalize_ctrader_data.py")
                if os.path.exists(norm_script):
                    await loop.run_in_executor(
                        None,
                        lambda: subprocess.run([sys.executable, norm_script, "--symbol", symbol], cwd=repo_root)
                    )
            else:
                await log_fn(f"⚠️ [CFD] {name}({symbol}) 수집 프로세스 종료 코드: {rc}")

        # 3. CME 해외선물
        elif category == "OVERSEAS_FUTURES":
            await log_fn(f"🌐 [CME] {name}({symbol}) 전 구간 다운로드 시작...")
            from CAiModelBaker.core.overseas_futures_collector import download_all_major_futures
            await loop.run_in_executor(
                None,
                lambda: download_all_major_futures(
                    symbols=[symbol],
                    local_log=lambda m: asyncio.run_coroutine_threadsafe(log_fn(m), loop).result()
                )
            )
            from CAiModelBaker.core.normalized_builder import build_single_asset_normalized
            await loop.run_in_executor(None, lambda: build_single_asset_normalized(symbol))
            await log_fn(f"✅ [CME] {name}({symbol}) 원천 재수집 및 정규화 완료")

        # 4. 미국 직투 자산 (US_ETF, US_STOCK)
        elif category in ("US_ETF", "US_STOCK") or primary_source in ("KIS", "YAHOO"):
            await log_fn(f"🇺🇸 [US Market] {name}({symbol}) 전 구간 다운로드 시작...")
            from CAiModelBaker.core.overseas_futures_collector import download_and_save_us_stock
            primary_type = str(info.get("primary_type", "1M_5M")).upper()
            target_intervals = []
            if "1M" in primary_type:
                target_intervals.append(("1m", "7d"))
            if "5M" in primary_type:
                target_intervals.append(("5m", "60d"))
            if "1D" in primary_type or "10Y" in primary_type:
                target_intervals.append(("1d", "10y"))
            if not target_intervals:
                target_intervals = [("1m", "7d"), ("5m", "60d"), ("1d", "10y")]

            for intv, rng in target_intervals:
                await loop.run_in_executor(
                    None,
                    lambda i=intv, r=rng: download_and_save_us_stock(
                        symbol, interval=i, range_str=r,
                        local_log=lambda m: asyncio.run_coroutine_threadsafe(log_fn(m), loop).result()
                    )
                )
            from CAiModelBaker.core.normalized_builder import build_single_asset_normalized
            await loop.run_in_executor(None, lambda: build_single_asset_normalized(symbol))
            await log_fn(f"✅ [US Market] {name}({symbol}) 원천 재수집 및 정규화 완료")

        # 5. 대신증권 (CREON)
        elif primary_source == "CREON":
            await log_fn(f"🏛️ [CREON] {name}({symbol}) 호스트 수집기 가동...")
            is_win = sys.platform.startswith("win")
            candidate_ps = [
                "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                shutil.which("powershell.exe"),
                "/mnt/c/Windows/sysnative/WindowsPowerShell/v1.0/powershell.exe",
                "/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            ]
            ps_exe = next((p for p in candidate_ps if p and os.path.exists(p)), None)
            uname_str = ""
            try:
                uname_str = os.uname().release.lower()
            except Exception:
                pass
            is_wsl = not is_win and (ps_exe is not None or "microsoft" in uname_str or "wsl" in uname_str)

            baker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if is_wsl and ps_exe:
                if category == "KRX_FUTURES":
                    creon_cmd = f'py -3.10-32 -u core\\creon_collector.py --type futures --symbol {symbol} --futures_symbol {symbol} --interval 1 --count 99999999'
                elif info.get("role") == "index_benchmark":
                    creon_cmd = f'py -3.10-32 -u core\\creon_collector.py --type index --symbol {symbol} --index_symbol {symbol} --interval 1 --count 99999999'
                else:
                    creon_cmd = f'py -3.10-32 -u core\\creon_collector.py --type minute --symbol A{clean_sym} --interval 1 --count 99999999'

                full_ps_cmd = f'"{ps_exe}" -NonInteractive -NoProfile -ExecutionPolicy Bypass -Command "{creon_cmd}"'
                await log_fn(f"   📡 명령 실행: {full_ps_cmd}")

                def _run_wsl():
                    proc = subprocess.Popen(
                        full_ps_cmd,
                        shell=True,
                        cwd=baker_dir,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        bufsize=1
                    )
                    for raw_line in iter(proc.stdout.readline, b''):
                        if raw_line:
                            try:
                                l_str = raw_line.decode('utf-8')
                            except UnicodeDecodeError:
                                l_str = raw_line.decode('cp949', errors='replace')
                            cl = l_str.strip()
                            if cl:
                                asyncio.run_coroutine_threadsafe(log_fn(f"   [Creon-Win] {cl}"), loop).result()
                    proc.stdout.close()
                    proc.wait()
                    return proc.returncode

                rc = await loop.run_in_executor(None, _run_wsl)
                if rc == 0:
                    await log_fn(f"✅ [CREON] {name}({symbol}) 원천 재수집 완결 ➔ 정규화 진행...")
                    from CAiModelBaker.core.normalized_builder import build_single_asset_normalized
                    await loop.run_in_executor(None, lambda: build_single_asset_normalized(symbol))
                else:
                    await log_fn(f"⚠️ [CREON] {name}({symbol}) 프로세스 종료 코드: {rc}")
            else:
                await log_fn(f"⚠️ [CREON] 호스트 Windows/WSL PowerShell 브릿지를 사용할 수 없습니다.")

        # 사이드카 매니페스트 SHA-256 감사 발행
        await log_fn(f"📜 [Manifest] {symbol} 1:1 사이드카 매니페스트 SHA-256 발행 중...")
        try:
            from shared.data_library.manifest_manager import ensure_all_manifests
            await loop.run_in_executor(None, ensure_all_manifests)
        except Exception:
            pass

        await log_fn(f"🎉 [Raw Store] '{name}'({symbol}) 원천 데이터 초기화 후 전체 재수집 완료!")
    except Exception as e:
        await log_fn(f"❌ [Raw Store] '{name}'({symbol}) 재수집 중 예외 발생: {e}")


async def run_active_feeds_sync_worker(
    active_maps: dict,
    payload: dict,
    progress_task: str = "raw_feed_sync",
    progress_range: tuple = (0, 100),
    emit_task_done: bool = True
):
    """등록된 활성 자산 핀포인트 순회 증분 수집 백그라운드 워커"""
    from shared.data_library.source_mapping_manager import SourceMappingManager
    log_fn = get_log_fn("RAW-FEED-SYNC")
    loop = asyncio.get_event_loop()
    total_targets = len(active_maps)
    sync_started_at = datetime.now().isoformat(timespec="seconds")
    await log_fn(f"🚀 [Raw Store] 총 {total_targets}개 활성 등록 자산 일괄 증분 동기화 시작...")

    async def _emit_progress(raw_p: int, phase_str: str):
        p_start, p_end = progress_range
        scaled_p = int(p_start + (raw_p / 100.0) * (p_end - p_start))
        p_data = {
            "type": "task_progress",
            "task": progress_task,
            "progress": scaled_p,
            "phase": phase_str
        }
        if progress_task == "medallion_full_pipeline":
            p_data["step"] = 1
        await manager.broadcast(json.dumps(p_data))

    try:
        # 출처 및 카테고리 기반 동적 자산 분류 (하드코딩 배제)
        creon_symbols = [s for s, info in active_maps.items() if info.get("primary_source") == "CREON"]
        cme_symbols = [s for s, info in active_maps.items() if info.get("category") == "OVERSEAS_FUTURES"]
        def _looks_domestic_krx_code(sym: str) -> bool:
            # 0167A0/0048J0처럼 영문이 섞인 KRX 코드도 국내로 인식 (해외 티커 오분류 방지)
            return sym.isdigit() or (len(sym) == 6 and sym.isalnum() and any(c.isdigit() for c in sym))

        us_stock_symbols = [
            s for s, info in active_maps.items()
            if info.get("category") in ("US_ETF", "US_STOCK") or
               (info.get("primary_source") in ("KIS", "YAHOO") and not _looks_domestic_krx_code(s) and s not in cme_symbols)
        ]
        crypto_symbols = [s for s, info in active_maps.items() if info.get("category") in ("CRYPTO_SPOT", "CRYPTO_FUTURES")]
        cfd_symbols = [s for s, info in active_maps.items() if info.get("primary_source") == "CTRADER"]

        # 1. 크레온 KRX 자산 일괄 증분 수집
        if creon_symbols:
            # CREON 수집에 필요한 역할별 심볼을 ledger(active_maps)에서 동적 도출 — 하드코딩 금지
            # 심볼을 결정할 수 없으면 조용한 오작동 대신 즉시 실패(Fail-Fast)
            # 검증은 반드시 이 블록 안에서만 수행한다: 밖에서 하면 CREON 자산이 하나도 활성화되지 않은
            # 가상자산/해외자산 단독 동기화까지 ValueError 로 차단된다.
            target_krx = str(payload.get("target_symbol") or payload.get("symbol") or "").strip()
            if not target_krx:
                _creon_targets = [s for s, info in active_maps.items()
                                  if info.get("primary_source") == "CREON" and info.get("role") == "target"]
                if not _creon_targets:
                    raise ValueError("active_maps에 CREON role=target 심볼이 없습니다. source_mappings.json을 확인하세요.")
                target_krx = _creon_targets[0]
            clean_tgt = SourceMappingManager.clean_symbol(target_krx)
            if not clean_tgt:
                raise ValueError(f"CREON target 심볼 '{target_krx}'을 정규화할 수 없습니다.")
            target_sym_arg = f"A{clean_tgt}"

            # 지수선물만 고른다: 개별주식선물(원장 underlying 보유, 예: 15000)도 KRX_FUTURES 라 순서에 기대면 엉뚱한 선물을 집는다.
            # 개별주식선물은 아래 ref_syms 로 넘어가 download_all_krx 가 선물 경로로 수집한다.
            from shared.config.config_manager import get_symbol_info as _gsi
            _creon_futures = [s for s, info in active_maps.items()
                              if info.get("primary_source") == "CREON" and info.get("category") == "KRX_FUTURES"
                              and not (_gsi(s) or {}).get("underlying")]
            if not _creon_futures:
                raise ValueError("active_maps에 CREON category=KRX_FUTURES 지수선물(underlying 없음)이 없습니다. source_mappings.json을 확인하세요.")
            futures_sym = _creon_futures[0]

            _creon_index = [s for s, info in active_maps.items()
                            if info.get("primary_source") == "CREON" and info.get("role") == "index_benchmark"]
            if not _creon_index:
                raise ValueError("active_maps에 CREON role=index_benchmark 심볼이 없습니다. source_mappings.json을 확인하세요.")
            index_sym = _creon_index[0]

            await _emit_progress(15, f"대신증권(CREON) {len(creon_symbols)}개 자산 수집 시작")
            tgt_set = {target_krx, f"A{clean_tgt}", clean_tgt} if clean_tgt else set()
            ref_syms = [s for s in creon_symbols if s not in tgt_set and s not in (futures_sym, index_sym)]
            # 심볼 목록은 반드시 따옴표로 감싼다: PowerShell 은 따옴표 없는 `005930,000660,...` 를 숫자 배열로 해석해
            # 앞자리 0 을 지우고(005930→5930), `0148E0` 같은 영숫자 코드는 지수 표기 숫자(148)로 바꿔 버린다.
            safe_refs = [s_ for s_ in ref_syms if re.fullmatch(r"[A-Za-z0-9]{1,12}", str(s_))]
            ref_arg = f" --ref_symbols '{','.join(safe_refs)}'" if safe_refs else ''
            
            is_win = sys.platform.startswith("win")
            uname_str = ""
            try:
                uname_str = os.uname().release.lower()
            except Exception:
                pass

            candidate_ps = [
                "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                shutil.which("powershell.exe"),
                "/mnt/c/Windows/sysnative/WindowsPowerShell/v1.0/powershell.exe",
                "/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            ]
            ps_exe = next((p for p in candidate_ps if p and os.path.exists(p)), None)

            is_wsl = not is_win and (
                ps_exe is not None or
                "microsoft" in uname_str or
                "wsl" in uname_str
            )
            
            if is_win:
                try:
                    from CAiModelBaker.core.creon_collector import download_all_krx
                    await loop.run_in_executor(
                        None,
                        lambda: download_all_krx(
                            target_symbol=target_sym_arg,
                            futures_symbol=futures_sym,
                            index_symbol=index_sym,
                            ref_symbols=ref_syms,
                            local_log=lambda m: asyncio.run_coroutine_threadsafe(log_fn(m), loop).result()
                        )
                    )
                except Exception as ce:
                    await log_fn(f"   ⚠️ CREON 수집 알림: {ce}")
            elif is_wsl and ps_exe:
                # WSL 환경: Windows PowerShell 브릿지를 통해 32-bit Python 크레온 수집기 가동
                await log_fn(f"   🌐 [WSL Bridge] 호스트 Windows PowerShell 브릿지({ps_exe})를 통해 32-bit 크레온 일괄 수집 가동...")
                baker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                cmd_str = f'"{ps_exe}" -NonInteractive -NoProfile -ExecutionPolicy Bypass -Command "py -3.10-32 -u core\\creon_collector.py --type all_krx --symbol {target_sym_arg} --futures_symbol {futures_sym} --index_symbol {index_sym} --interval 1 --count 99999999{ref_arg}"'
                await log_fn(f"   📡 명령 실행: {cmd_str}")
                try:
                    def _run_wsl_creon():
                        proc = subprocess.Popen(
                            cmd_str,
                            shell=True,
                            cwd=baker_dir,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            bufsize=1
                        )
                        for raw_line in iter(proc.stdout.readline, b''):
                            if raw_line:
                                try:
                                    l_str = raw_line.decode('utf-8')
                                except UnicodeDecodeError:
                                    l_str = raw_line.decode('cp949', errors='replace')
                                cl = l_str.strip()
                                if cl:
                                    asyncio.run_coroutine_threadsafe(log_fn(f"   [Creon-Win] {cl}"), loop).result()
                        proc.stdout.close()
                        proc.wait()
                        return proc.returncode
                    
                    rc = await loop.run_in_executor(None, _run_wsl_creon)
                    if rc == 0:
                        await log_fn("   ✅ [WSL Bridge] 크레온 수집 성공 완결")
                    else:
                        await log_fn(f"   ⚠️ [WSL Bridge] 크레온 프로세스 종료 코드: {rc}")
                except Exception as we:
                    await log_fn(f"   ⚠️ [WSL Bridge] 브릿지 실행 예외: {we}")
            elif is_wsl and not ps_exe:
                await log_fn("   ⚠️ [WSL Bridge] WSL 환경이 감지되었으나 Windows powershell.exe 경로(/mnt/c/...)에 접근할 수 없습니다. (호스트 마운트 또는 샌드박스 상태 점검 필요)")
            else:
                await log_fn("   ℹ️ [Linux VPS] Windows/WSL 환경이 아니므로 기존 원천 아카이브(1.30 GB)를 안전하게 유지합니다.")

            # 틱 일자 저장소: 윈도우 수집기가 봉인한 파일의 읽기 전용 속성은 WSL 쪽에 적용되지 않으므로 여기서 다시 잠근다
            try:
                from CAiModelBaker.core import tick_day_store as _tds
                from shared.data_library.paths import data_vault_path as _dvp_t
                _troot = _dvp_t("raw", "creon", "tick")
                for _d in (sorted(os.listdir(_troot)) if os.path.isdir(_troot) else []):
                    if _d.startswith("A") and os.path.isdir(os.path.join(_troot, _d)):
                        _new = _tds.lock_sealed(_troot, _d)
                        if _new:
                            await log_fn(f"   🔒 [DayStore] {_d}: 봉인 파일 잠금 {len(_new)}일 ({_new[-1]})")
            except Exception as _le:
                await log_fn(f"   ⚠️ [DayStore] 봉인 파일 잠금 건너뜀: {_le}")
            # 1분봉 일자 저장소(candle_1m/A{종목}/ · 선물·지수 A10100/ AU180/)도 같다
            try:
                from CAiModelBaker.core import candle_day_store as _cds
                from shared.data_library import candle_store as _cs
                _croot = _cs.candle_root()
                for _d in (sorted(os.listdir(_croot)) if os.path.isdir(_croot) else []):
                    if _d.startswith("A") and os.path.isdir(os.path.join(_croot, _d)):
                        _new = _cds.lock_sealed(_croot, _d)
                        if _new:
                            await log_fn(f"   🔒 [DayStore 1m] {_d}: 봉인 파일 잠금 {len(_new)}일 ({_new[-1]})")
            except Exception as _le:
                await log_fn(f"   ⚠️ [DayStore 1m] 봉인 파일 잠금 건너뜀: {_le}")

        # 2. CME 해외선물 다계층 증분 수집
        if cme_symbols:
            await _emit_progress(30, f"CME 해외선물({', '.join(cme_symbols)}) 수집 중...")
            await log_fn(f"📥 [2/6] CME 해외선물({', '.join(cme_symbols)}) 다층 타임프레임 증분 수집...")
            try:
                from CAiModelBaker.core.overseas_futures_collector import download_all_major_futures
                await loop.run_in_executor(
                    None,
                    lambda: download_all_major_futures(
                        symbols=cme_symbols,
                        local_log=lambda m: asyncio.run_coroutine_threadsafe(log_fn(m), loop).result()
                    )
                )
            except Exception as fe:
                await log_fn(f"   ⚠️ CME 선물 수집 알림: {fe}")

            # 원천의 원본은 DataVault(수집기가 공통 절차로 직접 저장). 옛 코드가 읽는 레거시 폴더에는 사본만 둔다 (단방향: DataVault → 레거시)
            try:
                from shared.data_library.paths import data_vault_path, data_path
                _mirror_raw_to_legacy(data_vault_path("raw", "overseas"), data_path("historical", "overseas_futures"),
                                      [s_ for s_ in cme_symbols])
            except Exception as se:
                await log_fn(f"   ⚠️ 해외선물 레거시 사본 알림: {se}")

        # 3. 미국 직투 자산 (US_ETF, US_STOCK) 다계층 증분 수집
        if us_stock_symbols:
            await _emit_progress(45, f"미국 직투 자산({', '.join(us_stock_symbols)}) 수집 중...")
            await log_fn(f"📥 [3/6] 미국 직투 자산({len(us_stock_symbols)}개: {', '.join(us_stock_symbols)}) 다층 타임프레임 증분 수집...")
            try:
                from CAiModelBaker.core.overseas_futures_collector import download_and_save_us_stock
                from shared.data_library.paths import data_vault_path, data_path
                baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                
                for us_sym in us_stock_symbols:
                    info = active_maps.get(us_sym, {})
                    primary_type = str(info.get("primary_type", "1M_5M")).upper()
                    target_intervals = []
                    if "1M" in primary_type:
                        target_intervals.append(("1m", "7d"))
                    if "5M" in primary_type:
                        target_intervals.append(("5m", "60d"))
                    if "1D" in primary_type or "10Y" in primary_type:
                        target_intervals.append(("1d", "10y"))
                    if not target_intervals:
                        target_intervals = [("1m", "7d"), ("5m", "60d"), ("1d", "10y")]
                        
                    for intv, rng in target_intervals:
                        await loop.run_in_executor(
                            None,
                            lambda s=us_sym, i=intv, r=rng: download_and_save_us_stock(
                                symbol=s, interval=i, range_str=r, baker_root=baker_root,
                                local_log=lambda m: asyncio.run_coroutine_threadsafe(log_fn(m), loop).result()
                            )
                        )
                # 원천의 원본은 DataVault/raw/yahoo (수집기가 공통 절차로 직접 저장) — 레거시 폴더에는 사본만 둔다
                for us_sym in us_stock_symbols:
                    for intv in ("1m", "5m", "1d"):
                        src_f = data_vault_path("raw", "yahoo", f"candle_{intv}", f"{us_sym}_{intv}.csv")
                        if os.path.exists(src_f):
                            dst_d = data_path("historical", "kis", f"candle_{intv}")
                            os.makedirs(dst_d, exist_ok=True)
                            shutil.copy2(src_f, os.path.join(dst_d, f"{us_sym}_{intv}.csv"))
            except Exception as ue:
                await log_fn(f"   ⚠️ 미국 직투 자산 수집 알림: {ue}")

        # 4. 가상자산(업비트/바이낸스/코인베이스) 증분 수집
        if crypto_symbols:
            await _emit_progress(60, f"가상자산({', '.join(crypto_symbols)}) 수집 중...")
            _tb4 = time.perf_counter()
            await log_fn(f"📥 [4/6] 가상자산({len(crypto_symbols)}개: {', '.join(crypto_symbols)}) 증분 수집...")
            try:
                from CAiModelBaker.core.crypto_collector import sync_all_active_crypto

                def _crypto_progress_cb(sub_p: int, phase_str: str = ""):
                    overall_p = int(60 + (sub_p / 100.0) * 18)
                    msg = f"가상자산 수집: {phase_str}" if phase_str else f"가상자산 수집 중 ({sub_p}%)"
                    try:
                        asyncio.run_coroutine_threadsafe(_emit_progress(overall_p, msg), loop).result()
                    except Exception:
                        pass

                await loop.run_in_executor(
                    None,
                    lambda: sync_all_active_crypto(
                        crypto_symbols,
                        local_log=lambda m: asyncio.run_coroutine_threadsafe(log_fn(m), loop).result(),
                        progress_callback=_crypto_progress_cb
                    )
                )
            except Exception as xe:
                await log_fn(f"   ⚠️ 가상자산 수집 알림: {xe}")
            await log_fn(f"   ⏱️ [4/6] 가상자산 구간 {time.perf_counter() - _tb4:.1f}초")

        # 5. CFD(ctrader) 대용 지표 증분 수집
        if cfd_symbols:
            await _emit_progress(78, f"CFD 대용지표({', '.join(cfd_symbols)}) 수집 중...")
            _tb5 = time.perf_counter()
            await log_fn(f"📥 [5/6] CFD 대용지표({len(cfd_symbols)}개: {', '.join(cfd_symbols)}) 증분 수집...")
            try:
                baker_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                repo_root = os.path.dirname(baker_root)
                ctrader_env_path = os.path.join(repo_root, "CTrade", ".env")
                cfd_env = os.environ.copy()
                if os.path.exists(ctrader_env_path):
                    with open(ctrader_env_path, "r", encoding="utf-8") as ef:
                        for line in ef:
                            line = line.strip()
                            if not line or line.startswith("#") or "=" not in line:
                                continue
                            k, v = line.split("=", 1)
                            cfd_env[k.strip()] = v.strip().strip("'").strip('"')

                if "CTRADE_ACCOUNT_ID" not in cfd_env:
                    await log_fn("   ⚠️ [CFD] CTRADE_ACCOUNT_ID 환경변수 누락 ➔ ctidTraderAccountId를 CTrade/.env에 추가해야 자동 수집이 동작합니다.")
                else:
                    script_path = os.path.join(repo_root, "scripts", "fetch_ctrader_history.py")
                    from shared.data_library import raw_ingest as _ri
                    from shared.data_library.paths import data_vault_path as _dvp2
                    from shared.data_library.source_mapping_manager import SourceMappingManager
                    mappings_all = SourceMappingManager.load_all_mappings()
                    def _run_ctrader(sym, days, period):
                        proc = subprocess.Popen(
                            [sys.executable, script_path, "--symbol", sym, "--period", period, "--days", str(days)],
                            cwd=repo_root,
                            env=cfd_env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            bufsize=1
                        )
                        for raw_line in iter(proc.stdout.readline, b''):
                            if raw_line:
                                l_str = raw_line.decode('utf-8', errors='replace').strip()
                                if l_str:
                                    asyncio.run_coroutine_threadsafe(log_fn(f"   [CFD-{sym}-{period}] {l_str}"), loop).result()
                        proc.stdout.close()
                        proc.wait()
                        return proc.returncode

                    async def _sync_cfd_symbol(cfd_sym):
                        # 심볼끼리만 병렬(파일이 심볼별로 분리), 같은 심볼의 주기는 순차. 동시 3개 상한(메모리·서버 부하).
                        async with cfd_sem:
                            _tc = time.perf_counter()
                            cfd_info = mappings_all.get(cfd_sym, {})
                            for sc in (cfd_info.get("ingestion_sources") or ["1M", "1D"]):
                                sc_str = str(sc).strip().lower()
                                csv_target = _dvp2("raw", "ctrader", f"{cfd_sym}_{sc_str}.csv")
                                _ranges = _ri.fetch_ranges(csv_target)
                                _days = max(1, (datetime.now() - min(r[0] for r in _ranges)).days + 1)
                                rc = await loop.run_in_executor(None, _run_ctrader, cfd_sym, _days, sc_str)
                                if rc != 0:
                                    await log_fn(f"   ⚠️ [CFD-{cfd_sym}-{sc_str}] 프로세스 종료 코드: {rc}")
                            await log_fn(f"   ⏱️ [CFD-{cfd_sym}] 수집 {time.perf_counter() - _tc:.1f}초")

                    cfd_sem = asyncio.Semaphore(3)
                    await asyncio.gather(*(_sync_cfd_symbol(s) for s in cfd_symbols))
            except Exception as ye:
                await log_fn(f"   ⚠️ CFD 수집 알림: {ye}")
            await log_fn(f"   ⏱️ [5/6] CFD 구간 {time.perf_counter() - _tb5:.1f}초")

        # 6. 매니페스트 전수 검증 및 발행
        await _emit_progress(90, "1:1 사이드카 매니페스트 SHA-256 감사 발행 중...")
        await log_fn("📜 [6/6] 수집 파일 1:1 사이드카 매니페스트 SHA-256 전수 감사 발행...")
        try:
            from shared.data_library.source_mapping_manager import SourceMappingManager
            from shared.data_library.manifest_manager import ManifestManager
            from shared.data_library import candle_store as _candle_store
            for s, info in active_maps.items():
                stat = SourceMappingManager.find_raw_file_and_manifest(s, info.get("primary_source", ""), info.get("category", ""))
                if stat.get("exists") and stat.get("file_path") and os.path.exists(stat["file_path"]):   # 1분봉 일자 저장소는 날짜 메타가 매니페스트를 대신한다
                    ManifestManager.generate_and_save_manifest(
                        data_file_path=stat["file_path"],
                        data_tier="raw",
                        provider=info.get("primary_source", "creon").lower(),
                        notes=info.get("name", s)
                    )
        except Exception as me:
            await log_fn(f"   ⚠️ 매니페스트 감사 알림: {me}")

        # 원천 요약(전 원천 공통): 사용자 확인이 필요한 항목(같은 시각에 다른 데이터)과 이번 동기화에서 확인된 영구 결손
        try:
            import glob as _glob
            from shared.data_library.paths import data_vault_path as _dvp
            try:
                from core import raw_day_store as _rds
            except ImportError:
                from CAiModelBaker.core import raw_day_store as _rds
            pend_total, lost_new = 0, []
            for _p in sorted(_glob.glob(os.path.join(_dvp("raw"), "**", "*.csv"), recursive=True)):
                _n = len(_rds.pending_conflicts(_p))
                if _n:
                    pend_total += _n
                    await log_fn(f"   ⚠️ [확인 필요] {os.path.basename(_p)}: 같은 시각에 기존과 다른 데이터 {_n}건 (기존 값 유지 중)")
                for _r in (_rds.load_ledger(_p) or {}).get("unrecoverable", []):
                    if _r.get("at", "") >= sync_started_at:
                        lost_new.append(f"{os.path.basename(_p)} {_r['after'][:10]}~{_r['before'][:10]}")
            if lost_new:
                await log_fn(f"   🚨 [영구 결손] 서버 보존 기간이 지나 받지 못한 구간: {', '.join(lost_new[:10])}")
            if pend_total:
                await log_fn(f"   👉 확인 방법: python scripts/raw_store_integrity.py conflicts  (결정: resolve --use new|old)")
            if not pend_total and not lost_new:
                await log_fn("   ✅ [Raw Store] 전 원천: 확인 필요 항목 없음 · 이번 동기화 영구 결손 없음")
        except Exception as _se:
            await log_fn(f"   ⚠️ 원천 요약 실패: {_se}")

        # 수집 완료 후 활성 자산 대상 초고속 증분 품질 감사 자동 실행
        try:
            from shared.data_library.data_quality import DataQualityAuditor
            from shared.data_library.raw_store_health import RawStoreHealthEngine
            await log_fn("🔍 [Raw Store] 수집 자산 품질 증분 감사(Incremental Audit) 자동 실행 중...")
            symbols_to_audit = list(active_maps.keys())
            await loop.run_in_executor(
                None,
                lambda: DataQualityAuditor.audit_all_symbols(symbols=symbols_to_audit, full=False, max_workers=1)   # 100MB+ CSV 병렬 적재는 WSL 8GB에서 OOM(2026-10-07 Killed) → 직렬
            )
            RawStoreHealthEngine.clear_cache()
            await log_fn("✅ [Raw Store] 수집 자산 품질 증분 감사 완료!")
        except Exception as _qe:
            await log_fn(f"   ⚠️ 품질 감사 자동 실행 알림: {_qe}")

        await log_fn("🎉 [Raw Store] 등록 자산 전수 증분 동기화 및 무결성 감사 완결!")
        await _emit_progress(100, "1단계 Bronze 계층 원천 수집 완료 (100%)")
        if emit_task_done:
            await manager.broadcast(json.dumps({
                "type": "task_progress",
                "task": progress_task,
                "progress": 100,
                "phase": "동기화 완결 (100%)"
            }))
            await manager.broadcast(json.dumps({
                "type": "task_done",
                "task": progress_task,
                "status": "success"
            }))
    except Exception as e:
        await log_fn(f"❌ [Raw Store] 동기화 중 예외 발생: {e}")
        if emit_task_done:
            await manager.broadcast(json.dumps({
                "type": "task_done",
                "task": progress_task,
                "status": "error",
                "error": str(e)
            }))
        if not emit_task_done:
            raise e


async def run_medallion_full_pipeline_worker(mode: str, payload: dict):
    """3단계 메달리온(Bronze -> Silver -> Gold) 원클릭 통합 파이프라인 백그라운드 워커 (엄격한 Fail-Fast Gate 탑재)"""
    log_fn = get_log_fn("MEDALLION-PIPELINE")
    loop = asyncio.get_event_loop()
    started_at = datetime.now().isoformat(timespec="seconds")
    await log_fn(f"🚀 [Medallion Pipeline] 3단계 메달리온 일괄 파이프라인 가동 (모드: {mode.upper()})...")
    _t0 = time.perf_counter()
    _timings = {}

    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        from shared.data_library import paths as dpaths
        active_maps = SourceMappingManager.get_active_mappings()
        if not active_maps:
            raise RuntimeError("활성화된 수집 대상 자산이 없습니다.")

        # =====================================================================
        # [Step 1 / 3] Bronze Layer: Raw Store 원천 수집
        # =====================================================================
        await manager.broadcast(json.dumps({
            "type": "task_progress",
            "task": "medallion_full_pipeline",
            "step": 1,
            "progress": 5,
            "phase": "1/3 Bronze: 활성 자산 원천 시계열 수집 중..."
        }))
        await log_fn(f"📥 [Step 1/3] Bronze 계층: {len(active_maps)}개 활성 자산 원천 시계열 수집 시작...")

        _t1 = time.perf_counter()
        await run_active_feeds_sync_worker(
            active_maps=active_maps,
            payload=payload,
            progress_task="medallion_full_pipeline",
            progress_range=(5, 35),
            emit_task_done=False
        )
        _timings["1 Bronze 수집"] = time.perf_counter() - _t1
        await log_fn(f"⏱️ [Step 1/3] Bronze 수집 {_timings['1 Bronze 수집']:.1f}초")

        # 🛡️ Gate 1 무결성 검증: 1단계 수집 결과 감사
        # 대상 심볼은 payload 우선, 없으면 ledger(active_maps)의 role=target 에서 도출한다.
        # (run_active_feeds_sync_worker 의 지역변수를 참조하면 NameError 가 된다)
        target_sym = str(payload.get("target_symbol") or payload.get("symbol") or "").strip()
        if not target_sym:
            _targets = [s for s, info in active_maps.items() if info.get("role") == "target"]
            target_sym = _targets[0] if _targets else ""
        clean_target = SourceMappingManager.clean_symbol(target_sym) if target_sym else ""
        missing_raw = []
        from shared.data_library import candle_store as _candle_store
        for s, info in active_maps.items():
            if s == clean_target or info.get("role") == "target":
                stat = SourceMappingManager.find_raw_file_and_manifest(s, info.get("primary_source", ""), info.get("category", ""))
                if not stat.get("exists") or not stat.get("file_path") or not _candle_store.path_exists(stat["file_path"]):
                    missing_raw.append(s)

        if missing_raw:
            err_msg = f"Gate 1 무결성 검증 실패: 필수 원천 파일 미확보 ({', '.join(missing_raw)}). 2단계 정규화 진입 중단."
            await log_fn(f"🛑 [Fail-Fast] {err_msg}")
            raise RuntimeError(err_msg)

        await log_fn("✅ [Gate 1 통과] Bronze 계층 원천 수집 무결성 확인 완료.")

        # =====================================================================
        # [Step 2 / 3] Silver Layer: Normalized Store 스마트 표준 정규화
        # =====================================================================
        await manager.broadcast(json.dumps({
            "type": "task_progress",
            "task": "medallion_full_pipeline",
            "step": 2,
            "progress": 40,
            "phase": f"2/3 Silver: 표준 정규화 Parquet {'전면 재구축' if mode == 'rebuild' else '스마트 정규화'} 중..."
        }))
        await log_fn(f"💎 [Step 2/3] Silver 계층: 단일 자산 표준 정규화 ({mode.upper()}) 실행...")

        _t2 = time.perf_counter()
        if mode == "rebuild":
            from CAiModelBaker.core.normalized_builder import build_all_active_normalized
            norm_res = await loop.run_in_executor(
                None,
                lambda: build_all_active_normalized(local_log=lambda m: asyncio.run_coroutine_threadsafe(log_fn(m), loop).result())
            )
            await log_fn(f"   성공 {norm_res['success_count']}개, 실패 {norm_res['failed_count']}개")
        else:
            from CAiModelBaker.core.normalized_builder import smart_normalize_all_active
            norm_res = await loop.run_in_executor(
                None,
                lambda: smart_normalize_all_active(local_log=lambda m: asyncio.run_coroutine_threadsafe(log_fn(m), loop).result())
            )
            await log_fn(f"   신규빌드 {norm_res.get('built_count', 0)}개, 증분업서트 {norm_res.get('upserted_count', 0)}개, 실패 {norm_res.get('failed_count', 0)}개")

        _timings["2 Silver 정규화"] = time.perf_counter() - _t2
        await log_fn(f"⏱️ [Step 2/3] Silver 정규화 {_timings['2 Silver 정규화']:.1f}초")

        # 🛡️ Gate 2 무결성 검증: 2단계 정규화 파일 확인
        req_intv = payload.get("interval") or "10s"
        target_pq = dpaths.normalized_path(f"candles_{req_intv}", f"{clean_target}_{req_intv}.parquet")
        fallback_pq = dpaths.normalized_path("candles_1m", f"{clean_target}_1m.parquet")
        has_valid_pq = (os.path.exists(target_pq) and os.path.getsize(target_pq) > 0) or (os.path.exists(fallback_pq) and os.path.getsize(fallback_pq) > 0)

        if not has_valid_pq:
            err_msg = f"Gate 2 무결성 검증 실패: [{clean_target}] 정규화 Parquet 미완공. 3단계 피처 결합 진입 중단."
            await log_fn(f"🛑 [Fail-Fast] {err_msg}")
            raise RuntimeError(err_msg)

        await log_fn("✅ [Gate 2 통과] Silver 계층 정규화 Parquet 무결성 확인 완료.")
        await manager.broadcast(json.dumps({
            "type": "task_progress",
            "task": "medallion_full_pipeline",
            "step": 2,
            "progress": 70,
            "phase": "2/3 Silver: 표준 정규화 Parquet 완공"
        }))

        # =====================================================================
        # [Step 3 / 3] Gold Layer: Features Store 피처셋 결합 및 스냅샷 생성
        # =====================================================================
        await manager.broadcast(json.dumps({
            "type": "task_progress",
            "task": "medallion_full_pipeline",
            "step": 3,
            "progress": 75,
            "phase": "3/3 Gold: 대장주/매크로 시점 결합 및 피처셋 스냅샷 생성 중..."
        }))
        await log_fn("🧩 [Step 3/3] Gold 계층: 온디맨드 피처셋 결합 및 스냅샷 생성...")

        from CAiModelBaker.core.feature_composer import FeatureComposer
        composer = FeatureComposer(local_log=lambda m: asyncio.run_coroutine_threadsafe(log_fn(m), loop).result())

        # 대상 프로젝트 목록 결정
        target_projects = []
        baker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        deploy_dir = os.path.join(baker_dir, "deploy")
        if os.path.exists(deploy_dir):
            for entry in sorted(os.listdir(deploy_dir)):
                epath = os.path.join(deploy_dir, entry)
                if os.path.isdir(epath) and "_" in entry:
                    # 세션(모델)이 없는 빈 디렉터리는 스킵
                    sub_dirs = [e for e in os.listdir(epath) if os.path.isdir(os.path.join(epath, e))]
                    if not sub_dirs:
                        continue
                    parts = entry.rsplit("_", 1)
                    if len(parts) == 2:
                        p_sym, p_intv = parts[0], parts[1]
                        if p_sym in ("BTC", "ETH", "USDT", "XRP", "SOL", "DOGE", "ADA"):
                            p_sym = f"KRW-{p_sym}"
                        target_projects.append((p_sym, p_intv))
        if not target_projects:
            cfg = load_config()
            target_projects.append((get_target_symbol(cfg), cfg.get("INTERVAL", "10s")))

        total_p = len(target_projects)
        compose_success = 0
        _t3 = time.perf_counter()
        for idx, (p_sym, p_intv) in enumerate(target_projects, 1):
            p_prog = 75 + int((idx / total_p) * 20)
            await manager.broadcast(json.dumps({
                "type": "task_progress",
                "task": "medallion_full_pipeline",
                "step": 3,
                "progress": p_prog,
                "phase": f"3/3 Gold: [{p_sym}] ({p_intv}) 피처셋 결합 중 ({idx}/{total_p})..."
            }))
            await log_fn(f"   🔗 피처셋 결합: {p_sym} ({p_intv})...")
            _tp = time.perf_counter()
            try:
                await loop.run_in_executor(
                    None,
                    lambda s=p_sym, i=p_intv: composer.compose(
                        target_symbol=s,
                        target_interval=i,
                        auto_bind_ssot=True,
                        save_snapshot=True,
                        align_to_common_dates=True
                    )
                )
                compose_success += 1
            except Exception as fe:
                await log_fn(f"   ⚠️ [{p_sym}] ({p_intv}) 피처셋 결합 알림: {fe}")
            await log_fn(f"   ⏱️ [{p_sym}] ({p_intv}) 결합 {time.perf_counter() - _tp:.1f}초")

        _timings["3 Gold 결합"] = time.perf_counter() - _t3
        if compose_success == 0:
            raise RuntimeError("3단계 피처셋 결합에 성공한 프로젝트가 없습니다.")

        # =====================================================================
        # 완결
        # =====================================================================
        await log_fn(f"🎉 [Medallion Pipeline] 3단계 메달리온 전체 파이프라인 무결 완결! (시작 시각: {started_at})")
        await log_fn("⏱️ [소요시간] " + " | ".join(f"{k} {v:.1f}초" for k, v in _timings.items()) + f" | 총 {time.perf_counter() - _t0:.1f}초")
        await manager.broadcast(json.dumps({
            "type": "task_progress",
            "task": "medallion_full_pipeline",
            "step": 3,
            "progress": 100,
            "phase": "3단계 메달리온 일괄 빌드 완결 (100%)"
        }))
        await manager.broadcast(json.dumps({
            "type": "task_done",
            "task": "medallion_full_pipeline",
            "status": "success",
            "message": "3단계 메달리온 일괄 빌드 완결"
        }))
    except Exception as e:
        await log_fn(f"🛑 [Medallion Pipeline 중단] {e}")
        await manager.broadcast(json.dumps({
            "type": "task_done",
            "task": "medallion_full_pipeline",
            "status": "error",
            "error": str(e)
        }))


@app.post("/api/data_vault/run_full_pipeline")
async def api_data_vault_run_full_pipeline(payload: dict, background_tasks: BackgroundTasks):
    """3단계 메달리온(Bronze -> Silver -> Gold) 일괄 파이프라인 트리거 (엄격한 단계별 검증)"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        active_maps = SourceMappingManager.get_active_mappings()
        if not active_maps:
            return {"status": "error", "message": "활성화된 수집 대상 자산이 없습니다."}

        mode = payload.get("mode", "smart")
        background_tasks.add_task(run_medallion_full_pipeline_worker, mode, payload)
        return {
            "status": "ok",
            "mode": mode,
            "message": "3단계 메달리온 일괄 파이프라인이 백그라운드에서 시작되었습니다."
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Raw Store 외부 백업 (scripts/backup_raw.py 의 함수를 그대로 사용 — 복구용 사본, 파이프라인은 읽지 않는다)
# ---------------------------------------------------------------------------
import threading as _bk_threading

_BK_JOB = {"running": False, "kind": None, "progress": 0.0, "current": "", "log": [], "result": None, "cancel": False, "started_at": None}
_BK_LOCK = _bk_threading.Lock()


def _bk_module():
    import importlib.util
    path = os.path.join(_CTRADE_SYSTEM_ROOT, "scripts", "backup_raw.py")
    spec = importlib.util.spec_from_file_location("backup_raw", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bk_log(msg):
    _BK_JOB["log"].append(f"{datetime.now().strftime('%H:%M:%S')} {msg}")
    del _BK_JOB["log"][:-300]


def _bk_run_job(kind: str, dry_run: bool):
    br = _bk_module()
    cfg = br.load_config()
    results = []
    try:
        targets = cfg.get("targets", [])
        for i, t in enumerate(targets):
            if _BK_JOB["cancel"]:
                break
            _bk_log(f"=== 대상: {t} ===")

            def prog(done, total, rel, _i=i, _n=len(targets)):
                _BK_JOB["progress"] = ((_i + (done / total if total else 1.0)) / _n) * 100
                _BK_JOB["current"] = rel

            cancel = lambda: _BK_JOB["cancel"]
            try:
                if kind == "verify":
                    bad = br.verify_target(t, log=_bk_log, progress=prog, cancel=cancel)
                    results.append({"target": t, "bad": [{"file": f, "why": w} for f, w in bad]})
                else:
                    rep = br.backup_one(t, br.RAW_ROOT, int(cfg.get("keep_versions", 3)), dry_run, log=_bk_log, progress=prog, cancel=cancel)
                    results.append({"target": t, "copied": len(rep["copied"]), "updated": len(rep["updated"]), "unchanged": rep["unchanged"],
                                    "conflicts": rep["conflicts"], "skipped": len(rep["skipped"]), "bytes": rep["bytes"]})
            except br.BackupError as e:
                _bk_log(f"❌ {e}")
                results.append({"target": t, "error": str(e)})
            except Exception as e:
                _bk_log(f"❌ 예기치 않은 오류: {e}")
                results.append({"target": t, "error": str(e)})
        _BK_JOB["progress"] = 100.0
    finally:
        _BK_JOB["result"] = results
        _BK_JOB["running"] = False


@app.get("/api/raw_backup/status")
async def api_raw_backup_status():
    br = _bk_module()
    cfg = br.load_config()
    loop = asyncio.get_event_loop()
    targets = [await loop.run_in_executor(None, br.target_status, t, br.RAW_ROOT) for t in cfg.get("targets", [])]
    job = {k: _BK_JOB[k] for k in ("running", "kind", "progress", "current", "result", "started_at")}
    job["log"] = _BK_JOB["log"][-60:]
    return {"status": "ok", "targets": targets, "keep_versions": cfg.get("keep_versions", 3), "job": job}


@app.get("/api/research_datasets")
async def api_research_datasets():
    """원장 밖 연구 데이터셋(DataVault/staging/*) 현황 — 읽기 전용. 종목 원장·동기화와 무관."""
    from shared.data_library import research_datasets as rd
    loop = asyncio.get_event_loop()
    items = await loop.run_in_executor(None, rd.list_datasets)
    return {"status": "ok", "root": rd.staging_root(), "datasets": items}


@app.get("/api/research_datasets/{name}/symbols")
async def api_research_dataset_symbols(name: str):
    """연구 데이터셋의 종목별 수집 상태·품질 — 읽기 전용."""
    from shared.data_library import research_datasets as rd
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, rd.symbols, name)
    if rows is None:
        return {"status": "error", "message": f"연구 데이터셋 없음: {name}"}
    return {"status": "ok", "name": name, "symbols": rows}


@app.get("/api/research_datasets/{name}/steps")
async def api_research_dataset_steps(name: str):
    """연구 데이터셋의 수집 단계 목록·실행 중 작업·최근 로그 (kr_equity 만 실행 가능)."""
    from shared.data_library import research_datasets as rd
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, rd.steps, name)
    if info is None:
        return {"status": "error", "message": f"연구 데이터셋 없음: {name}"}
    return {"status": "ok", "name": name, **info}


@app.post("/api/research_datasets/{name}/collect")
async def api_research_dataset_collect(name: str, payload: dict):
    """수집 단계 하나를 백그라운드로 시작 — 한 번에 하나만. payload: {step, full?}"""
    from shared.data_library import research_datasets as rd
    step, full = str(payload.get("step") or ""), bool(payload.get("full"))
    try:
        info = await asyncio.get_event_loop().run_in_executor(None, lambda: rd.start_step(name, step, full))
    except RuntimeError as e:
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "job": info}


@app.post("/api/raw_backup/targets")
async def api_raw_backup_add_target(payload: dict):
    br = _bk_module()
    path = os.path.abspath(br.normalize_path(str(payload.get("path", ""))))
    if not str(payload.get("path", "")).strip():
        return {"status": "error", "message": "경로를 입력하세요."}
    try:
        br.check_target(path, br.RAW_ROOT, lambda m: None)
    except br.BackupError as e:
        return {"status": "error", "message": str(e)}
    cfg = br.load_config()
    if path not in cfg["targets"]:
        cfg["targets"].append(path)
        br.save_config(cfg)
    return {"status": "ok", "path": path}


@app.post("/api/raw_backup/targets/remove")
async def api_raw_backup_remove_target(payload: dict):
    br = _bk_module()
    cfg = br.load_config()
    path = str(payload.get("path", ""))
    cfg["targets"] = [t for t in cfg.get("targets", []) if t != path]
    br.save_config(cfg)
    return {"status": "ok"}   # 설정에서만 뺀다 — 드라이브의 백업 파일은 건드리지 않는다


@app.post("/api/raw_backup/run")
async def api_raw_backup_run(payload: dict):
    kind = "verify" if payload.get("kind") == "verify" else "backup"
    dry_run = bool(payload.get("dry_run"))
    with _BK_LOCK:
        if _BK_JOB["running"]:
            return {"status": "error", "message": "이미 백업/검증이 실행 중입니다."}
        br = _bk_module()
        if not br.load_config().get("targets"):
            return {"status": "error", "message": "등록된 백업 대상이 없습니다."}
        _BK_JOB.update(running=True, kind=("dry" if dry_run and kind == "backup" else kind), progress=0.0, current="", log=[], result=None, cancel=False,
                       started_at=datetime.now().isoformat(timespec="seconds"))
    _bk_threading.Thread(target=_bk_run_job, args=(kind, dry_run), daemon=True).start()
    return {"status": "ok"}


@app.post("/api/raw_backup/cancel")
async def api_raw_backup_cancel():
    _BK_JOB["cancel"] = True
    return {"status": "ok"}


@app.post("/api/data_vault/smart_normalize_all")
async def api_data_vault_smart_normalize_all():
    """2단계 Normalized Store 스마트 표준 정규화 (미존재 시 자동 빌드, 존재 시 증분 업서트)"""
    try:
        from CAiModelBaker.core.normalized_builder import smart_normalize_all_active
        results = smart_normalize_all_active()
        return {
            "status": "ok",
            "message": f"스마트 정규화 완료 (신규빌드: {results['built_count']}, 증분업서트: {results['upserted_count']}, 실패: {results['failed_count']})",
            "results": results
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/raw_store/sync_active")
async def api_raw_store_sync_active(payload: dict, background_tasks: BackgroundTasks):
    """대장에 등록된 활성 자산들만 핀포인트 순회 증분 수집 트리거"""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        active_maps = SourceMappingManager.get_active_mappings()
        if not active_maps:
            return {"status": "error", "message": "활성화된 수집 대상 자산이 없습니다."}

        background_tasks.add_task(run_active_feeds_sync_worker, active_maps, payload)
        return {
            "status": "ok",
            "message": f"총 {len(active_maps)}개 활성 자산에 대한 일괄 증분 동기화를 시작합니다.",
            "target_count": len(active_maps)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Feature Screener & Preset API
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/features/screener")
async def api_run_feature_screener(data: Optional[dict] = None):
    """전구간 데이터 기반 단변량 선행 지표 스크리닝 실행"""
    try:
        import asyncio
        from core.feature_screener import run_full_period_feature_screening
        
        cfg = load_config()
        if data:
            if "ic_threshold" in data:
                cfg["ic_threshold"] = float(data["ic_threshold"])
            if "max_lag_candles" in data:
                cfg["max_lag_candles"] = int(data["max_lag_candles"])
                
        lookback = (data or {}).get("lookback_period", "project")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_full_period_feature_screening(
                cfg,
                max_lag_candles=int((data or {}).get("max_lag_candles", 10)),
                ic_threshold=float((data or {}).get("ic_threshold", 0.015)),
                lookback_period=lookback
            )
        )
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/horizon/defaults")
async def api_get_horizon_defaults(interval: Optional[str] = None):
    """지정 또는 현재 인터벌의 표준 사정거리 후보군 및 기본 고정값 조회"""
    try:
        from CAiModelBaker.core.horizon_registry import get_horizon_context
        config = load_config()
        intv = interval or config.get("INTERVAL", "10m")
        ctx = get_horizon_context(intv)
        return {"status": "success", **ctx}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/horizon/lock")
async def api_lock_horizon(data: dict):
    """지정된 사정거리/보유시간으로 고정 잠금 모드 전환 및 저장"""
    try:
        predict_ahead = int(data.get("predict_ahead", 6))
        max_hold = int(data.get("max_hold", predict_ahead))
        config = load_config()
        current_thc = config.get("TARGET_HORIZON_CONFIG", {})
        current_thc["mode"] = "locked"
        current_thc["locked_predict_ahead"] = predict_ahead
        current_thc["locked_max_hold"] = max_hold
        config["TARGET_HORIZON_CONFIG"] = current_thc
        save_config(config)
        return {"status": "success", "message": f"사정거리 {predict_ahead}캔들, 최대보유 {max_hold}캔들로 고정되었습니다.", "config": current_thc}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/features/presets")
async def api_get_feature_presets():
    """지표 프리셋 목록 조회"""
    try:
        presets_file = os.path.join(_BAKER_ROOT, "config/presets.json")
        if not os.path.exists(presets_file):
            return {"status": "success", "presets": {}}
        with open(presets_file, "r", encoding="utf-8") as f:
            presets = json.load(f)
        return {"status": "success", "presets": presets}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/features/presets")
async def api_save_feature_preset(data: dict):
    """지표 프리셋 저장 (새 프리셋 추가 또는 업데이트)"""
    try:
        name = data.get("name")
        if not name or not name.strip():
            return {"status": "error", "message": "프리셋 이름을 입력해 주세요."}
            
        features = data.get("features", [])
        if not features:
            return {"status": "error", "message": "최소 1개 이상의 지표가 포함되어야 합니다."}
            
        presets_file = os.path.join(_BAKER_ROOT, "config/presets.json")
        presets = {}
        if os.path.exists(presets_file):
            with open(presets_file, "r", encoding="utf-8") as f:
                presets = json.load(f)
                
        presets[name.strip()] = {
            "name": name.strip(),
            "description": data.get("description", ""),
            "market": data.get("market", "ALL"),
            "features": features,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(presets_file, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=4, ensure_ascii=False)
            
        return {"status": "success", "message": f"프리셋 '{name}'이(가) 성공적으로 저장되었습니다."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.delete("/api/features/presets/{preset_name}")
async def api_delete_feature_preset(preset_name: str):
    """지표 프리셋 삭제"""
    try:
        presets_file = os.path.join(_BAKER_ROOT, "config/presets.json")
        if not os.path.exists(presets_file):
            return {"status": "error", "message": "프리셋 파일이 없습니다."}
            
        with open(presets_file, "r", encoding="utf-8") as f:
            presets = json.load(f)
            
        if preset_name not in presets:
            return {"status": "error", "message": f"프리셋 '{preset_name}'을(를) 찾을 수 없습니다."}
            
        del presets[preset_name]
        
        with open(presets_file, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=4, ensure_ascii=False)
            
        return {"status": "success", "message": f"프리셋 '{preset_name}'이(가) 삭제되었습니다."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/features/inventory")
async def api_get_features_inventory():
    """Gold Feature Store 인벤토리 및 사이드카 매니페스트 헬스 서머리 반환"""
    try:
        # 절대 경로로 임포트한다 — CTrade/core, CCommander/core 와 패키지명이 겹쳐서 다른 쪽 core 가
        # 먼저 sys.modules 에 올라가면 `from core.feature_composer` 가 ModuleNotFoundError 로 깨진다.
        from CAiModelBaker.core.feature_composer import FeatureComposer
        composer = FeatureComposer()
        inventory = composer.list_gold_inventory()
        return {
            "status": "ok",
            "total_count": len(inventory),
            "inventory": inventory
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/features/compose")
async def api_compose_features(payload: Dict[str, Any] = Body(...)):
    """온디맨드 시점 결합(Point-in-Time Join) 및 Gold Feature 데이터셋 빌드"""
    try:
        # 절대 경로로 임포트한다 — CTrade/core, CCommander/core 와 패키지명이 겹쳐서 다른 쪽 core 가
        # 먼저 sys.modules 에 올라가면 `from core.feature_composer` 가 ModuleNotFoundError 로 깨진다.
        from CAiModelBaker.core.feature_composer import FeatureComposer
        composer = FeatureComposer()
        
        target_symbol = payload.get("target_symbol") or get_target_symbol(load_config())
        target_interval = payload.get("target_interval", "10s")
        ref_symbols = payload.get("ref_symbols")
        if ref_symbols is None:
            try:
                from shared.data_library.symbol_relations_manager import SymbolRelationsManager
                rels_mgr = SymbolRelationsManager()
                rels = rels_mgr.get_relations(target_symbol)
                if rels and rels.get("references"):
                    ref_symbols = [r["symbol"] for r in rels["references"]]
            except Exception as ex:
                print(f"[api_compose_features] symbol_relations 로드 예외: {ex}")

        macro_symbols = payload.get("macro_symbols")
        mtf_intervals = payload.get("mtf_intervals")
        dataset_name = payload.get("dataset_name")
        use_cache = payload.get("use_cache", True)
        
        df = composer.compose(
            target_symbol=target_symbol,
            target_interval=target_interval,
            ref_symbols=ref_symbols,
            macro_symbols=macro_symbols,
            mtf_intervals=mtf_intervals,
            dataset_name=dataset_name,
            use_cache=use_cache,
            synthesize_cross_features=True,
        )
        
        return {
            "status": "ok",
            "symbol": target_symbol,
            "interval": target_interval,
            "total_rows": len(df),
            "total_cols": len(df.columns),
            "columns": list(df.columns),
            "start_date": str(df["timestamp"].min()),
            "end_date": str(df["timestamp"].max()),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/features/correlation_matrix")
async def api_get_feature_correlation_matrix(
    symbol: str = "069500",
    interval: str = "10s",
    max_features: int = 15,
):
    """주요 활성 수치 피처들 간의 다중공선성 진단용 상관계수 행렬 및 위험 쌍 반환"""
    try:
        import pandas as pd
        # 절대 경로로 임포트한다 — CTrade/core, CCommander/core 와 패키지명이 겹쳐서 다른 쪽 core 가
        # 먼저 sys.modules 에 올라가면 `from core.feature_composer` 가 ModuleNotFoundError 로 깨진다.
        from CAiModelBaker.core.feature_composer import FeatureComposer
        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        from shared.data_library.source_mapping_manager import SourceMappingManager
        composer = FeatureComposer()

        clean_sym = SourceMappingManager.clean_symbol(symbol)
        # 결합할 참조는 symbol_relations 의 결합 계획(join_plan) 전부 — 다른 결합 경로(브리지/Gold 빌더)와 같은 계획을 쓴다.
        # role=="leading_stock" 만 골라 쓰면 선물(10100)·CFD 프록시(US_TECH_100 등) 참조가 조용히 빠진다.
        plan = SymbolRelationsManager.join_plan(clean_sym)
        df = composer.compose(
            target_symbol=clean_sym,
            target_interval=interval,
            ref_symbols=plan["refs"] or None,
            macro_symbols=plan["series"] or None,
            synthesize_cross_features=True,
            use_cache=True,
        )
        
        num_cols = [c for c in df.columns if c != "timestamp" and pd.api.types.is_numeric_dtype(df[c])]
        
        priority_keywords = ["vwap", "ofi", "rv", "spread", "corr", "disparity", "close", "volume", "zscore", "basis"]
        selected_cols = [c for c in num_cols if any(k in c.lower() for k in priority_keywords)]
        if len(selected_cols) < max_features:
            remaining = [c for c in num_cols if c not in selected_cols]
            selected_cols.extend(remaining[:max_features - len(selected_cols)])
        else:
            selected_cols = selected_cols[:max_features]
            
        if not selected_cols:
            return {"status": "ok", "features": [], "matrix": [], "high_corr_pairs": []}
            
        corr_df = df[selected_cols].corr().fillna(0.0).round(3)
        matrix = corr_df.values.tolist()
        
        high_corr_pairs = []
        for i in range(len(selected_cols)):
            for j in range(i + 1, len(selected_cols)):
                r_val = float(corr_df.iloc[i, j])
                if abs(r_val) >= 0.85:
                    high_corr_pairs.append({
                        "feature_a": selected_cols[i],
                        "feature_b": selected_cols[j],
                        "correlation": r_val,
                        "warning": "다중공선성 위험 (동일 정보 중복 가능성)"
                    })
                    
        return {
            "status": "ok",
            "symbol": clean_sym,
            "interval": interval,
            "features": selected_cols,
            "matrix": matrix,
            "high_corr_pairs": high_corr_pairs
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/features/select")
async def api_select_features():
    """학습 전 LightGBM 기여도 기반 자동 지표 선정"""
    config = load_config()
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        log_fn = get_log_fn("FEATURE-TUNER")
        
        def local_log(msg):
            asyncio.run_coroutine_threadsafe(log_fn(msg), loop).result()
            
        from core.feature_tuner import run_auto_feature_selection
        selected_features = await loop.run_in_executor(
            None,
            lambda: run_auto_feature_selection(config, log_fn=local_log)
        )
        return {
            "status": "ok",
            "selected_features": selected_features or config.get('FEATURE_CONFIG', {}).get('enabled_features', [])
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/api/features/optimize")
async def api_optimize_features():
    """학습 전 지표 파라미터 사전 최적화"""
    config = load_config()
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        log_fn = get_log_fn("FEATURE-TUNER")
        
        def local_log(msg):
            asyncio.run_coroutine_threadsafe(log_fn(msg), loop).result()
            
        from core.feature_tuner import run_pre_tuning_features
        best_params = await loop.run_in_executor(
            None,
            lambda: run_pre_tuning_features(config, log_fn=local_log)
        )
        return {
            "status": "ok",
            "best_params": best_params
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/api/analyze/save")
async def api_save_analysis_report(data: dict):
    """지정 구간 백테스트 성과 분석 보고서 저장"""
    try:
        coin = data.get("coin") or get_target_symbol(load_config())
        interval = data.get("interval", "15m")
        test_start = data.get("test_start", "")
        test_end = data.get("test_end", "")
        comment = data.get("comment", "")
        metrics = data.get("metrics", {})
        params = data.get("params", {})
        trades = data.get("trades", [])

        # 세션 정보 읽어오기
        config = load_config()
        params_path = get_params_path(config)
        
        opt_session_id = "N/A"
        train_period = "N/A"
        opt_period = "N/A"
        
        if os.path.exists(params_path):
            try:
                with open(params_path, 'r', encoding='utf-8') as pf:
                    bp_data = json.load(pf)
                    opt_session_id = bp_data.get("opt_session_id", "N/A")
                    train_start = bp_data.get("train_start", "N/A")
                    train_end = bp_data.get("train_end", "N/A")
                    opt_start = bp_data.get("opt_start", "N/A")
                    opt_end = bp_data.get("opt_end", "N/A")
                    
                    if train_start != "N/A":
                        train_period = f"{train_start} ~ {train_end}"
                    if opt_start != "N/A":
                        opt_period = f"{opt_start} ~ {opt_end}"
            except Exception:
                pass

        # 파일명 생성용 타임스탬프
        from datetime import datetime
        now = datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        date_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 세션 정보 및 저장 디렉토리 결정
        session_id_req = data.get("session_id", "active")
        resolved_session_id = session_id_req if session_id_req != "active" else opt_session_id

        symbol_code = coin.split('-')[-1]
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        if resolved_session_id and resolved_session_id != "N/A" and resolved_session_id.strip() != "":
            report_dir = os.path.join(project_root, "deploy", f"{symbol_code}_{interval}", resolved_session_id, "reports")
        else:
            report_dir = os.path.join(project_root, "data", symbol_code)

        os.makedirs(report_dir, exist_ok=True)
        
        filename = f"analysis_report_{coin}_{interval}_{timestamp_str}.md"
        filepath = os.path.join(report_dir, filename)

        # 마크다운 보고서 본문 작성
        md = f"""# AI 백테스트 성과 분석 보고서

- **작성 일시**: {date_str}
- **대상 자산**: {coin} ({interval})
- **연계 최적화 세션**: `{opt_session_id}`
- **학습 구간 (Train)**: {train_period}
- **최적화 구간 (Opt)**: {opt_period}
- **백테스트 구간 (Test)**: {test_start} ~ {test_end}

---

## 💡 퀀트 분석 의견 (Comment)

{comment if comment.strip() else "*기재된 의견이 없습니다.*"}

---

## 📊 핵심 투자 성과 지표 (Performance Metrics)

| 지표명 | 수치 | 설명 |
| :--- | :--- | :--- |
| **누적 수익률** | {metrics.get('total_return', '-')} | 거래 수수료/슬리피지(왕복 0.22%) 감산 후 최종 결과 |
| **최대 낙폭 (MDD)** | {metrics.get('mdd', '-')} | 자산 곡선 기준 역사적 최고점 대비 최대 하락 비율 |
| **총 매매 횟수** | {metrics.get('trades_count', '-')} 회 | 분석 기간 내 전체 진입 및 청산 완료 횟수 |
| **예측 신호 승률** | {metrics.get('win_rate', '-')} | 총 거래 중 익절 및 시간만료 수익 거래 비율 |
| **단순 보유 수익률 (B&H)** | {metrics.get('bh_return', '-')} | 같은 기간 동안 매수 후 단순 보유 시의 결과 |

---

## ⚙️ 적용된 AI 최적 파라미터 (Best Parameters)

| 파라미터 | 값 | 설명 |
| :--- | :--- | :--- |
| **익절 장벽 (Take Profit)** | {f"{params.get('take_profit')} ATR" if params.get('take_profit') else '-'} | 도달 시 즉시 익절 청산되는 상단 ATR 배수 |
| **손절 장벽 (Stop Loss)** | {f"{params.get('stop_loss')} ATR" if params.get('stop_loss') else '-'} | 도달 시 즉시 손절 청산되는 하단 ATR 배수 |
| **예측 기간 (Predict Ahead)** | {params.get('predict_ahead_candles', '-')} 캔들 | AI 모델이 정답지를 매길 때 기준이 된 시차 |
| **최대 보유시간 (Max Hold)** | {params.get('max_hold_candles', params.get('predict_ahead_candles', '-'))} 캔들 | 반등 없이 횡보 시 포지션을 강제 청산하는 시간 |
| **진입 문턱 (Buy Threshold)** | {params.get('buy_threshold', '-')} | 진입 시 요구되는 최소 AI 예측 상승 확률 값 |
| **쿨다운 대기시간** | {params.get('cooldown_minutes', '-')} 분 | 거래 직후 재진입을 방지하기 위한 유예 시간 |

---

## 📋 상세 거래 기록 (Trade Logs)

"""
        if trades:
            md += "| No | 진입시각 | 청산시각 | 진입가 | 청산가 | 보유(분) | 예측값 | 구분 | 순수익률 |\n"
            md += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
            for idx, t in enumerate(trades):
                md += f"| {idx+1} | {t.get('entry_time', '')} | {t.get('exit_time', '')} | {t.get('entry_price', 0.0):,.0f} | {t.get('exit_price', 0.0):,.0f} | {t.get('hold_mins', 0)} 분 | {t.get('prob', 0.0)*100:.1f}% | {t.get('exit_type', '')} | {t.get('net_return', 0.0):+.2f}% |\n"
        else:
            md += "*해당 기간 내 매매 거래 기록이 존재하지 않습니다.*"

        # 파일 저장
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md)

        return {"status": "ok", "filepath": filepath}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/api/features/optuna")
async def api_get_optuna_config():
    """Optuna 설정 반환"""
    config = load_config()
    feature_config = load_feature_config()
    return {
        "optuna_config": feature_config.get("optuna_config", DEFAULT_OPTUNA_CONFIG),
        "defaults": DEFAULT_OPTUNA_CONFIG
    }

# ---------------------------------------------------------------------------
# 백그라운드 태스크 (비동기 작업)
# ---------------------------------------------------------------------------
@app.post("/api/task/download")
async def api_task_download(data: dict, background_tasks: BackgroundTasks):
    config = load_config()
    target = data.get("target", "upbit")
    mkt = config['SYMBOL_UPBIT']
    start_ym = data.get("start_ym", config.get('ARCHIVE_START_YM', '2025-01'))
    end_ym = data.get("end_ym", config.get('ARCHIVE_END_YM', '2026-05'))
    interval = data.get("interval", config.get('INTERVAL', '15m'))

    config['ARCHIVE_START_YM'] = start_ym
    config['ARCHIVE_END_YM'] = end_ym
    config['INTERVAL'] = interval
    save_config(config)

    async def task():
        loop = asyncio.get_event_loop()

        if target == "all":
            log_fn = get_log_fn("DOWNLOAD-ALL")
            await log_fn(f"🚀 전체 일괄 다운로드 시작 (업비트/국내주식 + 바이낸스 + CME 해외선물): {mkt} ({start_ym}~{end_ym}) [{interval}]")
            
            last_progress = [0]
            def progress_callback_upbit(pct):
                overall = int(pct * 0.4)
                if overall - last_progress[0] >= 1 or overall == 40:
                    last_progress[0] = overall
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast(json.dumps({
                            "type": "download_progress",
                            "task": "download_all",
                            "progress": overall,
                            "phase": "upbit"
                        })), loop
                    ).result()

            def progress_callback_binance(pct):
                overall = int(40 + pct * 0.4)
                if overall - last_progress[0] >= 1 or overall == 80:
                    last_progress[0] = overall
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast(json.dumps({
                            "type": "download_progress",
                            "task": "download_all",
                            "progress": overall,
                            "phase": "binance"
                        })), loop
                    ).result()

            def progress_callback_futures(pct):
                overall = int(80 + pct * 0.2)
                if overall - last_progress[0] >= 1 or overall == 100:
                    last_progress[0] = overall
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast(json.dumps({
                            "type": "download_progress",
                            "task": "download_all",
                            "progress": overall,
                            "phase": "futures"
                        })), loop
                    ).result()

            try:
                # 1. 업비트 / 국내주식 다운로드 실행
                await log_fn("📥 [1/3] 업비트 / 국내주식 다운로드 진행 중...")
                await loop.run_in_executor(
                    None,
                    lambda: run_archive_download_pipeline(
                        config, "upbit", mkt, start_ym, end_ym, interval,
                        local_log=lambda msg: asyncio.run_coroutine_threadsafe(
                            log_fn(msg), loop
                        ).result(),
                        progress_callback=progress_callback_upbit
                    )
                )
                await log_fn("✅ [1/3] 업비트 / 국내주식 다운로드 완료! 바이낸스 다운로드를 시작합니다.")
                
                # 2. 바이낸스 다운로드 실행
                await log_fn("📥 [2/3] 바이낸스 및 글로벌 가상자산 다운로드 진행 중...")
                await loop.run_in_executor(
                    None,
                    lambda: run_archive_download_pipeline(
                        config, "binance", mkt, start_ym, end_ym, interval,
                        local_log=lambda msg: asyncio.run_coroutine_threadsafe(
                            log_fn(msg), loop
                        ).result(),
                        progress_callback=progress_callback_binance
                    )
                )
                await log_fn("✅ [2/3] 바이낸스 다운로드 완료! CME 해외선물(NQ/ES) 다운로드를 시작합니다.")

                # 3. 미국 CME 해외선물(NQ, ES) 다운로드 실행
                await log_fn("📥 [3/3] 미국 CME 선물(NQ, ES) 1분봉/일봉/주봉 다운로드 진행 중...")
                await loop.run_in_executor(
                    None,
                    lambda: run_archive_download_pipeline(
                        config, "futures", mkt, start_ym, end_ym, interval,
                        local_log=lambda msg: asyncio.run_coroutine_threadsafe(
                            log_fn(msg), loop
                        ).result(),
                        progress_callback=progress_callback_futures
                    )
                )
                await log_fn("🎉 전체 다운로드(국내/업비트 + 바이낸스 + CME 해외선물) 완료!")
                await manager.broadcast(json.dumps({
                    "type": "task_done",
                    "task": "download_all",
                    "status": "success"
                }))
            except Exception as e:
                await log_fn(f"❌ 전체 다운로드 중 오류 발생: {e}")
                await manager.broadcast(json.dumps({
                    "type": "task_done",
                    "task": "download_all",
                    "status": "error",
                    "error": str(e)
                }))

        else:
            log_fn = get_log_fn(f"DOWNLOAD-{target.upper()}")
            await log_fn(f"Download start: {target} {mkt} ({start_ym}~{end_ym}) [{interval}]")

            last_progress = [0]  # mutable ref for closure

            def progress_callback(pct):
                # Throttle: broadcast every 1% change for smoother progress feedback
                if pct - last_progress[0] >= 1 or pct == 100:
                    last_progress[0] = pct
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast(json.dumps({
                            "type": "download_progress",
                            "task": f"download_{target}",
                            "progress": pct
                        })), loop
                    ).result()

            try:
                await loop.run_in_executor(
                    None,
                    lambda: run_archive_download_pipeline(
                        config, target, mkt, start_ym, end_ym, interval,
                        local_log=lambda msg: asyncio.run_coroutine_threadsafe(
                            log_fn(msg), loop
                        ).result(),
                        callback_on_finish=lambda: asyncio.run_coroutine_threadsafe(
                            manager.broadcast(json.dumps({"type": "task_done", "task": f"download_{target}", "status": "success"})), loop
                        ).result(),
                        progress_callback=progress_callback
                    )
                )
                await log_fn(f"Download completed: {target}")
            except Exception as e:
                await log_fn(f"❌ Download failed: {e}")
                await manager.broadcast(json.dumps({
                    "type": "task_done",
                    "task": f"download_{target}",
                    "status": "error",
                    "error": str(e)
                }))

    background_tasks.add_task(task)
    return {"status": "started", "task": f"download_{target}"}

@app.post("/api/data/validate")
async def api_data_validate(data: dict = None):
    """수집된 데이터 파일들의 일자별 무결성 및 커버리지 검증"""
    config = load_config()
    data = data or {}
    start_ym = data.get("start_ym", config.get('ARCHIVE_START_YM', '2025-01'))
    end_ym = data.get("end_ym", config.get('ARCHIVE_END_YM', '2026-05'))
    interval = data.get("interval", config.get('INTERVAL', '15m'))
    res = validate_downloaded_data(config, start_ym=start_ym, end_ym=end_ym, interval=interval)
    return res

@app.post("/api/task/build")
async def api_task_build(data: dict, background_tasks: BackgroundTasks):
    config = load_config()
    mkt = config['SYMBOL_UPBIT']
    start_ym = data.get("start_ym", config.get('ARCHIVE_START_YM', '2025-01'))
    end_ym = data.get("end_ym", config.get('ARCHIVE_END_YM', '2026-05'))
    interval = data.get("interval", config.get('INTERVAL', '15m'))

    async def task():
        log_fn = get_log_fn("BUILD")
        await log_fn(f"Build dataset start ({interval})...")

        loop = asyncio.get_event_loop()

        def status_update(*args, **kwargs):
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(json.dumps({"type": "status_update"})), loop
            ).result()

        def progress_cb(pct, phase_str=""):
            try:
                asyncio.run_coroutine_threadsafe(
                    manager.broadcast(json.dumps({
                        "type": "build_progress",
                        "progress": pct,
                        "phase": phase_str
                    })), loop
                ).result()
            except Exception:
                pass

        try:
            await loop.run_in_executor(
                None,
                lambda: run_dataset_build_pipeline(
                    config, mkt, start_ym, end_ym, interval,
                    local_log=lambda msg: asyncio.run_coroutine_threadsafe(
                        log_fn(msg), loop
                    ).result(),
                    status_update_fn=status_update,
                    callback_on_finish=lambda: asyncio.run_coroutine_threadsafe(
                        manager.broadcast(json.dumps({"type": "task_done", "task": "build", "status": "success"})), loop
                    ).result(),
                    progress_callback=progress_cb
                )
            )
            await log_fn("Dataset build completed!")
        except Exception as e:
            await log_fn(f"❌ Dataset build failed: {e}")
            await manager.broadcast(json.dumps({
                "type": "task_done",
                "task": "build",
                "status": "error",
                "error": str(e)
            }))

    background_tasks.add_task(task)
    return {"status": "started", "task": "build"}

@app.post("/api/task/optimize")
async def api_task_optimize(data: dict, background_tasks: BackgroundTasks):
    config = load_config()
    t_start = data.get("train_start", config.get('TRAIN_START', ''))
    t_end = data.get("train_end", config.get('TRAIN_END', ''))
    o_start = data.get("opt_start", config.get('OPT_START', ''))
    o_end = data.get("opt_end", config.get('OPT_END', ''))
    memo = data.get("memo", "")

    # 시계열 검증 통제 장벽 6개 구간 모두 저장 (TEST 포함)
    config['TRAIN_START'] = t_start
    config['TRAIN_END'] = t_end
    config['OPT_START'] = o_start
    config['OPT_END'] = o_end
    config['TEST_START'] = data.get("test_start", config.get('TEST_START', ''))
    config['TEST_END'] = data.get("test_end", config.get('TEST_END', ''))

    # WFA 설정 업데이트
    optuna_cfg = config.get('OPTUNA_CONFIG', {})
    wfa_enabled = data.get("wfa_enabled", optuna_cfg.get("wfa_enabled", False))
    wfa_train_months = data.get("wfa_train_months", optuna_cfg.get("wfa_train_months", 12))
    wfa_opt_months = data.get("wfa_opt_months", optuna_cfg.get("wfa_opt_months", 3))
    wfa_test_months = data.get("wfa_test_months", optuna_cfg.get("wfa_test_months", 3))
    wfa_start_date = data.get("wfa_start_date", optuna_cfg.get("wfa_start_date", ""))
    wfa_end_date = data.get("wfa_end_date", optuna_cfg.get("wfa_end_date", ""))

    optuna_cfg["wfa_enabled"] = wfa_enabled
    optuna_cfg["wfa_train_months"] = wfa_train_months
    optuna_cfg["wfa_opt_months"] = wfa_opt_months
    optuna_cfg["wfa_test_months"] = wfa_test_months
    optuna_cfg["wfa_start_date"] = wfa_start_date
    optuna_cfg["wfa_end_date"] = wfa_end_date
    config['OPTUNA_CONFIG'] = optuna_cfg
    
    save_config(config)

    # 전역 중단 플래그 초기화
    global abort_optimize
    abort_optimize = False

    async def task():
        global abort_optimize
        log_fn = get_log_fn("OPTIMIZE")
        if wfa_enabled:
            await log_fn(f"🔄 Rolling WFA Optimization start: Train({wfa_train_months}M), Opt({wfa_opt_months}M), Test({wfa_test_months}M)")
        else:
            await log_fn(f"3-zone optimization: Train({t_start}~{t_end}) Opt({o_start}~{o_end})")

        loop = asyncio.get_event_loop()

        def status_update(*args, **kwargs):
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(json.dumps({"type": "status_update"})), loop
            ).result()

        def check_abort():
            global abort_optimize
            return abort_optimize

        def progress_callback(pct, info):
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(json.dumps({
                    "type": "optimize_progress",
                    "progress": pct,
                    "info": info
                }, ensure_ascii=False)), loop
            ).result()

        try:
            await loop.run_in_executor(
                None,
                lambda: process_02_relay_optimization(
                    config, t_start, t_end, o_start, o_end,
                    log_fn=lambda msg: asyncio.run_coroutine_threadsafe(
                        log_fn(msg), loop
                    ).result(),
                    status_update_fn=status_update,
                    check_abort_fn=check_abort,
                    memo=memo,
                    progress_fn=progress_callback
                )
            )
            if abort_optimize:
                await log_fn("🛑 3-zone optimization was aborted by user!")
                await manager.broadcast(json.dumps({"type": "task_done", "task": "optimize", "status": "error", "error": "User Aborted"}, ensure_ascii=False))
            else:
                await log_fn("3-zone optimization completed!")
                await manager.broadcast(json.dumps({"type": "task_done", "task": "optimize", "status": "success"}, ensure_ascii=False))
        except Exception as e:
            if abort_optimize or "Aborted by user" in str(e):
                await log_fn("🛑 3-zone optimization was aborted by user!")
                await manager.broadcast(json.dumps({"type": "task_done", "task": "optimize", "status": "error", "error": "User Aborted"}, ensure_ascii=False))
            else:
                await log_fn(f"❌ 3-zone optimization failed: {e}")
                await manager.broadcast(json.dumps({
                    "type": "task_done",
                    "task": "optimize",
                    "status": "error",
                    "error": str(e)
                }, ensure_ascii=False))
        finally:
            abort_optimize = False

    background_tasks.add_task(task)
    return {"status": "started", "task": "optimize"}

@app.post("/api/task/optimize/stop")
async def api_task_optimize_stop():
    global abort_optimize
    abort_optimize = True
    return {"status": "ok", "message": "Abort signal sent"}

@app.post("/api/task/backtest")
async def api_task_backtest(data: dict, background_tasks: BackgroundTasks):
    config = load_config()
    test_start = data.get("test_start", config.get('TEST_START', ''))
    test_end = data.get("test_end", config.get('TEST_END', ''))
    session_id = data.get("session_id", "active")

    config['TEST_START'] = test_start
    config['TEST_END'] = test_end
    save_config(config)

    async def task():
        log_fn = get_log_fn("BACKTEST")
        await log_fn(f"Isolated backtest start (Session: {session_id}): {test_start} ~ {test_end}")

        loop = asyncio.get_event_loop()

        try:
            res = await loop.run_in_executor(
                None,
                lambda: process_03_pure_backtest(
                    config, test_start, test_end,
                    log_fn=lambda msg: asyncio.run_coroutine_threadsafe(
                        log_fn(msg), loop
                    ).result(),
                    session_id=session_id
                )
            )
            
            # 백테스트 결과가 정상 리턴되었다면 JSON 파일로 저장
            if res and isinstance(res, dict):
                coin_code = config['SYMBOL_UPBIT'].split('-')[-1]
                interval = config.get('INTERVAL', '1m')
                if session_id and session_id != "active":
                    res_dir = os.path.join(_BAKER_ROOT, "deploy", f"{coin_code}_{interval}", session_id, "model")
                else:
                    res_dir = os.path.join(_BAKER_ROOT, "data", coin_code)
                
                os.makedirs(res_dir, exist_ok=True)
                res_path = os.path.join(res_dir, "last_backtest_result.json")
                try:
                    with open(res_path, 'w', encoding='utf-8') as f:
                        json.dump(res, f, indent=4, ensure_ascii=False)
                except Exception as save_e:
                    await log_fn(f"⚠️ Failed to save last_backtest_result.json: {save_e}")

            await log_fn("Backtest completed!")
            await manager.broadcast(json.dumps({
                "type": "task_done", 
                "task": "backtest", 
                "status": "success",
                "result": res
            }, ensure_ascii=False))
        except Exception as e:
            await log_fn(f"❌ Backtest failed: {e}")
            await manager.broadcast(json.dumps({
                "type": "task_done",
                "task": "backtest",
                "status": "error",
                "error": str(e)
            }, ensure_ascii=False))

    background_tasks.add_task(task)
    return {"status": "started", "task": "backtest"}

# ---------------------------------------------------------------------------
# 로그 저장 API
# ---------------------------------------------------------------------------
_last_saved_log_hash: str = ""  # 마지막 저장된 로그 content의 해시 (중복 방지)

@app.post("/api/save_logs")
async def api_save_logs(data: dict):
    """로그를 quant_engine_log.txt 파일에 저장 (중복 방지)"""
    global _last_saved_log_hash
    content = data.get("content", "")
    tab = data.get("tab", "unknown")
    
    if not content:
        return {"status": "error", "error": "내용이 없습니다."}
    
    # 📌 [중복 방지] content 해시로 동일 로그 블록 재저장 방지
    import hashlib
    content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
    if content_hash == _last_saved_log_hash:
        return {"status": "ok", "message": "이미 저장된 로그입니다 (중복 스킵)."}
    _last_saved_log_hash = content_hash
    
    try:
        log_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "quant_engine_log.txt")
        try:
            from CAiModelBaker.core.log_manager import append_with_rotation
        except (ImportError, AttributeError):
            from core.log_manager import append_with_rotation
        written = append_with_rotation(log_file, content)
        return {"status": "ok", "message": f"로그가 저장되었습니다. ({written} bytes)"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

# ---------------------------------------------------------------------------
# 데이터 품질 진단 헬퍼 (Anomaly Detection)
# ---------------------------------------------------------------------------
def detect_anomaly_zones(df, feature_ids, min_zone_rows=5):
    """활성 지표들의 이상 구간을 자동 감지 - 데이터 유효성/버그 확인용"""
    zones = []
    for fid in feature_ids:
        if fid not in df.columns:
            continue
        series = df[fid].astype(float)
        timestamps = df['timestamp']

        # 1) 상수 구간 감지
        diff = series.diff().abs()
        const_mask = diff < 1e-12
        const_start = None
        for i in range(len(const_mask)):
            if const_mask.iloc[i]:
                if const_start is None:
                    const_start = i
            else:
                if const_start is not None and (i - const_start) >= min_zone_rows:
                    zones.append({
                        "feature": fid,
                        "start": str(timestamps.iloc[const_start])[:19],
                        "end": str(timestamps.iloc[i - 1])[:19],
                        "count": i - const_start,
                        "reason": "상수 구간",
                        "severity": "warning",
                        "detail": f"값 {series.iloc[const_start]:.4f}로 {i - const_start}행 고정 (fillna 더미 의심)"
                    })
                const_start = None
        if const_start is not None and (len(const_mask) - const_start) >= min_zone_rows:
            zones.append({
                "feature": fid,
                "start": str(timestamps.iloc[const_start])[:19],
                "end": str(timestamps.iloc[-1])[:19],
                "count": len(const_mask) - const_start,
                "reason": "상수 구간",
                "severity": "warning",
                "detail": f"값 {series.iloc[const_start]:.4f}로 {len(const_mask) - const_start}행 고정 (fillna 더미 의심)"
            })

        # 2) 0.0 백필 구간
        zero_mask = (series == 0.0)
        zero_start = None
        for i in range(len(zero_mask)):
            if zero_mask.iloc[i]:
                if zero_start is None:
                    zero_start = i
            else:
                if zero_start is not None and (i - zero_start) >= min_zone_rows:
                    zones.append({
                        "feature": fid,
                        "start": str(timestamps.iloc[zero_start])[:19],
                        "end": str(timestamps.iloc[i - 1])[:19],
                        "count": i - zero_start,
                        "reason": "0.0 백필 구간",
                        "severity": "critical",
                        "detail": f"데이터 수집 누락/백필 더미 (0.0 x {i - zero_start}행)"
                    })
                zero_start = None
        if zero_start is not None and (len(zero_mask) - zero_start) >= min_zone_rows:
            zones.append({
                "feature": fid,
                "start": str(timestamps.iloc[zero_start])[:19],
                "end": str(timestamps.iloc[-1])[:19],
                "count": len(zero_mask) - zero_start,
                "reason": "0.0 백필 구간",
                "severity": "critical",
                "detail": f"데이터 수집 누락/백필 더미 (0.0 x {len(zero_mask) - zero_start}행)"
            })

        # 3) 극단 스파이크 (z-score > 10)
        mean = series.mean()
        std = series.std()
        if std > 1e-12:
            zscore = ((series - mean) / std).abs()
            spike_idx = zscore[zscore > 10].index[:10]
            for idx in spike_idx:
                zones.append({
                    "feature": fid,
                    "start": str(timestamps.loc[idx])[:19],
                    "end": str(timestamps.loc[idx])[:19],
                    "count": 1,
                    "reason": "극단 스파이크",
                    "severity": "info",
                    "detail": f"z-score {zscore.loc[idx]:.1f}, 값 {series.loc[idx]:.4f}"
                })

    return zones


# ---------------------------------------------------------------------------
@app.get("/api/project/candidates")
async def api_project_candidates(session_id: Optional[str] = None):
    """특정 세션(프로젝트) 내에서 사용 가능한 분석 대상 모델 목록(대표 모델 + AutoLab 후보군) 반환"""
    cfg = load_config()
    target_session = session_id or cfg.get("ACTIVE_SESSION_ID", "default")
    if target_session == "active":
        target_session = cfg.get("ACTIVE_SESSION_ID", "default")
        
    session_dir = _find_session_dir(target_session)
    if not session_dir or not os.path.exists(session_dir):
        return {"session_id": target_session, "models": []}
        
    interval = cfg.get('INTERVAL', '15m')
    models = []
    
    # 1. 대표 모델 (model/)
    model_dir = os.path.join(session_dir, "model")
    if os.path.exists(model_dir):
        spec_path = os.path.join(model_dir, "feature_spec.json")
        params_files = [f for f in os.listdir(model_dir) if f.startswith("best_params_") and f.endswith(".json")]
        params_data = {}
        features_count = 0
        if params_files:
            try:
                with open(os.path.join(model_dir, params_files[0]), 'r', encoding='utf-8') as pf:
                    params_data = json.load(pf)
            except Exception:
                pass
        if os.path.exists(spec_path):
            try:
                with open(spec_path, 'r', encoding='utf-8') as sf:
                    spec_data = json.load(sf)
                    features_count = len(spec_data.get("features_order", []))
                    if "interval" in spec_data:
                        interval = spec_data["interval"]
            except Exception:
                pass
                
        # model/ 은 합격 챔피언 또는 사용자가 직접 학습한 모델 자리 — 모델 파일이 있을 때만, 출처와 함께 표시
        has_model_file = any(f.startswith("model_") and f.endswith(".txt") for f in os.listdir(model_dir))
        source_info = {}
        try:
            with open(os.path.join(model_dir, MODEL_SOURCE_FILE), 'r', encoding='utf-8') as msf:
                source_info = json.load(msf)
        except Exception:
            source_info = {}
        kind = source_info.get("kind")
        label = ("🏆 대표 모델 (자동연구 챔피언)" if kind == "autolab_champion"
                 else "🛠 대표 모델 (직접 학습)" if kind == "manual"
                 else "📦 대표 모델 (후보 승격)" if kind == "autolab_candidate"
                 else "❔ 대표 모델 (출처 기록 없음)")
        if has_model_file:
            models.append({
                "target": "model",
                "rank": 0,
                "label": label,
                "features_count": features_count,
                "best_params": params_data,
                "is_champion": kind == "autolab_champion",
                "source": source_info,
            })
        
    # 2. leaderboard.json 및 candidates/ 폴더
    leaderboard_file = os.path.join(_run_view_dir(session_dir), "leaderboard.json")
    lb_map = {}
    if os.path.exists(leaderboard_file):
        try:
            with open(leaderboard_file, 'r', encoding='utf-8') as lf:
                lb_data = json.load(lf)
                for item in lb_data.get("leaderboard", []):
                    lb_map[int(item.get("rank", 0))] = item
        except Exception:
            pass
            
    candidates_dir = os.path.join(_run_view_dir(session_dir), "candidates")
    if os.path.exists(candidates_dir):
        for cdir in sorted(os.listdir(candidates_dir)):
            cpath = os.path.join(candidates_dir, cdir)
            if not os.path.isdir(cpath) or not cdir.startswith("rank_"):
                continue
            try:
                rank_no = int(cdir.replace("rank_", ""))
            except ValueError:
                continue
                
            c_params_files = [f for f in os.listdir(cpath) if f.startswith("best_params_") and f.endswith(".json")]
            c_params = {}
            if c_params_files:
                try:
                    with open(os.path.join(cpath, c_params_files[0]), 'r', encoding='utf-8') as pf:
                        c_params = json.load(pf)
                except Exception:
                    pass
                    
            c_spec_path = os.path.join(cpath, "feature_spec.json")
            c_feat_cnt = 0
            if os.path.exists(c_spec_path):
                try:
                    with open(c_spec_path, 'r', encoding='utf-8') as sf:
                        c_spec = json.load(sf)
                        c_feat_cnt = len(c_spec.get("features_order", []))
                except Exception:
                    pass
                    
            lb_item = lb_map.get(rank_no, {})
            medal = "🥇 1위" if rank_no == 1 else ("🥈 2위" if rank_no == 2 else ("🥉 3위" if rank_no == 3 else f"#{rank_no}"))
            val_p = lb_item.get("val_profit", c_params.get("validation_metrics", {}).get("total_return", 0.0))
            final_p = lb_item.get("final_profit", None)
            
            label_text = f"{medal} 후보 (지표 {c_feat_cnt}개 | Val {val_p:+.1f}%"
            if final_p is not None:
                label_text += f" | Test {final_p:+.1f}%"
            label_text += ")"
            
            models.append({
                "target": f"rank_{rank_no}",
                "rank": rank_no,
                "label": label_text,
                "features_count": c_feat_cnt,
                "best_params": c_params,
                "leaderboard_info": lb_item
            })
            
    return {"session_id": target_session, "models": models}

# ---------------------------------------------------------------------------
# 차트 분석 API
# ---------------------------------------------------------------------------
@app.post("/api/chart/analysis")
async def api_chart_analysis(data: dict):
    """차트 데이터 + 매매 시그널 분석"""
    config = load_config()
    snapshot_path = get_snapshot_path(config)
    session_id = data.get("session_id", "active")
    target_model = data.get("target_model", "model") # "model", "rank_1", "rank_2" 등
    interval = config.get('INTERVAL', '15m')
    symbol_code = get_target_symbol(config).split('-')[-1]
    
    if session_id and session_id != "active":
        deploy_dir = _find_session_dir(session_id) or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", f"{symbol_code}_{interval}", session_id)
    else:
        cfg_sess = config.get("ACTIVE_SESSION_ID")
        deploy_dir = _find_session_dir(cfg_sess) if cfg_sess else None

    target_sub = (os.path.relpath(os.path.join(_candidates_root(deploy_dir), target_model), deploy_dir)
                  if (deploy_dir and target_model.startswith("rank_")) else "model")
    
    if deploy_dir and os.path.exists(os.path.join(deploy_dir, target_sub)):
        target_dir = os.path.join(deploy_dir, target_sub)
        params_files = [f for f in os.listdir(target_dir) if f.startswith("best_params_") and f.endswith(".json")]
        params_path = os.path.join(target_dir, params_files[0]) if params_files else os.path.join(target_dir, f"best_params_{interval}.json")
        model_files = [f for f in os.listdir(target_dir) if f.startswith("model_") and f.endswith(".txt")]
        model_path = os.path.join(target_dir, model_files[0]) if model_files else os.path.join(target_dir, f"model_{interval}.txt")
        spec_path = os.path.join(target_dir, "feature_spec.json")
    else:
        params_path = get_params_path(config)
        model_path = get_model_path(config)
        spec_path = None

    if not os.path.exists(snapshot_path):
        return {"error": "Dataset not found"}

    import pandas as pd
    import numpy as np

    df = pd.read_csv(snapshot_path)
    df = df.sort_values('timestamp').reset_index(drop=True)
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    test_start = data.get("test_start", "")
    test_end = data.get("test_end", "")

    # 구간 정보
    periods = {
        "train": {"start": config.get('TRAIN_START', ''), "end": config.get('TRAIN_END', '')},
        "opt": {"start": config.get('OPT_START', ''), "end": config.get('OPT_END', '')},
        "test": {"start": config.get('TEST_START', ''), "end": config.get('TEST_END', '')},
    }

    result = {
        "periods": periods,
        "total_rows": len(df),
        "trades": [],
        "max_profit_trade": None,
    }

    # 모델이 있으면 매매 시그널 분석 (피처 계산은 필터링 전 전체 데이터셋 대상)
    has_model = os.path.exists(params_path) and os.path.exists(model_path)
    if has_model:
        import lightgbm as lgb
        
        with open(params_path, 'r') as f:
            bp = json.load(f)
            
        # 실제 최적화에 적용되었던 날짜 정보가 존재하면 periods를 모델 기준 정보로 업데이트
        if "train_start" in bp:
            periods["train"]["start"] = bp["train_start"]
        if "train_end" in bp:
            periods["train"]["end"] = bp["train_end"]
        if "opt_start" in bp:
            periods["opt"]["start"] = bp["opt_start"]
        if "opt_end" in bp:
            periods["opt"]["end"] = bp["opt_end"]
            
        primary_model_path = model_path.replace(f"model_{interval}.txt", f"model_{interval}_primary.txt")
        meta_model_path = model_path.replace(f"model_{interval}.txt", f"model_{interval}_meta.txt")
        
        if os.path.exists(primary_model_path) and os.path.exists(meta_model_path):
            primary_model = lgb.Booster(model_file=primary_model_path)
            meta_model = lgb.Booster(model_file=meta_model_path)
            has_double_model = True
            features = primary_model.feature_name()
        else:
            primary_model = lgb.Booster(model_file=model_path)
            meta_model = None
            has_double_model = False
            features = primary_model.feature_name()

        # 피처 사양 로드
        feat_config = None
        if spec_path and os.path.exists(spec_path):
            try:
                with open(spec_path, 'r', encoding='utf-8') as sf:
                    spec_data = json.load(sf)
                    feat_config = {
                        "enabled_features": features,
                        "feature_params": spec_data.get("feature_params", {})
                    }
            except Exception as se:
                print(f"Error loading spec feature order: {se}")
        
        if not feat_config:
            from core.feature_registry import load_feature_config
            feature_config = load_feature_config()
            feat_config = {
                "enabled_features": features,
                "feature_params": feature_config.get("feature_params", {})
            }
        
        # 📌 [버그 수정] 피처 엔지니어링을 필터링 전에 먼저 전체 데이터셋에 수행하여 롤링 윈도우 유실 방지
        df_feat_all = run_feature_engineering(df.copy(), interval, feature_config=feat_config, config=config)
        
        # 피처 계산 완료 후 기간 필터링 적용
        if test_start:
            df_feat = df_feat_all[df_feat_all['timestamp'] >= pd.to_datetime(test_start)].copy()
        else:
            df_feat = df_feat_all.copy()
        if test_end:
            df_feat = df_feat[df_feat['timestamp'] < pd.to_datetime(test_end) + pd.Timedelta(days=1)].copy()
            
        df_feat = df_feat.reset_index(drop=True)

        if len(df_feat) == 0:
            return {"error": "No data in selected test period"}

        # 예측 수행
        try:
            if has_double_model:
                preds_primary = primary_model.predict(df_feat[features])
                preds_meta = meta_model.predict(df_feat[features])
                preds = preds_primary[:, 1] if preds_primary.ndim > 1 else preds_primary
            else:
                preds_raw = primary_model.predict(df_feat[features])
                preds = preds_raw[:, 1] if preds_raw.ndim > 1 else preds_raw
                preds_meta = np.ones(len(df_feat))
        except ValueError as ve:
            if "categorical_feature do not match" in str(ve):
                df_temp = df_feat[features].copy()
                for col in df_temp.columns:
                    if df_temp[col].dtype.name == 'category':
                        df_temp[col] = df_temp[col].astype(int)
                if has_double_model:
                    preds_primary = primary_model.predict(df_temp)
                    preds_meta = meta_model.predict(df_temp)
                    preds = preds_primary[:, 1] if preds_primary.ndim > 1 else preds_primary
                else:
                    preds_raw = primary_model.predict(df_temp)
                    preds = preds_raw[:, 1] if preds_raw.ndim > 1 else preds_raw
                    preds_meta = np.ones(len(df_feat))
            else:
                raise ve

        # 메타 필터용 1차(primary) 상승 클래스 판정 (H-3: primary==1 일 때만 메타 승인 적용)
        if has_double_model:
            _pp = np.asarray(preds_primary)
            primary_is_buy = (np.argmax(_pp, axis=1) == 1) if _pp.ndim > 1 else (_pp >= 0.5)
        else:
            primary_is_buy = None


        from shared.core.market_utils import detect_market_type, is_krx_entry_allowed, is_krx_force_exit
        from shared.core.ai_policy import META_APPROVAL_THRESHOLD
        from shared.core.tbm_executor import resolve_exit
        _market_type = detect_market_type(config)
        intraday_square_off = data.get('intraday_square_off', config.get('INTRADAY_SQUARE_OFF', (_market_type == 'KRX')))
        is_intraday_krx = (_market_type == 'KRX' and intraday_square_off)

        cost = round_trip_cost(config.get('FEE_UPBIT', 0.0005), config.get('SLIPPAGE', 0.0006), resolve_sell_tax(config))
        cooldown_minutes = bp.get('cooldown_minutes', config.get('COOLDOWN_MINUTES', 3))

        holding = False
        entry_price = 0.0
        entry_time_idx = 0
        cooldown_until = 0
        trade_logs = []

        interval_str = config.get('INTERVAL', '15m')
        int_val = 15
        if interval_str.endswith('m'):
            try: int_val = int(interval_str[:-1])
            except: pass
        elif interval_str.endswith('h'):
            try: int_val = int(interval_str[:-1]) * 60
            except: pass
        elif interval_str.endswith('d'):
            try: int_val = int(interval_str[:-1]) * 1440
            except: pass

        cooldown_candles = max(1, int(cooldown_minutes / int_val))
        close_col = 'target_close' if 'target_close' in df_feat.columns else 'upbit_close'
        atr_col = 'target_atr' if 'target_atr' in df_feat.columns else ('upbit_atr' if 'upbit_atr' in df_feat.columns else None)
        low_col = 'target_low' if 'target_low' in df_feat.columns else ('upbit_low' if 'upbit_low' in df_feat.columns else None)

        for idx in range(len(df_feat)):
            current_price = df_feat[close_col].iloc[idx]
            current_time = df_feat['timestamp'].iloc[idx]

            if holding:
                elapsed = idx - entry_time_idx
                entry_atr = df_feat[atr_col].iloc[entry_time_idx] if atr_col else entry_price * 0.015
                krx_force_exit = (is_intraday_krx and is_krx_force_exit(current_time))
                current_low = df_feat[low_col].iloc[idx] if (low_col and low_col in df_feat.columns) else current_price
                # [단일 청산 판정] shared.tbm_executor.resolve_exit — 비상손절 포함/라벨 통일 (H-4)
                _exit = resolve_exit(
                    entry_price=entry_price, entry_atr=entry_atr,
                    current_price=current_price, current_low=current_low,
                    take_profit=bp['take_profit'], stop_loss=bp['stop_loss'],
                    emergency_sl_mult=bp.get('emergency_sl_mult', bp['stop_loss'] * 2.0),
                    max_hold_candles=bp.get('max_hold_candles', bp['predict_ahead_candles']),
                    elapsed_candles=elapsed, cost=cost,
                    force_exit=krx_force_exit, is_candle_close=True,
                    interval_minutes=int_val,
                )
                if _exit:
                    holding = False
                    cooldown_until = idx + cooldown_candles
                    trade_logs.append({
                        'entry_time': str(df_feat['timestamp'].iloc[entry_time_idx]),
                        'exit_time': str(current_time),
                        'entry_price': float(entry_price),
                        'exit_price': float(_exit['exit_price']),
                        'hold_mins': int(elapsed * int_val),
                        'prob': float(preds[entry_time_idx]),
                        'net_return': round(_exit['net_return'], 2),
                        'exit_type': _exit['exit_type']
                    })
            else:
                if is_intraday_krx and not is_krx_entry_allowed(current_time):
                    continue
                is_macro_allowed = True
                use_macro = data.get('use_macro_filter', config.get('USE_MACRO_FILTER', True))
                if use_macro:
                    rsi_threshold = float(data.get('macro_rsi_threshold', config.get('MACRO_RSI_FILTER_THRESHOLD', 50.0)))
                    rsi_col = 'target_rsi_daily' if 'target_rsi_daily' in df_feat.columns else ('upbit_rsi_daily' if 'upbit_rsi_daily' in df_feat.columns else None)
                    if rsi_col and rsi_col in df_feat.columns:
                        current_rsi = df_feat[rsi_col].iloc[idx]
                        if current_rsi < rsi_threshold:
                            is_macro_allowed = False

                # 2차 메타 필터링 (H-3: 임계값 단일화 + primary==1 게이트)
                is_meta_allowed = True
                if has_double_model:
                    is_meta_allowed = (preds_meta[idx] >= META_APPROVAL_THRESHOLD) if (primary_is_buy is None or primary_is_buy[idx]) else True

                if (idx >= cooldown_until and preds[idx] >= bp['buy_threshold'] and is_macro_allowed and is_meta_allowed):
                    holding = True
                    entry_price = current_price
                    entry_time_idx = idx

        result["trades"] = trade_logs
        result["best_params"] = bp
        if trade_logs:
            result["max_profit_trade"] = max(trade_logs, key=lambda x: x['net_return'])

        # 매수/매도 시그널 포인트
        signal_points = []
        for t in trade_logs:
            signal_points.append({"type": "buy", "time": t['entry_time'], "price": t['entry_price']})
            signal_points.append({"type": "sell", "time": t['exit_time'], "price": t['exit_price'], "return": t['net_return']})
        result["signal_points"] = signal_points
        
        # 차트용 데이터베이스 대상 슬라이싱
        df_chart_base = df_feat
    else:
        # 모델이 없는 경우 단순 필터링 적용
        df_filtered = df
        if test_start:
            df_filtered = df_filtered[df_filtered['timestamp'] >= pd.to_datetime(test_start)]
        if test_end:
            df_filtered = df_filtered[df_filtered['timestamp'] < pd.to_datetime(test_end) + pd.Timedelta(days=1)]
        df_chart_base = df_filtered.reset_index(drop=True)

    # 바이낸스 원화 환산 데이터 추가 (롤링 환율 역산 적용)
    if 'binance_spot_close' in df_chart_base.columns and 'upbit_close' in df_chart_base.columns:
        approx_usdkrw = (df_chart_base['upbit_close'] / (df_chart_base['binance_spot_close'] + 1e-8)).rolling(1440, min_periods=1).mean()
        df_chart_base['binance_spot_open_krw'] = (df_chart_base['binance_spot_open'] * approx_usdkrw).round(2)
        df_chart_base['binance_spot_high_krw'] = (df_chart_base['binance_spot_high'] * approx_usdkrw).round(2)
        df_chart_base['binance_spot_low_krw'] = (df_chart_base['binance_spot_low'] * approx_usdkrw).round(2)
        df_chart_base['binance_spot_close_krw'] = (df_chart_base['binance_spot_close'] * approx_usdkrw).round(2)

    # 기본 차트 데이터 (다운샘플링)
    max_points = 10000
    if len(df_chart_base) > max_points:
        idx = np.linspace(0, len(df_chart_base)-1, max_points, dtype=int)
        df_chart = df_chart_base.iloc[idx].copy()
    else:
        df_chart = df_chart_base.copy()

    chart_data = {
        "timestamps": df_chart['timestamp'].dt.strftime('%Y-%m-%d %H:%M').tolist(),
        "open":  df_chart['upbit_open'].tolist()  if 'upbit_open'  in df_chart.columns else [],
        "high":  df_chart['upbit_high'].tolist()  if 'upbit_high'  in df_chart.columns else [],
        "low":   df_chart['upbit_low'].tolist()   if 'upbit_low'   in df_chart.columns else [],
        "close": df_chart['upbit_close'].tolist(),
    }

    # ── [신규] 활성 지표 시계열 + 메타데이터 + 이상 구간 ──
    # 모델이 있으면 feat_config에서 활성 지표를, 없으면 레지스트리 기본값 사용
    active_feature_ids = feat_config["enabled_features"] if has_model else list(DEFAULT_FEATURE_REGISTRY.keys())
    feature_params_all = feat_config.get("feature_params", {}) if has_model else {}

    # 다운샘플링된 df_chart 기준 지표 시계열 반환
    feature_series = {}
    for fid in active_feature_ids:
        if fid in DEFAULT_FEATURE_REGISTRY and fid in df_chart.columns:
            feature_series[fid] = df_chart[fid].tolist()

    chart_data["features"] = feature_series

    # ── [신규] 원본 데이터(RAW) 시계열 — 지표 계산의 기반이 되는 거래소 데이터 ──
    RAW_DATA_COLUMNS = [
        "upbit_open", "upbit_high", "upbit_low", "upbit_close",
        "binance_spot_open", "binance_spot_high", "binance_spot_low", "binance_spot_close",
        "binance_spot_open_krw", "binance_spot_high_krw", "binance_spot_low_krw", "binance_spot_close_krw",
        "upbit_volume", "binance_spot_volume", "binance_usdc_volume", "binance_futures_volume",
        "upbit_trade_price", "binance_spot_quote_volume",
        "binance_futures_oi", "binance_futures_oi_value",
        "upbit_trade_count", "upbit_large_trade_count", "upbit_avg_trade_size",
        "upbit_trade_bid_ratio", "binance_spot_taker_ratio", "binance_spot_trade_count",
        "upbit_atr",
    ]

    raw_series = {}
    for col in RAW_DATA_COLUMNS:
        if col in df_chart.columns:
            raw_series[col] = df_chart[col].tolist()

    chart_data["raw_data"] = raw_series

    # 지표 메타데이터 (이름/카테고리/설명)
    feature_meta = {}
    for fid in active_feature_ids:
        if fid in DEFAULT_FEATURE_REGISTRY:
            fdef = DEFAULT_FEATURE_REGISTRY[fid]
            feature_meta[fid] = {
                "id": fid,
                "name": fdef.get("name", fid),
                "name_en": fdef.get("name_en", ""),
                "category": fdef.get("category", ""),
                "category_name": fdef.get("category_name", ""),
                "description": fdef.get("description", ""),
                "current_params": feature_params_all.get(fid, {}),
                "has_params": fdef.get("has_params", False),
            }

    RAW_DATA_META = {
        "upbit_open": "업비트 시가", "upbit_high": "업비트 고가", "upbit_low": "업비트 저가", "upbit_close": "업비트 종가",
        "binance_spot_open": "바이낸스 시가 ($)", "binance_spot_high": "바이낸스 고가 ($)", "binance_spot_low": "바이낸스 저가 ($)", "binance_spot_close": "바이낸스 종가 ($)",
        "binance_spot_open_krw": "바이낸스 시가 (원화 환산)", "binance_spot_high_krw": "바이낸스 고가 (원화 환산)", "binance_spot_low_krw": "바이낸스 저가 (원화 환산)", "binance_spot_close_krw": "바이낸스 종가 (원화 환산)",
        "upbit_volume": "업비트 거래량(BTC)", "binance_spot_volume": "바이낸스 현물 거래량(BTC)",
        "binance_usdc_volume": "바이낸스 USDC 거래량", "binance_futures_volume": "바이낸스 선물 거래량",
        "upbit_trade_price": "업비트 거래 대금(원)", "binance_spot_quote_volume": "바이낸스 달러 대금",
        "binance_futures_oi": "바이낸스 선물 미결제약정(수량)", "binance_futures_oi_value": "바이낸스 미결제약정 가치($)",
        "upbit_trade_count": "업비트 체결 건수", "upbit_large_trade_count": "업비트 대량 체결 건수",
        "upbit_avg_trade_size": "업비트 평균 체결 크기(원)",
        "upbit_trade_bid_ratio": "업비트 매수 체결 비율", "binance_spot_taker_ratio": "바이낸스 Taker 매수 비율",
        "binance_spot_trade_count": "바이낸스 체결 건수", "upbit_atr": "업비트 ATR (TBM 장벽 기준)",
    }

    raw_meta = {}
    for col in RAW_DATA_COLUMNS:
        if col in df_chart.columns:
            raw_meta[col] = {
                "id": col,
                "name": RAW_DATA_META.get(col, col),
                "category": "raw",
                "category_name": "원본 데이터",
            }

    result["raw_meta"] = raw_meta

    result["chart"] = chart_data
    result["chart_rows"] = len(df_chart)
    result["feature_meta"] = feature_meta

    # ── [신규] 데이터 이상 구간 자동 감지 (전체 df_feat 기준 — 다운샘플링 전) ──
    try:
        result["anomaly_zones"] = detect_anomaly_zones(df_feat_all if has_model else df, active_feature_ids)
    except Exception as ae:
        print(f"[ChartAnalysis] Anomaly detection error: {ae}")
        result["anomaly_zones"] = []

    return result

# ---------------------------------------------------------------------------
# 🤖 자동화 전략 연구 루프 (ASRL v2) API
# ---------------------------------------------------------------------------
@app.post("/api/auto-research/start")
async def api_auto_research_start(data: dict, background_tasks: BackgroundTasks):
    """자동 연구 루프 시작 (백그라운드 실행)"""
    config = load_config()

    # 사용자가 설정한 Seed 값을 적용하여 동적 실험성 확보
    seed = int(data.get("seed", 42))
    optuna_cfg = config.get("OPTUNA_CONFIG", {})
    optuna_cfg["seed"] = seed
    config["OPTUNA_CONFIG"] = optuna_cfg
    
    # [AUTOLAB_CONFIG 저장] 사용자 입력값 영구 보존
    config["AUTOLAB_CONFIG"] = {
        "min_profit": float(data.get("min_profit", 0.0)),
        "min_trades": int(data.get("min_trades", 10)),
        "max_mdd": float(data.get("max_mdd", 10.0)),
        "max_trials": int(data.get("max_trials", 30)),
        "seed": seed,
        "holdout_start": data.get("holdout_start", ""),
        "holdout_end": data.get("holdout_end", ""),
        "ic_threshold": float(data.get("ic_threshold", 0.020)),
        "min_features": int(data.get("min_features", 4)),
        "max_features": int(data.get("max_features", 10)),
        "complexity_penalty": float(data.get("complexity_penalty", 0.15)),
        "final_test_ratio": int(data.get("final_test_ratio", 20)),
        "holdout_mode": str(data.get("holdout_mode", "full")),
        "final_test_td": int(data.get("final_test_td", 3)) if data.get("final_test_td") else None,
        "use_all_features": bool(data.get("use_all_features", False))
    }
    save_config(config)

    target_criteria = {
        "min_profit": float(data.get("min_profit", 0.0)),
        "min_trades": int(data.get("min_trades", 10)),
        "max_mdd": float(data.get("max_mdd", 10.0)),
    }
    max_trials = int(data.get("max_trials", 30))
    holdout_start = data.get("holdout_start", "")
    holdout_end = data.get("holdout_end", "")

    # [AutoLab Phase 0] 자동연구 시작 시 최신 DataVault Features Store 기준 Gold 스냅샷 자동 빌드/동기화
    try:
        _pre_snap = get_snapshot_path(config)
        try:
            from core.autolab_bridge import ensure_gold_snapshot
        except ImportError:
            from CAiModelBaker.core.autolab_bridge import ensure_gold_snapshot
        _pre_log = get_log_fn("AUTO_RESEARCH")
        _pre_loop = asyncio.get_event_loop()

        def _pre_sync_log(msg):
            try:
                asyncio.run_coroutine_threadsafe(_pre_log(msg), _pre_loop)
            except Exception:
                print(f"[AUTO_RESEARCH] {msg}")

        _sym = str(get_target_symbol(config))
        _itv = str(config.get("INTERVAL", "1m"))
        _pre_sync_log(f"🏗️ [AutoLab Phase 0] {_sym}({_itv}) DataVault Features Store 기준 Gold 스냅샷 점검...")
        _prep = await _pre_loop.run_in_executor(
            None, lambda: ensure_gold_snapshot(_sym, _itv, _pre_snap, _pre_sync_log, force_rebuild=False)
        )
        if not _prep.get("ready"):
            return {"status": "error", "message": _prep.get("message", "Gold 스냅샷 준비 실패")}
        if _prep.get("built"):
            _pre_sync_log("✅ [AutoLab Phase 0] 최신 Gold 스냅샷 자동 빌드 완료 (WFA 연구 파이프라인 진입)")
        else:
            _pre_sync_log("⚡ [AutoLab Phase 0] 최신 Gold 스냅샷 캐시 즉시 재사용 (0.00s ➔ WFA 연구 파이프라인 진입)")
    except Exception as _prep_err:
        return {"status": "error", "message": f"Gold 스냅샷 자동 빌드 중 예외: {_prep_err}"}

    # [가드] 홀드아웃 시작일이 실제 스냅샷 데이터 범위보다 이르면(= 학습용 데이터가
    # 남지 않으면) 즉시 명확한 에러로 반환한다. 스마트 분할 산출이 실패했는데도
    # (예: 스냅샷 미존재) 화면에 이전 종목/세션의 값이 그대로 남아 제출되는 경우를 방지.
    if holdout_start:
        try:
            import pandas as _pd
            _snap_path = get_snapshot_path(config)
            if os.path.exists(_snap_path):
                _snap_ts = _pd.read_csv(_snap_path, usecols=["timestamp"])["timestamp"]
                _snap_min = _pd.to_datetime(_snap_ts, errors="coerce").min()
                _ho_start_dt = _pd.to_datetime(holdout_start, errors="coerce")
                if _snap_min is not None and _ho_start_dt is not None and _ho_start_dt <= _snap_min:
                    return {
                        "status": "error",
                        "message": (
                            f"홀드아웃 시작일({holdout_start})이 스냅샷 데이터 시작일"
                            f"({_snap_min.strftime('%Y-%m-%d')})보다 빠르거나 같아 학습용 데이터가"
                            f" 남지 않습니다. '분봉 맞춤 스마트 분할'을 다시 눌러 최신 데이터"
                            f" 기준으로 재계산하거나 홀드아웃 구간을 조정해주세요."
                        ),
                    }
        except Exception:
            pass  # 가드 실패 시에도 기존 흐름을 막지 않음 (research_loop 자체 검증에 위임)

    # 탐색 설정(고급) — 잠금 상태면 프로 표준 기본값 사용
    ic_threshold = float(data.get("ic_threshold", 0.020))
    min_features = int(data.get("min_features", 4))
    max_features = int(data.get("max_features", 10))
    complexity_penalty = float(data.get("complexity_penalty", 0.15))
    final_test_ratio = int(data.get("final_test_ratio", 20))
    holdout_mode = str(data.get("holdout_mode", "full"))
    final_test_td = int(data["final_test_td"]) if data.get("final_test_td") else None
    use_all_features = bool(data.get("use_all_features", False))
    single_run = bool(data.get("single_run", False))

    log_fn = get_log_fn("AUTO_RESEARCH")
    loop = asyncio.get_event_loop()

    def sync_log(msg):
        try:
            asyncio.run_coroutine_threadsafe(log_fn(msg), loop)
        except Exception:
            print(f"[AUTO_RESEARCH] {msg}")

    def sync_broadcast(payload: str):
        try:
            asyncio.run_coroutine_threadsafe(manager.broadcast(payload), loop)
        except Exception:
            pass

    result = start_research_in_thread(
        config=config,
        target_criteria=target_criteria,
        max_trials=max_trials,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        ic_threshold=ic_threshold,
        min_features=min_features,
        max_features=max_features,
        complexity_penalty=complexity_penalty,
        final_test_ratio=final_test_ratio,
        use_all_features=use_all_features,
        single_run=single_run,
        holdout_mode=holdout_mode,
        final_test_td=final_test_td,
        log_fn=sync_log,
        broadcast_fn=sync_broadcast,
    )
    return result


# ---------------------------------------------------------------------------
# WFA 윈도우 구성 헬퍼 (model_engine.py와 동일한 로직)
# ---------------------------------------------------------------------------
def _build_wfa_windows(first_date, last_date, train_months, opt_months, test_months,
                       timestamps=None, market="KRX", interval_minutes=None):
    """[R-1] 거래일 기준 WFA 창 생성 — 단일 구현(shared.split_planner.build_wfa_windows)에 위임.

    종전에는 본 함수가 `model_engine` 과 **동일한 달력일 산술(x 30.44)** 을 중복 구현하여
      · 창 경계가 휴장일에 떨어지고 (실측 Opt 종료 2026-08-23 일요일)
      · 간트차트가 학습 엔진과 **다른 창**을 표시할 수 있었다.
    반환 형식은 종전과 동일하게 'YYYY-MM-DD' 문자열 dict 리스트 (하위 호환).
    """
    import pandas as pd
    from CAiModelBaker.core.split_planner import build_wfa_windows as _ssot
    from CAiModelBaker.core.split_standards import trading_days_per_month

    tdpm = trading_days_per_month(market)
    train_td = max(1, int(round(float(train_months) * tdpm)))
    opt_td = max(1, int(round(float(opt_months) * tdpm)))
    test_td = max(1, int(round(float(test_months) * tdpm)))

    _ts = None
    if timestamps is not None:
        _lst = list(timestamps)
        if _lst:
            _ts = pd.to_datetime(pd.Series(_lst), errors='coerce').dropna()
    if _ts is not None and not _ts.empty:
        _d0 = pd.to_datetime(str(first_date)[:10]).date()
        _d1 = pd.to_datetime(str(last_date)[:10]).date()
        _ts = _ts[(_ts.dt.date >= _d0) & (_ts.dt.date <= _d1)]
    else:
        # 데이터 미전달 시 시장 캘린더로 유효 거래일을 합성 (경계 왜곡 방지)
        from shared.core.market_calendar import get_trading_days
        _ts = pd.to_datetime(get_trading_days(str(first_date)[:10], str(last_date)[:10], market))

    wins = _ssot(_ts, train_td, opt_td, test_td, interval_minutes=interval_minutes)
    return [
        {k: pd.Timestamp(v).strftime('%Y-%m-%d') for k, v in w.items()}
        for w in wins
    ]


def _calc_wfa_shift_start(holdout_start_dt, snapshot_start_dt,
                          train_months, opt_months, test_months,
                          market="KRX", timestamps=None, interval_minutes=None):
    """[R-1] 홀드아웃 시작일을 불변으로 두고, WFA 창이 홀드아웃 직전에 끝나도록 시작점을 역산한다.

    **거래일 기준**으로 계산한다.
      종전: `wfa_end = holdout_start - 1 달력일` — 휴장일을 무시했고,
            창 크기도 `(t+o+te) x 30.44 달력일` 이었다.
    """
    import pandas as pd
    from shared.core.market_calendar import get_trading_days, prev_trading_day_strict
    from CAiModelBaker.core.split_standards import trading_days_per_month

    # WFA 종료 = 홀드아웃 시작의 **직전 거래일** (당일 제외 — 당일이 거래일이면 포함되어 누수가 된다)
    _prev = prev_trading_day_strict(holdout_start_dt, market)
    if _prev is None:
        return None, 0, False
    wfa_end = pd.to_datetime(_prev)
    if wfa_end <= snapshot_start_dt:
        return None, 0, False

    tdpm = trading_days_per_month(market)
    t_train = max(1, int(round(float(train_months) * tdpm)))
    t_opt = max(1, int(round(float(opt_months) * tdpm)))
    t_test = max(1, int(round(float(test_months) * tdpm)))
    one_win = t_train + t_opt + t_test

    _ts = None
    if timestamps is not None:
        _lst = list(timestamps)
        if _lst:
            _ts = pd.to_datetime(pd.Series(_lst), errors='coerce').dropna()
    if _ts is None or _ts.empty:
        _ts = pd.to_datetime(get_trading_days(
            pd.Timestamp(snapshot_start_dt).strftime('%Y-%m-%d'),
            wfa_end.strftime('%Y-%m-%d'), market))

    _ts = _ts[(_ts.dt.date >= pd.Timestamp(snapshot_start_dt).date())
              & (_ts.dt.date <= wfa_end.date())].sort_values()
    if _ts.empty:
        return None, 0, False

    day_first = [pd.Timestamp(v) for v in _ts.groupby(_ts.dt.normalize()).first().values]
    avail_td = len(day_first)
    if avail_td < one_win:
        return None, 0, False

    num_windows = int((avail_td - one_win) // t_test) + 1
    if num_windows <= 0:
        return None, 0, False

    start_idx = avail_td - one_win - (num_windows - 1) * t_test
    clamped_flag = False
    if start_idx < 0:
        start_idx = 0
        clamped_flag = True
    first_win_train_start = day_first[start_idx]

    wins = _build_wfa_windows(
        first_win_train_start, wfa_end, train_months, opt_months, test_months,
        timestamps=_ts, market=market, interval_minutes=interval_minutes
    )
    return first_win_train_start, len(wins), clamped_flag


@app.get("/api/asrl/timeline")
async def api_asrl_timeline(
    holdout_start: str = "",
    holdout_end: str = "",
    align_mode: str = "",
    train_months: float = 0.0,
    opt_months: float = 0.0,
    test_months: float = 0.0,
    wfa_start_date: str = "",
    wfa_end_date: str = "",
    unit: str = "",
    train_td: int = 0,
    opt_td: int = 0,
    test_td: int = 0,
    wfa_windows_count: int = 0,
    holdout_td: int = 0
):
    """WFA & 홀드아웃 구간 타임라인 계산 반환 (AutoLab 타임라인 위젯용)

    align_mode:
      - ""              : 기존 모드 (홀드아웃이 WFA를 절삭)
      - "holdout_first" : 홀드아웃 고정, WFA 시작점 역산 (홀드아웃 직전에 WFA 정확히 종료)
      - "wfa_first"     : WFA 고정, 홀드아웃 시작을 WFA 마지막 Test 다음날로 미룸
      - "auto"          : 자동 제안 — 홀드아웃(지정/80%) 불변 + WFA 시작점 = max(snapshot_start, 역산값)
    """
    import pandas as pd
    from shared.core.market_calendar import (
        next_trading_day_strict, prev_trading_day, prev_trading_day_strict,
    )
    from CAiModelBaker.core.split_standards import trading_days_per_month
    config = load_config()
    snapshot_path = get_snapshot_path(config)

    if not os.path.exists(snapshot_path):
        return {"windows": [], "holdout_start": holdout_start, "holdout_end": holdout_end,
                "clamped": False, "snapshot_start": "", "snapshot_end": "", "overlapped": False,
                "wfa_train_months": 12, "wfa_opt_months": 3, "wfa_test_months": 3,
                "alignable": False, "suggested_wfa_start": "", "suggested_holdout_start": "",
                "holdout_trimmed": False, "align_mode": align_mode}

    df = pd.read_csv(snapshot_path)
    df = df.sort_values('timestamp').reset_index(drop=True)
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    snapshot_start_dt = df['timestamp'].iloc[0]
    snapshot_end_dt = df['timestamp'].iloc[-1]
    snapshot_start = snapshot_start_dt.strftime('%Y-%m-%d')
    snapshot_end = snapshot_end_dt.strftime('%Y-%m-%d')

    # [R-1] 시장/봉수 — 거래일 기준 창 산정 및 경계 스냅에 사용
    _mkt_key = str(config.get('SYMBOL_UPBIT') or config.get('SYMBOL') or 'KRX')
    tdpm = trading_days_per_month(_mkt_key)

    optuna_cfg = config.get('OPTUNA_CONFIG', {})
    raw_train = train_months if train_months > 0 else optuna_cfg.get('wfa_train_months', 12)
    raw_opt = opt_months if opt_months > 0 else optuna_cfg.get('wfa_opt_months', 3)
    raw_test = test_months if test_months > 0 else optuna_cfg.get('wfa_test_months', 3)

    # ── 단위(Unit: td / bars / months) 기반 거래일 정규화 (스케일 왜곡 방지) ──
    # 사용자가 단위 토글(TD) 상태에서 10일을 입력했을 때 이를 10개월(210일)로 오인하는 것을 방지
    total_data_td = len(df['timestamp'].dt.date.unique())
    is_td_unit = (unit == "td") or (train_td > 0) or (
        # 스마트 감지: 단위 미지정이어도 raw_train이 정수 형태이고, 개월 산출 시 데이터 전체를 초과하지만 일수로는 충분한 경우
        unit == "" and raw_train >= 4 and (raw_train * tdpm > total_data_td) and (raw_train <= total_data_td)
    )

    if is_td_unit:
        actual_train_td = train_td if train_td > 0 else max(1, int(round(raw_train)))
        actual_opt_td = opt_td if opt_td > 0 else max(1, int(round(raw_opt)))
        actual_test_td = test_td if test_td > 0 else max(1, int(round(raw_test)))
        train_months = round(actual_train_td / tdpm, 3)
        opt_months = round(actual_opt_td / tdpm, 3)
        test_months = round(actual_test_td / tdpm, 3)
    elif unit == "bars":
        _intv = str(config.get('INTERVAL', '1m')).lower()
        cpd = 2340.0 if ('s' in _intv or '10s' in _intv) else (381.0 if '1m' in _intv else 38.0)
        actual_train_td = max(1, int(round(raw_train / cpd)))
        actual_opt_td = max(1, int(round(raw_opt / cpd)))
        actual_test_td = max(1, int(round(raw_test / cpd)))
        train_months = round(actual_train_td / tdpm, 3)
        opt_months = round(actual_opt_td / tdpm, 3)
        test_months = round(actual_test_td / tdpm, 3)
    else:
        train_months = float(raw_train)
        opt_months = float(raw_opt)
        test_months = float(raw_test)

    wfa_start_date_str = wfa_start_date if wfa_start_date else optuna_cfg.get('wfa_start_date', '')
    wfa_end_date_str = wfa_end_date if wfa_end_date else optuna_cfg.get('wfa_end_date', '')
    _interval_minutes = None
    try:
        from shared.core.market_utils import parse_interval_minutes
        _interval_minutes = float(parse_interval_minutes(config.get('INTERVAL', '1m')))
    except Exception:
        _interval_minutes = None

    first_date, last_date = snapshot_start_dt, snapshot_end_dt

    if wfa_start_date_str:
        try:
            user_start_date = pd.to_datetime(wfa_start_date_str)
            if first_date <= user_start_date <= last_date:
                first_date = user_start_date
        except Exception:
            pass

    if wfa_end_date_str:
        try:
            user_end_date = pd.to_datetime(wfa_end_date_str)
            if first_date <= user_end_date <= last_date:
                last_date = user_end_date
        except Exception:
            pass

    # 80% 자동 지점 계산 — [R-2] 거래일 기준 SSOT 위임
    #   종전: split_idx = int(len(df) * 0.8)  ← bar 인덱스 (결측일/비균일 데이터에서 왜곡)
    auto_holdout_start = ""
    auto_split_info = None
    try:
        from CAiModelBaker.core.split_planner import plan_split as _plan_split
        auto_split_info = _plan_split(
            df, str(config.get('INTERVAL', '1m')), market=_mkt_key, mode='full'
        )
        if auto_split_info.get('ok'):
            auto_holdout_start = auto_split_info['holdout_start']
    except Exception as _e:
        print(f"⚠️ [R-2] 자동 홀드아웃 산정 실패: {type(_e).__name__}: {_e}")

    if not auto_holdout_start and len(df) > 0:
        # 폴백 (SSOT 실패 시에만): bar 인덱스 80%
        split_idx = min(int(len(df) * 0.8), len(df) - 1)
        auto_holdout_start = df['timestamp'].iloc[split_idx].strftime('%Y-%m-%d')

    active_holdout_start = holdout_start if holdout_start else auto_holdout_start

    # ── [NEW] 파라미터 기반 선언형 역산 (Declarative Parameter-driven Snap) ──
    # Train/Opt/Test TD, Windows 횟수, 홀드아웃 TD를 기준으로 데이터 스캔 기반 무결 역산
    is_param_driven = (wfa_windows_count > 0) or (holdout_td > 0)
    if is_param_driven:
        # 1. 실제 데이터 스캔 기반 거래일 리스트 (데이터 없는 날/휴장일 자동 배제)
        day_first_map = df.groupby(df['timestamp'].dt.normalize())['timestamp'].first()
        dates_td = [pd.Timestamp(v) for v in day_first_map.values]
        n_td = len(dates_td)
        available_total_td = n_td

        # 2. 홀드아웃 거래일수 및 구간 산출
        h_td_eff = holdout_td if holdout_td > 0 else 14
        h_end_idx = n_td - 1
        h_start_idx = max(0, h_end_idx - h_td_eff + 1)
        holdout_start = dates_td[h_start_idx].strftime('%Y-%m-%d')
        holdout_end = dates_td[h_end_idx].strftime('%Y-%m-%d')

        # 3. Purge 갭 (WFA 종료일과 홀드아웃 시작일 사이)
        purge_candles = _resolve_purge_candles(config)
        purge_days = 0
        if _interval_minutes and _interval_minutes >= 1440:
            purge_days = purge_candles
        elif purge_candles > 0 and _interval_minutes:
            bars_per_day = 1440.0 / _interval_minutes if tdpm >= 30.0 else 381.0
            purge_days = int(round(purge_candles / max(1.0, bars_per_day)))

        wfa_end_idx = max(0, h_start_idx - 1 - purge_days)
        wfa_end = dates_td[wfa_end_idx].strftime('%Y-%m-%d')

        # 4. WFA 윈도우 횟수 및 시작점 역산
        k_win = max(1, wfa_windows_count) if wfa_windows_count > 0 else 5
        required_wfa_td = actual_train_td + actual_opt_td + (k_win * actual_test_td)
        needed_total_td = required_wfa_td + h_td_eff + purge_days
        data_exceeded = False

        start_idx = wfa_end_idx - required_wfa_td + 1
        if start_idx < 0:
            data_exceeded = True
            start_idx = 0

        wfa_start = dates_td[start_idx].strftime('%Y-%m-%d')
        first_date = dates_td[start_idx]
        last_date = dates_td[wfa_end_idx]

        sub_ts = df['timestamp'][(df['timestamp'] >= first_date) & (df['timestamp'] <= df['timestamp'].iloc[-1])]
        windows = _build_wfa_windows(
            first_date, last_date, train_months, opt_months, test_months,
            timestamps=sub_ts, market=_mkt_key, interval_minutes=_interval_minutes
        )

        return {
            "windows": windows,
            "wfa_start": wfa_start,
            "wfa_end": wfa_end,
            "holdout_start": holdout_start,
            "holdout_end": holdout_end,
            "snapshot_start": snapshot_start,
            "snapshot_end": snapshot_end,
            "windows_count": len(windows),
            "requested_windows": k_win,
            "data_exceeded": data_exceeded,
            "needed_total_td": needed_total_td,
            "available_total_td": available_total_td,
            "holdout_td": h_td_eff,
            "train_td": actual_train_td,
            "opt_td": actual_opt_td,
            "test_td": actual_test_td,
            "overlapped": False,
            "clamped": False,
            "alignable": True,
            "wfa_train_months": train_months,
            "wfa_opt_months": opt_months,
            "wfa_test_months": test_months,
        }

    alignable = True
    clamped = False
    clamped_date = ""
    suggested_wfa_start = ""
    suggested_holdout_start = ""
    holdout_trimmed = False

    if align_mode in ("holdout_first", "auto"):
        # ── [신규] 홀드아웃 고정 + WFA 시작점 역산 ──
        # 홀드아웃 시작일(지정/80%) 불변, WFA가 홀드아웃 직전에 정확히 끝나도록 시작점 역산
        _holdout_base = active_holdout_start
        try:
            _holdout_dt = pd.to_datetime(_holdout_base)
            _shift_start, _win_cnt, _clamped_flag = _calc_wfa_shift_start(
                _holdout_dt, snapshot_start_dt,
                train_months, opt_months, test_months,
                market=_mkt_key, timestamps=df['timestamp'], interval_minutes=_interval_minutes
            )
            if _win_cnt <= 0:
                alignable = False
                holdout_start = _holdout_base
            else:
                first_date = _shift_start
                suggested_wfa_start = _shift_start.strftime('%Y-%m-%d')
                clamped = _clamped_flag
                holdout_start = _holdout_base

                # 📌 [FIX #3] WFA 종료점(last_date)을 홀드아웃 시작 **직전 거래일**로 강제.
                #    [R-1] 종전 `holdout_start - 1 달력일` 은 휴장일을 무시했고,
                #          `prev_trading_day`(당일 포함)는 홀드아웃 첫날을 침범할 수 있었다.
                _prev_td = prev_trading_day_strict(_holdout_dt, _mkt_key)
                if _prev_td:
                    _aligned_wfa_end = pd.to_datetime(_prev_td)
                    last_date = min(last_date, _aligned_wfa_end)

                if _clamped_flag:
                    clamped_date = snapshot_start
        except Exception as _e:
            # [R-1] 예외를 조용히 삼키면 정렬 실패가 UI 에서 보이지 않는다 — 원인을 남긴다
            print(f"⚠️ [R-1] WFA 정렬 실패: {type(_e).__name__}: {_e}")
            alignable = False
            holdout_start = _holdout_base if _holdout_base else active_holdout_start
    elif align_mode == "wfa_first":
        # ── [기존 개선] WFA 고정 + 홀드아웃 시작을 WFA 마지막 Test 다음날로 미룸 ──
        # WFA를 자르지 않고 끝까지 만든다 (last_date 고정)
        pass
    else:
        # ── 기존 모드 (홀드아웃이 WFA를 절삭) ──
        if holdout_start:
            try:
                _holdout_dt = pd.to_datetime(holdout_start)
                if first_date < _holdout_dt < last_date:
                    # [R-1] 홀드아웃 시작 **직전 거래일**까지만 WFA 를 사용 (달력 -1분 방식은 휴장일을 무시)
                    _prev_td = prev_trading_day_strict(_holdout_dt, _mkt_key)
                    last_date = pd.to_datetime(_prev_td) if _prev_td else (_holdout_dt - pd.Timedelta(minutes=1))
                    clamped = True
                    clamped_date = last_date.strftime('%Y-%m-%d')
            except Exception:
                pass

    windows = _build_wfa_windows(first_date, last_date, train_months, opt_months, test_months,
                                 timestamps=df['timestamp'], market=_mkt_key,
                                 interval_minutes=_interval_minutes)

    if align_mode == "wfa_first" and len(windows) > 0:
        last_w_test_end = windows[-1]["test_end"]
        try:
            # [R-1] WFA 마지막 Test 다음 **거래일**로 홀드아웃 시작을 미룸 (당일 제외 strict)
            _next_td = next_trading_day_strict(last_w_test_end, _mkt_key)
            _wfa_end_dt = pd.to_datetime(_next_td) if _next_td else (pd.to_datetime(last_w_test_end) + pd.Timedelta(days=1))
            # 홀드아웃 시작을 WFA 마지막 Test 다음날로 미룸 (snapshot_end 초과 시 경고)
            suggested_holdout_start = _wfa_end_dt.strftime('%Y-%m-%d')
            if _wfa_end_dt > snapshot_end_dt:
                holdout_trimmed = True
                suggested_holdout_start = snapshot_end
            holdout_start = suggested_holdout_start
        except Exception:
            holdout_start = last_w_test_end
    else:
        if not holdout_start:
            holdout_start = active_holdout_start

    if not holdout_end:
        holdout_end = snapshot_end

    # 겹침(overlap) 판단
    overlapped = False
    if len(windows) > 0 and holdout_start:
        try:
            holdout_start_dt = pd.to_datetime(holdout_start)
            for w in windows:
                w_end_dt = pd.to_datetime(w["test_end"])
                if w_end_dt >= holdout_start_dt:
                    overlapped = True
                    break
        except Exception:
            pass

    return {
        "windows": windows,
        "holdout_start": holdout_start,
        "holdout_end": holdout_end,
        "clamped": clamped,
        "clamped_date": clamped_date,
        "snapshot_start": snapshot_start,
        "snapshot_end": snapshot_end,
        "overlapped": overlapped,
        "alignable": alignable,
        "suggested_wfa_start": suggested_wfa_start,
        "suggested_holdout_start": suggested_holdout_start,
        "holdout_trimmed": holdout_trimmed,
        "wfa_train_months": train_months,
        "wfa_opt_months": opt_months,
        "wfa_test_months": test_months,
        "align_mode": align_mode,
    }


@app.post("/api/auto-research/stop")
async def api_auto_research_stop():
    """자동 연구 루프 강제 중단"""
    return abort_research()


@app.get("/api/auto-research/status")
async def api_auto_research_status():
    """현재 자동 연구 진행 상황 조회"""
    return get_research_status()


def _find_session_dir(session_id: Optional[str] = None, coin_code: Optional[str] = None, interval: Optional[str] = None) -> Optional[str]:
    """프로젝트 폴더 찾기 — 정확한 경로만 (종전: 모든 인터벌을 이름으로 뒤져 첫 매치를 썼다).

    1) 이름이 없거나 열린 프로젝트 이름이면 → 열린 프로젝트 폴더
    2) 종목·인터벌이 주어지면 → <종목>_<인터벌>/<이름>
    3) 이름만 주어지면 → 모든 인터벌에서 **유일할 때만** (겹치면 None)
    """
    active_ref = get_active_project_ref(PROJECT_NAME)
    if active_ref and (not session_id or session_id == active_ref.split("/", 1)[1]) and not (coin_code and interval):
        return project_store.project_dir(active_ref)
    if not session_id:
        return None
    if coin_code and interval:
        return project_store.resolve_dir(session_id, market_dir=f"{coin_code}_{interval}")
    return project_store.resolve_dir(session_id)


def _resolve_purge_candles(cfg: dict) -> int:
    """AFML Purge bar 수 — 예측 지평(predict_ahead_candles) 최대값에서 도출."""
    try:
        oc = cfg.get("OPTUNA_CONFIG", {}) or {}
        rng = oc.get("predict_ahead_candles_range", {}) or {}
        return int(rng.get("user_max") or rng.get("max") or 32)
    except Exception:
        return 32


@app.get("/api/autolab/smart_split")
async def api_autolab_smart_split(
    mode: str = "smart",
    interval: Optional[str] = None,
    holdout_mode: str = "full",
    final_test_td: Optional[int] = None,
    allow_gapped: Optional[bool] = None,
):
    """봉수 맞춤 스마트 분할(WFA + 홀드아웃)을 **데이터 스캔 기반**으로 산출한다 (P-3/P-5).

    무거래일(Volume 0 / 허수 거래일)을 자동 제외하며, allow_gapped=False (엄격 연속 모드)인 경우
    대규모 결측(Gap >= 3영업일)이 발견될 때 최신 연속 유효 구간을 기준으로 경계를 고정한다.
    allow_gapped=True (기본값)인 경우 비정상 결측 Gap을 건너뛰고 전체 유효 거래일을 통합한다.

    Args:
        mode: 'smart' (봉수 맞춤 3-Tier) | 'full' (전체 수동 분할)
        interval: 인터벌 강제 지정 (미지정 시 config.INTERVAL)
        holdout_mode: 'full' (100% 단일 순수 OOS) | 'split' (고정 거래일 2단계 분할)
        final_test_td: holdout_mode='split' 일 때 Final Test 고정 거래일수
        allow_gapped: 결측 Gap 건너뛰기 허용 여부 (None 시 AUTOLAB_CONFIG 또는 True 기본값)
    """
    try:
        try:
            from core.data_manager import get_snapshot_path
        except ImportError:
            from CAiModelBaker.core.data_manager import get_snapshot_path

        from CAiModelBaker.core.split_planner import plan_split, summarize_split
        import pandas as pd

        cfg = load_config()
        snap_path = get_snapshot_path(cfg)
        if not os.path.exists(snap_path):
            return {"status": "error", "message": "빌드된 데이터셋 스냅샷이 없습니다. 먼저 데이터를 빌드하세요."}

        itv = str(interval or cfg.get("INTERVAL", "1m"))
        symbol = str(cfg.get("SYMBOL_UPBIT") or cfg.get("SYMBOL") or "KRX")
        purge_n = _resolve_purge_candles(cfg)

        if allow_gapped is None:
            allow_gapped = bool(cfg.get("AUTOLAB_CONFIG", {}).get("allow_gapped_trading_days", True))
        req_contiguous = not allow_gapped

        # timestamp 및 volume 컬럼을 탐색하여 로드 (대용량 스냅샷 대비 초고속 2-Column 읽기)
        cols_header = pd.read_csv(snap_path, nrows=1).columns.tolist()
        use_cols = ["timestamp"]
        for c in ["target_volume", "upbit_volume", "volume", "vol"]:
            if c in cols_header:
                use_cols.append(c)
                break
        df = pd.read_csv(snap_path, usecols=use_cols)
        plan = plan_split(
            df, itv, market=symbol, mode=mode, purge_candles=purge_n,
            holdout_mode=holdout_mode, final_test_td=final_test_td, require_contiguous=req_contiguous
        )

        if not plan.get("ok"):
            return {
                "status": "ok",
                "ok": False,
                "interval": itv,
                "symbol": symbol,
                "mode": plan.get("mode"),
                "reason": plan.get("reason"),
                "summary": summarize_split(plan),
                "plan": plan,
            }

        return {
            "status": "ok",
            "ok": True,
            "interval": itv,
            "symbol": symbol,
            "purge_candles": purge_n,
            "summary": summarize_split(plan),
            "plan": plan,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/autolab/build_gold_snapshot")
async def api_autolab_build_gold_snapshot(interval: Optional[str] = None):
    """DataVault Features Store 기준으로 AutoLab Gold 스냅샷(data_snapshot_*.csv)을 재빌드한다.

    레거시 `run_dataset_build_pipeline`(다중 거래소 원시 수집 병합) 대신,
    `FeatureComposer.compose()`로 Normalized Store + symbol_relations.json에
    도킹된 대장주/매크로를 온디맨드 결합하여 `dpaths.gold_snapshot_path()`에 쓴다.
    이후 smart_split/model_engine/backtester 등은 코드 변경 없이 이 최신 스냅샷을 읽는다.
    """
    try:
        try:
            from core.autolab_bridge import build_gold_snapshot
        except ImportError:
            from CAiModelBaker.core.autolab_bridge import build_gold_snapshot

        cfg = load_config()
        itv = str(interval or cfg.get("INTERVAL", "1m"))
        symbol = str(get_target_symbol(cfg))

        result = build_gold_snapshot(symbol, itv)
        return {"status": "ok", "symbol": symbol, "interval": itv, **result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/market/trading_days")
async def api_market_trading_days(market: str = "KRX",
                                  start: Optional[str] = None,
                                  end: Optional[str] = None):
    """시장 캘린더 기반 유효 거래일 목록/개수 조회 (P-1/P-5).

    크립토는 연중무휴이므로 주말 포함 전 일수를 반환한다.
    """
    try:
        from shared.core.market_calendar import (
            count_trading_days, get_market_holidays, get_trading_days, is_continuous_market,
        )

        if not start or not end:
            return {"status": "error", "message": "start / end (YYYY-MM-DD) 가 필요합니다."}

        days = get_trading_days(start, end, market)
        holidays = sorted(
            h for h in get_market_holidays(market) if str(start) <= h <= str(end)
        )
        return {
            "status": "ok",
            "market": market,
            "continuous": is_continuous_market(market),
            "start": start,
            "end": end,
            "trading_days_count": count_trading_days(start, end, market),
            "trading_days": days,
            "holidays": holidays,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/market/split_point")
async def api_market_split_point(start: Optional[str] = None, end: Optional[str] = None,
                                 ratio: float = 0.8, market: str = "KRX"):
    """[R-2] **거래일 기준** 분할 지점 조회 — 앞 구간이 `ratio` 가 되는 지점.

    종전 UI 는 `start + (end − start) × 0.8` 의 **달력 밀리초** 산술을 사용했고,
    백엔드 타임라인은 `int(len(df) × 0.8)` 의 **bar 인덱스** 를 사용했다.
    두 방식 모두 거래일 밀도가 균일하지 않으면 경계가 왜곡된다.
    """
    try:
        import pandas as pd
        from shared.core.market_calendar import get_trading_days
        from CAiModelBaker.core.split_planner import split_point_by_ratio

        ts = None
        # 1) 빌드된 스냅샷(실제 bar) 우선 — 데이터 우선 원칙
        try:
            _snap = get_snapshot_path(load_config())
            if _snap and os.path.exists(_snap):
                _ts = pd.to_datetime(pd.read_csv(_snap, usecols=["timestamp"])["timestamp"], errors="coerce").dropna()
                if not _ts.empty:
                    ts = _ts
        except Exception:
            ts = None

        # 2) 스냅샷이 없으면 시장 캘린더로 합성
        if ts is None:
            if not start or not end:
                return {"status": "error", "message": "스냅샷이 없어 start/end (YYYY-MM-DD) 가 필요합니다."}
            ts = pd.to_datetime(get_trading_days(start, end, market))

        # 3) 범위 필터
        if start and end:
            _d0 = pd.Timestamp(str(start)[:10]).date()
            _d1 = pd.Timestamp(str(end)[:10]).date()
            ts = ts[(ts.dt.date >= _d0) & (ts.dt.date <= _d1)]

        sp = split_point_by_ratio(ts, ratio)
        if sp is None:
            return {"status": "error", "message": "분할 지점을 산출할 수 없습니다 (거래일 부족)."}

        return {
            "status": "ok",
            "market": market,
            "ratio": float(ratio),
            "split_point": pd.Timestamp(sp).strftime("%Y-%m-%d"),
            "trading_days": int(pd.Series(ts).dt.date.nunique()),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/autolab/leaderboard")
async def api_autolab_leaderboard(session_id: Optional[str] = None):
    """특정 세션 또는 현재 세션의 Top-3 리더보드 조회 (스마트 경로 매칭)"""
    try:
        cfg = load_config()
        target_session = session_id or cfg.get("ACTIVE_SESSION_ID", "default")
        
        session_dir = _find_session_dir(target_session)
        
        if session_dir and os.path.exists(session_dir):
            leaderboard_file = os.path.join(_run_view_dir(session_dir), "leaderboard.json")
            if os.path.exists(leaderboard_file):
                with open(leaderboard_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                    # 각 후보별 model_fingerprint 주입
                    for item in data.get("leaderboard", []):
                        rank_num = item.get("rank")
                        c_fp = "N/A"
                        cand_dir = os.path.join(_run_view_dir(session_dir), "candidates", f"rank_{rank_num}")
                        if not os.path.exists(cand_dir):
                            cand_dir = os.path.join(session_dir, "model")
                        if os.path.exists(cand_dir):
                            m_files = [f for f in os.listdir(cand_dir) if f.startswith("model_") and f.endswith(".txt")]
                            if m_files:
                                try:
                                    sha256 = hashlib.sha256()
                                    with open(os.path.join(cand_dir, m_files[0]), "rb") as mf:
                                        for chunk in iter(lambda: mf.read(65536), b""):
                                            sha256.update(chunk)
                                    c_fp = sha256.hexdigest()[:8]
                                except Exception:
                                    pass
                        item["model_fingerprint"] = c_fp
                        
                    return {"status": "success", "data": data}
        
        # 메모리 상태에서 조회 시도 (요청한 세션 ID와 일치할 때만 허용하여 세션 격리 누수 원천 차단)
        curr_status = get_research_status()
        if curr_status.get("session_id") == target_session:
            lb = curr_status.get("leaderboard", [])
            if lb:
                return {
                    "status": "success",
                    "data": {
                        "session_id": target_session,
                        "symbol": get_target_symbol(cfg),
                        "interval": cfg.get("INTERVAL", "30m"),
                        "leaderboard": lb
                    }
                }
        return {"status": "success", "data": {"session_id": target_session, "leaderboard": []}}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _extract_wfa_details_from_log(run_dir: str, trial_num: int) -> list:
    """train_run.log에서 특정 Trial의 WFA 윈도우별 성적을 정규식으로 안전하게 추출"""
    log_file = os.path.join(run_dir, "train_run.log")
    if not os.path.exists(log_file):
        return []
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        splits = re.split(r"🔬\s*\[Trial\s+(\d+)\/\d+\]", content)
        for i in range(1, len(splits), 2):
            if int(splits[i]) == trial_num:
                body = splits[i+1]
                matches = re.findall(r"\[Window\s+(\d+)\]\s+OOS\(Test\)\s+성과:\s*([+-]?\d+(?:\.\d+)?)\s*%\s*\/\s*(\d+)\s*건", body)
                if matches:
                    details = []
                    seen_w = set()
                    for m in matches:
                        w_no = int(m[0])
                        if w_no in seen_w:
                            break
                        seen_w.add(w_no)
                        p_val = round(float(m[1]), 2)
                        t_cnt = int(m[2])
                        details.append({
                            "window": w_no,
                            "profit": p_val,
                            "trades": t_cnt,
                            "passed": bool(p_val > 0)
                        })
                    return details
    except Exception:
        pass
    return []


@app.get("/api/autolab/trial_detail")
async def api_autolab_trial_detail(trial: int, session_id: Optional[str] = None, market_dir: Optional[str] = None):
    """특정 Trial의 상세 분석 데이터(매매로그, 피처스냅샷, 최적 파라미터, 지표 중요도 등) 온디맨드 조회"""
    try:
        cfg = load_config()
        target_session = session_id or cfg.get("ACTIVE_SESSION_ID", "default")
        
        session_dir = _find_session_dir(target_session)
        if session_dir and os.path.exists(session_dir):
            run_dir = _run_view_dir(session_dir, need="any")
            # 1. trials/trial_00X.json 전용 상세 파일 우선 조회
            trials_dir = os.path.join(run_dir, "trials")
            candidate_files = [
                os.path.join(trials_dir, f"trial_{trial:03d}.json"),
                os.path.join(trials_dir, f"trial_{trial}.json")
            ]
            for c_file in candidate_files:
                if os.path.exists(c_file):
                    with open(c_file, "r", encoding="utf-8") as f:
                        t_data = json.load(f)
                        # feature_importance가 비어있으면 모델 파일에서 실시간 추출 보강
                        if not t_data.get("feature_importance"):
                            m_dir = os.path.join(session_dir, "model")
                            if os.path.exists(m_dir):
                                for fname in os.listdir(m_dir):
                                    if fname.startswith("model_") and fname.endswith(".txt"):
                                        fi = extract_feature_importance_from_model_file(os.path.join(m_dir, fname))
                                        if fi:
                                            t_data["feature_importance"] = fi
                                            break
                        # WFA 창별 세부 성적 보강
                        if not t_data.get("wfa_window_details") and not (t_data.get("best_params", {}).get("training_meta", {}).get("wfa_window_details")):
                            parsed_wfa = _extract_wfa_details_from_log(run_dir, trial)
                            if parsed_wfa:
                                t_data["wfa_window_details"] = parsed_wfa
                        return {"status": "success", "data": t_data}

            # 2. leaderboard.json 파일 내 trial_history 또는 leaderboard fallback
            lb_file = os.path.join(_run_view_dir(session_dir), "leaderboard.json")
            if os.path.exists(lb_file):
                with open(lb_file, "r", encoding="utf-8") as f:
                    lb_data = json.load(f)
                    # trial_history 탐색
                    for th in lb_data.get("trial_history", []):
                        if th.get("trial") == trial:
                            if not th.get("wfa_window_details"):
                                th["wfa_window_details"] = _extract_wfa_details_from_log(run_dir, trial)
                            return {"status": "success", "data": th}
                    # leaderboard 상위 후보 탐색
                    for cand in lb_data.get("leaderboard", []):
                        if cand.get("trial") == trial or cand.get("rank") == trial:
                            mapped_data = {
                                "trial": trial,
                                "features": cand.get("features", []),
                                "passed": cand.get("passed", False),
                                "net_profit": cand.get("val_profit", cand.get("final_profit", 0.0)),
                                "monthly_profit": cand.get("val_monthly_profit", cand.get("final_monthly_profit", 0.0)),
                                "mdd": cand.get("val_mdd", cand.get("final_mdd", 0.0)),
                                "trades": cand.get("val_trades", cand.get("final_trades", 0)),
                                "win_rate": cand.get("win_rate", 0.0),
                                "profit_factor": cand.get("profit_factor", 0.0),
                                "sharpe_ratio": cand.get("sharpe_ratio", 0.0),
                                "avg_hold_mins": cand.get("avg_hold_mins", 0.0),
                                "best_params": cand.get("best_params", {}),
                                "feature_importance": cand.get("feature_importance", {}),
                                "trade_logs": cand.get("trades_detail", [])
                            }
                            return {"status": "success", "data": mapped_data}

        # 3. 메모리 상태 fallback
        curr_status = get_research_status()
        for th in curr_status.get("trial_history", []):
            if th.get("trial") == trial:
                return {"status": "success", "data": th}

        return {"status": "error", "message": f"Trial #{trial}의 상세 데이터를 찾을 수 없습니다."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/autolab/reliability")
async def api_autolab_reliability(data: dict):
    """리더보드 후보의 신뢰도 진단 — 무작위 진입(순환 이동) 대비·선택 편향 보정·거래 t값·WFA 일관성/집중도·독립 OOS.

    같은 홀드아웃·같은 실행 엔진으로 재시뮬레이션한다 (CAiModelBaker/core/reliability_check.py).
    """
    try:
        rank = int(data.get("rank", 1))
        cfg = load_config()
        target_session = data.get("session_id") or cfg.get("ACTIVE_SESSION_ID", "default")
        session_dir = _find_session_dir(target_session)
        if not session_dir or not os.path.exists(session_dir):
            return {"status": "error", "message": f"프로젝트 폴더를 찾을 수 없습니다: {target_session}"}
        run_dir = _run_view_dir(session_dir)
        if not os.path.exists(os.path.join(run_dir, "leaderboard.json")):
            return {"status": "error", "message": "완료된 자동연구 결과(리더보드)가 없습니다"}
        ref = project_store.ref_of_dir(session_dir)
        proj_cfg = project_store.read_project_config(ref) if ref else cfg
        from CAiModelBaker.core.reliability_check import evaluate
        result = await asyncio.to_thread(evaluate, proj_cfg, run_dir, rank)
        return {"status": "success", "data": result}
    except Exception as e:
        logging.exception("신뢰도 진단 실패")
        return {"status": "error", "message": str(e)}


@app.get("/api/autolab/candidate_detail")
async def api_autolab_candidate_detail(rank: int, session_id: Optional[str] = None):
    """특정 Rank 후보 모델의 상세 분석 데이터(매매로그, 피처스냅샷, 최적 파라미터, 지표 중요도 등) 온디맨드 조회"""
    try:
        cfg = load_config()
        target_session = session_id or cfg.get("ACTIVE_SESSION_ID", "default")
        session_dir = _find_session_dir(target_session)
        if not session_dir or not os.path.exists(session_dir):
            return {"status": "error", "message": f"세션 디렉토리를 찾을 수 없습니다: {target_session}"}

        cand_dir = os.path.join(_run_view_dir(session_dir), "candidates", f"rank_{rank}")
        detail_file = os.path.join(cand_dir, "candidate_detail.json")

        # 1. rank_{rank}/candidate_detail.json 우선 확인
        base_data = {}
        if os.path.exists(detail_file):
            try:
                with open(detail_file, "r", encoding="utf-8") as f:
                    base_data = json.load(f)
            except Exception:
                pass

        # 2. leaderboard.json에서 해당 rank 정보 확인 및 병합
        lb_file = os.path.join(_run_view_dir(session_dir), "leaderboard.json")
        lb_cand = {}
        if os.path.exists(lb_file):
            try:
                with open(lb_file, "r", encoding="utf-8") as lf:
                    lb_data = json.load(lf)
                    for c in lb_data.get("leaderboard", []):
                        if c.get("rank") == rank:
                            lb_cand = c
                            break
            except Exception:
                pass

        merged_data = {**lb_cand, **base_data}

        # 3. best_params 로드 보강
        best_params = merged_data.get("best_params") or {}
        if not best_params:
            if os.path.exists(cand_dir):
                for fname in os.listdir(cand_dir):
                    if fname.startswith("best_params_") and fname.endswith(".json"):
                        try:
                            with open(os.path.join(cand_dir, fname), "r", encoding="utf-8") as bf:
                                best_params = json.load(bf)
                                break
                        except Exception:
                            pass
            if not best_params:
                m_dir = os.path.join(session_dir, "model")
                if os.path.exists(m_dir):
                    for fname in os.listdir(m_dir):
                        if fname.startswith("best_params_") and fname.endswith(".json"):
                            try:
                                with open(os.path.join(m_dir, fname), "r", encoding="utf-8") as bf:
                                    best_params = json.load(bf)
                                    break
                            except Exception:
                                pass

        # 4. feature_importance 로드 및 실시간 계산 보강
        feature_importance = merged_data.get("feature_importance") or {}
        if not feature_importance:
            if os.path.exists(cand_dir):
                for fname in os.listdir(cand_dir):
                    if fname.startswith("model_") and fname.endswith(".txt"):
                        feature_importance = extract_feature_importance_from_model_file(os.path.join(cand_dir, fname))
                        if feature_importance:
                            break
            if not feature_importance:
                m_dir = os.path.join(session_dir, "model")
                if os.path.exists(m_dir):
                    for fname in os.listdir(m_dir):
                        if fname.startswith("model_") and fname.endswith(".txt"):
                            feature_importance = extract_feature_importance_from_model_file(os.path.join(m_dir, fname))
                            if feature_importance:
                                break

        merged_data["rank"] = rank
        merged_data["best_params"] = best_params
        merged_data["feature_importance"] = feature_importance
        merged_data["trade_logs"] = merged_data.get("trades_detail", []) or merged_data.get("trade_logs", [])

        return {"status": "success", "data": merged_data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/deploy/candidate")
async def api_deploy_candidate(data: dict):
    """AutoLab 후보군(Rank 1, 2, 3) 중 선택한 모델을 실전 활성 모델(model/)로 즉시 배포 (스마트 경로 매칭)"""
    try:
        cfg = load_config()
        coin_code = data.get("coin_code")
        interval = data.get("interval")
        session_id = data.get("session_id") or cfg.get("ACTIVE_SESSION_ID", "default")
        rank = int(data.get("rank", 1))

        session_dir = _find_session_dir(session_id, coin_code, interval)
        if not session_dir or not os.path.exists(session_dir):
            return {"status": "error", "message": f"세션 디렉토리를 찾을 수 없습니다: {session_id}"}

        candidate_dir = os.path.join(_candidates_root(session_dir), f"rank_{rank}")
        model_dir = os.path.join(session_dir, "model")

        if not os.path.exists(candidate_dir):
            return {"status": "error", "message": f"후보 모델 디렉토리가 존재하지 않습니다: {candidate_dir}"}

        # model/ 은 합격 모델 자리다 — 탈락 후보는 사유를 보고 명시적으로 force 한 경우만 올린다
        interval_of_session = project_store.interval_of_market_dir(os.path.basename(os.path.dirname(session_dir)))
        gate_ok, gate_reason = _deploy_gate(session_dir, f"rank_{rank}", interval_of_session)
        if not gate_ok and not data.get("force"):
            return {"status": "blocked", "message": gate_reason, "error": gate_reason, "can_force": True}

        # 1. 파일 복사 (candidate -> model/) — 이전 모델 잔재가 섞이지 않게 model/ 을 비우고 채운다
        if os.path.isdir(model_dir):
            shutil.rmtree(model_dir)
        os.makedirs(model_dir, exist_ok=True)
        copied_files = []
        for fname in os.listdir(candidate_dir):
            src = os.path.join(candidate_dir, fname)
            dst = os.path.join(model_dir, fname)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                copied_files.append(fname)
        with open(os.path.join(model_dir, MODEL_SOURCE_FILE), "w", encoding="utf-8") as mf:
            json.dump({"kind": "autolab_candidate", "rank": rank, "status": "PASSED" if gate_ok else "FORCED",
                       "source": os.path.relpath(candidate_dir, session_dir),
                       "promoted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, mf, indent=2, ensure_ascii=False)

        # 2. feature_spec.json에서 피처 목록 읽어서 feature_config 동기화
        spec_path = os.path.join(candidate_dir, "feature_spec.json")
        applied_features = []
        applied_params = {}
        if os.path.exists(spec_path):
            with open(spec_path, "r", encoding="utf-8") as sf:
                spec_data = json.load(sf)
                applied_features = spec_data.get("features_order") or spec_data.get("feature_names", [])
                applied_params = spec_data.get("feature_params", {})

        if applied_features:
            fc = load_feature_config()
            fc["enabled_features"] = applied_features
            fc["active_features"] = applied_features
            if applied_params:
                fc["feature_params"] = applied_params
            save_feature_config(
                enabled_features=fc["enabled_features"],
                feature_params=fc.get("feature_params", {}),
                optuna_config=fc.get("optuna_config"),
                active_features=fc["active_features"],
                feature_penalties=fc.get("feature_penalties"),
            )
            if "FEATURE_CONFIG" not in cfg:
                cfg["FEATURE_CONFIG"] = {}
            cfg["FEATURE_CONFIG"]["enabled_features"] = applied_features
            cfg["FEATURE_CONFIG"]["active_features"] = applied_features
            cfg["FEATURE_CONFIG"]["feature_params"] = fc.get("feature_params", {})
            cfg["ACTIVE_SESSION_ID"] = session_id
            save_config(cfg)

        # 3. 텔레그램 배포 알림 발송
        market_label = os.path.basename(os.path.dirname(session_dir))
        _send_telegram_notification_sync(
            f"🚀 <b>[AutoLab 수동 배포] Rank {rank} 후보 모델 실전 배치 완료</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"• 세션: <code>{session_id}</code> ({market_label})\n"
            f"• 선택 순위: <b>Rank {rank}</b>\n"
            f"• 적용 피처 ({len(applied_features)}개): {applied_features}\n"
            f"💡 실전 엔진이 선택한 후보 모델로 즉시 갱신되었습니다."
        )

        return {
            "status": "success",
            "message": f"Rank {rank} 후보 모델이 성공적으로 실전 배포(model/)되었습니다.",
            "session_id": session_id,
            "rank": rank,
            "applied_features": applied_features,
            "copied_files": copied_files
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/autolab/restore_candidate_config")
async def api_restore_candidate_config(data: dict):
    """AutoLab 후보군(Rank 1, 2, 3)의 피처 세팅을 현재 프로젝트(FEATURE_CONFIG)로 복원 (모델 파일은 유지)"""
    try:
        cfg = load_config()
        coin_code = data.get("coin_code")
        interval = data.get("interval")
        session_id = data.get("session_id") or cfg.get("ACTIVE_SESSION_ID", "default")
        rank = int(data.get("rank", 1))

        session_dir = _find_session_dir(session_id, coin_code, interval)
        if not session_dir or not os.path.exists(session_dir):
            return {"status": "error", "message": f"세션 디렉토리를 찾을 수 없습니다: {session_id}"}

        candidate_dir = os.path.join(_run_view_dir(session_dir), "candidates", f"rank_{rank}")
        applied_features = []
        applied_params = {}

        # 1. candidate_dir의 feature_spec.json 확인
        spec_path = os.path.join(candidate_dir, "feature_spec.json")
        if os.path.exists(spec_path):
            with open(spec_path, "r", encoding="utf-8") as sf:
                spec_data = json.load(sf)
                applied_features = spec_data.get("features_order") or spec_data.get("feature_names", [])
                applied_params = spec_data.get("feature_params", {})

        # 2. 없으면 leaderboard.json에서 rank 매칭
        if not applied_features:
            lb_path = os.path.join(_run_view_dir(session_dir), "leaderboard.json")
            if os.path.exists(lb_path):
                with open(lb_path, "r", encoding="utf-8") as lf:
                    lb_data = json.load(lf)
                    for item in lb_data.get("leaderboard", []):
                        if item.get("rank") == rank:
                            applied_features = item.get("features", [])
                            break

        if not applied_features:
            return {"status": "error", "message": f"Rank {rank} 후보의 피처 정보를 찾을 수 없습니다."}

        # 3. FEATURE_CONFIG 및 active_config 동기화 저장
        fc = load_feature_config()
        fc["enabled_features"] = applied_features
        fc["active_features"] = applied_features
        if applied_params:
            fc["feature_params"] = applied_params
        save_feature_config(
            enabled_features=fc["enabled_features"],
            feature_params=fc.get("feature_params", {}),
            optuna_config=fc.get("optuna_config"),
            active_features=fc["active_features"],
            feature_penalties=fc.get("feature_penalties"),
        )
        if "FEATURE_CONFIG" not in cfg:
            cfg["FEATURE_CONFIG"] = {}
        cfg["FEATURE_CONFIG"]["enabled_features"] = applied_features
        cfg["FEATURE_CONFIG"]["active_features"] = applied_features
        cfg["FEATURE_CONFIG"]["feature_params"] = fc.get("feature_params", {})
        cfg["ACTIVE_SESSION_ID"] = session_id
        save_config(cfg)

        return {
            "status": "success",
            "message": f"Rank {rank} 후보의 지표 설정({len(applied_features)}개)이 프로젝트에 복원되었습니다.",
            "session_id": session_id,
            "rank": rank,
            "applied_features": applied_features
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# 메인 실행
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    reload_flag = os.environ.get("BAKER_RELOAD", "false").lower() in ("true", "1")
    uvicorn.run(
        "CAiModelBaker.web_dashboard.main:app",
        host=os.environ.get("BAKER_HOST", "0.0.0.0"),
        port=8000,
        reload=reload_flag,
        reload_dirs=[str(BASE_DIR)] if reload_flag else None,
        log_level="info",
    )
