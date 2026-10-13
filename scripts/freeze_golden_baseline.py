# -*- coding: utf-8 -*-
"""
CTradeSystem - Golden Baseline Freezer (데이터 파이프라인 회귀 기준선 동결)
==========================================================================
목적
----
데이터 파이프라인(수집/정규화/피처 조립)을 리팩터링하기 *전에*,
"지금의 모델 입력과 산출물"을 해시로 동결하여 회귀 감지를 가능하게 한다.

동결 대상 (읽기 전용 — 원본 데이터는 절대 수정하지 않음)
--------------------------------------------------------
1. 학습 데이터셋 : CAiModelBaker/data/{SYM}/data_snapshot_{INTERVAL}.csv (+ .meta.json)
2. 배포 모델     : CAiModelBaker/deploy/{SYM}_{INTERVAL}/{SESSION}/model/*
3. 백테스트 성과 : CAiModelBaker/deploy/{SYM}_{INTERVAL}/{SESSION}/reports/test_trades.csv

출력
----
docs/reports/GOLDEN_BASELINE.json  (기준선 지문. 이 파일만 생성하며 데이터는 건드리지 않음)

사용법
------
  .venv/bin/python3 scripts/freeze_golden_baseline.py
  .venv/bin/python3 scripts/freeze_golden_baseline.py --skip-hash     # 대용량 해시 생략(빠른 조회)
  .venv/bin/python3 scripts/freeze_golden_baseline.py --check         # 기존 기준선과 비교(회귀 판정)
"""

import os
import sys
import json
import glob
import hashlib
import argparse
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_OUTPUT = os.path.join(_PROJECT_ROOT, "docs", "reports", "GOLDEN_BASELINE.json")

_HASH_CHUNK = 1024 * 1024  # 1MB 스트리밍 (364MB 스냅샷 대응)


# ==============================================================================
# 해시 및 측정 유틸
# ==============================================================================

def _sha256(file_path, skip=False):
    """파일 SHA256 (1MB 청크 스트리밍). skip=True면 None."""
    if skip:
        return None
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _rel(path):
    """프로젝트 루트 기준 상대경로 (POSIX 구분자로 정규화하여 플랫폼 간 비교 가능)"""
    return os.path.relpath(path, _PROJECT_ROOT).replace(os.sep, "/")


def _file_entry(path, skip_hash=False):
    st = os.stat(path)
    return {
        "path": _rel(path),
        "size": int(st.st_size),
        "sha256": _sha256(path, skip=skip_hash),
    }


# ==============================================================================
# 1. 학습 데이터셋 스냅샷
# ==============================================================================

def collect_snapshots(skip_hash=False):
    """data/*/data_snapshot_*.csv 및 동반 .meta.json 을 동결.

    .meta.json 에 이미 rows/columns/timestamp_min/max 가 기록되어 있으므로
    (data_manager.py:2660 대) CSV 본문을 다시 파싱하지 않고 메타를 그대로 승계한다.
    """
    pattern = os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", "*", "data_snapshot_*.csv")
    entries = []

    for csv_path in sorted(glob.glob(pattern)):
        entry = _file_entry(csv_path, skip_hash=skip_hash)
        symbol = os.path.basename(os.path.dirname(csv_path))
        entry["symbol"] = symbol
        entry["interval"] = os.path.basename(csv_path).replace("data_snapshot_", "").replace(".csv", "")

        meta_path = csv_path.replace(".csv", ".meta.json")
        if os.path.exists(meta_path):
            entry["meta"] = _file_entry(meta_path, skip_hash=skip_hash)
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                # 메타에서 계약 검증에 필요한 핵심 필드만 승계
                entry["rows"] = meta.get("rows")
                entry["columns"] = meta.get("columns")
                entry["timestamp_min"] = meta.get("timestamp_min")
                entry["timestamp_max"] = meta.get("timestamp_max")
                entry["built_at"] = meta.get("built_at")
            except Exception as e:
                entry["meta_parse_error"] = str(e)
        else:
            entry["meta"] = None

        entries.append(entry)

    return entries


# ==============================================================================
# 2. 배포 모델 아티팩트
# ==============================================================================

def collect_models(skip_hash=False):
    """deploy/{SYM}_{INTERVAL}/{SESSION}/model/* 아티팩트 동결."""
    pattern = os.path.join(_PROJECT_ROOT, "CAiModelBaker", "deploy", "*", "*", "model", "*")
    entries = []

    for path in sorted(glob.glob(pattern)):
        if not os.path.isfile(path):
            continue
        entry = _file_entry(path, skip_hash=skip_hash)
        session_dir = os.path.dirname(os.path.dirname(path))
        entry["session"] = os.path.basename(session_dir)
        entry["model_bundle"] = os.path.basename(os.path.dirname(session_dir))
        entries.append(entry)

    return entries


# ==============================================================================
# 3. 백테스트 성과 기준선
# ==============================================================================

def collect_backtests(skip_hash=False):
    """reports/test_trades.csv 의 성과 지표를 동결 (모델 동작 회귀의 최종 판정 기준)."""
    pattern = os.path.join(_PROJECT_ROOT, "CAiModelBaker", "deploy", "*", "*", "reports", "test_trades.csv")
    entries = []

    for path in sorted(glob.glob(pattern)):
        entry = _file_entry(path, skip_hash=skip_hash)
        session_dir = os.path.dirname(os.path.dirname(path))
        entry["session"] = os.path.basename(session_dir)
        entry["model_bundle"] = os.path.basename(os.path.dirname(session_dir))

        try:
            import pandas as pd
            df = pd.read_csv(path)
            entry["trade_rows"] = int(len(df))
            if "net_return" in df.columns and len(df) > 0:
                ret = pd.to_numeric(df["net_return"], errors="coerce").dropna()
                entry["net_return_mean"] = float(ret.mean()) if len(ret) else None
                entry["net_return_sum"] = float(ret.sum()) if len(ret) else None
                entry["win_rate"] = float((ret > 0).mean()) if len(ret) else None
            if "exit_type" in df.columns and len(df) > 0:
                entry["exit_type_counts"] = {str(k): int(v) for k, v in df["exit_type"].value_counts().items()}
        except Exception as e:
            entry["parse_error"] = str(e)

        entries.append(entry)

    return entries


# ==============================================================================
# 리포트 생성 / 비교
# ==============================================================================

def build_baseline(skip_hash=False):
    return {
        "frozen_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "project_root": os.path.basename(_PROJECT_ROOT),
        "hash_included": not skip_hash,
        "note": (
            "데이터 파이프라인 리팩터링 전 회귀 기준선. "
            "본 파일은 학습 데이터셋/배포 모델/백테스트 성과의 지문이며, "
            "리팩터링 후 scripts/freeze_golden_baseline.py --check 로 동일성을 검증한다."
        ),
        "snapshots": collect_snapshots(skip_hash=skip_hash),
        "models": collect_models(skip_hash=skip_hash),
        "backtests": collect_backtests(skip_hash=skip_hash),
    }


def _index_by_path(entries):
    return {e["path"]: e for e in entries}


def compare_baseline(old, new):
    """기존 기준선과 현재 상태를 비교하여 회귀/변경을 판정."""
    print("=" * 78)
    print(" 🔬 골든 기준선 회귀 판정 (GOLDEN BASELINE CHECK)")
    print("=" * 78)

    if not old.get("hash_included", True) or not new.get("hash_included", True):
        print(" ⚠️ 해시가 포함되지 않은 기준선입니다. 동일성 판정이 제한됩니다.")

    total_drift = 0
    for section in ("snapshots", "models", "backtests"):
        old_map = _index_by_path(old.get(section, []))
        new_map = _index_by_path(new.get(section, []))
        added = sorted(set(new_map) - set(old_map))
        removed = sorted(set(old_map) - set(new_map))
        changed = []
        for p in sorted(set(old_map) & set(new_map)):
            o, n = old_map[p], new_map[p]
            if o.get("sha256") and n.get("sha256") and o["sha256"] != n["sha256"]:
                changed.append((p, "sha256", o["sha256"][:12], n["sha256"][:12]))
            elif o.get("size") != n.get("size"):
                changed.append((p, "size", o.get("size"), n.get("size")))

        drift = len(added) + len(removed) + len(changed)
        total_drift += drift
        status = "✅ 동일" if drift == 0 else f"⚠️ 변경 {drift}건"
        print(f"\n[{section}] 총 {len(new_map)}개 — {status}")

        for p in added:
            print(f"   + 추가 : {p}")
        for p in removed:
            print(f"   - 삭제 : {p}")
        for p, field, o, n in changed:
            print(f"   ~ 변경 : {p} ({field}: {o} → {n})")

    print("\n" + "=" * 78)
    if total_drift == 0:
        print(" ✅ 회귀 없음 — 기준선과 현재 상태가 완전히 동일합니다.")
    else:
        print(f" ⚠️ 총 {total_drift}건의 차이가 감지되었습니다. 의도된 변경인지 검토하세요.")
    print("=" * 78)
    return total_drift


def main():
    parser = argparse.ArgumentParser(description="CTradeSystem 골든 기준선 동결/검증")
    parser.add_argument("--output", default=_DEFAULT_OUTPUT, help="기준선 JSON 출력 경로")
    parser.add_argument("--skip-hash", action="store_true", help="SHA256 계산 생략 (빠른 조회)")
    parser.add_argument("--check", action="store_true", help="기존 기준선과 비교하여 회귀 판정만 수행")
    args = parser.parse_args()

    if args.check:
        if not os.path.exists(args.output):
            print(f"❌ 기준선 파일이 없습니다: {args.output}")
            print("   먼저 인자 없이 실행하여 기준선을 동결하세요.")
            return 1
        with open(args.output, "r", encoding="utf-8") as f:
            old = json.load(f)
        drift = compare_baseline(old, build_baseline(skip_hash=args.skip_hash))
        return 0 if drift == 0 else 2

    print("🧊 골든 기준선(Golden Baseline) 동결을 시작합니다...")
    print(f"   대상 루트: {_PROJECT_ROOT}")
    if not args.skip_hash:
        print("   ※ SHA256 스트리밍 계산 중 (수백 MB 스냅샷은 수 초 소요)...")

    baseline = build_baseline(skip_hash=args.skip_hash)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 78)
    print(" 🧊 골든 기준선 동결 완료")
    print("=" * 78)
    print(f"   학습 데이터셋 : {len(baseline['snapshots'])}개")
    print(f"   배포 모델     : {len(baseline['models'])}개")
    print(f"   백테스트 성과 : {len(baseline['backtests'])}개")
    print(f"   해시 포함     : {baseline['hash_included']}")
    print(f"   출력          : {_rel(args.output)}")
    print("=" * 78)
    print(" 💡 리팩터링 후 회귀 판정:")
    print("    .venv/bin/python3 scripts/freeze_golden_baseline.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
