# -*- coding: utf-8 -*-
"""macro 시계열(국내선물/지수/해외선물) 데이터 파일은 종목 원장 키로 이름 짓는다 (futures_1m.csv 같은 별칭 이름 금지).

배경: KOSPI200 선물/지수 파일이 futures_1m.csv / spot_1m.csv 로 저장되어 파일이 어느 종목의 데이터인지 알 수 없었고,
빌더가 파일명에 "es"(fut-ur-es)가 들어 있는지로 해외선물을 판정해 국내선물 시계열이 1분 밀려 있었다.
별칭(alias)은 폐지되었다 — 파일 이름도 컬럼 이름(ref_{key}_*)도 원장 심볼에서 유도한다.
"""
import json
import os

import pandas as pd
import pytest

from shared.config import config_manager as cm
from shared.config.market_policies import BAR_CLOSE, BAR_OPEN, timestamp_convention
from shared.data_library import paths as dpaths
from shared.data_library.source_mapping_manager import SourceMappingManager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_macro_paths_are_keyed_by_the_ledger_symbol():
    for sym in ("10100", "U180", "NQ", "ES"):
        assert os.path.basename(dpaths.macro_normalized_path(sym, "1m")).startswith(f"{sym}_1m")
    assert os.path.basename(dpaths.macro_raw_path("10100", "1m")).startswith("10100_1m")
    assert os.path.basename(dpaths.macro_raw_path("U180", "1m")).startswith("U180_1m")
    # 별칭은 더 이상 원장 키로 해석되지 않는다 (해석되면 옛 이름 파일이 조용히 되살아난다)
    assert cm.resolve_macro_symbol("futures") is None and cm.resolve_macro_symbol("spot") is None


def test_macro_series_classification_follows_the_collection_category():
    assert SourceMappingManager.macro_series_info("10100")[0] == "10100"
    assert SourceMappingManager.macro_series_info("FUTURES") is None             # 별칭은 없다
    assert SourceMappingManager.macro_series_info("NQ")[1] == "OVERSEAS_FUTURES"
    # CFD 대용은 macro 시계열이 아니다 (일반 캔들 파이프라인) — 대신하는 실물은 원장 proxy_of 가 안다
    assert cm.get_proxy_of("US_500") == "ES"
    assert SourceMappingManager.macro_series_info("US_500") is None
    assert SourceMappingManager.macro_series_info("069500") is None


def test_proxy_relation_is_defined_only_by_the_ledger():
    """대용(CFD) 관계의 정의는 원장의 proxy_of 한 곳뿐이다 — 코드가 {실물: 대용} 표를 따로 들면 안 된다."""
    # 원장에서 양방향이 도출된다
    assert cm.get_proxy_for("NQ") == "US_TECH_100" and cm.get_proxy_of("US_TECH_100") == "NQ"
    assert cm.get_proxy_for("ES") == "US_500" and cm.get_proxy_of("US_500") == "ES"
    assert cm.get_proxy_for("069500") is None and cm.get_proxy_for("") is None

    # 원장에 선언된 모든 대용 관계는 역방향으로 왕복해야 한다
    for key, info in cm.load_symbols_registry(include_catalog=True).items():
        real = info.get("proxy_of") if isinstance(info, dict) else None
        if real:
            assert cm.get_proxy_for(real) is not None, f"{key}.proxy_of={real} 인데 역방향 조회가 비었다"

    # 운영 코드에 {실물: 대용} 리터럴 매핑이 되살아나지 않았는지 (래칫 정규식이 못 잡는 모양이라 별도 확인)
    pairs = [(k, v.get("proxy_of")) for k, v in cm.load_symbols_registry(include_catalog=True).items()
             if isinstance(v, dict) and v.get("proxy_of")]
    offenders = []
    for dp, dn, fn in os.walk(REPO_ROOT):
        dn[:] = [d for d in dn if d not in {".git", ".venv", "node_modules", "__pycache__",
                                            "DataVault", "logs", "vultr_logs", "deploy", "docs", ".pytest_cache"}
                 and not d.startswith(".")]   # 숨김 폴더(.claude/worktrees 사본) 제외
        for name in fn:
            if os.path.splitext(name)[1] not in {".py", ".js"} or name.startswith("test_"):
                continue
            path = os.path.join(dp, name)
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for proxy, real in pairs:
                for ln in text.splitlines():
                    stripped = ln.strip()
                    if stripped.startswith(("#", "//")):
                        continue
                    if f'"{real}"' in ln and f'"{proxy}"' in ln:
                        offenders.append(f"{os.path.relpath(path, REPO_ROOT)}: {stripped[:100]}")
    assert not offenders, (
        "대용 관계를 코드에 하드코딩한 줄이 있습니다 — cm.get_proxy_for()/get_proxy_of() 로 원장에서 읽으세요:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )


def test_bar_start_shift_is_decided_by_the_ledger_not_by_the_file_name():
    """규약 판정의 근거는 파일 이름도 심볼 이름도 아닌 수집처 원장이다."""
    assert timestamp_convention("NQ", "1m") == BAR_OPEN
    assert timestamp_convention("ES", "1m") == BAR_OPEN
    assert timestamp_convention("10100", "1m") == BAR_CLOSE     # KRX 는 봉 종료 시각 관례
    assert timestamp_convention("U180", "1m") == BAR_CLOSE


def _raw_csv(path, n=3):
    idx = pd.date_range("2026-04-15 09:01:00", periods=n, freq="1min")
    pd.DataFrame({"timestamp": idx, "close": [1.0, 2.0, 3.0][:n], "volume": [10, 20, 30][:n]}).to_csv(path, index=False)
    return idx


@pytest.mark.parametrize("key", ["10100", "U180", "NQ"])
def test_build_normalized_macro_never_shifts_timestamps(tmp_path, key):
    """[T-6] 정규화 단계는 타임스탬프를 건드리지 않는다 — 정렬은 결합 시점 한 곳에서만 한다.

    종전에는 정규화 단계(파일명/원장 기준)와 결합 단계 양쪽에 시프트가 있었고, 심볼이
    어느 빌드 경로를 타느냐에 따라 보정이 0번/1번/2번 적용됐다. 보정이 두 계층에 흩어져 있으면
    어느 파일이 몇 번 밀렸는지 아무도 알 수 없게 된다.
    """
    from CAiModelBaker.core.normalized_builder import build_normalized_macro
    raw = tmp_path / f"{key}_1m.csv"
    idx = _raw_csv(raw)
    out = build_normalized_macro(key, raw_path=str(raw), interval="1m", output_parquet_path=str(tmp_path / f"{key}_1m.parquet"),
                                 local_log=lambda s: None)
    got = pd.read_parquet(out)["timestamp"]
    assert list(got) == list(idx), "정규화 산출물은 원천 타임스탬프를 그대로 보존해야 한다"
    assert json.load(open(out + ".manifest.json", encoding="utf-8"))["symbol"] == key


def test_real_datavault_macro_files_use_ledger_keys():
    """실제 DataVault(gitignore 대상이라 CI 에는 없을 수 있음)의 macro/국내선물 원천 파일 이름이 원장 키여야 한다."""
    macro = os.path.join(REPO_ROOT, "DataVault", "normalized", "macro")
    raw = os.path.join(REPO_ROOT, "DataVault", "raw", "creon", "candle_1m")
    if not os.path.isdir(macro):
        pytest.skip("DataVault 없음")
    ledger = {k.upper() for k in cm.load_symbols_registry()}
    bad = [f for f in os.listdir(macro) if not f.endswith(".manifest.json") and f.rsplit("_", 1)[0].upper() not in ledger]
    if os.path.isdir(raw):
        bad += [f"raw/{f}" for f in os.listdir(raw) if f.split("_")[0] in ("futures", "spot")]
    assert bad == [], f"원장 키가 아닌 이름의 macro 데이터 파일: {bad}"
