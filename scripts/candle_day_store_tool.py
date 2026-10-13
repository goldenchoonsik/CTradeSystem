#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/candle_day_store_tool.py — 1분봉 일자 저장소 수동 작업
==============================================================
  python scripts/candle_day_store_tool.py migrate --src <통합 CSV 폴더> [--root <저장소 루트>]
      → 통합 CSV(`A{sym}_1m.csv`/`10100_1m.csv`)를 날짜별 `A{sym}/YYYY-MM-DD.csv` 로 쪼갠다(행 바이트 그대로). 날짜마다 (행·거래량·해시)를
        원본 `.days.json` 원장과 대조하고 하나라도 다르면 그 종목은 쓰지 않는다. 기존 일자 파일이 있으면 덮어쓰지 않는다.
  python scripts/candle_day_store_tool.py verify [--root R] [--symbols ...]   # 구조 불변식·메타 해시·봉인 잠금
  python scripts/candle_day_store_tool.py seal   [--root R] [--symbols ...]   # 마감일 봉인(구조·일봉 상한·틱 대조) + 리눅스 잠금
--root 기본값은 DataVault/raw/creon/candle_1m. --symbols 를 빼면 저장소의 모든 종목.
"""
import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "CAiModelBaker"))

from core import candle_day_store as cds      # noqa: E402
from core import raw_day_store as rds         # noqa: E402

RAW = os.path.join(ROOT, "DataVault", "raw", "creon")
DEFAULT_ROOT = os.path.join(RAW, "candle_1m")
TICK = os.path.join(RAW, "tick")


def daily_csv(sym):
    for p in (os.path.join(RAW, "candle_1d", f"A{sym}_1d.csv"), os.path.join(RAW, "candle_1d", f"{sym}_1d.csv")):
        if os.path.exists(p):
            return p
    return None


def migrate_one(src_csv, root):
    """통합 CSV 한 개 → 일자 저장소. 반환 (종목, 날짜 수, 문제 목록)."""
    stem = os.path.basename(src_csv)[: -len("_1m.csv")]
    sym = cds.core_symbol(stem)
    ledger = rds.load_ledger(src_csv) or {}
    led_days = ledger.get("days", {})
    header = rds.read_header(src_csv)
    vol_idx = rds._find_volume_index(header.split(","), None)
    by_day = {}
    for line in rds._read_lines(src_csv):
        if line == header:
            continue
        ts = rds._ts(line)
        if not rds._valid_ts(ts):
            return sym, 0, [f"시각 형식이 아닌 행 {line[:40]!r}"]
        by_day.setdefault(rds._day(ts), []).append(line)
    problems = []
    plan = {}
    for day, lines in by_day.items():
        acc = rds._DayAcc()
        prev = ""
        for l in lines:
            ts = rds._ts(l)
            if ts <= prev:
                problems.append(f"{day}: 시각 중복/역행 {prev} → {ts}")
                break
            prev = ts
            acc.add(l, ts, rds._volume_of(l, vol_idx))
        e = led_days.get(day)
        if not e:
            problems.append(f"{day}: 원장에 없는 날짜")
        elif (e.get("rows"), e.get("sha256")) != (acc.rows, acc.as_dict()["sha256"]):
            problems.append(f"{day}: 원장과 행/해시 불일치 (원장 {e.get('rows')}행)")
        plan[day] = (lines, acc)
    for day in led_days:
        if day not in by_day:
            problems.append(f"{day}: 원장에는 있는데 CSV 에 없음")
    if problems:
        return sym, 0, problems
    now = tds_now()
    for day, (lines, acc) in sorted(plan.items()):
        target = cds.day_csv(root, sym, day)
        if os.path.exists(target):
            problems.append(f"{day}: 이미 일자 파일이 있음 — 덮어쓰지 않음")
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as w:
            w.write(header + "\n")
            w.write("".join(l + "\n" for l in lines))
        os.replace(tmp, target)
        s = cds.scan_day(target, day)
        cds.write_meta(root, sym, day, {"symbol": sym, "date": day, "status": "OPEN", "rows": s["rows"], "volume": round(s["volume"], 4),
                                        "sha256": s["sha256"], "first": s["first"], "last": s["last"], "updated_at": now,
                                        "migrated_from": os.path.basename(src_csv)})
    return sym, len(plan), problems


def tds_now():
    from core import tick_day_store as tds
    return tds._now()


def symbols_in(root):
    return sorted(d[1:] for d in os.listdir(root) if d.startswith("A") and os.path.isdir(os.path.join(root, d)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["migrate", "verify", "seal"])
    ap.add_argument("--src")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--symbols", nargs="*")
    a = ap.parse_args()
    rc = 0
    if a.cmd == "migrate":
        if not a.src:
            ap.error("migrate 에는 --src 가 필요합니다")
        t0 = time.time()
        for f in sorted(glob.glob(os.path.join(a.src, "*_1m.csv"))):
            sym, n, probs = migrate_one(f, a.root)
            print(f"{os.path.basename(f)}: {'OK ' + str(n) + '일' if not probs else 'FAIL'}" + ("" if not probs else f" — {probs[:5]}"))
            rc |= 1 if probs else 0
        print(f"완료 {time.time() - t0:.0f}초")
        return rc
    syms = [cds.core_symbol(s) for s in a.symbols] if a.symbols else symbols_in(a.root)
    for sym in syms:
        if a.cmd == "verify":
            issues = cds.verify(a.root, sym)
            print(f"A{sym}: {'OK' if not issues else issues}")
            rc |= 1 if issues else 0
        elif a.cmd == "seal":
            rep = cds.seal_closed_days(a.root, sym, daily_csv=daily_csv(sym), tick_root=TICK)
            locked = cds.lock_sealed(a.root, sym)
            print(f"A{sym}: 봉인 {len(rep['sealed'])}일 · 격리 {rep['quarantined']} · 건너뜀 {rep['skipped']} · 잠금 {len(locked)}일")
    return rc


if __name__ == "__main__":
    sys.exit(main())
