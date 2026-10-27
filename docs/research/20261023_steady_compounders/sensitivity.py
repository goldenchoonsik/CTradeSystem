"""민감도(사전등록: 낙폭 15/25%, 연복리 10/20%) + 저변동 ETF 대조(보조). 출력 results_sensitivity.json
실행: .venv/bin/python -I sensitivity.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sc_common import HERE, ROOT, load_raw, price_tr  # noqa: E402
import analysis as A  # noqa: E402

VARIANTS = {"기본(15%/20%/0.8)": (0.15, -0.20), "낙폭15": (0.15, -0.15), "낙폭25": (0.15, -0.25),
            "연복리10": (0.10, -0.20), "연복리20": (0.20, -0.20)}


def main():
    X0 = A.load()
    out = {}
    for name, (c, m) in VARIANTS.items():
        X = X0.copy()
        valid = X.SC.notna()
        X.loc[valid, "SC"] = ((X.cagr >= c) & (X.mdd >= m) & (X.r2 >= 0.80))[valid].astype(float)
        X["grp"] = np.where(valid, np.where(X.SC == 1, "SC", "기타"), None)
        reg = A.market_regime(X)
        row = {"SC비율": round(float(X.SC.mean()), 4), "국면별SC": {k: v["SC비율"] for k, v in reg.items()}}
        for fname, col, sign, binary in A.FEATS:
            if col in ("p_SC",):
                continue
            dv = A.feature_lift(X, col, sign, binary, *A.DEV)
            ho = A.feature_lift(X, col, sign, binary, *A.HOLD)
            row[fname] = f"{dv['lift']}/{ho['lift']}"
        out[name] = row
    # 저변동 ETF(보조): 2017-01-03 ~ 2026-08-31, 2012-01-03 ~ 2016-12-30
    cal = load_raw("SPY").loc["2011-01-01":"2026-08-31"].index
    etf = {}
    for a, b in (("2012-01-03", "2016-12-30"), ("2017-01-03", "2026-08-31")):
        row = {}
        for t in ("SPY", "SPLV", "USMV", "RSP", "QQQ"):
            d = price_tr(t, cal)
            if d is None:
                continue
            tr = d["tr"].loc[a:b].dropna()
            r = tr.pct_change().dropna()
            yrs = len(r) / 252
            row[t] = {"CAGR%": round(100 * ((tr.iloc[-1] / tr.iloc[0]) ** (1 / yrs) - 1), 2),
                      "변동성%": round(100 * r.std() * np.sqrt(252), 2),
                      "MDD%": round(100 * float((tr / tr.cummax() - 1).min()), 1)}
        etf[f"{a[:4]}~{b[:7]}"] = row
    out["저변동ETF(보조)"] = etf
    with open(os.path.join(HERE, "results_sensitivity.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
