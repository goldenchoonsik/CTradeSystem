import hashlib
import os
import shutil

import pytest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# 테스트가 실수로 덮어쓰면 안 되는 추적 대상 실설정 파일
_WATCHED = (
    "shared/config/symbols_registry.json",
    "shared/config/accounts.json",
    "shared/config/common.json",
    "DataVault/config/source_mappings.json",
    "DataVault/config/symbol_relations.json",
    "DataVault/config/data_sources_catalog.json",
    "DataVault/config/market_holidays.json",
    "CAiModelBaker/config/active.json",
    "CAiModelBaker/config/active_config.json",
    "CAiModelBaker/config/active_project.json",
)
_before_hashes = {}

# 테스트 세션이 여는 사본 프로젝트 (conftest 의 _isolated_runtime_config 가 임시 deploy/ 에 만든다)
_HARNESS_SYMBOL = "069500"
_HARNESS_INTERVAL = "10s"
_HARNESS_NAME = "TEST_HARNESS"


def _hash(rel):
    try:
        with open(os.path.join(REPO_ROOT, rel), "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return None


# 실제 정규화 볼트에서 사라진 파일 감시 — 2026-10-10, 스케일 갱신/고아 정리 테스트가 실제 볼트에서 돌아
# 배분 엔진용 일봉(069500_1d)과 069500_15m 을 지운 사고. 사라지면 세션 끝에 경고한다.
_NORMALIZED_DIR = os.path.join(REPO_ROOT, "DataVault", "normalized")
_before_normalized = set()


def _normalized_files():
    out = set()
    for root, _, files in os.walk(_NORMALIZED_DIR):
        out.update(os.path.relpath(os.path.join(root, f), REPO_ROOT) for f in files if f.endswith(".parquet"))
    return out


def pytest_sessionstart(session):
    _before_hashes.update({rel: _hash(rel) for rel in _WATCHED})
    _before_normalized.update(_normalized_files())


def pytest_sessionfinish(session, exitstatus):
    changed = [rel for rel in _WATCHED if _hash(rel) != _before_hashes.get(rel)]
    vanished = sorted(_before_normalized - _normalized_files())
    if vanished:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        msg = "⚠️ 테스트 세션 중 실제 정규화 파일이 사라졌습니다 (테스트가 실제 볼트에서 정리/삭제를 실행함): " + ", ".join(vanished)
        if reporter:
            reporter.write_line(msg, red=True)
        else:
            print(msg)
    if changed:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        msg = ("⚠️ 테스트 세션 중 실제 설정 파일이 변경되었습니다 (테스트가 격리를 우회했거나 "
               "실행 중인 대시보드가 저장했을 수 있음): " + ", ".join(changed))
        if reporter:
            reporter.write_line(msg, yellow=True)
        else:
            print(msg)


@pytest.fixture(autouse=True)
def _restore_open_project_pointer():
    """테스트마다 열린 프로젝트 포인터를 원래대로 되돌린다.

    프로젝트를 열거나 지우는 테스트가 임시 프로젝트를 가리키는 포인터를 남기면, 다음 테스트가 깨진 포인터를 만난다
    (깨진 포인터는 이제 조용히 '프로젝트 없음' 이 되지 않고 BrokenProjectPointerError 를 낸다).
    """
    try:
        from shared.config import config_manager as cm
        from shared.config import project_store as ps
        path = ps.pointer_path(cm.get_config_dir("CAiModelBaker"))
    except Exception:
        yield
        return
    before = open(path, "rb").read() if os.path.exists(path) else None
    yield
    if before is None:
        if os.path.exists(path):
            os.remove(path)
    else:
        with open(path, "wb") as f:
            f.write(before)


@pytest.fixture(scope="session", autouse=True)
def _isolated_runtime_config(tmp_path_factory):
    """테스트가 실제 설정(종목 원장, common.json, active.json, active_config.json)을 오염시키지 않도록
    세션 동안 설정 경로를 임시 사본으로 돌려놓는다.

    과거에는 test_target_horizon 등이 실제 active.json/common.json 을 덮어써서
    (참조종목이 비워지거나 SYMBOL_UPBIT 가 바뀜) 테스트를 돌릴 때마다 git diff 를 손으로 되돌려야 했다.
    읽기 값은 사본이 현재 실설정과 동일하므로 테스트 기대값은 그대로 유지된다.
    """
    try:
        from shared.config import config_manager as cm
        from shared.features import feature_config
    except Exception:
        yield
        return

    mirror = tmp_path_factory.mktemp("config_mirror")

    ledger_original = cm.SYMBOLS_REGISTRY_PATH
    ledger_copy = str(mirror / "symbols_registry.json")
    if os.path.exists(ledger_original):
        shutil.copy(ledger_original, ledger_copy)

    project_dirs_original = dict(cm._PROJECT_DIRS)
    for project, real_dir in project_dirs_original.items():
        src = os.path.join(real_dir, "config")
        if os.path.isdir(src):
            # active_project.json(열린 프로젝트 포인터)은 복사하지 않는다 — 복사하면 load_config/save_config 가
            # 실제 deploy/<프로젝트>/config_snapshot.json 을 읽고 써서 격리가 뚫린다. 테스트는 자유 모드로 돈다.
            shutil.copytree(src, str(mirror / project / "config"),
                            ignore=shutil.ignore_patterns("*.bak", "active_project.json"))
        cm._PROJECT_DIRS[project] = str(mirror / project)

    common_original = cm.COMMON_CONFIG_PATH
    common_copy = str(mirror / "shared" / "config" / "common.json")
    os.makedirs(os.path.dirname(common_copy), exist_ok=True)
    if os.path.exists(common_original):
        shutil.copy(common_original, common_copy)

    # 대장(source_mappings.json)도 사본으로 돌린다. SourceMappingManager 의 쓰기 API(update_target_scales,
    # batch_update_* 등)는 모듈 전역 _MAPPINGS_FILE_PATH 를 그대로 쓰므로 격리하지 않으면 테스트가 실제 대장을
    # 영구히 고쳐 버린다 (2026-09-26: test_medallion_scale_separation 의 "원복" 호출이 KRW-BTC 의
    # target_scales 를 ["10m"] 로 잘라, 이후 KRW-BTC 15m 을 쓰는 테스트들이 줄줄이 실패했다).
    try:
        from shared.data_library import source_mapping_manager as smm
    except Exception:
        smm = None
    mappings_original = getattr(smm, "_MAPPINGS_FILE_PATH", None) if smm else None
    if mappings_original:
        mappings_copy = str(mirror / "DataVault" / "config" / "source_mappings.json")
        os.makedirs(os.path.dirname(mappings_copy), exist_ok=True)
        if os.path.exists(mappings_original):
            shutil.copy(mappings_original, mappings_copy)
        smm._MAPPINGS_FILE_PATH = mappings_copy
        smm.SourceMappingManager._cache = None
        smm.SourceMappingManager._last_mtime = 0.0

    try:
        from shared.data_library import symbol_relations_manager as srm
    except Exception:
        srm = None
    relations_original = getattr(srm, "_RELATIONS_FILE_PATH", None) if srm else None
    if relations_original:
        relations_copy = str(mirror / "DataVault" / "config" / "symbol_relations.json")
        os.makedirs(os.path.dirname(relations_copy), exist_ok=True)
        if os.path.exists(relations_original):
            shutil.copy(relations_original, relations_copy)
        srm._RELATIONS_FILE_PATH = relations_copy

    config_root_original = getattr(feature_config, "_CONFIG_ROOT_INTERNAL", None)
    set_root_original = feature_config.set_config_root
    try:
        from shared.features import feature_registry as _fr
    except Exception:
        _fr = None
    fr_set_root_original = getattr(_fr, "set_config_root", None)

    # 계좌 원장도 사본으로 돌린다 (현재 쓰기 API 는 없지만, 생기는 순간 실제 계좌 원장을 고치지 않도록 미리 막는다)
    from shared.config import account_registry as _ar
    accounts_original = _ar.ACCOUNTS_PATH
    accounts_copy = str(mirror / "shared" / "config" / "accounts.json")
    if os.path.exists(accounts_original):
        shutil.copy(accounts_original, accounts_copy)
    _ar.ACCOUNTS_PATH = accounts_copy
    _ar._cache["sig"] = None

    cm.SYMBOLS_REGISTRY_PATH = ledger_copy
    cm.COMMON_CONFIG_PATH = common_copy
    cm._registry_cache["sig"] = None
    set_root_original(str(mirror))
    # CAiModelBaker.core.feature_registry 는 import 시점에 set_config_root(<실제 Baker 경로>)를 호출해
    # 격리를 되돌린다 (테스트 본문에서 lazy import 되는 경우 포함). 세션 동안 무력화한다.
    noop = lambda root_path: None  # noqa: E731
    feature_config.set_config_root = noop
    if _fr is not None and fr_set_root_original is not None:
        _fr.set_config_root = noop

    # 테스트도 운영과 같은 경로로 돈다 — '열린 프로젝트' 가 있는 상태. (2026-10-01 자유 모드 은퇴 전에는 테스트가
    # 프로젝트 없이 전역 active.json 사본으로 돌아, 운영에서만 나는 설정 경로 결함을 보지 못했다.)
    # 배포 폴더도 사본으로 돌려 테스트가 실제 deploy/ 에 프로젝트를 만들지 않게 한다.
    from shared.data_library import paths as _dpaths
    from shared.config import project_store as _ps
    deploy_root_original = _dpaths.DEPLOY_ROOT
    _dpaths.DEPLOY_ROOT = str(mirror / "deploy")
    _harness_ref = _ps.make_ref(_ps.market_dir_for(_HARNESS_SYMBOL, _HARNESS_INTERVAL), _HARNESS_NAME)
    _ps.create_project_dir(_harness_ref)
    _harness_cfg = cm.load_base_config("CAiModelBaker")
    _harness_cfg.update({"SYMBOL_UPBIT": _HARNESS_SYMBOL, "INTERVAL": _HARNESS_INTERVAL, "ACTIVE_SESSION_ID": _HARNESS_NAME})
    _ps.write_project_config(_harness_ref, _harness_cfg)
    cm.set_active_project_ref(_harness_ref)
    try:
        yield
    finally:
        _dpaths.DEPLOY_ROOT = deploy_root_original
        if mappings_original:
            smm._MAPPINGS_FILE_PATH = mappings_original
            smm.SourceMappingManager._cache = None
            smm.SourceMappingManager._last_mtime = 0.0
        if relations_original:
            srm._RELATIONS_FILE_PATH = relations_original
        feature_config.set_config_root = set_root_original
        if _fr is not None and fr_set_root_original is not None:
            _fr.set_config_root = fr_set_root_original
        cm.SYMBOLS_REGISTRY_PATH = ledger_original
        cm.COMMON_CONFIG_PATH = common_original
        _ar.ACCOUNTS_PATH = accounts_original
        _ar._cache["sig"] = None
        cm._PROJECT_DIRS.clear()
        cm._PROJECT_DIRS.update(project_dirs_original)
        cm._registry_cache["sig"] = None
        set_root_original(config_root_original)


@pytest.fixture(autouse=True)
def _no_real_name_lookups(monkeypatch):
    """종목명 원천 조회(업비트 공개 API 등)가 테스트 중 실제 네트워크를 타지 않도록 막는다.
    이름 조회 동작을 검증하는 테스트는 fake provider 를 직접 등록하거나 이 패치를 되돌려 사용한다."""
    try:
        from shared.data_library import symbol_name_resolver as resolver
    except Exception:
        return
    monkeypatch.setattr(resolver, "_upbit_provider", lambda symbol, market_type="": None)
    monkeypatch.setattr(resolver, "_PROVIDERS", [])


# 테스트가 실제 원천(DataVault/raw)에 쓰지 못하게 막는다 — 2026-09-25, 수집기 저장 경로가 DataVault 로 바뀐 뒤
# 옛 테스트가 임시 폴더라고 믿고 실제 NQ_1m.csv 에 테스트 행을 써 넣은 사고가 있었다. 모든 원천 쓰기는
# raw_day_store.merge_file / widen_header / resolve_conflicts / record_unrecoverable 를 거치므로 그 입구에서 거부한다.
_REAL_RAW = os.path.realpath(os.path.join(REPO_ROOT, "DataVault", "raw")) + os.sep


@pytest.fixture(autouse=True)
def _forbid_writes_to_real_raw_store(monkeypatch):
    try:
        from CAiModelBaker.core import raw_day_store as rds
    except Exception:
        yield
        return

    def guard(fn, arg_index):
        def wrapped(*a, **k):
            target = a[arg_index] if len(a) > arg_index else None
            if target and os.path.realpath(str(target)).startswith(_REAL_RAW):
                raise AssertionError(f"테스트가 실제 원천에 쓰려고 함: {target}")
            return fn(*a, **k)
        return wrapped

    monkeypatch.setattr(rds, "merge_file", guard(rds.merge_file, 1))
    monkeypatch.setattr(rds, "widen_header", guard(rds.widen_header, 0))
    monkeypatch.setattr(rds, "resolve_conflicts", guard(rds.resolve_conflicts, 0))
    monkeypatch.setattr(rds, "record_unrecoverable", guard(rds.record_unrecoverable, 0))
    yield


# 테스트가 대시보드의 실제 품질 감사 캐시(DataVault/temp/cache/data_quality_cache.json)를 덮어쓰지 못하게 한다 — 2026-10-15 사고:
# 한 테스트가 캐시 경로를 임시로 바꿔 메모리 캐시(_mem)에 임시 내용을 올린 뒤 경로만 되돌아가고, 다음 실데이터 회귀 테스트가
# 그 메모리를 실제 경로에 통째로 저장해 전 종목 감사 결과(11MB)가 4항목(그중 하나는 pytest 임시 경로)으로 바뀌었다.
# 모든 테스트에서 경로와 메모리 상태를 함께 격리한다(끝나면 monkeypatch 가 셋 다 되돌린다).
@pytest.fixture(autouse=True)
def _isolate_data_quality_cache(monkeypatch, tmp_path):
    try:
        from shared.data_library import data_quality as dq
    except Exception:
        return
    monkeypatch.setattr(dq, "_CACHE_PATH", str(tmp_path / "data_quality_cache.json"))
    monkeypatch.setattr(dq.DataQualityAuditor, "_mem", {})
    monkeypatch.setattr(dq.DataQualityAuditor, "_loaded", False)
