"""QQQ 반도체 비중 추정 (2026-10-22). 상위 25 = stockanalysis.com 10-20 보유(주식 수·비중, 아래 표에 고정),
26위 이하 = Nasdaq API 10-21 시총 × (상위 25 중 비감축 종목의 비중/시총 비율). ADR(ASML·ARM)은 지수 주식 수가 달라 범위로.
  .venv/bin/python -I docs/research/20261022_qqq_rates/semis_weight.py <ndx.json 경로>
"""
import json, sys
import numpy as np

TOP25 = {"NVDA": (8.15, 182670617), "AAPL": (7.74, 110619420), "MSFT": (6.17, 56281831), "AMZN": (4.42, 81751105),
         "MU": (4.36, 23631050), "AMD": (3.76, 34157443), "META": (3.22, 22537126), "GOOGL": (3.12, 44474452),
         "TSLA": (3.07, 39815365), "GOOG": (2.90, 41890013), "SPCX": (2.73, 89759789), "AVGO": (2.48, 36058383),
         "WMT": (2.47, 101758474), "INTC": (2.08, 109944032), "PLTR": (1.91, 48140743), "COST": (1.90, 9279614),
         "CSCO": (1.81, 82471300), "LRCX": (1.42, 26183150), "AMAT": (1.39, 16605668), "NFLX": (1.23, 87132921),
         "PANW": (1.20, 17054023), "TXN": (1.09, 19110255), "AMGN": (1.02, 11312772), "CRWD": (1.00, 21426304), "LIN": (1.00, 9646139)}
SEMI = ["NVDA", "MU", "AMD", "AVGO", "INTC", "LRCX", "AMAT", "TXN", "ASML", "ARM", "MRVL", "KLAC", "SNDK", "ADI", "QCOM",
        "MPWR", "NXPI", "TER", "ALAB", "MCHP"]
MEMORY = ["MU", "SNDK"]
STORAGE = ["STX", "WDC"]          # 저장장치(하드웨어) — 메모리 사이클 민감, 반도체 분류는 아님
ADR = {"ASML", "ARM"}

rows = {r["symbol"]: r for r in json.load(open(sys.argv[1]))["data"]["data"]["rows"]}
px = {s: float(r["lastSalePrice"].strip("$").replace(",", "")) for s, r in rows.items()}
cap = {s: float(r["marketCap"].replace(",", "") or 0) for s, r in rows.items()}
# 검산: 비중 ∝ 보유 주식 × 가격
val = {s: sh * px[s] for s, (w, sh) in TOP25.items()}
nav = sum(val[s] for s in TOP25) / (sum(w for w, _ in TOP25.values()) / 100)
print("검산(보유×가격 → 비중):", " ".join(f"{s} {val[s] / nav * 100:.2f}/{TOP25[s][0]:.2f}" for s in ["NVDA", "AAPL", "MU", "AVGO", "LIN"]))
# 비감축 종목 비율: 상위 25 중 시총 대비 비중이 가장 큰 무리(중형)
ratio = {s: TOP25[s][0] / (cap[s] / 1e12) for s in TOP25 if s not in ("GOOGL", "GOOG")}
mid = [s for s in ratio if ratio[s] > np.median(list(ratio.values())) * 0.95]
k = np.median([ratio[s] for s in mid])
print("중형 무리 비율(%/조달러):", round(k, 2), "범위", round(min(ratio[s] for s in mid), 2), "~", round(max(ratio[s] for s in mid), 2), mid)
est = {s: TOP25[s][0] if s in TOP25 else cap[s] / 1e12 * k for s in rows}
tot = sum(est.values())
print("추정 합계(%):", round(tot, 1), "(100 이 아니면 비율 근사 오차)")


def grp(names, adr_zero=False):
    return sum(0 if (adr_zero and s in ADR and s not in TOP25) else est[s] for s in names if s in est)


lo, hi = grp(SEMI, True), grp(SEMI)
print(f"반도체(설계·제조·장비) {lo:.1f}~{hi:.1f}% (ADR 0~추정치), 100 정규화 {lo / tot * 100:.1f}~{hi / tot * 100:.1f}%")
print("  종목별:", " ".join(f"{s} {est[s]:.2f}" for s in sorted([s for s in SEMI if s in est], key=lambda s: -est[s])))
print(f"메모리(MU+SNDK) {grp(MEMORY):.1f}%, +저장장치(STX·WDC) {grp(MEMORY + STORAGE):.1f}%")
print(f"엔비디아 단독 {est['NVDA']:.2f}%, 상위 3 반도체 {sum(sorted([est[s] for s in SEMI if s in est])[-3:]):.1f}%")
