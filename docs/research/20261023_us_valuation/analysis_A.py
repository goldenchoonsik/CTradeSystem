"""A. 설명 — 배수 변동 원천 분해(A1)·배수 수준(A2)·배수 변화 동행(A3). 사전등록 PREREGISTRATION.md.

출력 results_A.json + 표 출력.  실행: .venv/bin/python -I analysis_A.py
"""
import json
import sys
import os

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vlib import HERE, ols, panel  # noqa: E402

K = 15


def a1(p):
    """연 단위(12월) 현재가치 항등식 분해. cf = 'D'(배당) 또는 'E10'(10년 평균 이익, 근사)."""
    dec = p[p.index.month == 12].copy()
    dec.index = dec.index.year
    E10 = p["E_real"].rolling(120, min_periods=120).mean()
    dec["E10"] = E10[E10.index.month == 12].values
    out = {}
    for cf in ("D", "E10"):
        C = dec["D_real"] if cf == "D" else dec["E10"]
        P = dec["P_real"]
        r = np.log((P + dec["D_real"]) / P.shift(1))          # 연 실질 총수익(그해 배당 포함)
        dc = np.log(C / C.shift(1))
        x = np.log(C / P)
        rho = 1 / (1 + np.exp(np.log(dec["D_real"] / P).mean()))
        yr = sum(rho ** (j - 1) * r.shift(-j) for j in range(1, K + 1))
        yd = sum(rho ** (j - 1) * dc.shift(-j) for j in range(1, K + 1))
        yx = rho ** K * x.shift(-K)
        res = {}
        for lab, (a, b) in {"전체": (1871, 2010), "1871~1945": (1871, 1945), "1946~2010": (1946, 2010)}.items():
            m = (x.index >= a) & (x.index <= b)
            X = pd.DataFrame({"x": x[m]})
            br = ols(yr[m], X, nw_lags=K)
            bd = ols(yd[m], X, nw_lags=K)
            bx = ols(yx[m], X, nw_lags=K)
            sd = x[m].std()
            res[lab] = {"n": br["n"], "rho": round(rho, 4),
                        "수익_몫": round(br["b"]["x"], 3), "수익_t": round(br["t"]["x"], 2),
                        "현금흐름_몫": round(-bd["b"]["x"], 3), "현금흐름_t": round(-bd["t"]["x"], 2),
                        "남은배수_몫": round(bx["b"]["x"], 3),
                        "합": round(br["b"]["x"] - bd["b"]["x"] + bx["b"]["x"], 3),
                        "x_1sd": round(sd, 3),
                        "1sd당_15년수익_연%p": round(100 * br["b"]["x"] * sd / sum(rho ** (j - 1) for j in range(1, K + 1)), 2)}
        out[cf] = res
    return out


ERAS = {"1881~1949": ("1881-01", "1949-12"), "1950~1997": ("1950-01", "1997-12"), "1998~2026": ("1998-01", "2026-10")}


def a2(p):
    d = p.copy()
    d["lcape"] = np.log(d["CAPE_pit"])
    reg = {}
    for lab, (a, b) in ERAS.items():
        s = d.loc[a:b]
        xs = ["real10", "infl12", "infl_vol"] + (["credit"] if lab != "1881~1949" else [])
        r = ols(s["lcape"], s[xs], nw_lags=24)
        single = {x: round(ols(s["lcape"], s[[x]])["r2"], 3) for x in xs}
        reg[lab] = {"n": r["n"], "r2": round(r["r2"], 3),
                    "계수(log CAPE / 1%p)": {k: round(v, 3) for k, v in r["b"].items() if k != "const"},
                    "t(NW24)": {k: round(v, 2) for k, v in r["t"].items() if k != "const"},
                    "단독R2": single}
    # 1919~1949 신용 포함 보조
    s = d.loc["1919-01":"1949-12"]
    r = ols(s["lcape"], s[["real10", "infl12", "infl_vol", "credit"]], nw_lags=24)
    reg["1919~1949(신용 포함)"] = {"n": r["n"], "r2": round(r["r2"], 3),
                                 "계수(log CAPE / 1%p)": {k: round(v, 3) for k, v in r["b"].items() if k != "const"}}
    bins = [-np.inf, 0, 2, 4, 6, np.inf]
    labs = ["<0", "0~2", "2~4", "4~6", "≥6"]
    bucket = {}
    for lab, (a, b) in ERAS.items():
        s = d.loc[a:b, ["CAPE_pit", "infl12"]].dropna()
        g = s.groupby(pd.cut(s["infl12"], bins, labels=labs), observed=False)["CAPE_pit"]
        bucket[lab] = {k: {"CAPE중앙": round(v, 1) if pd.notna(v) else None, "개월": int(n)}
                       for (k, v), n in zip(g.median().items(), g.size().values)}
    return {"회귀": reg, "물가구간": bucket}


def a3(p):
    d = p.copy()
    d["dlp"] = 100 * np.log(d["P"]).diff()
    for c in ("tips10", "breakeven", "credit", "GS10", "infl12"):
        d["d_" + c] = d[c].diff()
    out = {}
    specs = {
        "(i) TIPS 2003~2019": ("2003-02", "2019-12", ["d_tips10", "d_breakeven", "d_credit"]),
        "(i) TIPS 2020~2026": ("2020-01", "2026-10", ["d_tips10", "d_breakeven", "d_credit"]),
        "(i) TIPS 2003~2026": ("2003-02", "2026-10", ["d_tips10", "d_breakeven", "d_credit"]),
        "(ii) 1953~1997": ("1953-05", "1997-12", ["d_GS10", "d_infl12", "d_credit"]),
        "(ii) 1998~2026": ("1998-01", "2026-10", ["d_GS10", "d_infl12", "d_credit"]),
    }
    for lab, (a, b, xs) in specs.items():
        s = d.loc[a:b, ["dlp"] + xs].dropna()
        r = ols(s["dlp"], s[xs], nw_lags=3)
        z = {x: round(r["b"][x] * s[x].std() / s["dlp"].std(), 3) for x in xs}
        out[lab] = {"n": r["n"], "r2": round(r["r2"], 3),
                    "계수(%/1%p)": {x: round(r["b"][x], 2) for x in xs},
                    "t(NW3)": {x: round(r["t"][x], 2) for x in xs},
                    "표준화계수": z,
                    "단독R2": {x: round(ols(s["dlp"], s[[x]])["r2"], 3) for x in xs}}
    return out


def main():
    p = panel()
    res = {"A1": a1(p), "A2": a2(p), "A3": a3(p)}
    with open(os.path.join(HERE, "results_A.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
