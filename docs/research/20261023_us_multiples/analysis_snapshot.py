"""질문 6: 2026-10 현재 S&P500 기준 PER vs 예상 PER·예상 이익 성장(Yahoo 애널리스트 평균), 한 시점.
입력 raw/yahoo_quotesummary/<날짜>/snapshot.csv, 비교용 panel.parquet(과거 '실현' 관계). 출력 results_snapshot.json
실행: .venv/bin/python -I analysis_snapshot.py
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "20261023_steady_compounders"))
from sc_common import ROOT  # noqa: E402

BASE = os.path.join(ROOT, "staging", "us_multiples")


def sp(a, b):
    d = pd.concat([a, b], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    return round(float(d.iloc[:, 0].rank().corr(d.iloc[:, 1].rank())), 3), int(len(d))


def main():
    fp = sorted(glob.glob(os.path.join(BASE, "raw", "yahoo_quotesummary", "*", "snapshot.csv")))[-1]
    S = pd.read_csv(fp)
    S = S[S.mcap > 0]
    pos = S[(S.trailingEps > 0) & (S.forwardEps > 0)].copy()
    pos["g_impl"] = pos.forwardEps / pos.trailingEps - 1            # 예상 EPS / 최근 12개월 EPS − 1
    agg_tr = S.mcap.sum() / (S.mcap / S.trailingPE).where(S.trailingPE > 0).sum()
    agg_fw = S.mcap.sum() / (S.mcap / S.forwardPE).where(S.forwardPE > 0).sum()
    res = {"원천": os.path.relpath(fp, ROOT), "종목": int(len(S)),
           "S&P500 합산(시총가중) 기준PER": round(float(agg_tr), 1), "합산 예상PER": round(float(agg_fw), 1),
           "합산 함의 이익성장(기준/예상−1)": round(float(agg_tr / agg_fw - 1), 3),
           "중앙 기준PER": round(float(S.trailingPE[S.trailingPE > 0].median()), 1),
           "중앙 예상PER": round(float(S.forwardPE[S.forwardPE > 0].median()), 1),
           "중앙 PBR": round(float(S.priceToBook[S.priceToBook > 0].median()), 2),
           "중앙 예상 이익성장(forwardEps/trailingEps−1)": round(float(pos.g_impl.median()), 3),
           "중앙 애널리스트 내년 성장(+1y)": round(float(S.g_next_year.median()), 3) if S.g_next_year.notna().any() else None,
           "상관(순위)": {
               "기준PER vs 예상PER": sp(np.log(pos.trailingPE), np.log(pos.forwardPE)),
               "기준PER vs 예상 이익성장": sp(np.log(pos.trailingPE), pos.g_impl),
               "예상PER vs 애널리스트 내년 성장": sp(np.log(pos.forwardPE), pos.g_next_year),
               "PBR vs ROE": sp(np.log(S.priceToBook.where(S.priceToBook > 0)), S.roe),
           }}
    # 과거 '실현' 관계와 비교: 같은 척도(기준PER vs 다음 1년 실제 EPS 성장, 흑자→흑자)
    P = pd.read_parquet(os.path.join(BASE, "derived", "panel.parquet"))
    d = P[(P.pe > 0)][["start", "pe", "leps1"]].dropna()
    ics = d.groupby("start").apply(lambda g: np.log(g.pe).rank().corr(g.leps1.rank()), include_groups=False)
    res["비교: 과거 기준PER vs 실제 다음 1년 EPS 성장(분기 평균 순위상관)"] = round(float(ics.mean()), 3)
    # 예상 성장 분위별 예상PER
    pos["q"] = pd.qcut(pos.g_impl.rank(method="first"), 5, labels=["1(낮음)", "2", "3", "4", "5(높음)"])
    res["예상 성장 5분위별 중앙 기준PER·예상PER"] = {str(k): {"예상성장": round(float(v.g_impl.median()), 3), "기준PER": round(float(v.trailingPE.median()), 1),
                                                    "예상PER": round(float(v.forwardPE.median()), 1)} for k, v in pos.groupby("q", observed=True)}
    with open(os.path.join(HERE, "results_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
