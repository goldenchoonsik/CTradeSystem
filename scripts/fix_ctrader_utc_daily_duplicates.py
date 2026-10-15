#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cTrader 일봉 원천의 UTC 라벨 중복 행 제거 (2026-10-16, 1회성 정리 — 다시 돌려도 지울 행이 없으면 아무것도 안 한다).

실측: raw/ctrader/*_1d.csv 6개 파일에 2024-01-07 ~ 2026-10-01 의 일봉이 두 번씩 있었다 — KST 라벨(06:00/07:00, 뉴욕 17:00 롤오버)과
같은 봉을 UTC 시각 그대로 적은 라벨(21:00/22:00). UTC 행 4,093개 모두 +9시간 KST 행과 OHLCV 가 완전히 같다(순수 중복).
그 탓에 1m→1d 집계 정합 검사가 고가·저가 절반 불일치로 실패해 USDJPY 등 일봉 정규화가 10-11 이후 갱신되지 않았다.

지우는 조건(셋 다): 시각이 21:00/22:00 · +9시간 행이 있음 · 그 행과 OHLCV 동일. 백업 → 교체 → 원장(.days.json) 재작성 → 매니페스트 재발행.
  python scripts/fix_ctrader_utc_daily_duplicates.py            # 계획만
  python scripts/fix_ctrader_utc_daily_duplicates.py --apply
"""
import argparse
import glob
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from CAiModelBaker.core import raw_day_store as rds  # noqa: E402

VAULT = os.path.join(ROOT, "DataVault")
BACKUP_DIR = os.path.join(VAULT, "quarantine", "repair_backups")
COLS = ["open", "high", "low", "close", "volume"]


def utc_duplicates(df: pd.DataFrame) -> pd.Index:
    t = pd.to_datetime(df["timestamp"])
    kst = df.assign(_t=t).set_index("_t")[COLS]
    cand = df[t.dt.hour.isin([21, 22])]
    twin = pd.to_datetime(cand["timestamp"]) + pd.Timedelta(hours=9)
    other = kst.reindex(twin.values)
    same = (cand[COLS].to_numpy() == other.to_numpy()).all(axis=1)
    return cand.index[same]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    for fp in sorted(glob.glob(os.path.join(VAULT, "raw", "ctrader", "*_1d.csv"))):
        df = pd.read_csv(fp, dtype=str)
        for c in COLS:
            df[c] = df[c].astype(float)
        drop = utc_duplicates(df)
        print(f"{os.path.basename(fp)}: {len(df)}행 중 UTC 중복 {len(drop)}행")
        if not a.apply or not len(drop):
            continue
        rds._backup(fp, BACKUP_DIR, keep=5, log=print)
        keep = set(range(len(df))) - set(drop)
        with open(fp, encoding="utf-8") as f:
            lines = f.read().splitlines()
        out = [lines[0]] + [ln for i, ln in enumerate(lines[1:]) if i in keep]
        tmp = fp + ".dedupe.tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, fp)
        v = rds.rebuild_ledger(fp, note="UTC 라벨 중복 제거(2026-10-16 fix_ctrader_utc_daily_duplicates)")
        print(f"   원장 재작성: {v['summary']}")
        import json
        from shared.data_library.manifest_manager import ManifestManager
        try:
            with open(fp + ".manifest.json", encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = {}
        # 기존 매니페스트의 시장·시간대 표기는 그대로 둔다(이 정리는 행 제거만 한다)
        ManifestManager.generate_and_save_manifest(data_file_path=fp, data_tier="raw", provider="ctrader", symbol=old.get("symbol"),
                                                   asset_type=old.get("asset_type") or "standard",
                                                   market=old.get("market") or "US", timezone_name=old.get("timezone") or "UTC",
                                                   notes="UTC 라벨 중복 제거 후 재발행")


if __name__ == "__main__":
    main()
