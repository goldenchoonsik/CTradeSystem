"""보조(사전등록 밖, 탐색): 과거 PER 변화(재평가 추세)가 앞으로를 알려주나 — 지수·종목.

지수: Shiller 월 패널(us_valuation derived/panel_monthly.csv) — 과거 h개월 Δlog CAPE → 다음 h개월 Δlog CAPE·실질 총수익, 시대별.
종목: us_multiples panel.parquet(분기) — 과거 1년 Δlog PER(흑자) → 다음 1년 수익률·Δlog PER, 분기 단면 순위상관.
출력 results_pe_change.json.  실행: .venv/bin/python -I pe_change.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "20261023_steady_compounders"))
from sc_common import ROOT  # noqa: E402

ERAS = {"1881~1949": ("1881-01", "1949-12"), "1950~1997": ("1950-01", "1997-12"), "1998~2026": ("1998-01", "2026-10")}


def index_part():
    p = pd.read_csv(os.path.join(ROOT, "staging", "us_valuation", "derived", "panel_monthly.csv"), index_col="ym")
    p.index = pd.PeriodIndex(p.index, freq="M")
    lc, ltr = np.log(p.CAPE_pit), np.log(p.TR_real)
    out = {}
    for h in (12, 36, 60):
        past = lc - lc.shift(h)
        fut_c = lc.shift(-h) - lc
        fut_r = 100 * (np.exp((ltr.shift(-h) - ltr) * 12 / h) - 1)
        row = {}
        for lab, (a, b) in ERAS.items():
            d = pd.DataFrame({"past": past, "fc": fut_c, "fr": fut_r}).loc[a:b].dropna()
            q = d.past.rank(pct=True)
            row[lab] = {"과거ΔPER↔다음ΔPER": round(float(d.past.corr(d.fc)), 2),
                        "과거ΔPER↔다음 수익": round(float(d.past.corr(d.fr)), 2),
                        "과거 PER 급등(상위20%) 다음 연수익%": round(float(d.fr[q > 0.8].mean()), 1),
                        "과거 PER 급락(하위20%) 다음 연수익%": round(float(d.fr[q <= 0.2].mean()), 1),
                        "전체 평균%": round(float(d.fr.mean()), 1), "n": int(len(d))}
        out[f"{h}개월"] = row
    # 현재
    out["현재(2026-10)"] = {f"과거 {h}개월 ΔlogCAPE": round(float(lc.iloc[-1] - lc.iloc[-1 - h]), 3) for h in (12, 36, 60)}
    return out


def stock_part():
    P = pd.read_parquet(os.path.join(ROOT, "staging", "us_multiples", "derived", "panel.parquet"))
    P = P.sort_values(["ticker", "start"])
    P["lpe"] = np.log(P.pe)
    g = P.groupby("ticker")
    P["lpe_past4"] = g.lpe.shift(4)
    P["lpe_fut4"] = g.lpe.shift(-4)
    # 4분기 전이 실제로 1년 전인지 확인
    P["gap_past"] = (P.start - g.start.shift(4)).dt.days
    P["gap_fut"] = (g.start.shift(-4) - P.start).dt.days
    P["d_past"] = np.where(P.gap_past.between(350, 380), P.lpe - P.lpe_past4, np.nan)
    P["d_fut"] = np.where(P.gap_fut.between(350, 380), P.lpe_fut4 - P.lpe, np.nan)
    # 가격 변화 = PER 변화 + 이익 변화 → 과거 1년 수익(모멘텀) 대조
    P["ret_past"] = g.ret1.shift(4)
    P.loc[~P.gap_past.between(350, 380), "ret_past"] = np.nan
    split = pd.Timestamp("2018-01-01")

    def ic(a, b):
        d = P[["start", a, b]].replace([np.inf, -np.inf], np.nan).dropna()
        s = d.groupby("start").apply(lambda x: x[a].rank().corr(x[b].rank()) if len(x) >= 30 else np.nan, include_groups=False).dropna()
        return {"IC": round(float(s.mean()), 3), "2010~17": round(float(s[s.index < split].mean()), 3),
                "2018~": round(float(s[s.index >= split].mean()), 3), "분기": int(len(s))}

    def qs(a, b):
        d = P[["start", a, b]].replace([np.inf, -np.inf], np.nan).dropna()
        def one(x):
            q = x[a].rank(pct=True)
            return x[b][q > 0.8].mean() - x[b][q <= 0.2].mean()
        s = d.groupby("start").apply(one, include_groups=False).dropna()
        return round(100 * float(s.mean()), 2)

    return {"과거1년 ΔlogPER → 다음1년 수익": {**ic("d_past", "ret1"), "5분위차%p": qs("d_past", "ret1")},
            "과거1년 ΔlogPER → 다음1년 ΔlogPER": ic("d_past", "d_fut"),
            "(대조) 과거1년 수익(모멘텀) → 다음1년 수익": {**ic("ret_past", "ret1"), "5분위차%p": qs("ret_past", "ret1")}}


def main():
    res = {"지수(Shiller, 1881~)": index_part(), "종목(S&P500 2010~)": stock_part()}
    with open(os.path.join(HERE, "results_pe_change.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
