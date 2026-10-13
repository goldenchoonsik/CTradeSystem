# -*- coding: utf-8 -*-
"""Creon 종목 마스터 분류값 탐침 (Windows 32-bit, 표준 라이브러리만)."""
import sys, collections
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import win32com.client
mgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")
for mkt, label in [(1, "KOSPI"), (2, "KOSDAQ")]:
    codes = mgr.GetStockListByMarket(mkt)
    kinds = collections.Counter()
    samples = {}
    for c in codes:
        k = mgr.GetStockSectionKind(c)
        kinds[k] += 1
        samples.setdefault(k, []).append(mgr.CodeToName(c))
    print(label, len(codes), dict(kinds))
    for k, names in samples.items():
        print("  ", k, names[:6])
