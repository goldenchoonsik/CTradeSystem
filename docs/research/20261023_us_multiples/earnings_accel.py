"""보조(사전등록 밖, 탐색): 이익 '증가'(성장률>0) vs 이익 '가속'(성장률 상승) — 어느 쪽이 주가 상승 확률과 붙어 있나.

지수(Shiller 월, 1881~): t→t+12 실질 총수익 > 0 확률을 2×2(성장 부호 × 가속 부호)로.
  [미래를 안다면] 성장 = log E(t+12)/E(t), 가속 = 그 성장 − 직전 12개월 성장.
  [이미 발표된] 성장 = log E(t−3)/E(t−15), 가속 = 그 성장 − 12개월 전의 같은 값(또는 3개월 전 값).
종목(S&P500 2010~): 같은 기간 이익 변화/가격(pf_deps1_p) − 과거 1년 이익 변화/가격 = 가속, 시장 중앙값 이길 확률 2×2.
출력 results_earnings_accel.json.  실행: .venv/bin/python -I earnings_accel.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "20261023_steady_compounders"))
from sc_common import ROOT  # noqa: E402

ERAS = {"1881~1949": ("1881-01", "1949-12"), "1950~1997": ("1950-01", "1997-12"), "1998~2026": ("1998-01", "2026-10"), "전체": ("1881-01", "2026-10")}


def grid(ret, g, a):
    d = pd.DataFrame({"r": ret, "g": g, "a": a}).dropna()
    cell = lambda m: {"상승확률": round(float((d.r[m] > 0).mean()), 2), "평균수익%": round(100 * float(d.r[m].mean()), 1), "비중": round(float(m.mean()), 2)}
    return {"증가·가속": cell((d.g > 0) & (d.a > 0)), "증가·감속": cell((d.g > 0) & (d.a <= 0)),
            "감소·가속(감소폭 축소)": cell((d.g <= 0) & (d.a > 0)), "감소·감속": cell((d.g <= 0) & (d.a <= 0)),
            "가속 전체": cell(d.a > 0), "감속 전체": cell(d.a <= 0), "n": int(len(d))}


def index_part():
    p = pd.read_csv(os.path.join(ROOT, "staging", "us_valuation", "derived", "panel_monthly.csv"), index_col="ym")
    p.index = pd.PeriodIndex(p.index, freq="M")
    ltr, le = np.log(p.TR_real), np.log(p.E_real)
    rn = np.exp(ltr.shift(-12) - ltr) - 1
    g_fut = le.shift(-12) - le
    g_now = le - le.shift(12)
    g_rep = le.shift(3) - le.shift(15)
    out = {}
    for lab, (a, b) in ERAS.items():
        sl = slice(a, b)
        out[lab] = {"[미래를 안다면] 다음 12개월": grid(rn[sl], g_fut[sl], (g_fut - g_now)[sl]),
                    "[이미 발표된] 12개월 전 대비 가속": grid(rn[sl], g_rep[sl], (g_rep - g_rep.shift(12))[sl]),
                    "[이미 발표된] 3개월 전 대비 가속": grid(rn[sl], g_rep[sl], (g_rep - g_rep.shift(3))[sl])}
    gr = g_rep.dropna()
    out["현재"] = {"발표 기준 12개월 성장(log)": round(float(gr.iloc[-1]), 3), "3개월 전": round(float(gr.iloc[-4]), 3),
                 "12개월 전": round(float(gr.iloc[-13]), 3), "기준월(지연 반영)": str(gr.index[-1])}
    return out


def stock_part():
    P = pd.read_parquet(os.path.join(ROOT, "staging", "us_multiples", "derived", "panel.parquet"))
    d = P[["start", "pf_deps1_p", "eps_chg1_past", "ret1"]].replace([np.inf, -np.inf], np.nan).dropna().copy()
    d["win"] = d.ret1 > d.groupby("start").ret1.transform("median")
    d["acc"] = d.pf_deps1_p - d.eps_chg1_past
    cell = lambda m: {"중앙값 이길 확률": round(float(d.win[m].mean()), 2), "비중": round(float(m.mean()), 2)}
    g, a = d.pf_deps1_p > 0, d.acc > 0
    pg, pa = d.eps_chg1_past > 0, None
    return {"[같은 1년을 안다면]": {"증가·가속": cell(g & a), "증가·감속": cell(g & ~a), "감소·가속": cell(~g & a), "감소·감속": cell(~g & ~a)},
            "[이미 발표된] 과거 1년 증가": cell(pg), "[이미 발표된] 과거 1년 감소": cell(~pg), "n": int(len(d))}


def main():
    res = {"지수(Shiller, 다음 12개월 실질 총수익)": index_part(), "종목(S&P500 2010~, 다음 1년)": stock_part()}
    with open(os.path.join(HERE, "results_earnings_accel.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    I = res["지수(Shiller, 다음 12개월 실질 총수익)"]
    for era in ERAS:
        for k, v in I[era].items():
            print(f"{era:10s} {k:22s} " + " | ".join(f"{c}:{v[c]['상승확률']}/{v[c]['평균수익%']}%/{v[c]['비중']}" for c in ["증가·가속", "증가·감속", "감소·가속(감소폭 축소)", "감소·감속", "가속 전체", "감속 전체"]))
    print("현재", I["현재"])
    print(json.dumps(res["종목(S&P500 2010~, 다음 1년)"], ensure_ascii=False))


if __name__ == "__main__":
    main()
