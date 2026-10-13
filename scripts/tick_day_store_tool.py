#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/tick_day_store_tool.py — 틱 일자 저장소 수동 작업
========================================================
  python scripts/tick_day_store_tool.py verify  [--symbols 069500 ...]          # 구조 불변식·메타 해시·봉인 잠금 검사
  python scripts/tick_day_store_tool.py seal    [--symbols ...]                  # 마감일 봉인(봉인된 1분봉과 대조) + 리눅스 잠금
  python scripts/tick_day_store_tool.py accept  --symbols 000660 --days 2026-08-04 2026-08-05 --reason "사유"
      → 복구 불가가 확인된 '부족' 편차를 날짜별로 명시 등록(사유 필수). 등록 후 seal 하면 ACCEPTED_DEVIATION 으로 봉인된다.
--symbols 를 빼면 저장소의 모든 종목.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "CAiModelBaker"))

from core import candle_day_store as cds      # noqa: E402
from core import tick_day_store as tds        # noqa: E402

RAW = os.path.join(ROOT, "DataVault", "raw", "creon")
TICK = os.path.join(RAW, "tick")


CANDLE = os.path.join(RAW, "candle_1m")


def minute_ref(sym):
    """1분봉 일자 저장소의 **봉인된** 날 거래량 — 틱 봉인·허용 편차 등록의 기준(봉인 전 1분봉은 기준이 아니다)"""
    return lambda days: cds.sealed_volumes(CANDLE, sym, days)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["verify", "seal", "accept"])
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--days", nargs="*")
    ap.add_argument("--reason", default="")
    a = ap.parse_args()
    syms = [tds.core_symbol(s) for s in a.symbols] if a.symbols else \
        sorted(d[1:] for d in os.listdir(TICK) if d.startswith("A") and os.path.isdir(os.path.join(TICK, d)))
    rc = 0
    for sym in syms:
        if a.cmd == "verify":
            issues = tds.verify(TICK, sym)
            print(f"A{sym}: {'OK' if not issues else issues}")
            rc |= 1 if issues else 0
        elif a.cmd == "seal":
            rep = tds.seal_closed_days(TICK, sym, ref_volumes=minute_ref(sym))
            locked = tds.lock_sealed(TICK, sym)
            print(f"A{sym}: 봉인 {rep['sealed']} · 격리 {rep['quarantined']} · 잠금 {len(locked)}일")
        else:
            if not a.days or not a.reason.strip():
                ap.error("accept 에는 --days 와 --reason 이 필요합니다")
            for d in a.days:
                m = tds.accept_deviation(TICK, sym, d, None, a.reason, ref_volumes=minute_ref(sym))
                print(f"A{sym} {d}: 허용 편차 등록 (일치율 {m['deviation_ratio']})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
