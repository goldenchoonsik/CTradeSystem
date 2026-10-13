# -*- coding: utf-8 -*-
"""
CTradeSystem - Reference/Macro Staleness Audit (참조 지표 stale 전파 실측 감사)
==============================================================================
[T-3 / D-1] 목적
----------------
`merge_asof(tolerance=N)` 직후 `ffill()` 을 적용하면, tolerance 초과로 생긴 NaN 이
앞값으로 다시 채워져 **`ref_*_close` 에서 tolerance 가 무력화**된다(D-1, P-2 특성화로 발견).
그 결과 참조/매크로 값이 휴장·데이터 종료 구간을 넘어 **무한 전파**될 수 있다.

본 스크립트는 **동결된 기존 스냅샷만 읽어(쓰기 없음)** 실제로 얼마나 오래 전파되는지를
측정한다. stale 상한(ffill cap) 값을 임의로 정하지 않고 **근거를 확보**하기 위한 사전 감사다.

측정 방법 (lower bound)
-----------------------
참조 타임스탬프를 스냅샷에 저장하지 않으므로, **동일 값이 연속 반복된 구간(run)** 의
시간 폭을 stale 전파의 **하한**으로 사용한다.
  · 매 봉마다 신선하게 매칭되면 값은 거의 항상 변한다(연속 반복 ≈ 0)
  · ffill 로 전파되면 값이 정확히 동일하게 반복된다
  · 단, 원천 자체가 변하지 않은 경우(거래정지·동일 체결가)도 반복으로 잡히므로
    이 값은 **상한이 아니라 하한**이다.

대상 컬럼
---------
`ref_*_close` 및 매크로 `*_close` (가격류) — stale 전파가 직접 문제가 되는 컬럼.
`*_volume` / `*_bid_vol` 은 `fillna(0.0)` 처리이므로 별도로 결측(0) 비율만 집계한다.

사용법
------
  .venv/bin/python3 scripts/audit_reference_staleness.py
  .venv/bin/python3 scripts/audit_reference_staleness.py --json docs/reports/REFERENCE_STALENESS.json
"""

import os
import sys
import json
import glob
import argparse

import numpy as np
import pandas as pd

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_JSON = os.path.join(_PROJECT_ROOT, "docs", "reports", "REFERENCE_STALENESS.json")

# stale 판정 임계값 (시간)
_THRESHOLDS = [
    ("1h", pd.Timedelta(hours=1)),
    ("6h", pd.Timedelta(hours=6)),
    ("1d", pd.Timedelta(days=1)),
    ("3d", pd.Timedelta(days=3)),
    ("7d", pd.Timedelta(days=7)),
]

# stale 전파가 직접 문제가 되는 '가격류' 컬럼 (참조 대장주 + 매크로)
_PRICE_SUFFIX = "_close"
_VOLUME_SUFFIXES = ("_volume", "_bid_vol")


def _snapshot_files():
    pat = os.path.join(_PROJECT_ROOT, "CAiModelBaker", "data", "*", "data_snapshot_*.csv")
    return sorted(glob.glob(pat))


def _candidate_columns(csv_path):
    """헤더만 읽어 가격류(참조/매크로) 후보와 거래량류 후보를 분리."""
    header = pd.read_csv(csv_path, nrows=0)
    cols = list(header.columns)

    ref_price = [c for c in cols if c.startswith("ref_") and c.endswith(_PRICE_SUFFIX)]
    macro_price = [
        c for c in cols
        if c.endswith(_PRICE_SUFFIX)
        and not c.startswith("ref_")
        and "target_" not in c
        and "upbit_" not in c
        and c != "timestamp"
    ]
    vol_cols = [c for c in cols if c.startswith("ref_") and c.endswith(_VOLUME_SUFFIXES)]
    return ref_price + macro_price, vol_cols


def _run_stats(ts: pd.Series, val: pd.Series):
    """동일 값 연속 반복 구간(run)의 시간 폭 통계 (stale 하한)."""
    nn = val.notna()
    if nn.sum() < 2:
        return None

    ts_nn = ts[nn].reset_index(drop=True)
    val_nn = val[nn].reset_index(drop=True)

    # 값이 바뀔 때마다 새 run
    run_id = val_nn.ne(val_nn.shift()).cumsum()
    g = pd.DataFrame({"run": run_id, "ts": ts_nn}).groupby("run")["ts"]
    sizes = g.size()
    durs = (g.max() - g.min())

    total_rows = int(nn.sum())
    result = {
        "rows_nonnull": total_rows,
        "n_runs": int(len(durs)),
        "max_run_bars": int(sizes.max()),
        "max_run_hours": round(float(durs.max().total_seconds() / 3600.0), 2),
    }
    for label, thr in _THRESHOLDS:
        mask = durs > thr
        stale_rows = int(sizes[mask].sum())
        result[f"runs_gt_{label}"] = int(mask.sum())
        result[f"rows_gt_{label}"] = stale_rows
        result[f"pct_gt_{label}"] = round(100.0 * stale_rows / total_rows, 3) if total_rows else 0.0
    return result


def audit_snapshot(csv_path):
    """단일 스냅샷의 참조/매크로 컬럼 stale 전파 측정 (읽기 전용)."""
    ref_cols, vol_cols = _candidate_columns(csv_path)
    if not ref_cols and not vol_cols:
        return None

    symbol = os.path.basename(os.path.dirname(csv_path))
    interval = os.path.basename(csv_path).replace("data_snapshot_", "").replace(".csv", "")

    df = pd.read_csv(csv_path, usecols=["timestamp"] + ref_cols + vol_cols)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    diffs = df["timestamp"].diff().dropna()
    bar_seconds = float(diffs.median().total_seconds()) if len(diffs) else None

    price_stats = {}
    for c in ref_cols:
        st = _run_stats(df["timestamp"], pd.to_numeric(df[c], errors="coerce"))
        if st:
            price_stats[c] = st

    volume_stats = {}
    for c in vol_cols:
        s = pd.to_numeric(df[c], errors="coerce")
        volume_stats[c] = {
            "zero_or_null_ratio": round(float((s.fillna(0.0) == 0.0).mean()), 4),
        }

    return {
        "symbol": symbol,
        "interval": interval,
        "rows": int(len(df)),
        "bar_seconds": bar_seconds,
        "price_columns": price_stats,
        "volume_columns": volume_stats,
    }


def print_summary(results):
    print("=" * 104)
    print(" 🔬 참조/매크로 stale 전파 실측 (ffill 연속반복 구간 = stale 하한)")
    print("=" * 104)
    print(" ※ 값이 실제로 변하지 않은 경우도 반복으로 잡히므로 이 수치는 '하한'입니다.")

    worst = {"hours": 0.0, "col": None, "target": None}
    for r in results:
        if not r or not r["price_columns"]:
            continue
        print(f"\n■ {r['symbol']}/{r['interval']}   rows={r['rows']:,}   bar={r['bar_seconds']}s")
        print(f"  {'column':<26}{'maxHours':>10}{'maxBars':>9}{'>1d%':>8}{'>3d%':>8}{'>7d%':>8}")
        for c, st in sorted(r["price_columns"].items(), key=lambda kv: -kv[1]["max_run_hours"]):
            print(f"  {c:<26}{st['max_run_hours']:>10.2f}{st['max_run_bars']:>9}"
                  f"{st['pct_gt_1d']:>8.3f}{st['pct_gt_3d']:>8.3f}{st['pct_gt_7d']:>8.3f}")
            if st["max_run_hours"] > worst["hours"]:
                worst = {"hours": st["max_run_hours"], "col": c,
                         "target": f"{r['symbol']}/{r['interval']}"}

    print("\n" + "=" * 104)
    if worst["col"]:
        print(f" ⚠️ 최장 stale 전파(하한): {worst['target']} / {worst['col']} → "
              f"{worst['hours']:.2f}시간 ({worst['hours'] / 24:.2f}일)")
        print("    → ffill 상한(stale cap) 정책 수립의 근거로 사용합니다.")
    else:
        print(" ✅ 측정 대상 참조 컬럼 없음")
    print("=" * 104)


def main():
    parser = argparse.ArgumentParser(description="참조/매크로 stale 전파 실측 감사 (읽기 전용)")
    parser.add_argument("--json", default=_DEFAULT_JSON, help="리포트 JSON 출력 경로")
    args = parser.parse_args()

    files = _snapshot_files()
    if not files:
        print("❌ 스냅샷 파일을 찾을 수 없습니다.")
        return 1

    results = []
    for path in files:
        r = audit_snapshot(path)
        if r:
            results.append(r)

    print_summary(results)

    all_max = [st["max_run_hours"] for r in results for st in r["price_columns"].values()]
    summary = {
        "audited_snapshots": [
            {"snapshot": f"{r['symbol']}/{r['interval']}", "rows": r["rows"],
             "price_columns": len(r["price_columns"])}
            for r in results
        ],
        "max_stale_hours_lower_bound": round(max(all_max), 2) if all_max else None,
        "method": "동일값 연속반복 run 폭 = stale 전파 하한 (참조 타임스탬프 미저장으로 상한 불가)",
        "thresholds_hours": {label: thr.total_seconds() / 3600.0 for label, thr in _THRESHOLDS},
    }

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "details": results}, f, ensure_ascii=False, indent=2)

    print(f"\n📄 리포트 저장: {os.path.relpath(args.json, _PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
