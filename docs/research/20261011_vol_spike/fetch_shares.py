# -*- coding: utf-8 -*-
"""현재 상장주식수(MarketEye 20) — 시가총액 순위 근사용 (Windows 32-bit)."""
import csv, os, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import win32com.client
HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(HERE, "..", "..", "..", "DataVault", "staging", "vol_spike_universe")
cp = win32com.client.Dispatch("CpUtil.CpCybos")
me = win32com.client.Dispatch("CpSysDib.MarketEye")
mgr = win32com.client.Dispatch("CpUtil.CpCodeMgr")   # IsBigListingStock=1 이면 상장주식수 단위가 천 주
codes = [r["code"] for r in csv.DictReader(open(os.path.join(STAGE, "universe.csv"), encoding="utf-8"))]
rows = []
for i in range(0, len(codes), 200):
    chunk = codes[i:i + 200]
    while cp.GetLimitRemainCount(1) <= 2:
        time.sleep(0.5)
    me.SetInputValue(0, [0, 4, 20])
    me.SetInputValue(1, chunk)
    me.BlockRequest()
    n = me.GetHeaderValue(2)
    for k in range(n):
        code = me.GetDataValue(0, k)
        sh = me.GetDataValue(2, k) * (1000 if mgr.IsBigListingStock(code) else 1)
        rows.append((code, me.GetDataValue(1, k), sh))
with open(os.path.join(STAGE, "shares_now.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["code", "price", "shares"]); w.writerows(rows)
print("saved", len(rows), rows[:2])
