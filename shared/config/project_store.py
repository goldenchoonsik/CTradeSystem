"""
프로젝트 저장소 (Project Store) — CAiModelBaker 프로젝트 격리의 단일 진입점
==========================================================================

종전 결함 (2026-09-28 실측)
--------------------------
- 작업 설정은 전역 ``CAiModelBaker/config/active.json`` 한 벌이었고, 프로젝트 폴더는 그 사본을 받아 두는
  보관함이었다. 새 프로젝트는 직전 프로젝트 설정을 통째로 물려받았다.
- 프로젝트를 **이름만으로** ``deploy/*/<이름>`` 을 뒤져 첫 매치를 썼다 (6곳). 못 찾으면 IC 스크리닝은
  ``data/`` 아래 아무 종목의 스냅샷을 집었다.
- 재실행 산출물이 프로젝트 루트에 덮어써져, 탈락(REJECTED)한 실행 뒤에도 ``model/`` 에 마지막 재학습
  후보가 남았고 다른 실행의 ``candidate_detail.json`` 이 섞였다.

규칙
----
1. 프로젝트 식별자(ref)는 ``"{종목코드}_{인터벌}/{이름}"`` 전체 경로다. 이름은 모든 인터벌을 통틀어 유일하다.
2. 프로젝트 설정 원본은 ``<프로젝트>/config_snapshot.json`` 이다. 전역에는 "지금 열린 프로젝트"
   포인터(``active_project.json``)만 둔다.
3. 종목·인터벌·이름(신원 키)은 폴더 경로와 1:1 이라 생성 후 바뀌지 않는다.
4. 자동연구 실행 1회 = ``runs/<시각>/`` 폴더 1개. 최근 ``RUNS_KEEP`` 개만 보관한다.
   ``model/`` 에는 합격 모델(또는 사용자가 직접 학습한 모델)만 들어간다.
"""

import json
import os
import re
import shutil
from datetime import datetime
from typing import List, Optional

from shared.data_library import paths as dpaths

PROJECT_CONFIG_FILE = "config_snapshot.json"
ACTIVE_POINTER_FILE = "active_project.json"
RUNS_DIRNAME = "runs"
RUN_WORK_DIRNAME = "work"
RUNS_KEEP = 10

# 설정 dict 에 실행 중에만 싣는 키 (저장 금지). 값은 이번 실행 폴더 절대경로.
RUN_DIR_KEY = "_RUN_DIR"

# 프로젝트 신원 키 — 폴더 경로(<종목>_<인터벌>/<이름>)와 1:1
IDENTITY_KEYS = ("SYMBOL_UPBIT", "SYMBOL_BINANCE", "INTERVAL", "ACTIVE_SESSION_ID")

_SAFE_SEG = re.compile(r"^[A-Za-z0-9_\-]+$")


class ProjectError(Exception):
    pass


class ProjectNotFoundError(ProjectError):
    pass


class ProjectExistsError(ProjectError):
    pass


class AmbiguousProjectError(ProjectError):
    pass


class NoOpenProjectError(ProjectError):
    """열린 프로젝트가 없는데 설정을 저장하려 했다. 전역 설정 파일로 대신 쓰지 않는다."""


class BrokenProjectPointerError(ProjectError):
    """열린 프로젝트 포인터가 깨졌다(읽기 실패·빈 ref·폴더 없음). '프로젝트 없음' 으로 조용히 넘어가지 않는다."""


# =============================================================================
# 식별자
# =============================================================================

def _deploy_root() -> str:
    # 모듈 속성을 호출 시점에 읽는다 (테스트/환경변수 재정의 반영)
    return dpaths.DEPLOY_ROOT


def is_safe_segment(seg) -> bool:
    return isinstance(seg, str) and bool(_SAFE_SEG.match(seg)) and seg not in (".", "..")


def symbol_code(symbol: str) -> str:
    """KRW-BTC → BTC, 069500 → 069500 (폴더명에 쓰는 코드)"""
    return str(symbol or "").split("-")[-1]


def market_dir_for(symbol: str, interval: str) -> str:
    return f"{symbol_code(symbol)}_{interval}"


def make_ref(market_dir: str, name: str) -> str:
    if not (is_safe_segment(market_dir) and is_safe_segment(name)):
        raise ProjectError(f"프로젝트 경로가 올바르지 않습니다: {market_dir}/{name}")
    return f"{market_dir}/{name}"


def split_ref(ref: str):
    parts = str(ref or "").strip().strip("/").split("/")
    if len(parts) != 2 or not all(is_safe_segment(p) for p in parts):
        raise ProjectError(f"프로젝트 식별자가 올바르지 않습니다: {ref!r} (형식: <종목>_<인터벌>/<이름>)")
    return parts[0], parts[1]


def interval_of_market_dir(market_dir: str) -> str:
    return market_dir.rsplit("_", 1)[-1] if "_" in market_dir else ""


def project_dir(ref: str) -> str:
    market_dir, name = split_ref(ref)
    return os.path.join(_deploy_root(), market_dir, name)


def ref_of_dir(path: str) -> Optional[str]:
    """프로젝트 폴더 절대경로 → ref (deploy 루트 밖이면 None)"""
    try:
        rel = os.path.relpath(os.path.abspath(path), _deploy_root())
    except ValueError:
        return None
    parts = rel.split(os.sep)
    if len(parts) != 2 or parts[0].startswith(".."):
        return None
    try:
        return make_ref(parts[0], parts[1])
    except ProjectError:
        return None


# =============================================================================
# 조회
# =============================================================================

def list_projects() -> List[str]:
    root = _deploy_root()
    refs = []
    if not os.path.isdir(root):
        return refs
    for market_dir in sorted(os.listdir(root)):
        m_path = os.path.join(root, market_dir)
        if not os.path.isdir(m_path) or not is_safe_segment(market_dir):
            continue
        for name in sorted(os.listdir(m_path)):
            if os.path.isdir(os.path.join(m_path, name)) and is_safe_segment(name):
                refs.append(f"{market_dir}/{name}")
    return refs


def find_by_name(name: str) -> List[str]:
    return [r for r in list_projects() if r.split("/", 1)[1] == name]


def name_taken(name: str) -> bool:
    return bool(find_by_name(name))


def resolve(name: Optional[str] = None, market_dir: Optional[str] = None,
            symbol: Optional[str] = None, interval: Optional[str] = None) -> Optional[str]:
    """프로젝트 ref 를 찾는다. 없으면 None.

    - market_dir 또는 (symbol, interval) 이 주어지면 그 경로만 본다 (이름 검색 없음).
    - 이름만 주어지면 모든 인터벌에서 찾되, 2개 이상이면 AmbiguousProjectError.
      (종전 코드처럼 "처음 나온 것" 을 조용히 쓰지 않는다)
    """
    if not name or not is_safe_segment(name):
        return None
    if not market_dir and symbol and interval:
        market_dir = market_dir_for(symbol, interval)
    if market_dir:
        if not is_safe_segment(market_dir):
            return None
        ref = f"{market_dir}/{name}"
        return ref if os.path.isdir(project_dir(ref)) else None
    matches = find_by_name(name)
    if len(matches) > 1:
        raise AmbiguousProjectError(
            f"같은 이름의 프로젝트가 여러 인터벌에 있습니다: {matches} — 종목/인터벌을 지정하세요.")
    return matches[0] if matches else None


def resolve_dir(name: Optional[str] = None, market_dir: Optional[str] = None,
                symbol: Optional[str] = None, interval: Optional[str] = None) -> Optional[str]:
    try:
        ref = resolve(name, market_dir=market_dir, symbol=symbol, interval=interval)
    except AmbiguousProjectError:
        return None
    return project_dir(ref) if ref else None


# =============================================================================
# 활성 프로젝트 포인터
# =============================================================================

def pointer_path(config_dir: str) -> str:
    return os.path.join(config_dir, ACTIVE_POINTER_FILE)


def get_active_ref(config_dir: str) -> Optional[str]:
    """열린 프로젝트 ref. 포인터 파일이 없으면 None(열린 프로젝트 없음).

    포인터가 **있는데 깨졌으면** BrokenProjectPointerError 를 던진다. 종전에는 읽기 실패·폴더 없음을 모두
    조용히 None('자유 모드')으로 바꿔, 전역 active.json 의 낡은 설정이 대신 쓰였다(2026-10-01: 연구 루프와
    대시보드가 오랫동안 열린 프로젝트가 아닌 전역 설정으로 학습했다).
    """
    path = pointer_path(config_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            ref = (json.load(f) or {}).get("ref")
    except Exception as e:
        raise BrokenProjectPointerError(f"열린 프로젝트 포인터를 읽을 수 없습니다 ({e}) — {path}")
    if not ref:
        raise BrokenProjectPointerError(f"열린 프로젝트 포인터에 ref 가 비어 있습니다 — {path}")
    try:
        split_ref(ref)
    except ProjectError as e:
        raise BrokenProjectPointerError(f"열린 프로젝트 포인터가 올바르지 않습니다: {e}")
    if not os.path.isdir(project_dir(ref)):
        raise BrokenProjectPointerError(
            f"열린 프로젝트 폴더가 없습니다: {ref} — 프로젝트를 다시 열거나 새로 만드세요 (포인터: {path})")
    return ref


def set_active_ref(config_dir: str, ref: Optional[str]) -> None:
    path = pointer_path(config_dir)
    if ref is None:
        if os.path.exists(path):
            os.remove(path)
        return
    split_ref(ref)
    os.makedirs(config_dir, exist_ok=True)
    _write_json_atomic(path, {"ref": ref, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


# =============================================================================
# 프로젝트 설정 읽기/쓰기
# =============================================================================

def _write_json_atomic(path: str, data) -> None:
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp, path)


def config_path(ref: str) -> str:
    return os.path.join(project_dir(ref), PROJECT_CONFIG_FILE)


def read_project_config(ref: str) -> dict:
    path = config_path(ref)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def identity_of(ref: str, stored: Optional[dict] = None) -> dict:
    """프로젝트 신원. 종목 원문(KRW-BTC 등)은 저장된 설정에서, 인터벌·이름은 폴더 경로에서 얻는다."""
    market_dir, name = split_ref(ref)
    stored = stored if stored is not None else read_project_config(ref)
    interval = interval_of_market_dir(market_dir)
    symbol = stored.get("SYMBOL_UPBIT") or market_dir.rsplit("_", 1)[0]
    if symbol_code(symbol) != market_dir.rsplit("_", 1)[0]:
        # 저장본이 경로와 어긋나면 경로가 이긴다 (경로가 신원이다)
        symbol = market_dir.rsplit("_", 1)[0]
    return {
        "SYMBOL_UPBIT": symbol,
        "SYMBOL_BINANCE": stored.get("SYMBOL_BINANCE", ""),
        "INTERVAL": interval,
        "ACTIVE_SESSION_ID": name,
    }


def strip_runtime_keys(config: dict) -> dict:
    return {k: v for k, v in config.items() if not str(k).startswith("_")}


def write_project_config(ref: str, config: dict) -> None:
    """프로젝트 설정 저장. 신원 키는 항상 프로젝트 것으로 고정하고, 실행용 임시 키(_*)는 버린다."""
    pdir = project_dir(ref)
    if not os.path.isdir(pdir):
        raise ProjectNotFoundError(f"프로젝트 폴더가 없습니다: {ref}")
    data = strip_runtime_keys(dict(config))
    data.update(identity_of(ref, read_project_config(ref) or data))
    _write_json_atomic(config_path(ref), data)


META_KEY = "PROJECT_META"


def read_meta(ref: str) -> dict:
    """프로젝트 정보(메모·생성일·복사 원본). 종전에는 model/best_params 안에 있어 모델을 치우면 함께 사라졌다."""
    meta = read_project_config(ref).get(META_KEY)
    return dict(meta) if isinstance(meta, dict) else {}


def update_meta(ref: str, **fields) -> dict:
    cfg = read_project_config(ref)
    meta = cfg.get(META_KEY) if isinstance(cfg.get(META_KEY), dict) else {}
    meta.update({k: v for k, v in fields.items() if v is not None})
    cfg[META_KEY] = meta
    write_project_config(ref, cfg)
    return meta


def read_run_result(run_dir: str) -> dict:
    path = os.path.join(run_dir, "result.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def create_project_dir(ref: str) -> str:
    """새 프로젝트 폴더 생성. 이름이 어느 인터벌에든 이미 있으면 거부."""
    market_dir, name = split_ref(ref)
    existing = find_by_name(name)
    if existing:
        raise ProjectExistsError(f"이미 존재하는 프로젝트 이름입니다: {existing[0]} — 다른 이름을 쓰세요.")
    pdir = project_dir(ref)
    os.makedirs(os.path.join(pdir, "model"), exist_ok=True)
    os.makedirs(os.path.join(pdir, "reports"), exist_ok=True)
    return pdir


# =============================================================================
# 실행(run) 폴더
# =============================================================================

def runs_dir(ref: str) -> str:
    return os.path.join(project_dir(ref), RUNS_DIRNAME)


def list_runs(ref: str) -> List[str]:
    """실행 폴더 절대경로 목록 (오래된 것 → 최신)"""
    rdir = runs_dir(ref)
    if not os.path.isdir(rdir):
        return []
    names = sorted(n for n in os.listdir(rdir) if os.path.isdir(os.path.join(rdir, n)))
    return [os.path.join(rdir, n) for n in names]


def latest_run_dir(ref: str) -> Optional[str]:
    runs = list_runs(ref)
    return runs[-1] if runs else None


def new_run_dir(ref: str, keep: int = RUNS_KEEP, now: Optional[datetime] = None) -> str:
    """이번 실행 폴더를 만들고, 오래된 실행은 최근 keep 개만 남기고 지운다."""
    rdir = runs_dir(ref)
    os.makedirs(rdir, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(rdir, stamp)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(rdir, f"{stamp}_{n}")
    os.makedirs(os.path.join(path, RUN_WORK_DIRNAME))
    prune_runs(ref, keep=keep)
    return path


def prune_runs(ref: str, keep: int = RUNS_KEEP) -> List[str]:
    runs = list_runs(ref)
    removed = []
    if keep is None or keep < 1 or len(runs) <= keep:
        return removed
    for old in runs[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old)
    return removed


# =============================================================================
# 산출물 경로 — 학습 엔진·백테스터·연구 루프가 공유하는 단일 규칙
# =============================================================================

def session_root(config: dict, session_id: Optional[str] = None) -> str:
    """프로젝트 폴더 (실행 중이어도 프로젝트 루트)."""
    from shared.core.market_utils import get_target_symbol  # 순환 import 회피
    sid = session_id or config.get("ACTIVE_SESSION_ID")
    return os.path.join(_deploy_root(), market_dir_for(get_target_symbol(config), config.get("INTERVAL", "1m")), sid)


def artifact_dir(config: dict, session_id: Optional[str] = None) -> str:
    """학습 산출물(model/, reports/, README, 스냅샷)을 쓸 폴더.

    자동연구 실행 중(config[_RUN_DIR] 있음)이면 ``runs/<시각>/work`` — 프로젝트 루트의 ``model/`` 을
    건드리지 않는다. 그 외(사용자가 직접 학습·분석)에는 프로젝트 폴더.
    """
    run_dir = config.get(RUN_DIR_KEY)
    if run_dir:
        return os.path.join(run_dir, RUN_WORK_DIRNAME)
    return session_root(config, session_id)


def run_log_dir(config: dict, session_id: Optional[str] = None) -> str:
    """실행 로그(backtest_run.log 등)를 쓸 폴더 — 실행 중이면 run 폴더, 아니면 프로젝트 폴더."""
    return config.get(RUN_DIR_KEY) or session_root(config, session_id)


# =============================================================================
# 실행 로그 — 서버가 실행 중에 직접 기록 (브라우저 '로그 저장' 버튼·공용 로그 끝부분 복사에 의존하지 않는다)
# =============================================================================
#
# 종전 결함 (2026-09-28): 학습 로그는 브라우저 메모리에만 쌓였다가 '로그 저장' 을 눌러야 공용
# logs/quant_engine_log.txt 에 기록됐고, 학습 종료 시 그 파일 끝 2MB 를 세션 train_run.log 로 복사했다.
# 저장 전에 복사가 일어나 KODEX200_M1_TEST2(1m) 폴더에 10초봉 세션 로그가 들어갔다.

import threading as _threading

RUN_LOG_FILE = "train_run.log"
RUN_LOG_MAX_BYTES = 50 * 1024 * 1024
_LOG_LOCK = _threading.Lock()


def tee_log(log_fn, path: str, truncate: bool = False, max_bytes: int = RUN_LOG_MAX_BYTES):
    """log_fn(msg) 호출마다 path 에도 '[HH:MM:SS] msg' 를 기록하는 동기 래퍼를 돌려준다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if truncate or not os.path.exists(path):
        open(path, "w", encoding="utf-8").close()
    state = {"capped": False}

    def _tee(msg):
        try:
            with _LOG_LOCK:
                if not state["capped"]:
                    size = os.path.getsize(path) if os.path.exists(path) else 0
                    with open(path, "a", encoding="utf-8") as f:
                        if size >= max_bytes:
                            f.write(f"[{datetime.now():%H:%M:%S}] ⚠️ 로그 상한({max_bytes // (1024 * 1024)}MB) 도달 — 이후 기록 생략\n")
                            state["capped"] = True
                        else:
                            f.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
        except Exception:
            pass
        if log_fn is None:
            print(msg)
            return None
        return log_fn(msg)

    _tee.log_path = path
    return _tee
